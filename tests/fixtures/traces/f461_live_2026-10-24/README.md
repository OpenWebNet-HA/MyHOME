# Live capture: BTicino F461 (firmware 2.0.11), 2026-10-24, zone 2 setpoint behaviour

Every WHO 4 frame exchanged with the physical F461 gateway on 2026-10-24 (18:07 to 19:12 UTC) while the
before / trigger / after sequence asked for on MyHOME #791 ([comment](https://github.com/OpenWebNet-HA/MyHOME/pull/791#issuecomment-6388207693))
was run on the Living Now KW4691 standalone thermostat at zone 2: a bezel turn, 480 fast alternating
dimension 12 / 14 reads, the antifreeze command `*4*102*2##`, the dimension 7 templates `*#4*2*#7*1*3##` /
`*#4*2*#7*2*3##`, bezel presses in Manual and in antifreeze, and `climate.set_temperature` writes from a local
Home Assistant running the integration. Nothing is synthetic except the gateway's MAC and LAN address in the
`gateway` block (no WHO 13 identity frame was exchanged); the HMAC handshake is not included. Recorded with
OWNd master (`OWNCommandSession` + `OWNEventSession`).

The same plant's earlier captures are `tests/fixtures/traces/f461_live_2026-10-15/`, `f461_live_2026-10-18/`
and `f461_live_2026-10-23/` (the last one holds the `*#4*2##` reply with dimension 12 = 23.5 and dimension 14 = 21.0
that #791 started from, and the plant table in full).

## Plant

| Item | Value |
|---|---|
| Gateway | F461, firmware 2.0.11 (`*#13**16*2*0*11##`), kernel 5.10.35, distribution 2.26.4, WHO 13 device type `200`, WHO 1013 object model `134.15.5.0`, HMAC SHA-256 on both sessions (identified in `f461_live_2026-10-23`) |
| Climate | Living Now KW4691 standalone thermostat at zone 2 with a BT-F430/2 heating valve (actuator 1) and a BT-F430/4 fan coil (actuator 2); heating season; no central unit; zone 2 is the only WHO 4 device |
| Home Assistant | a local instance running the integration at #791's head (`abf6330f`) with the PyPI OWNd pin, `climate.thermo_ofice` = zone 2; its own requests are listed in `capture.home_assistant_requests` (from its debug log, which is not included) |
| Not involved | lights 06 / 07 / 09 and the shutter 05 of the plant (no WHO 1 / 2 frame in this capture) |

## File format: `myhome_trace_F461_live_2026-10-24.json`

Standard `MyHOME` derived trace archive:

- `capture` and `gateway`: metadata with the RFC 5737 documentation IP `192.0.2.92` and the synthetic MAC
  `00:03:50:00:10:23` (the same values as `f461_live_2026-10-23`, the same gateway). `capture.runs` lists each run
  with its listener, UTC window, purpose and `probe_segments` (one per probe invocation: its source file, step, UTC
  window, the probe's pause between requests and `listener_offset_s`, where the probe's t = 0 sits on the archive
  axis). `capture.home_assistant_requests` lists the ten requests Home Assistant sent on its own session (four
  `*#4*2*#14*…*3##` writes, three `*#4*2##` polls each followed by its `*#4*2*60##` humidity poll); their echoes
  and replies are in the event-session frames.
- `frames`: the 894 event-session `rx` frames (`timestamp`, `direction: "rx"`, `raw`, `run`, `session: "evt"`),
  replayed by `test_device_health_corpus.py` and parsed by `test_trace_oracle_validation.py` (tier 4).
- `rows`: the 3200 session-log entries in capture order:

| `dir` | Meaning | Fields |
|---|---|---|
| `tx` | frame written on the command session | `frame`, `is_status_request` (how OWNd sent it) |
| `rx` | frame read; `session: "cmd"` for the replies and the closing `*#*1##`, `session: "evt"` for the event monitor | `frame`; event rows also carry `ownd_class` and `ownd_log`, what OWNd master made of the frame at capture time. Command-session rows carry `t_estimated: true`: the session hands the replies over as a batch with the ACK / NACK, so their arrival time is unknown and they sit right after their request with `t` = the request's `t` |
| `result` | what the command session returned for the `tx` | `ack` (always true here), `reply_time_s`, `replies`, `events_after` (only the event-session frames that arrived while the request was pending, `tx.t <= t <= result.t`), `utc`, `note` on the frames this capture is about |

Sessions and timing: two event-only listeners ran for the whole capture (`evt.jsonl` 18:05-19:01 UTC, killed
after run 3; `transition_evt.jsonl` from 19:02 UTC) while short probe invocations sent the requests on their own
command + event sessions. Each run takes its event-session frames from the listener that covered it (one
source, no duplicate frames) and the probe's rows are placed on the listener clock through the frames both
sessions received: the same bus frame reached both sessions within 60 ms, and the probe's own event log matched
the listener's frames exactly over every request window before the probe's event records were dropped.
`reply_time_s` is measured; `tx.t` is reconstructed from the probe log (the probe records a request after its
pause, so `tx.t` = record time - pause - `reply_time_s`; the pause is 0.6, 1.5, 2 or 3 s per invocation) and
`result.t` = `tx.t` + `reply_time_s`. `t` is one monotonic axis over the whole archive: each run starts 1 s after
the previous one ended and `capture.runs[].t_offset` gives each run's start. `step` is the probe segment of the
request in flight (`listen` before the first request of a run).

## The runs

| `run` | UTC | What it was |
|---|---|---|
| `run1_knob_turn` | 18:07:55-18:08:55 | the bezel of the KW4691 turned by hand (29.0, then 18.0 two seconds later); 49 s later the probe reads `*#4*2*14##`, `*#4*2*12##`, `*#4*2##`; then Home Assistant's own poll |
| `run2_fast_poll_dim12_dim14` | 18:09:45-18:57:35 | 480 alternating `*#4*2*12##` / `*#4*2*14##` reads 0.6 s apart for 5 min 45 s while Home Assistant wrote 21.0 (18:11:46) and 23.5 (18:13:39); then 42 min of idle listening |
| `run3_antifreeze_split_and_restore` | 18:58:04-19:01:32 | `*4*102*2##`, reads of 12 / 14 / `*#4*2##` in antifreeze, Home Assistant's poll, `*#4*2*#14*0235*3##`, the same reads after it; the listener ends on the valve's dimension 20 echo |
| `run4_preset_templates` | 19:02:04-19:10:00 | Home Assistant's 21.0 write (the listener's first frames are its echo), the probe's `*#4*2##`, `*#4*2*#7*1*3##`, `*#4*2*#7*2*3##` 54 s later, the season flips 47 s after that, reads of 12 / 14 / `*#4*2##`, `*#4*2*#14*0235*3##` and `*#4*2##`, Home Assistant's poll |
| `run5_bezel_in_manual_and_in_antifreeze_then_ha_write` | 19:10:42-19:12:35 | bezel +0.5 in Manual, `*4*102*2##` and `*#4*2##`, bezel +0.5 in antifreeze, reads of 12 / 14 / `*#4*2##`, `*4*102*2##` and `*#4*2##` again, Home Assistant's `climate.set_temperature` 23.5 while 12 != 14, `*#4*2##` |

## What the plant established (each row has its frames in the archive)

* **A bezel turn emits dimension 12 only.** `*#4*2*12*0290*3##`, `*4*1*2##`, `*#4*2*0*0239##` (twice), then
  `*#4*2*12*0180*3##`, `*4*1*2##`, `*#4*2*0*0239##` 2.25 s later when the dial settled on 18.0; no dimension 14
  and no dimension 13 frame. The first `*#4*2*14##` read, 49 s later, already answered 18.0 (so the thermostat
  had moved dimension 14 by then, silently). In run 5 the same: `*#4*2*12*0240*3##`, `*4*1*2##`,
  `*#4*2*0*0239##` and the valve opening (`*#4*2#1*#20*1##`, `*#4*2#1*20*1##`) 3 s later; the `*#4*2##` 15 s
  later (already in antifreeze) read dimension 14 = 24.0.
* **A dimension 14 write echoes dimension 12, never dimension 14.** Every `*#4*2*#14*T*3##` (four from Home
  Assistant, two from the probe) is ACKed in 63-78 ms and echoed as `*#4*2*12*T*3##`, `*4*1*2##`,
  `*#4*2*0*…##` (the season frame comes first, twice, when the zone was in antifreeze). During the 0.6 s poll the
  next dimension 14 read already carried T: 0.7 s after the dimension 12 read for 21.0, and for 23.5 the echo
  arrived while a dimension 14 read was pending, whose reply (0.1 s later) was already 23.5. The 480 reads were
  all ACKed in 90-330 ms; the 240 dimension 14 frames of run 2 are all read replies.
* **`*4*102*2##` (antifreeze) is the only operation that split 12 from 14.** ACK in 65-80 ms, then
  `*4*102*2##`, `*#4*2*12*0070*3##`, `*4*102*2##`, `*#4*2*0*…##`: dimension 12 reports the protection setpoint
  7.0 together with the mode frame while dimension 14 keeps the last commanded value (23.5 in run 3, 24.0 and
  7.5 in run 5); `*#4*2##` answers `0`, `12`, `*4*102*2##`, `14`, `13` in that order (the mode frame takes the
  place of the season frame). Of the ten `*#4*2##` replies of the capture, the three with 12 != 14 are the three
  in antifreeze; the seven in the heating season all have 12 == 14. Home Assistant's own poll at 18:58:46 read
  the same split.
* **A bezel press in antifreeze leaves antifreeze at the protection value plus one step.** 24 s after
  `*4*102*2##`, +0.5 on the bezel gave `*4*1*2##`, `*#4*2*12*0075*3##`, `*4*1*2##`, `*#4*2*0*0239##` (heating
  season again, 7.5 in force); 27 s later dimension 12 and 14 both read 7.5. A second `*4*102*2##` then split them
  again (12 = 7.0, 14 = 7.5).
* **A Home Assistant write while 12 != 14 realigns both.** `climate.set_temperature` 23.5 in antifreeze
  (19:12:26): `*4*1*2##`, `*#4*2*12*0235*3##`, `*4*1*2##`, `*#4*2*0*0238##`; `*#4*2##` 10 s later: 12 = 14 = 23.5,
  heating season. The probe's `*#4*2*#14*0235*3##` in run 3 did the same from antifreeze.
* **The dimension 7 templates.** `*#4*2*#7*1*3##` is ACKed in 78 ms and echoed as `*#4*2*7*1*3##` (heating
  comfort), `*#4*2*12*0210*3##` (unchanged), `*4*108*2##`, `*#4*2*0*0239##`; `*#4*2*#7*2*3##` 54 s later as
  `*#4*2*7*2*3##` (cooling comfort), `*#4*2*12*0250*3##`, `*4*208*2##`, `*#4*2*0*0239##`. 47 s after that the
  zone flipped to `*4*0*2##` (conditioning) with 25.0, 3 s later back to `*4*1*2##` (heating) with 25.0, and 2 s
  later `*#4*2*12*0225*3##`; 10 s later dimension 12 and 14 both read 22.5, without any dimension 14 write. The
  KW4691 did not keep the comfort state it answered with. `*4*108*2##` / `*4*208*2##` parse in the pinned OWNd
  with no known mode ("Zone 2's mode is unknown."); `*#4*2*7*2*3##` parses as zone state `comfort` in the
  `cooling` context.
* **Observation, not a fact of the bus (run 4, local Home Assistant at #791's head):** after `*#4*2*7*2*3##` the
  climate entity's `hvac_mode` became `cool` and stayed `cool` through the later `*4*0*2##` / `*4*1*2##` season
  frames, the restore write and a `*#4*2##` poll that carried `*4*1*2##` (the pre-existing derivation keeps a
  zone's mode on a season frame once it has one, and the dimension 7 context sets it). The replay test shows the
  setpoints the entity displayed; the mode is left for a separate issue.
* The thermostat pushed `*#4*2*60*40##` (humidity) on its own three times in run 2, 16 min apart (18:25, 18:41,
  18:57 UTC; the other three humidity frames answer Home Assistant's `*#4*2*60##` polls), the measured temperature
  (`*#4*2*0*0239##` / `0238`) a few times an hour; the gateway's clock arrives on the event session every 10 min
  (`*#13**#1*06*24*10*2026##`, `*#13**#0*21*20*00*##`, an empty time-zone field as on 2026-10-23).

## What this capture backs

| Pull request / issue | Frames in this archive |
|---|---|
| MyHOME #791 (show the setpoint in force, dimension 12; keep dimension 14 as the commanded value; merged) | the causal sequence asked for in its review: run 1 (bezel turn emits dimension 12 only), run 2 (a write moves both within a poll cycle), run 3 and run 5 (antifreeze splits them, a write or a bezel press realigns them), run 4 (the dimension 7 templates and the season flips) |
| OWNd#140 (`actuators_status` builder) | `*#4*2#1*#20*1##` / `*#4*2#1*20*1##` and the `…*0##` pair: the valve's own dimension 20 echoes when the setpoint crossed the measured temperature (run 3, run 5) |
| OWNd#138 (`#`-prefixed dimension echoes) | `*#4*2#1*#20*1##`, `*#4*2#1*#20*0##` (run 3, run 5); the clock echoes `*#13**#0*21*20*00*##` with an empty time-zone field (run 2, run 4) |
| the `hvac_mode` stuck on `cool` observation | `*#4*2*7*2*3##`, `*4*208*2##`, `*4*0*2##`, `*4*1*2##` (run 4), for a separate issue |

Tests: `tests/test_f461_live_2026_10_24_trace.py`.
