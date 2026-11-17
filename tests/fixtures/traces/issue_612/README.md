# #612 MH200N Startup Sweep Verification, CEN Pushbutton, and Scenario 1 Execution

Authentic on-wire bus trace contributed by **@Depechie** on
[#612 (comment 6046447690)](https://github.com/OpenWebNet-HA/MyHOME/issues/612#issuecomment-6046447690).
Captured from a physical BTicino MH200N controller (`00:03:50:01:75:8b`) during Home Assistant startup discovery and subsequent scenario execution.

> [!NOTE]
> **Timezone Alignment**: Timestamps referenced in this document and in the original Home Assistant log output (e.g. `22:38:11`) are local European Central Summer Time (CEST, UTC+2). The accompanying JSON trace file records canonical ISO 8601 timestamps in UTC (e.g. `20:38:11Z`, window `20:37:46.407Z` to `20:38:23.840Z`).

## Contributed Files

| File | Gateway | Frames | Description |
|---|---|---|---|
| `myhome_trace_MH200N_startup_cen_scene_2026-10-07T20-38-24.json` | MH200N (fw 1.0, SSDP) | 193 | Complete startup discovery sequence, point-to-point status polls, CEN object 61 button 3 press (`*15*03*61##`), MH200N Scenario 1 start (`*17*1*1##`), sequence actuation (`*1*1*25##`, `*1*1*74##`, `*1*1*26##`, `*17*2*1##`, `*1*1*22##`), and WHO 4 multi-zone climate status queries. |

## Plant Architecture

- **Gateway**: BTicino MH200N (Scenario Programmer & Gateway)
- **Subsystems**:
  - 33 lighting actuators (WHO 1)
  - 5 switches / aux relays (WHO 1)
  - 5 climate zones (WHO 4, with 99-zone central unit / 4-zone master control)
  - CEN scenario pushbuttons (WHO 15)

## Empirical Protocol Discoveries & Test Invariants

1. **Elimination of Motion Sensor Area Sweeps**:
   - Confirms fix from PR #614: Startup discovery emits initial discovery poll `*#1*0##` without triggering unintended motion sensor sweeps (`*#1*00##`, `*#1*1##`).
2. **External Scenario Triggering (WHO 15 -> WHO 17)**:
   - At timestamp `22:38:11.321 CEST` (`20:38:11.321 UTC`), the bus captures an asynchronous user button press: `*15*03*61##` (Button 3 of CEN object 61 pressed).
   - Exactly 7 ms later (`22:38:11.328 CEST`), the MH200N internal scenario engine responds by launching Scenario 1: `*17*1*1##`.
3. **Controller-Autonomous Lighting Sequence**:
   - The MH200N autonomously broadcasts lighting ON commands for its programmed scene:
     - `*1*1*25##` (Light 25 switched ON)
     - `*1*1*74##` (Light 74 switched ON)
     - `*1*1*26##` (Light 26 switched ON)
     - `*17*2*1##` (Scenario 1 completes and stops)
     - `*1*1*22##` (Light 22 switched ON)
4. **WHO 17 Scene Event Protocol & Lifecycle Semantics**:
   - OpenWebNet scenario events take the form `*17*<STATE>*<SCENARIO>##`, where state:
     - `1` = started (`is_on: true`, `is_enabled: null`)
     - `2` = stopped (`is_on: false`, `is_enabled: null`)
     - `3` = enabled (`is_on: null`, `is_enabled: true`)
     - `4` = disabled (`is_on: null`, `is_enabled: false`)
   - **Crucial Distinction**: `is_on: true` denotes the scenario execution *lifecycle* (the scenario program has been launched), not the electrical state of physical loads. A scenario programmed to turn all house lights OFF will still emit `*17*1*<id>##` (`is_on: true`) when triggered, followed by light OFF commands, and finally `*17*2*<id>##` (`is_on: false`) upon completion.
   - Handled via `myhome_scene_event` bus events with payload `{scenario: 1, where: "1", state: 1, is_on: true, is_enabled: null, gateway_mac: ..., entry_id: ...}`.
5. **WHO 4 Climate Telemetry**:
   - Contains multi-dimensional climate status responses:
     - Zone ambient sensor temperature: `*#4*<zone>*0*<temp>##`
     - Local target temperature: `*#4*<zone>*12*<temp>*3##`
     - Operating mode: `*4*<mode>*<zone>##`
     - Local control state: `*#4*<zone>*13*<val>##`
     - Effective setpoint temperature: `*#4*<zone>*14*<temp>*3##`
