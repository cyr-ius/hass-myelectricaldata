"""Sensor for power energy."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .coordinator import EnedisDataUpdateCoordinator

from .const import DOMAIN, MANUFACTURER, URL

DAY_VALUES = {0: "na", 1: "green", 2: "orange", 3: "red"}
TEMPO_OPTIONS = ["blue", "white", "red"]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities = []
    for name in coordinator.data.keys():
        entities.append(PowerSensor(coordinator, name))
    if coordinator.tempo_day:
        entities.append(TempoSensor(coordinator))
    if coordinator.ecowatt_day:
        entities.append(EcoWattSensor(coordinator))

    async_add_entities(entities)


class PowerSensor(CoordinatorEntity[EnedisDataUpdateCoordinator], SensorEntity):
    """Sensor return power."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_has_entity_name = True

    def __init__(self, coordinator, sensor_name) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.sensor_mode = sensor_name
        self._attr_unique_id = f"{coordinator.pdl}_{sensor_name}"
        self._attr_name = sensor_name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.pdl)},
            name=f"Linky ({coordinator.pdl})",
            configuration_url=URL,
            manufacturer=MANUFACTURER,
            model=coordinator.contracts.get("subscribed_power"),
            suggested_area="Garage",
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        contracts = self.coordinator.contract
        self._attr_native_value = round(float(self.coordinator.data.get(self.name)), 2)
        self._attr_extra_state_attributes = {
            "offpeak hours": contracts.get("offpeak_hours"),
            "last activation date": contracts.get("last_activation_date"),
            "last tariff changedate": contracts.get(
                "last_distribution_tariff_change_date"
            ),
        }
        self.async_write_ha_state()


class TempoSensor(CoordinatorEntity, SensorEntity):
    """Sensor return token expiration date."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_name = "Tempo day"
    _attr_has_entity_name = True
    _attr_translation_key = "tempo"
    _attr_options = TEMPO_OPTIONS

    def __init__(self, coordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.pdl}_tempo_day"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.pdl)})

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self.coordinator.tempo_day
        self.async_write_ha_state()


class EcoWattSensor(CoordinatorEntity, SensorEntity):
    """Sensor return token expiration date."""

    _attr_name = "EcoWatt"
    _attr_has_entity_name = True
    _attr_translation_key = "ecowatt"

    def __init__(self, coordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.pdl}_ecowatt"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.pdl)})

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = DAY_VALUES[
            self.coordinator.ecowatt_day.get("value", 0)
        ]
        self._attr_extra_state_attributes = {
            "message": self.coordinator.ecowatt_day.get("message")
        }
        self.async_write_ha_state()
