
# HA Riello's Besmart thermostats 

Support for Riello's Besmart thermostats.
Be aware the thermostat may require more then 3 minute to refresh its states.

The thermostats support the season switch however this control will be managed with a 
different control.

Tested with home-assistant >= 2026.7

Using HACS, add the custom repository, selecting **"Integration"** as the value for **"Type"**

Once the repository has been successfully added, go to **Settings** → **Devices & services** → **Add Integration**, select **BeSmart**, and complete the required settings.

## Buttons

- **Refresh now** — immediately refreshes all polled data (boiler, thermostats,
  boiler modulation) instead of waiting for the next update interval. Presses
  less than 10 seconds apart, or while a refresh is running, are ignored.
  **Note:** the BeSMART cloud receives fresh data from the WiFi module only
  about every 3 minutes, so a forced refresh may still return values that have
  not been updated yet.
- **Read technical data** (diagnostic, boilers only) — reads the boiler
  technical values once (pressure, flow/return/flue temperatures, DHW values,
  flame, operating hours, …) and stores them as attributes of the
  **Technical data** sensor. These values are never polled. Endpoints the cloud
  does not support for your boiler are listed in the `unsupported_endpoints`
  attribute.

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
the user id are removed; WiFi box ids are reduced to their last 4 characters
(`***1234`); full request URLs are never logged, and error messages shown in
the UI never contain the original network error.

**Sharing logs and diagnostics safely.**

1. Prefer the diagnostics file: **Settings → Devices & services → BeSMART →
   ⋮ → Download diagnostics**. Credentials and identifiers are removed and
   WiFi boxes appear as `box_1`, `box_2`, ...
2. For logs, enable debug logging from the same menu, reproduce the issue,
   disable it to download the log, and **check it before posting**: search
   for your user id, WiFi box id and e-mail and replace them with `***`.
3. Never post screenshots of the BeSMART app's account or device pages.

## Contribute

Contributions are always welcome!

## License

[![CC0](https://licensebuttons.net/p/zero/1.0/88x31.png)](https://creativecommons.org/publicdomain/zero/1.0/)
