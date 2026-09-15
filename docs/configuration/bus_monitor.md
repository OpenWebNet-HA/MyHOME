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

### 3. Diagnostic Actions
- **🧹 Sweep Bus**: A 1-click button that invokes the `myhome.sweep_bus` service. It queries the current status of all lighting actuators, shutters, climate probes, and gateway clocks to immediately hydrate the ring buffer with fresh data.
- **💾 Export Trace**: Generates and downloads a structured `.json` diagnostic file containing all captured frames with timestamps, parsed semantic attributes, direction flags, and ACK/NACK status.

---

## 📄 Exported Frame Data Format

When exporting traces or inspecting WebSocket frames, each frame adheres to the following JSON schema:

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
