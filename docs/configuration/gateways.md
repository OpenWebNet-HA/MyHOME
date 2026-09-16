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

### Step 2: Connection Parameters
- **Host**: IP address of your gateway on the local network (e.g., `192.168.1.50`). A static IP or DHCP reservation is strongly advised.
- **Port**: Default is `20000` (standard OpenWebNet port).
- **Password**:
  - Leave blank if your gateway has authentication disabled (open LAN).
  - Enter your 4-digit or 9-digit numeric OpenWebNet password, or alphanumeric password configured in TiMyHome / MyHOME_Suite.
  - For **MyHomeServer1**, enter the installer OpenWebNet password configured in the MyHOME_Up app.

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

## 🪪 How the gateway model is identified

The model label decides the gateway profile (command sessions, pacing, queue size, which subsystems are queried) and appears in the entry title, the device registry, diagnostics and every bus-monitor export — so it must be right, and it must say *how* it was established.

| Source | Meaning | Trust |
| :--- | :--- | :--- |
| `ssdp` | the gateway announced its own `modelName` over UPnP/SSDP | authoritative |
| `serial` | USB/serial interface (Legrand 3578): model fixed by the transport | authoritative |
| `manual` | you picked the model in the config flow | trusted, but correctable by certain evidence |
| `who13` | no model was configured; labelled from the WHO=13 device-type reply | best effort |

**WHO=13 dimension 15 ("MODEL REQUEST", `*#13**15*<code>##`)** is the only in-band identity signal. Its official table — BTicino *OpenWebNet_Community_2_device* v1.0.0, 13 June 2006, §1.2.6 — is complete at six entries: `2` MHServer, `4` MH200, `6` F452, `7` F452V, `11` MHServer2, `13` H4684. Every gateway sold since (F454, F455, MH200N, MH202, MyHOMEServer1…) is absent and reuses or invents codes, so the reply can **corroborate** an identity but never establish one for a modern gateway. Field evidence collected so far: code `200` on a self-identified MyHOMEServer1 (#292/#297).

Rules applied when the reply arrives:

- **Same family** (e.g. configured MH200N, code `4` = MH200): consistent, nothing changes. A variant suffix is never downgraded.
- **`ssdp` / `serial` contradicted**: model kept; a repair issue *asks* you to confirm.
- **`manual` contradicted by an official code**: model, profile and device registry are corrected and a repair issue tells you (the old manual flow defaulted to F454, which is how mislabelled entries came to exist).
- **`manual` contradicted by an observed-only code**: model kept; a repair issue asks you to confirm.
- **No model configured**: labelled from the code (official first, then observed); the entry records `model_source: who13`.
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
