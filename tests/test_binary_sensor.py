"""Tests for custom_components.myelectricaldata.binary_sensor."""

from __future__ import annotations

from datetime import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from myelectricaldatapy import ATTR_INTERVALS, Subscription

from custom_components.myelectricaldata.binary_sensor import (
    BINARY_SENSOR_TYPES,
    CountdownSensor,
    OffpeakSensor,
    async_setup_entry,
)
from custom_components.myelectricaldata.const import (
    CONF_CONSUMPTION,
    CONF_RULE_END_TIME,
    CONF_RULE_START_TIME,
)

DESCRIPTIONS = {description.key: description for description in BINARY_SENSOR_TYPES}


def _intervals(*ranges: tuple[str, str]) -> dict[str, dict[str, str]]:
    return {
        str(idx): {CONF_RULE_START_TIME: start, CONF_RULE_END_TIME: end}
        for idx, (start, end) in enumerate(ranges, start=1)
    }


def _fake_api(**overrides):
    defaults = dict(
        contract=None,
        subscription=Subscription.HPHC,
        access=SimpleNamespace(
            consent_expiration_date=None, valid=True, quota_limit=10
        ),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_coordinator(*, api=None, intervals=None):
    options = {}
    if intervals is not None:
        options[CONF_CONSUMPTION] = {ATTR_INTERVALS: intervals}
    return SimpleNamespace(
        pdl="12345",
        api=api or _fake_api(),
        config_entry=SimpleNamespace(options=options),
        last_update_success=True,
        async_add_listener=lambda *args, **kwargs: (lambda: None),
    )


# ---------------------------------------------------------------------------
# async_setup_entry / _is_applicable
# ---------------------------------------------------------------------------


async def test_async_setup_entry_standard_only_adds_countdown():
    """A Standard subscription only gets the access-token binary sensor."""
    coordinator = _fake_coordinator(api=_fake_api(subscription=Subscription.STANDARD))
    entry = SimpleNamespace(runtime_data=coordinator)
    added: list = []

    await async_setup_entry(None, entry, lambda entities: added.extend(entities))

    assert {type(entity) for entity in added} == {CountdownSensor}


async def test_async_setup_entry_hphc_adds_both():
    """HP/HC (and Tempo) also get the offpeak-status binary sensor."""
    coordinator = _fake_coordinator(api=_fake_api(subscription=Subscription.HPHC))
    entry = SimpleNamespace(runtime_data=coordinator)
    added: list = []

    await async_setup_entry(None, entry, lambda entities: added.extend(entities))

    assert {type(entity) for entity in added} == {CountdownSensor, OffpeakSensor}


# ---------------------------------------------------------------------------
# CountdownSensor
# ---------------------------------------------------------------------------


def test_countdown_sensor_on_when_access_invalid_and_no_expiration():
    """No expiration date: the sensor mirrors ``access.valid is False``."""
    coordinator = _fake_coordinator(
        api=_fake_api(
            access=SimpleNamespace(consent_expiration_date=None, valid=False)
        )
    )
    sensor = CountdownSensor(coordinator, DESCRIPTIONS["access_token"])
    assert sensor._attr_is_on is True


def test_countdown_sensor_off_when_access_valid():
    coordinator = _fake_coordinator(
        api=_fake_api(access=SimpleNamespace(consent_expiration_date=None, valid=True))
    )
    sensor = CountdownSensor(coordinator, DESCRIPTIONS["access_token"])
    assert sensor._attr_is_on is False


def test_countdown_sensor_on_when_expiration_in_the_past():
    coordinator = _fake_coordinator(
        api=_fake_api(
            access=SimpleNamespace(
                consent_expiration_date="2000-01-01T00:00:00+00:00", valid=True
            )
        )
    )
    sensor = CountdownSensor(coordinator, DESCRIPTIONS["access_token"])
    assert sensor._attr_is_on is True


def test_countdown_sensor_off_when_expiration_in_the_future():
    coordinator = _fake_coordinator(
        api=_fake_api(
            access=SimpleNamespace(
                consent_expiration_date="2999-01-01T00:00:00+00:00", valid=True
            )
        )
    )
    sensor = CountdownSensor(coordinator, DESCRIPTIONS["access_token"])
    assert sensor._attr_is_on is False
    assert sensor._attr_extra_state_attributes["Expiration date"] == (
        "2999-01-01T00:00:00+00:00"
    )


def test_countdown_sensor_handle_coordinator_update():
    coordinator = _fake_coordinator(
        api=_fake_api(access=SimpleNamespace(consent_expiration_date=None, valid=True))
    )
    sensor = CountdownSensor(coordinator, DESCRIPTIONS["access_token"])
    coordinator.api.access = SimpleNamespace(consent_expiration_date=None, valid=False)
    with patch.object(sensor, "async_write_ha_state"):
        sensor._handle_coordinator_update()
    assert sensor._attr_is_on is True


# ---------------------------------------------------------------------------
# OffpeakSensor
# ---------------------------------------------------------------------------


def _offpeak_sensor(*ranges: tuple[str, str]) -> OffpeakSensor:
    coordinator = _fake_coordinator(intervals=_intervals(*ranges))
    return OffpeakSensor(coordinator, DESCRIPTIONS["offpeak_status"])


@patch("custom_components.myelectricaldata.binary_sensor.dt_util")
def test_offpeak_sensor_inside_simple_range(mock_dt):
    mock_dt.now.return_value = dt(2026, 1, 1, 3, 0)  # noqa: DTZ001
    sensor = _offpeak_sensor(("01:00:00", "06:00:00"))
    assert sensor._fetch_state() is True


@patch("custom_components.myelectricaldata.binary_sensor.dt_util")
def test_offpeak_sensor_outside_simple_range(mock_dt):
    mock_dt.now.return_value = dt(2026, 1, 1, 12, 0)  # noqa: DTZ001
    sensor = _offpeak_sensor(("01:00:00", "06:00:00"))
    assert sensor._fetch_state() is False


@patch("custom_components.myelectricaldata.binary_sensor.dt_util")
def test_offpeak_sensor_wrapping_range(mock_dt):
    mock_dt.now.return_value = dt(2026, 1, 1, 23, 0)  # noqa: DTZ001
    sensor = _offpeak_sensor(("22:00:00", "06:00:00"))
    for hour, expected in ((23, True), (3, True), (12, False)):
        mock_dt.now.return_value = dt(2026, 1, 1, hour, 0)  # noqa: DTZ001
        assert sensor._fetch_state() is expected


@patch("custom_components.myelectricaldata.binary_sensor.dt_util")
def test_offpeak_sensor_multiple_ranges(mock_dt):
    mock_dt.now.return_value = dt(2026, 1, 1, 13, 0)  # noqa: DTZ001
    sensor = _offpeak_sensor(("01:30:00", "08:00:00"), ("12:30:00", "14:00:00"))
    assert sensor._fetch_state() is True


def test_offpeak_sensor_extra_state_attributes_lists_ranges():
    sensor = _offpeak_sensor(("01:00:00", "06:00:00"))
    attrs = sensor._build_extra_state_attributes()
    assert attrs["offpeak hours"] == ["01:00-06:00"]


@patch("custom_components.myelectricaldata.binary_sensor.dt_util")
def test_offpeak_sensor_handle_coordinator_update(mock_dt):
    mock_dt.now.return_value = dt(2026, 1, 1, 3, 0)  # noqa: DTZ001
    sensor = _offpeak_sensor(("01:00:00", "06:00:00"))
    with patch.object(sensor, "async_write_ha_state"):
        sensor._handle_coordinator_update()
    assert sensor._attr_is_on is True


async def test_offpeak_sensor_added_to_hass_schedules_local_refresh(hass):
    """async_added_to_hass wires up a periodic local refresh timer."""
    sensor = _offpeak_sensor(("01:00:00", "06:00:00"))
    sensor.hass = hass
    sensor.entity_id = "binary_sensor.myelectricaldata_offpeak"

    with patch(
        "custom_components.myelectricaldata.binary_sensor.async_track_time_interval",
        return_value=lambda: None,
    ) as mock_track:
        await sensor.async_added_to_hass()

    mock_track.assert_called_once()


@pytest.mark.parametrize("key", list(DESCRIPTIONS))
def test_every_description_has_a_factory(key):
    assert callable(DESCRIPTIONS[key].cls)
