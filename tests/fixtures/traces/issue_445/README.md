# #445 MyHomeServer1 & LN-4660M2 Centralized Shutter Traces

Authentic on-wire bus traces contributed by **@f18m** (Francesco Montorsi) on [#445](https://github.com/OpenWebNet-HA/MyHOME/issues/445) and [#466 (comment 5853591733)](https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-5853591733), with a 2026-09-28 follow-up round answering the maintainer's outstanding questions ([comment 5854237269](https://github.com/OpenWebNet-HA/MyHOME/issues/445#issuecomment-5854237269)) in [comment 5868017035](https://github.com/OpenWebNet-HA/MyHOME/issues/445#issuecomment-5868017035).

## Hardware Profile

- **Gateway Model**: BTicino MyHomeServer1
- **Gateway Manufacturer**: BTicino S.p.A.
- **Connection**: TCP OpenWebNet (Port 20000)
- **Centralized Button Model**: BTicino LN-4660M2 (Livinglight centralized cover control button)
- **Actuators Tested**: 7 SCS cover actuators (`02`, `03`, `04`, `08`, `09`, `0010`, `0011`)
- **Environment**: Home Assistant 2026.9.3, MyHOME integration 2.0.0b13, OWNd 2.0.0b8

## Contributed Files

| File | Type | Frames | Description |
|---|---|---|---|
| `myhome_trace_MyHomeServer1_LN4660M2_centralized_up_then_stop_2026-09-26.json` | Bus Monitor Trace | 16 | LN-4660M2 centralized UP button pressed, followed by STOP button press, showing multi-actuator Dimension 10 position feedback and stop confirmations. |
| `myhome_trace_MyHomeServer1_LN4660M2_centralized_down_then_stop_2026-09-26.json` | Bus Monitor Trace | 26 | LN-4660M2 centralized DOWN button pressed, actuators reaching 0% / mid-travel, followed by STOP button press. |
| `myhome_trace_MyHomeServer1_LN4660M2_stationary_stop_2026-09-28.json` | Bus Monitor Trace | 15 | LN-4660M2 centralized STOP/PRESET button pressed while all shutters were already stationary, followed by Dimension 10 status polling across 7 actuators. |
| `myhome_trace_MyHomeServer1_LN4660M2_local_room_down_then_stop_2026-09-28.json` | Bus Monitor Trace | 4 | A single actuator's own LOCAL room keypad (not the centralized button) pressed DOWN then STOP, captured on address `02`. |

## Empirical Protocol Discoveries

1. **Centralized General Cover Commands Send Advanced Automation Frames**:
   - Rather than simple point-to-point or basic commands (`*2*1*0##` / `*2*2*0##`), the physical LN-4660M2 centralized controller broadcasts multi-parameter Advanced Automation commands on General address `0`:
     - **Advanced UP (General)**: `*2*11#100#001#1*0##` (WHAT `11`: target 100%, step `001`, priority `1`, WHERE `0`)
     - **Advanced DOWN (General)**: `*2*12#100#001#1*0##` (WHAT `12`: target 100%, step `001`, priority `1`, WHERE `0`)
     - **Advanced STOP (General)**: `*2*10#001#1*0##` (WHAT `10`: step `001`, priority `1`, WHERE `0`)

2. **Telemetry Burst Following Centralized Stop / Limit**:
   - When a centralized movement is stopped or completes, each addressed actuator reports:
     1. Its current Dimension 10 position: `*#2*WHERE*10*10*POS*001*0##`
     2. Individual stop confirmation: `*2*0*WHERE##`

3. **4-Digit Addressing for Automation Actuators**:
   - Actuators on MyHomeServer1 plants can report addresses in 4-digit format (`0010`, `0011`), validating address normalization in the parser and router.

4. **STOP/PRESET Is Identical Whether Stationary or Mid-Movement** (2026-09-28 follow-up):
   - Pressing the LN-4660M2's middle STOP/PRESET button while every shutter is already stationary sends the exact same `*2*10#001#1*0##` Advanced STOP frame observed mid-movement. No separate preset-recall command or Dimension 11 (tilt) write was seen on the bus, closing the ambiguity raised for this PR.

5. **Local Room Keypads Use the Basic Syntax, Not the Advanced One**:
   - The LN-4660M2's own local room button (controlling a single actuator, not the centralized/general one) sends the plain `*2*WHAT*WHERE##` syntax (`*2*2*02##` DOWN, `*2*0*02##` STOP) at the actuator's individual point address, never the multi-parameter Advanced form used on General address `0`. This is why `is_general` correctly gates the centralized device triggers: a local keypad press cannot be mistaken for a centralized one.
   - The same capture also shows the actuator reporting a transitional Dimension 10 state mid-travel: `*#2*02*10*12*100*001*0##` (status code `12` = "closing from position 100%"), the first real-world confirmation of `OWNAutomationEvent`'s `state == 12` branch.

## Out of Scope for This PR

A third 2026-09-28 capture (a "chaos test": power-cycling the MyHomeServer1 gateway while queuing several `cover` commands from Home Assistant, then restoring power) showed queued commands being delivered on reconnect and one cover sitting in an `Opening` state for roughly three minutes before settling. Nothing in that trace involves a frame shape this PR's device triggers need to parse, so it was not turned into a fixture or a golden entry here; it may be worth its own issue if the delayed-reconciliation behavior needs to be addressed.
