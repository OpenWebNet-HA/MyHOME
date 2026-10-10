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
| **F452 / F453AV** | OpenWebNet | 1 worker | 50 ms | ✅ Port 49153 | Audio/video & web server gateway |
| **HL4684** | OpenWebNet | 1 worker | 50 ms | ✅ SSDP | 10" Touch screen display IP gateway |
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
