"""Tests for custom_components.myelectricaldata.config_flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_TOKEN
from homeassistant.data_entry_flow import FlowResultType
from myelectricaldatapy import (
    ATTR_HPHC,
    ATTR_INTERVALS,
    ATTR_STANDARD,
    ATTR_TEMPO,
    DAILY_CONSUM,
    DETAIL_CONSUM,
    EnedisException,
)

from custom_components.myelectricaldata.config_flow import default_settings
from custom_components.myelectricaldata.const import (
    CONF_AUTH,
    CONF_CONSUMPTION,
    CONF_ECOWATT,
    CONF_PDL,
    CONF_PRODUCTION,
    CONF_RULE_END_TIME,
    CONF_RULE_ID,
    CONF_RULE_NEW_ID,
    CONF_RULE_START_TIME,
    CONF_SERVICE,
    CONF_SUBSCRIPTION,
    DEFAULT_CONSUMPTION_TEMPO,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def auto_setup(recorder_mock, enable_custom_integrations):
    """Make the custom component (and its recorder dep) loadable.

    ``recorder_mock`` is resolved before ``hass`` is fully initialized since
    pytest resolves this fixture's dependency subtree first.
    """
    return


@pytest.fixture(autouse=True)
def mock_setup_entry():
    """Keep entry setup inert: the flow tests only care about the flow itself."""
    with patch(
        "custom_components.myelectricaldata.async_setup_entry", return_value=True
    ):
        yield


@pytest.fixture(autouse=True)
def mock_enedis():
    """Patch the Enedis client used by the config/options flow."""
    with patch("custom_components.myelectricaldata.config_flow.Enedis") as mock_cls:
        instance = mock_cls.return_value
        instance.async_has_access = AsyncMock(return_value=True)
        instance.async_get_tempo_prices = AsyncMock(return_value=None)
        instance.async_get_contract = AsyncMock(return_value=None)
        instance.offpeaks = []
        yield instance


# ---------------------------------------------------------------------------
# User step
# ---------------------------------------------------------------------------


async def test_user_step_shows_form_initially(hass):
    """Without input, the user step just shows the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_step_creates_entry(hass, pdl):
    """A standard-subscription flow walks pricing + production pricing to an entry."""
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
            CONF_SUBSCRIPTION: ATTR_STANDARD,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pricing"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {ATTR_STANDARD: 0.17}
    )
    assert result["step_id"] == "production_pricing"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {ATTR_STANDARD: 0.06}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_PDL: pdl}
    assert result["options"][CONF_AUTH][CONF_TOKEN] == "a-token"
    assert result["options"][CONF_PRODUCTION][CONF_SERVICE] == "daily_production"
    assert result["options"][CONF_CONSUMPTION][CONF_SERVICE] == "daily_consumption"


async def test_user_step_connection_error_shows_form_with_error(
    hass, pdl, mock_enedis
):
    """A connection failure surfaces cannot_connect and re-shows the form."""
    mock_enedis.async_has_access = AsyncMock(
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
            CONF_SUBSCRIPTION: ATTR_STANDARD,
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
            CONF_SUBSCRIPTION: ATTR_STANDARD,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_step_tempo_subscription_goes_through_intervals(hass, pdl):
    """A Tempo consumption flow inserts the offpeak-intervals review step."""
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
            CONF_CONSUMPTION: True,
            CONF_SUBSCRIPTION: ATTR_TEMPO,
        },
    )
    assert result["step_id"] == "pricing"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "s_blue": 0.1,
            "s_white": 0.2,
            "s_red": 0.6,
            "o_blue": 0.09,
            "o_white": 0.15,
            "o_red": 0.5,
        },
    )
    assert result["step_id"] == "intervals"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_CONSUMPTION][CONF_SERVICE] == DETAIL_CONSUM
    assert result["options"][CONF_CONSUMPTION][ATTR_INTERVALS]


# ---------------------------------------------------------------------------
# default_settings
# ---------------------------------------------------------------------------


def test_default_settings_untouched_for_standard_subscription():
    """A Standard subscription leaves the consumption service untouched."""
    data = {
        CONF_AUTH: {CONF_SUBSCRIPTION: ATTR_STANDARD},
        CONF_CONSUMPTION: {CONF_SERVICE: DAILY_CONSUM},
    }
    result = default_settings(data)
    assert result[CONF_CONSUMPTION][CONF_SERVICE] == DAILY_CONSUM


@pytest.mark.parametrize("subscription", [ATTR_HPHC, ATTR_TEMPO])
def test_default_settings_forces_detail_service(subscription):
    """HP/HC and Tempo force the detail service with default offpeak intervals."""
    data = {
        CONF_AUTH: {CONF_SUBSCRIPTION: subscription},
        CONF_CONSUMPTION: {CONF_SERVICE: DAILY_CONSUM},
    }
    result = default_settings(data)
    assert result[CONF_CONSUMPTION][CONF_SERVICE] == DETAIL_CONSUM
    assert (
        result[CONF_CONSUMPTION][ATTR_INTERVALS]
        == DEFAULT_CONSUMPTION_TEMPO[ATTR_INTERVALS]
    )


def test_default_settings_noop_when_already_detail():
    """Already-detail consumption keeps its own intervals untouched."""
    data = {
        CONF_AUTH: {CONF_SUBSCRIPTION: ATTR_TEMPO},
        CONF_CONSUMPTION: {
            CONF_SERVICE: DETAIL_CONSUM,
            ATTR_INTERVALS: {"1": {"a": 1}},
        },
    }
    result = default_settings(data)
    assert result[CONF_CONSUMPTION][ATTR_INTERVALS] == {"1": {"a": 1}}


def test_default_settings_noop_without_consumption():
    """No consumption configured at all: nothing to force."""
    data = {CONF_AUTH: {CONF_SUBSCRIPTION: ATTR_TEMPO}}
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


async def test_options_flow_authentication_then_pricing_returns_to_menu(
    hass, config_entry
):
    """Authentication chains into the pricing form, which returns to the menu."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": CONF_AUTH}
    )
    assert result["step_id"] == CONF_AUTH

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TOKEN: "new-token", CONF_ECOWATT: True, CONF_SUBSCRIPTION: ATTR_STANDARD},
    )
    assert result["step_id"] == "pricing"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {ATTR_STANDARD: 0.2}
    )
    assert result["type"] is FlowResultType.MENU


async def test_options_flow_save_creates_entry(hass, config_entry):
    """The save step commits the accumulated options."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "save"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "last_update" in result["data"]


async def test_options_flow_consumption_step_updates_service(hass, config_entry):
    """Submitting the consumption step (without an interval) returns to the menu."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": CONF_CONSUMPTION}
    )
    assert result["step_id"] == CONF_CONSUMPTION

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SERVICE: DETAIL_CONSUM}
    )
    assert result["type"] is FlowResultType.MENU


async def test_options_flow_add_new_interval_shows_rules_form(hass, config_entry):
    """Selecting the 'add new interval' entry shows the rules form."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": CONF_CONSUMPTION}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SERVICE: DETAIL_CONSUM, ATTR_INTERVALS: CONF_RULE_NEW_ID},
    )
    assert result["step_id"] == "rules"


async def test_options_flow_rules_add_then_delete_interval(hass, config_entry):
    """A new interval can be added, then deleted, via the rules form."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": CONF_CONSUMPTION}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SERVICE: DETAIL_CONSUM, ATTR_INTERVALS: CONF_RULE_NEW_ID},
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

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SERVICE: DETAIL_CONSUM, ATTR_INTERVALS: "1"},
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
