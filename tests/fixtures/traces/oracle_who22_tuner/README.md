# Synthetic Traces: WHO 22 F500/F500N Tuner Lifecycle & Dual-Stack Scheduler Ordering

> [!NOTE]
> **Why these traces are synthetic**:
> Every file in this directory is **100% synthetic** (`"synthetic": true`, `"source": "firmware-oracle-contract"`). They are **not** captures from a physical installation; they are deterministically synthesized from static disassembly of the BTicino gateway translator daemons (`bt_multi` for WHO 22 and `bt_difson` for WHO 16) and live `up`-direction SCS bus replay in `own-firmware-oracle`.
>
> Real hardware captures are stored separately in:
> - `tests/fixtures/traces/f500_tuner/` (live `MH200N` + `F500N` bus monitor export where `WHO 16` arrived before `WHO 22`)
> - `tests/fixtures/traces/who22_myhomeserver1_f500_h4562/` (hand-transcribed, abbreviated debug-log excerpts from a live `MyHomeServer1` + `F500` plant in [OpenWebNet-HA/OWNd#103](https://github.com/OpenWebNet-HA/OWNd/pull/103#issuecomment-6113362654))

## What Gaps These Synthetic Traces Cover

1. **Unabbreviated WHO 22-Only Tuner Lifecycle (`myhome_trace_oracle_myhomeserver1_who22_only_tuner.json`)**:
   Modern gateways (`MyHomeServer1` `028206`, `F459` `020105`, `F460` `020012`, `F461` `020011`) ship `bt_multi` only (`bt_difson` is removed from firmware) and NACK all WHO 16 traffic. Because the only community `MyHomeServer1 + F500` trace (`who22_myhomeserver1_f500_h4562/`) was hand-transcribed and abbreviated (`1015` instead of `10150`, 5-code `RADIO` / `DEEJA` without trailing `*32` space padding, and elided `dim 5` frames), we lacked an unabbreviated WHO 22-only trace covering:
   - **WHO 16 NACK on startup**: `*16*101*101##` (`start_rds`) answered with `*#*0##` because `bt_difson` is absent.
   - **Stored preset tune (`station = '4'` $\rightarrow$ Preset 5 at `101.50 MHz`, SCS `$18D201F83402041FB8\r`)**: `bt_multi` (`0x091c0..0x092b0` in `MyHomeServer1 028206`) emits `dim 5` (`*#22*5#2#1*5*1*10150##`) followed immediately by `dim 11` (`*#22*5#2#1*11*1*10150*5##`) in 10 kHz units ($\text{steps} \times 5 + 8700$), followed by 8-code space-padded RDS (`*#22*5#2#1*10*82*65*68*73*79*32*32*32##` $\rightarrow$ `"RADIO"`).
   - **Manual / seek tune (`station = 'F'` at `99.50 MHz`, SCS `$18D201F834020F1F18\r`)**: `bt_multi` (`0x09248`) skips `dim 11` when `station_idx == 15` and emits **only** `dim 5` (`*#22*5#2#1*5*1*9950##`), which clears the previous preset (`Station 5` $\rightarrow$ `None`), followed by 8-space RDS retune blanking (`*#22*5#2#1*10*32*32*32*32*32*32*32*32##` $\rightarrow$ `media_title = None`).
   - **4-code partial RDS frame from a dropped middle SCS packet ($k=0$)**: `bt_multi` (`0x0be90..0x0c190`) reassembles 3-frame SCS sequences (`1` start + `4k` middle + `3` EOT bytes $= 4(k+1)$ ASCII codes). When the middle SCS packet is lost between Start (`$18D201F83501C00252\r`) and EOT (`$18D201F83541444904\r`), `bt_multi` emits a 4-code RDS frame (`*#22*5#2#1*10*82*65*68*73##` $\rightarrow$ `"RADI"`), followed on the next cycle by full 8-code `"DEEJAY  "` (`*#22*5#2#1*10*68*69*69*74*65*89*32*32##` $\rightarrow$ `"DEEJAY"`).
   - **12-code WHO 22 RDS buffer-collision glitch ($k=2$)**: When an EOT packet and the next Start packet are missed so two 4-byte middle packets accumulate before EOT (`1 + 4 + 4 + 3 = 12` codes), `bt_multi` emits `*#22*5#2#1*10*42*42*79*79*69*42*82*65*68*42*42*32##`, which is dropped without corrupting `"DEEJAY"`.
   - **Explicit `preset = 0` on `dim 11`** (`*#22*5#2#1*11*1*8950*0##`) and **direct `dim 6` station query reply** (`*#22*2#1*6##` $\rightarrow$ `*#22*2#1*6*3##`).

2. **Dual-Stack `WHO 22`-First Scheduler Ordering (`myhome_trace_oracle_mh200n_who22_first_dual_stack.json`)**:
   Dual-stack gateways (`MH200N` `010108`, `F454` `020051`, `F453AV` `030014`) run both `bt_difson` (WHO 16) and `bt_multi` (WHO 22) concurrently. While `WHO 16` arrived first in the live `f500_tuner/` capture, replaying SCS `D2` frames (`up` direction) through the emulated `MH200N` `010108` firmware in `own-firmware-oracle` demonstrated that `bt_multi` can beat `bt_difson` to the socket (`WHO 22` emitted before `WHO 16`). This synthetic trace records the exact oracle-emitted `WHO 22`-first sequence across preset tuning, 8-code RDS (`"KRONEHIT"`), 12-code RDS glitch ($k=2$), and manual tuning (`99.50 MHz`).

## Files

| File | Synthetic Scenario | Gateway Target |
|---|---|---|
| `myhome_trace_oracle_myhomeserver1_who22_only_tuner.json` | **Synthetic**: Unabbreviated WHO 22-only F500 lifecycle (10 kHz `dim 5`+`dim 11` preset tune, `dim 5`-only manual tune, 8-space blanking, 4-code $k=0$ partial RDS, 8-code $k=1$ full RDS, 12-code $k=2$ glitch, `dim 6` query reply) | `MyHomeServer1` (`2.82.6`, `bt_multi` SHA-256 `a4167794...`) |
| `myhome_trace_oracle_mh200n_who22_first_dual_stack.json` | **Synthetic**: Dual-stack `bt_multi` + `bt_difson` race where `WHO 22` arrives before `WHO 16` on preset tune, RDS, 12-code glitch, and manual tune | `MH200N` (`1.1.8`, `bt_multi` `a6d776bc...` + `bt_difson` `6bf6a4c6...`) |
