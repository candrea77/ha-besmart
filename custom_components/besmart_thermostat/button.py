"""Buttons for the BeSMART integration.

PATCH 0.7:
- "Read technical data" (per boiler): each press reads the single-value
  technical endpoints once (read-only GETs) and stores the result in the
  "Technical data" sensor. They are never polled.
- "Refresh now" (one per entry): immediate refresh of all polled data
  (boiler, thermostats, modulation). Repeated presses within a few seconds
  are ignored. The cloud itself refreshes the module data roughly every 3
  minutes, so a forced refresh may still return unchanged values.
"""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BesmartDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the button platform."""
    coordinator: BesmartDataUpdateCoordinator = config_entry.runtime_data

    new_entities = [
        ReadTechnicalDataButton(coordinator, config_entry, device.wifi_box, device.device_info)
        for device in coordinator.interface_devices
        # Only if this wifi box actually reports a boiler.
        if device.boiler is not None
    ]
    # One refresh button per entry: the coordinator refreshes every wifi box.
    if coordinator.interface_devices:
        new_entities.append(
            RefreshNowButton(coordinator, config_entry, coordinator.interface_devices[0].device_info)
        )

    if new_entities:
        async_add_entities(new_entities)


class ReadTechnicalDataButton(ButtonEntity):
    """Read the boiler technical data once."""

    _attr_has_entity_name = True
    _attr_translation_key = "read_technical_data"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clipboard-arrow-down"

    def __init__(self, coordinator, config_entry, wifi_box, device_info):
        """Initialize the button."""
        self._coordinator = coordinator
        self._wifi_box = wifi_box
        self._attr_unique_id = f"{config_entry.entry_id}:{wifi_box}:read_technical_data"
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        """Read the technical endpoints (HomeAssistantError if none answers)."""
        await self._coordinator.async_read_technical(self._wifi_box)


class RefreshNowButton(ButtonEntity):
    """Refresh all polled data immediately."""

    _attr_has_entity_name = True
    _attr_translation_key = "refresh_now"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator, config_entry, device_info):
        """Initialize the button."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{config_entry.entry_id}:refresh_now"
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        """Refresh now (HomeAssistantError if the refresh fails)."""
        await self._coordinator.async_manual_refresh()
