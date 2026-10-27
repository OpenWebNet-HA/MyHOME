# Live capture: BTicino F461 (firmware 2.0.11), 2026-10-15

Every frame exchanged with a physical F461 gateway during the live test of OWNd master `028f11c`
(session: 06:16 to 07:05 UTC). Nothing is synthetic; the only rows removed are the HMAC handshake
(`*98*2##` challenges and the 128-digit answers).

## Plant

| Item | Value |
|---|---|
| Gateway | F461, firmware 2.0.11, kernel 5.10.35, WHO 13 device type `200`, WHO 1013 object model `134`, clock timezone `999`, HMAC SHA-256 on both sessions |
| Lights | two F411 relay actuators, Home + Project addresses A=0 PL=1 (`WHERE 01`) and A=0 PL=4 (`WHERE 04`) |
| Climate | one standalone thermostat, zone 2, no central unit; actuator 1 = heating valve, actuator 2 = fan coil used in cooling season |

## File format: `myhome_trace_F461_live_2026-10-15.json`

Standard `MyHOME` derived trace archive containing:
- `capture` & `gateway`: metadata (with RFC 5737 documentation IP `192.0.2.92` and synthetic MAC `00:03:50:00:04:97`).
- `frames`: the 629 event-session `rx` frames (`direction: "rx"`, `raw: "..."`) replayed by the corpus health checks (`test_device_health_corpus.py`) and scanned by `scripts/update_trace_matrix.py`.
- `rows`: all 2,478 session log entries in capture order, with `run` naming the session:

| `dir` | Meaning | Fields |
|---|---|---|
| `tx` | frame written to the gateway | `session` (`cmd` or `evt`), `frame`, and for the `probe_*` runs `ack` (ACK/NACK), `reply_time_s`, `replies` (frames the command session collected), `events_after` (frames seen on the event session before the next command) |
| `rx` | frame read from the gateway | `session` (`cmd` reply or `evt` monitor), `frame`; event-session rows also carry `ownd_class` and `ownd_log`, what OWNd master 028f11c made of the frame at capture time |
| `result` | what the command session returned for a `tx` in the `run1_full` / `run3_climate` runs | `ack`, `replies`, `note` |

`t` is seconds since the start of that run; `utc` (probe runs) is wall clock. `step` is the phase name.

## The runs

| `run` | What it was |
|---|---|
| `run1_full` | WHO 13 dimensions, every WHO 1 command and status form on both lights, WHO 4 reads; ends when `*#4*2*22##` killed WHO 4 (t=157.6) |
| `run3_climate` | complete WHO 4 session: reads, setpoint writes (modes 1/2/3, plain and `#Z`), mode writes (303/311/302/102/202 plain and `#Z`), season frames, fan speed, timed manual, holiday, central-unit frames, other WHOs, 120 s listen |
| `probe_followup` | mode commands with the zone ON, light status forms |
| `probe_fancoil` | dimensions 19, 20, 11 in heating and in cooling season |
| `probe_dim22_confirm` | isolated `*#4*2*22##`: WHO 4 refused in 3 ms, gateway reboot 3 min 12 s later (uptime 0:00:41 afterwards) |
| `probe_area0` | `0001` / `01` / `02` / `1` address forms for the area-0 lights |
| `probe_monitor5min` | 300 s passive listen: one spontaneous `*#4*2*60*34##`, gateway clock broadcast, nothing else |

## What the plant established (each row has its frames in `myhome_trace_F461_live_2026-10-15.json`)

* `*#4*2##` is answered in 1.3 s with `*#4*2*0*T##`, `*#4*2*12*T*3##`, `*4*1*2##` (season), `*#4*2*14*T*3##`,
  `*#4*2*13*00##`; `*#4*0##`, `*#4*#2##`, `*#4*#0##` are NACKed.
* `*#4*2*19*5*1##` in heating season (cooling side "fan coil off", heating valve on) and `*#4*2*19*8*0##` in
  cooling season with the fan coil at speed 3. `*#4*2*11##` is NACKed in heating and answered in cooling.
* `*4*302*2##` puts the zone in protection (`*4*202*2##`, dimension 12 = 0350); `*4*302*#2##`, `*4*102*#2##`,
  `*4*202*#2##`, `*4*311*#2##`, `*4*303*#2##` are ACKed and change nothing; `*4*311*2##` is NACKed.
* `*#4*2*#14*T*1##` switches the zone to heating, `*2##` to cooling, `*3##` keeps the season; `*#4*#2*#14*…##` changes nothing.
* `*#4*Z*22##` and `*#4*#0*22##` crash the thermostat handler, the gateway reboots about 3 minutes later.
* `*#13**15*200##` is followed by the spontaneous `*#1013**1*134*15*5*0##`.
* Lights: `01` / `04` work, `0001` / `0004` / `02` are NACKed, `1` and `4` are areas.
