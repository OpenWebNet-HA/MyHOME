# Gateways & Connection Architecture (`WHO = 13`)

This guide details the network connection, authentication, and resilience architecture for Legrand / BTicino OpenWebNet gateways in Home Assistant.

---

## 🏛️ Supported Gateway Hardware

The MyHOME integration communicates with SCS bus gateways over TCP/IP or RS232/USB serial:

| Gateway Model | Connection Type | Auth Mechanism | Notes |
| :--- | :---: | :---: | :--- |
| **F454** | Ethernet (TCP `20000`) | Numeric / Alphanumeric / None | Modular IP Web Server. Full WHO support. |
| **MyHomeServer1 (MHS1)** | Ethernet (TCP `20000`) | HMAC-SHA1 / HMAC-SHA256 | Next-gen Linux gateway. Strict session handshake. |
| **MH200N / MH201 / MH202** | Ethernet (TCP `20000`) | Numeric / Alphanumeric | Scenario programmers with embedded OpenWebNet gateway. |
| **F452 / F453AV** | Ethernet (TCP `20000`) | Numeric / None | Audio/Video web servers. |
| **BTicino 3578** | USB / RS232 Serial | None (Hardware bus interface) | Direct serial connection without IP overhead. |

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

## ⚙️ Gateway Options Flow

You can customize runtime behavior by clicking **Configure** on the gateway integration card:

### 1. Worker Count (`CONF_WORKER_COUNT`)
- Range: `1` to `10` (Default: `1`).
- Defines how many simultaneous command workers can talk to the gateway.
- **Recommendation**: Keep at `1` or `2` for older gateways (MH200N, F452) to avoid saturating their limited CPU. Can be raised to `3`–`4` for F454 and MyHomeServer1.

### 2. Transition Mode (`CONF_TRANSITION_MODE`)
- `software_stepped` *(Default & Recommended)*: Home Assistant drives smooth software stepped transitions. Guarantees consistent fade behavior across all BTicino dimmer generations.
- `native`: Passes the transition duration directly to the gateway as hardware speed parameters (`WHAT = 2`–`9`). Only supported if all your physical dimmers (e.g., F41835) support native hardware speed parameters.
- `auto`: Alias for `software_stepped`.

### 3. Generate Events (`CONF_GENERATE_EVENTS`)
- Boolean switch (Default: `False`).
- When enabled, raw bus telegrams are emitted onto Home Assistant's event bus as `myhome_message_event` events.

---

## 🛡️ Reliability & Watchdogs

The integration includes enterprise-grade connection reliability safeguards:

- **Active Keep-Alive**: Periodically transmits diagnostic ping frames (`*#13**0##` or `*#13**22##`) to prevent gateway NAT socket closure.
- **Backoff & Auto-Reconnect**: If a network glitch or gateway reboot occurs, the event and command workers automatically cycle through an exponential backoff reconnect loop.
- **Availability Grace Period**: An entity availability grace timer (60 seconds) prevents entities from rapidly toggling to `Unavailable` during brief gateway reconnections or WiFi dropouts.
- **Silent Reconnect Cycles**: the read cycle in which OWNd re-establishes the event socket produces no frame and is skipped at `DEBUG` level; `Event connection lost, reconnecting...` is OWNd's own log line and is normal on gateways that close idle sockets (MH200/MH201).
- **Profile-Gated Discovery**: the startup status requests (`*#2*0##`, `*#4*0##`, `*#16*0*5##`) are only sent for subsystems the gateway profile advertises.
- **Reauthentication**: a rejected OpenWebNet password raises `ConfigEntryAuthFailed`; Home Assistant shows *Reauthentication required* and opens the reauth flow. Other connection failures are retried with backoff (`ConfigEntryNotReady`).

See [Runtime Behaviour Notes](runtime_behaviour.md) for the reasoning behind each of these.

---

## Gateway Timezone Configuration

OpenWebNet gateways manage an internal real-time clock (RTC) queried via WHO=13 dimension 0 (`*#13**0##`) or dimension 22 (`*#13**22##`). When the timezone has not been configured in the gateway's management interface, the gateway emits a placeholder sentinel value `999` in the timezone field (e.g. `*#13**0*<HH>*<MM>*<SS>*999##` or `*#13**22*...*999*...##`).

This placeholder can cause date and time parsing failures or dropped gateway diagnostic messages. When the integration detects this sentinel, it registers a Home Assistant Repair issue advising that the gateway requires configuration. (See also the [Wiki guide on Gateway Timezone Configuration](https://github.com/OpenWebNet-HA/MyHOME/wiki/Gateway-Timezone-Configuration)).

### How to resolve:
1. Log into the gateway's web administration interface, or open **MyHOME_Suite** / **TiMyHome** / **MyHOME_Up**.
2. Navigate to the **Date & Time** or **Clock** settings.
3. Configure the correct local time and timezone (or enable NTP synchronization if supported by your gateway).
4. Save the configuration and reboot or restart the gateway.

Once the gateway responds with a valid timezone offset, the repair issue automatically resolves and clears from your Home Assistant Repairs dashboard.

---

## How the gateway model is identified

The model label decides the gateway profile (command sessions, pacing, queue size, which subsystems are queried) and appears in the entry title, the device registry, diagnostics and every bus-monitor export — so it must be right, and it must say *how* it was established.

| Source | Meaning | Trust |
| :--- | :--- | :--- |
| `ssdp` | the gateway announced its own `modelName` over UPnP/SSDP | authoritative |
| `serial` | USB/serial interface (Legrand 3578): model fixed by the transport | authoritative |
| `manual` | you picked the model in the config flow | trusted, but correctable by certain evidence |
| `who13` | no model was configured; labelled from the WHO=13 device-type reply | best effort |

**WHO=13 dimension 15 ("MODEL REQUEST", `*#13**15*<code>##`)** is the only in-band identity signal. Its official table — BTicino *OpenWebNet_Community_2_device* v1.0.0, 13 June 2006, §1.2.6 — is complete at six entries: `2` MHServer, `4` MH200, `6` F452, `7` F452V, `11` MHServer2, `13` H4684. Every gateway sold since (F454, F455, MH200N, MH202, MyHOMEServer1…) is absent and reuses or invents codes, so the reply can **corroborate** an identity but never establish one for a modern gateway. Field evidence: code `200` is reported by both the F454 (#370) and MyHOMEServer1 (#292/#297), corroborating modern gateway models without uniquely identifying either.

Rules applied when the reply arrives:

- **Compatible model** (e.g. configured MH200 with code `4`, or configured F454 / MyHOMEServer1 with code `200`): consistent, nothing changes. A model the tables list by name must match by name or brand variant: an MH200N has a code of its own (`44`), so code `4` contradicts it. Only a variant suffix no table lists is compared by family and never downgraded.
- **`ssdp` / `serial` contradicted**: model kept; a repair issue *asks* you to confirm.
- **`manual` contradicted by an official code**: model, profile and device registry are corrected and a repair issue tells you (the old manual flow defaulted to F454, which is how mislabelled entries came to exist).
- **`manual` contradicted by an observed-only code**: model kept; a repair issue asks you to confirm.
- **No model configured**: labelled from an official code; ambiguous codes (such as `200`) do not auto-label and keep the gateway as generic.
- **Unknown code**: recorded, nothing changes — please attach a trace to an issue so the code can be documented.

Every diagnostics download and bus-monitor export carries an `identification` block: the model, its `source`, the raw `who13_code`, what the specification (`who13_model_official`) and field evidence (`who13_model_observed`) say it means, the `WHO=1013` reply when one was needed (`who1013_code`, `who1013_model`, and the `who1013_n_conf` / `who1013_brand` / `who1013_line` metadata that comes with it), firmware / kernel / distribution from dimensions 16 / 23 / 24, the active profile, and any `conflict`. A trace can therefore never hide a mislabelled gateway.

## 📦 Manual Installation Pitfalls

When installing a release `myhome.zip` by hand, the archive must be extracted **into** `/config/custom_components/myhome/` — never into `/config/custom_components/` itself:

```bash
unzip -q myhome.zip -d /config/custom_components/myhome     # correct
unzip -q myhome.zip -d /config/custom_components            # wrong
```

A stray `__init__.py` / `manifest.json` in the root of `custom_components` turns that folder into a regular Python package whose init is the integration code. On Home Assistant 2026.9+ the loader then imports **no custom integration at all** — every custom integration shows *Not loaded*, the bus-monitor card 404s, and nothing is logged at `warning` level.

Likewise keep backups **outside** `custom_components` (e.g. `/config/myhome_backup/`). A copy such as `custom_components/myhome_backup_2026…/` registers a second `myhome` domain: the loader logs *We found a custom integration myhome* twice and may load the backup instead of the real one (duplicate CEN units, stale code).

- **Bus Monitor Tap**: Zero-overhead in-band packet tap that copies incoming and outgoing frames directly to the diagnostic Lovelace bus card without opening additional sockets.

---

## ⚙️ Runtime Options Flow Parameters

You can adjust integration runtime parameters at any time without re-adding the gateway:

1. Navigate to **Settings → Devices & Services → MyHOME**.
2. Click **Configure** on the gateway integration card.

| Option | Key | Type | Default | Description |
| :--- | :--- | :---: | :---: | :--- |
| **Command Worker Concurrency** | `worker_count` | Integer (1–4) | `1` | Number of concurrent asynchronous command workers. Set to `1` on single-session scenario programmers (MH200/MH200N) to prevent command collision; can be increased to `2`–`4` on modern multi-session gateways (F454, MHS1). |
| **Dimmer Transition Mode** | `transition_mode` | Select | `software_stepped` | `software_stepped` (smooth 100-step software interpolation managed by Home Assistant) vs `native` (hardware fade execution on F418 modules). |
| **Event Bus Broadcasting** | `generate_events` | Boolean | `False` | Emits raw bus frames as `myhome_message_event` events to the Home Assistant global event bus for custom automations. |
| **Broadcast Re-sync** | `broadcast_resync` | Boolean | `True` | Automatically triggers a targeted query when general/area broadcast commands (`WHERE = 0` or area addresses) are detected on the bus to keep individual entity states synchronized. |
| **Dynamic Proxy Decoders** | `decoders` | Mapping | None | Maps external software audio players (e.g. Music Assistant, Squeezelite) to physical F441 audio matrix source inputs for Diffusione Sonora (`WHO = 16`). |
| **Bus Topology** | `bus_topology` | Select | `standalone` | Topology of the gateway: `standalone` (independent bus segment) or `shared` (multiple gateways wired to the same physical SCS bus). |
| **Gateway Role** | `gateway_role` | Select | `primary` | Role on a shared bus: `primary` (owns active discovery, polling, and general entities), `secondary` (subsystem offloading, suppresses duplicate entities), or `standby` (warm backup for high availability failover). |
| **Primary Gateway** | `primary_gateway` | Select | None | When configured as `secondary` or `standby`, selects the primary gateway entry for duplicate entity suppression and failover coordination. The selected gateway must itself be `shared` / `primary`. |
| **Delegated Subsystems** | `delegated_whos` | Multi-select | None | Subsystems (`WHO` codes) explicitly delegated to this secondary gateway (e.g. WHO=5 Burglar Alarm or WHO=16 Audio). |

---

## 🔗 Multi-Gateway & Shared Bus Support

In complex installations, multiple OpenWebNet gateways may exist in Home Assistant under two primary architectures:

### 1. Independent Bus Segments (`standalone`)
Each gateway is connected to its own separate physical SCS bus segment (for example, separate apartment units, outbuildings, or dedicated subsystems connected via galvanically isolated interfaces).
- **Behavior**: Every gateway independently discovers, polls, and creates entities.
- **Entity Unique IDs**: Scoped as `{mac}-{who}-{where}`, guaranteeing uniqueness across different gateways without conflicts.

### 2. Shared Bus (`shared`)
Two or more gateways are wired to the **same physical SCS wiring** (for example, a modern **MH201** handling general automation alongside a legacy **MH200N** running complex logic scenarios or a **3486** burglar alarm interface).

Without proper coordination on a shared bus:
- Both gateways observe the same bus traffic, causing duplicate Home Assistant entities for every physical light, cover, or thermostat.
- Startup discovery sweeps (`*#2*0##`, `*#4*0##`, etc.) sent simultaneously by multiple gateways collide on the SCS bus, triggering NACK storms and rate-limiting timeouts.
- Ambiguous service calls (such as `myhome.sweep_bus`) query all gateways redundantly.

#### Shared Bus Configuration:
- **Primary Gateway** (configure it first):
  - Set `bus_topology: shared` and `gateway_role: primary`.
  - Performs active startup sweeps and entity discovery for every subsystem not delegated to a secondary.
  - Cannot leave the primary role while a secondary or standby still points at it.
- **Secondary Gateway (Subsystem Offloading)**:
  - Set `bus_topology: shared` and `gateway_role: secondary`.
  - Select the **Primary Gateway** in the dropdown.
  - Active startup sweeps for non-delegated subsystems are automatically suppressed.
  - Automatic entity discovery on bus events is suppressed for non-delegated WHOs.
  - Any pre-existing duplicate secondary entities matching the primary gateway are pruned on startup.
  - Changing the role reloads the gateway once the options are saved.
- **Warm Standby Gateway (High Availability Failover)**:
  - Set `bus_topology: shared` and `gateway_role: standby`.
  - Select the **Primary Gateway** in the dropdown.
  - Functions as a warm backup (e.g. an MH202 or secondary F454 standing by behind a main F454).
  - While the primary gateway is healthy, duplicate entity discovery and startup sweeps are suppressed.
  - **Transparent Failover**: If the primary gateway loses connection or becomes unresponsive:
    - Outbound commands and status polls are seamlessly dispatched via the standby gateway, and so are their replies.
    - Inbound bus frames received by the standby gateway are bridged to primary entities (only from the standby, so a secondary on the same bus does not deliver them twice).
    - Once the outage outlasts the 60-second reconnect grace, a **Repair Issue** (`gateway_failover_active`) is raised alerting you to the offline primary unit while keeping your home fully functional.
    - When the primary gateway reconnects, Home Assistant automatically performs failback and clears the repair issue.
- **Delegated Subsystems**:
  - If the secondary gateway is a specialized unit (such as a 3486 for WHO=5 Burglar Alarm or an MH200N dedicated to WHO=16/22 Audio), select those subsystems under **Delegated Subsystems**.
  - New devices of a delegated subsystem are discovered by the secondary only; the primary stops sweeping and discovering that subsystem.
  - Devices the primary already had before the delegation stay on the primary, so no entity is renamed or loses its settings. To move one to the secondary, delete it from the primary gateway's device page; the secondary discovers it on its next bus frame.

#### Automatic Shared Bus Detection
The integration passively compares the traffic of every pair of gateways that is not configured on the same bus:
- If Gateway B receives a bus frame that Gateway A transmitted within 1.5 seconds (TX-to-RX echo), or
- If Gateway A and Gateway B receive the exact same physical frame concurrently within 0.3 seconds,
the integration records evidence. Upon 3 correlated frames within 10 minutes, an actionable **Home Assistant Repair Issue** (`shared_bus_detected`) is raised, alerting you to configure the shared bus relationship. A frame a gateway transmitted itself is never evidence, so identical commands sent to two separate buses do not count. The dual-gateway traces of #453 (F454 + MH202) show the same frame on both gateways 4-47 ms apart.


