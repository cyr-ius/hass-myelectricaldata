"""Data Update Coordinator."""

from __future__ import annotations

from datetime import datetime as dt, timedelta
import logging
from typing import Any

from myelectricaldatapy import EnedisByPDL, EnedisException, LimitReached

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_AUTH,
    CONF_CONSUMPTION,
    CONF_ECOWATT,
    CONF_INTERVALS,
    CONF_PDL,
    CONF_PRICINGS,
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
    async_add_statistics,
    async_get_db_infos,
    async_get_last_infos,
    map_attributes,
    next_date,
)

SCAN_INTERVAL = timedelta(hours=3)
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
        # Get tempo day
        if options.get(CONF_AUTH, {}).get(CONF_TEMPO):
            self.api.tempo_subscription(True)

        # Get ecowatt information
        if options.get(CONF_AUTH, {}).get(CONF_ECOWATT):
            self.api.ecowatt_subscription(True)

        dict_opts = dict(
            filter(
                lambda x: x[0] in [CONF_PRODUCTION, CONF_CONSUMPTION]
                and x[1].get(CONF_SERVICE),
                options.items(),
            )
        )

        attributes = {}
        for mode, opt in dict_opts.items():
            service = opt.get(CONF_SERVICE)
            intervals = [
                (interval[CONF_RULE_START_TIME], interval[CONF_RULE_END_TIME])
                for interval in opt.get(CONF_INTERVALS, {}).values()
            ]
            attrs = map_attributes(mode, self.pdl, intervals)
            dt_start, cum_values, cum_prices = await async_get_last_infos(
                self.hass, attrs
            )

            end = None
            if service in [CONSUMPTION_DETAIL, PRODUCTION_DETAIL]:
                end = next_date(dt_start, service) + timedelta(days=7)

            self.api.set_collects(
                service=service,
                start=next_date(dt_start, service),
                end=end,
                intervals=intervals,
                prices=opt.get(CONF_PRICINGS),
                cum_value=cum_values,
                cum_price=cum_prices,
            )
            attributes.update(attrs)

        force_refresh = (
            (self.retry != 0)
            and self.last_stat is not None
            and (self.last_stat.date() != dt.now().date())
        )

        # Refresh Api data
        try:
            await self.api.async_update(force_refresh=force_refresh)
            _LOGGER.debug("Refresh data: %s", self.api.last_refresh)
        except LimitReached as error:
            _LOGGER.error("Limit reached: %s", error)
        except EnedisException as error:
            raise UpdateFailed(f"Error to update data: {error}") from error

        # Add statistics in HA Database
        await self.entry.async_create_task(
            self.hass,
            async_add_statistics(self.hass, attributes, self.api.stats),
            "statistics",
        )

        self.access = self.api.access
        self.contract = self.api.contract
        self.tempo_day = self.api.tempo_day
        self.ecowatt_day = self.api.ecowatt_day
        self.last_access = self.api.last_access
        self.last_refresh = self.api.last_refresh
        self.retry -= 1

        statistics = {}
        for statistic_id, detail in attributes.items():
            summary, self.last_stat = await async_get_db_infos(self.hass, statistic_id)
            statistics.update({detail["friendly_name"].capitalize(): summary})
        _LOGGER.debug("[statistics] %s, last collect: %s", statistics, self.last_stat)
        return statistics
