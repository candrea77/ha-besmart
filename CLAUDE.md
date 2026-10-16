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
   `BesmartClient._redact()` (which also reduces the wifi box id to its last
   4 characters). Never log full URLs or the login payload.
   `diagnostics.py` must keep redacting `token`, `user_id`, `email` and the
   credentials, and pseudonymize wifi box ids (`box_1`...), also as dict keys.
   The cloud has no per-user token: **user_id + wifi_box_id are credentials**.
   Never show them in UI messages or device info (no `model_id`).
   Never chain cloud/aiohttp exceptions into Home Assistant exceptions
   (`ConfigEntryNotReady`, `UpdateFailed`, `HomeAssistantError`): HA shows
   `str(__cause__)` in the UI. Raise `BesmartError` / fixed messages
   `from None`; config/reauth/reconfigure flows return only error keys.
4. **Optional endpoints must fail soft.** Extra reads (e.g.
   `Boilers/modulation_ratio`, the single-value technical endpoints in
   `BesmartClient.TECHNICAL_ENDPOINTS`) must never make the other entities
   unavailable, never fail the coordinator update on their own, and never
   reset `self._user` (which would force a re-login every cycle).
   `Boilers/technical_datas` no longer exists (404): do not use it.
5. **Don't hammer the cloud.** No new polled calls without the maintainer's
   approval; technical endpoints are read only on button press (max 3
   concurrent, 5 s timeout, 3 attempts, no retry on 404). The cloud refreshes
   module data only every ~3 minutes.

## Architecture

- Config entry version 2: credentials in `entry.data`, everything else in
  `entry.options` (migration v1 -> v2 is irreversible). `unique_id` is a hash
  of the cloud user id, set at the first successful setup.
- Write commands validate their input in `api.py` (`BesmartValidationError`,
  turned into `ServiceValidationError` by the entities) before sending.
  A write fails only on HTTP error, `status: false` or `error_code` != 0.
- All HTTP goes through `BesmartClient._request()` (timeout, 512 KB body cap,
  sanitized exceptions).

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
- CI (`.github/workflows/combined.yaml`) runs hassfest, HACS validation and
  `pytest tests` on Python 3.14 against the minimum supported Home Assistant
  (2026.7, pinned in the workflow matrix) and the latest one
  (`requirements_test.txt`, bumped by Dependabot). Tests use
  pytest-homeassistant-custom-component (real `hass` fixture) and never hit
  the real cloud: use the fakes in `tests/conftest.py`. Actions are pinned to
  commit SHAs; the workflow token is read-only.
- No `country` key in `hacs.json`: the integration must be visible in every
  country.
