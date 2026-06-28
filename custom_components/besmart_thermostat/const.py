"""Constants for the BeSMART Thermostat."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "besmart_thermostat"

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.WATER_HEATER,
    Platform.SENSOR,
]

# --- Config / options keys -------------------------------------------------
# verify_ssl: TLS certificate verification toggle (was hardcoded False in api.py).
# scan_interval: coordinator polling period (cloud refreshes roughly every ~3 min).
CONF_SCAN_INTERVAL = "scan_interval"

# verify_ssl default for the SCHEMA (new installs are secure-by-default = True).
# NOTE: __init__ reads options.get(CONF_VERIFY_SSL, False) so that EXISTING entries
# without the key keep the legacy behaviour (no verification) and don't break on
# upgrade. Opening the Options form shows True and lets the user opt in.
DEFAULT_VERIFY_SSL = True

# Coordinator update interval (seconds): default + allowed range for the option.
DEFAULT_SCAN_INTERVAL = 300
MIN_SCAN_INTERVAL = 180
MAX_SCAN_INTERVAL = 600
SCAN_INTERVAL_STEP = 60

# Fallback used if the stored value is somehow out of range / missing.
UPDATE_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
