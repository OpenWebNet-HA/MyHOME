# OpenWebNet Golden Corpus Conformance Suite

A declarative conformance suite of OpenWebNet frames providing cross-framework verification across Home Assistant (`MyHOME` / `OWNd`), openHAB (`openwebnet4j` & `org.openhab.binding.openwebnet`), and official Legrand OpenWebNet specifications via `openwebnet-mcp`.

## The Three Authorities Triad

| Layer | Source | Role |
|---|---|---|
| **Judge** | Official Legrand PDFs via `openwebnet-mcp` | Validates whether a frame is syntactically and semantically legal |
| **Oracle** | `openwebnet4j` & openHAB binding (by Massimo Valla) | Provides mature reference factory outputs and empirical test vectors |
| **SUT** | `OWNd` / `custom_components/myhome` | System Under Test: verified against judge and oracle |

## Supported Subsystems Catalog (58 Fixtures)

- **Signaling (`who00_signaling.yaml`)**: Gateway ACK (`*#*1##`) and NACK (`*#*0##`).
- **WHO=0 Scenarios (`who00_scenario.yaml`)**: Basic scenario execution and stop.
- **WHO=1 Lighting (`who01_lighting.yaml`)**: Point-to-point ON/OFF, status requests, local bus routing (`0311#4#01`), group broadcast, speed transitions (`*1*1#5*12##`), and dimension writes.
- **WHO=2 Automation (`who02_automation.yaml`)**: Shutter UP/DOWN/STOP, private bus routing (`21#4#1`), absolute position percentages, and slat tilt angles.
- **WHO=4 Thermoregulation (`who04_thermo.yaml`)**: Measured temperature queries (`21.5°C`), setpoint writes, Antifreeze/Protection modes, and negative temperature probe status (`-4.8°C`).
- **WHO=5 Burglar Alarm (`who05_alarm.yaml`)**: Status requests and silent alarm events across zones and central units.
- **WHO=9 Auxiliary (`who09_auxiliary.yaml`)**: Activation and deactivation of AUX relay channels.
- **WHO=13 Gateway Management (`who13_gateway.yaml`)**: Firmware versions and gateway internal datetime responses.
- **WHO=15 CEN Pushbuttons (`who15_cen.yaml`)**: Short press, start long press, release, and extended hold events.
- **WHO=18 Energy Management (`who18_energy.yaml`)**: Instantaneous active power and cumulative energy totalizers.
- **WHO=25 CEN+ / Dry Contacts (`who25_cen_plus.yaml`)**: CEN+ press events and physical dry contact inputs.

## Directory Structure

```text
tests/golden/
  README.md                     # This documentation
  SOURCE.yaml                   # Provenance manifest of authorities
  schema.json                   # JSON Schema (Draft-07) for frame records
  frames/
    who00_signaling.yaml        # Bus signaling (ACK/NACK)
    who00_scenario.yaml         # WHO=0 Basic Scenarios
    who01_lighting.yaml         # WHO=1 Lighting
    who02_automation.yaml       # WHO=2 Covers & Shutters
    who04_thermo.yaml           # WHO=4 Heating & Cooling
    who05_alarm.yaml            # WHO=5 Burglar Alarm
    who09_auxiliary.yaml        # WHO=9 Auxiliary Relays
    who13_gateway.yaml          # WHO=13 Gateway Management
    who15_cen.yaml              # WHO=15 CEN Scenario Buttons
    who18_energy.yaml           # WHO=18 Energy Management
    who25_cen_plus.yaml         # WHO=25 CEN+ & Dry Contacts
tools/golden/
  MCP_NOTES.md                  # MCP tool capabilities & verification log
  validate_corpus.py            # Corpus schema validator
  harvest_4j_fixtures.py        # Oracle test harvester & divergence detector
  HARVEST_REPORT.md             # Harvest analysis report
  MASSI_OUTREACH.md             # Community proposal draft for Massi Valla
tests/golden/
  corpus.json                   # Zero-dependency standard library runtime cache
tests/
  test_golden_conformance.py    # Pytest suite running conformance tests
docs/
  protocol_conformance_matrix.md # Verifiable truth matrix across all 11 subsystems
```

## Running Conformance Verification

To validate all YAML fixtures against `schema.json` and synchronize `corpus.json`:
```powershell
& "C:\Users\laurensvdb\Documents\GitHub\MyHOME\.venv\Scripts\python.exe" tools/golden/validate_corpus.py
```

To run the automated pytest conformance suite (zero external dependencies required):
```powershell
& "C:\Users\laurensvdb\Documents\GitHub\MyHOME\.venv\Scripts\python.exe" -m pytest tests/test_golden_conformance.py -v
```
