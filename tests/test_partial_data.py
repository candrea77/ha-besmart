"""b5: partial thermostat reads (set points at 0) and technical timeout."""

import logging

import pytest

from custom_components.besmart_thermostat.api import BesmartClient

from .conftest import WIFI_BOX, FakeResponse

ROOM = "029410DD"

# Values reported by a real installation (b4 test).
COMPLETE = {
    "status": True,
    "message": {
        "id": ROOM,
        "current_temp": "20.4",
        "target_temp": "21.0",
        "comfort_temp": "21.0",
        "economy_temp": "18.0",
        "frost_temp": "10.0",
        "max_heating_set_point": "35.0",
        "min_heating_set_point": "24.0",
        "sensor_influence": "10",
        "climatic_curve": "0.6",
        "cmd_stand_status": "0",
    },
}
PARTIAL = {
    "status": True,
    "message": {
        "id": ROOM,
        "current_temp": "20.6",
        "target_temp": "21.0",
        "comfort_temp": "0.0",
        "economy_temp": "0.0",
        "frost_temp": "0.0",
        "max_heating_set_point": "0.0",
        "min_heating_set_point": "0.0",
        "sensor_influence": "0",
        "climatic_curve": "0.0",
        "cmd_stand_status": "1",
    },
}
COMPLETE_AFTER = {
    "status": True,
    "message": {**COMPLETE["message"], "current_temp": "20.8", "comfort_temp": "21.5"},
}
SETPOINTS = {
    "comfort_temp": "21.0",
    "economy_temp": "18.0",
    "frost_temp": "10.0",
    "max_heating_set_point": "35.0",
    "min_heating_set_point": "24.0",
    "sensor_influence": "10",
    "climatic_curve": "0.6",
}


def coordinator_for(make_coordinator, thermostat_reads):
    co = make_coordinator(
        {
            "Boilers/data": {"status": True, "message": {"flame_status": "0"}},
            "Thermostats/data": thermostat_reads,
            "modulation_ratio": {"status": True, "message": {"key": "modulation_ratio", "value": "0.0"}},
        }
    )
    co.interface_devices[0].thermostats = [{"id": ROOM}]
    return co


async def refresh(co):
    co.data = await co._async_update_data()
    return co.data[WIFI_BOX]["thermostats"][ROOM]


async def test_partial_read_keeps_last_valid_set_points(make_coordinator, caplog):
    co = coordinator_for(make_coordinator, [COMPLETE, PARTIAL, COMPLETE_AFTER])

    first = await refresh(co)
    assert {k: first[k] for k in SETPOINTS} == SETPOINTS

    with caplog.at_level(logging.DEBUG):
        partial = await refresh(co)
    # Set points: last valid values, not 0.
    assert {k: partial[k] for k in SETPOINTS} == SETPOINTS
    # Live values from the partial read are still used.
    assert partial["current_temp"] == "20.6"
    assert partial["target_temp"] == "21.0"
    assert "partial thermostat data for ***10DD" in caplog.text
    assert ROOM not in caplog.text

    after = await refresh(co)
    assert after["current_temp"] == "20.8"
    assert after["comfort_temp"] == "21.5"  # new valid value accepted


async def test_partial_read_without_history_drops_zero_fields(make_coordinator):
    co = coordinator_for(make_coordinator, [PARTIAL])
    data = await refresh(co)
    for field in ("comfort_temp", "economy_temp", "frost_temp", "climatic_curve",
                  "min_heating_set_point", "max_heating_set_point", "sensor_influence"):
        assert field not in data
    assert data["current_temp"] == "20.6"


async def test_cmd_stand_status_alone_is_not_partial(make_coordinator, caplog):
    """cmd_stand_status "1" is only a hint: valid values are accepted."""
    stand_by_valid = {"status": True, "message": {**COMPLETE["message"], "cmd_stand_status": "1", "comfort_temp": "22.0"}}
    co = coordinator_for(make_coordinator, [COMPLETE, stand_by_valid])
    await refresh(co)
    with caplog.at_level(logging.DEBUG):
        data = await refresh(co)
    assert data["comfort_temp"] == "22.0"
    assert "partial thermostat data" not in caplog.text


async def test_sensor_influence_zero_is_valid_on_complete_reads(make_coordinator):
    no_influence = {"status": True, "message": {**COMPLETE["message"], "sensor_influence": "0"}}
    co = coordinator_for(make_coordinator, [COMPLETE, no_influence])
    await refresh(co)
    data = await refresh(co)
    assert data["sensor_influence"] == "0"


async def test_single_zero_set_point_is_replaced(make_coordinator):
    """Not only the all-zero case: any 0 set point is not a real value."""
    one_zero = {"status": True, "message": {**COMPLETE["message"], "frost_temp": "0.0"}}
    co = coordinator_for(make_coordinator, [COMPLETE, one_zero])
    await refresh(co)
    data = await refresh(co)
    assert data["frost_temp"] == "10.0"


# ------------------------------------------------------- season write guard

SETTINGS = {k: v for k, v in COMPLETE["message"].items() if k not in ("id",)}
SETTINGS["unit"] = "0"
WRITE_OK = {"error_code": 0, "status": True}


@pytest.mark.parametrize(
    "override",
    [
        # partial read: zeros
        {"min_heating_set_point": "0.0"},
        {"max_heating_set_point": "0.0"},
        {"climatic_curve": "0.0"},
        {**{k: "0.0" for k in ("min_heating_set_point", "max_heating_set_point", "climatic_curve")}, "sensor_influence": "0", "cmd_stand_status": "1"},
        # official app limits (Setting.java)
        {"max_heating_set_point": "14.9"},
        {"max_heating_set_point": "80.5"},
        {"min_heating_set_point": "9.9"},
        {"min_heating_set_point": "75.5", "max_heating_set_point": "80"},
        {"min_heating_set_point": "40", "max_heating_set_point": "35"},  # min > max
        {"climatic_curve": "0.1"},
        {"climatic_curve": "0.3"},  # in range but not an app value
        {"climatic_curve": "1.4"},
        {"climatic_curve": "3.5"},
        {"sensor_influence": "21"},
        {"sensor_influence": "-1"},
    ],
)
async def test_season_refuses_partial_or_out_of_range_settings(make_client, caplog, override):
    client = make_client(
        {"thermostats/setting": {"status": True, "message": {**SETTINGS, **override}}, "Thermostats/setting": WRITE_OK}
    )
    assert await client.setThermostatSeason(WIFI_BOX, ROOM, "1") is False
    assert client._session.puts == []
    assert ROOM not in caplog.text


@pytest.mark.parametrize(
    "override",
    [
        {"max_heating_set_point": "15", "min_heating_set_point": "10"},  # lower limits
        {"max_heating_set_point": "80", "min_heating_set_point": "75"},  # upper limits
        {"max_heating_set_point": "35", "min_heating_set_point": "35"},  # min == max
        {"climatic_curve": "0.2"},
        {"climatic_curve": "1.5"},
        {"climatic_curve": "3.0"},
        {"climatic_curve": "2"},
        {"sensor_influence": "0"},
        {"sensor_influence": "20"},
        {"cmd_stand_status": "1"},  # 0.8: complete read, still synchronising
    ],
)
async def test_season_accepts_app_limits(make_client, override):
    client = make_client(
        {"thermostats/setting": {"status": True, "message": {**SETTINGS, **override}}, "Thermostats/setting": WRITE_OK}
    )
    assert await client.setThermostatSeason(WIFI_BOX, ROOM, "1") is True
    assert len(client._session.puts) == 1


@pytest.mark.parametrize(
    ("override", "kept"),
    [
        ({"max_heating_set_point": "85"}, {"max_heating_set_point": "35.0"}),
        ({"min_heating_set_point": "8"}, {"min_heating_set_point": "24.0"}),
        ({"min_heating_set_point": "40"}, {"min_heating_set_point": "24.0", "max_heating_set_point": "35.0"}),
        ({"climatic_curve": "0.7"}, {"climatic_curve": "0.6"}),
        ({"sensor_influence": "25"}, {"sensor_influence": "10"}),
    ],
)
async def test_out_of_range_reads_keep_last_valid(make_coordinator, override, kept):
    """Readings outside the app limits are treated like the partial zeros."""
    bad = {"status": True, "message": {**COMPLETE["message"], **override}}
    co = coordinator_for(make_coordinator, [COMPLETE, bad])
    await refresh(co)
    data = await refresh(co)
    for field, value in kept.items():
        assert data[field] == value, field


async def test_in_range_reads_are_accepted(make_coordinator):
    new = {"status": True, "message": {**COMPLETE["message"], "max_heating_set_point": "80", "min_heating_set_point": "10", "climatic_curve": "3.0", "sensor_influence": "20"}}
    co = coordinator_for(make_coordinator, [COMPLETE, new])
    await refresh(co)
    data = await refresh(co)
    assert (data["max_heating_set_point"], data["min_heating_set_point"], data["climatic_curve"], data["sensor_influence"]) == ("80", "10", "3.0", "20")


async def test_season_with_valid_settings_is_sent(make_client):
    client = make_client({"thermostats/setting": {"status": True, "message": SETTINGS}, "Thermostats/setting": WRITE_OK})
    assert await client.setThermostatSeason(WIFI_BOX, ROOM, "0") is True
    (_, form), = client._session.puts
    assert form["min_heating_set_point"] == "24.0" and form["climatic_curve"] == "0.6"


# ------------------------------------------------------- technical timeout


async def test_technical_endpoints_use_10s_timeout(make_client):
    assert BesmartClient.TECHNICAL_TIMEOUT == 10
    client = make_client({"/Boilers/": {"status": True, "message": {"value": "1"}}})
    seen = []
    original = client._request

    async def spy(method, url, **kwargs):
        seen.append(kwargs.get("timeout"))
        return await original(method, url, **kwargs)

    client._request = spy
    values = await client.boiler_technical(WIFI_BOX)
    assert len(values) == len(BesmartClient.TECHNICAL_ENDPOINTS)
    assert seen and set(seen) == {10}


async def test_technical_timeout_then_success_on_second_attempt(make_client):
    """dhw_flow_rate / heat_exchanger_hours_amassed: timeout, then OK (retries unchanged)."""
    ok = {"status": True, "message": {"value": "3"}}
    routes = {
        "dhw_flow_rate": [TimeoutError("t"), ok],
        "heat_exchanger_hours_amassed": [TimeoutError("t"), ok],
        "/Boilers/": ok,
    }
    client = make_client(routes)
    values = await client.boiler_technical(WIFI_BOX)
    assert values["dhw_flow_rate"] == "3" and values["heat_exchanger_hours_amassed"] == "3"
    assert client._session.count("dhw_flow_rate") == 2
    assert BesmartClient.TECHNICAL_ATTEMPTS == 3


async def test_sensor_influence_zero_kept_when_only_stand_by(make_coordinator):
    """cmd_stand_status "1" with all set points valid: sensor_influence 0 is real."""
    stand_by = {"status": True, "message": {**COMPLETE["message"], "cmd_stand_status": "1", "sensor_influence": "0"}}
    co = coordinator_for(make_coordinator, [COMPLETE, stand_by])
    await refresh(co)
    data = await refresh(co)
    assert data["sensor_influence"] == "0"
