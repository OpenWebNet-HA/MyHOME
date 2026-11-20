# Thermostats found at start, and a standalone zone next to a central unit: live capture, 2026-11-18

Recorded facts: never edit a frame. `frames.json` was copied by script from Home Assistant debug logs and from the logs
of a direct OpenWebNet client (WHO 4 frames only).

## Plant

One SCS bus, reached through two gateways at once (each Home Assistant instance sees the other's requests answered):

| Item | Value |
|---|---|
| Gateways | F461 firmware 2.0.11, F455 firmware 1.0.76 |
| Central unit | BTicino 3550 (`#0`), in antifreeze: `*4*102*#0##` |
| Zone 3 | of the 3550, two plain valves (`3#1`, `3#2`), heating: `*4*1*3##` |
| Zone 4 | of the 3550, a valve (`4#1`) and a fan coil (`4#2`), heating: `*4*1*4##` |
| Zone 2 | a standalone thermostat with a fan coil, not run by the 3550, conditioning: `*4*0*2##` |

One more read was taken on a MyHomeServer1 3.87.13 plant (`myhomeserver1_dim12_read`).

## What was wrong before

1. **No thermostat was found at start** on a gateway whose firmware drops the general request `*#4*0##`: nothing else
   asked a thermostat, so a freshly added F461 had no climate entity until a zone reported by itself.
2. **A zone with plain valves only** is not created from its actuator frames (#785: such an address may be a towel warmer
   with no thermostat), so even an actuator request did not create it.
3. **Zone 2 was shown `heat` (F461) or `auto` (F455) while it was cooling**, and offered no `cool`: found on the bus on a
   plant with a central unit, it was taken as one of the central unit's zones until its first setpoint write (#951).

## What the capture shows (`f461_blank_start`, `f455_blank_start`)

Both instances started with no climate entity in the registry and no `myhome.yaml`, 40 s after the 3550's quarter-hour
report, so no spontaneous frame creates a zone.

| | F461 instance | F455 instance |
|---|---|---|
| start | 16:00:51.631 | 16:00:52.004 |
| sweep | `*#4*#0##` 16:00:58.889, `*#4*0#0*20##` 16:01:00.020 | `*#4*0#0*20##` 16:00:58.906 |
| zone 3, valves only | `*#4*3*0##` 16:01:05.314, answer `*#4*3*0*0260##` | `*#4*3*0##` 16:01:04.381, answer `*#4*3*0*0260##` |
| zone 2's season | `*4*0*2##` | `*4*0*2##` |
| confirmation read, 10 s later | `*#4*2*12##` 16:01:31.601 | `*#4*2*12##` 16:01:30.712 |
| zone 2 handled as standalone | 16:01:31.628 | 16:01:30.711 |
| result | central unit and zones 2, 3, 4 with temperature and setpoint; zone 2 `cool`, modes off / heat / cool | the same |

The time of a `tx` row is the "successfully sent" log line, written when the reply is complete; the frame left earlier.

Seen in the Home Assistant logs of the same start (a refused read leaves no `tx` row), not changed here:
- F461: `*#4*4*11##` is refused twice (the fan speed of zone 4, whose fan coil is on the cooling side);
- F455: zone 3's reply came back for the read of zone 2 (`*#4*2*0##`, "Gateway FIFO desync" at 16:01:05.300) and the read
  of zone 3 was sent once more (16:01:07.400).

## The confirmation read (`*_dim12_reads`, `myhomeserver1_dim12_read`)

WHO 4 document, section 1.5.3: `*#4*where*12##` is answered with `*#4*where*12*T*3##` and the zone operation mode frame
`*4*what*where##` (0 Conditioning, 1 Heating, 102 Antifreeze, 202 Thermal Protection, 303 Generic OFF).

| Gateway | Read | Answer | Time |
|---|---|---|---|
| F461 2.0.11 | `*#4*2*12##` | `*#4*2*12*0200*3##`, `*4*0*2##` | 3.7 s |
| F461 2.0.11 | `*#4*3*12##` | `*#4*3*12*0265*3##`, `*4*1*3##` | 2.5 s |
| F455 1.0.76 | `*#4*2*12##` | `*#4*2*12*0200*3##`, `*4*0*2##` | 0.1 s |
| F455 1.0.76 | `*#4*3*12##` | `*#4*3*12*0265*3##`, `*4*1*3##` | 0.1 s |
| MyHomeServer1 3.87.13 | `*#4*70*12##` | `*#4*70*12*0230*3##`, `*4*1*70##` | 0.2 s |

Other gateways: every emulated firmware of own-firmware-oracle puts `*#4*1*12##` on the bus (`trace-corpus.tsv:57` of
each of the 13 images). In this corpus a zone's dimension 12 frame comes with its mode frame on F453, F453AV, F454, F455,
F461, H4684, H4890, MH200N, MH201, MH202 and MyHomeServer1; there is no dimension 12 frame from an F459, an MH200 or a
067292. The integration's silent-zone poll already sends this read on every model (#466, #932).

## What a user does (`user_actions_*.log`)

The same sequence through Home Assistant's services on both gateways, once with the zones found on the bus
(`user_actions_discovered_*`) and once with them listed in `myhome.yaml` (`user_actions_myhome_yaml_*`). `CALL` is the
service call, `->` the entity ten or more seconds later, `READ via F461` a read of the plant through the F461.

| Action | Entity | Plant |
|---|---|---|
| zone 2 setpoint 20.0 -> 21.5 | 21.5 | `*#4*2*14*0215*3##` |
| zone 2 off / cool / heat / cool | off / cool / heat / cool | `*4*303*2##` / `*4*0*2##` / `*4*1*2##` / `*4*0*2##` |
| zone 3 setpoint 24.0, off, heat, 26.5 | follows | `*#4*3*14*0240*3##`, `*4*103*#3##`, `*4*110*#3##`, `*#4*#3*14*0265*1##` |
| zone 4 setpoint 24.5, off, heat, 26.5 | follows | `*#4*4*14*0245*3##`, `*4*103*#4##`, `*4*110*#4##`, `*#4*#4*14*0265*1##` |
| central unit off | off; zones 3 and 4 off; zone 2 stays `cool` at 20.0 | `*4*103*#0##` |
| central unit heat | stays off (`*4*1*#0##` is the season frame; not changed here) | - |
| central unit 21.0 | heat; zones 3 and 4 at 21.0; zone 2 unchanged | `*4*110#0210*#0##` |

No service call failed and no ERROR was logged in any of the four runs; the plant was put back as it was.

## Not established by this capture

- A MyHomeServer1 or any other gateway running the integration with this start-up logic: only the read above.
- An address with actuators and no thermostat (none on this plant).
- A standalone thermostat in the same season as the central unit: it is not told apart by its season; its write form is
  still found by its first setpoint write (#951).
