# #629 F454 Gateway Traces (Bus Sweep & Thermoregulation Central Unit #0 Responses)

Verbatim bus traces contributed by **@ricdijk** on [#629 (comment 5993412070)](https://github.com/OpenWebNet-HA/MyHOME/issues/629#issuecomment-5993412070) and [#629 (comment 5993604980)](https://github.com/OpenWebNet-HA/MyHOME/issues/629#issuecomment-5993604980). They are facts: never edit a frame.

## Hardware Profile

- **Gateway Model**: BTicino F454 (OpenWebNet IP Gateway / Web Server)
- **Firmware**: 1.0.34 (`*#13**16*1*0*34##`)
- **WHO 13 Identification**: Device code `200` (`*#13**15*200##`)
- **Plant Topology**: 99-zone thermoregulation system (zones 1–7 and 11–15, central unit `#0`), installed circa 2013/2014
- **Home Assistant Version**: 2026.9.4
- **Integration Version**: 2.0.0b14
- **OWNd Protocol Engine**: 2.0.0b9
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_sweep_F454_all_2026-10-05T10-51-55.json` | Full Bus Sweep Capture (122 frames) | Full bus sweep of the F454 installation (FW 1.0.34) covering 7 subsystems: WHO 1 (lights), WHO 2 (covers), WHO 4 (climate query), WHO 5 (burglar alarm), WHO 13 (gateway info/clock), WHO 16 (audio), WHO 18 (energy), and WHO 1013 (gateway diagnostics). |
| `myhome_trace_F454_all_2026-10-05T11-14-00.json` | Bus Monitor Trace (7 frames) | Manual test sending plain status request `*#4*#0##` to the 99-zone central unit `#0`. The gateway replies within 0.17 s with `*4*202*#0##` (OFF mode) and operational flags `*4*21*#0##`, `*4*22*#0##`, `*4*24*#0##`. |
| `myhome_trace_F454_all_2026-10-05T11-14-57.json` | Bus Monitor Trace (1 frame) | Manual test sending dimension-14 status request `*#4*#0*14##` to `#0`. The gateway rejects the frame fast (~0.1 s, NACK). |
| `myhome_trace_F454_all_2026-10-05T11-16-07.json` | Bus Monitor Trace (1 frame) | Manual test sending status request `*#4*#0#1##` to 4-zone central unit address `#0#1`. Fails after 8 s per attempt (timeout, no such device on bus). |
| `home-assistant_myhome_2026-10-05T11-16-10.935Z.cleaned.log` | HA Debug Log (157 KB) | Complete Home Assistant debug log capturing integration startup, full initial entity poll, and the three manual test requests. |

## Sequence of Actions Recorded & Analysis

1. **Full Installation Bus Sweep (`myhome_sweep_F454_all_...`)**:
   - **WHO 13 (Gateway Identity & Clock, 6 frames)**: Gateway time/date query `*#13**0##` -> reports `*#13**0*12*50*40*001##`. Gateway model code query `*#13**15##` -> reports `*#13**15*200##` (F454). Firmware query `*#13**16##` -> reports `*#13**16*1*0*34##` (FW 1.0.34).
   - **WHO 1013 (Gateway Diagnostics, 2 frames)**: Diagnostic query `*#1013*0*1##` -> reports `*#1013**1*51*15*1*0##`.
   - **WHO 1 (Lighting, 55 frames)**: Relays and switches reporting OFF status (`*1*0*WHERE##`) across 20+ bus addresses (`11`, `21`, `31`, `51`, `61`, `71`, `81`, `12`, `22`, `32`, `42`, etc.).
   - **WHO 2 (Automation / Covers, 8 frames)**: General scan request `*#2*0##`, followed by motorized shutter actuators reporting STOP status (`*2*0*WHERE##`) across 7 addresses (`42`, `43`, `44`, `45`, `46`, `49`, `39`).
   - **WHO 4 (Climate / Heating, 1 frame)**: General scan request `*#4*0##`. In this 99-zone plant with central unit `#0`, the gateway does not return a bulk response to `*#4*0##`.
   - **WHO 5 (Burglar Alarm, 9 frames)**: System status frames (`*5*0*##`, `*5*9*##` disengage, `*5*5*##` battery ok, `*5*7*##` network present) and zones `#1` through `#8` reporting idle/armed status (`*5*11*#1##` .. `*5*11*#8##`).
   - **WHO 16 (Audio / Sound Diffusion, 1 frame)**: Status query `*#16*0*5##`.
   - **WHO 18 (Energy Management, 36 frames)**: Energy meters `71#0` through `79#0` queried on dimension 51 (`*#18*WHERE*51##`) and dimension 1200 (`*#18*WHERE*1200##`).

2. **Test 1: Plain Status Request `*#4*#0##` (`myhome_trace_F454_all_2026-10-05T11-14-00.json`)**:
   - Status request sent: `*#4*#0##`
   - Gateway response within 0.17 s:
     - `*4*202*#0##`: Central Unit `#0` mode is set to conditional off (`WHAT 202`).
     - `*4*21*#0##`: Central Unit operational status.
     - `*4*22*#0##`: Central Unit operational status.
     - `*4*24*#0##`: Central Unit operational status.
     - `*4*210*#5##`: Zone 5 cooling status.
     - `*4*210*#12##`: Zone 12 cooling status.
   - **Finding**: A 99-zone central unit at `#0` reliably answers the plain status frame `*#4*#0##` by immediately broadcasting its operating mode (`*4*202*#0##`) and status flags.

3. **Test 2: Dimension 14 Status Request `*#4*#0*14##` (`myhome_trace_F454_all_2026-10-05T11-14-57.json`)**:
   - Request sent: `*#4*#0*14##`
   - Gateway response: Immediate NACK / rejection (`Could not send message *#4*#0*14##. Retrying (1)... No more retries`).
   - **Finding**: F454 firmware rejects dimension-14 status query on central unit `#0`. Polling `#0` with dimension 14 causes repeated poll failures, triggering `failed_polls: 2` and the `unresponsive_since` diagnostic repair.

4. **Test 3: 4-Zone Central Unit Address `*#4*#0#1##` (`myhome_trace_F454_all_2026-10-05T11-16-07.json`)**:
   - Request sent: `*#4*#0#1##`
   - Gateway response: Socket timeout (8 s retry, 16 s total before giving up).
   - **Finding**: Addresses of non-existent central units result in transport timeout rather than fast gateway NACK.
