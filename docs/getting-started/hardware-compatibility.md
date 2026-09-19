# Hardware Compatibility Matrix

The MyHOME integration supports all official BTicino and Legrand OpenWebNet gateways communicating via TCP/IP sockets or USB/Serial interfaces.

---

## Supported Gateways

| Gateway Model | Manufacturer | Connection Type | Port / Baud | Max Sockets | HMAC / SHA Auth | Recommended Profile |
| :--- | :--- | :--- | :---: | :---: | :---: | :--- |
| **MyHomeServer1** | BTicino | Ethernet (IP) | `20000` | 4 | **Yes** (required) | `MyHomeServer1Profile` |
| **F454** | BTicino / Legrand | Ethernet (IP) | `20000` | 2–3 | Optional | `F454Profile` |
| **F455** | BTicino | Ethernet (IP) | `20000` | 2–3 | Optional | `F455Profile` |
| **MH200N** | BTicino | Ethernet (IP) | `20000` | 1 | No | `MH200NProfile` |
| **MH201** | BTicino | Ethernet (IP) | `20000` | 1 | No | `MH201Profile` |
| **MH202** | BTicino | Ethernet (IP) | `20000` | 1 | No | `MH202Profile` |
| **F452 / F452V** | BTicino | Ethernet (IP) | `20000` | 1 | No | `F452Profile` |
| **MHServer / MHServer2** | BTicino | Ethernet (IP) | `20000` | 1 | No | `MHServerProfile` |
| **F461** | BTicino | Ethernet (IP) | `20000` | 2 | No | `F461Profile` |
| **Legrand 3578** | Legrand | USB / RS232 Serial | `57600` | 1 | N/A | `SerialProfile` |

> [!NOTE]
> Gateways with only **1 concurrent command session** (such as the MH200N or MH201) are automatically tuned with command pacing (150 ms) to avoid queue flooding. Modern multi-session gateways (F454, MyHomeServer1) use 20 ms pacing with worker pools.

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
| **Sound System** | `16` | `media_player` | F441, F441M (Audio matrix), 3445 (amplifiers), 3482 |
| **Energy Management** | `18` | `sensor` | F520, F521, F522, F523, 3522 |
| **Scenario Control (CEN+)** | `25` | `device_trigger`, `event` | L4652/3, LN4652, H4652 (Rotary dials, pushbuttons) |
