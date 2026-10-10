# #466 MH200N Gateway Traces (Bus Sweep & Thermoregulation Interactions)

Verbatim bus traces contributed by **@caiosweet** on [#466 (comment 5834435606)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5834435606). They are facts: never edit a frame.

## Hardware Profile

- **Gateway Model**: BTicino MH200N (2nd Generation Scenario Programmer)
- **Firmware**: 1.1.8
- **WHO 13 Device Type Code**: `44`
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_sweep_MH200N_all_2026-09-25T14-40-10.json` | Bus Card Export (200 frames) | Full on-wire trace captured via `<myhome-bus-card>` during `myhome.sweep_bus` and climate interactions. |
| `config_entry-myhome-80a1577fb7ae6f68f05e0cc5a1ead27d.json` | HA Diagnostic Download | Home Assistant config entry diagnostic summary from the reporter's plant. |

## Sequence of Actions Recorded

1. **Bus Sweep (`myhome.sweep_bus`)**:
   - Firmware query `*#13**16##` -> reports `*#13**16*1*1*8##` (firmware 1.1.8).
   - Automation scan `*#2*0##` -> reports stopped covers (`19`, `29`, `69`, `78`, `79`, `0715`).
   - Lighting status queries across multiple points (`12`, `13`, `22`, `23`, `24`, `25`, `32`, `41`, `42`, `47`, `52`, `53`, `54`, `61`, `62`, `63`, `71`, `81`, `91`, `0315`, `0614`).
   - Climate status scan `*#4*0##` -> reports zones 1–4 temperatures and heating valve actuator states (`*#4*Z#1*20*0##`), plus external probe `105` at 29.6 °C (`*#4*105*0*0296##`).
   - Energy totalizer sweep across meters 51–57 (`*#18*51*51##` -> `*#18*51*51*23791364##`, up to meter 57).

2. **Thermoregulation Setpoint Change**:
   - Reporter set the plant temperature to 18.0 °C:
     - `*4*110#0180*#0##`, `*4*21*#0##`
     - Zones 1–3 confirm manual heating setpoint 18.0 °C (`*#4*Z*14*0180*3##`).

3. **Thermostat Knob Local Offset Adjustment**:
   - Reporter manually turned the probe adjustment wheel on Zone 4 through its complete local offset range (-3 °C to +3 °C):
     - `*#4*4*13*00##` (0 °C offset)
     - `*#4*4*13*01##` (+1 °C)
     - `*#4*4*13*02##` (+2 °C)
     - `*#4*4*13*03##` (+3 °C)
     - `*#4*4*13*02##` (+2 °C)
     - `*#4*4*13*01##` (+1 °C)
     - `*#4*4*13*00##` (0 °C)
     - `*#4*4*13*11##` (-1 °C)
     - `*#4*4*13*12##` (-2 °C)
     - `*#4*4*13*13##` (-3 °C)
     - `*#4*4*13*12##` (-2 °C)
     - `*#4*4*13*11##` (-1 °C)

## Subsystems Verified (MH200N)

- **WHO 1 (Lighting)**: 21 lighting endpoints reporting OFF / ON status during sweep.
- **WHO 2 (Automation)**: 6 shutter/blind endpoints reporting stopped state (`*2*0*WHERE##`).
- **WHO 4 (Thermoregulation)**: 4 climate zones, external temperature probe 105, heating actuator valves, manual setpoints, and local knob offsets (Dimension 13).
- **WHO 13 (Gateway Management)**: Firmware version query/response (`1.1.8`) and internal date/time broadcasts.
- **WHO 18 (Energy Management)**: Cumulative energy meter totalizers on addresses 51 through 57.

---

# #466 H4890 Touch Screen Gateway Traces (Bus Sweep, Burglar Alarm, Audio Diffusion)

Verbatim bus traces contributed by **@nicolacavallo84** on [#466 (comment 5846053726)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5846053726).

## Hardware Profile

- **Gateway Model**: BTicino H4890 (Axolute 3.5" Color Touch Screen with integrated LAN OpenWebNet server; shares board architecture with `AM4890`, `LN4890`, and `LN4890A`)
- **Firmware**: 4.0.15
- **WHO 13 Device Type Code**: `200`
- **WHO 1013 Object Model**: `30`
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_sweep_H4890_all_2026-09-26T11-48-10.json` | Bus Card Export (200 frames) | Full bus sweep capture via `<myhome-bus-card>` covering lights, covers, power, audio, sound diffusion, energy, and CEN+. |
| `myhome_trace_H4890_all_2026-09-26T11-51-26.json` | Bus Monitor Trace (51 frames) | Real-world WHO 5 Burglar Alarm events (`*5*9*0##` disarm, `*5*1*0##` arm away, zone statuses), power, energy, and CEN+. |
| `myhome_trace_H4890_all_2026-09-26T11-52-48.json` | Bus Monitor Trace (32 frames) | Sound diffusion (WHO 16 / WHO 22) and power control traffic. |

## Subsystems Verified (H4890)

- **WHO 1 (Lighting)**: Points reporting on/off status.
- **WHO 2 (Automation)**: Cover/shutter states.
- **WHO 5 (Burglar Alarm)**: System arm/disarm transitions and zone status reporting (`*5*11*#1##` through `#6##`, `*5*18*#7##`, `#8##`).
- **WHO 9 (Power / Auxiliary)**: Auxiliary load control events.
- **WHO 16 & 22 (Sound & Audio Diffusion)**: Multi-source audio control and diffusion events.
- **WHO 18 (Energy Management)**: Cumulative energy meter reports.
- **WHO 25 (CEN+ / Dry Contact)**: CEN+ pushbutton / scenario status events.

---

# #466 MH200N Gateway Traces (Burglar Alarm Discovery, WHO 1013 Diagnostic, CEN+ Dry Contact, Timed Turn-On)

Verbatim bus traces contributed by **@manfredgittmaier-afk** on [#466 (comment 5848807742)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5848807742).

## Hardware Profile

- **Gateway Model**: BTicino MH200N (2nd Generation Scenario Programmer)
- **Firmware**: 1.0 (WHO 1013: N_CONF 15, BRAND 0, LINE 0)
- **WHO 1013 Object Model**: `44`
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_trace_MH200N_all_2026-09-26T18-32-34.json` | Bus Monitor Trace (37 frames) | Targeted diagnostic capture covering WHO 5 burglar alarm query behavior, WHO 1013 gateway diagnostic identification, F428 dry contact events, auxiliary query, timed light turn-on, and scenario module notifications. |

## Sequence of Actions Recorded & Subsystems Verified

1. **Burglar Alarm (WHO 5)**:
   - Query `*#5*0##` on a bus **without an alarm central unit**: the MH200N answers anyway after ~2.5s with `*5*0*##`, `*5*9*##`, `*5*5*##`, `*5*7*##` (empty-where frames) followed by partition statuses `*5*11*#1##` through `*5*11*#8##` (reporting full 8-zone alarm status).
   - Partition status query `*#5*#1##` -> reports `*5*11*#1##`.
   - Confirms that MH200N gateways answer WHO 5 queries even without alarm hardware, creating phantom alarm partitions if discovery relies solely on query response.

2. **Gateway Diagnostic (WHO 1013)**:
   - Query `*#1013*0*1##` -> reports `*#1013**1*44*15*0*0##`.
   - Confirms OBJECT_MODEL `44` (MH200N), `N_CONF` 15, `BRAND` 0, and `LINE` 0.

3. **CEN+ Dry Contact (WHO 25) & Auxiliary (WHO 9)**:
   - F428 contact interface in "contact status" mode emits dry contact frame `*25*32#1*31##` (motion detector with normally-closed output configured with inverted logic).
   - Auxiliary channel query `*#9*0##` -> reports no auxiliary channels `*9*0*0##`.

4. **Hardware Timer & Scenario Module (WHO 1 / WHO 17)**:
   - Timed turn-on command `*#1*65*#2*2*0*0##` (2 hours) confirmed by `*1*1*65##`.
   - Scenario module reports reactions `*17*1*4##` and `*17*2*4##` to the light event.

5. **Audio & Other Subsystems (WHO 16, WHO 22, WHO 13, WHO 4)**:
   - Audio diffusion frames `*#16*101*8*...##` and `*#22*5#2#1*10*...##`.
   - Real-time clock broadcast `*#13**22*...##`.
   - Climate valve actuator status `*#4*4#1*20*0##`.

---

# #466 F454 & MH202 Gateway Traces (Bus Sweep, Dimmer Progression, Advanced Covers, CEN+, F520 Energy & Actuator Lock)

Verbatim bus traces contributed by **@anotherjulien** on [#466 (comment 5849027587)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5849027587).

## Hardware Profile

- **Gateway 1**: BTicino F454
  - Firmware: 2.0.51
  - WHO 13 Device Type Code: `200`
  - Connection: TCP OpenWebNet (Port 20000)
- **Gateway 2**: BTicino MH202 (Scenario Programmer)
  - Firmware: 1.0.21
  - WHO 13 Device Type Code: `200`
  - Connection: TCP OpenWebNet (Port 20000)

---

# #466 MH200 Gateway Traces (Live Bus Monitor & Subsystem Sweep)

Authentic on-wire bus trace captured from a physical BTicino MH200 scenario programmer via the `myhome-gateway` live session.

## Hardware Profile

- **Gateway Model**: BTicino MH200 (1st Generation Scenario Programmer)
- **Firmware**: 2.0.0
- **WHO 13 Device Type Code**: `4` (`*#13**15*4##`)
- **WHO 1013 Object Model**: `4` (`*#1013**1*4##`)
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_sweep_F454_all_2026-09-26T16-59-13.json` | Bus Card Export (238 frames) | Full bus sweep initiated on F454 and captured via `<myhome-bus-card>` covering lights, dimmers, covers, auxiliary, actuator lock, F520 energy totalizers, and CEN+. |
| `myhome_trace_MH202_all_2026-09-26T16-59-17.json` | Bus Monitor Trace (314 frames) | Live bus traffic captured concurrently on MH202 observing the sweep, physical switch dimmer interactions, advanced cover positioning, actuator lock/unlock, and dry contacts. |

## Sequence of Actions Recorded & Subsystems Verified

1. **Lighting & Physical Switch Dimmer Progression (WHO 1 - Resolves Issue #434)**:
   - Physical wall switch dimming up and down: `*1*1000#30*14##` (dim up) and `*1*1000#31*14##` (dim down).
   - Physical switch direct turn-on: `*1*1000#1*14##` (turn on to 100%) and `*1*1000#0*14##` (turn off).
   - Dimmers emit Dimension 1 level reports (`*#1*14*1*130*5##` through `*#1*14*1*200*5##`) confirming physical 100-level dimmers emit Dimension 1 status with speed parameter rather than Dimension 4.

2. **Advanced Automation & Shutter Positioning (WHO 2)**:
   - Shutter movement: UP (`*2*1*31##`), DOWN (`*2*2*31##`), and STOP (`*2*0*31##`).
   - Preset height commands (`*#2*31*#11#001*40##` and `*#2*31*#11#001#1*40##`).
   - Dimension 10 multi-parameter position and status feedback (`*#2*31*10*10*40*001*0##`, `*#2*31*10*11*40*001*0##`, `*#2*31*10*12*66*001*0##`, `*#2*31*10*10*57*001*0##`, `*#2*31*10*10*30*001*0##`).

3. **Actuator Lock / Unlock (WHO 14)**:
   - Actuator lock engagement: `*14*1*32##`.
   - Actuator lock release: `*14*0*32##`.
   - Closes critical hardware matrix blind spot for both F454 and MH202.

4. **Energy Management (WHO 18 - F520 Energy Meter)**:
   - Totalizer telemetry on meter 52: `*#18*52*51##` -> `*#18*52*51*14159553##`.
   - Dimension 113 daily/monthly totalizer requests and responses (`*#18*52*113*1##`, `*#18*53*113*0##`, `*#18*52*53*1##`).

5. **CEN+ Pushbuttons & Dry Contact Interfaces (WHO 25)**:
   - Pushbutton short press: `*25*21#1*21##`.
   - Pushbutton release after long press: `*25*21#2*21##`.
   - Pushbutton long press progression: `*25*21#8*233##`, `*25*22#8*233##`, `*25*23#8*233##`, `*25*24#8*233##`.
   - Dry contact transitions: `*25*32#1*33##` (active) and `*25*31#1*33##` (inactive).

6. **Auxiliary Subsystem (WHO 9)**:
   - Periodic AUX relay events: `*9*1*4##` (relay ON) and `*9*0*4##` (relay OFF).

## Subsystems Verified Summary

- **F454**: WHO 1 (Lights & Dimmers), WHO 2 (Covers), WHO 4 (Climate), WHO 9 (Power/Aux), WHO 13 (Gateway), WHO 14 (Lock), WHO 18 (Energy), WHO 25 (CEN+ / Diag).
- **MH202**: WHO 1 (Lights & Dimmers), WHO 2 (Covers), WHO 4 (Climate), WHO 9 (Power/Aux), WHO 13 (Gateway), WHO 14 (Lock), WHO 18 (Energy), WHO 25 (CEN+ / Diag).

---

| `myhome_trace_MH200_all_2026-09-26T21-00-00.json` | Bus Monitor Trace (144 frames) | Authentic physical capture across WHO 1, 2, 5, 9, 13, 16, 17, 1001, and 1013, closing key hardware gaps in the test matrix. |

## Sequence of Actions Recorded & Subsystems Verified

1. **Gateway Identification & Diagnostics (WHO 13 & WHO 1013)**:
   - Device model query `*#13**15##` -> `*#13**15*4##` (MH200).
   - Clock broadcasts `*#13**22*...##`.
   - Gateway object model diagnostic `*#1013*0*1##` -> `*#1013**1*4##`.

2. **Auxiliary Subsystem Verification (WHO 9)**:
   - Status requests for auxiliary channels 0, 1, and 2 (`*#9*0##`, `*#9*1##`, `*#9*2##`).
   - Hardware responses confirming channel states: `*9*0*0##`, `*9*0*1##`, `*9*0*2##`.

3. **Advanced Scenario Module (WHO 17)**:
   - Querying programmed scenarios (`*#17*0##`, `*#17*30##`, `*#17*137##`).
   - Hardware replies confirming resident scenarios 30 and 137 (`*17*2*30##`, `*17*3*30##`, `*17*2*137##`, `*17*3*137##`).

4. **Burglar Alarm Interaction (WHO 5)**:
   - Status poll `*#5*0##` -> returns empty-WHERE status frames (`*5*0*##`, `*5*9*##`, `*5*5*##`, `*5*7*##`) and active zones 1 through 8 (`*5*11*#1##` to `*5*11*#8##`).

5. **Audio System (WHO 16)**:
   - Status polls and zone event broadcasts across amplifiers and sources: `*16*13*21##`, `*16*3*101##`, `*16*3*102##`, `*16*3*122##`, and zone 23 volume reports (`*#16*23*1*28##`, `*#16*23*1*10##`).

6. **Physical Layer Lighting Diagnostic (WHO 1001)**:
   - Bus diagnostic emission from lighting node 74: `*#1001*74*11*111110111111111111110111##`.

7. **Automation & Lighting (WHO 1 & WHO 2)**:
   - Interface 02 shutter status frames (`*2*0*WHERE#4#02##`).
   - Comprehensive lighting sweeps across standard and dimmable loads.

## Subsystems Verified (MH200)

- **WHO 1 (Lighting)**: Multi-point on/off and dimmer level events.
- **WHO 2 (Automation)**: Covers on interface 02 and local actuators.
- **WHO 5 (Burglar Alarm)**: System status flags and zones 1–8.
- **WHO 9 (Power / Auxiliary)**: Auxiliary channels 0, 1, and 2.
- **WHO 13 (Gateway Management)**: Device type `4` and datetime broadcasts.
- **WHO 16 (Sound System / Audio)**: Zones 14, 17, 18, 21, 22, 23, 35, 36, 122 and volume feedback.
- **WHO 17 (Advanced Scenarios)**: Resident scenario states for 30 and 137.
- **WHO 1001 (Lighting Diagnostic)**: Node 74 diagnostic bitmask.
- **WHO 1013 (Gateway Diagnostics)**: Object model `4`.

---

# #466 BTicino F414 Classic Dimmer Verification (MH200 Live Bus Trace)

Empirical bus trace recorded on an authentic **BTicino MH200** (firmware 2.1.0) testing a physical **BTicino F414** classic 10-level dimmer on WHERE=`99` (Woonkamer plafond).

Settles the question raised by **@anotherjulien** in [#466 (comment 5849245960)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5849245960) regarding whether older/classic dimmers behave differently from modern universal dimmers (F418U2):
- Proves that the classic F414 also uses OpenWebNet **Dimension 1** (`*#1*99*1*LEVEL*SPEED##`).
- Proves that the F414 accepts **Dimension 1 writes** (`*#1*99*#1*150*0##`), immediately setting 50% brightness and broadcasting a Dimension 1 report (`*#1*99*1*150*5##`) with active transition speed.
- Discloses the non-linear transformer/logarithmic mapping of discrete WHAT levels on the F414 (`10`=100%, `9`=74%, `8`=63%, `7`=50%).
- Confirms that the F414 **rejects Dimension 4** (`*#1*99*4##`), returning NACK after a 2s timeout without bus emission.

## Hardware Profile

- **Gateway**: BTicino MH200 (1st Generation Scenario Programmer)
- **Gateway Firmware**: 2.1.0
- **Actuator Model**: BTicino F414 (Classic 10-level incandescent/ferromagnetic modular dimmer, 60–1000 VA)
- **Actuator Address**: WHERE `99` (`light.woonkamer_plafond`)
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_trace_MH200_f414_dimmer_2026-09-26T21-59-00.json` | Bus Monitor Trace (18 frames) | Complete sequential trace of initial status query, discrete WHAT commands, fine Dimension 1 writes, coarse status mapping, and final 100% restoration on the F414. |

---

# #466 / #501 BTicino F418U2 Modern Dimmer Verification (F454 Live Bus Trace)

Empirical bus trace recorded on an authentic **BTicino F454** (firmware 2.0.51) testing a physical **BTicino F418U2** modern modular dimmer on WHERE=`32`, contributed by **@anotherjulien** in [#466 (comment 5854469497)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5854469497) and tracked in [#501](https://github.com/OpenWebNet-HA/MyHOME/issues/501).

Complements the classic F414 findings by revealing the modern dimmer Dimension 4 fallback and write mechanics:
- **Dimension 4 Fallback When OFF**: When the dimmer is OFF, sending a status query for Dimension 1 (`*#1*32*1##`) or Dimension 4 (`*#1*32*4##`) causes the F418U2 to reply with **Dimension 4** (`*#1*32*4*100*2##`), where level `100` represents 0% brightness at transition speed 2.
- **Dimension Queries When ON**: When the dimmer is ON (e.g. at 30%), querying Dimension 1 (`*#1*32*1##`) replies with Dimension 1 (`*#1*32*1*130*5##`), and querying Dimension 4 (`*#1*32*4##`) replies with Dimension 4 (`*#1*32*4*130*2##`).
- **Dimension 4 Writes are Ignored**: Writing to Dimension 4 (`*#1*32*#4*130*0##`) is completely ignored by the dimmer and produces no bus emission.
- **Dimension 1 Writes are Supported**: Writing to Dimension 1 (`*#1*32*#1*130*0##`) succeeds and is acknowledged with a Dimension 1 report (`*#1*32*1*130*5##`).
- **Switching OFF**: Writing level 0 via Dimension 1 or Dimension 4 does not turn off the channel; switching off strictly requires discrete WHAT `0` (`*1*0*32##`).

## Hardware Profile

- **Gateway**: BTicino F454
- **Gateway Firmware**: 2.0.51
- **Actuator Model**: BTicino F418U2 (Modern 2-channel universal modular dimmer)
- **Actuator Address**: WHERE `32`
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_trace_F454_f418u2_dimmer_2026-09-27T08-58-54.json` | Bus Monitor Trace (20 frames) | Complete sequential trace of Dimension 1 / Dimension 4 queries while OFF, level 5 (30%) switch ON, Dimension 1 / Dimension 4 queries while ON, Dimension 4 write attempt (ignored), Dimension 1 write, and discrete WHAT 0 switch OFF. |

---

# #466 / #501 BTicino F418U2 Modern Dimmer Verification (MH200 Live Bus Trace)

Empirical bus trace recorded on an authentic physical **BTicino MH200** (firmware 2.1.0) testing a physical **BTicino F418U2** modern modular dimmer on WHERE=`62` (`light.hal_inkom_plafond_bureau`).

Verifies modern F418U2 dimmer behavior when routed through older 1st-generation gateway firmware (MH200 FW 2.1.0) compared against 2nd-generation Linux gateways (F454 FW 2.0.51):
- **Dimension 1 Query When OFF**: Unlike the F454 which surfaces Dimension 4 (`*#1*32*4*100*2##`) when queried with Dimension 1 while OFF, the MH200 responds with **Dimension 1** (`*#1*62*1*100*2##`), confirming 0% brightness at speed 2.
- **Dimension 4 Handling**: The MH200 does not emit/forward Dimension 4 status queries (`*#1*62*4##`), returning no event emission.
- **Dimension 4 Writes are Ignored**: Writing to Dimension 4 (`*#1*62*#4*130*0##`) is ignored, exactly as observed on the F454.
- **Dimension 1 Writes are Fully Functional**: Sending fine Dimension 1 writes (`*#1*62*#1*150*0##` for 50%, `*#1*62*#1*200*0##` for 100%) immediately triggers hardware transition and broadcasts Dimension 1 status with active transition speed 5 (`*#1*62*1*LEVEL*5##`).
- **Dimming Curve Concordance**: Both gateways confirm identical non-linear WHAT-to-percentage internal dimming curves on the F418U2 hardware:
  - Discrete Level 3 (`*1*3*WHERE##`) -> 10% brightness (`110`)
  - Discrete Level 5 (`*1*5*WHERE##`) -> 30% brightness (`130`)
  - Discrete Level 7 (`*1*7*WHERE##`) -> 50% brightness (`150`)
  - Discrete Level 10 (`*1*10*WHERE##`) -> 100% brightness (`200`)

## Hardware Profile

- **Gateway**: BTicino MH200 (1st Generation Scenario Programmer)
- **Gateway Firmware**: 2.1.0
- **Actuator Model**: BTicino F418U2 (Universal 2-channel modular dimmer, 300 VA)
- **Actuator Address**: WHERE `62` (`light.hal_inkom_plafond_bureau`)
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_trace_MH200_f418u2_dimmer_2026-09-27T12-15-00.json` | Bus Monitor Trace (30 frames) | Full empirical trace testing Dimension 1 write (50%), discrete WHAT status mapping (WHAT 7), Dimension 4 write rejection, discrete Level 3 / Level 5 dimming curves, turn OFF, Dimension 1 query when OFF (level 100), Dimension 1 restoration (100%), and standard status confirmation (WHAT 10). |

---

# #466 / #501 BTicino F418U2 Modern Dimmer Verification (MH202 Live Bus Trace)

Empirical bus trace recorded on an authentic **BTicino MH202** (firmware 1.0.21) testing a physical **BTicino F418U2** modern modular dimmer on WHERE=`32`, contributed by **@anotherjulien** in [#466 (comment 5854805415)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5854805415) and analyzed in [#466 (comment 5854854969)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5854854969).

Settles crucial cross-gateway architectural differences between the MH202, F454, and MH200:
- **Positive Dimension 4 Writes Function on MH202**: Unlike the F454 and MH200 where Dimension 4 write commands (`*#1*32*#4*130*0##`) are ignored by the gateway/actuator, sending a Dimension 4 write on the MH202 **successfully turns on the channel and sets the requested level**:
  ```text
  TX  *#1*32*#4*130*0##
  RX  *#1*32*#4*130*0##          (echo)
  RX  *#1*32*#13*1*130*0*0##     (Dimension 13 write echo)
  RX  *#1*32*4*130*2##           (Dimension 4 response at speed 2)
  RX  *#1*32*13*1*130*2*0##      (Dimension 13 response)
  ```
- **Dimension 1 Reads When OFF**: On MH202, querying Dimension 1 while OFF (`*#1*32*1##`) returns **Dimension 1** (`*#1*32*1*100*0##`), with transition speed 0 (no Dimension 4 substitution like on F454).
- **Dimension 4 Reads When OFF**: Querying Dimension 4 while OFF (`*#1*32*4##`) returns **Dimension 4** (`*#1*32*4*100*0##`).
- **Extended Command Translation Events**: The MH202 emits command translation frames with speed prefix 1000 (`*1*1000#5*32##`, `*1*1000#0*32##`) and speed parameter events (`*1*1#0*32##`).
- **Multi-Parameter Dimension 13 Feedback**: Emits comprehensive Dimension 13 reports (`*#1*32*13*2*130*5*0##`) carrying actuator mode, percentage level, and active speed.
- **Switching OFF**: Writing level 100 (0% brightness) via Dimension 1 or Dimension 4 does not switch off the channel; switching OFF strictly requires discrete WHAT 0 (`*1*0*32##`).

## Hardware Profile

- **Gateway**: BTicino MH202 (Scenario Programmer)
- **Gateway Firmware**: 1.0.21
- **Actuator Model**: BTicino F418U2 (Modern 2-channel universal modular dimmer)
- **Actuator Address**: WHERE `32`
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_trace_MH202_f418u2_dimmer_2026-09-27T09-43-45.json` | Bus Monitor Trace (41 frames) | Full empirical trace testing Dimension 1 and Dimension 4 reads when OFF, discrete level 5 ON, Dimension 1 / Dimension 4 reads when ON, discrete WHAT 0 OFF, positive Dimension 4 write (successful turn ON), Dimension 1 write, Dimension 4 write to 0% (remains ON), and discrete WHAT 0 OFF. |

---

# #466 MyHomeServer1 Gateway Traces (CEN+ Pushbuttons, Bistable Covers, Group Lighting & Physical Layer Diagnostics)

Verbatim bus traces contributed by **@TheDarkWizard** on [#466 (comment 5855690408)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5855690408).

## Hardware Profile

- **Gateway Model**: BTicino MyHomeServer1
- **Firmware**: 2.87.13
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_trace_MyHomeServer1_cen_scenario_2026-09-27T13-28-10.json` | Bus Monitor Trace (8 frames) | CEN+ scenario activation via KW8011 button (`*25*21#1*21##`) triggering multi-light cascade on fixtures 16, 18, 19. |
| `myhome_trace_MyHomeServer1_group_lights_2026-09-27T13-32-12.json` | Bus Monitor Trace (16 frames) | Group lighting control on `#1` (`*1*0*#1##`, `*1*1*#1##`) interleaved with fixture status updates. |
| `myhome_trace_MyHomeServer1_cen_cover_scenarios_2026-09-27T13-47-33.json` | Bus Monitor Trace (8 frames) | Multi-scenario activation across CEN+ addresses 21, 22, 23, shutter target positioning to 85% (`*#2*03*#11#001#1*85##`), and active power telemetry on meters 51 and 52. |
| `myhome_trace_MyHomeServer1_scenarios_cover_diagnostic_2026-09-27T13-47-33.json` | Bus Monitor Trace (73 frames) | Full multi-subsystem sequence covering CEN+, shutter positioning to 0% and 100%, active power (Dimension 113) and power threshold alarms (Dimension 1200), zone 2 temperature (27.1 °C), and WHO 1001 diagnostics. |
| `myhome_trace_MyHomeServer1_monostable_cover_diagnostic_2026-09-27T13-51-19.json` | Bus Monitor Trace (114 frames) | Physical layer device diagnostics (WHO 1001) memory dump: session start/stop (`*1000*5*0##` / `*1000*6*0##`), slot objects, and indexed configuration parameters (Dimension 35 across 84 registers). |
| `myhome_trace_MyHomeServer1_bistable_cover_2026-09-27T13-54-05.json` | Bus Monitor Trace (11 frames) | Complete bistable cover cycle on point 03: advanced DOWN command (`*2*1000#12#100#001#1*03##`), Dimension 10 transit status, mid-travel STOP (`*2*1000#10#001#1*03##`), stopped status at 83% (`*#2*03*10*10*83*001*0##`), advanced UP command (`*2*1000#11#100#001#1*03##`), moving status, and final limit switch STOP at 100%. |
| `myhome_trace_MyHomeServer1_who25_2026-09-27T20-56-45.json` | Bus Monitor Trace (9 frames) | CEN+ button press, extended press (long press start `*25*22#1*21##`), and release (`*25*24#1*21##`) across 0s, 3s, and 6s durations with KW8011 on MyHomeServer1 (comment 5859798807), confirming no hold repeat (`23#`) is generated by the switch. |

## Subsystems Verified (MyHomeServer1)

- **WHO 1 (Lighting)**: Fixture points 16, 18, 19 and group lighting address `#1`.
- **WHO 2 (Automation / Covers)**: Monostable & bistable cover movement, advanced translation commands (`*2*1000#...`), Dimension 10 position feedback (stops at 83% and 100%), and direct target position writes (`*#2*03*#11#001#1*85##`).
- **WHO 4 (Thermoregulation)**: Zone 2 ambient temperature report (`*#4*2*0*0271##` -> 27.1 °C).
- **WHO 18 (Energy Management)**: Instantaneous active power telemetry (Dimension 113) on meters 51 and 52, and power threshold notifications (Dimension 1200).
- **WHO 25 (CEN+ / Pushbutton Scenarios)**: KW8011 3-position device short-press events on addresses 21, 22, and 23 (`*25*21#1*21##`, `*25*21#1*22##`, `*25*21#1*23##`), plus extended press lifecycle: short press (`*25*21#1*21##`), long press start (`*25*22#1*21##`), and long release (`*25*24#1*21##`) across multiple hold durations without intermediate hold repeats.
- **WHO 1001 (Physical Layer Diagnostics)**: Diagnostic announcements (`*1001*9#...`), identity object model 119, firmware 1.3.8, slot objects, and indexed configuration registers (Dimension 35).

---

# #466 MyHomeServer1 Mixed Bus Capture (Lighting, Dimmer Level Reads, Thermoregulation)

Verbatim bus trace contributed by **@gdluck** on [#466 (comment 5895736715)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5895736715), exported from the bus card (HA 2026.9.4, integration 2.0.0b13, OWNd 2.0.0b8, gateway firmware 3.87.13).

| File | Type | Description |
|---|---|---|
| `myhome_trace_MyHomeServer1_all_2026-09-29T17-57-37.json` | Bus Card Export (200 frames, buffer truncated) | Startup light-state sweep (`*1*0*WHERE##` on 10-15), grouped on/off of points 24, 27, 28, 33, 36, dimmer level reads on point 66 (`*#1*66*#1*WHERE*0##` answered by `*#1*66*1*WHERE*2##`), WHO 1 command `*1*1000#0*0415##` and thermoregulation actuator/valve traffic (`*4*4002#NN*0#Z##`, `*#4*60/61*...`) with the gateway's `*#13` clock frames.

The file is in the tree because its 200 frames include the probe reading `*#4*169*0*...##` (probe 1 of zone 69) beside zones 35-70; the replay in `tests/test_probe_frames_not_zones.py` checks that no such frame names a heating zone (#549).

---

# #466 MyHomeServer1 Guided Cover Calibration Runs (WHERE 91 & Interleaved Energy Telemetry)

Verbatim bus trace contributed by **@Interstellar0verdrive** on [#466 (comment 5927373969)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5927373969), exported from the bus monitor (HA 2026.9.2, integration 2.0.0b13, OWNd 2.0.0b8, gateway firmware 2.87.13).

## Hardware Profile

- **Gateway Model**: BTicino MyHomeServer1
- **Firmware**: 2.87.13
- **Actuator Model**: BTicino F411 (as reported by contributor; on-wire frame identifies WHERE `91`)
- **Actuator Address**: WHERE `91`
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_trace_MyHomeServer1_all_2026-10-01T07-59-11.json` | Bus Monitor Trace (159 frames) | Guided cover calibration runs: eight timed movements on cover WHERE `91` started and stopped by the calibration wizard, with gateway translation frames (`*2*1000#x*`) and basic status replies, interleaved with WHO 18 Dimension 113 and Dimension 1200 telemetry on meters 51, 52, and 53. |

## Sequence of Actions Recorded & Subsystems Verified

1. **Cover Calibration Runs (WHO 2 - Eight Timed Movements)**:
   - Eight start/stop runs on WHERE `91`, four down and four up:

| Run | Start | Stop | Direction | Duration |
|---|---|---|---|---|
| 1 | 07:49:47 | 07:50:03 | down | ~16.0 s |
| 2 | 07:50:05 | 07:50:08 | up | ~3.5 s |
| 3 | 07:50:15 | 07:50:20 | down | ~5.4 s |
| 4 | 07:50:22 | 07:50:37 | up | ~15.2 s |
| 5 | 07:50:41 | 07:50:56 | down | ~15.0 s |
| 6 | 07:50:58 | 07:51:08 | up | ~9.4 s |
| 7 | 07:51:13 | 07:51:20 | up | ~6.5 s |
| 8 | 07:51:21 | 07:51:27 | down | ~6.4 s |

   - **Start Movement Sequence (Leading Idle Echo)**:
     Every directional TX command (`*2*1*91##` for UP, `*2*2*91##` for DOWN) triggers a distinctive 4-frame response pattern:
     1. TX `*2*dir*91##` (command write)
     2. RX `*2*1000#0*91##` then RX `*2*0*91##` ~50 ms later — a **leading idle/stopped status report** emitted before the motor begins moving. A naive cover state machine could mishandle this by staying stopped.
     3. RX `*2*1000#dir*91##` (moving translation frame)
     4. RX `*2*dir*91##` (basic moving status frame, ~450 ms after the leading idle)
   - **Stop Movement Sequence**:
     Every stop TX command (`*2*0*91##`) is answered strictly by `*2*1000#0*91##` and `*2*0*91##`.

2. **Energy Management (WHO 18)**:
   - **Dimension 113 (Active Power)**: 86 frames recording instantaneous active power on meters 51, 52, and 53 (`*#18*51*113*WATT##`). Meter 51 climbs from ~367 W to 526 W during cover motion; meters 52 and 53 remain steady at ~6 W and ~1 W.
   - **Dimension 1200 (Power Threshold)**: 9 frames total:
     - 3 threshold telemetry responses: `*#18*51*1200#1*125##`, `*#18*52*1200#1*125##`, `*#18*53*1200#1*125##` at 07:50:34.
     - 6 poll requests across 2 periodic rounds (5-minute interval): meters 51, 52, 53 queried at 07:52:39 and 07:57:39 (`*#18*METER*1200##`).

## Subsystems Verified (MyHomeServer1)

- **WHO 2 (Automation / Covers)**: WHERE `91` cover movement, eight start/stop cycles, leading idle frame transition handling (`*2*1000#0*` + `*2*0*` followed by `*2*1000#dir*` + `*2*dir*`), and clean stop transitions.
- **WHO 18 (Energy Management)**: Real-time Dimension 113 power readings on meters 51, 52, and 53, and Dimension 1200 periodic threshold polling and reports.

---

# #466 F455 Basic Gateway Traces (Bus Sweep, Dimmer Telemetry & Pushbuttons)

Verbatim bus trace contributed by **@lionelser** on [#466 (comment 5938596974)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5938596974), exported from Home Assistant diagnostics (HA 2026.9.4, integration 2.0.0b14, OWNd 2.0.0b9, gateway firmware 1.0.86).

## Hardware Profile

- **Gateway Model**: Legrand F455 ("Basic gateway", single SCS bus)
- **Firmware**: 1.0.86
- **WHO 13 Device Type Code**: `200`
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `config_entry-myhome_F455.json` | HA Diagnostic Download (80 frames: 42 rx / 38 tx) | Full diagnostic export with bus monitor capture during `myhome.sweep_bus`, physical dimmer queries (WHERE 11–17), physical pushbutton presses, and gateway identity replies. |

## Sequence of Actions Recorded & Subsystems Verified

1. **Gateway Identity & Diagnostics (WHO 13)**:
   - Device type `*#13**15##` -> `*#13**15*200##` (corroborating modern gateway family alongside F454, MH202, F461, H4890, MyHomeServer1).
   - Firmware version `*#13**16##` -> `*#13**16*1*0*86##` (firmware 1.0.86).
   - Gateway clock `*#13**0##` -> `*#13**0*20*40*21*##`.

2. **Lighting & Physical Dimmer Telemetry (WHO 1)**:
   - **Dimension 4 (Physical Dimmer Level Reports)**: WHERE `13` and WHERE `15` report Dimension 4 status (`*#1*13*4*100*2##` and `*#1*15*4*100*2##`), confirming physical modular dimmer telemetry on F455.
   - **Dimension 1 (Standard Level Reports)**: WHERE `11`, `12`, `14`, `16`, and `17` report Dimension 1 status (`*#1*12*1*134*5##`, `*#1*17*1*175*1##`, `*#1*16*1*200*5##`, `*#1*14*1*175*5##`, `*#1*11*1*200*5##`).
   - **Command Translation Pushbuttons**: Pushbutton press (`*1*1000#1*<where>##`) and release (`*1*1000#0*<where>##`) captured across WHERE `12`, `13`, and `17`.

3. **Subsystem Scans**:
   - Outbound queries for automation (`*#2*0##`), thermoregulation (`*#4*0##`), sound (`*#16*0*5##`), burglar alarm (`*#5*0##`), energy (`*#18*51*51##`...`*#18*59*51##`), and diagnostic identity (`*#1013*0*1##`).

---

# #466 F454 Relative Step Cover Positioning & Motor Deadband Telemetry (WHERE 31)

Verbatim bus trace contributed by **@anotherjulien** on [#466 (comment 6038376669)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-6038376669), exported from Home Assistant diagnostics (HA 2026.9.4, integration 2.0.0b14, OWNd 2.0.0b9, gateway firmware 2.0).

## Hardware Profile

- **Gateway Model**: BTicino F454
- **Firmware**: 2.0
- **Actuator Model / Motors**: Advanced cover actuator with Somfy Ilmo 50 WT tubular motors
- **Actuator Address**: WHERE `31`
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `config_entry-myhome_F454_step_commands.json` | HA Diagnostic Download (500 frames: 130 tx / 370 rx) | Autonomous relative step positioning runs on WHERE `31` (10%, 2%, and 1% steps Up/Down), command echos (`*2*1000#...`), in-flight Dimension 10 status reports, autonomous stop frames without HA stop command, and periodic WHO 18 energy telemetry on meters 51-53. |

## Sequence of Actions Recorded & Subsystems Verified

1. **Autonomous Relative Step Positioning (WHO 2)**:
   - **10% Step Runs**:
     - Down 10%: `*2*12#10#001*31##` -> echoed as `*2*1000#12#10#001#1*31##` -> in-flight Dimension 10 report `*#2*31*10*12*40*001*0##` (Moving Down from level 40) -> running event `*2*2*31##` -> stopped Dimension 10 report `*#2*31*10*10*30*001*0##` (level 30) -> autonomous stop frame `*2*0*31##` after ~2.04 s motor time.
     - Up 10%: `*2*11#10#001*31##` -> in-flight Dimension 10 report `*#2*31*10*11*30*001*0##` (Moving Up from level 30) -> stopped Dimension 10 report `*#2*31*10*10*40*001*0##` (level 40) -> autonomous stop frame `*2*0*31##` after ~2.17 s motor time.
   - **2% Step Runs**:
     - Down 2%: `*2*12#2#001*31##` -> level 40 to 38 in ~370 ms runtime -> autonomous stop.
     - Up 2%: `*2*11#2#001*31##` -> level 38 to 40 in ~355 ms runtime -> autonomous stop.
   - **1% Step Runs & Motor Deadband Discovery**:
     - Repeated 1% steps (`*2*12#1#001*31##` Down x3, then `*2*11#1#001*31##` Up x3) ran for ~130–210 ms each.
     - Actuator relay clicked, but duration was too brief to overcome the mechanical deadband/inertia of the Somfy Ilmo 50 WT motors, causing 0 physical curtain movement despite the actuator's internal register updating its Dimension 10 level by 1% per pulse.

---

# #466 BTicino MH200 F422 Secondary Bus Timed Covers & Plant Verification

Authentic on-wire bus trace recorded on a physical **BTicino MH200** (firmware 2.1.0) testing 10 motorized covers on secondary private bus `02` connected via an **F422 bus interface** (`WHERE = XX#4#02`), local bus cover 85, 35 physical lighting points, 8 burglar alarm zones, and 8 sound diffusion zones.

## Hardware Profile

- **Gateway**: BTicino MH200 (1st Generation Scenario Programmer)
- **Firmware**: 2.1.0
- **WHO 13 Device Type Code**: `4` (MH200)
- **WHO 1013 Object Model**: `4`
- **Bus Interface**: BTicino F422 (Bus 02)
- **Connection**: TCP OpenWebNet (Port 20000)

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_trace_MH200_f422_timed_covers.json` | Bus Monitor Trace (425 frames: 412 rx / 13 tx) | Complete plant trace recording F422 secondary bus 02 cover status query (`*#2*0#4#02##`), 10 stopped cover endpoints (`*2*0*XX#4#02##`), local bus cover 85, point-to-point queries, Dimension 10 query confirming standard timed relay actuator profile (no reply), 35 lighting points with discrete brightness levels (WHAT 7, 9, 10), 8 burglar alarm zones (`*5*11*#1##` .. `#8##`), and 8 sound diffusion zones with 2 sources. |

## Subsystems Verified (MH200 F422)

1. **WHO 2 (Automation / Covers)**:
   - **F422 Secondary Bus Addressing (`XX#4#02`)**: Bus scan `*#2*0#4#02##` discovers all 10 cover endpoints on private bus `02`: `11#4#02`, `21#4#02`, `12#4#02`, `22#4#02`, `13#4#02`, `14#4#02`, `15#4#02`, `16#4#02`, `18#4#02`, `19#4#02`.
   - **Local Bus Cover**: WHERE `85` reports stopped state on main bus (`*2*0*85##`).
   - **Standard Timed Relay Profile**: Point-to-point query `*#2*11#4#02##` returns `*2*0*11#4#02##`, while Dimension 10 position query `*#2*11#4#02*10##` returns no reply, confirming standard relay actuator operation (F411U2).

2. **WHO 1 (Lighting)**:
   - Status sweep covering 35 lighting endpoints with discrete brightness levels (WHAT 7, 9, 10) and live toggling events (`*1*1*51##`, `*1*0*51##`).

3. **WHO 5 (Burglar Alarm)**:
   - Multi-partition query `*#5*0##` returning empty-where partition states and all 8 zone statuses (`*5*11*#1##` through `*5*11*#8##`).

4. **WHO 16 (Sound Diffusion)**:
   - Sound system scan `*#16*0*5##` returning 8 audio zones (`21`, `22`, `23`, `14`, `35`, `36`, `17`, `18`) and 2 sound sources (`101`, `102`).

5. **WHO 13 & WHO 1013 (Gateway Identity)**:
   - Model code 4 (MH200), Firmware 2.1.0 (`*#13**16*2*1*0##`), and Object Model 4 (`*#1013**1*4##`).

---

# #466 BTicino F454 & F520 WHO 18 Dimension 1200 Energy Stream Lifecycle

Authentic on-wire bus trace captured and contributed by **@xtimmy86x** on [PR #661 (comment 6067730589, 6067865874)](https://github.com/OpenWebNet-HA/MyHOME/pull/661#issuecomment-6067730589) from a physical BTicino F454 gateway (firmware 2.0) and physical BTicino F520 energy meters. This verifies the complete lifecycle of WHO 18 Dimension 1200 automatic instant power updates from physical hardware on a live plant, including the start command, meter interval confirmation, periodic active power telemetry, and the zero-duration interval notification emitted upon 2-minute stream expiry.

## Hardware Profile

- **Gateway**: BTicino F454
- **Firmware**: 2.0
- **Energy Meter**: BTicino F520 (multi-circuit single-phase electricity meter / load controller)
- **Tested Energy Meter Addresses**: WHERE `56` (streamed address), plus background meters `51`, `53`, `54`, `55`, `57`, `58`, `59`, `510`, `511`
- **Connection**: TCP OpenWebNet (Port 20000)
- **Client Environment**: Home Assistant 2026.10.0.dev0, MyHOME 2.0.0b14, OWNd 2.0.0b9

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_trace_F454_who18_2026-10-08T19-30-16.json` | Bus Monitor Trace (66 frames: 1 tx / 65 rx) | Authentic, untouched on-wire trace capturing start stream command (`*#18*56*#1200#1*2##`), meter interval confirmation (`*#18*56*1200#1*2##`, delay ~235 ms), periodic active power telemetry across meters 51-511, zero-duration stream lease expiry notification (`*#18*56*1200#1*0##`, delay ~126.2 s), subsequent interval frame (`*#18*56*1200#1*255##`), and totalizer consumption reports (dimensions 51 and 54). |

## Sequence of Actions Recorded & Subsystems Verified

1. **Auto-Update Stream Start Command (WHO 18 Dimension 1200 Write)**:
   - Client sends 2-minute instantaneous active power stream request to meter 56:
     - `19:27:55.541903 TX *#18*56*#1200#1*2##`
   - Frame structure: `*#18*WHERE*#1200#Type*Time##` (`Type 1` = active power, `Time 2` = 2 minutes).

2. **Meter Auto-Update Interval Confirmation**:
   - Energy meter 56 confirms active stream lease ~235 ms after start command:
     - `19:27:55.776619 RX *#18*56*1200#1*2##`
   - Proves on physical hardware that meters answer Dimension 1200 writes with `*#18*WHERE*1200#Type*Time##`.

3. **Autonomous Active Power Telemetry (WHO 18 Dimension 113)**:
   - Periodic active power telemetry is received without polling queries:
     - Meter 56: `*#18*56*113*9##` (9 W, arriving at ~30s cadence across the capture window)
     - Meter 51: `*#18*51*113*572##`, `570##`, `569##`, `568##`, `571##`
     - Meter 53: `*#18*53*113*365##`, `366##`, `364##`
     - Meter 54: `*#18*54*113*0##`
     - Meter 55: `*#18*55*113*89##`, `88##`
     - Meter 57: `*#18*57*113*0##`
     - Meter 58: `*#18*58*113*0##`
     - Meter 59: `*#18*59*113*20##`
     - Meter 510: `*#18*510*113*144##`, `145##`
     - Meter 511: `*#18*511*113*67##`, `68##`

4. **Stream Expiry / Lease End Notification (Interval 0)**:
   - Exactly 126.18 seconds (~2 minutes plus bus roundtrip/scheduling) after the start command, meter 56 autonomously emits an interval 0 notification:
     - `19:30:01.726662 RX *#18*56*1200#1*0##`
   - This provides definitive physical proof that BTicino F520 energy meters broadcast `*#18*WHERE*1200#Type*0##` when the auto-update stream expires, allowing Home Assistant / MyHOME to seamlessly transition stream status and restart or deactivate streaming without polling.

5. **Subsequent Interval Frame**:
   - `19:30:01.870024 RX *#18*56*1200#1*255##` arrives ~143 ms after the expiry frame. Meter 54 also broadcasts `*#18*54*1200#1*255##` at `19:28:59.553859`.

6. **Cumulative Energy Totalizer & Partial Consumption Telemetry**:
   - Periodic totalizer reports:
     - Dimension 51 (Totalizer): `*#18*51*51*35098267##` (35,098,267 Wh), `*#18*53*51*18915332##`, `*#18*54*51*2186911##`, `*#18*55*51*3033954##`
     - Dimension 54 (Current day partial consumption): `*#18*51*54*13165##` (13,165 Wh), `*#18*53*54*7919##`, `*#18*54*54*1179##`, `*#18*55*54*1916##`

---

# #466 BTicino MyHomeServer1 WHO 25 Scenario Plus Trace & Bus Echo (`*25*11#0*11##`)

Verbatim bus trace contributed by **@TheDarkWizard** on [#466 (comment 6085724930)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-6085724930) in response to the community call for real-world WHO 25 scenario plus traces ([#466 (comment 6085158753)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-6085158753)), exported from Home Assistant diagnostics (HA 2026.9.4, integration 2.0.0b14, OWNd 2.0.0b9, gateway firmware 2.87.13).

## Hardware Profile

- **Gateway Model**: BTicino MyHomeServer1
- **Firmware**: 2.87.13
- **Connection**: TCP OpenWebNet (Port 20000)
- **Profile**: `MyHomeServer1Profile`

## Contributed Files

| File | Type | Description |
|---|---|---|
| `config_entry-myhome_MyHomeServer1_who25_scenario_plus.json` | HA Diagnostic Download (500 frames: 6 tx / 494 rx) | Authentic physical plant capture confirming gateway acceptance, SCS bus transmission, and OpenWebNet monitor echo of WHO 25 Scheduled Scenario PLUS command `*25*11#0*11##`, interleaved with WHO 18 Dimension 113 power readings on meters 51 and 52, WHO 4 climate reports, and WHO 1 lighting status. Originally uploaded as `Cen&Cen+CallToAction.json`. |

## Sequence of Actions Recorded & Subsystems Verified

1. **Scheduled Scenario PLUS (WHO 25)**:
   - Command transmitted from Home Assistant: `[tx] *25*11#0*11##` (`2026-10-09T17:14:12.215385+00:00`).
   - Gateway acknowledges and puts `B1 01 93 00` onto the physical SCS bus, echoing back on the monitor session: `[rx] *25*11#0*11##` (`2026-10-09T17:14:12.298174+00:00`) exactly 82.8 ms later.
   - Immediate status inquiry: `[tx] *#25*11##` (`2026-10-09T17:14:12.300230+00:00`).
   - OFF command attempt with parameter `#0`: `[tx] *25*12#0*11##` (`2026-10-09T17:14:39.916800+00:00`). Note that in the OpenWebNet protocol and `bt_luci` firmware, OFF takes no `#0` parameter (`*25*12*WHERE##`), explaining the absence of a monitor echo for this variant.

2. **Energy Management (WHO 18)**:
   - 424 frames of active power measurements (`*#18*51*113*<watts>##` on meter 51 and `*#18*52*113*<watts>##` on meter 52).

3. **Thermoregulation (WHO 4)**:
   - 63 frames reporting thermoregulation dimension 60 setpoints and status across zones (`*#4*1*60*49##`, `*#4*2*60*49##`, `*#4*5*60*49##`, etc.).

4. **Lighting (WHO 1)**:
   - 9 frames reporting actuator lighting status (`*1*0*0014##`, `*1*0*0016##`, etc.).



