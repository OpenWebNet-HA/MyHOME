# OpenWebNet Protocol Conformance & Capability Matrix

> **Authoritative Specification & Verification Baseline**  
> *Last Updated: November 17, 2026*  
> *Corpus Version: 2.0.0 (455 Fixtures, 21 Subsystems, 1,100+ Automated Conformance Assertions)*

This document serves as the **verifiable truth** for OpenWebNet protocol conformance across the Home Assistant (`OpenWebNet-HA/MyHOME`) and Python protocol driver (`OpenWebNet-HA/OWNd`) ecosystem.

---

## 1. The Verification Authorities

To prevent divergence and eliminate guesswork, every protocol frame and capability is evaluated against four authorities:

| Authority | Reference Implementation | Role & Authority |
|---|---|---|
| **1. The Judge** | Legrand OpenWebNet Official Specification Manuals (interrogated via `openwebnet-mcp`) | **Legal Grammar**: Defines whether a frame syntax, dimension boundary, or parameter structure is strictly valid per Legrand standards. |
| **2. The Reference Oracle** | `openwebnet4j` & openHAB OpenWebNet Binding (by Massimo Valla) | **Wire Baseline**: Provides empirical on-wire factory strings, real-world edge cases, and 10+ years of mature production testing. |
| **3. The Firmware Oracle** | `own-firmware-oracle` (`tests/golden/firmware_oracle.json`, 520 inputs across 12 emulated gateways) | **Hardware Truth**: Provides hash-pinned gateway replies, SCS bus forwarding verdicts, and crash boundaries from real gateway binaries. |
| **4. The SUT** | `OWNd` protocol engine & `custom_components/myhome` runtime | **System Under Test**: Must parse, validate, roundtrip serialize, and build frames with 100% fidelity. |

---

## 2. Subsystem Conformance & Capability Matrix

| WHO | Subsystem | Verified Operations | Canonical Frame Examples | Judge (Legrand PDF) | Oracles & Captures | Runtime Status |
|:---:|---|---|---|:---:|:---:|:---:|
| **-** | **Signaling** | Gateway ACK / NACK | `*#*1##`, `*#*0##` | Valid | Matched | **VERIFIED (100%)** |
| **0** | **Scenarios** | Activate, Stop, Execute, Programmer Lock/Unlock | `*0*1*1##`, `*0*0*5##`, `*0*2*05##`, `*0*41*01##` | Valid | Matched + Live Captures | **VERIFIED (100%)** |
| **1** | **Lighting** | ON/OFF, Status, Speed Dimming, Private Bus (`#4#01`), DALI DT8 Tunable White, RGB/HSV | `*1*1*0311#4#01##`, `*1*1#5*12##`, `*#1*99*#1*150*0##` | Valid | Matched + Live Captures | **VERIFIED (100%)** |
| **2** | **Automation** | Shutter UP/DOWN/STOP, Bus Routing (`#4#01`), Go-to-level Position % (`#11#PRIORITY`) | `*2*1*21#4#01##`, `*#2*93*#11#001*75##`, `*2*2*93##` | Valid | Matched + Live Captures | **VERIFIED (100%)** |
| **3** | **Load Control** | Enabled priority states (`#1..#8`), Force priority (`*3*2*#1##`), priority status and CU measurement requests (`Dims 0..4`, probe-only) | `*3*1*#8##`, `*3*1*#1##`, `*3*2*#1##`, `*#3*10*0##` | Valid | Firmware Oracle + Capture | **VERIFIED (100%)** |
| **4** | **Thermoregulation** | Probe queries (`21.5°C`), Setpoints, Knob offset (`Dim 13`), 0–10 V & Fan-coil (`Dim 19/20`), CU `#0` | `*#4*1*0*0215##`, `*#4*88*13*13##`, `*#4*1#3*20*100##`, `*4*3100*#0##` | Valid | Matched + Live Captures | **VERIFIED (100%)** |
| **5** | **Burglar Alarm** | Zone/CU status, Intrusion & battery events, Empty/star-`WHERE` alarm status frames | `*5*1*0##`, `*#5*2##`, `*5*11*#2##`, `*5*1*##` | Valid | Matched + Live Captures | **VERIFIED (100%)** |
| **6** | **Door Entry** | Door lock release, Staircase light, Camera ON (probe-only), Free-channel short frames, Open lock in conversation | `*6*10*4000##`, `*6*10*4003##`, `*6*9##`, `*6*22*1##` | Valid | Live Captures | **VERIFIED (100%)** |
| **7** | **Multimedia** | External camera video stream open and close (`OWNAVCommand`) | `*7*0*4000##`, `*7*9**##` | Valid | Legrand PDF only (no capture) | **VERIFIED (100%)** |
| **8** | **Video Intercom** | Internal/External audio-video calls, Pager broadcast and Status reads (probe-only), Entrance-panel calls, Caller-address report | `*8*1#1#4#20*11##`, `*8*1#1#4*74##`, `*8*9#1#4*20##`, `*8*1#14#2#11*4##` | Valid | Firmware Oracle + Capture | **VERIFIED (100%)** |
| **9** | **Auxiliary** | Relay ON/OFF indications and generic status queries across auxiliary channels | `*9*1*4##`, `*9*0*4##`, `*#9*1##` | Valid | Matched + Live Captures | **VERIFIED (100%)** |
| **13 / 1013** | **Gateway Mgmt** | Time/Date/Timezone (`999` & empty TZ), Firmware/Kernel/Distro, WHO 1013 object model | `*#13**0##`, `*#13**15##`, `*#13**#0*14*59*05*##`, `*#1013**1*67*15*5*0##` | Valid | Firmware Oracle + Capture | **VERIFIED (100%)** |
| **14** | **Actuator Lock** | Point-to-point & `#4#01` routed actuator lock (`0`) / unlock (`1`), General unlock event | `*14*0*11##`, `*14*0*07#4#01##`, `*14*1*0##` | Valid | Firmware Oracle + Capture | **VERIFIED (100%)** |
| **15** | **CEN Scenarios** | Press, short release, long release, extended hold (`#1/#2/#3`), 4-digit source addresses | `*15*02*11##`, `*15*02#1*11##`, `*15*02#2*11##`, `*15*03#3*0512##` | Valid | Firmware Oracle + Capture | **VERIFIED (100%)** |
| **16** | **Sound System** | Source ON/OFF, F441M matrix routing, Amplifier volume (`Dim 1`), RDS (`Dim 8`) | `*16*3*101##`, `*16*3*111##`, `*#16*23*1*15##`, `*#16*23*#1*15##` | Valid | Firmware Oracle + Capture | **VERIFIED (100%)** |
| **17** | **Scene Programmer** | MH200N / MH202 scene start, stop, enable, disable & status requests | `*17*1*30##`, `*17*2*30##`, `*17*3*30##`, `*#17*0##` | Valid | Firmware Oracle + Capture | **VERIFIED (100%)** |
| **18** | **Energy Mgmt** | Active power (`113` W), Cumulative/Monthly/Daily Energy (Wh) (`51..54`), Stream `1200`, Stop & Go | `*#18*51*113##`, `*#18*51*51*8151646##`, `*#18*56*#1200#1*2##`, `*18*21*11##` | Valid | Firmware Oracle + Capture | **VERIFIED (100%)** |
| **22** | **Sound Diffusion** | Multi-parameter speaker/source control, MH200N dual-frame mirror events, Volume/Preset/State | `*22*1#4#1*3#1#1##`, `*22*2#4#1*5#2#2##`, `*#22*3#1#1*#1*17##` | Valid | Firmware Oracle + Capture | **VERIFIED (100%)** |
| **25** | **CEN+ / Dry Contacts**| Short press; start, hold and release of an extended press (`2OBJECT`); ScenarioPlus; dry contacts | `*25*21#1*21##`, `*25*22#1*21##`, `*25*12*131##`, `*25*31#1*339##` | Valid | Matched + Live Captures | **VERIFIED (100%)** |
| **1001** | **Lighting Diag** | Actuator autodiagnostic 24-bit (`Dim 7` / `Dim 11`) bitmasks, Object model/FW/ID, Status request (probe-only) | `*#1001*0*7*111111111111111101101111##`, `*#1001*74*11*111110111111111111110111##`, `*#1001*0*2*1*3*8##` | Valid | Live Captures | **VERIFIED (100%)** |
| **1004** | **Thermo Diag** | Central/Probe/Zone 24-bit (`Dim 7/11`) & 16-bit (`Dim 20/21/22`) masks, Fault counters (`23`) | `*#1004*#0*7##`, `*#1004*66*11*111111111111111111111111##`, `*#1004*#66*22*1111111111111111##`, `*#1004*#0*23##` | Valid | Live Captures | **VERIFIED (100%)** |

A `probe-only` operation is a command or request that a contributor sent to an F461 (firmware 2.0.11) and that the gateway answered with NACK; no stored trace receives it. The matrix counts it as parsed and round-tripped by OWNd, not as a function confirmed on hardware. The fixture notes in `tests/golden/frames/` name the trace behind each capture.

Sources for the examples changed in [OWNd#77](https://github.com/OpenWebNet-HA/OWNd/issues/77). The MyHomeServer1 firmware, run on an emulated bus, produces a bus frame for each of them and refuses the examples they replace. The WHO 15 press and short release (`*15*01*73##`, `*15*01#1*73##`) were captured on an MH200N + MyHomeServer1 bus in [MyHOME#541](https://github.com/OpenWebNet-HA/MyHOME/issues/541). Long release and extended hold (`#2`, `#3`) are verified in both firmware and the four-digit `0512` capture in [MyHOME#624](https://github.com/OpenWebNet-HA/MyHOME/issues/624). The WHO 25 examples are MH201 and H4890 captures from [MyHOME#418](https://github.com/OpenWebNet-HA/MyHOME/issues/418) and [MyHOME#466](https://github.com/OpenWebNet-HA/MyHOME/issues/466). The WHO 13 reads use the published empty-`WHERE` form `*#13**DIMENSION##` (Encyclopedia `who-13-integration-gateway/dimensions.md` "Read form"): in `own-firmware-oracle` (`ownd-audit-frames` and `who13-clock`) all emulated gateways answer `*#13**0##` and `*#13**22##` with ACK and the current time, while the `WHERE 0` spelling `*#13*0*0##` is `nack, silent`; `OWNGatewayCommand.request_time()` and the event keepalive emit `*#13**0##`.

---

## 3. Key Architectural Truths & Design Decisions

### 3.1 String Preservation for Hardware Addresses
- **Observation**: Addresses such as `0311` (Lighting PTP) and `0110` (CEN) begin with leading zeros.
- **Verifiable Truth**: Addresses **must never be converted to integers** (`311` or `110`). In OpenWebNet, leading zeros denote specific address spaces and module wiring. OWNd strictly enforces string types for `where` across all subsystems.

### 3.2 Private Bus Routing (`#4#bus`)
- **Observation**: Installations with multiple physical SCS buses behind an F454 or 3486 gateway use interface routing (e.g. `*1*1*0311#4#01##`).
- **Verifiable Truth**: The interface routing suffix `#4#<interface>` is an addressing qualifier, not a dimension parameter. OWNd extracts this cleanly into `parsed.interface = '01'` and `parsed.where = '0311'`, preserving 100% roundtrip wire string equality.

### 3.3 Strict Standards vs. Tolerant Synthesis (The Alarm WHERE Omission)
- **Observation**: In `openwebnet4j`, frame `*5*7*##` (Alarm) synthesizes `WHERE = '0'` when the address is omitted, while gateways without a panel answer `*#5*0##` with `*5*9*##`.
- **Verifiable Truth**: OWNd parses empty-`WHERE` alarm status frames (`*5*9*##`) with `where == ""` so consumers never synthesize a phantom zone `0` device, while outbound command builders require an explicit address.

### 3.4 Extended Thermoregulation (Dimension 12 Setpoint After Local Offset)
- **Observation**: Dimension 12 is the complete probe status: its value is the zone's setpoint after the local offset, not a measured temperature (Encyclopedia `who-4-temperature-control/dimensions.md`, "DIMENSION 12 - complete probe status": `*#4*WHERE*12*T*3##`, `T` in `0020..0430`; measured temperatures are Dimension 0). The openwebnet4j fixture `*#4*6*12*1048*3##` carries a signed value the published range does not include.
- **Verifiable Truth**: The value parameter `1048` decodes as `-4.8°C` with the `SXXX` sign rule (the initial `1` indicates negative sign; `048` is tenths of a degree). OWNd stores it in `local_set_temperature` (`message_type` `local_target_temperature`); `main_temperature` stays `None`.

---

## 4. Independent Local Verification

Any contributor or user can independently verify the conformance suite:

```bash
# Run automated assertions against the 455-fixture golden corpus
python -m pytest tests/test_golden_conformance.py tests/test_golden_privacy.py

# Run non-mutating schema, privacy & corpus.json sync validation
python tools/golden/validate_corpus.py --check
```
