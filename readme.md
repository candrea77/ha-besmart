# BeSMART for Home Assistant

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=candrea77&repository=ha-besmart&category=integration)
[![Validation](https://github.com/candrea77/ha-besmart/actions/workflows/combined.yaml/badge.svg)](https://github.com/candrea77/ha-besmart/actions/workflows/combined.yaml)

Home Assistant integration for **Riello BeSMART** WiFi thermostats and the
boilers connected to them (Riello, Beretta and other brands of the group),
through the BeSMART cloud — the same service used by the official app.

<!-- SCREENSHOT 1: device page with thermostat, water heater and sensors -->
<!-- ![BeSMART device in Home Assistant](docs/images/device.png) -->

<!-- SCREENSHOT 2: thermostat card / technical data attributes -->
<!-- ![Thermostat and technical data](docs/images/technical-data.png) -->

## Features

- Control of every BeSMART thermostat: target temperature, presets
  (Auto, Manual, Eco, Party, Off/anti-freeze), heating/cooling season.
- Domestic hot water (DHW): target temperature and boiler work mode.
- Boiler monitoring: pressure, flow (water) temperature, outdoor probe,
  **burner modulation (%)**, flame / circulator / DHW-tap states.
- On-demand **technical data** (flow/return/flue temperatures, DHW values,
  operating hours, ...) read only when you press a button.
- Config flow (UI setup), reauthentication, reconfigure, options,
  diagnostics, Repairs warnings, English and Italian translations.
- Security-minded: TLS verification on by default, secrets never logged,
  write commands validated before reaching the cloud (see
  [Privacy and security](#privacy-and-security)).

## Requirements

- Home Assistant **2026.7.0** or newer.
- A BeSMART account working in the official app (WiFi box online).

## Installation

### HACS (recommended)

1. Click the **Open in HACS** badge above, or in HACS add this repository as
   a *custom repository* of type **Integration**:
   `https://github.com/candrea77/ha-besmart`.
2. Download **BeSMART** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → BeSMART** and
   enter your BeSMART username and password.

> [!NOTE]
> This integration uses the domain `besmart_thermostat`, like
> [coder89/ha-besmart](https://github.com/coder89/ha-besmart): only one of the
> two can be installed at a time. Coming from coder89? See
> [Switching from coder89/ha-besmart](#switching-from-coder89ha-besmart).

### Manual

Copy `custom_components/besmart_thermostat` into the `custom_components`
folder of your Home Assistant configuration and restart.

### Switching from coder89/ha-besmart

Your existing BeSMART entry is migrated automatically: you do **not** need to
remove it or enter your credentials again.

1. **Make a Home Assistant backup** (the migration cannot be undone).
2. In HACS, open *BeSMART* from coder89 → ⋮ → **Remove** (this removes only
   the downloaded files, not your configuration). Do not delete the
   integration from *Settings → Devices & services*.
3. Add this repository in HACS (badge above), download **BeSMART** and
   **restart** Home Assistant.
4. On start, the entry is upgraded: credentials move to protected storage,
   TLS verification is turned on and the default update interval (300 s) is
   used. Check **Settings → Repairs** for any message.

What you keep and what changes:

- Thermostats (climate), water heater and the BeSMART device keep their
  entity IDs, history, dashboards and automations (the displayed name may now
  be prefixed with the device name).
- coder89's **Thermostat Battery** percentage sensors are replaced by
  **Thermostat Battery Status** (low / good). The old sensors become
  unavailable: update dashboards/automations that use them, then delete them
  from *Settings → Entities*.
- New entities (temperatures, pressure, modulation, flame/circulator/DHW
  binary sensors, buttons, technical data) are added.
- Username and password are no longer in the integration **Options**: use
  **Reconfigure** to change them.

To go back to coder89's version, restore the backup made in step 1.

## Configuration

| Setting | Where | Notes |
|---|---|---|
| Name, username, password | Setup | Credentials are verified before saving. Change them later with **Reconfigure**. |
| Work modes | Setup / Options | Heating and/or cooling. |
| Verify TLS certificate | Setup / Options | On by default. Turn off only if login fails because of a broken certificate chain (a Repairs warning is shown). |
| Update interval | Setup / Options | 180–600 s (default 300). |

## Entities

One **BeSMART WiFi box** device is created per account, with:

| Entity | Type | Notes |
|---|---|---|
| *&lt;Room&gt;* Thermostat | climate | One per thermostat. |
| *&lt;Room&gt;* Temperature | sensor | Temperature measured by the thermostat. |
| *&lt;Room&gt;* Thermostat Battery Status | sensor (diagnostic) | low / good / unknown. |
| Water Heater | water_heater | DHW target 30–60 °C (whole degrees), work mode. |
| Boiler System Pressure | sensor | bar. |
| Boiler Water Temperature | sensor | Central heating flow temperature. |
| Outdoor Temperature | sensor | If an outdoor probe is installed. |
| **Boiler Modulation** | sensor | Burner modulation in %, polled with the other data. |
| Boiler Flame / Boiler Circulator / DHW Tap | binary_sensor | Boiler states reported by the cloud (see [Known limitations](#known-limitations)). |
| **Refresh now** (*Aggiorna ora*) | button | Immediate refresh of all polled data. Presses less than 10 s apart, or during a refresh, are ignored. |
| **Read technical data** (*Leggi dati tecnici*) | button (diagnostic) | Reads the boiler technical values once (never polled). 30 s cooldown. |
| Technical data (*Dati tecnici*) | sensor (diagnostic) | State = time of the last reading; attributes: `modulation_pct`, `pressure_bar`, `flow_temperature`, `return_temperature`, `flue_temperature`, `dhw_temperature`, `dhw_set_point`, `heating_set_point`, `dhw_flow_rate`, `flame`, `outdoor_temperature`, `heat_exchanger_hours`, `heating_hours`, `dhw_hours`, `temperature_unit`, `unsupported_endpoints`. Restored after a restart. |

Boiler entities are created only when the WiFi box reports a boiler.

## Tested devices

- **Beretta Mynute X 30 C** with BeSMART WiFi thermostat.

Other BeSMART-compatible boilers should work; please report your model in an
issue (with a diagnostics file) so it can be added here.

## Known limitations

- **Cloud refresh:** the BeSMART cloud receives fresh data from the WiFi
  module only about **every 3 minutes**. Polling faster does not help, and
  **Refresh now** may return values that have not changed yet.
- **Flame state is not reliable:** the cloud's `flame_status` (Boiler Flame
  binary sensor) can stay *off* while the burner is running, e.g. during a
  DHW draw. Use **Boiler Modulation** (> 0 % means the burner is on) or the
  `flame` attribute of **Technical data** instead.
- Some technical values are not available on every boiler: they are listed
  in the `unsupported_endpoints` attribute.
- Cloud only: no local control. If the BeSMART cloud is down, the
  integration cannot work.
- The login endpoint only accepts credentials in the URL (see below).

## Differences from the original project

This repository is a fork of
[coder89/ha-besmart](https://github.com/coder89/ha-besmart), itself derived
from [muchasuerte/ha-besmart](https://github.com/muchasuerte/ha-besmart).
Main changes:

- **Architecture:** single `DataUpdateCoordinator` (one round of cloud calls
  per cycle instead of one per entity), async HTTP with timeouts, no blocking
  calls in the event loop.
- **Setup and account:** config flow with login validation, reauthentication,
  reconfigure, options (update interval, TLS verification), one entry per
  account, diagnostics with redaction, Repairs warnings.
- **New entities:** boiler modulation, pressure, water and outdoor
  temperature, per-thermostat temperature, flame/circulator/DHW-tap binary
  sensors, Refresh now and Read technical data buttons, Technical data sensor.
- **Security:** TLS verification on by default, credentials and identifiers
  never written to logs or UI messages, validation of every command sent to
  the cloud, CI with pinned actions and automated tests.
- **Fixes:** season switching endpoint, timezone of the thermostat program,
  thermostat temperature rounding, many robustness fixes against malformed
  cloud data.

The full history is in the [release notes](https://github.com/candrea77/ha-besmart/releases).

## Privacy and security

**What is sent to the BeSMART cloud.** The integration talks only to the
Riello BeSMART cloud (`api.besmart-home.com`), like the official app:

- your BeSMART **username and password**, at login. The cloud's login
  endpoint only accepts them **in the URL query string** (POST is not
  supported), so they may end up in the access logs of Riello's servers: this
  is a residual risk we cannot remove. They always travel over HTTPS, with the
  server certificate verified by default;
- your **user id** and **WiFi box id**, in every request, together with a
  fixed token that is the same for every user (it is hardcoded in the
  official app too);
- the commands you send (temperatures, modes).

**Treat `user_id` and `wifi_box_id` as secrets.** The Riello cloud does not use
a per-user token: whoever knows your user id and WiFi box id may be able to
read and control your system. Never publish them.

**Where your credentials are stored.** In Home Assistant's config entry
storage (`.storage/core.config_entries`, like every integration). They are not
shown in the options form; change them with **Reconfigure** from the
integration menu. Removing the integration removes them.

**TLS.** Certificate verification is on by default (entries created by
versions before 0.7 are switched on automatically). If it is turned off, a
warning is shown in **Settings → Repairs**. Turn it off only if login fails
because of a broken certificate chain and you trust your network.

**What is redacted in logs.** Password, username/e-mail, the API token and
the user id are removed; WiFi box, thermostat and boiler ids are reduced to
their last 4 characters (`***1234`); the thermostat weekly program is
summarized; full request URLs are never logged, and error messages shown in
the UI never contain the original network error.

**Sharing logs and diagnostics safely.**

1. Prefer the diagnostics file: **Settings → Devices & services → BeSMART →
   ⋮ → Download diagnostics**. Credentials and identifiers are removed and
   WiFi boxes appear as `box_1`, `box_2`, ...
2. For logs, enable debug logging from the same menu, reproduce the issue,
   disable it to download the log, and **check it before posting**: search
   for your user id, WiFi box id and e-mail and replace them with `***`.
3. Never post screenshots of the BeSMART app's account or device pages.

## Troubleshooting

- Enable debug logging from **Settings → Devices & services → BeSMART → ⋮ →
  Enable debug logging**, or in `configuration.yaml`:

  ```yaml
  logger:
    logs:
      custom_components.besmart_thermostat: debug
  ```

- Open an [issue](https://github.com/candrea77/ha-besmart/issues) with the
  diagnostics file; read [Sharing logs and diagnostics safely](#privacy-and-security)
  first.

## Credits

- **Andrea Nicotra** ([muchasuerte/ha-besmart](https://github.com/muchasuerte/ha-besmart)) —
  original author of the integration (2020), whose MIT copyright notice is
  kept in [LICENSE](LICENSE).
- [coder89/ha-besmart](https://github.com/coder89/ha-besmart) — config flow,
  water heater and battery sensor, base of this fork.
- Everyone who reported issues and tested on their boilers.

This project is not affiliated with or endorsed by Riello S.p.A.

## Contributing

Contributions are welcome. Tests run with `pytest` (see
`requirements_test.txt`); development rules are in [CLAUDE.md](CLAUDE.md).

## License

[MIT](LICENSE)
