# #667 MH200 & F461 Video Door Entry, Intercom & Impulse Gate/Garage Traces

Authentic physical bus traces recorded on two BTicino MyHOME plants for PR #667:
1. **BTicino MH200 (Firmware 2.1.0)**: Outdoor entrance panel bell ring, WHO 8 video intercom call, indoor handset pickup, `*6*9##` camera teardown, and WHO 1 gate/garage impulse relay actuation.
2. **BTicino F461 (Firmware 2.0.11 / WHO 1013 `134.15.5.0`) + BT-F411/4 + BT-F411/2**: Live hardware verification of WHO 1 `WHAT=18` (`*1*18*06##` / `*1*18*07##`) 0.5-second hardware-timed impulse with autonomous hardware relay release, 3-step software impulse cover cycle (`OPEN` -> mid-flight `STOP` -> `CLOSE`), unpopulated relay `NACK` timing (`*1*18*71##`), and `WHO=6` / `WHO=8` command session behavior on an automation-only DIN gateway.

> **Privacy & Anonymization Guarantee**:
> In accordance with project security standards, all physical network identifiers are scrubbed to RFC 5737 documentation addresses (`192.0.2.92`) and synthetic MACs (`00:03:50:00:04:97`), and customer station addresses in the MH200 capture are mapped to disjoint mock addresses (`71`–`74`, `81`–`88`). No private IP addresses, credentials, or personal identifiers are present.

## Contributed Files

| File | Gateway & Hardware | Description |
|---|---|---|
| `myhome_trace_MH200_intercom_bell_gate_2026-10-09.json` | **MH200** (`2.1.0`, WHO 13 code `44`) | 34-frame on-wire capture across an outdoor entrance panel video call (`*8*1#1#4*74##`), audio matrix routing (`*#16*81*1*1##`..`88##`), handset answer (`*8*9#1#4*73##`), camera off (`*6*9##`), and gate/garage pulses (`*1*1*71##`, `*1*1*72##`). |
| `myhome_trace_F461_impulse_intercom_2026-10-19.json` | **F461** (`2.0.11`, WHO 13 code `200`, WHO 1013 code `134`) + **BT-F411/4** (`06`, `07`) + **BT-F411/2** (`05`) | 34 `evt` frames and 161 chronological `cmd`/`evt`/`result` rows across 4 structured runs (`run1_f411_impulse_what18_and_software_pulse`, `run2_who6_door_entry_lock_and_camera`, `run3_who8_intercom_call_kinds`, `run4_plant_idle_verification`). |

## Sequence of Actions Recorded

### 1. `myhome_trace_MH200_intercom_bell_gate_2026-10-09.json` (MH200)

1. **Garage & Gate Impulse Open (from Garage Wall Switch)**:
   - `*1*1*71##` (Pulsed garage door motor contact via WHO 1)
   - `*1*1*72##` (Pulsed entrance gate motor contact via WHO 1)
2. **Gate Closed & Re-Opened via Outdoor Keypad Code**:
   - `*1*1*72##` (Impulse close pulse, followed by open pulse)
3. **Outdoor Entrance Panel Bell Pressed**:
   - `*#16*81*1*1##` through `*#16*88*1*1##`: Audio matrix activates routing on sound system zones.
   - `*8*1#1#4*74##`: **WHO 8 Video Door Entry Call initiated** (`Kind=1` PE1, `MMType=4` Audio/Video) directed to indoor handset `74`.
4. **Call Answered & Completed on Indoor Handset**:
   - `*8*9#1#4*73##`: Caller address notification from entrance panel (`Kind=1`, `MMType=4`, address `73`).
   - `*6*9##`: Camera session terminated (Camera OFF).
5. **Gate Closed from Secondary Handset**:
   - `*1*1*72##` (Impulse close pulse).

### 2. `myhome_trace_F461_impulse_intercom_2026-10-19.json` (F461 + BT-F411/4)

1. **`run1_f411_impulse_what18_and_software_pulse`**:
   - **Hardware-timed 0.5 s pulse (`WHAT=18`: `*1*18*06##` and `*1*18*07##`)**:
     - `F461` ACKs `*1*18*06##` in `0.090 s` (`0.093 s` on `07`).
     - The physical `BT-F411/4` actuator immediately closes the relay and emits `*1*1*06##` (`t = 4.315 s`) and Group `#1` aggregate status `*#1*#1*15*1##` (`t = 4.388 s`).
     - Exactly **0.539 seconds later** (`t = 4.854 s` on `06`; **0.511 seconds later** at `t = 8.007 s` on `07`), the physical `BT-F411/4` relay **autonomously opens in hardware** and broadcasts `*1*0*06##` + `*#1*#1*15*0##` on the event monitor session without any software `OFF` command from Home Assistant.
     - Subsequent status polls (`*#1*06##`, `*#1*07##`) confirm `['*1*0*06##']` and `['*1*0*07##']`.
   - **Software-timed 0.5 s impulse cover cycle (`OPEN` -> `STOP` -> `CLOSE` on `06`)**:
     - Exercises the software `ON` (`*1*1*06##`) + 0.5 s `OFF` (`*1*0*06##`) fallback across a full 3-step `OPEN` (1.5 s travel) -> mid-flight `STOP` (1.0 s settle) -> `CLOSE` cycle on physical `BT-F411/4` channel `06`.
   - **Unpopulated relay address `71` (`*1*18*71##` and `*1*1*71##`)**:
     - `bt_fls` on the `F461` waits `2.065 s` / `2.071 s` for an actuator response on the 2-wire SCS bus, emits nothing on `evt`, and returns `*#*0##` (`NACK`), validating that `MyHOMEImpulseCover` only starts `min_cycle_time` after a confirmed bus write.
2. **`run2_who6_door_entry_lock_and_camera` & `run3_who8_intercom_call_kinds`**:
   - On an automation-only DIN gateway without a video door-entry riser (`F461`), `WHO=6` (`*6*10*4000##`, `*6*10*4001#2##`, `*6*10*1##`, `*6*22*1##`, `*6*0*4000##`, `*6*9##`, `*6*9**##`, `*6*6*1##`, `*6*6*4100##`) and `WHO=8` (`*8*1#1#4*74##`, `*8*1#1#4#20*11##`, `*8*9#1#4*73##`, `*8*1#6#2#11*16##`, `*8*1#7#2#11*16##`, `*8*1#14#2#11*4##`) return a fast `~55 ms` `NACK` (`*#*0##`) with no bus echo.
3. **`run4_plant_idle_verification`**:
   - Final polls (`*#1*06##`, `*#1*07##`, `*#2*05##`) confirm both `BT-F411/4` channels are `OFF` and the `BT-F411/2` shutter (`05`) is stopped (`*2*0*05##`).
