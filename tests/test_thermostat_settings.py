"""0.8.0: thermostat settings (climatic curve, flow temperatures, sensor influence).

PUT Thermostats/setting is a read-modify-write of the fresh settings read,
with the same parameters as the official app; the value is verified after
the write. Never hits the real cloud.
"""

import asyncio
import json
import logging

import pytest
from homeassistant.const import CONF_MODE, CONF_PASSWORD, CONF_USERNAME, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.besmart_thermostat import coordinator as coordinator_module
from custom_components.besmart_thermostat import number as number_module
from custom_components.besmart_thermostat.api import (
    BesmartClient,
    BesmartSettingsError,
    BesmartValidationError,
)
from custom_components.besmart_thermostat.const import DOMAIN
from custom_components.besmart_thermostat.coordinator import BoilerLink

from .conftest import PASSWORD, USER_ID, USERNAME, WIFI_BOX
from .test_setup_and_flows import OPTIONS, _custom_integrations, assert_no_secrets, cloud  # noqa: F401

ROOM = "r1"
WRITE_OK = {"error_code": 0, "status": True}
SETTINGS = {
    "unit": "0",
    "season": "1",
    "max_heating_set_point": "45.0",
    "min_heating_set_point": "24.0",
    "sensor_influence": "10",
    "climatic_curve": "0.6",
    "cmd_stand_status": "0",
}
# Parameters of the official app (ParamsMapUtils.getSettingsParams): base
# parameters + unit, season and the four settings.
APP_PARAMS = {
    "user_id", "id", "wifi_box_id", "token", "thermostat_id",
    "unit", "season", "max_heating_set_point", "min_heating_set_point",
    "sensor_influence", "climatic_curve",
}


def settings(**override):
    return {"status": True, "message": {**SETTINGS, **override}}


def client_for(make_client, reads, write=WRITE_OK):
    return make_client({"thermostats/setting": reads, "Thermostats/setting": write})


def form_of(client):
    (url, form), = client._session.puts
    assert url.endswith("Android/Thermostats/setting")
    return form


# ------------------------------------------------------------------ api.py


@pytest.mark.parametrize(
    ("field", "value", "sent"),
    [
        ("max_heating_set_point", 60.0, "60"),
        ("min_heating_set_point", 30, "30"),
        ("sensor_influence", 0.0, "0"),
        ("climatic_curve", "1.5", "1.5"),
        ("climatic_curve", 2, "2.0"),
    ],
)
async def test_payload_matches_the_app(make_client, field, value, sent):
    """Same parameter names as the app; other fields exactly as read."""
    client = client_for(make_client, settings())
    assert await client.writeThermostatSettings(WIFI_BOX, ROOM, {field: value}) == {field: sent}
    form = form_of(client)
    assert set(form) == APP_PARAMS
    expected = {k: SETTINGS[k] for k in APP_PARAMS & set(SETTINGS)}
    expected[field] = sent
    assert {k: form[k] for k in expected} == expected
    assert form["user_id"] == form["id"] == USER_ID
    assert form["wifi_box_id"] == WIFI_BOX and form["thermostat_id"] == ROOM
    assert form["token"] == BesmartClient.TOKEN


@pytest.mark.parametrize(
    "override",
    [
        # partial read: zeros with cmd_stand_status "1"
        {**{k: "0.0" for k in ("min_heating_set_point", "max_heating_set_point", "climatic_curve")},
         "sensor_influence": "0", "cmd_stand_status": "1"},
        {"max_heating_set_point": "0.0"},
        {"climatic_curve": None},
        {"unit": "7"},
        {"sensor_influence": "21"},  # existing field out of the app limits
        {"min_heating_set_point": "40", "max_heating_set_point": "35"},
    ],
)
async def test_partial_or_invalid_read_sends_nothing(make_client, caplog, override):
    client = client_for(make_client, settings(**override))
    with pytest.raises(BesmartSettingsError) as err:
        await client.writeThermostatSettings(WIFI_BOX, ROOM, {"sensor_influence": 5})
    assert err.value.translation_key == "settings_incomplete"
    assert client._session.puts == []


async def test_complete_read_while_synchronising_is_written(make_client):
    """0.8.0: cmd_stand_status "1" alone does not block the PUT."""
    client = client_for(make_client, settings(cmd_stand_status="1"))
    assert await client.writeThermostatSettings(WIFI_BOX, ROOM, {"max_heating_set_point": 50}) == {
        "max_heating_set_point": "50"}
    form = form_of(client)
    assert form["max_heating_set_point"] == "50"
    assert form["min_heating_set_point"] == "24.0" and form["climatic_curve"] == "0.6"
    assert "cmd_stand_status" not in form


async def test_failed_read_sends_nothing(make_client):
    client = client_for(make_client, {"status": False, "error_code": 1})
    with pytest.raises(BesmartSettingsError) as err:
        await client.writeThermostatSettings(WIFI_BOX, ROOM, {"sensor_influence": 5})
    assert err.value.translation_key == "settings_read_failed"
    assert client._session.puts == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_heating_set_point", 81),
        ("max_heating_set_point", 14),
        ("max_heating_set_point", 45.5),  # step 1
        ("min_heating_set_point", 9),
        ("min_heating_set_point", 76),
        ("sensor_influence", 21),
        ("sensor_influence", -1),
        ("climatic_curve", "1.4"),
        ("climatic_curve", "0"),
        ("climatic_curve", "abc"),
        ("max_heating_set_point", float("nan")),
        ("max_heating_set_point", None),
        ("unit", "1"),  # not a writable setting
    ],
)
async def test_out_of_range_request_reads_and_sends_nothing(make_client, field, value):
    client = client_for(make_client, settings())
    with pytest.raises(BesmartValidationError):
        await client.writeThermostatSettings(WIFI_BOX, ROOM, {field: value})
    assert client._session.calls == []


@pytest.mark.parametrize(
    ("change", "shown"),
    [
        ({"min_heating_set_point": 50}, {"min": "50", "max": "45"}),
        ({"max_heating_set_point": 20}, {"min": "24", "max": "20"}),
    ],
)
async def test_min_above_max_is_refused_not_corrected(make_client, change, shown):
    client = client_for(make_client, settings())
    with pytest.raises(BesmartValidationError) as err:
        await client.writeThermostatSettings(WIFI_BOX, ROOM, change)
    assert err.value.translation_key == "min_above_max"
    assert err.value.placeholders == shown
    assert client._session.puts == []


async def test_min_equal_max_is_accepted(make_client):
    client = client_for(make_client, settings())
    await client.writeThermostatSettings(WIFI_BOX, ROOM, {"min_heating_set_point": 45})
    assert form_of(client)["min_heating_set_point"] == "45"


@pytest.mark.parametrize(
    "response",
    [{"status": False, "error_code": 0}, {"status": True, "error_code": 5}, {"status": False}],
)
async def test_cloud_rejection_is_an_error(make_client, response):
    client = client_for(make_client, settings(), write=response)
    with pytest.raises(BesmartSettingsError) as err:
        await client.writeThermostatSettings(WIFI_BOX, ROOM, {"sensor_influence": 5})
    assert err.value.translation_key == "settings_write_failed"


async def test_season_fallback_when_the_read_has_none(make_client):
    reads = {"status": True, "message": {k: v for k, v in SETTINGS.items() if k != "season"}}
    client = client_for(make_client, reads)
    await client.writeThermostatSettings(WIFI_BOX, ROOM, {"sensor_influence": 5}, season="0")
    assert form_of(client)["season"] == "0"

    client = client_for(make_client, reads)
    with pytest.raises(BesmartSettingsError):
        await client.writeThermostatSettings(WIFI_BOX, ROOM, {"sensor_influence": 5})
    assert client._session.puts == []


async def test_season_change_keeps_the_settings(make_client):
    client = client_for(make_client, settings())
    assert await client.setThermostatSeason(WIFI_BOX, ROOM, "0") is True
    form = form_of(client)
    assert form["season"] == "0"
    assert {k: form[k] for k in ("max_heating_set_point", "min_heating_set_point",
                                 "sensor_influence", "climatic_curve")} == {
        "max_heating_set_point": "45.0", "min_heating_set_point": "24.0",
        "sensor_influence": "10", "climatic_curve": "0.6"}


# ---------------------------------------------------- coordinator: verify


@pytest.fixture
def no_verify_delay(monkeypatch):
    monkeypatch.setattr(coordinator_module, "SETTINGS_VERIFY_DELAY", 0)


def coordinator_with(make_coordinator, reads, write=WRITE_OK):
    co = make_coordinator({"thermostats/setting": reads, "Thermostats/setting": write})
    co.data = {WIFI_BOX: {"boiler": {}, "modulation": None, "thermostats": {
        ROOM: {**SETTINGS, "boiler": {"online": "1"}}}}}
    return co


async def set_settings(co, changes):
    await co.async_set_thermostat_settings(WIFI_BOX, ROOM, "Living", changes)


def setting_reads(co):
    return co.client._session.count("thermostats/setting")


async def test_verified_value_is_ok(make_coordinator, no_verify_delay):
    co = coordinator_with(make_coordinator, [settings(), settings(sensor_influence="5")])
    await set_settings(co, {"sensor_influence": 5})
    assert setting_reads(co) == 2  # fresh read + one read-back


async def test_different_value_while_synchronising_is_not_an_error(
    make_coordinator, no_verify_delay, caplog
):
    co = coordinator_with(make_coordinator, [settings(), settings(cmd_stand_status="1")])
    with caplog.at_level(logging.DEBUG):
        await set_settings(co, {"sensor_influence": 5})
    assert setting_reads(co) == 2
    assert "being synchronised" in caplog.text
    assert_no_secrets(caplog.text)


async def test_different_value_is_retried_then_ok(make_coordinator, no_verify_delay):
    co = coordinator_with(
        make_coordinator, [settings(), settings(), settings(), settings(sensor_influence="5.0")]
    )
    await set_settings(co, {"sensor_influence": 5})
    assert setting_reads(co) == 4


async def test_different_value_fails_after_two_retries(make_coordinator, monkeypatch, caplog):
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(coordinator_module.asyncio, "sleep", fake_sleep)
    co = coordinator_with(make_coordinator, [settings()])
    with caplog.at_level(logging.DEBUG), pytest.raises(HomeAssistantError) as err:
        await set_settings(co, {"sensor_influence": 5})
    assert err.value.translation_key == "settings_not_confirmed"
    assert err.value.__cause__ is None
    assert setting_reads(co) == 1 + 3  # fresh read + read-back + 2 retries
    assert [d for d in delays if d != 0.01] == [3, 3]  # 0.01: FakeSession
    assert "not confirmed" in caplog.text
    assert_no_secrets(caplog.text)


async def test_failed_read_back_counts_as_a_mismatch(make_coordinator, no_verify_delay):
    co = coordinator_with(make_coordinator, [settings(), {"status": False}])
    with pytest.raises(HomeAssistantError) as err:
        await set_settings(co, {"sensor_influence": 5})
    assert err.value.translation_key == "settings_not_confirmed"


async def test_errors_are_translated_and_not_chained(make_coordinator, no_verify_delay):
    co = coordinator_with(make_coordinator, [settings()])
    with pytest.raises(ServiceValidationError) as err:
        await set_settings(co, {"min_heating_set_point": 50})
    assert err.value.translation_key == "min_above_max"
    assert err.value.translation_placeholders == {"room": "Living", "min": "50", "max": "45"}

    co = coordinator_with(make_coordinator, [settings()], write={"status": False})
    with pytest.raises(HomeAssistantError) as err:
        await set_settings(co, {"sensor_influence": 5})
    assert err.value.translation_key == "settings_write_failed"
    assert err.value.__cause__ is None

    partial = {k: "0.0" for k in ("min_heating_set_point", "max_heating_set_point", "climatic_curve")}
    co = coordinator_with(make_coordinator, [settings(**partial, cmd_stand_status="1")])
    with pytest.raises(HomeAssistantError) as err:
        await set_settings(co, {"sensor_influence": 5})
    assert err.value.translation_key == "settings_incomplete"
    assert co.client._session.puts == []


async def test_one_write_at_a_time_per_thermostat(make_coordinator, no_verify_delay):
    co = coordinator_with(make_coordinator, [settings()])
    co.client._session.routes["thermostats/setting"] = [
        settings(), settings(sensor_influence="5"), settings(sensor_influence="5"),
        settings(sensor_influence="5", climatic_curve="1.0"),
    ]
    await asyncio.gather(
        set_settings(co, {"sensor_influence": 5}),
        set_settings(co, {"climatic_curve": "1.0"}),
    )
    sequence = ["PUT" if "Thermostats/setting" in url else "GET" for url in co.client._session.calls]
    assert sequence == ["GET", "PUT", "GET", "GET", "PUT", "GET"]
    # The second write starts from the value written by the first one.
    assert co.client._session.puts[1][1]["sensor_influence"] == "5"


@pytest.mark.parametrize(
    ("thermostat_boiler", "topology_boiler", "online"),
    [
        ({"online": "1"}, {"id": 1}, True),
        ({"online": "0"}, {"id": 1, "online": "1"}, False),
        ({"online": 1}, {"id": 1}, True),
        (None, {"id": 1, "online": "1"}, True),  # fallback: Wifi_boxes/data
        (None, {"id": 1}, False),
        (None, None, False),
        # PATCH 0.10: a boiler object without "online" does not fall back,
        # and the thermostat's own top-level "Online" is ignored.
        ({"id": 1}, {"id": 1, "online": "1"}, False),
        ({"online": "0", "Online": "1"}, {"id": 1}, False),
    ],
)
def test_boiler_online(make_coordinator, thermostat_boiler, topology_boiler, online):
    co = make_coordinator({})
    co.interface_devices[0].boiler = topology_boiler
    data = dict(SETTINGS)
    if thermostat_boiler is not None:
        data["boiler"] = thermostat_boiler
    co.data = {WIFI_BOX: {"boiler": {}, "thermostats": {ROOM: data}}}
    # PATCH 0.10: boiler_link(wifi_box, room_id) replaces boiler_online().
    assert (co.boiler_link(WIFI_BOX, ROOM) is BoilerLink.ONLINE) is online


# --------------------------------------------------------- Home Assistant


@pytest.fixture
def settings_cloud(cloud, monkeypatch):
    monkeypatch.setattr(coordinator_module, "SETTINGS_VERIFY_DELAY", 0)
    # 0.8.1: no debounce wait unless a test sets one.
    monkeypatch.setattr(number_module, "SETTINGS_WRITE_DEBOUNCE", 0)
    cloud.set_route(
        "Thermostats/data",
        {"status": True, "message": {
            "mode": "1", "season": "1", "current_temp": "20.5", "unit": "0",
            **{k: v for k, v in SETTINGS.items() if k not in ("unit", "season")},
            "boiler": {"online": "1"},
        }},
    )
    cloud.set_route("thermostats/setting", settings())
    cloud.set_route("Thermostats/setting", WRITE_OK)
    return cloud


async def setup_entry(hass, modes=("heat",)):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="Casa",
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        options={**OPTIONS, CONF_MODE: list(modes)},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_entities_show_the_polled_values(hass, settings_cloud):
    await setup_entry(hass)
    assert hass.states.get("select.living_climatic_curve").state == "0.6"
    assert hass.states.get("number.living_max_flow_temperature").state == "45.0"
    assert hass.states.get("number.living_min_flow_temperature").state == "24.0"
    assert hass.states.get("number.living_room_sensor_influence").state == "10.0"
    max_state = hass.states.get("number.living_max_flow_temperature")
    # b2: dynamic lower limit = current minimum (24).
    assert (max_state.attributes["min"], max_state.attributes["max"], max_state.attributes["step"]) == (24, 80, 1)
    assert hass.states.get("select.living_climatic_curve").attributes["options"] == [
        "0.2", "0.4", "0.6", "0.8", "1.0", "1.2", "1.5", "2.0", "2.5", "3.0"]


async def test_set_number_writes_and_verifies(hass, settings_cloud, caplog):
    await setup_entry(hass)
    settings_cloud.set_route("thermostats/setting", [settings(), settings(max_heating_set_point="60")])
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.living_max_flow_temperature", "value": 60}, blocking=True,
    )
    (_, form), = settings_cloud.puts
    assert form["max_heating_set_point"] == "60"
    assert form["min_heating_set_point"] == "24.0" and form["climatic_curve"] == "0.6"
    assert_no_secrets(caplog.text)


async def test_select_option_writes(hass, settings_cloud):
    await setup_entry(hass)
    settings_cloud.set_route("thermostats/setting", [settings(), settings(climatic_curve="1.5")])
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": "select.living_climatic_curve", "option": "1.5"}, blocking=True,
    )
    (_, form), = settings_cloud.puts
    assert form["climatic_curve"] == "1.5" and form["sensor_influence"] == "10"


@pytest.mark.parametrize(
    ("min_value", "max_value", "min_limits", "max_limits"),
    [
        ("24.0", "45.0", (10, 45), (24, 80)),
        ("40", "60", (10, 60), (40, 80)),
        ("10", "80", (10, 75), (15, 80)),  # clamped to the absolute limits
        ("75", "80", (10, 75), (75, 80)),
        ("35", "35", (10, 35), (35, 80)),
    ],
)
async def test_dynamic_limits(hass, settings_cloud, min_value, max_value, min_limits, max_limits):
    """b2: min's maximum = current max, max's minimum = current min."""
    route = json.loads(json.dumps(settings_cloud.routes["Thermostats/data"]))
    route["message"].update(min_heating_set_point=min_value, max_heating_set_point=max_value)
    settings_cloud.set_route("Thermostats/data", route)
    await setup_entry(hass)
    min_state = hass.states.get("number.living_min_flow_temperature")
    max_state = hass.states.get("number.living_max_flow_temperature")
    assert (min_state.attributes["min"], min_state.attributes["max"]) == min_limits
    assert (max_state.attributes["min"], max_state.attributes["max"]) == max_limits


async def test_dynamic_limits_follow_the_coordinator(hass, settings_cloud):
    entry = await setup_entry(hass)
    route = json.loads(json.dumps(settings_cloud.routes["Thermostats/data"]))
    route["message"].update(min_heating_set_point="30", max_heating_set_point="55")
    settings_cloud.set_route("Thermostats/data", route)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("number.living_min_flow_temperature").attributes["max"] == 55
    assert hass.states.get("number.living_max_flow_temperature").attributes["min"] == 30


async def test_min_above_max_is_refused_without_put(hass, settings_cloud):
    """Home Assistant refuses it first (dynamic limit)."""
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": "number.living_min_flow_temperature", "value": 50}, blocking=True,
        )
    assert settings_cloud.puts == []


async def test_min_above_max_still_validated_in_code(hass, settings_cloud):
    """Limits stale (the max changed in the app meanwhile): clear error, no PUT."""
    await setup_entry(hass)
    settings_cloud.set_route("thermostats/setting", settings(max_heating_set_point="35"))
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": "number.living_min_flow_temperature", "value": 40}, blocking=True,
        )
    assert str(err.value) == (
        "The minimum flow temperature (40 °C) cannot be higher than the maximum flow "
        "temperature (35 °C) of thermostat Living. Change the other value first"
    )  # Home Assistant drops the final period
    assert settings_cloud.puts == []
    # The real value is written back to the state machine.
    assert hass.states.get("number.living_min_flow_temperature").state == "24.0"


async def test_settings_logs_carry_the_masked_thermostat_id(make_client, caplog):
    """b2: debug logs name the thermostat (***xxxx), never the full ids."""
    room = "029410A73D"
    client = make_client({"thermostats/setting": settings(), "Thermostats/setting": WRITE_OK})
    client._known_thermostat_ids.add(room)
    with caplog.at_level(logging.DEBUG):
        await client.writeThermostatSettings(WIFI_BOX, room, {"sensor_influence": 5})
    assert "thermostat settings (thermostat ***A73D)" in caplog.text
    assert "setThermostatSettings ok (thermostat ***A73D)" in caplog.text
    assert room not in caplog.text
    assert_no_secrets(caplog.text)


async def test_unconfirmed_write_shows_an_error(hass, settings_cloud):
    await setup_entry(hass)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": "number.living_room_sensor_influence", "value": 5}, blocking=True,
        )
    assert "did not confirm the new settings of thermostat Living" in str(err.value)
    assert len(settings_cloud.puts) == 1


@pytest.mark.parametrize("boiler", [{"online": "0"}, {}])
async def test_unavailable_when_the_boiler_is_offline(hass, settings_cloud, boiler):
    route = json.loads(json.dumps(settings_cloud.routes["Thermostats/data"]))
    route["message"]["boiler"] = boiler
    settings_cloud.set_route("Thermostats/data", route)
    entry = await setup_entry(hass)
    for entity_id in (
        "select.living_climatic_curve",
        "number.living_max_flow_temperature",
        "number.living_min_flow_temperature",
        "number.living_room_sensor_influence",
    ):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE, entity_id
    # The climate entity is not affected.
    assert hass.states.get("climate.living_thermostat").state != STATE_UNAVAILABLE

    # A direct write is refused as well (HA skips unavailable entities).
    entity = next(
        e for e in hass.data["entity_components"]["number"].entities
        if e.entity_id == "number.living_max_flow_temperature"
    )
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_set_native_value(60)
    assert err.value.translation_key == "boiler_offline"
    assert settings_cloud.puts == []
    assert entry.runtime_data is not None


async def test_season_change_uses_the_same_write_path(hass, settings_cloud):
    await setup_entry(hass, modes=("heat", "cool"))
    settings_cloud.set_route("thermostats/setting", [settings(), settings(season="0")])
    await hass.services.async_call(
        "climate", "set_hvac_mode",
        {"entity_id": "climate.living_thermostat", "hvac_mode": "cool"}, blocking=True,
    )
    (_, form), = settings_cloud.puts
    assert form["season"] == "0" and form["max_heating_set_point"] == "45.0"


# ------------------------------------------------------------ 0.8.1 debounce

MAX_FLOW = "number.living_max_flow_temperature"
MIN_FLOW = "number.living_min_flow_temperature"


def set_value(hass, entity_id, value):
    return hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True,
    )


def with_data(cloud, **fields):
    route = json.loads(json.dumps(cloud.routes["Thermostats/data"]))
    route["message"].update(fields)
    cloud.set_route("Thermostats/data", route)


async def test_close_changes_send_a_single_put(hass, settings_cloud, monkeypatch):
    """Arrow clicks 45 -> 46 ... 50: one PUT with 50, state shown at once."""
    monkeypatch.setattr(number_module, "SETTINGS_WRITE_DEBOUNCE", 0.2)
    await setup_entry(hass)
    settings_cloud.set_route("thermostats/setting", [settings(), settings(max_heating_set_point="50")])
    calls = []
    for value in range(46, 51):
        calls.append(hass.async_create_task(set_value(hass, MAX_FLOW, value)))
        await asyncio.sleep(0.02)
        assert hass.states.get(MAX_FLOW).state == f"{value}.0"
    assert settings_cloud.puts == []
    await asyncio.gather(*calls)
    (_, form), = settings_cloud.puts
    assert form["max_heating_set_point"] == "50"
    assert form["min_heating_set_point"] == "24.0" and form["sensor_influence"] == "10"
    # The polled data still says 45: the written value stays visible...
    assert hass.states.get(MAX_FLOW).state == "50.0"
    # ...until the cloud reports it.
    with_data(settings_cloud, max_heating_set_point="50.0")
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(MAX_FLOW).state == "50.0"
    assert entry.runtime_data.pending_settings == {}


async def test_debounce_is_per_entity(hass, settings_cloud, monkeypatch):
    monkeypatch.setattr(number_module, "SETTINGS_WRITE_DEBOUNCE", 0.1)
    await setup_entry(hass)
    settings_cloud.set_route("thermostats/setting", [
        settings(), settings(max_heating_set_point="50"),
        settings(max_heating_set_point="50"),
        settings(max_heating_set_point="50", sensor_influence="5"),
    ])
    await asyncio.gather(
        set_value(hass, MAX_FLOW, 50),
        set_value(hass, "number.living_room_sensor_influence", 5),
    )
    assert len(settings_cloud.puts) == 2


async def test_failed_final_write_restores_the_cloud_value(hass, settings_cloud, monkeypatch):
    monkeypatch.setattr(number_module, "SETTINGS_WRITE_DEBOUNCE", 0.1)
    await setup_entry(hass)
    settings_cloud.set_route("Thermostats/setting", {"status": False, "error_code": 1})
    first = hass.async_create_task(set_value(hass, MAX_FLOW, 46))
    await asyncio.sleep(0.02)
    with pytest.raises(HomeAssistantError):
        await set_value(hass, MAX_FLOW, 47)
    await first  # superseded: no write, no error
    assert len(settings_cloud.puts) == 1
    assert hass.states.get(MAX_FLOW).state == "45.0"
    assert hass.config_entries.async_entries(DOMAIN)[0].runtime_data.pending_settings == {}


async def test_written_value_is_dropped_after_the_timeout(hass, settings_cloud, monkeypatch):
    """The cloud never reports the value: back to the polled one."""
    monkeypatch.setattr(number_module, "SETTINGS_CONFIRM_TIMEOUT", 0)
    entry = await setup_entry(hass)
    settings_cloud.set_route("thermostats/setting", [settings(), settings(max_heating_set_point="50")])
    await set_value(hass, MAX_FLOW, 50)
    await asyncio.sleep(0.01)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(MAX_FLOW).state == "45.0"


async def test_dynamic_limits_follow_the_pending_value(hass, settings_cloud, monkeypatch):
    """Max lowered to 30 and not yet written: min refuses 35 at once."""
    monkeypatch.setattr(number_module, "SETTINGS_WRITE_DEBOUNCE", 0.2)
    await setup_entry(hass)
    settings_cloud.set_route("thermostats/setting", [settings(), settings(max_heating_set_point="30")])
    pending = hass.async_create_task(set_value(hass, MAX_FLOW, 30))
    await asyncio.sleep(0.02)
    assert hass.states.get(MIN_FLOW).attributes["max"] == 30
    with pytest.raises(ServiceValidationError):
        await set_value(hass, MIN_FLOW, 35)
    await pending
    (_, form), = settings_cloud.puts
    assert form["max_heating_set_point"] == "30"
