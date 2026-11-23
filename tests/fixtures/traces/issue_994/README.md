# #994 F454 with a 4-zone central unit (`#0#1`)

Bus Monitor exports from a physical **BTicino F454** gateway (firmware 2.0.51, MyHOME 2.0.0, OWNd 2.0.1,
Home Assistant 2026.11.2) on a heating plant with a **4-zone central unit** that reports as `#0#1` and
zones 1, 3 and 4. The first captures of such a plant.

Contributed by **@paolozamberletti-maker** on [#994](https://github.com/OpenWebNet-HA/MyHOME/issues/994). Thank you!

| File | Content |
|---|---|
| `myhome_trace_F454_all_2026-11-22T18-38-46.json` | 14 frames: a setpoint change made on the central unit, then a setpoint written from Home Assistant by auto-discovery (frame 13, `tx`) |
| `myhome_trace_F454_all_2026-11-22T21-40-54.json` | 75 frames, 7 sent by hand from the Bus Monitor: status read, setpoint writes, off, a weekly program |

## What it shows

Frame numbers are positions in the second file (1-based).

| Sent | What the plant answered |
|---|---|
| 1 `*#4*#0#1##` | 2-4 `*4*110#0160*#0#1##`, `*4*21*#0#1##`, `*4*31*#0#1##` |
| 5 `*#4*#0#1*#14*0170*3##` | 6-8 `*4*110#0170*#0#1##`, `*4*21*#0#1##`, `*4*31*#0#1##`; zone 1 `*#4*1*13*00##`, `*#4*1*0*0194##`; zone 3 `*#4*3*12*0170*3##`, `*4*1*3##`; zone 4 `*#4*4*12*0170*3##`, `*4*1*4##` |
| 19 `*#4*#0#1*#14*0175*1##` | 20-22 `*4*110#0175*#0#1##`, `*4*21*#0#1##`, `*4*31*#0#1##`; zone 3 `*#4*3*12*0175*3##`, zone 4 `*#4*4*12*0175*3##` |
| 33 `*#4*3*#14*0170*3##` | 34-36 `*#4*3*0*0195##`, `*#4*3*12*0170*3##`, `*4*1*3##`: zone 3 alone |
| 37 `*#4*#3#1*#14*0175*3##` | nothing until the next frame sent, 48 s later |
| 38 `*4*303*#0#1##` | 39-41 `*4*103*#0#1##`, `*4*21*#0#1##`, `*4*31*#0#1##`; `*4*303*1##`, `*#4*1*12*0175*3##`, `*4*303*3##`, `*#4*3*12*0170*3##`, `*4*303*4##`, `*#4*4*12*0175*3##` |
| 55 `*4*1103*#0#1##` | zones `*4*1*1##`, `*#4*1*12*0140*3##`, `*4*1*3##`, `*#4*3*12*0140*3##`, `*4*1*4##`, `*#4*4*12*0140*3##`; then 66-69 `*4*1103*#0#1##`, `*4*21*#0#1##`, `*4*31*#0#1##`, `*4*22*#0#1##` |

- A write to the central unit `#0#1` sets every zone: zones 3 and 4 follow to the same setpoint.
- One zone is written directly (`*#4*3*#14*T*3##`). The form through a central unit gets no answer: `#3#1` here
  (frame 37), and `*#4*#1*#14*0150*3##` in the first file (frame 13), the form a plant with a 99-zone central unit
  requires (`../issue_697/README.md`).
- The central unit answers the status read `*#4*#0#1##`.
- Not in these captures: a direct write to zone 1, `*4*1*#0#1##`, `*4*311*...`, any dimension 14 frame.
