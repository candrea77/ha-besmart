## Changes as compared to your installed version:

> **0.7.0b1 is a pre-release.** Enable "Show beta versions" in HACS to install it.

### Breaking Changes
- None. Home Assistant **2026.7.0 or newer** is still required.

### Changes
- New `button` platform added to the integration.
- **Security:** log/exception messages now redact the API token and the user id (in addition to username and password).
- New `CLAUDE.md` with the development rules (read-only cloud endpoints only, never write `Boilers/TSP` or call `Boilers/reset`, secrets never logged).

### Features
- **New boiler sensor: Boiler Modulation** (%) — burner modulation ratio, polled from `Boilers/modulation_ratio`. `0.0` is reported as 0 %; empty / `null` / `DNCON` / `disable` values and failed reads are *unknown*. A failure of this call never affects the other entities.
- **New button: Read technical data** — reads `Boilers/technical_datas` **once** per press (it is not polled).
- **New sensor: Technical data** — state is the time of the last reading; attributes hold the values read: `modulation_pct`, `pressure_bar`, `flow_temperature`, `return_temperature`, `flue_temperature`, `dhw_temperature`, `dhw_hot_point`, `dhw_flow_rate`, `flame`, `heat_exchanger_hours` (heat exchanger cleaning counter), `heating_hours`, `dhw_hours`, `outdoor_temperature`, `room_temperature`, `temperature_now`, `temperature_target`, `tsp`, `temperature_unit`. The last reading survives Home Assistant restarts.
- Diagnostics now include the last technical data reading.

### Bugfixes
- None in this release.
