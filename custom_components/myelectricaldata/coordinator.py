"""Data Update Coordinator."""

import logging
from datetime import UTC, date, timedelta
from datetime import datetime as dt
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ENTITY_ID,
    CONF_TOKEN,
    CONF_UNIQUE_ID,
    EVENT_RECORDER_5MIN_STATISTICS_GENERATED,
    EVENT_RECORDER_HOURLY_STATISTICS_GENERATED,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from myelectricaldatapy import (
    DAILY_CONSUM,
    DAILY_PROD,
    Enedis,
    EnedisByPDL,
    EnedisException,
    EnergyCollect,
    LimitReached,
    Subscription,
    ThrottlingError,
)
from myelectricaldatapy.const import ATTR_HPHC, ATTR_INTERVALS, ATTR_TEMPO

from .const import (
    CONF_AUTH,
    CONF_AUTO_OFFPEAK,
    CONF_CONSUMPTION,
    CONF_ECOWATT,
    CONF_PDL,
    CONF_PRODUCTION,
    CONF_RULE_END_TIME,
    CONF_RULE_START_TIME,
    CONF_SERVICE,
    CONF_SUBSCRIPTION,
    CONF_SUMMARY,
    DOMAIN,
    ISSUE_OFFPEAK_MISMATCH,
    ISSUE_OFFPEAK_UPDATED,
)
from .helpers import (
    async_clear_short_term_statistics,
    async_clear_today_statistics,
    async_get_db_infos,
    async_get_last_infos,
    async_import_sensor_statistics,
    async_migrate_legacy_statistics,
    async_rebuild_statistics,
    build_sensor_items,
    format_offpeak,
    next_date,
    parse_offpeak_hours,
    read_prices,
)

SCAN_INTERVAL = timedelta(hours=3)

# Enedis rejects any daily request starting before the meter (re)activation
# with this message (ADAM-ERR0123).
ACTIVATION_ERROR = "last activation date"

_LOGGER = logging.getLogger(__name__)


def _is_activation_error(error: EnedisException) -> bool:
    """Return True when Enedis refused a period older than the meter activation."""
    return ACTIVATION_ERROR in str(error).lower()


def _parse_activation_date(value: str | None) -> date | None:
    """Parse a contract date such as ``2015-10-12+02:00``."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        _LOGGER.debug("Could not parse activation date: %s", value)
        return None


def _parse_next_access_time(value: str | None) -> dt | None:
    """Parse the raw ``nextAccessTime`` carried by a ThrottlingError.

    The gateway formats it as ``2026-Sep-05 16:00:00+0000 UTC`` (English month
    abbreviation, redundant trailing timezone name).
    """
    if not value:
        return None
    raw = value.removesuffix(" UTC").strip()
    for fmt in ("%Y-%b-%d %H:%M:%S%z", "%Y-%b-%d %H:%M:%S"):
        try:
            parsed = dt.strptime(raw, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    _LOGGER.debug("Could not parse nextAccessTime: %s", value)
    return None


class EnedisDataUpdateCoordinator(DataUpdateCoordinator):
    """Define an object to fetch data."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Class to manage fetching data API."""
        self.last_stat: dt | None = None
        self.pdl: str = config_entry.data[CONF_PDL]
        self._migrated_legacy_stats = False
        # Earliest start date Enedis accepted for each daily service, learnt
        # by _async_probe_first_accepted_date after an activation date error.
        self._start_floor: dict[str, date] = {}
        self._throttle_retry_unsub: CALLBACK_TYPE | None = None

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

    async def _async_setup(self) -> None:
        """Set up the coordinator."""
        try:
            subscription = getattr(
                Subscription,
                self.config_entry.options[CONF_AUTH][CONF_SUBSCRIPTION].upper(),
            )
            self.api = EnedisByPDL(
                pdl=self.pdl,
                token=self.config_entry.options[CONF_AUTH][CONF_TOKEN],
                subscription=subscription,
                session=async_create_clientsession(self.hass),
                timeout=30,
                timezone=dt_util.get_default_time_zone(),
            )
            self.api.set_ecowatt_subscription(
                self.config_entry.options[CONF_AUTH][CONF_ECOWATT]
            )
        except EnedisException as error:
            raise UpdateFailed(f"Error to setup coordinator: {error}") from error

        # Any 5-minute/hourly rows the recorder compiled for our entities
        # before the previous shutdown are stale by definition (this listens
        # for future compilations, see _async_clear_generated_statistics) and
        # would otherwise linger until overwritten by the next import.
        statistic_ids = self._configured_statistic_ids()
        async_clear_short_term_statistics(self.hass, statistic_ids)
        async_clear_today_statistics(self.hass, statistic_ids)

        self.config_entry.async_on_unload(
            self.hass.bus.async_listen(
                EVENT_RECORDER_5MIN_STATISTICS_GENERATED,
                self._async_clear_generated_statistics,
            )
        )
        self.config_entry.async_on_unload(
            self.hass.bus.async_listen(
                EVENT_RECORDER_HOURLY_STATISTICS_GENERATED,
                self._async_clear_today_statistics,
            )
        )
        self.config_entry.async_on_unload(self._cancel_throttle_retry)

    def _configured_statistic_ids(self) -> set[str]:
        """Return every statistic_id that could exist for the current config.

        Pure config parsing (no API calls), so it's safe to call before the
        first data refresh -- used to clear stale statistics left over from
        a previous run. Always passing has_price=True over-includes the cost
        companion ids when pricing isn't actually configured for a mode, but
        clearing a statistic_id with no matching metadata is a no-op.
        """
        options = self.config_entry.options
        statistic_ids: set[str] = set()
        for mode in (CONF_PRODUCTION, CONF_CONSUMPTION):
            opt = options.get(mode, {})
            if not (service := opt.get(CONF_SERVICE)):
                continue
            intervals = [
                (interval[CONF_RULE_START_TIME], interval[CONF_RULE_END_TIME])
                for interval in opt.get(ATTR_INTERVALS, {}).values()
            ]
            statistic_ids.update(
                item[CONF_ENTITY_ID]
                for item in build_sensor_items(
                    self.hass, mode, self.pdl, service, intervals, has_price=True
                )
            )
        return statistic_ids

    @callback
    def _async_clear_generated_statistics(self, event: Event) -> None:
        """Delete the 5-minute statistics the recorder just compiled for our sensors.

        See async_clear_short_term_statistics for why these are unwanted.
        """
        if not self.data:
            return
        statistic_ids = {item[CONF_ENTITY_ID] for item in self.data.values()}
        async_clear_short_term_statistics(self.hass, statistic_ids)

    @callback
    def _async_clear_today_statistics(self, event: Event) -> None:
        """Delete today's long-term statistics the recorder just folded for our sensors.

        See async_clear_today_statistics for why these are unwanted.
        """
        if not self.data:
            return
        statistic_ids = {item[CONF_ENTITY_ID] for item in self.data.values()}
        async_clear_today_statistics(self.hass, statistic_ids)

    @callback
    def _async_sync_offpeak_intervals(self) -> None:
        """Align the configured offpeak windows with the ones on the contract.

        Enedis can change a contract's offpeak hours during the year. With the
        automatic mode (CONF_AUTO_OFFPEAK) a difference is written back to the
        options, which reloads the entry so every sensor and the next data
        collection use the new windows, and a repair issue tells the user.
        Without it, a repair issue offers to enable the automatic mode.
        """
        options = self.config_entry.options
        consumption = options.get(CONF_CONSUMPTION, {})
        if not consumption.get(CONF_SERVICE) or options.get(CONF_AUTH, {}).get(
            CONF_SUBSCRIPTION
        ) not in (ATTR_HPHC, ATTR_TEMPO):
            return
        contract = self.api.contract
        offpeak_hours = contract.offpeak_hours if contract else None
        intervals = parse_offpeak_hours(offpeak_hours)
        if not intervals:
            return

        entry_id = self.config_entry.entry_id
        current = consumption.get(ATTR_INTERVALS) or {}
        mismatch_id = f"{ISSUE_OFFPEAK_MISMATCH}_{entry_id}"
        if intervals == current:
            ir.async_delete_issue(self.hass, DOMAIN, mismatch_id)
            return

        placeholders = {
            "pdl": self.pdl,
            "old": format_offpeak(current) or "-",
            "new": format_offpeak(intervals),
        }
        if not consumption.get(CONF_AUTO_OFFPEAK):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                mismatch_id,
                data={"entry_id": entry_id, "offpeak_hours": offpeak_hours},
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_OFFPEAK_MISMATCH,
                translation_placeholders=placeholders,
            )
            return

        _LOGGER.info(
            "Offpeak hours changed on the contract (%s), updating configuration",
            offpeak_hours,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{ISSUE_OFFPEAK_UPDATED}_{entry_id}",
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_OFFPEAK_UPDATED,
            translation_placeholders=placeholders,
        )
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={
                **options,
                CONF_CONSUMPTION: {**consumption, ATTR_INTERVALS: intervals},
            },
        )

    @callback
    def _cancel_throttle_retry(self) -> None:
        """Drop a pending post-throttle refresh, if one is scheduled."""
        if self._throttle_retry_unsub is not None:
            self._throttle_retry_unsub()
            self._throttle_retry_unsub = None

    @callback
    def _schedule_throttle_retry(self, next_access: dt) -> None:
        """Queue a one-off refresh shortly after the gateway quota reopens.

        The regular SCAN_INTERVAL refresh still runs; this only adds an
        earlier attempt so data isn't stale for up to three hours after a
        transient gateway throttle.
        """
        when = next_access + timedelta(minutes=1)
        if when <= dt_util.utcnow():
            return
        self._cancel_throttle_retry()
        self._throttle_retry_unsub = async_track_point_in_time(
            self.hass, self._async_throttle_retry, when
        )
        _LOGGER.info(
            "Gateway throttled until %s, refresh rescheduled at %s", next_access, when
        )

    @callback
    def _async_throttle_retry(self, _now: dt) -> None:
        """Fire the rescheduled refresh once the quota window has reopened."""
        self._throttle_retry_unsub = None
        self.config_entry.async_create_task(
            self.hass, self.async_request_refresh(), "throttle-retry"
        )

    def _start_floor_for(self, service: str) -> date | None:
        """Return the earliest date a collection for ``service`` may start at.

        Combines the contract's last activation date with whatever earlier
        probing learnt (the contract value may lag behind a meter swap).
        """
        floors = [
            floor
            for floor in (
                _parse_activation_date(
                    self.api.contract.last_activation_date
                    if self.api.contract is not None
                    else None
                ),
                self._start_floor.get(service),
            )
            if floor is not None
        ]
        return max(floors) if floors else None

    async def _async_handle_activation_error(self, requested: dict[str, dt]) -> None:
        """Unstick a collection Enedis refuses because it starts too early.

        Without this, every refresh requests the same rejected period again
        and no data is ever imported anymore (issue #143).
        """
        learnt = False
        for service, start in requested.items():
            if service not in (DAILY_CONSUM, DAILY_PROD):
                continue
            try:
                floor = await self._async_probe_first_accepted_date(
                    service, start.date()
                )
            except EnedisException as error:
                _LOGGER.warning("Could not probe %s start date: %s", service, error)
                return
            if floor is None or floor <= start.date():
                continue
            _LOGGER.warning(
                "Enedis refuses %s data before %s (meter activation), "
                "collection restarts from that date",
                service,
                floor,
            )
            self._start_floor[service] = floor
            learnt = True

        if learnt:
            # Collect from the new start shortly instead of in SCAN_INTERVAL.
            self._cancel_throttle_retry()
            self._throttle_retry_unsub = async_track_point_in_time(
                self.hass,
                self._async_throttle_retry,
                dt_util.utcnow() + timedelta(minutes=1),
            )

    async def _async_probe_first_accepted_date(
        self, service: str, start: date
    ) -> date | None:
        """Binary search the first day Enedis accepts for ``service``.

        Enedis rejects every period starting before the meter activation and
        accepts every period after it, so a one-day request per step finds
        the boundary in about log2(days) calls. Returns None when ``start``
        is already accepted, and today when even yesterday is refused.
        """
        client = Enedis(
            self.config_entry.options[CONF_AUTH][CONF_TOKEN],
            async_create_clientsession(self.hass),
        )

        async def accepted(day: date) -> bool:
            begin = dt.combine(day, dt.min.time())
            try:
                await client.async_fetch_datas(
                    service, self.pdl, begin, begin + timedelta(days=1)
                )
            except LimitReached:
                raise
            except EnedisException as error:
                if _is_activation_error(error):
                    return False
            return True

        low = start
        high = dt_util.now().date() - timedelta(days=1)
        if low > high or await accepted(low):
            return None
        if not await accepted(high):
            return high + timedelta(days=1)
        # Invariant: low is refused, high is accepted.
        while (high - low).days > 1:
            middle = low + timedelta(days=(high - low).days // 2)
            if await accepted(middle):
                high = middle
            else:
                low = middle
        return high

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via API."""
        self._cancel_throttle_retry()
        options = self.config_entry.options

        dict_opts = dict(
            filter(
                lambda x: (
                    x[0] in [CONF_PRODUCTION, CONF_CONSUMPTION]
                    and x[1].get(CONF_SERVICE)
                ),
                options.items(),
            )
        )

        items: list[dict[str, Any]] = []
        # Start of each period handed to the library this cycle, per service.
        requested: dict[str, dt] = {}
        for mode, opt in dict_opts.items():
            service: EnergyCollect = opt.get(CONF_SERVICE)
            intervals = [
                (interval[CONF_RULE_START_TIME], interval[CONF_RULE_END_TIME])
                for interval in opt.get(ATTR_INTERVALS, {}).values()
            ]
            prices = read_prices(
                options,
                mode,
                service,
                intervals,
                tempo=self.api.has_tempo_subscription and mode == CONF_CONSUMPTION,
            )
            mode_items = build_sensor_items(
                self.hass, mode, self.pdl, service, intervals, has_price=bool(prices)
            )
            if not self._migrated_legacy_stats:
                await async_migrate_legacy_statistics(self.hass, mode_items)
            dt_start, cum_values, cum_prices = await async_get_last_infos(
                self.hass, mode_items
            )

            collect_start = next_date(dt_start, service)
            if (floor := self._start_floor_for(service)) is not None:
                collect_start = max(collect_start, dt.combine(floor, dt.min.time()))
            if collect_start.date() < dt_util.now().date():
                # The API only ever publishes up to yesterday's data, so a
                # start date of today or later can never return anything:
                # skip the call instead of retrying it every SCAN_INTERVAL.
                # end is left at None (up to now) even for detail services:
                # the library's _async_get_details already paginates a wide
                # range into its own 7-day sub-requests, so a catch-up gap
                # is closed in this single refresh instead of one 7-day
                # chunk per SCAN_INTERVAL.
                requested[service] = collect_start
                self.api.set_data_fetch(
                    service=service,
                    start=collect_start,
                    end=None,
                    intervals=intervals,
                    prices=prices,
                    cum_value=cum_values,
                    cum_price=cum_prices,
                )
            items.extend(mode_items)

        self._migrated_legacy_stats = True

        # force_refresh left at its default (False): the library already
        # re-checks access/contract/ecowatt once per calendar day on its own
        # (EnedisByPDL.async_update compares last_access.date() to today).
        try:
            await self.api.async_update()
            _LOGGER.debug("Refresh data: %s", self.api.last_refresh)
        except ThrottlingError as error:
            next_access = _parse_next_access_time(error.next_access_time)
            _LOGGER.warning("Gateway throttled: %s", error)
            if next_access is not None:
                self._schedule_throttle_retry(next_access)
        except LimitReached as error:
            _LOGGER.error("Limit reached: %s", error)
        except EnedisException as error:
            _LOGGER.error("Error to update data: %s", error)
            if _is_activation_error(error):
                await self._async_handle_activation_error(requested)
        else:
            self._async_sync_offpeak_intervals()

        # Import statistics directly onto their own sensor entity
        last_stats = await self.config_entry.async_create_task(
            self.hass,
            async_import_sensor_statistics(self.hass, items, self.api.stats),
            "statistics",
        )

        if last_stats:
            # The chunk just imported was seeded from whatever cum_value
            # snapshot async_get_last_infos happened to read this cycle; if
            # that snapshot didn't match what's already in the database
            # (e.g. a same-day stub row async_clear_today_statistics hasn't
            # cleared yet), the new rows land at the wrong level. Rebuilding
            # reconnects the seam the same way the manual backfill service
            # already does for out-of-order chunks.
            rebuilt = await async_rebuild_statistics(
                self.hass,
                [item for item in items if item[CONF_ENTITY_ID] in last_stats],
            )
            for entity_id, summary in rebuilt.items():
                last_stats[entity_id] = (summary, last_stats[entity_id][1])

        sensors_data = {}
        for item in items:
            if item[CONF_ENTITY_ID] in last_stats:
                # Just-imported value: read straight from what we sent
                # rather than the database, since the recorder write is
                # only queued at this point, not yet committed.
                summary, self.last_stat = last_stats[item[CONF_ENTITY_ID]]
            else:
                summary, self.last_stat = await async_get_db_infos(
                    self.hass, item[CONF_ENTITY_ID]
                )
            item[CONF_SUMMARY] = summary
            sensors_data.update({item.pop(CONF_UNIQUE_ID): item})

        return sensors_data
