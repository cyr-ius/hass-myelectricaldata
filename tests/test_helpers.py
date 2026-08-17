"""Tests for custom_components.myelectricaldata.helpers."""

from __future__ import annotations

from datetime import UTC, timedelta
from datetime import datetime as dt

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    async_import_statistics,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.myelectricaldata.const import (
    CONF_CONSUMPTION,
    CONF_OFFPEAK,
    CONF_PRICE,
    CONF_STD,
    CONSUMPTION_DAILY,
    CONSUMPTION_DETAIL,
    DEFAULT_CC_PRICE,
    DEFAULT_HC_PRICE,
    DOMAIN,
    PRODUCTION_DAILY,
)
from custom_components.myelectricaldata.helpers import (
    _legacy_statistic_id,
    async_get_db_infos,
    async_get_last_infos,
    async_import_sensor_statistics,
    async_migrate_legacy_statistics,
    async_rebuild_statistics,
    build_price_items,
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
    items = build_sensor_items(
        CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], has_price=False
    )
    assert len(items) == 1
    item = items[0]
    assert item["note"] == CONF_STD
    assert item["kind"] == "energy"
    assert item["mode"] == CONF_CONSUMPTION
    assert item["pdl"] == PDL
    assert item["suffix"] == CONF_STD
    assert item["unique_id"] == f"{PDL}_{CONF_CONSUMPTION}_{CONF_STD}"
    assert item["entity_id"].startswith("sensor.")


def test_build_sensor_items_daily_with_price_adds_cost_item():
    """When pricing is configured, a companion cost item is added."""
    items = build_sensor_items(
        CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], has_price=True
    )
    assert len(items) == 2
    kinds = {item["kind"] for item in items}
    assert kinds == {"energy", "cost"}
    cost_item = next(item for item in items if item["kind"] == "cost")
    assert cost_item["unique_id"].endswith("_cost")


def test_build_sensor_items_detail_without_intervals_is_single_bucket():
    """Detail service without offpeak intervals behaves like a daily service."""
    items = build_sensor_items(
        CONF_CONSUMPTION, PDL, CONSUMPTION_DETAIL, [], has_price=False
    )
    assert len(items) == 1
    assert items[0]["note"] == CONF_STD


def test_build_sensor_items_detail_with_intervals_splits_std_offpeak():
    """Detail service with offpeak intervals yields standard + offpeak buckets."""
    items = build_sensor_items(
        CONF_CONSUMPTION,
        PDL,
        CONSUMPTION_DETAIL,
        [("01:00:00", "06:00:00")],
        has_price=True,
    )
    notes = [item["note"] for item in items]
    assert notes.count(CONF_STD) == 2  # energy + cost
    assert notes.count(CONF_OFFPEAK) == 2
    suffixes = {item["suffix"] for item in items}
    assert suffixes == {"full", CONF_OFFPEAK}


# ---------------------------------------------------------------------------
# build_price_items
# ---------------------------------------------------------------------------


def test_build_price_items_daily_single_std_price():
    """Daily consumption without offpeak intervals needs a single price item."""
    items = build_price_items(CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], tempo=False)
    assert len(items) == 1
    assert items[0]["note"] == CONF_STD
    assert items[0]["default"] == DEFAULT_CC_PRICE


def test_build_price_items_detail_with_intervals_std_and_offpeak():
    """Detail service with intervals needs standard and offpeak prices."""
    items = build_price_items(
        CONF_CONSUMPTION,
        PDL,
        CONSUMPTION_DETAIL,
        [("01:00:00", "06:00:00")],
        tempo=False,
    )
    assert {item["note"] for item in items} == {CONF_STD, CONF_OFFPEAK}
    offpeak_item = next(item for item in items if item["note"] == CONF_OFFPEAK)
    assert offpeak_item["default"] == DEFAULT_HC_PRICE


def test_build_price_items_tempo_returns_six_colour_prices():
    """Tempo pricing always needs the six standard/offpeak x colour prices."""
    items = build_price_items(CONF_CONSUMPTION, PDL, CONSUMPTION_DETAIL, [], tempo=True)
    assert len(items) == 6
    assert {item["key"] for item in items} == {"blue", "white", "red"}
    assert {item["note"] for item in items} == {CONF_STD, CONF_OFFPEAK}


def test_build_price_items_production_uses_production_default():
    """Production items default to the production price, not consumption."""
    from custom_components.myelectricaldata.const import (
        CONF_PRODUCTION,
        DEFAULT_PC_PRICE,
    )

    items = build_price_items(CONF_PRODUCTION, PDL, PRODUCTION_DAILY, [], tempo=False)
    assert items[0]["default"] == DEFAULT_PC_PRICE


# ---------------------------------------------------------------------------
# read_prices
# ---------------------------------------------------------------------------


async def test_read_prices_falls_back_to_default_when_no_state(hass):
    """When the number entity has no state yet, the descriptor default is used."""
    items = build_price_items(CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], tempo=False)
    prices = read_prices(hass, items)
    assert prices[CONF_STD][CONF_PRICE] == DEFAULT_CC_PRICE


async def test_read_prices_uses_live_entity_state(hass):
    """When the number entity has a valid numeric state, it takes precedence."""
    items = build_price_items(CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], tempo=False)
    hass.states.async_set(items[0]["entity_id"], "0.25")
    prices = read_prices(hass, items)
    assert prices[CONF_STD][CONF_PRICE] == 0.25


async def test_read_prices_ignores_unavailable_state(hass):
    """An 'unavailable' state should not raise and falls back to default."""
    items = build_price_items(CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], tempo=False)
    hass.states.async_set(items[0]["entity_id"], "unavailable")
    prices = read_prices(hass, items)
    assert prices[CONF_STD][CONF_PRICE] == DEFAULT_CC_PRICE


async def test_read_prices_ignores_non_numeric_state(hass):
    """A non-numeric state should not raise and falls back to default."""
    items = build_price_items(CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], tempo=False)
    hass.states.async_set(items[0]["entity_id"], "not-a-number")
    prices = read_prices(hass, items)
    assert prices[CONF_STD][CONF_PRICE] == DEFAULT_CC_PRICE


# ---------------------------------------------------------------------------
# next_date
# ---------------------------------------------------------------------------


def test_next_date_no_previous_date_daily_service_looks_back_3_years():
    result = next_date(None, CONSUMPTION_DAILY)
    delta = dt.now() - result  # noqa: DTZ005
    assert 1094 <= delta.days <= 1095


def test_next_date_no_previous_date_detail_service_looks_back_7_days():
    result = next_date(None, CONSUMPTION_DETAIL)
    delta = dt.now() - result  # noqa: DTZ005
    assert delta.days == 7


def test_next_date_with_previous_date_daily_adds_one_day():
    previous = dt(2026, 1, 1, 12, 0, 0)
    assert next_date(previous, CONSUMPTION_DAILY) == previous + timedelta(days=1)


def test_next_date_with_previous_date_detail_adds_one_hour():
    previous = dt(2026, 1, 1, 12, 0, 0)
    assert next_date(previous, CONSUMPTION_DETAIL) == previous + timedelta(hours=1)


def test_next_date_strips_tzinfo_from_tz_aware_previous_date():
    previous = dt(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    result = next_date(previous, CONSUMPTION_DAILY)
    assert result.tzinfo is None
    assert result == dt(2026, 1, 2, 12, 0, 0)


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
        hass,
        statistic_id,
        [StatisticData(start=start, state=10, sum=10)],
    )
    summary, last_dt = await async_get_db_infos(hass, statistic_id)
    assert summary == 10
    assert last_dt is not None


async def test_async_get_last_infos_splits_energy_and_cost(recorder_mock, hass):
    """Energy items feed sum_values/last date, cost items feed sum_prices only."""
    items = build_sensor_items(
        CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], has_price=True
    )
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
    items = build_sensor_items(
        CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], has_price=True
    )
    energy_item = next(item for item in items if item["kind"] == "energy")
    cost_item = next(item for item in items if item["kind"] == "cost")
    start = dt_util.utc_from_timestamp(0)

    data_collected = {
        CONF_CONSUMPTION: [
            {
                "notes": CONF_STD,
                "date": start,
                "value": 12.0,
                "sum_value": 12.0,
                "price": 3.0,
                "sum_price": 3.0,
            }
        ]
    }

    await async_import_sensor_statistics(hass, items, data_collected)
    await async_wait_recording_done(hass)

    energy_summary, _ = await async_get_db_infos(hass, energy_item["entity_id"])
    cost_summary, _ = await async_get_db_infos(hass, cost_item["entity_id"])
    assert energy_summary == 12.0
    assert cost_summary == 3.0


async def test_async_import_sensor_statistics_skips_items_without_rows(
    recorder_mock, hass
):
    """Items with no matching data don't raise and simply import nothing."""
    items = build_sensor_items(
        CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], has_price=False
    )
    await async_import_sensor_statistics(hass, items, {})
    await async_wait_recording_done(hass)
    summary, last_dt = await async_get_db_infos(hass, items[0]["entity_id"])
    assert summary == 0
    assert last_dt is None


# ---------------------------------------------------------------------------
# async_migrate_legacy_statistics
# ---------------------------------------------------------------------------


async def test_async_migrate_legacy_statistics_copies_history(recorder_mock, hass):
    """Legacy external statistics get copied onto the new entity id."""
    items = build_sensor_items(
        CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], has_price=False
    )
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
    items = build_sensor_items(
        CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], has_price=False
    )
    item = items[0]
    start = dt_util.utc_from_timestamp(0)
    await _import_metadata(
        hass, item["entity_id"], [StatisticData(start=start, state=99, sum=99)]
    )

    # Should not raise, and should not touch the existing value.
    await async_migrate_legacy_statistics(hass, items)
    await async_wait_recording_done(hass)

    summary, _ = await async_get_db_infos(hass, item["entity_id"])
    assert summary == 99


async def test_async_migrate_legacy_statistics_no_legacy_data_is_noop(
    recorder_mock, hass
):
    """No legacy statistics found: function returns without error."""
    items = build_sensor_items(
        CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], has_price=False
    )
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
    items = build_sensor_items(
        CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], has_price=False
    )
    item = items[0]
    start1 = dt_util.utc_from_timestamp(10 * 86400)
    start2 = dt_util.utc_from_timestamp(10 * 86400 + 3600)

    # Import two hourly rows in one go with an inconsistent cumulative sum
    # (as if two out-of-order backfill chunks had been stitched together).
    await _import_metadata(
        hass,
        item["entity_id"],
        [
            StatisticData(start=start1, state=4, sum=100),
            StatisticData(start=start2, state=6, sum=6),
        ],
    )

    await async_rebuild_statistics(hass, items)
    await async_wait_recording_done(hass)

    summary, _ = await async_get_db_infos(hass, item["entity_id"])
    assert summary == 10  # 4 + 6, recomputed as a clean running total


async def test_async_rebuild_statistics_no_data_is_noop(recorder_mock, hass):
    """Rebuilding an entity with no history at all does not raise."""
    items = build_sensor_items(
        CONF_CONSUMPTION, PDL, CONSUMPTION_DAILY, [], has_price=False
    )
    await async_rebuild_statistics(hass, items)
    await async_wait_recording_done(hass)
