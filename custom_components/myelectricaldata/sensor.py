"""Sensor for power energy."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import MyElectricalDataConfigEntry
from .const import (
    CONF_AUTH,
    CONF_BLUE,
    CONF_GREEN,
    CONF_HPHC,
    CONF_NA,
    CONF_OFF_PRICE,
    CONF_OFFPEAK,
    CONF_ORANGE,
    CONF_PRICE,
    CONF_PRICINGS,
    CONF_PRODUCTION,
    CONF_RED,
    CONF_SERVICE,
    CONF_STD,
    CONF_SUMMARY,
    CONF_TEMPO,
    CONF_WHITE,
)
from .coordinator import EnedisDataUpdateCoordinator
from .entity import MyElectricalEntity

DAY_VALUES = {0: CONF_NA, 1: CONF_GREEN, 2: CONF_ORANGE, 3: CONF_RED}
TEMPO_OPTIONS = [CONF_BLUE, CONF_WHITE, CONF_RED]
EUR = "EUR"
EUR_PER_KWH = "EUR/kWh"


@dataclass(frozen=True, kw_only=True)
class MyElectricalSensorEntityDescription(SensorEntityDescription):
    """Describes MyElectricalData sensor entity."""

    cls: Callable[
        [EnedisDataUpdateCoordinator, "MyElectricalSensorEntityDescription"],
        SensorEntity,
    ]
    subscription: Literal["hphc", "tempo", "standard"] = CONF_STD
    requires_production: bool = False
    value_fn: Callable[[dict[str, Any]], float] | None = None


SENSOR_TYPES: Final[tuple[MyElectricalSensorEntityDescription, ...]] = (
    MyElectricalSensorEntityDescription(
        key="consomption",
        name="Consomption",
        translation_key="consomption",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
    ),
    MyElectricalSensorEntityDescription(
        key="consomption_cost",
        name="Consomption Cost",
        translation_key="consomption_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=EUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
    ),
    MyElectricalSensorEntityDescription(
        key="production",
        name="Production",
        translation_key="production",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
        requires_production=True,
    ),
    MyElectricalSensorEntityDescription(
        key="production_cost",
        name="Production Cost",
        translation_key="production_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=EUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
        requires_production=True,
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_full",
        name="Consumption Full",
        translation_key="consumption_full",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
        subscription=CONF_HPHC,
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_full_cost",
        name="Consumption Full Cost",
        translation_key="consumption_full_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=EUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
        subscription=CONF_HPHC,
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_offpeak",
        name="Consumption Offpeak",
        translation_key="consumption_offpeak",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
        subscription=CONF_HPHC,
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_offpeak_cost",
        name="Consumption Offpeak Cost",
        translation_key="consumption_offpeak_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=EUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
        subscription=CONF_HPHC,
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_price",
        name="Consumption Price",
        translation_key="consumption_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        value_fn=lambda options: options[CONF_AUTH][CONF_PRICE],
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_full_price",
        name="Consumption Full Price",
        translation_key="consumption_full_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        subscription=CONF_HPHC,
        value_fn=lambda options: options[CONF_AUTH][CONF_PRICE],
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_offpeak_price",
        name="Consumption Offpeak Price",
        translation_key="consumption_offpeak_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        subscription=CONF_HPHC,
        value_fn=lambda options: options[CONF_AUTH][CONF_OFF_PRICE],
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_blue_price",
        name="Consumption Blue Price",
        translation_key="consumption_standard_blue_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        subscription=CONF_TEMPO,
        value_fn=lambda options: options[CONF_AUTH][CONF_PRICINGS][CONF_STD][CONF_BLUE],
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_white_price",
        name="Consumption White Price",
        translation_key="consumption_standard_white_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        subscription=CONF_TEMPO,
        value_fn=lambda options: options[CONF_AUTH][CONF_PRICINGS][CONF_STD][
            CONF_WHITE
        ],
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_red_price",
        name="Consumption Red Price",
        translation_key="consumption_standard_red_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        subscription=CONF_TEMPO,
        value_fn=lambda options: options[CONF_AUTH][CONF_PRICINGS][CONF_STD][CONF_RED],
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_offpeak_blue_price",
        name="Consumption Offpeak Blue Price",
        translation_key="consumption_offpeak_blue_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        subscription=CONF_TEMPO,
        value_fn=lambda options: options[CONF_AUTH][CONF_PRICINGS][CONF_OFFPEAK][
            CONF_BLUE
        ],
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_offpeak_white_price",
        name="Consumption Offpeak White Price",
        translation_key="consumption_offpeak_white_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        subscription=CONF_TEMPO,
        value_fn=lambda options: options[CONF_AUTH][CONF_PRICINGS][CONF_OFFPEAK][
            CONF_WHITE
        ],
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_offpeak_red_price",
        name="Consumption Offpeak Red Price",
        translation_key="consumption_offpeak_red_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        subscription=CONF_TEMPO,
        value_fn=lambda options: options[CONF_AUTH][CONF_PRICINGS][CONF_OFFPEAK][
            CONF_RED
        ],
    ),
    MyElectricalSensorEntityDescription(
        key="production_price",
        name="Production Price",
        translation_key="production_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        requires_production=True,
        value_fn=lambda options: options[CONF_PRODUCTION][CONF_PRICE],
    ),
    MyElectricalSensorEntityDescription(
        key="tempo",
        name="Tempo",
        translation_key="tempo",
        device_class=SensorDeviceClass.ENUM,
        native_unit_of_measurement=None,
        options=TEMPO_OPTIONS,
        cls=lambda coordinator, description: TempoSensor(coordinator, description),
        subscription=CONF_TEMPO,
    ),
    MyElectricalSensorEntityDescription(
        key="ecowatt",
        name="EcoWatt",
        translation_key="ecowatt",
        device_class=SensorDeviceClass.ENUM,
        native_unit_of_measurement=None,
        cls=lambda coordinator, description: EcoWattSensor(coordinator, description),
    ),
    MyElectricalSensorEntityDescription(
        key="last_api_call",
        name="Last API Call",
        translation_key="last_api_call",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: LastApiCallSensor(
            coordinator, description
        ),
    ),
)


def _is_applicable(
    description: MyElectricalSensorEntityDescription,
    coordinator: EnedisDataUpdateCoordinator,
) -> bool:
    """Return whether the sensor is relevant for the account's configuration."""
    if description.key == "ecowatt":
        return coordinator.api._ecowatt_subs  # noqa: SLF001
    if description.key == "last_api_call":
        return True
    if description.requires_production:
        return bool(
            coordinator.config_entry.options.get(CONF_PRODUCTION, {}).get(CONF_SERVICE)
        )
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
        for description in SENSOR_TYPES
        if _is_applicable(description, coordinator)
    ]

    async_add_entities(entities)


class PowerSensor(MyElectricalEntity, SensorEntity):
    """Sensor backed by its own long-term statistics (energy or cost bucket)."""

    entity_description: MyElectricalSensorEntityDescription

    def __init__(self, coordinator, description) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description)
        self._attr_extra_state_attributes = self._build_extra_state_attributes()
        self._attr_native_value = self.coordinator.data[self.unique_id][CONF_SUMMARY]

    def _build_extra_state_attributes(self) -> dict:
        """Return extra state."""
        return {
            "offpeak hours": self.coordinator.api.contract.get("offpeak_hours"),
            "last activation date": self.coordinator.api.contract.get(
                "last_activation_date"
            ),
            "last tariff changedate": self.coordinator.api.contract.get(
                "last_distribution_tariff_change_date"
            ),
        }

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self.coordinator.data[self.unique_id][CONF_SUMMARY]
        super()._handle_coordinator_update()


class PriceSensor(MyElectricalEntity, SensorEntity):
    """Read-only mirror of a tariff set via the config/options flow."""

    entity_description: MyElectricalSensorEntityDescription

    def __init__(self, coordinator, description) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description)
        self._attr_native_value = description.value_fn(coordinator.config_entry.options)


class TempoSensor(MyElectricalEntity, SensorEntity):
    """Sensor return token expiration date."""

    entity_description: MyElectricalSensorEntityDescription

    def __init__(self, coordinator, description) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description)
        self._attr_native_value = coordinator.api.tempo_day

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Return the current tempo day."""
        self._attr_native_value = self.coordinator.api.tempo_day
        super()._handle_coordinator_update()


class LastApiCallSensor(MyElectricalEntity, SensorEntity):
    """Sensor exposing the last call made to the Enedis API."""

    def __init__(self, coordinator, description) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description)
        self._attr_native_value = self._fetch_state()
        self._attr_extra_state_attributes = self._build_extra_state_attributes()

    def _fetch_state(self) -> datetime | None:
        """Return the date of the last call to the API."""
        last_refresh = self.coordinator.api.last_refresh
        if last_refresh and last_refresh.tzinfo is None:
            last_refresh = dt_util.as_utc(last_refresh)
        return last_refresh

    def _build_extra_state_attributes(self) -> dict:
        """Return extra state."""
        access = self.coordinator.api.access
        return {
            "Call number": access.get("call_number"),
            "Banned": access.get("ban"),
            "Quota": access.get("quota_limit"),
            "Quota reached": access.get("quota_reached"),
        }

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self._fetch_state()
        self._attr_extra_state_attributes = self._build_extra_state_attributes()
        super()._handle_coordinator_update()


class EcoWattSensor(MyElectricalEntity, SensorEntity):
    """Sensor return token expiration date."""

    def __init__(self, coordinator, description) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description)
        self._attr_extra_state_attributes = self._build_extra_state_attributes()

    def _build_extra_state_attributes(self) -> dict:
        """Return extra state."""
        return {
            "message": self.coordinator.api.ecowatt_day.get("message"),
            "start_date": self.coordinator.api.ecowatt.get("start_date"),
            "end_date": self.coordinator.api.ecowatt.get("end_date"),
        }

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = DAY_VALUES[
            self.coordinator.api.ecowatt_day.get("value", 0)
        ]
        self._attr_extra_state_attributes = self._build_extra_state_attributes()
        super()._handle_coordinator_update()
