# Bus Monitor trace: MyHomeServer1 + F401, slat pulses on an external Venetian blind (PR #991)

Second field test of PR #991, contributed by Claripinto, on the same plant as
`tests/fixtures/traces/pr_991_myhomeserver1_k4672m2s`: same gateway, same blind model and motor, another actuator.
https://github.com/OpenWebNet-HA/MyHOME/pull/991#issuecomment-7104500956 (report) and
https://github.com/OpenWebNet-HA/MyHOME/pull/991#issuecomment-7104589682 (the file).

## Provenance

- **File**: `myhome_trace_kitchen_MyHomeServer1_all_2026-11-23T14-42-26.json`, the Bus Monitor export as attached,
  unedited (82 frames, not truncated; the export carries the MAC prefix `00:03:50` only).
- **Gateway**: MyHomeServer1, firmware 2.87.13.
- **Actuator**: BTicino F401 (DIN rail) at WHERE `62`.
- **Blind**: Lupak Metal "Lume" external Venetian blind, Somfy J410 HTM motor, the same models as the blind of the
  K4672M2S run. The reporter notes that this blind is more worn: its bottom slat jammed in a first attempt, and
  the capture is the second run, started again from step 1.
- **Environment**: Home Assistant 2026.11.2, MyHOME 2.0.1 + PR #991 (`dfc9499`), OWNd 2.0.1.
- **Configuration of the cover**: `slat_tilt: true`, `slat_time: 0.8`, `min_motor_pulse: 0.2`, `min_position_delta: 1`.
  `slat_time` is the value measured on the other blind; it was not measured again here.

## Steps and what Home Assistant showed (the reporter's table)

| Step | Action | Frames (index) | Home Assistant | Blind |
|---|---|---|---|---|
| 1 | position 0 | 0-8 | position 0, tilt 0 | fully down, slats closed |
| 2 | tilt 0 -> 50 | 13-23 | position 1, tilt 58 | slats half open, no lift |
| 3a | tilt 58 -> 75 | none | unchanged | no frame: the pulse would be shorter than `min_motor_pulse` |
| 3b | tilt -> 100 | 28-38 | position 1, tilt 100 | slats horizontal, pack not lifting |
| 4 | tilt 100 -> 50 | 47-57 | position 0, tilt 42 | back to the half-open angle; the entity is `closed` |
| 5a | position 6 (`*#2*62*#11#001*6##`) | 62-67 | position 6, tilt 100 | slats horizontal, first two slats lifted |
| 5b | position 3 (`*#2*62*#11#001*3##`) | 68-73 | position 3, tilt 0 | slats closed; the two bottom slats stayed stacked |
| 6 | wall keypad, short "up" | 74-79 | position 3 -> 5, tilt 76 | from the third slat up about half open |

Other frames: 9-12, 24-27, 39-42 and 58-61 are light frames (WHO 1), 43-46 another blind (WHERE `0012`), 80-81 the
gateway's date and time broadcast (WHO 13).

## What the trace shows

- **The stopped level after each slat pulse, and how the F401 differs from the K4672M2S.** After the pulse's stop
  the actuator reports `*#2*62*10*10*1*001*0##` (frame 22, level 0 -> 1), `*#2*62*10*10*1*001*0##` (frame 37,
  unchanged at 1) and `*#2*62*10*10*0*001*0##` (frame 56, 1 -> 0), 0.07-0.12 s after the stop command and with no
  moving level before it. The K4672M2S went 0 -> 1, 1 -> 2 and 2 -> 1 for the same three pulses. Each time the
  angle the pulse set is kept (58, 100, 42).
- **Level 0 with the slats half open.** The closing pulse of step 4 is booked as 1 -> 0, so the cover's position
  is 0 and its state `closed` while the tilt is 42.
- **The gateway stops the actuator before it moves it**, as in the K4672M2S run: `*2*1000#0*62##`, a stopped level
  and `*2*0*62##` come before the moving level (frames 14-18, 29-33, 48-52). The motor starts 0.198 s, 0.208 s and
  0.195 s after the command (frames 13 -> 18, 28 -> 33, 47 -> 52), and the pulse is timed from that moving level:
  frame 18 to the stop command of frame 20 is 0.424 s for a 0.4 s pulse.
- **A level command downwards without a moving level** (step 5b): after `*#2*62*#11#001#1*3##` the actuator
  reports the stopped level 6 (frame 70) and, 1.5 s later, the stopped level 3 (frame 72). The command was Home
  Assistant's own, so the slats are closed with the curtain.
- **The keypad pulse** (step 6) is announced as advanced commands: `*2*1000#11#100#001#1*62##` (frame 74) for the
  press and `*2*1000#10#001#1*62##` (frame 77) for the release. The K4672M2S capture has neither. The moving level
  of frame 75 to the stopped level of frame 78 is 0.612 s, and the slats are turned in proportion: 0.612 / 0.8 =
  76.5 %. The reporter saw about half open above the two stacked bottom slats, so the estimate was too high here.

## Replay

`tests/test_pr_991_myhomeserver1_f401_trace.py` replays the 82 frames on the capture's clock and asserts the
position and tilt of every step. The export holds no acknowledgement times: the replay confirms each written
frame when the next frame is received, which gives the reporter's values for steps 1 to 5b. Step 6 comes out as
77 where the reporter read 76: the pulse is 0.612181 s on the export's clock, 76.52 % of 0.8 s.
