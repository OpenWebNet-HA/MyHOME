# Issue #645: MyHomeServer1 Cover Send Queue & Stop Latency Traces

Captured and documented by **@Interstellar0verdrive** on **2026-10-11** on [Issue #645 (comment 6111193057)](https://github.com/OpenWebNet-HA/MyHOME/issues/645#issuecomment-6111193057).

## Hardware & Environment

- **Gateway Model**: BTicino MyHomeServer1 (Server MyHOME_Up)
- **Firmware Version**: 2.87.13
- **Bus Topology**: Main bus, ten timed covers controlled by BTicino F411 actuators (target cover at address WHERE `91`), no F422 area interface.
- **Home Assistant Version**: 2026.9.2
- **Integration**: MyHOME v2
- **Network / Concurrency Context**: A second Home Assistant instance was concurrently connected to the same MyHomeServer1 during test execution, maintaining active gateway sessions.

## Background & Objectives

Issue #645 addresses latency in sending stop commands for covers timed by Home Assistant. In a strictly first-in, first-out (FIFO) send queue:
- Auto-stops during position positioning (`set_cover_position`) and Venetian slat micro-pulses (#508) suffer latency if queued behind other commands or discovery queries, causing over-travel.
- The proposal introduces prioritized stop dispatching in `gateway_sessions.py` under the strict constraint: **A stop must never overtake an unwritten frame for the cover it stops (nor overlapping area, group, or general scopes)**.
- These traces provide empirical ground truth on a physical MyHomeServer1 regarding per-frame queue serialization delay, idle command-session reconnect latency, and back-to-back direction/stop mechanical deadband behavior.

---

## Recorded Traces & Observations

### 1. Status Request Burst (Queue Latency Baseline)
- **Command**: Ten point-to-point status requests `*#2*<WHERE>##` queued within 2 ms to ten distinct covers from one script.
- **Worker Concurrency**: Processed strictly in order by a single sending worker (worker 0); the second command session supported by MyHomeServer1 profile was not engaged (HA integration default worker count is 1).
- **Latency Measurements**:
  - Unqueue-to-ACK round trip: ~148 ms per frame.
  - Cover status reply arrived 65–105 ms after frame unqueued.
  - Next frame was unqueued only after the previous frame was acknowledged by the gateway.
  - 10th frame unqueued at **+1.33 s** and acknowledged at **+1.44 s**.
  - **Verdict**: In a pure FIFO queue without prioritization, a frame queued behind $n$ items incurs $n \times 145\text{ ms}$ of queue latency.

### 2. Burst After Idle Session Timeout (Reconnect Penalty)
- **Command**: The same 10 status requests executed after 90 s of bus idle (exceeding the 15 s command session idle disconnect timeout).
- **Reconnect Timing**: Socket opened, OpenWebNet SHA HMAC handshake completed, and session established in **12 ms**.
- **Per-Frame Latency**: First frame ACK at +118 ms; subsequent frames 132–140 ms per frame. 10th frame acknowledged at **+1.36 s**.
- **Verdict**: Command session reconnection overhead is negligible (~12 ms); latency is overwhelmingly dominated by serial bus round trips.

### 3. Direction Immediately Followed by Stop (No Delay)
- **Command**: `*2*1*91##` (up) and `*2*0*91##` (stop) queued in the exact same millisecond after idle session timeout.
- **Observed Bus Timeline**:
  ```text
    +0 ms   *2*1*91##       taken by worker 0 (command session reopened)
   +50 ms   *2*1000#0*91##  received (gateway translation)
   +95 ms   *2*0*91##       received (momentary stop status)
  +118 ms   *2*1000#1*91##  received (motion started)
  +119 ms   *2*1*91##       acknowledged by gateway (ACK)
  +139 ms   *2*0*91##       taken by worker 0
  +179 ms   *2*1000#0*91##  received (stopping translation)
  +179 ms   *2*0*91##       acknowledged by gateway (ACK)
  +216 ms   *2*0*91##       received (actuator stop status)
  ```
- **Physical Actuator Behavior**: The writes landed 60 ms apart on the bus. The motor did not move at all, confirming that 60 ms is well within the motor relay actuation deadband (<0.4 s, as observed in #647).
- **FIFO Constraint Invariant**: Preserving FIFO order per cover ensures that immediate stops never cause spurious motor movement.

---

## Fixture Files in this Directory

| File | Format | Description |
|---|---|---|
| `live_command_timeline_issue_645.log` | Raw Monitor & Dispatch Log | Raw millisecond-accurate dispatch and monitor log of Trace 3 (direction followed by stop). |
| `myhome_trace_MyHomeServer1_cover_direction_then_stop_2026-10-11.json` | JSON Trace Archive | Structured OpenWebNet trace archive for Trace 3. |
| `myhome_trace_MyHomeServer1_cover_burst_latency_2026-10-11.json` | JSON Trace Archive | Structured OpenWebNet trace archive for Trace 1 (ten status requests queue latency). |
