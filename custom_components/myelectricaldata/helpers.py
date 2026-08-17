"""Helpers functions for MyElectricalData."""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime as dt
from datetime import timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify
from homeassistant.util.unit_conversion import EnergyConverter

from .const import (
    CONF_BLUE,
    CONF_CONSUMPTION,
    CONF_OFFPEAK,
    CONF_PRICE,
    CONF_PRICINGS,
    CONF_RED,
    CONF_STD,
    CONF_WHITE,
    CONSUMPTION_DAILY,
    CONSUMPTION_DETAIL,
    DEFAULT_CC_PRICE,
    DEFAULT_CONSUMPTION_TEMPO,
    DEFAULT_HC_PRICE,
    DEFAULT_PC_PRICE,
    DOMAIN,
    PRODUCTION_DAILY,
    PRODUCTION_DETAIL,
)

_LOGGER = logging.getLogger(__name__)


async def async_get_db_infos(hass: HomeAssistant, statistic_id: str) -> tuple[str, dt]:
    """Fetch last information in database."""
    last_stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
    )
    last_summary, dt_last_stat = (
        (0, None)
        if not last_stats
        else (
            last_stats[statistic_id][0]["sum"],
            dt_util.as_local(
                dt_util.utc_from_timestamp(last_stats[statistic_id][0]["start"])
            ),
        )
    )
    _LOGGER.debug(
        "[%s] summary: %s, last date: %s", statistic_id, last_summary, dt_last_stat
    )
    return (last_summary, dt_last_stat)


async def async_get_last_infos(
    hass: HomeAssistant, items: list[dict[str, Any]]
) -> tuple[dt, dict[str, float], dict[str, float]]:
    """Set default api."""
    sum_values: dict[str, float] = {}
    sum_prices: dict[str, float] = {}
    _dt_last: dt | None = None
    for item in items:
        summary, dt_last = await async_get_db_infos(hass, item["entity_id"])
        if item["kind"] == "energy":
            sum_values[item["note"]] = float(summary)
            _dt_last = dt_last if _dt_last is None else _dt_last
        else:
            sum_prices[item["note"]] = float(summary)

    _LOGGER.debug(
        "[infosdb] last date: %s, sum value: %s, sum price: %s",
        _dt_last,
        sum_values,
        sum_prices,
    )
    return _dt_last, sum_values, sum_prices


def build_sensor_items(
    mode: str, pdl: str, service: str, intervals: list[Any], has_price: bool
) -> list[dict[str, Any]]:
    """Return one sensor descriptor per tariff bucket actually collected.

    A single "standard" bucket is used for daily (aggregated) services, since
    Enedis doesn't expose sub-daily granularity to split by offpeak hours.
    Detail (load curve) services get a "standard"/"offpeak" pair as soon as
    offpeak intervals are configured, so each bucket can be added as its own
    consumption source in the Energy dashboard and summed back into a day.
    A "cost" companion item is added next to each energy bucket when pricing
    is configured.
    """
    is_detail = service in (CONSUMPTION_DETAIL, PRODUCTION_DETAIL)
    notes = [CONF_STD, CONF_OFFPEAK] if is_detail and intervals else [CONF_STD]

    items: list[dict[str, Any]] = []
    for note in notes:
        suffix = note if len(notes) == 1 else ("full" if note == CONF_STD else note)
        unique_id = f"{pdl}_{mode}_{suffix}"
        name = f"{pdl} {mode} {suffix}".capitalize()
        items.append(
            {
                "unique_id": unique_id,
                "entity_id": f"sensor.{slugify(f'{DOMAIN}_{unique_id}')}",
                "name": name,
                "friendly_name": f"{mode} {suffix}",
                "note": note,
                "mode": mode,
                "kind": "energy",
                "pdl": pdl,
                "suffix": suffix,
            }
        )
        if has_price:
            cost_unique_id = f"{unique_id}_cost"
            items.append(
                {
                    "unique_id": cost_unique_id,
                    "entity_id": f"sensor.{slugify(f'{DOMAIN}_{cost_unique_id}')}",
                    "name": f"{name} cost",
                    "friendly_name": f"{mode} {suffix} cost",
                    "note": note,
                    "mode": mode,
                    "kind": "cost",
                    "pdl": pdl,
                    "suffix": suffix,
                }
            )
    _LOGGER.debug("[items] %s", items)
    return items


def build_price_items(
    mode: str, pdl: str, service: str, intervals: list[Any], tempo: bool
) -> list[dict[str, Any]]:
    """Return one editable-tariff (number entity) descriptor per price needed.

    Mirrors build_sensor_items' bucket cardinality: a single price for daily
    services or detail services without offpeak intervals, a standard/offpeak
    pair once offpeak intervals are configured, or the six Tempo colour
    prices when Tempo is enabled (Tempo always needs both buckets).
    """
    is_detail = service in (CONSUMPTION_DETAIL, PRODUCTION_DETAIL)
    has_offpeak = is_detail and bool(intervals)

    items: list[dict[str, Any]] = []
    if tempo:
        tempo_defaults = DEFAULT_CONSUMPTION_TEMPO[CONF_PRICINGS]
        for note in (CONF_STD, CONF_OFFPEAK):
            for color in (CONF_BLUE, CONF_WHITE, CONF_RED):
                unique_id = f"{pdl}_{mode}_{note}_{color}"
                items.append(
                    {
                        "unique_id": unique_id,
                        "entity_id": f"number.{slugify(f'{DOMAIN}_{unique_id}')}",
                        "name": f"{mode} {note} {color}".capitalize(),
                        "mode": mode,
                        "note": note,
                        "key": color,
                        "default": tempo_defaults[note][color],
                    }
                )
        return items

    default_std = DEFAULT_CC_PRICE if mode == CONF_CONSUMPTION else DEFAULT_PC_PRICE
    notes = [CONF_STD, CONF_OFFPEAK] if has_offpeak else [CONF_STD]
    for note in notes:
        unique_id = f"{pdl}_{mode}_{note}_{CONF_PRICE}"
        items.append(
            {
                "unique_id": unique_id,
                "entity_id": f"number.{slugify(f'{DOMAIN}_{unique_id}')}",
                "name": f"{mode} {note} price".capitalize(),
                "mode": mode,
                "note": note,
                "key": CONF_PRICE,
                "default": default_std if note == CONF_STD else DEFAULT_HC_PRICE,
            }
        )
    return items


def read_prices(hass: HomeAssistant, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Read the live value of each tariff number entity.

    Falls back to the descriptor's default when the entity isn't available
    yet (e.g. the very first coordinator refresh, before platforms load).
    """
    prices: dict[str, Any] = {}
    for item in items:
        state = hass.states.get(item["entity_id"])
        value = item["default"]
        if state is not None and state.state not in (None, "unknown", "unavailable"):
            with contextlib.suppress(ValueError):
                value = float(state.state)
        prices.setdefault(item["note"], {})[item["key"]] = value
    _LOGGER.debug("[prices] %s", prices)
    return prices


async def async_import_sensor_statistics(
    hass: HomeAssistant,
    items: list[dict[str, Any]],
    data_collected: dict[str, Any],
) -> None:
    """Import statistics directly onto their own real sensor entity."""
    for item in items:
        rows: list[StatisticData] = []
        for data in data_collected.get(item["mode"], []):
            if data["notes"] != item["note"]:
                continue
            if item["kind"] == "energy" and data.get("value"):
                rows.append(
                    StatisticData(
                        start=data["date"], state=data["value"], sum=data["sum_value"]
                    )
                )
            elif item["kind"] == "cost" and data.get("price"):
                rows.append(
                    StatisticData(
                        start=data["date"], state=data["price"], sum=data["sum_price"]
                    )
                )

        if not rows:
            continue

        _LOGGER.debug("[import_stats] %s -> %s rows", item["entity_id"], len(rows))
        is_energy = item["kind"] == "energy"
        metadata = StatisticMetaData(
            has_sum=True,
            name=None,
            source="recorder",
            statistic_id=item["entity_id"],
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR if is_energy else "EUR",
            mean_type=StatisticMeanType.NONE,
            unit_class=EnergyConverter.UNIT_CLASS if is_energy else None,
        )
        await get_instance(hass).async_add_executor_job(
            async_import_statistics, hass, metadata, rows
        )


async def async_reassert_statistics(
    hass: HomeAssistant, known_sums: dict[str, tuple[dt | None, float, str]]
) -> None:
    """Overwrite the current hour's statistic with our own last known-good sum.

    Each PowerSensor's statistic_id is its own entity_id (source="recorder"),
    so HA's built-in sensor/recorder.py compiler also generates "sum"
    statistics for it from its live state history, independently of and
    racing with async_import_sensor_statistics. Called right after
    EVENT_RECORDER_HOURLY_STATISTICS_GENERATED, this re-writes the current
    hour with the value we actually imported, undoing whatever the native
    compiler just wrote for it - unless we ourselves already wrote real data
    for this exact hour this cycle, in which case there's nothing to correct
    and doing so would clobber that real per-period state.
    """
    instance = get_instance(hass)
    hour_start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    for entity_id, (last_real_dt, last_real_sum, kind) in known_sums.items():
        if last_real_dt is not None and dt_util.as_utc(last_real_dt) >= hour_start:
            continue  # this hour was legitimately written by us this cycle

        is_energy = kind == "energy"
        metadata = StatisticMetaData(
            has_sum=True,
            name=None,
            source="recorder",
            statistic_id=entity_id,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR if is_energy else "EUR",
            mean_type=StatisticMeanType.NONE,
            unit_class=EnergyConverter.UNIT_CLASS if is_energy else None,
        )
        rows = [StatisticData(start=hour_start, state=0, sum=last_real_sum)]
        await instance.async_add_executor_job(
            async_import_statistics, hass, metadata, rows
        )


def _legacy_statistic_id(item: dict[str, Any]) -> str:
    """Reproduce the pre-2.4 external statistic_id naming (myelectricaldata:...).

    Kept only so async_migrate_legacy_statistics can locate and copy the
    history collected before statistics moved onto real sensor entities.
    """
    name = f"{item['pdl']} {item['mode']} {item['suffix']}".capitalize()
    legacy_id = f"{DOMAIN}:" + slugify(name.lower())
    if item["kind"] == "cost":
        legacy_id = f"{legacy_id}_cost"
    return legacy_id


async def async_migrate_legacy_statistics(
    hass: HomeAssistant, items: list[dict[str, Any]]
) -> None:
    """One-time copy of the old external statistics onto their new entity.

    Runs once per coordinator lifetime (see EnedisDataUpdateCoordinator).
    Purely a local database copy, no Enedis API call involved, so upgrading
    doesn't strand years of already-collected history behind a 7-day cold
    start just because the statistic_id moved onto a real sensor entity.
    """
    instance = get_instance(hass)
    migrated_items: list[dict[str, Any]] = []
    for item in items:
        new_id = item["entity_id"]
        if (await async_get_db_infos(hass, new_id))[1] is not None:
            continue  # already has data, nothing to migrate

        legacy_id = _legacy_statistic_id(item)
        result = await instance.async_add_executor_job(
            statistics_during_period,
            hass,
            dt_util.utc_from_timestamp(0),
            None,
            {legacy_id},
            "hour",
            None,
            {"state", "sum"},
        )
        values = result.get(legacy_id)
        if not values:
            continue

        rows = [
            StatisticData(
                start=dt_util.utc_from_timestamp(value["start"]),
                state=value["state"],
                sum=value["sum"],
            )
            for value in values
        ]
        is_energy = item["kind"] == "energy"
        metadata = StatisticMetaData(
            has_sum=True,
            name=None,
            source="recorder",
            statistic_id=new_id,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR if is_energy else "EUR",
            mean_type=StatisticMeanType.NONE,
            unit_class=EnergyConverter.UNIT_CLASS if is_energy else None,
        )
        await instance.async_add_executor_job(
            async_import_statistics, hass, metadata, rows
        )
        _LOGGER.info(
            "Migrated %s historical points from %s to %s", len(rows), legacy_id, new_id
        )
        migrated_items.append(item)

    if migrated_items:
        # The legacy sum was copied verbatim and may already carry a
        # discontinuity from a pre-refactor manual backfill that was never
        # rebuilt (see async_rebuild_statistics). Recomputing it from the
        # just-imported state values (same local, API-free operation)
        # prevents that stale discontinuity from becoming a phantom spike
        # in the Energy dashboard on the new entity.
        await async_rebuild_statistics(hass, migrated_items)


async def async_rebuild_statistics(
    hass: HomeAssistant, items: list[dict[str, Any]]
) -> None:
    """Recompute a clean, monotonic cumulative sum across an entity's full history.

    A manual backfill (see services.FETCH_SERVICE) is typically done in
    several chunks spread over several days to stay under Enedis' daily
    quota, and can land before, after, or in the middle of data that's
    already there. Each chunk's "sum" column is computed locally by
    EnedisAnalytics from whatever baseline was known at import time, so
    stitching chunks together out of order leaves discontinuities at the
    seams (a value the last chunk imported doesn't know about the running
    total of a chunk imported afterward that precedes it).

    This re-reads every raw state value for the statistic (chronologically,
    across its whole history) and rewrites the sum as a plain running total
    from zero, so the result is correct regardless of how many calls it took
    to get there or in what order. No Enedis API call involved.
    """
    instance = get_instance(hass)
    for item in items:
        statistic_id = item["entity_id"]
        result = await instance.async_add_executor_job(
            statistics_during_period,
            hass,
            dt_util.utc_from_timestamp(0),
            None,
            {statistic_id},
            "hour",
            None,
            {"state"},
        )
        values = result.get(statistic_id)
        if not values:
            continue

        running_sum = 0.0
        rows = []
        for value in sorted(values, key=lambda v: v["start"]):
            running_sum += value.get("state") or 0
            rows.append(
                StatisticData(
                    start=dt_util.utc_from_timestamp(value["start"]),
                    state=value.get("state") or 0,
                    sum=running_sum,
                )
            )

        is_energy = item["kind"] == "energy"
        metadata = StatisticMetaData(
            has_sum=True,
            name=None,
            source="recorder",
            statistic_id=statistic_id,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR if is_energy else "EUR",
            mean_type=StatisticMeanType.NONE,
            unit_class=EnergyConverter.UNIT_CLASS if is_energy else None,
        )
        await instance.async_add_executor_job(
            async_import_statistics, hass, metadata, rows
        )
        _LOGGER.info("Rebuilt %s statistic points for %s", len(rows), statistic_id)


def next_date(date_: dt | None, service: str) -> dt:
    """Return next date.

    myelectricaldatapy expects a naive local datetime and applies its own
    tz_localize; a tz-aware value (e.g. from dt_util.as_local) makes it
    crash with "Cannot localize tz-aware Timestamp", so tzinfo is stripped
    here before returning.
    """
    if date_ and date_.tzinfo is not None:
        date_ = date_.replace(tzinfo=None)
    if date_ and service in [PRODUCTION_DETAIL, CONSUMPTION_DETAIL]:
        return date_ + timedelta(hours=1)
    elif date_:
        return date_ + timedelta(days=1)
    return (
        dt_util.now().replace(tzinfo=None) - timedelta(days=1095)
        if service in [PRODUCTION_DAILY, CONSUMPTION_DAILY]
        else dt_util.now().replace(tzinfo=None) - timedelta(days=7)
    )
