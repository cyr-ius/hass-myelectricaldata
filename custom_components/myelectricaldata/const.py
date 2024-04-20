"""Constants for the Enedis integration."""
CLEAR_SERVICE = "clear_data"
CONF_AUTH = "authentication"
CONF_CONSUMPTION = "consumption"
CONF_ECOWATT = "ecowatt"
CONF_END_DATE = "end_date"
CONF_ENTRY = "entry"
CONF_PDL = "pdl"
CONF_POWER_MODE = "power_mode"
CONF_INTERVALS = "intervals"
CONF_PRODUCTION = "production"
CONF_RULE_DELETE = "rule_delete"
CONF_RULE_END_TIME = "rule_end_time"
CONF_RULE_ID = "rule_id"
CONF_RULE_NEW_ID = "rule_new_id"
CONF_RULE_START_TIME = "rule_start_time"
CONF_SERVICE = "service"
CONF_START_DATE = "start_date"
CONF_STATISTIC_ID = "statistic_id"
CONF_TEMPO = "tempo"
CONSUMPTION_DAILY = "daily_consumption"
CONSUMPTION_DETAIL = "consumption_load_curve"
CONF_OFF_PRICE = "off_price"
CONF_PRICE = "price"
CONF_PRICINGS = "pricings"
CONF_BLUE = "blue"
CONF_RED = "red"
CONF_WHITE = "white"
CONF_STD = "standard"
CONF_OFFPEAK = "offpeak"
DEFAULT_CC_PRICE = 0.1740
DEFAULT_HC_PRICE = 0.1470
DEFAULT_HP_PRICE = 0.1841
DEFAULT_PC_PRICE = 0.06
DOMAIN = "myelectricaldata"
FETCH_SERVICE = "fetch_data"
MANUFACTURER = "Enedis"
PLATFORMS = ["sensor", "binary_sensor"]
PRODUCTION_DAILY = "daily_production"
PRODUCTION_DETAIL = "production_load_curve"
SAVE = "save"
URL = "https://myelectricaldata.fr"
DEFAULT_CONSUMPTION_TEMPO = {
    CONF_PRICINGS: {
        CONF_STD: {
            CONF_BLUE: round(DEFAULT_HP_PRICE * 0.7, 2),
            CONF_WHITE: round(DEFAULT_HP_PRICE * 0.9, 2),
            CONF_RED: round(DEFAULT_HP_PRICE * 3, 2),
        },
        CONF_OFFPEAK: {
            CONF_BLUE: round(DEFAULT_HC_PRICE * 0.6, 2),
            CONF_WHITE: round(DEFAULT_HC_PRICE * 0.76, 2),
            CONF_RED: round(DEFAULT_HC_PRICE * 0.85, 2),
        },
    },
    CONF_INTERVALS: {
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
DEFAULT_PRODUCTION = {
    CONF_PRICINGS: {
        CONF_STD: {
            CONF_PRICE: DEFAULT_PC_PRICE,
        },
    },
    CONF_INTERVALS: {},
}
DEFAULT_CONSUMPTION = {
    CONF_PRICINGS: {
        CONF_STD: {
            CONF_PRICE: DEFAULT_CC_PRICE,
        },
    },
    CONF_INTERVALS: {},
}
