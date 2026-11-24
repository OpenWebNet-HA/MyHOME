# OpenWebNet Protocol & WHO Specifications Archive

Welcome to the **OpenWebNet Protocol & WHO Specifications Archive**. This document serves as the official open-access registry and cross-check inventory of all technical manuals, frame syntax, dimension definitions, and PDF specifications published by BTicino / Legrand for the OpenWebNet protocol across MyHOME systems.

Historically, these technical specifications were distributed through the *MyOpen Community* portal (`myopen-legrandgroup.com` / `myopen-bticino.it`), which is no longer active. To ensure that developers, installers, and community members have permanent access to accurate protocol documentation, we maintain this centralized registry.

### 🌐 Official & Community Documentation Links

- 🏛️ **Official Legrand Developer Portal**: [Legrand Local Interoperability — PDF Documentation](https://developer.legrand.com/local-interoperability/#PDF%20documentation)
- 📦 **Master Document Archive**: [OWN DOC.zip (GitHub PR #232 Attachment)](https://github.com/user-attachments/files/32008617/OWN.DOC.zip) — Complete 15-document bundle preserved and contributed by **@GianlucaCh**.
- 🪄 **Home Assistant Community Blueprint**: [MyHome CEN+ Commands Blueprint](https://community.home-assistant.io/t/myhome-cen-commands/260345) — Connect physical MyHOME CEN / CEN+ scenario pushbuttons to toggle non-BTicino entities and dim lights.
- 📖 **OpenWebNet Encyclopedia (Machine-Readable KB & Reference)**: [OpenWebNet-HA/OpenWebNet-Encyclopedia](https://github.com/OpenWebNet-HA/OpenWebNet-Encyclopedia) — Epistemic protocol reference cross-checked against official specifications and hardware traces.

---

## 📋 Community Cross-Check & Verification Status

Thanks to community contributions—in particular the comprehensive technical manual archive contributed by **@GianlucaCh** in [PR #232](https://github.com/OpenWebNet-HA/MyHOME/pull/232)—**15 official specifications** are now preserved, verified, and cross-checked against live installations!

If you are a **certified BTicino / Legrand installer**, **system integrator**, or **MyHOME software specialist** (such as **@xtimmy86x**, **@lyubomirtraykov**, and fellow community professionals), your real-world experience across varied plant configurations and engineering software continues to help us fill the final remaining gaps:

### 🔍 Remaining Specifications & Items Under Review:

1. **Document Versions & Revision Dates**:
    - Compare your PDF archive against the [Master WHO Family Inventory](#master-who-family-inventory) table below.
    - Look at the cover page and revision history (e.g. *Last date modify*, *Version number*). If your copy is newer than what is listed, please share the version details!

2. **Remaining Missing / Legacy Specifications**:
    - **`WHO = 3`**: Load control legacy central unit (F421) (Seeking newer official PDF than 2006).
    - **`WHO = 6`**: Dedicated audio door entry call frames (Seeking standalone document beyond WHO 7 / Intro).
    - **`WHO = 9`**: Auxiliary channels (AUX 1–9) (Seeking newer official PDF than 2006).
    - *(Note: **`WHO = 14`** Actuator Lock has no separate `WHO_14.pdf` file because it is published inside `WHO_1.pdf` — `Who = 1 LIGHTING` v1.1.0, Chapter 4 "WHO 14", §4.2, p. 21 & §5.1, p. 22 — and cross-verified below against gateway firmwares and live hardware).*

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
| **0** | **Scenarios (Basic)** | `Open Web Net Language (Scenarios)` / `WHO_0.pdf` | **v2.0.0 (2010-10-01)** | 🟢 | `—` (`myhome.send_message`) | 32 standard scenarios. Legrand 03551, 88301; BTicino F420, IR interface 3456. Contributed by GianlucaCh. |
| **1** | **Lighting (Illuminazione)** | `Who = 1 LIGHTING` / `WHO_1.pdf` | **v1.1.0 (2014-11-17)** | 🟢 | `light`, `switch`, `cover`, `binary_sensor`, `sensor` | ON, OFF, Dimming (1-100%, 10 levels, steps), Blink, Timer, Speed of transition, impulse covers, motion/lux sensors. Extended lighting & DALI dimensions (tunable white / RGBW via F429/F429G). Contributed by GianlucaCh. |
| **2** | **Automation (Automazione)** | `Messages - Automation` / `WHO_2.pdf` | **v1.0.0 (2015-11-12)** | 🟢 | `cover` | Roller shutters, venetian blinds (lift & slat tilt), motorized curtains, gates. Standard UP/DOWN/STOP and advanced absolute positioning percentage (0-100%, Legrand 67557). Contributed by GianlucaCh. |
| **3** | **Load Control (Legacy)** | `OpenWebNet_Community_3_LoadControl` / `WHO_3.pdf` | v1.0.0 (2006) | 🟡 | — (not implemented) | Priority-based load disconnection central unit (F421). Inhibit/force actuators. |
| **4** | **Thermoregulation (Termoregolazione)** | `Open Web Net Language - Heating adjustment` / `WHO_4 2.pdf` | **v2.0.0 (2013-11-27)** | 🟢 | `climate`, `sensor`, `switch` | 4-zone / 99-zone central units (4695, 3550), standalone thermostats (L/N/NT4691, Living Now), probe-less actuator relays, external probe sensors (3475). Supports dimension 0 (temperature), dimension 11 (fancoil 3-speed), dimension 12/14 (setpoints), dimension 13 (offset), dimension 15 (slave probes), dimension 20 (actuators), and dimension 60 (relative humidity). Contributed by GianlucaCh. |
| **5** | **Burglar Alarm (Antifurto)** | `MyHome Burglar Alarm` / `WHO_5.pdf` | **(2008-02-13)** | 🟢 | `alarm_control_panel`, `myhome_alarm_event` | Central units (3485, 3486), read-only panel status and event bus notifications (arm/disarm via installer-programmed AUX frames). Authored by Lorenzo Pini. Contributed by GianlucaCh. |
| **6** | **Door Entry Call & Lock** | `OpenWebNet_Community_DoorEntry` / `WHO_6.pdf` | v1.0.0 (2006) | 🟡 | `lock`, `myhome_doorbell_event` | Audio door entry calls, door lock release (`*6*10*<WHERE>##`), incoming call notifications. |
| **7** | **Video Door Entry / Multimedia** | `Open Web Net WHO=7` / `WHO_7.pdf` | **v1.0.1 (2011-12-01)** | 🟢 | — (not implemented) | Video session establishment, camera selection, video stream routing over IP for Video Server F453AV. Contributed by GianlucaCh. |
| **8** | **Video Door Entry / Intercom** | *Internal / Installer Spec* (`bt_vct`) | — | 🟡 | `myhome_doorbell_event` | Entrance panel video doorbell notifications (`*8*1#<1..4>#<mm>*<WHERE>##`) on gateways connected to the door-entry riser (`F454`, `F459`, etc.). |
| **9** | **Auxiliary (Comandi Ausiliari)** | `OpenWebNet_Community_Auxiliary` / `WHO_9.pdf` | v1.0.0 (2006) | 🟡 | `binary_sensor`, `switch` | Auxiliary channels (AUX 0 to AUX 9, declared in `myhome.yaml`) and per-device **Invert state** configuration switches. |
| **13** | **Gateway Management** | `OpenWebNet_Community_2_device_v1_0_0_EN` / `WHO_13.pdf` | **v1.0.0 (2006-06-13)** | 🟢 | — (Core diagnostics, `myhome.sync_time`) | Date/time synchronization (`*#13**#0*...`), firmware version query, IP configuration, MAC address, device model probing. Contributed by GianlucaCh. |
| **14** | **Actuators Lock (Light & Shutters)** | `Who = 1 LIGHTING` / `WHO_1.pdf` *(Chapter 4, §4.2 p. 21 & §5.1 p. 22)* | **v1.1.0 (2014-11-17)** | 🟢 | `button` | Physical endpoint lock/unlock: Lock (`*14*0*<WHERE>##`) and Unlock (`*14*1*<WHERE>##`); `*#14*<WHERE>##` status queries are NACKed on tested gateways (write/event-only, tracked via `actuator_locked`). Disables/enables physical wall buttons and bus actuation. Documented in `WHO_1.pdf` Chapter 4 rather than a separate `WHO_14.pdf`. |
| **15** | **CEN Scenario Control (Scheduler)** | `CEN Frames for Scenario Scheduler` / `WHO_15-25.pdf` | **v1.0.0 (2010-10-01)** | 🟢 | `myhome_cen_event`, device triggers | Pushbutton scenario events for Scenario Scheduler (MH200, MH200N, Legrand 03565): Virtual/physical pressure, short release, extended pressure, release after extended pressure. Contributed by GianlucaCh. |
| **16** | **Sound Distribution (Zone/Amp Control)** | `OpenWebNet_Community_4_soundsystem` / `WHO_16.pdf` | **v1.0.1 (2011-11-24)** | 🟢 | `media_player`, `number`, `button` | Multi-room audio matrix & zone control: Amplifier ON/OFF, volume control (step & %), audio input source selection (RDS tuner, RCA/aux, network decoders), station presets (F441, F441M, F500, F500N, F450, 3487). |
| **17** | **Scenario Programmer (Scenes)** | `Who = 17 SCENES` / `WHO_17.pdf` | **v1.0.0 (2015-04-09)** | 🟢 | — (not implemented; `myhome_scene_event` only) | MH200 / MH200N / MH202 scenario programmer lifecycle telemetry (`myhome_scene_event` on start/stop/enable/disable). Contributed by GianlucaCh. |
| **18** | **Energy Management Functions** | `Energy Management Functions` / `WHO_18 1.pdf` | **v1.0.0 (2011-07-15)** | 🟢 | `sensor` | Instantaneous active power (W) and cumulative/daily/monthly active energy counters (Wh) in Home Assistant (F80/x, F520, F521, F522, F523, 3522; spec also defines V/mA/tariff dimensions). Contributed by GianlucaCh. |
| **22** | **Sound Diffusion (Source & Speaker)** | `Who = 22 Sound Diffusion` / `WHO_22.pdf` | **v1.1.0 (2014-06-12)** | 🟢 | `media_player`, `number`, `button` | Sound source navigation & speaker control: Radio FM frequency step up/down, station navigation, track skipping (next/previous track), RDS text display control (`*22*31...`), station presets, active-source area notifications (`4#<area>`), and speaker volume (F500, F500N). Contributed by GianlucaCh. |
| **24** | **Lighting Management (Gestione Luci)** | `Who_24_eng_PUBBLIC.doc` / `WHO_24.pdf` | **v1.0.0 (2012-04-06)** | 🟢 | — (not implemented) | Legrand / BTicino Lighting Management System (BMNE500 / 002645 room controller, BMview, Lighting Console). Zone addressing (`1000+zone`), dimensions 1–12, dimension 17 (Auto/Manual/Stop), dimension 18 (centralised lux reporting). Contributed by GianlucaCh. |
| **25** *(CEN+)* | **CEN+ Scenario Control (Scheduler)** | `CEN Frames for Scenario Scheduler` / `WHO_15-25.pdf` | **v1.0.0 (2010-10-01)** | 🟢 | `myhome_cenplus_event`, `myhome_scenario_plus_event`, device triggers | Extended CEN+ protocol for MH200/MH200N/03565: Short pressure (<0.5s), start of extended pressure (>=0.5s), extended pressure holding, release after extended pressure, rotary dials, and Scenario Plus actions (`WHAT = 11..15`). Contributed by GianlucaCh. |
| **25** *(Contacts)* | **Dry Contact & IR State Functions** | `DRY CONTACT AND IR STATE FUNCTIONS` / `WHO_25.pdf` | **v1.0.0 (2010-11-04)** | 🟢 | `binary_sensor`, `switch` | Dry contact interfaces & IR sensor state: State ON / IR detection (`WHAT=31`), State OFF / IR not detected (`WHAT=32`), plus per-device **Invert state** configuration switches. BTicino 3477, F428, 3480, F482, IR 4610/4611/4640; Legrand 573996, 03553, 067513, etc. Contributed by GianlucaCh. |
| **HMAC** | **Gateway Security & Authentication** | `Hmac Specification` / `Hmac.pdf` | **v1.1.0 (2016-08-05)** | 🟢 | Core transport | Cryptographic challenge-response HMAC-SHA256 authentication replacing legacy OPEN numeric password authentication for modern gateways (`F454`, `F455`, `F461`, `MH202`, `MyHomeServer1`). Contributed by GianlucaCh. |
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

### 2. Multi-Room Sound System (`WHO = 16`) vs Sound Diffusion (`WHO = 22`)

`WHO = 16` and `WHO = 22` are two distinct OpenWebNet audio dialects encoding overlapping concepts with different commands, addresses, and dimensions:

1. **WHO = 16 (Sound System — Amplifiers, Matrix Routing & Sources)**:
    - Original BTicino sound-system dialect (`WHO_16.pdf` v1.0.1, `bt_difson` gateway daemon) covering **both** room amplifiers (`01`–`99`), F441/F441M environment matrix routing (`1ES`), **and** audio source / F500 / F500N FM tuner devices (`101`–`109`).
    - Functions include: Zone power (`WHAT = 0/3/10/13`), volume (`0–31` scale), environment-to-source routing (`*16*3*1ES##`), tone equalizers, FM tuner frequency (`*#16*10S*#6*0*<KHZ>##`), presets (`*#16*10S*#7/<station>##`, `*#16*10S*#10*<slot>##`), seek, and RDS (`*#16*10S*8*...##`).
    - Mapped in Home Assistant to zone `media_player` entities (plus dynamic streaming proxy) and tuner `media_player` / `number` / `button` entities.

2. **WHO = 22 (Sound Diffusion / Multimedia — Structured Later Dialect)**:
    - Later multimedia dialect (`WHO_22.pdf` v1.1.0, `bt_multi` gateway daemon) using tagged addresses for sources (`2#<S>` / `5#2#<S>`), speakers (`3#<A>#<P>`), and speaker areas (`4#<A>`). Dual-stack gateways (`MH200N`, `MH202`, `F454`, `H4890`, `H/L4684` FW ≥ `6.0.0`) emit both dialects; newer gateways (`MyHomeServer1`, `F459`, `F460`, `F461`) ship `bt_multi` (`WHO = 22`) only.
    - Functions include:
        - FM tuner frequency search/tune (`WHAT = 5` / `WHAT = 6`), station preset navigation (`WHAT = 9` / `WHAT = 10`), preset storage (`WHAT = 33`), frequency/station telemetry (`Dimension 5 / 6 / 11`), and RDS text (`WHAT = 31` / `32`, `Dimension 10`).
        - Active-source area notifications (`WHAT = 1/2/21/23#4#<area>`) and speaker volume/tone controls (`WHAT = 3` / `4`, `Dimension 1–4`).
    - In Home Assistant, declared F500 / F500N FM tuners (`media_player`, `number`, `button`) are controlled and decoded across **both** `WHO = 16` and `WHO = 22` (including `WHO = 22`-only gateways), and dual-stack gateways also track `WHO = 22` active-source area routing (`4#<area>`) alongside `WHO = 16` room amplifier zones.

---

### 3. Actuator Diagnostics & Endpoint Safety Locks (`WHO = 14`)

`WHO = 14` is published inside `WHO_1.pdf` (`Who = 1 LIGHTING`, v1.1.0, 2014-11-17, Chapter 4 *"WHO 14"*, §4.2 *"WHERE Table"*, p. 21 and §5.1 *"Command session - Special Commands"*, p. 22) rather than as a standalone `WHO_14.pdf`. Its frame grammar, gateway translation rules, and per-generation actuator behaviour have been cross-verified across the QEMU Firmware Oracle and live hardware captures (`F461` `2.0.11`, `MH200N` `1.1.8`, `MH201` `3.3.6` & `3.6.44`, `F454` `2.0.51`, `MH202` `1.0.24`):

- **Protocol Function**: Inhibits physical wall pushbuttons and remote OpenWebNet/SCS actuation on `WHO = 1` lighting and `WHO = 2` shutter actuators for safety, maintenance, window-contact interlocks, or child locking. Lock state lives in volatile actuator RAM and clears on a mains bus power cycle.
- **Lock Actuator (Disable Controls)**: `*14*0*<WHERE>##` (SCS opcode `11 00`) — freezes lighting outputs (or immediately opens motorized shutter relays) and blocks physical switch inputs and bus actuation writes.
- **Unlock Actuator (Enable Controls)**: `*14*1*<WHERE>##` (SCS opcode `10 00`) — restores normal wall-button and bus operation.
- **Published `WHERE` Scopes (`WHO_1.pdf` §4.2, p. 21 & §2.3, p. 7)**: Point-to-point (`<A><PL>`), Environment/Area (`00`, `1`–`9`, `100`), and General (`0`) per `WHO_1.pdf` §4.2, plus `F422` local-bus interfaces (`<WHERE>#4#<Int>`, `#4#01`–`#4#09` and `#4#11`–`#4#15` per `WHO_1.pdf` §2.3, verified across all 13 emulated gateways on `*14*0*31#4#01##` / `*14*0*31#4#02##` in the firmware oracle `interface-routing` suite and on live `F461` / `MH200` + `F422` hardware). *(Note: `WHO_1.pdf` §4.2 does not publish `#1`–`#255` group addresses for `WHO = 14`, and group `WHO = 14` frames have not been replayed in the firmware oracle; Home Assistant resolves configured `#G` groups defensively if seen. `WHO = 14` is SCS-only and does not apply to `#9` ZigBee endpoints.)*
- **No Status Query (`*#14*<WHERE>##` Rejected on Live Gateways)**: Although `*#14*<WHERE>##` matches generic OpenWebNet `*#WHO*WHERE##` syntax (`WHO_1.pdf` §5.1 notes no monitor-session answer for point `A;PL`, and `*#14*<WHERE>##` has not yet been replayed in the firmware oracle), **every live gateway tested rejects `*#14*<WHERE>##` with `*#*0##` NACK and transmits zero SCS bus traffic** (`50–52 ms` on `F461` `2.0.11`, immediate `NACK` on `MH200` `2.1.0` and `MH201` `3.3.6` / `3.6.44`). `WHO = 14` is strictly write/event-only on SCS.
- **Home Assistant Integration**: Exposed via per-device configuration buttons (`DisableCommandButtonEntity` / `EnableCommandButtonEntity`: `button.<name>_lock` and `button.<name>_unlock`) and tracked bidirectionally via the `actuator_locked` state attribute across inbound `*99*1##` events, `*#*1##`-acknowledged outbound `WHO = 14` commands, and `WHO = 2` `Dimension 10` `SHUTTER_INFO` telemetry ([#976](https://github.com/OpenWebNet-HA/MyHOME/issues/976)).

#### Gateway `WHO = 14` Command & Event-Session Matrix

Event-session (`*99*1##`) echoing of `*14*0*<WHERE>##` / `*14*1*<WHERE>##` is a **two-factor product** of gateway firmware and whether the physical actuator or `F422` local-bus interface transmits SCS `B8 <addr> 11/10 00` back onto the main bus:

| Gateway Family | Command Session (`*14*0/1*<WHERE>##`) | Status Query (`*#14*<WHERE>##`) | Event Session (`*99*1##`) Echo of `*14*0/1*<WHERE>##` |
| :--- | :--- | :--- | :--- |
| **`MH202`** (`1.0.24`, `coso`) | Immediate `*#*1##` ACK in empty-bus emulation | `*#*0##` NACK *(not yet replayed in oracle)* | **Always echoed** — synthesized locally by `coso` even on an empty bus (including `#4#<Int>` local-bus frames). |
| **`F460`, `F461` (`2.0.11`), `MyHomeServer1`, `3488`** (`bt_luci` + `empty_bus_txack`) | `*#*1##` ACK in `~90–127 ms` when populated; **`*#*0##` NACK after `~2.05 s`** (`3×` retry) on unpopulated addresses | `*#*0##` NACK (`50–52 ms` on live `F461`, zero SCS traffic) | **Actuator / local-bus dependent** — echoed only when the target emits SCS `B8 <addr> 11/10 00` (echoed on `F411/2`, `F411/4`, General `0`; **omitted** behind `F422` `#4#II` local-bus interfaces and on 1st-gen actuators). |
| **`F453`, `F453AV`, `F454` (`2.0.51`), `F459`, `H4684`, `MH200` (`2.1.0`), `MH200N` (`1.1.8`)** (`bt_luci` `pic_ack`) | `*#*1##` ACK via PIC bus controller (`pic_ack` in empty-bus emulation; mediated by PIC TX-ACK on physical SCS) | `*#*0##` NACK (verified live on `MH200` `2.1.0`) | **Actuator / local-bus dependent** — echoed when the actuator emits `B8 <addr> 11/10 00` (e.g. `F418U2` / dimmer `55`), **omitted** on 1st-gen `L4671/2`, legacy `MH200` / `MH200N` relays, and `#4#II` local-bus interfaces. |
| **`MH201`** (`3.3.6` & `3.6.44`, `OPEN_SERVICE`) | Immediate `*#*1##` ACK (locks/unlocks actuator on SCS) | `*#*0##` NACK (verified live) | **Never echoed** — `OPEN_SERVICE` never relays `WHO = 14` onto `*99*1##`. |
| **`F450`** (`2.0.10`, thermo/BACnet) / **`F455`** (`1.0.76`) | **`*#*0##` NACK** (`F450` drops `WHO = 14` silently) | `*#*0##` NACK | Never echoed. |

#### Actuator Hardware Generations While Locked

Physical BTicino / Legrand actuators implement SCS lock (`11 00`) across three distinct hardware generations:

| Generation & Representative Hardware | Lock Entry Bus Behaviour (`*14*0*<WHERE>##`) | Behaviour While Locked on Actuation Writes (`WHO = 1` / `WHO = 2` / Scopes) | Behaviour While Locked on Status Queries (`*#1*<WHERE>##` / `*#2*<WHERE>##`) |
| :--- | :--- | :--- | :--- |
| **1st-Gen (Transceiver-Level Lock)**: `L4671/2`, `H4671/2`, `AM5851/2` (`Object 7`), legacy relays | Opens motor relay immediately and emits `*2*0*<WHERE>##`; **omits** `B8 <addr> 11 00` (`*14*0*<WHERE>##`). | **Silent on point & `#G` group writes; stationary `*2*0*<WHERE>##` on Area** — stays physically still; emits zero bus frames on point-to-point and `#1` group commands, and answers Area commands immediately with stationary `*2*0*<WHERE>##` (verified on `MH201` `3.6.44` + `L4671/2` `64`). | **100 % silent on SCS** — ignores `*#2*<WHERE>##` (`*#*0##` NACK on `MH201` / `MH200N` / `F461` / `MyHomeServer1` / `F453`; bare `*#*1##` ACK with zero frames on `F454` / `F459` / `MH202`). |
| **2nd-Gen (Output-Stage Lock)**: `F411/2`, `F411/4`, `F418U2` / `F414` dimmers | Emits `B8 <addr> 11/10 00` (`*14*0/1*<WHERE>##` on `bt_luci`); `F411/2` shutters also emit `*2*0*<WHERE>##` (`+54 ms`). | **Ignores writes** (`WHO = 1`, `WHO = 2`, `Dimension 1`, `#1` / Area / General); only the gateway's own `*2*1000#0/1/2*` command translations appear on `F454` / `F461` / `MyHomeServer1`. | **Answers with frozen state** — `*#2*<WHERE>##` returns `*2*0*<WHERE>##`; `*#1*<WHERE>##` and `*#1*<WHERE>*1##` return frozen `on`/`off` and brightness. |
| **3rd-Gen Positioned Shutters (`Object 218`)**: `F401`, `K4672M2S`, `H4661M2` / `LN4661M2` | Halts motor and transitions `Object 218` (*Shutter actuator*) to disabled state. | Blocks `*2*1/2*<WHERE>##` and `Dimension 11` (`GoToLevel`); reports `Dimension 10` `SHUTTER_INFO = 14` (*command not executed*) or `12` / `13`. | Answers `*#2*<WHERE>*10##` (`*#2*W*10*STATUS*LEVEL*PRIO*INFO##`) with **`SHUTTER_INFO = 12`** (*PUL + disabled*) or **`13`** (*disabled*), and **`0`** (*normal*) / **`15`** (*PUL*) when unlocked. |

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
4. **Zero-Delay Bus Processing**: Fully compatible with the stateless `myhome_cen_event` and `myhome_cenplus_event` events dispatched by the modernized MyHOME integration.

---

## 🔌 Gateway Architecture & Hardware Profiles

The table below outlines supported hardware gateways, session concurrency, inter-frame pacing requirements, and authentication mechanisms (automatically synchronized with `OWNd` profiles):

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
| **F452 / F453 / F453AV** | OpenWebNet | 1 worker | 150 ms | ✅ Port 49153 | Audio/video & web server gateway |
| **H4684 / H/L4684** | OpenWebNet | 1 worker | 50 ms | ✅ SSDP | Colour touch screen IP gateway (Axolute / LivingLight) |
<!-- GATEWAY_PROFILES_END -->

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
    *Examples*:

    - `*#4*1*0##` (Query measured temperature of Zone 1)
    - `*#4*1*60##` (Query relative humidity of Zone 1)

3. **Dimension Writing (Command with Parameters)**:
    ```text
    *#WHO*WHERE*#DIMENSION*VAL1*VAL2*...*VALn##
    ```
    *Example*: `*#16*1*#1*30##` (Set volume of audio Amplifier 1 to step 30 of 31, ~97%)

4. **Dimension Response (Status Report)**:
    ```text
    *#WHO*WHERE*DIMENSION*VAL1*VAL2*...*VALn##
    ```
    *Examples*:

    - `*#4*1*0*0215*1##` (Zone 1 temperature is 21.5 °C, Heating mode)
    - `*#4*1*60*55##` (Zone 1 relative humidity is 55%)

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
3. Click **`📋 Copy Trace`** or **`💾 Export Trace`** to export a sanitized capture with the exact bus frames.
