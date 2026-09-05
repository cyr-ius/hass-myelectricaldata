"""Tests for custom_components.myelectricaldata.helpers."""

from __future__ import annotations

from datetime import UTC, timedelta
from datetime import datetime as dt

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    async_import_statistics,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.util import dt as dt_util
from myelectricaldatapy import (
    ATTR_OFFPEAK,
    ATTR_PRICE,
    ATTR_PRICES,
    ATTR_STANDARD,
    DAILY_CONSUM,
    DAILY_PROD,
    DETAIL_CONSUM,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.myelectricaldata.const import (
    CONF_CONSUMPTION,
    CONF_PRODUCTION,
    DEFAULT_CC_PRICE,
    DEFAULT_CONSUMPTION_TEMPO,
    DEFAULT_PC_PRICE,
    DOMAIN,
)
from custom_components.myelectricaldata.helpers import (
    _legacy_statistic_id,
    async_get_db_infos,
    async_get_last_infos,
    async_import_sensor_statistics,
    async_migrate_legacy_statistics,
    async_rebuild_statistics,
    build_sensor_items,
    next_date,
    read_prices,
)

PDL = "12345678901234"


async def _import_metadata(hass, statistic_id: str, rows: list[StatisticData]) -> None:
    """Import statistics onto a statistic_id and wait for the recorder."""
    metadata = {
        "has_sum": True,
        "name": None,
        "source": "recorder",
        "statistic_id": statistic_id,
        "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
        "mean_type": StatisticMeanType.NONE,
        "unit_class": None,
    }
    await get_instance(hass).async_add_executor_job(
        async_import_statistics, hass, metadata, rows
    )
    await async_wait_recording_done(hass)


# ---------------------------------------------------------------------------
# build_sensor_items
# ---------------------------------------------------------------------------


def test_build_sensor_items_daily_no_price():
    """A daily service always yields a single standard energy item."""
    items = build_sensor_items(CONF_CONSUMPTION, PDL, DAILY_CONSUM, [], has_price=False)
    assert len(items) == 1
    item = items[0]
    assert item["note"] == ATTR_STANDARD
    assert item["kind"] == "energy"
    assert item["mode"] == CONF_CONSUMPTION
    assert item["pdl"] == PDL
    assert item["entity_id"].startswith("sensor.")


def test_build_sensor_items_daily_with_price_adds_cost_item():
    """When pricing is configured, a companion cost item is added."""
    items = build_sensor_items(CONF_CONSUMPTION, PDL, DAILY_CONSUM, [], has_price=True)
    assert len(items) == 2
    kinds = {item["kind"] for item in items}
    assert kinds == {"energy", "cost"}
    cost_item = next(item for item in items if item["kind"] == "cost")
    assert cost_item["unique_id"].endswith("_cost")


def test_build_sensor_items_detail_without_intervals_is_single_bucket():
    """Detail service without offpeak intervals behaves like a daily service."""
    items = build_sensor_items(
        CONF_CONSUMPTION, PDL, DETAIL_CONSUM, [], has_price=False
    )
    assert len(items) == 1
    assert items[0]["note"] == ATTR_STANDARD


def test_build_sensor_items_detail_with_intervals_splits_std_offpeak():
    """Detail service with offpeak intervals yields standard + offpeak buckets."""
    items = build_sensor_items(
        CONF_CONSUMPTION,
        PDL,
        DETAIL_CONSUM,
        [("01:00:00", "06:00:00")],
        has_price=True,
    )
    notes = [item["note"] for item in items]
    assert notes.count(ATTR_STANDARD) == 2  # energy + cost
    assert notes.count(ATTR_OFFPEAK) == 2
    prefix = f"{PDL}_{CONF_CONSUMPTION}_"
    suffixes = {item["unique_id"].removeprefix(prefix) for item in items}
    assert suffixes == {"full", "full_cost", "offpeak", "offpeak_cost"}


# ---------------------------------------------------------------------------
# read_prices
# ---------------------------------------------------------------------------


def test_read_prices_production_defaults_to_production_price():
    """Production without a stored tariff falls back to the production default."""
    prices = read_prices({}, CONF_PRODUCTION, DAILY_PROD, [], tempo=False)
    assert prices[ATTR_STANDARD][ATTR_PRICE] == DEFAULT_PC_PRICE


def test_read_prices_production_uses_stored_tariff():
    """A stored production tariff takes precedence over the default."""
    options = {CONF_PRODUCTION: {ATTR_PRICES: {ATTR_STANDARD: {ATTR_PRICE: 0.1}}}}
    prices = read_prices(options, CONF_PRODUCTION, DAILY_PROD, [], tempo=False)
    assert prices[ATTR_STANDARD][ATTR_PRICE] == 0.1


def test_read_prices_consumption_daily_single_standard_default():
    """Daily consumption without a stored tariff yields a single standard price."""
    prices = read_prices({}, CONF_CONSUMPTION, DAILY_CONSUM, [], tempo=False)
    assert prices == {ATTR_STANDARD: {ATTR_PRICE: DEFAULT_CC_PRICE}}


def test_read_prices_consumption_tempo_returns_colour_prices():
    """Tempo consumption returns the six standard/offpeak colour prices."""
    prices = read_prices({}, CONF_CONSUMPTION, DETAIL_CONSUM, [], tempo=True)
    assert prices == DEFAULT_CONSUMPTION_TEMPO[ATTR_PRICES]


def test_read_prices_consumption_detail_with_intervals_keeps_offpeak():
    """Detail consumption with intervals keeps both standard and offpeak buckets."""
    options = {
        CONF_CONSUMPTION: {
            ATTR_PRICES: {
                ATTR_STANDARD: {ATTR_PRICE: 0.2},
                ATTR_OFFPEAK: {ATTR_PRICE: 0.15},
            }
        }
    }
    prices = read_prices(
        options,
        CONF_CONSUMPTION,
        DETAIL_CONSUM,
        [("01:00:00", "06:00:00")],
        tempo=False,
    )
    assert prices == {
        ATTR_STANDARD: {ATTR_PRICE: 0.2},
        ATTR_OFFPEAK: {ATTR_PRICE: 0.15},
    }


def test_read_prices_consumption_detail_without_offpeak_price_is_single_bucket():
    """Without an offpeak price stored, only the standard bucket is returned."""
    options = {CONF_CONSUMPTION: {ATTR_PRICES: {ATTR_STANDARD: {ATTR_PRICE: 0.2}}}}
    prices = read_prices(
        options,
        CONF_CONSUMPTION,
        DETAIL_CONSUM,
        [("01:00:00", "06:00:00")],
        tempo=False,
    )
    assert prices == {ATTR_STANDARD: {ATTR_PRICE: 0.2}}


# ---------------------------------------------------------------------------
# next_date
# ---------------------------------------------------------------------------


def test_next_date_no_previous_date_daily_service_looks_back_3_years():
    result = next_date(None, DAILY_CONSUM)
    delta = dt.now() - result  # noqa: DTZ005
    assert 1094 <= delta.days <= 1095


def test_next_date_no_previous_date_detail_service_looks_back_7_days():
    result = next_date(None, DETAIL_CONSUM)
    delta = dt.now() - result  # noqa: DTZ005
    assert delta.days == 7


def test_next_date_with_previous_date_daily_adds_one_day():
    previous = dt(2026, 1, 1, 12, 0, 0)  # noqa: DTZ001
    assert next_date(previous, DAILY_CONSUM) == previous + timedelta(days=1)


def test_next_date_with_previous_date_detail_adds_one_hour():
    previous = dt(2026, 1, 1, 12, 0, 0)  # noqa: DTZ001
    assert next_date(previous, DETAIL_CONSUM) == previous + timedelta(hours=1)


def test_next_date_strips_tzinfo_from_tz_aware_previous_date():
    previous = dt(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    result = next_date(previous, DAILY_CONSUM)
    assert result.tzinfo is None
    assert result == dt(2026, 1, 2, 12, 0, 0)  # noqa: DTZ001


# ---------------------------------------------------------------------------
# async_get_db_infos / async_get_last_infos
# ---------------------------------------------------------------------------


async def test_async_get_db_infos_no_data_returns_zero(recorder_mock, hass):
    """With no statistics recorded yet, summary is 0 and date is None."""
    summary, last_dt = await async_get_db_infos(hass, f"sensor.{DOMAIN}_missing")
    assert summary == 0
    assert last_dt is None


async def test_async_get_db_infos_returns_last_sum(recorder_mock, hass):
    """After importing statistics, the last sum and date are returned."""
    statistic_id = f"sensor.{DOMAIN}_{PDL}_consumption_full"
    start = dt_util.utc_from_timestamp(0)
    await _import_metadata(
        hass, statistic_id, [StatisticData(start=start, state=10, sum=10)]
    )
    summary, last_dt = await async_get_db_infos(hass, statistic_id)
    assert summary == 10
    assert last_dt is not None


async def test_async_get_last_infos_splits_energy_and_cost(recorder_mock, hass):
    """Energy items feed sum_values/last date, cost items feed sum_prices only."""
    items = build_sensor_items(CONF_CONSUMPTION, PDL, DAILY_CONSUM, [], has_price=True)
    energy_item = next(item for item in items if item["kind"] == "energy")
    cost_item = next(item for item in items if item["kind"] == "cost")

    start = dt_util.utc_from_timestamp(0)
    await _import_metadata(
        hass, energy_item["entity_id"], [StatisticData(start=start, state=5, sum=5)]
    )
    await _import_metadata(
        hass, cost_item["entity_id"], [StatisticData(start=start, state=2, sum=2)]
    )

    dt_last, sum_values, sum_prices = await async_get_last_infos(hass, items)
    assert dt_last is not None
    assert sum_values[energy_item["note"]] == 5
    assert sum_prices[cost_item["note"]] == 2


# ---------------------------------------------------------------------------
# async_import_sensor_statistics
# ---------------------------------------------------------------------------


async def test_async_import_sensor_statistics_writes_energy_and_cost(
    recorder_mock, hass
):
    """Rows matching an item's mode/note/kind get imported onto its entity."""
    items = build_sensor_items(CONF_CONSUMPTION, PDL, DAILY_CONSUM, [], has_price=True)
    energy_item = next(item for item in items if item["kind"] == "energy")
    cost_item = next(item for item in items if item["kind"] == "cost")
    start = dt_util.utc_from_timestamp(0)

    data_collected = {
        CONF_CONSUMPTION: [
            {
                "notes": ATTR_STANDARD,
                "date": start,
                "value": 12.0,
                "sum_value": 12.0,
                "price": 3.0,
                "sum_price": 3.0,
            }
        ]
    }

    last_stats = await async_import_sensor_statistics(hass, items, data_collected)
    await async_wait_recording_done(hass)

    assert last_stats[energy_item["entity_id"]][0] == 12.0
    assert last_stats[cost_item["entity_id"]][0] == 3.0
    energy_summary, _ = await async_get_db_infos(hass, energy_item["entity_id"])
    cost_summary, _ = await async_get_db_infos(hass, cost_item["entity_id"])
    assert energy_summary == 12.0
    assert cost_summary == 3.0


async def test_async_import_sensor_statistics_skips_items_without_rows(
    recorder_mock, hass
):
    """Items with no matching data don't raise and simply import nothing."""
    items = build_sensor_items(CONF_CONSUMPTION, PDL, DAILY_CONSUM, [], has_price=False)
    last_stats = await async_import_sensor_statistics(hass, items, {})
    await async_wait_recording_done(hass)
    assert last_stats == {}
    summary, last_dt = await async_get_db_infos(hass, items[0]["entity_id"])
    assert summary == 0
    assert last_dt is None


# ---------------------------------------------------------------------------
# async_migrate_legacy_statistics
# ---------------------------------------------------------------------------


async def test_async_migrate_legacy_statistics_copies_history(recorder_mock, hass):
    """Legacy external statistics get copied onto the new entity id."""
    items = build_sensor_items(CONF_CONSUMPTION, PDL, DAILY_CONSUM, [], has_price=False)
    item = items[0]
    legacy_id = _legacy_statistic_id(item)

    start = dt_util.utc_from_timestamp(10 * 86400)
    metadata = {
        "has_sum": True,
        "name": None,
        "source": DOMAIN,
        "statistic_id": legacy_id,
        "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
        "mean_type": StatisticMeanType.NONE,
        "unit_class": None,
    }
    await get_instance(hass).async_add_executor_job(
        async_add_external_statistics,
        hass,
        metadata,
        [StatisticData(start=start, state=7, sum=7)],
    )
    await async_wait_recording_done(hass)

    await async_migrate_legacy_statistics(hass, items)
    await async_wait_recording_done(hass)

    summary, last_dt = await async_get_db_infos(hass, item["entity_id"])
    assert summary == 7
    assert last_dt is not None


async def test_async_migrate_legacy_statistics_skips_when_already_populated(
    recorder_mock, hass
):
    """If the new entity already has data, nothing is migrated."""
    items = build_sensor_items(CONF_CONSUMPTION, PDL, DAILY_CONSUM, [], has_price=False)
    item = items[0]
    start = dt_util.utc_from_timestamp(0)
    await _import_metadata(
        hass, item["entity_id"], [StatisticData(start=start, state=99, sum=99)]
    )

    await async_migrate_legacy_statistics(hass, items)
    await async_wait_recording_done(hass)

    summary, _ = await async_get_db_infos(hass, item["entity_id"])
    assert summary == 99


async def test_async_migrate_legacy_statistics_no_legacy_data_is_noop(
    recorder_mock, hass
):
    """No legacy statistics found: function returns without error."""
    items = build_sensor_items(CONF_CONSUMPTION, PDL, DAILY_CONSUM, [], has_price=False)
    await async_migrate_legacy_statistics(hass, items)
    await async_wait_recording_done(hass)
    summary, last_dt = await async_get_db_infos(hass, items[0]["entity_id"])
    assert summary == 0
    assert last_dt is None


# ---------------------------------------------------------------------------
# async_rebuild_statistics
# ---------------------------------------------------------------------------


async def test_async_rebuild_statistics_recomputes_running_total(recorder_mock, hass):
    """Rebuild replaces the stored sum with a clean running total from state."""
    items = build_sensor_items(CONF_CONSUMPTION, PDL, DAILY_CONSUM, [], has_price=False)
    item = items[0]
    start1 = dt_util.utc_from_timestamp(10 * 86400)
    start2 = dt_util.utc_from_timestamp(10 * 86400 + 3600)

    await _import_metadata(
        hass,
        item["entity_id"],
        [
            StatisticData(start=start1, state=4, sum=100),
            StatisticData(start=start2, state=6, sum=6),
        ],
    )

    final_sums = await async_rebuild_statistics(hass, items)
    await async_wait_recording_done(hass)

    assert final_sums[item["entity_id"]] == 10
    summary, _ = await async_get_db_infos(hass, item["entity_id"])
    assert summary == 10  # 4 + 6, recomputed as a clean running total


async def test_async_rebuild_statistics_no_data_is_noop(recorder_mock, hass):
    """Rebuilding an entity with no history at all does not raise."""
    items = build_sensor_items(CONF_CONSUMPTION, PDL, DAILY_CONSUM, [], has_price=False)
    final_sums = await async_rebuild_statistics(hass, items)
    await async_wait_recording_done(hass)
    assert final_sums == {}


# ---------------------------------------------------------------------------
# _legacy_statistic_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "suffix"),
    [("energy", ""), ("cost", "_cost")],
)
def test_legacy_statistic_id_shape(kind, suffix):
    """The legacy id keeps the pre-2.4 ``domain:pdl_mode_note`` shape."""
    item = {"pdl": PDL, "mode": CONF_CONSUMPTION, "note": ATTR_STANDARD, "kind": kind}
    legacy_id = _legacy_statistic_id(item)
    assert legacy_id.startswith(f"{DOMAIN}:")
    assert legacy_id.endswith(suffix or "standard")
