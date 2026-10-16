## Changes as compared to your installed version:

> **0.7.0b3 is a pre-release** (release candidate for 0.7.0). Enable "Show beta versions" in HACS to install it.

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

### New features
- **Boiler Modulation** sensor (%), polled with the other boiler data.
  `0.0` is a valid 0 %; invalid values and failed reads are *unknown*.
- **Refresh now** button: immediate refresh of all polled data (presses
  within 10 s, or during a refresh, are ignored).
- **Read technical data** button and **Technical data** sensor: on-demand
  reading of the boiler technical values (flow/return/flue temperatures, DHW
  values, flame, heat exchanger / heating / DHW hours, ...), never polled;
  endpoints not supported by your boiler are listed in
  `unsupported_endpoints`. The last reading survives restarts.
- **Reconfigure** step to change the account credentials.
- Italian translation.

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
  messages. The API token is redacted too.
- Config flow validates the login and prevents adding the same account
  twice; reauthentication and reconfigure cannot switch to another account.
- Commands are validated before being sent: thermostat temperature
  3–35 °C rounded to 0.1 °C, DHW temperature 30–60 °C (whole degrees),
  thermostat/boiler modes, season settings (nothing is sent if the
  thermostat settings read from the cloud are incomplete).
- Response size limit (512 KB), malformed cloud data ignored, 30 s cooldown
  on "Read technical data", unused write endpoints removed.
- CI: GitHub Actions pinned to commit SHAs, read-only token, Dependabot;
  automated tests on the minimum supported and the latest Home Assistant.
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
- Technical data no longer uses the removed `Boilers/technical_datas`
  endpoint (HTTP 404).

### Known limitations
- The cloud updates the module data only about every 3 minutes.
- The cloud's flame state (Boiler Flame) is not reliable, e.g. during a DHW
  draw: use Boiler Modulation (> 0 % = burner on) instead.
