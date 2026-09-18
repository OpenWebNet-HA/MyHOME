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
- When enabled, raw bus telegrams are emitted onto Home Assistant's event bus under the `myhome_event` topic.

---

## 🛡️ Reliability & Watchdogs

The integration includes enterprise-grade connection reliability safeguards:

- **Active Keep-Alive**: Periodically transmits diagnostic ping frames (`*#13**0##` or `*#13**22##`) to prevent gateway NAT socket closure.
- **Backoff & Auto-Reconnect**: If a network glitch or gateway reboot occurs, the event and command workers automatically cycle through an exponential backoff reconnect loop.
- **Availability Grace Period**: An entity availability grace timer (60 seconds) prevents entities from rapidly toggling to `Unavailable` during brief gateway reconnections or WiFi dropouts.
- **Silent Reconnect Cycles**: the read cycle in which OWNd re-establishes the event socket produces no frame and is skipped at `DEBUG` level; `Event connection lost, reconnecting...` is OWNd's own log line and is normal on gateways that close idle sockets (MH200/MH201).
- **Profile-Gated Discovery**: the startup status requests (`*#2*0##`, `*#4*0##`, `*#16*0##`) are only sent for subsystems the gateway profile advertises, so an MH200N is never asked for audio it does not have.
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

- **Compatible model** (e.g. configured MH200N with code `4` = MH200, or configured F454 / MyHOMEServer1 with code `200`): consistent, nothing changes. A variant suffix is never downgraded.
- **`ssdp` / `serial` contradicted**: model kept; a repair issue *asks* you to confirm.
- **`manual` contradicted by an official code**: model, profile and device registry are corrected and a repair issue tells you (the old manual flow defaulted to F454, which is how mislabelled entries came to exist).
- **`manual` contradicted by an observed-only code**: model kept; a repair issue asks you to confirm.
- **No model configured**: labelled from an official code; ambiguous codes (such as `200`) do not auto-label and keep the gateway as generic.
- **Unknown code**: recorded, nothing changes — please attach a trace to an issue so the code can be documented.

Every diagnostics download and bus-monitor export carries an `identification` block: the model, its `source`, the raw `who13_code`, what the specification (`who13_model_official`) and field evidence (`who13_model_observed`) say it means, firmware / kernel / distribution from dimensions 16 / 23 / 24, the active profile, and any `conflict`. A trace can therefore never hide a mislabelled gateway.

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
| **Event Bus Broadcasting** | `generate_events` | Boolean | `True` | Emits raw bus frames as `myhome_event` events to the Home Assistant global event bus for custom automations. |
| **Broadcast Re-sync** | `broadcast_resync` | Boolean | `True` | Automatically triggers a targeted query when general/area broadcast commands (`WHERE = 0` or area addresses) are detected on the bus to keep individual entity states synchronized. |
| **Dynamic Proxy Decoders** | `decoders` | Mapping | None | Maps external software audio players (e.g. Music Assistant, Squeezelite) to physical F441 audio matrix source inputs for Diffusione Sonora (`WHO = 16`). |

