# -*- coding: utf-8 -*-
"""
Support for Riello's Besmart water heater controller.
Be aware the thermostat may require more then 3 minute to refresh its states.

version: 0.5 (DataUpdateCoordinator + RestoreEntity)
"""
import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.water_heater import WaterHeaterEntity, WaterHeaterEntityFeature
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_NAME,
    UnitOfTemperature,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import BesmartDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "BeSMART Water Heater"

ATTR_PREVIOUS_CLIMATE_ACTIVE = "previous_climate_active"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: BesmartDataUpdateCoordinator = config_entry.runtime_data

    new_entities = []
    # PATCH 0.5: topology now lives on the coordinator.
    for device in coordinator.interface_devices:
        wifi_box = device.wifi_box
        new_entities.append(WaterHeater(coordinator, config_entry, wifi_box, device))

    if new_entities:
        async_add_entities(new_entities)


# pylint: disable=abstract-method
# pylint: disable=too-many-instance-attributes
class WaterHeater(
    CoordinatorEntity[BesmartDataUpdateCoordinator], WaterHeaterEntity, RestoreEntity
):
    """Representation of a Besmart water heater."""

    _attr_has_entity_name = True
    _attr_unique_id: str

    # BeSmart work_mode
    STATE_GAS = "gas"  # Normal or DHW operation
    STATE_OFF = "off"  # Anti-frost operation

    DHW_TEMP_MAX = 60.0
    DHW_TEMP_MIN = 30.0
    DHW_TEMP_STEP = 1.0
    DHW_TEMP_PRECISION = 1.0

    def __init__(self, coordinator, config_entry, wifi_box, interface_device):
        """Initialize the water heater."""
        super().__init__(coordinator)
        self._entry_name = config_entry.options[CONF_NAME]
        self._entry_id = config_entry.entry_id
        self._wifi_box = wifi_box
        self._cl = coordinator.client

        # Safe defaults
        self._current_temp = 0.0
        self._current_mode = "2"
        self._tempSet = 0.0
        self._flame_status = 0
        self._outdoor_temperature = 0.0
        self._system_pressure = 0.0
        # PATCH 0.5: restored across restarts via RestoreEntity (see
        # async_added_to_hass), so turn-on after a reboot picks the right mode.
        self._previous_climate_active = None

        if len(interface_device.thermostats) > 0:
            self._current_unit = interface_device.thermostats[0].get("unit", "0")
        else:
            self._current_unit = "0"

        # link to BeSMART device
        self._attr_device_info = interface_device.device_info

        # unique_id = <deviceID>:<wifiBox>:water_heater
        self._attr_unique_id = f"{self._entry_id}:{self._wifi_box}:water_heater"

        # PATCH 0.5: removed the dead async_generate_entity_id call.
        self._attr_name = "Water Heater"

        # Disable backwards compatibility for new turn_on/off methods
        self._enable_turn_on_off_backwards_compatibility = False

        # Populate initial state from coordinator data.
        self._update_attrs()

    async def async_added_to_hass(self) -> None:
        """Restore previous_climate_active after a restart."""
        await super().async_added_to_hass()
        if self._previous_climate_active is None:
            last_state = await self.async_get_last_state()
            if last_state is not None:
                value = last_state.attributes.get(ATTR_PREVIOUS_CLIMATE_ACTIVE)
                if isinstance(value, bool):
                    self._previous_climate_active = value

    @property
    def _data(self):
        """Latest boiler data dict from the coordinator (or None)."""
        return self.coordinator.boiler_data(self._wifi_box)

    @property
    def available(self) -> bool:
        return super().available and self._data is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_attrs()
        self.async_write_ha_state()

    def _update_attrs(self) -> None:
        """Parse coordinator boiler data into entity state (no I/O)."""
        boiler = self._data
        if not boiler:
            return

        self._current_mode = boiler.get("work_mode", "2")

        try:
            self._tempSet = float(boiler.get("dhw_target_temp", 0.0))
        except (ValueError, TypeError):
            self._tempSet = 0.0

        try:
            self._current_temp = float(boiler.get("dhw_current_temp", 0.0))
        except (ValueError, TypeError):
            self._current_temp = 0.0

        try:
            # PATCH 0.5: flame_status is a discrete state, parse as int.
            self._flame_status = int(float(boiler.get("flame_status", 0)))
        except (ValueError, TypeError):
            self._flame_status = 0

        try:
            self._outdoor_temperature = float(boiler.get("outdoor_probe_temp", 0.0))
        except (ValueError, TypeError):
            self._outdoor_temperature = 0.0

        try:
            self._system_pressure = float(boiler.get("system_pressure", 0.0))
        except (ValueError, TypeError):
            self._system_pressure = 0.0

        self._current_unit = boiler.get("unit", "0")

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._current_temp

    @property
    def max_temp(self):
        """The maximum temperature."""
        return self.DHW_TEMP_MAX

    @property
    def min_temp(self):
        """The minimum temperature."""
        return self.DHW_TEMP_MIN

    @property
    def precision(self):
        """The temperature precision."""
        return self.DHW_TEMP_PRECISION

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._tempSet

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return self.DHW_TEMP_STEP

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        if self._current_unit == "0":
            return UnitOfTemperature.CELSIUS
        else:
            return UnitOfTemperature.FAHRENHEIT

    @property
    def current_operation(self):
        """Return the current work mode."""
        if self._current_mode == "2":
            return self.STATE_OFF
        else:
            return self.STATE_GAS

    @property
    def operation_list(self):
        """Return available work modes."""
        return [self.STATE_GAS, self.STATE_OFF]

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE |
            WaterHeaterEntityFeature.OPERATION_MODE
        )

    @property
    def extra_state_attributes(self):
        """Return the device specific state attributes."""
        return {
            "flame_status": self._flame_status,
            "outdoor_temperature": self._outdoor_temperature,
            "system_pressure": self._system_pressure,
            # PATCH 0.5: exposed so RestoreEntity can persist it across restarts.
            ATTR_PREVIOUS_CLIMATE_ACTIVE: self._previous_climate_active,
        }

    async def async_turn_on(self):
        """Turn on the heater."""
        await self.async_set_operation_mode(self.STATE_GAS)

    async def async_turn_off(self):
        """Turn off the heater."""
        await self.async_set_operation_mode(self.STATE_OFF)

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)

        # PATCH 0.5: `if not temperature` also rejected 0; be explicit.
        if temperature is None:
            return

        ok = await self._cl.setBoilerTemp(self._wifi_box, temperature)
        if not ok:
            raise HomeAssistantError("Failed to set DHW temperature on BeSMART cloud")
        await self.coordinator.async_request_refresh()

    async def async_set_operation_mode(self, mode):
        """Set work mode (gas / off)."""
        if mode == self.STATE_OFF:
            # Read thermostats from the coordinator instead of an extra
            # devices() API call.
            thermostats = (self.coordinator.data or {}).get(self._wifi_box, {}).get("thermostats", {})
            self._previous_climate_active = any(
                x.get("mode") not in ("5", "4", 5, 4) for x in thermostats.values()
            )
            ok = await self._cl.setBoilerMode(self._wifi_box, "2")
        elif self._previous_climate_active:
            ok = await self._cl.setBoilerMode(self._wifi_box, "0")
        else:
            ok = await self._cl.setBoilerMode(self._wifi_box, "1")
        if not ok:
            raise HomeAssistantError("Failed to set boiler mode on BeSMART cloud")
        _LOGGER.debug("Set operation mode=%s", str(mode))
        await self.coordinator.async_request_refresh()
