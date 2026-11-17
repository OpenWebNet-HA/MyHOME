# Discussion #770 BTicino F453 (FW 2.0.10) + N4692 Knob Sweep & 3550 Central Unit Trace

- **Gateway Model**: BTicino `F453` (`WHO=13` `Device Type = 16`, `WHO=1013` `OBJECT_MODEL = 16`, `BRAND = 1` BTicino, `F453Profile` `0.15 s` command queue pacing)
- **Firmware Version**: `2.0.10` (Linux kernel `2.3.8`, ETLinux File System `3.0.1`)
- **Thermoregulation & Contact Hardware**:
  - **Central Unit**: BTicino `3550` 99-zone central unit (`WHERE = #0`, manual heating at `8.0 °C` `*4*110#0080*#0##`, circulation pumps `0#1` and `0#2`)
  - **Zone Probes**: BTicino `N4692` flush-mounted thermostat probes with local ±3 °C / `OFF` / `*` antifreeze knob across 61 active `WHO=4` zones (`1`, `21`, `23`, `25–28`, `32–38`, `41–48`, `51–58`, `61–64`, `66`, `71–78`, `80–88`, `91–99`), interactive knob sweep on **Zone `1`**
  - **Dry-Contact Interfaces**: BTicino `3480` CEN/automation contact interface (`WHO=25`, `WHERE = 329`)
- **Contributed by**: **@DinoMesina** in [Discussion #770 (comment 19616515)](https://github.com/orgs/OpenWebNet-HA/discussions/770#discussioncomment-19616515)
- **Environment**: Home Assistant 2026.11.1, MyHOME 2.0.0b17, OWNd 2.0.0b13

| File | SHA-256 | Content | Frame Count |
|---|---|---|:---:|
| `myhome_sweep_F453_all_2026-11-14T13-34-42.json` | `c6baad54a8b09d802855e523c28297a6726838d0461dd66efe3bfe8ce24ee827` | Unfiltered `myhome.sweep_bus` export covering point-to-point `Dimension 0`/`13` queries, broadcast `Dimension 13` non-response, full `N4692` knob/mode sweep on Zone `1`, `3550` `#0` flag updates, and `WHO=25` `3480` contact events | 200 (`196 rx`, `4 tx`; `196 WHO=4`, `4 WHO=25`) |

---

## 1. Point-to-Point vs Broadcast/Central `Dimension 13` Queries (`frames[130..135]`)

Manual `myhome.send_message` queries on the live `F453` (`2.0.10`) confirm both point-to-point forwarding and why general/central `Dimension 13` forms are never used by `MyHOME` (in `WHO_4` rev. `2.0.0` of `27/11/2013`, section `1.5.4` pp. `16–17` gives `where = [1-99]` for `Dimension 13` requests and answers, listing `WHERE = 0` only in the general `WHERE` table; on `@gdluck`'s test plant, a `BTicino F461` FW `2.0.11` also answers `*#4*0*13##` with `NACK` after `5.4 ms` and emits no bus frame in the next `12 s`):

| Index | TX Query | RX Reply | Latency | Hardware Verdict |
|:---:|---|---|:---:|---|
| `130–131` | `*#4*1*0##` | `*#4*1*0*0130##` | `94.8 ms` | Zone `1` `N4692` ambient temperature = `13.0 °C` |
| `132–133` | `*#4*1*13##` | `*#4*1*13*00##` | `87.1 ms` | Zone `1` `N4692` local knob at centre (`0`) |
| `134` | `*#4*0*13##` | *(no reply over `9.75 s`)* | — | General `WHERE=0` `Dimension 13` query produces zero zone replies (`F461` `2.0.11` answers `NACK` in `5.4 ms`) |
| `135` | `*#4*#0*13##` | *(no reply)* | — | Central `WHERE=#0` `Dimension 13` query produces zero replies |

---

## 2. `BTicino N4692` Local Knob Protocol Behaviour (`frames[136..197]`)

Under a `BTicino 3550` central unit (`*4*110#0080*#0##`, nominal `8.0 °C`), the `N4692` probe exhibits two distinct frame patterns when its physical dial is turned:

1. **Mode-Boundary Transitions (`OFF` / `*` Antifreeze ↔ Active Heating)**:
   - Entering antifreeze (`*`, `frames[137..142]`, `149..154`, `175..180`): emits `*4*102*1##` → `*#4*1*13*5##` → `*#4*1*12*0070*3##` (`7.0 °C` protection), followed by `#0` reporting `*4*110#0080*#0##`, `*4*21*#0##`, `*4*23*#0##` (`at_least_one_probe_antifreeze = True`).
   - Entering local `OFF` (`frames[143..148]`, `182..187`): emits `*4*303*1##` → `*#4*1*13*4##` → `*#4*1*12*0080*3##`, followed by `#0` reporting `*4*110#0080*#0##`, `*4*21*#0##`, `*4*22*#0##` (`at_least_one_probe_off = True`).
   - Returning from `OFF`/`*` to a numeric detent (`frames[157..161]` at `-3`, `frames[190..194]` at `-2`): emits `*4*1*1##` → `*#4*1*13*13##` / `*#4*1*13*12##` → **`*#4*1*12*0050*3##` (`5.0 °C`) / `*#4*1*12*0060*3##` (`6.0 °C`)** (`50.5 ms` after `Dimension 13`), followed by `#0` clearing `WHAT 22`/`23`.

2. **Standalone Live Numeric Knob Detents (NO `Dimension 12` or `Dimension 14` Frame)**:
   - While already running in heating mode, turning the `N4692` dial across numeric detents (`frames[162..174]`: `-2 → -1 → 0 → -1 → +1 → +2 → +3 → +1 → 0 → -1 → -3`, and `frames[195..196]`: `-1 → 0` starting just **`529.4 ms`** after `frames[192]` `*#4*1*12*0060*3##`) emits **only** `*#4*1*13*OO##`. Neither the `N4692` nor the `3550` emits a follow-up `*#4*1*12*TTTT*3##` or `*#4*1*14*TTTT*3##` frame.
   - Prior to the Discussion #770 fix, `_local_target_reported = True` from the mode-entry `Dimension 12` frame froze `target_temperature` at `5.0 °C` / `6.0 °C` across all subsequent numeric knob movements. Pairing `Dimension 12` with its adjacent burst `Dimension 13` while applying `(new_offset - prev_offset)` on standalone numeric `Dimension 13` transitions tracks the live `N4692` setpoint (`5.0..11.0 °C`, ending at `8.0 °C` at centre `0`) without double-counting `F461` `*#4*Z##` status bursts.

---

## 3. `WHO=25` `BTicino 3480` Dry-Contact Events (`frames[188..189]`, `frames[198..199]`)

First `WHO=25` capture on a physical `BTicino F453` gateway in the `MyHOME` trace corpus: `WHERE = 329` transitions twice between closed/short-press (`*25*31#1*329##`) and open/released (`*25*32#1*329##`).
