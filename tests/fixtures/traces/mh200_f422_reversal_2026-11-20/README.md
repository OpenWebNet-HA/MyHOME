# MH200 (FW 2.1.0) + F422 Interface (`WHERE = 12#4#02`): Timed Cover Mid-Run Reversal Trace

Live hardware verification capture for [#973](https://github.com/OpenWebNet-HA/MyHOME/issues/973), reproducing the `MH201 + L4671/2` observation reported in [#959](https://github.com/OpenWebNet-HA/MyHOME/issues/959) on a **BTicino `MH200` + `F422`** installation.

- **Gateway Model**: BTicino `MH200` (`WHO=13` device code `4`, `MH200Profile`, `0.05 s` command queue pacing)
- **Firmware Version**: `2.1.0`
- **Actuator / Bus**: `WHO=2` timed shutter on local bus interface `02` (`WHERE = 12#4#02`, behind BTicino `F422` bus interface, `advanced: False`; `Dimension 10` probes `*#2*...*10##` across all 11 covers return immediate `NACK *#*0##`, `M=0` hardware relay cutoff `~61.0 s`)
- **Environment**: `OWNd` `2.0.1` (`myhome-gateway` capture session on TCP port `20000`)
- **Captured**: `2026-11-20` UTC
- **Privacy**: `host` and `port` removed (`null`); only the public BTicino `00:03:50` MAC prefix is retained; local entity names omitted

| File | SHA-256 | Content | Frame Count |
|---|---|---|:---:|
| `myhome_trace_MH200_where12_bus02_reversal_2026-11-20.json` | `ca76b36c6bcbc5ed70c2d59e30f3417503e9b1b357003aaa0acee018dcceaba7` | Initial status query, full homing UP (`61.027 s` relay cutoff), then DOWN -> ~10 s mid-run reversal UP (`60.963 s` relay cutoff) | `13` (`9 rx`, `4 tx`) |

---

## 1. Frame Sequence (`frames[0..12]`)

### Initial Poll & Homing UP (`frames[0..5]`)

| Index | Time (UTC) | Dir | Frame | Delta / Meaning |
|:---:|---|:---:|---|---|
| `0` | `16:43:25.035Z` | `tx` | `*#2*12#4#02##` | Initial status query |
| `1` | `16:43:25.187Z` | `rx` | `*2*0*12#4#02##` | `+152 ms` — reported idle (`STOP`) |
| `2` | `16:43:33.263Z` | `tx` | `*2*1*12#4#02##` | Command `UP` |
| `3` | `16:43:33.372Z` | `rx` | `*2*0*12#4#02##` | `+109 ms` from `[2]` — pre-move `STOP` before relay engages |
| `4` | `16:43:33.885Z` | `rx` | `*2*1*12#4#02##` | `+622 ms` from `[2]` (`+513 ms` after `[3]`) — motor start `UP` |
| `5` | `16:44:34.912Z` | `rx` | `*2*0*12#4#02##` | **`+61.027 s`** after `[4]` (`61.649 s` after `[2]`) — `M=0` hardware relay cutoff |

### Mid-Run Reversal DOWN -> ~10 s -> UP (`frames[6..12]`)

| Index | Time (UTC) | Dir | Frame | Delta / Meaning |
|:---:|---|:---:|---|---|
| `6` | `16:45:08.932Z` | `tx` | `*2*2*12#4#02##` | Command `DOWN` from top |
| `7` | `16:45:09.127Z` | `rx` | `*2*0*12#4#02##` | `+195 ms` from `[6]` — pre-move `STOP` |
| `8` | `16:45:09.627Z` | `rx` | `*2*2*12#4#02##` | `+695 ms` from `[6]` (`+500 ms` after `[7]`) — motor start `DOWN` |
| `9` | `16:45:19.694Z` | `tx` | `*2*1*12#4#02##` | **`10.067 s`** after `[8]` (`10.762 s` after `[6]`) — mid-run reversal `UP` |
| `10` | `16:45:19.803Z` | `rx` | `*2*0*12#4#02##` | `+109 ms` from `[9]` (`10.176 s` after `[8]`) — pre-move `STOP` |
| `11` | `16:45:20.303Z` | `rx` | `*2*1*12#4#02##` | `+609 ms` from `[9]` (`+500 ms` after `[10]`) — motor start `UP` |
| `12` | `16:46:21.266Z` | `rx` | `*2*0*12#4#02##` | **`+60.963 s`** after `[11]` (`61.572 s` after `[9]`) — `M=0` hardware relay cutoff |

---

## 2. Protocol & Hardware Findings (`Refs #973`, `Refs #959`, `Refs #962`)

1. **Gateway pre-move `STOP` (`bt_luci` `MH200`).** Every `UP` or `DOWN` command on this `MH200 (2.1.0) + F422` plant produces an immediate pre-move `*2*0*12#4#02##` frame `109–195 ms` after `tx`, followed `500–513 ms` later by the motion frame (`*2*1*12#4#02##` or `*2*2*12#4#02##`). Unlike bare-metal `MH201` (`mh201_l4671_reversal_2026-11-18`), a single mid-run reversal command on `MH200` transitions `DOWN -> pre-move STOP -> UP` directly within `~500 ms` without requiring a second command.
2. **No bus event when reaching the mechanical limit switch.** Although the downward travel before reversal was only `~10.1 s` (ending at roughly halfway rather than the bottom of travel), returning `UP` emitted **no** `*2*0*12#4#02##` when the shutter physically reached the top ~`10 s` later. The only stop frame emitted by the actuator is the `~61.0 s` (`61.027 s` in `[4..5]`, `60.963 s` in `[11..12]`) `M=0` relay cutoff.
3. **Why `#973` happens.** When `_settle_run_to_end` / `_arm_wall_settle` schedules the settle timeout for the **full** `travel_time + END_SETTLE_MARGIN` after `[11]`, dead-reckoning reaches `100 %` at physical arrival while Home Assistant continues reporting `opening` for the remainder of `travel_time + END_SETTLE_MARGIN`.

---

## 3. Verification Suite

`tests/test_mh200_f422_reversal_trace.py` pins the fixture SHA-256 digest, privacy checks (`findings == []`), `MH200` `2.1.0` (`who13_code=4`) metadata, `OWNd` parsing of all 13 frames, the `109–195 ms` pre-move `STOP` and `~500 ms` motor-start cadence on local bus `02`, the `10.067 s` mid-run reversal, and the `61.027 s` / `60.963 s` `M=0` hardware relay cutoffs.
