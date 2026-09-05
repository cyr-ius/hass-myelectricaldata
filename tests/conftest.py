"""Fixtures for the MyElectricalData integration tests."""

from __future__ import annotations

import pytest
from homeassistant.const import CONF_TOKEN
from myelectricaldatapy import (
    ATTR_PRICE,
    ATTR_PRICES,
    ATTR_STANDARD,
    DAILY_CONSUM,
    DAILY_PROD,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myelectricaldata.const import (
    CONF_AUTH,
    CONF_CONSUMPTION,
    CONF_ECOWATT,
    CONF_PDL,
    CONF_PRODUCTION,
    CONF_SERVICE,
    CONF_SUBSCRIPTION,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture
def load_integration(recorder_mock, enable_custom_integrations):
    """Make the myelectricaldata custom component (and its recorder dep) loadable.

    ``recorder_mock`` must resolve before ``hass`` is fully initialized, so it
    is listed first here, ahead of ``enable_custom_integrations`` which merely
    reuses the already-cached ``hass`` instance.
    """
    return


@pytest.fixture
def pdl() -> str:
    """Return a fake PDL identifier."""
    return "12345678901234"


@pytest.fixture
def config_entry(pdl: str) -> MockConfigEntry:
    """Return a MockConfigEntry with consumption and production enabled."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"Linky ({pdl})",
        unique_id=pdl,
        version=4,
        data={CONF_PDL: pdl},
        options={
            CONF_AUTH: {
                CONF_TOKEN: "fake-token",
                CONF_ECOWATT: False,
                CONF_SUBSCRIPTION: ATTR_STANDARD,
            },
            CONF_PRODUCTION: {
                CONF_SERVICE: DAILY_PROD,
                ATTR_PRICES: {ATTR_STANDARD: {ATTR_PRICE: 0.06}},
            },
            CONF_CONSUMPTION: {
                CONF_SERVICE: DAILY_CONSUM,
                ATTR_PRICES: {ATTR_STANDARD: {ATTR_PRICE: 0.174}},
            },
        },
    )
