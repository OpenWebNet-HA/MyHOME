# Trace: WHO 4 Dimension 20 Proportional 0–10 V Fan-Coil Heating Ramp `120..200` → `100` (Issue #949)

- **Model**: `BTicino MyHomeServer1` (`2.87.13`)
- **Source**: Verbatim 9-frame live log excerpt (`"kind": "issue_log_excerpt"`, `"synthetic": false`) reported by `@xmavgithub` in [OpenWebNet-HA/MyHOME#949](https://github.com/OpenWebNet-HA/MyHOME/issues/949) (follow-up to [OpenWebNet-HA/MyHOME#908](https://github.com/OpenWebNet-HA/MyHOME/issues/908)).
- **Plant Topology (Zone 1 — Living Room)**:
  - Ambient temperature `18.9 °C`, living-room setpoint raised `19.0 °C` → `24.0 °C` at `08:29:07` local (`07:29:07+00:00`) and restored to `19.0 °C` at `08:50:12`.
  - Actuator `1#1`: floor-heating valve (`0` / `1`).
  - Actuator `1#2`: fan-coil enable relay (`0` / `1`).
  - Actuator `1#3`: Sabiana 0–10 V proportional fan coil via `F459` / `OWN-DEV-0146` interface (`100..200` → `0..100 %`, often paired with `*#4*1#3*#20*1xx##` write frames).

## Verbatim Captured Frames (`08:29:07` → `08:50:18` CET)

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
  - Keeps `actuators` strictly `"on"` / `"off"` (`{"1": "on", "2": "on", "3": "on"}` during heating, `{"1": "off", "2": "off", "3": "off"}` at rest).
  - Exposes `actuator_levels: {"3": 20}` → `23` → `50` → `80` → `100` → `0` (only proportional actuators appear in `actuator_levels`, never discrete `1#1` or `1#2`).
- `sensor.climate_zone_1_actuator_3_output_level`:
  - Auto-discovered dynamically from `*#4*1#3*20*120##` (`native_unit_of_measurement = "%"`, `state_class = "measurement"`, `suggested_display_precision = 0`, `icon = "mdi:gauge"`), attached to the exact same Home Assistant Device as `climate.climate_zone_1`, with `generate_events` disabled.
  - Ignores paired `#20` write frames (`*#4*1#3*#20*1xx##`) so state writes are never doubled, and resets cleanly to `0` on `*#4*1#3*20*100##`.
  - Discrete actuators `1#1` and `1#2` (`0` / `1`) create zero extra `sensor` entities.
