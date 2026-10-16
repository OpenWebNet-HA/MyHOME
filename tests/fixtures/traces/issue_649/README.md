# #649 / MyHomeServer1 Physical Plant Trace (Thermostat Zone 60, Energy Meter 51, Light 43)

Verbatim physical plant bus monitor trace captured from an authentic BTicino MyHomeServer1 gateway by **@gdluck** on [#649 (comment 6046117228)](https://github.com/OpenWebNet-HA/MyHOME/pull/649#issuecomment-6046117228).
Captured live on 2026-10-07 between 20:06:54 and 20:13:22 UTC. In MyHOME, physical traces are immutable ground truths: never edit a raw monitor frame.

## Fixture Status & Provenance

> [!IMPORTANT]
> **Ground Truth vs. Derived Fixture**:
> - `live_test_monitor_2026-10-07.log` is the **sole authentic ground truth** artifact: 107 monitor lines (`1791403614.835`–`1791404002.913`) captured verbatim from the gateway's monitor session. It contains no passwords or secrets.
> - `myhome_trace_MyHomeServer1_live_2026-10-07.json` is a **derived archive** generated from the raw monitor log under proposed OWNd WHO 4 season-decoding semantics (OWNd #94 / #97). It is **not** a direct Home Assistant runtime diagnostics export (`exported_at` is synthetic, `total_tx` is 0, and all 107 frames are monitor `rx`). Released OWNd 2.0.0b10 still decodes `*4*1*Z##` as `hvac_mode`.

## Hardware Profile

- **Gateway Model**: BTicino MyHomeServer1 (Server MyHOME_Up)
- **Firmware Version**: 3.87.13 (reported by operator inspection; not encoded in WHO 13 frame)
- **IP Address**: 192.168.0.218 (port 20000)
- **WHO 13 Identification**: Code `130` (`*#130**1*1*0*3*1*8##`)
- **Plant Devices Observed in Session**:
  - **Thermostat Zone 60**: Heating zone with no central unit (`#0` absent). Normal operating temperature 23.0 °C.
  - **Thermostat Zones 39 & 50**: Additional zones reporting external probe, mode, and actuator status on the bus.
  - **Energy Meter 51**: BTicino F520 single-phase electricity meter (polled via command session; absent from monitor log).
  - **Lighting Actuator 43**: Standard relay actuator (non-dimmer).
- **Environment**:
  - Home Assistant: 2026.9.4
  - Integration: 2.0.0b15
  - OWNd Protocol Engine: 2.0.0b10 (synthesized with proposed season-decoder branch)
- **Session Capture**:
  - 107 frames captured on MONITOR session (`live_test_monitor_2026-10-07.log`).
  - 37 frames transmitted by operator on separate COMMAND session (absent from monitor capture; see comment evidence below).

## Files in this Fixture

| File | Format | Description |
|---|---|---|
| `live_test_monitor_2026-10-07.log` | Raw Monitor Log (107 frames) | **Authentic Ground Truth**. 107 monitor log lines (`<epoch_timestamp> OWN <frame>`) captured from the physical gateway session. |
| `myhome_trace_MyHomeServer1_live_2026-10-07.json` | Derived Trace Archive (107 frames) | **Derived Archive**. Structured trace archive synthesized from the raw log under proposed OWNd season decoding. Every frame is monitor `rx`. |

## Plant Observations & Comment Evidence vs. Firmware Emulator Replay

> [!NOTE]
> **Comment Evidence vs. Fixture Evidence**:
> The 37 command-session frames, ACKs, and NACKs in this table are reported from `@gdluck`'s commentary on [#649](https://github.com/OpenWebNet-HA/MyHOME/pull/649#issuecomment-6046117228).
> They are **NOT** present in the raw monitor fixture (`live_test_monitor_2026-10-07.log`), because OpenWebNet monitor sessions do not capture command session ACKs/NACKs.
> Specifically:
> - **Command session frames**: The 37 command transmissions and their ACK/NACK responses were on a separate TCP command connection.
> - **Energy meter (WHO 18)**: Rows for F520 (`*#18*51*1200##` NACK, `510#9`, `513#9`) are comment evidence only; no WHO 18 frames appear on the monitor log.
> - **Timed relay command**: The command `*#1*43*#2*0*0*5##` is not in the file. The monitor log shows relay 43 ON at `1791403915.726` and OFF at `1791403920.864` (5.138 s), which is consistent with a 5 s timer, but the +0.1 s / +5.2 s delta relative to command transmission is comment evidence.
> - **Mode digit 3**: That digit 3 "leaves the season alone" is observed as the absence of a season frame between polls on the monitor log, not a paired command and report.

| Frame sent (Comment Evidence) | Live reply (Comment Evidence) | Live report on the monitor (Fixture Evidence) | Emulator | Match |
|---|---|---|---|---|
| `*#4*60##` | ACK, 5 frames | dim 0 (`0235`), dim 12 (`0230*3`), `*4*1*60##`, dim 14 (`0230*3`), dim 13 (`00`) | NACK, **no bus frame** | **differs** (see 1 below) |
| `*#4*60*11##` | NACK | – | request sent, no device | = (no fan coil on 60) |
| `*#4*60*19##` | ACK `*#4*60*19*5*0##` | – | request sent | = |
| `*#4*60*20##` | ACK `*#4*60#1*20*0##` | – | request sent | = |
| `*#4*#60##` | NACK | – | not replayed | plant has no central unit |
| `*#4*60*15##`, `15#1` | NACK | – | request sent, no device | = (no slave probe) |
| `*#4*60*14##`, `13##` | ACK with value | – | request sent | = |
| `*#4*60*#14*0230*3##` | ACK | dim 12 `0230*3` + `*4*1*60##` (season **unchanged**) | ACK, `92 17 30 2C` | = |
| `*#4*60*#14*0230*2##` | ACK | `*4*0*60##` + dim 12 (season → conditioning) | ACK, `91 17 30 2C` | = |
| `*#4*60*#14*0230*1##` | ACK | `*4*1*60##` + dim 12 (season → heating), actuator `60#2*20*5` | ACK, `90 17 30 2C` | = |
| `*#4*60*#11*2##`, `#11*0` | ACK | nothing (no fan coil) | ACK | = |
| `*4*303*60##` | ACK | `*4*303*60##` + dim 12 | ACK, `93 17 30 0F` | = |
| `*4*311*60##` | **NACK** | – | NACK, nothing | = |
| `*4*311*#60##` | ACK | **nothing**, zone unchanged | ACK, `D1 00 03 02 C2 17 00 00` | = on acceptance (see 2 below) |
| `*4*102*60##` | ACK | `*4*102*60##` + dim 12 `0070*3` (antifreeze, 7.0 °C) | ACK | = |
| `*4*303*#60##` | ACK | nothing | ACK | = on acceptance (see 2 below) |
| `*4*311*#60##` (again) | ACK | nothing; `*#4*60##` 2 min later still antifreeze | ACK | see 2 below |
| `*#18*51*51##`, `54`, `53`, `113` | ACK | – | ACK / "silent" (waiting for the bus) | = |
| `*#18*51*#1200#1*255##` | ACK | – | ACK | = |
| `*#18*51*1200##` | **NACK** | – | NACK, nothing | = |
| `*18*510#9*51##`, `*#18*51*513#9##`, `*18*59#9*51##`, `*#18*51*516##` | ACK | – | ACK / silent | = |
| `*#1*43##`, `*#1*43*1##` | ACK `*1*0*43##` | – | request sent | = |
| `*1*11*43##` | ACK | `*1*1*43##` (light on; switched off by operator 6 s later) | ACK, `11 00 12 16` | = |
| `*#1*43*#2*0*0*5##` | ACK | `*1*1*43##` at +0.1 s, `*1*0*43##` at **+5.2 s** | ACK, `D1 11 01 42 06 …` | = (5-s timer ran) |
| `*#1*43*2##` | ACK `*#1*43*2*0*0*0##` | – | request sent | = |
| `*1*2*43##` | ACK | nothing (relay, not a dimmer) | ACK, `11 00 12 1D` | = |

## Empirical Findings & Fixture Validation

### 1. What the Monitor Log Confirms

- **Bare zone poll cache replay**: Polling `*#4*60##` yields immediate sequential monitor frames: `*#4*60*0*0235##`, `*#4*60*12*0230*3##`, `*4*1*60##`, `*#4*60*14*0230*3##`, `*#4*60*13*00##`.
- **Season change on the bus**: Season flip to conditioning is recorded at `1791403701.897` (`*4*0*60##`), and return to heating at `1791403714.186` (`*4*1*60##`), with active setpoint dimension 12 maintaining `0230*3`.
- **Actuator state tracking**: `*#4*60#2*20*6##` reports following the conditioning flip, and `*#4*60#2*20*5##` reports following the return to heating. On this plant, actuator 2 is a heating valve actuator, not a fan coil fan speed (even though OWNd describes `20*5` as fan speed 0).
- **Antifreeze persistence**: `*4*102*60##` and `*#4*60*12*0070*3##` appear at `1791403787`, persist across polls at `1791403946`, and are cleared only at `1791403994` by `*4*1*60##` and setpoint write `0230*3`.
- **Relay 43 timed behavior**: Relay 43 reports ON at `1791403915.726` and OFF at `1791403920.864` (a duration of 5.138 s, consistent with a 5 s timer).

### 2. Additional Unmentioned Bus Traffic in the Log

The capture includes broader plant activity not covered in the original three-device discussion:
- **Zone 39 traffic**:
  - `*4*4002#39*0#1##` at 1791403682.224 (external probe / mode report; parsed by OWNd as zone 0 due to `0#1` address format).
  - `*4*4002*39##` at 1791403682.224.
  - `*#4*39#1*#20*0##` and `*#4*39#1*20*0##` at 1791403684 (actuator 1 status).
- **Zone 50 traffic**:
  - `*4*4002#50*0#3##` at 1791403874.276.
  - `*4*4002*50##` at 1791403874.340.
  - `*#4*50#1*#20*0##` and `*#4*50#1*20*0##` at 1791403876 (actuator 1 status).
- **Unexplained Dimension Frames on Zone 60**:
  - `*#4*59*60*80##` at 1791403744.277 and `*#4*59*60*86##` at 1791404001.928 (WHO 4 dimension 59 query/status).
  - `*#4*40*60*42##` at 1791403814.839 (WHO 4 dimension 40 status).
  - `*#4*48*60*53##` at 1791403829.228 (WHO 4 dimension 48 status).
- **Gateway identity frame**:
  - `*#130**1*1*0*3*1*8##` at 1791403740.765 (WHO 13 model 130 = MyHomeServer1). Firmware `3.87.13` is an operator note, not encoded in this frame.

### 3. What the Fixture Does Not Prove

- **Command session transmissions**: The 37 command frames and their ACK/NACK responses are absent from the capture.
- **WHO 18 energy meter**: F520 meter frames (`*#18*...`) do not appear on the monitor log; statements regarding F520 behavior rest entirely on comment evidence.
- **Digit 3 season preservation**: Mode digit 3 leaving the season unchanged is inferred from the absence of a season broadcast after setpoint writes, not an atomic command-reply pair in this capture.
