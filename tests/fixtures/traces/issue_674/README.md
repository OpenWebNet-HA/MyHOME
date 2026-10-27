# #674 Legrand F455 Gateway Trace (16 Sequential Climate Zone Timeouts vs. Spontaneous Bus Telemetry)

Verbatim physical plant Home Assistant log captured from an authentic Legrand F455 gateway by **@gdluck** on [#674](https://github.com/OpenWebNet-HA/MyHOME/issues/674).
Captured live on 2026-10-09 between 20:31:20 and 20:34:11 UTC. In MyHOME, physical traces are immutable ground truths: never edit a raw monitor frame.

## Hardware & Plant Profile

- **Gateway Model**: Legrand F455 (Basic Web Server / OpenWebNet IP Gateway)
- **IP Address**: 192.168.1.136 (port 20000)
- **Model Name**: `F455` (WHO=1013 OBJECT_MODEL `8` / catalog 003594)
- **Architecture**: Embedded hardware gateway bridging IP to physical SCS bus. Does not run the Server MyHOME_Up daemon.
- **Plant Topology**:
  - At least 16 active heating zones (`35`, `34`, `36`, `28`, `37`, `27`, `29`, `26`, `15`, `17`, `16`, `14`, `9`, `6`, `8`, `21`)
  - Peripheral zones: `42` (with humidity sensor), `43`, `45`, `46`, and actuator zones `64`, `65`
  - Central unit `#0` absent (standalone zone configuration)
- **Environment**:
  - Home Assistant: 2026.9.4 / 2026.10
  - Integration: 2.0.0b15

## Files in this Fixture

| File | Type | Description |
|---|---|---|
| `home-assistant_2026-10-09T17-34-15.609Z.log` | Authentic HA Log (315 lines) | **Ground Truth**. Complete verbatim Home Assistant debug log capturing startup, the 16 consecutive command timeouts, and concurrent monitor frames. |
| `myhome_trace_f455_climate_timeouts_2026-10-09.json` | Derived Trace Archive (67 RX, 16 timeouts) | **Derived Archive**. Structured JSON archive containing all 67 received frames and all 16 command timeout events with ISO timestamps. |

## Sequence of Events & Analysis

### 1. The 16 Consecutive 10-Second Command Session Timeouts

When Home Assistant starts, the integration executes `initial_discovery()` with the WHO 4 startup sweep (`*#4*0##`).
Concurrently, because `message_has_state` unconditionally returned `False` for `ClimateEntity`, all 16 discovered climate entities retained `_poll_on_add = True`.
When initial discovery completed, all 16 entities immediately invoked `async_update()`, queuing point-to-point status requests `*#4*<zone>##` on command worker 0:

| Timestamp | Timeout Request | Command Session Action | Elapsed |
|---|---|---|---|
| 20:31:36.708 | `*#4*35##` | Socket timeout (10.08 s) -> command session closed | 10.1 s |
| 20:31:46.793 | `*#4*34##` | Reconnect, HMAC-SHA256 handshake -> Socket timeout (10.08 s) | 10.1 s |
| 20:31:56.881 | `*#4*36##` | Reconnect, HMAC-SHA256 handshake -> Socket timeout (10.08 s) | 10.1 s |
| 20:32:06.964 | `*#4*28##` | Reconnect, HMAC-SHA256 handshake -> Socket timeout (10.08 s) | 10.1 s |
| 20:32:17.055 | `*#4*37##` | Reconnect, HMAC-SHA256 handshake -> Socket timeout (10.08 s) | 10.1 s |
| 20:32:27.144 | `*#4*27##` | Reconnect, HMAC-SHA256 handshake -> Socket timeout (10.08 s) | 10.1 s |
| 20:32:37.225 | `*#4*29##` | Reconnect, HMAC-SHA256 handshake -> Socket timeout (10.08 s) | 10.1 s |
| 20:32:47.299 | `*#4*26##` | Reconnect, HMAC-SHA256 handshake -> Socket timeout (10.08 s) | 10.1 s |
| 20:32:57.394 | `*#4*15##` | Reconnect, HMAC-SHA256 handshake -> Socket timeout (10.08 s) | 10.1 s |
| 20:33:07.494 | `*#4*17##` | Reconnect, HMAC-SHA256 handshake -> Socket timeout (10.08 s) | 10.1 s |
| 20:33:17.583 | `*#4*16##` | Reconnect, HMAC-SHA256 handshake -> Socket timeout (10.08 s) | 10.1 s |
| 20:33:27.673 | `*#4*14##` | Reconnect, HMAC-SHA256 handshake -> Socket timeout (10.08 s) | 10.1 s |
| 20:33:39.809 | `*#4*9##`  | Reconnect, HMAC-SHA256 handshake -> Socket timeout (12.13 s) | 12.1 s |
| 20:33:49.885 | `*#4*6##`  | Reconnect, HMAC-SHA256 handshake -> Socket timeout (10.08 s) | 10.1 s |
| 20:33:59.993 | `*#4*8##`  | Reconnect, HMAC-SHA256 handshake -> Socket timeout (10.11 s) | 10.1 s |
| 20:34:10.083 | `*#4*21##` | Reconnect, HMAC-SHA256 handshake -> Socket timeout (10.09 s) | 10.1 s |

**Total Lockup Duration**: 161.3 seconds (~2.7 minutes). During this entire time, command session worker 0 was 100% paralyzed, blocking all other automation and control frames (lighting, covers).

### 2. Concurrent Spontaneous Bus Telemetry from the Sweep

Crucially, while the command worker was serial-timing-out, the OpenWebNet monitor connection was actively receiving all zone telemetry triggered by the `*#4*0##` bus sweep:

- **Measured Temperatures (Dimension 0)**:
  - `*#4*46*0*0208##` -> Zone 46 is 20.8 °C
  - `*#4*35*0*0196##` -> Zone 35 is 19.6 °C
  - `*#4*34*0*0187##` -> Zone 34 is 18.7 °C
  - `*#4*36*0*0193##` -> Zone 36 is 19.3 °C
- **Target Setpoints (Dimension 14)**:
  - `*#4*45*14*0100*3##` -> Zone 45 setpoint 10.0 °C
  - `*#4*35*14*0100*3##` -> Zone 35 setpoint 10.0 °C
  - `*#4*34*14*0100*3##` -> Zone 34 setpoint 10.0 °C
  - `*#4*36*14*0100*3##` -> Zone 36 setpoint 10.0 °C
- **Operational Targets (Dimension 12)**:
  - `*#4*45*12*0100*3##` -> Zone 45 active target 10.0 °C
  - `*#4*34*12*0100*3##` -> Zone 34 active target 10.0 °C
  - `*#4*36*12*0100*3##` -> Zone 36 active target 10.0 °C
  - `*#4*35*12*0100*3##` -> Zone 35 active target 10.0 °C
- **Local Offsets (Dimension 13)**:
  - `*#4*35*13*00##` -> Zone 35 knob offset 0 °C
  - `*#4*34*13*00##` -> Zone 34 knob offset 0 °C
  - `*#4*36*13*00##` -> Zone 36 knob offset 0 °C
- **HVAC Modes**:
  - `*4*1*36##` -> Zone 36 Heating
  - `*4*1*35##` -> Zone 35 Heating
  - `*4*1*34##` -> Zone 34 Heating
- **Valves & Actuators (Dimension 19)**:
  - 16 zones (`6`, `7`, `8`, `9`, `14`, `15`, `16`, `17`, `26`, `28`, `29`, `34`, `35`, `36`, `42`, `43`) reporting `*#4*Z*19*0*0##` (cooling valve OFF, heating valve OFF)
- **Humidity (Dimension 60)**:
  - `*#4*42*60*52##` -> Zone 42 main sensor humidity 52.0%

All entity states had ALREADY arrived spontaneously. The point-to-point polls were completely redundant and caused the failure.

## Root Cause

1. **Protocol Specification (WHO 4)**:
   In OpenWebNet WHO 4 (`WHO_4 2.pdf`), dimension-less status requests `*#4*WHERE##` are only defined for:
   - `WHERE = 0` (`*#4*0##`): General bus sweep.
   - `WHERE = #0` (`*#4*#0##`): 99-zone central unit.
   Individual zones (`1..99`) do NOT recognize dimension-less status requests.
2. **Physical Gateways vs. Server Gateways**:
   - On **MyHomeServer1**, the proprietary Linux daemon intercepts `*#4*<zone>##` and replays 5 synthetic frames from its internal database (#649).
   - On **F455** (and F454, MH200N, MH202, F452), the gateway is a pure bridge forwarding frames directly to the SCS bus. Physical thermostats (LN4691, 3550, etc.) have no handler for `*#4*<zone>##` and ignore it. The SCS bus is silent, the gateway sends nothing, and the OWNd command session times out after 10 seconds.
3. **Integration Polling Loop**:
   - `message_has_state` unconditionally returned `False` for `ClimateEntity`, forcing `_poll_on_add = True` on all zones.
   - `async_update()` called `OWNHeatingCommand.status(self._full_where)`, generating `*#4*<zone>##`.
