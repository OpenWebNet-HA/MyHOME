# Legrand F455 (firmware 1.0.76): the silent-zone poll on a mixed plant, 2026-11-17 (#932)

The plant: a physical test plant with a **Legrand F455** (firmware 1.0.76) and a BTicino F461
(firmware 2.0.11) on the same SCS bus, a 3550 central unit (`#0`), zones `3` and `4` under the
central unit, and zone `2`, a standalone thermostat with its own season. On that day the central
unit was in cooling (`*4*210#0215*#0##`) and zone 2 in heating.

Home Assistant (Core 2026.11.1) had one MyHOME entry, the F455, as the primary gateway, with one
command worker; the F461 had no entry and sent nothing. Each log is the WHO 4 traffic one gateway
delivered on an event session, written by a listen-only monitor (OWNd `OWNEventSession`, no request
and no command sent): one line per frame, UTC time, the frame, and OWNd's reading of it. Nothing
was changed in the lines.

| File | What it is |
|---|---|
| `F455_who4_monitor_start_nothing_stored_2026-11-17.log` | 08:31:50Z to 08:34:05Z, as the F455 delivered it. A start with no stored state for the four climate entities (core init 08:31:32Z): the central unit's answer to `*#4*#0##` (asked when the central unit was added), one temperature report of zone 2 by itself at 08:33:10Z, and from 08:33:53Z, 120 s after the startup sweep (the grace of the build that ran then), the answers to the reads of the silent-zone poll: zone 2 `*13`, `*14`, `*12`, `#1*20`; zones 3 and 4 `*0`, `*13`, `*14`, `*12`, `#1*20`. |
| `F461_who4_monitor_start_nothing_stored_2026-11-17.log` | The same window as the F461 delivered it. |
| `F455_who4_monitor_poll_2026-11-17T08-00.log` | 08:00:46Z to 08:00:52Z, as the F455 delivered it: the answers to the poll of an earlier start the same day (state restored, so zone 2 was also asked `*0`). |
| `F461_who4_monitor_poll_2026-11-17T08-00.log` | The same window as the F461 delivered it. |

## What the logs show

- **Every read of the poll was answered**: 14 reads at 08:33:53Z to 08:33:55Z, 13 at 08:00:47Z to
  08:00:51Z. In the Home Assistant log the answer was collected on the command session before
  the ACK, 0.08 to 0.18 s after the read was taken from the queue, for 26 of the 27 reads; the
  27th collected another zone's frame (below) and its own answer came on the event session.
- **Zones under the central unit and the standalone zone answer differently.** Zones 3 and 4
  follow their `*12` answer with the season of the central unit (`*4*0*3##`, `*4*0*4##`,
  conditioning); zone 2 follows it with its own (`*4*1*2##`, heating).
- **The F455 delivers frames the F461 does not.** `*4*311*#Z##` next to answers of a zone (the
  lines with `mode is set to 'auto'`), and the central unit's three frames a second time, about
  one second after the first, after one `*#4*#0##`.
- **The state of zone 3's actuator comes twice after one read.** `*#4*3#1*20*0##` at 08:00:49.172Z
  and 08:00:49.261Z, and at 08:33:55.201Z and 08:33:55.470Z. On the F461 a write-form frame to
  that actuator, `*#4*3#1*#20*0##`, stands between the two copies both times (08:00:49.209Z,
  08:33:55.431Z); the F455 does not deliver that frame. The logs do not show which device sent it.
- **The second copy can fall inside the next read.** At 08:00:49Z it arrived while
  `*#4*4#1*20##` was open, was collected as that read's reply, and Home Assistant logged
  `Gateway FIFO desync on *#4*4#1*20##: command session returned delayed replies for zone(s) 3`
  (the guard of #886). At 08:33:55Z it arrived 0.13 s after the reply to `*#4*4#1*20##`, and
  nothing was logged. Six more starts the same day (not in these files) had the two copies as
  well, and five of them logged the warning: eight starts, the frame twice in every one, the
  warning in six. In one of the six it was for `*#4*3#1*20##` and zone 2, whose actuator frame
  `*#4*2#1*20*7##` had come twice, 0.06 s apart.
- **The builds behind these logs sent the reads of all zones back to back.** Since then the poll
  asks one zone after another and waits for the bus to be quiet (0.75 s without a frame) after
  every answer, so that a second copy passes before the next read opens; a read the guard still
  drops is sent once more, unless the zone's own answer arrived in the meantime.
