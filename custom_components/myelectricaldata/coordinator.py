"""Data Update Coordinator."""

import logging
from datetime import datetime as dt
from datetime import timedelta
from typing import Any, Literal

from homeassistant.components.recorder.const import (
    EVENT_RECORDER_5MIN_STATISTICS_GENERATED,
    EVENT_RECORDER_HOURLY_STATISTICS_GENERATED,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ENTITY_ID, CONF_TOKEN, CONF_UNIQUE_ID
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from myelectricaldatapy import EnedisByPDL, EnedisException, LimitReached

from .const import (
    CONF_AUTH,
    CONF_CONSUMPTION,
    CONF_ECOWATT,
    CONF_INTERVALS,
    CONF_PDL,
    CONF_PRODUCTION,
    CONF_RULE_END_TIME,
    CONF_RULE_START_TIME,
    CONF_SERVICE,
    CONF_STD,
    CONF_SUBSCRIPTION,
    CONF_SUMMARY,
    CONF_TEMPO,
    CONSUMPTION_DETAIL,
    DOMAIN,
    PRODUCTION_DETAIL,
)
from .helpers import (
    async_clear_short_term_statistics,
    async_clear_today_statistics,
    async_get_db_infos,
    async_get_last_infos,
    async_import_sensor_statistics,
    async_migrate_legacy_statistics,
    build_sensor_items,
    next_date,
    read_prices,
)

SCAN_INTERVAL = timedelta(hours=3)
STORAGE_VERSION = 1

_LOGGER = logging.getLogger(__name__)


class EnedisDataUpdateCoordinator(DataUpdateCoordinator):
    """Define an object to fetch data."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Class to manage fetching data API."""
        self.last_stat: dt | None = None
        self.pdl: str = config_entry.data[CONF_PDL]
        self._migrated_legacy_stats = False
        self.subscription: Literal["hphc", "tempo", "standard"] = CONF_STD
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}_{self.pdl}_api_cache"
        )

        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)

    async def _async_setup(self) -> None:
        """Set up the coordinator."""
        try:
            self.api = EnedisByPDL(
                pdl=self.pdl,
                token=self.config_entry.options[CONF_AUTH][CONF_TOKEN],
                session=async_create_clientsession(self.hass),
                timeout=30,
                timezone=dt_util.get_default_time_zone(),
            )
        except EnedisException as error:
            raise UpdateFailed(f"Error to setup coordinator: {error}") from error
        await self._async_restore_api_cache()

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
                for interval in opt.get(CONF_INTERVALS, {}).values()
            ]
            statistic_ids.update(
                item[CONF_ENTITY_ID]
                for item in build_sensor_items(
                    mode, self.pdl, service, intervals, has_price=True
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

    async def _async_restore_api_cache(self) -> None:
        """Restore access/contract/ecowatt info cached before the last restart.

        Enedis quotas calls to ~50/day, checked once a day by the library
        (access.last_access.date() != today). Without this, every HA restart
        looks like a new day to a fresh EnedisByPDL instance and re-spends
        that daily check even though it already ran today. If the cached day
        doesn't match today, the quota window has rolled over: leave the api
        untouched so it fetches fresh, as normal.
        """
        if (data := await self._store.async_load()) is None:
            return
        last_access = dt_util.parse_datetime(data["last_access"])
        if last_access is None or last_access.date() != dt_util.now().date():
            return
        self.api.access = data["access"]
        self.api.contract = data["contract"]
        self.api.address = data["address"]
        self.api.ecowatt = data["ecowatt"]
        self.api.tempo = data["tempo"]
        self.api.max_power = data["max_power"]
        self.api.last_access = last_access
        if last_refresh := data["last_refresh"]:
            self.api.last_refresh = dt_util.parse_datetime(last_refresh)

    async def _async_save_api_cache(self) -> None:
        """Persist access/contract/ecowatt info so a restart doesn't re-fetch it."""
        await self._store.async_save(
            {
                "last_access": self.api.last_access.isoformat(),
                "last_refresh": (
                    self.api.last_refresh.isoformat() if self.api.last_refresh else None
                ),
                "access": self.api.access,
                "contract": self.api.contract,
                "address": self.api.address,
                "ecowatt": self.api.ecowatt,
                "tempo": self.api.tempo,
                "max_power": self.api.max_power,
            }
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via API."""
        options = self.config_entry.options
        self.subscription = options[CONF_AUTH][CONF_SUBSCRIPTION]
        tempo = bool(self.subscription == CONF_TEMPO)

        # Get tempo day
        self.api.tempo_subscription(tempo)

        # Get ecowatt information
        self.api.ecowatt_subscription(options[CONF_AUTH][CONF_ECOWATT])

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
        for mode, opt in dict_opts.items():
            service = opt.get(CONF_SERVICE)
            intervals = [
                (interval[CONF_RULE_START_TIME], interval[CONF_RULE_END_TIME])
                for interval in opt.get(CONF_INTERVALS, {}).values()
            ]
            prices = read_prices(
                options,
                mode,
                service,
                intervals,
                tempo=tempo and mode == CONF_CONSUMPTION,
            )
            mode_items = build_sensor_items(
                mode, self.pdl, service, intervals, has_price=bool(prices)
            )
            if not self._migrated_legacy_stats:
                await async_migrate_legacy_statistics(self.hass, mode_items)
            dt_start, cum_values, cum_prices = await async_get_last_infos(
                self.hass, mode_items
            )

            collect_start = next_date(dt_start, service)
            if collect_start.date() < dt_util.now().date():
                # The API only ever publishes up to yesterday's data, so a
                # start date of today or later can never return anything:
                # skip the call instead of retrying it every SCAN_INTERVAL.
                end = None
                if service in [CONSUMPTION_DETAIL, PRODUCTION_DETAIL]:
                    end = collect_start + timedelta(days=7)

                self.api.set_collects(
                    service=service,
                    start=collect_start,
                    end=end,
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
        except LimitReached as error:
            _LOGGER.error("Limit reached: %s", error)
        except EnedisException as error:
            _LOGGER.error("Error to update data: %s", error)

        await self._async_save_api_cache()

        # Import statistics directly onto their own sensor entity
        last_stats = await self.config_entry.async_create_task(
            self.hass,
            async_import_sensor_statistics(self.hass, items, self.api.stats),
            "statistics",
        )

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
        _LOGGER.debug(
            "[sensors_data] %s, last collect: %s", sensors_data, self.last_stat
        )
        return sensors_data
