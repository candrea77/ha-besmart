"""Config flow for BeSMART.

PATCH 0.7 (security):
- credentials are stored in entry.data (not options) and validated with a
  real login before saving (user, reauth and reconfigure steps);
- the options form no longer contains (nor sends to the browser) them;
- unique_id = hash of the cloud user id: the same account cannot be added
  twice and reauth/reconfigure cannot switch to another account;
- error messages are fixed translation keys: the original exception (whose
  text may embed the login URL with the credentials) is never shown.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.const import (
    CONF_NAME,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_MODE,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import selector
from homeassistant.components.climate.const import HVACMode

from .api import BesmartClient, BesmartTLSError
from .const import (
    DOMAIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    SCAN_INTERVAL_STEP,
    account_unique_id,
)

_LOGGER = logging.getLogger(__name__)

CREDENTIALS_SCHEMA = {
    vol.Required(CONF_USERNAME): selector.TextSelector(),
    vol.Required(CONF_PASSWORD): selector.TextSelector(
        {"type": selector.TextSelectorType.PASSWORD}
    ),
}

OPTIONS_SCHEMA = {
    vol.Required(CONF_NAME): selector.TextSelector(),
    vol.Required(CONF_MODE): selector.SelectSelector({
        "options": [
            { "label": "Heating", "value": HVACMode.HEAT },
            { "label": "Cooling", "value": HVACMode.COOL },
        ],
        "multiple": True,
    }),
    # TLS certificate verification. Default True (secure). Uncheck only if the
    # BeSMART cloud endpoint presents a broken/incomplete certificate chain.
    vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): selector.BooleanSelector(),
    # Polling period in seconds for the data coordinator.
    vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=MIN_SCAN_INTERVAL,
            max=MAX_SCAN_INTERVAL,
            step=SCAN_INTERVAL_STEP,
            unit_of_measurement="s",
            mode=selector.NumberSelectorMode.SLIDER,
        )
    ),
}

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): selector.TextSelector(),
        **CREDENTIALS_SCHEMA,
        **{k: v for k, v in OPTIONS_SCHEMA.items() if k != CONF_NAME},
    }
)

# PATCH 0.5: schema shown during reauthentication (credentials only).
REAUTH_SCHEMA = vol.Schema(CREDENTIALS_SCHEMA)


async def async_validate_login(
    hass: HomeAssistant, username: str, password: str, verify_ssl: bool
) -> tuple[str | None, dict[str, str]]:
    """Log in once; return (unique_id, errors).

    Only fixed error keys are returned; the exception text is not used.
    """
    client = BesmartClient(hass, username, password, verify_ssl)
    try:
        await client.login()
    except ConfigEntryAuthFailed:
        return None, {"base": "invalid_auth"}
    except BesmartTLSError:
        return None, {"base": "tls_error"}
    except Exception as ex:  # pylint: disable=broad-except
        _LOGGER.debug("BeSMART login check failed: %s", type(ex).__name__)
        return None, {"base": "cannot_connect"}
    if not client.user_id:
        return None, {"base": "cannot_connect"}
    return account_unique_id(client.user_id), {}


def _without_password(user_input: dict[str, Any] | None) -> dict[str, Any]:
    """Suggested values for a re-shown form (never echo the password)."""
    return {k: v for k, v in (user_input or {}).items() if k != CONF_PASSWORD}


class ConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BeSMART."""

    # PATCH 0.7: version 2 = credentials in entry.data (see async_migrate_entry).
    VERSION = 2
    # PATCH 0.10: 2.2 = presets/attributes renamed (one-time Repairs notice).
    MINOR_VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return BesmartOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create an entry after a successful login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            unique_id, errors = await async_validate_login(
                self.hass,
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            )
            if not errors:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                    options={
                        k: v
                        for k, v in user_input.items()
                        if k not in (CONF_USERNAME, CONF_PASSWORD)
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, _without_password(user_input)
            ),
            errors=errors,
        )

    # PATCH 0.5: reauth support. The coordinator raises ConfigEntryAuthFailed
    # on error_code "6"; without these steps HA showed the "reauthentication
    # required" repair but clicking it failed because no reauth step existed.

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication request."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials and validate them."""
        entry = self._get_reauth_entry()
        return await self._async_credentials_step(
            "reauth_confirm", entry, user_input, {"name": entry.title}
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """PATCH 0.7: change the credentials of an existing entry."""
        entry = self._get_reconfigure_entry()
        return await self._async_credentials_step(
            "reconfigure", entry, user_input, {"name": entry.title}
        )

    async def _async_credentials_step(
        self,
        step_id: str,
        entry: ConfigEntry,
        user_input: dict[str, Any] | None,
        placeholders: dict[str, str],
    ) -> ConfigFlowResult:
        """Validate new credentials for the same account and save them."""
        errors: dict[str, str] = {}
        if user_input is not None:
            unique_id, errors = await async_validate_login(
                self.hass,
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                entry.options.get(CONF_VERIFY_SSL, True),
            )
            if not errors:
                if entry.unique_id is not None and entry.unique_id != unique_id:
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        suggested = _without_password(user_input) or {
            CONF_USERNAME: entry.data.get(CONF_USERNAME)
        }
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(REAUTH_SCHEMA, suggested),
            description_placeholders=placeholders,
            errors=errors,
        )


class BesmartOptionsFlow(OptionsFlowWithReload):
    """PATCH 0.7: options without credentials (use Reconfigure for those).

    OptionsFlowWithReload reloads the entry when the options change (the
    integration has no update listener, see __init__.py).
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(OPTIONS_SCHEMA), self.config_entry.options
            ),
        )
