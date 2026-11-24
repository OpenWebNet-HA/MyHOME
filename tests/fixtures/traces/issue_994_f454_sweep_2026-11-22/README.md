# Issue #994: F454 (`FW 2.0.51`) + 4-Zone Central Unit (`LN4695`) Bus Sweep Capture

Authentic live `myhome.sweep_bus` capture contributed by **@paolozamberletti-maker** in [Issue #994 (comment 7086354767)](https://github.com/OpenWebNet-HA/MyHOME/issues/994#issuecomment-7086354767) (complements the command/event traces from the same plant).

- **Gateway Model**: BTicino `F454` (`WHO=13` device code `200`, `WHO=1013` `OBJECT_MODEL = 51`, `F454Profile`, `0.05 s` command queue pacing)
- **Firmware Version**: `2.0.51` (Linux kernel `2.6.32`, distribution `2.16.11`)
- **Environment**: Home Assistant `2026.11.2`, MyHOME `2.0.0`, OWNd `2.0.1`
- **Captured**: `2026-11-22T21:33:46Z` → `21:35:16Z` UTC
- **Privacy**: No LAN IPv4 addresses, full MACs, passwords, or local paths; only the public BTicino `00:03:50` MAC prefix is present (`findings == []`).

| File | SHA-256 | Content | Frame Count |
|---|---|---|:---:|
| `myhome_sweep_F454_all_2026-11-22T21-35-56.json` | `2997b5daa2cdd93c572a60a270384148b3eb3436bdd342111a033d67f442184d` | Full bus sweep: `WHO=13`/`1013` identity, 69 `WHO=1` lighting addresses, `WHO=18` probes (no meters installed), and `WHO=4` readings for Zones `1, 3, 4` | `197` (`152 rx`, `45 tx`) |

---

## Hardware Observations

1. **Identity fingerprint (`frames[0..11]`):**
   - `*#13**15##` → `*#13**15*200##`, `*#13**16##` → `*#13**16*2*0*51##`, `*#13**23##` → `*#13**23*2*6*32##`, `*#13**24##` → `*#13**24*2*16*11##`, `*#1013*0*1##` → `*#1013**1*51*15*5*0##`.
2. **Lighting sweep (`*#1*0##`):**
   - Reports `141` `WHO = 1` `rx` frames covering `69` unique addresses (`13`..`99`).
3. **Thermoregulation (`frames[192..196]`):**
   - Captures spontaneous `WHO = 4` temperature and actuator reports for Zones `1`, `3`, and `4`: `*#4*1*0*0194##`, `*#4*3*0*0195##`, `*#4*4*0*0194##`, `*#4*4#1*20*0##`, `*#4*1#1*20*0##`.
