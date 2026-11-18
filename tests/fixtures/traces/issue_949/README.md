# Trace: WHO 4 Dimension 20 Proportional 0–10 V Fan-Coil Heating Ramp `120..200` → `100` (Issue #949)

- **Model**: `BTicino MyHomeServer1` (`2.87.13`)
- **Source**: Verbatim 9-frame live log excerpt (`"kind": "issue_log_excerpt"`, `"synthetic": false`) reported by `@xmavgithub` in [OpenWebNet-HA/MyHOME#949](https://github.com/OpenWebNet-HA/MyHOME/issues/949) (follow-up to [OpenWebNet-HA/MyHOME#908](https://github.com/OpenWebNet-HA/MyHOME/issues/908)). Calendar date, UTC offset, Home Assistant / `ownd` versions, and gateway MAC address were not reported in #949 (`gateway.mac` is sanitized to `00:03:50:00:09:08` matching the #908 fixture of the same plant, with `"mac_redacted": true`).
- **Plant Topology (Zone 1 — Living Room)**:
  - Ambient temperature `18.9 °C`, living-room setpoint raised `19.0 °C` → `24.0 °C` at `08:29:07` (wall-clock time) and restored to `19.0 °C` at `08:50:12`.
  - Actuator `1#1`: floor-heating valve (`0` / `1`).
  - Actuator `1#2`: fan-coil enable relay (`0` / `1`).
  - Actuator `1#3`: Sabiana 0–10 V proportional fan coil (behind an `F459` 0–10 V interface per [#908](https://github.com/OpenWebNet-HA/MyHOME/issues/908); `100..200` → `0..100 %`, often paired with `*#4*1#3*#20*1xx##` write frames).

## Verbatim Captured Frames (`08:29:07` → `08:50:18` local wall-clock time)

1. `08:29:07`: `*#4*1#2*20*1##` — fan-coil enable `on`
2. `08:29:17`: `*#4*1#3*20*120##` — `20 %`
3. `08:29:44`: `*#4*1#3*20*123##` — `23 %` (`+3..4 %` every ~5 s)
4. `08:30:21`: `*#4*1#3*20*150##` — `50 %`
5. `08:31:11`: `*#4*1#3*20*180##` — `80 %`
6. `08:31:41`: `*#4*1#3*20*200##` — `100 %` (held until setpoint restore)
7. `08:50:14`: `*#4*1#3*20*100##` — `0 %`, immediate (no ramp-down)
8. `08:50:17`: `*#4*1#2*20*0##` — fan-coil enable `off`
9. `08:50:18`: `*#4*1#1*20*0##` — floor valve `off`

## Verified Integration Behaviour

- `climate.climate_zone_1`:
  - **Unseeded 9-frame capture replay**: Replaying only the 9 verbatim frames in `myhome_trace_MyHomeServer1_fancoil_dim20_ramp_2026-11-18.json` yields `actuators: {"2": "on", "3": "on"}` during heating (because `1#1`'s opening frame precedes the excerpt) and `actuators: {"1": "off", "2": "off", "3": "off"}` at rest after `*#4*1#1*20*0##`.
  - **Seeded ramp + paired-write replay (`test_issue_949_ramp_with_seeded_zone_context_and_paired_writes`)**: Seeds the pre-excerpt and setpoint-restore zone context (`*#4*1*0*0189##` ambient `18.9 °C`, `*#4*1*12*0240*1##` setpoint `24.0 °C`, `*4*1*1##` heating mode, `*#4*1#1*20*1##` floor valve `on`, and `*#4*1*12*0190*1##` setpoint restore `19.0 °C`), six paired `*#4*1#3*#20*1xx##` write frames (`*#4*1#3*#20*120##`, `*#4*1#3*#20*123##`, `*#4*1#3*#20*150##`, `*#4*1#3*#20*180##`, `*#4*1#3*#20*200##`, and `*#4*1#3*#20*100##`), plus a synthetic duplicate `*#4*1#3*20*200##` status frame for deduplication verification. With the full zone context seeded, `climate.climate_zone_1` exposes `actuators: {"1": "on", "2": "on", "3": "on"}` during heating and `{"1": "off", "2": "off", "3": "off"}` at rest.
  - Exposes `actuator_levels: {"3": 20}` → `23` → `50` → `80` → `100` → `0` (only explicit `Z#N` proportional actuators appear in `actuator_levels`, never discrete `1#1` or `1#2`), and `actuator_levels` is excluded from Home Assistant's recorder (`_unrecorded_attributes`) so ramp steps record history only on the `sensor` entity.
- `sensor.climate_zone_1_actuator_3_output_level`:
  - Auto-discovered dynamically from `*#4*1#3*20*120##` (`native_unit_of_measurement = "%"`, `state_class = "measurement"`, `suggested_display_precision = 0`, `icon = "mdi:gauge"`), attached to the exact same Home Assistant Device as `climate.climate_zone_1`, with `generate_events` disabled.
  - Inherits `SensorEntity` (not `RestoreSensor`), so it does **not** restore stale values across restarts (`unknown` until the first bus frame arrives), and unconditionally registers `watch_zone_actuators` when the interface has not refused zone/actuator polls (`not interface_refused`).
  - Ignores paired `#20` write frames (`*#4*1#3*#20*1xx##`) so state writes are never doubled, and resets cleanly to `0` on `*#4*1#3*20*100##`.
  - Discrete actuators `1#1` and `1#2` (`0` / `1`) create zero extra `sensor` entities.
