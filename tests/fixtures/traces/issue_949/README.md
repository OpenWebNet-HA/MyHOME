# Traces: WHO 4 Dimension 20 Proportional 0–10 V Fan-Coil Heating Ramps `120..200` → `100` (Issues #949 & #952)

This directory contains **2 authentic live hardware captures** (`"synthetic": false`) from `@xmavgithub`'s `BTicino MyHomeServer1` (`2.87.13`) + `F459` (`2.1.4`, *HVAC 0-10V FanControl* `1.6.4` + *Air Quality* `2.4.4`) plant (follow-up to [OpenWebNet-HA/MyHOME#908](https://github.com/OpenWebNet-HA/MyHOME/issues/908)):

- **Confirmed Plant Topology**:
  - Gateway: `BTicino MyHomeServer1` (`2.87.13`).
  - Driver Manager: `F459` (`2.1.4`) running *HVAC 0-10V FanControl* (`1.6.4`: 120 s ramp, 20 % minimum speed, 30 s valve→fan delay; heating hysteresis 0.5 °C, integrated deviation 1.0 °C, PID range 2.0 °C; cooling 0.3 / 0.5 / 1.0 °C).
  - Thermostat zones 1 & 2 (Sabiana 0–10 V fan coils integrated with radiant floor): `N=1` floor valve (`F430/2`, `OWN-DEV-0147`), `N=2` fan-coil enable relay (`0` / `1`), `N=3` proportional 0–10 V speed (`F430V10`, `OWN-DEV-0145`, `100..200` → `0..100 %`). Zones 4 & 5 are floor-only; standalone `H4691` (`OWN-DEV-0046`) probes.
  - While active, `MyHomeServer1` emits paired `#20` writes (`*#4*Z#N*#20*V##`) followed ~10–300 ms later by `*#4*Z#N*20*V##` status frames (often repeated once more), re-transmitted every ~4–5 s even when unchanged.

---

## 1. Full 221-Frame Live UTC Cycle (`myhome_trace_MyHomeServer1_fancoil_dim20_full_cycle_2026-11-18.json`)

- **Source**: Verbatim 221-frame bus-listener capture (`"kind": "bus_listener_log"`, `2026-11-18` `16:26:51.293` → `16:30:14.928` UTC) reported by `@xmavgithub` in [OpenWebNet-HA/MyHOME#952 comment #6984792289](https://github.com/OpenWebNet-HA/MyHOME/pull/952#issuecomment-6984792289) while testing PR #952 (`04472f26`, Home Assistant `2026.11.2`, `OWNd` `2.0.1`). Gateway IP/MAC were omitted and use sanitized placeholders (`00:03:50:00:09:08`, `"mac_redacted": true`).
- **Frame Breakdown**:
  - `16:26:51.293`: Zone 1 setpoint `19 → 23 °C` (`*#4*1*12*0230*3##`, `*4*1*1##`, ambient `19.6 °C`).
  - `16:26:54`–`16:26:56`: `1#1` and `1#2` turn ON (`1`); central pump demand (`*4*4001#1*0#1##`, `*#4*0#1*20*1##`).
  - `16:27:27.438` (36 s after call, matching the 30 s valve→fan delay): first `*#4*1#3*20*120##` (`20 %`), ramping across all 25 steps (`120, 123, 126, 130 … 196, 200` → `20 % … 100 %` at `16:29:39.464`), comprising **31 `#20` write frames** and **63 `20` status frames** on `1#3` interleaved with `H4691` humidity (`*#4*1*60*61##`, `*#4*2*60*63##`), Zone 2 (`19.3 °C`, setpoint `18.0 °C`), and floor-only Zones 4 (`20.0 °C`) & 5 (`19.6 °C`).
  - `16:30:10`–`16:30:14`: setpoint back to `19 °C` → `1#3` `100` (`0 %`), `1#2` `0`, pump release (`*4*4002#1*0#1##`, `*#4*0#1*20*0##`, `*4*4002*1##`), `1#1` `0`.
- **Test (`test_issue_952_full_221_frame_live_cycle_unseeded`)**: Starts from a completely blank `MockConfigEntry` (zero pre-registered entities, zero seeded frames), verifying cold-start discovery of Zones 1, 2, 4, and 5, zero sensors for `1#1`, `1#2`, `0#1`, `2`, `4`, or `5`, exact 25-step ramp publication (`20 → 23 → … → 100 → 0`) with zero state writes on `#20` writes or repeated `20` frames, and clean shutdown to `idle`.

---

## 2. Verbatim 9-Frame Excerpt (`myhome_trace_MyHomeServer1_fancoil_dim20_ramp_2026-11-18.json`)

- **Source**: Verbatim 9-frame live log excerpt (`"kind": "issue_log_excerpt"`, `"synthetic": false`) reported by `@xmavgithub` in [OpenWebNet-HA/MyHOME#949](https://github.com/OpenWebNet-HA/MyHOME/issues/949). Calendar date, UTC offset, Home Assistant / `ownd` versions, and gateway MAC address were not reported in #949 (`gateway.mac` is sanitized to `00:03:50:00:09:08` matching the #908 fixture of the same plant, with `"mac_redacted": true`).
- **Plant Topology (Zone 1 — Living Room)**:
  - Ambient temperature `18.9 °C`, living-room setpoint raised `19.0 °C` → `24.0 °C` at `08:29:07` (wall-clock time) and restored to `19.0 °C` at `08:50:12`.
  - Actuators `1#1` (floor valve), `1#2` (fan-coil enable), `1#3` (0–10 V proportional fan coil).

### Verbatim Captured Frames (`08:29:07` → `08:50:18` local wall-clock time)

1. `08:29:07`: `*#4*1#2*20*1##` — fan-coil enable `on`
2. `08:29:17`: `*#4*1#3*20*120##` — `20 %`
3. `08:29:44`: `*#4*1#3*20*123##` — `23 %` (`+3..4 %` every ~5 s)
4. `08:30:21`: `*#4*1#3*20*150##` — `50 %`
5. `08:31:11`: `*#4*1#3*20*180##` — `80 %`
6. `08:31:41`: `*#4*1#3*20*200##` — `100 %` (held until setpoint restore)
7. `08:50:14`: `*#4*1#3*20*100##` — `0 %`, immediate (no ramp-down)
8. `08:50:17`: `*#4*1#2*20*0##` — fan-coil enable `off`
9. `08:50:18`: `*#4*1#1*20*0##` — floor valve `off`

### Verified Integration Behaviour

- `climate.climate_zone_1`:
  - **Unseeded 9-frame capture replay**: Replaying only the 9 verbatim frames in `myhome_trace_MyHomeServer1_fancoil_dim20_ramp_2026-11-18.json` yields `actuators: {"2": "on", "3": "on"}` during heating (because `1#1`'s opening frame precedes the excerpt) and `actuators: {"1": "off", "2": "off", "3": "off"}` at rest after `*#4*1#1*20*0##`.
  - **Seeded ramp + paired-write replay (`test_issue_949_ramp_with_seeded_zone_context_and_paired_writes`)**: Seeds the pre-excerpt and setpoint-restore zone context (`*#4*1*0*0189##`, `*#4*1*12*0240*1##`, `*4*1*1##`, `*#4*1#1*20*1##`, `*#4*1*12*0190*1##`), six paired `*#4*1#3*#20*1xx##` write frames, and a synthetic duplicate `*#4*1#3*20*200##` status frame, verifying `actuators: {"1": "on", "2": "on", "3": "on"}` during heating and `{"1": "off", "2": "off", "3": "off"}` at rest.
  - Exposes `actuator_levels: {"3": 20}` → `23` → `50` → `80` → `100` → `0`, excluded from Home Assistant's recorder (`_unrecorded_attributes`) so ramp steps record history only on the `sensor` entity.
- `sensor.climate_zone_1_actuator_3_output_level`:
  - Auto-discovered dynamically from `*#4*1#3*20*120##` (`native_unit_of_measurement = "%"`, `state_class = "measurement"`, `suggested_display_precision = 0`, `icon = "mdi:gauge"`), attached to the exact same Home Assistant Device as `climate.climate_zone_1`, with `generate_events` disabled.
  - Inherits `SensorEntity` (not `RestoreSensor`), so it does **not** restore stale values across restarts (`unknown` until the first bus frame arrives), and unconditionally registers `watch_zone_actuators` when the interface has not refused zone/actuator polls (`not interface_refused`).
  - Ignores paired `#20` write frames (`*#4*1#3*#20*1xx##`) so state writes are never doubled, and resets cleanly to `0` on `*#4*1#3*20*100##`.
  - Discrete actuators `1#1` and `1#2` (`0` / `1`) create zero extra `sensor` entities.
