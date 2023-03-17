"""Data Update Coordinator."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Tuple

from myelectricaldatapy import EnedisAnalytics, EnedisByPDL, EnedisException

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
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import slugify

from .const import (
    CONF_AUTH,
    CONF_CONSUMPTION,
    CONF_ECOWATT,
    CONF_INTERVALS,
    CONF_OFFPEAK,
    CONF_PDL,
    CONF_PRICINGS,
    CONF_PRODUCTION,
    CONF_RULE_END_TIME,
    CONF_RULE_START_TIME,
    CONF_SERVICE,
    CONF_STD,
    CONF_TEMPO,
    CONSUMPTION_DAILY,
    CONSUMPTION_DETAIL,
    DOMAIN,
    PRODUCTION_DAILY,
    PRODUCTION_DETAIL,
)

SCAN_INTERVAL = timedelta(hours=3)

_LOGGER = logging.getLogger(__name__)


class EnedisDataUpdateCoordinator(DataUpdateCoordinator):
    """Define an object to fetch datas."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Class to manage fetching data API."""
        self.last_access: datetime | None = None
        self.hass = hass
        self.entry = entry
        self.pdl: str = entry.data[CONF_PDL]
        self.access: dict[str, Any] = {}
        self.contract: dict[str, Any] = {}
        self.last_access: datetime | None = None
        self.tempo: dict[str, Any] = {}
        self.tempo_day: str | None = None
        self.ecowatt: dict[str, Any] = {}
        self.ecowatt_day: str | None = None
        token: str = entry.options[CONF_AUTH][CONF_TOKEN]

        self.api = EnedisByPDL(
            token=token, session=async_create_clientsession(hass), timeout=30
        )
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)

    async def _async_update_data(self) -> list(str, str):
        """Update data via API."""
        options = self.entry.options
        try:
            # Check  has a valid access
            self.access = await self.api.async_valid_access(self.pdl)
            if self.last_access is None or self.last_access < datetime.now().date():
                # Get contract
                self.contract = await self.api.async_get_contract(self.pdl)

                str_date = datetime.now().strftime("%Y-%m-%d")
                # Get tempo day
                if options.get(CONF_AUTH, {}).get(CONF_TEMPO) and self.api.last_access:
                    dt_date = datetime.now() - timedelta(days=7)
                    self.tempo = await self.api.async_get_tempoday(dt_date)
                    self.tempo_day = self.tempo.get(str_date)

                # Get ecowatt information
                if options.get(CONF_AUTH, {}).get(CONF_ECOWATT):
                    self.ecowatt = await self.api.async_get_ecowatt()
                    self.ecowatt_day = self.ecowatt.get(str_date)

                self.last_access = datetime.now().date()
        except EnedisException as error:
            _LOGGER.error(error)

        # Add statistics in HA Database
        statistics = {}
        services = []
        if production := options.get(CONF_PRODUCTION, {}).get(CONF_SERVICE):
            services.append(production)
        if consumption := options.get(CONF_CONSUMPTION, {}).get(CONF_SERVICE):
            services.append(consumption)
        try:
            for service in services:
                stats = await async_statistics(
                    self.hass,
                    self.api,
                    self.pdl,
                    service,
                    self.tempo,
                    **self.entry.options,
                )
                statistics.update(stats)
        except EnedisException as error:
            _LOGGER.error("Update stats %s", error)

        return statistics


async def async_fetch_datas(
    api: EnedisByPDL, pdl: str, service: str, start_date: datetime, end_date: datetime
) -> dict[str, Any]:
    """Fetch datas."""
    dataset = {}
    try:
        if end_date.date() > start_date.date():
            _LOGGER.debug("Fetch datas for %s at %s", service, start_date.date())
            dataset = await api.async_fetch_datas(service, pdl, start_date, end_date)
    except EnedisException as error:
        _LOGGER.error("Fetch datas for %s (%s): %s", service, pdl, error)
    finally:
        dataset = dataset.get("meter_reading", {}).get("interval_reading", [])

    _LOGGER.debug(dataset)
    return dataset


async def async_statistics(
    hass: HomeAssistant,
    api: EnedisByPDL,
    pdl: str,
    service: str,
    tempo: dict[str, Any] | None = None,
    no_update: bool = False,
    search_date: Tuple[datetime, datetime] | None = None,
    **kwargs: Any,
):
    """Compute statistics."""
    global_statistics = {}
    power_mode = (
        CONF_CONSUMPTION
        if service in [CONSUMPTION_DAILY, CONSUMPTION_DETAIL]
        else CONF_PRODUCTION
    )
    intervals = kwargs.get(power_mode, {}).get(CONF_INTERVALS, {})
    pricings = kwargs.get(power_mode, {}).get(CONF_PRICINGS, {})
    intervals = [
        (interval[CONF_RULE_START_TIME], interval[CONF_RULE_END_TIME])
        for interval in intervals.values()
    ]

    cumsums = {}
    infos_db = {}
    for mode in pricings.keys():
        name = f"{pdl} {power_mode} {mode}".capitalize()
        if mode == CONF_STD and pricings.get(CONF_OFFPEAK):
            name = f"{pdl} {power_mode} full".capitalize()
        name_cost = f"{name} cost".capitalize()
        statistic_id = f"{DOMAIN}:" + slugify(name.lower())
        statistic_id_cost = f"{statistic_id}_cost"

        # Fetch last information in database
        last_stats = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, statistic_id, True, "sum"
        )
        summary = 0 if not last_stats else last_stats[statistic_id][0]["sum"]

        last_stats_cost = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, statistic_id_cost, True, "sum"
        )
        sumcost = 0 if not last_stats else last_stats_cost[statistic_id_cost][0]["sum"]

        # Fetch last time in database
        last_stats_time = (
            None
            if not last_stats
            else datetime.fromtimestamp(last_stats[statistic_id][0]["start"])
        )
        if last_stats_time and service in [PRODUCTION_DETAIL, CONSUMPTION_DETAIL]:
            start_date = last_stats_time + timedelta(hours=1)
        elif last_stats_time:
            start_date = last_stats_time + timedelta(days=1)
        else:
            start_date = (
                datetime.now() - timedelta(days=365)
                if service in [PRODUCTION_DAILY, CONSUMPTION_DAILY]
                else datetime.now() - timedelta(days=6)
            )

        infos_db[mode] = {
            "statistic_id": statistic_id,
            "statistic_id_cost": statistic_id_cost,
            "name": name,
            "name_cost": name_cost,
            "search_date": (start_date, datetime.now()),
        }
        cumsums[mode] = {"sum_value": summary, "sum_price": sumcost}

    # Get search date from parameter or last info in database
    search_date = search_date if search_date else infos_db[CONF_STD]["search_date"]
    cumsums = (
        {
            "standard": {"sum_value": 0, "sum_price": 0},
            "offpeak": {"sum_value": 0, "sum_price": 0},
        }
        if search_date
        else cumsums
    )
    start_date, end_date = search_date

    # Fetch datas
    dataset = await async_fetch_datas(api, pdl, service, start_date, end_date)

    # Compute datas
    analytics = EnedisAnalytics(dataset)
    datas_collected = analytics.get_data_analytics(
        convertKwh=True,
        convertUTC=False,
        intervals=intervals,
        groupby=True,
        start_date=start_date,
        cumsums=cumsums,
        summary=True,
        prices=pricings,
        tempo=tempo,
    )

    for mode in pricings.keys():
        stats = []
        costs = []
        for datas in datas_collected:
            if datas["notes"] == mode:
                _LOGGER.debug(datas)
                stats.append(
                    StatisticData(
                        start=datas["date"],
                        state=datas["value"],
                        sum=datas["sum_value"],
                    )
                )
                costs.append(
                    StatisticData(
                        start=datas["date"],
                        state=datas["price"],
                        sum=datas["sum_price"],
                    )
                )
                cumsums[mode] = {
                    "sum_value": datas["sum_value"],
                    "sum_price": datas["sum_price"],
                }

        if stats and costs:
            _LOGGER.debug("Add %s stat in table", mode)
            metadata = StatisticMetaData(
                has_mean=False,
                has_sum=True,
                name=infos_db[mode]["name"],
                source=DOMAIN,
                statistic_id=infos_db[mode]["statistic_id"],
                unit_of_measurement=ENERGY_KILO_WATT_HOUR,
            )
            hass.async_add_executor_job(
                async_add_external_statistics, hass, metadata, stats
            )
            _LOGGER.debug("Add %s cost in table", mode)
            metacost = StatisticMetaData(
                has_mean=False,
                has_sum=True,
                name=infos_db[mode]["name_cost"],
                source=DOMAIN,
                statistic_id=infos_db[mode]["statistic_id_cost"],
                unit_of_measurement="EUR",
            )
            hass.async_add_executor_job(
                async_add_external_statistics, hass, metacost, costs
            )

        # Fill sensor value
        if no_update is False:
            for name, summaries in cumsums.items():
                if (summary := summaries["sum_value"]) > 0:
                    global_statistics.update(
                        {f"{power_mode} {name}".capitalize(): summary}
                    )

    return global_statistics
