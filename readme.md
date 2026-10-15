
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

## Contribute

Contributions are always welcome!

## License

[![CC0](https://licensebuttons.net/p/zero/1.0/88x31.png)](https://creativecommons.org/publicdomain/zero/1.0/)
