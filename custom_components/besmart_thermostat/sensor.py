"""Sensors for the BeSMART integration.

- Battery status (enum) per thermostat, read from the coordinator data.
- PATCH 0.5: boiler system pressure and outdoor probe temperature exposed as
  real sensors (previously only attributes of the water heater), so they can be
  graphed and used in automations directly.
"""

import logging
from enum import StrEnum

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfPressure, UnitOfTemperature
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
    """Set up the sensor platform."""
    coordinator: BesmartDataUpdateCoordinator = config_entry.runtime_data

    new_entities = []
    # PATCH 0.5: topology now lives on the coordinator.
    for device in coordinator.interface_devices:
        wifi_box = device.wifi_box
        for thermostat in device.thermostats:
            room_id = thermostat.get("id")
            room_name = thermostat.get("name")
            new_entities.append(
                BatteryStatusSensor(coordinator, config_entry, wifi_box, room_id, room_name, device.device_info)
            )

        # PATCH 0.5: boiler sensors, only if this wifi box actually reports a
        # boiler.
        if device.boiler is not None:
            new_entities.append(
                BoilerSystemPressureSensor(coordinator, config_entry, wifi_box, device.device_info)
            )
            new_entities.append(
                BoilerOutdoorTemperatureSensor(coordinator, config_entry, wifi_box, device.device_info)
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


class BesmartBoilerSensor(CoordinatorEntity[BesmartDataUpdateCoordinator], SensorEntity):
    """Base class for boiler-derived sensors."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    # Key of the boiler data dict to expose; set by subclasses.
    DATA_KEY: str = ""

    def __init__(self, coordinator, config_entry, wifi_box, device_info):
        """Initialize the boiler sensor."""
        super().__init__(coordinator)
        self._wifi_box = wifi_box
        self._attr_unique_id = f"{config_entry.entry_id}:{wifi_box}:{self.DATA_KEY}"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.boiler_data(self._wifi_box) is not None

    @property
    def native_value(self):
        """Return the parsed value, or None when missing/unparsable."""
        data = self.coordinator.boiler_data(self._wifi_box) or {}
        value = data.get(self.DATA_KEY)
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None


class BoilerSystemPressureSensor(BesmartBoilerSensor):
    """Boiler heating circuit pressure."""

    DATA_KEY = "system_pressure"
    _attr_device_class = SensorDeviceClass.PRESSURE
    _attr_native_unit_of_measurement = UnitOfPressure.BAR
    _attr_suggested_display_precision = 1
    _attr_name = "Boiler System Pressure"


class BoilerOutdoorTemperatureSensor(BesmartBoilerSensor):
    """Boiler outdoor probe temperature (if a probe is installed)."""

    DATA_KEY = "outdoor_probe_temp"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_suggested_display_precision = 1
    _attr_name = "Outdoor Temperature"

    @property
    def native_unit_of_measurement(self):
        """Follow the unit reported by the boiler (0 = Celsius)."""
        data = self.coordinator.boiler_data(self._wifi_box) or {}
        if data.get("unit", "0") == "0":
            return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.FAHRENHEIT
