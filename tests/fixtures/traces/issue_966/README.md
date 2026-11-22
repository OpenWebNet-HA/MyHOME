# Trace: SCS Bus Bit-Flip Phantom Zone `65` on `MyHomeServer1` (`2026-11-19`, Issue #966)

This directory contains an **authentic live hardware capture** (`"synthetic": false`) from `@xmavgithub`'s `BTicino MyHomeServer1` (`2.87.13`) plant, reported in [OpenWebNet-HA/MyHOME Discussion #963 comment #19773009](https://github.com/orgs/OpenWebNet-HA/discussions/963#discussioncomment-19773009) and tracked in [OpenWebNet-HA/MyHOME#966](https://github.com/OpenWebNet-HA/MyHOME/issues/966).

- **Gateway Model**: BTicino `MyHomeServer1` (`2.87.13`, host/MAC redacted to `192.0.2.98` / `00:03:50:00:09:08`).
- **Capture Window**: `2026-11-19T23:05:03.308446+00:00` → `2026-11-19T23:13:50.884234+00:00` (**57 verbatim UTC frames**, `WHO = 4` and `WHO = 18`).
- **Real Plant Devices**:
  - Climate zones `1`, `2`, `4`, `5` (with actuators `1#1`..`1#3`, `2#1`..`2#3`, `4#1`, `5#1` and central pump `0#1`).
  - Energy meter `51` (`WHO = 18`, dimensions `51`, `53`, `54`).
- **Single-Bit Address Corruption Event**:
  - `23:09:20.755578+00:00`: `*#4*1*0*0190##` (Zone `1`, `0000001`, `19.0 °C`)
  - `23:09:20.768495+00:00` (`+12.9 ms`): `*#4*65*0*0190##` (Zone `65`, `1000001`, `19.0 °C`) — single-bit flip (`bit 6`) on the 2-wire SCS bus, the only Zone `65` frame observed in 40 hours.
- **Confirmed Fix Behaviour**:
  - Replaying all 57 frames against an unconfigured plant discovers real Zones `1`, `2`, `4`, and `5` while holding Zone `65` unconfirmed and creating zero phantom `climate.climate_zone_65` entity (`read_zone_temperature("65")` NACKs / returns empty ACK on `MH202` and evicts the candidate).
