"""On-demand technical data (single endpoints) and the "Refresh now" logic."""

import asyncio
import logging

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.besmart_thermostat import coordinator as coordinator_module
from custom_components.besmart_thermostat.api import BesmartClient
from custom_components.besmart_thermostat.sensor import (
    TECHNICAL_ATTRIBUTE_NAMES,
    technical_attributes,
)

from .conftest import USER_ID, WIFI_BOX, FakeResponse, ok

ENDPOINTS = BesmartClient.TECHNICAL_ENDPOINTS
TOKEN = BesmartClient.TOKEN

ALL_OK = {
    "system_pressure": ok("1.5"),
    "flow_temperature": ok("55.0"),
    "return_temperature": ok("DNCON"),
    "flues_temperature": ok("null"),
    "dhw_temperature": ok("48"),
    "dhw_set_point": ok("50"),
    "heating_set_point": ok("60"),
    "dhw_flow_rate": ok("disable"),
    "flame_status": ok("1"),
    "external_temperature": ok("-3.5"),
    "heat_exchanger_hours_amassed": ok("1234"),
    "worked_hours_in_heating": ok("5678"),
    "worked_hours_in_dhw": ok("90"),
    "/unit/": ok("0"),
}


def run(coro):
    return asyncio.run(coro)


def test_whitelist_is_read_only():
    assert "technical_datas" not in "".join(ENDPOINTS)
    assert not any("TSP" in e or "reset" in e.lower() for e in ENDPOINTS)
    assert not hasattr(BesmartClient, "GET_BOILER_TECHNICAL")


def test_all_endpoints_read_with_bounded_concurrency(make_client):
    client = make_client(dict(ALL_OK))
    values = run(client.boiler_technical(WIFI_BOX))
    assert set(values) == set(ENDPOINTS)
    assert values["flame_status"] == "1"
    assert client._session.max_in_flight <= BesmartClient.TECHNICAL_CONCURRENCY
    assert client._session.max_in_flight > 1


def test_retry_then_success(make_client):
    routes = dict(ALL_OK)
    routes["system_pressure"] = [TimeoutError("t"), FakeResponse({}, 503), ok("1.4")]
    client = make_client(routes)
    values = run(client.boiler_technical(WIFI_BOX))
    assert values["system_pressure"] == "1.4"
    assert client._session.count("system_pressure") == 3


def test_gives_up_after_3_attempts(make_client):
    routes = dict(ALL_OK)
    routes["flow_temperature"] = [TimeoutError("t")]
    client = make_client(routes)
    values = run(client.boiler_technical(WIFI_BOX))
    assert "flow_temperature" not in values
    assert client._session.count("flow_temperature") == 3
    assert client._user == {"id": USER_ID}


def test_404_not_retried_logged_once_and_skipped(make_client, caplog):
    routes = dict(ALL_OK)
    routes["dhw_flow_rate"] = FakeResponse({}, 404)
    client = make_client(routes)
    with caplog.at_level(logging.DEBUG):
        run(client.boiler_technical(WIFI_BOX))
        run(client.boiler_technical(WIFI_BOX))
    assert client._session.count("dhw_flow_rate") == 1
    assert client.unsupported_endpoints == {"dhw_flow_rate"}
    records = [r for r in caplog.records if "dhw_flow_rate" in r.getMessage()]
    assert len(records) == 1 and records[0].levelno == logging.DEBUG
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_logs_are_redacted(make_client, caplog):
    routes = dict(ALL_OK)
    routes["flow_temperature"] = [TimeoutError("t")]
    routes["system_pressure"] = FakeResponse({}, 400)
    client = make_client(routes)
    with caplog.at_level(logging.DEBUG):
        run(client.boiler_technical(WIFI_BOX))
    assert TOKEN not in caplog.text
    assert f"user_id/{USER_ID}" not in caplog.text
    assert WIFI_BOX not in caplog.text


def test_coordinator_partial_failure_and_attributes(make_coordinator):
    routes = dict(ALL_OK)
    routes["dhw_flow_rate"] = FakeResponse({}, 404)
    routes["flow_temperature"] = [TimeoutError("t")]
    co = make_coordinator(routes, data={WIFI_BOX: {"modulation": "0.0"}})
    run(co.async_read_technical(WIFI_BOX))
    reading = co.technical[WIFI_BOX]
    assert reading["unsupported"] == ["dhw_flow_rate"]
    attrs = technical_attributes(reading)
    assert set(attrs) == set(TECHNICAL_ATTRIBUTE_NAMES)
    assert attrs["modulation_pct"] == 0.0
    assert attrs["flow_temperature"] is None  # failed endpoint
    assert attrs["dhw_flow_rate"] is None  # unsupported
    assert attrs["return_temperature"] is None  # DNCON
    assert attrs["pressure_bar"] == 1.5
    assert attrs["heat_exchanger_hours"] == 1234.0
    assert attrs["heating_hours"] == 5678.0 and attrs["dhw_hours"] == 90.0
    assert attrs["outdoor_temperature"] == -3.5
    assert attrs["flame"] is True
    assert attrs["temperature_unit"] == "°C"
    assert attrs["unsupported_endpoints"] == ["dhw_flow_rate"]
    assert co.listener_updates == 1
    # the modulation is not re-read on button press
    assert co.client._session.count("modulation_ratio") == 0


def test_coordinator_error_only_when_all_fail(make_coordinator):
    co = make_coordinator({"/Boilers/": FakeResponse({}, 503)})
    with pytest.raises(HomeAssistantError):
        run(co.async_read_technical(WIFI_BOX))
    assert WIFI_BOX not in co.technical
    assert co.client._user == {"id": USER_ID}


def test_coordinator_failure_keeps_previous_reading(make_coordinator):
    co = make_coordinator(dict(ALL_OK))
    run(co.async_read_technical(WIFI_BOX))
    first = co.technical[WIFI_BOX]
    co.client._session.routes = {"/Boilers/": FakeResponse({}, 500)}
    with pytest.raises(HomeAssistantError):
        run(co.async_read_technical(WIFI_BOX))
    assert co.technical[WIFI_BOX] is first


def test_all_404_is_an_error(make_coordinator):
    co = make_coordinator({"/Boilers/": FakeResponse({}, 404)})
    with pytest.raises(HomeAssistantError):
        run(co.async_read_technical(WIFI_BOX))
    assert co.client.unsupported_endpoints == set(ENDPOINTS)


# ------------------------------------------------------------- Refresh now


def _with_fake_refresh(co, succeed=True, delay=0.01):
    co.refreshes = 0

    async def async_refresh():
        co.refreshes += 1
        await asyncio.sleep(delay)
        co.last_update_success = succeed
        co.last_exception = None if succeed else Exception("no data")

    co.async_refresh = async_refresh
    return co


def test_refresh_now_runs_once_for_concurrent_presses(make_coordinator):
    co = _with_fake_refresh(make_coordinator({}))

    async def presses():
        await asyncio.gather(co.async_manual_refresh(), co.async_manual_refresh())

    run(presses())
    assert co.refreshes == 1


def test_refresh_now_cooldown(make_coordinator, monkeypatch):
    co = _with_fake_refresh(make_coordinator({}))
    clock = [1000.0]
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: clock[0])
    run(co.async_manual_refresh())
    clock[0] += 5
    run(co.async_manual_refresh())  # within 10 s: ignored
    assert co.refreshes == 1
    clock[0] += 6
    run(co.async_manual_refresh())
    assert co.refreshes == 2


def test_refresh_now_failure_raises(make_coordinator):
    co = _with_fake_refresh(make_coordinator({}), succeed=False)
    with pytest.raises(HomeAssistantError):
        run(co.async_manual_refresh())


def test_refresh_now_does_not_read_technical_data(make_coordinator):
    routes = {
        "Boilers/data": {"status": True, "message": {"flame_status": "1"}},
        "Thermostats/data": {"status": True, "message": {"current_temp": "20"}},
        "modulation_ratio": ok("73.0", key="modulation_ratio"),
    }
    co = make_coordinator(routes)
    co.refreshes = 0

    async def async_refresh():  # the real refresh path, minus HA scheduling
        co.refreshes += 1
        co.data = await co._async_update_data()

    co.async_refresh = async_refresh
    run(co.async_manual_refresh())
    assert co.data[WIFI_BOX]["modulation"] == "73.0"
    assert not any(e in url for url in co.client._session.calls for e in ENDPOINTS if e != "flame_status")
    assert co.client._session.count("flame_status") == 0
