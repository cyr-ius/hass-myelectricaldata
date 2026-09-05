"""Tests for the custom_components.myelectricaldata top-level package."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_TOKEN
from myelectricaldatapy import (
    ATTR_HPHC,
    ATTR_INTERVALS,
    ATTR_PRICE,
    ATTR_PRICES,
    ATTR_STANDARD,
    ATTR_TEMPO,
    DETAIL_CONSUM,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myelectricaldata import (
    async_migrate_entry,
    async_unload_entry,
)
from custom_components.myelectricaldata.const import (
    CONF_AUTH,
    CONF_CONSUMPTION,
    CONF_OFF_PRICE,
    CONF_SERVICE,
    CONF_SUBSCRIPTION,
    DOMAIN,
    PLATFORMS,
)


async def test_async_setup_entry_forwards_platforms_and_registers_services(
    load_integration, hass, config_entry
):
    """Setup builds the coordinator, forwards platforms and registers services."""
    config_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.myelectricaldata.EnedisDataUpdateCoordinator"
        ) as mock_coordinator_cls,
        patch(
            "custom_components.myelectricaldata.async_services", new=AsyncMock()
        ) as mock_services,
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ) as mock_forward,
    ):
        coordinator = mock_coordinator_cls.return_value
        coordinator.async_config_entry_first_refresh = AsyncMock()

        result = await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert result is True
    coordinator.async_config_entry_first_refresh.assert_awaited_once()
    assert config_entry.runtime_data is coordinator
    mock_services.assert_awaited_once_with(hass)
    mock_forward.assert_awaited_once_with(config_entry, PLATFORMS)


async def test_async_unload_entry_unloads_platforms(hass, config_entry):
    """Unloading delegates to hass.config_entries.async_unload_platforms."""
    config_entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
    ) as mock_unload:
        result = await async_unload_entry(hass, config_entry)

    assert result is True
    mock_unload.assert_awaited_once_with(config_entry, PLATFORMS)


# ---------------------------------------------------------------------------
# async_migrate_entry
# ---------------------------------------------------------------------------


async def test_migrate_entry_v1_infers_subscription_from_tempo_flag(hass):
    """A v1 entry with the legacy tempo flag migrates to the tempo subscription."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={"pdl": "12345678901234"},
        options={
            CONF_AUTH: {CONF_TOKEN: "t", ATTR_TEMPO: True},
            CONF_CONSUMPTION: {},
        },
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_AUTH][CONF_SUBSCRIPTION] == ATTR_TEMPO


async def test_migrate_entry_v1_infers_hphc_from_intervals(hass):
    """A v1 entry with offpeak intervals migrates to the HP/HC subscription."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={"pdl": "12345678901234"},
        options={
            CONF_AUTH: {CONF_TOKEN: "t"},
            CONF_CONSUMPTION: {ATTR_INTERVALS: {"1": {}}},
        },
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_AUTH][CONF_SUBSCRIPTION] == ATTR_HPHC


async def test_migrate_entry_v3_moves_tariffs_under_consumption(hass):
    """A v3 HP/HC entry moves its price/off_price pair under CONF_CONSUMPTION."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        data={"pdl": "12345678901234"},
        options={
            CONF_AUTH: {
                CONF_TOKEN: "t",
                CONF_SUBSCRIPTION: ATTR_HPHC,
                ATTR_PRICE: 0.18,
                CONF_OFF_PRICE: 0.14,
            },
            CONF_CONSUMPTION: {CONF_SERVICE: "daily_consumption"},
        },
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 4
    prices = entry.options[CONF_CONSUMPTION][ATTR_PRICES]
    assert prices[ATTR_STANDARD][ATTR_PRICE] == 0.18
    assert prices["offpeak"][ATTR_PRICE] == 0.14
    # HP/HC can only be billed from the load curve.
    assert entry.options[CONF_CONSUMPTION][CONF_SERVICE] == DETAIL_CONSUM
