"""Helper module."""

from __future__ import annotations

import logging

from myelectricaldatapy import EnedisByPDL
import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import homeassistant.helpers.config_validation as cv

from .const import (
    CLEAR_SERVICE,
    CONF_AUTH,
    CONF_CONSUMPTION,
    CONF_END_DATE,
    CONF_ENTRY,
    CONF_INTERVALS,
    CONF_OFF_PRICE,
    CONF_PDL,
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
from .helpers import async_add_statistics, async_get_last_infos, map_attributes

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
    async def async_reload_history(call: ServiceCall) -> None:
        """Load data in statistics table."""
        entry = hass.config_entries.async_get_entry(call.data[CONF_ENTRY])
        if entry is None:
            raise ServiceValidationError("Config entry not found")
        service = call.data[CONF_SERVICE]
        options = entry.options
        pdl = entry.data[CONF_PDL]
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
        attributes = map_attributes(mode, pdl, intervals)

        api = EnedisByPDL(
            pdl=pdl,
            token=options[CONF_AUTH][CONF_TOKEN],
            session=async_create_clientsession(hass),
            timeout=30,
        )
        # Get last information from data
        attrs = map_attributes(mode, pdl, intervals)

        # Get last sum and price
        _, sum_values, sum_prices = await async_get_last_infos(hass, attrs)

        # Set api collector
        api.set_collects(
            service,
            start=start_date,
            end=end_date,
            intervals=intervals,
            prices=prices,
            cum_value=sum_values,
            cum_price=sum_prices,
        )

        # Update data
        await api.async_update_collects()
        # Add statistics in HA Database
        if api.has_collected:
            await async_add_statistics(hass, attributes, api.stats)

    @callback
    async def async_clear(call: ServiceCall) -> None:
        """Clear data in database."""
        statistic_id = call.data[CONF_STATISTIC_ID]
        if not statistic_id.startswith("myelectricaldata:"):
            _LOGGER.error("Statistic_id is incorrect %s", statistic_id)
            return
        get_instance(hass).async_clear_statistics([statistic_id])

    hass.services.async_register(
        DOMAIN, FETCH_SERVICE, async_reload_history, schema=HISTORY_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, CLEAR_SERVICE, async_clear, schema=CLEAR_SERVICE_SCHEMA
    )
