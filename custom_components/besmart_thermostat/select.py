"""PATCH 0.8: climatic curve per thermostat (values of the official app)."""

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import CLIMATIC_CURVES
from .entity import BesmartThermostatSettingEntity, thermostat_setting_entities

CLIMATIC_CURVE_OPTIONS = [f"{curve:.1f}" for curve in CLIMATIC_CURVES]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the select platform."""
    entities = thermostat_setting_entities(
        config_entry.runtime_data, config_entry, (ClimaticCurveSelect,)
    )
    if entities:
        async_add_entities(entities)


class ClimaticCurveSelect(BesmartThermostatSettingEntity, SelectEntity):
    """Climatic curve (climatic_curve): 0.2 ... 3.0."""

    FIELD = "climatic_curve"
    _attr_translation_key = "climatic_curve"
    _attr_options = CLIMATIC_CURVE_OPTIONS
    _attr_icon = "mdi:chart-bell-curve-cumulative"

    @property
    def current_option(self) -> str | None:
        value = self._value
        if value is None:
            return None
        return next(
            (option for option in CLIMATIC_CURVE_OPTIONS if abs(float(option) - value) < 1e-6),
            None,
        )

    async def async_select_option(self, option: str) -> None:
        await self._async_write(option)
