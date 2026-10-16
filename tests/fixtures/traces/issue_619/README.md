# #619 F453AV Gateway Traces & Bus Sweep

Verbatim bus traces contributed by **@nce2704** on [#619 (comment 5978395483)](https://github.com/OpenWebNet-HA/MyHOME/issues/619#issuecomment-5978395483), [#619 (comment 5983731573)](https://github.com/OpenWebNet-HA/MyHOME/issues/619#issuecomment-5983731573), and [OWNd#97 (comment 6110745254)](https://github.com/OpenWebNet-HA/OWNd/pull/97#issuecomment-6110745254). They are facts: never edit a frame.

## Hardware Profile

- **Gateway Model**: BTicino F453AV (Audio/Video Web Server & Gateway)
- **Firmware**: 1.0 (WHO 13 device code `12`) / 3.0.14 (`*#13**16*3*0*14##`)
- **Actuator Models**:
  - BTicino F414 (Classic 1000 W modular DIN phase-cut dimmer, 60–1000 VA)
  - Relays, motorized shutters/covers, thermostats/climate zones, central unit #0, burglar alarm, energy meters
- **Actuator Tested**: WHERE `13` (connected to a 35 W halogen lamp); WHERE `14`, `24`, `31`, `34` (unloaded); Central Unit `#0` and subordinate climate zones
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_trace_F453AV_all_2026-10-04T08-28-13.json` | Bus Monitor Trace (23 frames) | Verification of physical F414 dimmer on WHERE `13` with 35 W load, cycling through discrete levels 2..7 (`*1*2*13##` through `*1*7*13##`) and switching OFF (`*1*0*13##`). |
| `myhome_sweep_F453AV_all_2026-10-04T19-45-11.json` | Full Bus Sweep Capture (157 frames) | Full bus sweep of the F453AV installation (FW 3.0.14) covering 8 subsystems: WHO 1 (lights), WHO 2 (covers), WHO 4 (climate), WHO 5 (burglar alarm), WHO 13 (gateway info/clock), WHO 16 (audio), WHO 18 (energy), and WHO 1001 (actuator diagnostics). |
| `myhome_trace_F453AV_who4_2026-10-11T11-02-55.Holiday.ON.json` | Bus Monitor Trace (88 frames) | Setting Central Unit #0 to Daily Holiday (Holiday Plan) via `*4*115#1101*#0##` specifying weekly program 1 (`1101`), accompanied by central status flags `*4*21*#0##`, `*4*22*#0##`, and subzone program reports `*4*111*#Z##`. |
| `myhome_trace_F453AV_who4_2026-10-11T11-10-23.Holiday.OFF.automatically.json` | Bus Monitor Trace (20 frames) | Automatic expiration of Daily Holiday, restoring Central Unit #0 to weekly program 1 via `*4*1101*#0##`, status flags `*4*21*#0##` / `*4*22*#0##`, and subzone program synchronization. |
| `myhome_trace_F453AV_who4_2026-10-11T11-13-06.Antifreeze.ON.json` | Bus Monitor Trace (112 frames) | Switching Central Unit #0 to Antifreeze via `*4*102*#0##`, propagating to subzones `*4*102*#Z##` and physical actuators `*4*102*Z##`, with inactive zones remaining OFF (`*4*303*Z##`). |
| `myhome_trace_F453AV_who4_2026-10-11T11-15-50.one.zone.to.cool.json` | Bus Monitor Trace (63 frames) | Freedompro app transition switching a zone from Heat to Cool: Central Unit #0 automatically transitions plant from Winter to Summer into Thermal Protection via `*4*202*#0##` / `*4*202*Z##`, updating setpoints to cooling targets. |
| `myhome_trace_F453AV_who4_2026-10-11T10-49-26.Central.to.OFF.json` | Bus Monitor Trace (115 frames) | Setting Central Unit #0 to OFF via `*4*103*#0##`, propagating `*4*103*#Z##` and turning off physical zones via `*4*303*Z##`. |
| `myhome_trace_F453AV_who4_2026-10-11T10-29-57.json` | Bus Sweep & State Trace (121 frames) | Central unit #0 and zone status trace demonstrating concurrent `*4*1*Z##` (zone heating season status) and `*4*111*#Z##` (zone weekly program), proving season telemetry does not displace weekly schedule mode. |

## Sequence of Actions Recorded & Subsystems Verified

1. **Physical F414 Normal Operation With Load (WHO 1)**:
   - Initial OFF state: `*1*0*13##`.
   - Discrete brightness levels: Level 4 (`*1*4*13##`), Level 3 (`*1*3*13##`), Level 2 (`*1*2*13##`), ramp up through Levels 2, 3, 4, 5, 6, 7 (`*1*7*13##`).
   - Normal turn OFF: `*1*0*13##`.
   - Proves that when connected to a closed load circuit, the F414 operates with standard discrete levels 2..7 and discrete OFF `0`, without emitting `WHAT 19`. Unloaded channels emit `WHAT 19` (`*1*19*WHERE##`).

2. **Full Installation Bus Sweep (`myhome_sweep_F453AV_all_...`)**:
   - **WHO 1 (Lighting, 69 frames)**: Standard relays reporting ON/OFF (`*1*0*11##`, `*1*1*71##`), plus unloaded F414 dimmers on WHERE `13`, `14`, `24`, `31`, `34` reporting `WHAT 19` (`*1*19*WHERE##`).
   - **WHO 2 (Automation / Covers, 9 frames)**: Actuators on WHERE `74`, `84`, `05`, `75`, `06`, `96`, `77`, `69` reporting STOP status (`*2*0*WHERE##`).
   - **WHO 4 (Climate / Heating, 30 frames)**: Zones 4 and 5 reporting setpoint dimension 14 (`*#4*WHERE*14*0220*3##`), mode dimension 12 (`*#4*WHERE*12*0220*3##`), operating state (`*4*1*4##`), zone temperature dimension 0 (`*#4*5*0*0263##` = 26.3 °C), and central unit control.
   - **WHO 5 (Burglar Alarm, 14 frames)**: Zones `#1` through `#8` reporting arm status (`*5*18*#ZONE##` / `*5*11*#ZONE##` / `*5*15*#ZONE##`) and system-wide state (`*5*1*##` activation, `*5*9*##` disengage, `*5*5*##` battery ok, `*5*7*##` network present).
   - **WHO 13 (Gateway Identity & Clock, 11 frames)**: Gateway model code 12 (`*#13**15*12##`), firmware 3.0.14 (`*#13**16*3*0*14##`), time/date (`*#13**0*22*12*27*001##`).
   - **WHO 16 (Audio / Sound Diffusion, 1 frame)**: Status query `*#16*0*5##`.
   - **WHO 18 (Energy Management, 18 frames)**: Energy meters / pulse counters at WHERE `51`, `52`, `53`, `54`, `55`, etc., reporting dimensions 51 and 1200.
   - **WHO 1001 (Actuator Diagnostics, 5 frames)**: Diagnostic bitmasks (`*#1001*WHERE*11*111110111111111111110111##`) emitted for unloaded F414 dimmers on WHERE `13`, `14`, `24`, `31`, `34`.

3. **Authentic WHO 4 Central Unit #0, Holiday & Season Transitions (`myhome_trace_F453AV_who4_...`)**:
   - **Daily Holiday Program Extraction**: `*4*115#1101*#0##` proves parameter `1101` encodes weekly program 1 (`1101 % 100 == 1`), permanently disproving the legacy misparsing of `1101` as temperature `-10.1 °C`.
   - **Holiday Automatic Expiration**: `*4*1101*#0##` demonstrates the central unit cleanly transitioning back to weekly program 1.
   - **Central Antifreeze & OFF Propagation**: `*4*102*#0##` and `*4*103*#0##` demonstrate clean broadcast to central subzones (`#Z`) and physical zone actuators (`Z`).
   - **Season Decoupling & Winter-Summer Switch**: `myhome_trace_F453AV_who4_2026-10-11T10-29-57.json` demonstrates zone heating season frames `*4*1*Z##` coexisting with weekly program `*4*111*#Z##` without overriding active schedule mode. `myhome_trace_F453AV_who4_2026-10-11T11-15-50.one.zone.to.cool.json` captures switching a zone from Heat to Cool triggering the central unit to switch from Winter to Summer into Thermal Protection (`*4*202*#0##`).
   - **Central Status Flags**: Every central mode transition is verified with companion flags `*4*21*#0##` and `*4*22*#0##`.
