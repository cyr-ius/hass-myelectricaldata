"""Helpers functions for MyElectricalData."""

from __future__ import annotations

import logging
from datetime import datetime as dt
from datetime import timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify
from homeassistant.util.unit_conversion import EnergyConverter

from .const import (
    CONF_BLUE,
    CONF_CONSUMPTION,
    CONF_OFFPEAK,
    CONF_PRICE,
    CONF_PRICINGS,
    CONF_RED,
    CONF_STD,
    CONF_WHITE,
    CONSUMPTION_DAILY,
    CONSUMPTION_DETAIL,
    DEFAULT_CC_PRICE,
    DEFAULT_CONSUMPTION_TEMPO,
    DEFAULT_HC_PRICE,
    DEFAULT_PC_PRICE,
    DOMAIN,
    PRODUCTION_DAILY,
    PRODUCTION_DETAIL,
)

_LOGGER = logging.getLogger(__name__)


async def async_get_db_infos(hass: HomeAssistant, statistic_id: str) -> tuple[str, dt]:
    """Fetch last information in database."""
    last_stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
    )
    last_summary, dt_last_stat = (
        (0, None)
        if not last_stats
        else (
            last_stats[statistic_id][0]["sum"],
            dt.fromtimestamp(last_stats[statistic_id][0]["start"]),  # noqa: DTZ006
        )
    )
    _LOGGER.debug(
        "[%s] summary: %s, last date: %s", statistic_id, last_summary, dt_last_stat
    )
    return (last_summary, dt_last_stat)


async def async_get_last_infos(
    hass: HomeAssistant, items: list[dict[str, Any]]
) -> tuple[dt, float, float]:
    """Set default api."""
    sum_values: dict[str, float] = {}
    sum_prices: dict[str, float] = {}
    _dt_last = None
    for item in items:
        summary, dt_last = await async_get_db_infos(hass, item["entity_id"])
        if item["kind"] == "energy":
            sum_values[item["note"]] = summary
            _dt_last = dt_last if _dt_last is None else _dt_last
        else:
            sum_prices[item["note"]] = summary

    _LOGGER.debug(
        "[infosdb] last date: %s, sum value: %s, sum price: %s",
        _dt_last,
        sum_values,
        sum_prices,
    )
    return _dt_last, sum_values, sum_prices


def build_sensor_items(
    mode: str, pdl: str, service: str, intervals: list[Any], has_price: bool
) -> list[dict[str, Any]]:
    """Return one sensor descriptor per tariff bucket actually collected.

    A single "standard" bucket is used for daily (aggregated) services, since
    Enedis doesn't expose sub-daily granularity to split by offpeak hours.
    Detail (load curve) services get a "standard"/"offpeak" pair as soon as
    offpeak intervals are configured, so each bucket can be added as its own
    consumption source in the Energy dashboard and summed back into a day.
    A "cost" companion item is added next to each energy bucket when pricing
    is configured.
    """
    is_detail = service in (CONSUMPTION_DETAIL, PRODUCTION_DETAIL)
    notes = [CONF_STD, CONF_OFFPEAK] if is_detail and intervals else [CONF_STD]

    items: list[dict[str, Any]] = []
    for note in notes:
        suffix = note if len(notes) == 1 else ("full" if note == CONF_STD else note)
        unique_id = f"{pdl}_{mode}_{suffix}"
        name = f"{pdl} {mode} {suffix}".capitalize()
        items.append(
            {
                "unique_id": unique_id,
                "entity_id": f"sensor.{slugify(f'{DOMAIN}_{unique_id}')}",
                "name": name,
                "friendly_name": f"{mode} {suffix}",
                "note": note,
                "mode": mode,
                "kind": "energy",
            }
        )
        if has_price:
            cost_unique_id = f"{unique_id}_cost"
            items.append(
                {
                    "unique_id": cost_unique_id,
                    "entity_id": f"sensor.{slugify(f'{DOMAIN}_{cost_unique_id}')}",
                    "name": f"{name} cost",
                    "friendly_name": f"{mode} {suffix} cost",
                    "note": note,
                    "mode": mode,
                    "kind": "cost",
                }
            )
    _LOGGER.debug("[items] %s", items)
    return items


def build_price_items(
    mode: str, pdl: str, service: str, intervals: list[Any], tempo: bool
) -> list[dict[str, Any]]:
    """Return one editable-tariff (number entity) descriptor per price needed.

    Mirrors build_sensor_items' bucket cardinality: a single price for daily
    services or detail services without offpeak intervals, a standard/offpeak
    pair once offpeak intervals are configured, or the six Tempo colour
    prices when Tempo is enabled (Tempo always needs both buckets).
    """
    is_detail = service in (CONSUMPTION_DETAIL, PRODUCTION_DETAIL)
    has_offpeak = is_detail and bool(intervals)

    items: list[dict[str, Any]] = []
    if tempo:
        tempo_defaults = DEFAULT_CONSUMPTION_TEMPO[CONF_PRICINGS]
        for note in (CONF_STD, CONF_OFFPEAK):
            for color in (CONF_BLUE, CONF_WHITE, CONF_RED):
                unique_id = f"{pdl}_{mode}_{note}_{color}"
                items.append(
                    {
                        "unique_id": unique_id,
                        "entity_id": f"number.{slugify(f'{DOMAIN}_{unique_id}')}",
                        "name": f"{mode} {note} {color}".capitalize(),
                        "mode": mode,
                        "note": note,
                        "key": color,
                        "default": tempo_defaults[note][color],
                    }
                )
        return items

    default_std = DEFAULT_CC_PRICE if mode == CONF_CONSUMPTION else DEFAULT_PC_PRICE
    notes = [CONF_STD, CONF_OFFPEAK] if has_offpeak else [CONF_STD]
    for note in notes:
        unique_id = f"{pdl}_{mode}_{note}_{CONF_PRICE}"
        items.append(
            {
                "unique_id": unique_id,
                "entity_id": f"number.{slugify(f'{DOMAIN}_{unique_id}')}",
                "name": f"{mode} {note} price".capitalize(),
                "mode": mode,
                "note": note,
                "key": CONF_PRICE,
                "default": default_std if note == CONF_STD else DEFAULT_HC_PRICE,
            }
        )
    return items


def read_prices(hass: HomeAssistant, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Read the live value of each tariff number entity.

    Falls back to the descriptor's default when the entity isn't available
    yet (e.g. the very first coordinator refresh, before platforms load).
    """
    prices: dict[str, Any] = {}
    for item in items:
        state = hass.states.get(item["entity_id"])
        value = item["default"]
        if state is not None and state.state not in (None, "unknown", "unavailable"):
            try:
                value = float(state.state)
            except ValueError:
                pass
        prices.setdefault(item["note"], {})[item["key"]] = value
    _LOGGER.debug("[prices] %s", prices)
    return prices


async def async_import_sensor_statistics(
    hass: HomeAssistant,
    items: list[dict[str, Any]],
    data_collected: dict[str, Any],
) -> None:
    """Import statistics directly onto their own real sensor entity."""
    for item in items:
        rows: list[StatisticData] = []
        for data in data_collected.get(item["mode"], []):
            if data["notes"] != item["note"]:
                continue
            if item["kind"] == "energy" and data.get("value"):
                rows.append(
                    StatisticData(
                        start=data["date"], state=data["value"], sum=data["sum_value"]
                    )
                )
            elif item["kind"] == "cost" and data.get("price"):
                rows.append(
                    StatisticData(
                        start=data["date"], state=data["price"], sum=data["sum_price"]
                    )
                )

        if not rows:
            continue

        _LOGGER.debug("[import_stats] %s -> %s rows", item["entity_id"], len(rows))
        is_energy = item["kind"] == "energy"
        metadata = StatisticMetaData(
            has_sum=True,
            name=None,
            source="recorder",
            statistic_id=item["entity_id"],
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR if is_energy else "EUR",
            mean_type=StatisticMeanType.NONE,
            unit_class=EnergyConverter.UNIT_CLASS if is_energy else None,
        )
        await get_instance(hass).async_add_executor_job(
            async_import_statistics, hass, metadata, rows
        )


def next_date(date_: dt | None, service: str) -> dt:
    """Return next date."""
    if date_ and service in [PRODUCTION_DETAIL, CONSUMPTION_DETAIL]:
        return date_ + timedelta(hours=1)
    elif date_:
        return date_ + timedelta(days=1)
    return (
        dt.now() - timedelta(days=1095)  # noqa: DTZ005
        if service in [PRODUCTION_DAILY, CONSUMPTION_DAILY]
        else dt.now() - timedelta(days=7)  # noqa: DTZ005
    )
