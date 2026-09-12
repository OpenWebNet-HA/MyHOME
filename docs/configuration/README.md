# MyHOME Integration Configuration Guides

Welcome to the configuration documentation for the **MyHOME Home Assistant Integration** (v2.0 Beta / Phase 1 Architecture).

This directory provides comprehensive, step-by-step guides for connecting, configuring, and automating your Legrand / BTicino MyHOME SCS bus installation using Home Assistant.

> [!NOTE]
> **Branch & Architecture Note**: This documentation reflects the next-generation **v2.0 architecture** (`v2-phase1-architecture`).
> In v2.0, setup and device management are **100% UI-first** through Home Assistant's native Config Flow and Options Flow.

---

## 📚 Configuration Guides Directory

| Guide | Target Subsystem / Feature | OpenWebNet WHO | Key Topics Covered |
| :--- | :--- | :---: | :--- |
| [**Gateways & Connection Setup**](gateways.md) | Gateway Setup & Network | `WHO = 13` | IP gateways (F454, MyHomeServer1, MH200N/201/202), serial interfaces (3578), OpenWebNet password & HMAC authentication, connection resilience, keep-alive, and worker pool sizing. |
| [**Sound System / Media Player**](media_player.md) | Diffusione Sonora | `WHO = 16` | F441/F441M hardware analog audio matrix, Dynamic Proxy for Music Assistant / Spotify, decoder pool management, gain staging (anti-hiss), and physical wall-panel routing. |
| [**CEN & CEN+ Device Triggers**](cen_cenplus.md) | Scenario Pushbuttons | `WHO = 15`, `WHO = 25` | Native Home Assistant UI Device Triggers, physical button numbers 0–31, short press, long press, release, rotary dials, and automation blueprints. |
| [**Lovelace Bus Monitor Card**](bus_monitor.md) | In-Band Diagnostic Monitor | All WHOs | Native Lovelace card (`custom:myhome-bus-card`), 500-frame circular ring buffer, real-time live streaming, WHO filtering, 1-click **Sweep Bus**, and 1-click **Export Trace**. |
| [**Lovelace Dashboard Recipes**](lovelace_recipes.md) | UI & Dashboard Showcase | All WHOs | Dynamic auto-collapsing active lights, multiroom audio player cards, perimeter security status, and equipment runtime tracker. |
| [**Integration Services Reference**](services.md) | Integration Actions | All WHOs | Reference for `myhome.send_message`, `myhome.turn_on_timed` (hardware SCS timers), `myhome.sync_time`, `myhome.start_sending_instant_power`, and `myhome.sweep_bus`. |

---

## 🚀 Quick Setup Overview

### 1. Add Integration via UI
1. Navigate to **Settings** -> **Devices & Services** -> **Add Integration**.
2. Search for **MyHOME**.
3. Enter your gateway IP address (or serial port), port (default `20000`), and OpenWebNet password (if configured).
4. Select your gateway model (e.g., `MyHomeServer1`, `F454`, `MH201`).

### 2. Auto-Discovery
Once the gateway is added:
- The integration connects to the gateway event session (`*99*1##`) and command session (`*99*0##`).
- Existing configured entities (lights, covers, climate probes, switches) are discovered and created automatically.
- Diagnostic entities (reconnection counters, latency, firmware version, uptime) are exposed under the Gateway device.

### 3. Adjust Options
Access **Configure** on the integration card to fine-tune:
- **Worker Concurrency**: Number of asynchronous command workers (default: `1`).
- **Dimmer Transition Mode**: `software_stepped` (reliable smooth software stepping) vs. `native` hardware fade.
- **Dynamic Proxy Decoders**: Map network audio decoders (Music Assistant, Squeezelite) to physical F441 matrix inputs.
- **Event Bus Broadcasting**: Toggle whether raw bus events are published to the global Home Assistant event bus (`myhome_event`).
