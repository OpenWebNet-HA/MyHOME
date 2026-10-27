# Live capture: BTicino F461 (firmware 2.0.11) + F411/4 + F411/2 — WHO 14 Actuator Lock, 2026-10-18

Every frame exchanged with a physical BTicino F461 gateway during the live testbench capture of WHO 14 (`Special commands`: actuator lock `*14*0*WHERE##` and unlock `*14*1*WHERE##`). Nothing is synthetic; the only frames omitted are the HMAC SHA-256 authentication handshakes (`*98*2##` challenges and 128-digit answers).

## Plant

| Item | Value |
|---|---|
| Gateway | F461, firmware 2.0.11 (`020011`), kernel 5.10.35, WHO 13 device type `200`, WHO 1013 object model `134` (`134.15.5.0`), clock timezone `999`, HMAC SHA-256 on both sessions |
| Lights | BTicino F411/4 relay actuator, addresses A=0 PL=6 (`WHERE 06`, initially ON) and A=0 PL=7 (`WHERE 07`, initially OFF) |
| Shutter | BTicino F411/2 motorized shutter actuator, address A=0 PL=5 (`WHERE 05`, 5 s configured travel time, initially STOP) |
| Climate | BTicino KW4691 standalone thermostat, zone `2` (heating mode, `22.7 °C` measured, `22.5 °C` setpoint) |

## File format: `myhome_trace_F461_who14_live_2026-10-18.json`

Standard `MyHOME` derived trace archive containing:
- `capture` & `gateway`: metadata (with RFC 5737 documentation IP `192.0.2.92` and synthetic MAC `00:03:50:00:04:97`).
- `frames`: the 58 event-session `rx` frames (`direction: "rx"`, `raw: "..."`) replayed by the corpus health checks (`test_device_health_corpus.py`), oracle validation (`test_trace_oracle_validation.py`), and scanned by `scripts/update_trace_matrix.py`.
- `rows`: all 236 session log entries in capture order, with `run` naming the session:

| `dir` | Meaning | Fields |
|---|---|---|
| `tx` | frame written to the gateway | `session` (`cmd` or `evt`), `frame`, `ack` (ACK/NACK), `reply_time_s`, `replies` (frames the command session collected), `events_after` (frames seen on the event session before the next command) |
| `rx` | frame read from the gateway | `session` (`cmd` reply or `evt` monitor), `frame`; event-session rows also carry `ownd_class` and `ownd_log` |
| `result` | what the command session returned for a `tx` | `ack`, `replies`, `note` |

## The runs

| `run` | What it was |
|---|---|
| `run1_gateway_and_sweeps` | Gateway identity (`*#13**15##`, `*#13**16##`, `*#13**22##`, `*#13**23##`, `*#13**26##`, `*#13**0##`, `*#13**1##`) and baseline status sweeps for lights (`06`, `07`), shutter (`05`), and climate (`2`) |
| `run2_who14_lighting_f411_4` | Complete WHO 14 lock/unlock verification on F411/4 lighting channels `07` (locked while OFF) and `06` (locked while ON), verifying that `*1*1*07##` and `*1*0*06##` are ignored while locked and normal switching resumes after `*14*1*WHERE##` |
| `run3_who14_shutter_f411_2` | Complete WHO 14 lock/unlock verification on F411/2 shutter channel `05` (5 s travel time): locking (`*14*0*05##`), verifying `*2*1*05##` (UP) and `*2*2*05##` (DOWN) are blocked while locked, unlocking (`*14*1*05##`), and verifying full 5 s UP and DOWN runs with automatic `*2*0*05##` end-of-travel stops |
| `run4_who14_boundaries_and_routing` | Boundary and addressing probes: unpopulated `WHERE 99` (`2.05 s` SCS bus ACK timeout -> NACK), status queries `*#14*WHERE##` and invalid `WHAT=2` (`50 ms` immediate `bt_luci` parser rejection -> NACK), private riser routing `*14*0*07#4#01##` / `*14*1*07#4#01##`, group `*14*0*#1##` / `*14*1*#1##`, and general `*14*0*0##` / `*14*1*0##`, followed by full baseline restoration check |

## What the plant established

* **Populated vs. unpopulated `WHERE` explains the firmware oracle `NACK + OUT` behavior**: In `own-firmware-oracle`, F461 (`020011`), F460 (`020012`), and MyHomeServer1 (`028206`) forwarded `$00332001100\r` onto the SCS bus (`verdict: "out"`) but replied `*#*0##` (NACK) because no physical actuator existed at `WHERE 32`. On the live F461 bench, unpopulated `*14*0*99##` and `*14*1*99##` time out after **2.052–2.053 s** waiting for a low-level SCS bus ACK and return `*#*0##` with no monitor broadcast. Targeting populated F411/4 (`06`, `07`) or F411/2 (`05`) channels returns **`*#*1##` (ACK) in 89–111 ms** AND broadcasts **`*14*0*WHERE##` / `*14*1*WHERE##` on the `*99*1##` monitor session** 27–42 ms later.
* **F411/4 lighting lock behavior (`WHERE 06, 07`)**: While `07` is locked (`*14*0*07##`), `*1*1*07##` is ACKed by the gateway (`93 ms`) and forwarded to the bus, but the F411/4 ignores it (no monitor event; `*#1*07##` confirms `*1*0*07##`). While `06` is locked (`*14*0*06##`) in the ON state, `*1*0*06##` is similarly ignored (`*#1*06##` confirms `*1*1*06##`). Sending `*14*1*WHERE##` unlocks the channel and restores normal switching.
* **F411/2 shutter lock behavior (`WHERE 05`, 5 s travel time)**: Locking `05` (`*14*0*05##`) emits `*14*0*05##` on the monitor session followed **54 ms later by an automatic `*2*0*05##` (STOP) broadcast** from the F411/2. While locked, `*2*1*05##` (UP) and `*2*2*05##` (DOWN) emit command-translation frames (`*2*1000#0*05##` + `*2*1000#1/2*05##`) on the monitor session, but the F411/2 does not actuate (`*#2*05##` confirms `*2*0*05##`). Once unlocked (`*14*1*05##`), `*2*1*05##` and `*2*2*05##` actuate `*2*1*05##` / `*2*2*05##` and auto-stop with `*2*0*05##` after `5.05–5.13 s`.
* **Parser rejection vs. bus timeout**: Status requests (`*#14*07##`, `*#14*06##`, `*#14*05##`, `*#14*0##`) and invalid `WHAT` (`*14*2*07##`) are rejected immediately by `bt_luci`'s flex scanner in **50–53 ms** (`*#*0##`) without touching the SCS bus.
