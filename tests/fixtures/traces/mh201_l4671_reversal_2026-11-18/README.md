# MH201 (FW 3.3.6) + BTicino L4671/2: Opposite Direction Stops the Actuator Instead of Reversing It

- **Gateway Model**: BTicino `MH201` (`WHO=13` `Device Type = 200`, `WHO=1013` `OBJECT_MODEL = 10`, `MH201Profile`, `0.1 s` command queue pacing, 1 send worker)
- **Firmware Version**: `3.3.6`
- **Actuator**: BTicino `L4671/2` shutter actuator on `WHERE = 41`, configured `strategy: timed`, `travel_time: 15` (the plant has 35 such covers)
- **Environment**: MyHOME 2.0.0, OWNd 2.0.1
- **Captured**: 2026-11-18 (UTC) from the `myhome/bus_monitor/subscribe` history; the four `tx` frames were sent raw with the `myhome/bus_monitor/send` WebSocket command, not by the cover entity
- **Physical observation (user-confirmed)**: the shutter went down about halfway, stopped, then went fully up
- **Privacy**: host and port removed (`null`); only the public BTicino `00:03:50` MAC prefix is kept

| File | SHA-256 | Content | Frame Count |
|---|---|---|:---:|
| `myhome_trace_MH201_where41_reversal_2026-11-18.json` | `8a6dfcaa82e36011a47ab867f1d35c9548ede5a02642e8c1a49de4f163821c98` | `WHO = 2` / `WHERE = 41` frames of the bus monitor history: an earlier run, then the reversal reproduction | 10 (`6 rx`, `4 tx`) |

---

## 1. Reversal Reproduction (`frames[3..9]`)

| Index | Time (UTC) | Dir | Frame | Meaning |
|:---:|---|:---:|---|---|
| `3` | `21:08:56.008` | tx | `*2*2*41##` | Down |
| `4` | `21:08:56.071` | rx | `*2*2*41##` | Actuator moving down (`63 ms`) |
| `5` | `21:09:01.096` | tx | `*2*1*41##` | Up, `5.09 s` into the 15 s run |
| `6` | `21:09:01.151` | rx | `*2*0*41##` | Actuator **stopped**, did not reverse (`55 ms`) |
| `7` | `21:09:05.104` | tx | `*2*1*41##` | Up again |
| `8` | `21:09:05.171` | rx | `*2*1*41##` | Actuator moving up (`67 ms`) |
| `9` | `21:10:06.550` | rx | `*2*0*41##` | Actuator's own stop, `61.38 s` after the up start |

1. **Opposite direction = stop.** While the `L4671/2` runs, `*2*1*41##` is executed as a stop (`frames[5..6]`); a second `*2*1*41##` starts it (`frames[7..8]`).
2. **Run-time cutoff outlasts the travel.** The actuator's own stop arrives `61.38 s` after the start on a 15 s shutter (`frames[8..9]`): the actuator keeps "running" until its ~60 s cutoff. Any opposite command inside that minute (for example an open shortly after a full close) is only a stop.
3. **No stop status before the direction status on this gateway.** The MH201 relays `*2*2*41##` (`frames[3..4]`) and `*2*1*41##` (`frames[7..8]`) with no `*2*0*41##` in front. In `tests/golden/firmware_oracle.json` (`trace-corpus` suite, `*2*1*31##` / `*2*2*31##`), `bt_autom` on F453, F453AV, F454, F459, F460, F461, H4684, MH200, MH200N and MyHomeServer1 puts a STOP telegram (`$033100120A`) on the SCS bus before the UP / DOWN telegram, and an F461 capture shows the matching `*2*1000#0*05##` → `*2*0*05##` pre-move stop (MyHOME#753, `who02_automation.yaml`); MH202 `010024` sends the direction telegram only. The MH201 is catalogued in the oracle (`030644`) without verdicts, so its SCS output is not known; the monitor frames above are consistent with no pre-move stop.

`frames[0..2]` (`21:06:41`–`21:07:10`: `rx *2*1*41##`, `tx *#2*41##`, `rx *2*0*41##`) are an earlier run in the same history, kept verbatim and not part of the reproduction.

## 2. Integration Impact

Without `stop_before_move`, the `*2*0*41##` that answers HA's `*2*1*41##` 55 ms later falls inside the direction frame's echo window (`ECHO_WINDOW`, `1.5 s`), where gateways relay a momentary stop before the motor start, so the timed cover keeps its opening run and settles at `100 %` while the shutter stands still. With `stop_before_move: true` the cover sends `*2*0*41##`, waits for its write plus `STOP_BEFORE_MOVE_DELAY` (`0.6 s`), then the direction frame: reversals and an open right after a full close worked on this plant.

## Replay Verification

`tests/test_cover_stop_before_move.py` pins the SHA-256, privacy (`findings == []`), `OWNd` parsing of all 10 frames and the timings above, replays `frames[3..6]` against a timed cover without the option (it stays `opening`) and checks the stop / pause / direction order with the option across open, close, `set_cover_position` and a mid-run reversal.
