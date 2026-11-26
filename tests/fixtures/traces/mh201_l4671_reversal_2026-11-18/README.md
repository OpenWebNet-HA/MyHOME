# MH201 (FW 3.3.6) + BTicino L4671/2: Opposite Direction Stops the Actuator Instead of Reversing It

Contributed by **@ermanno00** (Ermanno Nicoletti) in [#959](https://github.com/OpenWebNet-HA/MyHOME/issues/959) / [#960](https://github.com/OpenWebNet-HA/MyHOME/pull/960) (continuing the hardware report in [#72](https://github.com/OpenWebNet-HA/MyHOME/issues/72)).

- **Gateway Model**: BTicino `MH201` (`WHO=13` `Device Type = 200`, `WHO=1013` `OBJECT_MODEL = 10` with `N_CONF = 15`, `BRAND = 0`, `LINE = 5`, `MH201Profile`, `0.1 s` command queue pacing, 1 send worker)
- **Firmware Version**: `3.3.6`
- **Actuator**: BTicino `L4671/2` flush-mounted 2-relay interlocked shutter actuator (`OWN-DEV-0098`, factory `M=0` `STOP_TIME = 60 s`, `LOCAL_BUTTON = 12` bistable control) on `WHERE = 41`, configured `strategy: timed`, `travel_time: 15` (the plant has 35 such covers)
- **Environment**: MyHOME `2.0.0`, OWNd `2.0.1`
- **Captured**: 2026-11-18 (UTC) from `myhome/bus_monitor/history` (`who = "2"`, `where = "41"`); the `tx` frames were sent raw with the `myhome/bus_monitor/send` WebSocket command rather than by the cover entity
- **Physical observation (user-confirmed)**: the shutter went down about halfway, stopped on the first `*2*1*41##`, then went fully up on the second `*2*1*41##`
- **Privacy**: `host` and `port` removed (`null`); only the public BTicino `00:03:50` MAC prefix is kept

| File | SHA-256 | Content | Frame Count |
|---|---|---|:---:|
| `myhome_trace_MH201_where41_reversal_2026-11-18.json` | `8a6dfcaa82e36011a47ab867f1d35c9548ede5a02642e8c1a49de4f163821c98` | `WHO = 2` / `WHERE = 41` frames of the bus monitor history: an earlier run, then the reversal reproduction | 10 (`6 rx`, `4 tx`) |

---

## 1. Frame Sequence (`frames[0..9]`)

### Earlier Run in Ring Buffer (`frames[0..2]`)

| Index | Time (UTC) | Dir | Frame | Meaning |
|:---:|---|:---:|---|---|
| `0` | `21:06:41.764` | rx | `*2*1*41##` | Actuator moving up (prior run) |
| `1` | `21:06:52.879` | tx | `*#2*41##` | Point status query `11.12 s` into the run (`0` `WHO=2` point frames returned) |
| `2` | `21:07:10.374` | rx | `*2*0*41##` | Actuator stopped `28.61 s` after `[0]` |

### Reversal Reproduction (`frames[3..9]`)

| Index | Time (UTC) | Dir | Frame | Meaning |
|:---:|---|:---:|---|---|
| `3` | `21:08:56.009` | tx | `*2*2*41##` | Down (after `105.63 s` idle) |
| `4` | `21:08:56.072` | rx | `*2*2*41##` | Actuator moving down (`63.02 ms` RTT; no pre-move `*2*0*41##`) |
| `5` | `21:09:01.096` | tx | `*2*1*41##` | Up, `5.09 s` after `[3]` (`5.02 s` into the 15 s close) |
| `6` | `21:09:01.151` | rx | `*2*0*41##` | Actuator **stopped**, did not reverse (`55.26 ms` RTT; no `*2*1*41##` follows) |
| `7` | `21:09:05.104` | tx | `*2*1*41##` | Up again (`3.95 s` after `[6]`) |
| `8` | `21:09:05.171` | rx | `*2*1*41##` | Actuator moving up (`67.21 ms` RTT) |
| `9` | `21:10:06.550` | rx | `*2*0*41##` | Actuator's internal relay cutoff stop, `61.38 s` after `[8]` (physical travel is `15 s`) |

---

## 2. Protocol & Hardware Findings (`Refs #959`)

1. **Opposite direction while energized = STOP (`OWN-DEV-0098`).** Standard interlocked 2-relay shutter actuators (`L4671/2`, `F411/2`, `F411/4`, `F411U2`) protect the AC motor windings and phase-shift capacitor from instantaneous direction reversal. While a relay coil is energized, receiving the opposite direction on SCS without a preceding `STOP` (`12 0A`) releases the active relay and puts the actuator into `STOP` (`frames[5..6]`, emitting `*2*0*41##` in `55.26 ms` with no `*2*1*41##`). Once de-energized, a second `*2*1*41##` starts the new direction (`frames[7..8]`, `67.21 ms`).
2. **~61.38 s internal relay hold outlasts physical travel.** Although physical curtain travel is `15 s`, the `L4671/2` keeps its direction relay energized until its internal `M=0` (~60 s) run-time cutoff expires (`frames[8..9]`: `61.38 s` after `rx *2*1*41##`, within `const.py`'s `60.05–61.5 s` `CALIBRATION_CUTOFFS` window). Any opposite-direction command sent during the ~46 s window after the curtain reaches its mechanical limit switch (while the relay is still energized) is likewise answered with `STOP` (`*2*0*41##`).
3. **Gateway pre-move `STOP` divergence (`bt_luci` / `coso` vs `MH202` & bare-metal `MH201`).** In `tests/golden/firmware_oracle.json` (`trace-corpus` suite, `*2*1*31##` / `*2*2*31##`), 11 of 12 emulated Linux automation gateways (`bt_luci` on `F453`, `F453AV`, `H4684`, `MH200`, `MH200N`; `bt_luci` + `coso` `libplug_automation_tr.so` on `F454`, `F459`, `F460`, `F461`, `MyHomeServer1`, `3488`) synthesize an SCS `STOP` telegram (`$033100120A\r`) ahead of `UP` / `DOWN` (`$0331001208\r` / `$0331001209\r`), with `coso` gateways also broadcasting `*2*1000#0*W##` -> `*2*0*W##` before `*2*1000#1|2*W##` (captured live on `F461` in [#753](https://github.com/OpenWebNet-HA/MyHOME/pull/753), `who02_automation.yaml`). By contrast, **`MH202` (`010024`, `coso` without `bt_luci`)** emits only the single direction SCS telegram (`$0331001208\r` / `$0331001209\r`) with no pre-move `STOP`, and **bare-metal `MH201`** (STM32F217 Cortex-M3 + CMX-RTX `OPEN_SERVICE`, catalogued as `030644` with `0` oracle verdicts) likewise relays `rx *2*2*41##` (`frames[3..4]`) and `rx *2*1*41##` (`frames[7..8]`) with no preceding `*2*0*41##`.
4. **Why `ECHO_WINDOW` (`1.5 s`) matters (`Refs #959`).** Because pre-`STOP` gateways emit a momentary `*2*0*WHERE##` ~50–150 ms before the motor-start `*2*1` / `*2*2` frame, `cover_motion.ECHO_WINDOW` (`1.5 s`) ignores `*2*0*WHERE##` inside an outgoing direction command's echo window while waiting for the motor-start status. When a gateway omits pre-move `STOP` (`MH201`, `MH202`) and an interlocked actuator answers an opposite-direction command with a terminal `*2*0*41##` at `+55.26 ms` (`frames[5..6]`) and **no** subsequent `*2*1*41##` arrives before `ECHO_WINDOW` expires, treating that lone `*2*0*41##` identically to a transient pre-move stop leaves a timed entity assuming motion started (`Refs #959`).

---

## 3. Verification Suite

`tests/test_mh201_l4671_reversal_trace.py` pins the SHA-256 digest, privacy checks (`findings == []`), `MH201` `3.3.6` (`WHO=13` / `WHO=1013` `10.15.0.5`) and `L4671/2` metadata, `OWNd` parsing of all 10 frames, the `+55.26 ms` terminal stop reply on mid-close reversal, the second `*2*1*41##` UP start, the `+61.38 s` internal relay cutoff, and the `firmware_oracle.json` gateway pre-move `STOP` split.
