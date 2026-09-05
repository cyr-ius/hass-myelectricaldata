"""Sensor for power energy."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, override

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
from myelectricaldatapy import (
    ATTR_OFFPEAK,
    ATTR_PRICE,
    ATTR_PRICES,
    ATTR_STANDARD,
    TEMPO_B,
    TEMPO_DAYS,
    TEMPO_R,
    TEMPO_W,
    Subscription,
)

from . import MyElectricalDataConfigEntry
from .const import (
    CONF_CONSUMPTION,
    CONF_GREEN,
    CONF_NA,
    CONF_ORANGE,
    CONF_PRODUCTION,
    CONF_RED,
    CONF_SERVICE,
    CONF_SUMMARY,
)
from .coordinator import EnedisDataUpdateCoordinator
from .entity import MyElectricalEntity

DAY_VALUES = (CONF_NA, CONF_GREEN, CONF_ORANGE, CONF_RED)
TEMPO_DAYS = list(TEMPO_DAYS)
EUR = "EUR"
EUR_PER_KWH = "EUR/kWh"
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class MyElectricalSensorEntityDescription(SensorEntityDescription):
    """Describes MyElectricalData sensor entity."""

    cls: Callable[
        [EnedisDataUpdateCoordinator, "MyElectricalSensorEntityDescription"],
        SensorEntity,
    ]
    subscriptions: Subscription | None = None
    requires_production: bool = False
    value_fn: Callable[[dict[str, Any]], float] | None = None


SENSOR_TYPES: Final[tuple[MyElectricalSensorEntityDescription, ...]] = (
    MyElectricalSensorEntityDescription(
        key="consumption_standard",
        name="Consumption",
        translation_key="consumption_standard",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
        subscriptions=Subscription.STANDARD,
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_standard_cost",
        name="Consumption Cost",
        translation_key="consumption_standard_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=EUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
        subscriptions=Subscription.STANDARD,
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_standard_price",
        name="Consumption Price",
        translation_key="consumption_standard_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        value_fn=lambda options: options[CONF_CONSUMPTION][ATTR_PRICES][ATTR_STANDARD][
            ATTR_PRICE
        ],
        subscriptions=Subscription.STANDARD,
    ),
    MyElectricalSensorEntityDescription(
        key="production_standard",
        name="Production",
        translation_key="production",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
        requires_production=True,
    ),
    MyElectricalSensorEntityDescription(
        key="production_standard_cost",
        name="Production Cost",
        translation_key="production_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=EUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
        requires_production=True,
    ),
    MyElectricalSensorEntityDescription(
        key="production_standard_price",
        name="Production Price",
        translation_key="production_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        requires_production=True,
        value_fn=lambda options: options[CONF_PRODUCTION][ATTR_PRICES][ATTR_STANDARD][
            ATTR_PRICE
        ],
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_full",
        name="Consumption",
        translation_key="consumption_full",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
        subscriptions=Subscription.HPHC | Subscription.TEMPO,
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_full_cost",
        name="Consumption Cost",
        translation_key="consumption_full_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=EUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
        subscriptions=Subscription.HPHC | Subscription.TEMPO,
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_full_price",
        name="Consumption Price",
        translation_key="consumption_full_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        value_fn=lambda options: options[CONF_CONSUMPTION][ATTR_PRICES][ATTR_STANDARD][
            ATTR_PRICE
        ],
        subscriptions=Subscription.HPHC,
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_offpeak",
        name="Consumption Offpeak",
        translation_key="consumption_offpeak",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
        subscriptions=Subscription.HPHC | Subscription.TEMPO,
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_offpeak_cost",
        name="Consumption Offpeak Cost",
        translation_key="consumption_offpeak_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=EUR,
        cls=lambda coordinator, description: PowerSensor(coordinator, description),
        subscriptions=Subscription.HPHC | Subscription.TEMPO,
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_offpeak_price",
        name="Consumption Offpeak Price",
        translation_key="consumption_offpeak_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        value_fn=lambda options: options[CONF_CONSUMPTION][ATTR_PRICES][ATTR_OFFPEAK][
            ATTR_PRICE
        ],
        subscriptions=Subscription.HPHC,
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_blue_price",
        name="Consumption Blue Price",
        translation_key="consumption_blue_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        subscriptions=Subscription.TEMPO,
        value_fn=lambda options: options[CONF_CONSUMPTION][ATTR_PRICES][ATTR_STANDARD][
            TEMPO_B
        ],
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_white_price",
        name="Consumption White Price",
        translation_key="consumption_white_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        subscriptions=Subscription.TEMPO,
        value_fn=lambda options: options[CONF_CONSUMPTION][ATTR_PRICES][ATTR_STANDARD][
            TEMPO_W
        ],
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_red_price",
        name="Consumption Red Price",
        translation_key="consumption_red_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        subscriptions=Subscription.TEMPO,
        value_fn=lambda options: options[CONF_CONSUMPTION][ATTR_PRICES][ATTR_STANDARD][
            TEMPO_R
        ],
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_offpeak_blue_price",
        name="Consumption Offpeak Blue Price",
        translation_key="consumption_offpeak_blue_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        subscriptions=Subscription.TEMPO,
        value_fn=lambda options: options[CONF_CONSUMPTION][ATTR_PRICES][ATTR_OFFPEAK][
            TEMPO_B
        ],
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_offpeak_white_price",
        name="Consumption Offpeak White Price",
        translation_key="consumption_offpeak_white_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        subscriptions=Subscription.TEMPO,
        value_fn=lambda options: options[CONF_CONSUMPTION][ATTR_PRICES][ATTR_OFFPEAK][
            TEMPO_W
        ],
    ),
    MyElectricalSensorEntityDescription(
        key="consumption_offpeak_red_price",
        name="Consumption Offpeak Red Price",
        translation_key="consumption_offpeak_red_price",
        native_unit_of_measurement=EUR_PER_KWH,
        entity_category=EntityCategory.DIAGNOSTIC,
        cls=lambda coordinator, description: PriceSensor(coordinator, description),
        subscriptions=Subscription.TEMPO,
        value_fn=lambda options: options[CONF_CONSUMPTION][ATTR_PRICES][ATTR_OFFPEAK][
            TEMPO_R
        ],
    ),
    MyElectricalSensorEntityDescription(
        key="tempo",
        name="Tempo",
        translation_key="tempo",
        device_class=SensorDeviceClass.ENUM,
        native_unit_of_measurement=None,
        options=TEMPO_DAYS,
        cls=lambda coordinator, description: TempoSensor(coordinator, description),
        subscriptions=Subscription.TEMPO,
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
    options = coordinator.config_entry.options
    api = coordinator.api

    # Ecowatt is a standalone signal, gated only on its own subscription.
    if description.key == "ecowatt":
        return api.has_ecowatt_subscription

    # Production sensors exist only once a production service is configured.
    if description.requires_production:
        return bool(options.get(CONF_PRODUCTION, {}).get(CONF_SERVICE))

    # Sensors with no subscription constraint always apply (e.g. last_api_call).
    if description.subscriptions is None:
        return True

    # HP/HC and Tempo are pinned to the load curve by the config flow, so the
    # subscription type alone decides which consumption sensors are relevant.
    applicable = api.subscription in description.subscriptions
    _LOGGER.debug("Sensor: %s, is_applicable: %s", description.key, applicable)
    return applicable


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
        self._attr_native_value = self.coordinator.data.get(self.unique_id, {}).get(
            CONF_SUMMARY
        )

    def _build_extra_state_attributes(self) -> dict:
        """Return extra state."""
        contract = self.coordinator.api.contract
        return (
            {
                "offpeak hours": contract.offpeak_hours,
                "last activation date": contract.last_activation_date,
                "last tariff changedate": contract.last_distribution_tariff_change_date,
            }
            if contract is not None
            else {}
        )

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self.coordinator.data.get(self.unique_id, {}).get(
            CONF_SUMMARY
        )
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
        self._attr_native_value = coordinator.api.tempo
        self._attr_extra_state_attributes = {"next": coordinator.api.tempo_next}

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Return the current tempo day."""
        self._attr_native_value = self.coordinator.api.tempo
        self._attr_extra_state_attributes = {"next": self.coordinator.api.tempo_next}

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
        return (
            {
                "Call number": access.call_number,
                "Banned": access.ban,
                "Quota": access.quota_limit,
                "Quota reached": access.quota_reached,
                "Last access": self.coordinator.api.last_access,
            }
            if access is not None
            else {}
        )

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

        idx_ecowatt = (
            self.coordinator.api.ecowatt_day.value
            if self.coordinator.api.ecowatt_day is not None
            else 0
        )
        self._attr_native_value = DAY_VALUES[idx_ecowatt]

    def _build_extra_state_attributes(self) -> dict:
        """Return extra state."""
        ecowatt_day = self.coordinator.api.ecowatt_day
        return (
            {
                "message": ecowatt_day.message,
                # "start_date": ecowatt.start_date,
                # "end_date":ecowatt.end_date,
            }
            if ecowatt_day is not None
            else {}
        )

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        idx_ecowatt = (
            self.coordinator.api.ecowatt_day.value
            if self.coordinator.api.ecowatt_day is not None
            else 0
        )

        self._attr_native_value = DAY_VALUES[idx_ecowatt]

        self._attr_extra_state_attributes = self._build_extra_state_attributes()
        super()._handle_coordinator_update()
