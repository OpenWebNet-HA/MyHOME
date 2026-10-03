# Burglar Alarm (WHO = 5)

The **MyHOME** integration monitors BTicino / Legrand intrusion detection systems on OpenWebNet **WHO = 5** (`alarm_control_panel`).

The panel is **read-only**: it shows the central unit's state, but it cannot arm or disarm the alarm. To arm and disarm from Home Assistant, see [Arming and disarming](#arming-and-disarming).

In v2, setup is **UI-first**: the central unit is discovered from the SCS bus without manual YAML configuration files.

---

## 🚀 Auto-Discovery

When your gateway connects to Home Assistant:

1. **Dynamic Bus Discovery**: The first central-unit frame on the SCS bus that names the panel (for example the `*5*9*0##` a disarmed panel sends when polled, or `*5*8*0##` when it is armed) registers the `alarm_control_panel` entity.
   * **Depends on the gateway.** An MH202 answers a poll with `*5*9*0##`, so the panel appears at once ([#564](https://github.com/OpenWebNet-HA/MyHOME/issues/564)). An F454 answers with system-level frames that have an empty WHERE (`*5*1*##`, `*5*5*##`, `*5*7*##`, `*5*9*##`, see [#311](https://github.com/OpenWebNet-HA/MyHOME/issues/311)), and MyHOME deliberately creates no entity from an empty WHERE. On such a gateway the panel appears at the first `*5*8*0##` or `*5*9*0##`, that is the first real arm or disarm after a blank start.
2. **Global Broadcast Zone 0 Listening**: Entities follow global broadcast zone 0 telemetry (`myhome_update_<mac>_5_0`) alongside their own address (`myhome_update_<mac>_5_<where>`), so every alarm panel stays in sync.
3. **UI Customization**: You can rename the alarm panel, assign it to an Area (e.g. *Entrance*, *Security*), and change its icon in the Home Assistant UI.

---

## 🛡️ Supported Hardware

The platform reads Legrand / BTicino SCS burglar alarm central units:

* **BTicino 3485 / 3486**: Multi-zone central alarm control units.
* **BTicino HC4600 / L4600**: Security control keypads and display terminals.
* **BTicino 3481**: Zone expansion and partition modules.
* **Technical Alarm Transmitters**: Flood/water leak detectors and gas safety sensors.

---

## 🔒 States

The panel follows what the central unit reports on the bus:

| State | OpenWebNet Frame | Description |
| :--- | :--- | :--- |
| **`disarmed`** | `*5*9*<where>##`, `*5*2*<where>##`, `*5*0*<where>##` | Disengaged, deactivated, or maintenance. |
| **`armed_away`** | `*5*8*<where>##` | Engaged. |
| **`triggered`** | `*5*12*`, `*5*15*`, `*5*16*`, `*5*17*`, `*5*31*` | Technical, intrusion, tampering, anti-panic or silent alarm. |

* `*5*1*<where>##` (*activation*) is **not** an armed state. The central unit sends it while disarming (`*5*2*0##` → `*5*1*0##` → `*5*9*0##`) and in the status of a disarmed system, as well as while arming (`*5*1*0##` → `*5*8*0##`).
* **`armed_home`** is never reported: home and away arming look the same on the bus.
* Zones (`*5*11*#n##` active, `*5*18*#n##` not active) never change the panel's state.

---

## 🔑 Arming and disarming

Current central-unit firmware rejects arm and disarm commands sent as WHO 5 frames over the SCS bus, whichever gateway sends them. A plant owner reported this, quoting BTicino support ([#564](https://github.com/OpenWebNet-HA/MyHOME/issues/564#issuecomment-5913248544)), and no capture shows such a command being accepted. The `alarm_control_panel` entity therefore offers no arm actions, and a disarm request is refused with an error.

The route that works is an **auxiliary (WHO 9) command**. Your installer programs the central unit so that receiving, for example, `*9*1*7##` (AUX channel 7 on) arms a set of zones. Which AUX channels and values arm or disarm depends on that programming: check the central unit's configuration or ask your installer.

Combine the MyHOME panel's state with those AUX frames in a core [template alarm control panel](https://www.home-assistant.io/integrations/template/#alarm-control-panel).

> [!WARNING]
> The template panel is a separate entity, so the MyHOME panel's refusal does not protect it. Without a code check, anyone who can reach Home Assistant can disarm the burglar alarm with one tap, including from the alarm card. The recipe below therefore asks for a code on disarm and stops unless it matches.

```yaml
template:
  - alarm_control_panel:
      - name: Home alarm
        unique_id: home_alarm
        # Unavailable until the MyHOME panel has reported. Falling back to
        # "disarmed" instead would show an armed alarm as disarmed after a restart.
        availability: "{{ states('alarm_control_panel.alarm_0') in ['disarmed', 'armed_away', 'triggered'] }}"
        state: "{{ states('alarm_control_panel.alarm_0') }}"
        code_format: number
        code_arm_required: false
        # Example AUX frames: use the ones your central unit is programmed for
        arm_away:
          - action: myhome.send_message
            data:
              gateway: "00:03:50:00:00:00"
              message: "*9*1*7##"
        disarm:
          # Stop here unless the right code was entered
          - condition: template
            value_template: "{{ code == '1234' }}"
          - action: myhome.send_message
            data:
              gateway: "00:03:50:00:00:00"
              message: "*9*0*7##"
```

To keep the code out of the YAML, for example if you share your configuration, move the **whole** template into `secrets.yaml`. `!secret` replaces a complete value, so it cannot be used inside the template string (`{{ code == !secret ... }}` does not work):

```yaml
# configuration.yaml
          - condition: template
            value_template: !secret alarm_disarm_check
```

```yaml
# secrets.yaml
alarm_disarm_check: "{{ code == '1234' }}"
```

Alternatively, compare `code` against a helper, such as an `input_text` in password mode.

The template panel's state changes once the central unit reports `*5*8*0##` (armed) or `*5*9*0##` (disarmed) on the bus, not when the AUX frame is sent.

---

## 📊 Dashboard Display (Lovelace Alarm Panel)

Use the template panel on the alarm card so the buttons work:

```yaml
type: alarm-panel
entity: alarm_control_panel.home_alarm
name: Home Security System
states:
  - arm_away
```

---

## 🧪 Interactive Diagnostics via Bus Monitor

You can query the alarm with the [Lovelace Bus Monitor Card](bus_monitor.md) Command Injector:

* **Query Central Status**: `*#5*0##`
* **Query Zone Status**: `*#5*#1##` (for zone 1)

---

## 🔄 Legacy YAML Note

> [!NOTE]
> If you are upgrading from legacy v0.9 installations and still have manual `alarm_control_panel:` blocks in `/config/myhome.yaml`, please refer to the [v0.9.4 Legacy Alarm Documentation](../../0.9.4/configuration/alarm/) or the [Legacy YAML Migration Guide](../migration/legacy-yaml.md). In v2, alarm panels are discovered dynamically.
