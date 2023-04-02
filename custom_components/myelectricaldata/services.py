"""Helper module."""
from __future__ import annotations

import logging

from myelectricaldatapy import EnedisByPDL
import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import clear_statistics
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant, ServiceCall, callback
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
    CONF_PRICE,
    CONF_PRICINGS,
    CONF_PRODUCTION,
    CONF_SERVICE,
    CONF_START_DATE,
    CONF_STATISTIC_ID,
    CONSUMPTION_DAILY,
    CONSUMPTION_DETAIL,
    DOMAIN,
    FETCH_SERVICE,
)
from .coordinator import async_add_statistics, async_set_cumsums, get_attributes

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


@callback
async def async_services(hass: HomeAssistant):
    """Register services."""

    async def async_reload_history(call: ServiceCall) -> None:
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
        has_intervals = True if options.get(mode, {}).get(CONF_INTERVALS) else False

        if price := call.data.get(CONF_PRICE):
            pricings = {"standard": {CONF_PRICE: price}}
            if has_intervals and (off_price := call.data.get(CONF_OFF_PRICE)):
                pricings.update({"offpeak": {CONF_PRICE: off_price}})
            else:
                has_intervals = False
        else:
            pricings = options[mode].get(CONF_PRICINGS)

        api = EnedisByPDL(
            pdl=entry.pdl,
            token=options[CONF_AUTH][CONF_TOKEN],
            session=async_create_clientsession(hass),
            timeout=30,
        )

        modes = {mode: {"start": start_date, "end": end_date, "service": service}}
        # Get attributes
        attributes = get_attributes(mode, entry.pdl, has_intervals)
        # Cumulative summary
        await async_set_cumsums(hass, api, mode, attributes, service, pricings)
        # Update datas
        await api.async_update(modes=modes, force_refresh=True)
        # Add statistics in HA Database
        await async_add_statistics(hass, {mode: attributes}, api.stats)

    async def async_clear(call: ServiceCall) -> None:
        """Clear data in database."""
        statistic_id = call.data[CONF_STATISTIC_ID]
        if not statistic_id.startswith("myelectricaldata:"):
            _LOGGER.error("statistic_id is incorrect %s", statistic_id)
            return
        hass.async_add_executor_job(
            clear_statistics, get_instance(hass), [statistic_id]
        )

    hass.services.async_register(
        DOMAIN, FETCH_SERVICE, async_reload_history, schema=HISTORY_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, CLEAR_SERVICE, async_clear, schema=CLEAR_SERVICE_SCHEMA
    )
