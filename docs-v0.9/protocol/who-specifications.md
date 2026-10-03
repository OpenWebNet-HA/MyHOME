# OpenWebNet Protocol & WHO Specifications Archive

Welcome to the **OpenWebNet Protocol & WHO Specifications Archive**. This document serves as the official open-access registry of technical manuals, frame syntax, dimension definitions, and specifications published by BTicino / Legrand for the OpenWebNet protocol across MyHOME systems.

Historically, these technical specifications were distributed through the *MyOpen Community* portal (`myopen-legrandgroup.com` / `myopen-bticino.it`), which is no longer active. To ensure permanent access to accurate protocol documentation, this centralized registry preserves the complete protocol reference.

---

## 📚 Master WHO Family Inventory

The table below catalogs every known OpenWebNet function family (`WHO`), its official Legrand document title, current known version, archive status, and Home Assistant platform mapping:

| WHO | Subsystem / Function | Official Document Title | Known Version & Date | Status | Home Assistant Entity | Notes & Supported Hardware |
|:---:|:---|:---|:---:|:---:|:---|:---|
| **0** | **Scenarios (Basic)** | `Open Web Net Language (Scenarios)` | v2.0.0 (2010-10-01) | Verified | `event`, automations | 32 standard scenarios (03551, 88301, F420, IR 3456). |
| **1** | **Lighting** | `Who = 1 LIGHTING` | v1.1.0 (2014-11-17) | Verified | `light` | ON, OFF, Dimming (1-100%, 10 levels, steps), Blink, Timer, Speed of transition. DALI tunable white / RGBW via F429/F429G. |
| **2** | **Automation** | `Messages - Automation` | v1.0.0 (2015-11-12) | Verified | `cover` | Roller shutters, venetian blinds, motorized curtains, gates. Standard UP/DOWN/STOP and absolute positioning percentage (Legrand 67557). |
| **3** | **Load Control (Legacy)** | `OpenWebNet_Community_3_LoadControl` | v1.0.0 (2006) | Verified | `switch`, `sensor` | Priority-based load disconnection central unit (F421). Inhibit/force actuators. |
| **4** | **Thermoregulation** | `Open Web Net Language - Heating adjustment` | v2.0.0 (2013-11-27) | Verified | `climate`, `sensor` | 4-zone / 99-zone central units (3550), standalone thermostats (L/N/NT4691), external probes (3475). |
| **5** | **Burglar Alarm** | `MyHome Burglar Alarm` | (2008-02-13) | Verified | `alarm_control_panel` | Central units (3485, 3486), partition arming/disarming, panic alarms, gas/water technical alarms. |
| **6** | **Door Entry Call & Lock** | `OpenWebNet_Community_DoorEntry` | v1.0.0 (2006) | Verified | `lock`, `switch` | Audio door entry calls, door lock release (`*6*10*<WHERE>##`), staircase light, camera switching. |
| **7** | **Video Door Entry / Multimedia** | `Open Web Net WHO=7` | v1.0.1 (2011-12-01) | Verified | `camera` | Video session establishment, camera selection, video stream routing over IP for Video Server F453AV. |
| **9** | **Auxiliary Channels** | `OpenWebNet_Community_Auxiliary` | v1.0.0 (2006) | Verified | `switch` | Auxiliary channels (AUX 1 to AUX 9) for triggering remote relays or annunciators without occupying lighting addresses. |
| **13** | **Gateway Management** | `OpenWebNet_Community_2_device` | v1.0.0 (2006-06-13) | Verified | Diagnostics | Date/time synchronization (`*#13**0*...`), firmware version query, IP configuration, MAC address, uptime, reboot. |
| **14** | **Actuator Safety Lock** | `Light & Shutter Actuators Lock` | v1.0.0 (2008) | Verified | Diagnostics, `button`, `lock` | Physical endpoint lock/unlock. Lock (`*14*0*<WHERE>##`), Unlock (`*14*1*<WHERE>##`). Inverts/locks physical wall buttons. |
| **15** | **CEN Scenario Control** | `CEN Frames for Scenario Scheduler` | v1.0.0 (2010-10-01) | Verified | `event`, device triggers | Pushbutton scenario events for Scenario Scheduler (MH200, MH200N, Legrand 03565): Short press, extended pressure, release. |
| **16** | **Sound Distribution** | `OpenWebNet_Community_4_soundsystem` | v1.0.1 (2011-11-24) | Verified | `media_player` | Multi-room audio matrix & zone control: Amplifier ON/OFF, volume control, audio source routing, tone equalizers (F441, F450, 3487). |
| **17** | **Scenario Programmer (Scenes)** | `Who = 17 SCENES` | v1.0.0 (2015-04-09) | Verified | `switch`, `event` | MH200 / MH200N / MH202 scenario programmer integration, enable/disable automated schedules. |
| **18** | **Energy Management** | `Energy Management Functions` | v1.0.0 (2011-07-15) | Verified | `sensor` | Electricity, water, and gas pulse meters. Instantaneous power (W), active energy (kWh), current (mA), voltage (V) (F520, F522, 3522). |
| **22** | **Sound Diffusion** | `Who = 22 Sound Diffusion` | v1.1.0 (2014-06-12) | Verified | `media_player` | Source navigation & speaker control: Radio FM frequency step up/down, station presets, track skipping, RDS display streaming. |
| **24** | **Lighting Management** | `Who_24_eng_PUBBLIC.doc` | v1.0.0 (2012-04-06) | Verified | `light`, `sensor` | Legrand Lighting Management System (BMNE500, BMview). Room controllers, lux thresholds, auto-off delay timers. |
| **25** *(CEN+)* | **CEN+ Scenario Control** | `CEN Frames for Scenario Scheduler` | v1.0.0 (2010-10-01) | Verified | `event`, device triggers | Extended CEN protocol with up to 256 buttons/scenarios for MH200/MH200N/03565. |
| **25** *(Contacts)* | **Dry Contact & IR State** | `DRY CONTACT AND IR STATE FUNCTIONS` | v1.0.0 (2010-11-04) | Verified | `binary_sensor` | Dry contact interfaces & IR sensor state: State ON / IR detection (`WHAT=31`), State OFF (`WHAT=32`). BTicino 3477, F428, 3480, 4610. |
| **HMAC** | **Gateway Authentication** | `Hmac Specification` | v1.1.0 (2016-08-05) | Verified | Core transport | Cryptographic challenge-response HMAC-SHA256 authentication replacing legacy OPEN numeric password authentication. |
| **INTRO** | **OpenWebNet Architecture** | `INTRODUCTION Examples of Integration` | (2012-10-03) | Verified | Core protocol | Foundational architecture manual detailing frame delimiters, session separation (Command vs Event/Status), ACK/NACK signaling. |

---

## 🔍 OpenWebNet Frame Syntax Reference

OpenWebNet messages always begin with `*` and end with `##`. Fields are separated by `*`:

### 1. Standard Command / Status Message
```text
*WHO*WHAT*WHERE##
```
*Example*: `*1*1*21##` (Turn ON light at address A=2, PL=1)

### 2. Dimension Request (Query)
```text
*#WHO*WHERE*DIMENSION##
```
*Example*: `*#4*1*0##` (Query measured temperature of Zone 1)

### 3. Dimension Writing (Command with Parameters)
```text
*#WHO*WHERE*#DIMENSION*VAL1*VAL2*...*VALn##
```
*Example*: `*#16*1*#1*30##` (Set volume of audio Amplifier 1 to 30%)

### 4. Dimension Response (Status Report)
```text
*#WHO*WHERE*DIMENSION*VAL1*VAL2*...*VALn##
```
*Example*: `*#4*1*0*0215*1##` (Zone 1 temperature is 21.5 °C, Heating mode)

### 5. Acknowledge (ACK / NACK)
- `*#*1##` : **ACK** (Command accepted by gateway/bus)
- `*#*0##` : **NACK** (Command rejected, syntax error, or buffer busy)

---

## 🔌 Gateway Architecture & Hardware Profiles

| Model | Hardware Type | Transport | Default Port | Auth Protocol | Queue Pacing | Notes |
|:---|:---|:---|:---:|:---|:---:|:---|
| **MH200** | Scenario Programmer | TCP/IP | 20000 | Open password / None | 150 ms | Embedded ARM, scenario scheduler, legacy buffer limits. |
| **MH200N** | Scenario Programmer | TCP/IP | 20000 | Open password / None | 100 ms | Updated network interface, scenario engine. |
| **MH201** | Scenario Controller | TCP/IP | 20000 | Open password / None | 80 ms | DIN-rail scenario controller. |
| **MH202** | Scenario Programmer | TCP/IP | 20000 | Open password / None | 50 ms | High-speed ARM CPU, expanded memory. |
| **F452** | Web Server IP | TCP/IP | 20000 | Open password / None | 150 ms | Early generation IP gateway. |
| **F453AV** | Audio/Video Web Server | TCP/IP | 20000 | Open password / None | 120 ms | Supports door entry and basic web control. |
| **F454** | Web Server IP | TCP/IP | 20000 | Open numeric password | 80 ms | Dual bus interface, widely deployed standard DIN gateway. |
| **F455** | Basic IP Gateway | TCP/IP | 20000 | Open numeric password | 50 ms | Single SCS bus basic gateway (lights, automation, temperature, energy). |
| **MyHomeServer1** | Modern IoT Gateway | TCP/IP | 20000 | **HMAC-SHA2 (SHA-256)** | 30 ms | Fast SoC, alphanumeric credentials. |
| **F461** | Next-Gen DIN Server | TCP/IP | 20000 | Alphanumeric / HMAC | 20 ms | Latest generation BTicino DIN-rail server/gateway. |
| **Legrand 3578** | OpenZigBee USB Interface | Serial USB | `/dev/ttyUSB*` | None (Serial bypass) | 40 ms | 19200 baud, 8N1, ZigBee wireless SCS bridge. |
