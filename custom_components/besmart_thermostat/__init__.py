"""The besmart_thermostat integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceEntry

from .const import PLATFORMS
from .device import BesmartInterfaceDevice
from .api import BesmartClient
from .coordinator import BesmartDataUpdateCoordinator

# runtime_data now holds the coordinator (it exposes .client for write commands).
type BesmartConfigEntry = ConfigEntry[BesmartDataUpdateCoordinator]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BesmartConfigEntry,
) -> bool:
    """Set up besmart_thermostat from a config entry."""

    # 1. Create API instance.
    besmart_config = entry.options
    # Legacy entries created before this option keep the old behaviour (no
    # verification); new entries default to True via the config flow schema.
    verify_ssl = besmart_config.get(CONF_VERIFY_SSL, False)
    client = BesmartClient(
        hass,
        besmart_config[CONF_USERNAME],
        besmart_config[CONF_PASSWORD],
        verify_ssl,
    )

    # 2. Validate the API connection (and authentication).
    try:
        wifi_boxes = await client.login()
    except ConfigEntryAuthFailed:
        raise
    except Exception as ex:
        raise ConfigEntryNotReady from ex

    # 3. Register BeSMART Controller devices for all wifi boxes (topology).
    interface_devices = []
    for wifi_box in wifi_boxes:
        devices = await client.devices(wifi_box)
        if devices is None:
            raise ConfigEntryNotReady(f"No data for wifi box {wifi_box}")
        interface_devices.append(BesmartInterfaceDevice(hass, entry, wifi_box, devices))
    entry.interface_devices = interface_devices

    # 4. Create the coordinator and do the first refresh before adding entities.
    coordinator = BesmartDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_config_entry_update_listener))

    return True


async def async_config_entry_update_listener(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Update listener, called when the config entry options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """Remove a config entry from a device."""
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: BesmartConfigEntry,
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
