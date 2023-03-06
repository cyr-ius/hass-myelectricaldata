"""Config flow to configure integration."""
from datetime import datetime as dt
import logging
from typing import Any

from myelectricaldatapy import EnedisByPDL, EnedisException
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_TOKEN
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TimeSelector,
    TimeSelectorConfig,
)

from .const import (
    CONF_CONSUMPTION,
    CONF_ECOWATT,
    CONF_PDL,
    CONF_PRICE_NEW_ID,
    CONF_PRICING_COST,
    CONF_PRICING_DELETE,
    CONF_PRICING_ID,
    CONF_PRICING_INTERVALS,
    CONF_PRICING_NAME,
    CONF_PRICINGS,
    CONF_PRODUCTION,
    CONF_RULE_DELETE,
    CONF_RULE_END_TIME,
    CONF_RULE_ID,
    CONF_RULE_NEW_ID,
    CONF_RULE_START_TIME,
    CONF_RULES,
    CONF_SERVICE,
    CONF_TEMPO,
    CONSUMPTION_DAILY,
    CONSUMPTION_DETAIL,
    DEFAULT_CC_PRICE,
    DEFAULT_CONSUMPTION,
    DEFAULT_PC_PRICE,
    DEFAULT_PRODUCTION,
    DEFAULT_CONSUMPTION_TEMPO,
    DOMAIN,
    PRODUCTION_DAILY,
    PRODUCTION_DETAIL,
    SAVE,
    CONF_AUTH,
)

PRODUCTION_CHOICE = [
    SelectOptionDict(value=PRODUCTION_DAILY, label="daily"),
    SelectOptionDict(value=PRODUCTION_DETAIL, label="detail"),
]
CONSUMPTION_CHOICE = [
    SelectOptionDict(value=CONSUMPTION_DAILY, label="daily"),
    SelectOptionDict(value=CONSUMPTION_DETAIL, label="detail"),
]


DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PDL): str,
        vol.Required(CONF_TOKEN): str,
        vol.Optional(CONF_ECOWATT): bool,
        vol.Optional(CONF_PRODUCTION): bool,
        vol.Optional(CONF_CONSUMPTION): bool,
        vol.Optional(CONF_TEMPO): bool,
    }
)

_LOGGER = logging.getLogger(__name__)


class EnedisFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a Enedis config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get option flow."""
        return EnedisOptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        errors = {}
        if user_input is not None:
            self._async_abort_entries_match({CONF_PDL: user_input[CONF_PDL]})
            api = EnedisByPDL(
                token=user_input[CONF_TOKEN],
                session=async_create_clientsession(self.hass),
                timeout=30,
            )
            try:
                await api.async_has_access(user_input[CONF_PDL])
            except EnedisException as error:
                _LOGGER.error(error)
                errors["base"] = "cannot_connect"
            else:
                opts = {
                    CONF_AUTH: {
                        CONF_TOKEN: user_input.get(CONF_TOKEN),
                        CONF_ECOWATT: user_input.get(CONF_ECOWATT),
                    }
                }
                if b_tempo := user_input[CONF_TEMPO]:
                    opts.update({CONF_CONSUMPTION: {CONF_TEMPO: b_tempo}})
                if user_input.get(CONF_PRODUCTION):
                    opts.update({CONF_PRODUCTION: {CONF_SERVICE: PRODUCTION_DAILY}})
                if user_input.get(CONF_CONSUMPTION):
                    opts.update({CONF_CONSUMPTION: {CONF_SERVICE: CONSUMPTION_DAILY}})

                options = default_settings(opts)
                return self.async_create_entry(
                    title=f"Linky ({user_input[CONF_PDL]})",
                    data=user_input,
                    options=options,
                )

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )


class EnedisOptionsFlowHandler(OptionsFlow):
    """Handle option."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        _auth: dict[str, Any] = config_entry.options.get(CONF_AUTH, {})
        _production: dict[str, Any] = config_entry.options.get(CONF_PRODUCTION, {})
        _consumption: dict[str, Any] = config_entry.options.get(CONF_CONSUMPTION, {})
        self._datas = {
            CONF_AUTH: _auth.copy(),
            CONF_PRODUCTION: _production.copy(),
            CONF_CONSUMPTION: _consumption.copy(),
        }
        self._conf_rule_id: int | None = None
        self._conf_pricing_id: int | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle options flow."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[CONF_AUTH, CONF_PRODUCTION, CONF_CONSUMPTION, SAVE],
        )

    async def async_step_authentication(self, user_input: dict[str, Any] | None = None):
        """Authentification step."""
        step_id = CONF_AUTH
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_TOKEN,
                    default=self._datas[step_id].get(
                        CONF_TOKEN, self.config_entry.data[CONF_TOKEN]
                    ),
                ): str,
                vol.Optional(
                    CONF_ECOWATT,
                    default=self._datas[step_id].get(CONF_ECOWATT, False),
                ): bool,
            }
        )
        if user_input is not None:
            self._datas[step_id].update(**user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id=step_id, data_schema=schema, last_step=False
        )

    async def async_step_production(self, user_input: dict[str, Any] | None = None):
        """Production step."""
        step_id = CONF_PRODUCTION
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SERVICE,
                    description={
                        "suggested_value": self._datas[step_id].get(CONF_SERVICE)
                    },
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=PRODUCTION_CHOICE,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                        translation_key="production_choice",
                    )
                ),
                vol.Optional(CONF_PRICINGS): SelectSelector(
                    SelectSelectorConfig(
                        options=self.get_pricing_list(step_id),
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        if user_input is not None:
            self._datas[step_id].update({CONF_SERVICE: user_input.get(CONF_SERVICE)})
            if sel_pricing := user_input.get(CONF_PRICINGS):
                return await self.async_step_pricings(None, sel_pricing, step_id)
            return await self.async_step_init()
        return self.async_show_form(
            step_id=step_id, data_schema=schema, last_step=False
        )

    async def async_step_consumption(self, user_input: dict[str, Any] | None = None):
        """Consumption step."""
        step_id = CONF_CONSUMPTION
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_TEMPO,
                    default=self._datas[step_id].get(CONF_TEMPO, False),
                ): bool,
                vol.Optional(
                    CONF_SERVICE,
                    description={
                        "suggested_value": self._datas[step_id].get(CONF_SERVICE)
                    },
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=CONSUMPTION_CHOICE,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                        translation_key="consumption_choice",
                    )
                ),
                vol.Optional(CONF_PRICINGS): SelectSelector(
                    SelectSelectorConfig(
                        options=self.get_pricing_list(step_id),
                        mode=SelectSelectorMode.LIST,
                        translation_key="pricing_key",
                    )
                ),
            }
        )
        if user_input is not None:
            self._datas[step_id].update({CONF_SERVICE: user_input.get(CONF_SERVICE)})
            self._datas[step_id].update({CONF_TEMPO: user_input[CONF_TEMPO]})
            if sel_pricing := user_input.get(CONF_PRICINGS):
                return await self.async_step_pricings(None, sel_pricing, step_id)
            return await self.async_step_init()
        return self.async_show_form(
            step_id=step_id, data_schema=schema, last_step=False
        )

    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult():
        """Save the updated options."""
        self._datas = default_settings(self._datas)
        self._datas.update({"last_update": dt.now()})

        return self.async_create_entry(title="", data=self._datas)

    async def async_step_pricings(
        self,
        user_input: dict[str, Any] | None = None,
        pricing_id: str | None = None,
        step_id: str | None = None,
    ) -> FlowResult:
        """Handle options flow for apps list."""
        if pricing_id is not None:
            self._conf_pricing_id = (
                pricing_id if pricing_id != CONF_PRICE_NEW_ID else None
            )
            return self._async_pricings_form(pricing_id, step_id)

        if user_input is not None:
            pricing_id = user_input.get(CONF_PRICING_ID, self._conf_pricing_id)
            step_id = user_input["step_id"]
            if pricing_id:
                pricings = self._datas[step_id].get(CONF_PRICINGS, {})
                if user_input.get(CONF_PRICING_DELETE, False):
                    pricings.pop(pricing_id)
                else:
                    intervals = pricings.get(pricing_id, {}).get(
                        CONF_PRICING_INTERVALS, {}
                    )
                    default_price = (
                        DEFAULT_CC_PRICE
                        if step_id == CONF_CONSUMPTION
                        else DEFAULT_PC_PRICE
                    )
                    pricings.update(
                        {
                            str(pricing_id): {
                                CONF_PRICING_NAME: user_input.get(CONF_PRICING_NAME),
                                CONF_PRICING_COST: float(
                                    user_input.get(CONF_PRICING_COST, default_price)
                                ),
                                CONF_PRICING_INTERVALS: intervals,
                            }
                        }
                    )

                    if self._datas[step_id].get(CONF_TEMPO):
                        pricings[pricing_id].update(
                            {
                                CONF_PRICING_COST: CONF_TEMPO,
                                "BLUE": user_input["BLUE"],
                                "WHITE": user_input["WHITE"],
                                "RED": user_input["RED"],
                            }
                        )

                    self._datas[step_id][CONF_PRICINGS] = pricings

                    if rule_id := user_input.get(CONF_RULES):
                        return await self.async_step_rules(
                            rule_id=rule_id, pricing_id=pricing_id, step_id=step_id
                        )

        if step_id == CONF_CONSUMPTION:
            return await self.async_step_consumption()
        else:
            return await self.async_step_production()

    @callback
    def _async_pricings_form(self, pricing_id: str, step_id: str) -> FlowResult:
        """Return configuration form for rules."""
        schema = {
            vol.Required("step_id"): step_id,
            vol.Optional(CONF_PRICING_NAME): str,
        }
        standard_schema = {
            vol.Optional(CONF_PRICING_COST): cv.positive_float,
        }
        tempo_schema = {
            vol.Optional("BLUE"): cv.positive_float,
            vol.Optional("WHITE"): cv.positive_float,
            vol.Optional("RED"): cv.positive_float,
        }

        if self._datas[step_id].get(CONF_TEMPO):
            schema.update(tempo_schema)
        else:
            schema.update(standard_schema)

        schema.update(
            {
                vol.Optional(CONF_RULES): SelectSelector(
                    SelectSelectorConfig(
                        options=self.get_intervals(step_id, pricing_id),
                        mode=SelectSelectorMode.LIST,
                        translation_key="interval_key",
                    )
                ),
            }
        )

        pricings = self._datas[step_id].get(CONF_PRICINGS, {})
        if pricing_id == CONF_PRICE_NEW_ID:
            id = int(max(pricings.keys())) + 1 if pricings.keys() else 1
            data_schema = vol.Schema({vol.Required(CONF_PRICING_ID): str(id), **schema})
        else:
            data_schema = vol.Schema(
                {**schema, vol.Optional(CONF_PRICING_DELETE, default=False): bool}
            )

        return self.async_show_form(
            step_id="pricings",
            data_schema=self.add_suggested_values_to_schema(
                data_schema, pricings.get(pricing_id, {})
            ),
            last_step=False,
        )

    async def async_step_rules(
        self,
        user_input: dict[str, Any] | None = None,
        rule_id: str | None = None,
        pricing_id: str | None = None,
        step_id: str | None = None,
    ) -> FlowResult:
        """Handle options flow for apps list."""
        if rule_id is not None:
            self._conf_rule_id = rule_id if rule_id != CONF_RULE_NEW_ID else None
            return self._async_rules_form(rule_id, pricing_id, step_id)

        if user_input is not None:
            rule_id = user_input.get(CONF_RULE_ID, self._conf_rule_id)
            step_id = user_input["step_id"]
            pricing_id = user_input[CONF_PRICING_ID]
            if rule_id:
                rules = self._datas[step_id][CONF_PRICINGS][pricing_id].get(
                    CONF_PRICING_INTERVALS, {}
                )
                if user_input.get(CONF_RULE_DELETE, False):
                    rules.pop(str(rule_id))
                else:
                    rules.update(
                        {
                            str(rule_id): {
                                CONF_RULE_START_TIME: user_input.get(
                                    CONF_RULE_START_TIME
                                ),
                                CONF_RULE_END_TIME: user_input.get(CONF_RULE_END_TIME),
                            }
                        }
                    )
                    self._datas[step_id][CONF_PRICINGS][pricing_id][
                        CONF_PRICING_INTERVALS
                    ].update(**rules)

            return await self.async_step_pricings(None, pricing_id, step_id)

    @callback
    def _async_rules_form(
        self, rule_id: str, pricing_id: str, step_id: str
    ) -> FlowResult:
        """Return configuration form for rules."""
        intervals = (
            self._datas.get(step_id, {})
            .get(CONF_PRICINGS, {})
            .get(pricing_id, {})
            .get(CONF_PRICING_INTERVALS)
        )
        schema = {
            vol.Required("step_id"): step_id,
            vol.Required(CONF_PRICING_ID): pricing_id,
            vol.Optional(CONF_RULE_START_TIME): TimeSelector(TimeSelectorConfig()),
            vol.Optional(CONF_RULE_END_TIME): TimeSelector(TimeSelectorConfig()),
        }

        if rule_id == CONF_RULE_NEW_ID:
            id = int(max(intervals.keys())) + 1 if intervals.keys() else 1
            data_schema = vol.Schema({vol.Required(CONF_RULE_ID): str(id), **schema})
        else:
            data_schema = vol.Schema(
                {**schema, vol.Optional(CONF_RULE_DELETE, default=False): bool}
            )

        return self.async_show_form(
            step_id="rules",
            data_schema=self.add_suggested_values_to_schema(
                data_schema, intervals.get(rule_id, {})
            ),
            last_step=False,
        )

    def get_pricing_list(self, step_id: str) -> dict[str, Any]:
        """Return pricing list."""
        list_pricing = [
            SelectOptionDict(
                value=pricing_id,
                label=f"{v.get(CONF_PRICING_NAME)} - {v.get(CONF_PRICING_COST)}",
            )
            for pricing_id, v in self._datas[step_id].get(CONF_PRICINGS, {}).items()
        ]
        list_pricing.append(
            SelectOptionDict(value=CONF_PRICE_NEW_ID, label="Add new pricing")
        )

        return list_pricing

    def get_intervals(self, step_id: str, pricing_id: str) -> dict[str, Any]:
        """Return intervals."""
        intervals = (
            self._datas[step_id]
            .get(CONF_PRICINGS, {})
            .get(pricing_id, {})
            .get(CONF_PRICING_INTERVALS, {})
        )
        list_intervals = [
            SelectOptionDict(
                value=rule_id,
                label=f"{v.get(CONF_RULE_START_TIME)} - {v.get(CONF_RULE_END_TIME)}",
            )
            for rule_id, v in intervals.items()
        ]
        list_intervals.append(
            SelectOptionDict(value=CONF_RULE_NEW_ID, label="Add new interval")
        )

        return list_intervals


def default_settings(datas: dict[str, Any]):
    """Set default datas if missing."""
    production = datas.get(CONF_PRODUCTION)
    if (
        production
        and production.get(CONF_SERVICE)
        and len(production.get(CONF_PRICINGS, {})) == 0
    ):
        datas[CONF_PRODUCTION][CONF_PRICINGS] = DEFAULT_PRODUCTION

    consumption = datas.get(CONF_CONSUMPTION)
    if (
        consumption
        and consumption.get(CONF_SERVICE)
        and len(consumption.get(CONF_PRICINGS, {})) == 0
    ):
        datas[CONF_CONSUMPTION][CONF_PRICINGS] = DEFAULT_CONSUMPTION

    if (
        consumption
        and consumption.get(CONF_TEMPO)
        and len(consumption.get(CONF_PRICINGS, {})) == 0
    ):
        datas[CONF_CONSUMPTION] = {
            CONF_SERVICE: CONSUMPTION_DETAIL,
            CONF_TEMPO: consumption.get(CONF_TEMPO),
            CONF_PRICINGS: DEFAULT_CONSUMPTION_TEMPO,
        }

    return datas
