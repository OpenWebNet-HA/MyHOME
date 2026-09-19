# MyHOME Integration (v0.9.4 Legacy)

> [!WARNING]
> **Legacy Documentation (v0.9.4)**: You are viewing documentation for the older **YAML-based configuration (`/config/myhome.yaml`)**.
> If you are using MyHOME v2 or newer (UI-first setup with auto-discovery and config flow), please switch to the [**v2 (beta) documentation**](../beta/) using the version switcher in the header.
> To upgrade your existing v0.9.4 plant to v2, see the [**Upgrade to v2 Guide**](../beta/migration/upgrade-from-094/).

Welcome to the legacy documentation for the **MyHOME for Home Assistant** integration (v0.9.4).

This integration connects your BTicino / Legrand MyHOME bus and OpenWebNet gateway directly to Home Assistant.

---

## 🏛️ Supported Gateways

The integration communicates with the SCS bus using an OpenWebNet IP or serial gateway:

- **IP Gateways**:
  - **F454**: Standard IP gateway / web server (recommended)
  - **MH202 / MH200 / MH201**: Scenario programmers and IP gateways
  - **MyHomeServer1**: Modern IP gateway
  - **F455**: IP gateway with video door entry support
  - **F452 / F453**: Older web server gateways
- **USB / Serial Gateways**:
  - **Legrand 3578 USB**: USB-to-SCS gateway
  - **F461**: Serial-to-SCS gateway

---

## ⚙️ Configuration Overview (v0.9.x)

In v0.9.x, all device configurations are defined in a dedicated YAML file located at:

```text
/config/myhome.yaml
```

This file is placed in the same directory as your Home Assistant `configuration.yaml`.

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  light:
    garage:
      where: '01'
      name: Garage
      dimmable: false
  cover:
    living_room:
      where: '12'
      name: Living Room Shutter
      stop_supported: true
```

---

## 📚 Documentation Sections

### 🚀 Getting Started
- **[Installation Guide](getting-started/installation.md)**: Installing v0.9.4 production via HACS or manual ZIP.
- **[Upgrade from v0.9.4 to v2.0 (Beta)](../beta/migration/upgrade-from-094/)**: Ready to migrate to the next-generation v2 architecture? Follow our comprehensive upgrade guide.

### ⚙️ YAML Configuration Guides
- **[Configuration Overview](configuration/index.md)**: Top-level gateway hierarchy, MAC address requirement, common device keys (`who`, `where`, `interface`, `name`).
- **[Lights](configuration/lights.md)**: On/off and dimmable lights, F418/F418U2 dimmers, temporized bus timers, and SCS/DALI lighting groups.
- **[Covers & Shutters](configuration/covers.md)**: Rollers, shutters, Venetian blinds, `stop_supported`, and F422 interface addressing.
- **[Switches & Relays](configuration/switches.md)**: General relays for appliances and plugs (`WHO = 1` or `WHO = 2`).
- **[Climate & Thermoregulation](configuration/climate.md)**: Heating, cooling, 4-zone systems, 99-zone central units, probes, and setpoints.
- **[Sensors](configuration/sensors.md)**: Power, energy, and temperature sensors (`WHO = 18`).
- **[Binary Sensors](configuration/binary-sensors.md)**: Dry contacts, presence detectors, and auxiliary inputs (`WHO = 25`).
- **[Alarm System](configuration/alarm.md)**: BTicino burglar alarm control panel integration (`WHO = 5`).
- **[Complete Sample Config](configuration/sample-config.md)**: Full reference `/config/myhome.yaml` file.

### 🏛️ Gateways
- **[Gateway Identification](gateways/identification.md)**: How to discover your gateway IP, find the MAC address, and check firmware compatibility.
- **[Gateway Timezone Configuration](gateways/timezone.md)**: Setting gateway timezone and DST synchronization.

### 💡 Advanced Usage
- **[Automations & Bus Events](advanced/advanced-uses.md)**: Intercepting raw bus frames, handling physical switch events (`myhome_group_light_event`), and sending custom OpenWebNet frames via `myhome.send_message`.

### 📜 Protocol Reference
- **[OpenWebNet & WHO Specifications](protocol/who-specifications.md)**: Official BTicino WHO codes, frame structures, and dimension definitions.

### 🔄 Migration History
- **[Pre-v0.9 Legacy Configuration](migration/legacy-pre-v09.md)**: Historical configuration formats prior to v0.9.
