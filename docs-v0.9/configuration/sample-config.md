# Complete Sample myhome.yaml (v0.9.x)

> [!WARNING]
> **Legacy Configuration**: This page describes manual YAML configuration in `/config/myhome.yaml` used by MyHOME v0.9.x.
> In MyHOME v2.0+, configuration is performed entirely in the Home Assistant UI.

Here is a complete, working example of `/config/myhome.yaml` demonstrating all supported device platforms:

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'

  light:
    garage:
      where: '01'
      name: Garage
      dimmable: false
      manufacturer: Arnould
      model: 64391
    dining_room:
      where: '17'
      name: Dining room
      dimmable: false
      manufacturer: BTicino
      model: F411U2
    main_bedroom_1:
      where: '23'
      name: Main bedroom
      dimmable: true
      manufacturer: BTicino
      model: F418

  switch:
    bed_heater:
      where: '0211'
      name: Mattress heating pad
      class: outlet
      manufacturer: BTicino
      model: F411U2
    door_bell:
      where: '0515'
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

  cover:
    living_shutter:
      where: '11'
      name: Living room shutter
      advanced: true
      manufacturer: Legrand
      model: 67557
    kitchen_shutter:
      where: '12'
      name: Kitchen shutter
      advanced: true
      manufacturer: Legrand
      model: 67557
    dining_room_shutter:
      where: '13'
      name: Dining room shutter
      advanced: true
      manufacturer: Legrand
      model: 67557

  alarm_control_panel:
    central_alarm:
      where: '0'
      name: Central Alarm
      manufacturer: BTicino
      model: 3486
    ground_floor_alarm:
      where: '1'
      name: Ground Floor Alarm
      manufacturer: BTicino
      model: 3485

  binary_sensor:
    garage_door:
      where: '31'
      name: Garage door
      class: garage_door
      manufacturer: BTicino
      model: 3477
    office_motion:
      who: '1'
      where: '0312'
      name: Office
      class: motion
      manufacturer: Legrand
      model: 48822

  sensor:
    general_power:
      where: '51'
      name: Total power
      class: power
      manufacturer: BTicino
      model: F520
    office_illuminance:
      where: '0312'
      name: Office
      class: illuminance
      manufacturer: Legrand
      model: 48822
```
