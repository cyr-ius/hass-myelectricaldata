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
from myelectricaldatapy import (
    ATTR_HPHC,
    ATTR_INTERVALS,
    ATTR_OFFPEAK,
    ATTR_PRICE,
    ATTR_PRICES,
    ATTR_STANDARD,
    ATTR_TEMPO,
    CONF_ECOWATT,
    DAILY_CONSUM,
    DAILY_PROD,
    DETAIL_CONSUM,
    DETAIL_PROD,
    TEMPO_B,
    TEMPO_DAYS,
    TEMPO_R,
    TEMPO_W,
    Enedis,
    EnedisException,
    Prices,
    StandardPrice,
    TempoPrice,
)

from .const import (
    CONF_AUTH,
    CONF_CONSUMPTION,
    CONF_PDL,
    CONF_PRODUCTION,
    CONF_RULE_DELETE,
    CONF_RULE_END_TIME,
    CONF_RULE_ID,
    CONF_RULE_NEW_ID,
    CONF_RULE_START_TIME,
    CONF_SERVICE,
    CONF_SUBSCRIPTION,
    DEFAULT_CC_PRICE,
    DEFAULT_CONSUMPTION_TEMPO,
    DEFAULT_HC_PRICE,
    DEFAULT_HP_PRICE,
    DEFAULT_PC_PRICE,
    DOMAIN,
    SAVE,
)

# Plain value lists so the frontend resolves each label through the
# selector's translation_key (an explicit SelectOptionDict label would be
# shown verbatim instead).
PRODUCTION_CHOICE = [DAILY_PROD, DETAIL_PROD]
CONSUMPTION_CHOICE = [DAILY_CONSUM, DETAIL_CONSUM]
SUBSCRIBER_CHOICE = [ATTR_STANDARD, ATTR_HPHC, ATTR_TEMPO]

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PDL): str,
        vol.Required(CONF_TOKEN): str,
        vol.Required(CONF_ECOWATT, default=False): bool,
        vol.Required(CONF_PRODUCTION, default=False): bool,
        vol.Required(CONF_CONSUMPTION, default=False): bool,
        vol.Required(CONF_SUBSCRIPTION, default=ATTR_STANDARD): SelectSelector(
            SelectSelectorConfig(
                options=SUBSCRIBER_CHOICE, translation_key="subscriber"
            )
        ),
    }
)

_LOGGER = logging.getLogger(__name__)


async def _async_tempo_defaults(api: Enedis) -> dict[str, Any]:
    """Return the current Tempo colour prices, falling back to static defaults."""
    try:
        prices = await api.async_get_tempo_prices()
    except EnedisException as error:
        _LOGGER.debug("Tempo prices unavailable: %s", error)
        prices = None
    if prices is None or prices.offpeak is None:
        return DEFAULT_CONSUMPTION_TEMPO[ATTR_PRICES]
    return {
        ATTR_STANDARD: prices.standard.model_dump(),
        ATTR_OFFPEAK: prices.offpeak.model_dump(),
    }


def _normalize_offpeak(value: str) -> str:
    """Convert an Enedis offpeak bound like '22H30' to 'HH:MM:SS'."""
    hour, _, minute = value.upper().partition("H")
    return f"{int(hour):02d}:{int(minute or 0):02d}:00"


def _default_intervals() -> dict[str, dict[str, str]]:
    """Return a fresh copy of the built-in off-peak windows (22:00 -> 06:00)."""
    return {
        key: dict(value)
        for key, value in DEFAULT_CONSUMPTION_TEMPO[ATTR_INTERVALS].items()
    }


async def _async_intervals_defaults(api: Enedis, pdl: str) -> dict[str, dict[str, str]]:
    """Return the contract offpeak windows, falling back to static defaults."""
    try:
        await api.async_get_contract(pdl)
    except EnedisException as error:
        _LOGGER.debug("Contract unavailable: %s", error)
    if api.offpeaks:
        return {
            str(idx): {
                CONF_RULE_START_TIME: _normalize_offpeak(start),
                CONF_RULE_END_TIME: _normalize_offpeak(end),
            }
            for idx, (start, end) in enumerate(api.offpeaks, start=1)
        }
    return _default_intervals()


def _subscription_pricing_schema(
    subscription: str,
    current: dict[str, Any],
    tempo_defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    """Return the tariff fields relevant to the given subscription type."""
    prices = current.get(ATTR_PRICES, {})
    standard = prices.get(ATTR_STANDARD, {})
    offpeak = prices.get(ATTR_OFFPEAK, {})
    if subscription == ATTR_HPHC:
        return vol.Schema(
            {
                vol.Required(
                    ATTR_STANDARD, default=standard.get(ATTR_PRICE, DEFAULT_HP_PRICE)
                ): vol.Coerce(float),
                vol.Required(
                    ATTR_OFFPEAK,
                    default=offpeak.get(ATTR_PRICE, DEFAULT_HC_PRICE),
                ): vol.Coerce(float),
            }
        )
    if subscription == ATTR_TEMPO:
        defaults = tempo_defaults or DEFAULT_CONSUMPTION_TEMPO[ATTR_PRICES]
        schema: dict[Any, Any] = {}
        for note, prefix in ((ATTR_STANDARD, "s"), (ATTR_OFFPEAK, "o")):
            for color in (TEMPO_B, TEMPO_W, TEMPO_R):
                default = prices.get(note, {}).get(color, defaults[note][color])
                schema[vol.Required(f"{prefix}_{color}", default=default)] = vol.Coerce(
                    float
                )
        return vol.Schema(schema)
    return vol.Schema(
        {
            vol.Required(
                ATTR_STANDARD, default=standard.get(ATTR_PRICE, DEFAULT_CC_PRICE)
            ): vol.Coerce(float)
        }
    )


def _extract_subscription_prices(
    subscription: str, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Build the Prices payload from a submitted pricing form.

    Returns a mapping shaped like myelectricaldatapy's Prices model: a
    ``standard`` mapping and, when the subscription splits peak/offpeak, a
    matching ``offpeak`` one. The caller stores it under ATTR_PRICES.
    """
    if subscription == ATTR_HPHC:
        prices = Prices(
            standard=StandardPrice(price=user_input[ATTR_STANDARD]),
            offpeak=StandardPrice(price=user_input[ATTR_OFFPEAK]),
        )
    elif subscription == ATTR_TEMPO:
        prices = Prices(
            standard=TempoPrice(**{c: user_input[f"s_{c}"] for c in TEMPO_DAYS}),
            offpeak=TempoPrice(**{c: user_input[f"o_{c}"] for c in TEMPO_DAYS}),
        )
    else:
        prices = Prices(standard=StandardPrice(price=user_input[ATTR_STANDARD]))
    return prices.model_dump(exclude_none=True)


def _standard_prices(price: float) -> dict[str, Any]:
    """Return a single-rate Prices payload holding only the standard bucket."""
    return Prices(standard=StandardPrice(price=price)).model_dump(exclude_none=True)


def _read_standard_price(prices: dict[str, Any], default: float) -> float:
    """Return the standard single-rate price from a Prices payload."""
    return prices.get(ATTR_STANDARD, {}).get(ATTR_PRICE, default)


class _RulesFlowMixin:
    """Shared off-peak interval (rules) editing sub-flow.

    Subclasses expose ``_intervals_for(step_id)`` (the mutable id -> window
    map to edit) and ``_after_rules(step_id)`` (the step to resume once a
    window has been saved or deleted).
    """

    _conf_rule_id: str | None = None

    def _intervals_for(self, step_id: str | None) -> dict[str, dict[str, str]]:
        """Return the mutable interval map for the given section."""
        raise NotImplementedError

    async def _after_rules(self, step_id: str | None) -> FlowResult:
        """Return the step to resume after the rules form."""
        raise NotImplementedError

    async def async_step_rules(
        self,
        user_input: dict[str, Any] | None = None,
        rule_id: str | None = None,
        step_id: str | None = None,
    ) -> FlowResult:
        """Add, edit or delete a single off-peak window."""
        if rule_id is not None:
            self._conf_rule_id = rule_id if rule_id != CONF_RULE_NEW_ID else None
            return self._async_rules_form(rule_id, step_id)

        if user_input is not None:
            rule_id = user_input.get(CONF_RULE_ID, self._conf_rule_id)
            step_id = user_input["step_id"]
            if rule_id:
                rules = self._intervals_for(step_id)
                if user_input.get(CONF_RULE_DELETE, False):
                    rules.pop(str(rule_id), None)
                else:
                    rules[str(rule_id)] = {
                        CONF_RULE_START_TIME: user_input.get(CONF_RULE_START_TIME),
                        CONF_RULE_END_TIME: user_input.get(CONF_RULE_END_TIME),
                    }

        return await self._after_rules(step_id)

    @callback
    def _async_rules_form(self, rule_id: str, step_id: str | None) -> FlowResult:
        """Return the configuration form for a single off-peak window."""
        intervals = self._intervals_for(step_id)
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

    def get_intervals(self, step_id: str | None) -> list[SelectOptionDict]:
        """Return the interval picker options plus an 'add new' entry."""
        intervals = self._intervals_for(step_id)
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


class MyElectricalFlowHandler(
    _RulesFlowMixin, config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle a config flow."""

    VERSION = 4

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}
        self._prices: dict[str, Any] = {}
        self._production_price: float | None = None
        self._tempo_defaults: dict[str, Any] | None = None
        self._intervals: dict[str, dict[str, str]] = {}

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
                if user_input[CONF_SUBSCRIPTION] == ATTR_TEMPO:
                    self._tempo_defaults = await _async_tempo_defaults(api)
                if user_input[CONF_CONSUMPTION] and user_input[CONF_SUBSCRIPTION] in (
                    ATTR_HPHC,
                    ATTR_TEMPO,
                ):
                    self._intervals = await _async_intervals_defaults(
                        api, user_input[CONF_PDL]
                    )
                return await self.async_step_pricing()

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_pricing(self, user_input: dict[str, Any] | None = None):
        """Collect the tariff(s) matching the chosen subscription."""
        subscription = self._data[CONF_SUBSCRIPTION]
        has_intervals_step = self._data[CONF_CONSUMPTION] and subscription in (
            ATTR_HPHC,
            ATTR_TEMPO,
        )
        if user_input is not None:
            self._prices = _extract_subscription_prices(subscription, user_input)
            if has_intervals_step:
                return await self.async_step_intervals()
            if self._data[CONF_PRODUCTION]:
                return await self.async_step_production_pricing()
            return self._async_create_entry()

        return self.async_show_form(
            step_id="pricing",
            data_schema=_subscription_pricing_schema(
                subscription, {}, self._tempo_defaults
            ),
            last_step=not has_intervals_step and not self._data[CONF_PRODUCTION],
        )

    async def async_step_intervals(self, user_input: dict[str, Any] | None = None):
        """Review the off-peak windows applied to the load curve.

        HP/HC and Tempo split the load curve into full/offpeak buckets using
        these windows; they default to 22:00 -> 06:00 (see const). Submitting
        without a selection keeps the current windows and moves on.
        """
        if user_input is not None:
            if selected := user_input.get(ATTR_INTERVALS):
                return await self.async_step_rules(None, selected, CONF_CONSUMPTION)
            if self._data[CONF_PRODUCTION]:
                return await self.async_step_production_pricing()
            return self._async_create_entry()

        return self.async_show_form(
            step_id="intervals",
            data_schema=vol.Schema(
                {
                    vol.Optional(ATTR_INTERVALS): SelectSelector(
                        SelectSelectorConfig(
                            options=self.get_intervals(CONF_CONSUMPTION),
                            mode=SelectSelectorMode.LIST,
                            translation_key="interval_key",
                        )
                    )
                }
            ),
            last_step=not self._data[CONF_PRODUCTION],
        )

    def _intervals_for(self, step_id: str | None) -> dict[str, dict[str, str]]:
        """Return the single interval map tracked across the config flow."""
        return self._intervals

    async def _after_rules(self, step_id: str | None) -> FlowResult:
        """Return to the interval review screen after editing a window."""
        return await self.async_step_intervals()

    async def async_step_production_pricing(
        self, user_input: dict[str, Any] | None = None
    ):
        """Collect the cost per kWh of the energy the user produces."""
        if user_input is not None:
            self._production_price = user_input[ATTR_STANDARD]
            return self._async_create_entry()

        return self.async_show_form(
            step_id="production_pricing",
            data_schema=vol.Schema(
                {
                    vol.Required(ATTR_STANDARD, default=DEFAULT_PC_PRICE): vol.Coerce(
                        float
                    )
                }
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
            }
        }
        if self._data[CONF_PRODUCTION]:
            opts[CONF_PRODUCTION] = {
                CONF_SERVICE: DAILY_PROD,
                ATTR_PRICES: _standard_prices(self._production_price),
            }

        if self._data[CONF_CONSUMPTION]:
            consumption: dict[str, Any] = {
                CONF_SERVICE: DAILY_CONSUM,
                ATTR_PRICES: self._prices,
            }
            if self._intervals:
                consumption[ATTR_INTERVALS] = self._intervals
            opts[CONF_CONSUMPTION] = consumption

        options = default_settings(opts)
        return self.async_create_entry(
            title=f"Linky ({self._data[CONF_PDL]})", data=data, options=options
        )


class MyElectricalDataOptionsFlowHandler(_RulesFlowMixin, config_entries.OptionsFlow):
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
        self._conf_rule_id: str | None = None

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
                    default=self._data[step_id].get(CONF_SUBSCRIPTION, ATTR_STANDARD),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=SUBSCRIBER_CHOICE, translation_key="subscriber"
                    )
                ),
            }
        )
        if user_input is not None:
            previous = self._data[step_id].get(CONF_SUBSCRIPTION)
            self._data[step_id].update(**user_input)
            new = user_input[CONF_SUBSCRIPTION]
            if new != previous and self._data[CONF_CONSUMPTION]:
                # Standard bills a single rate, so it carries no windows; HP/HC
                # and Tempo split the load curve and fall back to 22:00 -> 06:00.
                if new == ATTR_STANDARD:
                    self._data[CONF_CONSUMPTION].pop(ATTR_INTERVALS, None)
                else:
                    self._data[CONF_CONSUMPTION][ATTR_INTERVALS] = _default_intervals()
            return await self.async_step_pricing()
        return self.async_show_form(
            step_id=step_id, data_schema=schema, last_step=False
        )

    async def async_step_pricing(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect the tariff(s) matching the chosen subscription."""
        auth = self._data[CONF_AUTH]
        consumption = self._data[CONF_CONSUMPTION]
        subscription = auth[CONF_SUBSCRIPTION]
        if user_input is not None:
            consumption[ATTR_PRICES] = _extract_subscription_prices(
                subscription, user_input
            )
            return await self.async_step_init()

        tempo_defaults = None
        if subscription == ATTR_TEMPO:
            api = Enedis(
                token=auth[CONF_TOKEN],
                session=async_create_clientsession(self.hass),
                timeout=30,
            )
            tempo_defaults = await _async_tempo_defaults(api)
        return self.async_show_form(
            step_id="pricing",
            data_schema=_subscription_pricing_schema(
                subscription, consumption, tempo_defaults
            ),
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
                        translation_key="production_choice",
                    )
                ),
                vol.Required(
                    ATTR_STANDARD,
                    default=_read_standard_price(
                        self._data[step_id].get(ATTR_PRICES, {}), DEFAULT_PC_PRICE
                    ),
                ): vol.Coerce(float),
            }
        )
        if user_input is not None:
            self._data[step_id].update(
                {
                    CONF_SERVICE: user_input.get(CONF_SERVICE),
                    ATTR_PRICES: _standard_prices(user_input[ATTR_STANDARD]),
                }
            )
            return await self.async_step_init()
        return self.async_show_form(
            step_id=step_id, data_schema=schema, last_step=False
        )

    async def async_step_consumption(self, user_input: dict[str, Any] | None = None):
        """Consumption step."""
        step_id = CONF_CONSUMPTION
        consumption = self._data[step_id]
        # HP/HC and Tempo can only be billed from the load curve, so they don't
        # get the daily/detail choice: default_settings() would force it back
        # to detail on save anyway (see there).
        forces_detail = self._data[CONF_AUTH].get(CONF_SUBSCRIPTION) in (
            ATTR_HPHC,
            ATTR_TEMPO,
        )
        if forces_detail and not consumption.get(ATTR_INTERVALS):
            api = Enedis(
                token=self._data[CONF_AUTH][CONF_TOKEN],
                session=async_create_clientsession(self.hass),
                timeout=30,
            )
            consumption[ATTR_INTERVALS] = await _async_intervals_defaults(
                api, self.config_entry.data[CONF_PDL]
            )
        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SERVICE,
                    description={
                        "suggested_value": DETAIL_CONSUM
                        if forces_detail
                        else self._data[step_id].get(CONF_SERVICE)
                    },
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[DETAIL_CONSUM]
                        if forces_detail
                        else CONSUMPTION_CHOICE,
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="consumption_choice",
                    )
                ),
                vol.Optional(ATTR_INTERVALS): SelectSelector(
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
            if sel_interval := user_input.get(ATTR_INTERVALS):
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

    def _intervals_for(self, step_id: str | None) -> dict[str, dict[str, str]]:
        """Return the mutable interval map for the given section."""
        return self._data[step_id].setdefault(ATTR_INTERVALS, {})

    async def _after_rules(self, step_id: str | None) -> FlowResult:
        """Return to the edited section after saving a window."""
        if step_id == CONF_CONSUMPTION:
            return await self.async_step_consumption()
        return await self.async_step_production()


def default_settings(data: dict[str, Any]):
    """Set default data if missing.

    Consumption tariffs live under CONF_CONSUMPTION and production tariffs
    under CONF_PRODUCTION (both shaped like myelectricaldatapy's Prices
    model), entered via the config/options flow and mirrored read-only by the
    price sensors (see sensor.py). This only takes care of forcing the detail
    service with its default offpeak windows once HP/HC or Tempo data
    collection is enabled, since both need the load curve split into
    standard/offpeak buckets; daily_consumption would collapse them to a
    single value, which is what the Standard subscription is for. The stored
    tariff is left untouched.
    """
    auth = data.get(CONF_AUTH, {})
    consumption = data.get(CONF_CONSUMPTION)
    if (
        consumption
        and consumption.get(CONF_SERVICE)
        and auth.get(CONF_SUBSCRIPTION) in (ATTR_HPHC, ATTR_TEMPO)
        and consumption.get(CONF_SERVICE) != DETAIL_CONSUM
    ):
        data[CONF_CONSUMPTION] = {
            **consumption,
            CONF_SERVICE: DETAIL_CONSUM,
            ATTR_INTERVALS: consumption.get(ATTR_INTERVALS) or _default_intervals(),
        }

    return data
