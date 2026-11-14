# Synthetic Traces: WHO 4 Dimension 20 Value `100` (0–10 V `0%` OFF) & Extended Fan-Coil OFF States Across Gateways (Issue #908)

> [!NOTE]
> **Why these traces are synthetic**:
> Every JSON file in this directory is **100% synthetic** (`"synthetic": true`, `"source": "firmware-oracle-contract"`), deterministically synthesized from:
> 1. The live plant topology and symptoms reported by `@xmavgithub` in [OpenWebNet-HA/MyHOME#908](https://github.com/OpenWebNet-HA/MyHOME/issues/908) (`F459` gateway, floor-heating valves on actuator `#1` + Sabiana 0–10 V fan coils on actuator `#3` in zones 1 & 2, ~8-minute periodic `*#4*Z#3*20*100##` broadcasts).
> 2. Direct ARM disassembly of `bt_termo` across `F454` / `F459` / `MH200N` (`0xfe60..0x1013c`, discrete table `0x142ec`) and `MyHomeServer1` (`0x11e34..0x11e8c` + `bt_thermo_manager` strings `*#4*%s*#%d*100##` / `*#4*%s*#%d*200##`).
> 3. Cross-gateway `own-firmware-oracle` verdicts from suite `thermo-zone-requests` (`*#4*1*20##`, `*#4*#0*20##`, `*#4*1*19##`).

## Firmware Disassembly Ground Truth (`bt_termo`)

Inbound 11-byte SCS actuator status telegrams `$04B7<DST>3<ACT><VAL>\r` (e.g. `$04B7813380\r` = zone 1, actuator 3, `<VAL> = 0x80`) are translated by `bt_termo` into `*#4*Z#N*20*CODE##` via two branches:

1. **Proportional 0–10 V modulating range (`0x80 <= VAL <= 0xE4`, i.e. `0%..100%`)**:
   - `sub r3, r0, #0x1c` (`100 + pct`) maps:
     - `0x80` (`0%` output = **OFF / stopped**) → **`*#4*Z#N*20*100##`**
     - `0x81..0xE4` (`1%..100%` output = **active**) → **`*#4*Z#N*20*101##` .. `*#4*Z#N*20*200##`**
   - Corroborated independently in `MyHomeServer1` `bt_thermo_manager` write templates `*#4*%s*#%d*100##` (`0%`) and `*#4*%s*#%d*200##` (`100%`).
2. **Discrete lookup table**:
   - `F454` / `F459` / `MH200N` (`0..9`): `0` (`'01'` OFF), `1` (`'00'` ON), `2` (`'10'` Opened), `3` (`'11'` Closed), `4` (`'12'` Stop), `5` (`'20'` Fan OFF), `6..8` (`'21'..'23'` Fan speeds 1..3), `9` (`'2F'` Fan Auto ON).
   - `MyHomeServer1` (`0..16`): adds **`14` (`'24'`), `15` (`'25'`), `16` (`'26'`)** = extended **fan-coil OFF-speed states 1, 2, 3** (`ownkb:claim:c003275`, `ownkb:claim:c003276`).

## Why Gateway Behaviour Leads to Different Outcomes

1. **`F459` (`020105`) / `F454` (`020051`) / `F461` (`020011`) — Per-Zone `Dimension 20` Poll Forwarded** (`myhome_trace_oracle_f459_sabiana_fancoil_dim20_100.json`):
   - `bt_termo` ACKs and forwards `*#4*1*20##` (`$04A9013010\r`) and `*#4*2*20##` at startup, immediately populating `_active_source = "actuators"` (`#1 = 0`, `#3 = 100`), plus Sabiana 0–10 V interfaces broadcast `$04B7813380\r` → `*#4*1#3*20*100##` every ~8 minutes.
   - **Pre-fix**: Zone 1 (`20.1 °C > 19.0 °C`) stayed permanently stuck on `hvac_action = "heating"`; Zone 2 (`19.3 °C > 18.0 °C`), which also emitted periodic `*#4*2*19*5*0##` clearing `_actuator_states`, flipped between `"heating"` and `"idle"` 55–75 times/day.
   - **Post-fix**: Both zones stay `idle` at `100` (`0%`), report `heating` when `#1 -> 1` or `#3 -> 155` (`55%`), and return to `idle` when `#1 -> 0` and `#3 -> 100`.

2. **`MH200N` (`010108`) / `H4684` (`020054`) / `F453AV` (`030014`) — Per-Zone `Dimension 20` Poll Dropped (`NACK` + `SILENT`)** (`myhome_trace_oracle_mh200n_dim20_poll_dropped_periodic_100.json`):
   - `own-firmware-oracle` proves these gateways drop `*#4*1*20##` (`NACK`, `SILENT`), so `MyHOME` sets `actuator_request_refused = True` and relies on `*#4*1*19##` (`_active_source = "zone"`, `idle`) at startup.
   - **Pre-fix**: The zone started `idle` at `T+0s`, then ~8 minutes later an unsolicited `$04B7813380\r` → `*#4*1#3*20*100##` switched `_active_source` from `"zone"` to `"actuators"` with `{"3": True}`, silently flipping `hvac_action` to `"heating"` even though no thermostat state changed!
   - **Post-fix**: The unsolicited `*#4*1#3*20*100##` switches `_active_source` to `"actuators"` with `{"3": False}`, keeping `hvac_action = "idle"`.

3. **`MyHomeServer1` (`028206`) — Extended Fan-Coil OFF States `14..16` & `bt_thermo_manager` `#20*100` / `#20*200` Writes** (`myhome_trace_oracle_myhomeserver1_extended_fancoil_off_14_16.json`):
   - `MyHomeServer1` emits `14, 15, 16` (`'24'..'26'`) on `Dimension 19` and `Dimension 20` when a fan coil is OFF with a remembered speed, and `bt_thermo_manager` emits `*#4*Z#N*#20*100##` / `*#4*Z#N*#20*200##` write frames.
   - **Pre-fix**: `14, 15, 16` were treated as `is_active = True, fan_on = True` (`hvac_action = "heating"`), and `*#4*7#1*20*100##` on a probe-less actuator zone bypassed the #785 deferral guard and spawned a ghost `climate.zone_7`.
   - **Post-fix**: `14..16` resolve to `is_active = False, fan_on = False, running_fan_speed = "off"`, and `#20*100` / `20*100` on probe-less zones defer cleanly to `MyHOMEThermoSwitch` as `off`.
