# Traces: WHO 18 Energy Totalizer Startup & Polling Behaviour (Issue #907)

This directory contains **1 authentic live hardware capture** from `@xmavgithub`'s `MyHomeServer1` (`2.87.13`) plant alongside **2 synthetic cross-gateway firmware-oracle traces**:

1. **Live hardware capture (`"synthetic": false`)** — `myhome_trace_MyHomeServer1_who18_meter51_2026-11-14.json`:
   - Extracted and sanitized (`WHO = 18`, `WHERE = 51`, 96 frames over ~24 minutes) from `@xmavgithub`'s `MyHomeServer1` (`2.87.13`) diagnostic capture ([`gist:78754dd5a9e1ad7159231cbbc28bda14`](https://gist.github.com/xmavgithub/78754dd5a9e1ad7159231cbbc28bda14)) attached to [OpenWebNet-HA/MyHOME#907](https://github.com/OpenWebNet-HA/MyHOME/issues/907).
   - Shows `MyHomeServer1` (`2.87.13`) polling `*#18*51*113##`, `*#18*51*54##`, `*#18*51*53##`, and `*#18*51*51##` every 5 minutes (plus `*#18*51*51##` and `*#18*51*1200##` during `myhome.sweep_bus`), with integer `Wh`/`W` meter replies `*#18*51*51*8151646##` (`8151.646 kWh`), `*#18*51*53*88973##`, `*#18*51*54*3017##`, and active-power streaming `*#18*51*113*106##`..`*#18*51*113*144##`.

2. **Synthetic cross-gateway oracle traces (`"synthetic": true`, `"source": "firmware-oracle-contract"`)**:
   - Deterministically constructed from the recorder timeline reported in [OpenWebNet-HA/MyHOME#907](https://github.com/OpenWebNet-HA/MyHOME/issues/907) (`8133608 Wh` = `8133.608 kWh`, ~14–17 s startup window before the first `WHO = 18` `DIMENSION 51` frame) and empirical gateway verdicts from `own-firmware-oracle` suites `energy-params` and `energy-ts10` (`results/<gateway>/<fw>/oracle/full/energy-ts10.tsv`).

## Why Gateway Behaviour Leads to Different Outcomes

When `MyHOMEEnergySensor` (`native_unit_of_measurement = "Wh"`, `state_class = total_increasing`) is added to Home Assistant with user display unit `kWh`, `MyHOMEEntity.async_added_to_hass()` restores the previous state at `T+0s` and defers `async_update()` (`*#18*WHERE*51##`) behind `_async_poll_after_discovery()` while the gateway startup discovery sweep runs:

1. **Forwarding gateways (`MyHomeServer1` `2.87.13` / `028206`, `F454` `020051`, `F459` `020105`, `F461` `020011`, `MH200N` `010108`, `MH202` `010024`, `F453` `010100`, `F453AV` `030014`, `3488` `030205`)** — live `myhome_trace_MyHomeServer1_who18_meter51_2026-11-14.json` and synthetic `myhome_trace_oracle_f454_who18_totalizer_delayed_poll.json`:
   - `bt_supervisione` ACKs `*#18*51*51##` (`*#*1##`) and emits SCS `$06D1A1023350150000\r` once initial discovery completes (~14–17 s after startup).
   - The `F520` / `F521` meter answers `*#18*51*51*<Wh>##` on the monitor session as an integer `Wh` counter (`8151646` in the live `MyHomeServer1` capture; `8133608` in the issue report).
   - **Pre-fix outcome**: Writing `float(last_state.state)` (`8133.608`, already in `kWh`) into `_attr_native_value` (`Wh`) produced a 17-second `8133.608 -> 8.133608 -> 8133.608 kWh` dip; Home Assistant's `total_increasing` statistics compiler treated the >10% drop as a meter reset and added `+8125.474 kWh` of phantom energy on every restart or reload.
   - **Post-fix outcome**: `RestoreSensor` restores exact integer native `Wh` (`8151646` / `8133608`, formatted `"8151646"` in `Wh` or `"8151.646"` in `kWh`) at `T+0s` with zero meter reset.

2. **Poll-dropping gateways (`H4684` `020054`, `MH200` `020102`, `F450` `020010`)** — `myhome_trace_oracle_h4684_who18_poll_refused_unsolicited_only.json`:
   - `own-firmware-oracle` proves these firmwares reject `*#18*51*51##`, `*#18*51*53##`, and `*#18*51*54##` with `NACK (*#*0##)` and `SILENT (Drop)` (zero SCS telegrams transmitted).
   - **Pre-fix outcome**: Because the post-discovery poll produces no meter response, the 1000x-corrupted state (`8.133608 kWh`) persisted until an unsolicited meter broadcast arrived (triggering the same `+8.13 MWh` reset) — or, if restarted again before any unsolicited broadcast, compounded by another `1000x` (`8133.608 -> 8.133608 -> 0.008133608 kWh`).
   - **Post-fix outcome**: Native `8133608 Wh` (`8133.608 kWh`) survives NACKed startup polls and consecutive restarts unchanged until an unsolicited `*#18*51*51*8133620##` frame arrives on the bus.

## Files

| File | Provenance & Scenario | Gateway Target |
|---|---|---|
| `myhome_trace_MyHomeServer1_who18_meter51_2026-11-14.json` | **Live hardware capture** (`"synthetic": false`): 96 `WHO = 18` meter `51` frames from `@xmavgithub`'s plant (`*#18*51*51*8151646##`, `*#18*51*53*88973##`, `*#18*51*54*3017##`, `*#18*51*113*106..144##`, and `sweep_bus` `*#18*51*1200##`) | `MyHomeServer1` (`2.87.13`) |
| `myhome_trace_oracle_f454_who18_totalizer_delayed_poll.json` | **Synthetic** (`"synthetic": true`): Startup discovery window (~17 s) followed by ACKed `*#18*51*51##` poll (`$06D1A1023350150000\r`) and `F520` totalizer reply `*#18*51*51*8133608##` + incremental `*#18*51*51*8133615##` | `F454` (`2.0.51`, forwarding gateway family) |
| `myhome_trace_oracle_h4684_who18_poll_refused_unsolicited_only.json` | **Synthetic** (`"synthetic": true`): Startup `*#18*51*51##`, `*#18*51*53##`, `*#18*51*54##` polls refused (`*#*0##`, `SILENT`) followed later by unsolicited bus report `*#18*51*51*8133620##` | `H4684` (`2.0.54`, oracle SHA `5a4597747e20`) |
