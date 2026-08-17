"""Tests for custom_components.myelectricaldata.number."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.myelectricaldata.number import TariffNumber, async_setup_entry

PRICE_ITEM = {
    "unique_id": "12345_consumption_standard_price",
    "entity_id": "number.myelectricaldata_12345_consumption_standard_price",
    "name": "Consumption standard price",
    "default": 0.174,
}


def _fake_coordinator(**overrides):
    """Build a minimal stand-in for EnedisDataUpdateCoordinator."""
    defaults = dict(pdl="12345", price_items=[PRICE_ITEM])
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_tariff_number_initializes_from_item():
    """The number entity is seeded with the descriptor's default value."""
    coordinator = _fake_coordinator()
    number = TariffNumber(coordinator, PRICE_ITEM)

    assert number.entity_id == PRICE_ITEM["entity_id"]
    assert number._attr_unique_id == PRICE_ITEM["unique_id"]
    assert number._attr_name == PRICE_ITEM["name"]
    assert number._attr_native_value == PRICE_ITEM["default"]
    assert number._attr_device_info["manufacturer"] == "Enedis"


async def test_async_set_native_value_updates_state(hass):
    """Setting a new value updates the stored native value and writes state."""
    coordinator = _fake_coordinator()
    number = TariffNumber(coordinator, PRICE_ITEM)
    number.hass = hass
    number.entity_id = PRICE_ITEM["entity_id"]

    await number.async_set_native_value(0.25)

    assert number._attr_native_value == 0.25
    state = hass.states.get(PRICE_ITEM["entity_id"])
    assert state is not None
    assert float(state.state) == 0.25


async def test_async_added_to_hass_restores_previous_value(hass):
    """On restart, the previously stored value is restored if available."""
    coordinator = _fake_coordinator()
    number = TariffNumber(coordinator, PRICE_ITEM)
    number.hass = hass
    number.entity_id = PRICE_ITEM["entity_id"]

    async def _fake_last_number_data():
        return SimpleNamespace(native_value=0.42)

    number.async_get_last_number_data = _fake_last_number_data
    await number.async_added_to_hass()

    assert number._attr_native_value == 0.42


async def test_async_setup_entry_adds_one_number_per_price_item():
    """The platform adds one TariffNumber entity per coordinator price item."""
    second_item = {**PRICE_ITEM, "unique_id": "x2", "entity_id": "number.x2"}
    coordinator = _fake_coordinator(price_items=[PRICE_ITEM, second_item])
    entry = SimpleNamespace(runtime_data=coordinator)
    added: list = []

    await async_setup_entry(None, entry, lambda entities: added.extend(entities))

    assert len(added) == 2
    assert all(isinstance(entity, TariffNumber) for entity in added)
