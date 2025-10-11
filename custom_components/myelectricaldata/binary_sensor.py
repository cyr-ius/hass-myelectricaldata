"""Binary Sensor for power energy."""

from __future__ import annotations

import logging
import re
from datetime import datetime as dt
from datetime import time, timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyElectricalDataConfigEntry
from .const import DOMAIN
from .coordinator import EnedisDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyElectricalDataConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities([CountdownSensor(coordinator), OffpeakSensor(coordinator)])


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
            "Last access": self.coordinator.last_access,
            "Last refresh": self.coordinator.last_refresh,
        }
        super()._handle_coordinator_update()


class OffpeakSensor(CoordinatorEntity[EnedisDataUpdateCoordinator], BinarySensorEntity):
    """Sensor return offpeak status."""

    _attr_name = "Offpeak hours"
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.pdl}_offpeak_status"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.pdl)})
        self._attr_available = coordinator.contract.get("offpeak_hours") is not None
        self._attr_is_on = self._fetch_state()
        self._unsubscribe: CALLBACK_TYPE | None = None

    @callback
    def _fetch_state(self) -> bool:
        """Fetch state."""
        # Format HC (1H30-8H00;12H30-14H00)
        data = self.coordinator.contract.get("offpeak_hours", "")
        ranges = re.findall(r"(\d{1,2}H\d{2})-(\d{1,2}H\d{2})", data)

        def parse_heure(h_str: str) -> time:
            """Convert string '8H00' to time object."""
            h, m = h_str.replace("H", ":").split(":")
            return time(int(h), int(m))

        range_hours = [(parse_heure(d), parse_heure(f)) for d, f in ranges]
        now = dt.now().time()

        for debut, fin in range_hours:
            if debut <= fin:
                if debut <= now <= fin:
                    return True
            elif now >= debut or now <= fin:
                return True
        return False

    async def async_added_to_hass(self) -> None:
        """Démarre la boucle d’auto-refresh local toutes les 10 min."""
        await super().async_added_to_hass()
        self._unsubscribe = async_track_time_interval(
            self.hass, self._hass_create_refresh_task, interval=timedelta(minutes=1)
        )

    async def _hass_create_refresh_task(self, _: dt) -> None:
        """Crée une tâche périodique de recalcul local."""
        self._attr_is_on = self._fetch_state()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_is_on = self._fetch_state()
        super()._handle_coordinator_update()

    async def async_unload(self) -> None:
        """Unload the heartbeat."""
        if self._unsubscribe is not None:
            self._unsubscribe()
