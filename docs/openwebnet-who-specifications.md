# OpenWebNet Protocol & WHO Specifications Archive

Welcome to the **OpenWebNet Protocol & WHO Specifications Archive**. This document serves as the official open-access registry and cross-check inventory of all technical manuals, frame syntax, dimension definitions, and PDF specifications published by BTicino / Legrand for the OpenWebNet protocol across MyHOME systems.

Historically, these technical specifications were distributed through the *MyOpen Community* portal (`myopen-legrandgroup.com` / `myopen-bticino.it`), which is no longer active. To ensure that developers, installers, and community members have permanent access to accurate protocol documentation, we maintain this centralized registry.

### 🌐 Official & Community Documentation Links
- 🏛️ **Official Legrand Developer Portal**: [Legrand Local Interoperability — PDF Documentation](https://developer.legrand.com/local-interoperability/#PDF%20documentation)
- 📦 **Master Document Archive**: [OWN DOC.zip (GitHub PR #232 Attachment)](https://github.com/user-attachments/files/32008617/OWN.DOC.zip) — Complete 15-document bundle preserved and contributed by **@GianlucaCh**.
- 🪄 **Home Assistant Community Blueprint**: [MyHome CEN+ Commands Blueprint](https://community.home-assistant.io/t/myhome-cen-commands/260345) — Connect physical MyHOME CEN / CEN+ scenario pushbuttons to toggle non-BTicino entities and dim lights.
- 📖 **Interactive GitHub Wiki**: [OpenWebNet Protocol & WHO Specifications](https://github.com/OpenWebNet-HA/MyHOME/wiki/OpenWebNet-Protocol-&-WHO-Specifications)

---

## 📋 Community Cross-Check & Verification Status

Thanks to community contributions—in particular the comprehensive technical manual archive contributed by **@GianlucaCh** in [PR #232](https://github.com/OpenWebNet-HA/MyHOME/pull/232)—**15 official specifications** are now preserved, verified, and cross-checked against live installations!

If you are a **certified BTicino / Legrand installer**, **system integrator**, or **MyHOME software specialist** (such as **@xtimmy86x**, **@lyubomirtraykov**, and fellow community professionals), your real-world experience across varied plant configurations and engineering software continues to help us fill the final remaining gaps:

### 🔍 Remaining Specifications & Items Under Review:

1. **Document Versions & Revision Dates**:
   - Compare your PDF archive against the [Master WHO Family Inventory](#-master-who-family-inventory) table below.
   - Look at the cover page and revision history (e.g. *Last date modify*, *Version number*). If your copy is newer than what is listed, please share the version details!

2. **Remaining Missing / Legacy Specifications**:
   - **`WHO = 3`**: Load control legacy central unit (F421) (Seeking newer official PDF than 2006).
   - **`WHO = 6`**: Dedicated audio door entry call frames (Seeking standalone document beyond WHO 7 / Intro).
   - **`WHO = 9`**: Auxiliary channels (AUX 1–9) (Seeking newer official PDF than 2006).
   - *(Note: **`WHO = 14`** Actuator Lock is fully reverse-engineered and documented below; Legrand never published a public standalone PDF).*

3. **Software Tooling & Dictionaries (MyHOME_Suite / TiMyHome)**:
   - Command definitions or exported XML dictionaries from Legrand/BTicino configuration software (**MyHOME_Suite**, **TiMyHome**, **Virtual Configurator**, **MyHOME_Up**).

> **💡 How to Contribute:** You can share filenames, revision dates, technical sheets, or bus monitor traces by opening an issue on the [OpenWebNet-HA/MyHOME GitHub repository](https://github.com/OpenWebNet-HA/MyHOME/issues) or commenting directly in [PR #232](https://github.com/OpenWebNet-HA/MyHOME/pull/232). Every contribution helps ensure open, permanent documentation for the entire MyHOME ecosystem.

---

## 📚 Master WHO Family Inventory

The table below catalogs every known OpenWebNet function family (`WHO`), its official Legrand document title, current known version, archive status, and Home Assistant platform mapping.

### Status Legend
- 🟢 **Archived & Verified**: Official PDF is preserved in our archive with full syntax verified.
- 🟡 **Legacy Copy Available / Cross-Check Needed**: Legacy documentation or reverse-engineered definitions exist; seeking confirmation of the latest official version.
- 🔴 **Needed / Missing**: Seeking the official Legrand/BTicino PDF specification.

| WHO | Subsystem / Function | Official Document Title / Filename | Known Version & Date | Status | Home Assistant Entity / Platform | Notes & Supported Hardware |
|:---:|:---|:---|:---:|:---:|:---|:---|
| **0** | **Scenarios (Basic)** | `Open Web Net Language (Scenarios)` / `WHO_0.pdf` | **v2.0.0 (2010-10-01)** | 🟢 | `event`, automations | 32 standard scenarios. Legrand 03551, 88301; BTicino F420, IR interface 3456. Contributed by GianlucaCh. |
| **1** | **Lighting (Illuminazione)** | `Who = 1 LIGHTING` / `WHO_1.pdf` | **v1.1.0 (2014-11-17)** | 🟢 | `light` | ON, OFF, Dimming (1-100%, 10 levels, steps), Blink, Timer, Speed of transition. Extended lighting & DALI dimensions (tunable white / RGBW via F429/F429G). Contributed by GianlucaCh. |
| **2** | **Automation (Automazione)** | `Messages - Automation` / `WHO_2.pdf` | **v1.0.0 (2015-11-12)** | 🟢 | `cover` | Roller shutters, venetian blinds, motorized curtains, gates. Standard UP/DOWN/STOP and advanced absolute positioning percentage (0-100%, Legrand 67557). Contributed by GianlucaCh. |
| **3** | **Load Control (Legacy)** | `OpenWebNet_Community_3_LoadControl` / `WHO_3.pdf` | v1.0.0 (2006) | 🟡 | `switch`, `sensor` | Priority-based load disconnection central unit (F421). Inhibit/force actuators. |
| **4** | **Thermoregulation (Termoregolazione)** | `Open Web Net Language - Heating adjustment` / `WHO_4 2.pdf` | **v2.0.0 (2013-11-27)** | 🟢 | `climate`, `sensor` | 4-zone / 99-zone central units (3550), standalone thermostats (L/N/NT4691), external probe sensors (3475). Added dimension 22 (offset) and dimension 11 (fancoil 3-speed). Contributed by GianlucaCh. |
| **5** | **Burglar Alarm (Antifurto)** | `MyHome Burglar Alarm` / `WHO_5.pdf` | **(2008-02-13)** | 🟢 | `alarm_control_panel` | Central units (3485, 3486), partition arming/disarming, panic alarms, gas/water technical alarms, sensor zone status. Authored by Lorenzo Pini. Contributed by GianlucaCh. |
| **6** | **Door Entry Call & Lock** | `OpenWebNet_Community_DoorEntry` / `WHO_6.pdf` | v1.0.0 (2006) | 🟡 | `lock`, `switch`, `event` | Audio door entry calls, door lock release (`*6*10*<WHERE>##`), staircase light, camera switching, incoming call chimes. |
| **7** | **Video Door Entry / Multimedia** | `Open Web Net WHO=7` / `WHO_7.pdf` | **v1.0.1 (2011-12-01)** | 🟢 | `camera` | Video session establishment, camera selection, video stream routing over IP for Video Server F453AV. Contributed by GianlucaCh. |
| **9** | **Auxiliary (Comandi Ausiliari)** | `OpenWebNet_Community_Auxiliary` / `WHO_9.pdf` | v1.0.0 (2006) | 🟡 | `switch` | Auxiliary channels (AUX 1 to AUX 9) for triggering remote relays, annunciators, or inter-system signals without occupying lighting addresses. |
| **13** | **Gateway Management** | `OpenWebNet_Community_2_device_v1_0_0_EN` / `WHO_13.pdf` | **v1.0.0 (2006-06-13)** | 🟢 | Core diagnostics | Date/time synchronization (`*#13**0*...`), firmware version query, IP configuration, MAC address, uptime, reboot command. Contributed by GianlucaCh. |
| **14** | **Actuators Lock (Light & Shutters)** | `Light & Shutter Actuators Lock` *(No Public PDF)* | v1.0.0 (2008) | 🟢 | Core diagnostics, `button`, `lock` | Physical endpoint lock/unlock. Lock (`*14*0*<WHERE>##`), Unlock (`*14*1*<WHERE>##`), Status query (`*#14*<WHERE>##`). Inverts/locks physical wall button input. Legrand never distributed a public standalone PDF (internal diagnostic spec). |
| **15** | **CEN Scenario Control (Scheduler)** | `CEN Frames for Scenario Scheduler` / `WHO_15-25.pdf` | **v1.0.0 (2010-10-01)** | 🟢 | `event`, device triggers | Pushbutton scenario events for Scenario Scheduler (MH200, MH200N, Legrand 03565): Virtual/physical pressure, short release, extended pressure, release after extended pressure. Contributed by GianlucaCh. |
| **16** | **Sound Distribution (Zone/Amp Control)** | `OpenWebNet_Community_4_soundsystem` / `WHO_16.pdf` | **v1.0.1 (2011-11-24)** | 🟢 | `media_player` | Multi-room audio matrix & zone control: Amplifier ON/OFF, volume control (step & %), audio input source selection (RDS tuner, RCA/aux, USB, Bluetooth), equalizer (bass, treble, balance), station presets (F441, F441M, F450, 3487). |
| **17** | **Scenario Programmer (Scenes)** | `Who = 17 SCENES` / `WHO_17.pdf` | **v1.0.0 (2015-04-09)** | 🟢 | `switch`, `event` | MH200 / MH200N / MH202 scenario programmer integration, enable/disable automated schedules, trigger macro executions. Contributed by GianlucaCh. |
| **18** | **Energy Management Functions** | `Energy Management Functions` / `WHO_18 1.pdf` | **v1.0.0 (2011-07-15)** | 🟢 | `sensor` | Electricity, water, and gas pulse meters. Instantaneous power (W), cumulative active energy (kWh), current (mA), voltage (V), power factor, tariff periods (F80/x, F520, F521, F522, F523, 3522). Contributed by GianlucaCh. |
| **22** | **Sound Diffusion (Source & Speaker)** | `Who = 22 Sound Diffusion` / `WHO_22.pdf` | **v1.1.0 (2014-06-12)** | 🟢 | `media_player` | Sound source navigation & speaker control: Radio FM frequency step up/down, station navigation, track skipping (next/previous track), RDS text display control (`*22*31...`), station presets, and speaker volume. Contributed by GianlucaCh. |
| **24** | **Lighting Management (Gestione Luci)** | `Who_24_eng_PUBBLIC.doc` / `WHO_24.pdf` | **v1.0.0 (2012-04-06)** | 🟢 | `light`, `sensor` | Legrand / BTicino Lighting Management System (BMNE500 / 002645 room controller, BMview, Lighting Console). Zone addressing (`1000+zone`), dimensions 1–12 (switch-on level %, max lux, maintained level lux, auto switch on/off, switch on/off delay, delay timer, standby timer/value, off value, slave offset GAP), dimension 17 (state: Auto/Manual/Stop), dimension 18 (centralised lux sensor reporting). Profile frames (`*24*1#Profile_ID*WHERE##`) and slave offset (`*24*2#[0-1]*WHERE##`). Contributed by GianlucaCh. |
| **25** *(CEN+)* | **CEN+ Scenario Control (Scheduler)** | `CEN Frames for Scenario Scheduler` / `WHO_15-25.pdf` | **v1.0.0 (2010-10-01)** | 🟢 | `event`, device triggers | Extended CEN protocol with up to 256 buttons/scenarios for MH200/MH200N/03565: Short pressure (<0.5s), start of extended pressure (>=0.5s), extended pressure holding, release after extended pressure. Contributed by GianlucaCh. |
| **25** *(Contacts)* | **Dry Contact & IR State Functions** | `DRY CONTACT AND IR STATE FUNCTIONS` / `WHO_25.pdf` | **v1.0.0 (2010-11-04)** | 🟢 | `binary_sensor` | Dry contact interfaces & IR sensor state: State ON / IR detection (`WHAT=31`), State OFF / IR not detected (`WHAT=32`), event-driven (`PARAM=1`) or poll request (`PARAM=0`). BTicino 3477, F428, 3480, F482, IR 4610/4611/4640; Legrand 573996, 03553, 067513, etc. Contributed by GianlucaCh. |
| **HMAC** | **Gateway Security & Authentication** | `Hmac Specification` / `Hmac.pdf` | **v1.1.0 (2016-08-05)** | 🟢 | Core transport | Cryptographic challenge-response HMAC-SHA256 authentication replacing legacy OPEN numeric password authentication for modern gateways (MyHomeServer1, F455, F461). Contributed by GianlucaCh. |
| **INTRO** | **OpenWebNet System Architecture** | `INTRODUCTION Examples of Integration` / `OWN INTRO.pdf` | **(2012-10-03)** | 🟢 | Core protocol | Foundational architecture manual by BTicino detailing frame delimiters, session separation (Command vs Event/Status), ACK/NACK signaling, and system integration patterns. Contributed by GianlucaCh. |
| **1000** | **Automation Group Commands** | Embedded in WHO 2 | — | 🟡 | `cover` | Area and General group broadcast commands for automation. |
| **1001** | **Lighting Group Commands** | Embedded in WHO 1 | — | 🟡 | `light` | Area and General group broadcast commands for lighting. |
| **1004** | **Temperature Group Commands** | Embedded in WHO 4 | — | 🟡 | `climate` | Zone group commands for thermoregulation. |

---

## 📑 Protocol Clarifications & Subsystem Distinctions

### 1. CEN/CEN+ Scenario Schedulers (`WHO_15-25.pdf`) vs Dry Contacts & IR (`WHO_25.pdf`)

A common source of confusion in the OpenWebNet ecosystem stems from the shared `WHO = 25` code. As highlighted by **@GianlucaCh**, these are two completely distinct official specifications:

1. **Scenario Scheduler CEN / CEN+ (`WHO_15-25.pdf`)**:
   - **Covered Devices**: Scenario Programmers and Controllers (MH200, MH200N, MH201, Legrand 03565).
   - **Protocol Nature**: Pushbutton scenario trigger events.
   - **Syntax**: `*25*<WHAT>*<WHERE>##` where `WHAT` encodes:
     - `21`: Short pressure (< 0.5 seconds).
     - `22`: Start of extended pressure (>= 0.5 seconds).
     - `23`: Extended pressure holding.
     - `24`: Release after extended pressure.
   - **Addressing (`WHERE`)**: Scenario address up to 256 buttons/scenarios (`[1-256]`).

2. **Dry Contact Interfaces & IR Sensors (`WHO_25.pdf`)**:
   - **Covered Devices**: Physical interface modules (BTicino 3477, F428, 3480, F482, IR 4610/4611/4640; Legrand 573996, 03553, 067513, etc.).
   - **Protocol Nature**: Hardware state telemetry for magnetic window/door reed switches, technical contacts, and passive infrared motion detectors.
   - **Syntax**: `*25*<WHAT>*<WHERE>##` or dimension frames where `WHAT` indicates:
     - `31`: Contact ON / IR detection (closed contact or motion detected).
     - `32`: Contact OFF / IR not detected (opened contact or motion cleared).
   - **Parameter (`PARAM`)**:
     - `PARAM = 0`: State response upon polling request.
     - `PARAM = 1`: State response upon real-time system event.
   - **Home Assistant Entity**: Exposed as `binary_sensor` (e.g. window opened/closed, PIR motion).

---

### 2. Multi-Room Sound Distribution (`WHO = 16`) vs Sound Diffusion (`WHO = 22`)

The sound subsystem is split across two dedicated OpenWebNet WHO families:

1. **WHO = 16 (Sound Distribution — Zone & Matrix Management)**:
   - Controls physical audio amplifiers and source matrix units (F441, F441M, F450, 3487).
   - Functions include: Zone power (ON/OFF), volume attenuation (0–31 scale / percentages), audio input channel routing (IN 1 to IN 4), tone equalizers (bass, treble, balance), and master room follow-me.
   - Mapped to Home Assistant `media_player` entities.

2. **WHO = 22 (Sound Diffusion — Source & Speaker Navigation)**:
   - Controls the audio source devices themselves (FM tuner, CD player, multimedia source) and individual speaker units.
   - Functions include:
     - FM tuner frequency step up/down (`WHAT = 5` / `WHAT = 6`).
     - Station preset navigation (`WHAT = 9` next / `WHAT = 10` previous).
     - Track navigation (`WHAT = 11` next track / `WHAT = 12` previous track).
     - RDS text string display streaming (`WHAT = 31` start / `WHAT = 32` stop).
     - Direct frequency tuning and preset memorization (`WHAT = 33`).
     - Speaker volume adjustment (`WHAT = 3` increase / `WHAT = 4` decrease).

---

### 3. Actuator Diagnostics & Endpoint Safety Locks (`WHO = 14`)

BTicino and Legrand never distributed a standalone `WHO_14.pdf` in their public developer distributions, retaining it as an internal/installer diagnostic function. However, the protocol grammar is fully reverse-engineered and verified across live systems:

- **Protocol Function**: Inhibits physical wall buttons (rocker switches, CEN pushbuttons) and remote actuation for safety, maintenance, or child locking on lighting and cover/shutter actuators (F411, F411U2, F418, LN4672M2).
- **Lock Actuator (Disable Controls)**: `*14*0*<WHERE>##`
  - Freezes the relay in its current position and disables physical switch inputs.
- **Unlock Actuator (Enable Controls)**: `*14*1*<WHERE>##`
  - Restores normal operation and re-enables physical wall buttons and bus commands.
- **Status Query**: `*#14*<WHERE>##`
  - Returns `*14*0*<WHERE>##` (Locked) or `*14*1*<WHERE>##` (Unlocked).
- **Home Assistant Integration**: Represented via configuration button entities (`DisableCommandButtonEntity` and `EnableCommandButtonEntity`) under each actuator device.
- **Gateway Support**: Supported on modern gateways (F454, MyHomeServer1). Older gateways (e.g. early F455 firmware) return NACK (`*#*0##`).

---

## 🪄 CEN / CEN+ Automation & Community Blueprints

One of the most powerful capabilities of physical MyHOME installations is using existing BTicino wall pushbuttons and dry contact modules (such as the **3477** interface) to trigger automations across modern smart home ecosystems.

### 🌟 Community Blueprint: MyHome CEN+ Commands

A highly recommended blueprint developed by the Home Assistant community (by **gST84**) is available on the Home Assistant Community Forum:

👉 **[MyHome - CEN+ commands Blueprint (Community Forum)](https://community.home-assistant.io/t/myhome-cen-commands/260345)**  
👉 **[Raw Blueprint Source YAML (GitHub)](https://github.com/gST84/myHome_blueprints/blob/f4f3151aab73d85b0ab098b67f51d68e731f534f/CENplus/myhome-cen-plus.yaml)**

### Key Features of the Blueprint:
1. **Control Non-BTicino Devices**: Use your native BTicino wall switches to switch or toggle non-BTicino smart devices (Philips Hue, Zigbee, Z-Wave, Shelly, or Tuya).
2. **Short vs. Long Press Handling**:
   - **Short Press (< 0.5s)**: Instantly toggles lights or triggers scenes.
   - **Extended / Long Press (>= 0.5s)**: Enables smooth dimming up or down.
3. **Dimming Direction Helper**:
   - By creating a simple `input_boolean` helper (e.g. `input_boolean.living_dim_direction`), the blueprint tracks dimming direction so alternating long presses cycle between brightening and dimming.
4. **Zero-Delay Bus Processing**: Fully compatible with the stateless `myhome.cen` and `myhome.cen_plus` events dispatched by the modernized MyHOME integration.

---

## 🔌 Gateway Architecture & Hardware Profiles

The table below outlines supported hardware gateways, transport layers, queue pacing requirements, and authentication mechanisms:

| Model | Hardware Type | Transport | Default Port | Auth Protocol | Queue Pacing | Features & Firmware Notes |
|:---|:---|:---|:---:|:---|:---:|:---|
| **MH200** | Scenario Programmer | TCP/IP | 20000 | Open password / None | 150 ms | Embedded ARM, scenario scheduler, legacy buffer limits. |
| **MH200N** | Scenario Programmer | TCP/IP | 20000 | Open password / None | 100 ms | Updated network interface, scenario engine. |
| **MH201** | Scenario Controller | TCP/IP | 20000 | Open password / None | 80 ms | High-performance DIN-rail scenario controller. |
| **MH202** | Scenario Programmer | TCP/IP | 20000 | Open password / None | 50 ms | High-speed ARM CPU, expanded memory and scenario memory. |
| **F452** | Web Server IP | TCP/IP | 20000 | Open password / None | 150 ms | Early generation IP gateway. |
| **F453AV** | Audio/Video Web Server | TCP/IP | 20000 | Open password / None | 120 ms | Supports door entry and basic web control. |
| **F454** | Web Server IP | TCP/IP | 20000 | Open numeric password | 80 ms | Dual bus interface, widely deployed standard DIN gateway. |
| **F455** | Advanced IP Gateway | TCP/IP | 20000 | Open numeric password | 50 ms | High-throughput industrial gateway. |
| **MyHomeServer1** | Modern IoT Gateway | TCP/IP | 20000 | **HMAC-SHA2 (SHA-256)** | 30 ms | Fast SoC, alphanumeric credentials, cloud integration bridge. |
| **F461** | Next-Gen DIN Server | TCP/IP | 20000 | Alphanumeric / HMAC | 20 ms | Latest generation BTicino DIN-rail server/gateway. |
| **Legrand 3578** | OpenZigBee USB Interface | Serial USB | `/dev/ttyUSB*` | None (Serial bypass) | 40 ms | 19200 baud, 8N1, ZigBee wireless SCS bridge (`#<unit_id>` addressing). |

---

## 🔍 OpenWebNet Frame Syntax Quick Reference

OpenWebNet messages always begin with `*` and end with `##`. Fields are separated by `*`:

1. **Standard Command / Status Message**:
   ```text
   *WHO*WHAT*WHERE##
   ```
   *Example*: `*1*1*21##` (Turn ON light at address A=2, PL=1)

2. **Dimension Request (Query)**:
   ```text
   *#WHO*WHERE*DIMENSION##
   ```
   *Example*: `*#4*1*0##` (Query measured temperature of Zone 1)

3. **Dimension Writing (Command with Parameters)**:
   ```text
   *#WHO*WHERE*#DIMENSION*VAL1*VAL2*...*VALn##
   ```
   *Example*: `*#16*1*#1*30##` (Set volume of audio Amplifier 1 to 30%)

4. **Dimension Response (Status Report)**:
   ```text
   *#WHO*WHERE*DIMENSION*VAL1*VAL2*...*VALn##
   ```
   *Example*: `*#4*1*0*0215*1##` (Zone 1 temperature is 21.5 °C, Heating mode)

5. **Acknowledge (ACK / NACK)**:
   - `*#*1##` : **ACK** (Command accepted by gateway/bus)
   - `*#*0##` : **NACK** (Command rejected, syntax error, or buffer busy)

---

## 🛠️ Testing & Live Bus Capture in Home Assistant

To capture raw OpenWebNet frames from physical hardware or diagnostic sessions:

1. Add the Lovelace Bus Monitor Card to your dashboard:
   ```yaml
   type: custom:myhome-openwebnet-bus-monitor
   # Note: custom:myhome-bus-card is also supported as an alias
   ```
2. Operate your devices (e.g. adjust a DALI color ballast, toggle a dry contact switch, or change audio volume).
3. Click **`📋 Report Issue / Copy Trace`** to copy a sanitized diagnostics report with the exact bus frames.
