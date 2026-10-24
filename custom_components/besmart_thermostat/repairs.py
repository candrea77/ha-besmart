"""PATCH 0.10: Repairs fix flows.

The one-time notice of the renamed presets/attributes (raised by the config
entry migration 2.1 -> 2.2) is closed by confirming it.
"""

from __future__ import annotations

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the flow that closes a Repairs issue of this integration."""
    return ConfirmRepairFlow()
