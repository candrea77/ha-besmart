"""PATCH 0.8: max/min flow temperature and room sensor influence per thermostat."""

import asyncio
import time

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    MAX_HEATING_SET_POINT_RANGE,
    MIN_HEATING_SET_POINT_RANGE,
    SENSOR_INFLUENCE_RANGE,
    parse_value,
)
from .entity import BesmartThermostatSettingEntity, thermostat_setting_entities

# PATCH 0.8.1: seconds without changes before the value is written: clicks
# on the arrows of the number box send a single PUT with the final value.
SETTINGS_WRITE_DEBOUNCE = 1.5
# PATCH 0.8.1: seconds a written value is shown while the polled data still
# reports the old one (the cloud refreshes module data every ~3 minutes).
SETTINGS_CONFIRM_TIMEOUT = 300


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
        # PATCH 0.8.1: debounce. Every change bumps the generation; only the
        # change still current after SETTINGS_WRITE_DEBOUNCE s is written.
        self._generation = 0
        # monotonic() until which a written value is shown (None: not
        # written yet).
        self._confirm_until: float | None = None

    @property
    def _pending_key(self) -> tuple[str, str, str]:
        return (self._wifi_box, self._room_id, self.FIELD)

    @property
    def _pending(self) -> float | None:
        return self.coordinator.pending_settings.get(self._pending_key)

    @property
    def native_value(self) -> float | None:
        """PATCH 0.8.1: the chosen value until the cloud reports it."""
        pending = self._pending
        return self._value if pending is None else pending

    def _set_pending(self, value: float | None) -> None:
        if value is None:
            self.coordinator.pending_settings.pop(self._pending_key, None)
        else:
            self.coordinator.pending_settings[self._pending_key] = value
        # All listeners: the other set point's dynamic limits change too.
        self.coordinator.async_update_listeners()

    @callback
    def _handle_coordinator_update(self) -> None:
        """PATCH 0.8.1: drop a written value once polled (or too old)."""
        pending = self._pending
        if (
            pending is not None
            and self._confirm_until is not None
            and (self._value == pending or time.monotonic() > self._confirm_until)
        ):
            self._confirm_until = None
            self.coordinator.pending_settings.pop(self._pending_key, None)
        super()._handle_coordinator_update()

    async def async_set_native_value(self, value: float) -> None:
        """PATCH 0.8.1: show the value now, write it once the changes stop.

        A change superseded by a newer one within SETTINGS_WRITE_DEBOUNCE s
        returns without writing; the last one writes and reports its error.
        """
        self._check_online()
        self._generation += 1
        generation = self._generation
        self._confirm_until = None
        self._set_pending(value)
        await asyncio.sleep(SETTINGS_WRITE_DEBOUNCE)
        if generation != self._generation:
            return
        try:
            await self._async_write(value)
        except Exception:
            # PATCH 0.8 (b2) / 0.8.1: back to the real state of the cloud,
            # unless a newer value is waiting to be written.
            if generation == self._generation:
                self._set_pending(None)
            raise
        if generation == self._generation:
            self._confirm_until = time.monotonic() + SETTINGS_CONFIRM_TIMEOUT
            self._handle_coordinator_update()

    async def async_will_remove_from_hass(self) -> None:
        """PATCH 0.8.1: a waiting change is dropped on unload."""
        self._generation += 1
        self.coordinator.pending_settings.pop(self._pending_key, None)
        await super().async_will_remove_from_hass()

    def _other(self, field: str) -> float | None:
        """Current value of the other set point (dynamic limits)."""
        # PATCH 0.8.1: its chosen value while it is being written.
        pending = self.coordinator.pending_settings.get((self._wifi_box, self._room_id, field))
        if pending is not None:
            return pending
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
