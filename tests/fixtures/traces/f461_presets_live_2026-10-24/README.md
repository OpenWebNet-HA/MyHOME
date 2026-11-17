# Live capture: BTicino F461 (firmware 2.0.11), 2026-10-24, heating comfort / eco presets on zone 2

Every WHO 4 frame exchanged with the physical F461 gateway on 2026-10-24 between 20:08 and 20:12 UTC while
the follow-up asked for in the review of MyHOME #810 ([review](https://github.com/OpenWebNet-HA/MyHOME/pull/810#pullrequestreview-5639746797),
results posted on #791 in [comment 6388993746](https://github.com/OpenWebNet-HA/MyHOME/pull/791#issuecomment-6388993746))
was run on the Living Now KW4691 standalone thermostat at zone 2: from a 23.5 baseline, the dimension 7
templates `*#4*2*#7*1*3##` (heating comfort) and `*#4*2*#7*1*4##` (heating eco), each followed at once by
reads of dimension 12 (the setpoint in force) and dimension 14 (the last commanded value), a `*#4*2##` poll and
reads 48 s into eco, then a `climate.set_temperature` write from a local Home Assistant running the integration
while the two dimensions differed. In `f461_live_2026-10-24` run 4 the comfort template had been sent from a
21.0 baseline that already matched the comfort preset, and the "eco" request there was `*#4*2*#7*2*3##`,
cooling comfort; this capture closes both gaps. Nothing is synthetic except the gateway's MAC and LAN address
in the `gateway` block (no WHO 13 identity frame was exchanged); the HMAC handshake is not included. Recorded
with OWNd master (`OWNCommandSession` + `OWNEventSession`).

The same plant's earlier captures are `tests/fixtures/traces/f461_live_2026-10-15/`, `f461_live_2026-10-18/`,
`f461_live_2026-10-23/` and `f461_live_2026-10-24/` (the last one holds the bezel turn, the antifreeze split,
the cooling comfort template and the plant table in full).

## Plant

| Item | Value |
|---|---|
| Gateway | F461, firmware 2.0.11 (`*#13**16*2*0*11##`), kernel 5.10.35, distribution 2.26.4, WHO 13 device type `200`, WHO 1013 object model `134.15.5.0`, HMAC SHA-256 on both sessions (identified in `f461_live_2026-10-23`) |
| Climate | Living Now KW4691 standalone thermostat at zone 2 with a BT-F430/2 heating valve (actuator 1) and a BT-F430/4 fan coil (actuator 2); heating season; no central unit; zone 2 is the only WHO 4 device |
| Home Assistant | the same local instance as `f461_live_2026-10-24` (integration at #791's head, `abf6330f`, with the PyPI OWNd pin, not restarted since), `climate.climate_zone_2` = zone 2; its only request in the window is listed in `capture.home_assistant_requests` (from its debug log, which is not included) |
| Not involved | lights 06 / 07 / 09 and the shutter 05 of the plant (no WHO 1 / 2 frame in this capture) |

## File format: `myhome_trace_F461_presets_live_2026-10-24.json`

Standard `MyHOME` derived trace archive, in the layout of `f461_live_2026-10-24`:

- `capture` and `gateway`: metadata with the RFC 5737 documentation IP `192.0.2.92` and the synthetic MAC
  `00:03:50:00:10:23` (the same values as `f461_live_2026-10-23` and `f461_live_2026-10-24`, the same gateway).
  `capture.runs` lists each run with its listener, UTC window, purpose and `probe_segments` (one per probe
  invocation: its source file, the steps it covered, UTC window, the probe's pause between requests and
  `listener_offset_s`, where the probe's t = 0 sits on the archive axis). `capture.home_assistant_requests`
  lists the one request Home Assistant sent on its own session (the `*#4*2*#14*0235*3##` write at 20:11:20 UTC);
  its echo is in the event-session frames. Home Assistant sent no poll between the listener start and the write.
- `frames`: the 47 event-session `rx` frames (`timestamp`, `direction: "rx"`, `raw`, `run`, `session: "evt"`),
  replayed by `test_device_health_corpus.py` and parsed by `test_trace_oracle_validation.py` (tier 4).
- `rows`: the 122 session-log entries in capture order:

| `dir` | Meaning | Fields |
|---|---|---|
| `tx` | frame written on the command session | `frame`, `is_status_request` (how OWNd sent it) |
| `rx` | frame read; `session: "cmd"` for the replies and the closing `*#*1##`, `session: "evt"` for the event monitor | `frame`; event rows also carry `ownd_class` and `ownd_log`, what OWNd master made of the frame at capture time. Command-session rows carry `t_estimated: true`: the session hands the replies over as a batch with the ACK / NACK, so their arrival time is unknown and they sit right after their request with `t` = the request's `t` |
| `result` | what the command session returned for the `tx` | `ack` (always true here), `reply_time_s`, `replies`, `events_after` (only the event-session frames that arrived while the request was pending, `tx.t <= t <= result.t`), `utc`, `note` on every request (what it established) |

Sessions and timing: one event-only listener (`eco_evt.jsonl`, started 20:08:44 UTC for 1800 s) ran for the
whole capture while two probe invocations sent the requests on their own command + event sessions
(`eco_cmd.jsonl`, 20:08:49-20:09:57 and 20:11:28-20:11:31 UTC). Both runs take their event-session frames from
the listener (one source, no duplicate frames) and the probe's rows are placed on the listener clock through the
frames both sessions received: the same bus frame reached both sessions within 60 ms, and the probe's own event
log matched the listener's frames exactly over every request window before the probe's event records were
dropped. `reply_time_s` is measured; `tx.t` is reconstructed from the probe log (the probe records a request
after its pause, so `tx.t` = record time - pause - `reply_time_s`; the pause is 1.0 s in run 1 and 1.5 s in run 2)
and `result.t` = `tx.t` + `reply_time_s`. `t` is one monotonic axis over the whole archive: run 2 starts 1 s
after run 1 ended and `capture.runs[].t_offset` gives each run's start. `step` is the probe step of the request
in flight (`listen` before the first request of a run). The listener's frames after 20:12:04 UTC (the gateway
clock, a humidity push, a measured temperature) are not included.

## The runs

| `run` | UTC | What it was |
|---|---|---|
| `run1_heating_comfort_then_heating_eco` | 20:08:49-20:10:22 | `*#4*2##` (12 = 14 = 23.5, heating); `*#4*2*#7*1*3##`, reads of 12 / 14 at +1 s and +13 s; `*#4*2*#7*1*4##` 15.5 s later, reads of 12 / 14 at +1 s and +13 s, `*#4*2##` at +15.5 s, reads at +48 s; then the gateway clock (20:10:00) and a humidity frame (20:10:22) with no request pending |
| `run2_home_assistant_write_realigns` | 20:11:20-20:12:04 | Home Assistant's `climate.set_temperature` 23.5 while 12 = 18.0 and 14 = 23.5 (the run's first frames are its echo), the probe's `*#4*2##` 7.4 s after the echo, the valve's dimension 20 echo 43 s after the write |

## What the plant established (each row has its frames in the archive)

* **A heating preset moves dimension 12 and leaves dimension 14 at the last commanded value.**
  `*#4*2*#7*1*3##` from 12 = 14 = 23.5 was ACKed in 68 ms and echoed as `*#4*2*7*1*3##` (heating comfort),
  `*#4*2*12*0210*3##`, `*4*108*2##`, `*#4*2*0*0238##`; `*#4*2*12##` answered `*#4*2*12*0210*3##`, `*4*108*2##`
  and `*#4*2*14##` answered `*#4*2*14*0235*3##` at +1 s and again at +13 s. `*#4*2*#7*1*4##` (ACK 89 ms) was
  echoed as `*#4*2*7*1*4##` (heating eco), `*#4*2*12*0180*3##`, `*4*107*2##`, `*#4*2*0*0238##`; dimension 12
  read 18.0 (with `*4*107*2##`) and dimension 14 read 23.5 at +1 s, +13 s and +48 s. Every 12 / 14 pair read
  after the comfort echo differed (21.0 / 23.5 twice, 18.0 / 23.5 three times); no frame realigned them while a
  preset was active. In this capture the split was observed for 48 s into eco; how long it lasts beyond that
  was not measured.
* **The mode frame takes the season frame's place.** With a preset active, `*#4*2*12##` returns the mode frame
  `*4*108*2##` / `*4*107*2##` where the heating season returns `*4*1*2##`, and `*#4*2##` 15.5 s into eco answered
  `*#4*2*0*0238##`, `*#4*2*12*0180*3##`, `*4*107*2##`, `*#4*2*14*0235*3##`, `*#4*2*13*00##` (as `*4*102*2##`
  does in antifreeze, `f461_live_2026-10-24` run 3). The season frame appears once in run 1, in the baseline
  poll. `*4*107*2##` / `*4*108*2##` parse in the pinned OWNd with no known mode ("Zone 2's mode is unknown.";
  OpenWebNet-HA/OWNd#146); `*#4*2*7*1*4##` parses as zone state `eco` in the `heating` context.
* **A Home Assistant write while 12 != 14 realigns both and brings the season frame back.**
  `climate.set_temperature` 23.5 at 20:11:20 UTC, 2 min into eco: `*4*1*2##`, `*#4*2*12*0235*3##`, `*4*1*2##`,
  `*#4*2*0*0238##` (no dimension 14 frame, as every dimension 14 write of `f461_live_2026-10-24`); the `*#4*2##`
  sent 7.4 s after the echo (result at 8.8 s, ACK in 1.44 s) read `0`, `12` = 23.5, `*4*1*2##`, `14` = 23.5, `13`.
  Home Assistant's write went through its own command session and has no `tx` / `result` row here (its timing
  comes from its debug log). 43 s after the write the valve reported itself off (`*#4*2#1*#20*0##`,
  `*#4*2#1*20*0##`; the measured 23.8 was above the 23.5 setpoint); no actuator frame accompanied the preset changes.
* **Timings.** Single reads of dimension 12 / 14 were ACKed in 95-149 ms, the three `*#4*2##` polls in 1.30-1.44 s,
  the two template writes in 68 / 89 ms; the echo of a template reached the event session 35-50 ms after its ACK.
  No dimension 14 frame other than a read reply and no dimension 7 frame other than the two template echoes is in
  the capture.
* **Entity replay (base branch, pinned OWNd).** Fed run 1's event frames, the climate entity shows 23.5, then
  21.0 after the comfort echo and 18.0 after the eco echo, `hvac_mode: heat` throughout (the dimension 14 read
  replies do not move the shown setpoint, the mode frames 107 / 108 leave the mode alone, the dimension 7 heating
  context keeps `heat`); run 2's echo moves it to 23.5 at once. Pinned in the tests.
* The gateway's clock arrived on the event session at 20:10:00 (`*#13**#1*06*24*10*2026##`, `*#13**#0*23*10*00*##`,
  an empty time-zone field as on 2026-10-23); the `*#4*2*60*40##` at 20:10:22 arrived with no request in either
  log (a humidity push, as the three of `f461_live_2026-10-24` run 2).

## What this capture backs

| Pull request / issue | Frames in this archive |
|---|---|
| MyHOME #791 (show the setpoint in force, dimension 12; keep dimension 14 as the commanded value; merged) and the follow-up asked for on #810 | run 1: `*#4*2*#7*1*3##` from 23.5 and `*#4*2*#7*1*4##` with dimension 12 / 14 read while the preset is active (12 follows the preset, 14 keeps 23.5 for 48 s); run 2: the write realigns both |
| OpenWebNet-HA/OWNd#146 (mode frames `*4*107*2##` / `*4*108*2##`, `mode = None`) | `*4*108*2##` (3 times) and `*4*107*2##` (5 times) in run 1, each next to a dimension 12 frame or in the `*#4*2##` reply |
| OpenWebNet-HA/OWNd#140 (`actuators_status` builder) and OpenWebNet-HA/OWNd#138 (`#`-prefixed dimension echoes) | `*#4*2#1*#20*0##`, `*#4*2#1*20*0##` (run 2); the clock echo `*#13**#0*23*10*00*##` with an empty time-zone field (run 1) |

Tests: `tests/test_f461_presets_live_2026_10_24_trace.py`.
