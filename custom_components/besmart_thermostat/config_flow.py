"""Config flow for BeSMART."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import voluptuous as vol

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import (
    CONF_NAME,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_MODE,
    CONF_VERIFY_SSL,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import selector
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaConfigFlowHandler,
    SchemaFlowFormStep,
)
from homeassistant.components.climate.const import HVACMode

from .api import BesmartClient
from .const import (
    DOMAIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    SCAN_INTERVAL_STEP,
)

OPTIONS_SCHEMA = {
    vol.Required(CONF_NAME): selector.TextSelector(),
    vol.Required(CONF_USERNAME): selector.TextSelector(),
    vol.Required(CONF_PASSWORD): selector.TextSelector({ "type": selector.TextSelectorType.PASSWORD }),
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

CONFIG_SCHEMA = {
    **OPTIONS_SCHEMA,
}

# PATCH 0.5: schema shown during reauthentication (credentials only).
REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): selector.TextSelector(),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            {"type": selector.TextSelectorType.PASSWORD}
        ),
    }
)


CONFIG_FLOW = {
    "user": SchemaFlowFormStep(vol.Schema(CONFIG_SCHEMA)),
}

OPTIONS_FLOW = {
    "init": SchemaFlowFormStep(vol.Schema(OPTIONS_SCHEMA)),
}


class ConfigFlowHandler(SchemaConfigFlowHandler, domain=DOMAIN):
    """Handle a config or options flow."""

    VERSION = 1

    config_flow = CONFIG_FLOW
    options_flow = OPTIONS_FLOW

    def async_config_entry_title(self, options: Mapping[str, Any]) -> str:
        """Return config entry title."""
        return cast(str, options[CONF_NAME])

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
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            client = BesmartClient(
                self.hass,
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                entry.options.get(CONF_VERIFY_SSL, False),
            )
            try:
                await client.login()
            except ConfigEntryAuthFailed:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    options={
                        **entry.options,
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"name": entry.title},
            errors=errors,
        )
