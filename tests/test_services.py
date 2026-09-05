"""Tests for custom_components.myelectricaldata.services."""

from __future__ import annotations

from datetime import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from myelectricaldatapy import ATTR_PRICE, ATTR_STANDARD, DAILY_CONSUM

from custom_components.myelectricaldata.const import (
    CLEAR_SERVICE,
    CONF_END_DATE,
    CONF_ENTRY,
    CONF_SERVICE,
    CONF_START_DATE,
    CONF_STATISTIC_ID,
    DOMAIN,
    FETCH_SERVICE,
    REBUILD_SERVICE,
)
from custom_components.myelectricaldata.services import async_services


def _make_api_mock() -> MagicMock:
    api = MagicMock()
    api.set_data_fetch = MagicMock()
    api.async_update_collects = AsyncMock()
    api.has_collected = True
    api.stats = {}
    return api


async def test_async_services_registers_all_services(hass):
    """fetch_data, clear_data and rebuild_data all get registered."""
    await async_services(hass)
    assert hass.services.has_service(DOMAIN, FETCH_SERVICE)
    assert hass.services.has_service(DOMAIN, CLEAR_SERVICE)
    assert hass.services.has_service(DOMAIN, REBUILD_SERVICE)


async def test_reload_history_raises_when_entry_missing(hass):
    """Calling fetch_data with an unknown entry_id raises a validation error."""
    await async_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            FETCH_SERVICE,
            {
                CONF_ENTRY: "unknown-entry-id",
                CONF_SERVICE: DAILY_CONSUM,
                CONF_START_DATE: dt(2026, 1, 1),  # noqa: DTZ001
                CONF_END_DATE: dt(2026, 1, 2),  # noqa: DTZ001
            },
            blocking=True,
        )


async def test_reload_history_uses_explicit_price_and_imports_statistics(
    recorder_mock, hass, config_entry
):
    """An explicit price override is used and results get imported + rebuilt."""
    config_entry.add_to_hass(hass)
    await async_services(hass)

    api = _make_api_mock()
    with (
        patch(
            "custom_components.myelectricaldata.services.EnedisByPDL", return_value=api
        ),
        patch(
            "custom_components.myelectricaldata.services.async_import_sensor_statistics",
            new=AsyncMock(),
        ) as mock_import,
        patch(
            "custom_components.myelectricaldata.services.async_rebuild_statistics",
            new=AsyncMock(),
        ) as mock_rebuild,
    ):
        await hass.services.async_call(
            DOMAIN,
            FETCH_SERVICE,
            {
                CONF_ENTRY: config_entry.entry_id,
                CONF_SERVICE: DAILY_CONSUM,
                CONF_START_DATE: dt(2026, 1, 1),  # noqa: DTZ001
                CONF_END_DATE: dt(2026, 1, 2),  # noqa: DTZ001
                ATTR_PRICE: 0.2,
            },
            blocking=True,
        )

    api.async_update_collects.assert_awaited_once()
    mock_import.assert_awaited_once()
    mock_rebuild.assert_awaited_once()
    prices = api.set_data_fetch.call_args.kwargs["prices"]
    assert prices[ATTR_STANDARD][ATTR_PRICE] == 0.2


async def test_reload_history_skips_import_when_nothing_collected(
    recorder_mock, hass, config_entry
):
    """When the API reports nothing collected, statistics aren't imported."""
    config_entry.add_to_hass(hass)
    await async_services(hass)

    api = _make_api_mock()
    api.has_collected = False
    with (
        patch(
            "custom_components.myelectricaldata.services.EnedisByPDL", return_value=api
        ),
        patch(
            "custom_components.myelectricaldata.services.async_import_sensor_statistics",
            new=AsyncMock(),
        ) as mock_import,
    ):
        await hass.services.async_call(
            DOMAIN,
            FETCH_SERVICE,
            {
                CONF_ENTRY: config_entry.entry_id,
                CONF_SERVICE: DAILY_CONSUM,
                CONF_START_DATE: dt(2026, 1, 1),  # noqa: DTZ001
                CONF_END_DATE: dt(2026, 1, 2),  # noqa: DTZ001
                ATTR_PRICE: 0.2,
            },
            blocking=True,
        )

    mock_import.assert_not_called()


async def test_clear_service_rejects_foreign_statistic_id(hass):
    """A statistic_id that doesn't belong to this integration is rejected."""
    await async_services(hass)
    with patch(
        "custom_components.myelectricaldata.services.get_instance"
    ) as mock_get_instance:
        await hass.services.async_call(
            DOMAIN,
            CLEAR_SERVICE,
            {CONF_STATISTIC_ID: "sensor.other_integration_value"},
            blocking=True,
        )
        mock_get_instance.return_value.async_clear_statistics.assert_not_called()


async def test_clear_service_clears_matching_statistic_id(hass):
    """A statistic_id owned by this integration is forwarded to the recorder."""
    await async_services(hass)
    statistic_id = f"sensor.{DOMAIN}_12345_consumption_standard"
    with patch(
        "custom_components.myelectricaldata.services.get_instance"
    ) as mock_get_instance:
        await hass.services.async_call(
            DOMAIN,
            CLEAR_SERVICE,
            {CONF_STATISTIC_ID: statistic_id},
            blocking=True,
        )
        mock_get_instance.return_value.async_clear_statistics.assert_called_once_with(
            [statistic_id]
        )


async def test_clear_service_with_range_uses_range_clear(hass):
    """A start/end pair routes to the range-limited clear helper."""
    await async_services(hass)
    statistic_id = f"sensor.{DOMAIN}_12345_consumption_standard"
    with patch(
        "custom_components.myelectricaldata.services.async_clear_statistics_range"
    ) as mock_range:
        await hass.services.async_call(
            DOMAIN,
            CLEAR_SERVICE,
            {
                CONF_STATISTIC_ID: statistic_id,
                CONF_START_DATE: dt(2026, 1, 1),  # noqa: DTZ001
            },
            blocking=True,
        )
    mock_range.assert_called_once()


async def test_rebuild_service_calls_rebuild_helper(hass):
    """rebuild_data forwards a single-item descriptor to async_rebuild_statistics."""
    await async_services(hass)
    statistic_id = f"sensor.{DOMAIN}_12345_consumption_standard_cost"
    with patch(
        "custom_components.myelectricaldata.services.async_rebuild_statistics",
        new=AsyncMock(),
    ) as mock_rebuild:
        await hass.services.async_call(
            DOMAIN,
            REBUILD_SERVICE,
            {CONF_STATISTIC_ID: statistic_id},
            blocking=True,
        )
    mock_rebuild.assert_awaited_once()
    item = mock_rebuild.await_args.args[1][0]
    assert item["entity_id"] == statistic_id
    assert item["kind"] == "cost"
