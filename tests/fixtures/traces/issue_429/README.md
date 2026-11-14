# #429 WHO 4 traces (MyHomeServer1 + Home+Control)

Verbatim bus traces contributed on [#429](https://github.com/OpenWebNet-HA/MyHOME/issues/429). They are facts: never edit a frame. `tests/test_issue_429_zone_state.py` replays them through the climate entity.

On these plants the zone's mode and setpoint travel as WHO 4 dimension 7 (`*#4*Z*7*CONTEXT*STATE[*TTTT]##`), which is not in the public WHO 4 document and never appears in the reply to `*#4*Z##`. OWNd decodes it from OWNd#60 on.

## @TheDarkWizard: MyHomeServer1 2.87.13, Home+Control, four KM4691 zones (zone 4 is a heating-only bathroom)

Card exports (WHO 4 filter). The reporter's times match the `iso_time` of the frames.

| File | Comment | What the reporter did |
|---|---|---|
| `…T18-13-49.json` | [5800423418](https://github.com/OpenWebNet-HA/MyHOME/issues/429#issuecomment-5800423418) | Zone 2 OFF → manual (the app set 26 °C), then unlimited manual, then OFF, plant in cooling |
| `…T19-22-25.json` | [5801732850](https://github.com/OpenWebNet-HA/MyHOME/issues/429#issuecomment-5801732850) | 19:18 zone 2 on at 29 °C manual; 19:20 a setpoint that opens the valve; 19:21 one that closes it; 19:22 OFF |
| `…T19-28-14.json` | 5801732850 | 19:26 "estate" (summer) scenario on, 19:27 off |
| `…T19-32-29.json` | 5801732850 | 19:30 "inverno" (winter) command turns all thermostats on, 19:32 all OFF |
| `…T19-39-59.json` | 5801732850 | Heating ↔ cooling a few times from the MyHome app and MyHome_Touch |
| `…T20-20-42.json` | [5802318777](https://github.com/OpenWebNet-HA/MyHOME/issues/429#issuecomment-5802318777) | 20:18 OFF → winter; 20:19 Away until changed; 20:19:50 Away until 20:35; 20:20:23 Away setpoint 21 °C |
| `…T22-25-37.json` | [5804228957](https://github.com/OpenWebNet-HA/MyHOME/issues/429#issuecomment-5804228957) | Home Assistant restart: the status replies carry no dimension 7 |
| `…T22-45-26.json` | 5804228957 | 22:36 OFF → cooling; 22:37 zone 2 manual 20 °C; 22:38 back to the schedule; 22:45 the schedule moves to "Summer ECO" |

## @xtimmy86x: seven heating zones; Home Assistant on an F454, MyHomeServer1 runs the program on the same bus

| File | Comment | What it shows |
|---|---|---|
| `xtimmy86x_capture.24092026.00-13.txt` | [5813061888](https://github.com/OpenWebNet-HA/MyHOME/issues/429#issuecomment-5813061888) | 13 h passive trace, all zones in automatic. 06:00 Night (17 °C) → Comfort (19 / 20 / 18.5 °C), 09:00 → Eco (18 °C). MyHomeServer1 writes `*#4*Z*#7*1*1*TTTT##` and re-asserts `*#4*Z*#5*0##` every ~794 s |
| `xtimmy86x_ha_setpoint_2026-09-24.txt` | [5814005492](https://github.com/OpenWebNet-HA/MyHOME/issues/429#issuecomment-5814005492) | The trace block of that comment, as posted. 13:56:14 Home Assistant sets zone 1 to 20 °C manual (`*#4*1*#14*0200*1##`); MyHomeServer1's next program writes go to zones 2-7 only, and zone 1 still holds 20 °C at 14:21 |
