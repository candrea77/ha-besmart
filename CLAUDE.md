# CLAUDE.md

Home Assistant custom integration for Riello BeSMART thermostats/boilers
(`custom_components/besmart_thermostat`), talking to the BeSMART cloud API
`https://api.besmart-home.com/BeSMART_release/v1/api/`.

## Hard rules (non-negotiable)

1. **Read-only cloud access for new features.** New code may only add `GET`
   endpoints that read data. The existing write commands (`PUT` thermostat
   mode/temperature/settings, boiler work mode, DHW target temperature) stay as
   they are; do not add new write endpoints without explicit approval from the
   maintainer.
2. **NEVER write to `Boilers/TSP`** (boiler technical/installer parameters) and
   **NEVER call `Boilers/reset`** — not in integration code, tests, scripts,
   or ad-hoc debugging. These can alter or lock out the boiler.
3. **Secrets never reach the logs.** The API token (`BesmartClient.TOKEN`), the
   user id, the username and the password are part of request URLs and
   therefore of aiohttp exception messages. Every log line or exception text
   that may contain a URL or a cloud payload must go through
   `BesmartClient._redact()`. Never log full URLs or the login payload.
   `diagnostics.py` must keep redacting `token`, `user_id`, `email` and the
   credentials.
4. **Optional endpoints must fail soft.** Extra reads (e.g.
   `Boilers/modulation_ratio`, `Boilers/technical_datas`) must never make the
   other entities unavailable, never fail the coordinator update on their own,
   and never reset `self._user` (which would force a re-login every cycle).

## Architecture

- `api.py` — `BesmartClient`, one method per endpoint. Read methods return
  `None` on error (logging a redacted warning); `ConfigEntryAuthFailed` always
  propagates so HA starts the reauth flow.
- `coordinator.py` — a single `DataUpdateCoordinator` per config entry fetches
  everything once per cycle (`asyncio.gather` per wifi box). Entities read only
  from `coordinator.data`; they never call the API while polling.
- Platforms: `climate`, `water_heater`, `sensor`, `binary_sensor`, `button`.
- Cloud responses look like `{"error_code": .., "status": true, "message": {...}}`.
  Invalid values: empty, `None`, `"null"`, `"DNCON"`, strings containing
  `disable`. Use `api.parse_value()`; `"0.0"` is a valid 0, not unknown.

## Conventions

- Comments mark changes with `PATCH x.y:`, matching the existing code.
- On every release bump `version` in
  `custom_components/besmart_thermostat/manifest.json` and update `info.md`
  (HACS release notes). Pre-releases use `X.Y.ZbN` and a `vX.Y.ZbN` tag
  published as a GitHub pre-release.
- Release branches are named `release-vX.Y`.
- CI (`.github/workflows/combined.yaml`) runs hassfest and HACS validation.
