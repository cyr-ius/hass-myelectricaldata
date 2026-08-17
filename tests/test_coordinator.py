"""Tests for custom_components.myelectricaldata.coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from myelectricaldatapy import EnedisException, LimitReached

from custom_components.myelectricaldata.const import CONF_AUTH, CONF_TEMPO
from custom_components.myelectricaldata.coordinator import (
    EnedisDataUpdateCoordinator,
)


def _make_api_mock() -> MagicMock:
    """Return a MagicMock standing in for EnedisByPDL."""
    api = MagicMock()
    api.tempo_subscription = MagicMock()
    api.ecowatt_subscription = MagicMock()
    api.set_collects = MagicMock()
    api.async_update = AsyncMock()
    api.access = {"valid": True}
    api.contract = {"offpeak_hours": None}
    api.tempo_day = None
    api.ecowatt_day = None
    api.last_access = None
    api.last_refresh = None
    api.stats = {}
    return api


@pytest.fixture
def coordinator(hass, config_entry):
    """Return a coordinator bound to the mock config entry."""
    config_entry.add_to_hass(hass)
    return EnedisDataUpdateCoordinator(hass, config_entry)


async def test_init_sets_defaults_from_entry(coordinator, pdl):
    """The coordinator reads its PDL from the config entry data."""
    assert coordinator.pdl == pdl
    assert coordinator.access == {}
    assert coordinator.contract == {}
    assert coordinator.retry == 3


async def test_async_setup_builds_api(coordinator):
    """_async_setup instantiates the EnedisByPDL client without error."""
    with patch(
        "custom_components.myelectricaldata.coordinator.EnedisByPDL"
    ) as mock_cls:
        mock_cls.return_value = _make_api_mock()
        await coordinator._async_setup()
        assert mock_cls.called
        assert coordinator.api is mock_cls.return_value


async def test_async_update_data_populates_sensors(recorder_mock, coordinator):
    """A full update cycle stores per-entity summaries in the returned dict."""
    api = _make_api_mock()
    coordinator.api = api

    data = await coordinator._async_update_data()

    assert api.async_update.await_count == 1
    assert data  # at least one sensor entity present (production + consumption)
    for entity_id, payload in data.items():
        assert entity_id.startswith("sensor.")
        assert "value" in payload
    assert coordinator.access == api.access
    assert coordinator.contract == api.contract
    assert coordinator.retry == 2  # decremented once


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


async def test_async_update_data_enables_tempo_subscription(
    recorder_mock, coordinator, config_entry
):
    """Tempo subscription is toggled on when the option is enabled."""
    hass = coordinator.hass
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            **config_entry.options,
            CONF_AUTH: {**config_entry.options[CONF_AUTH], CONF_TEMPO: True},
        },
    )
    api = _make_api_mock()
    coordinator.api = api

    await coordinator._async_update_data()
    api.tempo_subscription.assert_called_once_with(True)


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
