"""0.10.1: climate and water_heater use the native_* temperature API.

Home Assistant 2026.11 deprecates current_temperature, target_temperature
and temperature_unit (they stop working in 2027.11). The expected values
below were recorded on 0.10.0 with Home Assistant 2026.10 (old API): the
switch must not change anything the user sees or what is sent to the cloud.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from custom_components.besmart_thermostat.climate import Thermostat
from custom_components.besmart_thermostat.water_heater import WaterHeater

from .test_setup_and_flows import _custom_integrations, cloud, v2_entry  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

THERMOSTAT = {
    "mode": "1",
    "season": "1",
    "current_temp": "20.56",
    "target_temp": "21.3",
    "comfort_temp": "21.3",
    "economy_temp": "18.1",
    "frost_temp": "7.0",
    "heating_status": "1",
}
BOILER = {
    "work_mode": "0",
    "dhw_target_temp": "48",
    "dhw_current_temp": "46.5",
    "flame_status": "0",
}

# Recorded on 0.10.0 / Home Assistant 2026.10.1 (metric system). With unit
# "1" the cloud values are Fahrenheit and Home Assistant converts them.
EXPECTED = {
    "0": {
        "climate.living_thermostat": {
            "current_temperature": 20.6,
            "temperature": 21.3,
            "min_temp": 18.3,
            "max_temp": 35.0,
            "target_temp_step": 0.2,
            "updating_temp": False,
        },
        "water_heater.casa_water_heater": {
            "current_temperature": 46,
            "temperature": 48,
            "min_temp": 30,
            "max_temp": 60,
            "target_temp_step": 1.0,
        },
    },
    "1": {
        "climate.living_thermostat": {
            "current_temperature": -6.4,
            "temperature": -5.9,
            "min_temp": -7.6,
            "max_temp": 1.7,
            "target_temp_step": 0.2,
            "updating_temp": False,
        },
        "water_heater.casa_water_heater": {
            "current_temperature": 8,
            "temperature": 9,
            "min_temp": -1,
            "max_temp": 16,
            "target_temp_step": 1.0,
        },
    },
}

DEPRECATED = ("current_temperature", "target_temperature", "temperature_unit")


async def _setup(hass, cloud, unit="0"):
    cloud.set_route("Thermostats/data", {"status": True, "message": {**THERMOSTAT, "unit": unit}})
    cloud.set_route("Boilers/data", {"status": True, "message": {**BOILER, "unit": unit}})
    entry = v2_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize("unit", ["0", "1"])
async def test_temperatures_unchanged(hass, cloud, unit):
    await _setup(hass, cloud, unit)
    for entity_id, expected in EXPECTED[unit].items():
        attrs = hass.states.get(entity_id).attributes
        assert {key: attrs.get(key) for key in expected} == expected, entity_id


async def test_set_temperature_sends_the_same_command(hass, cloud):
    await _setup(hass, cloud)
    cloud.set_route("Thermostats/temperature", {"error_code": 0, "status": True})
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.living_thermostat", "temperature": 22.4},
        blocking=True,
    )
    await hass.services.async_call(
        "water_heater",
        "set_temperature",
        {"entity_id": "water_heater.casa_water_heater", "temperature": 50},
        blocking=True,
    )
    sent = [
        (url.split("/api/")[1], {k: v for k, v in data.items() if k not in ("user_id", "id", "token", "wifi_box_id")})
        for url, data in cloud.puts
    ]
    assert sent == [
        (
            "Android/Thermostats/temperature",
            {"fraction_part": 4, "integer_part": 22, "temp_mode": "2", "thermostat_id": "r1"},
        ),
        ("Android/Boilers/dhw_target_temp", {"temp": 50}),
    ]


@pytest.mark.parametrize("cls", [Thermostat, WaterHeater])
def test_no_deprecated_temperature_members(cls):
    defined = vars(cls)
    for name in DEPRECATED:
        assert name not in defined, name
        assert f"_attr_{name}" not in defined, name
        assert f"native_{name}" in defined, name


def test_no_deprecation_warning_on_import():
    """Home Assistant logs the warning once, when the entity class is defined.

    That happens at import time, usually before any test captures the log,
    so the platforms are imported in a fresh interpreter.
    """
    code = (
        "import logging, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "logging.basicConfig(level=logging.WARNING, stream=sys.stdout)\n"
        "import custom_components.besmart_thermostat.climate\n"
        "import custom_components.besmart_thermostat.water_heater\n"
        "print('imported')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "imported" in result.stdout, output
    assert "deprecated" not in output, output
