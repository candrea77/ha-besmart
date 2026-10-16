"""DataUpdateCoordinator for the BeSMART integration.

A single coordinator per config entry fetches, once per cycle, the boiler data
and every thermostat's data for all WiFi boxes. All entities (climate,
water_heater, sensor) then read from ``coordinator.data`` instead of polling the
cloud API independently.

PATCH 0.5:
- The topology (interface devices) now lives on the coordinator itself instead
  of being stored as a custom attribute on ConfigEntry (which Home Assistant is
  deprecating / blocking).
- Boiler + thermostat requests for each wifi box are fetched concurrently with
  asyncio.gather to shorten the update cycle.

PATCH 0.7:
- Boilers/modulation_ratio is polled with the boiler data (boxes with a boiler
  only). It is optional: a failure yields None for that cycle (no fallback to
  the previous value, since modulation changes quickly) and never fails the
  update on its own.
- Technical data is NOT polled: the single-value technical endpoints are read
  on demand (button entity) via async_read_technical() and kept in
  ``self.technical``.
- async_manual_refresh() backs the "Refresh now" button: one refresh at a time
  and presses within MANUAL_REFRESH_COOLDOWN seconds are ignored.

data layout:
    {
        <wifi_box_id>: {
            "boiler": { ... } | None,
            "modulation": "<raw value>" | None,   # PATCH 0.7
            "thermostats": { <room_id>: { ... }, ... },
        },
        ...
    }
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    BesmartClient,
    mask_id,
    parse_value,
    valid_climatic_curve,
    valid_heating_set_points,
    valid_max_heating_set_point,
    valid_min_heating_set_point,
    valid_sensor_influence,
)
from .const import (
    DOMAIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# PATCH 0.7 (b5): thermostat set points that a partial cloud read reports
# as 0 (together, often, with cmd_stand_status "1"). A 0 / invalid value is
# replaced by the last valid one of that thermostat.
THERMOSTAT_SETPOINT_FIELDS = (
    "comfort_temp",
    "economy_temp",
    "frost_temp",
    "min_heating_set_point",
    "max_heating_set_point",
    "climatic_curve",
)
# 0 is a legitimate value for this one: treated as stale only when the read
# is already partial and cmd_stand_status is "1".
THERMOSTAT_SENSOR_INFLUENCE = "sensor_influence"

# PATCH 0.7: minimum seconds between two accepted "Refresh now" presses.
MANUAL_REFRESH_COOLDOWN = 10
# PATCH 0.7: minimum seconds between two accepted "Read technical data"
# presses (each one sends up to 14 requests).
TECHNICAL_READ_COOLDOWN = 30

type BesmartData = dict[str, dict]


def _resolve_interval(entry: ConfigEntry) -> timedelta:
    """Read scan_interval from options, clamped to the allowed range."""
    try:
        value = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    except (TypeError, ValueError):
        value = DEFAULT_SCAN_INTERVAL
    value = max(MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, value))
    return timedelta(seconds=value)


class BesmartDataUpdateCoordinator(DataUpdateCoordinator[BesmartData]):
    """Coordinate a single round of cloud calls for the whole entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: BesmartClient,
        interface_devices: list,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        # PATCH 0.5: topology owned by the coordinator (see module docstring).
        self.interface_devices = interface_devices
        # PATCH 0.7: on-demand technical data per wifi box:
        # {wifi_box: {"values": {endpoint: raw}, "modulation": raw,
        #             "unsupported": [...], "last_read": datetime}}.
        self.technical: dict[str, dict] = {}
        self._technical_locks: dict[str, asyncio.Lock] = {}
        self._last_technical_read: dict[str, float] = {}
        # PATCH 0.7 (b5): last valid set points per (wifi_box, room_id).
        self._last_valid_setpoints: dict[tuple[str, str], dict] = {}
        # PATCH 0.7: "Refresh now" button state.
        self._manual_refresh_lock = asyncio.Lock()
        self._last_manual_refresh: float | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=_resolve_interval(entry),
        )

    async def _async_update_data(self) -> BesmartData:
        """Fetch boiler + thermostat data for all WiFi boxes in one cycle."""
        # Pre-flight login so auth failures surface as ConfigEntryAuthFailed
        # (-> reauth) instead of being swallowed as None by the resource methods.
        try:
            await self.client.ensure_login()
        except ConfigEntryAuthFailed:
            raise
        except Exception as ex:  # pylint: disable=broad-except
            # PATCH 0.7: BesmartError messages are already redacted; never
            # chain the original exception (HA may show its text).
            raise UpdateFailed(f"Login failed: {self.client._redact(str(ex))}") from None

        previous: BesmartData = self.data or {}
        result: BesmartData = {}

        for device in self.interface_devices:
            wifi_box = device.wifi_box
            prev_box = previous.get(wifi_box, {})

            room_ids = [
                t.get("id") for t in device.thermostats if t.get("id") is not None
            ]

            # PATCH 0.7: modulation only for boxes that have a boiler.
            has_boiler = device.boiler is not None

            # PATCH 0.5: fetch boiler + all thermostats of this box concurrently.
            responses = await asyncio.gather(
                self.client.boiler(wifi_box),
                *[self.client.thermostat(wifi_box, rid) for rid in room_ids],
                *([self.client.boiler_modulation(wifi_box)] if has_boiler else []),
                return_exceptions=True,
            )

            # ConfigEntryAuthFailed must always propagate (-> reauth flow).
            for resp in responses:
                if isinstance(resp, ConfigEntryAuthFailed):
                    raise resp

            boiler = responses[0]
            if isinstance(boiler, Exception) or boiler is None:
                # Transient error: keep the previous snapshot (no flapping).
                boiler = prev_box.get("boiler")

            thermostats: dict[str, dict] = {}
            for room_id, data in zip(room_ids, responses[1 : 1 + len(room_ids)]):
                if isinstance(data, Exception) or data is None:
                    data = prev_box.get("thermostats", {}).get(room_id)
                elif isinstance(data, dict):
                    data = self._sanitize_thermostat(wifi_box, room_id, data)
                if data is not None:
                    thermostats[room_id] = data

            # PATCH 0.7: optional; failure -> None (unknown), no stale value.
            modulation = None
            if has_boiler:
                modulation = responses[-1]
                if isinstance(modulation, Exception):
                    _LOGGER.debug("modulation read failed: %s", type(modulation).__name__)
                    modulation = None

            result[wifi_box] = {
                "boiler": boiler,
                "modulation": modulation,
                "thermostats": thermostats,
            }

        # If nothing at all came back (and we have no previous data to fall back
        # on), mark the update as failed so entities go unavailable.
        # PATCH 0.7: "modulation" deliberately does not count here.
        has_any = any(
            box.get("boiler") is not None or box.get("thermostats")
            for box in result.values()
        )
        if not has_any:
            raise UpdateFailed("No data returned from BeSMART cloud")

        return result

    def _sanitize_thermostat(self, wifi_box: str, room_id: str, data: dict) -> dict:
        """PATCH 0.7 (b5): keep the last valid set points on partial reads.

        The cloud sometimes answers with comfort/economy/frost temperatures,
        min/max heating set point and climatic curve all at 0 while the
        current/target temperatures are valid. A 0, invalid or out-of-range
        value of those fields (limits of the official app, see api.py) is
        replaced by the last valid one (or dropped if there is none yet). cmd_stand_status "1" is only an extra hint: it never
        marks a read as partial on its own.
        """
        last = self._last_valid_setpoints.setdefault((wifi_box, room_id), {})
        # PATCH 0.7 (b6): besides 0, values outside the official app's
        # limits are not valid either (min/max set point, climatic curve,
        # sensor influence; min must not exceed max).
        validators = {
            "min_heating_set_point": valid_min_heating_set_point,
            "max_heating_set_point": valid_max_heating_set_point,
            "climatic_curve": valid_climatic_curve,
        }
        invalid = [
            field
            for field in THERMOSTAT_SETPOINT_FIELDS
            if field in data
            and not (validators.get(field, parse_value))(data[field])
        ]
        set_min, set_max = "min_heating_set_point", "max_heating_set_point"
        if (
            set_min in data and set_max in data
            and set_min not in invalid and set_max not in invalid
            and not valid_heating_set_points(data[set_min], data[set_max])
        ):
            invalid += [set_min, set_max]
        stand_by = str(data.get("cmd_stand_status", "")).strip() == "1"
        influence = data.get(THERMOSTAT_SENSOR_INFLUENCE)
        if THERMOSTAT_SENSOR_INFLUENCE in data and (
            not valid_sensor_influence(influence)
            or (invalid and stand_by and not parse_value(influence))
        ):
            invalid.append(THERMOSTAT_SENSOR_INFLUENCE)

        for field in (*THERMOSTAT_SETPOINT_FIELDS, THERMOSTAT_SENSOR_INFLUENCE):
            if field in data and field not in invalid:
                last[field] = data[field]

        if not invalid:
            return data

        data = dict(data)
        for field in invalid:
            if field in last:
                data[field] = last[field]
            else:
                data.pop(field)
        _LOGGER.debug(
            "partial thermostat data for %s (cmd_stand_status=%s): "
            "kept last valid %s",
            mask_id(room_id),
            data.get("cmd_stand_status"),
            ", ".join(invalid),
        )
        return data

    # --- Convenience accessors used by entities -----------------------------

    def thermostat_data(self, wifi_box: str, room_id: str) -> dict | None:
        """Return the latest data dict for a thermostat, or None."""
        return (self.data or {}).get(wifi_box, {}).get("thermostats", {}).get(room_id)

    def boiler_data(self, wifi_box: str) -> dict | None:
        """Return the latest boiler data dict for a wifi box, or None."""
        return (self.data or {}).get(wifi_box, {}).get("boiler")

    def modulation(self, wifi_box: str):
        """PATCH 0.7: return the latest raw modulation value, or None."""
        return (self.data or {}).get(wifi_box, {}).get("modulation")

    def technical_data(self, wifi_box: str) -> dict | None:
        """PATCH 0.7: return the last on-demand technical reading, or None."""
        return self.technical.get(wifi_box)

    async def async_read_technical(self, wifi_box: str) -> None:
        """PATCH 0.7: read the technical endpoints once (button press).

        Endpoints that fail are stored as missing (None attributes). Raises
        HomeAssistantError (shown in the UI) only when none answered, keeping
        the previous reading untouched. A per-box lock collapses repeated
        presses and presses within TECHNICAL_READ_COOLDOWN seconds are
        ignored. The modulation is taken from the polled data, not re-read.
        """
        lock = self._technical_locks.setdefault(wifi_box, asyncio.Lock())
        now = monotonic()
        last = self._last_technical_read.get(wifi_box)
        if lock.locked() or (last is not None and now - last < TECHNICAL_READ_COOLDOWN):
            _LOGGER.debug("technical data read ignored (in progress or pressed too soon)")
            return
        self._last_technical_read[wifi_box] = now
        async with lock:
            try:
                values = await self.client.boiler_technical(wifi_box)
            except ConfigEntryAuthFailed:
                raise
            except Exception:  # pylint: disable=broad-except
                # PATCH 0.7: fixed message, original exception not chained.
                raise HomeAssistantError(
                    "Could not read technical data from the BeSMART cloud"
                ) from None
            if not values:
                raise HomeAssistantError(
                    "Could not read technical data from the BeSMART cloud"
                )
            self.technical[wifi_box] = {
                "values": values,
                "modulation": self.modulation(wifi_box),
                "unsupported": sorted(self.client.unsupported_endpoints),
                "last_read": dt_util.utcnow(),
            }
        self.async_update_listeners()

    async def async_manual_refresh(self) -> None:
        """PATCH 0.7: immediate refresh of all polled data ("Refresh now").

        Ignored while a manual refresh is running or within
        MANUAL_REFRESH_COOLDOWN seconds of the last accepted press. On failure
        raises HomeAssistantError; entities behave as after a failed poll.
        """
        now = monotonic()
        if self._manual_refresh_lock.locked() or (
            self._last_manual_refresh is not None
            and now - self._last_manual_refresh < MANUAL_REFRESH_COOLDOWN
        ):
            _LOGGER.debug("manual refresh ignored (in progress or pressed too soon)")
            return
        self._last_manual_refresh = now
        async with self._manual_refresh_lock:
            await self.async_refresh()
        if not self.last_update_success:
            raise HomeAssistantError(
                f"BeSMART refresh failed: {self.last_exception or 'no data from the cloud'}"
            )
