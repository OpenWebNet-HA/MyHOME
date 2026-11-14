# Pre-v0.9 Legacy Configuration Format

> [!WARNING]
> **Historical Archive**: This document describes the deprecated configuration syntax used before version 0.9, where devices were declared under individual domain keys directly inside `configuration.yaml` (`platform: myhome`).
> For v0.9.x, devices must be declared in `/config/myhome.yaml` keyed by gateway MAC address.

Prior to v0.9.0, devices were configured inside the main Home Assistant `configuration.yaml` split across platform domains using `platform: myhome`:

---

## 1. Lights (`light`)

```yaml
light:
  - platform: myhome
    devices:
      garage:
        where: '01'
        name: Garage
        dimmable: false
        manufacturer: Arnould
        model: 64391
      main_bedroom:
        where: '23'
        name: Main bedroom
        dimmable: true
        manufacturer: BTicino
        model: F418
```

---

## 2. Switches (`switch`)

```yaml
switch:
  - platform: myhome
    devices:
      bed_heater:
        where: '0211'
        name: Mattress heating pad
        class: outlet
        manufacturer: BTicino
        model: F411U2
```

---

## 3. Covers (`cover`)

```yaml
cover:
  - platform: myhome
    devices:
      living_shutter:
        where: '11'
        name: Living room shutter
        advanced: true
        manufacturer: Legrand
        model: 67557
```

---

## 4. Binary Sensors (`binary_sensor`)

```yaml
binary_sensor:
  - platform: myhome
    devices:
      garage_door:
        where: '31'
        name: Garage door
        class: garage_door
        manufacturer: BTicino
        model: 3477
```

---

## Why This Changed in v0.9.0

1. **Multi-Gateway Support**: Placing devices under top-level domains in `configuration.yaml` made it impossible to specify which gateway controlled which device in multi-gateway installations.
2. **Centralized Configuration**: In v0.9.0, all devices moved into `/config/myhome.yaml` under the specific gateway's MAC address (`mac: '00:03:50:xx:xx:xx'`).
3. **v2.0 Transition**: In v2.0+, manual YAML files are completely superseded by UI-first Config Flow with automatic bus discovery.
