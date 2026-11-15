# OpenWebNet Golden Corpus Conformance Suite

A declarative conformance suite of OpenWebNet frames providing cross-framework verification across Home Assistant (`MyHOME` / `OWNd`), openHAB (`openwebnet4j` & `org.openhab.binding.openwebnet`), and official Legrand OpenWebNet specifications via `openwebnet-mcp`.

## Verification Authorities

| Layer | Source | Role |
|---|---|---|
| **Judge** | Official Legrand PDFs via `openwebnet-mcp` | Validates whether a frame is syntactically legal (its semantics are not authoritative; see Provenance below) |
| **Oracle** | `openwebnet4j` & openHAB binding (by Massimo Valla) | Provides mature reference factory outputs and empirical test vectors |
| **Firmware Translation Oracle** | `own-firmware-oracle` (`tests/golden/firmware_oracle.json`) | Provides hash-pinned empirical verdicts, gateway reply codes, and bus frames from real emulated BTicino/Legrand gateway binaries |
| **SUT** | `OWNd` / `custom_components/myhome` | System Under Test: verified against judge and oracles |

## Provenance: which fixtures may change

Only `community-plant-capture` fixtures were recorded from a real bus. They are facts: never edit their frame, `what`, `where` or dimension values. If OWNd disagrees with a capture, OWNd is wrong.

Some `community-plant-capture` fixtures are command or request frames that a contributor sent to a gateway and that no stored trace receives. Their note says so. A note that starts its provenance with `probe only` describes a frame the gateway answered with NACK: it records what was sent and refused, not that the function works on that gateway. The meaning given in a capture note is a reading of the PDFs or of the Encyclopedia and can be corrected like any other reading; the frame cannot.

Every other source (`legrand-spec`, `encyclopedia`, `openwebnet4j`, `public-readme`, `mcp-draft`, `conflict`) is a reading of a document or of another implementation, and can be wrong. Such a fixture may be corrected, but only in a commit that names the source it now follows (page and commit) and explains what was wrong.

- `mcp_valid: true` means openwebnet-mcp accepted the **grammar**. It is not evidence of meaning: the judge's WHO 15 catalog described `*15*1*11#2##` as "short press on button 2", which is how three wrong CEN fixtures entered this corpus (corrected in OpenWebNet-HA/OWNd#49). Its WHO 25 catalog did the same for `*25*21#1*12##`, `*25*22#1*12##` and `*25*24#1*12##`: `12` is not a CEN+ Object (`WHERE` is `2` + Object 0..2047). Those were replaced by a capture in the same PR.
- A `builder:` block proves the builder matches the fixture, not that either is right. When both are written in the same change from the same reading, the test only compares the code with itself. Prefer builder parity against a capture or an independent source such as `openwebnet4j`.
- When a capture arrives for a frame that so far exists only as a spec-derived fixture, add the capture and keep the spec entry only if it agrees.

## Canonical Upstream & Staged-Superset Architecture

`OpenWebNet-HA/OWNd` (`tests/golden/`) is the **Canonical Golden Corpus** across the OpenWebNet-HA ecosystem. `OpenWebNet-HA/MyHOME` (`tests/golden/`) mirrors `OWNd` as a **validated strict-superset staging buffer** (`MyHOME >= OWNd`), enforced in CI by `scripts/sync_golden_corpus.py --check` and `tools/golden/check_trace_shape_coverage.py`:

1. Every fixture in `OWNd/tests/golden/` must exist identically in `MyHOME/tests/golden/` (zero missing IDs, zero payload divergence, zero raw-frame collisions).
2. Any new physical bus trace added to `MyHOME/tests/fixtures/` is checked by `tools/golden/check_trace_shape_coverage.py` so no novel protocol shape can land in a trace without a corresponding Golden Corpus fixture.
3. Newly staged `MyHOME` fixtures are promoted upstream to `OWNd` via `python scripts/sync_golden_corpus.py --to-ownd` and pulled downstream via `python scripts/sync_golden_corpus.py --from-ownd`.

## Supported Subsystems Catalog (452 Fixtures)

> Frames marked `community-plant-capture` come from real installations contributed by users. They are the only authority for behaviour the official PDFs leave undefined, and they are kept verbatim except where a fixture note records a deliberate substitution.

- **Signaling (`who00_signaling.yaml`, 2 fixtures)**: Gateway ACK (`*#*1##`) and NACK (`*#*0##`).
- **WHO=0 Scenarios (`who00_scenario.yaml`, 6 fixtures)**: Stored scenario activation (`*0*1*1##`, `*0*2*05##`, `*0*14*95##`), stop (`*0*0*5##`), and scenario programmer lock/unlock states (`*0*41*01##`, `*0*44*01##`).
- **WHO=1 Lighting (`who01_lighting.yaml`, 85 fixtures)**: Point-to-point ON/OFF, status requests, local bus routing (`0311#4#01`), group broadcast, speed transitions (`*1*1#5*12##`), DALI DT8 Tunable White (Dimension 14) and RGB/HSV ballasts, motion/PIR events, and authentic captures across classic BTicino F414 10-level dimmers (`WHERE 99`) and F418U2 universal dimmers on F454, MH200, MH202, F455, and F461 plants.
- **WHO=2 Automation (`who02_automation.yaml`, 53 fixtures)**: Shutter UP/DOWN/STOP, local bus routing (`21#4#01`; the firmware refuses a one-digit interface), and absolute position percentages (Dimension 11 go-to-level `*#2*WHERE*#11#PRIORITY*LEVEL##`; there is no Dimension 10 write and no slat-angle dimension in WHO 2, Encyclopedia `who-2-automation/dimensions.md`). Includes general / area / group scope runs (MH201, MyHOME#433 & #390), LN-4660M2 advanced multi-parameter UP/DOWN/STOP commands and Dimension 10 position reports (MyHOME#445), and F461 5s timed shutter command-translation sequences.
- **WHO=3 Load Control (`who03_load_control.yaml`, 10 fixtures)**: Priority forcing (`*3*2*#1##`), H/L4684 F521 live enabled priority state reports (`*3*1*#8##`, `*3*1*#1##`, MyHOME#466), priority and general status reads (`*#3*#1##`, `*#3*0##`), and central-unit voltage/current/power/energy Dimension `0..4` measurement queries (`*#3*10*0##` .. `*#3*10*4##`). The seven requests are probe-only: an F461 answered each with NACK (the WHO 3 PDF prints the general request as `*#3##`).
- **WHO=4 Thermoregulation (`who04_thermo.yaml`, 84 fixtures)**: Measured temperature queries (`21.5°C`), setpoint writes, Antifreeze/Protection modes, signed Dimension 12 complete-probe-status (`-4.8°C`), Home+Control Dimension 7 zone states and program writes (MyHOME#429), local knob offset reads and live knob rotation broadcasts (`*#4*88*13##` -> `*#4*88*13*13##`, `*4*110*#33##`, `*#4*0#3*20*0##`, `*#4*33#1*20*5##`, MyHOME#466), MyHomeServer1 Sabiana 0–10 V fan-coil `0%` OFF (`*#4*1#3*#20*100##`, `*#4*1#3*20*100##`), proportional `101..200` active output (`*#4*1#3*20*155##`, `*#4*7#1*#20*200##`), extended fan-coil OFF speeds `14..16`, circulation-pump demand release (`*4*4002#2*0#1##`, `*4*4002*2##`), and H4691 humidity (`*#4*1*60*60##`, MyHOME#908), plus 3550 99-zone central unit restore-last-program (`*4*3100*#0##`) and fan-coil speed transitions (MyHOME#912 / OWNd#169).
- **WHO=5 Burglar Alarm (`who05_alarm.yaml`, 15 fixtures)**: Zone and central-unit status requests (`*#5*2##`, `*#5*#2##`, `*#5*0##`), system/zone intrusion and battery events (`*5*1*0##`, `*5*11*#2##`, `*5*18*#5##`), and empty/star-`WHERE` gateway alarm status frames (`*5*1*##`, `*5*0*##`, `*5*1**##`).
- **WHO=6 Door Entry (`who06_door_entry.yaml`, 12 fixtures)**: Electric door lock release (`*6*10*4000##`, `*6*10*4001#2##`), staircase light (`*6*12*4000##`), camera ON (`*6*0*4000##`), free-channel signaling (`*6*9##`, `*6*9**##`), incoming broadcast call (`*6*6*4100##`), open lock with the external unit in conversation (`*6*22*1##`), WHAT 23 / 24 events (`*6*23*4000##`, `*6*24*1##`), and general status poll (`*#6*0##`). Received on MH202, MH200 or H/L4684 buses: `*6*10*4000##`, `*6*10*4003##`, `*6*9##`, `*6*9**##`, `*6*22*1##`, `*6*23*4000##`, `*6*24*1##`. Probe-only (NACK on an F461): `*6*10*4001#2##`, `*6*0*4000##`, `*6*6*4100##`, `*#6*0##`.
- **WHO=7 Multimedia (`who07_multimedia.yaml`, 2 fixtures)**: Camera video stream open (`*7*0*4000##`) and close (`*7*9**##`) via `OWNAVCommand`. Both are read from the WHO 7 PDF, whose frame tables print `*7*0*WHERE*##` with a trailing empty tag and `*7*9**##`; there is no capture of either.
- **WHO=8 Video Door Entry / Intercom (`who08_intercom.yaml`, 8 fixtures)**: Audio/video call initiation (`*8*1#6#2#11*16##`, `*8*1#7#2#11*16##`), pager broadcast (`*8*1#14#2#11*4##`), entrance-panel calls (`*8*1#1#4#20*11##`, `*8*1#1#4*74##`), caller-address report (`*8*9#1#4*20##`), and status/dimension reads (`*#8*11##`, `*#8*11*0##`). The entrance-panel calls and the caller-address report were received on MH202 / MH200 buses; the other five are probe-only (NACK on an F461).
- **WHO=9 Auxiliary (`who09_auxiliary.yaml`, 5 fixtures)**: Activation (`*9*1*1##`, `*9*1*4##`), deactivation (`*9*0*1##`, `*9*0*4##`), and generic status query (`*#9*1##`, `OWNStatusRequest.request`). The channel 4 pair is a live H/L4684 bus capture (MyHOME#466).
- **WHO=13 Gateway Management & WHO=1013 Diagnostics (`who13_gateway.yaml`, 36 fixtures)**: Time, date, IP, netmask, MAC, model code (`15`), firmware (`16`), uptime (`19`), datetime (`22`), kernel (`23`), and distribution (`24`) dimensions, including F461 `2.0.11` naive timezone `999` replies (MyHOME#865), H/L4684 code `23` (MyHOME#466), and MyHomeServer1 `2.87.13` trailing-empty-timezone clock sync (`*#13**#0*14*59*05*##`) and WHO 1013 object model `67` (`*#1013**1*67*15*5*0##`, MyHOME#907/#908).
- **WHO=14 Special Commands / Actuator Lock (`who14_actuator_lock.yaml`, 6 fixtures)**: Point-to-point and `#4#01` routed actuator lock (`*14*0*11##`, `*14*0*07#4#01##`) and unlock (`*14*1*11##`) commands/events, plus general unlock bus capture (`*14*1*0##`, MyHOME#466).
- **WHO=15 CEN Pushbuttons (`who15_cen.yaml`, 8 fixtures)**: Pressure, short release, long release, and extended pressure (`*15*BUTTON[#1|#2|#3]*WHERE##`), including a four-digit source address captured on an MH200N + MyHomeServer1 bus (`0512`, MyHOME#624).
- **WHO=16 Sound System (`who16_sound.yaml`, 30 fixtures)**: Source devices (`101`-`102`), F441M matrix routing (`1` + environment + source), amplifier ON/OFF (`*16*3*11##`, `*16*13*11##`), volume (`DIMENSION 1`), general/source state (`DIMENSION 5`), and RDS (`DIMENSION 8`), captured across MH200, MH200N, and MH202 installations.
- **WHO=17 Scenario Programmer (`who17_scene.yaml`, 17 fixtures)**: MH200N / MH202 scene start (`*17*1*30##`), stop (`*17*2*30##`), enable (`*17*3*30##`), disable (`*17*4*30##`), and status requests (`*#17*0##`, `*#17*1##`). The scene events were received on MH200N and MH200 buses; `*#17*0##` is answered with scene status frames by an MH200, and an F461 answers both requests with NACK (`*#17*1##` is probe-only).
- **WHO=18 Energy Management (`who18_energy.yaml`, 29 fixtures)**: Instantaneous active power (`113`), cumulative (`51`), monthly (`53`), and daily (`54`) integer `Wh` totalizers (MyHOME#907), Dimension `1200` power reporting lifecycle (`#1200#1`, MyHOME#661), and F522/F523 Stop & Go breaker commands and status mask queries (`*18*21*11##`, `*18*22*11##`, `*#18*11*250##`, `OWNStopAndGoCommand`).
- **WHO=22 Sound Diffusion (`who22_sound_diffusion.yaml`, 17 fixtures)**: Multi-parameter WHO 22 speaker/source commands (`*22*1#4#1*3#1#1##`), MH200N dual-frame mirror events (`*22*2#4#AREA*5#2#SOURCE##`), Dimension 1 volume, Dimension 5 frequency and Dimension 6 preset requests, Dimension 10 RDS, and Dimension 12 stereo device state (`*#22*3#1#1*12*1*4##`).
- **WHO=25 CEN+ / Dry Contacts (`who25_cen_plus.yaml`, 11 fixtures)**: CEN+ short press, hold start, hold repeat, and release (`*25*21..24#PUSHBUTTON*2OBJECT##`, captured on MH201), ScenarioPlus ON / OFF via `OWNScenarioPlusCommand` (`*25*11#0*11##`, `*25*12*131##`), a probe-only status request (`*#25*21##`, NACK on an F461 and on an MH200), and H4890 dry-contact events (`*25*31#1*339##`).
- **WHO=1001 Lighting & Automation Diagnostics (`who1001_lighting_diag.yaml`, 7 fixtures)**: 24-bit Dimension 7 (`*#1001*0*7*111111111111111101101111##`) and Dimension 11 (`*#1001*74*11*111110111111111111110111##`) actuator autodiagnostic bitmasks, Dimension 1 object model, Dimension 2 firmware version, Dimension 13 device ID, and a probe-only general status request (`*#1001*0##`, NACK on an F461).
- **WHO=1004 Thermoregulation Diagnostics (`who1004_thermo_diag.yaml`, 9 fixtures)**: Central unit (`#0`), probe (`66`), and zone (`#66`) 24-bit diagnostic bitmasks (`Dimension 7`, `Dimension 11`), 16-bit zone failure/diagnostic masks (`Dimension 20`, `Dimension 21`, `Dimension 22`), Dimension 23 fault counter request (`*#1004*#0*23##`), and a probe-only status inquiry (`*#1004*2##`, NACK on an F461).

## Directory Structure

```text
tests/golden/
  README.md                     # This documentation
  SOURCE.yaml                   # Provenance manifest of authorities
  schema.json                   # JSON Schema (Draft-07) for frame records
  corpus.json                   # Zero-dependency standard library runtime cache
  firmware_oracle.json          # Hash-pinned empirical verdict index from own-firmware-oracle
  frames/
    who00_signaling.yaml        # Bus signaling (ACK/NACK)
    who00_scenario.yaml         # WHO=0 Basic Scenarios
    who01_lighting.yaml         # WHO=1 Lighting
    who02_automation.yaml       # WHO=2 Covers & Shutters
    who03_load_control.yaml     # WHO=3 Load Control
    who04_thermo.yaml           # WHO=4 Heating & Cooling
    who05_alarm.yaml            # WHO=5 Burglar Alarm
    who06_door_entry.yaml       # WHO=6 Door Entry System
    who07_multimedia.yaml       # WHO=7 Multimedia Video Door Entry
    who08_intercom.yaml         # WHO=8 Video Door Entry / Intercom
    who09_auxiliary.yaml        # WHO=9 Auxiliary Relays
    who13_gateway.yaml          # WHO=13 & WHO=1013 Gateway Management
    who14_actuator_lock.yaml    # WHO=14 Actuator Lock
    who15_cen.yaml              # WHO=15 CEN Scenario Buttons
    who16_sound.yaml            # WHO=16 Sound System & AV Matrix
    who17_scene.yaml            # WHO=17 MH200N/MH202 Scenario Programmer
    who18_energy.yaml           # WHO=18 Energy Management & Stop&Go
    who22_sound_diffusion.yaml  # WHO=22 Advanced Sound Diffusion
    who25_cen_plus.yaml         # WHO=25 CEN+ & Dry Contacts
    who1001_lighting_diag.yaml  # WHO=1001 Lighting/Automation Autodiagnostics
    who1004_thermo_diag.yaml    # WHO=1004 Thermoregulation Autodiagnostics
tools/golden/
  validate_corpus.py            # Corpus schema validator (--check supported)
tests/
  test_golden_conformance.py    # Pytest suite running conformance tests
  test_golden_privacy.py        # Pytest suite enforcing PII/LAN/secret scrubbing
  test_firmware_oracle_conformance.py # Pytest suite testing parser resilience against real firmware
docs/
  protocol_conformance_matrix.md # Verifiable truth matrix across all 21 subsystems
```

## Firmware Oracle Dataset (`tests/golden/firmware_oracle.json`)

The firmware oracle fixture captures empirical OpenWebNet protocol execution across the BTicino/Legrand gateway fleet generated by `own-firmware-oracle`.

- **Schema Version**: `1.1.0` (Format `1.0.0`)
- **Digest Pin**: `verdicts_sha256`: `e14901b758acdf4a90e7da2458dac86feb51bcabf8d4224ecc4f835299b61113`
- **Total Unique Input Frames**: 520 frames across 23 test suites
- **Total Verdict Rows**: 6,644 empirical verdicts across 12 emulated gateways (552–556 rows each). The pin is the `results/mcp_index.json` of own-firmware-oracle at the time of the `sound-who22-bare-power` run; the oracle's `main` has since grown (13 emulated gateways with the 3488 HOMETOUCH, 30 suites), a refresh is a separate chore.
- **Fleet Emulation Status**:
  - **12 Emulated**: `F450` (020010), `F453` (010100), `F453AV` (030014), `F454` (020051), `F459` (020105), `F460` (020012), `F461` (020011), `H4684` (020054), `MH200` (020102), `MH200N` (010108), `MH202` (010024), `MyHomeServer1` (028206).
  - **5 Catalogued**: `3550` (030020), `F455` (010102), `L4561N` (040006), `L4686SDK` (012001), `MH201` (030644).

### Key Architectural Behaviors & Empirical Insights

1. **Deterministic Emulation & Crash Rows**: With `own-firmware-oracle`, all 12 emulated gateways complete execution with **0 timeouts** across all 6,644 verdict rows; the only **crash** rows are the 64 of the `sound-who22-bare-power` suite (the bare WHO 22 power frame `*22*1*WHERE##` / `*22*0*WHERE##` on F453, F453AV, F454, F459, F460, F461, MH200N and MyHomeServer1), the evidence behind the send-layer refusal `is_who22_bare_power_frame`.
2. **NACK with SCS Bus Forwarding**: 1,085 rows return NACK at the command session while echoing the telegram onto the SCS bus (predominantly `F460`, `F461`, and `MyHomeServer1` with 228 rows each, `MH202` with 74 rows, `F453`, `F453AV`, and `MH200N` with 53 rows each, `F454` and `F459` with 51 rows each, `MH200` with 24 rows, `H4684` with 23 rows, and `F450` with 19 rows).
3. **PIC Bus Dispatch vs Actuator Replies**: In energy management, requests like `*#18*51*51##` are accepted (`ack`, `out`) and dispatched to the PIC serial bus (`$06D1A1023350150000\r`). On physical plants with active energy meters, an actuator responds with the dimension value (`*#18*51*51*23791364##`); in the emulator, the forward is verified while no actuator is attached.

## Running Conformance Verification

To validate all YAML fixtures against `schema.json` and synchronize `corpus.json`:
```bash
python tools/golden/validate_corpus.py
python tools/golden/validate_corpus.py --check
```

To run the automated pytest conformance suite:
```bash
python -m pytest tests/test_golden_conformance.py tests/test_golden_privacy.py tests/test_firmware_oracle_conformance.py -v
```
