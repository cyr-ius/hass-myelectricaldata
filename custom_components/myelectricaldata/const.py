"""Constants for the Enedis integration."""

from homeassistant.const import Platform
from myelectricaldatapy import ATTR_INTERVALS, ATTR_OFFPEAK, ATTR_PRICES, ATTR_STANDARD

CLEAR_SERVICE = "clear_data"
CONF_AUTH = "authentication"
CONF_CONSUMPTION = "consumption"
CONF_ECOWATT = "ecowatt"
CONF_END_DATE = "end_date"
CONF_ENTRY = "entry"
CONF_PDL = "pdl"
CONF_PRODUCTION = "production"
CONF_RULE_DELETE = "rule_delete"
CONF_RULE_END_TIME = "rule_end_time"
CONF_RULE_ID = "rule_id"
CONF_RULE_NEW_ID = "rule_new_id"
CONF_RULE_START_TIME = "rule_start_time"
CONF_SERVICE = "service"
CONF_START_DATE = "start_date"
CONF_STATISTIC_ID = "statistic_id"
CONF_OFF_PRICE = "off_price"
CONF_BLUE = "blue"
CONF_GREEN = "green"
CONF_NA = "na"
CONF_ORANGE = "orange"
CONF_RED = "red"
CONF_WHITE = "white"
CONF_SUBSCRIPTION = "abonnement"
CONF_SUMMARY = "summary"
DEFAULT_CC_PRICE = 0.1740
DEFAULT_HC_PRICE = 0.1470
DEFAULT_HP_PRICE = 0.1841
DEFAULT_PC_PRICE = 0.06
DOMAIN = "myelectricaldata"
FETCH_SERVICE = "fetch_data"
MANUFACTURER = "Enedis"
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
PRODUCTION_DAILY = "daily_production"
PRODUCTION_DETAIL = "production_load_curve"
REBUILD_SERVICE = "rebuild_data"
SAVE = "save"
URL = "https://myelectricaldata.fr"
DEFAULT_CONSUMPTION_TEMPO = {
    ATTR_PRICES: {
        ATTR_STANDARD: {
            CONF_BLUE: 0.1654,
            CONF_WHITE: 0.1921,
            CONF_RED: 0.7295,
        },
        ATTR_OFFPEAK: {
            CONF_BLUE: 0.1356,
            CONF_WHITE: 0.1536,
            CONF_RED: 0.1615,
        },
    },
    ATTR_INTERVALS: {
        "1": {
            CONF_RULE_START_TIME: "00:00:00",
            CONF_RULE_END_TIME: "06:00:00",
        },
        "2": {
            CONF_RULE_START_TIME: "22:00:00",
            CONF_RULE_END_TIME: "00:00:00",
        },
    },
}
