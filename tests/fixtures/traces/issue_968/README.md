# Issue #968: MH200N (`FW 1.1.8`) Full Bus Sweep Capture

Authentic live `myhome.sweep_bus` capture contributed by **@wave68runner** in [Issue #968](https://github.com/OpenWebNet-HA/MyHOME/issues/968).

- **Gateway Model**: BTicino `MH200N` (`WHO=13` device code `44`, `WHO=1013` `OBJECT_MODEL = 44`, `MH200NProfile`, `0.15 s` command queue pacing)
- **Firmware Version**: `1.1.8` (Linux kernel `2.3.8`, distribution `3.0.1`)
- **Environment**: Home Assistant `2026.11.1`, MyHOME `2.0.0`, OWNd `2.0.1`
- **Captured**: `2026-11-20T12:46:07Z` → `12:46:34Z` UTC
- **Privacy**: No LAN IPv4 addresses, full MACs, passwords, or local paths; only the public BTicino `00:03:50` MAC prefix is present (`findings == []`).

| File | SHA-256 | Content | Frame Count |
|---|---|---|:---:|
| `myhome_sweep_MH200N_all_2026-11-20T12-47-02.json` | `19be38a919c19639849a6b02a37c055507e274440ed6537ed11d06d7100e33bc` | Full bus sweep: `WHO=13`/`1013` identity, 39 `WHO=1` lights, `WHO=1001` diagnostics (`WHERE = 72`), and `WHO=16`/`22` sound system responses | `117` (`105 rx`, `12 tx`) |

---

## Hardware Observations

1. **Identity fingerprint (`frames[0..11]`):**
   - `*#13**15##` → `*#13**15*44##`, `*#13**16##` → `*#13**16*1*1*8##`, `*#13**23##` → `*#13**23*2*3*8##`, `*#13**24##` → `*#13**24*3*0*1##`, `*#1013*0*1##` → `*#1013**1*44*15*0*0##`.
2. **Lighting sweep (`*#1*0##`):**
   - Reports `80` `WHO = 1` `rx` frames covering `39` unique lighting addresses (`0011`, `01`..`72`), notably omitting `51` (confirming the ghost entity in `#968` was not reported by the gateway).
3. **Sound system sweep (`*#16*0*5##`, `frames[99..115]`):**
   - Produces `8` paired `WHO = 16` (`*16*13*...##`, `*16*3*101##`) and `WHO = 22` (`*#22*3#...*12*0*10##`, `*#22*2#1*12*1*4##`) status replies.
