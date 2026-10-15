"""Boiler binary sensors read from the Boilers/data payload."""

import logging
import types

import pytest

from custom_components.besmart_thermostat.binary_sensor import (
    BoilerCirculatorBinarySensor,
    BoilerDhwTapBinarySensor,
    BoilerFlameBinarySensor,
)

from .conftest import WIFI_BOX

# Example Boilers/data message during a DHW draw with the burner lit.
BOILER_DATA_FLAME_ON = {
    "flame_status": "1",
    "heating_status": "0",
    "dhw_active": "1",
    "system_pressure": "1.4",
    "heating_current_temp": "58.0",
    "unit": "0",
}


def is_on(cls, payload):
    coordinator = types.SimpleNamespace(boiler_data=lambda box: payload)
    entity = types.SimpleNamespace(coordinator=coordinator, _wifi_box=WIFI_BOX, DATA_KEY=cls.DATA_KEY)
    return cls.is_on.fget(entity)


def test_flame_on_from_boiler_data():
    assert is_on(BoilerFlameBinarySensor, BOILER_DATA_FLAME_ON) is True
    assert is_on(BoilerDhwTapBinarySensor, BOILER_DATA_FLAME_ON) is True
    assert is_on(BoilerCirculatorBinarySensor, BOILER_DATA_FLAME_ON) is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), (1, True), ("1.0", True), ("0", False), (0, False), ("0.0", False),
     (None, None), ("", None), ("DNCON", None)],
)
def test_flame_parsing(raw, expected):
    assert is_on(BoilerFlameBinarySensor, {"flame_status": raw}) is expected


def test_boiler_state_fields_logged_redacted(make_client, caplog):
    import asyncio

    from custom_components.besmart_thermostat.api import BesmartClient

    client = make_client({"Boilers/data": {"status": True, "message": BOILER_DATA_FLAME_ON}})
    with caplog.at_level(logging.DEBUG):
        asyncio.run(client.boiler(WIFI_BOX))
    line = next(r.getMessage() for r in caplog.records if "boiler state fields" in r.getMessage())
    assert "'flame_status': '1'" in line and "'dhw_active': '1'" in line
    assert "'heating_status': '0'" in line
    assert "system_pressure" not in line
    assert BesmartClient.TOKEN not in caplog.text
