"""Security fixes in the API client (no Home Assistant instance needed)."""

import logging
from urllib.parse import quote

import aiohttp
import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.besmart_thermostat import api
from custom_components.besmart_thermostat.api import (
    MAX_RESPONSE_BYTES,
    BesmartClient,
    BesmartError,
    BesmartTLSError,
    BesmartValidationError,
)

from .conftest import LOGIN_OK, PASSWORD, USER_ID, USERNAME, WIFI_BOX, FakeResponse

TOKEN = BesmartClient.TOKEN
SECRETS = (PASSWORD, quote(PASSWORD, safe=""), USERNAME, quote(USERNAME, safe=""), TOKEN)

HTML_503 = FakeResponse("<html><body>503 Service Unavailable</body></html>", status=503)


def assert_no_secrets(text):
    for secret in SECRETS:
        assert secret not in text, secret
    assert f"user_id/{USER_ID}" not in text
    assert WIFI_BOX not in text


# ------------------------------------------------------------------ A1 login


@pytest.mark.parametrize(
    "route",
    [
        HTML_503,
        FakeResponse({"error_code": 1, "status": False}, status=500),
        aiohttp.ClientResponseError(None, (), status=502, message="Bad Gateway"),
        aiohttp.ClientConnectionError("connection reset"),
        TimeoutError("t"),
        FakeResponse({"status": True, "message": {}}),  # no user
    ],
)
async def test_login_errors_are_sanitized(make_client, caplog, route):
    client = make_client({"users/login_new": route}, logged_in=False)
    with caplog.at_level(logging.DEBUG), pytest.raises(BesmartError) as err:
        await client.login()
    assert_no_secrets(str(err.value))
    # Never chained: HA shows str(__cause__) when the message is empty.
    assert err.value.__cause__ is None
    assert err.value.__context__ is None or err.value.__suppress_context__
    assert_no_secrets(caplog.text)


async def test_login_tls_error(make_client):
    cert_error = aiohttp.ClientSSLError(
        aiohttp.client_reqrep.ConnectionKey("h", 443, True, None, None, None, None),
        OSError("certificate verify failed"),
    )
    client = make_client({"users/login_new": cert_error}, logged_in=False)
    with pytest.raises(BesmartTLSError) as err:
        await client.login()
    assert_no_secrets(str(err.value))


async def test_login_invalid_credentials(make_client):
    client = make_client({"users/login_new": {"error_code": 6, "status": False}}, logged_in=False)
    with pytest.raises(ConfigEntryAuthFailed):
        await client.login()


async def test_login_ok_learns_ids_without_logging_them(make_client, caplog):
    client = make_client({"users/login_new": LOGIN_OK}, logged_in=False)
    with caplog.at_level(logging.DEBUG):
        assert await client.login() == [WIFI_BOX]
    assert client.user_id == USER_ID
    assert USER_ID not in caplog.text and WIFI_BOX not in caplog.text


# --------------------------------------------------------- M1/M2 log redaction


async def test_payload_logs_are_redacted(make_client, caplog):
    payload = {
        "status": True,
        "message": {
            "id": "r1",
            "current_temp": "20.5",
            "user_id": USER_ID,
            "wifi_box_id": WIFI_BOX,
            "email": USERNAME,
            "note": f"box {WIFI_BOX} of user {USER_ID}",
        },
    }
    client = make_client({"Thermostats/data": payload})
    with caplog.at_level(logging.DEBUG):
        data = await client.thermostat(WIFI_BOX, "r1")
    assert data["current_temp"] == "20.5"
    assert_no_secrets(caplog.text)
    assert USER_ID not in caplog.text
    assert "***0456" in caplog.text  # masked box id, last 4 kept


async def test_sensitive_keys_scrubbed_even_with_unknown_values(make_client, caplog):
    payload = {
        "status": True,
        "message": {
            "current_temp": "20.5",
            "mac": "AA:BB:CC:DD:EE:FF",
            "serial_number": "SN-998877",
            "wifi_box_id": "777888999000",  # not learnt at login
            "user_id": "55555",
            "nested": [{"Email": "other@example.org"}],
        },
    }
    client = make_client({"Thermostats/data": payload})
    with caplog.at_level(logging.DEBUG):
        await client.thermostat(WIFI_BOX, "r1")
    for value in ("AA:BB:CC:DD:EE:FF", "SN-998877", "777888999000", "55555", "other@example.org"):
        assert value not in caplog.text, value
    assert "20.5" in caplog.text


def test_redact_masks_known_ids_but_not_other_numbers(make_client):
    client = make_client({})
    text = client._redact(f"user {USER_ID} box {WIFI_BOX} took 1{USER_ID} ms, {USER_ID}0 bytes")
    assert text == f"user *** box ***0456 took 1{USER_ID} ms, {USER_ID}0 bytes"


def test_mask_wifi_box():
    client_mask = api.mask_id(WIFI_BOX)
    assert client_mask == "***0456"


# --------------------------------------------------------------- B3 responses


async def test_oversized_response_is_rejected(make_client, caplog):
    big = FakeResponse(b"x" * (MAX_RESPONSE_BYTES + 10))
    client = make_client({"Boilers/data": big})
    assert await client.boiler(WIFI_BOX) is None
    assert big.released
    assert "response too large" in caplog.text


async def test_declared_oversized_response_is_not_read(make_client):
    big = FakeResponse(
        {"status": True, "message": {"flame_status": "1"}},
        content_length=MAX_RESPONSE_BYTES + 1,
    )
    client = make_client({"Boilers/data": big})
    assert await client.boiler(WIFI_BOX) is None
    assert big.content.reads == 0 and big.released


@pytest.mark.parametrize(
    "payload",
    [{"status": True, "message": "oops"}, {"status": True, "message": [1, 2]}, [1, 2], "not json"],
)
async def test_malformed_messages_return_none(make_client, payload):
    client = make_client({"Thermostats/data": payload, "Boilers/data": payload})
    assert await client.thermostat(WIFI_BOX, "r1") is None
    client._user = {"id": USER_ID}
    assert await client.boiler(WIFI_BOX) is None


# -------------------------------------------------------------- B6 user race


async def test_logged_out_concurrently_is_a_clean_failure(make_client, caplog):
    client = make_client({"users/login_new": HTML_503})
    client._user = None  # e.g. reset by another coroutine
    assert await client.boiler(WIFI_BOX) is None
    assert "AttributeError" not in caplog.text


# ------------------------------------------------------------------ writes

WRITE_OK = {"error_code": 0, "status": True, "message": "ok"}


@pytest.mark.parametrize(
    ("temp", "integer", "fraction"),
    [(20.96, 21, 0), (20.94, 20, 9), (20.5, 20, 5), (3, 3, 0), (35, 35, 0), ("21.25", 21, 3)],
)
async def test_thermostat_temp_rounding(make_client, temp, integer, fraction):
    """B1: 20.96 used to be sent as 20 + 10 tenths."""
    client = make_client({"Thermostats/temperature": WRITE_OK})
    assert await client.setThermostatTemp(WIFI_BOX, "r1", temp, "2") is True
    (_, form), = client._session.puts
    assert (form["integer_part"], form["fraction_part"]) == (integer, fraction)


@pytest.mark.parametrize(
    ("temp", "mode"),
    [(2.9, "2"), (35.1, "2"), (float("nan"), "2"), (float("inf"), "2"), ("abc", "2"), (None, "2"), (20, "3"), (20, None)],
)
async def test_thermostat_temp_validation(make_client, temp, mode):
    client = make_client({"Thermostats/temperature": WRITE_OK})
    with pytest.raises(BesmartValidationError):
        await client.setThermostatTemp(WIFI_BOX, "r1", temp, mode)
    assert client._session.puts == []


@pytest.mark.parametrize("mode", [-1, 6, 7, "x", None])
async def test_thermostat_mode_validation(make_client, mode):
    """B2."""
    client = make_client({"Thermostats/mode": WRITE_OK})
    with pytest.raises(BesmartValidationError):
        await client.setThermostatMode(WIFI_BOX, "r1", mode)
    assert client._session.puts == []


async def test_thermostat_mode_ok(make_client):
    client = make_client({"Thermostats/mode": WRITE_OK})
    assert await client.setThermostatMode(WIFI_BOX, "r1", 5) is True
    (_, form), = client._session.puts
    assert form["mode"] == 5 and form["user_id"] == USER_ID and form["token"] == TOKEN


@pytest.mark.parametrize("temp", [29, 61, 45.5, float("nan"), "x", None])
async def test_dhw_temp_validation(make_client, temp):
    """M4: Home Assistant does not range-check water heater targets."""
    client = make_client({"Boilers/dhw_target_temp": WRITE_OK})
    with pytest.raises(BesmartValidationError):
        await client.setBoilerTemp(WIFI_BOX, temp)
    assert client._session.puts == []


@pytest.mark.parametrize("temp", [30, 45.0, 60])
async def test_dhw_temp_ok(make_client, temp):
    client = make_client({"Boilers/dhw_target_temp": WRITE_OK})
    assert await client.setBoilerTemp(WIFI_BOX, temp) is True
    assert client._session.puts[0][1]["temp"] == int(temp)


@pytest.mark.parametrize("mode", ["3", 3, -1, None])
async def test_boiler_mode_validation(make_client, mode):
    client = make_client({"Boilers/work_mode": WRITE_OK})
    with pytest.raises(BesmartValidationError):
        await client.setBoilerMode(WIFI_BOX, mode)


SETTINGS_OK = {
    "status": True,
    "message": {
        "unit": "0",
        "min_heating_set_point": "25",
        "max_heating_set_point": "80",
        "sensor_influence": "0",
        "climatic_curve": "1.5",
    },
}


async def test_season_writes_validated_settings(make_client):
    client = make_client({"thermostats/setting": SETTINGS_OK, "Thermostats/setting": WRITE_OK})
    assert await client.setThermostatSeason(WIFI_BOX, "r1", "0") is True
    (_, form), = client._session.puts
    assert form["season"] == "0" and form["climatic_curve"] == "1.5"


@pytest.mark.parametrize(
    "broken",
    [{"climatic_curve": None}, {"min_heating_set_point": ""}, {"unit": "7"}, {"sensor_influence": "DNCON"}],
)
async def test_season_with_incomplete_settings_sends_nothing(make_client, broken):
    """M5."""
    settings = {"status": True, "message": {**SETTINGS_OK["message"], **broken}}
    client = make_client({"thermostats/setting": settings, "Thermostats/setting": WRITE_OK})
    assert await client.setThermostatSeason(WIFI_BOX, "r1", "1") is False
    assert client._session.puts == []


async def test_season_validation(make_client):
    client = make_client({})
    with pytest.raises(BesmartValidationError):
        await client.setThermostatSeason(WIFI_BOX, "r1", "2")


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"error_code": 0, "status": True}, True),
        ({"error_code": "0", "status": True}, True),
        ({"status": True}, True),
        ({"error_code": 0}, True),  # status missing -> success (debug log)
        ({"message": "done"}, True),
        ("plain text ok", True),
        (None, True),
        ({"status": False}, False),
        ({"error_code": 3, "status": True}, False),
        ({"error_code": "12"}, False),
        (FakeResponse({"status": True}, status=500), False),
    ],
)
async def test_write_result_rules(make_client, response, expected):
    """M6: failure only on status false or error_code present and != 0."""
    client = make_client({"Boilers/work_mode": response if response is not None else FakeResponse(None)})
    assert await client.setBoilerMode(WIFI_BOX, "1") is expected


async def test_write_missing_status_logged_redacted(make_client, caplog):
    client = make_client({"Boilers/work_mode": {"echo": {"user_id": USER_ID, "wifi_box_id": WIFI_BOX}}})
    with caplog.at_level(logging.DEBUG):
        assert await client.setBoilerMode(WIFI_BOX, "1") is True
    assert "no status in response, assuming success" in caplog.text
    assert_no_secrets(caplog.text)
    assert USER_ID not in caplog.text


async def test_write_error_log_redacted(make_client, caplog):
    client = make_client({"Boilers/work_mode": aiohttp.ClientConnectionError("boom")})
    assert await client.setBoilerMode(WIFI_BOX, "1") is False
    assert_no_secrets(caplog.text)


# ------------------------------------------------------------ B5 / CLAUDE.md


def test_no_forbidden_or_unused_write_endpoints():
    endpoints = [v for k, v in vars(BesmartClient).items() if k.isupper() and isinstance(v, str)]
    assert not any("TSP" in e or "reset" in e.lower() for e in endpoints)
    for unused in ("program_196", "advance", "holiday_end_time", "thermostats/program"):
        assert not any(unused in e for e in endpoints), unused


# ------------------------------------------- thermostat ids / program summary

THERMOSTAT_ID = "029410DD"
PROGRAM = [["2"] * 48 for _ in range(7)]


async def test_thermostat_ids_masked_and_program_summarized(make_client, caplog):
    """b3 feedback: thermostat ids were logged in clear, program in full."""
    client = make_client(
        {
            "Wifi_boxes/data": {
                "status": True,
                "message": {
                    "boiler": {"id": "B0099887"},
                    "thermostat": [{"id": THERMOSTAT_ID, "name": "Living", "program": PROGRAM}],
                },
            },
            "Thermostats/data": {
                "status": True,
                "message": {
                    "id": THERMOSTAT_ID,
                    "thermostat_id": THERMOSTAT_ID,
                    "current_temp": "20.5",
                    "program": PROGRAM,
                    "note": f"room {THERMOSTAT_ID}",
                },
            },
        }
    )
    with caplog.at_level(logging.DEBUG):
        devices = await client.devices(WIFI_BOX)
        data = await client.thermostat(WIFI_BOX, THERMOSTAT_ID)

    # The returned data is untouched; only the logs are redacted.
    assert devices["thermostats"][0]["id"] == THERMOSTAT_ID
    assert data["program"] == PROGRAM

    assert THERMOSTAT_ID not in caplog.text
    assert "B0099887" not in caplog.text
    assert "***10DD" in caplog.text and "***9887" in caplog.text
    assert "7 days x 48 slots" in caplog.text
    assert "'2', '2', '2'" not in caplog.text
    assert "20.5" in caplog.text


def test_short_ids_masked_by_key_but_not_in_free_text(make_client):
    """A short room id like "1" must not turn every "1" in the logs into ***1."""
    client = make_client({})
    text = client._redact_payload({"id": "1", "mode": "1", "season": "1"})
    assert text == "{'id': '***1', 'mode': '1', 'season': '1'}"
