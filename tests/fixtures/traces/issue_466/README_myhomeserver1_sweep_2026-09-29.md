# #466 MyHomeServer1 sweep with a controlled setpoint experiment

Verbatim bus trace (a truncated 200-frame tail: the card was armed at 18:17:14, the buffer starts at 18:19:24; the setpoint experiment is complete) contributed by **@gdluck** on [#466](https://github.com/OpenWebNet-HA/MyHOME/issues/466)
(bus card export, HA 2026.9.4, integration 2.0.0b13, OWNd 2.0.0b8, gateway firmware 3.87.13). Never edit a frame.

| File | Type | Description |
|---|---|---|
| `myhome_sweep_MyHomeServer1_all_2026-09-29T18-20-22.json` | Sweep export (200 frames, buffer truncated) | Lighting status sweep, zone-state sweep of 32 zones (35-70), then setpoint writes on zones 60, 68 and 55. |

## What it shows

- **Setpoint write echo**: `*#4*Z*#14*T*1##` is answered by `*#4*Z*12*T*3##`, `*4*1*Z##` and the temperature; no dimension 7 or 14 echo. An unchanged repeat write gets only the temperature back.
- **Zone 55 (measured 24.6 C)**, setpoint above the room: actuator `*#4*55#1*20*1##`, ~2.3 s later pump call `*4*4001#55*0#3##` + `*#4*0#3*20*1##`.
  Setpoint below: pump stops (`*4*4002#55*0#3##`, `*#4*0#3*20*0##`, `*4*4002*55##`) about 2 s **before** the valve closes (`*#4*55#1*20*0##`).
- `*#1*66*4*100*4##`: unsolicited WHO 1 dimension 4 report on point 66 (undocumented for WHO 1).
- Dimension 12 carries mode `3` on an ordinary manual heating write.

## Diagnostics download (startup poll)

`diagnostics_MyHomeServer1_startup_poll_2026-09-29T18-31.json`: the same gateway's HA diagnostics download
([#466 comment 5896287944](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5896287944)), with 500 bus frames
of one startup poll (18:29:10 - 18:31:17). The config-entry id (`entry_id`, the `setup_times` key and the issue id) and the time zone were made
synthetic (`01PLANT000000000000000466`, `UTC`); host, MAC and password were already redacted by the download. The `home_assistant` block keeps only the installation type, version and time zone, and only the `myhome` custom component is listed. `scripts/anonymize_plant_fixture.py --check` passes.

- The integration polls `*#4*Z##` once per climate zone, one after the other. A zone that answers costs ~1.3 s of the queue.
- Eleven restored zones (0-4, 6, 32, 33, 71, 75, 76) never answer: no frame of theirs is on the bus, and consecutive polls are ~6.4 s apart, ~70 s in total. The bus card records no `NACK` frame (a refused status request is not recorded); the debug log below shows how the gateway ends the wait.
- Zones 36, 40, 42, 55, 60 and 68 answer dimensions 0, 12, 13 and 14 but no dimension 7 (#454).

## Debug log of the dead-zone poll

`MyHomeServer1_dead_zone_nacks_2026-09-29T22-45.txt`: 44 lines of the reporter's debug log
([#553 comment 5897634705](https://github.com/OpenWebNet-HA/MyHOME/issues/553#issuecomment-5897634705), startup of 22:45, before the dead-zone skip),
only the climate entity count, the dead-zone status requests as they were queued, and their retries and refusals. The rest of the log (other
integrations, the light and heating traffic) is left out. Gateway address and MAC were made synthetic (`192.0.2.9`, `00:03:50:00:04:66`). The reporter's
`myhome.yaml` configures 33 zones (35-70, `standalone`); the 44 climate entities are those plus the 11 restored ones.

- The gateway **does** answer a silent zone: with a NACK. The worker sends `*#4*Z##`, OWNd retries once 3.2 s later, and the gateway's NACK arrives another 3.2 s after that (`Gateway rejected status request ... (NACK, 0 response(s))`): ~6.4 s per zone, 69 s from the first request to the last.
- The general request `*#4*0##` (zone 0) is refused at once, without the retry delay.
- So the wait is not a timeout: a refused status request is the "never answers" signal `poll_health` counts (the request's delivery future is cancelled).
