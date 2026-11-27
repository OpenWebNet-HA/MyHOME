# #994 F454 with a 4-zone central unit (`#0#1`)

Bus Monitor exports from a physical **BTicino F454** gateway (firmware 2.0.51, Home Assistant 2026.11.2;
Nov 22 captures on MyHOME 2.0.0 / OWNd 2.0.1, Nov 26 verification capture on MyHOME 2.0.1 / OWNd 2.0.2) on
a heating plant with a **4-zone central unit** that reports as `#0#1` and zones 1, 3 and 4. The first
captures of such a plant.

Contributed by **@paolozamberletti-maker** on [#994](https://github.com/OpenWebNet-HA/MyHOME/issues/994) (third file in [comment 7202325704](https://github.com/OpenWebNet-HA/MyHOME/issues/994#issuecomment-7202325704)). Thank you!

| File | Content |
|---|---|
| `myhome_trace_F454_all_2026-11-22T18-38-46.json` | 14 frames (`13` rx, `1` tx): a status burst of the central unit and its zones, then a setpoint written from Home Assistant 2.0.0 on the auto-discovered zone 1 (frame 13, `tx`); the capture ends 0.08 s after it |
| `myhome_trace_F454_all_2026-11-22T21-40-54.json` | 75 frames (`68` rx, `7` tx), 7 sent by hand: status read, setpoint writes, off, a weekly program |
| `myhome_trace_F454_all_2026-11-26T20-03-46.json` | 18 frames (`16` rx, `2` tx), `MyHOME 2.0.1` / `OWNd 2.0.2` verification capture (`truncated: false`, SHA-256 `b97ab2691c3e046f10a25d84a4e84afea1b03a9f5121b70599e2c2316ee92127`): HA sets zone 1 / `#0#1` to 18.5 °C (propagating to zones 3 and 4 and activating actuators `1#1` and `4#1`), then sets zone 3 independently to 18.0 °C |

## What it shows

Frame numbers are positions in the second file (1-based).

| Sent | What the plant answered |
|---|---|
| 1 `*#4*#0#1##` | 2-4 `*4*110#0160*#0#1##`, `*4*21*#0#1##`, `*4*31*#0#1##` |
| 5 `*#4*#0#1*#14*0170*3##` | 6-8 `*4*110#0170*#0#1##`, `*4*21*#0#1##`, `*4*31*#0#1##`; zone 1 `*#4*1*13*00##`, `*#4*1*0*0194##`; zone 3 `*#4*3*12*0170*3##`, `*4*1*3##`; zone 4 `*#4*4*12*0170*3##`, `*4*1*4##` |
| 19 `*#4*#0#1*#14*0175*1##` | 20-22 `*4*110#0175*#0#1##`, `*4*21*#0#1##`, `*4*31*#0#1##`; zone 3 `*#4*3*12*0175*3##`, `*4*1*3##`; zone 4 `*#4*4*12*0175*3##`, `*4*1*4##` |
| 33 `*#4*3*#14*0170*3##` | 34-36 `*#4*3*0*0195##`, `*#4*3*12*0170*3##`, `*4*1*3##`: zone 3 alone |
| 37 `*#4*#3#1*#14*0175*3##` | nothing until the next frame sent, 48 s later |
| 38 `*4*303*#0#1##` | 39-41 `*4*103*#0#1##`, `*4*21*#0#1##`, `*4*31*#0#1##`; `*4*303*1##`, `*#4*1*12*0175*3##`, `*4*303*3##`, `*#4*3*12*0170*3##`, `*4*303*4##`, `*#4*4*12*0175*3##` |
| 55 `*4*1103*#0#1##` | zones `*4*1*1##`, `*#4*1*12*0140*3##`, `*4*1*3##`, `*#4*3*12*0140*3##`, `*4*1*4##`, `*#4*4*12*0140*3##`; then 66-69 `*4*1103*#0#1##`, `*4*21*#0#1##`, `*4*31*#0#1##`, `*4*22*#0#1##` |

### `v2.0.1` verification capture (third file, 1-based)

| Sent (from Home Assistant `2.0.1`) | What the plant answered |
|---|---|
| 1 `*#4*#0#1*#14*0185*3##` | 2-4 `*4*110#0185*#0#1##`, `*4*21*#0#1##`, `*4*31*#0#1##`; zone 1 (5-6) `*#4*1*13*00##`, `*#4*1*0*0143##`; zone 3 (7-9) `*#4*3*0*0162##`, `*#4*3*12*0185*3##`, `*4*1*3##`; zone 4 (10-12) `*#4*4*0*0159##`, `*#4*4*12*0185*3##`, `*4*1*4##`; actuators ON (13-14) `*#4*1#1*20*1##` (+3.35 s), `*#4*4#1*20*1##` (+9.11 s) |
| 15 `*#4*3*#14*0180*3##` | 16-18 `*#4*3*0*0162##`, `*#4*3*12*0180*3##`, `*4*1*3##`: zone 3 alone (`#0#1` and zone 4 stay at 18.5 °C) |

- A write to the central unit `#0#1` sets every zone: zones 3 and 4 follow to the same setpoint, and in the third
  file actuators `1#1` and `4#1` turn ON (`Dimension 20 = 1`) as both rooms are below 18.5 °C.
- One zone is written directly (`*#4*3*#14*T*3##`). `*#4*#3#1*#14*0175*3##` (frame 37 of the second file) gets no answer.
- The form a plant with a 99-zone central unit requires (`../issue_697/README.md`) is `*#4*#Z*#14*T*3##`. The first
  file holds one such write, `*#4*#1*#14*0150*3##` (frame 13), and ends 0.08 s after it, so the capture does not
  show whether anything followed. That the plant does not act on that form is the reporter's account in the issue.
- The central unit answers the status read `*#4*#0#1##`.
- Not in these captures: a direct write to zone 1, `*4*1*#0#1##`, `*4*311*...`, any received dimension 14 frame
  (the dimension 14 frames above are the writes that were sent), the gateway's ACK or NACK of any sent frame.
