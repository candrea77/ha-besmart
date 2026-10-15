"""Shared fixtures: make ``custom_components`` importable and fake the cloud."""

import asyncio
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

USER_ID = "4242"
WIFI_BOX = "100200300456"


class FakeResponse:
    """Minimal aiohttp-like response."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status
        self.ok = status < 400

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        raise RuntimeError(f"{self.status}, url='{self.url}'")


class FakeSession:
    """Routes GETs by URL substring.

    A route value is a payload dict, a FakeResponse, an Exception instance, or
    a list of those consumed one per call (the last one repeats).
    """

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def get(self, url):
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
                        raise type(value)(f"{value} for url {url}")
                    response = value if isinstance(value, FakeResponse) else FakeResponse(value)
                    response.url = url
                    return response
            raise AssertionError(f"unexpected url {url}")
        finally:
            self.in_flight -= 1

    def count(self, key):
        return sum(key in url for url in self.calls)


def ok(value, key="x"):
    """Single-value endpoint payload."""
    return {"error_code": 0, "status": True, "message": {"key": key, "value": value}}


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setattr(BesmartClient, "TECHNICAL_RETRY_DELAY", 0)

    def _make(routes):
        with patch.object(api, "async_get_clientsession", return_value=FakeSession(routes)):
            client = BesmartClient(None, "mario.rossi@example.com", "p&ss w0rd", True)
        client._user = {"id": USER_ID}
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
