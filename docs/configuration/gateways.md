# Gateways & Connection Architecture (`WHO = 13`)

This guide details the network connection, authentication, and resilience architecture for Legrand / BTicino OpenWebNet gateways in Home Assistant.

---

## 🏛️ Supported Gateway Hardware

The MyHOME integration communicates with SCS bus gateways over TCP/IP or RS232/USB serial:

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

---

## 🔌 Connection Setup via Config Flow

### Step 1: Initial Discovery
- In many networks, MyHOME gateways announce themselves via **SSDP** or **mDNS**.
- If discovered automatically, Home Assistant displays a notification prompting to configure the discovered gateway.
- If configuring manually: Go to **Settings** -> **Devices & Services** -> **Add Integration** -> search **MyHOME**.

### Step 2: Installation Parameters Reference

| Parameter | Key | Type | Default | Description |
| :--- | :--- | :---: | :---: | :--- |
| **Host** | `host` | String | - | IPv4 address or hostname of the OpenWebNet gateway (e.g. `192.168.1.50`). A static IP or permanent DHCP reservation is strongly advised. |
| **Port** | `port` | Integer | `20000` | TCP port for the OpenWebNet service (standard default is `20000`). |
| **Password** | `password` | String | None | OpenWebNet password. Can be numeric (4 or 9 digits) or alphanumeric depending on gateway model and firmware. For **MyHomeServer1**, use the installer password configured in MyHOME_Up. Leave blank if open LAN is active. |
| **Serial Device** | `device` | String | None | Port path (e.g. `/dev/ttyUSB0` or `COM3`) when connecting via BTicino 3578 USB/Serial interface. |
| **Gateway Model** | `model` | Select | Auto-detected | Hardware model (e.g. `MyHomeServer1`, `F454`, `MH201`, `F453AV`). Auto-detected during handshake, or selected manually. |

---

## ⚡ Dual-Session Architecture

OpenWebNet gateways manage communication using two distinct connection modes:

```
┌────────────────────────────────────────────────────────┐
│                   Home Assistant                       │
└──────────────┬──────────────────────────▲──────────────┘
               │                          │
        Command Session             Event Session
          (*99*0##)                   (*99*1##)
               │                          │
        Transactional              Persistent Stream
     (Sends WHAT/DIMENSION)     (Listens to Bus Traffic)
               │                          │
               ▼                          ▼
┌────────────────────────────────────────────────────────┐
│               MyHOME OpenWebNet Gateway                │
│                 (F454 / MHS1 / MH201)                  │
└──────────────────────────┬─────────────────────────────┘
                           │
                     SCS 2-Wire Bus
```

1. **Event Session (`*99*1##`)**:
   - Long-lived persistent TCP socket opened at startup.
   - Listens passively for all telegrams occurring on the physical SCS bus (e.g. wall switch presses, sensor readings, actuator confirmations).
   - Feeds the in-band **Bus Monitor** and updates Home Assistant entity states immediately.

2. **Command Session (`*99*0##`)**:
   - Dedicated transactional channel used to dispatch actions (e.g. turning on a light, opening a shutter, syncing gateway time).
   - Manages request queueing, rate limiting, and response verification (`*#*1##` ACK vs. `*#*0##` NACK).

---

## ⚙️ Gateway Runtime Options Flow

You can adjust integration runtime parameters at any time without re-adding the gateway:

1. Navigate to **Settings → Devices & Services → MyHOME**.
2. Click **Configure** on the gateway integration card.

<!-- GATEWAY_OPTIONS_START -->
| Option | Key | Selector / Type | Default | Session / Model Limits | Description |
| :--- | :--- | :---: | :---: | :--- | :--- |
| **Command Worker Concurrency** | `worker_count` | Integer | `1` | Range 1–10 (capped by model: 1 for MH200/MH201, 2 for MH202, 4 for F454/MHS1) | Number of concurrent asynchronous command sessions dispatched to the gateway. |
| **Dimmer Transition Mode** | `transition_mode` | Select | `software_stepped` | `software_stepped`, `native`, `auto` | Home Assistant software-stepped fade vs native hardware speed parameter. |
| **Event Bus Broadcasting** | `generate_events` | Boolean | `False` | All gateways | Emits raw OpenWebNet bus frames onto the Home Assistant event bus as `myhome_message_event`. |
| **Broadcast Re-sync** | `broadcast_resync` | Boolean | `True` | All gateways | Automatically triggers targeted entity queries when general or area broadcast commands (`WHERE = 0`) are received. |
| **Gateway Host Address** | `address` | IPv4 String | Current Host | Valid IPv4 | In-place update of gateway IP address without deleting the integration entry. |
| **Gateway Password** | `own_password` | String | Current Pass | Alphanumeric / Numeric | In-place update of OpenWebNet password without deleting the integration entry. |
| **Gateway Hardware Model** | `name` | Select | Current Model | `SUPPORTED_GATEWAY_MODELS` | In-place correction of gateway hardware model and active profile. |
| **Audio Source Names** | `source_name_1`..`4` | Text | `""` | 4 Matrix inputs | Custom labels for physical sound sources plugged into F441/F441M matrix inputs (S1–S4). |
| **Audio Source Tuner Flag** | `source_tuner_1`..`4` | Boolean | `False` | 4 Matrix inputs | Declares whether an input is an SCS radio tuner (enables RDS and frequency tuning commands). |
| **Audio Default Routing** | `source_default_<env>` | Select | `none` | Active audio environments | Per-environment default sound source assigned when turning on amplifiers. |
| **Proxy Decoder Entity** | `decoder_entity_1`..`4` | Entity (`media_player`) | `""` | 4 Decoder slots | External software audio player entity (e.g. Music Assistant, Squeezelite) mapped to matrix inputs. |
| **Proxy Decoder Source** | `decoder_source_1`..`4` | Select | Slot index | 1–4 | Matrix source input plugged into the external audio player's sound card / DAC. |
| **Proxy Decoder Pre-Gain** | `decoder_pre_gain_1`..`4` | Number | `0` | -20 dB to +20 dB | Gain trim compensation to balance volume levels across streaming sources and physical tuners. |
<!-- GATEWAY_OPTIONS_END -->

### 1. Command Worker Concurrency (`worker_count`)
- **Range**: `1` to `10` (Default: `1`).
- Dynamically capped and validated against the gateway model's hardware limit:
  - **1 worker**: MH200, MH200N, MH201, F452, F453AV, AM4890 / H4890 / LN4890, Legrand 3578 Serial, Generic.
  - **2 workers**: MH202.
  - **Up to 4 workers**: F454, F455, F461, MyHomeServer1.
- Prevents socket flooding and gateway CPU exhaustion while maximizing throughput on modern multi-session gateways.

### 2. Dimmer Transition Mode (`transition_mode`)
- `software_stepped` *(Default & Recommended)*: Home Assistant calculates and dispatches smooth 100-step brightness interpolation. Guarantees consistent fade behavior across all BTicino dimmer generations (F418, F41835, DALI interfaces).
- `native`: Passes the transition duration directly to the gateway as hardware speed parameters (`WHAT = 2`–`9`). Only supported if all physical dimmers support native hardware speed parameters.
- `auto`: Alias for `software_stepped`.

### 3. Event Bus Broadcasting (`generate_events`)
- Boolean switch (Default: `False`).
- When enabled, raw OpenWebNet bus telegrams are emitted onto Home Assistant's event bus as `myhome_message_event` events for custom automations.

### 4. Broadcast Re-sync (`broadcast_resync`)
- Boolean switch (Default: `True`).
- When enabled, detecting general (`WHERE = 0`) or room-wide broadcast commands automatically triggers targeted queries to keep individual entity states synchronized.

### 5. Multi-Room Audio Routing & Dynamic Proxy Decoders (`WHO = 16`)
- **Audio Source Names & Tuner Flags (`source_name_1`..`4`, `source_tuner_1`..`4`)**: Assign friendly names for the physical inputs on the F441/F441M audio matrix (e.g. "Living Room HiFi", "FM Tuner"). Flag tuner inputs so frequency and RDS commands are enabled.
- **Default Source per Environment (`source_default_<env>`)**: Declares which source input is selected when an amplifier in that environment is switched on.
- **Dynamic Proxy Decoders (`decoder_entity_1`..`4`, `decoder_source_1`..`4`, `decoder_pre_gain_1`..`4`)**: Map external software streaming players (e.g., Music Assistant, Squeezelite) to physical matrix inputs, with pre-gain calibration (-20 dB to +20 dB).

### 6. In-Place Gateway Reconfiguration
- You can update the gateway IP address (`address`), password (`own_password`), or hardware model (`name`) directly within the Options Flow without removing and re-adding devices or breaking entity IDs.

---

## 🛡️ Reliability & Watchdogs

The integration includes enterprise-grade connection reliability safeguards:

- **Active Keep-Alive**: Periodically transmits diagnostic ping frames (`*#13**0##` or `*#13**22##`) to prevent gateway NAT socket closure.
- **Backoff & Auto-Reconnect**: If a network glitch or gateway reboot occurs, the event and command workers automatically cycle through an exponential backoff reconnect loop.
- **Availability Grace Period**: An entity availability grace timer (60 seconds) prevents entities from rapidly toggling to `Unavailable` during brief gateway reconnections or WiFi dropouts.
- **Silent Reconnect Cycles**: The read cycle in which OWNd re-establishes the event socket produces no frame and is skipped at `DEBUG` level; `Event connection lost, reconnecting...` is OWNd's own log line and is normal on gateways that close idle sockets (MH200/MH201).
- **Profile-Gated Discovery**: The startup status requests (`*#2*0##`, `*#4*0##`, `*#16*0*5##`) are only sent for subsystems the gateway profile advertises.
- **Reauthentication**: A rejected OpenWebNet password raises `ConfigEntryAuthFailed`; Home Assistant shows *Reauthentication required* and opens the reauth flow. Other connection failures are retried with backoff (`ConfigEntryNotReady`).
- **Bus Monitor Tap**: Zero-overhead in-band packet tap that copies incoming and outgoing frames directly to the diagnostic Lovelace bus card without opening additional sockets.

See [Runtime Behaviour Notes](runtime_behaviour.md) for the reasoning behind each of these.

---

## 🕒 Gateway Timezone Configuration

OpenWebNet gateways manage an internal real-time clock (RTC) queried via WHO=13 dimension 0 (`*#13**0##`) or dimension 22 (`*#13**22##`). When the timezone has not been configured in the gateway's management interface, the gateway emits a placeholder sentinel value `999` in the timezone field (e.g. `*#13**0*<HH>*<MM>*<SS>*999##` or `*#13**22*...*999*...##`).

This placeholder can cause date and time parsing failures or dropped gateway diagnostic messages. When the integration detects this sentinel, it registers a Home Assistant Repair issue advising that the gateway requires configuration. (See also the [Wiki guide on Gateway Timezone Configuration](https://github.com/OpenWebNet-HA/MyHOME/wiki/Gateway-Timezone-Configuration)).

### How to resolve:
1. Log into the gateway's web administration interface, or open **MyHOME_Suite** / **TiMyHome** / **MyHOME_Up**.
2. Navigate to the **Date & Time** or **Clock** settings.
3. Configure the correct local time and timezone (or enable NTP synchronization if supported by your gateway).
4. Save the configuration and reboot or restart the gateway.

Once the gateway responds with a valid timezone offset, the repair issue automatically resolves and clears from your Home Assistant Repairs dashboard.

---

## 🔍 How the Gateway Model is Identified

The model label decides the gateway profile (command sessions, pacing, queue size, which subsystems are queried) and appears in the entry title, the device registry, diagnostics and every bus-monitor export — so it must be right, and it must say *how* it was established.

| Source | Meaning | Trust |
| :--- | :--- | :--- |
| `ssdp` | The gateway announced its own `modelName` over UPnP/SSDP | Authoritative |
| `serial` | USB/serial interface (Legrand 3578): model fixed by the transport | Authoritative |
| `manual` | You picked the model in the config flow | Trusted, but correctable by certain evidence |
| `who13` | No model was configured; labelled from the WHO=13 device-type reply | Best effort |

**WHO=13 dimension 15 ("MODEL REQUEST", `*#13**15*<code>##`)** is the only in-band identity signal. Its official table — BTicino *OpenWebNet_Community_2_device* v1.0.0, 13 June 2006, §1.2.6 — is complete at six entries: `2` MHServer, `4` MH200, `6` F452, `7` F452V, `11` MHServer2, `13` H4684. Every gateway sold since (F454, F455, MH200N, MH202, MyHOMEServer1…) is absent and reuses or invents codes, so the reply can **corroborate** an identity but never establish one for a modern gateway. Field evidence: code `200` is reported by both the F454 (#370) and MyHOMEServer1 (#292/#297), corroborating modern gateway models without uniquely identifying either.

Rules applied when the reply arrives:

- **Compatible model** (e.g. configured MH200 with code `4`, or configured F454 / MyHOMEServer1 with code `200`): consistent, nothing changes. A model the tables list by name must match by name or brand variant: an MH200N has a code of its own (`44`), so code `4` contradicts it. Only a variant suffix no table lists is compared by family and never downgraded.
- **`ssdp` / `serial` contradicted**: model kept; a repair issue *asks* you to confirm.
- **`manual` contradicted by an official code**: model, profile and device registry are corrected and a repair issue tells you (the old manual flow defaulted to F454, which is how mislabelled entries came to exist).
- **`manual` contradicted by an observed-only code**: model kept; a repair issue asks you to confirm.
- **No model configured**: labelled from an official code; ambiguous codes (such as `200`) do not auto-label and keep the gateway as generic.
- **Unknown code**: recorded, nothing changes — please attach a trace to an issue so the code can be documented.

Every diagnostics download and bus-monitor export carries an `identification` block: the model, its `source`, the raw `who13_code`, what the specification (`who13_model_official`) and field evidence (`who13_model_observed`) say it means, the `WHO=1013` reply when one was needed (`who1013_code`, `who1013_model`, and the `who1013_n_conf` / `who1013_brand` / `who1013_line` metadata that comes with it), firmware / kernel / distribution from dimensions 16 / 23 / 24, the active profile, and any `conflict`. A trace can therefore never hide a mislabelled gateway.

---

## 📦 Manual Installation Pitfalls

When installing a release `myhome.zip` by hand, the archive must be extracted **into** `/config/custom_components/myhome/` — never into `/config/custom_components/` itself:

```bash
unzip -q myhome.zip -d /config/custom_components/myhome     # correct
unzip -q myhome.zip -d /config/custom_components            # wrong
```

A stray `__init__.py` / `manifest.json` in the root of `custom_components` turns that folder into a regular Python package whose init is the integration code. On Home Assistant 2026.9+ the loader then imports **no custom integration at all** — every custom integration shows *Not loaded*, the bus-monitor card 404s, and nothing is logged at `warning` level.

Likewise keep backups **outside** `custom_components` (e.g. `/config/myhome_backup/`). A copy such as `custom_components/myhome_backup_2026…/` registers a second `myhome` domain: the loader logs *We found a custom integration myhome* twice and may load the backup instead of the real one (duplicate CEN units, stale code).

---

## 🔗 Multi-Gateway & Shared Bus Support

In complex installations, multiple OpenWebNet gateways may exist in Home Assistant under two primary architectures:

```text
                      +-------------------------------------------------+
                      |              HOME ASSISTANT CORE                |
                      |                                                 |
                      |   +-------------------+   +-----------------+   |
                      |   | Primary Entities  |   | Secondary Ent.  |   |
                      |   |{prim_mac}-WHO-ADDR|   |{sec_mac}-WHO-ADDR|   |
                      |   +---------^---------+   +--------^--------+   |
                      +-------------|----------------------|------------+
                                    |                      |
            +-----------------------+                      |
            |                       |                      |
   [Normal Operation]      [Failover Active]               |
            |                       |                      |
            v                       v                      v
    +---------------+       +---------------+      +---------------+
    |    PRIMARY    |       | WARM STANDBY  |      |   SECONDARY   |
    |    GATEWAY    |       |    GATEWAY    |      |    GATEWAY    |
    |  (F454 / etc) |       |  (MH202 / etc)|      | (Audio/Cover) |
    +-------+-------+       +-------+-------+      +-------+-------+
            |                       |                      |
            |     TX Echo Filter    | Inbound Bridging     | Delegated WHOs
            |     (1.5s window)     | (CEN, Events, Cmds)  | (e.g. WHO 2, 16)
            |                       |                      |
    ========+=======================+======================+========
                     PHYSICAL SCS BUS (Twisted Pair)
    ========+=======================+======================+========
            |                       |                      |
    +-------+-------+       +-------+-------+      +-------+-------+
    | Light Actuator|       | Shutter Switch|      | Audio F441    |
    | (WHO 1)       |       | (WHO 2)       |      | (WHO 16)      |
    +---------------+       +---------------+      +---------------+
```

### 1. Independent Bus Segments (standalone)
Each gateway is connected to its own separate physical SCS bus segment (for example, separate apartment units, outbuildings, or dedicated subsystems connected via galvanically isolated interfaces).
- **Behavior**: Every gateway independently discovers, polls, and creates entities.
- **Entity Unique IDs**: Scoped as {mac}-{who}-{where}, guaranteeing uniqueness across different gateways without conflicts.

### 2. Shared Bus (shared)
Two or more gateways are wired to the **same physical SCS wiring** (for example, a modern **MH201** handling general automation alongside a legacy **MH200N** running complex logic scenarios or a **3486** burglar alarm interface).

Without proper coordination on a shared bus:
- Both gateways observe the same bus traffic, causing duplicate Home Assistant entities for every physical light, cover, or thermostat.
- Startup discovery sweeps (*#2*0##, *#4*0##, etc.) sent simultaneously by multiple gateways collide on the SCS bus, triggering NACK storms and rate-limiting timeouts.
- Ambiguous service calls (such as myhome.sweep_bus) query all gateways redundantly.

#### Shared Bus Configuration:
- **Primary Gateway** (configure it first):
  - Set us_topology: shared and gateway_role: primary.
  - Performs active startup sweeps and entity discovery for every subsystem not delegated to a secondary.
  - Cannot leave the primary role while a secondary or standby still points at it.
- **Secondary Gateway (Subsystem Offloading)**:
  - Set us_topology: shared and gateway_role: secondary.
  - Select the **Primary Gateway** in the dropdown.
  - Active startup sweeps for non-delegated subsystems are automatically suppressed.
  - Automatic entity discovery on bus events is suppressed for non-delegated WHOs.
  - Any pre-existing duplicate secondary entities matching the primary gateway are pruned on startup.
  - Changing the role reloads the gateway once the options are saved.
- **Warm Standby Gateway (High Availability Failover)**:
  - Set us_topology: shared and gateway_role: standby.
  - Select the **Primary Gateway** in the dropdown.
  - Functions as a warm backup (e.g. an MH202 or secondary F454 standing by behind a main F454).
  - While the primary gateway is healthy, duplicate entity discovery and startup sweeps are suppressed.
  - **Transparent Failover**: If the primary gateway loses connection or becomes unresponsive:
    - Outbound commands and status polls are seamlessly dispatched via the standby gateway, and so are their replies.
    - Inbound bus frames received by the standby gateway are bridged to primary entities (only from the standby, so a secondary on the same bus does not deliver them twice).
    - Scenario control events (CEN WHO=15 and CEN+ WHO=25) are bridged with the primary's MAC address and entry ID to both the HA event bus and dispatcher listeners, allowing device triggers to fire transparently.
    - Once the outage outlasts the 60-second reconnect grace, a **Repair Issue** (gateway_failover_active) is raised alerting you to the offline primary unit while keeping your home fully functional.
    - When the primary gateway reconnects, Home Assistant automatically performs failback and clears the repair issue.

```text
+-------------------------------------------------------------------------------+
|                      STANDBY FAILOVER STATE MACHINE                            |
+-------------------------------------------------------------------------------+
|                                                                               |
|   +------------------------+                    +-------------------------+   |
|   |     PRIMARY ONLINE     |  Primary Session   |    FAILOVER ACTIVE      |   |
|   |                        |  Drops (> 60s)     |                         |   |
|   |  - Standby silent      | -----------------> |  - Outbound via Standby |   |
|   |  - No duplicate ent.   |                    |  - Inbound bridged to   |   |
|   |  - Primary routes cmds | <----------------- |    primary entities     |   |
|   +------------------------+   Primary Returns  |  - CEN events mapped    |   |
|                                                 |  - Repair issue raised  |   |
|                                                 +------------+------------+   |
|                                                              |                |
|                                                Standby Drops | Standby        |
|                                                Too           | Reconnects     |
|                                                              v                |
|                                                 +-------------------------+   |
|                                                 |   FULL BUS OUTAGE       |   |
|                                                 |  - Primary entities     |   |
|                                                 |    marked UNAVAILABLE   |   |
|                                                 +-------------------------+   |
+-------------------------------------------------------------------------------+
```

- **Delegated Subsystems**:
  - If the secondary gateway is a specialized unit (such as a 3486 for WHO=5 Burglar Alarm or an MH200N dedicated to WHO=16/22 Audio), select those subsystems under **Delegated Subsystems**.
  - New devices of a delegated subsystem are discovered by the secondary only; the primary stops sweeping and discovering that subsystem.
  - Devices the primary already had before the delegation stay on the primary, so no entity is renamed or loses its settings. To move one to the secondary, delete it from the primary gateway's device page; the secondary discovers it on its next bus frame.

#### Automated Topology & Subsystem Delegation Inference
Rather than requiring users to manually calculate subsystem overlaps and gateway tiers, Home Assistant automatically infers the optimal shared bus configuration based on gateway hardware models, performance tiers, and OpenWebNet command capabilities.

```text
                      +------------------------------------------+
                      |   Two Gateways on Shared Physical Bus    |
                      |          (Gateway A & Gateway B)         |
                      +--------------------+---------------------+
                                           |
                                           v
                      +------------------------------------------+
                      |   1. Compare Hardware Performance Tiers  |
                      |   - Tier 1: F454, F455, F461, MHS1       |
                      |   - Tier 2: MH201, MH202, H4890/Touch    |
                      |   - Tier 3: MH200N, MH200, F452, F453    |
                      +--------------------+---------------------+
                                           |
                                           v
                      +-----------------------------------------------+
                      |   Rank by (Tier ASC, WHO Count DESC, MAC ASC) |
                      |   Higher Tier / More WHOs    = PRIMARY        |
                      |   Remaining Gateway          = FOLLOWER       |
                      +-----------------------+-----------------------+
                                           |
                                           v
                      +------------------------------------------+
                      |   2. Compute Capability Delta Formula    |
                      |          Δ = S_follower \ S_primary      |
                      +--------------------+---------------------+
                                           |
                    +----------------------+----------------------+
                    |                                             |
            Δ = ∅ (Empty Delta)                         Δ ≠ ∅ (Subsystems in Δ)
                    |                                             |
                    v                                             v
     +------------------------------+             +-------------------------------+
     |   Assign ROLE_STANDBY        |             |   Assign ROLE_SECONDARY       |
     |   (Warm Standby HA Failover) |             |   (Subsystem Offloading)      |
     +--------------+---------------+             +---------------+---------------+
                    |                                             |
                    | - 0 duplicate entities                      | - Delegate WHOs in Δ
                    | - Suppress secondary sweeps                 | - Auto-couple WHO 16 & 22
                    | - Transparent failover on                   | - Primary stops sweeping
                    |   primary disconnect                        |   delegated subsystems
                    |                                             |
                    +----------------------+----------------------+
                                           |
                                           v
                      +------------------------------------------+
                      |   3. Automated Execution & Deployment    |
                      |   - 1-Click UI: SharedBusRepairFlow      |
                      |   - Options Flow: Smart Pre-population   |
                      +------------------------------------------+
```

##### Gateway Performance Tiers & Pacing
1. **Tier 1 (High Throughput / Multi-Session, $\le 50\text{ ms}$ pacing)**: `F454`, `F455`, `F461`, `MyHomeServer1`.
2. **Tier 2 (Linux / Touchscreen Gateways, $100\text{ ms}$ pacing)**: `MH201`, `MH202`, `H4890` / `AM4890` / `LN4890`.
3. **Tier 3 (Legacy Microcontroller Gateways, $150\text{ ms}$ pacing)**: `MH200N`, `MH200`, `F452`, `F453`.

When two gateways are paired on a shared bus, the integration ranks them by performance tier (Tier 1 > Tier 2 > Tier 3), supported OpenWebNet subsystem coverage (more supported WHO dimensions wins), and deterministic MAC address ordering (`pri_mac <= sec_mac`) as a final tie-breaker. The broader, more performant gateway is assigned as the **Primary**.

##### Capability Delta Formula
The follower gateway's role and delegated subsystems are calculated by evaluating the set difference of supported OpenWebNet subsystems ($S$):
$$\Delta = S_{\text{sec}} \setminus S_{\text{pri}}$$

- **Empty Delta ($\Delta = \emptyset$)**:
  - When the primary already covers all subsystems supported by the secondary (e.g. **F454 + MH202**), the secondary is assigned the **Warm Standby (`standby`)** role.
  - No duplicate entities are created, and the secondary transparently takes over bus communication if the primary fails.
- **Non-Empty Delta ($\Delta \neq \emptyset$)**:
  - When the secondary supports specialized subsystems absent from the primary (e.g. **MyHomeServer1 + H4890**, where H4890 provides Burglar Alarm `WHO=5`, Auxiliary `WHO=9`, and Sound Diffusion `WHO=16`/`22`), the secondary is assigned the **Secondary (`secondary`)** role with delegated subsystems $\Delta$.
  - In addition, audio subsystems (`WHO=16` Matrix and `WHO=22` Sound Diffusion) are automatically coupled so both route through the dedicated audio hardware.

##### Smart Defaults & 1-Click Repair
- **Options Flow**: Selecting `bus_topology: shared` and picking a Primary gateway dynamically pre-populates the inferred **Gateway Role** and **Delegated Subsystems** multi-select options.
- **Repair Flow**: When an unconfigured shared bus is detected via TX-to-RX echoes (`shared_bus_detected`), Home Assistant generates a 1-click repair issue displaying the inferred topology, assigned roles, and rationale. Submitting the repair dialog automatically applies the topology to both gateways and reloads them.

#### Automatic Shared Bus Detection
The integration passively compares the traffic of every pair of gateways that is not configured on the same bus. Due to false positives with external automation platforms, concurrent RX triggers are ignored. The only accepted evidence is a strict **TX-to-RX echo**:
- Gateway B receives a physical point-to-point frame on the bus that Gateway A transmitted less than 1.5 seconds prior (SHARED_BUS_TX_ECHO_S = 1.5).
- General lighting (WHERE=0), general automation (WHERE=0), area commands (WHERE starting with # or area codes), and group commands (WHERE=#0) are filtered out to prevent false correlations when automations trigger synchronized broadcast scenes across separate physical buses.
- Three correlated frames within an evidence window of 10 minutes (SHARED_BUS_EVIDENCE_WINDOW_S = 600.0) raise an actionable **Home Assistant Repair Issue** (shared_bus_detected), alerting you to configure the shared bus relationship. Gateway-local WHO=13/1013 frames are ignored. Configuring the pair on one bus dismisses the issue.

#### Multi-Gateway Logging & Diagnostic Traces
When commissioning or diagnosing multi-gateway installations, Home Assistant logs every step of the topology evaluation and failover lifecycle.

Enable debug logging in `configuration.yaml` or via the Home Assistant UI (**Settings → Devices & Services → MyHOME → ⋮ → Enable debug logging**):

```yaml
logger:
  logs:
    custom_components.myhome: debug
    custom_components.myhome.topology: debug
    custom_components.myhome.repairs: debug
    OWNd: debug
```

##### What to Look for in the Logs:
- **Topology Inference & Subsystem Delegation**:
  ```text
  DEBUG: Evaluating shared bus topology between F454 (Tier 1, WHOs [1, 2, 4, ...]) and MH202 (Tier 2, WHOs [1, 2, 4, ...]). Primary selection: F454 (Tier 1 < Tier 2). Secondary capability delta: [] (audio coupled: False)
  INFO: Inferred shared bus topology: Primary=F454 (00:03:50:aa:bb:01, Tier 1), Follower=MH202 (00:03:50:aa:bb:02, Tier 2, role=standby, delegated=[]). F454 (Tier 1) selected as Primary (Tier 1 < Tier 2). MH202 capabilities are fully covered by Primary; configured as Warm Standby for failover.
  ```
- **Smart Options Flow Pre-Population**:
  ```text
  DEBUG: Inferred shared-bus smart defaults for 00:03:50:aa:bb:02: role=standby, delegated_whos=[] (selected primary 00:03:50:aa:bb:01)
  ```
- **1-Click Repair Execution**:
  ```text
  INFO: Applied recommended shared bus topology via 1-click repair: Primary=F454 (00:03:50:aa:bb:01), Follower=MH202 (00:03:50:aa:bb:02, role=standby, delegated WHOs=[])
  ```
- **Shared-Bus Detection (TX-to-RX Echoes)**:
  ```text
  DEBUG: Recorded shared bus TX echo #1/3 between 00:03:50:aa:bb:01 and 00:03:50:aa:bb:02 (delta 24ms, frame *1*1*12##)
  ```
- **Delegated Command Routing**:
  ```text
  DEBUG: Routing delegated WHO 16 command *#16*1*0*1## from Primary (F454) to Secondary (H4890)
  ```
- **Warm Standby Failover & Inbound Bridging**:
  ```text
  WARNING: Primary gateway F454 event session offline (> 60s); engaging warm standby failover via MH202
  DEBUG: Bridging inbound bus frame *1*1*11## from standby MH202 to primary F454 entities
  ```

##### Opening an Issue on GitHub:
When reporting behavior relating to shared buses, failover, or inference:
1. Navigate to **Settings → Devices & Services → MyHOME**.
2. Click **⋮ → Download diagnostics** on both gateway cards. The downloaded JSON includes full `bus_topology`, `gateway_role`, `delegated_whos`, failover state, command queue pacing, and the rolling 500-frame buffer (with credentials redacted).
3. Attach both diagnostic JSON files to your GitHub issue.
