# WHO 22 on a MyHomeServer1: F500 tuner, F441 matrix and H4562 amplifiers

Raw OpenWebNet frames captured on a physical plant by **@bboykaos**, who enabled debug logging on a
running Home Assistant instance and operated the controls by hand. Contributed in
[OpenWebNet-HA/OWNd#103](https://github.com/OpenWebNet-HA/OWNd/pull/103#issuecomment-6113362654).
Thank you!

## Plant

- **Gateway**: BTicino MyHomeServer1 (firmware 2.x)
- **FM tuner**: BTicino F500 stereo FM RDS tuner, configured as source 1 (`2#1`, compound `5#2#1`)
- **Audio matrix**: BTicino F441 (input 1 = F500, input 2 = external streamer / AUX 2)
- **Amplifiers**: BTicino H4562 in-wall units, 16 zones (e.g. Living Room `3#7#5`, Kitchen `3#7#6`)
- **Paging**: whole-house intercom push button, broadcast address `5#3#0#0`

The gateway address was deliberately left out of the fixtures.

## Files

| File | Content |
|---|---|
| `trace1_living_room_3-7-5.json` | Power ON with source, F500 preset change with RDS, matrix switch to AUX 2, volume steps, atomic OFF |
| `trace2_kitchen_3-7-6.json` | Power ON into source 2, physical H4562 keypress, matrix cycling, atomic OFF |
| `trace3_whole_house_paging.json` | Paging open (`19#1`), voice start (`20#1`), global ducking (`12*0*10`), volume restore |

These are **hand-transcribed excerpts** from the reporter's comment, not exports from the bus monitor.
Repeated frames the reporter elided with `...` are not reconstructed; every frame listed appears
literally in the comment. Each frame carries a short `note` with the reporter's interpretation.

## What the traces show

- **Tuner frequency is in 100 kHz units**: `*#22*5#2#1*11*1*1015*5##` is 101.5 MHz, preset 5; `933` is 93.3 MHz.
- **RDS** arrives as ASCII codes in dimension 10 (`*#22*5#2#1*10*82*65*68*73*79##` spells `RADIO`);
  the captured chunks carry 5 codes.
- **Dimension 12 `STATE*X`**: with state 1, `X` is the audio channel / source (1 or 2); `12*0*10`
  appears as standby/ducking during paging and after atomic OFF.
- **Matrix routing**: `*22*35#4#1#N*<zone>##` routes a zone to source `N`.
- **WHAT forms**: `1#4#1` ON with source, `0#4#0` atomic OFF, `9`/`2#1` next preset on source 1,
  `19#1` / `20#1` paging open / voice, `3#1` / `4#1` volume step up / down.
- **Physical keypress**: `*22*9*5#3#7#6##` (WHERE `5#3#A#P`) is emitted by the H4562 keypad.

These behaviours are not yet all decoded by OWNd; see the OWNd#103 discussion.
