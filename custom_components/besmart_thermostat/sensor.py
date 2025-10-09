import logging
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import DiscoveryInfoType

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the battery sensor platform."""
    new_entities = []
    for device in config_entry.interface_devices:
        wifi_box = device.wifi_box
        for thermostat in device.thermostats:
            room_id = thermostat.get("id")
            room_name = thermostat.get("name")
            # Look up climate entity by unique_id
            unique_id = f"{config_entry.entry_id}:{room_id}"
            climate_entity = hass.data.get(DOMAIN, {}).get(unique_id)
            if climate_entity:
                new_entities.append(BatteryLowSensor(climate_entity, room_name, device.device_info))
    
    if new_entities:
        async_add_entities(new_entities, update_before_add=True)

class BatteryLowSensor(BinarySensorEntity):
    """Representation of a binary sensor for BeSMART thermostat battery status."""

    _attr_has_entity_name = True
    _attr_should_poll = True

    def __init__(self, climate_entity, room_name, device_info):
        """Initialize the battery sensor."""
        self._climate_entity = climate_entity
        self._attr_unique_id = f"{climate_entity.unique_id}_battery_low"
        self._attr_name = f"{room_name} Thermostat Battery Low"
        self._attr_device_class = BinarySensorDeviceClass.BATTERY
        self._attr_device_info = device_info  # Inherit device info from climate entity

    def update(self) -> None:
        """Fetch new state data for the sensor."""
        try:
            if self._climate_entity is None or self._climate_entity.extra_state_attributes is None:
                _LOGGER.warning("Climate entity or extra_state_attributes not available for %s", self._attr_unique_id)
                self._attr_is_on = None
                return
            battery_low = self._climate_entity.extra_state_attributes.get("battery_low")
            self._attr_is_on = battery_low  # True (low), False (good), or None (unknown)
            _LOGGER.debug("Updated battery sensor %s: battery_low=%s", self._attr_unique_id, battery_low)
        except Exception as ex:
            _LOGGER.error("Error updating battery sensor %s: %s", self._attr_unique_id, ex)
            self._attr_is_on = None