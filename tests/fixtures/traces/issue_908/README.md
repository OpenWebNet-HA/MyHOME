# Traces: WHO 4 Dimension 20 Value `100` (0–10 V `0%` OFF) & Extended Fan-Coil OFF States Across Gateways (Issue #908)

This directory contains **1 authentic live hardware capture** from `@xmavgithub`'s `MyHomeServer1` (`2.87.13`) plant alongside **3 synthetic cross-gateway firmware-oracle traces**:

1. **Live hardware capture (`"synthetic": false`)** — `myhome_trace_MyHomeServer1_fancoil_dim20_100_2026-11-14.json`:
   - Extracted and sanitized (`WHO = 4`, `WHERE in {0, 1, 2}`, 130 frames over ~22 minutes) from `@xmavgithub`'s `MyHomeServer1` (`2.87.13`) diagnostic capture ([`gist:78754dd5a9e1ad7159231cbbc28bda14`](https://gist.github.com/xmavgithub/78754dd5a9e1ad7159231cbbc28bda14)) attached to [OpenWebNet-HA/MyHOME#908](https://github.com/OpenWebNet-HA/MyHOME/issues/908).
   - Captures Zones 1 (`20.1 °C`, heating setpoint `19.0 °C`, humidity `60%`) and 2 (`19.5 °C`, heating setpoint `18.0 °C`, humidity `63%`) with floor-heating valves on actuator `#1` and 0–10 V Sabiana fan coils on actuator `#3`, emitting paired `#20` write + `20` status frames (`*#4*1#3*#20*100##`, `*#4*1#3*20*100##`, `*#4*2#3*#20*100##`, 2× `*#4*2#3*20*100##`, pump release `*4*4002#2*0#1##` / `*4*4002*2##`, and `*#4*2#1*#20*0##`, `*#4*2#1*20*0##`) with zero `Dimension 19` frames.

2. **Synthetic cross-gateway oracle traces (`"synthetic": true`, `"source": "firmware-oracle-contract"`)**:
   - Deterministically synthesized from verified gateway firmware contracts across `MH200N` (`010108`), `F454` (`020051`), `MH202` (`010024`), `MyHomeServer1` (`028206`, including `bt_thermo_manager` `*#4*%s*#%d*100##` / `*#4*%s*#%d*200##` templates), and `F461` (`020011`), combined with `own-firmware-oracle` verdicts from suites `who13-identification` (`*#13**16##`), `thermo-actuator` (`*#4*1*20##`), `thermo-valve` (`*#4*1*19##`), and `thermo-zone-requests` (`*#4*2*20##`, `*#4*2*19##`, `*#4*#0*20##`).

## Firmware Ground Truth (5 Inspected Gateways)

Inbound short 4-byte SCS actuator status telegrams `$17B7<DST>3<ACT><VAL>\r` (2-digit UART PIC, e.g. `$17B7813380\r` = zone 1, actuator 3, `<VAL> = 0x80`) or `$017B7<DST>3<ACT><VAL>\r` (3-digit RPMsg PIC on `F460`/`F461`) — whereas `$04...` / `$104...` / `$004...` are outbound SCS TX prefixes — are translated into **WHO=4 Dimension 20 (`*#4*Z#N*20*...##`) frames only**, according to each gateway's firmware capabilities (Dimension 19 `100..200` on `MyHomeServer1` `028206` / `F461` `020011` comes instead from extended 8-byte zone valve replies `D2 <Z> 03 04 13 <HV> <CV> 00`, i.e. `$18D2...` / `$018D2...`, for which there is no live capture yet):

1. **Proportional 0–10 V modulating range (`0x80 <= VAL <= 0xE4`, mapped as `100 + pct` for `0%..100%`)**:
   - `0x80` (`0%` output = **OFF / stopped**) → **`100`** (`*#4*Z#N*20*100##`)
   - `0x81..0xE4` (`1%..100%` output = **active**) → **`101` .. `200`** (`*#4*Z#N*20*101##` .. `*#4*Z#N*20*200##`)
   - **Gateway support matrix** (for inbound `$17B7...` / `$017B7...` → Dimension 20, vs extended 8-byte `$18D2...` / `$018D2...` → Dimension 19):
     - `MH200N` (`010108`): **unsupported** (no OpenWebNet event emitted — silent — for `0x80..0xE5` on both Dimension 19 and Dimension 20, while discrete `0x01` still reports).
     - `F459` (`020105`) and `MH202` (`010024`): supported on **Dimension 20 only** — emulator-verified via `$17B7...`, `0x80..0xE4`; silent on Dimension 19.
     - `F454` (`020051`): **Dimension 20 only** by firmware reading, **not observed on the emulator** — the emulated `020051` image emitted no OpenWebNet event on inbound `$17B7...`, including the `17B40230F9` control frame.
     - `MyHomeServer1` (`028206`) and `F461` (`020011`): supported on **Dimension 20** (via `$17B7...` / `$017B7...`, `0x80..0xE4`) and `#20`; **Dimension 19 (`CV`/`HV`)** `100..200` is translated only from extended 8-byte `D2 <Z> 03 04 13 <HV> <CV> 00` (`$18D2...` / `$018D2...`) in firmware/emulator analysis with no live capture yet.
   - Corroborated independently in `MyHomeServer1` `bt_thermo_manager` write templates `*#4*%s*#%d*100##` (`0%`) and `*#4*%s*#%d*200##` (`100%`), and observed directly in `@xmavgithub`'s live `MyHomeServer1` (`2.87.13`) bus captures (`*#4*1#3*#20*100##` + `*#4*1#3*20*100##` at rest here, and both the `100, 120..200` heating ramp and full 221-frame cycle with `F430V10` (`OWN-DEV-0145`), `F430/2` (`OWN-DEV-0147`), `H4691` (`OWN-DEV-0046`), and `F459` `2.1.4` *HVAC 0-10V FanControl* `1.6.4` in `../issue_949/`).
2. **Discrete lookup table**:
   - `MH200N` (`010108`), `F454` (`020051`), `MH202` (`010024`), `MyHomeServer1` (`028206`), and `F461` (`020011`) (`0..13`, including `9` on Dimension 19 as well as Dimension 20):
     `0` (`'01'` OFF), `1` (`'00'` ON), `2` (`'10'` Opened), `3` (`'11'` Closed), `4` (`'12'` Stop), `5` (`'20'` Fan OFF), `6..8` (`'21'..'23'` Fan speeds 1..3), `9` (`'2F'` Fan Auto ON), `10` (`'30'`), `11` (`'31'`), `12` (`'32'`), `13` (`'3F'`).
   - `MyHomeServer1` (`028206`) and `F461` (`020011`) expanded 23-entry table (`0..22` across Dimension 19, Dimension 20, and `#20`):
     adds **`14` (`'24'`), `15` (`'25'`), `16` (`'26'`)** = extended **fan-coil OFF-speed states 1, 2, 3** (`ownkb:claim:c003275`, `ownkb:claim:c003276`), plus `17` (`'40'`), `18` (`'41'`), `19` (`'42'`), `20` (`'43'`), `21` (`'44'`), `22` (`'45'`).
   - `MyHomeServer1` `bt_thermo_manager` active/inactive bitmask classifies `{0, 3, 5, 13, 14, 15, 16, 17, 100}` as inactive and `{1, 2, 4, 6..12, 18..22, 101..200}` as active (`MyHOME` keeps `4` = 3-point motorized valve `Stop` inactive so stopped valves report `HVACAction.IDLE`, and decodes `10..13` and `17..22` with `fan_on = None, fan_speed = None`; `10..13`, `14..16`, and `17..22` have not yet been captured on a live plant).
   - **Out-of-table Dimension 19 bytes**: In `own-firmware-oracle` emulator probes for out-of-table SCS telegram bytes (`0x46`, `0x7F`, `0xE5`), `MyHomeServer1` (`028206`) and `F461` (`020011`) emit Dimension 19 with an empty field (`*#4*1*19*0*##`) rather than staying silent; it remains untyped in `OWNd` (`message_type is None`) and returns `None` from `thermo_dim19_status`.

## Why Gateway Behaviour Leads to Different Outcomes

1. **`MyHomeServer1` (`2.87.13`, live capture)** — `myhome_trace_MyHomeServer1_fancoil_dim20_100_2026-11-14.json`:
   - Emits paired `#20` write + `20` status frames (`*#4*1#3*#20*100##` + `*#4*1#3*20*100##` and `*#4*2#3*#20*100##` + `*#4*2#3*20*100##` when 0–10 V fan coils report `0%`, and `*#4*2#1*#20*0##` + `*#4*2#1*20*0##` after pump release `*4*4002#2*0#1##` / `*4*4002*2##`).
   - **Pre-fix**: Zone 1 (`20.1 °C > 19.0 °C`) stayed stuck on `hvac_action = "heating"` because `20*100` set `actuators["3"] = True`; Zone 2 (`19.5 °C > 18.0 °C`) also had `actuators["3"] = True` despite `#1` turning `0` (`off`).
   - **Post-fix**: `#20*100` write frames are ignored for entity state and `20*100` resolves to `is_active = False` (`actuators["3"] = "off"`), keeping both zones `idle`.

2. **`F459` (`020105`) / `F454` (`020051`) / `F461` (`020011`) — Per-Zone `Dimension 20` Poll Forwarded (`verdict: out`)** (`myhome_trace_oracle_f459_sabiana_fancoil_dim20_100.json`, synthetic):
   - These gateways forward `*#4*1*20##` (`$04A9013010\r` on `F454`/`F459`, `$004A9013010\r` on `F461`; oracle `verdict: out`, `reply: nack` when no slave answers in QEMU) and `*#4*2*20##` (`$04A9023010\r` / `$004A9023010\r`) onto the SCS bus at startup, immediately populating `_active_source = "actuators"` (`#1 = 0`, `#3 = 100`), plus 0–10 V interfaces broadcast inbound `$17B7813380\r` (2-digit UART PIC, or `$017B7813380\r` on 3-digit RPMsg PIC `F460`/`F461`; emulator-verified on `F459`/`F461`, and firmware reading — not observed on the emulator — on `F454` `020051`) → `*#4*1#3*20*100##` every ~8 minutes. On `@xmavgithub`'s live plant, the hardware behind the `MyHomeServer1` (`2.87.13`) capture is confirmed as `F430V10` (`OWN-DEV-0145`) for `N=3`, `F430/2` (`OWN-DEV-0147`) for floor valves `N=1`, `H4691` (`OWN-DEV-0046`) standalone probes, and `F459` (`2.1.4`, *HVAC 0-10V FanControl* `1.6.4`).
   - **Post-fix**: Both zones stay `idle` at `100` (`0%`), report `heating` when `#1 -> 1` or `#3 -> 155` (`55%`), and return to `idle` when `#1 -> 0` and `#3 -> 100`.

3. **`MH202` (`010024`) / `MH200N` (`010108`) / `H4684` (`020054`) / `F453AV` (`030014`) — Per-Zone `Dimension 20` Poll Dropped (`NACK` + `SILENT`)** (`myhome_trace_oracle_mh202_dim20_poll_dropped_periodic_100.json`, synthetic):
   - `own-firmware-oracle` proves these gateways drop `*#4*1*20##` (`NACK`, `SILENT`), so `MyHOME` sets `actuator_request_refused = True` and relies on `*#4*1*19##` (`_active_source = "zone"`, `idle`) at startup until an unsolicited `*#4*1#3*20*100##` arrives ~8 minutes later (`MH202` `010024` supports `100..200` on Dimension 20, whereas `MH200N` `010108` drops `0x80..0xE4`).
   - **Post-fix**: The unsolicited `*#4*1#3*20*100##` switches `_active_source` to `"actuators"` with `{"3": False}`, keeping `hvac_action = "idle"`.

4. **`MyHomeServer1` (`028206` oracle) — Extended Fan-Coil OFF States `14..16` & `bt_thermo_manager` `#20*100` / `#20*200` Writes** (`myhome_trace_oracle_myhomeserver1_extended_fancoil_off_14_16.json`, synthetic):
   - `14..16` resolve to `is_active = False, fan_on = False, running_fan_speed = "off"`, and `#20*100` / `20*100` on probe-less zones defer cleanly to `MyHOMEThermoSwitch` as `off`.
