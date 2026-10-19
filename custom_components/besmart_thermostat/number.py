"""PATCH 0.8: max/min flow temperature and room sensor influence per thermostat."""

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    MAX_HEATING_SET_POINT_RANGE,
    MIN_HEATING_SET_POINT_RANGE,
    SENSOR_INFLUENCE_RANGE,
    parse_value,
)
from .entity import BesmartThermostatSettingEntity, thermostat_setting_entities


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the number platform."""
    entities = thermostat_setting_entities(
        config_entry.runtime_data,
        config_entry,
        (MaxFlowTemperatureNumber, MinFlowTemperatureNumber, SensorInfluenceNumber),
    )
    if entities:
        async_add_entities(entities)


class BesmartSettingNumber(BesmartThermostatSettingEntity, NumberEntity):
    """Integer thermostat setting, limits of the official app."""

    # Box, not slider: an installer parameter must not change on a stray drag.
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    RANGE: tuple[float, float]

    def __init__(self, *args) -> None:
        """Initialize the number."""
        super().__init__(*args)
        self._attr_native_min_value, self._attr_native_max_value = self.RANGE

    @property
    def native_value(self) -> float | None:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self._async_write(value)
        except Exception:
            # PATCH 0.8 (b2): re-send the real state after a refused value.
            # The value typed in the number box stays visible anyway: Home
            # Assistant does not send an unchanged state to the frontend
            # (only last_reported changes), a frontend limit. The dynamic
            # limits below make Home Assistant refuse such values first.
            self.async_write_ha_state()
            raise

    def _other(self, field: str) -> float | None:
        """Current value of the other set point (dynamic limits)."""
        data = self.coordinator.thermostat_data(self._wifi_box, self._room_id)
        return parse_value(data.get(field)) if data else None


class MaxFlowTemperatureNumber(BesmartSettingNumber):
    """Maximum flow temperature (max_heating_set_point), 15-80 °C."""

    FIELD = "max_heating_set_point"
    RANGE = MAX_HEATING_SET_POINT_RANGE
    _attr_translation_key = "max_heating_set_point"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:thermometer-chevron-up"

    @property
    def native_min_value(self) -> float:
        """PATCH 0.8 (b2): never below the current minimum (within 15-80)."""
        low, high = self.RANGE
        current_min = self._other("min_heating_set_point")
        return low if current_min is None else min(high, max(low, current_min))


class MinFlowTemperatureNumber(BesmartSettingNumber):
    """Minimum flow temperature (min_heating_set_point), 10-75 °C."""

    FIELD = "min_heating_set_point"
    RANGE = MIN_HEATING_SET_POINT_RANGE
    _attr_translation_key = "min_heating_set_point"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:thermometer-chevron-down"

    @property
    def native_max_value(self) -> float:
        """PATCH 0.8 (b2): never above the current maximum (within 10-75)."""
        low, high = self.RANGE
        current_max = self._other("max_heating_set_point")
        return high if current_max is None else min(high, max(low, current_max))


class SensorInfluenceNumber(BesmartSettingNumber):
    """Room sensor influence (sensor_influence), 0-20."""

    FIELD = "sensor_influence"
    RANGE = SENSOR_INFLUENCE_RANGE
    _attr_translation_key = "sensor_influence"
    _attr_icon = "mdi:home-thermometer"
