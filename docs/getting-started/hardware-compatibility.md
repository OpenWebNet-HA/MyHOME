# Hardware Compatibility Matrix

The MyHOME integration supports all official BTicino and Legrand OpenWebNet gateways communicating via TCP/IP sockets or USB/Serial interfaces.

---

## Supported Gateways

<!-- GATEWAY_PROFILES_START -->
| Gateway Model | Protocol Support | Max Command Workers | Inter-Frame Delay | UPnP Discovery | Notes |
|---|---|---|---|---|---|
| **F454** | OpenWebNet / HMAC | 4 workers | 50 ms | ✅ Port 49153 | Full high-speed multi-session support |
| **F455** | OpenWebNet / HMAC | 4 workers | 50 ms | ✅ Port 49153 | Basic gateway (single SCS bus) |
| **F461** | OpenWebNet / HMAC | 4 workers | 50 ms | ❌ Manual | Compact DIN Ethernet Web Server |
| **MH202** | OpenWebNet / HMAC | 2 workers | 100 ms | ✅ Port 49153 | Modern scenario programmer gateway |
| **MH201** | OpenWebNet | 1 worker | 100 ms | ✅ Port 49153 | Second-generation scenario programmer |
| **MyHomeServer1** | OpenWebNet / HMAC | 4 workers | 20 ms | ✅ SSDP | Cloud/local hybrid gateway |
| **MH200N** | OpenWebNet | 1 worker | 150 ms | ✅ SSDP | Second-generation scenario programmer |
| **MH200** *(Legacy)* | OpenWebNet | 1 worker | 150 ms | ✅ SSDP | Strict single-session pacing; watchdog hardened |
| **H4890 / AM4890** | OpenWebNet | 1 worker | 50 ms | ✅ SSDP | 3.5" Touch screen display IP gateway (Axolute / Livinglight) |
| **F452 / F453AV** | OpenWebNet | 1 worker | 150 ms | ✅ Port 49153 | Audio/video & web server gateway |
| **H4684 / H/L4684** | OpenWebNet | 1 worker | 50 ms | ✅ SSDP | Colour touch screen IP gateway (Axolute / LivingLight) |
| **Legrand 3578** | OpenWebNet (Serial) | 1 worker | 50 ms | ❌ Manual (Serial) | USB / Serial gateway & OpenZigBee interface |
<!-- GATEWAY_PROFILES_END -->

> [!NOTE]
> Gateways with only **1 concurrent command session** (such as the MH200N, MH200, or MH201) are automatically tuned with command pacing (100–150 ms) to avoid queue flooding. Modern multi-session gateways (F454, F455, MyHomeServer1) use 20–50 ms pacing with worker pools (up to 4 workers).

---

## Supported Bus Subsystems (`WHO`)

| Subsystem | WHO Code | Home Assistant Platform | Typical Hardware Modules |
| :--- | :---: | :--- | :--- |
| **Lighting** | `1` | `light`, `switch` | F411/1, F411/2, F411/4, F418 (dimmer), F429 (DALI), 3560, L4652 |
| **Automation / Covers** | `2` | `cover` | F401, F411, LN4672M2, 67557 |
| **Thermoregulation** | `4` | `climate`, `sensor` | 3550, 4695 (Central Units), 3455, L4691, L4577, F430/2, F430/4 |
| **Burglar Alarm** | `5` | `alarm_control_panel` | 3485, 3486 (Central Units), 3480 |
| **Gateway Diagnostics** | `13` | `diagnostics`, `repair` | Gateway internal RTC clock, firmware, uptime, device types |
| **Scenario Control (CEN)** | `15` | `device_trigger`, `event` | 3477, L4651/2, L4652/2 (Short / Long press) |
| **Sound System** | `16` | `media_player` | F441, F441M (Audio matrix), L4561N / L4561, L4560, 3482, 3495 (Audio source interfaces & isolator), H4562, L4562, F502, 3484, 3487 (Room amplifiers), F500, F500COAX (FM RDS Tuners) |
| **Energy Management** | `18` | `sensor` | F520, F521, F522, F523, 3522 |
| **Scenario Control (CEN+)** | `25` | `device_trigger`, `event` | L4652/3, LN4652, H4652 (Rotary dials, pushbuttons) |

> [!NOTE]
> **Sound System (WHO = 16) Wiring**: The 2-wire SCS bus carries both digital commands (power, volume, input routing via OpenWebNet WHO 16) and modulated stereo audio simultaneously over the same 2-wire bus. External audio streamers connect their stereo analog line-out to an audio source interface (e.g. Legrand / BTicino L4561N, L4560, or 3482), which modulates the audio onto the 2-wire SCS bus into the F441/F441M matrix inputs S1–S4. For full details and the official wiring schematic, see the [Sound System / Media Player Guide](../configuration/media_player.md).

---

## Gateway Startup Discovery Capabilities

During startup initial discovery, status queries are gated by the gateway profile (the WHOs it advertises) and then by what the gateway firmware actually forwards: a general request the firmware drops before the bus (`*#4*0##`, `*#16*0*5##` on the models noted below) is left out even though the profile advertises its WHO, and the climate zones of the SCS-only models among them ask `*#4*<WHERE>*0##` themselves. The second column is the profile, the third what the sweep sends:

<!-- GATEWAY_DISCOVERY_START -->
| Gateway Model | Profile Subsystems (advertised) | Startup Sweep Requests (sent) | Profile Support Notes |
| :--- | :--- | :--- | :--- |
| **MH202** | WHO 1, 2, 4, 5, 16, 18 | WHO 1, 2, 5, 18 (firmware drops `*#4*0##`, `*#16*0*5##`) | Burglar Alarm (`*#5*0##`) and Energy (`*#18*51*...`) queries. WHO 4 and WHO 16 are advertised, but the `010024` firmware drops `*#4*0##` and `*#16*0*5##` (own-firmware-oracle), so the sweep leaves both out and each climate zone asks `*#4*<WHERE>*0##` itself. |
| **H4890 / AM4890** | WHO 1, 2, 4, 5, 16, 18 | WHO 1, 2, 4, 5, 16, 18 | Supports Burglar Alarm, Sound (`*#16*0*5##`), and Energy meter 51 queries. |
| **F454** | WHO 1, 2, 4, 16, 18 | WHO 1, 2, 16, 18 (firmware drops `*#4*0##`) | Full lighting, cover, sound (`bt_difson` firmware-verified), and energy meter 51 queries. The `020051` firmware drops `*#4*0##` (own-firmware-oracle), so each climate zone asks `*#4*<WHERE>*0##` itself. Alarm query omitted. |
| **F452 / F453 / F453AV** | WHO 1, 2, 4, 16, 18 | WHO 1, 2, 4, 16, 18 | Lighting, cover, climate, sound (`bt_difson` firmware-verified on F453/F453AV), and energy meter 51 queries. Alarm query omitted. |
| **F461** | WHO 1, 2, 4, 16, 18 | WHO 1, 2, 18 (firmware drops `*#4*0##`, `*#16*0*5##`) | Lighting, cover, and energy meter 51 queries. The `020011` firmware drops `*#4*0##` and `*#16*0*5##` (own-firmware-oracle, confirmed live on 2.0.11), so the sweep leaves both out; zones are polled with `*#4*<WHERE>##`. Alarm query omitted. |
| **F455** | WHO 1, 2, 4, 18 | WHO 1, 2, 4, 18 | Entry-level gateway: lighting, cover, climate, and energy meter 51 queries. Sound (WHO 16/22) and Alarm (WHO 5) omitted per installer manual. |
| **MyHomeServer1** | WHO 1, 2, 4, 16, 18 | WHO 1, 2, 18 (firmware drops `*#4*0##`, `*#16*0*5##`) | Lighting, cover, and energy meter 51 queries. The firmware drops `*#4*0##` and `*#16*0*5##` (own-firmware-oracle `028206`, confirmed live on 3.87.13), so the sweep leaves both out; zones are polled with `*#4*<WHERE>##`. Alarm query omitted. |
| **MH201** | WHO 1, 2, 4, 16, 18 | WHO 1, 2, 16, 18 (firmware drops `*#4*0##`) | Lighting, cover, climate, sound (unmeasured class default), and energy meter 51 queries. Not in own-firmware-oracle; a physical MH201 3.3.6 returned no zone frame to `*#4*0##` although its zones report (`tests/fixtures/traces/issue_466`), so the sweep leaves it out and each climate zone asks `*#4*<WHERE>*0##` itself. Alarm query omitted. |
| **MH200** *(Legacy)* | WHO 1, 2, 4, 16 | WHO 1, 2, 16 (firmware drops `*#4*0##`) | Lighting, cover, climate, and sound queries (`bt_difson` hardware and firmware-verified). The `020102` firmware drops `*#4*0##` (own-firmware-oracle), so its climate zones ask `*#4*<WHERE>*0##` themselves. Alarm and energy queries omitted. |
| **MH200N** | WHO 1, 2, 4, 16 | WHO 1, 2, 4, 16 | Lighting, cover, climate, and sound queries (`bt_difson` hardware and firmware-verified). The `010108` firmware forwards `*#4*0##` (own-firmware-oracle), unlike the MH200. Alarm and energy queries omitted. |
| **H4684 / H/L4684** | WHO 1, 2, 4, 16, 18 | WHO 1, 2, 16, 18 (firmware drops `*#4*0##`) | Lighting, cover, climate, sound, and energy meter 51 queries (`bt_multi` WHO 22 also enabled on firmware >= `6.0.0`). Both `020054` (own-firmware-oracle) and live `6.0.11` (`tests/fixtures/traces/issue_466`) drop `*#4*0##`, so each climate zone asks `*#4*<WHERE>*0##` itself; the SSDP spellings `H/L4684` and `HL4684` resolve to `H4684Profile`. Alarm query omitted. |
<!-- GATEWAY_DISCOVERY_END -->

