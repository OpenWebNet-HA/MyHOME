# Burglar Alarm (WHO = 5)

The **MyHOME** integration provides full control and monitoring for BTicino / Legrand intrusion detection systems and partition devices operating on OpenWebNet **WHO = 5** (`alarm_control_panel`).

In v2, setup and management are **100% UI-first**: alarm central units and zone partitions are automatically discovered over the SCS bus without manual YAML configuration files.

---

## 🚀 Auto-Discovery

When your gateway connects to Home Assistant:

1. **Dynamic Bus Discovery**: Any incoming alarm event frame on the SCS bus (e.g. `*5*1*0##` for arming, `*5*2*0##` for disarming, or `*5*15*0##` for intrusion) automatically registers the `alarm_control_panel` entity.
2. **Global Broadcast Zone 0 Listening**: Entities automatically subscribe to global broadcast zone 0 telemetry (`myhome_update_<mac>_5_0`) alongside their specific zone/partition address (`myhome_update_<mac>_5_<where>`), guaranteeing synchronized state updates across all alarm panels in your home.
3. **UI Customization**: You can rename the alarm panel, assign it to an Area (e.g. *Entrance*, *Security*), and customize icons directly in the Home Assistant UI.

---

## 🛡️ Supported Hardware

The platform interfaces with Legrand / BTicino SCS burglar alarm central units:

* **BTicino 3485 / 3486**: Multi-zone central alarm control units.
* **BTicino HC4600 / L4600**: Security control keypads and display terminals.
* **BTicino 3481**: Zone expansion and partition modules.
* **Technical Alarm Transmitters**: Flood/water leak detectors and gas safety sensors.

---

## 🔒 Supported Features & States

The `alarm_control_panel` platform provides:

| State | OpenWebNet Frame | Description |
| :--- | :--- | :--- |
| **`disarmed`** | `*5*2*<where>##` | System deactivated, idle, or maintenance mode. |
| **`armed_home`** | `*5*1*<where>##` | Partial perimeter arming (e.g. night mode). |
| **`armed_away`** | `*5*1*<where>##` | Total plant armed; all zones active. |
| **`triggered`** | `*5*15*<where>##` / `*5*17*<where>##` | Active intrusion alarm, tampering, anti-panic, or technical emergency. |

---

## 📊 Dashboard Display (Lovelace Alarm Panel)

Add the native Home Assistant alarm card to your dashboard:

```yaml
type: alarm-panel
entity: alarm_control_panel.central_alarm
name: Home Security System
states:
  - arm_home
  - arm_away
```

---

## 🧪 Interactive Diagnostics via Bus Monitor

You can test alarm telemetry and commands using the [Lovelace Bus Monitor Card](bus_monitor.md) Command Injector:

* **Query Central Status**: `*#5*0##`
* **Query Zone Status**: `*#5*#1##` (for zone 1)
* **Arm System (Total)**: `*5*1*0##`
* **Disarm System**: `*5*2*0##`
* **Trigger Panic Alarm**: `*5*17*0##`
* **Simulate Intrusion**: `*5*15*0##`

---

## 🔄 Legacy YAML Note

> [!NOTE]
> If you are upgrading from legacy v0.9 installations and still have manual `alarm_control_panel:` blocks in `/config/myhome.yaml`, please refer to the [v0.9.4 Legacy Alarm Documentation](../../0.9.4/configuration/alarm/) or the [Legacy YAML Migration Guide](../migration/legacy-yaml.md). In v2, alarm panels are discovered dynamically.
