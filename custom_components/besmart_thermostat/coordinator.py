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

from .api import BesmartClient
from .const import (
    DOMAIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# PATCH 0.7: minimum seconds between two accepted "Refresh now" presses.
MANUAL_REFRESH_COOLDOWN = 10

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
            # PATCH 0.7: redacted, the message may contain the login URL.
            raise UpdateFailed(f"Login failed: {self.client._redact(str(ex))}") from ex

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
        presses. The modulation is taken from the polled data, not re-read.
        """
        lock = self._technical_locks.setdefault(wifi_box, asyncio.Lock())
        if lock.locked():
            _LOGGER.debug("technical data read already in progress, ignoring press")
            return
        async with lock:
            try:
                values = await self.client.boiler_technical(wifi_box)
            except ConfigEntryAuthFailed:
                raise
            except Exception as ex:  # pylint: disable=broad-except
                raise HomeAssistantError(
                    f"Could not read technical data: {self.client._redact(str(ex))}"
                ) from ex
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
