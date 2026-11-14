# F459 gateway — full Home Assistant debug log, 2026-11-05 (#466)

A real plant on a BTicino **F459** Ethernet web server: 158 lights (67 addresses answering, 20 of
them behind an F422 bus interface at `#4#01`), 40 covers and 19 thermostat zones. The F459 is not
in the trace matrix before this capture.

`F459_full_debug_log_2026-11-05T16-23-24.txt`: the complete `home-assistant_myhome` log of the
session (2302 lines, 18:13:29 → 18:23:11 local, exported 16:23:24Z), verbatim from the first
`custom_components.myhome` line; only the container start-up boilerplate above it is left out. The
gateway address and MAC were made synthetic (`192.0.2.59`, `00:03:50:00:11:05`, keeping the BTicino
OUI); nothing else was changed. The gateway uses the OPEN password (numeric nonce); no password is
echoed. Home Assistant 2026.10.x, MyHOME 2.0.0b17, OWNd 2.0.0b13.

What it carries:

- **Setup flow** (18:13–18:21): auto-discovery fails and the entry is created manually; after a
  `myhome.yaml` reload at 18:20:31 the F459 refuses three test-session connects in a row (10 s each,
  `ConfigEntryNotReady`) and accepts the fourth 5 s later; the first command-session connect after
  the event session opens times out (10 s) and the retry 1 s later succeeds.
- **Identification**: WHO 13 dimension 15 answers device type `200`, so MyHOME keeps the configured
  `F459` and asks WHO 1013 dimension 1, which answers `*#1013**1*65*15*5*0##`; OWNd decodes it as
  object model F459 (65). `*#13**15*200##` is received five times at 25 s intervals with no queued
  request logged. A second diagnostic, `*#1013*260*11*111111111111111101101111##` (WHERE 260,
  dimension 11), parses in OWNd but MyHOME logs it as unhandled.
- **General status requests**: `*#1*0##` completes in 6.0 s with 79 light frames and the final
  acknowledgement. `*#2*0##` delivers 44 cover frames in 3.2 s and then **never terminates**: MyHOME's
  10 s window expires, it logs `Timed out awaiting the complete response` and closes the command
  session (twice in the log, 18:15:32 and 18:21:51), then reads the 40 covers one by one
  (`*#2*WHERE##`, 18:21:51 → 18:22:31). `*#4*0##` and `*#16*0*5##` are skipped by the F459 profile;
  the 19 zones are polled per zone with dimensions 0, 20 and 60.
- **613 inbound frames** (18:21:24 → 18:23:08): 427 WHO 1 (238 events including the group event
  `*1*1*#5##`, 156 dimension-1 level replies with levels 132–200 and speeds 2/4/5, 33 dimension-4
  replies), 126 WHO 2 (all `*2*0*WHERE##`, 40 covers), 53 WHO 4 (19 temperatures 17.4–27.8 °C,
  22 actuator-1 states, 12 humidity readings 37–45 % on 11 zones), 5 WHO 13 and 2 WHO 1013.
- **F422 routing on WHO 1**: 161 of the WHO 1 frames carry `#4#01`, 82 of them events
  (`*1*WHAT*WHERE#4#01##`) and the rest dimension-1 replies to `*#1*WHERE#4#01*1##`; the F459 forwards
  lighting requests and status across the interface in both directions.
- **OFF dimmers answer the dimension-1 read with the dimension-4 form**: 33 of the `*#1*WHERE*1##`
  reads are answered `*#1*WHERE*4*100*S##` (level 100 = off, speed 2 or 4), collected as the command
  response to that read; ON dimmers answer with dimension 1 (levels 132–200). The Encyclopedia records
  the same behaviour on an F454 (`who-1-lighting/dimensions.md`, "a `DIMENSION 1` request while the
  actuator was OFF was answered with a `DIMENSION 4` frame instead"); OWNd decodes both as
  `OWNLightingEvent` and MyHOME logs the dimension-4 form as `switched off`.
- **WHO 18**: `*#18*51*51##` and `*#18*51*113##` are acknowledged with no reply frame (no meter on
  the plant).
- **OWNd parse coverage, identical on 2.0.0b13 and dev:** 613 of 613 parse (427 `OWNLightingEvent`,
  126 `OWNAutomationEvent`, 53 `OWNHeatingEvent`, 5 `OWNGatewayEvent`, 2 `OWNGatewayDiagnosticEvent`);
  no `Data received is not a message` and no `Unsupported message type` line in the log. Five
  lighting reads are NACKed (`*#1*0113##`, `*#1*0114##`, `*#1*0713#4#01##`, `*#1*25#4#01*1##`,
  `*#1*92*1##`).

Pinned by `tests/test_issue_466_f459_full_log_2026_11_05_trace.py` (SHA-256 provenance).
