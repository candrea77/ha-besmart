"""Unit tests for the cloud value parser and the log redaction (no network)."""

from unittest.mock import patch

import pytest

from custom_components.besmart_thermostat import api
from custom_components.besmart_thermostat.api import BesmartClient, parse_value

TOKEN = BesmartClient.TOKEN
USERNAME = "mario.rossi@example.com"
PASSWORD = "p&ss w0rd=+%#"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.0", 0.0),
        ("0", 0.0),
        (0, 0.0),
        ("37.5", 37.5),
        (" 12 ", 12.0),
        ("-3.5", -3.5),
        ("", None),
        ("   ", None),
        (None, None),
        ("null", None),
        ("NULL", None),
        ("DNCON", None),
        ("dncon", None),
        ("disable", None),
        ("Sensor DISABLED", None),
        ("abc", None),
    ],
)
def test_parse_value(raw, expected):
    assert parse_value(raw) == expected


def test_parse_value_zero_is_not_unknown():
    assert parse_value("0.0") is not None


@pytest.fixture
def client():
    with patch.object(api, "async_get_clientsession", return_value=None):
        c = BesmartClient(None, USERNAME, PASSWORD, True)
    c._user = {"id": "4242"}
    return c


def test_redact_token_and_user_id_in_url(client):
    msg = (
        "500, message='Internal Server Error', url='https://api.besmart-home.com/"
        "BeSMART_release/v1/api/Android/Boilers/modulation_ratio/user_id/4242/"
        f"wifi_box_id/77/token/{TOKEN}'"
    )
    out = client._redact(msg)
    assert TOKEN not in out
    assert "4242" not in out
    assert "user_id/***" in out
    assert "wifi_box_id/77" in out


def test_redact_user_id_query_param(client):
    out = client._redact("bad request user_id=4242&thermostat_id=9")
    assert "4242" not in out
    assert "thermostat_id=9" in out


def test_redact_credentials_plain_and_url_encoded(client):
    from urllib.parse import quote

    msg = (
        f"login?username={quote(USERNAME, safe='')}&password={quote(PASSWORD, safe='')} "
        f"{USERNAME} {PASSWORD}"
    )
    out = client._redact(msg)
    assert USERNAME not in out and quote(USERNAME, safe="") not in out
    assert PASSWORD not in out and quote(PASSWORD, safe="") not in out


def test_redact_leaves_unrelated_text_alone(client):
    # The user id is matched by position, never by value: digits elsewhere stay.
    msg = "timeout after 4242 ms on 2024-04-24"
    assert client._redact(msg) == msg
