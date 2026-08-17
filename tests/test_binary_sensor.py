"""Tests for custom_components.myelectricaldata.binary_sensor."""

from __future__ import annotations

from datetime import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.myelectricaldata.binary_sensor import (
    CountdownSensor,
    OffpeakSensor,
    async_setup_entry,
)


def _fake_coordinator(**overrides):
    """Build a minimal stand-in for EnedisDataUpdateCoordinator."""
    defaults = dict(
        pdl="12345",
        access={},
        contract={},
        last_access=None,
        last_refresh=None,
        last_update_success=True,
        async_add_listener=lambda *args, **kwargs: (lambda: None),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_async_setup_entry_adds_both_sensors():
    """The platform always adds a countdown and an offpeak sensor."""
    coordinator = _fake_coordinator()
    entry = SimpleNamespace(runtime_data=coordinator)
    added: list = []

    await async_setup_entry(None, entry, lambda entities: added.extend(entities))

    kinds = {type(entity) for entity in added}
    assert kinds == {CountdownSensor, OffpeakSensor}


def test_countdown_sensor_is_on_when_access_invalid():
    """The token binary sensor is 'on' (problem) when access isn't valid."""
    coordinator = _fake_coordinator(access={"valid": False, "quota_limit": 10})
    sensor = CountdownSensor(coordinator)
    assert sensor._attr_is_on is True
    assert sensor._attr_extra_state_attributes["Quota"] == 10


def test_countdown_sensor_is_off_when_access_valid():
    """The token binary sensor is 'off' when access is valid."""
    coordinator = _fake_coordinator(access={"valid": True})
    sensor = CountdownSensor(coordinator)
    assert sensor._attr_is_on is False


def test_countdown_sensor_handle_coordinator_update():
    """A coordinator update recomputes is_on from the fresh access dict."""
    coordinator = _fake_coordinator(access={"valid": True})
    sensor = CountdownSensor(coordinator)
    coordinator.access = {"valid": False}
    with patch.object(sensor, "async_write_ha_state"):
        sensor._handle_coordinator_update()
    assert sensor._attr_is_on is True


def test_offpeak_sensor_unavailable_without_offpeak_hours():
    """No offpeak_hours in the contract makes the sensor unavailable."""
    coordinator = _fake_coordinator(contract={})
    sensor = OffpeakSensor(coordinator)
    assert sensor._attr_available is False
    assert sensor._attr_is_on is False


def test_offpeak_sensor_fetch_state_true_inside_simple_range():
    """A simple (non-wrapping) offpeak range is detected as active."""
    coordinator = _fake_coordinator(contract={"offpeak_hours": "01H00-06H00"})
    sensor = OffpeakSensor(coordinator)
    with patch("custom_components.myelectricaldata.binary_sensor.dt_util") as mock_dt:
        mock_dt.now.return_value = dt(2026, 1, 1, 3, 0)
        assert sensor._fetch_state() is True


def test_offpeak_sensor_fetch_state_false_outside_simple_range():
    """Outside a simple offpeak range, the sensor is inactive."""
    coordinator = _fake_coordinator(contract={"offpeak_hours": "01H00-06H00"})
    sensor = OffpeakSensor(coordinator)
    with patch("custom_components.myelectricaldata.binary_sensor.dt_util") as mock_dt:
        mock_dt.now.return_value = dt(2026, 1, 1, 12, 0)
        assert sensor._fetch_state() is False


def test_offpeak_sensor_fetch_state_true_inside_wrapping_range():
    """A range that wraps past midnight (e.g. 22H00-06H00) is handled."""
    coordinator = _fake_coordinator(contract={"offpeak_hours": "22H00-06H00"})
    sensor = OffpeakSensor(coordinator)
    with patch("custom_components.myelectricaldata.binary_sensor.dt_util") as mock_dt:
        mock_dt.now.return_value = dt(2026, 1, 1, 23, 30)
        assert sensor._fetch_state() is True
    with patch("custom_components.myelectricaldata.binary_sensor.dt_util") as mock_dt:
        mock_dt.now.return_value = dt(2026, 1, 1, 3, 0)
        assert sensor._fetch_state() is True
    with patch("custom_components.myelectricaldata.binary_sensor.dt_util") as mock_dt:
        mock_dt.now.return_value = dt(2026, 1, 1, 12, 0)
        assert sensor._fetch_state() is False


def test_offpeak_sensor_fetch_state_multiple_ranges():
    """Multiple ';'-separated ranges are all checked."""
    coordinator = _fake_coordinator(
        contract={"offpeak_hours": "01H30-08H00;12H30-14H00"}
    )
    sensor = OffpeakSensor(coordinator)
    with patch("custom_components.myelectricaldata.binary_sensor.dt_util") as mock_dt:
        mock_dt.now.return_value = dt(2026, 1, 1, 13, 0)
        assert sensor._fetch_state() is True


def test_offpeak_sensor_handle_coordinator_update_recomputes_state():
    """A coordinator update re-evaluates whether we're in offpeak hours."""
    coordinator = _fake_coordinator(contract={"offpeak_hours": "01H00-06H00"})
    sensor = OffpeakSensor(coordinator)
    with (
        patch("custom_components.myelectricaldata.binary_sensor.dt_util") as mock_dt,
        patch.object(sensor, "async_write_ha_state"),
    ):
        mock_dt.now.return_value = dt(2026, 1, 1, 3, 0)
        sensor._handle_coordinator_update()
    assert sensor._attr_is_on is True


async def test_offpeak_sensor_added_to_hass_schedules_refresh(hass):
    """async_added_to_hass wires up a periodic local refresh."""
    coordinator = _fake_coordinator(contract={"offpeak_hours": "01H00-06H00"})
    sensor = OffpeakSensor(coordinator)
    sensor.hass = hass
    sensor.entity_id = "binary_sensor.myelectricaldata_offpeak"

    await sensor.async_added_to_hass()
    assert sensor._unsubscribe is not None

    await sensor._hass_create_refresh_task(dt.now())

    await sensor.async_unload()
