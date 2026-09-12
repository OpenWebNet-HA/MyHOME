# Migrating from SDomotica to Native MyHOME (OpenWebNet)

This guide provides a step-by-step, zero-touch migration path for users transitioning from the legacy **SDomotica** bridge to the native Home Assistant **MyHOME (OpenWebNet)** integration.

---

## 📌 Executive Summary

Many Legrand & BTicino MyHOME installations were previously integrated into Home Assistant via SDomotica (either through MQTT packages or the custom bridge add-on). In these setups, Home Assistant entity IDs are automatically generated based on each installation's SCS bus addresses and device types:
- **Lights & Relays**: `light.sdomoticabticino<where>` (or `light.sdomoticabticino2_<where>` for secondary gateways)
- **F422 Bus Interfaces**: `light.sdomoticabticino<where>_4_<interface>` (e.g. `where: 41`, `interface: 01`)
- **Covers & Shutters**: `cover.sdomoticabticino<where>` (e.g. `cover.sdomoticabticino31`)
- **Climate & Thermoregulation**: `climate.sdomoticabticino_4_<zone>` (e.g. `climate.sdomoticabticino_4_1`)
- **Audio Sound System**: `media_player.audio_zone_<zone>` (e.g. `media_player.audio_zone_2`)
- **Burglar Alarm**: `alarm_control_panel.sdomoticabtalarm` (or `sdomotica_alarm`)
- **Energy & Power Sensors**: `sensor.sdomoticabticino<where>` (e.g. F522/F523 power meters)
- **Auxiliary Contacts / Binary Sensors**: `binary_sensor.sdomoticabticino<where>` (e.g. 3477 dry contacts)

### Our Migration Guarantees
1. **Zero Dashboard Changes**: Your existing Lovelace cards, entity grids, auto-entities filters, and ApexCharts remain 100% operational with your exact entity IDs.
2. **Zero Automation Changes**: Automations, scenes, and scripts referencing your existing entities continue working without renaming.
3. **Preserved History & Areas**: Historical database statistics, energy records, and room/area assignments are preserved.

---

## 🚀 Migration Options

You can migrate using either:
1. **The Automated Migration CLI Tool (`scripts/migrate_from_sdomotica.py`)** *(Recommended)*
2. **Direct `myhome.yaml` Configuration**

---

### Option 1: The Automated Migration CLI Tool (Recommended)

The MyHOME repository includes a multi-source automated migration utility located at `scripts/migrate_from_sdomotica.py`.

#### Supported Ingestion Sources & Add-ons

The migration engine automatically scans and discovers all configurations across SDomotica add-ons:
1. **Home Assistant Entity Registry (`.storage/core.entity_registry`)**: Your live HA registry with custom names, icons, and room/area assignments. Automatically recognizes `sdomoticabticino*`, `sdomoticabticino2*` (secondary gateway), `sdomoticabtalarm*` (burglar alarm), and `platform: MyHomeAudio`.
2. **Sdomotica Gateway `config.json`** (`--sdomotica-json`): The Homebridge-standard config from the SDomotica Add-on WebUI containing device capabilities (`can_dim`, `WindowsAdvance`, `travel_time`, `SAThermoHC`, `Sensor3477inv`, gateway IP and credentials). Discovered automatically in `/config`, `/config/sdomotica/`, or `/share/sdomotica/`.
3. **Sdomotica Package YAMLs (`--sdomotica-yaml`)**: Discovers and loads all package files in `/config/packages/` (e.g. `sdomoticabticino.yaml`, `sdomoticabticino2.yaml`, and `sdomoticabtalarm.yaml`), parsing lights, covers, switches, climates, media players, power/energy sensors, binary contacts, and burglar alarms simultaneously.

#### Step 1: Preview Entities (Dry Run)
Inspect your installation and preview all discovered entities:
```bash
# Auto-discover from HA config directory:
python scripts/migrate_from_sdomotica.py --config-dir /config --dry-run

# Or directly preview from Sdomotica config.json:
python scripts/migrate_from_sdomotica.py --sdomotica-json config.json --dry-run
```
*Example output summary (discovering all devices configured in your installation):*
```
--- Discovered SDomotica Entities ---
  • binary_sensor       :   4 devices
  • climate             :   2 devices
  • cover               :  11 devices
  • light               :  62 devices
  • media_player        :   6 devices
  • sensor              :   3 devices
  • switch              :   3 devices
-------------------------------------
```

#### Step 2: Choose Your Migration Mode

##### Mode A: Generate `myhome.yaml` (Safe Export)
Outputs a clean, schema-compliant `myhome.yaml` pre-keyed to your exact entity IDs:
```bash
python scripts/migrate_from_sdomotica.py --config-dir /config --generate-yaml /config/myhome.yaml --gateway-mac 00:03:50:20:00:01
```
Or directly from your Sdomotica `config.json` backup:
```bash
python scripts/migrate_from_sdomotica.py --sdomotica-json config.json --generate-yaml /config/myhome.yaml --gateway-mac 00:03:50:20:00:01
```

Sample generated `myhome.yaml`:
```yaml
myhome:
  mac: "00:03:50:20:00:01"

  # ── LIGHT (3 devices) ──────────────────────
  light:
    sdomoticabticino_12:
      where: "12"
      name: "Cucina"
    sdomoticabticino_19:
      where: "19"
      name: "Dimmer TV"
      dimmable: true
    sdomoticabticino_41_4_01:
      where: "41"
      interface: "01"
      name: "Palla Balcone"

  # ── COVER (2 devices) ──────────────────────
  cover:
    sdomoticabticino_31:
      where: "31"
      name: "Veranda"
      advanced_shutter: false
      travel_time: 20
    sdomoticabticino_55:
      where: "55"
      name: "Veranda Avanzata"
      advanced_shutter: true

  # ── CLIMATE (2 devices) ──────────────────────
  climate:
    sdomoticabticino_4_1:
      zone: "1"
      heat: true
      cool: false
      name: "Soggiorno"
    sdomoticabticino_4_2:
      zone: "2"
      heat: true
      cool: true
      name: "Camera Singola"

  # ── BINARY_SENSOR (2 devices) ──────────────────────
  binary_sensor:
    sdomoticabticino_19:
      where: "19"
      name: "Sensore Invertito"
      inverted: true
```

##### Mode B: In-Place Entity Registry Migration (Zero-Touch)
To seamlessly adopt your entities directly inside Home Assistant's entity registry without changing any YAML files:
1. **Stop Home Assistant**:
   ```bash
   ha core stop
   ```
2. **Execute In-Place Registry Migration**:
   ```bash
   python scripts/migrate_from_sdomotica.py --config-dir /config --migrate-registry --gateway-mac 00:03:50:20:00:01
   ```
   *Note: A timestamped backup (`core.entity_registry.backup_sdomotica_<timestamp>`) is automatically created before any modification.*
3. **Start Home Assistant**:
   ```bash
   ha core start
   ```

When Home Assistant restarts, the native MyHOME integration binds directly to existing entity registry records. **Zero duplicate entities, no `_2` suffixes, and 100% preservation of Lovelace cards and automations.**

---

### Option 2: Complete SDomotica Device Mapping Catalog

Based on the official **Sdomotica Gateway Manual** (pages 18–23):

| SDomotica Accessory Type | Description | MyHOME Platform | OpenWebNet WHO | Key Parameters |
| :--- | :--- | :--- | :---: | :--- |
| `Lightbulb` (`can_dim: false`) | Standard on/off light | `light` | 1 | `where: "<addr>"` |
| `Lightbulb` (`can_dim: true`) | Dimmable actuator | `light` | 1 | `where: "<addr>"`, `dimmable: true` |
| `Lightbulb` (`"41#4#01"`) | Light via F422 interface | `light` | 1 | `where: "41"`, `interface: "01"` |
| `Outlets` | Controlled power outlet | `switch` | 1 | `where: "<addr>"`, `device_class: "outlet"` |
| `Switch` | SCS switch relay | `switch` | 1 | `where: "<addr>"`, `device_class: "switch"` |
| `Windows` | Standard shutter/blind | `cover` | 2 | `where: "<addr>"`, `advanced_shutter: false` |
| `WindowsAdvance` | Position-aware shutter (%) | `cover` | 2 | `where: "<addr>"`, `advanced_shutter: true` |
| `Sensor` | Actuator feedback sensor | `binary_sensor` | 1 / 25 | `where: "<addr>"` |
| `Sensor3477` | 3477 Aux contact | `binary_sensor` | 25 | `where: "<addr>"` |
| `Sensor3477inv` | Inverted 3477 Aux contact | `binary_sensor` | 25 | `where: "<addr>"`, `inverted: true` |
| `Energy` | Central energy meter | `sensor` | 18 | `where: "<addr>"`, `class: "energy"` |
| `F522`, `F523` | Controlled load actuator | `sensor` | 18 | `where: "<addr>"`, `class: "power"` |
| `Thermostat` | 99-zone central heating | `climate` | 4 | `zone: "<addr>"`, `heat: true`, `cool: false` |
| `4ZThermo` | 4-zone heating & cooling | `climate` | 4 | `zone: "<addr>"`, `heat: true`, `cool: true` |
| `SAThermoHC` | Standalone Heating & Cooling | `climate` | 4 | `zone: "<addr>"`, `heat: true`, `cool: true` |
| `SAThermoC` | Standalone Cooling only | `climate` | 4 | `zone: "<addr>"`, `heat: false`, `cool: true` |
| `SAThermoH` | Standalone Heating only | `climate` | 4 | `zone: "<addr>"`, `heat: true`, `cool: false` |
| `TemperatureSensors` | External temperature probe | `sensor` | 4 | `where: "<addr>"`, `class: "temperature"` |
| `Audio` | Multi-channel sound amplifier | `media_player` | 16 | `zone: "<zone>"` |
| `SecuritySystem` | Burglar alarm central (3486) | `alarm_control_panel` | 5 | `where: "<zone>"` |
| `Door` | Garage / gate door opener | `switch` | 1 / 6 | `where: "<addr>"` |

---

## 🛡️ Post-Migration Verification Checklist

After restarting Home Assistant with MyHOME:
- [ ] Open **Developer Tools ➔ States** and search for `sdomoticabticino`. Verify all entities report their correct state (`on`, `off`, `open`, `closed`).
- [ ] Check your Lovelace dashboards (e.g. Active Lights, Covers grid). Ensure no cards show yellow *"Entity not found"* warnings.
- [ ] Test toggling a light and operating a cover to confirm bidirectional bus feedback.
- [ ] Check the **MyHOME OpenWebNet Bus Monitor** card (`custom:myhome-openwebnet-bus-monitor`) to verify live frame traffic.

---

## ⚖️ Legal & Trademark Notice

- **Trademarks**: SDomotica® and BTicino® / Legrand® are trademarks or registered trademarks of their respective owners. The Home Assistant MyHOME integration and this migration tool are independent open-source projects licensed under AGPL-3.0 and are not affiliated with, maintained by, or endorsed by SDomotica or BTicino/Legrand.
- **Interoperability & Fair Use**: This migration script operates strictly as a data conversion and configuration translation utility for a user's own Home Assistant configuration files (`core.entity_registry` and YAML packages), in compliance with European Union Directive 2009/24/EC Article 6 (Software Interoperability), GDPR Article 20 (Right to Data Portability), and US Copyright Act Fair Use / 17 U.S.C. § 1201(f).
- **Clean-Room Compliance**: This utility contains no proprietary software, code, binaries, or reverse-engineered binaries from SDomotica. It strictly parses public Home Assistant entity IDs and standard Home Assistant YAML schemas created by end-users.

