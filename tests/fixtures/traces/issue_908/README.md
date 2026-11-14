# Traces: WHO 4 Dimension 20 Value `100` (0–10 V `0%` OFF) & Extended Fan-Coil OFF States Across Gateways (Issue #908)

This directory contains **1 authentic live hardware capture** from `@xmavgithub`'s `MyHomeServer1` (`2.87.13`) plant alongside **3 synthetic cross-gateway firmware-oracle traces**:

1. **Live hardware capture (`"synthetic": false`)** — `myhome_trace_MyHomeServer1_fancoil_dim20_100_2026-11-14.json`:
   - Extracted and sanitized (`WHO = 4`, `WHERE in {0, 1, 2}`, 130 frames over ~22 minutes) from `@xmavgithub`'s `MyHomeServer1` (`2.87.13`) diagnostic capture ([`gist:78754dd5a9e1ad7159231cbbc28bda14`](https://gist.github.com/xmavgithub/78754dd5a9e1ad7159231cbbc28bda14)) attached to [OpenWebNet-HA/MyHOME#908](https://github.com/OpenWebNet-HA/MyHOME/issues/908).
   - Captures Zones 1 (`20.1 °C`, heating setpoint `19.0 °C`, humidity `60%`) and 2 (`19.5 °C`, heating setpoint `18.0 °C`, humidity `63%`) with floor-heating valves on actuator `#1` and 0–10 V Sabiana fan coils on actuator `#3`, emitting paired `#20` write + `20` status frames (`*#4*1#3*#20*100##`, `*#4*1#3*20*100##`, `*#4*2#3*#20*100##`, 2× `*#4*2#3*20*100##`, pump release `*4*4002#2*0#1##` / `*4*4002*2##`, and `*#4*2#1*#20*0##`, `*#4*2#1*20*0##`) with zero `Dimension 19` frames.

2. **Synthetic cross-gateway oracle traces (`"synthetic": true`, `"source": "firmware-oracle-contract"`)**:
   - Deterministically synthesized from direct ARM disassembly of `bt_termo` across `F454` / `F459` / `MH200N` (`0xfe60..0x1013c`, discrete table `0x142ec`) and `MyHomeServer1` (`0x11e34..0x11e8c` + `bt_thermo_manager` strings `*#4*%s*#%d*100##` / `*#4*%s*#%d*200##`), combined with `own-firmware-oracle` verdicts from suites `thermo-actuator` (`*#4*1*20##`), `thermo-valve` (`*#4*1*19##`), and `thermo-zone-requests` (`*#4*2*20##`, `*#4*2*19##`, `*#4*#0*20##`).

## Firmware Disassembly Ground Truth (`bt_termo`)

Inbound 11-byte SCS actuator status telegrams `$04B7<DST>3<ACT><VAL>\r` (e.g. `$04B7813380\r` = zone 1, actuator 3, `<VAL> = 0x80`) are translated by `bt_termo` into `*#4*Z#N*20*CODE##` via two branches:

1. **Proportional 0–10 V modulating range (`0x80 <= VAL <= 0xE4`, i.e. `0%..100%`)**:
   - `sub r3, r0, #0x1c` (`100 + pct`) maps:
     - `0x80` (`0%` output = **OFF / stopped**) → **`*#4*Z#N*20*100##`**
     - `0x81..0xE4` (`1%..100%` output = **active**) → **`*#4*Z#N*20*101##` .. `*#4*Z#N*20*200##`**
   - Corroborated independently in `MyHomeServer1` `bt_thermo_manager` write templates `*#4*%s*#%d*100##` (`0%`) and `*#4*%s*#%d*200##` (`100%`), and observed directly in `@xmavgithub`'s live `MyHomeServer1` (`2.87.13`) bus capture (`*#4*1#3*#20*100##` + `*#4*1#3*20*100##`).
2. **Discrete lookup table**:
   - `F454` / `F459` / `MH200N` (`0..9`): `0` (`'01'` OFF), `1` (`'00'` ON), `2` (`'10'` Opened), `3` (`'11'` Closed), `4` (`'12'` Stop), `5` (`'20'` Fan OFF), `6..8` (`'21'..'23'` Fan speeds 1..3), `9` (`'2F'` Fan Auto ON).
   - `MyHomeServer1` (`0..16`): adds **`14` (`'24'`), `15` (`'25'`), `16` (`'26'`)** = extended **fan-coil OFF-speed states 1, 2, 3** (`ownkb:claim:c003275`, `ownkb:claim:c003276`).

## Why Gateway Behaviour Leads to Different Outcomes

1. **`MyHomeServer1` (`2.87.13`, live capture)** — `myhome_trace_MyHomeServer1_fancoil_dim20_100_2026-11-14.json`:
   - Emits paired `#20` write + `20` status frames (`*#4*1#3*#20*100##` + `*#4*1#3*20*100##` and `*#4*2#3*#20*100##` + `*#4*2#3*20*100##` when 0–10 V fan coils report `0%`, and `*#4*2#1*#20*0##` + `*#4*2#1*20*0##` after pump release `*4*4002#2*0#1##` / `*4*4002*2##`).
   - **Pre-fix**: Zone 1 (`20.1 °C > 19.0 °C`) stayed stuck on `hvac_action = "heating"` because `20*100` set `actuators["3"] = True`; Zone 2 (`19.5 °C > 18.0 °C`) also had `actuators["3"] = True` despite `#1` turning `0` (`off`).
   - **Post-fix**: `#20*100` write frames are ignored for entity state and `20*100` resolves to `is_active = False` (`actuators["3"] = "off"`), keeping both zones `idle`.

2. **`F459` (`020105`) / `F454` (`020051`) / `F461` (`020011`) — Per-Zone `Dimension 20` Poll Forwarded (`verdict: out`)** (`myhome_trace_oracle_f459_sabiana_fancoil_dim20_100.json`, synthetic):
   - `bt_termo` forwards `*#4*1*20##` (`$04A9013010\r`, oracle `verdict: out`, `reply: nack` when no slave answers in QEMU) and `*#4*2*20##` (`$04A9023010\r`) onto the SCS bus at startup, immediately populating `_active_source = "actuators"` (`#1 = 0`, `#3 = 100`), plus 0–10 V interfaces broadcast `$04B7813380\r` → `*#4*1#3*20*100##` every ~8 minutes.
   - **Post-fix**: Both zones stay `idle` at `100` (`0%`), report `heating` when `#1 -> 1` or `#3 -> 155` (`55%`), and return to `idle` when `#1 -> 0` and `#3 -> 100`.

3. **`MH200N` (`010108`) / `H4684` (`020054`) / `F453AV` (`030014`) — Per-Zone `Dimension 20` Poll Dropped (`NACK` + `SILENT`)** (`myhome_trace_oracle_mh200n_dim20_poll_dropped_periodic_100.json`, synthetic):
   - `own-firmware-oracle` proves these gateways drop `*#4*1*20##` (`NACK`, `SILENT`), so `MyHOME` sets `actuator_request_refused = True` and relies on `*#4*1*19##` (`_active_source = "zone"`, `idle`) at startup until an unsolicited `*#4*1#3*20*100##` arrives ~8 minutes later.
   - **Post-fix**: The unsolicited `*#4*1#3*20*100##` switches `_active_source` to `"actuators"` with `{"3": False}`, keeping `hvac_action = "idle"`.

4. **`MyHomeServer1` (`028206` oracle) — Extended Fan-Coil OFF States `14..16` & `bt_thermo_manager` `#20*100` / `#20*200` Writes** (`myhome_trace_oracle_myhomeserver1_extended_fancoil_off_14_16.json`, synthetic):
   - `14..16` resolve to `is_active = False, fan_on = False, running_fan_speed = "off"`, and `#20*100` / `20*100` on probe-less zones defer cleanly to `MyHOMEThermoSwitch` as `off`.
