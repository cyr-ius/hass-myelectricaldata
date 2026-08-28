"""Binary Sensor for power energy."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Final, Literal, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from . import MyElectricalDataConfigEntry
from .const import (
    CONF_CONSUMPTION,
    CONF_HPHC,
    CONF_INTERVALS,
    CONF_RULE_END_TIME,
    CONF_RULE_START_TIME,
    CONF_STD,
)
from .coordinator import EnedisDataUpdateCoordinator
from .entity import MyElectricalEntity


@dataclass(frozen=True, kw_only=True)
class MyElectricalBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes MyElectricalData binary sensor entity."""

    cls: Callable[
        [EnedisDataUpdateCoordinator, "MyElectricalBinarySensorEntityDescription"],
        BinarySensorEntity,
    ]
    subscription: Literal["hphc", "tempo", "standard"] = CONF_STD


BINARY_SENSOR_TYPES: Final[tuple[MyElectricalBinarySensorEntityDescription, ...]] = (
    MyElectricalBinarySensorEntityDescription(
        key="access_token",
        name="Access Token",
        translation_key="access_token",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: CountdownSensor(coordinator, description),
    ),
    MyElectricalBinarySensorEntityDescription(
        key="offpeak_status",
        name="Offpeak Status",
        translation_key="offpeak_status",
        device_class=BinarySensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: OffpeakSensor(coordinator, description),
        subscription=CONF_HPHC,
    ),
)


def _is_applicable(
    description: MyElectricalBinarySensorEntityDescription,
    coordinator: EnedisDataUpdateCoordinator,
) -> bool:
    """Return whether the binary sensor is relevant for the account's configuration."""
    if description.key == "access_token":
        return True
    return description.subscription == coordinator.subscription


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyElectricalDataConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    entities = [
        description.cls(coordinator, description)
        for description in BINARY_SENSOR_TYPES
        if _is_applicable(description, coordinator)
    ]
    async_add_entities(entities)


class CountdownSensor(MyElectricalEntity, BinarySensorEntity):
    """Sensor that turns on once the API access consent has expired."""

    def __init__(self, coordinator, description) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description)
        self._attr_is_on = self._fetch_state()
        self._attr_extra_state_attributes = self._build_extra_state_attributes()

    def _fetch_state(self) -> bool:
        """Return True once the consent expiration date has been reached."""
        access = self.coordinator.api.access
        expiration_date = dt_util.parse_datetime(
            access.get("consent_expiration_date") or ""
        )
        if expiration_date is None:
            return access.get("valid", False) is False
        if expiration_date.tzinfo is None:
            expiration_date = dt_util.as_utc(expiration_date)
        return dt_util.now() >= expiration_date

    def _build_extra_state_attributes(self) -> dict:
        """Return extra state."""
        return {
            "Expiration date": self.coordinator.api.access.get(
                "consent_expiration_date"
            ),
        }

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_is_on = self._fetch_state()
        self._attr_extra_state_attributes = self._build_extra_state_attributes()
        super()._handle_coordinator_update()


OFFPEAK_RECALC_INTERVAL = timedelta(minutes=5)


class OffpeakSensor(MyElectricalEntity, BinarySensorEntity):
    """Sensor return offpeak status."""

    def __init__(self, coordinator, description) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description)
        self._attr_is_on = self._fetch_state()
        self._attr_extra_state_attributes = self._build_extra_state_attributes()

    def _get_offpeak_ranges(self) -> list[tuple[time, time]]:
        """Return the offpeak/tempo time ranges configured via the config flow."""
        intervals = self.coordinator.config_entry.options.get(CONF_CONSUMPTION, {}).get(
            CONF_INTERVALS, {}
        )
        return [
            (
                time.fromisoformat(interval[CONF_RULE_START_TIME]),
                time.fromisoformat(interval[CONF_RULE_END_TIME]),
            )
            for interval in intervals.values()
        ]

    def _build_extra_state_attributes(self) -> dict:
        """Return extra state."""
        return {
            "offpeak hours": [
                f"{debut.isoformat(timespec='minutes')}-{fin.isoformat(timespec='minutes')}"
                for debut, fin in self._get_offpeak_ranges()
            ],
        }

    @override
    async def async_added_to_hass(self) -> None:
        """Recompute the offpeak status on a fixed clock.

        The coordinator only polls the API every SCAN_INTERVAL (hours), but
        this sensor is a pure local time comparison against the configured
        offpeak/tempo ranges, so it can and should be recalculated much more
        often.
        """
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._async_recalculate, OFFPEAK_RECALC_INTERVAL
            )
        )

    @callback
    def _async_recalculate(self, now: datetime) -> None:
        """Recalculate offpeak status from the current time."""
        self._attr_is_on = self._fetch_state()
        self.async_write_ha_state()

    @callback
    def _fetch_state(self) -> bool:
        """Fetch state."""
        now = dt_util.now().time()
        for debut, fin in self._get_offpeak_ranges():
            if debut <= fin:
                if debut <= now <= fin:
                    return True
            elif now >= debut or now <= fin:
                return True
        return False

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_is_on = self._fetch_state()
        self._attr_extra_state_attributes = self._build_extra_state_attributes()
        super()._handle_coordinator_update()
