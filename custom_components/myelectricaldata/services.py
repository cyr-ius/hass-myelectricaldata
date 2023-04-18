"""Helper module."""
from __future__ import annotations

from datetime import datetime as dt
import logging

from myelectricaldatapy import EnedisByPDL
import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.const import CONF_TOKEN, UnitOfEnergy
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    CLEAR_SERVICE,
    CONF_AUTH,
    CONF_CONSUMPTION,
    CONF_END_DATE,
    CONF_ENTRY,
    CONF_INTERVALS,
    CONF_OFF_PRICE,
    CONF_PRICE,
    CONF_PRICINGS,
    CONF_PRODUCTION,
    CONF_RULE_END_TIME,
    CONF_RULE_START_TIME,
    CONF_SERVICE,
    CONF_START_DATE,
    CONF_STATISTIC_ID,
    CONSUMPTION_DAILY,
    CONSUMPTION_DETAIL,
    DOMAIN,
    FETCH_SERVICE,
)
from .coordinator import async_add_statistics, map_attributes

_LOGGER = logging.getLogger(__name__)

HISTORY_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTRY): str,
        vol.Required(CONF_SERVICE): str,
        vol.Required(CONF_START_DATE): cv.datetime,
        vol.Required(CONF_END_DATE): cv.datetime,
        vol.Optional(CONF_PRICE): cv.positive_float,
        vol.Optional(CONF_OFF_PRICE): cv.positive_float,
    }
)
CLEAR_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_STATISTIC_ID): str,
    }
)


async def async_services(hass: HomeAssistant):
    """Register services."""

    @callback
    async def async_reload_history(  # pylint: disable=too-many-function-args
        call: ServiceCall,
    ) -> None:
        """Load datas in statics table."""
        entry = hass.data[DOMAIN].get(call.data[CONF_ENTRY])
        service = call.data[CONF_SERVICE]
        options = entry.config_entry.options
        start_date = call.data[CONF_START_DATE]
        end_date = call.data[CONF_END_DATE]
        mode = (
            CONF_CONSUMPTION
            if service in [CONSUMPTION_DAILY, CONSUMPTION_DETAIL]
            else CONF_PRODUCTION
        )
        intervals = options.get(mode, {}).get(CONF_INTERVALS, {})
        intervals = [
            (interval[CONF_RULE_START_TIME], interval[CONF_RULE_END_TIME])
            for interval in intervals.values()
        ]

        # Set price
        if price := call.data.get(CONF_PRICE):
            prices = {"standard": {CONF_PRICE: price}}
            if len(intervals) != 0 and (off_price := call.data.get(CONF_OFF_PRICE)):
                prices.update({"offpeak": {CONF_PRICE: off_price}})
            else:
                intervals = []
        else:
            prices = options[mode].get(CONF_PRICINGS)

        # Get attributes
        attributes = map_attributes(mode, entry.pdl, intervals)

        api = EnedisByPDL(
            pdl=entry.pdl,
            token=options[CONF_AUTH][CONF_TOKEN],
            session=async_create_clientsession(hass),
            timeout=30,
        )
        # Set collector
        api.set_collects(
            service,
            start=start_date,
            end=end_date,
            intervals=intervals,
            prices=prices,
        )
        # Update datas
        await api.async_update_collects()
        # Add statistics in HA Database
        await async_add_statistics(hass, attributes, api.stats)
        await _async_normalize_datas(attributes)

    @callback
    async def async_clear(call: ServiceCall) -> None:
        """Clear data in database."""
        statistic_id = call.data[CONF_STATISTIC_ID]
        if not statistic_id.startswith("myelectricaldata:"):
            _LOGGER.error("Statistic_id is incorrect %s", statistic_id)
            return
        get_instance(hass).async_clear_statistics([statistic_id])

    async def _async_normalize_datas(attributes) -> None:
        """Fix statistics datas."""
        for statistic_id, attrs in attributes.items():
            rslt = await get_instance(hass).async_add_executor_job(
                statistics_during_period,
                hass,
                dt_util.as_local(dt.fromtimestamp(0)),
                dt_util.now(),
                {statistic_id},
                "hour",
                UnitOfEnergy.KILO_WATT_HOUR,
                {"state", "sum"},
            )

            metadata = StatisticMetaData(
                has_mean=False,
                has_sum=True,
                name=attrs["friendly_name"],
                source=DOMAIN,
                statistic_id=statistic_id,
                unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            )

            for values in rslt.values():
                vsum = None
                stats = []
                for val in values:
                    vsum = (
                        val.get("state", 0)
                        if vsum is None
                        else vsum + val.get("state", 0)
                    )
                    stats.append(
                        StatisticData(
                            start=dt_util.utc_from_timestamp(val["start"]),
                            state=val["state"],
                            sum=vsum,
                        )
                    )
            instance = get_instance(hass)
            instance.async_clear_statistics([statistic_id])
            await instance.async_add_executor_job(
                async_add_external_statistics, hass, metadata, stats
            )

    hass.services.async_register(
        DOMAIN, FETCH_SERVICE, async_reload_history, schema=HISTORY_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, CLEAR_SERVICE, async_clear, schema=CLEAR_SERVICE_SCHEMA
    )
