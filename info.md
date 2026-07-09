## Changes as compared to your installed version:

### Breaking Changes
- Home Assistant **2026.7.0 or newer** is required. This release has been tested on Home Assistant 2026.7.

### Changes
- New `binary_sensor` platform added to the integration.

### Features
- **New boiler binary sensors** (created only when a boiler is present):
  - **Boiler Flame** — burner flame present (`flame_status`).
  - **Boiler Circulator** — central heating active / circulator pump running (`heating_status`).
  - **DHW Tap** — domestic hot water draw in progress, i.e. a hot water tap is open (`dhw_active`).
- **New boiler sensor**: **Boiler Water Temperature** — central heating flow temperature (`heating_current_temp`).
- **New per-thermostat sensors**: **&lt;Room&gt; Temperature** — the temperature measured by each thermostat, created dynamically for every thermostat found (same value shown by the climate entity, exposed as a standalone sensor for graphing, statistics and automations).

### Bugfixes
- None in this release.
