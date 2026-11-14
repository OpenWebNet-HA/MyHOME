# Live capture: BTicino F461 (firmware 2.0.11), 2026-11-04 — full debug log and bus sweep

Every frame exchanged with the physical F461 gateway of the test plant on 2026-11-04 (15:37 to
15:45 UTC) while a local Home Assistant 2026.10.2 ran MyHOME 2.0.0b17 with the **released OWNd
2.0.0b13**, driven over the REST API: every device the plant has was exercised and the gateway's
WHO 13 dimensions were read, then `myhome.sweep_bus` ran and the bus-monitor buffer was exported.
Nothing is synthetic except the gateway's LAN address and MAC (see below). The capture is the
plant-side evidence asked for in the 2026-11-04 reviews of OWNd #155, #156, #159, #160 and #161:
what an F461 actually sends for the items those pull requests touched.

The earlier captures of the same plant are `tests/fixtures/traces/f461_live_2026-10-15/`, `-18/`,
`-23/`, `-24/` and `f461_who13_clock_tz_live_2026-10-26/`; the F418 dimmer at `0010` was added to
the plant on 2026-11-04 and appears here for the first time.

## Plant

| Item | Value |
|---|---|
| Gateway | F461, firmware 2.0.11 (`*#13**16*2*0*11##`), kernel 5.10.35, distribution 2.26.4, WHO 13 device type `200`, dimension 27 model name `70#52#54#49` = "F461", WHO 1013 `134*15*5*0`, hardware `*#13**17*4*2*0##` |
| Lights | BT-F411 at `WHERE 06` and `07`; `WHERE 09` behind the F422 interface `01` (`09#4#01`); BT-F418 dimmer at `WHERE 0010` |
| Shutter | BT-F411/2 at `WHERE 05`, no position reporting |
| Climate | standalone thermostat at zone 2 with a heating valve (actuator `2#1`) and a fan coil (actuator `2#2`); heating season; no central unit |
| Not present | WHO 16 / 22 sound devices, WHO 18 meters, WHO 5 / 9 alarm, WHO 6 / 7 / 8 video door entry, CEN |

## Files

| File | What it is |
|---|---|
| `F461_full_debug_log_2026-11-04T15-45-56.txt` | the complete `custom_components.myhome` DEBUG log of the session, verbatim from the first `custom_components.myhome` line (15:37:12 UTC, the local-time stamps in the file are UTC+2) to the export (15:45:47 UTC): 1134 lines, 107 frames sent, 169 frames received, 26 NACKs. Only the Home Assistant start-up boilerplate above the first MyHOME line is left out. |
| `myhome_sweep_F461_all_2026-11-04T15-45-56.json` | the bus-monitor export after `myhome.sweep_bus`: the 200-frame ring buffer window 15:43:02 to 15:45:47 UTC (`truncated: true`, 97 tx / 103 rx), in the format the bus card's export button produces (`capture`, `environment`, `gateway`, `telemetry`, `frames`). It covers the cover, climate and sweep part of the session; the lights, dimmer and WHO 13 part is in the log only. |

The gateway's LAN address and MAC were made synthetic: `192.0.2.92` (RFC 5737) and
`00:03:50:00:11:04` (BTicino OUI kept), rewritten in their text forms and in their WHO 13 byte
forms (`*#13**10*192*0*2*92##`, `*#13**12*0*3*80*0*17*4##`). No password is echoed in the log. The
91 `homeassistant.components.http.ban` warnings and the `haffmpeg` warning in the log are local
Home Assistant noise (an expired API token during the drive, no ffmpeg installed), kept verbatim.

## What the plant said

- **WHO 13**: dimensions 0, 1, 10, 11, 12, 15, 16, 17, 19, 22, 23, 24 and 27 all answer. Dates carry
  a four-digit year and a weekday (`*#13**1*03*04*11*2026##`, `*#13**22*17*42*27*999*03*04*11*2026##`)
  and the time zone field is `999` (`*#13**0*17*45*24*999##`), as in every earlier F461 capture.
- **WHO 1 dimmer (F418 at `0010`)**: a level write `*#1*0010*#1*130*0##` is answered on the event
  session by `*#1*0010*1*130*4##` (dimension 1: level, speed 4). `*1*30*0010##` (step up at full
  level) is answered with `*1*10*0010##`; `*1*31*0010##` with `*1*9*0010##` and `*#1*0010*1*179*4##`.
  The parameterised `*1*30#2#255*0010##` is answered by **both** dimension 1 and dimension 4
  (`*#1*0010*1*181*4##`, `*#1*0010*4*181*2##`), and so is `*1*1#255*0010##`; the dimension 4 read
  `*#1*0010*4##` answers `*#1*0010*4*200*2##`. Every dimensionless
  status read of the dimmer (`*#1*0010##`, `*#1*0010*4##`) is followed by a WHO 1001 diagnostic frame
  `*#1001*0010*11*111110111111111101100111##` (16 in the log), which MyHOME 2.0.0b17 logs as
  `Unsupported message type`.
- **WHO 2 shutter (F411/2 at `05`)**: `*#2*05*11##` (dimension 11 read) is **NACKed** and
  `*#2*05*10##` (dimension 10 status) gets no reply at all, while `*#2*05##` answers `*2*0*05##` /
  `*2*2*05##`; the basic
  `*2*1*05##` / `*2*2*05##` / `*2*0*05##` are echoed and the gateway adds `*2*1000#1*05##` /
  `*2*1000#2*05##` / `*2*1000#0*05##` events. The published advanced commands are accepted and echoed
  in the same `1000#` event form with their parameters: `*2*11#1#001*05##` -> `*2*1000#11#1#001#1*05##`,
  `*2*10#001#1*05##` -> `*2*1000#10#001#1*05##`, `*2*12#1#001*05##` -> `*2*1000#12#1#001#1*05##`,
  `*2*13*05##` -> `*2*1000#13#001#1*05##`, `*2*14*05##` -> `*2*1000#14#001#1*05##`.
- **WHO 4 zone 2**: the per-dimension reads 0, 12, 13, 14, 19 (`*#4*2*19*5*0##`), 20 (`*#4*2#1*20*0##`)
  and 60 (`*#4*2*60*41##`) and the single actuator read `*#4*2#1*20##` all answer; `*#4*2*11##` is
  NACKed on the command session while `*#4*2*11*0##` still arrives on the event session; `*#4*2##`
  answers dimension 0, 12, the mode event `*4*1*2##`, 14 and 13 in that order. The central-unit
  forms `*#4*#2##`, `*#4*#0##` and `*#4*#0*19##` are **NACKed** (no central unit on this plant), as
  is `*#17##`. The setpoint
  writes `*#4*2*#14*0245*M##` are answered by `*#4*2*12*0245*3##` plus the mode event (`*4*1*2##`
  after a heat / generic write, `*4*0*2##` after a cool write); `*4*303*2##` (off) is echoed. The zone
  reports `*4*1*2##` (heating) after the restore, while the Home Assistant entity kept showing `cool`
  with `hvac_action: heating` (a MyHOME state question, recorded here, not changed).
- **WHO 14**: `*14*1*0##` is echoed; MyHOME logs it as `Unsupported message type`.
- **WHO 18**: every `*#18*5N*51##` / `*#18*7N#0*51##` / `*#18*7N#0*1200##` of the sweep is NACKed
  (no meter, no actuator on this plant).

Pinned by `tests/test_f461_live_2026_11_04_trace.py` (SHA-256 provenance).
