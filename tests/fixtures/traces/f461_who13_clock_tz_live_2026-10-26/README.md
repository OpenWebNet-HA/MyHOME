# Live capture: BTicino F461 (firmware 2.0.11), 2026-10-26, WHO 13 clock writes with non-standard time zones

Every frame exchanged with the physical F461 gateway on 2026-10-26 between 20:46:39 and 20:59:16 UTC while the
capture asked for in [comment 6426869553](https://github.com/OpenWebNet-HA/OWNd/pull/144#issuecomment-6426869553)
of OpenWebNet-HA/OWNd#144 was run: an event-only listener on the bus for the whole run, and a probe command
session that wrote the gateway clock with the time-zone field `0530`, then `124`, in the dimension 0 form
(`*#13**#0*HH*MM*SS*TZ##`) and in the dimension 22 form (`*#13**#22*HH*MM*SS*TZ*W*DD*MM*YYYY##`), each write
followed by the `*#13**0##` / `*#13**22##` read-backs; then the listener alone through the gateway's spontaneous
10-minute clock broadcast; then the restore `*#13**#0*HH*MM*SS*999##`. Every write carried the current local time
(UTC + 2 h) and today's date, as the comment asked. Nothing is synthetic except the gateway's MAC and LAN address
in the `gateway` block (no WHO 13 dimension 10 / 12 was requested); the HMAC handshakes are not included.

Listener and probe ran **OpenWebNet-HA/OWNd#144 head `da77a53`**: the probe sends raw strings but refuses a frame
its OWNd cannot parse, and OWNd master raises `ValueError` on a `0530` / `124` time zone, so master could not have
sent the writes. Every frame was then decoded again offline with **OWNd master `050dc1d`**, and every row that
carries a frame stores both decodes (see "Which decoder is which").

The same plant's earlier captures are `tests/fixtures/traces/f461_live_2026-10-15/`, `f461_live_2026-10-18/`,
`f461_live_2026-10-23/` (whose run 6 holds the F461's 10-minute broadcasts `*#13**#0*HH*MM*00*##` with the same
empty time-zone field, and whose `*#13**0##` reads are also delivered on the event session, as here),
`f461_live_2026-10-24/`, `f461_presets_live_2026-10-24/` and `f461_actuators_live_2026-10-25/`.

## Plant

| Item | Value |
|---|---|
| Gateway | F461, firmware 2.0.11 (`*#13**16*2*0*11##`), kernel 5.10.35, distribution 2.26.4, WHO 13 device type `200`, WHO 1013 object model `134.15.5.0`, clock time zone `999`, gateway clock on local time (UTC + 2 h, in step with the capture host's wall clock to the second), HMAC SHA-256 on both sessions (identified in `f461_live_2026-10-23`) |
| Climate | Living Now KW4691 standalone thermostat at zone 2; its two spontaneous reports during the broadcast wait (`*#4*2*0*0240##` at 20:52:00, `*#4*2*60*39##` at 20:55:44 UTC) are the only non-WHO 13 frames |
| Not involved | lights 06 / 07 / 09, shutter 05, no Home Assistant instance was connected |

## File format: `myhome_trace_F461_who13_clock_tz_live_2026-10-26.json`

Standard `MyHOME` derived trace archive, in the layout of `f461_actuators_live_2026-10-25` with the probe
segments of `f461_live_2026-10-24`:

- `capture` and `gateway`: metadata with the RFC 5737 documentation IP `192.0.2.92` and the synthetic MAC
  `00:03:50:00:10:26` (the gateway of the earlier F461 captures, a new synthetic MAC for this capture).
  `capture.listener` is the event-only listener (`00_listener.jsonl`, 1500 s, 20:46:32.8 - 20:59:39 UTC) and the
  OWNd it ran; `capture.runs` lists each run with its UTC window, `t_offset`, `probe_requests` and
  `probe_segments` (one per probe invocation: its source file, step, UTC window, the probe's 3 s pause and
  `listener_offset_s`, where the probe's clock sits on the archive axis, with the number of frames both sessions
  received and their spread, at most 1 ms); `capture.decoders` says which decoder wrote which fields.
- `frames`: the 16 event-session `rx` frames (`timestamp`, `direction: "rx"`, `raw`, `run`, `session: "evt"`),
  replayed by `test_device_health_corpus.py` and parsed by `test_trace_oracle_validation.py` (tier 4).
- `rows`: the 79 session-log entries in capture order:

| `dir` | Meaning | Fields |
|---|---|---|
| `tx` | frame written on the probe's command session | `frame`, `utc`, `is_status_request` (how the probe sent it), the two decodes of the written string |
| `rx` | frame read; `session: "cmd"` for the replies and the closing `*#*1##`, `session: "evt"` for the listener | `frame`; the two decodes (not on the ACK). Command-session rows carry `t_estimated: true`: the session hands the replies over as a batch with the ACK, so they sit at the request's `t` and the ACK at the result's `t`. Event rows carry `utc` |
| `result` | what the command session returned for the `tx` | `ack` (always true here), `reply_time_s`, `replies` (empty on an ACK that carried no data), `events_after` (the event-session frames of the pending window, `tx.t <= t <= result.t`), `events_within_wait` (the frames the probe's own event session received between the send and its record 3 s later; `wait_s: 3.0`), `utc`, `note` |

### Which decoder is which

| Fields | Decoder | What it is |
|---|---|---|
| `ownd_class`, `ownd_log`, `ownd_timezone`, `ownd_raw_timezone` | OpenWebNet-HA/OWNd#144 head `da77a53` | the OWNd the listener and the probe ran. On event rows these are what the listener logged at capture time (re-decoded offline to the same result); on `tx` and command-session rows they were decoded offline (`OWNCommand.parse` for `tx`, `OWNMessage.parse` for `rx`) |
| `ownd_master_class`, `ownd_master_log` | OWNd master `050dc1d` | decoded offline the same way |
| `ownd_master_error` | OWNd master `050dc1d` | the exception text, on the rows where master raised instead (only the four `tx` rows of the non-standard writes) |

Sessions and timing: `t` is the listener's clock, one monotonic axis over all runs, shifted so `t = 0` is the
baseline `*#13**0##` request (20:46:39 UTC; `utc` strings are the listener's wall clock at 1 s resolution). The
probe's rows are placed on it through the frames both sessions received (each read's reply reached the listener
and the probe's event session 4 - 14 ms apart, the offsets of the two frames of each invocation agree within
1 ms). `reply_time_s` is measured (the time the command session waited for the ACK); `tx.t` is reconstructed from
the probe log (the probe records a request after its pause, so `tx.t` = record time - 3 s - `reply_time_s`) and
`result.t` = `tx.t` + `reply_time_s`. `step` is the probe segment of the request in flight, `listen` for the
broadcast run.

## The runs

| `run` | UTC | What it was |
|---|---|---|
| `run1_baseline_reads` | 20:46:39-20:46:42 | `*#13**0##` -> `*#13**0*22*46*39*999##` (ACK 10 ms), `*#13**22##` -> `*#13**22*22*46*42*999*01*26*10*2026##` (11 ms); both replies also delivered on the event session |
| `run2_write_dim0_tz_0530` | 20:46:47-20:46:53 | `*#13**#0*22*46*45*0530##`: ACK in 10 ms, no reply, nothing on the event session; read-backs `*#13**0*22*46*50*999##`, `*#13**22*22*46*53*999*01*26*10*2026##` |
| `run3_write_dim0_tz_124` | 20:46:57-20:47:03 | `*#13**#0*22*46*56*124##`: ACK 9 ms, no reply, no echo; read-backs `999` (`*#13**22##` ACK 59 ms, the slowest of the capture) |
| `run4_write_dim22_tz_0530` | 20:47:07-20:47:14 | `*#13**#22*22*47*06*0530*1*26*10*2026##`: ACK 18 ms, no reply, no echo; read-backs `999` |
| `run5_write_dim22_tz_124` | 20:47:18-20:47:24 | `*#13**#22*22*47*17*124*1*26*10*2026##`: ACK 12 ms, no reply, no echo; read-backs `999` |
| `run6_clock_broadcast_listen` | 20:50:00-20:55:44 | the listener alone, 2.6 min after the last write: `*#13**#1*01*26*10*2026##` then `*#13**#0*22*50*00*##` 2 ms later (the empty time-zone field, not `124`); then the thermostat's temperature and humidity reports |
| `run7_restore_tz_999` | 20:59:10-20:59:16 | `*#13**#0*22*59*08*999##`: ACK 12 ms, no reply, no echo; read-backs `*#13**0*22*59*13*999##`, `*#13**22*22*59*16*999*01*26*10*2026##` |

## What the plant established (each row has its frames in the archive)

* **The physical F461 ACKs a clock write with a non-standard time zone and does nothing with it.** All four
  writes (`0530` and `124`, dimension 0 and dimension 22) were ACKed in 9 - 18 ms with no reply. Nothing reached
  the event session within the 3 s the probe waited after each write (`events_within_wait: []`), nor between a
  write and its first read-back, nor anywhere else in the 13-minute listener window: the only write-form WHO 13
  frames the F461 emitted are the two of its 10-minute broadcast. No event-session frame carries `0530` or `124`
  in any field.
* **The read-back path stays `999`.** Every `*#13**0##` / `*#13**22##` before, after each write and after the
  restore answered the time zone `999` on the command session, and the reply was delivered on the event session
  too (`events_after == replies`), as in `f461_live_2026-10-23`. Unlike the emulated F450 of the oracle
  (`who13-clock-readback`, "the only firmware that echoes a non-standard offset back"), the F461 does not store
  the value.
* **The 10-minute broadcast carries no stored time zone.** At 20:50:00 UTC, with `124` the last value written,
  the F461 broadcast `*#13**#1*01*26*10*2026##` and `*#13**#0*22*50*00*##`: the same form as the broadcasts of
  `f461_live_2026-10-23`, empty time-zone field.
* **The restore `999` is ACKed and not echoed either.** The oracle's emulated F461 `020011` (`who13-clock`,
  `ack` / `out`) echoes `*#13**#0*...*999##` on the event session; the physical one ACKed it in 12 ms and put
  nothing on the event session (`events_within_wait: []`, no `*#13**#0*...*999##` in the listener window).
  Taken with the four non-standard writes, the emulation's "echoed on the event session" column for the F461 does
  not reproduce on the physical gateway for any of the five writes of this capture.
* **No written time was in effect at the read-back (deduction at 1 s resolution).** Each write carried the capture
  host's wall clock as sampled 1 - 2 s before the send (the shell expanded the time before the probe connected);
  had the F461 applied it, the read-back 3.0 s later would lag the wall clock by that 1 - 2 s. Instead every
  read-back equals the wall clock of its own request (UTC + 2 h): the read-back after the `0530` write is 5 s past
  the written time, after the `124` write 4 s, after the dimension 22 writes 5 s and 4 s, after the restore 5 s.
  The gateway clock was in step with the host's clock before the first write and still was after the restore. The
  rows cannot tell a write that was ignored from one that was applied and corrected by another time source within
  those 3 s; what they show is that no written time was in effect when it was read back.
* **What this means for OWNd master's `ValueError`.** On the frames the F461 actually emitted (16 event frames,
  all `999` or an empty time-zone field) the #144 head and master decode identically
  (`ownd_class == ownd_master_class`, `ownd_log == ownd_master_log` on every `rx` row). The only strings on
  which they differ are the four written command strings: master raises `Invalid gateway timezone: '0530'` on
  the `0530` forms and the `timedelta` range error (`offset must be a timedelta strictly between ...`) on the
  `124` forms; the #144 head parses them as `OWNGatewayCommand` with `timezone None` and `raw_timezone`
  `"0530"` / `"124"`. So the physical F461 cannot trigger master's `ValueError` through its event session: the
  path that raises is a sender parsing its own write (what stopped the probe under master), and the tolerance of
  #144 is what lets such a write be sent at all.
* **Entity replay.** The integration's only WHO 13 clock handling is `_handle_gateway_diagnostics`: fed through
  `GatewayEventDispatcher.process_message` (what the listening loop calls), each `999` read-back deletes the
  "unconfigured time zone" repair issue of an F461 entry, the broadcast (an `OWNGatewayCommand`) and the two
  WHO 4 reports leave it alone; the written strings never reach it as events (on master they raise at parse, on
  the #144 head they are commands, only logged). Pinned in the tests.
* **Timings.** Reads 9 - 18 ms (one 59 ms); writes 9 - 18 ms; the probe's event session received each read
  reply 4 - 14 ms after the listener did.

## MyHomeServer1 comparison (not in the archive)

The same sequence was run on 2026-10-26, 21:01-21:03 UTC, on a MyHomeServer1 3.87.13 (a private home; the raw
capture holds the house's own WHO 4 traffic, so its frames are not in this repository). What it did: the
dimension 0 writes with `0530`, `124` and even the restore `999` got no reply at all (probe timeout 10 s, no ACK /
NACK, no echo), and the next command was answered only after about 7 s; the dimension 22 writes with `0530` /
`124` were ACKed in 0.01 s with no echo; every read-back stayed `999`; the clock was never changed. That is what
the oracle's emulation of the MyHomeServer1 `028206` says (`who13-clock`: drops `#0` without a reply, ACKs `#22`
with no echo), so the comment's "it adds nothing here" is physically confirmed; the ~7 s stall after a dropped
dimension 0 write is the only new fact.

## What this capture backs

| Pull request / issue | Frames in this archive |
|---|---|
| OpenWebNet-HA/OWNd#144 (tolerate `0530` / `124` time zones in WHO 13 echoes and replies; comment 6426869553 asked for this capture) | the four non-standard writes (`tx` rows with both decodes: master `ownd_master_error`, head `OWNGatewayCommand` / `raw_timezone`), the 16 event frames on which both decode identically, the `999` read-backs, the broadcast, the restore |
| own-firmware-oracle `who13-clock` / `who13-clock-readback` (F461 `020011` `ack` / `out`; F450 read-back `T=0530`) | the physical F461 ACKs without an echo on all five writes and keeps `999` on read-back |
| MyHOME `_handle_gateway_diagnostics` (time-zone repair issue, F461 exemption) | the `999` read-backs and the broadcast replayed through the dispatcher |

Tests: `tests/test_f461_who13_clock_tz_live_2026_10_26_trace.py`.
