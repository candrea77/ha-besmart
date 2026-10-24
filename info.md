## Changes as compared to your installed version:

## 0.10.0b1 (pre-release)

### ⚠️ Breaking changes: thermostat presets and attributes renamed
Home Assistant can only translate lowercase values, so the thermostat
presets and two attributes have new names (translated in English and
Italian):

| Before | Now | BeSMART app |
|---|---|---|
| preset `AUTO` | `auto` | Auto |
| preset `MANUAL` | `manual` | Manuale - Comfort |
| preset `ECO` | `away` (Home Assistant standard preset) | Vacanza - Economy |
| preset `PARTY` | `party` | Party - Comfort |
| preset `IDLE` | `frost_protection` | Spento - Antigelo |
| preset `DHW` (hidden, used to turn off) | not a preset: `climate.turn_off` or `hvac_mode: off` | Sanitario |
| attribute `setpoint_OT` | `ot_set_point` | |
| attribute `confort_temp` | `comfort_temp` (typo fixed) | |

- **Check automations, scripts, scenes, templates, dashboard cards and
  blueprints** that use `preset_mode`, `preset_modes`, `setpoint_OT` or
  `confort_temp`.
- Triggers and conditions with the old values **stop matching without any
  error**; `climate.set_preset_mode` calls with the old values fail with
  "invalid preset" (Home Assistant checks the value before the
  integration sees it, so the old values cannot be accepted any more).
- The numeric `mode` attribute (0–5) is unchanged: use it for automations
  that do not depend on preset names.
- Entity ids and unique ids are unchanged. The history keeps the old values
  until the update.
- A notice in **Settings → Repairs** shows the same table once, after the
  update; confirm it to close it. New installations do not get it.
- Going back to 0.9.x is possible (the config entry only gets a new minor
  version); if the notice was still open, 0.9.x shows it without text:
  close it with *Ignore*.

### Other
- Each preset has its own icon.
- An unknown thermostat mode from the cloud no longer shows as `IDLE`: the
  preset is empty (logged once).
- Removed an unused internal holiday end time; the holiday end is not
  available from the cloud.
- No new requests to the cloud, nothing new is written.

## 0.9.0

### New: climatic curve chart
- A new image entity for each thermostat, **Climatic curve chart** (in
  Italian *Grafico curva climatica*), in the configuration section of the
  thermostat device.
- It draws the curves of the Riello manual (0.2 … 3.0) with the selected
  climatic curve limited between the minimum and maximum flow temperature,
  the working point at the outdoor temperature and the flow temperature
  actually requested for that thermostat (only while the thermostat calls
  for heat: its own requested value, or the boiler target if that is not
  valid; no point when it does not call for heat), so you can see the
  effect of the room sensor influence.
- The chart is indicative (traced from the Riello manual), not the exact
  calculation of the boiler.
- The flow axis follows the max flow temperature: from 20 °C to max + 15 °C
  (at least 50 °C, rounded up to the next 10), starting from 10 °C when the
  min or max flow temperature is below 20 °C.
- The legend shows the curve value at the outdoor temperature and, when the
  min or max flow temperature applies, the limited value: e.g. *Outdoor
  15.3 °C → curve 23.3 °C, limited to 24 °C*.
- Values off the chart are held at its edge with an "off scale" mark and
  their real value in the legend: an outdoor temperature above 20 °C or
  below -16 °C (the curve value at the edge is used, never extrapolated) and
  a requested flow above or below the axis. Nothing is drawn outside the
  image.
- No points while the boiler is offline (curve and limits stay); thermostats
  or boilers not set to °C show "unsupported unit".
- Redrawn only for visible changes (curve, limits, influence, or outdoor
  temperature / requested flow moving by 0.5 °C or more); the attributes
  always describe the image shown and are not recorded.
- Texts in English or Italian (Home Assistant language). Light/dark colours
  follow the operating system or browser, not the Home Assistant theme.
- Read only: no extra requests to the cloud, nothing is written.

## 0.8.1

### Improvements
- **Max Flow Temperature**, **Min Flow Temperature** and **Room Sensor
  Influence** wait 1.5 s after the last change and then send a single write
  with the final value: clicking the arrows of the box several times (e.g.
  35 → 40) no longer sends, and applies to the boiler, every intermediate
  value. The chosen value is shown at once and stays visible until the
  cloud reports it (at most 5 minutes); if the write fails the box goes
  back to the value reported by the cloud and the error is shown. The
  Climatic Curve select is unchanged (one write per choice).
- The Home Assistant activity log may still list the intermediate values:
  only the final one is sent to the boiler.
- The limits of Max/Min Flow Temperature also follow a value that is still
  waiting to be written.

## 0.8.0

### ⚠️ New devices: one per thermostat
- Each thermostat now has its **own device**, named after the room and
  linked to the BeSMART WiFi box device. The thermostat, its temperature,
  battery status and settings move there; boiler, water heater, buttons and
  technical data stay on the WiFi box device.
- **Entity ids, unique ids and entity-based automations do not change.**
- **Automations, scripts and scenes that use a *device* trigger, condition
  or action on a thermostat must be linked again** to the new thermostat
  device.
- Assign each new thermostat device to its **area** (Settings → Devices).
- Displayed names become *&lt;room&gt; &lt;entity&gt;*, e.g. "Living
  Thermostat" (was "Casa Living Thermostat"), "Living Termostato" in
  Italian. Names you set yourself are kept. New installations get entity ids
  such as `climate.living_thermostat`.

### New features
- **Thermostat settings**, on each thermostat device (configuration
  entities): **Climatic Curve** (select: 0.2–3.0, the values of the official
  app), **Max Flow Temperature** (15–80 °C), **Min Flow Temperature**
  (10–75 °C) and **Room Sensor Influence** (0–20). Italian names: *Curva
  climatica*, *Mandata massima*, *Mandata minima*, *Influenza sensore
  ambiente*.
- ⚠️ **These are installer parameters that change the boiler flow
  temperature: write down the current values before changing them.**
- Unavailable while the boiler is offline, like in the official app.
- The limits of Max/Min Flow Temperature follow each other: the minimum can
  never go above the current maximum and vice versa.
- Every change reads the current settings first, changes only the selected
  value and sends all the others exactly as read. Nothing is sent if that
  read is partial (set points at 0, missing or out-of-range values); a
  complete read with `cmd_stand_status` "1" (the cloud still passing a
  previous command to the boiler) does not block the change, so a value can
  be restored right away. A minimum above the maximum is refused.
- After sending, the value is read back: if it differs while the cloud is
  still synchronising (`cmd_stand_status` "1") it updates in the next
  cycles; otherwise it is read again twice, 3 s apart, and an error is
  shown if the cloud does not confirm it.

### Other
- The heat/cool season switch uses the same write path (one change at a
  time per thermostat, read-back check, translated error messages).
- Debug logs of the thermostat settings show the masked thermostat id
  (`***1234`).

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
- **User id and WiFi box id are treated as secrets**: masked in all logs
  (also inside payloads), replaced by `box_1`, `box_2`... in diagnostics,
  removed from device info and error messages. The API token is redacted
  too. Thermostat and boiler ids are reduced to their last 4 characters in
  logs and diagnostics, and the weekly program is summarized
  ("7 days x 48 slots") instead of being logged in full on every update.
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
