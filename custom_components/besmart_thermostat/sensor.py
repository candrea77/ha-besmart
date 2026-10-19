"""Sensors for the BeSMART integration.

- Battery status (enum) per thermostat, read from the coordinator data.
- 0.5: boiler system pressure and outdoor probe temperature exposed as real
  sensors (previously only attributes of the water heater).
- PATCH 0.6: boiler water temperature (central heating flow temperature,
  cloud key ``heating_current_temp``) and one temperature sensor per
  thermostat (cloud key ``current_temp``), created dynamically like the
  battery sensors.
- PATCH 0.7: "Boiler Modulation" (%) polled from Boilers/modulation_ratio, and
  a single "Technical data" sensor holding the last on-demand reading of the
  single-value technical endpoints (triggered by the "Read technical data"
  button) as attributes, with the reading time as state.
"""

import logging
from enum import StrEnum

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import parse_value
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
    for device in coordinator.interface_devices:
        wifi_box = device.wifi_box
        for thermostat in device.thermostats:
            room_id = thermostat.get("id")
            room_name = thermostat.get("name")
            new_entities.append(
                BatteryStatusSensor(coordinator, config_entry, wifi_box, room_id, room_name, device.thermostat_device_info(room_id))
            )
            # PATCH 0.6: room temperature measured by each thermostat.
            new_entities.append(
                ThermostatTemperatureSensor(coordinator, config_entry, wifi_box, room_id, room_name, device.thermostat_device_info(room_id))
            )

        # Boiler sensors, only if this wifi box actually reports a boiler.
        if device.boiler is not None:
            new_entities.append(
                BoilerSystemPressureSensor(coordinator, config_entry, wifi_box, device.device_info)
            )
            new_entities.append(
                BoilerOutdoorTemperatureSensor(coordinator, config_entry, wifi_box, device.device_info)
            )
            # PATCH 0.6: central heating flow (water) temperature.
            new_entities.append(
                BoilerWaterTemperatureSensor(coordinator, config_entry, wifi_box, device.device_info)
            )
            # PATCH 0.7: modulation (polled) + technical data (on demand).
            new_entities.append(
                BoilerModulationSensor(coordinator, config_entry, wifi_box, device.device_info)
            )
            new_entities.append(
                BoilerTechnicalDataSensor(coordinator, config_entry, wifi_box, device.device_info)
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
        # PATCH 0.7.1: translated name "{room} Thermostat Battery Status".
        # PATCH 0.8 (b2): no {room} in the name: the device (named
        # after the room) already prefixes it.
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


class ThermostatTemperatureSensor(CoordinatorEntity[BesmartDataUpdateCoordinator], SensorEntity):
    """PATCH 0.6: temperature measured by a BeSMART thermostat.

    Same value shown by the climate entity, exposed as a standalone sensor for
    easier graphing, statistics and use in automations.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "room_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, config_entry, wifi_box, room_id, room_name, device_info):
        """Initialize the thermostat temperature sensor."""
        super().__init__(coordinator)
        self._wifi_box = wifi_box
        self._room_id = room_id
        self._attr_unique_id = f"{config_entry.entry_id}:{room_id}_temperature"
        # PATCH 0.7.1: translated name "{room} Temperature".
        # PATCH 0.8 (b2): no {room} in the name: the device (named
        # after the room) already prefixes it.
        self._attr_device_info = device_info

    @property
    def _data(self):
        return self.coordinator.thermostat_data(self._wifi_box, self._room_id)

    @property
    def available(self) -> bool:
        return super().available and self._data is not None

    @property
    def native_value(self):
        """Return the measured temperature, or None when missing/unparsable."""
        data = self._data or {}
        value = data.get("current_temp")
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    @property
    def native_unit_of_measurement(self):
        """Follow the unit reported by the thermostat (0 = Celsius)."""
        data = self._data or {}
        if str(data.get("unit", "0")) == "0":
            return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.FAHRENHEIT


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
    _attr_translation_key = "system_pressure"  # PATCH 0.7.1: was "Boiler System Pressure"


class BoilerOutdoorTemperatureSensor(BesmartBoilerSensor):
    """Boiler outdoor probe temperature (if a probe is installed)."""

    DATA_KEY = "outdoor_probe_temp"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_suggested_display_precision = 1
    _attr_translation_key = "outdoor_temperature"  # PATCH 0.7.1: was "Outdoor Temperature"

    @property
    def native_unit_of_measurement(self):
        """Follow the unit reported by the boiler (0 = Celsius)."""
        data = self.coordinator.boiler_data(self._wifi_box) or {}
        if data.get("unit", "0") == "0":
            return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.FAHRENHEIT


class BoilerWaterTemperatureSensor(BesmartBoilerSensor):
    """PATCH 0.6: central heating flow (water) temperature.

    Cloud key ``heating_current_temp``: temperature of the water sent by the
    boiler to the heating circuit.
    """

    DATA_KEY = "heating_current_temp"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_suggested_display_precision = 1
    _attr_translation_key = "water_temperature"  # PATCH 0.7.1: was "Boiler Water Temperature"

    @property
    def native_unit_of_measurement(self):
        """Follow the unit reported by the boiler (0 = Celsius)."""
        data = self.coordinator.boiler_data(self._wifi_box) or {}
        if data.get("unit", "0") == "0":
            return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.FAHRENHEIT


class BoilerModulationSensor(CoordinatorEntity[BesmartDataUpdateCoordinator], SensorEntity):
    """PATCH 0.7: burner modulation ratio (Boilers/modulation_ratio).

    Invalid values (empty/null/DNCON/disable) and failed reads are unknown;
    "0.0" is a valid 0 %. The entity is unavailable only when the whole
    coordinator update fails.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "modulation"  # PATCH 0.7.1: was "Boiler Modulation"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:fire-circle"

    def __init__(self, coordinator, config_entry, wifi_box, device_info):
        """Initialize the modulation sensor."""
        super().__init__(coordinator)
        self._wifi_box = wifi_box
        self._attr_unique_id = f"{config_entry.entry_id}:{wifi_box}:modulation_ratio"
        self._attr_device_info = device_info

    @property
    def native_value(self):
        """Return the modulation percentage, or None when unknown."""
        return parse_value(self.coordinator.modulation(self._wifi_box))


# PATCH 0.7 (b2): technical endpoint -> attribute name. Numeric values go
# through parse_value() (invalid -> None, "0.0" -> 0.0); endpoints that failed
# or are unsupported give None. Hour counters, with the official app's labels
# (values-it/strings.xml):
# - heat_exchanger_hours_amassed: "Ore di funzionamento in regime di elevata
#   condensazione" (operating hours in high-condensation mode; the resource
#   id says "cleaning", the label does not);
# - worked_hours_in_heating: "Ore di funzionamento in riscaldamento";
# - worked_hours_in_dhw: "Ore di funzionamento in sanitario".
TECHNICAL_NUMERIC_ATTRIBUTES = {
    "system_pressure": "pressure_bar",
    "flow_temperature": "flow_temperature",
    "return_temperature": "return_temperature",
    "flues_temperature": "flue_temperature",
    "dhw_temperature": "dhw_temperature",
    "dhw_set_point": "dhw_set_point",
    "heating_set_point": "heating_set_point",
    "dhw_flow_rate": "dhw_flow_rate",
    "external_temperature": "outdoor_temperature",
    "heat_exchanger_hours_amassed": "high_condensation_hours",
    "worked_hours_in_heating": "heating_hours",
    "worked_hours_in_dhw": "dhw_hours",
}
TECHNICAL_ATTRIBUTE_NAMES = (
    "modulation_pct",
    *TECHNICAL_NUMERIC_ATTRIBUTES.values(),
    "flame",
    "temperature_unit",
    "unsupported_endpoints",
)


def technical_attributes(reading: dict) -> dict:
    """PATCH 0.7: convert a stored technical reading into entity attributes."""
    values = reading.get("values") or {}
    attrs = {"modulation_pct": parse_value(reading.get("modulation"))}
    attrs.update(
        {
            attr: parse_value(values.get(endpoint))
            for endpoint, attr in TECHNICAL_NUMERIC_ATTRIBUTES.items()
        }
    )
    flame = parse_value(values.get("flame_status"))
    attrs["flame"] = None if flame is None else bool(int(flame))
    unit = parse_value(values.get("unit"))
    attrs["temperature_unit"] = {
        0: UnitOfTemperature.CELSIUS,
        1: UnitOfTemperature.FAHRENHEIT,
    }.get(None if unit is None else int(unit))
    attrs["unsupported_endpoints"] = list(reading.get("unsupported") or [])
    return attrs


class BoilerTechnicalDataSensor(
    CoordinatorEntity[BesmartDataUpdateCoordinator], RestoreEntity, SensorEntity
):
    """PATCH 0.7: last on-demand reading of the technical endpoints.

    State: time of the last successful reading. Attributes: the parsed values.
    Never polled: it only changes when the "Read technical data" button is
    pressed. The last reading is restored across Home Assistant restarts and
    stays visible even while the cloud is unreachable.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "technical_data"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clipboard-text-clock"

    def __init__(self, coordinator, config_entry, wifi_box, device_info):
        """Initialize the technical data sensor."""
        super().__init__(coordinator)
        self._wifi_box = wifi_box
        self._attr_unique_id = f"{config_entry.entry_id}:{wifi_box}:technical_data"
        self._attr_device_info = device_info
        self._restored_last_read = None
        self._restored_attrs: dict = {}

    async def async_added_to_hass(self) -> None:
        """Restore the last reading saved before a restart."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None:
            return
        self._restored_last_read = dt_util.parse_datetime(last_state.state)
        if self._restored_last_read is not None:
            self._restored_attrs = {
                k: last_state.attributes.get(k) for k in TECHNICAL_ATTRIBUTE_NAMES
            }

    @property
    def available(self) -> bool:
        """A stored snapshot does not depend on the cloud being reachable."""
        return True

    @property
    def native_value(self):
        """Return the time of the last successful reading."""
        reading = self.coordinator.technical_data(self._wifi_box)
        if reading is not None:
            return reading["last_read"]
        return self._restored_last_read

    @property
    def extra_state_attributes(self):
        """Return the parsed technical values of the last reading."""
        reading = self.coordinator.technical_data(self._wifi_box)
        if reading is not None:
            return technical_attributes(reading)
        return self._restored_attrs or None
