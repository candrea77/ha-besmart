"""PATCH 0.10: boiler entities while the boiler is offline
(candrea77/ha-besmart#10).

The boiler state comes only from boiler.online in the thermostat reads and
is confirmed offline after BOILER_OFFLINE_CONFIRM seconds of consecutive
offline cycles. Runs against a real Home Assistant instance; the BeSMART
cloud is replaced by FakeSession and the coordinator clock is controlled.
"""

import json
from pathlib import Path

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.besmart_thermostat import coordinator as coordinator_module
from custom_components.besmart_thermostat.coordinator import BoilerLink, _box_link
from custom_components.besmart_thermostat.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import WIFI_BOX
from .test_setup_and_flows import HTML_503, _custom_integrations, cloud, v2_entry  # noqa: F401

CONFIRM = coordinator_module.BOILER_OFFLINE_CONFIRM
BOILER_ENTITIES = [
    f"{WIFI_BOX}:system_pressure",
    f"{WIFI_BOX}:outdoor_probe_temp",
    f"{WIFI_BOX}:heating_current_temp",
    f"{WIFI_BOX}:modulation_ratio",
    f"{WIFI_BOX}:flame_status",
    f"{WIFI_BOX}:heating_status",
    f"{WIFI_BOX}:dhw_active",
    f"{WIFI_BOX}:water_heater",
]
UNCHANGED_ENTITIES = [
    f"{WIFI_BOX}:technical_data",
    f"{WIFI_BOX}:read_technical_data",
    "refresh_now",
    "r1",
    "r1_temperature",
    "r1_battery_status",
]
MISSING = object()


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: now[0])
    return now


def set_online(cloud, online, **extra):
    """Thermostats/data answer with boiler.online = online (MISSING = no key)."""
    message = {"mode": "1", "season": "1", "current_temp": "20.5", "unit": "0", **extra}
    if online is not MISSING:
        message["boiler"] = {"online": online}
    cloud.set_route("Thermostats/data", {"status": True, "message": message})


async def setup(hass):
    entry = v2_entry(minor_version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def refresh(hass, entry):
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


def entity_id(hass, entry, suffix):
    return next(
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.unique_id == f"{entry.entry_id}:{suffix}"
    )


def available(hass, entry, suffix):
    return hass.states.get(entity_id(hass, entry, suffix)).state != STATE_UNAVAILABLE


async def go_offline_confirmed(hass, entry, cloud, clock):
    set_online(cloud, "0")
    await refresh(hass, entry)
    clock[0] += CONFIRM
    await refresh(hass, entry)


# ------------------------------------------------------- raw state of a box


@pytest.mark.parametrize(
    ("reads", "link"),
    [
        ([{"boiler": {"online": "1"}}], BoilerLink.ONLINE),
        ([{"boiler": {"online": 1}}], BoilerLink.ONLINE),
        ([{"boiler": {"online": "0"}}], BoilerLink.OFFLINE),
        # Two thermostats that disagree: online.
        ([{"boiler": {"online": "1"}}, {"boiler": {"online": "0"}}], BoilerLink.ONLINE),
        ([{"boiler": {"online": "0"}}, {"boiler": {}}], BoilerLink.OFFLINE),
        # The thermostat's own top-level "Online" is ignored.
        ([{"Online": "1", "boiler": {"online": "0"}}], BoilerLink.OFFLINE),
        ([{"Online": "1"}], BoilerLink.UNKNOWN),
        ([{"boiler": {"Online": "1"}}], BoilerLink.UNKNOWN),
        ([{"boiler": None}, {}], BoilerLink.UNKNOWN),
    ],
)
def test_box_link(reads, link):
    assert _box_link(reads) is link


# ------------------------------------------------- entities, 1 / 0 / missing


@pytest.mark.parametrize("suffix", BOILER_ENTITIES)
async def test_boiler_entity_online_offline_missing(hass, cloud, clock, suffix):
    set_online(cloud, "1")
    entry = await setup(hass)
    assert available(hass, entry, suffix)

    await go_offline_confirmed(hass, entry, cloud, clock)
    assert not available(hass, entry, suffix)

    set_online(cloud, MISSING)
    await refresh(hass, entry)
    assert available(hass, entry, suffix)

    clock[0] += 10 * CONFIRM
    await refresh(hass, entry)
    assert available(hass, entry, suffix)


@pytest.mark.parametrize("suffix", UNCHANGED_ENTITIES)
async def test_other_entities_unchanged_when_offline(hass, cloud, clock, suffix):
    set_online(cloud, "1")
    entry = await setup(hass)
    await go_offline_confirmed(hass, entry, cloud, clock)
    assert available(hass, entry, suffix)


async def test_online_in_boilers_data_is_ignored(hass, cloud, clock):
    set_online(cloud, "1")
    route = json.loads(json.dumps(cloud.routes["Boilers/data"]))
    route["message"]["online"] = "0"
    cloud.set_route("Boilers/data", route)
    entry = await setup(hass)
    clock[0] += CONFIRM
    await refresh(hass, entry)
    assert available(hass, entry, f"{WIFI_BOX}:outdoor_probe_temp")
    assert entry.runtime_data.boiler_link(WIFI_BOX) is BoilerLink.ONLINE


# ------------------------------------------------------------- no flapping


async def test_single_offline_cycle_changes_nothing(hass, cloud, clock):
    set_online(cloud, "1")
    entry = await setup(hass)
    outdoor = entity_id(hass, entry, f"{WIFI_BOX}:outdoor_probe_temp")
    changes = []
    hass.bus.async_listen("state_changed", lambda e: changes.append(e.data["entity_id"]))

    set_online(cloud, "0")
    await refresh(hass, entry)
    clock[0] += CONFIRM - 1
    set_online(cloud, "1")
    await refresh(hass, entry)
    for suffix in BOILER_ENTITIES:
        assert available(hass, entry, suffix), suffix
    assert outdoor not in changes


async def test_offline_confirmed_after_threshold_and_back_at_once(hass, cloud, clock):
    set_online(cloud, "1")
    entry = await setup(hass)
    suffix = f"{WIFI_BOX}:outdoor_probe_temp"
    set_online(cloud, "0")
    await refresh(hass, entry)
    clock[0] += CONFIRM - 1
    await refresh(hass, entry)
    assert available(hass, entry, suffix)
    clock[0] += 1
    await refresh(hass, entry)
    assert not available(hass, entry, suffix)
    # Back online: available at the first online read.
    set_online(cloud, "1")
    await refresh(hass, entry)
    assert available(hass, entry, suffix)


async def test_failed_read_neither_confirms_nor_resets(hass, cloud, clock):
    set_online(cloud, "1")
    entry = await setup(hass)
    suffix = f"{WIFI_BOX}:outdoor_probe_temp"
    set_online(cloud, "0")
    await refresh(hass, entry)  # streak starts
    clock[0] += CONFIRM
    cloud.set_route("Thermostats/data", HTML_503)
    await refresh(hass, entry)  # previous data reused: no confirmation
    assert available(hass, entry, suffix)
    clock[0] += 10
    set_online(cloud, "0")
    await refresh(hass, entry)  # same streak: confirmed
    assert not available(hass, entry, suffix)


async def test_refresh_now_twice_does_not_confirm(hass, cloud, clock):
    set_online(cloud, "1")
    entry = await setup(hass)
    set_online(cloud, "0")
    await entry.runtime_data.async_manual_refresh()
    clock[0] += 100
    await entry.runtime_data.async_manual_refresh()
    await hass.async_block_till_done()
    assert available(hass, entry, f"{WIFI_BOX}:outdoor_probe_temp")


async def test_unknown_resets_the_streak(hass, cloud, clock):
    set_online(cloud, "1")
    entry = await setup(hass)
    suffix = f"{WIFI_BOX}:outdoor_probe_temp"
    set_online(cloud, "0")
    await refresh(hass, entry)
    clock[0] += 100
    set_online(cloud, MISSING)
    await refresh(hass, entry)
    clock[0] += CONFIRM
    set_online(cloud, "0")
    await refresh(hass, entry)  # new streak
    assert available(hass, entry, suffix)
    clock[0] += CONFIRM
    await refresh(hass, entry)
    assert not available(hass, entry, suffix)


async def test_no_persistence_after_reload(hass, cloud, clock):
    set_online(cloud, "1")
    entry = await setup(hass)
    await go_offline_confirmed(hass, entry, cloud, clock)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert available(hass, entry, f"{WIFI_BOX}:outdoor_probe_temp")


# ------------------------------------------------------------ water heater


async def test_water_heater_commands_when_offline(hass, cloud, clock):
    cloud.set_route("Boilers/dhw_target_temp", {"error_code": 0, "status": True})
    set_online(cloud, "1")
    entry = await setup(hass)
    water_heater = entity_id(hass, entry, f"{WIFI_BOX}:water_heater")
    await go_offline_confirmed(hass, entry, cloud, clock)
    # Unavailable: Home Assistant skips the call, nothing is sent.
    await hass.services.async_call(
        "water_heater", "set_temperature", {"entity_id": water_heater, "temperature": 50}, blocking=True
    )
    assert cloud.puts == []
    # A command that still reaches the entity is refused.
    entity = hass.data["water_heater"].get_entity(water_heater)
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_set_temperature(temperature=50)
    assert err.value.translation_key == "boiler_offline_dhw"
    with pytest.raises(HomeAssistantError):
        await entity.async_set_operation_mode("off")
    assert cloud.puts == []


async def test_water_heater_accepts_commands_before_confirmation(hass, cloud, clock):
    """Same confirmed state as its availability: while it looks available
    (offline not confirmed yet) commands are sent, not refused."""
    cloud.set_route("Boilers/dhw_target_temp", {"error_code": 0, "status": True})
    set_online(cloud, "1")
    entry = await setup(hass)
    water_heater = entity_id(hass, entry, f"{WIFI_BOX}:water_heater")
    set_online(cloud, "0")  # thermostat read offline: settings refuse at once
    await refresh(hass, entry)
    clock[0] += CONFIRM - 1
    await refresh(hass, entry)
    assert available(hass, entry, f"{WIFI_BOX}:water_heater")
    await hass.services.async_call(
        "water_heater", "set_temperature", {"entity_id": water_heater, "temperature": 50}, blocking=True
    )
    assert cloud.puts[-1][1]["temp"] == 50


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("strings.json", "The boiler is offline: domestic hot water cannot be changed."),
        ("translations/en.json", "The boiler is offline: domestic hot water cannot be changed."),
        ("translations/it.json", "La caldaia è offline: l'acqua calda sanitaria non può essere modificata."),
    ],
)
def test_boiler_offline_dhw_translation(name, message):
    base = Path(__file__).resolve().parents[1] / "custom_components" / "besmart_thermostat"
    exceptions = json.loads((base / name).read_text(encoding="utf-8"))["exceptions"]
    assert exceptions["boiler_offline_dhw"]["message"] == message
    # The thermostat settings keep their own message.
    assert "{room}" in exceptions["boiler_offline"]["message"]


# ------------------------------------------- cloud calls, secrets, diagnostics


async def test_no_extra_cloud_calls(hass, cloud, clock):
    set_online(cloud, "1")
    entry = await setup(hass)

    async def cycle_calls():
        start = len(cloud.calls)
        await refresh(hass, entry)
        return sorted(
            next(key for key in ("Boilers/data", "Thermostats/data", "modulation_ratio") if key in url)
            for url in cloud.calls[start:]
        )

    expected = ["Boilers/data", "Thermostats/data", "modulation_ratio"]
    assert await cycle_calls() == expected
    set_online(cloud, "0")
    assert await cycle_calls() == expected
    clock[0] += CONFIRM
    assert await cycle_calls() == expected
    assert cloud.puts == []


async def test_logs_and_diagnostics_hide_the_wifi_box(hass, cloud, clock, caplog):
    caplog.set_level("DEBUG", logger="custom_components.besmart_thermostat")
    set_online(cloud, "1")
    entry = await setup(hass)
    await go_offline_confirmed(hass, entry, cloud, clock)
    assert "online -> offline" in caplog.text
    assert WIFI_BOX not in caplog.text

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["boiler_link"] == {
        "box_1": {"raw": "offline", "confirmed": "offline", "offline_seconds": CONFIRM}
    }
    assert WIFI_BOX not in json.dumps(diag, default=str)
