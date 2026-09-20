"""Tests for custom_components.myelectricaldata.repairs."""

from __future__ import annotations

from homeassistant.components.repairs import ConfirmRepairFlow
from homeassistant.data_entry_flow import FlowResultType
from myelectricaldatapy import ATTR_INTERVALS

from custom_components.myelectricaldata.const import (
    CONF_AUTO_OFFPEAK,
    CONF_CONSUMPTION,
    ISSUE_OFFPEAK_MISMATCH,
    ISSUE_OFFPEAK_UPDATED,
)
from custom_components.myelectricaldata.repairs import (
    OffpeakMismatchRepairFlow,
    async_create_fix_flow,
)


async def test_create_fix_flow_selects_flow_by_issue(hass, config_entry):
    """Only the mismatch issue gets the enabling flow."""
    data = {"entry_id": config_entry.entry_id, "offpeak_hours": "HC (1H30-7H30)"}
    assert isinstance(
        await async_create_fix_flow(hass, f"{ISSUE_OFFPEAK_MISMATCH}_x", data),
        OffpeakMismatchRepairFlow,
    )
    assert isinstance(
        await async_create_fix_flow(hass, f"{ISSUE_OFFPEAK_UPDATED}_x", None),
        ConfirmRepairFlow,
    )


async def test_mismatch_flow_enables_auto_mode(hass, config_entry):
    """Confirming enables the automatic mode and applies the contract's hours."""
    config_entry.add_to_hass(hass)
    flow = OffpeakMismatchRepairFlow(config_entry.entry_id, "HC (1H30-7H30)")
    flow.hass = hass

    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"

    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    consumption = config_entry.options[CONF_CONSUMPTION]
    assert consumption[CONF_AUTO_OFFPEAK] is True
    assert consumption[ATTR_INTERVALS] == {
        "1": {"rule_start_time": "01:30:00", "rule_end_time": "07:30:00"}
    }
