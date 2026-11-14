# Live capture: BTicino F461 (firmware 2.0.11), 2026-10-25, actuator status at startup and valve switching on zone 2

Every frame the physical F461 gateway put on its event session on 2026-10-25 between 08:31:43 and 08:35:57 UTC
while the actuator-switching capture asked for in [review 5642758778](https://github.com/OpenWebNet-HA/MyHOME/pull/792#pullrequestreview-5642758778)
of MyHOME #792 was run: a local Home Assistant running the integration at the head of #792 (`0e44bcd7`, PyPI OWNd
pin) was restarted with an event-only listener on the bus; its startup sweep of the Living Now KW4691 standalone
thermostat at zone 2 was followed by the all-zones actuator request `*#4*#0*20##` that #792 adds, answered by both
actuators within 0.13 s; then `climate.set_temperature` 26.0 made the heating valve (actuator 1) report itself on
2.2 s after the setpoint echo, and `climate.set_temperature` 23.5 made it report itself off 2.1 s after the echo.
The fan coil (actuator 2) sent nothing but its startup reply. Nothing is synthetic except the gateway's MAC and
LAN address in the `gateway` block (no WHO 13 frame was exchanged); the HMAC handshakes are not included. Recorded
with OWNd master (`OWNEventSession`).

The same plant's earlier captures are `tests/fixtures/traces/f461_live_2026-10-15/`, `f461_live_2026-10-18/`,
`f461_live_2026-10-23/` (run 6 holds the previous `*#4*2#1*20*0##` / `*#4*2#2*20*5##` pair, 2.2 s after a zone
poll, and the plant table in full), `f461_live_2026-10-24/` and `f461_presets_live_2026-10-24/` (both hold a valve
off report 3 - 43 s after a Home Assistant write; none holds the valve switching on).

## Plant

| Item | Value |
|---|---|
| Gateway | F461, firmware 2.0.11 (`*#13**16*2*0*11##`), kernel 5.10.35, distribution 2.26.4, WHO 13 device type `200`, WHO 1013 object model `134.15.5.0`, HMAC SHA-256 on both sessions (identified in `f461_live_2026-10-23`) |
| Climate | Living Now KW4691 standalone thermostat at zone 2 with a BT-F430/2 heating valve (actuator 1) and a BT-F430/4 fan coil (actuator 2); heating season; no central unit; zone 2 is the only WHO 4 device; measured 24.0 throughout |
| Home Assistant | a local instance with the integration at #792's head (`0e44bcd7`) and the PyPI OWNd pin, started 12 s before the first row; `climate.climate_zone_2` = zone 2 (`heat` + `cool`, standalone); every request in the archive is its own, taken from its debug log (not included) |
| Cover | the BT-F411/2 timed shutter `05` answers Home Assistant's post-discovery poll `*#2*05##` (the first row); lights 06 / 07 / 09 are not involved |

## File format: `myhome_trace_F461_actuators_live_2026-10-25.json`

Standard `MyHOME` derived trace archive, in the layout of `f461_presets_live_2026-10-24`:

- `capture` and `gateway`: metadata with the RFC 5737 documentation IP `192.0.2.92` and the synthetic MAC
  `00:03:50:00:10:25` (the gateway of `f461_live_2026-10-23` / `-24`, a new synthetic MAC for this capture).
  `capture.listener` is the event-only listener (`evt.jsonl`, started 08:31:36.5 UTC for 3600 s) and how Home
  Assistant's log was placed on its clock; `capture.runs` lists each run with its UTC window, `t_offset`, purpose
  and `home_assistant_requests` (the frames Home Assistant sent in it). There is no probe command session in this
  capture, so there are no `probe_segments`. `capture.home_assistant_entity_states` holds the climate entity's
  service calls and state changes as recorded from Home Assistant's event bus for the first write (UTC wall clock,
  `hvac_action` and the `actuators` attribute of #792); the harness's first restore call (08:33:59 UTC) was dropped
  by Home Assistant (the websocket message reused an id, so no service ran and nothing reached the bus) and its
  record is listed with that note; the restore that reached the bus went through the REST API at 08:35:55 UTC and
  its state change (idle at 08:35:57.464 UTC) was read back, not recorded, so it is not listed.
- `frames`: the 20 event-session `rx` frames (`timestamp`, `direction: "rx"`, `raw`, `run`, `session: "evt"`),
  replayed by `test_device_health_corpus.py` and parsed by `test_trace_oracle_validation.py` (tier 4).
- `rows`: the 51 session-log entries in capture order:

| `dir` | Meaning | Fields |
|---|---|---|
| `tx` | frame Home Assistant wrote on its command session | `frame`, `is_status_request` (how OWNd classifies it), `source: "home_assistant"`, `t_estimated: true` |
| `rx` | frame read; `session: "cmd"` for the replies and the closing `*#*1##` (from Home Assistant's log, `source` / `t_estimated` as above), `session: "evt"` for the listener | `frame`; event rows also carry `ownd_class` and `ownd_log`, what OWNd master made of the frame at capture time. Unlike the probe captures, the command-session replies sit at the time the log collected each one (the F461 delivers them one by one), between the request and its ACK |
| `result` | what the command session returned for the `tx` | `ack` (always true here), `reply_time_s`, `replies`, `events_after` (only the event-session frames that arrived while the request was pending, `tx.t <= t <= result.t`), `utc`, `note` on every request (what it established) |

Sessions and timing: the listener's `t` is the archive's axis, shifted so that `t = 0` is the first row (Home
Assistant's `*#2*05##`, whose reply is the listener's first frame). Home Assistant's debug log (local clock, UTC +
2 h, millisecond resolution) was placed on that axis through the 14 frames both logs hold (log seconds - listener
`t` = 36.544 - 36.547 s), so every `tx` / command-session `rx` / `result` row carries `t_estimated: true`: `tx.t` is
when the worker handed the frame to the session ("unqueued"), `result.t` the ACK ("successfully sent"), each
reply the moment the log collected it. The two setpoint writes opened a new command session first (the previous
one had been closed idle 15 s after the startup); their `reply_time_s` includes that HMAC handshake (24 - 27 ms).
`capture.runs[].t_offset` gives each run's first row. `step` is the request in flight.

## The runs

| `run` | UTC | What it was |
|---|---|---|
| `run1_startup_sweep_and_actuator_request` | 08:31:43-08:31:48 | Home Assistant's startup, 12 s after the climate entity was added: `*#2*05##` (cover 05 stopped), `*#4*2##` (`0`, `12` = 23.5, `*4*1*2##`, `14` = 23.5, `13` in 1.31 s), `*#4*2*60##` (34 %, 124 ms), `*#4*2*19##` (`*#4*2*19*5*0##`, 103 ms), then `*#4*#0*20##` 0.75 s later: `*#4*2#2*20*5##` at +88 ms, `*#4*2#1*20*0##` at +128 ms, ACK at +2.13 s; the command session was closed idle 15 s later |
| `run2_setpoint_26_0_valve_on` | 08:32:59-08:33:02 | `climate.set_temperature` 26.0 from 23.5 (measured 24.0): `*#4*2*#14*0260*3##` ACKed in 90 ms with no reply, echoed 26 ms after the ACK as `*#4*2*12*0260*3##`, `*4*1*2##`, `*#4*2*0*0240##`; 2.2 s after the echo `*#4*2#1*#20*1##`, then `*#4*2#1*20*1##` 37 ms later |
| `run3_restore_23_5_valve_off` | 08:35:55-08:35:57 | `climate.set_temperature` 23.5: `*#4*2*#14*0235*3##` ACKed in 104 ms with no reply, echoed 50 ms after the ACK as `*#4*2*12*0235*3##`, `*4*1*2##`, `*#4*2*0*0240##`; 2.1 s after the echo `*#4*2#1*#20*0##`, then `*#4*2#1*20*0##` 3 ms later; the listener saw nothing else until it was stopped at 08:37 UTC |

Home Assistant's requests before the listener started (`*#1*0##`, `*#2*0##`, the `*#4*0##` and `*#16*0*5##`
NACKs, the WHO 18 meter reads) are not in the archive.

## What the plant established (each row has its frames in the archive)

* **The all-zones actuator request is answered by both actuators within 0.2 s.** `*#4*#0*20##`, sent 0.75 s
  after the startup sweep's last ACK (2.4 s after `*#4*2##` went out), was answered on both sessions by
  `*#4*2#2*20*5##` (the fan coil, off) at +88 ms and `*#4*2#1*20*0##` (the valve, off) at +128 ms; the ACK came
  2.13 s after the request, 2.0 s after the last reply (the gateway closes the all-zones batch on a timeout, as the
  MyHomeServer1's 43-actuator reply of `myhomeserver1_live_2026-10-24` did not). With #792 the entity showed
  `hvac_action: idle` and `actuators: {"1": "off", "2": "off"}` 12.1 s after it was added; on the previous build
  the first dimension 20 frame after a start was the F461's periodic broadcast, 56 min later (not in the archive).
* **A setpoint write above the measured temperature switches the valve on 2.2 s after the echo.**
  `*#4*2*#14*0260*3##` (ACK 90 ms, no reply) was echoed as `*#4*2*12*0260*3##`, `*4*1*2##`, `*#4*2*0*0240##` (no
  dimension 14 frame, as every dimension 14 write of `f461_live_2026-10-24`); 2.203 s after the echo the valve
  reported itself on, first in the command form `*#4*2#1*#20*1##` and 37 ms later in the status form
  `*#4*2#1*20*1##`. The same 2.2 s separated the setpoint echo and the valve report on the old build the evening
  before, so the delay is the thermostat's, not the integration's.
* **The restore switches it off 2.1 s after the echo.** `*#4*2*#14*0235*3##` (ACK 104 ms) was echoed the same
  way with 23.5; 2.126 s later `*#4*2#1*#20*0##`, then `*#4*2#1*20*0##` 3 ms later.
* **The fan coil stays 5 / off.** Actuator 2's only frame is the startup reply `*#4*2#2*20*5##`; `*#4*2*19*5*0##`
  (cooling fan coil off, heating valve off) says the fan coil is the cooling side. The valve's two switches are the
  only dimension 20 frames outside the startup reply.
* **The `#20` command form.** Each spontaneous switch arrives twice: `*#4*2#1*#20*V##` (the dimension written as
  `#20`, the form a write would use) 3 - 37 ms before `*#4*2#1*20*V##`; the startup reply has no command form.
  The pinned OWNd parses the command form as `OWNHeatingCommand` with no zone, so the integration logs it as
  unsupported and the entity acts on the status form only (no OWNd change decodes the `#20` write-form
  echo yet; the tests accept an event decoding as well).
* **Entity replay (base branch, pinned OWNd).** Fed the event frames, the climate entity shows `hvac_action`
  `None` until `*#4*2*19*5*0##`, then `idle` through both startup actuator replies, `heating` on `*#4*2#1*20*1##`
  and `idle` on `*#4*2#1*20*0##`; the setpoint follows the dimension 12 echoes 23.5 -> 26.0 -> 23.5 in `heat`;
  `fan_coil_cooling: true`, `fan_mode: auto`, `running_fan_speed: off`, humidity 34 %. Pinned in the tests; the
  `actuators` attribute is asserted only when the entity exposes it (#792).
* **Timings.** `*#4*2##` 1.314 s, `*#4*2*60##` 124 ms, `*#4*2*19##` 103 ms, the two writes 90 / 104 ms including
  the session handshake; every reply reached the listener 1 - 3 ms before Home Assistant's log collected it.

## What this capture backs

| Pull request / issue | Frames in this archive |
|---|---|
| MyHOME #792 (actuator status request after the startup sweep, `hvac_action` and `actuators` from dimension 20; review 5642758778 asked for this capture) | run 1: `*#4*#0*20##` -> `*#4*2#2*20*5##`, `*#4*2#1*20*0##` within 0.13 s; runs 2 / 3: `*#4*2#1*20*1##` 2.2 s after `*#4*2*12*0260*3##`, `*#4*2#1*20*0##` 2.1 s after `*#4*2*12*0235*3##`; `capture.home_assistant_entity_states` |
| MyHOME #791 (the setpoint in force is dimension 12; merged) | the two write echoes carry dimension 12 and the season frame, no dimension 14 frame |
| OpenWebNet-HA/OWNd#140 (`actuators_status` builder); the `#20` write-form echoes have no OWNd decoder yet | `*#4*#0*20##` sent by the integration (`is_status_request: false`); `*#4*2#1*#20*1##` / `*#4*2#1*#20*0##` parsed as commands by the pinned OWNd |
| own-firmware-oracle#60 (`*#4*#0*20##` forwarding matrix) | the physical F461's reply order (fan coil first) and the 2.13 s ACK |

Tests: `tests/test_f461_actuators_live_2026_10_25_trace.py`.
