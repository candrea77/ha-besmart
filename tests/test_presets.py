"""PATCH 0.10: translated thermostat presets, renamed attributes and the
one-time Repairs notice (candrea77/ha-besmart#4).

Runs against a real Home Assistant instance; the BeSMART cloud is replaced
by FakeSession (see test_setup_and_flows).
"""

import json
from pathlib import Path

import pytest
from homeassistant.components.climate import ATTR_PRESET_MODE, ATTR_PRESET_MODES
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.besmart_thermostat.const import DOMAIN

from .test_setup_and_flows import (  # noqa: F401
    OPTIONS,
    _custom_integrations,
    cloud,
    v2_entry,
)
from .conftest import PASSWORD, USERNAME

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "besmart_thermostat"
PRESETS = ["auto", "manual", "away", "party", "frost_protection"]
ISSUE = "presets_renamed"
MODE_OK = {"error_code": 0, "status": True}


def _set_mode(cloud, mode, **extra):
    cloud.set_route(
        "Thermostats/data",
        {
            "status": True,
            "message": {"mode": str(mode), "season": "1", "current_temp": "20.5", "unit": "0", **extra},
        },
    )


async def _setup(hass, entry=None):
    entry = entry or v2_entry(minor_version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _climate(hass):
    (state,) = hass.states.async_all("climate")
    return state


def _modes_written(cloud):
    return [data["mode"] for url, data in cloud.puts if "Thermostats/mode" in url]


# ------------------------------------------------------------------ read


@pytest.mark.parametrize("mode, preset", list(enumerate(PRESETS)))
async def test_preset_read(hass, cloud, mode, preset):
    _set_mode(cloud, mode)
    await _setup(hass)
    state = _climate(hass)
    assert state.attributes[ATTR_PRESET_MODE] == preset
    assert state.attributes[ATTR_PRESET_MODES] == PRESETS
    assert state.attributes["mode"] == mode  # unchanged numeric attribute


async def test_dhw_is_off_without_presets(hass, cloud):
    _set_mode(cloud, 5)
    await _setup(hass)
    state = _climate(hass)
    assert state.state == "off"
    assert ATTR_PRESET_MODE not in state.attributes
    assert ATTR_PRESET_MODES not in state.attributes
    assert state.attributes["mode"] == 5


async def test_unknown_mode_has_no_preset_and_is_logged_once(hass, cloud, caplog):
    _set_mode(cloud, 9)
    entry = await _setup(hass)
    assert _climate(hass).attributes[ATTR_PRESET_MODE] is None
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _climate(hass).attributes[ATTR_PRESET_MODE] is None
    assert caplog.text.count("Unknown BeSMART mode 9") == 1


# ----------------------------------------------------------------- write


@pytest.mark.parametrize("mode, preset", list(enumerate(PRESETS)))
async def test_preset_write(hass, cloud, mode, preset):
    cloud.set_route("Thermostats/mode", MODE_OK)
    await _setup(hass)
    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": _climate(hass).entity_id, "preset_mode": preset},
        blocking=True,
    )
    assert _modes_written(cloud) == [mode]


async def test_preset_write_rejected_by_cloud(hass, cloud):
    cloud.set_route("Thermostats/mode", {"error_code": 1, "status": False})
    await _setup(hass)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "climate",
            "set_preset_mode",
            {"entity_id": _climate(hass).entity_id, "preset_mode": "away"},
            blocking=True,
        )


@pytest.mark.parametrize("value", ["AUTO", "MANUAL", "ECO", "PARTY", "IDLE", "DHW", "dhw", "eco"])
async def test_old_and_invalid_presets_rejected_by_ha(hass, cloud, value):
    cloud.set_route("Thermostats/mode", MODE_OK)
    await _setup(hass)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "climate",
            "set_preset_mode",
            {"entity_id": _climate(hass).entity_id, "preset_mode": value},
            blocking=True,
        )
    assert err.value.translation_key == "not_valid_preset_mode"
    assert cloud.puts == []


async def test_set_preset_mode_never_falls_back_to_auto(hass, cloud):
    """Called from code with a value outside the table: nothing is sent."""
    cloud.set_route("Thermostats/mode", MODE_OK)
    await _setup(hass)
    entity = hass.data["climate"].get_entity(_climate(hass).entity_id)
    with pytest.raises(ServiceValidationError) as err:
        await entity.async_set_preset_mode("DHW")
    assert err.value.translation_key == "invalid_preset"
    assert cloud.puts == []


@pytest.mark.parametrize(
    "service, data",
    [("turn_off", {}), ("set_hvac_mode", {"hvac_mode": "off"})],
)
async def test_turn_off_writes_dhw(hass, cloud, service, data):
    cloud.set_route("Thermostats/mode", MODE_OK)
    await _setup(hass)
    await hass.services.async_call(
        "climate", service, {"entity_id": _climate(hass).entity_id, **data}, blocking=True
    )
    assert _modes_written(cloud) == [5]


@pytest.mark.parametrize(
    "service, data",
    [("turn_on", {}), ("set_hvac_mode", {"hvac_mode": "heat"})],
)
async def test_turn_on_from_off_writes_auto(hass, cloud, service, data):
    cloud.set_route("Thermostats/mode", MODE_OK)
    _set_mode(cloud, 5)
    await _setup(hass)
    await hass.services.async_call(
        "climate", service, {"entity_id": _climate(hass).entity_id, **data}, blocking=True
    )
    assert _modes_written(cloud) == [0]


# ------------------------------------------------------------ attributes


async def test_renamed_attributes(hass, cloud):
    _set_mode(
        cloud,
        1,
        comfort_temp="21.5",
        central_heating_thermostat_OT_set_point="5.0",
        advance="1",
        holiday_end_time="1728601196",
    )
    await _setup(hass)
    attrs = _climate(hass).attributes
    assert attrs["ot_set_point"] == 5.0
    assert attrs["comfort_temp"] == 21.5
    assert "setpoint_OT" not in attrs
    assert "confort_temp" not in attrs
    assert not [name for name in attrs if "holiday" in name]


# --------------------------------------------------------------- Repairs


def _issue(hass, entry):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE}_{entry.entry_id}")


async def test_update_from_2_1_raises_notice_once(hass, cloud):
    entry = v2_entry()  # MockConfigEntry default: minor_version 1 (0.9.x)
    data, options = dict(entry.data), dict(entry.options)
    await _setup(hass, entry)
    assert (entry.version, entry.minor_version) == (2, 2)
    assert dict(entry.data) == data and dict(entry.options) == options
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.is_fixable and issue.is_persistent
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.translation_key == ISSUE
    assert issue.translation_placeholders == {"name": "Casa"}

    # A reload (restart) does not raise it again; the existing one stays.
    ir.async_get(hass).async_delete(DOMAIN, f"{ISSUE}_{entry.entry_id}")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None


async def test_new_entry_has_no_notice(hass, cloud):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**OPTIONS, "username": USERNAME, "password": PASSWORD},
    )
    await hass.async_block_till_done()
    entry = result["result"]
    assert (entry.version, entry.minor_version) == (2, 2)
    assert _issue(hass, entry) is None


async def test_v1_entry_migrates_to_2_2_with_notice(hass, cloud):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        title="Casa",
        data={},
        options={**OPTIONS, "username": USERNAME, "password": PASSWORD},
    )
    await _setup(hass, entry)
    assert (entry.version, entry.minor_version) == (2, 2)
    assert _issue(hass, entry) is not None


async def test_future_minor_version_is_left_alone(hass, cloud):
    """Downgrade from a future 2.3+: True, nothing changed, no notice."""
    entry = v2_entry(minor_version=3)
    data, options = dict(entry.data), dict(entry.options)
    await _setup(hass, entry)
    assert entry.state.name == "LOADED"
    assert (entry.version, entry.minor_version) == (2, 3)
    assert dict(entry.data) == data and dict(entry.options) == options
    assert _issue(hass, entry) is None


async def test_confirm_flow_closes_notice(hass, cloud, hass_client):
    from homeassistant.components.repairs import DOMAIN as REPAIRS
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, REPAIRS, {})
    entry = await _setup(hass, v2_entry())
    issue_id = f"{ISSUE}_{entry.entry_id}"
    client = await hass_client()
    resp = await client.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    assert resp.status == 200
    flow = await resp.json()
    assert flow["step_id"] == "confirm"
    assert flow["description_placeholders"] == {"name": "Casa"}
    resp = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    assert resp.status == 200
    assert (await resp.json())["type"] == "create_entry"
    assert _issue(hass, entry) is None

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None


async def test_notice_removed_with_entry(hass, cloud):
    entry = await _setup(hass, v2_entry())
    assert _issue(hass, entry) is not None
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None


# ---------------------------------------------------- translations, icons


def _load(name):
    return json.loads((BASE / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json", "translations/it.json"])
def test_preset_and_attribute_translations(name):
    data = _load(name)
    attrs = data["entity"]["climate"]["thermostat"]["state_attributes"]
    # "away" is translated by Home Assistant (climate domain fallback).
    assert set(attrs["preset_mode"]["state"]) == set(PRESETS) - {"away"}
    assert "ot_set_point" in attrs and "comfort_temp" in attrs
    assert "setpoint_OT" not in attrs and "confort_temp" not in attrs
    confirm = data["issues"][ISSUE]["fix_flow"]["step"]["confirm"]
    assert "{name}" in data["issues"][ISSUE]["title"]
    assert "{name}" in confirm["description"]
    for old, new in [("AUTO", "auto"), ("ECO", "away"), ("IDLE", "frost_protection"),
                     ("setpoint_OT", "ot_set_point"), ("confort_temp", "comfort_temp")]:
        assert f"`{old}`" in confirm["description"] and f"`{new}`" in confirm["description"]
    assert "{preset}" in data["exceptions"]["invalid_preset"]["message"]


def test_icons_cover_every_preset():
    icons = _load("icons.json")
    preset = icons["entity"]["climate"]["thermostat"]["state_attributes"]["preset_mode"]
    assert list(preset["state"]) == PRESETS
    assert all(icon.startswith("mdi:") for icon in [preset["default"], *preset["state"].values()])
