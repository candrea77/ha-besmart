"""DataUpdateCoordinator for the BeSMART integration.

A single coordinator per config entry fetches, once per cycle, the boiler data
and every thermostat's data for all WiFi boxes. All entities (climate,
water_heater, sensor) then read from ``coordinator.data`` instead of polling the
cloud API independently every 30s.

data layout:
    {
        <wifi_box_id>: {
            "boiler": { ... } | None,
            "thermostats": { <room_id>: { ... }, ... },
        },
        ...
    }
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BesmartClient
from .const import (
    DOMAIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

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
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
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
            raise UpdateFailed(f"Login failed: {ex}") from ex

        previous: BesmartData = self.data or {}
        result: BesmartData = {}

        # Topology (which wifi boxes / thermostats exist) is static, taken from
        # the interface devices built at setup time.
        for device in getattr(self.config_entry, "interface_devices", []):
            wifi_box = device.wifi_box
            prev_box = previous.get(wifi_box, {})

            boiler = await self.client.boiler(wifi_box)
            if boiler is None:
                # Transient error: keep the previous snapshot (no flapping).
                boiler = prev_box.get("boiler")

            thermostats: dict[str, dict] = {}
            for thermostat in device.thermostats:
                room_id = thermostat.get("id")
                if room_id is None:
                    continue
                data = await self.client.thermostat(wifi_box, room_id)
                if data is None:
                    data = prev_box.get("thermostats", {}).get(room_id)
                if data is not None:
                    thermostats[room_id] = data

            result[wifi_box] = {"boiler": boiler, "thermostats": thermostats}

        # If nothing at all came back (and we have no previous data to fall back
        # on), mark the update as failed so entities go unavailable.
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
