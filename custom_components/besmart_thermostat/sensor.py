import logging
from enum import StrEnum
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import DiscoveryInfoType
from .const import DOMAIN

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
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the battery sensor platform."""
    if DOMAIN not in hass.data or not hass.data[DOMAIN]:
        _LOGGER.error("No climate entities found in hass.data[%s]", DOMAIN)
        return

    new_entities = []
    for device in config_entry.interface_devices:
        for thermostat in device.thermostats:
            room_id = thermostat.get("id")
            room_name = thermostat.get("name")
            unique_id = f"{config_entry.entry_id}:{room_id}"
            climate_entity = hass.data[DOMAIN].get(unique_id)
            if climate_entity:
                new_entities.append(BatteryStatusSensor(climate_entity, room_name, device.device_info))
            else:
                _LOGGER.warning("Climate entity not found for unique_id %s", unique_id)

    if new_entities:
        async_add_entities(new_entities, update_before_add=True)

class BatteryStatusSensor(SensorEntity):
    """Representation of an enum sensor for BeSMART thermostat battery status."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(BatteryStates)
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "battery_status"

    def __init__(self, climate_entity, room_name, device_info):
        """Initialize the battery sensor."""
        self._climate_entity = climate_entity
        self._attr_unique_id = f"{climate_entity.unique_id}_battery_status"
        self._attr_name = f"{room_name} Thermostat Battery Status"
        self._attr_device_info = device_info
        self._attr_state = BatteryStates.Unknown

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        battery_low = None
        if self._climate_entity and self._climate_entity.extra_state_attributes:
            battery_low = self._climate_entity.extra_state_attributes.get("battery_low")
        self._attr_state = BATTERY_STATES_MAP.get(battery_low, BatteryStates.Unknown)
        return self._attr_state

    @property
    def icon(self):
        """Return the icon based on the state."""
        return {
            BatteryStates.Low: "mdi:battery-low",
            BatteryStates.Good: "mdi:battery",
            BatteryStates.Unknown: "mdi:battery-unknown",
        }.get(self._attr_state, "mdi:battery-unknown")