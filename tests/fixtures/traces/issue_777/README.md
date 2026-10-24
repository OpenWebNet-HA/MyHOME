# #777 BTicino F454 (`2.0.51`) + Legrand `067226` Motion Detector & BTicino `F411` Light Sharing `WHERE="52"`

Bus frames and command/event session timing captured on a physical **BTicino F454** gateway (`2.0.51`, `command_worker_count = 1`, Home Assistant `2026.10.2`, integration `2.0.0b16`, `OWNd 2.0.0b11`) where a **Legrand 067226** Céliane BUS motion/luminosity detector with push button (configured at `A=5, PL=2` with no internal relay) switches a **BTicino F411** relay actuator channel at the same address `WHERE="52"`.

Contributed by **@eC0y0te** on [#777 (comment 6364859778)](https://github.com/OpenWebNet-HA/MyHOME/issues/777#issuecomment-6364859778) and [#777 (comment 6375223714)](https://github.com/OpenWebNet-HA/MyHOME/issues/777#issuecomment-6375223714). Thank you!

| File | Content |
|---|---|
| `f454_067226_f411_shared_where52.json` | 39 bus frames from a 20-minute live session (`13:54:54`–`14:17:38`) + 6 reload timing events (`23:11:59.564`–`23:12:03.742`) verified alongside `tcpdump` |

## What it shows

### 1. Point-to-point status `*#1*52##` is answered only by the `F411`
- While empty and OFF (`13:54:54.780`): `tx *#1*52##` $\rightarrow$ single reply `rx *1*0*52##` (`+97 ms`) and `ACK` on the command session.
- While occupied and ON (`13:58:58.519`): `tx *#1*52##` $\rightarrow$ single reply `rx *1*1*52##` (`+104 ms`).
- The `067226` detector never answers `*#1*52##`, so point-to-point lighting status polling does not collide on the shared address.

### 2. Occupancy cycle: `WHAT 34` (movement) $\rightarrow$ `WHAT 39` (end of movement)
Across every captured occupancy cycle:
1. **Movement detected**: `*1*34*52##` (`WHAT 34`), followed `+40 ms` later by the detector's command translation `*1*1000#1*52##` and `+33 ms` later by the F411 state report `*1*1*52##`.
2. **End of movement (~45 s after the last `*1*34*52##`)**:
   - Cycle 1 (`13:57:56.599` $\rightarrow$ `13:58:42.547`, `45.948 s`): `*1*39*52##` $\rightarrow$ `*1*1000#0*52##` (`+71 ms`) $\rightarrow$ `*1*0*52##` (`+20 ms`).
   - Cycle 2 (`14:02:55.459` $\rightarrow$ `14:03:42.028`, `46.569 s`): `*1*39*52##` $\rightarrow$ `*1*1000#0*52##` (`+50 ms`) $\rightarrow$ `*1*0*52##` (`+32 ms`).
   - Cycle 3 (`14:16:52.782` $\rightarrow$ `14:17:38.588`, `45.806 s`): `*1*39*52##` $\rightarrow$ `*1*1000#0*52##` (`+68 ms`) $\rightarrow$ `*1*0*52##` (`+14 ms`).
3. **Front-plate push button**: Pressing the physical push button on the `067226` emits `*1*1000#<0|1>*52##` followed by the F411's `*1*<0|1>*52##`, with no `WHAT 34` frame in between.

### 3. Anonymous `WHERE=0` dimension replies precede the command-session `*#*0##` NACK
When Home Assistant queries `DIMENSION 5` (`*#1*52*5##`, PIR sensitivity) and `DIMENSION 7` (`*#1*52*7##`, motion timeout) on reload (`command_worker_count = 1`), the `067226` answers on the SCS bus with address byte `00` (`D5 00 21 44 {15,16} …`, confirmed by [OpenWebNet-HA/own-firmware-oracle#56](https://github.com/OpenWebNet-HA/own-firmware-oracle/pull/56)):

| Timestamp | Session | Event | Frame | Delta from Request |
|---|---|---|---|---|
| `23:11:59.564` | Command (worker 0) | Request unqueued / sent | `*#1*52*5##` | `0.000 s` |
| `23:11:59.702` | Event (`*99*1##`) | Anonymous reply received | `*#1*0*5*2##` (`Sensitivity: high`) | **`+0.138 s`** (`138 ms`) |
| `23:12:01.673` | Command (`*99*0##`) | Gateway timeout NACK (`tcpdump`) | `*#*0##` | **`+2.109 s`** |
| `23:12:01.724` | Command (worker 0) | Request unqueued / sent | `*#1*52*7##` | `0.000 s` |
| `23:12:01.863` | Event (`*99*1##`) | Anonymous reply received | `*#1*0*7*0*0*45##` (`45 s`) | **`+0.139 s`** (`139 ms`) |
| `23:12:03.742` | Command (`*99*0##`) | Gateway timeout NACK (`tcpdump`) | `*#*0##` | **`+2.018 s`** |

- By contrast, querying `*#1*53*7##` (another channel on the same `F411` module with no `067226` attached) produces no `WHERE=0` reply on the event session.
- Because the `WHERE=0` reply arrives on the event connection at `~140 ms` while `_command_session.send(...)` is still awaiting the `~2.0 s` command-session NACK, correlating against the worker pool's in-flight request deterministically attributes `*#1*0*5*2##` and `*#1*0*7*0*0*45##` to sensor `52`.
