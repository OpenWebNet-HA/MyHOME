# Discussion #770 BTicino F454 + F401 Advanced Shutter & Mechanical Slat-Tilt Traces (`WHERE="21"`)

Verbatim bus traces captured on a physical **BTicino F454** gateway (`00:03:50`, firmware `2.0.51`, Home Assistant `2026.10.2`, integration `2.0.0b16`, `OWNd 2.0.0b11`) driving a **BTicino F401** advanced shutter actuator (`WHERE="21"`, `advanced: true`) on a ~3.75 m roller shutter with mechanically tilting slats and a **BTicino H4660M2** 3-button (`up`, `down`, `stop`) wall keypad.

Contributed by **@eC0y0te** on [Discussion #770](https://github.com/orgs/OpenWebNet-HA/discussions/770) ([comment 19144038](https://github.com/orgs/OpenWebNet-HA/discussions/770#discussioncomment-19144038) and [comment 19144521](https://github.com/orgs/OpenWebNet-HA/discussions/770#discussioncomment-19144521)). Thank you!

| File | Content |
|---|---|
| `myhome_trace_F454_2026-10-23_anon.json` | 220 frames across `WHO=1` and `WHO=2` (`186 rx`, `34 tx`): H4660M2 keypad short pulses, HA `set_cover_position` 5% and 2% step progressions, full 91.3 s open run, 45.8 s mid-run stop at 47%, and 41.9 s close to 0% |
| `myhome_trace_F454_all_2026-10-23T11-11-26.json` | 125 frames across `WHO=2`, `WHO=4`, and `WHO=13` (`103 rx`, `22 tx`): `slat_tilt: true` (`slat_time: 1.4`, `min_motor_pulse: 0.2`) timed tilt pulses (`15%`, `20%`, `25%`, `50%`, `100%`, `0%`) followed by `set_cover_position: 2` |

## Hardware Profile

- **Gateway**: BTicino F454 (firmware `2.0.51`, `F454Profile`)
- **Actuator**: BTicino F401 (`WHERE="21"`, `advanced: true`, `Dimension 10` position feedback `*#2*21*10*<STATE>*<POS>*001*15##` with `priority=001`, `info=15`)
- **Wall Keypad**: BTicino H4660M2 (3 buttons: `up`, `down`, `stop`; emits `WHO=2` advanced translation frames `*2*1000#11#100#001#1*21##`, `*2*1000#12#100#001#1*21##`, `*2*1000#10#001#1*21##`)
- **Shutter Mechanics**: ~3.75 m wide roller shutter (`91.3 s` full travel up from `0%`, `87.8 s` full travel down, ~`0.87–0.91 s` per percent). When reversing direction or starting from closed, the first ~`1.4 s` (< `2%` of travel) mechanically pivots the slats before the curtain begins lifting.

---

## Trace 1: `myhome_trace_F454_2026-10-23_anon.json` (220 frames)

### 1. H4660M2 Wall Keypad Short Pulses from Fully Closed (`10:23:00`–`10:24:22` UTC, frames `0..29`)
The H4660M2 has no short-step button, so the user pressed `UP` followed immediately by `STOP` three times to pivot the slats in steps, then pressed `DOWN` to close again:
- **Pulse 1 (`10:23:00.288`–`10:23:01.471`, `1.10 s` motor run)**:
  - Keypad `UP`: `*2*1000#11#100#001#1*21##` → F401 start `*#2*21*10*11*0*001*15##` + `*2*1*21##`
  - Keypad `STOP`: `*2*1000#10#001#1*21##` → F401 stop `*#2*21*10*10*2*001*15##` + `*2*0*21##` (`2%`, slats pivot)
- **Pulse 2 (`10:23:02.186`–`10:23:02.536`, `0.29 s` motor run)**:
  - Keypad `UP` + `STOP` → F401 starts at `2%` (`*#2*21*10*11*2*001*15##`) and stops at `2%` (`*#2*21*10*10*2*001*15##`, slats pivot further)
- **Pulse 3 (`10:23:03.658`–`10:23:04.060`, `0.35 s` motor run)**:
  - Keypad `UP` + `STOP` → F401 starts at `2%` and stops at `2%` (`*#2*21*10*10*2*001*15##`, curtain starts rising; all three sub-2% pulses report `2%`)
- **Return to Closed (`10:24:20.471`–`10:24:22.404`, `1.88 s` motor run)**:
  - Keypad `DOWN`: `*2*1000#12#100#001#1*21##` → F401 starts at `2%` (`*#2*21*10*12*2*001*15##`) and stops at bottom limit `0%` (`*#2*21*10*10*0*001*15##` + `*2*0*21##`).

### 2. Home Assistant `set_cover_position` 5% Steps (`10:25:06`–`10:26:17` UTC, frames `32..75`)
- Upward 5% steps (`5%` → `10%` → `15%` → `20%`) via `tx *#2*21*#11#001*<LEVEL>##` (~`4.44–4.55 s` per 5% step) and downward 5% steps (`15%` → `10%` → `5%`, ~`4.33–4.35 s` per step), followed by `tx *2*2*21##` for `0%` (~`4.44 s`).
- On every `Dimension 11` write `tx *#2*21*#11#001*<LEVEL>##`, the F454 echoes `*#2*21*#11#001#1*<LEVEL>##` on the monitor session ~`93–101 ms` later, followed ~`40 ms` later by F401's start `Dimension 10` frame `*#2*21*10*<11|12>*<START>*001*15##` + `*2*<1|2>*21##`, and upon reaching `<LEVEL>` by `*#2*21*10*10*<LEVEL>*001*15##` + `*2*0*21##`.

### 3. Home Assistant `set_cover_position` 2% Steps (`10:27:21`–`10:29:10` UTC, frames `78..187`)
- Upward steps from `0%`: `2%`, `4%`, `6%`, `8%`, `10%` (`1.74–1.81 s` per 2% step), then `15%` (`4.54 s`), `20%` (`4.55 s`).
- Direction reversal `20%` → `18%` (`1.73 s`), then downward 2% steps `16%`, `14%`, `12%`, `10%`, `8%`, `6%`, `4%`, `2%` (`1.72–1.75 s` per 2% step), and `0%` via `tx *2*2*21##` (`1.75 s`).
- Every 2% step (`~1.75 s`) already exceeds the ~`1.4 s` slat pivot duration, so the first 2% step both fully opens/closes the slats and moves the curtain slightly; `min_position_delta: 1` (~`0.9 s` per 1% step) is required on this shutter to reach intermediate slat angles via `Dimension 11`.

### 4. Full Open Run, Mid-Run Stop at 47%, and Close to 0% (`10:29:31`–`10:32:48` UTC, frames `190..219`)
- **Full Open (`0%` → `100%`, `91.28 s`)**: `tx *2*1*21##` → F454 relays leading idle `*2*1000#0*21##`, `*#2*21*10*10*0*001*15##`, `*2*1000#1*21##`, `*2*0*21##`, then F401 starts (`*#2*21*10*11*0*001*15##` + `*2*1*21##` at `10:29:31.592`) and emits **no intermediate `Dimension 10` frames during the 91.28 s run**, reporting `*#2*21*10*10*100*001*15##` + `*2*0*21##` at `10:31:02.874`.
- **Mid-Run Stop (`100%` → `47%`, `45.77 s`)**: `tx *2*2*21##` starts closing at `10:31:09.120` (`*#2*21*10*12*100*001*15##`); `tx *2*0*21##` at `10:31:54.765` stops the F401 at `47%` (`*#2*21*10*10*47*001*15##` at `10:31:54.885`).
- **Close to Bottom (`47%` → `0%`, `41.93 s`)**: `tx *2*2*21##` starts closing at `10:32:06.489` (`*#2*21*10*12*47*001*15##`) and stops at the bottom limit switch at `10:32:48.420` (`*#2*21*10*10*0*001*15##` + `*2*0*21##`).

---

## Trace 2: `myhome_trace_F454_all_2026-10-23T11-11-26.json` (125 frames)

Configured with `slat_tilt: true`, `slat_time: 1.4`, and `min_motor_pulse: 0.2` on the same advanced F401 cover `21`:

| Step | Time (UTC) | Tilt Command | TX Start / Stop | Motor Run (`10*11`/`12` → `10*10`) | Reported `Dimension 10` Level | Observed Slat Angle |
|---|---|---|---|---|---|---|
| 1 | `11:02:47.920` | `0%` → `15%` (`0.21 s`) | `tx *2*1*21##` → `tx *2*0*21##` (`+200 ms` after `*2*1*21##`) | `0.320 s` | `0%` → `1%` | Slightly open |
| — | — | `15%` → `20%` (`0.07 s`) | *(skipped: `< min_motor_pulse: 0.2`)* | — | `1%` | Unchanged |
| 2 | `11:03:40.212` | `15%` → `0%` (`0.21 s`) | `tx *2*2*21##` → `tx *2*0*21##` (`+198 ms` after `*2*2*21##`) | `0.312 s` | `1%` → `0%` | Closed |
| 3 | `11:04:20.074` | `0%` → `20%` (`0.28 s`) | `tx *2*1*21##` → `tx *2*0*21##` (`+241 ms` after `*2*1*21##`) | `0.386 s` | `0%` → `0%` | Slightly open (same to the eye as 15%) |
| 4 | `11:04:51.630` | `20%` → `0%` (`0.28 s`) | `tx *2*2*21##` → `tx *2*0*21##` (`+239 ms` after `*2*2*21##`) | `0.386 s` | `0%` → `0%` | Closed |
| 5 | `11:05:10.777` | `0%` → `25%` (`0.35 s`) | `tx *2*1*21##` → `tx *2*0*21##` (`+319 ms` after `*2*1*21##`) | `0.486 s` | `0%` → `1%` | Slightly open (same to the eye as 15%–20%) |
| 6 | `11:05:27.807` | `25%` → `0%` (`0.35 s`) | `tx *2*2*21##` → `tx *2*0*21##` (`+308 ms` after `*2*2*21##`) | `0.453 s` | `1%` → `0%` | Closed |
| 7 | `11:05:35.420` | `0%` → `50%` (`0.70 s`) | `tx *2*1*21##` → `tx *2*0*21##` (`+686 ms` after `*2*1*21##`) | `0.810 s` | `0%` → `1%` | **Horizontal** |
| 8 | `11:05:57.624` | `50%` → `0%` (`0.70 s`) | `tx *2*2*21##` → `tx *2*0*21##` (`+645 ms` after `*2*2*21##`) | `0.793 s` | `1%` → `0%` | Closed |
| 9 | `11:06:17.124` | `0%` → `100%` (`1.40 s`) | `tx *2*1*21##` → `tx *2*0*21##` (`+1.390 s` after `*2*1*21##`) | `1.509 s` | `0%` → `1%` | **Fully open**, curtain just starting to lift |
| 10 | `11:07:11.369` | `100%` → `0%` (`1.40 s`) | `tx *2*2*21##` → `tx *2*0*21##` (`+1.361 s` after `*2*2*21##`) | `1.515 s` | `1%` → `0%` | Closed |
| 11 | `11:07:47.599` | `set_cover_position: 2` | `tx *#2*21*#11#001*2##` | `1.768 s` | `0%` → `2%` | Slats fully open + slight curtain lift (`2%`) |
