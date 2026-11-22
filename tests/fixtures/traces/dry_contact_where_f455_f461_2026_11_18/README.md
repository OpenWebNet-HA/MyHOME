# Dry contact WHERE: F455 `301` / `302`, F461 `31` / `32` (live, 2026-11-18)

One plant, one SCS bus, two gateways: an F461 (firmware 2.0.11) and an F455 (firmware 1.0.76), and an
automation dry-contact interface with contacts 1 and 2.

`frames.json` holds `[time, gateway monitor, frame]` per window (local time of the capturing host), the
frames each gateway showed on its event session:

- `status_read_301_302`: a client sent `*#25*301##` (00:13:46.116) and `*#25*302##` (00:13:48.244) on an
  F461 command session. The F455 showed the request and the contact's answer with the padded WHERE
  (`*25*31#0*301##`), the F461 showed the same answer 13 ms later as `*25*31#0*31##`. The F461 put the
  padded request on the bus and showed the answer in its own spelling; on 2026-11-22 the same F461 answered
  `*#25*301##` on the command session with NACK (four reads of four) and acknowledged `*#25*31##`.
- `failover_read_31_through_f455`: Home Assistant, with the F461 unreachable and the F455 as warm standby,
  sent `*#25*31##` through the F455 (01:19:47.563). The F455 answered `*25*31#0*301##`: it accepted the
  unpadded spelling and answered in its own.

The WHO 25 PDF writes contact 1 as `*25*31#1*31##` (WHERE = `3` + `[1-201]`); F454, MH200N and MH202
captures in `issue_466` carry `31` / `33`. No other capture has the padded form.

Replayed by `tests/test_dry_contact_where_f455_f461_2026_11_18.py`.
