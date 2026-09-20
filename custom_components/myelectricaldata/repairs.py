"""Repairs for MyElectricalData."""

from typing import cast

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from myelectricaldatapy.const import ATTR_INTERVALS

from .const import (
    CONF_AUTO_OFFPEAK,
    CONF_CONSUMPTION,
    ISSUE_OFFPEAK_MISMATCH,
)
from .helpers import parse_offpeak_hours


class OffpeakMismatchRepairFlow(RepairsFlow):
    """Offer to enable the automatic offpeak mode and apply the contract's hours."""

    def __init__(self, entry_id: str, offpeak_hours: str) -> None:
        """Initialize the flow."""
        self._entry_id = entry_id
        self._offpeak_hours = offpeak_hours

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Handle the first step of the fix flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Enable the automatic mode once the user confirmed."""
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is not None:
                options = {**entry.options}
                options[CONF_CONSUMPTION] = {
                    **options.get(CONF_CONSUMPTION, {}),
                    CONF_AUTO_OFFPEAK: True,
                    ATTR_INTERVALS: parse_offpeak_hours(self._offpeak_hours),
                }
                self.hass.config_entries.async_update_entry(entry, options=options)
            return self.async_create_entry(data={})

        return self.async_show_form(step_id="confirm")


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow matching an issue."""
    if issue_id.startswith(ISSUE_OFFPEAK_MISMATCH) and data:
        return OffpeakMismatchRepairFlow(
            cast(str, data["entry_id"]), cast(str, data["offpeak_hours"])
        )
    return ConfirmRepairFlow()
