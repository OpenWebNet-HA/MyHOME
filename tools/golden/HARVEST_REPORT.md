# openwebnet4j & openHAB Test Vector Harvest Report

- **Sources**:
  - `mvalla/openwebnet4j` tag `0.15.0` (`MessageTest.java`)
  - `openhab-addons` (`OwnIdTest.java` by Massimo Valla)
- **Total Unique Frames Extracted**: 63
- **OWNd Parse Compatibility**: 58 / 63 (92.1%)

## Key Protocol Findings & Discrepancy Analysis

1. **Intentional Negative Test Cases in 4j**:
   - Frames `*1##`, `*1*##`, `*1**##`, and `*5*1*##` are test cases in `MessageTest.java` designed to trigger `MalformedFrameException`.
   - OWNd correctly rejects all of them (`None`), confirming negative-testing alignment.

2. **Empty Address Heuristic in Alarm (`*5*7*##`)**:
   - In openHAB's `OwnIdTest.java`, frame `*5*7*##` has no `WHERE` parameter, but `WhereAlarm` synthesizes `WHERE = '0'` for the system.
   - `openwebnet-mcp` (Legrand PDF specification) flags `*5*7*##` as a syntax error because standard grammar requires WHERE.
   - OWNd rejects `*5*7*##` as malformed. This is an empirical divergence between openHAB's internal heuristic and standard OpenWebNet grammar.

3. **Extended Thermoregulation Dimension 12 (`*#4*6*12*1048*3##`)**:
   - Frame reports probe status with negative temperature `1048` (-4.8°C).
   - Dimension 12 is an extended BTicino dimension reverse-engineered into 4j and fully parsed by OWNd into `_measured_temperature = -4.8`.

## Breakdown by Subsystem (WHO)

| WHO | Subsystem | Frame Count | OWNd Parsed | Example Frames |
|:---:|:---|:---:|:---:|:---|
| `0` | Scenarios (Basic) | 3 | 3/3 | `*0*14*95##`, `*0*2*05##` *+1 more* |
| `1` | Lighting | 19 | 16/19 | `*#1*#25#4#01##`, `*#1*0714*1*147*2##` *+17 more* |
| `2` | Automation (Covers & Shutters) | 5 | 5/5 | `*2*1000#0*55##`, `*#2*55*10*10*100*0*0##` *+3 more* |
| `3` | Load Control | 1 | 1/1 | `*3*1*123##` |
| `4` | Thermoregulation (Heating/Cooling) | 13 | 13/13 | `*#4*6*12*1048*3##`, `*4*13012*#0##` *+11 more* |
| `5` | Burglar Alarm | 7 | 5/7 | `*#5##`, `*5*1*##` *+5 more* |
| `9` | Auxiliary | 2 | 2/2 | `*9*1*1##`, `*9*1*4##` |
| `13` | Gateway Management | 3 | 3/3 | `*#13**16*1*2*3##`, `*#13**#16#5#4*255*3##` *+1 more* |
| `15` | CEN (Scenario Pushbuttons) | 3 | 3/3 | `*15*01#3*0001##`, `*15*02*22##` *+1 more* |
| `18` | Energy Management | 3 | 3/3 | `*#18*51*113##`, `*#18*51*53##` *+1 more* |
| `19` | WHO=19 | 1 | 1/1 | `*19*1*123##` |
| `25` | CEN+ (Dry Contacts) | 3 | 3/3 | `*25*22#2*22047##`, `*25*21#31*212##` *+1 more* |

## Harvested Frames Catalog

### WHO = 0: Scenarios (Basic)

| Frame | Source | Context / Method | OWNd Class | OWNd Status |
|---|---|---|---|---|
| `*0*14*95##` | `openwebnet4j` | `testScenario` | `OWNScenarioEvent` | ✅ PASS |
| `*0*2*05##` | `openwebnet4j` | `testScenario` | `OWNScenarioEvent` | ✅ PASS |
| `*0*40#5*06##` | `openwebnet4j` | `testScenario` | `OWNScenarioEvent` | ✅ PASS |

### WHO = 1: Lighting

| Frame | Source | Context / Method | OWNd Class | OWNd Status |
|---|---|---|---|---|
| `*#1*#25#4#01##` | `openwebnet4j` | `testWhereLightAutom` | `OWNLightingCommand` | ✅ PASS |
| `*#1*0714*1*147*2##` | `openwebnet4j` | `testLightingDimmerLevel100Status` | `OWNLightingEvent` | ✅ PASS |
| `*#1*0714*4*200*2##` | `openwebnet4j` | `testLightingDimmerLevel100Status` | `OWNLightingEvent` | ✅ PASS |
| `*#1*0714*4*110*2##` | `openwebnet4j` | `testLightingDimmerLevel100Status` | `OWNLightingEvent` | ✅ PASS |
| `*#1*0714*4*100*2##` | `openwebnet4j` | `testLightingDimmerLevel100Status` | `OWNLightingEvent` | ✅ PASS |
| `*#1*0714*4*201*2##` | `openwebnet4j` | `testLightingDimmerLevel100Status` | `OWNLightingEvent` | ✅ PASS |
| `*1*1000#1#01#2#3*0311#4#01##` | `openwebnet4j` | `testLightingCommandTranslationAndParams` | `OWNLightingEvent` | ✅ PASS |
| `*1*1*702053501#9##` | `openwebnet4j` | `testZigBeeLightingWhere` | `OWNLightingEvent` | ✅ PASS |
| `*1##` | `openwebnet4j` | `testMalformedCmdAndDimFrames` | - | ❌ FAIL (None) |
| `*1*##` | `openwebnet4j` | `testMalformedCmdAndDimFrames` | - | ❌ FAIL (None) |
| `*1**##` | `openwebnet4j` | `testMalformedCmdAndDimFrames` | - | ❌ FAIL (None) |
| `*1*1*789309801#9##` | `openhab-addons` | `global` | `OWNLightingEvent` | ✅ PASS |
| `*1*1*789301201#9##` | `openhab-addons` | `global` | `OWNLightingEvent` | ✅ PASS |
| `*1*1*789301202#9##` | `openhab-addons` | `global` | `OWNLightingEvent` | ✅ PASS |
| `*1*1*51##` | `openhab-addons` | `global` | `OWNLightingEvent` | ✅ PASS |
| `*1*1*0##` | `openhab-addons` | `global` | `OWNLightingEvent` | ✅ PASS |
| `*1*1*5##` | `openhab-addons` | `global` | `OWNLightingEvent` | ✅ PASS |
| `*1*1*#25##` | `openhab-addons` | `global` | `OWNLightingEvent` | ✅ PASS |
| `*1*1*25#4#01##` | `openhab-addons` | `global` | `OWNLightingEvent` | ✅ PASS |

### WHO = 2: Automation (Covers & Shutters)

| Frame | Source | Context / Method | OWNd Class | OWNd Status |
|---|---|---|---|---|
| `*2*1000#0*55##` | `openwebnet4j` | `testAutomation` | `OWNAutomationEvent` | ✅ PASS |
| `*#2*55*10*10*100*0*0##` | `openwebnet4j` | `testAutomation` | `OWNAutomationEvent` | ✅ PASS |
| `*2*4*11##` | `openwebnet4j` | `testUnsupportedWhat` | `OWNAutomationEvent` | ✅ PASS |
| `*2*0*93##` | `openhab-addons` | `global` | `OWNAutomationEvent` | ✅ PASS |
| `*2*1*#25##` | `openhab-addons` | `global` | `OWNAutomationEvent` | ✅ PASS |

### WHO = 3: Load Control

| Frame | Source | Context / Method | OWNd Class | OWNd Status |
|---|---|---|---|---|
| `*3*1*123##` | `openwebnet4j` | `testUnknownUnsupportedWho` | `OWNEvent` | ✅ PASS |

### WHO = 4: Thermoregulation (Heating/Cooling)

| Frame | Source | Context / Method | OWNd Class | OWNd Status |
|---|---|---|---|---|
| `*#4*6*12*1048*3##` | `openwebnet4j` | `testThermoregulation` | `OWNHeatingEvent` | ✅ PASS |
| `*4*13012*#0##` | `openwebnet4j` | `testThermoregulation` | `OWNHeatingEvent` | ✅ PASS |
| `*4*3000*#0##` | `openwebnet4j` | `testThermoregulation` | `OWNHeatingEvent` | ✅ PASS |
| `*4*115#1102*#0##` | `openwebnet4j` | `testThermoregulation` | `OWNHeatingEvent` | ✅ PASS |
| `*#4*1#1*20*0##` | `openwebnet4j` | `testWhereThermo` | `OWNHeatingEvent` | ✅ PASS |
| `*4*21*#0#1##` | `openwebnet4j` | `testWhereThermo` | `OWNHeatingEvent` | ✅ PASS |
| `*4*2215*#0##` | `openwebnet4j` | `testWhatThermo` | `OWNHeatingEvent` | ✅ PASS |
| `*#4*1*0*0020##` | `openhab-addons` | `global` | `OWNHeatingEvent` | ✅ PASS |
| `*#4*#1*0*0020##` | `openhab-addons` | `global` | `OWNHeatingEvent` | ✅ PASS |
| `*#4*#0##` | `openhab-addons` | `global` | `OWNHeatingCommand` | ✅ PASS |
| `*#4*#0#1##` | `openhab-addons` | `global` | `OWNHeatingCommand` | ✅ PASS |
| `*#4*1#2*20*0##` | `openhab-addons` | `global` | `OWNHeatingEvent` | ✅ PASS |
| `*#4*500*15*1*0020*0001##` | `openhab-addons` | `global` | `OWNHeatingEvent` | ✅ PASS |

### WHO = 5: Burglar Alarm

| Frame | Source | Context / Method | OWNd Class | OWNd Status |
|---|---|---|---|---|
| `*#5##` | `openwebnet4j` | `testAlarm` | `OWNAlarmCommand` | ✅ PASS |
| `*5*1*##` | `openwebnet4j` | `testAlarm` | - | ❌ FAIL (None) |
| `*5*11*#2##` | `openwebnet4j` | `testAlarm` | `OWNAlarmEvent` | ✅ PASS |
| `*#5*#2##` | `openhab-addons` | `global` | `OWNAlarmCommand` | ✅ PASS |
| `*#5*2##` | `openhab-addons` | `global` | `OWNAlarmCommand` | ✅ PASS |
| `*5*2*0##` | `openhab-addons` | `global` | `OWNAlarmEvent` | ✅ PASS |
| `*5*7*##` | `openhab-addons` | `global` | - | ❌ FAIL (None) |

### WHO = 9: Auxiliary

| Frame | Source | Context / Method | OWNd Class | OWNd Status |
|---|---|---|---|---|
| `*9*1*1##` | `openwebnet4j` | `testAuxiliary` | `OWNAuxEvent` | ✅ PASS |
| `*9*1*4##` | `openhab-addons` | `global` | `OWNAuxEvent` | ✅ PASS |

### WHO = 13: Gateway Management

| Frame | Source | Context / Method | OWNd Class | OWNd Status |
|---|---|---|---|---|
| `*#13**16*1*2*3##` | `openwebnet4j` | `testDimParams` | `OWNGatewayEvent` | ✅ PASS |
| `*#13**#16#5#4*255*3##` | `openwebnet4j` | `testDimWritingParamsValues` | `OWNGatewayCommand` | ✅ PASS |
| `*#13**22*09*37*30*102*03*01*05*2019##` | `openwebnet4j` | `testGatewayMgmt` | `OWNGatewayEvent` | ✅ PASS |

### WHO = 15: CEN (Scenario Pushbuttons)

| Frame | Source | Context / Method | OWNd Class | OWNd Status |
|---|---|---|---|---|
| `*15*01#3*0001##` | `openwebnet4j` | `testCEN` | `OWNCENEvent` | ✅ PASS |
| `*15*02*22##` | `openwebnet4j` | `testCEN` | `OWNCENEvent` | ✅ PASS |
| `*15*31*51##` | `openhab-addons` | `global` | `OWNCENEvent` | ✅ PASS |

### WHO = 18: Energy Management

| Frame | Source | Context / Method | OWNd Class | OWNd Status |
|---|---|---|---|---|
| `*#18*51*113##` | `openwebnet4j` | `testEnergyManagerUnit` | `OWNEnergyCommand` | ✅ PASS |
| `*#18*51*53##` | `openwebnet4j` | `testEnergyManagerUnit` | `OWNEnergyCommand` | ✅ PASS |
| `*#18*51*54##` | `openwebnet4j` | `testEnergyManagerUnit` | `OWNEnergyCommand` | ✅ PASS |

### WHO = 19: WHO=19

| Frame | Source | Context / Method | OWNd Class | OWNd Status |
|---|---|---|---|---|
| `*19*1*123##` | `openwebnet4j` | `testUnknownUnsupportedWho` | `OWNEvent` | ✅ PASS |

### WHO = 25: CEN+ (Dry Contacts)

| Frame | Source | Context / Method | OWNd Class | OWNd Status |
|---|---|---|---|---|
| `*25*22#2*22047##` | `openwebnet4j` | `testCENPlus` | `OWNCENPlusEvent` | ✅ PASS |
| `*25*21#31*212##` | `openhab-addons` | `global` | `OWNCENPlusEvent` | ✅ PASS |
| `*25*32#1*399##` | `openhab-addons` | `global` | `OWNDryContactEvent` | ✅ PASS |
