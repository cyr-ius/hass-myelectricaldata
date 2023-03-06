"""Binary Sensor for power energy."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([CountdownSensor(coordinator)])


class CountdownSensor(CoordinatorEntity, BinarySensorEntity):
    """Sensor return token expiration date."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "MyElectricalData Token"
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        """Initialize the sensor."""
        super().__init__(coordinator)
        access = coordinator.access
        self._attr_unique_id = f"{coordinator.pdl}_token_expire"
        self._attr_extra_state_attributes = {
            "Call number": access.get("call_number"),
            "Last call": access.get("last_call"),
            "Banned": access.get("ban"),
            "Quota": access.get("quota_limit"),
            "Quota reached": access.get("quota_reached"),
            "Expiration date": access.get("consent_expiration_date"),
        }
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.pdl)})

    @property
    def is_on(self):
        """Value power."""
        return self.coordinator.access.get("valid", False) is False
