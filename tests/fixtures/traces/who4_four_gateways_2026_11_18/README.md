# WHO 4 on four gateways, two plants (2026-11-18)

Live captures taken while testing MyHOME#955. `frames.json` holds one list per trace: `[time, direction, frame]`,
local time (UTC+2). Only WHO 4 frames and the ACK (`*#*1##`) or NACK (`*#*0##`) that ends a request on a command
session; no address, MAC or name of either plant.

| Plant | Gateways | Devices |
|---|---|---|
| Bus 1 | F461 2.0.11, F455 1.0.76 | BTicino 3550 99-zone central unit (`#0`) running zones 3 (two plain heating valves) and 4 (a heating valve and a cooling fan coil); zone 2, a standalone thermostat with a fan coil that the 3550 does not run |
| Bus 2 | MyHomeServer1 3.87.13, 3488 3.2.5 | 34 standalone thermostats (zones 35 to 70): 29 heating only, 5 with a cooling fan coil (47, 48, 57, 60, 70); no central unit |

The 3488 was set up in Home Assistant under the model name `MyHomeServer1`, which the setup form suggests for a
gateway typed by hand (it answers `*#13**15##` with `*#13**15*200##` and `*#13**16##` with `*#13**16*3*2*5##`).

What each trace is, and how it was taken, is in `frames.json` under `_traces`. Directions: `tx` a frame written,
`rx` a frame received, `nack` (Home Assistant traces only) a status request the gateway refused.

Replayed by `tests/test_who4_four_gateways_2026_11_18.py`.
