"""Tests for custom_components.myelectricaldata.sensor."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from custom_components.myelectricaldata.sensor import (
    DAY_VALUES,
    EcoWattSensor,
    PowerSensor,
    TempoSensor,
    async_setup_entry,
)


def _fake_coordinator(**overrides):
    """Build a minimal stand-in for EnedisDataUpdateCoordinator."""
    defaults = dict(
        pdl="12345",
        contract={
            "subscribed_power": "9 kVA",
            "offpeak_hours": "01H00-06H00",
            "last_activation_date": "2020-01-01",
            "last_distribution_tariff_change_date": "2021-01-01",
        },
        data={},
        tempo_day=None,
        ecowatt_day=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


ENERGY_ITEM = {
    "unique_id": "12345_consumption_standard",
    "entity_id": "sensor.myelectricaldata_12345_consumption_standard",
    "friendly_name": "consumption standard",
    "kind": "energy",
    "value": "12.345",
}
COST_ITEM = {
    "unique_id": "12345_consumption_standard_cost",
    "entity_id": "sensor.myelectricaldata_12345_consumption_standard_cost",
    "friendly_name": "consumption standard cost",
    "kind": "cost",
    "value": "3.456",
}


def test_power_sensor_energy_kind_attributes():
    """An energy item configures ENERGY device class and increasing state class."""
    coordinator = _fake_coordinator(data={ENERGY_ITEM["entity_id"]: ENERGY_ITEM})
    sensor = PowerSensor(coordinator, ENERGY_ITEM["entity_id"])

    assert sensor._attr_unique_id == ENERGY_ITEM["unique_id"]
    assert sensor._attr_native_value == 12.35
    assert sensor._attr_device_info["manufacturer"] == "Enedis"
    assert sensor.extra_state_attributes["offpeak hours"] == "01H00-06H00"


def test_power_sensor_cost_kind_attributes():
    """A cost item configures MONETARY device class and a plain total state class."""
    coordinator = _fake_coordinator(data={COST_ITEM["entity_id"]: COST_ITEM})
    sensor = PowerSensor(coordinator, COST_ITEM["entity_id"])

    assert sensor._attr_native_unit_of_measurement == "EUR"
    assert sensor._attr_native_value == 3.46


def test_power_sensor_handle_coordinator_update_refreshes_value():
    """A coordinator update recomputes the native value from fresh data."""
    coordinator = _fake_coordinator(data={ENERGY_ITEM["entity_id"]: ENERGY_ITEM})
    sensor = PowerSensor(coordinator, ENERGY_ITEM["entity_id"])

    updated_item = {**ENERGY_ITEM, "value": "20.0"}
    coordinator.data[ENERGY_ITEM["entity_id"]] = updated_item
    with patch.object(sensor, "async_write_ha_state"):
        sensor._handle_coordinator_update()

    assert sensor._attr_native_value == 20.0


def test_power_sensor_handle_coordinator_update_missing_entity_keeps_value():
    """If the entity disappears from coordinator data, the last value is kept."""
    coordinator = _fake_coordinator(data={ENERGY_ITEM["entity_id"]: ENERGY_ITEM})
    sensor = PowerSensor(coordinator, ENERGY_ITEM["entity_id"])

    coordinator.data.clear()
    with patch.object(sensor, "async_write_ha_state"):
        sensor._handle_coordinator_update()

    assert sensor._attr_native_value == 12.35


def test_tempo_sensor_initial_and_update():
    """TempoSensor exposes the coordinator's tempo_day and refreshes on update."""
    coordinator = _fake_coordinator(tempo_day="blue")
    sensor = TempoSensor(coordinator)

    assert sensor._attr_unique_id == "12345_tempo_day"
    assert sensor._attr_native_value == "blue"

    coordinator.tempo_day = "red"
    with patch.object(sensor, "async_write_ha_state"):
        sensor._handle_coordinator_update()
    assert sensor._attr_native_value == "red"


def test_ecowatt_sensor_initial_and_update():
    """EcoWattSensor maps the numeric day value to its label and message."""
    coordinator = _fake_coordinator(ecowatt_day={"value": 2, "message": "be careful"})
    sensor = EcoWattSensor(coordinator)

    assert sensor._attr_native_value == DAY_VALUES[2]
    assert sensor._attr_extra_state_attributes["message"] == "be careful"

    coordinator.ecowatt_day = {"value": 3, "message": "critical"}
    with patch.object(sensor, "async_write_ha_state"):
        sensor._handle_coordinator_update()
    assert sensor._attr_native_value == DAY_VALUES[3]
    assert sensor._attr_extra_state_attributes["message"] == "critical"


def test_ecowatt_sensor_defaults_to_na_when_value_missing():
    """A missing 'value' key falls back to the 'na' label."""
    coordinator = _fake_coordinator(ecowatt_day={})
    sensor = EcoWattSensor(coordinator)
    assert sensor._attr_native_value == "na"


async def test_async_setup_entry_adds_power_sensors_only():
    """Without tempo/ecowatt data, only PowerSensor entities are added."""
    coordinator = _fake_coordinator(data={ENERGY_ITEM["entity_id"]: ENERGY_ITEM})
    entry = SimpleNamespace(runtime_data=coordinator)
    added: list = []

    await async_setup_entry(None, entry, lambda entities: added.extend(entities))

    assert len(added) == 1
    assert isinstance(added[0], PowerSensor)


async def test_async_setup_entry_adds_tempo_and_ecowatt_sensors():
    """Tempo and EcoWatt sensors are added when the coordinator has that data."""
    coordinator = _fake_coordinator(
        data={ENERGY_ITEM["entity_id"]: ENERGY_ITEM},
        tempo_day="blue",
        ecowatt_day={"value": 1, "message": "ok"},
    )
    entry = SimpleNamespace(runtime_data=coordinator)
    added: list = []

    await async_setup_entry(None, entry, lambda entities: added.extend(entities))

    kinds = {type(entity) for entity in added}
    assert kinds == {PowerSensor, TempoSensor, EcoWattSensor}
