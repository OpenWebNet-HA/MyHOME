# Sensors Configuration (v0.9.x YAML)

> [!WARNING]
> **Legacy Configuration**: This page describes manual YAML configuration in `/config/myhome.yaml` used by MyHOME v0.9.x.
> In MyHOME v2.0+, sensors are configured directly via the Home Assistant user interface.

Three types of sensors are available in the MyHOME integration:

---

## 1. Power and Energy Sensors (`WHO = 18`)

- `who`: Optional, defaults to `18` for power/energy meters.
- `where`:
  - For `F520` meters: `'5'` followed by the sensor number (`'51'`, `'52'`, etc.).
  - For `F522` meters: `'7'` followed by the sensor number (`'71'`, `'72'`, etc.).
- `class`: Required, either `power` or `energy`.
  - Setting `power`: Creates a live power entity (in Watts) plus 3 energy counter entities (total kWh, daily kWh, and monthly kWh).
  - Setting `energy`: Creates energy counter entities without the live Watt display.

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  sensor:
    general_power:
      where: '51'
      name: Total power
      class: power
      manufacturer: BTicino
      model: F520
    water_heater_power:
      where: '52'
      name: Water heater
      class: power
      manufacturer: BTicino
      model: F520
    washing_machine:
      where: '71'
      name: Washing machine
      class: power
      manufacturer: BTicino
      model: F522
```

---

## 2. Temperature Sensors (`WHO = 4`)

- `who`: Optional, defaults to `4`.
- `where`: Zone number for main zone sensors (e.g. `'1'`, `'2'`), or 3 digits for secondary temperature probes (e.g. `'105'` for the 1st secondary sensor in Zone 5).
- `class`: Must be set to `temperature`.

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  sensor:
    bedroom_temperature:
      where: '1'
      name: Bedroom temperature
      class: temperature
      manufacturer: BTicino
      model: L4692
    secondary_probe:
      where: '105'
      name: Secondary Sensor Zone 5
      class: temperature
      manufacturer: BTicino
      model: 3455
```

---

## 3. Illuminance Sensors (`WHO = 1`)

- `who`: Optional, defaults to `1`.
- `class`: Must be set to `illuminance`.
- `where`: APL address of the sensor (must be configured in "scenario" mode).

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  sensor:
    office_illuminance:
      where: '0312'
      name: Office Illuminance
      class: illuminance
      manufacturer: Legrand
      model: 048822
```
