"""PATCH 0.9: climatic curve chart per thermostat (SVG image, read only).

Draws the curves of the Riello manual with the climatic curve, max/min flow
temperature and room sensor influence of the thermostat, the working point
at the outdoor probe temperature and the requested flow temperature. Only
data already polled by the coordinator: no extra cloud calls, no writes.

The image is redrawn only when the chart would visibly change (see
curve_chart.needs_redraw); the state (image_last_updated) and the attributes
are written only then, so they always describe the image shown.
"""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE, EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import curve_chart
from .api import (
    mask_id,
    parse_value,
    valid_heating_set_points,
    valid_sensor_influence,
)
from .coordinator import BesmartDataUpdateCoordinator, BoilerLink
from .entity import thermostat_setting_entities

# Fields of the polled thermostat data drawn on the chart.
_SETTINGS = ("climatic_curve", "max_heating_set_point", "min_heating_set_point", "sensor_influence")


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the image platform."""
    entities = thermostat_setting_entities(
        config_entry.runtime_data, config_entry, (ClimaticCurveChart,)
    )
    if entities:
        async_add_entities(entities)


def _celsius(data: dict | None) -> bool:
    """Same rule as the other entities: unit "0" (or missing) = Celsius."""
    return str((data or {}).get("unit", "0")) == "0"


def _plausible_flow(value) -> float | None:
    """A flow temperature the boiler could run (not the 5 °C "no request")."""
    flow = parse_value(value)
    if flow is None or not curve_chart.PLAUSIBLE_FLOW[0] <= flow <= curve_chart.PLAUSIBLE_FLOW[1]:
        return None
    return flow


class ClimaticCurveChart(CoordinatorEntity[BesmartDataUpdateCoordinator], ImageEntity):
    """Climatic curve chart of a thermostat (SVG)."""

    _attr_has_entity_name = True
    _attr_translation_key = "climatic_curve_chart"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_content_type = "image/svg+xml"
    _unrecorded_attributes = frozenset(
        {
            "climatic_curve",
            "max_flow_temperature",
            "min_flow_temperature",
            "sensor_influence",
            "outdoor_temperature",
            "curve_flow_temperature",
            "limited_flow_temperature",
            "requested_flow_temperature",
            "requested_flow_source",
            "status",
            # PATCH 0.9 (b2): values held at the edge of the chart.
            "outdoor_off_scale",
            "requested_off_scale",
        }
    )

    def __init__(self, coordinator, config_entry, wifi_box, room_id, room_name, device_info):
        """Initialize the chart and draw the first image."""
        super().__init__(coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self._wifi_box = wifi_box
        self._room_id = room_id
        self._room_name = room_name
        # unique_id = <entry_id>:<thermostat id>_climatic_curve_chart, like
        # the settings entities of 0.8.0.
        self._attr_unique_id = f"{config_entry.entry_id}:{room_id}_climatic_curve_chart"
        self._attr_device_info = device_info
        # Last valid settings, for partial reads the coordinator could not fill.
        self._last_valid: dict[str, float | None] = dict.fromkeys(_SETTINGS)
        self._drawn: curve_chart.ChartValues | None = None
        self._svg = b""
        self._attr_extra_state_attributes = {}
        self._written_available: bool | None = None
        self._redraw()

    async def async_added_to_hass(self) -> None:
        """Also redraw when the Home Assistant language changes."""
        await super().async_added_to_hass()
        self._written_available = self.available
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, self._handle_config_update)
        )

    @property
    def available(self) -> bool:
        """Thermostat and boiler data present (the boiler may be offline)."""
        return (
            super().available
            and self.coordinator.thermostat_data(self._wifi_box, self._room_id) is not None
            and self.coordinator.boiler_data(self._wifi_box) is not None
        )

    async def async_image(self) -> bytes | None:
        """Return the last drawn SVG."""
        return self._svg

    # ------------------------------------------------------------ values

    def _settings(self, thermostat: dict) -> dict[str, float | None]:
        """Curve, max, min and influence; last valid values on partial reads.

        The coordinator already replaces zeros, missing and out-of-range set
        points with the last valid ones; a field it could not fill keeps the
        value of the last image (or none at all: never a line at 0).
        """
        curve = parse_value(thermostat.get("climatic_curve"))
        if curve is not None and curve_chart.CURVE_RANGE[0] <= curve <= curve_chart.CURVE_RANGE[1]:
            self._last_valid["climatic_curve"] = curve
        set_min = thermostat.get("min_heating_set_point")
        set_max = thermostat.get("max_heating_set_point")
        # A min > max pair (or one out of range) counts as a partial read.
        if valid_heating_set_points(set_min, set_max):
            self._last_valid["min_heating_set_point"] = parse_value(set_min)
            self._last_valid["max_heating_set_point"] = parse_value(set_max)
        influence = thermostat.get("sensor_influence")
        if valid_sensor_influence(influence):
            self._last_valid["sensor_influence"] = parse_value(influence)
        return self._last_valid

    def _requested_flow(self, thermostat: dict, boiler: dict) -> tuple[float | None, str | None]:
        """Requested flow temperature as read, never recalculated.

        Only while the thermostat calls for heat: its OpenTherm set point if
        plausible (at rest it reports 5 °C), else the boiler target.
        """
        if str(thermostat.get("heating_status", "")).strip() != "1":
            return None, None
        flow = _plausible_flow(thermostat.get("central_heating_thermostat_OT_set_point"))
        if flow is not None:
            return flow, curve_chart.SOURCE_THERMOSTAT
        flow = _plausible_flow(boiler.get("heating_target_temp"))
        if flow is not None:
            return flow, curve_chart.SOURCE_BOILER
        return None, None

    def _boiler_offline(self) -> bool:
        """PATCH 0.10: same confirmed state as the boiler sensors.

        Without boiler.online in the reads of the box, as before: offline
        unless this thermostat's read (or Wifi_boxes/data) says "1".
        """
        co = self.coordinator
        if co.boiler_link_raw(self._wifi_box) is BoilerLink.UNKNOWN:
            return co.boiler_link(self._wifi_box, self._room_id) is not BoilerLink.ONLINE
        return co.boiler_link(self._wifi_box) is BoilerLink.OFFLINE

    def _values(self) -> curve_chart.ChartValues:
        thermostat = self.coordinator.thermostat_data(self._wifi_box, self._room_id) or {}
        boiler = self.coordinator.boiler_data(self._wifi_box) or {}
        settings = self._settings(thermostat)
        common = {
            "curve": settings["climatic_curve"],
            "max_flow": settings["max_heating_set_point"],
            "min_flow": settings["min_heating_set_point"],
            "influence": settings["sensor_influence"],
            "language": curve_chart.chart_language(self.coordinator.hass.config.language),
        }
        if not (_celsius(thermostat) and _celsius(boiler)):
            return curve_chart.ChartValues(**common, status=curve_chart.STATUS_UNSUPPORTED_UNIT)
        if self._boiler_offline():
            return curve_chart.ChartValues(**common, status=curve_chart.STATUS_BOILER_OFFLINE)
        requested, source = self._requested_flow(thermostat, boiler)
        return curve_chart.ChartValues(
            **common,
            outdoor=parse_value(boiler.get("outdoor_probe_temp")),
            requested=requested,
            requested_source=source,
            heat_request=str(thermostat.get("heating_status", "")).strip() == "1",
        )

    # ------------------------------------------------------------ drawing

    def _redraw(self) -> bool:
        """Draw a new image if the chart changed visibly; True if drawn."""
        values = self._values()
        if not curve_chart.needs_redraw(self._drawn, values):
            return False
        warn_key = mask_id(self._room_id)
        self._svg = curve_chart.render_svg(values, warn_key).encode()
        self._drawn = values
        self._attr_image_last_updated = dt_util.utcnow()
        flow = None
        if values.curve is not None and values.outdoor is not None:
            flow = curve_chart.curve_flow(values.curve, values.outdoor, warn_key)
        limited = curve_chart.limit_flow(flow, values.min_flow, values.max_flow)
        self._attr_extra_state_attributes = {
            "climatic_curve": values.curve,
            "max_flow_temperature": values.max_flow,
            "min_flow_temperature": values.min_flow,
            "sensor_influence": values.influence,
            "outdoor_temperature": values.outdoor,
            "curve_flow_temperature": None if flow is None else round(flow, 1),
            "limited_flow_temperature": None if limited is None else round(limited, 1),
            "requested_flow_temperature": values.requested,
            "requested_flow_source": values.requested_source,
            "status": values.status,
            "outdoor_off_scale": curve_chart.outdoor_off_scale(values.outdoor),
            "requested_off_scale": curve_chart.requested_off_scale(values),
        }
        return True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write the state only for a new image or a change of availability."""
        drawn = self._redraw()
        available = self.available
        if drawn or available != self._written_available:
            self._written_available = available
            self.async_write_ha_state()

    @callback
    def _handle_config_update(self, _event: Event) -> None:
        """The language is part of the chart."""
        self._handle_coordinator_update()
