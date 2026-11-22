# Getting Started & Configuration Overview

This guide walks you through onboarding, configuring, and automating your Legrand / BTicino MyHOME SCS installation with Home Assistant using the **v2.0 UI-first architecture**.

---

## 📋 Prerequisites

Before adding the integration to Home Assistant, ensure:

1. **Network Connectivity**: Your OpenWebNet IP gateway (F454, MyHomeServer1, MH200N/201/202, F453AV) is connected to your local network and powered on.
2. **Fixed IP Address**: A static IP address or permanent DHCP lease reservation on your local router is strongly recommended.
3. **OpenWebNet Password**:
    - For standard gateways (F454, MH201): Note your numeric (4 or 9 digits) or alphanumeric password configured in MyHOME_Suite, MyHotelSuite (for MH201), or TiMyHome.
    - For MyHomeServer1: Note the installer password configured via the MyHOME_Up app.
    - If open LAN authentication is disabled on the gateway, no password is required.
4. **Integration Installed**: The MyHOME custom component is installed (see the [Installation Guide](../getting-started/installation.md)).

---

## 🚀 Step 1: Add the Gateway via Config Flow

In v2, gateway setup is **100% UI-first**:

1. In Home Assistant, navigate to **Settings → Devices & Services**.
2. If your gateway is discovered automatically via **SSDP / UPnP**, click **Configure** on the discovery card (OpenWebNet gateways do not use mDNS).
3. If adding manually:
    - Click **Add Integration** in the bottom right corner, search for **MyHOME**, and choose **Custom (IP Gateway)**.
4. Fill in the connection parameters:
    - **Address** (`address` / `host`): Strict numeric IPv4 address of the gateway (e.g. `192.168.1.50`; `.local` mDNS hostnames are rejected).
    - **Port** (`port`): `20000` (default OpenWebNet TCP port).
    - **MAC Address & Device Type** (`serialNumber` / `modelName`): Prompted only if UPnP metadata lookup at that IP fails; refined automatically via `WHO = 13` / `WHO = 1013` probing once connected.
    - **Password** (`password`): Prompted when the gateway requires numeric OPEN or SHA-256 HMAC authentication.
5. Click **Submit**. Home Assistant will establish the command session (`*99*0##`) and event listening session (`*99*1##`), verify the gateway hardware identity, and create the gateway device entry.

For full parameter specifications and troubleshooting, see [Gateways & Connection Setup](gateways.md).

---

## 🔍 Step 2: First Bus Discovery & Device Creation

Once connected:

* **Automatic Bus Scanning**: Startup discovery queries supported stateful subsystems (`WHO = 1, 2, 4, 5, 16, 18`, gated by the gateway's hardware profile and firmware-refusal guards) plus any registered `3xxx` (`WHO = 25`) dry contacts — stateless `WHO = 15` / `WHO = 25` scenario pushbuttons are never polled at startup and instead register on button press.
* **Device Registry Linking**: Discovered actuators, thermostats, and sensors are automatically grouped and linked to your gateway device via Home Assistant's `via_device_id` registry model.
* **Non-Destructive Transition**: If you have an existing `/config/myhome.yaml` file from v0.9.4, entity names and physical SCS groups (`#G` under `light:`) are read on startup as a compatibility overlay.
* **Organizing Entities**: Open **Settings → Devices & Services → Entities** to customize entity names, assign rooms/areas (e.g. *Living Room*, *Kitchen*), and set custom icons.

---

## ⚙️ Step 3: Tune Integration Options

Fine-tune runtime parameters by clicking **Configure** on the MyHOME integration card:

* **Command Worker Concurrency (`command_worker_count`)**: Number of asynchronous command workers (default `1`, or `2` on `F455` and `MyHomeServer1`; up to `2` on `MH202` and up to `4` on `F454`, `F455`, `F461`, and `MyHomeServer1`).
* **Dimmer Transition Mode (`transition_mode`)**: Choose between `software_stepped` (smooth **25-step** software fade interpolation managed by Home Assistant) and `native` (actuator hardware fade speed parameter).
* **Event Bus Broadcasting (`generate_events`)**: Toggle whether raw bus frames are emitted as `myhome_message_event` events to Home Assistant for custom event automations.
* **Dynamic Proxy Decoders (`decoder_1_entity`–`decoder_4_entity`)**: Map network audio decoders (Music Assistant, Squeezelite, WiiM, DLNA DMR) to physical F441/F441M matrix source inputs for Diffusione Sonora.

---

## 📚 Detailed Subsystem Guides

Explore dedicated guides for each MyHOME subsystem:

| Subsystem / Feature | OpenWebNet WHO | Documentation Guide |
| :--- | :---: | :--- |
| **Gateways & Hardware Identification** | `WHO = 13` | [Gateways & Connection Setup](gateways.md) • [Gateway Identification](gateway-identification.md) |
| **Lighting & Dimmers** | `WHO = 1` | [Lights & Dimmers Guide](lights.md) |
| **Motorized Covers & Shutters** | `WHO = 2` | [Covers & Shutters Guide](covers.md) |
| **Heating & Climate Control** | `WHO = 4` | [Climate & Heating Guide](climate.md) |
| **Diffusione Sonora (Sound System)** | `WHO = 16`, `WHO = 22` | [Sound System / Media Player Guide](media_player.md) |
| **Scenario Pushbuttons & Rotary Dials** | `WHO = 15`, `WHO = 25` | [CEN & CEN+ Device Triggers Guide](cen_cenplus.md) |
| **Switches, Relays & Sockets** | `WHO = 1` | [Switches & Relays Guide](switches.md) |
| **Electrical Energy & Power Meters** | `WHO = 18` | [Sensors & Energy Guide](sensors.md) |
| **Dry Contacts & Motion Detectors** | `WHO = 25`, `WHO = 1` | [Binary Sensors & Contacts Guide](binary-sensors.md) |
| **Burglar Alarm Central Units** | `WHO = 5` | [Burglar Alarm Guide](alarm.md) |
| **Expose HA Entities to SCS Bus** | `WHO = 1, 2, 4, 13, 18` | [Expose to Bus Guide](expose.md) |
| **Integration Service Actions** | All WHOs | [Services Action Reference](services.md) |
| **In-Band Bus Monitor** | All WHOs | [Lovelace Bus Monitor Card](bus_monitor.md) |
| **Troubleshooting & Known Limits** | All WHOs | [Troubleshooting Guide](troubleshooting.md) • [Known Limitations](known_limitations.md) |
