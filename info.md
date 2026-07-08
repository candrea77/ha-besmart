## Changes as compared to your installed version:

### Breaking Changes
- Home Assistant **2026.5.0 or newer** is required.
- The boiler values `system_pressure` and `outdoor_probe_temp` are now exposed as dedicated sensor entities. They are still available as attributes of the water heater entity, but dashboards/automations should migrate to the new sensors.
- The season/settings write command (`HEAT`/`COOL` switch) now targets the proper `Thermostats/setting` cloud endpoint instead of `Thermostats/temperature`. If season switching stops working on your device, please open an issue (a single-line rollback is documented in `api.py`).

### Changes
- Topology (wifi boxes / thermostats) is now owned by the data coordinator instead of being stored as a custom attribute on the config entry (pattern deprecated by Home Assistant).
- Boiler and thermostat data for each wifi box are fetched concurrently, shortening the update cycle.
- Failed write commands (set temperature, preset, season, boiler mode) now raise an error visible in the UI instead of failing silently.
- Cleaner logging: lazy formatting everywhere, no full login payload dumped at debug level.
- Removed dead code (unused entity_id generation, unused constants, empty platform hooks) and fixed type hints.
- Updated GitHub Actions workflow: current action versions plus official `hassfest` validation.
- Cleaned `hacs.json` and `manifest.json` from deprecated/invalid keys.

### Features
- **Reauthentication flow**: when the BeSMART cloud rejects the stored credentials, Home Assistant now offers a working "Reauthenticate" repair to enter them again.
- **New sensors**: Boiler System Pressure (bar) and Outdoor Temperature (from the boiler outdoor probe, if installed).
- **Diagnostics support**: download a redacted diagnostics dump (credentials and user identifiers removed) from the integration page to attach to GitHub issues.
- A warning is logged at startup when TLS certificate verification is disabled, as a reminder to re-enable it from the integration options.

### Bugfixes
- Season switch was sent to the wrong cloud endpoint (see Breaking Changes).
- Credentials can no longer leak into the Home Assistant log through aiohttp exception messages containing the login URL.
- Response body reads are now covered by the request timeout (a stalled server can no longer hang an update cycle).
- Concurrent write commands can no longer trigger parallel logins (login is now serialised with a lock).
- Auth error detection now also matches a numeric `error_code` (previously only the string `"6"` was recognised).
- Setting a target temperature of exactly 0 is no longer silently ignored.
- The AUTO program slot is computed with the Home Assistant timezone instead of the container timezone.
- Invalid `mode` values now fall back to IDLE consistently (previously AUTO).
- The water heater remembers `previous_climate_active` across restarts, so turn-on after a reboot restores the correct boiler mode.
