"""Diagnostics support for the BeSMART integration.

PATCH 0.5: lets users attach a redacted diagnostics dump to GitHub issues
(Settings -> Devices & Services -> BeSMART -> three-dot menu -> Download
diagnostics) without leaking credentials or personal identifiers.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

# Keys removed from the dump: credentials from the entry options, plus any
# user identifiers that may appear in the cloud payloads.
TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    "email",
    "user_id",
    "token",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "coordinator_data": async_redact_data(coordinator.data or {}, TO_REDACT),
        "wifi_boxes": [device.wifi_box for device in coordinator.interface_devices],
    }
