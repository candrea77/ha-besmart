"""Shared fixtures: make ``custom_components`` importable and fake the cloud.

Tests never contact the real BeSMART cloud.
"""

import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.besmart_thermostat import api  # noqa: E402
from custom_components.besmart_thermostat.api import BesmartClient  # noqa: E402
from custom_components.besmart_thermostat.coordinator import (  # noqa: E402
    BesmartDataUpdateCoordinator,
)

USERNAME = "mario.rossi@example.com"
PASSWORD = "p&ss w0rd"
# Long and distinctive, so it cannot appear by chance in random ids/timestamps
# that Home Assistant writes to the captured log.
USER_ID = "987650123"
WIFI_BOX = "100200300456"


class _Content:
    def __init__(self, raw: bytes):
        self._raw = raw

    reads = 0

    async def read(self, n=-1):
        self.reads += 1
        return self._raw if n < 0 else self._raw[:n]


class FakeResponse:
    """Minimal aiohttp-like response.

    ``payload`` may be a dict/list (sent as JSON), a str/bytes (sent raw,
    e.g. an HTML error page) or None (empty body).
    """

    def __init__(self, payload=None, status=200, content_length=None):
        if isinstance(payload, (dict, list)):
            raw = json.dumps(payload).encode()
        elif isinstance(payload, str):
            raw = payload.encode()
        else:
            raw = payload or b""
        self.content = _Content(raw)
        self.content_length = content_length
        self.status = status
        self.ok = status < 400
        self.released = False

    def release(self):
        self.released = True


class FakeSession:
    """Routes requests by URL substring.

    A route value is a payload (dict/str), a FakeResponse, an Exception
    instance (raised, with the URL in its message like aiohttp does), or a
    list of those consumed one per call (the last one repeats).
    """

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.puts = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def get(self, url):
        return await self._handle("GET", url)

    async def put(self, url, data=None):
        self.puts.append((url, data))
        return await self._handle("PUT", url)

    async def _handle(self, method, url):
        self.calls.append(url)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)  # yield like a real request
            for key, value in self.routes.items():
                if key in url:
                    if isinstance(value, list):
                        value = value.pop(0) if len(value) > 1 else value[0]
                    if isinstance(value, Exception):
                        try:
                            error = type(value)(f"{value}, url='{url}'")
                        except TypeError:  # exceptions with a custom signature
                            error = value
                        raise error
                    if isinstance(value, FakeResponse):
                        return value
                    return FakeResponse(value)
            raise AssertionError(f"unexpected {method} {url}")
        finally:
            self.in_flight -= 1

    def count(self, key):
        return sum(key in url for url in self.calls)


def ok(value, key="x"):
    """Single-value endpoint payload."""
    return {"error_code": 0, "status": True, "message": {"key": key, "value": value}}


LOGIN_OK = {
    "error_code": 0,
    "status": True,
    "message": {
        "user": {"id": USER_ID, "email": USERNAME},
        "wifi_box": [{"id": WIFI_BOX}],
    },
}


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setattr(BesmartClient, "TECHNICAL_RETRY_DELAY", 0)

    def _make(routes, logged_in=True):
        with patch.object(api, "async_get_clientsession", return_value=FakeSession(routes)):
            client = BesmartClient(None, USERNAME, PASSWORD, True)
        if logged_in:
            client._user = {"id": USER_ID}
            client._known_user_ids.add(USER_ID)
            client._known_wifi_boxes.add(WIFI_BOX)
        return client

    return _make


@pytest.fixture
def make_coordinator(make_client):
    """Coordinator without a running Home Assistant (no scheduling)."""

    def _make(routes, boiler=True, data=None):
        client = make_client(routes)

        async def ensure_login():
            return None

        client.ensure_login = ensure_login
        coordinator = BesmartDataUpdateCoordinator.__new__(BesmartDataUpdateCoordinator)
        coordinator.client = client
        coordinator.interface_devices = [
            types.SimpleNamespace(
                wifi_box=WIFI_BOX,
                boiler={"id": 1} if boiler else None,
                thermostats=[{"id": "r1"}],
            )
        ]
        coordinator.data = data
        coordinator.technical = {}
        coordinator._technical_locks = {}
        coordinator._last_technical_read = {}
        coordinator._last_valid_setpoints = {}
        coordinator._manual_refresh_lock = asyncio.Lock()
        coordinator._last_manual_refresh = None
        coordinator.last_update_success = True
        coordinator.last_exception = None
        coordinator.listener_updates = 0

        def update_listeners():
            coordinator.listener_updates += 1

        coordinator.async_update_listeners = update_listeners
        return coordinator

    return _make


# PATCH 0.7 (b8): Home Assistant reports deprecated usage through the
# homeassistant.helpers.frame logger (e.g. "has an update listener and should
# use it for scheduling a reload"): it is only a log line, so without this
# fixture such problems pass CI unnoticed. Any frame warning, or any warning
# record about deprecated usage by this integration, fails the test.
_DOMAIN = "besmart_thermostat"


@pytest.fixture(autouse=True)
def fail_on_ha_deprecations(caplog):
    yield
    offending = [
        record
        for phase in ("setup", "call", "teardown")
        for record in caplog.get_records(phase)
        if record.levelno >= 30  # WARNING
        and (
            record.name.startswith("homeassistant.helpers.frame")
            or (
                "deprecat" in record.getMessage().lower()
                and _DOMAIN in record.getMessage()
            )
        )
    ]
    if offending:
        pytest.fail(
            "Home Assistant reported deprecated usage:\n"
            + "\n".join(f"[{r.name}] {r.getMessage()}" for r in offending),
            pytrace=False,
        )
