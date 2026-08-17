"""Fixtures for the MyElectricalData integration tests."""

from __future__ import annotations

import pytest
from homeassistant.const import CONF_TOKEN
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myelectricaldata.const import (
    CONF_AUTH,
    CONF_CONSUMPTION,
    CONF_ECOWATT,
    CONF_PDL,
    CONF_PRODUCTION,
    CONF_SERVICE,
    CONF_TEMPO,
    CONSUMPTION_DAILY,
    DOMAIN,
    PRODUCTION_DAILY,
)

pytest_plugins = "pytest_homeassistant_custom_component"


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
        data={CONF_PDL: pdl},
        options={
            CONF_AUTH: {
                CONF_TOKEN: "fake-token",
                CONF_ECOWATT: False,
                CONF_TEMPO: False,
            },
            CONF_PRODUCTION: {CONF_SERVICE: PRODUCTION_DAILY},
            CONF_CONSUMPTION: {CONF_SERVICE: CONSUMPTION_DAILY},
        },
    )
