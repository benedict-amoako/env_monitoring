"""Settings, overridable with environment variables (see README)."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def load() -> dict:
    data_dir = Path(os.environ.get("ENVMON_DATA_DIR", BASE_DIR / "data"))
    return {
        "host": os.environ.get("ENVMON_HOST", "0.0.0.0"),
        "port": int(os.environ.get("ENVMON_PORT", "8000")),
        "i2c_bus": int(os.environ.get("ENVMON_I2C_BUS", "1")),
        "i2c_address": int(os.environ.get("ENVMON_I2C_ADDR", "0x76"), 0),
        "interval_s": float(os.environ.get("ENVMON_INTERVAL", "1.0")),
        "heater_temp_c": int(os.environ.get("ENVMON_HEATER_TEMP", "320")),
        "heater_ms": int(os.environ.get("ENVMON_HEATER_MS", "150")),
        # The BME688 and Pi warm the air around the sensor; set a negative
        # offset (e.g. -1.5) after comparing with a reference thermometer.
        "temp_offset_c": float(os.environ.get("ENVMON_TEMP_OFFSET", "0")),
        "aq_burn_in_s": int(os.environ.get("ENVMON_AQ_BURN_IN", "300")),
        "simulate": _bool("ENVMON_SIMULATE"),
        # A log file is split into a new _partNNN file before it exceeds this size.
        "log_max_bytes": int(float(os.environ.get("ENVMON_LOG_MAX_MB", "1024")) * 1024 * 1024),
        "log_dir": data_dir / "logs",
    }
