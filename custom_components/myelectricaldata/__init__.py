"""The MyElectricalData integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from myelectricaldatapy import (
    ATTR_HPHC,
    ATTR_INTERVALS,
    ATTR_OFFPEAK,
    ATTR_PRICE,
    ATTR_PRICES,
    ATTR_STANDARD,
    ATTR_TEMPO,
    DETAIL_CONSUM,
)

from .const import (
    CONF_AUTH,
    CONF_CONSUMPTION,
    CONF_OFF_PRICE,
    CONF_PRODUCTION,
    CONF_SERVICE,
    CONF_SUBSCRIPTION,
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

        if auth.pop(ATTR_TEMPO, False):
            subscriber = ATTR_TEMPO
        elif consumption.get(ATTR_INTERVALS):
            subscriber = ATTR_HPHC
        else:
            subscriber = ATTR_STANDARD
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
        subscription = auth.get(CONF_SUBSCRIPTION, ATTR_STANDARD)
        if subscription == ATTR_HPHC:
            auth.setdefault(ATTR_PRICE, DEFAULT_HP_PRICE)
            auth.setdefault(CONF_OFF_PRICE, DEFAULT_HC_PRICE)
        elif subscription == ATTR_TEMPO:
            auth.setdefault("pricings", DEFAULT_CONSUMPTION_TEMPO[ATTR_PRICES])
        else:
            auth.setdefault(ATTR_PRICE, DEFAULT_CC_PRICE)
        options[CONF_AUTH] = auth

        if production := options.get(CONF_PRODUCTION):
            production = {**production}
            production.setdefault(ATTR_PRICE, DEFAULT_PC_PRICE)
            options[CONF_PRODUCTION] = production

        hass.config_entries.async_update_entry(entry, options=options, version=3)
        _LOGGER.debug("Migrated entry %s to version 3", entry.entry_id)

    if entry.version == 3:
        # v3 kept the consumption tariff inside CONF_AUTH: a flat "price"
        # (standard), a "price"/"off_price" pair (HP/HC) or a "pricings"
        # colour map (Tempo). Tariffs now live under CONF_CONSUMPTION /
        # CONF_PRODUCTION, each shaped like myelectricaldatapy's Prices model.
        # Any stray "pricings" key left under those sections is dropped.
        options = {**entry.options}
        auth = {**options.get(CONF_AUTH, {})}

        price = auth.pop(ATTR_PRICE, None)
        off_price = auth.pop(CONF_OFF_PRICE, None)
        pricings = auth.pop("pricings", None)
        if pricings is not None:
            consum_prices = pricings
        elif off_price is not None:
            consum_prices = {
                ATTR_STANDARD: {ATTR_PRICE: price},
                ATTR_OFFPEAK: {ATTR_PRICE: off_price},
            }
        else:
            fallback = price if price is not None else DEFAULT_CC_PRICE
            consum_prices = {ATTR_STANDARD: {ATTR_PRICE: fallback}}
        options[CONF_AUTH] = auth

        consumption = {**options.get(CONF_CONSUMPTION, {})}
        consumption.pop("pricings", None)
        consumption.setdefault(ATTR_PRICES, consum_prices)
        if (
            consumption.get(CONF_SERVICE)
            and auth.get(CONF_SUBSCRIPTION) in (ATTR_HPHC, ATTR_TEMPO)
            and consumption.get(CONF_SERVICE) != DETAIL_CONSUM
        ):
            # HP/HC and Tempo are billed from the load curve only; older
            # installs could still pair them with daily_consumption. Force the
            # detail service (with default offpeak windows) so the full/offpeak
            # sensors match the collected data.
            consumption[CONF_SERVICE] = DETAIL_CONSUM
            consumption.setdefault(
                ATTR_INTERVALS, DEFAULT_CONSUMPTION_TEMPO[ATTR_INTERVALS]
            )
        options[CONF_CONSUMPTION] = consumption

        if production := options.get(CONF_PRODUCTION):
            production = {**production}
            production.pop("pricings", None)
            prod_price = production.pop(ATTR_PRICE, DEFAULT_PC_PRICE)
            production.setdefault(
                ATTR_PRICES, {ATTR_STANDARD: {ATTR_PRICE: prod_price}}
            )
            options[CONF_PRODUCTION] = production

        hass.config_entries.async_update_entry(entry, options=options, version=4)
        _LOGGER.debug("Migrated entry %s to version 4", entry.entry_id)

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
