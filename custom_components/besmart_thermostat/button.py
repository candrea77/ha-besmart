"""Buttons for the BeSMART integration.

PATCH 0.7: "Read technical data" button. Each press reads
Boilers/technical_datas once (read-only GET) and stores the result in the
"Technical data" sensor. The endpoint is never polled.
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
        """Read Boilers/technical_datas (raises HomeAssistantError on failure)."""
        await self._coordinator.async_read_technical(self._wifi_box)
