# F459 (`FW 2.1.5`) Full Home Assistant Debug Log Capture (`2026-11-26`)

Authentic full Home Assistant debug log (`home-assistant_myhome_2026-11-26T15-46-40.499Z.log`) from a physical BTicino `F459` running firmware **`2.1.5`** under **MyHOME `v2.0.1` / `OWNd v2.0.2`**, archived for the community hardware trace corpus (Refs #466).

- **Gateway Model**: BTicino `F459` (`WHO=13` device code `200`, `WHO=1013` `OBJECT_MODEL = 65`, `N_CONF = 15`, `BRAND = 5` Legrand BTicino, `LINE = 0`)
- **Firmware Version**: `2.1.5` (`*#13**16*2*1*5##`, explicitly captured on connect)
- **Environment**: MyHOME `v2.0.1`, OWNd `v2.0.2` (`OPEN` numeric-nonce authentication; no password echoed)
- **Window**: `2026-11-26 16:27:19.638+02:00` → `17:46:21.975+02:00` (`Message received` span `17:42:28.923+02:00` → `17:46:21.974+02:00`)
- **Sanitization**:
  - Container startup boilerplate (`s6-overlay` and third-party integration startup warnings before the first `custom_components.myhome` line) and three unrelated third-party log lines (`soundtouch` ULID warnings, `samsungtvws.connection`) are omitted, retaining all `2,943` `custom_components.myhome` lines verbatim in order.
  - The gateway LAN IPv4 address is replaced with RFC 5737 documentation IP `192.0.2.59`, the gateway MAC with synthetic BTicino-OUI `00:03:50:00:11:26`, and the `119` user-named `light.<slug>` entity IDs in `state already reported on the bus, skipping post-discovery poll` lines with deterministic generic IDs `light.light_001`..`light.light_119`.
  - All `1,719` backticked OpenWebNet frames (`818` `Message received`, `336` `Collected command response`, queued/sent/NACKed commands) are preserved byte-for-byte verbatim (`findings == []`).

| File | SHA-256 | Content | Inbound Frames |
|---|---|---|:---:|
| `F459_full_debug_log_2026-11-26T15-46-40.log` | `cd817823e9d58c6564d73e91bc7696f86274013edacbe1314e5ffa51b2674fce` | Full HA debug session (`2,943` lines): `WHO=1` (main bus + F422 `#4#01`, `Dim 1`/`Dim 4`, groups, `1000#` translation), `WHO=2` (`*#2*0##` timeout + per-cover fallback + opening), `WHO=4` (19 active zones × 5 dims + `0#1..3` pumps + silent zone `61`), `WHO=13`/`1013`, `WHO=18` probes, and the first `F459` `WHO=25` dry-contact and `WHO=1001` lighting-diagnostic captures | `818` `Message received` + `336` `Collected command response` (`1,154` `rx` lines) |

---

## Hardware Observations

1. **First `WHO = 25` (`CEN+` / Dry Contact) capture on `F459`:**
   - Startup status poll `*#25*31##` is answered with `*25*31#0*31##` (`OWNDryContactEvent`, `Sensor 1 reported ON.`), adding `WHO 25` (`✅`) to `F459` in the Hardware Trace Availability Matrix.
2. **First `WHO = 1001` (Lighting & Automation Diagnostics) capture on `F459`:**
   - `10` inbound `WHO = 1001` `Dimension 11` 24-bit autodiagnostic bitmasks (`OWNLightingDiagnosticEvent`) across both the main bus (`*#1001*52*11*011110111111110101100111##`) and F422 local bus `#4#01` (`*#1001*0614#4#01*11*111110111111111101100111##`, `*#1001*97#4#01*11*011110111111110101100111##`), adding `WHO 1001` (`✅`) to `F459` in the Hardware Trace Availability Matrix.
3. **Gateway identity (`WHO = 13` / `1013`):**
   - `12` `WHO = 13` `Message received` frames: `*#13**15*200##` (`11×`, including autonomous 25 s broadcasts) and `*#13**16*2*1*5##` (first on-wire `F459` firmware `2.1.5` reply in the corpus).
   - `2` `WHO = 1013` `Message received` frames: `*#1013**1*65*15*5*0##` (`OBJECT_MODEL = 65` = `F459`) and `*#1013*260*11*111111111111111101101111##`.
4. **Lighting (`WHO = 1`, `528` `Message received`):**
   - `283` lighting events (`*1*...##`), `219` `Dimension 1` ON dimmer level replies (levels `101`–`200`, speeds `2`/`4`/`5`), and `26` `Dimension 4` frames (`25` OFF replies `*#1*W*4*100*S##` to `*#1*W*1##` reads plus spontaneous ON `*#1*1009*4*200*2##` 90 ms after translation frame `*1*1000#1#255*1009##`).
   - `208` `WHO = 1` inbound frames routed across F422 local bus `#4#01` (`216` total `#4#01` `Message received` frames including `8` `WHO = 1001` diagnostics), plus group sweep cancellation after member echoes (`#5#4#01`, `#6`, `#6#4#01`, `#10#4#01`).
5. **Automation (`WHO = 2`, `127` `Message received` across `40` covers):**
   - General status `*#2*0##` times out (`Timed out awaiting the complete response for *#2*0##`; also NACKed once) and MyHOME falls back to polling all `40` covers individually (`*#2*WHERE##`); `124` stopped (`WHAT = 0`) and `3` opening (`*2*1*0015##`) events captured.
6. **Thermoregulation (`WHO = 4`, `138` `Message received`):**
   - `F459` profile skips `*#4*0##` and polls zones individually: `19` active zones report `Dim 0` temperature (`16.9`–`24.1 °C`), `Dim 12` target (`12.0`–`23.0 °C`), `Dim 13` local offset (`00`), `Dim 20` actuator (`0`), `Dim 60` humidity (`32`–`51%`), and `*4*1*Z##` heating season; central pump/boiler actuators `0#1`, `0#2`, `0#3` report `*#4*0#N*20*0##`; silent Zone `61` NACKs startup fallback reads (`*#4*61*0##`, `*#4*61*20##`, `*#4*61*13##`).
7. **Energy (`WHO = 18`):**
   - `*#18*51*51##` and `*#18*51*113##` are sent cleanly with `0` `WHO = 18` replies (no energy meter installed).
8. **OWNd parse coverage (`OWNd 2.0.2`):**
   - `818/818` `Message received` frames parse cleanly (`528` `OWNLightingEvent`, `138` `OWNHeatingEvent`, `127` `OWNAutomationEvent`, `12` `OWNGatewayEvent`, `10` `OWNLightingDiagnosticEvent`, `2` `OWNGatewayDiagnosticEvent`, `1` `OWNDryContactEvent`).

Pinned by `tests/test_f459_full_log_2026_11_26_trace.py`.
