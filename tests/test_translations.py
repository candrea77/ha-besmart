"""0.7.1: entity names via translation_key (EN unchanged, IT translated).

The English snapshot below was taken from the 0.7.0 code (fixed English
names): new English installs must keep exactly these entity_ids.
"""

import json
from pathlib import Path

import pytest
from homeassistant.helpers import entity_registry as er

from .conftest import WIFI_BOX
from .test_setup_and_flows import _custom_integrations, cloud, v2_entry  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "custom_components" / "besmart_thermostat"

# unique_id suffix (after "<entry_id>:") -> (entity_id, friendly name EN, friendly name IT)
ENTITIES = {
    "r1": ("climate.casa_living_thermostat", "Casa Living Thermostat", "Casa Termostato Living"),
    "r1_temperature": ("sensor.casa_living_temperature", "Casa Living Temperature", "Casa Temperatura Living"),
    "r1_battery_status": (
        "sensor.casa_living_thermostat_battery_status",
        "Casa Living Thermostat Battery Status",
        "Casa Stato batteria termostato Living",
    ),
    f"{WIFI_BOX}:water_heater": ("water_heater.casa_water_heater", "Casa Water Heater", "Casa Acqua calda sanitaria"),
    f"{WIFI_BOX}:system_pressure": (
        "sensor.casa_boiler_system_pressure",
        "Casa Boiler System Pressure",
        "Casa Pressione impianto caldaia",
    ),
    f"{WIFI_BOX}:heating_current_temp": (
        "sensor.casa_boiler_water_temperature",
        "Casa Boiler Water Temperature",
        "Casa Temperatura mandata caldaia",
    ),
    f"{WIFI_BOX}:outdoor_probe_temp": (
        "sensor.casa_outdoor_temperature",
        "Casa Outdoor Temperature",
        "Casa Temperatura esterna",
    ),
    f"{WIFI_BOX}:modulation_ratio": ("sensor.casa_boiler_modulation", "Casa Boiler Modulation", "Casa Modulazione caldaia"),
    f"{WIFI_BOX}:flame_status": ("binary_sensor.casa_boiler_flame", "Casa Boiler Flame", "Casa Fiamma caldaia"),
    f"{WIFI_BOX}:heating_status": (
        "binary_sensor.casa_boiler_circulator",
        "Casa Boiler Circulator",
        "Casa Circolatore caldaia",
    ),
    f"{WIFI_BOX}:dhw_active": ("binary_sensor.casa_dhw_tap", "Casa DHW Tap", "Casa Prelievo ACS"),
    "refresh_now": ("button.casa_refresh_now", "Casa Refresh now", "Casa Aggiorna ora"),
    f"{WIFI_BOX}:read_technical_data": (
        "button.casa_read_technical_data",
        "Casa Read technical data",
        "Casa Leggi dati tecnici",
    ),
    f"{WIFI_BOX}:technical_data": ("sensor.casa_technical_data", "Casa Technical data", "Casa Dati tecnici"),
}


async def _setup(hass, language, entry=None):
    hass.config.language = language
    entry = entry or v2_entry()
    if entry.entry_id not in [e.entry_id for e in hass.config_entries.async_entries()]:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _registry_by_suffix(hass, entry):
    prefix = f"{entry.entry_id}:"
    return {
        e.unique_id.removeprefix(prefix): e
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }


async def test_new_english_install_keeps_0_7_0_entity_ids_and_names(hass, cloud):
    entry = await _setup(hass, "en")
    registry = _registry_by_suffix(hass, entry)
    assert set(registry) == set(ENTITIES)
    for suffix, (entity_id, name_en, _) in ENTITIES.items():
        assert registry[suffix].entity_id == entity_id, suffix
        assert hass.states.get(entity_id).attributes["friendly_name"] == name_en


async def test_italian_names_are_translated(hass, cloud):
    entry = await _setup(hass, "it")
    registry = _registry_by_suffix(hass, entry)
    for suffix, (_, _, name_it) in ENTITIES.items():
        state = hass.states.get(registry[suffix].entity_id)
        assert state.attributes["friendly_name"] == name_it, suffix


async def test_new_italian_install_gets_italian_entity_ids(hass, cloud):
    """Standard HA behaviour for languages with native entity ids (documented)."""
    entry = await _setup(hass, "it")
    registry = _registry_by_suffix(hass, entry)
    assert registry[f"{WIFI_BOX}:modulation_ratio"].entity_id == "sensor.casa_modulazione_caldaia"
    assert registry["r1"].entity_id == "climate.casa_termostato_living"


@pytest.mark.parametrize("language", ["en", "it"])
async def test_existing_entities_keep_unique_id_and_entity_id(hass, cloud, language):
    """Entities registered by 0.7.0 (English names) are unchanged after the update."""
    entry = v2_entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for suffix, (entity_id, name_en, _) in ENTITIES.items():
        domain, object_id = entity_id.split(".")
        registry.async_get_or_create(
            domain,
            "besmart_thermostat",
            f"{entry.entry_id}:{suffix}",
            suggested_object_id=object_id,
            config_entry=entry,
            original_name=name_en,
        )
    await _setup(hass, language, entry)
    after = _registry_by_suffix(hass, entry)
    assert set(after) == set(ENTITIES)
    for suffix, (entity_id, _, name_it) in ENTITIES.items():
        assert after[suffix].entity_id == entity_id, suffix
        assert hass.states.get(entity_id) is not None
    if language == "it":
        # Only the displayed name changes.
        assert hass.states.get("sensor.casa_boiler_modulation").attributes["friendly_name"] == "Casa Modulazione caldaia"


@pytest.mark.parametrize("language", ["en", "it"])
async def test_user_custom_name_is_kept(hass, cloud, language):
    entry = v2_entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "climate", "besmart_thermostat", f"{entry.entry_id}:r1",
        suggested_object_id="casa_living_thermostat", config_entry=entry,
    )
    registry.async_update_entity("climate.casa_living_thermostat", name="Soggiorno")
    await _setup(hass, language, entry)
    entity = registry.async_get("climate.casa_living_thermostat")
    assert entity.name == "Soggiorno"
    assert hass.states.get("climate.casa_living_thermostat").attributes["friendly_name"] == "Soggiorno"


# ------------------------------------------------------- translation files


def _load(path):
    return json.loads((BASE / path).read_text())


def _keys(obj, prefix=""):
    if isinstance(obj, dict):
        out = set()
        for k, v in obj.items():
            out |= _keys(v, f"{prefix}.{k}" if prefix else k)
        return out
    return {prefix}


def test_translation_files_have_the_same_keys():
    strings, en, it = _load("strings.json"), _load("translations/en.json"), _load("translations/it.json")
    assert strings == en
    assert _keys(it) == _keys(en)


def test_every_entity_uses_a_translation_key():
    sources = "".join(
        (BASE / f).read_text()
        for f in ("climate.py", "water_heater.py", "sensor.py", "binary_sensor.py", "button.py")
    )
    assert "_attr_name =" not in sources
    en = _load("translations/en.json")["entity"]
    for platform, keys in {
        "climate": ["thermostat"],
        "water_heater": ["water_heater"],
        "sensor": ["battery_status", "room_temperature", "system_pressure", "water_temperature",
                   "outdoor_temperature", "modulation", "technical_data"],
        "binary_sensor": ["flame", "circulator", "dhw_tap"],
        "button": ["refresh_now", "read_technical_data"],
    }.items():
        for key in keys:
            assert f'_attr_translation_key = "{key}"' in sources, key
            assert en[platform][key]["name"], key


def test_italian_texts():
    it = _load("translations/it.json")["entity"]
    assert it["binary_sensor"]["flame"]["state"] == {"on": "Accesa", "off": "Spenta"}
    assert it["binary_sensor"]["dhw_tap"]["state"] == {"on": "In corso", "off": "Nessun prelievo"}
    assert it["sensor"]["battery_status"]["state"] == {"low": "Bassa", "good": "Buona", "unknown": "Sconosciuta"}
    assert it["climate"]["thermostat"]["name"] == "Termostato {room}"

