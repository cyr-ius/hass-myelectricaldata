"""Editable tariff entities for MyElectricalData.

These numbers are the single source of truth for pricing: their live value
is read back by the coordinator (see helpers.read_prices) every update cycle
to compute the cost statistics, and their value is restored across HA
restarts so the user only has to set a tariff once (and edit it whenever it
changes) instead of going through the config flow.
"""

import logging

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
    RestoreNumber,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyElectricalDataConfigEntry
from .const import DOMAIN, MANUFACTURER, URL
from .coordinator import EnedisDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyElectricalDataConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the tariff numbers."""
    coordinator = entry.runtime_data
    async_add_entities(
        TariffNumber(coordinator, item) for item in coordinator.price_items
    )


class TariffNumber(RestoreNumber, NumberEntity):
    """A user-editable tariff, persisted across restarts."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 2
    _attr_native_step = 0.0001
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_device_class = NumberDeviceClass.MONETARY

    def __init__(self, coordinator: EnedisDataUpdateCoordinator, item: dict) -> None:
        """Initialize the tariff number."""
        self.coordinator = coordinator
        self.entity_id = item["entity_id"]
        self._attr_unique_id = item["unique_id"]
        self._attr_name = item["name"]
        self._attr_native_value = item["default"]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.pdl)},
            name=f"Linky ({coordinator.pdl})",
            configuration_url=URL,
            manufacturer=MANUFACTURER,
        )

    async def async_added_to_hass(self) -> None:
        """Restore the previous value on startup."""
        await super().async_added_to_hass()
        if (last_data := await self.async_get_last_number_data()) is not None:
            self._attr_native_value = last_data.native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        self._attr_native_value = value
        self.async_write_ha_state()
