"""Helpers functions for MyElectricalData."""

import logging
from dataclasses import dataclass
from datetime import datetime as dt
from datetime import timedelta
from typing import Any, override

from homeassistant.components.recorder import Recorder, get_instance
from homeassistant.components.recorder.db_schema import Statistics, StatisticsShortTerm
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
from homeassistant.components.recorder.tasks import RecorderTask
from homeassistant.components.recorder.util import session_scope
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify
from homeassistant.util.unit_conversion import EnergyConverter
from sqlalchemy import delete

from .const import (
    CONF_AUTH,
    CONF_OFF_PRICE,
    CONF_OFFPEAK,
    CONF_PRICE,
    CONF_PRICINGS,
    CONF_PRODUCTION,
    CONF_STD,
    CONSUMPTION_DAILY,
    CONSUMPTION_DETAIL,
    DEFAULT_CC_PRICE,
    DEFAULT_CONSUMPTION_TEMPO,
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
                "translation_key": f"{mode}_{suffix}",
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
                    "translation_key": f"{mode}_{suffix}_cost",
                    "note": note,
                    "mode": mode,
                    "kind": "cost",
                    "pdl": pdl,
                    "suffix": suffix,
                }
            )
    _LOGGER.debug("[items] %s", items)
    return items


def read_prices(
    options: dict[str, Any], mode: str, service: str, intervals: list[Any], tempo: bool
) -> dict[str, Any]:
    """Return the tariff(s) configured via the config/options flow, by bucket.

    Mirrors build_sensor_items' bucket cardinality: a single price for daily
    services or detail services without offpeak intervals, a standard/offpeak
    pair once offpeak intervals are configured, or the six Tempo colour
    prices when Tempo is enabled (Tempo always needs both buckets).
    """
    if mode == CONF_PRODUCTION:
        price = options.get(CONF_PRODUCTION, {}).get(CONF_PRICE, DEFAULT_PC_PRICE)
        return {CONF_STD: {CONF_PRICE: price}}

    auth = options.get(CONF_AUTH, {})
    if tempo:
        return auth.get(CONF_PRICINGS, DEFAULT_CONSUMPTION_TEMPO[CONF_PRICINGS])

    is_detail = service in (CONSUMPTION_DETAIL, PRODUCTION_DETAIL)
    has_offpeak = is_detail and bool(intervals)
    if has_offpeak and CONF_OFF_PRICE in auth:
        return {
            CONF_STD: {CONF_PRICE: auth[CONF_PRICE]},
            CONF_OFFPEAK: {CONF_PRICE: auth[CONF_OFF_PRICE]},
        }
    return {CONF_STD: {CONF_PRICE: auth.get(CONF_PRICE, DEFAULT_CC_PRICE)}}


async def async_import_sensor_statistics(
    hass: HomeAssistant,
    items: list[dict[str, Any]],
    data_collected: dict[str, Any],
) -> dict[str, tuple[float, dt]]:
    """Import statistics directly onto their own real sensor entity.

    Returns the last imported (sum, start) per entity_id for items that got
    new rows. async_import_statistics only enqueues the write on the
    recorder's own worker thread and returns immediately, it doesn't wait
    for the write to be committed -- callers that need the freshly imported
    total right away must use this return value instead of reading it back
    from the database, which races the still-pending write and can observe
    stale (e.g. zero) values.
    """
    last_stats: dict[str, tuple[float, dt]] = {}
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

        last_row = max(rows, key=lambda row: row["start"])
        last_stats[item["entity_id"]] = (
            last_row["sum"],
            dt_util.as_local(last_row["start"]),
        )

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
    return last_stats


@dataclass(slots=True)
class _ClearShortTermStatisticsTask(RecorderTask):
    """Delete short-term (5-minute) statistics rows for statistic_ids."""

    statistic_ids: set[str]

    @override
    def run(self, instance: Recorder) -> None:
        """Handle the task."""
        with session_scope(session=instance.get_session()) as session:
            metadata = instance.statistics_meta_manager.get_many(
                session, self.statistic_ids
            )
            if not (
                metadata_ids := [metadata_id for metadata_id, _ in metadata.values()]
            ):
                return
            session.execute(
                delete(StatisticsShortTerm).where(
                    StatisticsShortTerm.metadata_id.in_(metadata_ids)
                )
            )


def async_clear_short_term_statistics(
    hass: HomeAssistant, statistic_ids: set[str]
) -> None:
    """Delete the 5-minute statistics the recorder just compiled for statistic_ids.

    These entities only ever refresh at SCAN_INTERVAL from data already
    finalized by async_import_sensor_statistics, so the recorder's own
    5-minute compilation has nothing new to contribute; left in place it
    would get folded into the next hourly rollup and clobber the sum
    async_import_sensor_statistics already imported for that hour.

    Runs as a queued RecorderTask rather than an executor job so the delete
    is serialized on the recorder's own worker thread: this listener fires
    right as the recorder finishes writing those same rows, and racing that
    write from a separate db_executor thread caused sqlite "database is
    locked" errors.
    """
    if not statistic_ids:
        return
    get_instance(hass).queue_task(_ClearShortTermStatisticsTask(statistic_ids))


@dataclass(slots=True)
class _ClearTodayStatisticsTask(RecorderTask):
    """Delete long-term statistics rows for statistic_ids at or after `since`."""

    statistic_ids: set[str]
    since: dt

    @override
    def run(self, instance: Recorder) -> None:
        """Handle the task."""
        with session_scope(session=instance.get_session()) as session:
            metadata = instance.statistics_meta_manager.get_many(
                session, self.statistic_ids
            )
            if not (
                metadata_ids := [metadata_id for metadata_id, _ in metadata.values()]
            ):
                return
            session.execute(
                delete(Statistics).where(
                    Statistics.metadata_id.in_(metadata_ids),
                    Statistics.start_ts >= self.since.timestamp(),
                )
            )


def async_clear_today_statistics(hass: HomeAssistant, statistic_ids: set[str]) -> None:
    """Delete today's long-term statistics the recorder compiled for statistic_ids.

    async_import_sensor_statistics only ever backfills a day once Enedis has
    published it (never today), so any long-term row the recorder folds
    together for today comes from the entity's current, not-yet-confirmed
    state and would otherwise linger, wrong, until tomorrow's import
    eventually overwrites it.

    The recorder compiles each hour's statistics right at the top of the
    following hour, so the 23:00-00:00 row is only finalized -- and this
    called -- a few seconds into the new day. Anchoring "today" on the
    current wall-clock time at that moment would resolve to the new day's
    midnight and miss that still-unconfirmed row entirely; backing off a few
    minutes keeps the anchor on the day that row actually belongs to.

    Runs as a queued RecorderTask; see async_clear_short_term_statistics for
    why this must be serialized on the recorder's own worker thread.
    """
    if not statistic_ids:
        return
    since = dt_util.start_of_local_day(dt_util.now() - timedelta(minutes=5))
    get_instance(hass).queue_task(_ClearTodayStatisticsTask(statistic_ids, since))


@dataclass(slots=True)
class _ClearStatisticsRangeTask(RecorderTask):
    """Delete long-term statistics rows for statistic_ids within [start, end)."""

    statistic_ids: set[str]
    start: dt | None
    end: dt | None

    @override
    def run(self, instance: Recorder) -> None:
        """Handle the task."""
        with session_scope(session=instance.get_session()) as session:
            metadata = instance.statistics_meta_manager.get_many(
                session, self.statistic_ids
            )
            if not (
                metadata_ids := [metadata_id for metadata_id, _ in metadata.values()]
            ):
                return
            conditions = [Statistics.metadata_id.in_(metadata_ids)]
            if self.start is not None:
                conditions.append(Statistics.start_ts >= self.start.timestamp())
            if self.end is not None:
                conditions.append(Statistics.start_ts < self.end.timestamp())
            session.execute(delete(Statistics).where(*conditions))


def async_clear_statistics_range(
    hass: HomeAssistant,
    statistic_ids: set[str],
    start: dt | None = None,
    end: dt | None = None,
) -> None:
    """Delete a statistic's long-term rows within [start, end), keeping its metadata.

    Unlike get_instance(hass).async_clear_statistics, which drops the
    statistic_id's metadata entirely (removing it from the Energy dashboard
    until the next import recreates it), this only removes rows in the given
    window, so a bad backfill for a specific period can be cleared without
    losing the rest of the entity's history. Omitting a bound leaves that
    side of the range open (e.g. no end deletes everything from start
    onward).
    """
    if not statistic_ids:
        return
    get_instance(hass).queue_task(
        _ClearStatisticsRangeTask(
            statistic_ids,
            dt_util.as_utc(start) if start else None,
            dt_util.as_utc(end) if end else None,
        )
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
