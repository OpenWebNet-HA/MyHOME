# Climate Configuration (v0.9.x YAML)

> [!WARNING]
> **Legacy Configuration**: This page describes manual YAML configuration in `/config/myhome.yaml` used by MyHOME v0.9.x.
> In MyHOME v2.0+, thermoregulation zones are discovered directly from the bus.

Climate entities are developed for OpenWebNet **WHO = 4** (Thermoregulation).

Depending on whether your physical installation uses a 99-zone central unit, a 4-zone central unit, or standalone zone probes, choose the matching structure below:

---

## 1. 99-Zone Central Unit (`3550`)

In a 99-zone setup, the physical central unit has the special address `#0`, and all subordinate zones have their own zone number with `standalone: false`:

- `zone`: The zone address (`'#0'` for the central unit, `'1'`..`'99'` for zones).
- `heat`: Optional boolean (default `true`), set to `true` if the zone supports heating.
- `cool`: Optional boolean (default `false`), set to `true` if the zone supports cooling.
- `standalone`: Must be set to `false` in a 99-zone architecture.

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  climate:
    central_unit:
      zone: '#0'
      name: Central Unit
      heat: true
      cool: false
      standalone: false
      manufacturer: BTicino
      model: 3550
    zone_1:
      zone: '1'
      name: Living room
      heat: true
      cool: false
      standalone: false
      manufacturer: BTicino
      model: F430/4
```

---

## 2. 4-Zone Central Unit (`HC4695`, `L4695`, `LN4691`)

In a 4-zone setup, the master unit acts simultaneously as the central coordinator and as zone 1 (`central: true`):

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  climate:
    central_unit:
      zone: '1'
      name: Central Unit Living Room
      heat: true
      cool: false
      central: true
      standalone: false
      manufacturer: BTicino
      model: HC4695
    zone_2:
      zone: '2'
      name: Bedroom
      heat: true
      cool: false
      standalone: true
      manufacturer: BTicino
      model: F430/4
```

---

## 3. Standalone Probes (No Central Unit)

If you have independent chronothermostats or probes (`H4691`, `LN4691`) operating autonomously without a central unit, configure each as `standalone: true`:

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  climate:
    zone_1:
      zone: '1'
      name: Living room
      heat: true
      cool: false
      standalone: true
      manufacturer: BTicino
      model: H4691
    zone_2:
      zone: '2'
      name: Bedroom
      heat: true
      cool: false
      standalone: true
      manufacturer: BTicino
      model: H4691
```
