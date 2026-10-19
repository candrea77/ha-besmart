## Changes as compared to your installed version:

## 0.7.1

### ⚠️ Italian users: displayed names change
- All entities now have translated names, states and attributes. With Home
  Assistant in **Italian**, the displayed names change, e.g.
  "PIANO 1 Thermostat" → "Termostato PIANO 1", "Water Heater" →
  "Acqua calda sanitaria", "Boiler Modulation" → "Modulazione caldaia";
  Boiler Flame shows *Accesa/Spenta*, DHW Tap *In corso/Nessun prelievo*.
- **Check voice commands (Assist), aliases, dashboards and templates that use
  the entity name (`friendly_name`)**.
- **Entity ids and existing automations are unchanged**; names you set
  yourself are kept.
- New Italian installations get Italian entity ids (e.g.
  `sensor.<device>_modulazione_caldaia`): standard Home Assistant behaviour.
- English names and entity ids are unchanged.
- Thermostat presets are not translated yet.

### Other
- Manifest declares the HACS categories (thermostats/heating, water heaters).
- No other behaviour changes.

## 0.7.0

### ⚠️ Before updating — breaking / irreversible
- **Make a Home Assistant backup first.** On the first start the BeSMART
  config entry is **migrated to a new format (version 2). The migration is
  irreversible: downgrading to 0.6 or earlier is not supported** afterwards
  (restore the backup if you need to go back).
  - Username and password move from the integration options to the protected
    entry data. They are **no longer shown in Options**: change them with
    **Reconfigure** from the integration menu.
  - **TLS certificate verification is turned on** for entries created before
    0.7 (they defaulted to off). If you had explicitly turned it off, your
    choice is kept and a warning appears in **Settings → Repairs**.
- The BeSMART device no longer shows the WiFi box id as "Model ID"
  (model is now "BeSMART WiFi box").
- Home Assistant **2026.7.0 or newer** is required (unchanged from 0.6).
- Coming from **coder89/ha-besmart** (same domain)? Your entry is migrated
  automatically; see "Switching from coder89/ha-besmart" in the README.

### New features
- **Boiler Modulation** sensor (%), polled with the other boiler data.
  `0.0` is a valid 0 %; invalid values and failed reads are *unknown*.
- **Refresh now** button: immediate refresh of all polled data (presses
  within 10 s, or during a refresh, are ignored).
- **Read technical data** button and **Technical data** sensor: on-demand
  reading of the boiler technical values — flow/return/flue temperatures,
  DHW values, flame, operating hours in heating, in DHW and in
  high-condensation mode (`heating_hours`, `dhw_hours`,
  `high_condensation_hours`, named as in the official app) — never polled
  (10 s timeout, 3 attempts). Endpoints not supported by your boiler are
  listed in `unsupported_endpoints`. The last reading survives restarts.
- **Reconfigure** step to change the account credentials.
- Italian translation (setup, options, Repairs, new entities and attributes).

### Reliability
- **Partial thermostat data:** the cloud sometimes reports a thermostat's
  set points as 0 (comfort/economy/frost temperature, min/max heating set
  point, climatic curve), often with `cmd_stand_status` "1". These values,
  and any value outside the limits of the official app (max heating set
  point 15–80 °C, min 10–75 °C with min ≤ max, climatic curve 0.2–3.0 in the
  app's steps, sensor influence 0–20), are ignored: the last valid value is
  kept.
- **Season switch:** no settings are written back if the thermostat data is
  partial or outside those limits.
- Options, reauthentication and reconfigure reload the integration the way
  Home Assistant now requires (no deprecation warning; ready for 2026.12).

### Security fixes
- Credentials could appear in clear in Settings ("Retrying setup: ...") and
  in the logs when the cloud answered with an error page: the original
  network error is no longer exposed anywhere (setup, config flow, reauth,
  reconfigure, refresh).
- TLS verification on by default; Repairs warnings when it is off or when
  the cloud certificate cannot be verified (credentials are not sent then).
- The BeSMART cloud has no per-user token, so **user id and WiFi box id are
  treated as secrets**: masked in all logs (also inside payloads), replaced
  by `box_1`, `box_2`... in diagnostics, removed from device info and error
  messages. The API token is redacted too. Thermostat and boiler ids are
  reduced to their last 4 characters in logs and diagnostics, and the
  weekly program is summarized ("7 days x 48 slots") instead of being
  logged in full on every update.
- Config flow validates the login and prevents adding the same account
  twice; reauthentication and reconfigure cannot switch to another account.
- Commands are validated before being sent: thermostat temperature
  3–35 °C rounded to 0.1 °C, DHW temperature 30–60 °C (whole degrees),
  thermostat/boiler modes, season settings.
- Response size limit (512 KB), malformed cloud data ignored, 30 s cooldown
  on "Read technical data", unused write endpoints removed.
- CI: GitHub Actions pinned to commit SHAs, read-only token, Dependabot;
  automated tests on the minimum supported (2026.7) and the latest Home
  Assistant, failing on any Home Assistant deprecation warning.
- **Known residual risk:** the BeSMART login endpoint only accepts the
  credentials in the URL query string (POST is not supported). They travel
  over verified HTTPS and are never logged by the integration, but may be
  stored in the access logs of Riello's servers.

### Bugfixes
- Thermostat temperatures such as 20.96 °C were sent as an invalid value
  (20 + 10 tenths); they are now rounded to 21.0 °C.
- A command rejected by the cloud (`status: false` / error code) is now
  reported as an error instead of a silent success.
- Boiler binary sensors parse values like `"1.0"` as *on* (was unknown).

### Known limitations
- The cloud updates the module data only about every 3 minutes.
- The cloud's flame state (Boiler Flame) is not reliable, e.g. during a DHW
  draw: use Boiler Modulation (> 0 % = burner on) instead.

### If you tested the 0.7.0 betas
- Updating from 0.7.0b3 or later needs no migration.
- The Technical data attribute `heat_exchanger_hours` (betas up to b6) is now
  `high_condensation_hours`: update templates/automations that used it.
