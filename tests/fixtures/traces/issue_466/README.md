# #466 MH200N Gateway Traces (Bus Sweep & Thermoregulation Interactions)

Verbatim bus traces contributed by **@caiosweet** on [#466 (comment 5834435606)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5834435606). They are facts: never edit a frame.

## Hardware Profile

- **Gateway Model**: BTicino MH200N (2nd Generation Scenario Programmer)
- **Firmware**: 1.1.8
- **WHO 13 Device Type Code**: `44`
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_sweep_MH200N_all_2026-09-25T14-40-10.json` | Bus Card Export (200 frames) | Full on-wire trace captured via `<myhome-bus-card>` during `myhome.sweep_bus` and climate interactions. |
| `config_entry-myhome-80a1577fb7ae6f68f05e0cc5a1ead27d.json` | HA Diagnostic Download | Home Assistant config entry diagnostic summary from the reporter's plant. |

## Sequence of Actions Recorded

1. **Bus Sweep (`myhome.sweep_bus`)**:
   - Firmware query `*#13**16##` -> reports `*#13**16*1*1*8##` (firmware 1.1.8).
   - Automation scan `*#2*0##` -> reports stopped covers (`19`, `29`, `69`, `78`, `79`, `0715`).
   - Lighting status queries across multiple points (`12`, `13`, `22`, `23`, `24`, `25`, `32`, `41`, `42`, `47`, `52`, `53`, `54`, `61`, `62`, `63`, `71`, `81`, `91`, `0315`, `0614`).
   - Climate status scan `*#4*0##` -> reports zones 1–4 temperatures and heating valve actuator states (`*#4*Z#1*20*0##`), plus external probe `105` at 29.6 °C (`*#4*105*0*0296##`).
   - Energy totalizer sweep across meters 51–57 (`*#18*51*51##` -> `*#18*51*51*23791364##`, up to meter 57).

2. **Thermoregulation Setpoint Change**:
   - Reporter set the plant temperature to 18.0 °C:
     - `*4*110#0180*#0##`, `*4*21*#0##`
     - Zones 1–3 confirm manual heating setpoint 18.0 °C (`*#4*Z*14*0180*3##`).

3. **Thermostat Knob Local Offset Adjustment**:
   - Reporter manually turned the probe adjustment wheel on Zone 4 through its complete local offset range (-3 °C to +3 °C):
     - `*#4*4*13*00##` (0 °C offset)
     - `*#4*4*13*01##` (+1 °C)
     - `*#4*4*13*02##` (+2 °C)
     - `*#4*4*13*03##` (+3 °C)
     - `*#4*4*13*02##` (+2 °C)
     - `*#4*4*13*01##` (+1 °C)
     - `*#4*4*13*00##` (0 °C)
     - `*#4*4*13*11##` (-1 °C)
     - `*#4*4*13*12##` (-2 °C)
     - `*#4*4*13*13##` (-3 °C)
     - `*#4*4*13*12##` (-2 °C)
     - `*#4*4*13*11##` (-1 °C)

## Subsystems Verified (MH200N)

- **WHO 1 (Lighting)**: 21 lighting endpoints reporting OFF / ON status during sweep.
- **WHO 2 (Automation)**: 6 shutter/blind endpoints reporting stopped state (`*2*0*WHERE##`).
- **WHO 4 (Thermoregulation)**: 4 climate zones, external temperature probe 105, heating actuator valves, manual setpoints, and local knob offsets (Dimension 13).
- **WHO 13 (Gateway Management)**: Firmware version query/response (`1.1.8`) and internal date/time broadcasts.
- **WHO 18 (Energy Management)**: Cumulative energy meter totalizers on addresses 51 through 57.

---

# #466 H4890 Touch Screen Gateway Traces (Bus Sweep, Burglar Alarm, Audio Diffusion)

Verbatim bus traces contributed by **@nicolacavallo84** on [#466 (comment 5846053726)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5846053726).

## Hardware Profile

- **Gateway Model**: BTicino H4890 (Axolute 3.5" Color Touch Screen with integrated LAN OpenWebNet server; shares board architecture with `AM4890`, `LN4890`, and `LN4890A`)
- **Firmware**: 4.0.15
- **WHO 13 Device Type Code**: `200`
- **WHO 1013 Object Model**: `30`
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_sweep_H4890_all_2026-09-26T11-48-10.json` | Bus Card Export (200 frames) | Full bus sweep capture via `<myhome-bus-card>` covering lights, covers, power, audio, sound diffusion, energy, and CEN+. |
| `myhome_trace_H4890_all_2026-09-26T11-51-26.json` | Bus Monitor Trace (51 frames) | Real-world WHO 5 Burglar Alarm events (`*5*9*0##` disarm, `*5*1*0##` arm away, zone statuses), power, energy, and CEN+. |
| `myhome_trace_H4890_all_2026-09-26T11-52-48.json` | Bus Monitor Trace (32 frames) | Sound diffusion (WHO 16 / WHO 22) and power control traffic. |

## Subsystems Verified (H4890)

- **WHO 1 (Lighting)**: Points reporting on/off status.
- **WHO 2 (Automation)**: Cover/shutter states.
- **WHO 5 (Burglar Alarm)**: System arm/disarm transitions and zone status reporting (`*5*11*#1##` through `#6##`, `*5*18*#7##`, `#8##`).
- **WHO 9 (Power / Auxiliary)**: Auxiliary load control events.
- **WHO 16 & 22 (Sound & Audio Diffusion)**: Multi-source audio control and diffusion events.
- **WHO 18 (Energy Management)**: Cumulative energy meter reports.
- **WHO 25 (CEN+ / Dry Contact)**: CEN+ pushbutton / scenario status events.
