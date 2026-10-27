# Live capture: Legrand / BTicino MyHomeServer1 (firmware 3.87.13), 2026-10-25, actuator, valve, fan-speed and humidity reads per zone

Status reads only (no command was sent) against the physical MyHomeServer1 of `myhomeserver1_live_2026-10-24`
(a large home plant, WHO 4 zones 35-70), 2026-10-25 10:46 to 11:02 UTC, recorded with OWNd master
(`OWNCommandSession` + `OWNEventSession`, `tools/probe.py` as for the F461 captures). The purpose: the reads
behind MyHOME #792 (actuator status at startup, `hvac_action` from dimension 20) and #793 (humidity gate) on
a second gateway family, next to `f461_actuators_live_2026-10-25`: the all-zones actuator request, then for
every zone the valve status (dimension 19), the actuator status (20), the fan speed (11) and the humidity (60),
then the general humidity read and a 600 s listen. Every actuator of the plant was off at capture time.

Two caveats that apply to every row: the firmware oracle (`own-firmware-oracle`) emulates MyHomeServer1
**2.82.06**, this unit runs **3.87.13**; and the gateway's identity (`*#13**15##`, `*#13**27##`, `*#1013**1##`)
was not re-read here, the `gateway.identification` block repeats `myhomeserver1_live_2026-10-24`. The MAC and
LAN address in the `gateway` block are synthetic (`00:03:50:00:10:26`, `192.0.2.9`); no WHO 13 dimension 12
frame was exchanged.

## Plant

| Item | Value |
|---|---|
| Gateway | MyHomeServer1, firmware 3.87.13 (`*#13**16*3*87*13##`), WHO 13 device type `200`, WHO 1013 object model `67.15.5.0`, clock time zone `999` (identified in `myhomeserver1_live_2026-10-24`) |
| Thermoregulation | WHO 4 zones 35-70: 34 zones answer (54 and 56 do not exist), every one with a valve actuator `Z#1`; five (47, 48, 57, 60, 70) also have a fan coil `Z#2` (the cooling side, `*#4*Z*19*5*0##`); zone 0 has the pumps `0#1`-`0#4`; 17 zones (39, 40, 41, 42, 43, 47, 48, 53, 55, 57, 58, 59, 60, 61, 68, 69, 70) have a humidity probe; the plant broadcasts a periodic sweep (season, dimensions 12 / 14 / 13, setpoints as dimension 7, temperatures, dimension 5, humidity) and the valves' off reports |
| Lighting | WHO 1 traffic of the house during the capture (168 frames in the listen); not involved |

## File format: `myhome_trace_MyHomeServer1_live_2026-10-25.json`

Standard `MyHOME` derived trace archive, the same layout as `myhomeserver1_live_2026-10-24` (see
`f461_live_2026-10-23` for the field table): `capture` (with `runs` and `probe`), `gateway`, `frames` (677
event-session `rx` frames) and `rows` (1224 `tx` / `rx` / `result` entries; `result.replies` are the frames
collected on the command session, `result.events_after` only the event-session frames that arrived while the
request was pending, `tx.t <= t <= result.t`). Command-session `rx` rows carry `t_estimated: true` and sit at
their request's `t` (the session hands the replies over as a batch with the ACK / NACK); `tx.t` is
reconstructed from the probe record (record time - the 1.2 s pause - `reply_time_s`) and `result.t` = `tx.t` +
`reply_time_s`; `t` is one monotonic axis over the four runs (`capture.runs[].t_offset` gives each run's first
row; `t = 0` is the `*#13**16##` request). `step` is the request in flight (`zone_Z` for the per-zone reads,
`listen` in run 4).

## The runs

| `run` | UTC | What it was |
|---|---|---|
| `run1_identify_and_all_zones_actuators` | 10:46:46-10:46:50 | `*#13**16##` (3.87.13, 27 ms), `*#13**0##` (20 ms, echoed on the event session), `*#4*#0*20##` |
| `run2_per_zone_reads_19_20_11_60` | 10:46:52-10:52:53 | for each zone 35..70, one request at a time with a 1.2 s pause: `*#4*Z*19##`, `*#4*Z*20##`, `*#4*Z*11##`, `*#4*Z*60##` (144 requests); the house's spontaneous frames meanwhile |
| `run3_general_humidity` | 10:52:55-10:52:56 | `*#4*0*60##` |
| `run4_listen_600_s` | 10:54:02-11:02:48 | no request pending: the house's own WHO 4 and WHO 1 traffic and the 13:00 (local) clock broadcast |

## What the plant established (each row has its frames in the archive)

* **`*#4*#0*20##` is answered by every actuator of the plant, pumps included, and the ACK does not wait for
  the burst.** ACK in 0.50 s with 8 frames collected on the command session; the event session received 47
  frames from +0.18 s to +1.91 s after the request (39 of them after the ACK), 43 distinct: 34 `Z#1` (zones
  35-70 but 54 and 56), the 5 `Z#2` fan coils at 5 (off) and `*#4*0#1*20*0##` ... `*#4*0#4*20*0##`; zones
  35-38 answered twice. Every value was 0 (valve off) or 5 (fan coil off): no actuator was on. The same 43
  frames as `myhomeserver1_live_2026-10-24` run 2.
* **Dimensions 19 and 20 per zone: ACK on the 34 zones in 0.16-0.25 s, NACK after 2.1 s on 54 and 56.**
  A valve zone reads `*#4*Z*19*0*0##` and answers `*#4*Z#1*20*0##`; a fan-coil zone reads `*#4*Z*19*5*0##`
  (fan coil off, valve off) and answers `*#4*Z#2*20*5##` and `*#4*Z#1*20*0##` on the event session (one of
  them collected on the command session). Zones 35, 40, 42, 51 and 64 echoed their dimension 20 reply twice on the event session (for 51 and 64 both copies arrived before the ACK).
* **Dimension 11 is refused on every zone.** `*#4*Z*11##` NACKs on all 36 zones (31 of them in 2.09-2.19 s,
  five between 2.85 and 5.81 s), fan-coil zones included, and no dimension 11 frame ever appears on the event session: the
  fan-speed dimension is refused where a valve is the actuator, the split the F461's KW4691 showed in its
  heating season (`f461_live_2026-10-15`, `probe_fancoil`: NACK in heating, ACK in cooling).
* **Humidity is answered only where a probe exists.** `*#4*Z*60##` ACKs on the 17 zones with a probe in
  0.19-0.29 s (39: 47 %, 40: 48, 41: 43, 42: 51, 43: 55, 47: 58, 48: 53, 53: 60, 55: 49, 57: 51, 58: 57, 59: 66,
  60: 54, 61: 52, 68: 55, 69: 52, 70: 68) and NACKs after 2.1-3.1 s on the other 19; `*#4*0*60##` is ACKed in 0.66 s
  and answered by the same 17 zones (8 collected on the command session, the last on the event session 0.3 s after
  the ACK) with the same values six minutes later, zones 39, 42 and 61 one point apart. The listen's
  humidity pushes come from the same 17 zones.
* **The house's own traffic.** Nine spontaneous valve off reports, each `*#4*Z#1*#20*0##` (the dimension
  written as `#20`, parsed as `OWNHeatingCommand` with no zone by the pinned OWNd; no OWNd decoder yet)
  followed within 50 ms by `*#4*Z#1*20*0##`, each about 2 s after the zone's pump call (`*4*4002#Z*0#N##` then
  `*4*4002*Z##`: zone 35 calls pump 1, 46 and 43 pump 2, 53, 55, 70 and 51 pump 3, 64 and 68 pump 4). The
  listen holds the periodic sweep (`*4*1*Z##` and dimensions 12 / 14 / 13 for each of the 34 zones, 47
  dimension 7 setpoints, 82 temperatures, 17 dimension 5 frames, 17 humidity pushes), 168 WHO 1 frames and
  the 13:00 local clock broadcast (`*#13**#1*00*25*10*2026##`, `*#13**#0*13*00*00*##`). No actuator switched
  on during the capture.
* **Entity replay (base branch, pinned OWNd).** Zones 35 (valve) and 47 (valve + fan coil) configured; the
  burst routed as the platform routes it (`_zone_route_keys`): zone 35 `hvac_action: idle` with actuator 1
  off, zone 47 idle with actuators 1 and 2 off, `fan_mode: auto`, `running_fan_speed: off`; the pump frames
  `0#1`-`0#4` have no route key and reach no entity (no zone 0 is created); the per-zone reads keep both
  idle, give zone 47 its humidity (58 %) and the fan-coil side, zone 35 none; the listen gives both their
  heating season. Pinned in the tests; the `actuators` attribute is asserted only when the entity exposes it
  (#792).

## What this capture backs

| Pull request / issue | Frames in this archive |
|---|---|
| MyHOME #792 (actuator status request after the startup sweep, `hvac_action` and `actuators` from dimension 20; dimension 19 / 11 fan-coil side) | run 1: `*#4*#0*20##` -> 43 distinct actuator frames, all off, pumps included; run 2: `*#4*Z*19##` / `*#4*Z*20##` on 34 zones, `*#4*Z*11##` refused on all 36 |
| MyHOME #793 (humidity poll gate) | run 2 / 3: `*#4*Z*60##` ACK on 17 zones, NACK on 19; `*#4*0*60##` answers the same 17 |
| OpenWebNet-HA/OWNd#140 (`actuators_status` builder); the `#20` write-form echoes have no OWNd decoder yet | `*#4*#0*20##` sent as a status request; the nine `*#4*Z#1*#20*0##` command forms |
| own-firmware-oracle#60 (`*#4*#0*20##` forwarding matrix) | the physical MyHomeServer1's 0.50 s ACK ahead of a 1.9 s burst |

Tests: `tests/test_myhomeserver1_live_2026_10_25_trace.py`.
