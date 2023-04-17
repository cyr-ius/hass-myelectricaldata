"""Data Update Coordinator."""
from __future__ import annotations

from collections.abc import Mapping
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
from homeassistant.const import CONF_TOKEN, UnitOfEnergy
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

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via API."""
        attributes = {}
        dict_opts = {}
        options = self.entry.options
        # Get tempo day
        if options.get(CONF_AUTH, {}).get(CONF_TEMPO):
            self.api.tempo_subscription(True)

        # Get ecowatt information
        if options.get(CONF_AUTH, {}).get(CONF_ECOWATT):
            self.api.ecowatt_subscription(True)

        if options.get(CONF_PRODUCTION, {}).get(CONF_SERVICE):
            dict_opts.update({CONF_PRODUCTION: options[CONF_PRODUCTION]})
        if options.get(CONF_CONSUMPTION, {}).get(CONF_SERVICE):
            dict_opts.update({CONF_CONSUMPTION: options[CONF_CONSUMPTION]})

        for mode, opt in dict_opts.items():
            service = opt.get(CONF_SERVICE)
            intervals = prepare_intervals(self.api, mode, options)
            has_intervals = len(intervals) != 0
            attrs = get_attributes(mode, self.pdl, has_intervals)
            dt_start, cum_values, cum_prices = await get_last_infos(
                self.hass, self.api, attrs, service
            )
            attributes.update({mode: attrs})
            self.api.set_collects(
                service=service,
                start=dt_start,
                intervals=intervals,
                prices=opt.get(CONF_PRICINGS),
                cum_value=cum_values,
                cum_price=cum_prices,
            )

        # Refresh Api datas
        try:
            # await self.api.async_update()
            _LOGGER.debug("Refresh datas: %s", self.api.last_refresh)
        except LimitReached as error:
            _LOGGER.error(error.args[1]["detail"])
        except EnedisException as error:
            raise UpdateFailed(
                f"{error.args[1]['detail']} ({error.args[0]})"
            ) from error

        # Add statistics in HA Database
        await async_add_statistics(self.hass, attributes, self.api.stats)

        self.access = self.api.access
        self.contract = self.api.contract
        self.tempo_day = self.api.tempo_day
        self.ecowatt_day = self.api.ecowatt_day

        return await async_get_statistics(self.hass, attributes, options)


async def async_get_statistics(
    hass: HomeAssistant, attributes: dict[str, Any], options: Mapping[str, Any]
) -> dict[str, Any]:
    """Return statistics from database."""
    statistics = {}
    for attribute in attributes.values():
        for statistic_id, detail in attribute.items():
            summary, _ = await async_get_db_infos(hass, statistic_id)
            statistics.update({detail["friendly_name"].capitalize(): summary})
    _LOGGER.debug("[statistics] %s", statistics)
    return statistics


async def async_get_db_infos(hass: HomeAssistant, statistic_id: str) -> tuple[str, dt]:
    """Fetch last information in database."""
    last_stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, "sum"
    )
    last_summary, dt_last_stat = (
        (0, None)
        if not last_stats
        else (
            last_stats[statistic_id][0]["sum"],
            dt.fromtimestamp(last_stats[statistic_id][0]["start"]),
        )
    )
    _LOGGER.debug("[database] summary: %s, last date: %s", last_summary, dt_last_stat)
    return (last_summary, dt_last_stat)


def get_attributes(mode: str, pdl: str, has_intervals: bool) -> dict[str, Any]:
    """Return attributes for database."""
    _attributes = {}
    suffix = "full" if has_intervals else "standard"
    name = f"{pdl} {mode} {suffix}".capitalize()
    _attributes.update(
        {
            f"{DOMAIN}:"
            + slugify(name.lower()): {
                "name": name,
                "friendly_name": f"{mode} {suffix}",
                "note": "standard",
            },
        }
    )
    if suffix == "full":
        name = f"{pdl} {mode} offpeak".capitalize()
        _attributes.update(
            {
                f"{DOMAIN}:"
                + slugify(name.lower()): {
                    "name": name,
                    "friendly_name": f"{mode} offpeak",
                    "note": "offpeak",
                }
            }
        )
    _LOGGER.debug("Attributes: %s", _attributes)
    return _attributes


def prepare_intervals(api: EnedisByPDL, mode: str, options: dict[str, Any]) -> None:
    """Set intervals."""
    intervals = options.get(mode, {}).get(CONF_INTERVALS, {})
    intervals = [
        (interval[CONF_RULE_START_TIME], interval[CONF_RULE_END_TIME])
        for interval in intervals.values()
    ]
    return intervals


async def get_last_infos(
    hass: HomeAssistant, api: EnedisByPDL, attributes: dict[str, Any], service: str
) -> tuple[dt, float, float]:
    """Set default api."""
    sum_values = {}
    sum_prices = {}
    _dt_last = None
    for statistic_id, detail in attributes.items():
        sum_value, _dt_value = await async_get_db_infos(hass, statistic_id)
        sum_cost, _dt_cost = await async_get_db_infos(hass, f"{statistic_id}_cost")
        note = detail["note"]
        sum_values[note] = sum_value
        sum_prices[note] = sum_cost

        _dt_last = _dt_value if _dt_last is None else _dt_last
        if _dt_value != _dt_cost:
            _LOGGER.warning(
                "The energy value has a date different from the date collected to calculate the cost"
            )

    # Calculate the next date
    if _dt_last and service in [
        PRODUCTION_DETAIL,
        CONSUMPTION_DETAIL,
    ]:
        _dt_next = _dt_last + timedelta(hours=1)
    elif _dt_last:
        _dt_next = _dt_last + timedelta(days=1)
    else:
        _dt_next = (
            dt.now() - timedelta(days=365)
            if service in [PRODUCTION_DAILY, CONSUMPTION_DAILY]
            else dt.now() - timedelta(days=6)
        )

    _LOGGER.debug("[cumsum] next date: %s", _dt_next)
    return _dt_next, sum_values, sum_prices


async def async_add_statistics(
    hass: HomeAssistant,
    extended_attrs: dict[str, Any],
    datas_collected: dict[str, Any],
) -> None:
    """Add statistics database."""
    for mode, offsets in extended_attrs.items():
        for statistic_id, detail in offsets.items():
            name = detail["name"]
            note = detail["note"]
            stats = []
            costs = []
            for datas in datas_collected.get(mode, []):
                if datas["notes"] != note:
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
                    unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
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
