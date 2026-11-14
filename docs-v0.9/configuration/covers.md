# Covers Configuration (v0.9.x YAML)

> [!WARNING]
> **Legacy Configuration**: This page describes manual YAML configuration in `/config/myhome.yaml` used by MyHOME v0.9.x.
> In MyHOME v2.0+, covers are automatically discovered from the bus via Config Flow.

The configuration remains similar to lights and switches.  
The key option is `advanced` (optional boolean, defaulting to `false`): set it to `true` if you have advanced cover modules that track and report real position values (e.g. `Céliane 67557`, `Axolute H4661M2`, `Livinglight LN4661M2`, and the `F401` DIN module).

---

## Configuration Example

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  cover:
    living_shutter:
      where: '11'
      name: Living room shutter
      advanced: true
      manufacturer: Legrand
      model: 67557
    kitchen_shutter:
      where: '12'
      interface: '03'
      name: Kitchen shutter
      advanced: true
      manufacturer: Legrand
      model: 67557
    dining_room_shutter:
      where: '13'
      name: Dining room shutter
      advanced: false
      manufacturer: Legrand
      model: 67557
```

---

## ⏱️ Timed Covers & Travel Time

Standard cover actuators with `advanced: false` do not report physical percentage feedback over the bus. For standard actuators, you can configure `travel_time` (duration in seconds for a full run from completely open to completely closed, default `25`):

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  cover:
    bedroom_shutter:
      where: '21'
      name: Bedroom shutter
      travel_time: 18
```

### Runtime Behaviour Notes
- **Clock Anchoring**: The internal timer starts when the direction frame is acknowledged by the gateway, not when queued in Home Assistant.
- **Echo Handling**: The gateway relays intermediate status echoes when the motor begins motion (~0.55 s); these echoes re-anchor the travel timer without resetting the travel state.
- **Opposite Commands**: Pressing an opposite direction key on a physical wall switch halts motion immediately and updates the calculated position.
