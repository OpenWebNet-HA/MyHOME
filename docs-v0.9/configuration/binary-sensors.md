# Binary Sensors Configuration (v0.9.x YAML)

> [!WARNING]
> **Legacy Configuration**: This page describes manual YAML configuration in `/config/myhome.yaml` used by MyHOME v0.9.x.
> In MyHOME v2.0+, binary sensors are configured directly via the Home Assistant user interface.

Three types of binary sensors are supported:

---

## 1. Dry Contacts (`WHO = 25`)

Dry contact interfaces (such as `3477`) monitor physical magnetic door/window reed contacts, technical alarms, and pushbuttons:

- `who`: Optional (defaults to `25`).
- `where`: Always `'3'` followed by the sensor number (`'31'`, `'32'`, etc.).
- `class`: Highly recommended; any valid Home Assistant `device_class` (e.g. `door`, `window`, `garage_door`, `moisture`, `smoke`).

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  binary_sensor:
    garage_door:
      where: '31'
      name: Garage door
      class: garage_door
      manufacturer: BTicino
      model: 3477
    front_door:
      where: '32'
      name: Front door
      class: door
      manufacturer: BTicino
      model: 3477
```

---

## 2. Motion Sensors (`WHO = 1`)

Light and motion sensors configured in "scenario" mode:

- `who`: Must be `'1'`.
- `where`: 4-digit APL address (e.g. `'0312'`).
- `class`: Must be `'motion'`.

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  binary_sensor:
    office_motion:
      who: '1'
      where: '0312'
      name: Office Motion
      class: motion
      manufacturer: Legrand
      model: 048822
```

---

## 3. Auxiliary Sensors from Burglar Alarm (`WHO = 9`)

Sensors connected to auxiliary inputs of the alarm system:

- `who`: Must be `'9'`.
- `where`: Auxiliary input number (`'0'` through `'9'`).
- `class`: Home Assistant `device_class` (e.g. `motion`, `smoke`, `gas`).

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  binary_sensor:
    living_room_pir:
      who: '9'
      where: '1'
      name: Motion Living Room
      class: motion
      manufacturer: BTicino
      model: L4610
```

---

## Common Supported Device Classes

- `door`: Open / closed
- `garage_door`: Open / closed
- `window`: Open / closed
- `motion`: Detected / clear
- `occupancy`: Detected / clear
- `smoke`: Detected / clear
- `gas`: Detected / clear
- `moisture`: Detected (wet) / clear (dry)
- `connectivity`: Connected / disconnected
- `power`: Power detected / no power
