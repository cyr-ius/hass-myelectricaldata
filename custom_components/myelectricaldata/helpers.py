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
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify
from homeassistant.util.unit_conversion import EnergyConverter

from .const import (
    CONSUMPTION_DAILY,
    CONSUMPTION_DETAIL,
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
            dt.fromtimestamp(last_stats[statistic_id][0]["start"]),
        )
    )
    _LOGGER.debug(
        "[%s] summary: %s, last date: %s", statistic_id, last_summary, dt_last_stat
    )
    return (last_summary, dt_last_stat)


async def async_get_last_infos(
    hass: HomeAssistant, attributes: dict[str, Any]
) -> tuple[dt, float, float]:
    """Set default api."""
    sum_values = {}
    sum_prices = {}
    _dt_last = None
    for statistic_id, detail in attributes.items():
        sum_value, _dt_value = await async_get_db_infos(hass, statistic_id)
        sum_cost, _dt_cost = await async_get_db_infos(hass, f"{statistic_id}_cost")
        note = detail["note"]
        sum_values[note] = sum_value
        sum_prices[note] = sum_cost

        _dt_last = _dt_value if _dt_last is None else _dt_last
        if _dt_value != _dt_cost:
            _LOGGER.warning(
                "The energy value has a date different from the date collected to calculate the cost"
            )

    _LOGGER.debug(
        "[infosdb] last date: %s, sum value: %s, sum price: %s",
        _dt_last,
        sum_values,
        sum_prices,
    )
    return _dt_last, sum_values, sum_prices


async def async_add_statistics(
    hass: HomeAssistant,
    attributes: dict[str, Any],
    data_collected: dict[str, Any],
) -> None:
    """Add statistics database."""
    for statistic_id, detail in attributes.items():
        name = detail["name"]
        note = detail["note"]
        mode = detail["mode"]
        stats = []
        costs = []
        for data in data_collected.get(mode, []):
            if data["notes"] != note:
                continue
            _LOGGER.debug(data)
            if data.get("value"):
                stats.append(
                    StatisticData(
                        start=data["date"],
                        state=data["value"],
                        sum=data["sum_value"],
                    )
                )
            if data.get("price"):
                costs.append(
                    StatisticData(
                        start=data["date"],
                        state=data["price"],
                        sum=data["sum_price"],
                    )
                )

        if stats:
            _LOGGER.debug("[addstats] Add %s stat in table", mode)
            metadata = StatisticMetaData(
                has_sum=True,
                name=name,
                source=DOMAIN,
                statistic_id=statistic_id,
                unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                mean_type=StatisticMeanType.NONE,
                unit_class=EnergyConverter.UNIT_CLASS,
            )
            hass.async_add_executor_job(
                async_add_external_statistics, hass, metadata, stats
            )
        if costs:
            _LOGGER.debug("[addstats] Add %s cost in table", mode)
            metacost = StatisticMetaData(
                has_sum=True,
                name=f"{name} cost",
                source=DOMAIN,
                statistic_id=f"{statistic_id}_cost",
                unit_of_measurement="EUR",
                mean_type=StatisticMeanType.NONE,
                unit_class=None,
            )
            hass.async_add_executor_job(
                async_add_external_statistics, hass, metacost, costs
            )


def next_date(date_: dt | None, service: str) -> dt:
    """Return next date."""
    if date_ and service in [PRODUCTION_DETAIL, CONSUMPTION_DETAIL]:
        return date_ + timedelta(hours=1)
    elif date_:
        return date_ + timedelta(days=1)
    return (
        dt.now() - timedelta(days=1095)
        if service in [PRODUCTION_DAILY, CONSUMPTION_DAILY]
        else dt.now() - timedelta(days=7)
    )


def map_attributes(mode: str, pdl: str, intervals: list[Any]) -> dict[str, Any]:
    """Return attributes for database."""
    _attributes = {}
    suffix = "full" if len(intervals) != 0 else "standard"
    name = f"{pdl} {mode} {suffix}".capitalize()
    _attributes.update(
        {
            f"{DOMAIN}:" + slugify(name.lower()): {
                "name": name,
                "friendly_name": f"{mode} {suffix}",
                "note": "standard",
                "mode": mode,
            },
        }
    )
    if suffix == "full":
        name = f"{pdl} {mode} offpeak".capitalize()
        _attributes.update(
            {
                f"{DOMAIN}:" + slugify(name.lower()): {
                    "name": name,
                    "friendly_name": f"{mode} offpeak",
                    "note": "offpeak",
                    "mode": mode,
                }
            }
        )
    _LOGGER.debug("[attributes] %s", _attributes)
    return _attributes


async def async_normalize_datas(hass, attributes) -> None:
    """Fix statistics data."""
    for statistic_id, attrs in attributes.items():
        rslt = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            dt_util.as_local(dt.fromtimestamp(0)),
            dt_util.now(),
            {statistic_id},
            "hour",
            UnitOfEnergy.KILO_WATT_HOUR,
            {"state", "sum"},
        )

        metadata = StatisticMetaData(
            has_sum=True,
            name=attrs["friendly_name"],
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            mean_type=StatisticMeanType.NONE,
            unit_class=EnergyConverter.UNIT_CLASS,
        )

        for values in rslt.values():
            vsum = None
            stats = []
            for val in values:
                vsum = (
                    val.get("state", 0) if vsum is None else vsum + val.get("state", 0)
                )
                stats.append(
                    StatisticData(
                        start=dt_util.utc_from_timestamp(val["start"]),
                        state=val["state"],
                        sum=vsum,
                    )
                )
        instance = get_instance(hass)
        instance.async_clear_statistics([statistic_id])
        await instance.async_add_executor_job(
            async_add_external_statistics, hass, metadata, stats
        )
