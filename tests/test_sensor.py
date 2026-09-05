"""Tests for custom_components.myelectricaldata.sensor."""

from __future__ import annotations

from datetime import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.util import dt as dt_util
from myelectricaldatapy import ATTR_PRICE, ATTR_PRICES, ATTR_STANDARD, Subscription

from custom_components.myelectricaldata.const import (
    CONF_AUTH,
    CONF_CONSUMPTION,
    CONF_PRODUCTION,
    CONF_SERVICE,
    CONF_SUBSCRIPTION,
    CONF_SUMMARY,
    DEFAULT_CONSUMPTION_TEMPO,
)
from custom_components.myelectricaldata.sensor import (
    DAY_VALUES,
    SENSOR_TYPES,
    EcoWattSensor,
    LastApiCallSensor,
    PowerSensor,
    PriceSensor,
    TempoSensor,
    async_setup_entry,
)

DESCRIPTIONS = {description.key: description for description in SENSOR_TYPES}

CONSUMPTION_OPTIONS = {
    CONF_AUTH: {CONF_SUBSCRIPTION: ATTR_STANDARD},
    CONF_CONSUMPTION: {
        CONF_SERVICE: "daily_consumption",
        ATTR_PRICES: {ATTR_STANDARD: {ATTR_PRICE: 0.174}},
    },
    CONF_PRODUCTION: {
        CONF_SERVICE: "daily_production",
        ATTR_PRICES: {ATTR_STANDARD: {ATTR_PRICE: 0.06}},
    },
}


def _fake_api(**overrides):
    defaults = dict(
        contract=SimpleNamespace(
            offpeak_hours="01H00-06H00",
            last_activation_date="2020-01-01",
            last_distribution_tariff_change_date="2021-01-01",
            subscribed_power="9 kVA",
        ),
        has_ecowatt_subscription=False,
        subscription=Subscription.STANDARD,
        tempo="blue",
        tempo_next="white",
        ecowatt_day=None,
        last_refresh=None,
        last_access=None,
        access=SimpleNamespace(
            call_number=3,
            ban=False,
            quota_limit=10,
            quota_reached=False,
            consent_expiration_date=None,
            valid=True,
        ),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_coordinator(*, api=None, data=None, options=None):
    return SimpleNamespace(
        pdl="12345",
        data=data or {},
        api=api or _fake_api(),
        config_entry=SimpleNamespace(options=options or CONSUMPTION_OPTIONS),
        last_update_success=True,
    )


# ---------------------------------------------------------------------------
# PowerSensor
# ---------------------------------------------------------------------------


def test_power_sensor_reads_summary_from_coordinator_data():
    """A PowerSensor reflects the coordinator's stored summary for its unique_id."""
    description = DESCRIPTIONS["consumption_standard"]
    coordinator = _fake_coordinator(
        data={"12345_consumption_standard": {CONF_SUMMARY: 12.34}}
    )
    sensor = PowerSensor(coordinator, description)

    assert sensor._attr_unique_id == "12345_consumption_standard"
    assert sensor._attr_native_value == 12.34
    assert sensor._attr_device_info["manufacturer"] == "Enedis"
    assert sensor._attr_extra_state_attributes["offpeak hours"] == "01H00-06H00"


def test_power_sensor_handle_coordinator_update_refreshes_value():
    """A coordinator update recomputes the native value from fresh data."""
    description = DESCRIPTIONS["consumption_standard"]
    coordinator = _fake_coordinator(
        data={"12345_consumption_standard": {CONF_SUMMARY: 1.0}}
    )
    sensor = PowerSensor(coordinator, description)

    coordinator.data["12345_consumption_standard"] = {CONF_SUMMARY: 20.0}
    with patch.object(sensor, "async_write_ha_state"):
        sensor._handle_coordinator_update()
    assert sensor._attr_native_value == 20.0


def test_power_sensor_without_contract_has_empty_attributes():
    """No contract on the API yields empty extra state attributes."""
    description = DESCRIPTIONS["consumption_standard"]
    coordinator = _fake_coordinator(api=_fake_api(contract=None))
    sensor = PowerSensor(coordinator, description)
    assert sensor._attr_extra_state_attributes == {}


# ---------------------------------------------------------------------------
# PriceSensor
# ---------------------------------------------------------------------------


def test_price_sensor_mirrors_configured_tariff():
    """A PriceSensor exposes the tariff stored in the config entry options."""
    description = DESCRIPTIONS["consumption_standard_price"]
    coordinator = _fake_coordinator()
    sensor = PriceSensor(coordinator, description)
    assert sensor._attr_native_value == 0.174


def test_production_price_sensor_reads_production_tariff():
    description = DESCRIPTIONS["production_standard_price"]
    coordinator = _fake_coordinator()
    sensor = PriceSensor(coordinator, description)
    assert sensor._attr_native_value == 0.06


# ---------------------------------------------------------------------------
# TempoSensor
# ---------------------------------------------------------------------------


def test_tempo_sensor_initial_and_update():
    """TempoSensor exposes the API tempo day and refreshes on update."""
    description = DESCRIPTIONS["tempo"]
    coordinator = _fake_coordinator()
    sensor = TempoSensor(coordinator, description)

    assert sensor._attr_native_value == "blue"
    assert sensor._attr_extra_state_attributes["next"] == "white"

    coordinator.api.tempo = "red"
    with patch.object(sensor, "async_write_ha_state"):
        sensor._handle_coordinator_update()
    assert sensor._attr_native_value == "red"


# ---------------------------------------------------------------------------
# EcoWattSensor
# ---------------------------------------------------------------------------


def test_ecowatt_sensor_initial_and_update():
    """EcoWattSensor maps the numeric day value to its label and message."""
    description = DESCRIPTIONS["ecowatt"]
    coordinator = _fake_coordinator(
        api=_fake_api(ecowatt_day=SimpleNamespace(value=2, message="be careful"))
    )
    sensor = EcoWattSensor(coordinator, description)

    assert sensor._attr_native_value == DAY_VALUES[2]
    assert sensor._attr_extra_state_attributes["message"] == "be careful"

    coordinator.api.ecowatt_day = SimpleNamespace(value=3, message="critical")
    with patch.object(sensor, "async_write_ha_state"):
        sensor._handle_coordinator_update()
    assert sensor._attr_native_value == DAY_VALUES[3]
    assert sensor._attr_extra_state_attributes["message"] == "critical"


def test_ecowatt_sensor_defaults_to_na_without_day():
    """A missing ecowatt day falls back to the 'na' label."""
    description = DESCRIPTIONS["ecowatt"]
    coordinator = _fake_coordinator(api=_fake_api(ecowatt_day=None))
    sensor = EcoWattSensor(coordinator, description)
    assert sensor._attr_native_value == DAY_VALUES[0]


# ---------------------------------------------------------------------------
# LastApiCallSensor
# ---------------------------------------------------------------------------


def test_last_api_call_sensor_localizes_naive_refresh():
    """A naive last_refresh datetime is coerced to UTC."""
    description = DESCRIPTIONS["last_api_call"]
    naive = dt(2026, 1, 1, 8, 0, 0)  # noqa: DTZ001
    coordinator = _fake_coordinator(api=_fake_api(last_refresh=naive))
    sensor = LastApiCallSensor(coordinator, description)
    assert sensor._attr_native_value == dt_util.as_utc(naive)
    assert sensor._attr_extra_state_attributes["Quota"] == 10


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_standard_subscription_entities():
    """A standard subscription with production yields the standard + production set."""
    coordinator = _fake_coordinator()
    entry = SimpleNamespace(runtime_data=coordinator)
    added: list = []

    await async_setup_entry(None, entry, lambda entities: added.extend(entities))

    keys = {entity.entity_description.key for entity in added}
    assert keys == {
        "consumption_standard",
        "consumption_standard_cost",
        "consumption_standard_price",
        "production_standard",
        "production_standard_cost",
        "production_standard_price",
        "last_api_call",
    }
    assert "ecowatt" not in keys
    assert "tempo" not in keys


async def test_async_setup_entry_includes_ecowatt_when_subscribed():
    """The EcoWatt sensor appears once the API reports an EcoWatt subscription."""
    coordinator = _fake_coordinator(api=_fake_api(has_ecowatt_subscription=True))
    entry = SimpleNamespace(runtime_data=coordinator)
    added: list = []

    await async_setup_entry(None, entry, lambda entities: added.extend(entities))

    assert "ecowatt" in {entity.entity_description.key for entity in added}


async def test_async_setup_entry_tempo_subscription_adds_tempo_sensors():
    """A Tempo subscription swaps in the full/offpeak + colour price sensors."""
    tempo_options = {
        **CONSUMPTION_OPTIONS,
        CONF_CONSUMPTION: {
            CONF_SERVICE: "consumption_load_curve",
            ATTR_PRICES: DEFAULT_CONSUMPTION_TEMPO[ATTR_PRICES],
        },
    }
    coordinator = _fake_coordinator(
        api=_fake_api(subscription=Subscription.TEMPO), options=tempo_options
    )
    entry = SimpleNamespace(runtime_data=coordinator)
    added: list = []

    await async_setup_entry(None, entry, lambda entities: added.extend(entities))

    keys = {entity.entity_description.key for entity in added}
    assert "tempo" in keys
    assert "consumption_full" in keys
    assert "consumption_standard" not in keys


@pytest.mark.parametrize("key", list(DESCRIPTIONS))
def test_every_description_has_a_factory(key):
    """Each sensor description knows how to build its entity."""
    assert callable(DESCRIPTIONS[key].cls)
