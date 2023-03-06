"""Helper module."""
from __future__ import annotations

import logging

from myelectricaldatapy import EnedisByPDL, EnedisException
import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import clear_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import homeassistant.helpers.config_validation as cv

from .const import (
    CLEAR_SERVICE,
    CONF_AUTH,
    CONF_END_DATE,
    CONF_ENTRY,
    CONF_PDL,
    CONF_POWER_MODE,
    CONF_SERVICE,
    CONF_START_DATE,
    CONF_STATISTIC_ID,
    DOMAIN,
    FETCH_SERVICE,
)
from .coordinator import EnedisDataUpdateCoordinator, async_statistics

_LOGGER = logging.getLogger(__name__)

HISTORY_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ENTRY): str,
        vol.Optional(CONF_POWER_MODE): str,
        vol.Optional(CONF_START_DATE): cv.date,
        vol.Optional(CONF_END_DATE): cv.date,
    }
)
CLEAR_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_STATISTIC_ID): str,
    }
)


@callback
async def async_services(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: EnedisDataUpdateCoordinator
):
    """Register services."""

    async def async_reload_history(call: ServiceCall) -> None:
        """Load datas in statics table."""
        entry = hass.data[DOMAIN].get(call.data[CONF_ENTRY])
        power_mode = call.data[CONF_POWER_MODE]
        option = entry.config_entry.options.get(power_mode, {})
        option.update({CONF_POWER_MODE: power_mode, CONF_PDL: entry.pdl})

        # Fetch datas
        dataset = {}
        try:
            api = EnedisByPDL(
                token=entry.config_entry.options[CONF_AUTH][CONF_TOKEN],
                session=async_create_clientsession(hass),
                timeout=30,
            )
            dataset = await api.async_fetch_datas(
                option[CONF_SERVICE],
                entry.pdl,
                call.data[CONF_START_DATE],
                call.data[CONF_END_DATE],
            )
        except EnedisException as error:
            raise EnedisException(error) from error
        finally:
            dataset = dataset.get("meter_reading", {}).get("interval_reading", [])

        # Add statistics in HA Database
        await async_statistics(hass, dataset, True, **option)

    async def async_clear(call: ServiceCall) -> None:
        """Clear data in database."""
        statistic_id = call.data[CONF_STATISTIC_ID]
        if not statistic_id.startswith("enedis:"):
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
