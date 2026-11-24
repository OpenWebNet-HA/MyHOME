# Issue #976: MH200N (`FW 1.1.8`) WHO = 14 Actuator Lock & Unlocked Control Traces

Authentic live hardware captures contributed by **@wave68runner** in [PR #964 (comment 7039364366)](https://github.com/OpenWebNet-HA/MyHOME/pull/964#issuecomment-7039364366) and [comment 7045130116](https://github.com/OpenWebNet-HA/MyHOME/pull/964#issuecomment-7045130116), tracked in [Issue #976](https://github.com/OpenWebNet-HA/MyHOME/issues/976).

- **Gateway Model**: BTicino `MH200N` (`WHO=13` device code `44`, `MH200NProfile`, `0.15 s` command queue pacing)
- **Firmware Version**: `1.1.8` (reported in `16-41-08` and the same-day sweep in `tests/fixtures/traces/issue_968/`; `12-17-07` was exported from `2.0.0b17` before `WHO=13` Dimension `16` ran, so its JSON header shows `"firmware": "1.0"` and `"who13_firmware": null`)
- **Captured**: `2026-11-20` UTC
- **Privacy**: No LAN IPv4 addresses, full MACs, passwords, or local paths; only the public BTicino `00:03:50` MAC prefix is present (`findings == []`).

| File | SHA-256 | Content | Frame Count |
|---|---|---|:---:|
| `myhome_trace_MH200N_all_2026-11-20T12-17-07.json` | `67aafc4440807dd88891702b1df832dc40201f78904c23622fd57d881680dee2` | `WHO=14` lock (`*14*0*55##`) and unlock (`*14*1*55##`) cycle on dimmer `WHERE = 55`, plus background `WHO=4` and `WHO=13` traffic | `49` (`33 rx`, `16 tx`) |
| `myhome_trace_MH200N_all_2026-11-20T16-41-08.json` | `6e317fd1d15179802dfbb28870135da4e8bcabedec243bddf8411efe022e6a63` | Unlocked control verification across `WHO=1` addresses `22, 31, 55, 56, 57, 72, 1015`, plus `WHO=17` (`*17*1*11##`) and `WHO=1001` diagnostics | `190` (`97 rx`, `93 tx`) |

---

## Hardware Observations (`myhome_trace_MH200N_all_2026-11-20T12-17-07.json`)

1. **`MH200N` relays outbound `WHO = 14` point commands on the event session:**
   - `[16] tx *14*0*55##` → `[18] rx *14*0*55##` (`+104 ms`)
   - `[37] tx *14*1*55##` → `[38] rx *14*1*55##` (`+74 ms`)
   - `[39] tx *14*1*55##` → `[40] rx *14*1*55##` (`+66 ms`)
2. **Locked dimmer silently ignores writes while still answering reads:**
   - Immediately after lock (`[19..20]`), `*#1*55*1##` still answers `*#1*55*1*120*5##`.
   - Between `[27..33]`, five `Dimension 1` writes (`*#1*55*#1*177*0##`, `*146*`, `*177*`, `*139*`, `*172*`) and two `OFF` writes (`*1*0*55##`) emit **zero** `WHO = 1` bus echoes.
3. **Immediate recovery after unlock (`[42..47]`):**
   - `*#1*55*#1*140*0##` → `*#1*55*1*140*5##`, `*#1*55*#1*171*0##` → `*#1*55*1*171*5##`, and `*1*0*55##` → `*#1*55*1*100*2##`.
