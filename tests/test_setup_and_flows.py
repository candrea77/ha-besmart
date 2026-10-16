"""Setup, migration, config/reauth/reconfigure flows, Repairs and diagnostics.

Runs against a real Home Assistant instance (pytest-homeassistant-custom-
component); the BeSMART cloud is replaced by FakeSession.
"""

import json
from unittest.mock import patch
from urllib.parse import quote

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_MODE,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.besmart_thermostat import api
from custom_components.besmart_thermostat.api import BesmartClient
from custom_components.besmart_thermostat.const import (
    CONF_SCAN_INTERVAL,
    DOMAIN,
    account_unique_id,
)
from custom_components.besmart_thermostat.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import LOGIN_OK, PASSWORD, USER_ID, USERNAME, WIFI_BOX, FakeResponse, FakeSession

SECRETS = (PASSWORD, quote(PASSWORD, safe=""), USERNAME, quote(USERNAME, safe=""), BesmartClient.TOKEN)
HTML_503 = FakeResponse("<html>503 Service Unavailable</html>", status=503)
OPTIONS = {
    CONF_NAME: "Casa",
    CONF_MODE: ["heat"],
    CONF_VERIFY_SSL: True,
    CONF_SCAN_INTERVAL: 300,
}
CLOUD_OK = {
    "users/login_new": LOGIN_OK,
    "Wifi_boxes/data": {
        "status": True,
        "message": {
            "boiler": {"id": "b1"},
            "thermostat": [{"id": "r1", "name": "Living", "unit": "0"}],
        },
    },
    "Boilers/data": {
        "status": True,
        "message": {
            "work_mode": "0",
            "dhw_target_temp": "48",
            "flame_status": "0",
            "unit": "0",
            "wifi_box_id": WIFI_BOX,
            "user_id": USER_ID,
            "note": f"box {WIFI_BOX}",
        },
    },
    "Thermostats/data": {
        "status": True,
        "message": {"mode": "1", "season": "1", "current_temp": "20.5", "unit": "0"},
    },
    "modulation_ratio": {"status": True, "message": {"key": "modulation_ratio", "value": "0.0"}},
    "Boilers/dhw_target_temp": {"error_code": 0, "status": True},
}


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def cloud():
    """Patch the aiohttp session used by BesmartClient with a fake cloud."""
    session = FakeSession(json.loads(json.dumps(CLOUD_OK)))

    def set_route(key, value):
        # The new route goes first (routes match by substring, in order).
        session.routes = {key: value, **{k: v for k, v in session.routes.items() if k != key}}

    session.set_route = set_route
    with patch.object(api, "async_get_clientsession", return_value=session) as factory:
        session.factory = factory
        yield session


def v2_entry(**kwargs):
    return MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="Casa",
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        options=dict(OPTIONS),
        **kwargs,
    )


def assert_no_secrets(text):
    for secret in SECRETS:
        assert secret not in text, secret
    assert USER_ID not in text
    assert WIFI_BOX not in text


# ------------------------------------------------------------- A1 setup


@pytest.mark.parametrize(
    "login_route",
    [HTML_503, aiohttp.ClientConnectionError("reset"), TimeoutError("t")],
)
async def test_setup_retry_message_has_no_secrets(hass, cloud, caplog, login_route):
    cloud.set_route("users/login_new", login_route)
    entry = v2_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.reason == "Cannot connect to the BeSMART cloud"
    assert_no_secrets(caplog.text)


async def test_setup_tls_error_raises_repair(hass, cloud):
    cloud.set_route(
        "users/login_new",
        aiohttp.ClientSSLError(
            aiohttp.client_reqrep.ConnectionKey("h", 443, True, None, None, None, None),
            OSError("certificate verify failed"),
        ),
    )
    entry = v2_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"tls_certificate_error_{entry.entry_id}")
    assert issue is not None and issue.severity == ir.IssueSeverity.ERROR


async def test_setup_ok_sets_hashed_unique_id(hass, cloud, caplog):
    entry = v2_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.unique_id == account_unique_id(USER_ID)
    assert USER_ID not in entry.unique_id
    assert_no_secrets(caplog.text)


async def test_setup_does_not_steal_unique_id_of_duplicate(hass, cloud):
    first = v2_entry(unique_id=account_unique_id(USER_ID))
    first.add_to_hass(hass)
    duplicate = v2_entry()
    duplicate.add_to_hass(hass)
    assert await hass.config_entries.async_setup(duplicate.entry_id)
    assert duplicate.unique_id is None


async def test_devices_failure_message_masks_box(hass, cloud):
    cloud.set_route("Wifi_boxes/data", HTML_503)
    entry = v2_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert WIFI_BOX not in entry.reason and "***0456" in entry.reason


# -------------------------------------------------------- A3 + M3 migration


async def test_migration_v1_legacy_entry(hass, cloud):
    """Credentials -> data; missing verify_ssl -> True; unique_id at setup."""
    legacy = {k: v for k, v in OPTIONS.items() if k != CONF_VERIFY_SSL}
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        title="Casa",
        data={},
        options={**legacy, CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 2
    assert entry.data == {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    assert CONF_USERNAME not in entry.options and CONF_PASSWORD not in entry.options
    assert entry.options[CONF_VERIFY_SSL] is True
    assert entry.unique_id == account_unique_id(USER_ID)
    assert not ir.async_get(hass).async_get_issue(DOMAIN, f"verify_ssl_disabled_{entry.entry_id}")


async def test_migration_keeps_explicit_verify_ssl_false_with_repair(hass, cloud):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        title="Casa",
        data={},
        options={**OPTIONS, CONF_VERIFY_SSL: False, CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.version == 2
    assert entry.options[CONF_VERIFY_SSL] is False
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"verify_ssl_disabled_{entry.entry_id}")
    assert issue is not None and issue.severity == ir.IssueSeverity.WARNING


async def test_migration_does_not_set_unique_id_if_setup_fails(hass, cloud):
    cloud.set_route("users/login_new", HTML_503)
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        title="Casa",
        data={},
        options={**OPTIONS, CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.version == 2
    assert entry.unique_id is None


async def test_v2_entry_without_verify_ssl_verifies(hass, cloud):
    entry = v2_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={k: v for k, v in OPTIONS.items() if k != CONF_VERIFY_SSL}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert cloud.factory.call_args.kwargs["verify_ssl"] is True


async def test_future_version_is_refused(hass, cloud):
    entry = MockConfigEntry(domain=DOMAIN, version=3, title="Casa", data={}, options=OPTIONS)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


# ------------------------------------------------------------ config flow

USER_INPUT = {
    CONF_NAME: "Casa",
    CONF_USERNAME: USERNAME,
    CONF_PASSWORD: PASSWORD,
    CONF_MODE: ["heat"],
    CONF_VERIFY_SSL: True,
    CONF_SCAN_INTERVAL: 300,
}


async def test_user_flow_creates_entry_with_credentials_in_data(hass, cloud):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.besmart_thermostat.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.version == 2
    assert entry.data == {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    assert CONF_PASSWORD not in entry.options and CONF_USERNAME not in entry.options
    assert entry.unique_id == account_unique_id(USER_ID)


async def test_user_flow_rejects_same_account_twice(hass, cloud):
    v2_entry(unique_id=account_unique_id(USER_ID)).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("login_route", "error"),
    [
        ({"error_code": 6, "status": False}, "invalid_auth"),
        (HTML_503, "cannot_connect"),
        (aiohttp.ClientConnectionError("reset"), "cannot_connect"),
        (
            aiohttp.ClientSSLError(
                aiohttp.client_reqrep.ConnectionKey("h", 443, True, None, None, None, None),
                OSError("certificate verify failed"),
            ),
            "tls_error",
        ),
    ],
)
async def test_user_flow_errors_have_no_exception_text(hass, cloud, caplog, login_route, error):
    cloud.set_route("users/login_new", login_route)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert_no_secrets(json.dumps(result, default=str))
    assert_no_secrets(caplog.text)


async def test_user_flow_does_not_echo_password(hass, cloud):
    cloud.set_route("users/login_new", HTML_503)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    schema = result["data_schema"].schema
    suggested = {str(k): (k.description or {}).get("suggested_value") for k in schema}
    assert suggested[CONF_PASSWORD] is None
    assert suggested[CONF_USERNAME] == USERNAME


# ------------------------------------------------------- reauth / reconfigure


async def _start(hass, entry, source):
    if source == "reauth":
        return await entry.start_reauth_flow(hass)
    return await entry.start_reconfigure_flow(hass)


@pytest.mark.parametrize("source", ["reauth", "reconfigure"])
async def test_credentials_update_success(hass, cloud, source):
    entry = v2_entry(unique_id=account_unique_id(USER_ID))
    entry.add_to_hass(hass)
    result = await _start(hass, entry, source)
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.besmart_thermostat.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: "new pass"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == ("reauth_successful" if source == "reauth" else "reconfigure_successful")
    assert entry.data[CONF_PASSWORD] == "new pass"
    assert CONF_PASSWORD not in entry.options


@pytest.mark.parametrize("source", ["reauth", "reconfigure"])
async def test_credentials_update_wrong_account(hass, cloud, source):
    entry = v2_entry(unique_id=account_unique_id("someone-else"))
    entry.add_to_hass(hass)
    result = await _start(hass, entry, source)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "wrong_account"
    assert entry.data[CONF_USERNAME] == USERNAME


@pytest.mark.parametrize("source", ["reauth", "reconfigure"])
@pytest.mark.parametrize(
    ("login_route", "error"),
    [({"error_code": 6, "status": False}, "invalid_auth"), (HTML_503, "cannot_connect")],
)
async def test_credentials_update_errors_have_no_exception_text(
    hass, cloud, caplog, source, login_route, error
):
    entry = v2_entry(unique_id=account_unique_id(USER_ID))
    entry.add_to_hass(hass)
    cloud.set_route("users/login_new", login_route)
    result = await _start(hass, entry, source)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert_no_secrets(json.dumps(result, default=str))
    assert_no_secrets(caplog.text)


# ---------------------------------------------------------------- options


async def test_options_flow_has_no_credentials(hass, cloud):
    entry = v2_entry(unique_id=account_unique_id(USER_ID))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    fields = {str(k) for k in result["data_schema"].schema}
    assert CONF_USERNAME not in fields and CONF_PASSWORD not in fields
    assert_no_secrets(json.dumps(result, default=str))
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**OPTIONS, CONF_SCAN_INTERVAL: 240}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_SCAN_INTERVAL] == 240
    assert entry.data[CONF_PASSWORD] == PASSWORD


# ------------------------------------------------- M1 device + diagnostics


async def test_device_has_no_wifi_box_model_id(hass, cloud):
    entry = v2_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.model == "BeSMART WiFi box"
    assert device.model_id is None


async def test_device_model_id_from_old_version_is_cleared(hass, cloud):
    entry = v2_entry()
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        model="BeSMART",
        model_id=WIFI_BOX,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.model_id is None


async def test_diagnostics_hide_ids_and_credentials(hass, cloud):
    entry = v2_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    coordinator = entry.runtime_data
    coordinator.technical[WIFI_BOX] = {
        "values": {"system_pressure": "1.4"},
        "modulation": "0.0",
        "unsupported": [],
        "last_read": __import__("homeassistant.util.dt", fromlist=["utcnow"]).utcnow(),
    }
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diag, default=str)
    assert_no_secrets(text)
    assert diag["wifi_boxes"] == ["box_1"]
    assert "box_1" in diag["coordinator_data"] and "box_1" in diag["technical_data"]
    assert diag["coordinator_data"]["box_1"]["boiler"]["dhw_target_temp"] == "48"


# ------------------------------------------------------- M4 entity layer


async def test_water_heater_out_of_range_is_rejected(hass, cloud):
    entry = v2_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = next(
        s.entity_id for s in hass.states.async_all("water_heater")
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "water_heater", "set_temperature", {"entity_id": entity_id, "temperature": 75}, blocking=True
        )
    assert cloud.puts == []
    await hass.services.async_call(
        "water_heater", "set_temperature", {"entity_id": entity_id, "temperature": 50}, blocking=True
    )
    assert cloud.puts[-1][1]["temp"] == 50


# ------------------------------------------- migration from coder89/ha-besmart


async def test_migration_from_coder89_entry(hass, cloud, caplog):
    """Entry created by coder89/ha-besmart (same domain, last version d88fdd2).

    Its SchemaConfigFlowHandler (VERSION 1) stored everything in options:
    name, username, password, mode; no verify_ssl, no scan_interval, empty
    data. Climate/water heater unique_ids and the device identifier are the
    same as ours, so those entities keep their entity_id and history.
    """
    from homeassistant.helpers import entity_registry as er

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        title="Casa",
        data={},
        options={
            CONF_NAME: "Casa",
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
            CONF_MODE: ["heat"],
        },
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    climate = registry.async_get_or_create(
        "climate", DOMAIN, f"{entry.entry_id}:r1",
        suggested_object_id="living_thermostat", config_entry=entry,
    )
    heater = registry.async_get_or_create(
        "water_heater", DOMAIN, f"{entry.entry_id}:{WIFI_BOX}:water_heater",
        suggested_object_id="water_heater", config_entry=entry,
    )
    old_battery = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}:r1_battery",
        suggested_object_id="living_thermostat_battery", config_entry=entry,
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 2
    assert entry.data == {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    assert entry.options == {CONF_NAME: "Casa", CONF_MODE: ["heat"], CONF_VERIFY_SSL: True}
    assert entry.unique_id == account_unique_id(USER_ID)
    assert cloud.factory.call_args.kwargs["verify_ssl"] is True

    # Same entity_ids, now provided by this integration.
    assert registry.async_get_entity_id("climate", DOMAIN, f"{entry.entry_id}:r1") == climate.entity_id
    assert hass.states.get(climate.entity_id) is not None
    assert registry.async_get_entity_id(
        "water_heater", DOMAIN, f"{entry.entry_id}:{WIFI_BOX}:water_heater"
    ) == heater.entity_id
    assert hass.states.get(heater.entity_id) is not None
    # coder89's percentage battery sensor has no counterpart: it is left
    # orphaned (documented in the README) and the enum sensor is created.
    assert hass.states.get(old_battery.entity_id) is None or (
        hass.states.get(old_battery.entity_id).state == "unavailable"
    )
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}:r1_battery_status")
    # Default update interval applied (coder89 had no option).
    assert entry.runtime_data.update_interval.total_seconds() == 300
    assert_no_secrets(caplog.text)


async def test_diagnostics_mask_thermostat_ids(hass, cloud):
    cloud.set_route(
        "Wifi_boxes/data",
        {
            "status": True,
            "message": {
                "boiler": {"id": "b1"},
                "thermostat": [{"id": "029410DD", "name": "Living", "unit": "0"}],
            },
        },
    )
    entry = v2_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diag, default=str)
    assert "029410DD" not in text
    assert "***10DD" in diag["coordinator_data"]["box_1"]["thermostats"]


async def test_options_change_reloads_entry_once(hass, cloud):
    """b8: the reload comes from OptionsFlowWithReload (no update listener)."""
    entry = v2_entry(unique_id=account_unique_id(USER_ID))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not entry.update_listeners
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            result["flow_id"], {**OPTIONS, CONF_SCAN_INTERVAL: 240}
        )
    reload.assert_called_once_with(entry.entry_id)


@pytest.mark.parametrize("source", ["reauth", "reconfigure"])
async def test_credentials_update_on_loaded_entry_reloads_without_warning(hass, cloud, caplog, source):
    """b8 regression: Reconfigure on a loaded entry logged the frame
    deprecation "has an update listener and should use it for scheduling a
    reload" (breaks in HA 2026.12). The autouse fixture fails on it."""
    import asyncio

    entry = v2_entry(unique_id=account_unique_id(USER_ID))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # In tests HA runs tasks eagerly: an update listener would reload (and
    # unload, removing itself) synchronously, before HA checks
    # entry.update_listeners, hiding the warning. On a real installation the
    # reload suspends first; reproduce that ordering.
    original_reload = hass.config_entries.async_reload

    async def suspending_reload(entry_id):
        await asyncio.sleep(0)
        return await original_reload(entry_id)

    with patch.object(hass.config_entries, "async_reload", suspending_reload):
        result = await _start(hass, entry, source)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: "new pass"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF_PASSWORD] == "new pass"
    assert entry.state is ConfigEntryState.LOADED
