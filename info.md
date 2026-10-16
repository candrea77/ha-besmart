## Changes as compared to your installed version:

> **0.7.0 (in preparation)** — security release.

### ⚠️ Breaking / irreversible
- **Config entry migration (v1 → v2), irreversible.** On first start the
  credentials are moved from the integration options to the protected entry
  data and TLS verification is turned on for entries created before 0.7 (an
  explicit "off" you chose is kept, with a warning in Settings → Repairs).
  **Make a Home Assistant backup before updating. Downgrading to 0.6 or
  earlier is not supported** after the migration.
- Username and password are no longer in the integration **Options**: change
  them with **Reconfigure** from the integration menu.
- The BeSMART device no longer shows the WiFi box id as "Model ID"
  (model is now "BeSMART WiFi box").

### Security fixes
- Credentials could appear in clear in Settings ("Retrying setup: ...") and in
  logs when the cloud answered with an error page: the original network
  error is no longer exposed anywhere (setup, config flow, reauth,
  reconfigure).
- TLS certificate verification on by default for old entries; Repairs warning
  when it is off or when the certificate cannot be verified.
- User id and WiFi box id are treated as secrets: masked in logs, removed
  from diagnostics (boxes appear as `box_1`...), no longer shown in device
  info or error messages.
- Debug logs of cloud payloads are redacted.
- Config flow validates the login, prevents adding the same account twice;
  reauth/reconfigure cannot switch to another account.
- Write commands are validated before being sent (thermostat temperature
  3–35 °C rounded to 0.1, DHW temperature 30–60 °C whole degrees, modes,
  season settings) and a command rejected by the cloud now shows an error.
- Response size limit, malformed responses ignored, 30 s cooldown on
  "Read technical data", unused write endpoints removed.
- CI: GitHub Actions pinned to commit SHAs, read-only token, Dependabot.
- Known residual risk: the BeSMART login endpoint only accepts the
  credentials in the URL query string (POST is not supported).

### Previous pre-release notes (0.7.0b2)

> **0.7.0b2 is a pre-release.** Enable "Show beta versions" in HACS to install it.

### Breaking Changes
- None. Home Assistant **2026.7.0 or newer** is still required.

### Changes since 0.7.0b1
- **Read technical data** no longer uses `Boilers/technical_datas` (removed from the cloud, HTTP 404). It now reads the single endpoints used by the official app: `system_pressure`, `flow_temperature`, `return_temperature`, `flues_temperature`, `dhw_temperature`, `dhw_set_point`, `heating_set_point`, `dhw_flow_rate`, `flame_status`, `external_temperature`, `heat_exchanger_hours_amassed`, `worked_hours_in_heating`, `worked_hours_in_dhw`, `unit` (max 3 concurrent requests, 5 s timeout, up to 3 attempts). The modulation is taken from the polled value.
  - An endpoint that fails leaves only its attribute empty; an error is shown only if none answers.
  - Endpoints answering 404 are not retried, are logged once at debug level and are listed in the new `unsupported_endpoints` attribute.
  - Attributes: `modulation_pct`, `pressure_bar`, `flow_temperature`, `return_temperature`, `flue_temperature`, `dhw_temperature`, `dhw_set_point`, `heating_set_point`, `dhw_flow_rate`, `flame`, `outdoor_temperature`, `heat_exchanger_hours`, `heating_hours`, `dhw_hours`, `temperature_unit`, `unsupported_endpoints` (removed: `dhw_hot_point`, `room_temperature`, `temperature_now`, `temperature_target`, `tsp`).
- **New button: Refresh now** — immediate refresh of all polled data (boiler, thermostats, modulation). Presses within 10 s or during a running refresh are ignored; a failed refresh shows an error. The cloud updates module data only about every 3 minutes, so values may not have changed yet.
- **Boiler binary sensors** (Flame, Circulator, DHW Tap) now parse values like `"1.0"` as On (previously unknown), consistently with the water heater.
- **Debugging:** the raw state fields of `Boilers/data` (`flame_status`, `dhw_active`, `heating_status`, …) are logged at debug level to compare with the official app.
- **Security:** logs now also mask the wifi box id (last 4 characters only); the "Login failed" update error is redacted too.

### Features in 0.7 (from 0.7.0b1)
- **Boiler Modulation** (%) sensor, polled from `Boilers/modulation_ratio`. `0.0` is 0 %; invalid values and failed reads are *unknown*; a failure never affects other entities.
- **Read technical data** button and **Technical data** sensor (state = time of the last reading, values as attributes, restored across restarts).
- Log redaction of the API token and user id.

### Bugfixes
- Technical data reading failed with HTTP 404 (`technical_datas` endpoint no longer available).
