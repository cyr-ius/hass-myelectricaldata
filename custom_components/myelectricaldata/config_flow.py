"""Config flow to configure integration."""

from __future__ import annotations

from datetime import datetime as dt
import logging
from typing import Any

from myelectricaldatapy import Enedis, EnedisException
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
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
    CONF_AUTH,
    CONF_BLUE,
    CONF_CONSUMPTION,
    CONF_ECOWATT,
    CONF_INTERVALS,
    CONF_OFF_PRICE,
    CONF_OFFPEAK,
    CONF_PDL,
    CONF_PRICE,
    CONF_PRICINGS,
    CONF_PRODUCTION,
    CONF_RED,
    CONF_RULE_DELETE,
    CONF_RULE_END_TIME,
    CONF_RULE_ID,
    CONF_RULE_NEW_ID,
    CONF_RULE_START_TIME,
    CONF_SERVICE,
    CONF_STD,
    CONF_TEMPO,
    CONF_WHITE,
    CONSUMPTION_DAILY,
    CONSUMPTION_DETAIL,
    DEFAULT_CC_PRICE,
    DEFAULT_CONSUMPTION,
    DEFAULT_CONSUMPTION_TEMPO,
    DEFAULT_HC_PRICE,
    DEFAULT_HP_PRICE,
    DEFAULT_PC_PRICE,
    DEFAULT_PRODUCTION,
    DOMAIN,
    PRODUCTION_DAILY,
    PRODUCTION_DETAIL,
    SAVE,
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
        vol.Required(CONF_ECOWATT, default=False): bool,
        vol.Required(CONF_PRODUCTION, default=False): bool,
        vol.Required(CONF_CONSUMPTION, default=False): bool,
        vol.Required(CONF_TEMPO, default=False): bool,
    }
)

_LOGGER = logging.getLogger(__name__)


class MyElectricalFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        """Get option flow."""
        return MyElectricalDataOptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        errors = {}
        if user_input is not None:
            self._async_abort_entries_match({CONF_PDL: user_input[CONF_PDL]})
            api = Enedis(
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
                data = {CONF_PDL: user_input[CONF_PDL]}
                opts = {
                    CONF_AUTH: {
                        CONF_TOKEN: user_input[CONF_TOKEN],
                        CONF_ECOWATT: user_input[CONF_ECOWATT],
                        CONF_TEMPO: user_input[CONF_TEMPO],
                    }
                }
                if user_input[CONF_PRODUCTION]:
                    opts.update({CONF_PRODUCTION: {CONF_SERVICE: PRODUCTION_DAILY}})
                if user_input[CONF_CONSUMPTION]:
                    opts.update({CONF_CONSUMPTION: {CONF_SERVICE: CONSUMPTION_DAILY}})

                options = default_settings(opts)
                return self.async_create_entry(
                    title=f"Linky ({user_input[CONF_PDL]})", data=data, options=options
                )

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )


class MyElectricalDataOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle option."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        _auth: dict[str, Any] = config_entry.options.get(CONF_AUTH, {})
        _production: dict[str, Any] = config_entry.options.get(CONF_PRODUCTION, {})
        _consumption: dict[str, Any] = config_entry.options.get(CONF_CONSUMPTION, {})
        self._data = {
            CONF_AUTH: _auth.copy(),
            CONF_PRODUCTION: _production.copy(),
            CONF_CONSUMPTION: _consumption.copy(),
        }
        self._conf_rule_id: int | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle options flow."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[CONF_AUTH, CONF_PRODUCTION, CONF_CONSUMPTION, SAVE],
        )

    async def async_step_authentication(self, user_input: dict[str, Any] | None = None):
        """Authenticate step."""
        step_id = CONF_AUTH
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TOKEN,
                    default=self._data[step_id].get(CONF_TOKEN),
                ): str,
                vol.Required(
                    CONF_ECOWATT,
                    default=self._data[step_id].get(CONF_ECOWATT, False),
                ): bool,
                vol.Required(
                    CONF_TEMPO,
                    default=self._data[step_id].get(CONF_TEMPO, False),
                ): bool,
            }
        )
        if user_input is not None:
            self._data[step_id].update(**user_input)
            return await self.async_step_init()
        return self.async_show_form(
            step_id=step_id, data_schema=schema, last_step=False
        )

    async def async_step_production(self, user_input: dict[str, Any] | None = None):
        """Production step."""
        step_id = CONF_PRODUCTION
        standard = self._data[step_id].get(CONF_PRICINGS, {}).get(CONF_STD, {})
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SERVICE,
                    description={
                        "suggested_value": self._data[step_id].get(CONF_SERVICE)
                    },
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=PRODUCTION_CHOICE,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                        translation_key="production_choice",
                    )
                ),
                vol.Optional(
                    CONF_PRICE, default=standard.get(CONF_PRICE, DEFAULT_PC_PRICE)
                ): cv.positive_float,
            }
        )
        if user_input is not None:
            self._data[step_id].update(
                {
                    CONF_SERVICE: user_input.get(CONF_SERVICE),
                    CONF_PRICINGS: {
                        CONF_STD: {CONF_PRICE: user_input.get(CONF_PRICE)},
                    },
                }
            )
            return await self.async_step_init()
        return self.async_show_form(
            step_id=step_id, data_schema=schema, last_step=False
        )

    async def async_step_consumption(self, user_input: dict[str, Any] | None = None):
        """Consumption step."""
        step_id = CONF_CONSUMPTION
        standard = self._data[step_id].get(CONF_PRICINGS, {}).get(CONF_STD, {})
        offpeak = self._data[step_id].get(CONF_PRICINGS, {}).get(CONF_OFFPEAK, {})
        schema = {
            vol.Optional(
                CONF_SERVICE,
                description={"suggested_value": self._data[step_id].get(CONF_SERVICE)},
            ): SelectSelector(
                SelectSelectorConfig(
                    options=CONSUMPTION_CHOICE,
                    mode=SelectSelectorMode.DROPDOWN,
                    custom_value=True,
                    translation_key="consumption_choice",
                )
            ),
        }
        standard_schema = {
            vol.Optional(
                CONF_PRICE, default=standard.get(CONF_PRICE, DEFAULT_CC_PRICE)
            ): cv.positive_float,
            vol.Optional(
                CONF_OFF_PRICE, default=offpeak.get(CONF_PRICE, DEFAULT_HC_PRICE)
            ): cv.positive_float,
        }
        tempo_schema = {
            vol.Optional(
                "s_blue",
                default=standard.get(CONF_BLUE, round(DEFAULT_HP_PRICE * 0.7, 2)),
            ): cv.positive_float,
            vol.Optional(
                "s_white",
                default=standard.get(CONF_WHITE, round(DEFAULT_HP_PRICE * 0.9, 2)),
            ): cv.positive_float,
            vol.Optional(
                "s_red", default=standard.get(CONF_RED, round(DEFAULT_HP_PRICE * 3, 2))
            ): cv.positive_float,
            vol.Optional(
                "o_blue",
                default=offpeak.get(CONF_BLUE, round(DEFAULT_HC_PRICE * 0.6, 2)),
            ): cv.positive_float,
            vol.Optional(
                "o_white",
                default=offpeak.get(CONF_WHITE, round(DEFAULT_HC_PRICE * 0.76, 2)),
            ): cv.positive_float,
            vol.Optional(
                "o_red",
                default=offpeak.get(CONF_RED, round(DEFAULT_HC_PRICE * 0.85, 2)),
            ): cv.positive_float,
        }

        if self._data[CONF_AUTH].get(CONF_TEMPO):
            schema.update(tempo_schema)
        else:
            schema.update(standard_schema)

        data_schema = vol.Schema(
            {
                **schema,
                vol.Optional(CONF_INTERVALS): SelectSelector(
                    SelectSelectorConfig(
                        options=self.get_intervals(step_id),
                        mode=SelectSelectorMode.LIST,
                        translation_key="interval_key",
                    )
                ),
            }
        )
        if user_input is not None:
            self._data[step_id].update({CONF_SERVICE: user_input.get(CONF_SERVICE)})
            if self._data[CONF_AUTH].get(CONF_TEMPO):
                self._data[step_id].update(
                    {
                        CONF_PRICINGS: {
                            CONF_STD: {
                                CONF_BLUE: user_input["s_blue"],
                                CONF_WHITE: user_input["s_white"],
                                CONF_RED: user_input["s_red"],
                            },
                            CONF_OFFPEAK: {
                                CONF_BLUE: user_input["o_blue"],
                                CONF_WHITE: user_input["o_white"],
                                CONF_RED: user_input["o_red"],
                            },
                        }
                    }
                )
            else:
                self._data[step_id].update(
                    {
                        CONF_PRICINGS: {
                            CONF_STD: {CONF_PRICE: user_input[CONF_PRICE]},
                            CONF_OFFPEAK: {CONF_PRICE: user_input[CONF_OFF_PRICE]},
                        }
                    }
                )
            if sel_interval := user_input.get(CONF_INTERVALS):
                return await self.async_step_rules(None, sel_interval, step_id)
            return await self.async_step_init()
        return self.async_show_form(
            step_id=step_id, data_schema=data_schema, last_step=False
        )

    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Save the updated options."""
        self._data = default_settings(self._data)
        self._data.update({"last_update": dt.now()})
        return self.async_create_entry(title="", data=self._data)

    async def async_step_rules(
        self,
        user_input: dict[str, Any] | None = None,
        rule_id: str | None = None,
        step_id: str | None = None,
    ) -> FlowResult:
        """Handle options flow for apps list."""
        if rule_id is not None:
            self._conf_rule_id = rule_id if rule_id != CONF_RULE_NEW_ID else None
            return self._async_rules_form(rule_id, step_id)

        if user_input is not None:
            rule_id = user_input.get(CONF_RULE_ID, self._conf_rule_id)
            step_id = user_input["step_id"]
            if rule_id:
                rules = self._data[step_id].get(CONF_INTERVALS, {})
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

                self._data[step_id][CONF_INTERVALS] = rules

        if step_id == CONF_CONSUMPTION:
            return await self.async_step_consumption()
        return await self.async_step_production()

    @callback
    def _async_rules_form(self, rule_id: str, step_id: str) -> FlowResult:
        """Return configuration form for rules."""
        intervals = self._data.get(step_id, {}).get(CONF_INTERVALS, {})
        schema = {
            vol.Required("step_id"): step_id,
            vol.Required(CONF_RULE_START_TIME): TimeSelector(TimeSelectorConfig()),
            vol.Required(CONF_RULE_END_TIME): TimeSelector(TimeSelectorConfig()),
        }

        if rule_id == CONF_RULE_NEW_ID:
            r_id = int(max(intervals.keys())) + 1 if intervals.keys() else 1
            data_schema = vol.Schema({vol.Required(CONF_RULE_ID): str(r_id), **schema})
        else:
            data_schema = vol.Schema(
                {**schema, vol.Required(CONF_RULE_DELETE, default=False): bool}
            )

        return self.async_show_form(
            step_id="rules",
            data_schema=self.add_suggested_values_to_schema(
                data_schema, intervals.get(rule_id, {})
            ),
            last_step=False,
        )

    def get_intervals(self, step_id: str) -> dict[str, Any]:
        """Return intervals."""
        intervals = self._data[step_id].get(CONF_INTERVALS, {})
        list_intervals = [
            SelectOptionDict(
                value=rule_id,
                label=f"{v.get(CONF_RULE_START_TIME)} - {v.get(CONF_RULE_END_TIME)}",
            )
            for rule_id, v in intervals.items()
        ]
        list_intervals.append(
            SelectOptionDict(value=CONF_RULE_NEW_ID, label="add_new_interval")
        )

        return list_intervals


def default_settings(data: dict[str, Any]):
    """Set default data if missing."""
    auth = data.get(CONF_AUTH)
    production = data.get(CONF_PRODUCTION)
    if (
        production
        and production.get(CONF_SERVICE)
        and production.get(CONF_PRICINGS) is None
    ):
        data[CONF_PRODUCTION].update(DEFAULT_PRODUCTION)

    consumption = data.get(CONF_CONSUMPTION)
    if (
        consumption
        and consumption.get(CONF_SERVICE)
        and consumption.get(CONF_PRICINGS) is None
    ):
        data[CONF_CONSUMPTION].update(DEFAULT_CONSUMPTION)

    if (
        consumption
        and auth.get(CONF_TEMPO)
        and CONF_BLUE not in consumption.get(CONF_PRICINGS, {}).get(CONF_STD, {})
        and CONF_BLUE not in consumption.get(CONF_PRICINGS, {}).get(CONF_OFFPEAK, {})
    ):
        data[CONF_CONSUMPTION] = {
            CONF_SERVICE: CONSUMPTION_DETAIL,
            **DEFAULT_CONSUMPTION_TEMPO,
        }

    if (
        consumption
        and not auth.get(CONF_TEMPO)
        and CONF_BLUE in consumption.get(CONF_PRICINGS, {}).get(CONF_STD, {})
        and CONF_BLUE in consumption.get(CONF_PRICINGS, {}).get(CONF_OFFPEAK, {})
    ):
        data[CONF_CONSUMPTION].update(DEFAULT_CONSUMPTION)

    return data
