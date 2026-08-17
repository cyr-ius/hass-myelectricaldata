"""Tests for custom_components.myelectricaldata.config_flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_TOKEN
from homeassistant.data_entry_flow import FlowResultType
from myelectricaldatapy import EnedisException

from custom_components.myelectricaldata.config_flow import default_settings
from custom_components.myelectricaldata.const import (
    CONF_AUTH,
    CONF_CONSUMPTION,
    CONF_ECOWATT,
    CONF_INTERVALS,
    CONF_PDL,
    CONF_PRODUCTION,
    CONF_RULE_END_TIME,
    CONF_RULE_ID,
    CONF_RULE_NEW_ID,
    CONF_RULE_START_TIME,
    CONF_SERVICE,
    CONF_TEMPO,
    CONSUMPTION_DAILY,
    CONSUMPTION_DETAIL,
    DEFAULT_CONSUMPTION_TEMPO,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def auto_setup(recorder_mock, enable_custom_integrations):
    """Make the myelectricaldata custom component (and its recorder dep) loadable.

    recorder_mock must be resolved before hass is fully initialized (pytest
    resolves its dependency subtree first since it's listed first here), so
    it stays ahead of enable_custom_integrations, which merely reuses the
    already-cached hass instance.
    """
    return


@pytest.fixture(autouse=True)
def mock_enedis_has_access():
    """Patch Enedis.async_has_access used by the user step."""
    with patch(
        "custom_components.myelectricaldata.config_flow.Enedis"
    ) as mock_cls:
        instance = mock_cls.return_value
        instance.async_has_access = AsyncMock(return_value=True)
        yield instance


async def test_user_step_shows_form_initially(hass):
    """Without input, the user step just shows the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_step_creates_entry(hass, pdl):
    """A successful token check creates the config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_PDL: pdl,
            CONF_TOKEN: "a-token",
            CONF_ECOWATT: False,
            CONF_PRODUCTION: True,
            CONF_CONSUMPTION: True,
            CONF_TEMPO: False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_PDL: pdl}
    assert result["options"][CONF_AUTH][CONF_TOKEN] == "a-token"
    assert result["options"][CONF_PRODUCTION][CONF_SERVICE] == "daily_production"
    assert result["options"][CONF_CONSUMPTION][CONF_SERVICE] == "daily_consumption"


async def test_user_step_connection_error_shows_form_with_error(
    hass, pdl, mock_enedis_has_access
):
    """A connection failure surfaces cannot_connect and re-shows the form."""
    mock_enedis_has_access.async_has_access = AsyncMock(
        side_effect=EnedisException(500, {"detail": "boom"})
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_PDL: pdl,
            CONF_TOKEN: "a-token",
            CONF_ECOWATT: False,
            CONF_PRODUCTION: False,
            CONF_CONSUMPTION: False,
            CONF_TEMPO: False,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_step_aborts_on_duplicate_pdl(hass, pdl, config_entry):
    """A PDL matching an already-configured entry aborts the flow."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_PDL: pdl,
            CONF_TOKEN: "a-token",
            CONF_ECOWATT: False,
            CONF_PRODUCTION: False,
            CONF_CONSUMPTION: False,
            CONF_TEMPO: False,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ---------------------------------------------------------------------------
# default_settings
# ---------------------------------------------------------------------------


def test_default_settings_untouched_without_tempo():
    """Without Tempo enabled, consumption settings pass through untouched."""
    data = {
        CONF_AUTH: {CONF_TEMPO: False},
        CONF_CONSUMPTION: {CONF_SERVICE: CONSUMPTION_DAILY},
    }
    result = default_settings(data)
    assert result[CONF_CONSUMPTION][CONF_SERVICE] == CONSUMPTION_DAILY


def test_default_settings_forces_detail_service_when_tempo_enabled():
    """Tempo enabled forces the detail service with default offpeak intervals."""
    data = {
        CONF_AUTH: {CONF_TEMPO: True},
        CONF_CONSUMPTION: {CONF_SERVICE: CONSUMPTION_DAILY},
    }
    result = default_settings(data)
    assert result[CONF_CONSUMPTION][CONF_SERVICE] == CONSUMPTION_DETAIL
    assert (
        result[CONF_CONSUMPTION][CONF_INTERVALS]
        == DEFAULT_CONSUMPTION_TEMPO[CONF_INTERVALS]
    )


def test_default_settings_noop_when_already_detail_with_tempo():
    """Already-detail consumption with Tempo enabled is left untouched."""
    data = {
        CONF_AUTH: {CONF_TEMPO: True},
        CONF_CONSUMPTION: {CONF_SERVICE: CONSUMPTION_DETAIL, CONF_INTERVALS: {"1": {}}},
    }
    result = default_settings(data)
    assert result[CONF_CONSUMPTION][CONF_INTERVALS] == {"1": {}}


def test_default_settings_noop_without_consumption():
    """No consumption configured at all: nothing to force."""
    data = {CONF_AUTH: {CONF_TEMPO: True}}
    result = default_settings(data)
    assert CONF_CONSUMPTION not in result


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


async def test_options_flow_init_shows_menu(hass, config_entry):
    """The options flow entry point is a menu."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"


async def test_options_flow_authentication_updates_and_returns_to_menu(
    hass, config_entry
):
    """Submitting the authentication step updates internal state and shows the menu."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": CONF_AUTH},
    )
    assert result["step_id"] == CONF_AUTH

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TOKEN: "new-token", CONF_ECOWATT: True, CONF_TEMPO: False},
    )
    assert result["type"] is FlowResultType.MENU


async def test_options_flow_save_creates_entry(hass, config_entry):
    """The save step commits the accumulated options."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "save"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "last_update" in result["data"]


async def test_options_flow_consumption_step_updates_service(hass, config_entry):
    """Submitting the consumption step (without an interval) returns to the menu."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": CONF_CONSUMPTION},
    )
    assert result["step_id"] == CONF_CONSUMPTION

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SERVICE: CONSUMPTION_DETAIL},
    )
    assert result["type"] is FlowResultType.MENU


async def test_options_flow_add_new_interval_shows_rules_form(hass, config_entry):
    """Selecting the 'add new interval' entry shows the rules form."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": CONF_CONSUMPTION},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SERVICE: CONSUMPTION_DETAIL, CONF_INTERVALS: CONF_RULE_NEW_ID},
    )
    assert result["step_id"] == "rules"


async def test_options_flow_rules_add_then_delete_interval(hass, config_entry):
    """A new interval can be added, then deleted, via the rules form."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": CONF_CONSUMPTION},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SERVICE: CONSUMPTION_DETAIL, CONF_INTERVALS: CONF_RULE_NEW_ID},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "step_id": CONF_CONSUMPTION,
            CONF_RULE_ID: "1",
            CONF_RULE_START_TIME: "01:00:00",
            CONF_RULE_END_TIME: "06:00:00",
        },
    )
    assert result["step_id"] == CONF_CONSUMPTION

    # Re-open the interval to delete it.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SERVICE: CONSUMPTION_DETAIL, CONF_INTERVALS: "1"},
    )
    assert result["step_id"] == "rules"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "step_id": CONF_CONSUMPTION,
            CONF_RULE_START_TIME: "01:00:00",
            CONF_RULE_END_TIME: "06:00:00",
            "rule_delete": True,
        },
    )
    assert result["step_id"] == CONF_CONSUMPTION
