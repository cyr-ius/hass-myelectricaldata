"""Binary Sensor for power energy."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EnedisDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([CountdownSensor(coordinator)])


class CountdownSensor(
    CoordinatorEntity[EnedisDataUpdateCoordinator], BinarySensorEntity
):
    """Sensor return token expiration date."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "MyElectricalData Token"
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.pdl}_token_expire"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.pdl)})
        self._attr_is_on = coordinator.access.get("valid", False) is False
        self._attr_extra_state_attributes = {
            "Call number": coordinator.access.get("call_number"),
            "Last call": coordinator.access.get("last_call"),
            "Banned": coordinator.access.get("ban"),
            "Quota": coordinator.access.get("quota_limit"),
            "Quota reached": coordinator.access.get("quota_reached"),
            "Expiration date": coordinator.access.get("consent_expiration_date"),
            "Last access": coordinator.last_access,
            "Last refresh": coordinator.last_refresh,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_is_on = self.coordinator.access.get("valid", False) is False
        self._attr_extra_state_attributes = {
            "Call number": self.coordinator.access.get("call_number"),
            "Last call": self.coordinator.access.get("last_call"),
            "Banned": self.coordinator.access.get("ban"),
            "Quota": self.coordinator.access.get("quota_limit"),
            "Quota reached": self.coordinator.access.get("quota_reached"),
            "Expiration date": self.coordinator.access.get("consent_expiration_date"),
            "Last access": self.oordinator.last_access,
            "Last refresh": self.coordinator.last_refresh,
        }
        super()._handle_coordinator_update()
