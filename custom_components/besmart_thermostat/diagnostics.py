"""Diagnostics support for the BeSMART integration.

PATCH 0.5: lets users attach a redacted diagnostics dump to GitHub issues
(Settings -> Devices & Services -> BeSMART -> three-dot menu -> Download
diagnostics) without leaking credentials or personal identifiers.

PATCH 0.7 (security): the BeSMART cloud has no per-user token, so the user id
and the wifi box id act as credentials. Wifi box ids are replaced by
"box_1", "box_2"... everywhere (also as dictionary keys, which
async_redact_data does not touch), sensitive keys are redacted and any known
id left in a string value is masked.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

# Keys removed from the dump: credentials, plus any user/box identifier that
# may appear in the cloud payloads.
TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    "email",
    "user_id",
    "token",
    "wifi_box_id",
    "wifi_box",
    "mac",
    "mac_address",
    "serial",
    "serial_number",
    "imei",
    "phone",
}


def _pseudonymize(obj: Any, aliases: dict[str, str], redact) -> Any:
    """Replace wifi box ids (keys and values) and scrub strings."""
    if isinstance(obj, dict):
        return {
            # PATCH 0.7: known thermostat ids used as keys are masked too.
            aliases.get(str(k)) or (redact(k) if isinstance(k, str) else k): _pseudonymize(v, aliases, redact)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_pseudonymize(v, aliases, redact) for v in obj]
    if isinstance(obj, str):
        return aliases.get(obj) or redact(obj)
    return obj


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    client = coordinator.client
    aliases = {
        str(device.wifi_box): f"box_{index}"
        for index, device in enumerate(coordinator.interface_devices, start=1)
    }

    def clean(data: Any) -> Any:
        return _pseudonymize(async_redact_data(data, TO_REDACT), aliases, client._redact)

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": clean(dict(entry.options)),
        "coordinator_data": clean(coordinator.data or {}),
        "wifi_boxes": list(aliases.values()),
        # PATCH 0.7: last on-demand technical reading per box.
        "technical_data": clean(
            {
                box: {**t, "last_read": t["last_read"].isoformat()}
                for box, t in coordinator.technical.items()
            }
        ),
    }
