# Switches Configuration (v0.9.x YAML)

> [!WARNING]
> **Legacy Configuration**: This page describes manual YAML configuration in `/config/myhome.yaml` used by MyHOME v0.9.x.
> In MyHOME v2.0+, switches are configured directly via the Home Assistant user interface.

The configuration is largely the same as lights, except switches are non-dimmable binary relays, and you can specify `class` if you wish to distinguish between `outlet` and `switch`.

---

## Configuration Example

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  switch:
    bed_heater:
      where: '0211'
      name: Mattress heating pad
      class: outlet
      manufacturer: BTicino
      model: F411U2
    door_bell:
      where: '0515'
      interface: '02'
      name: Doorbell
      class: switch
      manufacturer: BTicino
      model: 3476
    hvac_relay_1:
      where: '08'
      name: HVAC relay 1
      class: switch
      manufacturer: Arnould
      model: 64391
```

---

## Parameters

- `where` *(mandatory)*: OpenWebNet bus address of the relay (2 or 4 digits).
- `name` *(mandatory)*: Friendly name for the switch entity in Home Assistant.
- `interface` *(optional)*: Bus interface ID when located behind an `F422` bus-to-bus interface.
- `class` *(optional)*: Device class for Home Assistant (`switch` or `outlet`).
- `manufacturer` *(optional)*: Cosmetic manufacturer name.
- `model` *(optional)*: Cosmetic model designation.
