# Live capture: Legrand / BTicino MyHomeServer1 (firmware 3.87.13), 2026-10-24

Status reads only (no command was sent) against a physical MyHomeServer1 in a large home plant,
2026-10-24 13:19 to 13:22 UTC, recorded with OWNd master (`OWNCommandSession` + `OWNEventSession`,
`tools/probe.py` as for the F461 captures). The purpose: the facts established on the F461 2.0.11
plant on 2026-10-23 (`tests/fixtures/traces/f461_live_2026-10-23/`) checked on a second gateway
family for OWnd #137, #139, #140, #141, #142 and MyHOME #791-#794, #798.

Two caveats that apply to every row: the firmware oracle (`own-firmware-oracle`) emulates
MyHomeServer1 **2.82.06**, this unit runs **3.87.13**; and `*#13**12##` returns a real MAC address
here (synthetic in the fixture), where the emulated 2.82.06 returns zeros (oracle PR #59).

## Plant

| Item | Value |
|---|---|
| Gateway | MyHomeServer1, firmware 3.87.13 (`*#13**16*3*87*13##`), WHO 13 device type `200`, WHO 13 dimension 27 model name `77#121#72#111#109#101#83#101#114#118#101#114#49` = "MyHomeServer1", WHO 1013 object model `67.15.5.0` (`*#1013**1*67*15*5*0##`), clock time zone `999` |
| Thermoregulation | WHO 4 zones 35-70 (34 zones report a temperature); the plant broadcasts setpoints as dimension 7 (`*#4*Z*7*1*1*T##`), temperatures (`*#4*Z*0*T##`), dimension 5 and humidity on 17 zones; every zone has actuator `Z#1`, five have a fan coil `Z#2`, and zone 0 has actuators `0#1`-`0#4`; `*#4*#0##` is unanswered (no central unit answers) |
| Lighting | about 100 WHO 1 points in 74 addresses: two-digit `11`-`69`, four-digit area-0 `0010`-`0015` and four-digit `0110`-`0515` |
| Automation | `*#2*0##` is ACKed without data |
| Not present | WHO 16 sound diffusion, WHO 5 alarm (both refused locally) |

## File format: `myhome_trace_MyHomeServer1_live_2026-10-24.json`

Standard `MyHOME` derived trace archive, the same layout as `f461_live_2026-10-23` (see its README for the
field table): `capture` (with `runs`), `gateway` (synthetic MAC `00:03:50:00:20:24`, also as the WHO 13 dimension 12
reply `*#13**12*0*3*80*0*32*36##`; documentation IP `192.0.2.9`; an `oracle_note`), `frames` (376 event-session
`rx` frames) and `rows` (613 `tx` / `rx` / `result` entries; `result.replies` are the frames collected on the
command session, `result.events_after` only the event-session frames that arrived while the request was pending).
As there, command-session `rx` rows carry `t_estimated: true` and sit at their request's `t` (the session hands the
replies over as a batch with the ACK / NACK), and `t` is one monotonic axis over the three runs
(`capture.runs[].t_offset` gives each run's start).

## The runs

| `run` | UTC | What it was |
|---|---|---|
| `run1_identify` | 13:19-13:20 | `*#13**15##`, `16`, `0`, `1`, `22`, `27`, `12`, `*#1013**1##`; 106 WHO 4 frames of the plant's own traffic meanwhile |
| `run2_reads_thermo_sound_alarm_lights` | 13:20-13:21 | `*#4*#0##`, `*#4*#0*14##`, `*#4*35##`, `*#4*35*12##`, `*#4*35*14##`, `*#4*35*20##`, `*#4*#0*20##`, `*#4*35*60##`, `*#4*0*60##`, `*#16*0*5##`, `*#16*101*5##`, `*#5*0##`, `*#1*0##`, `*#2*0##` |
| `run3_reads_lights_addresses_humidity` | 13:22 | `*#4*0##`, `*#4*39*60##`, `*#1*0014##` / `*#1*14##`, `*#1*0010##` / `*#1*10##`, `*#1*0001##` / `*#1*01##`, `*#1*0004##` / `*#1*04##`, `*#13**0##` |

## What the plant established (each row has its frames in the archive)

* `*#4*0##` is refused in 51 ms (NACK, nothing on the event session). `*#4*#0##` and `*#4*#0*14##` are forwarded
  and unanswered: NACK after 4.2 s (OWNd retried the command once).
* `*#4*35##` is answered in 1.3 s with `*#4*35*0*0231##`, `*#4*35*12*0200*3##`, `*4*1*35##`, `*#4*35*14*0200*3##`,
  `*#4*35*13*00##`: the same order as the F461 (dimension 12 before dimension 14).
* `*#4*35*20##` -> `*#4*35#1*20*0##` (189 ms). `*#4*#0*20##` is ACKed in 0.39 s; the command session collected
  8 of the frames, the event session all 43 distinct actuator frames (34 `Z#1`, 5 `Z#2` fan coils, `0#1`-`0#4`):
  44 actuator frames in the run counting the zone 35 reply.
* `*#4*35*60##` NACKs after 2.1 s (no humidity probe in zone 35); `*#4*0*60##` is ACKed in 0.5 s and answered by the
  17 zones that have one (`*#4*39*60*51##` ... `*#4*70*60*69##`); `*#4*39*60##` -> `*#4*39*60*51##` in 0.2 s.
* `*#16*0*5##` and `*#16*101*5##` NACK in 3 ms, `*#5*0##` in 5 ms: refused by the gateway itself.
* `*#1*0##` is ACKed after 4.7 s with 100 point statuses (74 distinct addresses, a few reported twice): four-digit
  area-0 points `0010`-`0015`, four-digit `0110`-`0515` and two-digit `11`-`69`. `*#2*0##` is ACKed after 4.3 s
  with no data.
* `*#1*0014##` -> `*1*0*0014##` and `*#1*14##` -> `*1*0*14##`: two different devices. `*#1*0010##` -> `*1*0*0010##`
  while `*#1*10##` NACKs after 2.0 s (no device). `*#1*0001##` NACKs in 8 ms and `*#1*0004##` in 14 ms (the
  four-digit `000N` spelling is refused at once), whereas `*#1*01##` and `*#1*04##` NACK after 2.0 s (forwarded,
  no device): on this firmware a four-digit `00PL` with PL 10-15 is a real address, `000N` is not.
* `*#13**0##` is answered on the command session and the reply is also delivered on the event session
  (`*#13**0*16*19*49*999##`, `*#13**0*16*22*38*999##`). Every WHO 13 / 1013 reply of run 1 was echoed on the event
  session. `*#13**22##` reports time zone `999`.
* `*#13**27##` -> the model name "MyHomeServer1" as ASCII codes; `OWNMessage.parse` returns `None` for it in the pinned
  OWNd (OWNd #139 adds the decoder).

## What this capture backs

| Pull request | Frames in this archive |
|---|---|
| OWNd #137 (`set_central_mode` requires a central-unit address) | `*#4*#0*14##`, `*#4*#0##` forwarded and unanswered (run 2) |
| OWNd #139 (WHO 13 dimension 27 model name) | `*#13**27##` -> "MyHomeServer1" (run 1) |
| OWNd #140 (`actuators_status` builder) | `*#4*35*20##`, `*#4*#0*20##` with the `Z#1` / `Z#2` / `0#N` reply forms (run 2) |
| OWNd #141, MyHOME #798 (area-0 four-digit addresses) | `*#1*0001##` / `*#1*0004##` refused at once; `0010`-`0015` real devices distinct from `10`-`15` (run 3, run 2) |
| OWNd #142, MyHOME #794 (WHO 16 / startup requests the firmware refuses) | `*#16*0*5##`, `*#16*101*5##`, `*#4*0##` refused locally (run 2, run 3) |
| MyHOME #791 (setpoint in force = dimension 12) | `*#4*35##` reply order (run 2) |
| MyHOME #792 (request dimension 20 on add and after reconnect) | the actuator status is answered on a MyHomeServer1, per zone and for all zones (run 2) |
| MyHOME #793 (humidity poll gate) | per-zone humidity answered only where a probe exists, the general sweep answered (run 2, run 3) |
| alarm gate (misc PR) | `*#5*0##` refused in 5 ms (run 2) |
| event-session liveness probe (misc PR) | `*#13**0##` echoed on the event session (run 1, run 3) |

Tests: `tests/test_myhomeserver1_live_2026_10_24_trace.py`.
