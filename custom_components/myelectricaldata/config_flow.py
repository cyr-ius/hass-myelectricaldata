"""Config flow to configure integration."""

import logging
from datetime import datetime as dt
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TimeSelector,
    TimeSelectorConfig,
)
from myelectricaldatapy import Enedis, EnedisException

from .const import (
    CONF_AUTH,
    CONF_BLUE,
    CONF_CONSUMPTION,
    CONF_ECOWATT,
    CONF_HPHC,
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
    CONF_SUBSCRIPTION,
    CONF_TEMPO,
    CONF_WHITE,
    CONSUMPTION_DAILY,
    CONSUMPTION_DETAIL,
    DEFAULT_CC_PRICE,
    DEFAULT_CONSUMPTION_TEMPO,
    DEFAULT_HC_PRICE,
    DEFAULT_HP_PRICE,
    DEFAULT_PC_PRICE,
    DOMAIN,
    PRODUCTION_DAILY,
    PRODUCTION_DETAIL,
    SAVE,
)

PRODUCTION_CHOICE = [
    SelectOptionDict(value=PRODUCTION_DAILY, label=PRODUCTION_DAILY),
    SelectOptionDict(value=PRODUCTION_DETAIL, label=PRODUCTION_DETAIL),
]
CONSUMPTION_CHOICE = [
    SelectOptionDict(value=CONSUMPTION_DAILY, label=CONSUMPTION_DAILY),
    SelectOptionDict(value=CONSUMPTION_DETAIL, label=CONSUMPTION_DETAIL),
]

SUBSCRIBER_CHOICE = [
    SelectOptionDict(value=CONF_STD, label=CONF_STD),
    SelectOptionDict(value=CONF_HPHC, label=CONF_HPHC),
    SelectOptionDict(value=CONF_TEMPO, label=CONF_TEMPO),
]

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PDL): str,
        vol.Required(CONF_TOKEN): str,
        vol.Required(CONF_ECOWATT, default=False): bool,
        vol.Required(CONF_PRODUCTION, default=False): bool,
        vol.Required(CONF_CONSUMPTION, default=False): bool,
        vol.Required(CONF_SUBSCRIPTION, default=CONF_STD): SelectSelector(
            SelectSelectorConfig(
                options=SUBSCRIBER_CHOICE, translation_key="subscriber"
            )
        ),
    }
)

_PRICING_KEYS = (CONF_PRICE, CONF_OFF_PRICE, CONF_PRICINGS)

_LOGGER = logging.getLogger(__name__)


def _subscription_pricing_schema(
    subscription: str, current: dict[str, Any]
) -> vol.Schema:
    """Return the tariff fields relevant to the given subscription type."""
    if subscription == CONF_HPHC:
        return vol.Schema(
            {
                vol.Required(
                    CONF_PRICE, default=current.get(CONF_PRICE, DEFAULT_HP_PRICE)
                ): vol.Coerce(float),
                vol.Required(
                    CONF_OFF_PRICE,
                    default=current.get(CONF_OFF_PRICE, DEFAULT_HC_PRICE),
                ): vol.Coerce(float),
            }
        )
    if subscription == CONF_TEMPO:
        pricings = current.get(CONF_PRICINGS, {})
        defaults = DEFAULT_CONSUMPTION_TEMPO[CONF_PRICINGS]
        schema: dict[Any, Any] = {}
        for note, prefix in ((CONF_STD, "s"), (CONF_OFFPEAK, "o")):
            for color in (CONF_BLUE, CONF_WHITE, CONF_RED):
                default = pricings.get(note, {}).get(color, defaults[note][color])
                schema[vol.Required(f"{prefix}_{color}", default=default)] = vol.Coerce(
                    float
                )
        return vol.Schema(schema)
    return vol.Schema(
        {
            vol.Required(
                CONF_PRICE, default=current.get(CONF_PRICE, DEFAULT_CC_PRICE)
            ): vol.Coerce(float)
        }
    )


def _extract_subscription_prices(
    subscription: str, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Build the auth-dict pricing fields from a submitted pricing form."""
    if subscription == CONF_HPHC:
        return {
            CONF_PRICE: user_input[CONF_PRICE],
            CONF_OFF_PRICE: user_input[CONF_OFF_PRICE],
        }
    if subscription == CONF_TEMPO:
        return {
            CONF_PRICINGS: {
                CONF_STD: {
                    color: user_input[f"s_{color}"]
                    for color in (CONF_BLUE, CONF_WHITE, CONF_RED)
                },
                CONF_OFFPEAK: {
                    color: user_input[f"o_{color}"]
                    for color in (CONF_BLUE, CONF_WHITE, CONF_RED)
                },
            }
        }
    return {CONF_PRICE: user_input[CONF_PRICE]}


class MyElectricalFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 3

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}
        self._prices: dict[str, Any] = {}
        self._production_price: float | None = None

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
                self._data = user_input
                return await self.async_step_pricing()

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_pricing(self, user_input: dict[str, Any] | None = None):
        """Collect the tariff(s) matching the chosen subscription."""
        subscription = self._data[CONF_SUBSCRIPTION]
        if user_input is not None:
            self._prices = _extract_subscription_prices(subscription, user_input)
            if self._data[CONF_PRODUCTION]:
                return await self.async_step_production_pricing()
            return self._async_create_entry()

        return self.async_show_form(
            step_id="pricing",
            data_schema=_subscription_pricing_schema(subscription, {}),
            last_step=not self._data[CONF_PRODUCTION],
        )

    async def async_step_production_pricing(
        self, user_input: dict[str, Any] | None = None
    ):
        """Collect the cost per kWh of the energy the user produces."""
        if user_input is not None:
            self._production_price = user_input[CONF_PRICE]
            return self._async_create_entry()

        return self.async_show_form(
            step_id="production_pricing",
            data_schema=vol.Schema(
                {vol.Required(CONF_PRICE, default=DEFAULT_PC_PRICE): vol.Coerce(float)}
            ),
        )

    @callback
    def _async_create_entry(self) -> FlowResult:
        """Create the config entry from the data collected across the flow."""
        data = {CONF_PDL: self._data[CONF_PDL]}
        opts: dict[str, Any] = {
            CONF_AUTH: {
                CONF_TOKEN: self._data[CONF_TOKEN],
                CONF_ECOWATT: self._data[CONF_ECOWATT],
                CONF_SUBSCRIPTION: self._data[CONF_SUBSCRIPTION],
                **self._prices,
            }
        }
        if self._data[CONF_PRODUCTION]:
            opts[CONF_PRODUCTION] = {
                CONF_SERVICE: PRODUCTION_DAILY,
                CONF_PRICE: self._production_price,
            }
        if self._data[CONF_CONSUMPTION]:
            opts[CONF_CONSUMPTION] = {CONF_SERVICE: CONSUMPTION_DAILY}

        options = default_settings(opts)
        return self.async_create_entry(
            title=f"Linky ({self._data[CONF_PDL]})", data=data, options=options
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
                    CONF_SUBSCRIPTION,
                    default=self._data[step_id].get(CONF_SUBSCRIPTION, CONF_STD),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=SUBSCRIBER_CHOICE, translation_key="subscriber"
                    )
                ),
            }
        )
        if user_input is not None:
            self._data[step_id].update(**user_input)
            return await self.async_step_pricing()
        return self.async_show_form(
            step_id=step_id, data_schema=schema, last_step=False
        )

    async def async_step_pricing(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect the tariff(s) matching the chosen subscription."""
        auth = self._data[CONF_AUTH]
        subscription = auth[CONF_SUBSCRIPTION]
        if user_input is not None:
            for key in _PRICING_KEYS:
                auth.pop(key, None)
            auth.update(_extract_subscription_prices(subscription, user_input))
            return await self.async_step_init()
        return self.async_show_form(
            step_id="pricing",
            data_schema=_subscription_pricing_schema(subscription, auth),
            last_step=False,
        )

    async def async_step_production(self, user_input: dict[str, Any] | None = None):
        """Production step."""
        step_id = CONF_PRODUCTION
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
                vol.Required(
                    CONF_PRICE,
                    default=self._data[step_id].get(CONF_PRICE, DEFAULT_PC_PRICE),
                ): vol.Coerce(float),
            }
        )
        if user_input is not None:
            self._data[step_id].update(
                {
                    CONF_SERVICE: user_input.get(CONF_SERVICE),
                    CONF_PRICE: user_input[CONF_PRICE],
                }
            )
            return await self.async_step_init()
        return self.async_show_form(
            step_id=step_id, data_schema=schema, last_step=False
        )

    async def async_step_consumption(self, user_input: dict[str, Any] | None = None):
        """Consumption step."""
        step_id = CONF_CONSUMPTION
        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SERVICE,
                    description={
                        "suggested_value": self._data[step_id].get(CONF_SERVICE)
                    },
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=CONSUMPTION_CHOICE,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                        translation_key="consumption_choice",
                    )
                ),
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
            SelectOptionDict(value=CONF_RULE_NEW_ID, label=CONF_RULE_NEW_ID)
        )

        return list_intervals


def default_settings(data: dict[str, Any]):
    """Set default data if missing.

    Tariffs live directly under CONF_AUTH (a single price, a peak/offpeak
    pair, or the six Tempo colour prices, depending on the subscription) and
    under CONF_PRODUCTION, entered via the config/options flow and mirrored
    read-only by the price sensors (see sensor.py). This only takes care of
    forcing the detail service with its default offpeak windows once Tempo
    gets enabled, since Tempo always needs the load curve split into
    standard/offpeak.
    """
    auth = data.get(CONF_AUTH, {})
    consumption = data.get(CONF_CONSUMPTION)
    if (
        consumption
        and auth.get(CONF_SUBSCRIPTION) == CONF_TEMPO
        and consumption.get(CONF_SERVICE) != CONSUMPTION_DETAIL
    ):
        data[CONF_CONSUMPTION] = {
            CONF_SERVICE: CONSUMPTION_DETAIL,
            CONF_INTERVALS: DEFAULT_CONSUMPTION_TEMPO[CONF_INTERVALS],
        }

    return data
