"""Data Update Coordinator."""

from __future__ import annotations

import logging
from datetime import datetime as dt
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
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
    CONF_TEMPO,
    CONSUMPTION_DETAIL,
    DOMAIN,
    PRODUCTION_DETAIL,
)
from .helpers import (
    async_get_db_infos,
    async_get_last_infos,
    async_import_sensor_statistics,
    async_migrate_legacy_statistics,
    build_price_items,
    build_sensor_items,
    next_date,
    read_prices,
)

SCAN_INTERVAL = timedelta(hours=6)
RETRY = 3

_LOGGER = logging.getLogger(__name__)


class EnedisDataUpdateCoordinator(DataUpdateCoordinator):
    """Define an object to fetch data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Class to manage fetching data API."""
        self.entry = entry
        self.access: dict[str, Any] = {}
        self.contract: dict[str, Any] = {}
        self.ecowatt_day: str | None = None
        self.ecowatt: dict[str, Any] = {}
        self.last_access: dt | None = None
        self.last_refresh: dt | None = None
        self.last_stat: dt | None = None
        self.pdl: str = entry.data[CONF_PDL]
        self.price_items: list[dict[str, Any]] = []
        self._migrated_legacy_stats = False
        self.tempo_day: str | None = None
        self.tempo: dict[str, Any] = {}
        self.retry: int = RETRY

        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)

    async def _async_setup(self) -> None:
        """Set up the coordinator."""
        try:
            self.api = EnedisByPDL(
                pdl=self.pdl,
                token=self.entry.options[CONF_AUTH][CONF_TOKEN],
                session=async_create_clientsession(self.hass),
                timeout=30,
            )
        except EnedisException as error:
            raise UpdateFailed(f"Error to setup coordinator: {error}") from error

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via API."""
        options = self.entry.options
        tempo = bool(options.get(CONF_AUTH, {}).get(CONF_TEMPO))
        # Get tempo day
        if tempo:
            self.api.tempo_subscription(True)

        # Get ecowatt information
        if options.get(CONF_AUTH, {}).get(CONF_ECOWATT):
            self.api.ecowatt_subscription(True)

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
        price_items: list[dict[str, Any]] = []
        for mode, opt in dict_opts.items():
            service = opt.get(CONF_SERVICE)
            intervals = [
                (interval[CONF_RULE_START_TIME], interval[CONF_RULE_END_TIME])
                for interval in opt.get(CONF_INTERVALS, {}).values()
            ]
            mode_price_items = build_price_items(
                mode,
                self.pdl,
                service,
                intervals,
                tempo=tempo and mode == CONF_CONSUMPTION,
            )
            prices = read_prices(self.hass, mode_price_items)
            mode_items = build_sensor_items(
                mode, self.pdl, service, intervals, has_price=bool(prices)
            )
            if not self._migrated_legacy_stats:
                await async_migrate_legacy_statistics(self.hass, mode_items)
            dt_start, cum_values, cum_prices = await async_get_last_infos(
                self.hass, mode_items
            )

            end = None
            if service in [CONSUMPTION_DETAIL, PRODUCTION_DETAIL]:
                end = next_date(dt_start, service) + timedelta(days=7)

            self.api.set_collects(
                service=service,
                start=next_date(dt_start, service),
                end=end,
                intervals=intervals,
                prices=prices,
                cum_value=cum_values,
                cum_price=cum_prices,
            )
            items.extend(mode_items)
            price_items.extend(mode_price_items)

        self.price_items = price_items
        self._migrated_legacy_stats = True

        force_refresh = (
            (self.retry != 0)
            and self.last_stat is not None
            and (self.last_stat.date() != dt.now().date())  # noqa: DTZ005
        )

        # Refresh Api data
        try:
            await self.api.async_update(force_refresh=force_refresh)
            _LOGGER.debug("Refresh data: %s", self.api.last_refresh)
        except LimitReached as error:
            _LOGGER.error("Limit reached: %s", error)
        except EnedisException as error:
            _LOGGER.error("Error to update data: %s", error)

        # Import statistics directly onto their own sensor entity
        await self.entry.async_create_task(
            self.hass,
            async_import_sensor_statistics(self.hass, items, self.api.stats),
            "statistics",
        )

        self.access = self.api.access
        self.contract = self.api.contract
        self.tempo_day = self.api.tempo_day
        self.ecowatt_day = self.api.ecowatt_day
        self.last_access = self.api.last_access
        self.last_refresh = self.api.last_refresh
        self.retry -= 1

        sensors_data = {}
        for item in items:
            summary, self.last_stat = await async_get_db_infos(
                self.hass, item["entity_id"]
            )
            sensors_data[item["entity_id"]] = {**item, "value": summary}
        _LOGGER.debug(
            "[sensors_data] %s, last collect: %s", sensors_data, self.last_stat
        )
        return sensors_data
