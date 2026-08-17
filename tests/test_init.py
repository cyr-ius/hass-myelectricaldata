"""Tests for the custom_components.myelectricaldata top-level package."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from custom_components.myelectricaldata import async_unload_entry
from custom_components.myelectricaldata.const import PLATFORMS


async def test_async_setup_entry_forwards_platforms_and_registers_services(
    recorder_mock, enable_custom_integrations, hass, config_entry, pdl
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
        # OffpeakSensor schedules its own periodic local-refresh timer (see
        # binary_sensor.py) instead of using async_on_remove, so it wouldn't
        # get cleaned up here; not what this test is about, so keep it inert.
        patch(
            "custom_components.myelectricaldata.binary_sensor.async_track_time_interval",
            return_value=lambda: None,
        ),
    ):
        coordinator = mock_coordinator_cls.return_value
        coordinator.async_config_entry_first_refresh = AsyncMock()
        # Give the mocked coordinator real (empty) containers so the sensor,
        # binary_sensor and number platforms can iterate over it when
        # forwarded during setup.
        coordinator.pdl = pdl
        coordinator.data = {}
        coordinator.price_items = []
        coordinator.access = {}
        coordinator.contract = {}
        coordinator.tempo_day = None
        coordinator.ecowatt_day = None
        coordinator.last_access = None
        coordinator.last_refresh = None

        result = await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        assert result is True
        coordinator.async_config_entry_first_refresh.assert_awaited_once()
        assert config_entry.runtime_data is coordinator
        mock_services.assert_awaited_once_with(hass)


async def test_async_unload_entry_unloads_platforms(hass, config_entry):
    """Unloading delegates to hass.config_entries.async_unload_platforms."""
    config_entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
    ) as mock_unload:
        result = await async_unload_entry(hass, config_entry)

    assert result is True
    mock_unload.assert_awaited_once_with(config_entry, PLATFORMS)
