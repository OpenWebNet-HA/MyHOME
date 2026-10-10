# Alarm System Configuration (v0.9.x YAML)

> [!WARNING]
> **Legacy Configuration**: This page describes manual YAML configuration in `/config/myhome.yaml` used by MyHOME v0.9.x.
> In MyHOME v2.0+, alarm entities are configured directly via the Home Assistant user interface or discovered automatically.

Burglar Alarm entities are developed for OpenWebNet **WHO = 5** (`alarm_control_panel`).

---

## Supported Hardware

- **BTicino 3485 / 3486**: Central intrusion alarm units
- **BTicino HC4600 / L4600**: Keypads and transponder readers
- **BTicino 3481**: Zone expansion interfaces
- **Technical Alarms**: Gas / water leak sensors transmitting on WHO 5

---

## Configuration Structure

In your `/config/myhome.yaml`:

```yaml
f454:
  mac: '00:03:50:xx:xx:xx'
  alarm_control_panel:
    central_alarm:
      where: '0'
      name: Central Alarm
      manufacturer: BTicino
      model: 3486
    zone_1:
      where: '1'
      name: Ground Floor Alarm
      manufacturer: BTicino
      model: 3485
```

### Parameters
- `where` *(mandatory)*: OpenWebNet address of the partition or central unit:
  - `'0'`: Central unit / global system broadcast
  - `'1'` through `'8'`: Specific partition or zone
- `name` *(mandatory)*: Friendly name for the entity in Home Assistant.
- `manufacturer` *(optional)*: Hardware manufacturer (e.g. `BTicino`).
- `model` *(optional)*: Hardware model (e.g. `3486`, `3485`).

---

## Supported States and Features

- **States**:
  - `disarmed`: System deactivated / maintenance
  - `armed_home`: Partial / perimeter armed
  - `armed_away`: Total system armed
  - `triggered`: Intrusion alarm, technical alarm, or panic event
- **Commands**:
  - `ARM_AWAY` (`*5*1*<where>##`)
  - `ARM_HOME` (`*5*1*<where>##`)
  - `DISARM` (`*5*2*<where>##`)
  - `TRIGGER` (`*5*17*<where>##` panic alarm)
