"""Handle BeSMART Devices."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .models import Devices


class BesmartInterfaceDevice:
    """Class for BeSMART WiFi Box handling."""

    # PATCH 0.5: wifi_box is a plain string id (the old WifiBox TypedDict hint
    # was wrong: the code always passes the id string, not a dict).
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, wifi_box: str, devices: Devices) -> None:
        """Initialize interface device class."""
        device_registry = dr.async_get(hass)

        device_id = (DOMAIN, entry.entry_id)
        self.wifi_box = wifi_box
        self.boiler = devices["boiler"]
        self.thermostats = devices["thermostats"]
        self.device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={device_id},
            manufacturer="Riello S.p.a.",
            name=entry.options[CONF_NAME],
            # PATCH 0.7 (security): the wifi box id acts as a credential, so
            # it is no longer shown as model_id (None also clears the value
            # stored by older versions). It stays only as internal data.
            model="BeSMART WiFi box",
            model_id=None,
        )
        self.device_info = DeviceInfo(identifiers={device_id})

        # PATCH 0.8 (b2): one device per thermostat, named after the room,
        # linked to the WiFi box device. Identifier = entry id + cloud
        # thermostat id (stable when the room is renamed in the Riello app);
        # the id itself is never shown in the device info.
        self._thermostat_device_info: dict[str, DeviceInfo] = {}
        for thermostat in self.thermostats:
            room_id = thermostat.get("id")
            identifier = (DOMAIN, f"{entry.entry_id}:{room_id}")
            thermostat_device = device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={identifier},
                manufacturer="Riello S.p.a.",
                name=str(thermostat.get("name") or "Thermostat"),
                model="BeSMART thermostat",
            )
            # via_device_id through async_update_device: async_get_or_create
            # deprecates via_device in recent versions but HA 2026.7 has no
            # via_device_id there.
            if thermostat_device.via_device_id != self.device.id:
                device_registry.async_update_device(
                    thermostat_device.id, via_device_id=self.device.id
                )
            self._thermostat_device_info[room_id] = DeviceInfo(identifiers={identifier})

    def thermostat_device_info(self, room_id) -> DeviceInfo:
        """PATCH 0.8 (b2): device of a thermostat (see __init__)."""
        return self._thermostat_device_info[room_id]
