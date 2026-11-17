# #684 A=0 PL=2 physical press on an MH200 (Home + Project plant)

Frames seen on the event session (`*99*1##`) of a physical **MH200** (firmware 2.1.2, `*#13**15*4##`) while the
reporter pressed the wall switch of an F411/4 output that Home + Project addressed as **A=0 PL=2**. A BTicino F460
(Home + Project / Home + Control server, firmware 2.0.18) sits on the same installation. Reported by **@peterbeepping**
on [#684](https://github.com/OpenWebNet-HA/MyHOME/issues/684#issuecomment-6113090645). Thank you!

The gateway address is left out. These are hand-transcribed excerpts of the reporter's capture
`own_capture_luister_20261011_175623`, not bus-monitor exports.

| File | Content |
|---|---|
| `physical_press_a0pl2_mh200.json` | Four presses: `*1*1*2##` / `*1*0*2##`, each followed by the nine-frame area-2 burst `*1*0*21##` .. `*1*0*29##` |

## What it shows

- The point reports as the single-digit `WHERE 2`, indistinguishable on the wire from the **area 2** broadcast `*1*x*2##`.
- 0.1-1.1 s after each press the gateway emits `*1*0*21##` .. `*1*0*29##` about 40-50 ms apart: a state refresh of area 2.
  A commanded change does not trigger it (reporter's observation). Nothing in the capture says whether the MH200 or the
  F460 issues the refresh.
- Firmware oracle (`lights-area0` suite): MH200N / F453AV / F454 / F459 ACK the two-digit `*1*1*02##` and
  `*1*1*02#4#01##` with a telegram distinct from `*1*1*2##`; no emulated gateway accepts the four-digit `0002`
  (the MH202 ACKs it but translates it to `*1*1000#1*02##`). The physical MH200 2.1.2 NACKs both `02` and `0002`.
