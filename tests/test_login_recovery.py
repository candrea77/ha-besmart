"""0.10.1: sporadic cloud errors no longer force a new login.

A transient error (HTTP 5xx, timeout, connection error) of a core read keeps
the login; HTTP 4xx and invalid responses reset it as before. Safety net: if
no core read succeeds in a whole cycle, the coordinator logs in again at the
next one, whatever the error was (candrea77/ha-besmart#16).
"""

import logging

import aiohttp
import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from .conftest import LOGIN_OK, PASSWORD, USER_ID, USERNAME, WIFI_BOX, FakeResponse

THERMOSTAT_OK = {"status": True, "message": {"mode": "1", "season": "1", "current_temp": "20.5"}}
BOILER_OK = {"status": True, "message": {"flame_status": "0"}}
MODULATION_OK = {"status": True, "message": {"key": "modulation_ratio", "value": "0.0"}}
HTTP_500 = FakeResponse({"error": "internal"}, status=500)


def coordinator(make_coordinator, boiler=BOILER_OK, thermostat=THERMOSTAT_OK, modulation=MODULATION_OK):
    return make_coordinator(
        {
            "users/login_new": LOGIN_OK,
            "Boilers/data": boiler,
            "Thermostats/data": thermostat,
            "modulation_ratio": modulation,
        }
    )


async def cycle(co):
    co.data = await co._async_update_data()
    return co.data


def logins(co):
    return co.client._session.count("users/login_new")


@pytest.mark.parametrize(
    "error",
    [HTTP_500, FakeResponse("", status=503), TimeoutError("t"), aiohttp.ClientConnectionError("reset")],
    ids=["500", "503", "timeout", "connection"],
)
@pytest.mark.parametrize("read", ["boiler", "thermostat"])
async def test_transient_error_keeps_the_login(make_coordinator, read, error):
    routes = {"boiler": [error, BOILER_OK]} if read == "boiler" else {"thermostat": [error, THERMOSTAT_OK]}
    co = coordinator(make_coordinator, **routes)
    await cycle(co)
    assert co.client.user_id == USER_ID
    await cycle(co)
    assert logins(co) == 0


@pytest.mark.parametrize(
    "error",
    [
        FakeResponse({"error": "unauthorized"}, status=401),
        FakeResponse({"error": "forbidden"}, status=403),
        FakeResponse("", status=404),
        FakeResponse("<html>not json</html>", status=200),
    ],
    ids=["401", "403", "404", "invalid"],
)
async def test_4xx_or_invalid_response_logs_in_again(make_coordinator, error):
    co = coordinator(make_coordinator, boiler=[error, BOILER_OK])
    await cycle(co)
    assert co.client.user_id is None
    data = await cycle(co)
    assert logins(co) == 1
    assert data[WIFI_BOX]["boiler"] == BOILER_OK["message"]


async def test_whole_cycle_failed_logs_in_again(make_coordinator):
    """Safety net: a session problem reported as HTTP 500 is still recovered."""
    co = coordinator(
        make_coordinator,
        boiler=[HTTP_500, BOILER_OK],
        thermostat=[HTTP_500, THERMOSTAT_OK],
    )
    co.data = {WIFI_BOX: {"boiler": {"flame_status": "1"}, "modulation": None, "thermostats": {}}}
    await cycle(co)  # previous data kept, no fresh read
    assert co.client.user_id is None
    data = await cycle(co)
    assert logins(co) == 1
    assert data[WIFI_BOX]["boiler"] == BOILER_OK["message"]


async def test_boiler_offline_with_thermostats_ok_never_logs_in(make_coordinator):
    """Boilers/data answers 500 while the boiler is offline (issue #16)."""
    co = coordinator(make_coordinator, boiler=HTTP_500)
    for _ in range(5):
        await cycle(co)
    assert logins(co) == 0
    assert co.client.user_id == USER_ID


async def test_modulation_error_never_resets_the_login(make_coordinator):
    co = coordinator(make_coordinator, modulation=FakeResponse("", status=404))
    await cycle(co)
    await cycle(co)
    assert logins(co) == 0


async def test_new_login_with_wrong_password_starts_reauth(make_coordinator):
    co = coordinator(make_coordinator, boiler=[FakeResponse("", status=403), BOILER_OK])
    co.client._session.routes["users/login_new"] = {"error_code": 6, "status": False}
    await cycle(co)
    with pytest.raises(ConfigEntryAuthFailed):
        await cycle(co)


async def test_no_secrets_in_the_logs(make_coordinator, caplog):
    caplog.set_level(logging.DEBUG, logger="custom_components.besmart_thermostat")
    co = coordinator(make_coordinator, boiler=HTTP_500, thermostat=HTTP_500)
    for _ in range(2):
        with pytest.raises(UpdateFailed):  # no data at all, no previous data
            await cycle(co)
    assert logins(co) == 1
    assert "logging in again next cycle" in caplog.text
    for secret in (USER_ID, WIFI_BOX, USERNAME, PASSWORD):
        assert secret not in caplog.text
