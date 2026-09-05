"""Tests for custom_components.myelectricaldata.coordinator."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_ENTITY_ID
from myelectricaldatapy import (
    ATTR_PRICE,
    ATTR_STANDARD,
    DAILY_CONSUM,
    EnedisException,
    LimitReached,
    Subscription,
    ThrottlingError,
)

from custom_components.myelectricaldata.const import (
    CONF_CONSUMPTION,
    CONF_SUMMARY,
    DEFAULT_CONSUMPTION_TEMPO,
)
from custom_components.myelectricaldata.coordinator import (
    EnedisDataUpdateCoordinator,
    _parse_next_access_time,
)


def _make_api_mock() -> MagicMock:
    """Return a MagicMock standing in for EnedisByPDL."""
    api = MagicMock()
    api.has_tempo_subscription = False
    api.has_ecowatt_subscription = False
    api.subscription = Subscription.STANDARD
    api.set_data_fetch = MagicMock()
    api.set_ecowatt_subscription = MagicMock()
    api.async_update = AsyncMock()
    api.stats = {}
    api.contract = None
    api.access = None
    api.last_refresh = None
    api.last_access = None
    return api


@pytest.fixture
def coordinator(hass, config_entry):
    """Return a coordinator bound to the mock config entry."""
    config_entry.add_to_hass(hass)
    coord = EnedisDataUpdateCoordinator(hass, config_entry)
    coord.config_entry = config_entry
    return coord


# ---------------------------------------------------------------------------
# _parse_next_access_time
# ---------------------------------------------------------------------------


def test_parse_next_access_time_with_timezone():
    """The gateway's ``2026-Sep-05 16:00:00+0000 UTC`` format is parsed."""
    parsed = _parse_next_access_time("2026-Sep-05 16:00:00+0000 UTC")
    assert parsed == dt(2026, 9, 5, 16, 0, 0, tzinfo=UTC)


def test_parse_next_access_time_without_timezone_defaults_to_utc():
    """A value with no offset is assumed to be UTC."""
    parsed = _parse_next_access_time("2026-Sep-05 16:00:00")
    assert parsed == dt(2026, 9, 5, 16, 0, 0, tzinfo=UTC)


def test_parse_next_access_time_none_or_garbage():
    assert _parse_next_access_time(None) is None
    assert _parse_next_access_time("not-a-date") is None


# ---------------------------------------------------------------------------
# __init__ / _async_setup
# ---------------------------------------------------------------------------


async def test_init_reads_pdl_and_defaults(coordinator, pdl):
    """The coordinator reads its PDL from the config entry data."""
    assert coordinator.pdl == pdl
    assert coordinator.last_stat is None
    assert coordinator._migrated_legacy_stats is False


async def test_async_setup_builds_api(recorder_mock, coordinator):
    """_async_setup instantiates the EnedisByPDL client without error."""
    api = _make_api_mock()
    with patch(
        "custom_components.myelectricaldata.coordinator.EnedisByPDL", return_value=api
    ) as mock_cls:
        await coordinator._async_setup()

    assert mock_cls.called
    assert coordinator.api is api
    api.set_ecowatt_subscription.assert_called_once_with(False)


async def test_async_setup_wraps_enedis_error(recorder_mock, coordinator):
    """An EnedisException raised while building the client becomes UpdateFailed."""
    from homeassistant.helpers.update_coordinator import UpdateFailed

    with (
        patch(
            "custom_components.myelectricaldata.coordinator.EnedisByPDL",
            side_effect=EnedisException("boom"),
        ),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_setup()


# ---------------------------------------------------------------------------
# _async_update_data
# ---------------------------------------------------------------------------


async def test_async_update_data_populates_sensors(recorder_mock, coordinator):
    """A full update cycle stores per-entity summaries in the returned dict."""
    api = _make_api_mock()
    coordinator.api = api

    data = await coordinator._async_update_data()

    assert api.async_update.await_count == 1
    assert data  # production + consumption buckets
    for payload in data.values():
        assert payload[CONF_ENTITY_ID].startswith("sensor.")
        assert CONF_SUMMARY in payload
    # set_data_fetch is called once per configured mode (production + consumption).
    assert api.set_data_fetch.call_count == 2


async def test_async_update_data_handles_limit_reached(recorder_mock, coordinator):
    """A LimitReached error from the API is caught and doesn't raise."""
    api = _make_api_mock()
    api.async_update = AsyncMock(side_effect=LimitReached(409, {"detail": "quota"}))
    coordinator.api = api

    data = await coordinator._async_update_data()
    assert data is not None


async def test_async_update_data_handles_enedis_exception(recorder_mock, coordinator):
    """A generic EnedisException from the API is caught and doesn't raise."""
    api = _make_api_mock()
    api.async_update = AsyncMock(side_effect=EnedisException(500, {"detail": "boom"}))
    coordinator.api = api

    data = await coordinator._async_update_data()
    assert data is not None


async def test_async_update_data_reschedules_after_throttling(
    recorder_mock, coordinator
):
    """A ThrottlingError schedules a one-off retry once the quota reopens."""
    api = _make_api_mock()
    api.async_update = AsyncMock(
        side_effect=ThrottlingError(
            "throttled", next_access_time="2999-Jan-01 00:00:00+0000 UTC"
        )
    )
    coordinator.api = api

    with patch(
        "custom_components.myelectricaldata.coordinator.async_track_point_in_time",
        return_value=lambda: None,
    ) as mock_track:
        await coordinator._async_update_data()

    mock_track.assert_called_once()


async def test_async_update_data_uses_tempo_prices_for_tempo_subscription(
    recorder_mock, hass, coordinator, config_entry
):
    """A Tempo subscription feeds the six-colour Tempo prices to the API.

    ``read_prices`` only falls back to the Tempo defaults when no consumption
    tariff has been stored yet, so the stored prices are dropped first.
    """
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            **config_entry.options,
            CONF_CONSUMPTION: {
                k: v
                for k, v in config_entry.options[CONF_CONSUMPTION].items()
                if k != "prices"
            },
        },
    )
    api = _make_api_mock()
    api.has_tempo_subscription = True
    coordinator.api = api

    await coordinator._async_update_data()

    consumption_call = next(
        call
        for call in api.set_data_fetch.call_args_list
        if call.kwargs["service"] == DAILY_CONSUM
    )
    assert (
        consumption_call.kwargs["prices"]
        == DEFAULT_CONSUMPTION_TEMPO["prices"]
    )


async def test_async_update_data_migrates_legacy_stats_once(recorder_mock, coordinator):
    """Legacy statistics migration only runs on the first refresh cycle."""
    api = _make_api_mock()
    coordinator.api = api

    with patch(
        "custom_components.myelectricaldata.coordinator.async_migrate_legacy_statistics",
        new=AsyncMock(),
    ) as mock_migrate:
        await coordinator._async_update_data()
        assert mock_migrate.await_count >= 1
        assert coordinator._migrated_legacy_stats is True

        mock_migrate.reset_mock()
        await coordinator._async_update_data()
        mock_migrate.assert_not_called()


async def test_configured_statistic_ids_covers_both_modes(coordinator):
    """Every configured mode contributes its energy + cost statistic ids."""
    ids = coordinator._configured_statistic_ids()
    assert any("consumption" in sid for sid in ids)
    assert any("production" in sid for sid in ids)
    assert all(sid.startswith("sensor.") for sid in ids)


async def test_async_update_data_imports_and_rebuilds_when_api_has_stats(
    recorder_mock, coordinator
):
    """When the API returns collected stats, they are imported then rebuilt."""
    api = _make_api_mock()
    start = dt(2020, 1, 1, tzinfo=UTC)
    api.stats = {
        CONF_CONSUMPTION: [
            {
                "notes": ATTR_STANDARD,
                "date": start,
                "value": 5.0,
                "sum_value": 5.0,
                "price": 1.0,
                "sum_price": 1.0,
            }
        ]
    }
    coordinator.api = api

    data = await coordinator._async_update_data()

    consumption_energy = next(
        payload
        for payload in data.values()
        if payload["mode"] == CONF_CONSUMPTION and payload["kind"] == "energy"
    )
    assert consumption_energy[CONF_SUMMARY] == 5.0
    assert ATTR_PRICE  # keep import referenced
