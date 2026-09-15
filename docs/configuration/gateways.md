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
- **Bus Monitor Tap**: Zero-overhead in-band packet tap that copies incoming and outgoing frames directly to the diagnostic Lovelace bus card without opening additional sockets.
