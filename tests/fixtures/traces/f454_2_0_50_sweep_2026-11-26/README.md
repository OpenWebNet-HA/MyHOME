# F454 (`FW 2.0.50`) Full Bus Sweep & Climate / Lighting Interaction Capture (`2026-11-26`)

Authentic live `myhome.sweep_bus` + interactive control capture from a physical BTicino `F454` running firmware **`2.0.50`** (`distribution 2.16.2`), archived for the community hardware trace corpus (Refs #466).

- **Gateway Model**: BTicino `F454` (`WHO=13` device code `200`, `WHO=1013` `OBJECT_MODEL = 51`, `other_names = ["003598"]`, `N_CONF = 15`, `BRAND = 5` Legrand BTicino, `F454Profile`, `0.05 s` command queue pacing)
- **Firmware Version**: `2.0.50` (`*#13**16*2*0*50##`, Linux kernel `2.6.32` via `*#13**23*2*6*32##`, distribution `2.16.2` via `*#13**24*2*16*2##`) — first `F454` FW `2.0.50` capture in the corpus (existing `F454` captures are `1.0.34` and `2.0.51` / distribution `2.16.11`)
- **Environment**: Home Assistant `2026.11.3`, MyHOME `2.0.1`, OWNd `2.0.2`
- **Captured**: `2026-11-26T15:21:19.575Z` → `15:23:52.641Z` UTC (exported `2026-11-26T15:23:55.261Z`, `truncated: false`)
- **Privacy**: No LAN IPv4 addresses, full MACs, passwords, or local paths; only the public BTicino `00:03:50` MAC prefix is present (`findings == []`).

| File | SHA-256 | Content | Frame Count |
|---|---|---|:---:|
| `myhome_sweep_F454_all_2026-11-26T15-23-55.json` | `a1f53c737384812f2803b69fb81270cb2da24a4623aa844faafb1804f19aa4ac` | Full bus sweep + live `WHO=4` setpoint writes & `WHO=1` light toggle: `WHO=13`/`1013` identity, 70 `WHO=1` lighting addresses (48 2-digit + 22 4-digit), `WHO=2` cover `91`, `WHO=4` Zones `64`/`69`/`71`/`72` (central setpoints, `Dim 12` offsets, `Dim 14` readback, multi-actuator `Dim 20`), and `WHO=16`/`18` sweep probes | `240` (`186 rx`, `54 tx`) |

---

## Hardware Observations

1. **Identity fingerprint (`frames[0..11]`):**
   - `*#13**0##` → `*#13**0*18*19*23*001##`, `*#13**15##` → `*#13**15*200##`, `*#13**16##` → `*#13**16*2*0*50##`, `*#13**23##` → `*#13**23*2*6*32##`, `*#13**24##` → `*#13**24*2*16*2##`, `*#1013*0*1##` → `*#1013**1*51*15*5*0##`.
2. **Shared `WHO = 1` / `WHO = 2` general status sweep (`frames[12..85]`, `frames[98..171]`):**
   - Both `*#1*0##` (`frames[12]`) and `*#2*0##` (`frames[98]`) trigger identical 71-frame `WHO = 1` lighting bursts across `70` unique addresses (`48` standard 2-digit `10`..`59` and `22` 4-digit extended `A/PL` `0110`..`0415`, with both `*1*1*22##` and `*1*0*22##` observed on `22`) plus `WHO = 2` cover `*2*0*91##` (`frames[23]` and `frames[117]`).
   - Matches `own-firmware-oracle` `F454 020051`, where both `*#1*0##` (`lights-area0.tsv:16`) and `*#2*0##` (`trace-corpus.tsv:41`) translate to the same SCS general status telegram (`24 30 34 42 31 30 30 31 35 30 30 0d`).
3. **Live `WHO = 1` toggle (`frames[236..239]`):**
   - `tx *1*1*15##` → `rx *1*1*15##` (`108 ms`), followed by `tx *1*0*15##` → `rx *1*0*15##` (`108 ms`).
4. **Thermoregulation (`WHO = 4`, `34 rx`, `7 tx`):**
   - **Zone `64` (`frames[86..97]`, `frames[166]`):** Central-form manual setpoint writes `*#4*#64*#14*0210*3##` (`21.0 °C`) and `*#4*#64*#14*0220*3##` (`22.0 °C`) elicit measured temperature `*#4*64*0*0220##` (`22.0 °C`), heating season `*4*1*64##`, offset-adjusted `Dim 12` (`*#4*64*12*0220*3##`, then `*#4*64*12*0230*3##` = `+1.0 °C` above nominal), manual mode `*4*110*#64##`, explicit `Dim 14` nominal readback `*#4*64*14##` → `*#4*64*14*0220*3##` (`22.0 °C`), and actuator ON `*#4*64#1*20*1##`.
   - **Zone `69` (`frames[49]`):** Spontaneous actuator ON `*#4*69#1*20*1##`.
   - **Zone `71` (`frames[209..218]`, `frames[220]`):** Setpoint write `*#4*#71*#14*0230*3##` (`23.0 °C`) elicits `*#4*71*0*0207##` (`20.7 °C`), `*4*1*71##`, `*#4*71*12*0220*3##` (`22.0 °C` = `-1.0 °C` below nominal), `*4*110*#71##`, and six sequential actuator ON reports `*#4*71#1*20*1##` .. `*#4*71#6*20*1##`.
   - **Zone `72` (`frames[219]`, `frames[221..235]`):** Three setpoint writes (`0115` / `11.5 °C`, `0135` / `13.5 °C`, `0190` / `19.0 °C`) each return `*#4*72*0*0172##` (`17.2 °C`), `*4*1*72##`, `Dim 12` shifted `+1.0 °C` (`0125`, `0145`, `0200`), and `*4*110*#72##`, followed by actuator ON `*#4*72#1*20*1##` once the adjusted setpoint (`20.0 °C`) exceeds measured temperature (`17.2 °C`).
5. **Sound (`WHO = 16`) & Energy (`WHO = 18`) sweep probes (`frames[172..208]`):**
   - `tx *#16*0*5##`, `*#18*51..59*{51,1200}##`, and `*#18*71#0..79#0*{51,1200}##` (`37 tx`, `0 rx` replies; no audio or energy meters installed on this bus).
