"""0.9.0: climatic curve chart image entity (read only, no cloud writes)."""

import json
import logging
from pathlib import Path
import xml.etree.ElementTree as ET
from datetime import timedelta

import pytest
from homeassistant.const import (
    CONF_MODE,
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_STATE_CHANGED,
    STATE_UNAVAILABLE,
    EntityCategory,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.besmart_thermostat import coordinator as coordinator_module
from custom_components.besmart_thermostat import curve_chart
from custom_components.besmart_thermostat.const import DOMAIN

from .conftest import PASSWORD, USERNAME
from .test_setup_and_flows import OPTIONS, _custom_integrations, cloud  # noqa: F401

CHART = "image.living_climatic_curve_chart"
TOKEN_ONLY = {"access_token", "entity_picture"}
# PIANO 1 of Andrea's installation.
THERMOSTAT = {
    "mode": "1", "season": "1", "current_temp": "20.5", "unit": "0",
    "max_heating_set_point": "35.0", "min_heating_set_point": "24.0",
    "sensor_influence": "10", "climatic_curve": "0.6", "cmd_stand_status": "0",
    "heating_status": "1", "central_heating_thermostat_OT_set_point": "26",
    "boiler": {"online": "1"},
}
BOILER = {"work_mode": "0", "unit": "0", "outdoor_probe_temp": "15.3", "heating_target_temp": "40"}


@pytest.fixture
def chart_cloud(cloud):
    cloud.set_route("Thermostats/data", {"status": True, "message": dict(THERMOSTAT)})
    cloud.set_route("Boilers/data", {"status": True, "message": dict(BOILER)})
    return cloud


def update(cloud, route, **fields):
    """Change fields of the Thermostats/data or Boilers/data answer (None = remove)."""
    message = json.loads(json.dumps(cloud.routes[route]))["message"]
    for key, value in fields.items():
        if value is None:
            message.pop(key, None)
        else:
            message[key] = value
    cloud.set_route(route, {"status": True, "message": message})


async def setup_entry(hass, language="en"):
    hass.config.language = language
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, title="Casa",
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        options={**OPTIONS, CONF_MODE: ["heat"]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def refresh(hass, entry):
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


def entity_id_of(hass, suffix):
    """Entity id by unique id suffix (Italian installs get Italian entity ids)."""
    return next(
        e.entity_id for e in er.async_get(hass).entities.values()
        if e.platform == DOMAIN and e.unique_id.endswith(suffix)
    )


def chart_id(hass):
    return entity_id_of(hass, ":r1_climatic_curve_chart")


def chart_entity(hass):
    entity_id = chart_id(hass)
    return next(e for e in hass.data["entity_components"]["image"].entities if e.entity_id == entity_id)


async def svg_of(hass):
    image = await chart_entity(hass).async_image()
    return ET.fromstring(image.decode())


def drawn(root, tag, css):
    return [e for e in root.iter(f"{{http://www.w3.org/2000/svg}}{tag}") if e.get("class") == css]


def texts(root):
    return [e.text for e in root.iter("{http://www.w3.org/2000/svg}text")]


def integration_writes(events):
    """state_changed events of the chart not due to HA's access token rotation."""
    result = []
    for event in events:
        old, new = event.data["old_state"], event.data["new_state"]
        if old is not None and new is not None and old.state == new.state:
            changed = {
                k for k in set(old.attributes) | set(new.attributes)
                if old.attributes.get(k) != new.attributes.get(k)
            }
            if changed <= TOKEN_ONLY:
                continue
        result.append(event)
    return result


def track(hass):
    events = []
    entity_id = chart_id(hass)
    hass.bus.async_listen(
        EVENT_STATE_CHANGED,
        lambda event: events.append(event) if event.data["entity_id"] == entity_id else None,
    )
    return events


async def test_entity_created_without_warnings(hass, chart_cloud, caplog):
    caplog.set_level(logging.WARNING)
    entry = await setup_entry(hass)
    registry_entry = er.async_get(hass).async_get(CHART)
    assert registry_entry.unique_id == f"{entry.entry_id}:r1_climatic_curve_chart"
    assert registry_entry.entity_category is EntityCategory.CONFIG
    assert registry_entry.translation_key == "climatic_curve_chart"
    device = dr.async_get(hass).async_get(registry_entry.device_id)
    assert (DOMAIN, f"{entry.entry_id}:r1") in device.identifiers
    state = hass.states.get(chart_id(hass))
    assert state.attributes["friendly_name"] == "Living Climatic curve chart"
    assert chart_entity(hass).content_type == "image/svg+xml"
    ours = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING
        and r.name.startswith(("custom_components", "homeassistant.helpers", "homeassistant.components.image"))
    ]
    assert ours == [], caplog.text
    # Read only: no write to the cloud.
    assert chart_cloud.puts == []


async def test_piano_1(hass, chart_cloud):
    await setup_entry(hass)
    attrs = hass.states.get(chart_id(hass)).attributes
    assert attrs["climatic_curve"] == 0.6
    assert (attrs["max_flow_temperature"], attrs["min_flow_temperature"]) == (35, 24)
    assert attrs["sensor_influence"] == 10 and attrs["outdoor_temperature"] == 15.3
    assert attrs["curve_flow_temperature"] == 23.3
    assert attrs["limited_flow_temperature"] == 24
    assert attrs["requested_flow_temperature"] == 26
    assert attrs["requested_flow_source"] == "thermostat" and attrs["status"] == "ok"
    root = await svg_of(hass)
    assert drawn(root, "circle", "work") and drawn(root, "circle", "req")
    assert "Requested flow 26 °C (thermostat)" in texts(root)


@pytest.mark.parametrize(
    ("thermostat", "requested", "source"),
    [
        ({"heating_status": "0", "central_heating_thermostat_OT_set_point": "5"}, None, None),
        ({"heating_status": "0", "central_heating_thermostat_OT_set_point": "26"}, None, None),
        ({"heating_status": "1", "central_heating_thermostat_OT_set_point": "26"}, 26, "thermostat"),
        # 5 °C is not a flow temperature: boiler target while calling for heat.
        ({"heating_status": "1", "central_heating_thermostat_OT_set_point": "5"}, 40, "boiler"),
        ({"heating_status": "1", "central_heating_thermostat_OT_set_point": None}, 40, "boiler"),
        # Above max: drawn as read, never limited or recalculated.
        ({"heating_status": "1", "central_heating_thermostat_OT_set_point": "38.5"}, 38.5, "thermostat"),
    ],
)
async def test_requested_flow_source(hass, chart_cloud, thermostat, requested, source):
    update(chart_cloud, "Thermostats/data", **thermostat)
    await setup_entry(hass)
    attrs = hass.states.get(chart_id(hass)).attributes
    assert attrs["requested_flow_temperature"] == requested
    assert attrs["requested_flow_source"] == source


async def test_no_plausible_requested_flow(hass, chart_cloud):
    update(chart_cloud, "Thermostats/data", central_heating_thermostat_OT_set_point="5")
    update(chart_cloud, "Boilers/data", heating_target_temp="0")
    await setup_entry(hass)
    attrs = hass.states.get(chart_id(hass)).attributes
    assert attrs["requested_flow_temperature"] is None and attrs["requested_flow_source"] is None
    assert "Requested flow: not available" in texts(await svg_of(hass))


@pytest.mark.parametrize("outdoor", [None, "null", "DNCON", "", "abc", "disabled"])
async def test_outdoor_temperature_missing_or_not_numeric(hass, chart_cloud, outdoor):
    update(chart_cloud, "Boilers/data", outdoor_probe_temp=outdoor)
    await setup_entry(hass)
    state = hass.states.get(chart_id(hass))
    assert state.state != STATE_UNAVAILABLE
    assert state.attributes["outdoor_temperature"] is None
    assert state.attributes["curve_flow_temperature"] is None
    root = await svg_of(hass)
    assert not drawn(root, "circle", "work") and not drawn(root, "circle", "req")
    assert "Outdoor: not available" in texts(root)
    assert drawn(root, "polyline", "sel")


@pytest.mark.parametrize(
    ("route", "field"), [("Thermostats/data", "unit"), ("Boilers/data", "unit")]
)
async def test_unsupported_unit(hass, chart_cloud, route, field):
    update(chart_cloud, route, **{field: "1"})
    entry = await setup_entry(hass, "it")
    root = await svg_of(hass)
    assert hass.states.get(chart_id(hass)).attributes["status"] == "unsupported_unit"
    assert not root.findall(".//{http://www.w3.org/2000/svg}polyline")
    assert not root.findall(".//{http://www.w3.org/2000/svg}circle")
    assert "unità non supportata" in texts(root)
    # Back to Celsius: redrawn.
    update(chart_cloud, route, **{field: "0"})
    before = hass.states.get(chart_id(hass)).state
    await refresh(hass, entry)
    assert hass.states.get(chart_id(hass)).state != before
    assert hass.states.get(chart_id(hass)).attributes["status"] == "ok"


async def test_missing_unit_is_celsius(hass, chart_cloud):
    update(chart_cloud, "Thermostats/data", unit=None)
    update(chart_cloud, "Boilers/data", unit=None)
    await setup_entry(hass)
    assert hass.states.get(chart_id(hass)).attributes["status"] == "ok"


async def test_boiler_offline(hass, chart_cloud, monkeypatch):
    # PATCH 0.10: the chart follows the boiler state confirmed for the box
    # (offline after BOILER_OFFLINE_CONFIRM seconds), like the boiler sensors.
    clock = [1000.0]
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: clock[0])
    entry = await setup_entry(hass, "it")
    update(chart_cloud, "Thermostats/data", boiler={"online": "0"})
    await refresh(hass, entry)
    # One offline cycle: points still drawn, outdoor sensor unchanged; the
    # settings entities follow the thermostat read at once.
    assert hass.states.get(chart_id(hass)).attributes["status"] == "ok"
    assert hass.states.get(entity_id_of(hass, ":outdoor_probe_temp")).state == "15.3"
    assert hass.states.get(entity_id_of(hass, ":r1_max_heating_set_point")).state == STATE_UNAVAILABLE

    clock[0] += coordinator_module.BOILER_OFFLINE_CONFIRM
    await refresh(hass, entry)
    state = hass.states.get(chart_id(hass))
    assert state.state != STATE_UNAVAILABLE
    assert state.attributes["status"] == "boiler_offline"
    assert state.attributes["climatic_curve"] == 0.6
    assert state.attributes["outdoor_temperature"] is None
    assert state.attributes["requested_flow_temperature"] is None
    root = await svg_of(hass)
    assert drawn(root, "polyline", "sel") and drawn(root, "line", "max") and drawn(root, "line", "min")
    assert not drawn(root, "circle", "work") and not drawn(root, "circle", "req")
    assert "T esterna: caldaia non raggiungibile" in texts(root)
    # Same confirmed state: the outdoor sensor is unavailable too.
    assert hass.states.get(entity_id_of(hass, ":outdoor_probe_temp")).state == STATE_UNAVAILABLE
    # Back online: redrawn with the points.
    update(chart_cloud, "Thermostats/data", boiler={"online": "1"})
    await refresh(hass, entry)
    assert hass.states.get(chart_id(hass)).attributes["status"] == "ok"
    assert drawn(await svg_of(hass), "circle", "work")


async def test_boiler_online_missing_keeps_previous_rule(hass, chart_cloud):
    """No boiler.online in the reads: as before 0.10 (thermostat read, then
    Wifi_boxes/data), while the boiler sensors stay available."""
    update(chart_cloud, "Thermostats/data", boiler=None)
    await setup_entry(hass, "it")
    assert hass.states.get(chart_id(hass)).attributes["status"] == "boiler_offline"
    assert hass.states.get(entity_id_of(hass, ":outdoor_probe_temp")).state == "15.3"


async def test_unavailable_without_thermostat_data(hass, chart_cloud):
    entry = await setup_entry(hass)
    entry.runtime_data.data = {}
    entry.runtime_data.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(chart_id(hass)).state == STATE_UNAVAILABLE


# -------------------------------------------------------------- partial reads


@pytest.mark.parametrize(
    "partial",
    [
        {"max_heating_set_point": "0.0", "min_heating_set_point": "0.0", "climatic_curve": "0.0",
         "sensor_influence": "0", "cmd_stand_status": "1"},
        {"max_heating_set_point": None, "min_heating_set_point": None, "climatic_curve": None},
        {"max_heating_set_point": "95", "min_heating_set_point": "5", "climatic_curve": "4"},
        {"min_heating_set_point": "40", "max_heating_set_point": "35"},  # min > max
    ],
)
async def test_partial_read_without_previous_values(hass, chart_cloud, partial):
    update(chart_cloud, "Thermostats/data", **partial)
    await setup_entry(hass)
    attrs = hass.states.get(chart_id(hass)).attributes
    assert attrs["max_flow_temperature"] is None and attrs["min_flow_temperature"] is None
    root = await svg_of(hass)
    assert not drawn(root, "line", "max") and not drawn(root, "line", "min")
    if "climatic_curve" in partial:
        assert attrs["climatic_curve"] is None
        assert not drawn(root, "polyline", "sel") and not drawn(root, "circle", "work")
    assert "Max flow: not available" in texts(root)


@pytest.mark.parametrize(
    "partial",
    [
        {"max_heating_set_point": "0.0", "min_heating_set_point": "0.0", "climatic_curve": "0.0",
         "sensor_influence": "0", "cmd_stand_status": "1"},
        {"max_heating_set_point": None, "min_heating_set_point": None, "climatic_curve": None,
         "sensor_influence": None},
        {"max_heating_set_point": "95", "min_heating_set_point": "5", "climatic_curve": "4"},
        {"min_heating_set_point": "40", "max_heating_set_point": "35"},
    ],
)
async def test_partial_read_keeps_the_last_valid_values(hass, chart_cloud, partial):
    entry = await setup_entry(hass)
    before = hass.states.get(chart_id(hass))
    image_before = await chart_entity(hass).async_image()
    events = track(hass)
    update(chart_cloud, "Thermostats/data", **partial)
    await refresh(hass, entry)
    after = hass.states.get(chart_id(hass))
    assert after.state == before.state  # not redrawn
    assert integration_writes(events) == []
    assert await chart_entity(hass).async_image() == image_before
    assert (after.attributes["max_flow_temperature"], after.attributes["min_flow_temperature"]) == (35, 24)
    assert after.attributes["climatic_curve"] == 0.6


async def test_partial_read_with_a_new_valid_field_uses_last_values_for_the_rest(hass, chart_cloud):
    entry = await setup_entry(hass)
    update(chart_cloud, "Thermostats/data", max_heating_set_point="0.0", sensor_influence="12")
    await refresh(hass, entry)
    attrs = hass.states.get(chart_id(hass)).attributes
    assert attrs["max_flow_temperature"] == 35 and attrs["sensor_influence"] == 12


# --------------------------------------------------- redraw and state writes


async def test_flapping_outdoor_temperature_writes_nothing(hass, chart_cloud):
    entry = await setup_entry(hass)
    state = hass.states.get(chart_id(hass))
    events = track(hass)
    for outdoor in ("15.2", "15.3") * 5 + ("15.6", "15.0"):
        update(chart_cloud, "Boilers/data", outdoor_probe_temp=outdoor)
        await refresh(hass, entry)
    assert integration_writes(events) == []
    assert hass.states.get(chart_id(hass)).state == state.state
    assert hass.states.get(chart_id(hass)).attributes["outdoor_temperature"] == 15.3


async def test_slow_drift_redraws_at_the_threshold(hass, chart_cloud):
    update(chart_cloud, "Boilers/data", outdoor_probe_temp="15.2")
    entry = await setup_entry(hass)
    events = track(hass)
    for outdoor in ("15.4", "15.6"):
        update(chart_cloud, "Boilers/data", outdoor_probe_temp=outdoor)
        await refresh(hass, entry)
    assert integration_writes(events) == []
    update(chart_cloud, "Boilers/data", outdoor_probe_temp="15.8")
    await refresh(hass, entry)
    (event,) = integration_writes(events)
    assert event.data["new_state"].attributes["outdoor_temperature"] == 15.8
    assert event.data["new_state"].state != event.data["old_state"].state


async def test_requested_flow_threshold(hass, chart_cloud):
    entry = await setup_entry(hass)
    events = track(hass)
    update(chart_cloud, "Thermostats/data", central_heating_thermostat_OT_set_point="26.4")
    await refresh(hass, entry)
    assert integration_writes(events) == []
    update(chart_cloud, "Thermostats/data", central_heating_thermostat_OT_set_point="26.5")
    await refresh(hass, entry)
    assert len(integration_writes(events)) == 1
    assert hass.states.get(chart_id(hass)).attributes["requested_flow_temperature"] == 26.5


@pytest.mark.parametrize(
    ("route", "change"),
    [
        ("Thermostats/data", {"climatic_curve": "0.8"}),
        ("Thermostats/data", {"max_heating_set_point": "36.0"}),
        ("Thermostats/data", {"min_heating_set_point": "23.0"}),
        ("Thermostats/data", {"sensor_influence": "11"}),
        ("Thermostats/data", {"heating_status": "0"}),  # requested flow disappears
        ("Boilers/data", {"outdoor_probe_temp": "DNCON"}),  # outdoor disappears
    ],
)
async def test_other_changes_redraw_at_once(hass, chart_cloud, route, change):
    entry = await setup_entry(hass)
    image_before = await chart_entity(hass).async_image()
    events = track(hass)
    update(chart_cloud, route, **change)
    await refresh(hass, entry)
    assert len(integration_writes(events)) == 1
    assert await chart_entity(hass).async_image() != image_before


async def test_attributes_describe_the_drawn_image(hass, chart_cloud):
    entry = await setup_entry(hass)
    update(chart_cloud, "Boilers/data", outdoor_probe_temp="15.6")
    await refresh(hass, entry)
    attrs = hass.states.get(chart_id(hass)).attributes
    # Not redrawn: the attributes still show the drawn 15.3, like the image.
    assert attrs["outdoor_temperature"] == 15.3
    assert "Outdoor 15.3 °C → curve 23.3 °C, limited to 24 °C" in texts(await svg_of(hass))


async def test_attributes_are_not_recorded(hass, chart_cloud):
    await setup_entry(hass)
    entity = chart_entity(hass)
    unrecorded = entity._unrecorded_attributes | entity._entity_component_unrecorded_attributes
    assert set(hass.states.get(chart_id(hass)).attributes) - {"friendly_name"} <= unrecorded
    for name in (
        "climatic_curve", "max_flow_temperature", "min_flow_temperature", "sensor_influence",
        "outdoor_temperature", "curve_flow_temperature", "limited_flow_temperature",
        "requested_flow_temperature", "requested_flow_source", "status",
    ):
        assert name in entity._unrecorded_attributes


async def test_token_rotation_changes_only_the_token(hass, chart_cloud):
    await setup_entry(hass)
    before = hass.states.get(chart_id(hass))
    image_before = await chart_entity(hass).async_image()
    events = track(hass)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()
    after = hass.states.get(chart_id(hass))
    assert after.attributes["access_token"] != before.attributes["access_token"]
    assert after.state == before.state
    assert {k: v for k, v in after.attributes.items() if k not in TOKEN_ONLY} == {
        k: v for k, v in before.attributes.items() if k not in TOKEN_ONLY
    }
    assert await chart_entity(hass).async_image() == image_before
    assert events and integration_writes(events) == []


# ----------------------------------------------------------------- language


async def test_italian_chart(hass, chart_cloud):
    await setup_entry(hass, "it")
    root = await svg_of(hass)
    assert "Mandata richiesta 26 °C (termostato)" in texts(root)
    assert "T esterna 15.3 °C → curva 23.3 °C, limitata a 24 °C" in texts(root)


async def test_unknown_language_falls_back_to_english(hass, chart_cloud):
    await setup_entry(hass, "de")
    assert "Requested flow 26 °C (thermostat)" in texts(await svg_of(hass))


async def test_language_change_redraws(hass, chart_cloud):
    await setup_entry(hass, "en")
    before = hass.states.get(chart_id(hass)).state
    await hass.config.async_update(language="it")
    await hass.async_block_till_done()
    assert hass.states.get(chart_id(hass)).state != before
    assert "Mandata richiesta 26 °C (termostato)" in texts(await svg_of(hass))


async def test_image_served_by_home_assistant(hass, chart_cloud, hass_client):
    await setup_entry(hass)
    client = await hass_client()
    response = await client.get(f"/api/image_proxy/{chart_id(hass)}")
    assert response.status == 200
    assert response.content_type == "image/svg+xml"
    ET.fromstring(await response.text())


def test_curve_chart_does_not_import_home_assistant():
    source = curve_chart.__file__
    with open(source, encoding="utf-8") as file:
        assert "homeassistant" not in file.read()


# ----------------------------------------------- 0.9.0b2: axis, off scale


async def test_piano_1_attributes_b2(hass, chart_cloud):
    await setup_entry(hass)
    attrs = hass.states.get(chart_id(hass)).attributes
    assert attrs["outdoor_off_scale"] is False and attrs["requested_off_scale"] is False
    entity = chart_entity(hass)
    assert {"outdoor_off_scale", "requested_off_scale"} <= entity._unrecorded_attributes


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "Outdoor 23.1 °C (off scale, value at 20 °C) → curve 20 °C, limited to 24 °C"),
        ("it", "T esterna 23.1 °C (fuori scala, valore a 20 °C) → curva 20 °C, limitata a 24 °C"),
    ],
)
async def test_outdoor_off_scale_on_the_entity(hass, chart_cloud, language, text):
    update(chart_cloud, "Boilers/data", outdoor_probe_temp="23.1")
    await setup_entry(hass, language)
    attrs = hass.states.get(chart_id(hass)).attributes
    assert attrs["outdoor_temperature"] == 23.1 and attrs["outdoor_off_scale"] is True
    assert attrs["curve_flow_temperature"] == 20 and attrs["limited_flow_temperature"] == 24
    root = await svg_of(hass)
    assert text in texts(root)
    assert drawn(root, "circle", "workoff") and drawn(root, "polygon", "offscale")


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "Requested flow 60 °C, off scale (thermostat)"),
        ("it", "Mandata richiesta 60 °C, fuori scala (termostato)"),
    ],
)
async def test_requested_off_scale_on_the_entity(hass, chart_cloud, language, text):
    update(chart_cloud, "Thermostats/data", central_heating_thermostat_OT_set_point="60")
    await setup_entry(hass, language)
    attrs = hass.states.get(chart_id(hass)).attributes
    assert attrs["requested_flow_temperature"] == 60 and attrs["requested_off_scale"] is True
    root = await svg_of(hass)
    assert text in texts(root)
    assert drawn(root, "polygon", "offscale")


async def test_axis_follows_max_and_min(hass, chart_cloud):
    entry = await setup_entry(hass)

    def axis_labels(root):
        return [t.text for t in root.iter("{http://www.w3.org/2000/svg}text")
                if t.get("class") == "axis" and t.get("text-anchor") == "end" and t.text.isdigit()]

    assert axis_labels(await svg_of(hass))[-1] == "50"
    events = track(hass)
    update(chart_cloud, "Thermostats/data", max_heating_set_point="45.0")
    await refresh(hass, entry)
    assert len(integration_writes(events)) == 1
    assert axis_labels(await svg_of(hass))[-1] == "60"
    update(chart_cloud, "Thermostats/data", min_heating_set_point="15.0")
    await refresh(hass, entry)
    assert len(integration_writes(events)) == 2
    assert axis_labels(await svg_of(hass))[0] == "10"


async def test_off_scale_attribute_names_are_translated():
    base = Path(curve_chart.__file__).parent
    for name in ("strings.json", "translations/en.json", "translations/it.json"):
        attributes = json.loads((base / name).read_text(encoding="utf-8"))["entity"]["image"][
            "climatic_curve_chart"]["state_attributes"]
        assert {"outdoor_off_scale", "requested_off_scale"} <= set(attributes)
