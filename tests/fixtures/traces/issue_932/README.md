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
| `F455_who4_monitor_paced_poll_2026-11-17T11-47.log` | 11:47:44Z to 11:47:58Z, as the F455 delivered it. The poll of a start with nothing stored (core init 11:47:05Z) on the build that asks one zone after another and waits for a quiet bus: from 30 s after the startup sweep, zone 2 `*0`, `*13`, `*14`, `*12`, `#1*20`, then two reads zone 2 makes on its own once its actuator reported a fan coil (`*19`, `*11`), then zones 3 and 4 `*0`, `*13`, `*14`, `*12`, `#1*20`. |
| `F461_who4_monitor_paced_poll_2026-11-17T11-47.log` | The same window as the F461 delivered it. |
| `F455_who4_monitor_probe_303_2026-11-17T11-21.log` | 11:21:43Z and 11:31:44Z, as the F455 delivered it: two reports the slave probe `303` (zone 3, probe 03) sent by itself, 601.3 s apart. |
| `F455_ha_log_probe_303_read_2026-11-17T11-31.log` | Not a monitor log: the six DEBUG / INFO lines of Home Assistant from the first of those reports to the second that concern probe 303, as logged (local time, UTC+2; the address of the gateway replaced by `<ip>`). The first report as Home Assistant received it, the fallback read `*#4*303*15##` of the temperature sensor of that probe on the build of that hour (a report kept the read back for 300 s), a restart of Home Assistant made for the test (the `Initializing` line), and the second report. |

## The zone status read and a repeated actuator "off", 13:46Z

Three more files of the same plant and day. Zone 4 has two actuators, and the fan coil that cools
it is on actuator 2.

| File | What it is |
|---|---|
| `F455_who4_monitor_zone_status_read_2026-11-17T13-46.log` | 13:46:10Z to 13:46:27Z, as the F455 delivered it. The answers to the poll of a start with nothing stored, on the build whose poll ends a zone with `*#4*<zone>*19##`: zone 2 `*0`, `*13`, `*14`, `*12`, `#1*20`, `*19`, then `*11`, which zone 2 asks on its own; zones 3 and 4 `*0`, `*13`, `*14`, `*12`, `#1*20`, `*19`; then `*11` of zone 4, asked on its own. |
| `F455_who4_monitor_actuator_reads_2026-11-17T13-46.log` | 13:46:50Z to 13:46:56Z, as the F455 delivered it: the answers to three reads a test client sent through the F461, about 3 s apart: `*#4*4#1*20##`, `*#4*4#2*20##`, `*#4*4*19##`. |
| `F455_ha_states_zone_4_2026-11-17T13-46.jsonl` | Not a monitor log: the state of zone 4's climate entity in Home Assistant at three moments: before the answer to the first of those reads, after it, and after the answer to the second read (`t`: when the state was read; `last_updated`: Home Assistant's time of the last change). |

- **Dimension 19 is the last answer of a zone's poll and tells what actuator 1 does not.** Zone 4:
  `*#4*4#1*20*0##` (actuator 1 off) at 13:46:26.045Z, `*#4*4*19*8*0##` (the cooling fan at speed 3)
  at 13:46:26.830Z. The entity then showed `cooling`, `running_fan_speed: high` and
  `actuators: {"1": "off"}` (the first row of the states file). Zone 3: `*#4*3#1*20*0##` twice
  (13:46:20.740Z, 13:46:20.819Z), then `*#4*3*19*0*0##` 0.84 s after the second copy.
- **A read of actuator 1 by another client set the zone to idle.** `*#4*4#1*20*0##` came again at
  13:46:50.248Z, 23.4 s after the dimension 19 answer, and the entity went to `idle`,
  `running_fan_speed: off` (the second row). The read of actuator 2 was answered with two frames,
  `*#4*4#2*20*0##` at 13:46:53.378Z and `*#4*4#2*20*8##` 0.039 s later, and the entity was back
  to `cooling` and `high` with `actuators: {"1": "off", "2": "on"}` (the third row): 3.17 s of
  `idle` while the fan coil ran, by the entity's `last_updated`. Since then an "off" of an actuator
  that is already known to be off changes nothing while dimension 19 says a side is active.

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
- **The builds behind the logs of 08:00Z and 08:31Z sent the reads of all zones back to back.**
  Since then the poll asks one zone after another and waits for the bus to be quiet (0.75 s
  without a frame) after every answer, so that a second copy passes before the next read opens; a
  read the guard still drops is sent once more, unless the zone's own answer arrived in the
  meantime.
- **With the quiet time the second copy passes before the next read** (the logs of 11:47Z).
  `*#4*3#1*20*0##` comes twice again, at 11:47:53.942Z and 11:47:54.021Z on the F455, and the
  next frame, zone 4's answer `*#4*4*0*0257##`, follows 0.861 s after the second copy. All 15
  reads of the poll and the two reads of zone 2's own were answered, and Home Assistant logged no
  warning. In its log no read was written less than 0.75 s after a WHO 4 frame was received.
- **A probe that reports every 10 minutes was asked in between, and the F455 refused the read
  only after 9 s** (the two files of probe 303). The probe reported at 11:21:43.258Z and
  11:31:44.563Z, and Home Assistant received the first report at 11:21:43.315Z. The sensor's
  periodic update queued `*#4*303*15##` at 11:31:06.770Z, 563.5 s after that report, and the
  F455 answered with a NACK at 11:31:16.094Z, 9.324 s after the read was queued and 8.938 s after
  a worker took it. Since then a report of a probe keeps its fallback read back for 900 s, and a
  refused read is not sent again until the entry is reloaded.
