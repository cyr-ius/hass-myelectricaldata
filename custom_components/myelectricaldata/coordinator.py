"""Data Update Coordinator."""
from __future__ import annotations

from datetime import datetime as dt, timedelta
import logging
from typing import Any

from myelectricaldatapy import EnedisByPDL, EnedisException, LimitReached

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, ENERGY_KILO_WATT_HOUR
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import slugify

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
    CONSUMPTION_DAILY,
    CONSUMPTION_DETAIL,
    DOMAIN,
    PRODUCTION_DAILY,
    PRODUCTION_DETAIL,
)

SCAN_INTERVAL = timedelta(hours=3)
# SCAN_INTERVAL = timedelta(minutes=1)

_LOGGER = logging.getLogger(__name__)


class EnedisDataUpdateCoordinator(DataUpdateCoordinator):
    """Define an object to fetch datas."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Class to manage fetching data API."""
        self.last_access: dt | None = None
        self.hass = hass
        self.entry = entry
        self.pdl: str = entry.data[CONF_PDL]
        self.access: dict[str, Any] = {}
        self.contract: dict[str, Any] = {}
        self.last_access: dt | None = None
        self.tempo: dict[str, Any] = {}
        self.tempo_day: str | None = None
        self.ecowatt: dict[str, Any] = {}
        self.ecowatt_day: str | None = None
        self._last_access: dt | None = None
        token: str = entry.options[CONF_AUTH][CONF_TOKEN]

        self.api = EnedisByPDL(
            pdl=self.pdl,
            token=token,
            session=async_create_clientsession(hass),
            timeout=30,
        )
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)

    async def _async_update_data(self) -> list(str, str):
        """Update data via API."""
        attributes = {}
        options = self.entry.options
        try:
            modes = {}
            # Get tempo day
            if options.get(CONF_AUTH, {}).get(CONF_TEMPO):
                self.api.tempo_subscription(True)

            # Get ecowatt information
            if options.get(CONF_AUTH, {}).get(CONF_ECOWATT):
                self.api.ecowatt_subscription(True)

            # Get production
            if service := options.get(CONF_PRODUCTION, {}).get(CONF_SERVICE):
                option = options.get(CONF_PRODUCTION, {})
                _has_intervals = len(option.get(CONF_INTERVALS, {})) != 0
                _pricings = option.get(CONF_PRICINGS)
                _attrs = get_attributes(CONF_PRODUCTION, self.pdl, _has_intervals)
                _dt_start, _dt_cost = await async_set_cumsums(
                    self.hass,
                    self.api,
                    CONF_PRODUCTION,
                    _attrs,
                    service,
                    _pricings,
                )
                attributes.update({CONF_PRODUCTION: _attrs})
                modes.update(
                    {CONF_PRODUCTION: {"start": _dt_start, "service": service}}
                )

            # Get consumption
            if service := options.get(CONF_CONSUMPTION, {}).get(CONF_SERVICE):
                option = options.get(CONF_CONSUMPTION, {})
                _has_intervals = len(option.get(CONF_INTERVALS, {})) != 0
                _pricings = option.get(CONF_PRICINGS)
                _attrs = get_attributes(CONF_CONSUMPTION, self.pdl, _has_intervals)
                _dt_start, _dt_cost = await async_set_cumsums(
                    self.hass,
                    self.api,
                    CONF_CONSUMPTION,
                    _attrs,
                    service,
                    _pricings,
                )
                attributes.update({CONF_CONSUMPTION: _attrs})
                modes.update(
                    {CONF_CONSUMPTION: {"start": _dt_start, "service": service}}
                )
                intervals = option.get(CONF_INTERVALS, {})
                intervals = [
                    (interval[CONF_RULE_START_TIME], interval[CONF_RULE_END_TIME])
                    for interval in intervals.values()
                ]
                self.api.set_intervals(CONF_CONSUMPTION, intervals)

            # Refresh Api datas
            if self._last_access is None or self._last_access != dt.now().date():
                await self.api.async_update(modes=modes)

            # Add statistics in HA Database
            await async_add_statistics(self.hass, attributes, self.api.stats)

            self.access = self.api.access
            self.contract = self.api.contract
            self.tempo_day = self.api.tempo_day
            self.ecowatt_day = self.api.ecowatt_day

        except LimitReached as error:
            _LOGGER.error(error.args[1]["detail"])
            self._last_access = dt.now().date()
        except EnedisException as error:
            raise UpdateFailed(
                f"{error.args[1]['detail']} ({error.args[0]})"
            ) from error
        else:
            self._last_access = dt.now().date()
        finally:
            return await async_get_statistics(self.hass, attributes, options)


async def async_get_statistics(hass, attributes, options):
    """Return statistics from database."""
    statistics = {}
    for power, attribute in attributes.items():
        service = options.get(power, {}).get(CONF_SERVICE)
        for statistic_id, detail in attribute.items():
            summary, _ = await async_get_db_infos(hass, service, statistic_id)
            statistics.update({detail["friendly_name"].capitalize(): summary})
    _LOGGER.debug("[statistics] %s", statistics)
    return statistics


async def async_get_db_infos(hass, service, statistic_id) -> tuple[str, str]:
    """Fetch last information in database."""
    last_stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, "sum"
    )
    summary, dt_last_stats = (
        (0, None)
        if not last_stats
        else (
            last_stats[statistic_id][0]["sum"],
            dt.fromtimestamp(last_stats[statistic_id][0]["start"]),
        )
    )

    if dt_last_stats and service in [
        PRODUCTION_DETAIL,
        CONSUMPTION_DETAIL,
    ]:
        _dt_date = dt_last_stats + timedelta(hours=1)
    elif dt_last_stats:
        _dt_date = dt_last_stats + timedelta(days=1)
    else:
        _dt_date = (
            dt.now() - timedelta(days=365)
            if service in [PRODUCTION_DAILY, CONSUMPTION_DAILY]
            else dt.now() - timedelta(days=6)
        )

    _LOGGER.debug("Summary: %s, start date: %s", summary, _dt_date)
    return (summary, _dt_date)


def get_attributes(power: str, pdl: str, has_intervals: bool) -> dict[str, Any]:
    """Return attributes for database."""
    _attributes = {}
    suffix = "full" if has_intervals else "standard"
    name = f"{pdl} {power} {suffix}".capitalize()
    _attributes.update(
        {
            f"{DOMAIN}:"
            + slugify(name.lower()): {
                "name": name,
                "friendly_name": f"{power} {suffix}",
                "mode": "standard",
            },
        }
    )
    if suffix == "full":
        name = f"{pdl} {power} offpeak".capitalize()
        _attributes.update(
            {
                f"{DOMAIN}:"
                + slugify(name.lower()): {
                    "name": name,
                    "friendly_name": f"{power} offpeak",
                    "mode": "offpeak",
                }
            }
        )
    _LOGGER.debug("Attributes: %s", _attributes)
    return _attributes


async def async_set_cumsums(
    hass: HomeAssistant,
    api: EnedisByPDL,
    power: str,
    attributes: dict[str, Any],
    service: str,
    pricings: dict[str, Any],
) -> dict[str, Any]:
    """Set default api."""
    sum_values = {}
    sum_prices = {}
    _dt_start = None
    for statistic_id, detail in attributes.items():
        sum_value, start_date = await async_get_db_infos(hass, service, statistic_id)
        sum_cost, start_date_cost = await async_get_db_infos(
            hass, service, f"{statistic_id}_cost"
        )
        mode = detail["mode"]
        sum_values[mode] = sum_value
        _dt_start = start_date if _dt_start is None else _dt_start
        sum_prices[mode] = sum_cost
    if pricings:
        api.set_cumsum(power, "price", sum_prices)
        api.set_prices(power, pricings)

    api.set_cumsum(power, "value", sum_values)
    _LOGGER.debug("start date: %s, cost %s", _dt_start, start_date_cost)
    return _dt_start, start_date_cost


async def async_add_statistics(
    hass: HomeAssistant,
    extended_attrs: dict[str, Any],
    datas_collected: dict[str, Any],
):
    """Add statistics database."""
    for power, offsets in extended_attrs.items():
        for statistic_id, detail in offsets.items():
            name = detail["name"]
            mode = detail["mode"]
            stats = []
            costs = []
            for datas in datas_collected.get(power, []):
                if datas["notes"] != mode:
                    continue
                _LOGGER.debug(datas)
                if datas.get("value"):
                    stats.append(
                        StatisticData(
                            start=datas["date"],
                            state=datas["value"],
                            sum=datas["sum_value"],
                        )
                    )
                if datas.get("price"):
                    costs.append(
                        StatisticData(
                            start=datas["date"],
                            state=datas["price"],
                            sum=datas["sum_price"],
                        )
                    )

            if stats:
                _LOGGER.debug("Add %s stat in table", mode)
                metadata = StatisticMetaData(
                    has_mean=False,
                    has_sum=True,
                    name=name,
                    source=DOMAIN,
                    statistic_id=statistic_id,
                    unit_of_measurement=ENERGY_KILO_WATT_HOUR,
                )
                hass.async_add_executor_job(
                    async_add_external_statistics, hass, metadata, stats
                )
            if costs:
                _LOGGER.debug("Add %s cost in table", mode)
                metacost = StatisticMetaData(
                    has_mean=False,
                    has_sum=True,
                    name=f"{name} cost",
                    source=DOMAIN,
                    statistic_id=f"{statistic_id}_cost",
                    unit_of_measurement="EUR",
                )
                hass.async_add_executor_job(
                    async_add_external_statistics, hass, metacost, costs
                )
