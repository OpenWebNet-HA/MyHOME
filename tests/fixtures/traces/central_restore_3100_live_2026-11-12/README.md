# Live capture: 3550 central unit, `*4*311*#0##` against `*4*3100*#0##` (F461 2.0.11 + F455 1.0.76), 2026-11-12

The plant capture behind [OpenWebNet-HA/OWNd#169](https://github.com/OpenWebNet-HA/OWNd/pull/169)
(`set_central_mode("#0", "auto")` builds `*4*3100*#0##` since OWNd 2.0.0b14), committed here at the
maintainer's request in the review of that pull request. One run of 86 s on 2026-11-12, 16:08:24 to
16:09:50 UTC, logged at the same time by an event-session monitor on each of the two gateways of
the test plant. Every frame, time and result in the archives comes from those logs; nothing is
synthetic.

## Plant

| Item | Value |
|---|---|
| Gateways | BTicino F461, firmware 2.0.11, and Legrand F455, firmware 1.0.76, on the same SCS bus |
| Central unit | BTicino 3550 (99-zone central unit, WHERE `#0`), conditioning season, weekly program 1 before the run (`*#4*#0##` answers `*4*2101*#0##`) |
| Zones seen in the capture | 2, 3 and 4 (dimensions 0, 12, 13, 14, `#14`; fan coil actuators `2#1` and `4#2`, dimension 20) |
| Home Assistant | one MyHOME gateway entry per gateway; the harness steps name the entry they call (`F461 set_hvac_mode ...` uses `climate.climate_zone_0`, `F455 set_hvac_mode ...` uses `climate.central_unit`) |

## Files

| File | What it is |
|---|---|
| `myhome_trace_F461_central_restore_2026-11-12.json` | the F461 monitor: 176 frames, 218 rows |
| `myhome_trace_F455_central_restore_2026-11-12.json` | the F455 monitor: 186 frames, 228 rows |

Both are `derived_trace_archive` files with the four top-level keys of
`tests/fixtures/traces/f461_live_2026-10-24/` (`capture`, `gateway`, `frames`, `rows`):

- `frames`: every frame the gateway's event session delivered, in order, as
  `{timestamp, utc, direction: "rx", raw, session: "evt"}`. This is what the corpus scanners replay.
- `rows`: the same frames interleaved, in capture order, with the harness log of the run, which is
  identical in both archives. `dir` is one of
  - `step`: a harness step marker (its text is repeated in the `step` field of the rows that follow);
  - `call`: a Home Assistant service call (`service`, `data`, and `returned_after_ms` and `changed`
    as the harness logged them when the call returned);
  - `tx`: a frame the harness sent on the command session of **this** archive's gateway, with
    `reply_time_s` and either `ack: true` or `replies` (the frames the command session returned);
  - `tx_other_gateway`: the same for a frame sent on the command session of the other gateway
    (`gateway` names it);
  - `rx`: a monitor frame.
- `t` and `timestamp` are seconds since 16:08:24.760 UTC (the first line of the run); `utc` is the
  time of day as logged.

The frames Home Assistant itself sent (the `call` rows) are not `tx` rows: the harness did not log
them. They are in an archive only where a monitor delivered them.

Left out of the source logs: the five harness lines stamped before 16:08:24.760 (the end of the
harness's previous part: a step marker and two WHO 1 light read-backs with their results), the
run's banner line, and the decode text the monitor printed after each frame. The lines of this
run carry no IP address, MAC or password, so nothing had to be rewritten.

## The run

Two identical cycles (`c1`, `c2`), then the plant is put back:

1. `climate.set_hvac_mode off` on the F461 entry. Both monitors show `*4*203*#0##`, `*4*21*#0##`,
   `*4*31*#0##`, then `*4*303*Z##` from zones 2, 3 and 4. `*#4*#0##` answers `*4*203*#0##`.
2. `climate.set_hvac_mode auto` on the F461 entry. `*4*311*#0##` is on the F455 monitor 48 ms (c1)
   and 59 ms (c2) after the call; the F461 monitor shows nothing in this step. No monitor shows a
   frame from the 3550, and `*#4*#0##` still answers `*4*203*#0##` 2 s later (through the F455) and
   8 s later (through the F461).
3. `climate.set_hvac_mode cool` on the F455 entry. `*4*0*#0##` is on the F455 monitor; the F461
   monitor shows nothing in this step. `*#4*#0##` still answers `*4*203*#0##` 2 s later.
4. `*4*3100*#0##` on the F461 command session: ACK in 97 ms (c1) and 111 ms (c2). The frame itself
   is on neither monitor. `*4*2101*#0##`, `*4*21*#0##`, `*4*31*#0##`, `*4*22*#0##` follow on the
   F455 monitor 131 ms (c1) and 140 ms (c2) after the send and on the F461 monitor 147 ms and
   154 ms after it; zones 2, 3 and 4 then report `*4*0*Z##` and their setpoints again. `*#4*#0##`
   answers `*4*2101*#0##` 3 s later.
5. After c2: `*4*2101*#0##` on the F461 command session (ACK in 117 ms), the state the plant had
   before the run.

The eleven `*#4*#0##` read-backs (five through the F461, six through the F455) answer, in order:
2101, 203, 203, 203, 203, 2101, 203, 203, 203, 203, 2101. The last F455 read-back took 2.56 s and
its command session also returned `*4*311*#3##`, `*4*211*#3##`, `*4*311*#4##`, `*4*211*#4##`,
which the F455 monitor shows on the bus in that window.

## What the capture establishes

- On this plant the 3550 does not act on `*4*311*#0##` (what OWNd built for central unit `auto` up
  to 2.0.0b13) and does not act on the season command `*4*0*#0##` while it is OFF: both are on the
  bus, the central unit stays at `*4*203*#0##`.
- It acts on `*4*3100*#0##` (WHO 4 "last set up weekly program") and reports the weekly program it
  restored, `*4*2101*#0##`, within 0.16 s.
- The two monitors do not show the same frames. Only the F455 monitor shows `*4*311*#0##`,
  `*4*0*#0##` and the `*4*311*#Z##` frames of zones 2, 3 and 4; only the F461 monitor shows the
  actuator frames in the `*#4*Z#N*#20*V##` form. Neither shows `*4*3100*#0##`, and for the `off`
  call neither shows a central unit frame other than the 3550's `*4*203*#0##` report.

Not established by this capture: the behaviour of a 4-zone central unit (`#0#C`), and of any
gateway other than these two.

Pinned by `tests/test_central_restore_3100_live_2026_11_12_trace.py`, which also replays both
monitors through a central unit climate entity and checks its state against every read-back.
