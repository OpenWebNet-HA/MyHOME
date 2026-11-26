# Bus Monitor trace: MyHomeServer1 + K4672M2S, slat pulses on an external Venetian blind (PR #991)

Field test of PR #991 on a real external Venetian blind, contributed by Claripinto:
https://github.com/OpenWebNet-HA/MyHOME/pull/991#issuecomment-7097236466 (report) and
https://github.com/OpenWebNet-HA/MyHOME/pull/991#issuecomment-7097296015 (the file).

## Provenance

- **File**: `myhome_trace_MyHomeServer1_all_2026-11-23T09-34-10.json`, the Bus Monitor export as attached, unedited
  (60 frames, not truncated; the export carries the MAC prefix `00:03:50` only).
- **Gateway**: MyHomeServer1, firmware 2.87.13.
- **Actuator**: BTicino K4672M2S at WHERE `31`, motor mode "Standard", PUL off.
- **Blind**: Lupak Metal "Lume" external Venetian blind, Somfy J410 HTM motor (mechanical limit switches).
- **Environment**: Home Assistant 2026.11.2, MyHOME 2.0.1 + PR #991, OWNd 2.0.1.
- **Configuration of the cover**: `slat_tilt: true`, `slat_time: 0.8`, `min_motor_pulse: 0.2`, `min_position_delta: 1`.
- **Measured by the reporter on a 240 fps video**: moving up from closed, the slats reach horizontal in 0.77-0.80 s
  and the pack starts lifting at about 0.83 s.

## Steps and what Home Assistant showed (the reporter's table)

| Step | Action | Frames (index) | Home Assistant | Blind |
|---|---|---|---|---|
| 1 | position 0 | 0-8 | position 0, tilt 0 | fully down, slats closed |
| 2 | tilt 0 -> 50 | 9-19 | position 1, tilt 57 | slats half open, no lift |
| 3a | tilt 57 -> 75 | none | unchanged | no frame: the pulse would be shorter than `min_motor_pulse` |
| 3b | tilt -> 100 | 20-30 | position 2, tilt 100 | slats almost horizontal |
| 4 | tilt 100 -> 50 | 31-41 | position 1, tilt 42 | back to the same half-open angle |
| 5a | position 6 (`*#2*31*#11#001*6##`) | 42-47 | tilt 100 | slats horizontal, pack lifting |
| 5b | position 3 (`*#2*31*#11#001*3##`) | 50-55 | tilt 0 | slats closed |
| 6 | wall keypad, short "up" | 56-59 | position 3 -> 5, tilt 52 | slats half open |

Frames 48 and 49 are the gateway's date and time broadcast (WHO 13), not part of a step.

## What the trace shows

- **The stopped level one step further after each slat pulse.** After the pulse's stop the actuator reports
  `*#2*31*10*10*1*001*0##` (frame 18, level 0 -> 1), `*#2*31*10*10*2*001*0##` (frame 29, 1 -> 2) and
  `*#2*31*10*10*1*001*0##` (frame 40, 2 -> 1): it counts the pulse as travel. This is the report the PR keeps from
  turning the slats a full rotation; on a MyHomeServer1 it was known from a description only (#868).
- **The gateway stops the actuator before it moves it.** Each direction command is followed by `*2*1000#0*31##`, a
  stopped level and `*2*0*31##` before `*2*1000#1*31##` / `*2*1000#2*31##` and the moving level (frames 1-6, 10-15,
  21-26, 32-37). The motor starts 0.209 s, 0.234 s and 0.265 s after the command (frames 9 -> 14, 20 -> 25,
  31 -> 36), and the integration times the pulse from that moving level, not from its own command: frame 14 to the
  stop command of frame 16 is 0.422 s for a 0.4 s pulse.
- **A keypad pulse comes with motion frames** (frame 56, `*#2*31*10*11*3*001*0##`, then the stopped level of frame
  58, 0.419 s later): the slats are turned in proportion to that time, 0.419 / 0.8 = 52 %. Only a level that
  changes with no motion frame before it turns them all the way.

## Replay

`tests/test_pr_991_myhomeserver1_k4672m2s_trace.py` replays the 60 frames on the capture's clock and asserts the
position and tilt of every step. The export holds no acknowledgement times: the replay confirms each written
frame when the next frame is received, which gives exactly the tilt values the reporter read (57, 42).
