# MyHOME — OpenWebNet Integration for Home Assistant

[![Validate with hassfest](https://github.com/OpenWebNet-HA/MyHOME/actions/workflows/hassfest.yml/badge.svg)](https://github.com/OpenWebNet-HA/MyHOME/actions/workflows/hassfest.yml)
[![HACS Validation](https://github.com/OpenWebNet-HA/MyHOME/actions/workflows/validate.yml/badge.svg)](https://github.com/OpenWebNet-HA/MyHOME/actions/workflows/validate.yml)
[![test-coverage](https://github.com/OpenWebNet-HA/MyHOME/actions/workflows/test-coverage.yaml/badge.svg)](https://github.com/OpenWebNet-HA/MyHOME/actions/workflows/test-coverage.yaml)
[![Coverage](coverage.svg)](https://app.codecov.io/gh/OpenWebNet-HA/MyHOME/tree/v2-phase2-architecture)
[![Integration Quality Scale](https://github.com/OpenWebNet-HA/MyHOME/actions/workflows/quality-scale.yml/badge.svg)](https://github.com/OpenWebNet-HA/MyHOME/actions/workflows/quality-scale.yml)
[![Quality scale tier](quality_scale.svg)](custom_components/myhome/quality_scale.yaml)
[![Codecov](https://codecov.io/gh/OpenWebNet-HA/MyHOME/branch/v2-phase2-architecture/graph/badge.svg)](https://app.codecov.io/gh/OpenWebNet-HA/MyHOME/tree/v2-phase2-architecture)
[![PyPI Standards & Packaging](https://github.com/OpenWebNet-HA/MyHOME/actions/workflows/pypi_standards.yml/badge.svg?branch=v2-phase2-architecture)](https://github.com/OpenWebNet-HA/MyHOME/actions/workflows/pypi_standards.yml?query=branch%3Av2-phase2-architecture)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Latest Release](https://img.shields.io/github/v/release/OpenWebNet-HA/MyHOME?include_prereleases&label=release&logo=github)](https://github.com/OpenWebNet-HA/MyHOME/releases)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)
[![mypy](https://img.shields.io/badge/mypy-strict-blue.svg)](https://mypy.readthedocs.io/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Documentation](https://img.shields.io/badge/Docs-openwebnet--ha.github.io%2FMyHOME-blue.svg)](https://openwebnet-ha.github.io/MyHOME/beta/)
[![Discussions](https://img.shields.io/badge/Discussions-Join-blue?logo=github)](https://github.com/OpenWebNet-HA/MyHOME/discussions)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

Modern, async-native Home Assistant integration for **BTicino / Legrand MyHOME** SCS bus systems connected via OpenWebNet IP gateways.

Maintained by the **[OpenWebNet-HA](https://github.com/OpenWebNet-HA)** community organisation.

[📦 Installation](#-installation) • [🏛️ Supported Hardware](#️-supported-hardware) • [📚 Documentation](https://openwebnet-ha.github.io/MyHOME/beta/) • [💬 Discussions](https://github.com/OpenWebNet-HA/MyHOME/discussions) • [🤝 Contributing](CONTRIBUTING.md) • [🔒 Security](SECURITY.md)

> [!TIP]
> **🚀 V2 Phase 2 Architecture Now Live**: Phase 2 architecture is active across **OWNd** and **MyHOME**! Featuring strongly typed CEN / CEN+ scenario command builders and device triggers (**P2**), Thermoregulation Central Unit (3550 / 4695) master mode and zone coordination (**P4**), Multi-Gateway routing and physical plant isolation (**P6**), DALI Tunable White support, and 100.0% test coverage verified against the OpenWebNet Golden Corpus.

---

## 🌟 Key Features & Modern V2 Architecture

- **Strongly Typed CEN / CEN+ Device Triggers & Addressing (P2)**: Native Home Assistant UI device triggers for scenario buttons with string-preserved addressing (`"0001"`, `"01"`, `"15"`), enriched event payloads (`where`, `gateway_mac`, `entry_id`), and all 8 press/release/held actions without requiring external YAML blueprints.
- **Native Hardware Bus Light & Switch Timers (`WHO=1`)**: Hardware-offloaded countdown timers executed directly on Legrand DIN actuators (F411, etc.) via `myhome.turn_on_timed` or native `timer`/`duration` parameters in `light.turn_on` and `switch.turn_on`. Supports standard Legrand preset codes (0.5s, 30s, 1m, 2m, 3m, 4m, 5m, 15m) and custom Dimension 2 (`*#1*WHERE*#2*H*M*S##`) durations that turn off automatically even if Home Assistant restarts.
- **Real-World Gateway Trace Replay Fixtures in CI (P5)**: Automated pytest fixture engine (`tests/test_trace_replay.py`) replaying frozen on-wire bus captures from production gateways directly against the integration state machine, enabling deterministic bug reproduction and permanent regression defense for community beta testers without requiring physical hardware.
- **Thermoregulation Central Unit Coordination (P4)**: Dedicated master coordination for 99-zone Central Unit (`#0`, model `Central Unit (3550)`) and 4-zone Central Unit (`#0#1`, model `Central Unit (4695)`). Master Heating/Cooling switches (`*4*3xx*#0##`) propagate across internal dispatchers to subordinate zones (`standalone=False`), automatically synchronizing whole-home climate operations with physical central units.
- **Multi-Gateway Routing & Plant Isolation (P6)**: Namespaced event dispatchers (`f"myhome_cen_event_{mac}"`, `f"myhome_central_mode_{mac}"`) and device trigger filtering by parent gateway MAC (`via_device`), eliminating cross-talk and phantom triggers across physical plants combining multiple gateways (e.g. F454 + MH200N / MH201).
- **DALI Tunable White & Color Temperature**: Native support for DALI DT8 ballasts (F429 / F461 gateways) with auto-detection of color temperature (`ColorMode.COLOR_TEMP`, 2000K–6535K / mireds), seamless Kelvin/mireds conversion, and sentinel filtering.
- **Declarative Hardware Profiles**: Auto-detects and tunes connection limits and queue pacing specifically for your gateway model (`MH200`, `MH200N`, `MH202`, `F454`, `F455`, `AM4890`, `MyHomeServer1`, and `Legrand 3578`). Eliminates hardware session exhaustion and buffer overflows.
- **USB / Serial Gateway & OpenZigBee Support**: Native asynchronous transport for the **Legrand 3578 USB/Serial interface** via `pyserial-asyncio` with dynamic port discovery, authentication bypass, and OpenZigBee addressing (`<8-digit id>#9`).
- **Zero-Friction Migration**: Upgrades preserve all existing custom entity IDs (`light.keuken`, `cover.living`) and friendly names. Unique IDs migrate transparently (`MAC-WHERE` → `MAC-WHO-WHERE`) with no broken dashboards or automations.
- **Adaptive Inter-Frame Bus Pacing**: Hardened priority command queue with model-specific inter-frame delays (e.g. 150ms for legacy MH200 vs 20ms for F454) preventing command dropping during heavy automation bursts.
- **Dynamic Bus Auto-Discovery**: Automatically discovers entities from physical bus events and status sweeps without requiring manual `myhome.yaml` configuration. Full support for **F422 cross-bus routing** (e.g. `18#4#02`).
- **Sound System 2.0 & Audio Matrix (WHO=16)**: Complete multi-room audio support for F441 / F441M matrices and amplifiers, including zone power, volume normalization (0–31 scale), software mute emulation, and dynamic streaming proxy.
- **Streaming Audio Dynamic Proxy**: Seamlessly stream from **Music Assistant**, **Spotify Connect**, or any HA media player to wired BTicino audio zones using a thread-safe `DecoderPool` with analog gain-staging.
- **Dimmable Light Detection**: Auto-detects dimming capabilities directly from bus events with transition support.
- **OpenWebNet Golden Corpus Conformance**: Automated unit tests maintaining strict 100.0% line and branch coverage across all component modules, verified against multi-authority real-world captures across 11 OpenWebNet subsystems.

---

## 📚 Documentation & OpenWebNet Protocol Specifications (Wiki)

We now maintain a comprehensive, community-curated **[GitHub Wiki](https://github.com/OpenWebNet-HA/MyHOME/wiki/OpenWebNet-Protocol-&-WHO-Specifications)** and **[Master Specifications Registry](docs/openwebnet-who-specifications.md)** documenting the OpenWebNet protocol, hardware profiles, and WHO subsystem specifications:

👉 **[OpenWebNet Protocol & WHO Specifications Wiki](https://github.com/OpenWebNet-HA/MyHOME/wiki/OpenWebNet-Protocol-&-WHO-Specifications)**  
👉 **[Official Legrand Developer Portal (PDF Documentation)](https://developer.legrand.com/local-interoperability/#PDF%20documentation)**  
👉 **[Master Document Archive (15 Specifications — PR #232)](https://github.com/user-attachments/files/32008617/OWN.DOC.zip)**

### Key Wiki Resources & Current Status
- **[WHO Specifications Archive & Status Matrix](https://github.com/OpenWebNet-HA/MyHOME/wiki/OpenWebNet-Protocol-&-WHO-Specifications#openwebnet-who-specifications-matrix)**: Complete catalog of all OpenWebNet WHO families (WHO 0 to WHO 1004, HMAC authentication, and core system intro) with official PDF documentation references, current implementation status, and frame syntax.
- **[CEN / CEN+ Automations & Community Blueprint](https://community.home-assistant.io/t/myhome-cen-commands/260345)**: Community blueprint by **gST84** to trigger actions, toggle non-BTicino smart devices, and dim lights from physical MyHOME pushbuttons.
- **[Hardware Gateway Profiles](https://github.com/OpenWebNet-HA/MyHOME/wiki/Gateway-Profiles)**: Deep dive into connection constraints, socket limits, pacing delays, and watchdog behaviors for MH200, MH200N, MH202, F454, F455, MyHomeServer1, and Legrand 3578.
- **[Sound System 2.0 & Audio Matrix Guide](https://github.com/OpenWebNet-HA/MyHOME/wiki/Sound-System-2.0-&-Audio-Matrix)**: Setup instructions for F441/F441M matrices, room amplifier calibration, and Dynamic Proxy streaming.
- **[Bus Monitor Lovelace Card](https://github.com/OpenWebNet-HA/MyHOME/wiki/Bus-Monitor-Lovelace-Card)**: Bus card installation, live frame decoding, diagnostic logging, and syntax injector reference.
- **[Community Contribution Guide](https://github.com/OpenWebNet-HA/MyHOME/wiki/OpenWebNet-Protocol-&-WHO-Specifications#how-to-contribute-specifications)**: How to cross-check documentation versions and contribute missing WHO PDF specifications.

### In-repo guides (`docs/configuration/`)
- **[Supported Functions](docs/configuration/supported_functions.md)** — what each WHO subsystem and platform does, read-only or not at all.
- **[Known Limitations](docs/configuration/known_limitations.md)** — what is not supported, why, and the workaround.
- **[Troubleshooting](docs/configuration/troubleshooting.md)** — symptoms → log lines / bus frames → fix.
- **[Use Cases](docs/configuration/use_cases.md)** — end-to-end scenarios with the automations that make them work.
- **[Services](docs/configuration/services.md)**, **[Gateways](docs/configuration/gateways.md)**, **[Runtime Behaviour](docs/configuration/runtime_behaviour.md)**, **[Bus Monitor](docs/configuration/bus_monitor.md)**, **[Sound System](docs/configuration/media_player.md)**, **[CEN / CEN+](docs/configuration/cen_cenplus.md)**, **[Lovelace Recipes](docs/configuration/lovelace_recipes.md)**.

---

## 🏛️ Supported Hardware

### Gateway Profiles

| Gateway Model | Protocol Support | Max Command Workers | Inter-Frame Delay | UPnP Discovery | Notes |
|---|---|---|---|---|---|
| **F454** | OpenWebNet / HMAC | 4 workers | 20 ms | ✅ Port 49153 | Full high-speed multi-session support |
| **F455** | OpenWebNet / HMAC | 4 workers | 20 ms | ✅ Port 49153 | Basic gateway (single SCS bus) |
| **F461** | OpenWebNet / HMAC | 4 workers | 20 ms | ❌ Manual | Compact DIN Ethernet Web Server |
| **MH202** | OpenWebNet / HMAC | 3 workers | 30 ms | ✅ Port 49153 | Modern scenario programmer gateway |
| **MH201** | OpenWebNet | 2 workers | 60 ms | ✅ Port 49153 | Second-generation scenario programmer |
| **MyHomeServer1** | OpenWebNet / HMAC | 4 workers | 20 ms | ✅ SSDP | Cloud/local hybrid gateway |
| **MH200N** | OpenWebNet | 2 workers | 80 ms | ❌ Manual | Second-generation scenario programmer |
| **MH200** *(Legacy)* | OpenWebNet | 1 worker | 150 ms | ❌ Manual | Strict single-session pacing; watchdog hardened |
| **H4890 / AM4890** | OpenWebNet | 2 workers | 100 ms | ❌ Manual | 3.5" Touch screen display IP gateway (Axolute / Livinglight) |
| **F452 / F453AV** | OpenWebNet | 2 workers | 50 ms | ✅ Port 49153 | Audio/video & web server gateway |
| **HL4684** | OpenWebNet | 2 workers | 80 ms | ✅ SSDP | 10" Touch screen display IP gateway |
| **Legrand 3578** | OpenWebNet (Serial) | 2 workers | 50 ms | ❌ Manual (Serial) | USB / Serial gateway & OpenZigBee interface |


### 📊 Hardware Trace Availability Matrix

<!-- TRACE_MATRIX_START -->
| Gateway Model | WHO 0<br>Scenario | WHO 1<br>Lights | WHO 2<br>Autom. | WHO 4<br>Climate | WHO 5<br>Alarm | WHO 9<br>Power | WHO 13<br>Gateway | WHO 14<br>Lock | WHO 15<br>CEN | WHO 16<br>Audio | WHO 17<br>Scenario | WHO 18<br>Energy | WHO 22<br>Audio Diff. | WHO 25<br>Diag | WHO 1013<br>Diag | WHO 1022<br>Diag |
| :--- |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |
| **F454** |  | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |  |  | ✅ |  |  |  |  | ✅ |  |
| **F461** |  | ✅ |  | ✅ |  |  |  |  |  |  |  |  |  |  |  |  |
| **H4890 / AM4890** |  | ✅ | ✅ |  | ✅ | ✅ |  |  |  | ✅ |  | ✅ | ✅ | ✅ |  |  |
| **MH200** |  | ✅ | ✅ |  | ✅ |  | ✅ |  |  | ✅ |  |  |  |  |  |  |
| **MH200N** | ✅ | ✅ | ✅ | ✅ |  |  | ✅ | ✅ | ✅ |  | ✅ | ✅ |  |  |  | ✅ |
| **MH201** | ✅ | ✅ | ✅ | ✅ |  |  | ✅ | ✅ | ✅ | ✅ |  | ✅ |  | ✅ |  |  |
| **MH202** |  |  | ✅ | ✅ |  |  | ✅ |  |  |  |  |  |  |  | ✅ |  |
| **MyHomeServer1** |  | ✅ | ✅ | ✅ |  | ✅ | ✅ | ✅ |  | ✅ |  | ✅ |  |  | ✅ |  |
<!-- TRACE_MATRIX_END -->

*Checkmarks (✅) indicate that at least one `diagnostic_summary.json` or `.txt` bus capture in our test corpus contains frames for that subsystem from the specified gateway model. This matrix is automatically updated from the fixtures repository.*

### Supported Entity Domains & Automations

<!-- SUPPORTED_DOMAINS_START -->
| Domain | WHO | Capabilities |
|---|---|---|
| **`light`** | WHO=1 | On/Off, Dimmers with brightness control & transitions (stepped & native), DALI DT8 Tunable White (Dimension 14, 2000K–6535K), HS/RGB colour, Hardware-offloaded bus timers (`myhome.turn_on_timed` / `timer` parameter) |
| **`switch`** | WHO=1 | Relays, auxiliary switches, socket actuators (switch/outlet device classes), Hardware-offloaded bus timers (`myhome.turn_on_timed` / `timer` parameter) |
| **`cover`** | WHO=2 | Motorized shutters, blinds, roll-ups with state tracking, position-reporting actuators & virtual travel-time positioning |
| **`climate`** | WHO=4 | Heating, cooling, 4-pipe systems, thermostats, setpoints, fancoil 3-speed modes, offset tracking, Central Unit 3550 (`#0`) & 4695 (`#0#1`) master coordination & seasonal propagation |
| **`alarm_control_panel`** | WHO=5 | Central units (3485/3486), partitions, arm away/home, disarm, panic trigger, zone 0 broadcast sync |
| **`binary_sensor`** | WHO=1 / 9 / 25 | Magnetic contacts, door/window sensors, PIR motion, AUX channels (1–9), dry contacts (F482/3477), inverted contacts |
| **`sensor`** | WHO=1 / 4 / 18 | Power meters, energy counters (total/daily/monthly), temperature probes (3475), illuminance / lux sensors |
| **`button`** | WHO=14 / 2 | Hardware actuator lock/unlock for lights, switches & covers (WHO=14), cover travel time calibration buttons (per cover & gateway-wide, WHO=2) |
| **`media_player`** | WHO=16 | F441/F441M audio zones, source tracking, volume normalization, software mute, streaming dynamic proxy (Music Assistant / Spotify Connect) |
| **`device_trigger`** *(Automations)* | WHO=15 / 25 | Stateless CEN & CEN+ scenario pushbuttons with string-preserved addressing (`"0001"`), gateway MAC isolation, and 8 native UI trigger types (short press, long press start, held, release, rotary dials) |
<!-- SUPPORTED_DOMAINS_END -->

*This table is automatically updated from platform definitions and [`supported_functions.md`](docs/configuration/supported_functions.md).*

---

## 📦 Installation & Updating

> **Requires Home Assistant 2026.3 or newer** (Python 3.14 cores). Older cores stay on 2.0.0b12; see [Known Limitations](docs/configuration/known_limitations.md).

> [!CAUTION]
> **⚠️ Never store backup copies inside `/config/custom_components/` (e.g. `myhome.backup`)!**  
> Home Assistant automatically discovers **all** subdirectories containing `manifest.json` under `/config/custom_components/`. If you create a backup folder like `/config/custom_components/myhome.backup` or rename the old directory in place:  
> 1. Home Assistant registers `custom_components.myhome.backup` as the integration module path for domain `myhome`.  
> 2. Python treats dots (`.`) as module delimiters, attempting to load `backup.py` from `custom_components.myhome`, which does not exist.  
> 3. Home Assistant startup fails with:  
>    `Setup failed for custom integration 'myhome': Unable to import component: No module named 'custom_components.myhome.backup'`  
> 
> **Rule:** Always keep safety backups **outside** the `custom_components/` folder (e.g. in `/config/myhome_backup/`).

> [!WARNING]
> **⚠️ Do NOT use HACS to install beta / pre-release versions!**  
> In **HACS 2.0+**, pre-release access was moved to Home Assistant entity switches (`switch.myhome_pre_release`) that are disabled by default. Due to upstream Home Assistant registry caching, enabling these switches frequently gets stuck in an *"unavailable"* loop or reverts to *"disabled"*. Furthermore, because pre-releases are built on the active development branch (`v2-phase1-architecture`) while the default branch is `master`, HACS download validation frequently fails with:  
> `The version 2.0.0b13 for this integration can not be used with HACS`  
> 
> **To avoid frustration, please use Method 1 (Terminal & SSH) or Method 2 (Manual) below — they take less than 10 seconds and preserve all existing devices, entities, and settings 100% safely.**

---

### Method 1: One-Liner via Terminal & SSH Add-on (⭐ Strongly Recommended)

If you have the **Terminal & SSH** add-on enabled in Home Assistant, open **Terminal** from the sidebar and paste this command (press **`Ctrl + Shift + V`** / **`Shift + Ctrl + V`** or right-click to paste into the web terminal):

```bash
cd /config/custom_components
# Move any legacy in-place backup out of custom_components to prevent loader crashes:
[ -d myhome.backup ] && mv myhome.backup /config/myhome_backup_old
# Create a safety backup in /config (outside custom_components) before updating:
[ -d myhome ] && rm -rf /config/myhome_backup && cp -r myhome /config/myhome_backup
# Download and install the latest v2.0.0b13 release:
rm -rf myhome
wget -O myhome_beta.zip https://github.com/OpenWebNet-HA/MyHOME/releases/download/2.0.0b13/myhome.zip
unzip -q myhome_beta.zip -d myhome
rm myhome_beta.zip
ha core restart
```

*(For **Home Assistant Container / Docker**, run on your Docker host:)*
```bash
docker exec -it homeassistant bash -c 'cd /config/custom_components && [ -d myhome.backup ] && mv myhome.backup /config/myhome_backup_old; [ -d myhome ] && rm -rf /config/myhome_backup && cp -r myhome /config/myhome_backup; rm -rf myhome && wget -O myhome_beta.zip https://github.com/OpenWebNet-HA/MyHOME/releases/download/2.0.0b13/myhome.zip && unzip -q myhome_beta.zip -d myhome && rm myhome_beta.zip'
docker restart homeassistant
```

> [!NOTE]
> All existing entity names, custom entity IDs, and gateway configurations are preserved automatically.

---

### Method 2: Manual Installation (Archive / Samba)

1. Download the release package:  
   👉 **[Download myhome.zip (v2.0.0b13)](https://github.com/OpenWebNet-HA/MyHOME/releases/download/2.0.0b13/myhome.zip)** (or browse all [GitHub Releases](https://github.com/OpenWebNet-HA/MyHOME/releases))
2. Open your Home Assistant configuration directory (via **Samba Share**, **Studio Code Server**, or **File Editor** add-on).
3. **Important:** If you wish to back up your existing `myhome` folder first, copy it to `/config/myhome_backup/` (**outside** `custom_components/`). **Never rename or copy it to `custom_components/myhome.backup`.**
4. Extract `myhome.zip` directly into `/config/custom_components/myhome/` (overwriting the existing files).
5. Restart Home Assistant (**Settings → System → Restart**).

---

### Method 3: HACS (Not Recommended for Betas — Stable / Reference Only)

*(Available seamlessly once PR #232 is merged into master for the stable `v2.0.0` release)*

#### Step 1: Add the Organization Repository
1. Open **HACS** in your Home Assistant UI.
2. Click the **three dots (`⋮`)** in the top-right corner and select **Custom repositories**.
3. Add the repository details:
   - **Repository:** `https://github.com/OpenWebNet-HA/MyHOME`
   - **Type / Category:** `Integration`
4. Click **Add**.

> [!CAUTION]
> **Migrating from a personal fork? DO NOT delete the MyHOME integration from Home Assistant Settings!**
> Deleting the integration from *Settings → Devices & Services* will wipe all configured gateways and devices.
> If you previously tracked a personal fork (such as `GreenGrassBlueOcean/MyHOME` or `anotherjulien/MyHOME`):
> 1. Open **HACS → ⋮ → Custom repositories**.
> 2. Click the **red trash can icon** next to the old fork URL to unlink it.
> 3. Verify `OpenWebNet-HA/MyHOME` is present in the list.
> 4. All your configured devices, gateways, and automations remain 100% intact.

#### Step 2: Download & Restart
1. Open **HACS → Integrations → MyHome**.
2. Click the blue **Download** button (or `⋮` → **Redownload**).
3. Select the version and click **Download**.
4. Restart Home Assistant (**Settings → System → Restart**).

---

### 🩹 Troubleshooting: "No module named 'custom_components.myhome.backup'"

> More symptoms and fixes: [Troubleshooting guide](docs/configuration/troubleshooting.md).

If Home Assistant fails to load with the log error:
```text
Setup failed for custom integration 'myhome': Unable to import component: No module named 'custom_components.myhome.backup'
```
This is caused by a backup folder (`myhome.backup`) residing inside `/config/custom_components/`. Fix it by running:
```bash
mv /config/custom_components/myhome.backup /config/myhome_backup
ha core restart
```

---

### 🔄 Safe Rollback

If you ever need to revert to the legacy codebase (`0.9.4`):
- **Via HACS:** Open **MyHome** → click `⋮` → **Redownload** → select **`0.9.4`** → **Download** → Restart Home Assistant.
- **Via Terminal & SSH** *(use `Ctrl + Shift + V` to paste)*:
  ```bash
  cd /config/custom_components
  wget https://github.com/OpenWebNet-HA/MyHOME/releases/download/0.9.4/myhome.zip -O myhome_legacy.zip
  rm -rf myhome
  unzip -q myhome_legacy.zip -d myhome
  rm myhome_legacy.zip
  ha core restart
  ```

### 🗑️ Removing the Integration

1. Go to **Settings → Devices & services → MyHOME**, open the gateway entry's `⋮` menu and choose **Delete**. Repeat for every configured gateway. This closes the bus sessions, unloads all platforms and removes the gateway's devices and entities from the registries.
2. Restart Home Assistant if you also want to remove the code:
   - **HACS:** open **MyHome** in HACS → `⋮` → **Remove**.
   - **Manual / one-liner installs:** delete the folder:
     ```bash
     rm -rf /config/custom_components/myhome
     ha core restart
     ```
3. Optional clean-up the integration does not touch on its own:
   - `/config/myhome.yaml` — the legacy platform configuration file, if you used one.
   - **Settings → Dashboards → Resources**: the auto-registered `/myhome_static/myhome-bus-card.js` resource, and any `custom:myhome-openwebnet-bus-monitor` cards on your dashboards.
   - Automations and blueprints that reference `myhome.*` services or the `myhome_*` events (`myhome_cen_event`, `myhome_cenplus_event`, `myhome_message_event`, `myhome_cover_calibration`, …).

The gateway itself is not modified by installing or removing the integration; nothing needs to be reset on the OpenWebNet side.

---

## ⚙️ Configuration

### Adding the Gateway

1. Navigate to **Settings → Devices & Services → Add Integration**.
2. Search for **MyHOME**.
3. Choose your gateway type:
   - **Network Gateway (TCP/IP)**:
     - **Auto-Discovery**: The integration automatically discovers UPnP/SSDP-compatible gateways on your local subnet (e.g. F454, MH202, MyHomeServer1). Discovered gateways appear in the Home Assistant UI with standard **Configure** and **Ignore** options, requiring explicit user confirmation before any config entry is created.
     - **Manual IP Setup**: For gateways without UPnP (e.g. MH200), enter the gateway IP address, port (default `20000`), MAC address, and OpenWebNet password (default `12345`).
   - **USB / Serial Gateway (Legrand 3578 / OpenZigBee)**:
     - Select your physical serial device (e.g. `/dev/ttyUSB0` or `COM3`) from the dynamically populated port picker.
     - Select your baud rate (default `19200`).
     - Serial transport operates with zero authentication overhead (no IP password challenge needed) and natively routes OpenZigBee addresses (`<8-digit id>#9`).
4. Select or confirm your gateway hardware profile from the dropdown.

### Options Flow (Fine-Tuning)

Go to **Settings → Devices & Services → MyHOME → Configure** to fine-tune your installation:
- **Gateway Address & Password**: Update the gateway IP address or OpenWebNet password without recreating the integration.
- **Command Worker Count**: Adjust concurrent command sessions (1 to 10 workers, default 1).
- **Generate Bus Events (`myhome_message_event`)**: Enable firing raw OpenWebNet messages directly to the Home Assistant event bus for custom monitoring and blueprint automations.
- **Sweep group/area/general light addresses for status**: Enabled by default. After a group, area or general lighting command, the gateway is given a short (~250 ms) debounce window to echo each member's own status before the integration sweeps the group/area itself; disable this if your gateway needs a different cadence (see [Broadcast re-sync](docs/configuration/runtime_behaviour.md#-broadcast-re-sync-group--area--general)).
- **Light Transition Mode**: Select how brightness transitions are handled:
  - `software_stepped` *(Default & Recommended)*: Smooth 0.3s stepped fades interpolated in software, compatible with all MyHOME dimmers.
  - `native`: Passes through the OpenWebNet hardware speed parameter directly (for supported hardware dimmers).
- **Audio Decoders Pool**: Map network media players (Music Assistant, Spotify Connect, WiiM, Squeezelite) to physical matrix inputs 1–4 with per-source analog pre-gain offsets (0–50%).

---

### 📄 YAML Configuration & Zero-Friction Migration (`myhome.yaml`)

While the integration features **Dynamic Bus Auto-Discovery** that discovers devices automatically from bus events, existing configurations from older versions are 100% supported:

1. **Automatic Search Order**: The integration automatically locates your configuration file in:
   1. `/config/myhome.yaml` *(Standard HA config directory)*
   2. `/config/myhome/myhome.yaml`
   3. Custom component directory fallback
2. **Single & Multi-Gateway Syntax**:
   - Single gateway installations do not require a root MAC header; platforms are mapped automatically to your gateway.
   - Multi-gateway installations group platforms under their respective MAC addresses (`00:03:50:xx:xx:xx`).

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  light:
    living_light:
      where: '11'
      name: Living Room Light
      dimmable: True
  cover:
    kitchen_shutter:
      where: '21'
      name: Kitchen Shutter
      travel_time: 22
  alarm_control_panel:
    central_alarm:
      where: '0'
      name: Central Alarm
```

3. **How names work** (Home Assistant's device / entity model): `name` names the **device** on the bus. A light, switch, cover, thermostat, audio zone or alarm panel *is* its device, so its entity carries the device name (`light.living_room_light`, friendly name *Living Room Light*). Sensors and binary sensors are features of their device and are named after their device class — a power meter named `House` gives `sensor.house_power` (*House Power*) and `sensor.house_energy`; a dry contact named `Cancello` with `class: opening` gives `binary_sensor.cancello_opening` (*Cancello Opening*). Use `entity_name` on a sensor or binary sensor to name the feature yourself (`entity_name: Contact` → *Front Door Contact*); an `entity_name` equal to `name` means "the entity is the device". Lock/unlock and calibration buttons are named *Lock*, *Unlock*, *Calibrate travel time* under their device. Entity ids are assigned once by the entity registry: **existing installations keep every entity id and every name you set in the UI**, and deleting the integration by accident is safe — Home Assistant keeps the registry entries for 30 days and restores names, areas and ids when the gateway is added again.

4. **DALI DT8 capabilities and `lock_features`**: a light learns dimming, tunable white (Dimension 14) and HSV colour (Dimension 12) from the bus as the frames arrive. BTicino DALI gateways (F429 / F461) remember any HSV or colour-temperature value that was ever written to an address - even to a fixture that cannot use it - and replay it on every status sweep, so a plain dimmer can end up with a colour wheel. Declare what the fixture really is and lock it:

```yaml
  light:
    rgbw_spot:
      where: '25'
      interface: '02'
      name: RGBW Spot
      dimmable: true
      color_temp: true      # Dimension 14 tunable white
      rgb: true             # Dimension 12 HSV colour (alias: hs)
      lock_features: true   # exactly these modes, never learn another one
    hallway_relay:
      where: '26'
      interface: '02'
      name: Hallway
      lock_features: true   # on/off only, whatever the gateway replays
```

Without `lock_features` the three flags are only the starting point and auto-detection stays on. Uncommissioned sentinels (`*12*511*127*255##`, `*14*1##`) are filtered by the protocol layer and never promote a light, locked or not.

**What a locked light does with a frame it is locked out of.** The frame is dropped as a whole - not just the colour, the HSV *value* (`*12*H*S*V##`) too. A dimension the gateway replays to an address that cannot use it carries no truth in any field: the value is whatever was once written, not the current level. Brightness is never affected by this, because it always arrives on Dimension 1 (`*#1*WHERE*1*<level>*<speed>##`), and a light declared with `rgb` or `color_temp` is implicitly dimmable. Every dropped frame is written to the log at `DEBUG` level - enable `custom_components.myhome: debug` in the logger configuration if a locked light does not follow the app the way you expect:

```text
GATEWAY light 26#4#02 is locked to ['onoff']; ignoring Dimension 12 frame *#1*26#4#02*12*353*74*80##
```

5. **Declared lighting groups (P7, #368)**: a `#G` `WHERE` (`#1` through `#255`) declares a group that **already exists in your plant** (configured with MyHOME_Suite or a physical group-programmed actuator) - it does not configure group membership on the bus, and it is not a second kind of "group" competing with Home Assistant's own `light.group`. Use it when you want a single OpenWebNet frame (`*1*1*#G##`, or `*#1*#G*#14*<mireds>##` for a DALI colour-temperature change) to reach every actuator programmed into that group at once:

```yaml
  light:
    living_room_group:
      where: '#6'
      name: Living Room Group
      dimmable: true
      color_temp: true
```

Without `members` the entity is `assumed_state`: Home Assistant shows separate On / Off controls instead of a toggle, because the integration has no way to know the group's actual state - only what was last sent to it. Add `members` (the point-to-point `WHERE` of each actuator in the group) to derive real state instead, the same way core's `light.group` averages its members' brightness / colour temperature / HS colour:

```yaml
  light:
    living_room_group:
      where: '#6'
      name: Living Room Group
      dimmable: true
      color_temp: true
      members: ['12', '13', '0114']   # each member's own light WHERE
```

`members` never sends a single extra frame - it only tells the integration which existing light entities to watch. A group with `members` still accepts direct control (single-frame `*1*1*#6##`); it also picks up a group dimension write the gateway echoes back (`*#1*#6*#14*153##`, the DALI case from #300). Never an auto-discovered entity for a group, area or general address (#368) - only a declared one.

---

### ⚡ Custom Services

The integration registers three specialized services under the `myhome` domain:

| Service | Fields | Description |
|---|---|---|
| **`myhome.send_message`** | `gateway` *(optional)*<br>`message` *(required)* | Send an arbitrary, validated OpenWebNet frame (e.g. `*1*0*0##`) directly to the SCS bus. Useful for scripts, custom diagnostic probes, and testing. |
| **`myhome.sync_time`** | `gateway` *(optional)* | Synchronizes the gateway's internal real-time clock with Home Assistant's local time using standard OpenWebNet date/time frames (WHO=13). |
| **`myhome.start_sending_instant_power`** | `entity_id` *(required)*<br>`duration` *(required)* | Requests high-frequency instant active power telemetry (W) from energy management counters (WHO=18) for `duration` seconds. |

---

### 🔔 Event Bus Automation Triggers

The integration fires native events to the Home Assistant event bus for automation triggers:

* **`myhome_cen_event` & `myhome_cenplus_event`**: Pushbutton events from physical CEN (`WHO=15`) and CEN+ (`WHO=25`) scenario controllers. Event payload includes:
  - `object`: Scenario button unit number
  - `pushbutton`: Pushbutton index (0–31)
  - `event`: Trigger action (`pushbutton_short_press`, `pushbutton_short_release`, `pushbutton_long_press`, `pushbutton_long_press_repeat` (CEN+, every ~0.5 s while held), `pushbutton_long_release`, or rotary dial `rotary_cw_slow`, `rotary_cw_fast`, `rotary_ccw_slow`, `rotary_ccw_fast`)
* **`myhome_alarm_event`**: State transitions emitted by burglar alarm systems (WHO=5), including partition `where`, `state`, `state_code`, and `is_alarm` flag.
* **Broadcast Subsystem Events**: Global and area broadcast commands are mirrored as:
  - `myhome_general_light_event`, `myhome_area_light_event`, `myhome_group_light_event`
  - `myhome_general_automation_event`, `myhome_area_automation_event`, `myhome_group_automation_event`
* **`myhome_message_event`**: When `Generate Bus Events` is enabled in Options Flow, every valid OpenWebNet message received from the gateway is broadcast to the event bus with `gateway` and raw frame `message`.

---

## 🎵 Multi-Room Audio & Dynamic Proxy

The BTicino sound system matrix (F441 / F441M) is an analog matrix switch. It routes physical source inputs (IN 1–4) to amplified room zones.

This integration includes a **Dynamic Proxy** that lets you stream IP audio (via Music Assistant, Spotify Connect, AirPlay, etc.) directly to your wired BTicino zones.

### Hardware Routing Architecture

```
┌────────────────────────┐      ┌─────────────────────────┐      ┌─────────────────────────┐
│     Media Source       │      │       DecoderPool       │      │      F441M Matrix       │
│  (Music Assistant /    │─────▶│  - claims idle decoder  │─────▶│   (Hardware Routing)    │
│   Spotify Connect)     │      │  - gain staging (clean) │      │                         │
│                        │      │  - activates zone (O/I) │      │   IN 1 ────▶ Living     │
└────────────────────────┘      └─────────────────────────┘      │   IN 2 ────▶ Kitchen    │
                                             ▲                   │   IN 3 ────▶ Bedroom    │
                                             │                   └─────────────────────────┘
                                  ┌──────────┴──────────┐                     ▲
                                  │   Network Decoders   │                     │
                                  │                      │                     │
                                  │  Decoder 1 (Wiim)    │────── RCA ──────────┘ (IN 1)
                                  │  Decoder 2 (HiFiDAC) │────── RCA ──────────┘ (IN 2)
                                  └──────────────────────┘
```

### Setting Up Streaming

1. Wire your network streamer (e.g. Raspberry Pi running squeezelite, WiiM, Cambridge Audio) to one of the matrix inputs (e.g. Source 1 or 2).
2. In Home Assistant, ensure the streamer is available as a `media_player` entity.
3. Open **MyHOME Options** (`Configure`), navigate to **Decoders**, and specify:
   - **Entity**: The streamer's `media_player` entity ID.
   - **Source**: The physical matrix input number (1–4) it is plugged into.
   - **Pre-Gain**: Analog offset percentage (recommended `15–20%` for line-level DACs, `0%` for fixed pre-amps).
4. Send audio from Music Assistant or Spotify to your BTicino zone entity:
   - The proxy automatically claims the decoder, wakes it, applies gain staging, and activates the zone.
   - When playback stops, the decoder is released back to the pool.

---

## 📡 Real-Time Bus Monitor & Diagnostics

The integration includes an in-band real-time bus monitor operating over the existing gateway event stream with zero extra socket connections:

### Lovelace Bus Monitor Card (`<myhome-openwebnet-bus-monitor>`)

<p align="center">
  <img src="docs/images/myhome-bus-card.jpg" alt="MyHOME OpenWebNet Bus Monitor Lovelace Card" width="750">
</p>

A modern custom Lovelace element is automatically registered with zero configuration:

- **Visual Card Picker & GUI Editor**: Fully integrated with Home Assistant's card picker — simply search for **"MyHOME OpenWebNet Bus Monitor"** (or search **"MyHOME"** / **"OpenWebNet"**) under `+ Add Card` and configure the title or buffer size visually without touching raw YAML (YAML type `custom:myhome-openwebnet-bus-monitor`, with `custom:myhome-bus-card` supported as a backward-compatible alias).
- **Live Bus Stream**: High-performance scrolling feed with color-coded badges for subsystems (Lighting `WHO=1`, Automation `WHO=2`, Climate `WHO=4`, Sound `WHO=16`, Energy `WHO=18`, CEN `WHO=15/25`) and ACK (`*#*1##`) / NACK (`*#*0##`) highlighting.
- **Interactive Controls**: Live Pause/Resume, buffer clearing, and instant filtering by subsystem, WHERE address, and Direction (RX/TX).
- **Manual Frame Injector**: Send raw OpenWebNet diagnostic frames directly to the bus with syntax validation.
- **One-Click Diagnostic Bug Reporter**: Click **"📋 Copy Diagnostic Report"** to copy a sanitized, GitHub-ready Markdown bundle containing:
  - Home Assistant Core & integration versions
  - Hardware gateway profile, firmware, connection type, queue pacing, and worker counts
  - Live buffer depth and RX/TX counters
  - Collapsible OpenWebNet bus trace (`<details><summary>OpenWebNet Bus Trace</summary>`)
  - Direct link opening pre-filled GitHub Issue Forms!

> [!TIP]
> **Troubleshooting: Card not showing up or "Custom element doesn't exist"?**
> 
> 1. **Manual Resource Verification**: While the integration automatically registers the card resource, you can verify or manually add it under **Settings ➔ Dashboards ➔ Resources** (click the three dots ⋮ in the top-right corner):
>    - **URL:** `/myhome_static/myhome-bus-card.js`
>    - **Resource Type:** `JavaScript Module`
> 2. **Check for Conflicting HACS Cards**: If custom cards fail to load or the card picker spins indefinitely, inspect your browser console (`F12`). A common cause is conflicting or duplicate custom cards (e.g. having both `scheduler-card` and `lovelace-standalone-schedule-card` installed simultaneously). An uncaught `CustomElementRegistry` collision in an earlier card halts the browser's Lovelace resource-loading pipeline before subsequent cards can initialize. Removing the duplicate card resolves the blockage immediately.
> 3. **Hard Browser Refresh**: After adding resources or updating components, perform a hard refresh (`Ctrl + F5` or `Ctrl + Shift + R`) to ensure the browser loads the latest JavaScript bundle from the gateway.

### 📝 Structured GitHub Issue Forms

When reporting issues or requesting new device support on GitHub, interactive forms ensure complete diagnostics:
- **Bug Report**: Gateway profile dropdown, connection type, HA version, diagnostics JSON attachment, and pre-formatted bus trace.
- **Device Support Request**: Structured form for adding new BTicino/Legrand modular components with WHO codes and frame samples.

### 🔍 Native Home Assistant Diagnostics

In addition to the real-time bus monitor card, the integration implements Home Assistant's native diagnostic provider (`diagnostics.py`).
To download a sanitized diagnostic bundle:
1. Navigate to **Settings → Devices & Services → MyHOME**.
2. Click the **three dots (`⋮`)** next to your gateway and select **Download diagnostics**.
3. All sensitive credentials, IP addresses, and tokens are automatically redacted via `CONF_PASSWORD` and `CONF_HOST` anonymizers before being saved to JSON.

### 🧪 Real-World Gateway Trace Replay & Community Issue Reproduction (P5)

A major CI infrastructure enhancement introduced for beta testing is the **Trace Replay Engine** (`tests/test_trace_replay.py`), enabling deterministic bug reproduction and permanent regression defense:

```
┌──────────────────────────────────────┐
│  Beta Tester's Real Plant            │
│  (F454, MyHomeServer1, MH202, etc.)  │
└──────────────────┬───────────────────┘
                   │
                   │ 1-Click "📋 Copy Capture" in Bus Monitor Card
                   ▼
┌──────────────────────────────────────┐
│  diagnostic_summary.json             │
│  (100 frozen on-wire frames + config)│
└──────────────────┬───────────────────┘
                   │
                   │ Saved to tests/fixtures/plants/<issue_id>/
                   ▼
┌──────────────────────────────────────┐
│  Automated Pytest Replay Engine      │
│  - Replays 100% of frames in order   │
│  - Reproduces bug deterministically  │
│  - Permanent regression protection   │
└──────────────────────────────────────┘
```

#### How it Works:
1. **Zero Hardware Needed for Bug Triage**: Legrand and BTicino manufacture dozens of gateway models (F454, MyHomeServer1, MH200N, MH202, 3578 USB) and modular DIN actuators with subtle firmware timing variations. When a beta tester reports unexpected behavior, clicking **"📋 Copy Capture"** on the Bus Monitor card (or downloading HA Diagnostics) packages the last 100 on-wire OpenWebNet frames with precise microsecond timestamps.
2. **Automated Discovery & Plant Setup**: Pytest automatically scans `tests/fixtures/plants/*/` for any directory containing `diagnostic_summary.json` and `myhome.yaml`.
3. **Sequential On-Wire Replay**: The harness initializes a simulated gateway session and streams the frozen frames sequentially into Home Assistant's internal event dispatcher (`f"myhome_message_{mac}"`), exercising the exact same message routing path as physical hardware.
4. **End-to-End State Verification**: Verifies that every single frame across Lighting (`WHO=1`), Automation (`WHO=2`), Thermoregulation (`WHO=4`), Audio (`WHO=16`), Energy (`WHO=18`), Dry Contacts (`WHO=25`), and ACK/NACK control signals updates entity states accurately with zero unhandled exceptions.
5. **High-Frequency Stress Testing**: Simulates event storms (e.g. 50 rapid toggle frames) to prove that the integration's async event queue and state machines never drop messages or trigger race conditions.
6. **Permanent CI Regression Protection**: Once a tester's trace is committed, it runs automatically on every pull request and push to master, ensuring that a fix for one community member's installation never regresses in future updates.

#### Capturing Traces:
- **In Home Assistant**: Call service `myhome.sweep_bus` -> Download Diagnostics (or copy trace from Bus Card).
- **Standalone CLI**: Run `python scripts/record_gateway_trace.py --host <IP> --password <PASS> --model <MODEL>` to record an isolated gateway on a test bench directly into a ready-to-test fixture.

#### Privacy: fixtures are synthetic
A diagnostics download describes a home: room and family names in `myhome.yaml`, the LAN address and MAC of the gateway, the config-entry id, sometimes a password. None of that is needed to replay a bus - only the addresses, platforms, options and frames are - so **every fixture is anonymized before it is committed**, and `tests/test_fixture_privacy.py` fails the build if one is not:

```bash
python scripts/anonymize_plant_fixture.py tests/fixtures/plants/issue_<n>_<model>
```

Devices become `light_10` / `Light 10` (the address is the name), IPs move to the `192.0.2.0/24` documentation range, MACs to `00:03:50:00:<issue>`, the entry id to a synthetic one, passwords to `null`. The script prints the old → new entity-id mapping for the test you write against the fixture. Name the directory after the issue and the gateway model, not after the reporter.

What the integration emits is clean at the source: the card's issue bundle and trace/sweep export name the transport and the gateway model, never the LAN address, the serial device or your browser; the diagnostics download redacts host, MAC, SSDP identity, config-file path, entry id and title, and names a decoder slot's media player `media_player.decoder_<n>` rather than after your room - in the config entry's `data` and `options` only; the gateway, profile, queue, platform and bus-monitor blocks are not touched, so every frame's `where` / `who` / `what` is there for triage. Two things still need the script: your `myhome.yaml` (it is your file, with your room names) and the envelope Home Assistant wraps around every diagnostics download (the list of installed integrations, `setup_times`), which is not ours to strip. `python scripts/anonymize_plant_fixture.py --check tests` reports anything personal under `tests/`; the pre-commit hook in `.pre-commit-config.yaml` runs it before a commit exists, CI runs it on every push.

---

## 🛠️ Development & Quality Standards

This project enforces strict code quality and packaging standards:

```bash
# Run the complete test suite
pytest tests/

# Run with coverage report
pytest --cov=custom_components.myhome --cov-report=term-missing tests/

# Run containerized Home Assistant smoke test (stable, beta, dev, or all)
python scripts/run_ha_container_smoke.py --channel stable
python scripts/run_ha_container_smoke.py --channel all

# Run OWNd protocol engine smoke test (pinned, latest, dev, or all)
python scripts/run_ownd_smoke.py --target pinned
python scripts/run_ownd_smoke.py --target all

# Validate PyPI packaging and PEP 517 compliance
python -m build
twine check --strict dist/*
check-wheel-contents dist/*.whl
```

### 🐳 Containerized Smoke Testing

To verify integration installation and runtime cleanliness against official upstream Home Assistant Docker environments before deployment:

```bash
# Test against stable Home Assistant container image
python scripts/run_ha_container_smoke.py --channel stable

# Test against upcoming beta container image
python scripts/run_ha_container_smoke.py --channel beta

# Test across all channels (stable, beta, and dev)
python scripts/run_ha_container_smoke.py --channel all
```

This runner:
1. Pulls the official container (`ghcr.io/home-assistant/home-assistant:<channel>`).
2. Runs `hass --script check_config` to validate schemas and component manifests.
3. Automatically installs all integration dependencies (`manifest.json`).
4. Validates clean import of all 14 integration platform modules.
5. Boots Home Assistant in daemon mode and verifies zero exceptions and zero asyncio loop-blocking warnings.

### ⚡ OWNd Protocol Engine Smoke Testing

To verify protocol engine compatibility and prevent regressions across upstream library distributions:

```bash
# Run against pinned PyPI version (manifest.json lockstep)
python scripts/run_ownd_smoke.py --target pinned

# Run against latest PyPI pre-release
python scripts/run_ownd_smoke.py --target latest

# Run against upstream development branch (OWNd@master)
python scripts/run_ownd_smoke.py --target dev

# Test all distribution targets across the matrix
python scripts/run_ownd_smoke.py --target all
```

This runner executes 4 validation gates:
1. **Metadata Lockstep**: Verifies that the exact `OWNd==` pin in `manifest.json` matches the installed package.
2. **Golden Corpus Conformance**: Runs 191 OpenWebNet frame fixtures (`tests/test_golden_conformance.py`) verifying parser extraction and builder parity.
3. **Platform Clean Imports**: Verifies all 14 integration platform modules import cleanly without missing symbols or deprecation errors.
4. **Mock Gateway TCP Loopback**: Boots a mock OpenWebNet TCP server, negotiates session handshake (`*99*0##`), dispatches commands, and verifies frame parsing end-to-end.

See the [F454 regression checks](docs/f454-regression-checks.md) for the fixes,
automated coverage and physical gateway verification steps.

### CI Workflows
- **`hassfest`**: Official Home Assistant manifest, translation, and metadata validation.
- **`validate`**: Official HACS compliance checks.
- **`test-coverage`**: 2090 automated unit tests with snapshot matching and 100% line coverage enforcement on the `ownd` core package.
- **`ha-container-smoke`**: Automated containerized smoke testing against official Home Assistant Docker images (`stable`, `beta`, `dev`) verifying `check_config`, clean platform module imports, and zero asyncio loop-blocking calls.
- **`ownd-smoke`**: Automated smoke testing of the `OWNd` protocol engine across `pinned`, `latest`, and `upstream-dev` distributions on Python 3.14.
- **`ha-upstream-compat`**: Continuous integration testing against upstream Home Assistant Stable, Beta, and Dev channels.
- **`ha_standards`**: Automated architectural standards enforcement (`verify_ha_standards.py` / `test_ha_standards.py`) ensuring user-confirmed discovery flows, complete step translations, no deprecated constants, and no blocking calls in async coroutines.
- **`pypi_standards`**: Strict wheel hygiene, metadata verification, and packaging checks.
- **`quality-scale`**: Self-audit of `quality_scale.yaml` against the official Home Assistant Integration Quality Scale (`quality_scale_report.py`); reports the tier reached, refreshes the badge and the table below.
- **`strict-typing`**: `mypy --strict` over the integration, ratcheted per module (`scripts/typing_ratchet.py`, `mypy_baseline.json`) — a module may only ever get cleaner (Platinum rule `strict-typing`).

### 🏅 Home Assistant Integration Quality Scale

<!-- START_QUALITY_SCALE -->

**Tier reached: 🏆 Platinum**

| Tier | Rules satisfied | Status |
| :--- | :---: | :--- |
| 🥉 Bronze | 20 / 20 | ✅ complete |
| 🥈 Silver | 10 / 10 | ✅ complete |
| 🥇 Gold | 21 / 21 | ✅ complete |
| 🏆 Platinum | 3 / 3 | ✅ complete |

_Self-audit of [`quality_scale.yaml`](custom_components/myhome/quality_scale.yaml) against the official [Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/); a tier needs every rule of that tier and all lower tiers `done`/`exempt`. Updated by the [Integration Quality Scale workflow](https://github.com/OpenWebNet-HA/MyHOME/actions/workflows/quality-scale.yml); tiers are formally awarded only by Home Assistant core review._

<!-- END_QUALITY_SCALE -->

### 📊 Code Coverage & Quality Assurance

The integration maintains 2090 automated unit tests (100% line coverage across all modules) covering core protocol handling, hardware profiles, discovery, state reconciliation, and error boundaries.

<!-- START_COVERAGE_TABLE -->

| Component / Module | Coverage | Notes |
|---|:---:|---|
| [`__init__.py`](custom_components/myhome/__init__.py) | **100%** | Setup lifecycle and zero-friction entity migration |
| [`alarm_control_panel.py`](custom_components/myhome/alarm_control_panel.py) | **100%** | Core integration component |
| [`binary_sensor.py`](custom_components/myhome/binary_sensor.py) | **100%** | Magnetic contacts, door/window sensors, motion sensors |
| [`bus_monitor.py`](custom_components/myhome/bus_monitor.py) | **100%** | In-band 500-frame circular ring buffer tap (0 extra sockets) |
| [`button.py`](custom_components/myhome/button.py) | **100%** | Scenario buttons and bus diagnostic pings |
| [`climate.py`](custom_components/myhome/climate.py) | **100%** | Heating, cooling, 4-pipe systems, and thermostat controls |
| [`config_flow.py`](custom_components/myhome/config_flow.py) | **100%** | Step handlers, user entry, reauth, and options flow |
| [`const.py`](custom_components/myhome/const.py) | **100%** | Protocol commands, dimensions, and integration constants |
| [`core/transport/base.py`](custom_components/myhome/core/transport/base.py) | **100%** | Abstract transport layer defining OWN lifecycle contract |
| [`core/transport/serial.py`](custom_components/myhome/core/transport/serial.py) | **100%** | Async Serial/USB transport for Legrand 3578 / OpenZigBee |
| [`core/transport/tcp.py`](custom_components/myhome/core/transport/tcp.py) | **100%** | Modular TCP/IP socket transport with framed stream parsing |
| [`cover.py`](custom_components/myhome/cover.py) | **100%** | Motorized shutters, blinds, roll-ups with state tracking |
| [`data.py`](custom_components/myhome/data.py) | **100%** | Core integration component |
| [`decoder_pool.py`](custom_components/myhome/decoder_pool.py) | **100%** | Thread-safe streaming proxy audio pool |
| [`device_trigger.py`](custom_components/myhome/device_trigger.py) | **100%** | Stateless CEN/CEN+ scenario device automation triggers |
| [`diagnostics.py`](custom_components/myhome/diagnostics.py) | **100%** | Config entry diagnostics with sensitive data redaction |
| [`discovery.py`](custom_components/myhome/discovery.py) | **100%** | Core integration component |
| [`gateway.py`](custom_components/myhome/gateway.py) | **100%** | Hardware handler, lockout prevention, adaptive queue pacing |
| [`identity.py`](custom_components/myhome/identity.py) | **100%** | Core integration component |
| [`light.py`](custom_components/myhome/light.py) | **100%** | Relays, auto-dimmer detection, and brightness transitions |
| [`light_group.py`](custom_components/myhome/light_group.py) | **100%** | Core integration component |
| [`media_player.py`](custom_components/myhome/media_player.py) | **100%** | F441/F441M sound system zones, dynamic proxy, gain-staging |
| [`myhome_device.py`](custom_components/myhome/myhome_device.py) | **100%** | Home Assistant device registry schema compliance |
| [`repairs.py`](custom_components/myhome/repairs.py) | **100%** | Core integration component |
| [`router.py`](custom_components/myhome/router.py) | **100%** | Core integration component |
| [`sensor.py`](custom_components/myhome/sensor.py) | **100%** | Power meters, energy counters, and pulse sensors |
| [`services.py`](custom_components/myhome/services.py) | **100%** | Core integration component |
| [`switch.py`](custom_components/myhome/switch.py) | **100%** | Relay actuators, auxiliary switches, socket controllers |
| [`validate.py`](custom_components/myhome/validate.py) | **100%** | Device & gateway schemas, custom WHERE validators, sensor injections |
| [`websocket.py`](custom_components/myhome/websocket.py) | **100%** | WebSocket API for real-time bus streaming, history, and diagnostics |

<!-- END_COVERAGE_TABLE -->

> **Live Test Execution**: View the live code coverage dashboard directly on [**Codecov (v2-phase2-architecture)**](https://app.codecov.io/gh/OpenWebNet-HA/MyHOME/tree/v2-phase2-architecture) or download the interactive HTML report from the [**test-coverage GitHub Actions run**](https://github.com/OpenWebNet-HA/MyHOME/actions/workflows/test-coverage.yaml).

---

## 🗺️ Roadmap

The development of the MyHOME integration is organized into strategic release milestones aligned with community RFC #248. For comprehensive milestone details, technical specifications, and contributor attribution, refer to the full [**ROADMAP.md**](ROADMAP.md).

- [x] **Phase 1: Architecture Modernization & Core Feature Parity (v2.0 — Complete)**
  - [x] Declarative hardware gateway profiles (`MH200` to `F454`).
  - [x] Dual asynchronous transports: Async TCP & Serial/USB (`Legrand 3578 / OpenZigBee`).
  - [x] Adaptive inter-frame bus pacing & sentinel supervisor lifecycle.
  - [x] In-band Lovelace Bus Monitor Card (`<myhome-openwebnet-bus-monitor>`) & WebSocket streaming proxy.
  - [x] Native Home Assistant Diagnostics (`diagnostics.py`) & GitHub Issue Forms.
  - [x] Full feature parity across primary subsystems: Light, Switch, Cover, Climate (Fancoil), Alarm, Binary Sensor (3477 Dry Contact / IR), Device Triggers (CEN/CEN+).
  - [x] 100% automated test coverage across all component modules.
- [x] **Phase 2: CEN/CEN+ Triggers, Central Unit Coordination & Multi-Gateway Routing (v2.1 — Live)**
  - [x] Strongly typed CEN / CEN+ scenario command builders and native device triggers with string-preserved addressing (P2).
  - [x] Thermoregulation Central Unit (3550 / 4695) master mode toggles and whole-plant zone synchronization (P4).
  - [x] Multi-gateway plant routing, MAC namespacing, and cross-talk isolation (P6).
  - [x] DALI Tunable White (Dimension 14, 2000K–6535K) auto-detection and color temperature control.
  - [x] Multi-authority OpenWebNet Golden Corpus cross-validation with 100.0% line coverage (1,189 unit tests).
- [ ] **Phase 3: Native Bus Timers & Environmental Auto-Discovery (v2.2 — Q4 2026)**
  - [ ] Native SCS light actuator temporization / staircase timers (`WHO = 1` Dimension 2 & timed WHAT codes).
  - [ ] Dynamic discovery for illuminance & motion detectors (Legrand 048834).
  - [ ] Passive bus sniffing & topology auto-mapping.
- [ ] **Phase 4: Actuator Diagnostics & Endpoint Safety Locks (v2.3 — Q4 2026)**
  - [ ] Actuator hardware maintenance locks / endpoint disable (`WHO = 14`).
  - [ ] Relay health telemetry, operating cycle counters, and diagnostic failure codes.
- [ ] **Phase 5: Extended Lighting Management & DALI-2 (v2.4 — Q1 2027)**
  - [ ] Native support for Lighting Management Room Controllers (`WHO = 24` BMNE500 / 002645).
- [ ] **Phase 6: Smart Energy Management & Advanced Sound Diffusion (v2.5 — Q1 2027)**
  - [ ] Energy management central units & multi-function power meters (`WHO = 18` F520/F521/F522/F523/3522).
  - [ ] Multi-room sound diffusion source navigation, FM tuner presets, and RDS metadata streaming (`WHO = 22`).

---

## 👥 Credits & Attribution

This integration is developed and maintained by the **[OpenWebNet-HA](https://github.com/OpenWebNet-HA)** community.

Special thanks to:
- **[@anotherjulien](https://github.com/anotherjulien)** for creating the original MyHOME integration and laying the protocol foundations.
- **[@GreenGrassBlueOcean](https://github.com/GreenGrassBlueOcean)** for the v2 modernized architecture, gateway profiles, streaming proxy, and test suite.
- **[@GianlucaCh](https://github.com/GianlucaCh)** for preserving and contributing the comprehensive 15-manual BTicino/Legrand specification archive (`OWN DOC.zip`), the official `WHO_24.pdf` Lighting Management specification, and CEN+ community automation references.
- **[@mantovanellimatteo](https://github.com/mantovanellimatteo)**, **[@fedem95](https://github.com/fedem95)**, **[@lyubomirtraykov](https://github.com/lyubomirtraykov)**, **[@Interstellar0verdrive](https://github.com/Interstellar0verdrive)**, and **Cedric Rohou** for key bugfixes, platform extensions, and community testing.
