# Live capture: Legrand / BTicino MyHomeServer1 (firmware 3.87.13), 2026-11-09

Authentic bus sweep and WHO 4 actuator toggling trace captured on @andreasorbelli's physical
plant via Home Assistant 2026.11.0, integration 2.0.0b17 on OWNd 2.0.0b13
(pull request [#864](https://github.com/OpenWebNet-HA/MyHOME/pull/864), issue [#863](https://github.com/OpenWebNet-HA/MyHOME/issues/863)).

## Plant

| Item | Value |
|---|---|
| Gateway | BTicino MyHomeServer1 (`*#13**15*200##`), firmware 3.87.13 (`*#13**16*3*87*13##`), Linux kernel 5.10.35 (`*#13**23*5*10*35##`), distribution 2.26.3 (`*#13**24*2*26*3##`), WHO 1013 object model `67.15.5.0` (`*#1013**1*67*15*5*0##`), MAC prefix `00:03:50` |
| Thermoregulation | Zone 30 floor heating with 7 physical F430 actuators (`30#1`..`30#6`, `30#9`). The user toggled `30#4` (`switch.riscaldamento_sala`) on and off at the end of the sweep |
| Lighting | 42 active lighting actuators responding to `*#1*0##` (two-digit addresses `01`..`13` and four-digit area-0 `0001`..`0016`) |
| Energy Management | Device at `WHERE = 51` actively streaming active power (`DIMENSION 113`) at ~1 Hz (e.g. 263 W, 221 W, 186 W, 226 W, 281 W, 311 W, 349 W, 360 W) |
| Automation | `*#2*0##` ACKed without data (no covers on this plant) |

## File format: `myhome_sweep_MyHomeServer1_all_2026-11-09T13-00-57.json`

Standard `MyHOME` bus monitor sweep export (115 frames, 69 RX, 46 TX):
- `capture`: kind `"sweep"`, 2026-11-09 13:00:05 to 13:00:53 UTC (48 seconds).
- `gateway`: MyHomeServer1, firmware 3.87.13, MAC prefix `00:03:50`.
- `environment`: HA 2026.11.0, integration 2.0.0b17, OWNd 2.0.0b13.
- `frames`: 115 frames covering WHO 13/1013 (gateway discovery), WHO 1 (lights), WHO 2 (covers), WHO 18 (energy), and WHO 4 (actuator #4 toggling).

## What the trace establishes

1. **Physical confirmation of Issue #863 / PR #864 fix**:
   - HA commands actuator 4 ON: `*#4*30#4*#20*1##` (TX at 13:00:49.481).
   - Bus event confirmation: `*#4*30#4*20*1##` (RX at 13:00:49.604, +122.8 ms).
   - HA commands actuator 4 OFF: `*#4*30#4*#20*0##` (TX at 13:00:51.734).
   - Bus event confirmation: `*#4*30#4*20*0##` (RX at 13:00:51.860, +125.8 ms).
   - Before PR #864, `*#4*30#4*20*1##` was misrouted to actuator `"1"` because `tail = ['4']` had `tail[0] == "4"` mistaken for an F422 `#4#IF` bus interface marker. The physical trace confirms that `MyHomeServer1` emits `*#4*30#4*20*1##` on actuator toggle, and PR #864 correctly routes it to actuator `"4"`, leaving actuator `"1"` unchanged.
2. **Gateway general heating refusal**:
   - `*#4*0##` is withheld by `sweep_bus` because `MyHomeServer1Profile` sets `general_zone_request_refused = True`, avoiding an unnecessary NACK delay during diagnostic sweeps.
3. **Lighting & Energy telemetry**:
   - Lighting sweep `*#1*0##` returns 42 frames within 400 ms.
   - Energy queries (`*#18*51*51##`, `*#18*51*1200##`) start a live active-power streaming session (`DIMENSION 113`).
