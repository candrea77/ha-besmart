"""Battery status sensor for BeSMART thermostats.

PATCH: decoupled from the climate entity. The sensor used to read battery state
via hass.data[DOMAIN][unique_id] and the climate entity's extra_state_attributes.
It now reads battery_power directly from the coordinator data, like every other
entity.
"""

import logging
from enum import StrEnum

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import BesmartDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class BatteryStates(StrEnum):
    Low = "low"
    Good = "good"
    Unknown = "unknown"


BATTERY_STATES_MAP = {
    True: BatteryStates.Low,
    False: BatteryStates.Good,
    None: BatteryStates.Unknown,
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the battery sensor platform."""
    coordinator: BesmartDataUpdateCoordinator = config_entry.runtime_data

    new_entities = []
    for device in config_entry.interface_devices:
        wifi_box = device.wifi_box
        for thermostat in device.thermostats:
            room_id = thermostat.get("id")
            room_name = thermostat.get("name")
            new_entities.append(
                BatteryStatusSensor(coordinator, config_entry, wifi_box, room_id, room_name, device.device_info)
            )

    if new_entities:
        async_add_entities(new_entities)


class BatteryStatusSensor(CoordinatorEntity[BesmartDataUpdateCoordinator], SensorEntity):
    """Enum sensor for BeSMART thermostat battery status."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(BatteryStates)
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "battery_status"

    def __init__(self, coordinator, config_entry, wifi_box, room_id, room_name, device_info):
        """Initialize the battery sensor."""
        super().__init__(coordinator)
        self._wifi_box = wifi_box
        self._room_id = room_id
        self._attr_unique_id = f"{config_entry.entry_id}:{room_id}_battery_status"
        self._attr_name = f"{room_name} Thermostat Battery Status"
        self._attr_device_info = device_info

    def _battery_low(self):
        """Return True/False/None from the coordinator's thermostat data."""
        data = self.coordinator.thermostat_data(self._wifi_box, self._room_id)
        if not data:
            return None
        try:
            return bool(int(data.get("battery_power", 0)))
        except (ValueError, TypeError):
            return None

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return BATTERY_STATES_MAP.get(self._battery_low(), BatteryStates.Unknown)

    @property
    def icon(self):
        """Return the icon based on the state."""
        return {
            BatteryStates.Low: "mdi:battery-low",
            BatteryStates.Good: "mdi:battery",
            BatteryStates.Unknown: "mdi:battery-unknown",
        }.get(self.native_value, "mdi:battery-unknown")
