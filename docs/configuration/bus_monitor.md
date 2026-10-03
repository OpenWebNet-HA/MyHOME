# Lovelace Bus Monitor Card & Diagnostics

The MyHOME integration includes an embedded, real-time **OpenWebNet Bus Monitor** Lovelace card for inspecting SCS bus traffic, diagnosing communication issues, and generating trace reports.

---

## 🖥️ Overview & Architecture

Unlike traditional external diagnostic tools that require a separate gateway socket (which can exhaust the gateway's limited socket pool), the MyHOME Bus Monitor operates **completely in-band**:

- **Zero Socket Overhead**: It taps directly into the integration's existing persistent Event Session and Command Session.
- **Bounded Circular Buffer**: Maintains the latest 500 captured bus frames in a lightweight ring buffer in memory.
- **Real-Time WebSocket Streaming**: Frames are streamed live to the Lovelace frontend using Home Assistant's native WebSocket API.

```
┌─────────────────┐       ┌─────────────────┐
│ Event Session   │       │ Command Session │
│   (Bus RX)      │       │   (Bus TX)      │
└────────┬────────┘       └────────┬────────┘
         │                         │
         └───────────┬─────────────┘
                     ▼
       ┌───────────────────────────┐
       │     In-Band Packet Tap    │
       │   (bus_monitor.py: 500)   │
       └─────────────┬─────────────┘
                     ▼
       ┌───────────────────────────┐
       │   WebSocket Subscription  │
       │ (myhome/bus_monitor/sub)  │
       └─────────────┬─────────────┘
                     ▼
       ┌───────────────────────────┐
       │ Lovelace Dashboard Card   │
       │  (myhome-bus-card.js)     │
       └───────────────────────────┘
```

---

## 🎴 Adding the Card to your Lovelace Dashboard

The frontend card is bundled directly with the integration and registered automatically.

### Method 1: UI Dashboard Editor
1. In Home Assistant, open your dashboard and click the pencil icon (**Edit Dashboard**).
2. Click **Add Card** and choose **Manual** (at the bottom).
3. Paste the following configuration:

```yaml
type: custom:myhome-bus-card
title: MyHOME Bus Monitor
```

4. Click **Save**.

---

## 🔍 Card Controls & Features

The card interface provides a live telemetry stream and controls:

### 1. Live Streaming Controls
- **▶️ Resume / ⏸️ Pause**: Pause the live scrolling stream to examine a specific sequence of frames without new telegrams pushing it out of view.
- **🗑️ Clear**: Clears the current frontend display buffer.

### 2. Powerful Filtering
- **Filter by WHO Subsystem**: Click chips to isolate specific traffic:
  - `💡 WHO=1` Lighting
  - `🪟 WHO=2` Automation / Covers
  - `🌡️ WHO=4` Thermoregulation
  - `🚨 WHO=5` Burglar Alarm
  - `🔘 WHO=15 / WHO=25` CEN & CEN+ Scenario Controls
  - `🎵 WHO=16` Sound System / Audio Matrix
  - `⚙️ WHO=13` Gateway Diagnostics
- **Direction Filter**: Switch between `All`, `RX Only` (bus events), or `TX Only` (commands sent from Home Assistant).
- **Free-Text & Regex Search**: Search for specific addresses (e.g. `*1*1*12##` or `12#1`).

### 3. Capturing: Start Trace vs Sweep Bus

Two ways to begin a capture. **Both are harmless** — neither can switch a load, move a shutter or touch the alarm.

- **🔴 Start Trace / ⏹ Stop Trace**: clears the buffer and records what the bus says while you reproduce a problem (press a wall switch, run an automation, move a cover); the badge shows **● REC**. Nothing is sent. **Stop Trace** freezes the buffer (same as Pause) so the export is exactly what you reproduced; **Resume** returns to the live view. Use this for bug reports about behaviour.
- **🧹 Sweep Bus**: clears the buffer and invokes `myhome.sweep_bus`, which sends one read-only status request per subsystem; every device answers with its current state, so the buffer becomes a **device inventory**. Use this for "which devices does the integration see" questions (duplicates, missing zones).
- **Clear** returns to trace mode. Without pressing anything, the live buffer is a trace.
- **Across an HA restart**: the card keeps its buffer and, once Home Assistant is back, backfills the new process's ring (up to **Max Frames**, default 500 = the whole ring), so the startup status replies are in the next export. If you set **Max Frames** below 500 in the card config, raise it for this. **Download diagnostics** (integration page → ⋮) right after the restart is the backup: it carries the whole 500-frame ring as well.
- The blue **ⓘ** button in the title row (next to the LIVE / REC / PAUSED badge) opens this explanation inside the card; it turns amber while open.

### 4. Export / Copy
- **💾 Export Trace / Export Sweep**: downloads the frames **currently shown** (active WHO / WHERE / direction filters applied) as a structured `.json` file with timestamps, parsed attributes, direction and ACK/NACK flags. The label follows the capture kind you chose, and so does the file name:

  ```
  myhome_<kind>_<gateway model>_<filter>_<UTC timestamp>.json
  myhome_trace_MH200N_all_2026-09-13T11-52-19.json        passive capture, no filter
  myhome_trace_MH200N_who2-rx_2026-09-13T11-53-07.json    passive capture, WHO=2 + RX filter
  myhome_sweep_MH200N_all_2026-09-13T11-52-19.json        buffer populated by a Sweep Bus click
  ```

  Clear the filters first if you want the whole buffer.

- **📋 Copy Trace / Copy Sweep**: copies the same shown frames as a markdown diagnostic bundle (environment, gateway, capture kind, active filter, frames) to the clipboard and opens the GitHub issue form.

### 5. Transmit frame (⚠️ direct bus command)

The bar at the bottom writes a raw OpenWebNet frame to the SCS bus exactly as typed. That **can** switch loads, move shutters, or arm/disarm the burglar alarm, so it is disabled until you tick **I understand the risk** in the orange bar above it; the bar turns red while armed. Untick it when you are done. Start Trace and Sweep Bus never use this path. (The backend additionally refuses the command for non-administrator users.)

### 6. Time stamps

Frames are stamped in **UTC** by the integration (`timestamp` / `iso_time`) and rendered by the card in the **browser's local time zone**, so they line up with the Home Assistant logbook. Exports keep the UTC values. *(#305)*

### 7. Permissions

Reading the stream, history and gateway info is available to any signed-in user. **Send frame** and **Clear buffer** require an **administrator** user: a non-admin (or a kiosk/long-lived token created by one) gets `Unauthorized`, because a raw `*5*…##` frame can arm or disarm the burglar alarm.

---

## 📄 Exported Capture Format

Every export starts with a `capture` block describing what the file is, so it stays self-explanatory even after it is renamed:

```json
"capture": {
  "kind": "sweep",
  "started_at": "2026-09-13T11:52:15.104Z",
  "filters": { "who": "2", "where": null, "direction": "rx" },
  "window": {
    "first": "2026-09-13T11:49:44.845Z",
    "last": "2026-09-13T11:52:18.911Z",
    "frames": 37,
    "buffer_frames": 200,
    "buffer_depth": 200,
    "truncated": true
  }
}
```

`truncated: true` means the ring buffer had already wrapped when you exported — the first frame of a sequence you are looking for may have been evicted. Then follow `environment`, `gateway`, `telemetry` and `frames`; each frame adheres to the following schema (`direction`, `dimension` and the ACK/NACK flags are included in exports since 2.0.0b13):

```json
{
  "timestamp": 1726085842.123,
  "iso_time": "2026-09-11T20:17:22.123456+00:00",
  "direction": "rx",
  "raw": "*1*1*21##",
  "who": "1",
  "where": "21",
  "what": "1",
  "dimension": null,
  "is_ack": false,
  "is_nack": false
}
```

---

## 🩺 Home Assistant Diagnostics Integration

In addition to the real-time Lovelace card, MyHOME fully supports Home Assistant's native **Download Diagnostics** feature:

1. Navigate to **Settings** -> **Devices & Services** -> **MyHOME**.
2. Click the three-dots menu on your gateway device and select **Download diagnostics**.
3. The generated report includes sanitized gateway connection stats, active entities, latency metrics, and recent bus activity without exposing passwords or private credentials.
