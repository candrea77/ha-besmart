"""Constants for the BeSMART Thermostat."""

import hashlib

from homeassistant.components.climate import PRESET_AWAY
from homeassistant.const import Platform

DOMAIN = "besmart_thermostat"

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.WATER_HEATER,
    Platform.SENSOR,
    # PATCH 0.6: new binary_sensor platform (boiler flame, circulator, DHW tap).
    Platform.BINARY_SENSOR,
    # PATCH 0.7: "Read technical data" and "Refresh now" buttons.
    Platform.BUTTON,
    # PATCH 0.8: thermostat settings (climatic curve, flow temperatures,
    # room sensor influence).
    Platform.NUMBER,
    Platform.SELECT,
    # PATCH 0.9: climatic curve chart (SVG image, read only).
    Platform.IMAGE,
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

# PATCH 0.5: removed unused UPDATE_INTERVAL timedelta (dead code; the
# coordinator computes its interval from the option via _resolve_interval).

# PATCH 0.7 (security): Repairs issue ids (suffixed with the entry id).
ISSUE_VERIFY_SSL_DISABLED = "verify_ssl_disabled"
ISSUE_TLS_CERTIFICATE = "tls_certificate_error"
# PATCH 0.10: one-time notice of the renamed presets/attributes (raised by the
# config entry migration 2.1 -> 2.2, i.e. only for users who update).
ISSUE_PRESETS_RENAMED = "presets_renamed"

# PATCH 0.10: thermostat presets. Home Assistant translation keys must be
# lowercase, so the old uppercase values (AUTO, MANUAL, ECO, PARTY, IDLE)
# became these; "away" is Home Assistant's standard preset (BeSMART
# "Vacanza - Economy"), the others are translated by this integration.
# BeSMART mode 5 (DHW only) is not a preset: it is hvac_mode off.
PRESET_AUTO = "auto"
PRESET_MANUAL = "manual"
PRESET_PARTY = "party"
PRESET_FROST_PROTECTION = "frost_protection"
# preset -> BeSMART thermostat mode (order = order shown in the UI)
PRESET_TO_BESMART_MODE = {
    PRESET_AUTO: 0,
    PRESET_MANUAL: 1,
    PRESET_AWAY: 2,
    PRESET_PARTY: 3,
    PRESET_FROST_PROTECTION: 4,
}


def account_unique_id(user_id: str) -> str:
    """PATCH 0.7: config entry unique_id derived from the cloud user id.

    A one-way hash, so the user id (which acts as a credential together with
    the wifi box id) is never stored in clear in the entry registry.
    """
    return hashlib.sha256(f"besmart:{user_id}".encode()).hexdigest()[:32]
