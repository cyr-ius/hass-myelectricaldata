"""The MyElectricalData integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AUTH,
    CONF_CONSUMPTION,
    CONF_HPHC,
    CONF_INTERVALS,
    CONF_OFF_PRICE,
    CONF_PRICE,
    CONF_PRICINGS,
    CONF_PRODUCTION,
    CONF_STD,
    CONF_SUBSCRIPTION,
    CONF_TEMPO,
    DEFAULT_CC_PRICE,
    DEFAULT_CONSUMPTION_TEMPO,
    DEFAULT_HC_PRICE,
    DEFAULT_HP_PRICE,
    DEFAULT_PC_PRICE,
    PLATFORMS,
)
from .coordinator import EnedisDataUpdateCoordinator
from .services import async_services

type MyElectricalDataConfigEntry = ConfigEntry[EnedisDataUpdateCoordinator]

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(
    hass: HomeAssistant, entry: MyElectricalDataConfigEntry
) -> bool:
    """Migrate old entry."""
    if entry.version == 1:
        options = {**entry.options}
        auth = {**options.get(CONF_AUTH, {})}
        consumption = options.get(CONF_CONSUMPTION, {})

        if auth.pop(CONF_TEMPO, False):
            subscriber = CONF_TEMPO
        elif consumption.get(CONF_INTERVALS):
            subscriber = CONF_HPHC
        else:
            subscriber = CONF_STD
        auth[CONF_SUBSCRIPTION] = subscriber
        options[CONF_AUTH] = auth

        hass.config_entries.async_update_entry(entry, options=options, version=2)
        _LOGGER.debug("Migrated entry %s to version 2", entry.entry_id)

    if entry.version == 2:
        # Tariffs moved from the (now removed) editable number entities into
        # the config entry itself, seeded here with the same hardcoded
        # values those entities used to default to, so existing installs
        # keep computing the same costs until the user edits them.
        options = {**entry.options}
        auth = {**options.get(CONF_AUTH, {})}
        subscription = auth.get(CONF_SUBSCRIPTION, CONF_STD)
        if subscription == CONF_HPHC:
            auth.setdefault(CONF_PRICE, DEFAULT_HP_PRICE)
            auth.setdefault(CONF_OFF_PRICE, DEFAULT_HC_PRICE)
        elif subscription == CONF_TEMPO:
            auth.setdefault(CONF_PRICINGS, DEFAULT_CONSUMPTION_TEMPO[CONF_PRICINGS])
        else:
            auth.setdefault(CONF_PRICE, DEFAULT_CC_PRICE)
        options[CONF_AUTH] = auth

        if production := options.get(CONF_PRODUCTION):
            production = {**production}
            production.setdefault(CONF_PRICE, DEFAULT_PC_PRICE)
            options[CONF_PRODUCTION] = production

        hass.config_entries.async_update_entry(entry, options=options, version=3)
        _LOGGER.debug("Migrated entry %s to version 3", entry.entry_id)

    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: MyElectricalDataConfigEntry
) -> bool:
    """Set up config entry."""
    coordinator = EnedisDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: MyElectricalDataConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: MyElectricalDataConfigEntry
) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
