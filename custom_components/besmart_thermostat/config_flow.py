"""Config flow for BeSMART."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import voluptuous as vol

from homeassistant.const import (
    CONF_NAME,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_MODE,
    CONF_VERIFY_SSL,
)
from homeassistant.helpers import selector
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaConfigFlowHandler,
    SchemaFlowFormStep,
)
from homeassistant.components.climate.const import HVACMode

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
