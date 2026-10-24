# Live capture: BTicino F461 (firmware 2.0.11), 2026-10-23

Every frame exchanged with a physical F461 gateway on 2026-10-23 (20:38 to 23:03 UTC) while the
findings behind OWNd #136-#142 and MyHOME #791-#798 were collected: an oracle replay of 165 read-only
requests, reviewer reads, a setpoint restore, two report-form writes, and the event session while a
local Home Assistant ran the integration under test. Nothing is synthetic except the gateway's MAC
and LAN address (rewritten in both their text and their WHO 13 byte forms, see below); the HMAC
handshake is not included. Recorded with OWNd master (`OWNCommandSession` + `OWNEventSession`),
one request at a time with a 1.2-1.5 s pause.

The earlier capture of the same plant is `tests/fixtures/traces/f461_live_2026-10-15/` (lights still
at `01` / `04`). It already holds the WHO 4 central-mode pair cited by OWNd #137 (`*4*311*2##` NACK,
`*4*311*#2##` ACK and ignored, `run3_climate` t=252 and `probe_followup` t=7), the dimension 22
reboot behind OWNd #136 (`probe_dim22_confirm`) and the `0001` / `01` / `02` / `1` address forms behind
OWNd #141 / MyHOME #798 (`probe_area0`); those frames are not duplicated here.

## Plant

| Item | Value |
|---|---|
| Gateway | F461, firmware 2.0.11 (`*#13**16*2*0*11##`), kernel 5.10.35, distribution 2.26.4, WHO 13 device type `200`, WHO 13 dimension 27 model name `70#52#54#49` = "F461", WHO 1013 object model `134.15.5.0`, clock time zone `999`, HMAC SHA-256 on both sessions |
| Lights | BT-F411/4 at A=0 PL=6 (`WHERE 06`) and A=0 PL=7 (`WHERE 07`), Group `#1`, General `0`; an F422 interface with a light at `WHERE 09` whose actuator was missing configurator "2" at capture time: it appears in the area sweep (`*1*0*09##`) but answers no point request or command |
| Shutter | BT-F411/2 at A=0 PL=5 (`WHERE 05`), no position reporting, stops itself 5.1-5.9 s after a run starts |
| Climate | Living Now KW4691 standalone thermostat at zone 2 with a BT-F430/2 heating valve (actuator 1) and a BT-F430/4 fan coil (actuator 2); heating season; no central unit |
| Not present | WHO 16 / 22 sound devices, WHO 18 meters, WHO 5 / 9 alarm, central unit |

## File format: `myhome_trace_F461_live_2026-10-23.json`

Standard `MyHOME` derived trace archive:

- `capture` and `gateway`: metadata with the RFC 5737 documentation IP `192.0.2.92` and the synthetic
  MAC `00:03:50:00:10:23`. The MAC also appears as the WHO 13 dimension 12 reply `*#13**12*0*3*80*0*16*35##`
  and the IP as `*#13**10*192*0*2*92##`: the decimal bytes of the synthetic values, rewritten consistently.
  `capture.runs` lists each run with its source, UTC window and purpose.
- `frames`: the 299 event-session `rx` frames (`timestamp`, `direction: "rx"`, `raw`, `run`, `session: "evt"`),
  replayed by `test_device_health_corpus.py` and parsed by `test_trace_oracle_validation.py` (tier 4).
- `rows`: the 939 session-log entries in capture order:

| `dir` | Meaning | Fields |
|---|---|---|
| `tx` | frame written on the command session | `frame`, `is_status_request` (how OWNd sent it) |
| `rx` | frame read; `session: "cmd"` for the replies and the closing `*#*1##` / `*#*0##`, `session: "evt"` for the event monitor | `frame`; event rows also carry `ownd_class` and `ownd_log`, what OWNd master made of the frame at capture time. Command-session rows carry `t_estimated: true`: the session hands the replies over as a batch with the ACK / NACK, so their arrival time is unknown and they sit right after their request with `t` = the request's `t` |
| `result` | what the command session returned for the `tx` | `ack`, `reply_time_s`, `replies` (frames collected on the command session; empty on a NACK and on an ACK that carried no data), `events_after` (only the event-session frames that arrived while the request was pending, `tx.t <= t <= result.t`; what arrived during the pause before the next request is in the chronological rows only), `utc`, `note` on the frames the pull requests cite |

`reply_time_s` is measured (the time the command session waited for the ACK / NACK); `tx.t` is reconstructed from
the probe log (the probe records a request after its pause, so `tx.t` = record time - pause - `reply_time_s`) and
`result.t` = `tx.t` + `reply_time_s`; event-session `t` is the arrival time as logged.
`t` is one monotonic axis over the whole archive: each run starts 1 s after the previous one ended, and
`capture.runs[].t_offset` gives each run's start (`t - t_offset` is the time since that run's first frame), so
corpus replayers that drive a clock from `frames[].timestamp` never go backwards. `step` is the phase
(`oracle_who<N>` / `plant_reads` in run 1, the run name elsewhere, `listen` for the event-only runs).

## The runs

| `run` | UTC | What it was |
|---|---|---|
| `run1_oracle_replay_readonly` | 20:38-20:44 | 165 status / dimension requests mirrored from the firmware-oracle suites (WHO 1, 2, 3, 4, 5, 8, 9, 13, 14, 16, 17, 18, 22, 25, 1001, 1013) plus 31 plant reads (`*#1*06##`, `*#4*2*12##`, `*#13**27##`, ...); read-only, no WHO 4 dimension 22, no report-form writes |
| `run2_reviewer_reads` | 21:02 | `*#1*0007##` / `*#1*07##` / `*#1*7##`, `*#4*0*60##`, `*#4*2*60##`, `*#22*3#1#1*1##`, `*#13**0##` |
| `run3_climate_setpoint_restore` | 21:13 | `*#4*2*#14*0235*3##` restores the setpoint in force, then `*#4*2##` |
| `run4_final_state` | 21:13 | `*#1*06##`, `*#1*07##`, `*#2*05##`, `*#4*2##` after the Home Assistant session (dimension 12 = 14 = 21.0) |
| `run4b_final_state_after_restore` | 23:03 | the same reads two hours after the restore (dimension 12 = 14 = 23.5) |
| `run5_injection_flags` | 21:10 | report-form writes used by the expose engine: `*#18*51*113*100##`, `*#4*#0*0*0215##` |
| `run6_event_session_ha_fixed_integration` | 22:31-23:00 | event session only (1770 s) while a local Home Assistant ran the integration with the fixes under review: six reconnect sweeps, actuator frames after every zone poll, eight shutter runs, one general stop |
| `run7_localha_bus_monitor` | 21:03-21:13 | event session only: the local Home Assistant on the unpatched branch (lights 06 / 07, `close_cover`, `set_cover_position 40`, `close_cover`, `set_temperature 22` then 21, a manual `*#4*2*20##`) |
| `run8_flicker_repro`, `run8b_flicker_repro_who22` | 20:49-20:50 | the reads that preceded a 100 ms pulse of lights 07 and 06 in run 1 (`*#25*21##`, `*#3*#1##`, three WHO 22 reads) re-sent twice: no pulse, no event; the pulse came from somewhere else |

## What the plant established (each row has its frames in the archive)

* `*#4*2##` is answered in 1.3 s with `*#4*2*0*0239##`, `*#4*2*12*0235*3##`, `*4*1*2##`, `*#4*2*14*0210*3##`,
  `*#4*2*13*00##`: dimension 12 (23.5, the setpoint in force, set at the thermostat) precedes and differs from
  dimension 14 (21.0, the last value written from Home Assistant). `*#4*2*#14*0235*3##` (mode 3) is ACKed in
  78 ms, dimension 12 follows at once, dimension 14 catches up 0.3 s later, the season frame stays `*4*1*2##`.
* `*#4*2*20##` is ACKed in 111 ms with `*#4*2#1*20*0##` on the command session, followed by `*#4*2#2*20*5##` on the
  event session; `*#4*#0*20##` returns both on the command session after 2.1 s. In run 6 the two actuator frames
  followed every `*#4*2##` burst within 3 s because the patched integration under test (#792) sent `*#4*#0*20##`
  after each zone poll; the F461 does not emit dimension 20 in reply to `*#4*2##` (run 1: `*#4*2##` answers
  dimensions 0, 12, 14, 13 and the season only).
* `*#4*0*60##` is answered by zone 2 (`*#4*2*60*39##`) after 2.1 s, `*#4*2*60##` in 0.14 s; the thermostat pushed
  `*#4*2*60*38##` three times in 30 minutes on its own.
* `*#4*0##`, `*#5*0##`, `*#9*0##`, `*#17*0##`, `*#14*06##`, `*#3*…##`, `*#8*…##` and all 18 WHO 16 forms are refused
  by the gateway itself in under 10 ms (`*#5*#1##` in 13 ms). `*#25*21##` NACKed after 950 ms in run 1 and in 5-6 ms
  when re-sent in runs 8 / 8b. `*#4*#2##`, `*#4*#2*14##`, `*#4*#0##`, `*#4*#0*14##` and the 13 WHO 22 reads are
  forwarded to the bus and NACKed after the TX-ACK wait (1.8-2.9 s).
* `*#1*0007##`, `*#1*0001##`, `*#1*0002##` are refused in 5-7 ms; `*#1*07##` answers `*1*0*07##` in 88 ms;
  `*#1*7##` is area 7 (ACK, no data, 1.0 s). `*#1*0##` and `*#1*00##` list `06`, `07` and `09`; `*#1*09##` times
  out on the bus. Dimension 15 is group-only (`*#1*06*15##` NACK, `*#1*#1*15##` ACK and echoed).
* `*#13**0##` sent on the command session is also delivered on the event session (`*#13**0*23*38*50*999##`,
  `*#13**0*00*02*31*999##`). `*#13**10##`, `*#13**11##`, `*#13**12##`, `*#13**19##`, `*#13**27##` are answered
  on an F461 (`*#13**27*70#52#54#49##` has no decoder in the pinned OWNd: `OWNMessage.parse` returns `None`
  without raising; OWNd #139 adds it). The spontaneous `*#13**12*…##` and `*#1013**1*134*15*5*0##` open every
  event session. The gateway's clock echoes carry an empty time-zone field (`*#13**#0*01*40*00*##`).
* `*#18*51*51##`, `*#18*51*113##`, `*#18*51*54##` are ACKed with no data after 0.6-0.8 s (no meter);
  `*#18*51*1200##` is refused; `*#18*51*113*100##` (report form) is ACKed in 1.4 s and not echoed;
  `*#4*#0*0*0215##` is NACKed after 4.1 s.
* The F411/2 shutter: every run starts with the gateway burst `*2*1000#0*05##`, `*2*0*05##`, `*2*1000#<1|2>*05##`,
  `*2*<1|2>*05##` (the order of the two translation echoes and the stop status varies) and a full run ends with
  the actuator's own `*2*0*05##` 5.1-5.9 s later (nine full runs); the two `set_cover_position` runs were stopped
  by Home Assistant after 2.45 s and 3.65 s. `*#2*05*10##` ACKs with no data,
  `*#2*05*11##` is refused. `*2*0*0##` (one general stop from Home Assistant) appears exactly once.
* Lights 06 / 07 switched from Home Assistant echo `*1*<0|1>*0<6|7>##` in about 0.1 s with no group or area frame.

## What this capture backs

| Pull request | Frames in this archive |
|---|---|
| OWNd #136 (refuse WHO 4 dimension 22 in every send path) | not in this capture (no dimension 22 was sent); the crash is `probe_dim22_confirm` in `f461_live_2026-10-15` |
| OWNd #137 (`set_central_mode` requires a central-unit address) | `*#4*#2##`, `*#4*#2*14##`, `*#4*#0##`, `*#4*#0*14##` NACK after the bus timeout on a plant without a central unit (run 1); the `*4*311*2##` NACK / `*4*311*#2##` ACK pair is in `f461_live_2026-10-15` |
| OWNd #138 (`#`-prefixed dimension 14 setpoints, WHO 13 clock echoes) | `*#4*2#1*#20*0##` (run 6, a `#`-prefixed dimension echo), `*#13**#0*01*40*00*##` / `*#13**#1*06*24*10*2026##` clock echoes on the event session (run 6) |
| OWNd #139 (WHO 13 dimension 27 model name, WHO 1001 diagnostic event) | `*#13**27##` -> `*#13**27*70#52#54#49##` (run 1); `*#1001*74##` NACK (no WHO 1001 device on this plant) |
| OWNd #140 (`actuators_status` builder) | `*#4*2*20##` -> `*#4*2#1*20*0##`, `*#4*2#2*20*5##`; `*#4*#0*20##` (run 1, run 6, run 7) |
| OWNd #141 (`normalize_where` area-0 four-digit points) | `*#1*0007##` NACK vs `*#1*07##` ACK vs `*#1*7##` (run 2); `*#1*0001##`, `*#1*0002##` NACK (run 1) |
| OWNd #142 (drop WHO 16 from the F461 profile) | all 18 WHO 16 requests NACK in under 10 ms (run 1) |
| MyHOME #791 (setpoint in force = dimension 12) | `*#4*2##` reply order and dimension 12 != 14 (run 1, run 4); `*#4*2*#14*0235*3##` restore (run 3, run 4b) |
| MyHOME #792 (request dimension 20 on add and after reconnect) | `*#4*2*20##`, `*#4*#0*20##` (run 1); the actuator frames after every reconnect (run 6); the manual request (run 7) |
| MyHOME #793 (humidity poll gate) | `*#4*0*60##`, `*#4*2*60##` (run 2); spontaneous `*#4*2*60*38##` (run 6) |
| MyHOME #794 (skip the startup requests the firmware refuses) | `*#4*0##`, `*#16*0*5##`, `*#5*0##` NACK in under 10 ms (run 1) |
| MyHOME #795 (timed cover starts unknown) | the shutter runs and their hardware stops 5.1-5.9 s later (run 6, run 7) |
| MyHOME #796 (yaml `class:` for WHO 2) | nothing on the bus; configuration only |
| MyHOME #797 (scope events only for frames that carry a state) | one `*2*0*0##` (run 6); light toggles without group / area frames (run 7) |
| MyHOME #798 (canonicalise four-digit area-0 addresses) | `*#1*0007##` NACK, `*#1*07##` ACK (run 2); `probe_area0` in `f461_live_2026-10-15` |
| MyHOME #800 (diagnostics profile fields) | no frame in this capture backs it; diagnostics only |
| MyHOME #801 (legacy yaml MAC mismatch warning) | no frame in this capture backs it; configuration only |
| MyHOME #802 (warn once per frame shape for unparsed frames) | `*#13**27*70#52#54#49##` (run 1), the wire form of a frame the pinned OWNd does not decode |
| MyHOME #803 (alarm: skip `*#5*0##` where the firmware refuses it) | `*#5*0##` NACK in 3 ms (run 1) |
| MyHOME #804 (identify a manually added gateway after authentication) | `*#13**15##` -> `200`, `*#1013*0*1##` -> `134.15.5.0`, `*#13**12##` -> the MAC (run 1) |
| MyHOME #805 (closed service schemas) | no frame in this capture backs it; configuration only |
| MyHOME #806 (event-session liveness probe) | `*#13**0##` echoed on the event session (run 1, run 2) |

Tests: `tests/test_f461_live_2026_10_23_trace.py`.
