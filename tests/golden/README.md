# OpenWebNet Golden Corpus Conformance Suite

A declarative conformance suite of OpenWebNet frames providing cross-framework verification across Home Assistant (`MyHOME` / `OWNd`), openHAB (`openwebnet4j` & `org.openhab.binding.openwebnet`), and official Legrand OpenWebNet specifications via `openwebnet-mcp`.

## The Three Authorities Triad

| Layer | Source | Role |
|---|---|---|
| **Judge** | Official Legrand PDFs via `openwebnet-mcp` | Validates whether a frame is syntactically legal (its semantics are not authoritative; see Provenance below) |
| **Oracle** | `openwebnet4j` & openHAB binding (by Massimo Valla) | Provides mature reference factory outputs and empirical test vectors |
| **SUT** | `OWNd` / `custom_components/myhome` | System Under Test: verified against judge and oracle |

## Provenance: which fixtures may change

Only `community-plant-capture` fixtures were recorded from a real bus. They are facts: never edit their frame, `what`, `where` or dimension values. If OWNd disagrees with a capture, OWNd is wrong.

Every other source (`legrand-spec`, `encyclopedia`, `openwebnet4j`, `public-readme`, `mcp-draft`) is a reading of a document or of another implementation, and can be wrong. Such a fixture may be corrected, but only in a commit that names the source it now follows (page and commit) and explains what was wrong.

- `mcp_valid: true` means openwebnet-mcp accepted the **grammar**. It is not evidence of meaning: the judge's WHO 15 catalog described `*15*1*11#2##` as "short press on button 2", which is how three wrong CEN fixtures entered this corpus (corrected in OpenWebNet-HA/OWNd#49). Its WHO 25 catalog did the same for `*25*21#1*12##`, `*25*22#1*12##` and `*25*24#1*12##`: `12` is not a CEN+ Object (`WHERE` is `2` + Object 0..2047). Those were replaced by a capture in the same PR.
- A `builder:` block proves the builder matches the fixture, not that either is right. When both are written in the same change from the same reading, the test only compares the code with itself. Prefer builder parity against a capture or an independent source such as `openwebnet4j`.
- When a capture arrives for a frame that so far exists only as a spec-derived fixture, add the capture and keep the spec entry only if it agrees.

## Supported Subsystems Catalog (183 Fixtures)

> Frames marked `community-plant-capture` come from real installations contributed by users. They are the only authority for behaviour the official PDFs leave undefined, and they are kept verbatim except where a fixture note records a deliberate substitution.

- **Signaling (`who00_signaling.yaml`)**: Gateway ACK (`*#*1##`) and NACK (`*#*0##`).
- **WHO=0 Scenarios (`who00_scenario.yaml`)**: Basic scenario execution and stop.
- **WHO=1 Lighting (`who01_lighting.yaml`)**: Point-to-point ON/OFF, status requests, local bus routing (`0311#4#01`), group broadcast, speed transitions (`*1*1#5*12##`), and dimension writes. An MH200 dimmer capture (MyHOME#434) pins what a dimmer actually sends: a keypad hold is one `*1*LEVEL*WHERE##` per 10% step, a `DIMENSION 1` reply of `100` means OFF, and a `DIMENSION 4` request is NACKed; no source produced a `DIMENSION 4` frame, so the `openwebnet4j` one (`*#1*0714*4*200*2##`) is still the only evidence for its layout.
- **WHO=2 Automation (`who02_automation.yaml`)**: Shutter UP/DOWN/STOP, private bus routing (`21#4#1`), absolute position percentages, and slat tilt angles. Records tagged `scope` pin OWNd's general / area / group classification; an MH201 capture (MyHOME#433) adds an area-1 stop echo (`*2*0*1##`) and the per-actuator end-of-run stop that closes a general UP: a scope command never gets a stop of its own (WHO_2.pdf §3.0.1).
- **WHO=4 Thermoregulation (`who04_thermo.yaml`)**: Measured temperature queries (`21.5°C`), setpoint writes, Antifreeze/Protection modes, and negative temperature probe status (`-4.8°C`). Two MyHomeServer1 + Home+Control plants (#429) add dimension 7, which carries the zone state and setpoint on those plants and is not in `WHO_4 2.pdf`: cooling and heating setpoints, protection (the zone is OFF, with `*4*202*Z##`), the 35.0 °C protection value sent just before a real setpoint, MyHomeServer1's `*#4*Z*#7*...##` program writes, and the dimension 5 re-assert. An MH200N capture (#466) adds local knob offset transitions (Dimension 13: `*#4*4*13*01##` / `*#4*4*13*11##`). The full traces are in `tests/fixtures/traces/issue_429` and `tests/fixtures/traces/issue_466`.
- **WHO=5 Burglar Alarm (`who05_alarm.yaml`)**: Status requests and silent alarm events across zones and central units.
- **WHO=9 Auxiliary (`who09_auxiliary.yaml`)**: Activation and deactivation of AUX relay channels.
- **WHO=13 Gateway Management (`who13_gateway.yaml`)**: Firmware versions and gateway internal datetime responses. `*#13**15*4##` is the device type a physical MH200 (firmware 2.1.0) answers. It is also the regression frame for an MH200 set up by hand as MH200N (live 2026-09-23): the code corrects that label to MH200, because the MH200N has a code of its own (`44`) and a family match (`MH200N` → `MH200`) is no longer enough (`test_manual_mh200n_on_a_live_mh200_is_corrected`). An MH200N capture (#466) adds on-wire firmware version 1.1.8 (`*#13**16*1*1*8##`).
- **WHO=15 CEN Pushbuttons (`who15_cen.yaml`)**: Pressure, short release, long release and extended pressure (`*15*BUTTON[#1|#2|#3]*WHERE##`).
- **WHO=16 Sound System (`who16_sound.yaml`)**: Source devices (`101`-`109`), matrix routing (`1` + environment + source), amplifier ON/OFF, volume (`DIMENSION 1`) and RDS (`DIMENSION 8`). Captured on two independent F441M installations — an MH200 and an MH200N — rather than derived from the specification, because the routing address is not documented in `WHO_16.pdf`.
- **WHO=22 Sound Diffusion (`who22_sound_diffusion.yaml`)**: The mirror frames an MH200N emits alongside every WHO=16 sound event. Kept as structural evidence for the WHO=16 addressing: this dialect writes the environment and the source into separate fields (`*22*2#4#AREA*5#2#SOURCE##`) where WHO=16 packs them into one pseudo address. Judged against the Encyclopedia's WHO=22 reference, since `openwebnet-mcp`'s catalog for this family is still a stub.
- **WHO=18 Energy Management (`who18_energy.yaml`)**: Instantaneous active power and cumulative energy totalizers (`*#18*51*51*23791364##`, captured on an MH200N on #466).
- **WHO=25 CEN+ / Dry Contacts (`who25_cen_plus.yaml`)**: CEN+ short press, hold start, hold repeat and release (`*25*21..24#PUSHBUTTON*2OBJECT##`, captured on an MH201) and a dry-contact event (`*25*31#1*WHERE##`).

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
python tools/golden/validate_corpus.py
```

To run the automated pytest conformance suite (zero external dependencies required):
```powershell
python -m pytest tests/test_golden_conformance.py -v
```
