"""Shared typed structures for the BeSMART integration."""

from __future__ import annotations

from typing import TypedDict


class Devices(TypedDict):
    """Payload returned by BesmartClient.devices()."""

    # PATCH 0.5: boiler can legitimately be None (thermostat-only setups);
    # removed the unused/incorrect WifiBox TypedDict (wifi box ids are plain
    # strings everywhere in the code).
    boiler: dict | None
    thermostats: list[dict]
