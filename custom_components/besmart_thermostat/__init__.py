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
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceEntry

from .const import (
    DOMAIN,
    PLATFORMS,
    ISSUE_TLS_CERTIFICATE,
    ISSUE_VERIFY_SSL_DISABLED,
    account_unique_id,
)
from .device import BesmartInterfaceDevice
from .api import BesmartClient, BesmartError, BesmartTLSError
from .coordinator import BesmartDataUpdateCoordinator

# runtime_data holds the coordinator, which owns both the API client (for
# write commands) and the interface devices (topology).
# PATCH 0.5: no more custom attributes on ConfigEntry (deprecated by HA).
type BesmartConfigEntry = ConfigEntry[BesmartDataUpdateCoordinator]

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries.

    PATCH 0.7 (security), version 1 -> 2 (irreversible):
    - credentials move from entry.options to entry.data, so the options form
      no longer holds (and sends to the browser) the password;
    - verify_ssl missing (legacy entries, which defaulted to no verification)
      becomes True. An explicit False chosen by the user is kept, with a
      Repair warning raised at setup.
    The unique_id is not set here but at the first successful setup.
    """
    if entry.version > 2:
        # Downgrade from a future version: refuse.
        return False

    if entry.version == 1:
        options = dict(entry.options)
        data = {
            **entry.data,
            CONF_USERNAME: options.pop(CONF_USERNAME, entry.data.get(CONF_USERNAME)),
            CONF_PASSWORD: options.pop(CONF_PASSWORD, entry.data.get(CONF_PASSWORD)),
        }
        options.setdefault(CONF_VERIFY_SSL, True)
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, version=2
        )
        _LOGGER.info("Migrated BeSMART config entry to version 2")

    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BesmartConfigEntry,
) -> bool:
    """Set up besmart_thermostat from a config entry."""

    # 1. Create API instance.
    # PATCH 0.7: credentials live in entry.data; TLS verification defaults to
    # on (legacy entries were migrated, see async_migrate_entry).
    verify_ssl = entry.options.get(CONF_VERIFY_SSL, True)
    if verify_ssl:
        ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_VERIFY_SSL_DISABLED}_{entry.entry_id}")
    else:
        # PATCH 0.5/0.7: make the insecure mode visible (log + Repairs).
        _LOGGER.warning(
            "TLS certificate verification for the BeSMART cloud is DISABLED. "
            "Enable 'Verify TLS certificate' in the integration options unless "
            "login fails because of a broken certificate chain."
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{ISSUE_VERIFY_SSL_DISABLED}_{entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_VERIFY_SSL_DISABLED,
            translation_placeholders={"name": entry.title},
        )
    client = BesmartClient(
        hass,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        verify_ssl,
    )

    # 2. Validate the API connection (and authentication).
    # PATCH 0.7 (security): never chain the original exception into
    # ConfigEntryNotReady: HA shows str(__cause__) in the UI, and aiohttp
    # messages embed the login URL with username and password.
    tls_issue_id = f"{ISSUE_TLS_CERTIFICATE}_{entry.entry_id}"
    try:
        wifi_boxes = await client.login()
    except ConfigEntryAuthFailed:
        raise
    except BesmartTLSError:
        ir.async_create_issue(
            hass,
            DOMAIN,
            tls_issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_TLS_CERTIFICATE,
            translation_placeholders={"name": entry.title},
        )
        raise ConfigEntryNotReady(
            "TLS certificate verification of the BeSMART cloud failed"
        ) from None
    except BesmartError:
        raise ConfigEntryNotReady("Cannot connect to the BeSMART cloud") from None
    ir.async_delete_issue(hass, DOMAIN, tls_issue_id)

    # PATCH 0.7: unique_id (hash of the account id) set at the first
    # successful setup after the migration; never overwrite a duplicate.
    if entry.unique_id is None and client.user_id:
        unique_id = account_unique_id(client.user_id)
        if hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, unique_id) is None:
            hass.config_entries.async_update_entry(entry, unique_id=unique_id)
        else:
            _LOGGER.warning(
                "Another BeSMART entry already uses this account; "
                "consider removing the duplicate"
            )

    # 3. Register BeSMART Controller devices for all wifi boxes (topology).
    interface_devices = []
    for wifi_box in wifi_boxes:
        devices = await client.devices(wifi_box)
        if devices is None:
            raise ConfigEntryNotReady(
                f"No data for wifi box {client.mask_wifi_box(wifi_box)}"
            )
        interface_devices.append(BesmartInterfaceDevice(hass, entry, wifi_box, devices))

    # 4. Create the coordinator (owns client + topology) and do the first
    #    refresh before adding entities.
    coordinator = BesmartDataUpdateCoordinator(hass, entry, client, interface_devices)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # PATCH 0.7 (b8): no update listener. Reloads are scheduled by the flows:
    # OptionsFlowWithReload for options, async_update_reload_and_abort for
    # reauth/reconfigure (an update listener made HA log a deprecation that
    # breaks in 2026.12, and caused a double reload after reauth).

    return True


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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """PATCH 0.7: drop the Repairs raised for a removed entry.

    The credentials are deleted by Home Assistant together with the entry.
    """
    for issue in (ISSUE_TLS_CERTIFICATE, ISSUE_VERIFY_SSL_DISABLED):
        ir.async_delete_issue(hass, DOMAIN, f"{issue}_{entry.entry_id}")
