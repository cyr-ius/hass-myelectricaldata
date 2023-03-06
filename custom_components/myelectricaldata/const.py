"""Constants for the Enedis integration."""
CLEAR_SERVICE = "clear_datas"
CONF_AUTH = "authentication"
CONF_CONSUMPTION = "consumption"
CONF_CONTRACT = "contracts"
CONF_DATASET = "dataset"
CONF_DISABLED = "disabled"
CONF_ECOWATT = "ecowatt"
CONF_FREQUENCY = "frequency"
CONF_END_DATE = "end_date"
CONF_ENTRY = "entry"
CONF_PDL = "pdl"
CONF_POWER_MODE = "power_mode"
CONF_PRICE_NEW_ID = "pricing_new_id"
CONF_PRICING_COST = "pricing_cost"
CONF_PRICING_DELETE = "pricing_delete"
CONF_PRICING_ID = "pricing_id"
CONF_PRICING_INTERVALS = "pricing_intervals"
CONF_PRICING_NAME = "pricing_name"
CONF_PRICINGS = "pricings"
CONF_PRODUCTION = "production"
CONF_RULE_DELETE = "rule_delete"
CONF_RULE_END_TIME = "rule_end_time"
CONF_RULE_ID = "rule_id"
CONF_RULE_NEW_ID = "rule_new_id"
CONF_RULE_START_TIME = "rule_start_time"
CONF_RULES = "rules"
CONF_SERVICE = "service"
CONF_START_DATE = "start_date"
CONF_STATISTIC_ID = "statistic_id"
CONF_TEMPO = "bool_tempo"
CONSUMPTION_DAILY = "daily_consumption"
CONSUMPTION_DETAIL = "consumption_load_curve"
COST_CONSUMPTION = "consumption_cost"
COST_PRODUCTION = "production_cost"
DEFAULT_CC_PRICE = 0.1740
DEFAULT_HC_PRICE = 0.1470
DEFAULT_HP_PRICE = 0.1841
DEFAULT_PC_PRICE = 0.06
DOMAIN = "myelectricaldata"
FETCH_SERVICE = "fetch_datas"
MANUFACTURER = "Enedis"
PLATFORMS = ["sensor", "binary_sensor"]
PRODUCTION_DAILY = "daily_production"
PRODUCTION_DETAIL = "production_load_curve"
TEMPO_DAY = "tempo_day"
TEMPO = "dict_tempo"
SAVE = "save"
URL = "https://myelectricaldata.fr"
DEFAULT_CONSUMPTION_TEMPO = {
    "1": {
        CONF_PRICING_NAME: "Heure pleine",
        "BLUE": round(DEFAULT_HP_PRICE * 0.7, 2),
        "WHITE": round(DEFAULT_HP_PRICE * 0.9, 2),
        "RED": round(DEFAULT_HP_PRICE * 3, 2),
        CONF_PRICING_INTERVALS: {
            "1": {
                CONF_RULE_START_TIME: "06:00:00",
                CONF_RULE_END_TIME: "22:00:00",
            },
        },
    },
    "2": {
        CONF_PRICING_NAME: "Heure creuse",
        "BLUE": round(DEFAULT_HC_PRICE * 0.6, 2),
        "WHITE": round(DEFAULT_HC_PRICE * 0.76, 2),
        "RED": round(DEFAULT_HC_PRICE * 0.85, 2),
        CONF_PRICING_INTERVALS: {
            "1": {
                CONF_RULE_START_TIME: "00:00:00",
                CONF_RULE_END_TIME: "06:00:00",
            },
            "2": {
                CONF_RULE_START_TIME: "22:00:00",
                CONF_RULE_END_TIME: "00:00:00",
            },
        },
    },
}
DEFAULT_PRODUCTION = {
    "1": {
        CONF_PRICING_NAME: "Heure standard",
        CONF_PRICING_COST: DEFAULT_PC_PRICE,
        CONF_PRICING_INTERVALS: {
            "1": {
                CONF_RULE_START_TIME: "00:00:00",
                CONF_RULE_END_TIME: "00:00:00",
            }
        },
    }
}
DEFAULT_CONSUMPTION = {
    "1": {
        CONF_PRICING_NAME: "Heure standard",
        CONF_PRICING_COST: DEFAULT_CC_PRICE,
        CONF_PRICING_INTERVALS: {
            "1": {
                CONF_RULE_START_TIME: "00:00:00",
                CONF_RULE_END_TIME: "00:00:00",
            }
        },
    }
}
