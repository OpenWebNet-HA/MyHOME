# A zone switched off and on again before its actuator had stopped (2026-11-19)

Live captures taken while testing MyHOME#955. `frames.json` holds one list per trace: `[time, direction, frame]`,
local time (UTC+2). Only WHO 4 frames, taken from the debug log of one Home Assistant instance per gateway: `tx` a
frame the instance sent (logged when the gateway acknowledged it), `rx` a frame it received. No address, MAC or name
of either plant.

| Plant | Gateways | Zone in these traces |
|---|---|---|
| Bus 1 | F461 2.0.11, F455 1.0.76 | zone 2, a standalone thermostat with a fan coil (actuator `2#1`), cooling at 22.0 with 23.5 measured |
| Bus 2 | MyHomeServer1 3.87.13, 3488 3.2.5 (set up under the model name `MyHomeServer1`) | zone 53, a thermostat with a plain heating valve (`53#1`), open |

What they show:

- `f461_zone2_off_8s`: off for 8.3 s. The fan coil reports its off (`*#4*2#1*20*5##`) 3.1 s after the zone's and its
  on (`*#4*2#1*20*8##`) 4.9 s after the zone runs again.
- `f461_zone2_off_1s`: off for 1.2 s, before the change. No frame of the actuator follows the off or the on, and Home
  Assistant showed the zone idle. 98.7 s after the zone was on again another client asked every actuator, and the fan
  coil answered `*#4*2#1*20*8##`: it had not stopped.
- `f461_zone2_off_2s_asked`, `f455_zone2_off_2s_asked`: off for 2.0 s, with the change. The instance asks every
  actuator (`*#4*0#0*20##`) once the zone runs and the fan coil's `*#4*2#1*20*8##` comes 1.3 s after the zone's on.
- `myhomeserver1_zone53_off_2s_asked`, `g3488_zone53_off_2s_asked`: the same on the other plant, a plain valve.
- `f461_zone2_off_2s_actuator_reported`: off for 2.5 s, and this time the fan coil reported its off 2.1 s after the
  zone's and its on 5.6 s after the zone ran again. A first version of the change asked here as well; the rule
  does not ask for an actuator that has reported.

What each trace is, and how it was taken, is in `frames.json` under `_traces`.

Replayed by `tests/test_actuators_asked_after_short_off_2026_11_19.py`.
