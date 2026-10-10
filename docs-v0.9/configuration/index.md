# General Configuration (v0.9.x YAML)

> [!WARNING]
> **Legacy Configuration**: This page describes manual YAML configuration in `/config/myhome.yaml` used by MyHOME v0.9.x.
> If you are using MyHOME v2.0 or newer, configuration is performed directly via the Home Assistant user interface.

Once your gateway is added to Home Assistant, you can start defining your devices in the `/config/myhome.yaml` file. (This file must be placed in the same folder as your main Home Assistant `configuration.yaml`.)

---

## General Structure

The overall `/config/myhome.yaml` file is structured hierarchically by gateway, then platform, then individual device:

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  light:
    # Light devices
    [...]
  switch:
    # Switch devices
    [...]
  cover:
    # Cover / shutter devices
    [...]
  climate:
    # Thermoregulation zones
    [...]
  alarm_control_panel:
    # Alarm panel
    [...]
  binary_sensor:
    # Binary sensors
    [...]
  sensor:
    # Sensors (energy, power, temp)
    [...]

mh202:
  mac: '00:03:50:yy:yy:yy'
  light:
    [...]
```

### Top-Level Gateway Identifier
The topmost item in the hierarchy (`f454` and `mh202` in the above example) is a user-friendly identifier to help you organize multiple gateways. Its text value does not affect integration behavior.

### Gateway MAC Address
It is **mandatory** that you supply your gateway's correct MAC address in lowercase (`mac: '00:03:50:xx:xx:xx'`). This MAC address uniquely pairs the device list with the discovered gateway entry in Home Assistant.

---

## Device Structure

Under each platform, individual devices follow a common structure:

```yaml
    <configuration_identifier>:
      who: <str>
      where: <str>
      interface: <str>
      name: <str>
      manufacturer: <str>
      model: <str>
```

- `<configuration_identifier>`: A unique key for your device within that platform (e.g. `kitchen_light`, `living_shutter`). Used only for internal organization.
- `who` *(optional)*: Specifies the OpenWebNet subsystem. Usually omitted because each platform defaults to its standard WHO (`light` defaults to `1`, `cover` to `2`), but needed for platforms supporting multiple WHOs (such as `sensor` or `binary_sensor`).
- `where` *(mandatory)*: The OpenWebNet address of the device on the bus (the 'APL'). By OpenWebNet standard, it must be either 2 digits (e.g. `'01'`, `'12'`) or 4 digits (e.g. `'0102'`). It can **never** be 3 digits, as the bus protocol cannot distinguish between area and point.
- `interface` *(optional)*: The 2-digit BUS-BUS Interface ID when using an `F422` local bus interface (e.g. `interface: '02'`).
- `name` *(mandatory)*: Friendly name for the device in Home Assistant.
- `manufacturer` *(optional)*: Cosmetic device manufacturer displayed in Home Assistant device info (e.g. `BTicino`, `Legrand`, `Arnould`).
- `model` *(optional)*: Cosmetic device model number (e.g. `F411U2`, `F418`, `F422`).

---

## Platform Guides

Detailed configuration options and examples for each device type:

- **[Lights Configuration](lights.md)**: Dimmable lights, timers, DALI/SCS groups.
- **[Covers Configuration](covers.md)**: Shutters, blinds, stop command support.
- **[Switches Configuration](switches.md)**: Relays and appliances.
- **[Climate Configuration](climate.md)**: Thermoregulation zones, 4-zone and 99-zone systems.
- **[Sensors Configuration](sensors.md)**: Energy, power, and temperature sensors.
- **[Binary Sensors Configuration](binary-sensors.md)**: Dry contacts and motion inputs.
- **[Alarm Configuration](alarm.md)**: Burglar alarm control panel.
- **[Complete Sample Config](sample-config.md)**: A complete, working `/config/myhome.yaml` file.
