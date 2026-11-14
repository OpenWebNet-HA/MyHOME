# Lights Configuration (v0.9.x YAML)

> [!WARNING]
> **Legacy Configuration**: This page describes manual YAML configuration in `/config/myhome.yaml` used by MyHOME v0.9.x.
> In MyHOME v2.0+, lights are automatically discovered from the bus via Config Flow.

For lights, the only variation compared to the generic device elements is the `dimmable` option.  
`dimmable` is an optional boolean defaulting to `false` that you need to set to `true` if your device supports dimming (e.g. `F418` and `F418U2` DIN modules).

---

## Configuration Example

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
      interface: '02'
      name: Main bedroom
      dimmable: true
      manufacturer: BTicino
      model: F418
```

---

## 🎨 Colour Capabilities Learned from the Bus

Discovered lights start as on/off and gain capabilities from the frames they report: brightness (dimension `1`), HSV colour (dimension `12`, → `hs`) and colour temperature (dimension `14`, → `color_temp`). Capabilities are **added, never removed**, so a DALI DT8 driver that reports both `12` and `14` ends up with `supported_color_modes: [hs, color_temp]` and its active `color_mode` follows the last frame.

---

## ⏱️ Native Bus Timers (Temporized Lights)

MyHOME light actuators can run hardware timers directly on the SCS bus. Instead of keeping software delay timers active in Home Assistant (which risk leaving lights ON if Home Assistant restarts or reconnects), you can send native timed turn-on commands (`WHAT = 11..18` or `Dimension = 2`).

See the complete guide and frame syntax in [Advanced Uses: Native Bus Timers](../advanced/advanced-uses.md#native-bus-timers-temporized-lights).

---

## 💡 Lighting Groups (SCS & DALI Groups)

In MyHOME systems, lighting actuators can be grouped physically via configurators or MyHOME Suite into **SCS Groups** (`WHERE = #1` through `#255`), which is also the standard mechanism used to group fixtures on DALI gateway interfaces such as the **F429G**.

### Recommended Approach: Home Assistant Native Light Groups
For Home Assistant installations, **managing lighting groups software-side using Home Assistant's native Light Group helper (`light.group`) is the officially recommended approach**:

1. **No Protocol Discovery**: The OpenWebNet protocol provides no mechanism to query the gateway for group memberships (there is no command to ask *"which lights belong to Group #1?"*).
2. **Preventing Desynchronization**: On many physical gateways and area configurations, individual actuators do not emit status updates after executing group commands on the bus. Exposing hardware groups directly would cause individual entity states in Home Assistant to drift out of sync. Home Assistant Light Groups avoid this by maintaining 100% accurate aggregate state tracking across all members.
3. **Cross-Technology Support**: Home Assistant Light Groups allow combining DALI fixtures, standard F411 relays, F418 dimmers, and third-party smart bulbs (Zigbee, Hue, etc.) into a unified group entity.

### How to Configure in Home Assistant
1. In Home Assistant, go to **Settings → Devices & Services → Helpers**.
2. Click **Create Helper → Group → Light Group**.
3. Name your group (e.g. *Living Room DALI Lights*) and select all member lights.

### Synchronizing Physical Wall Switches (SCS Group Buttons)
If you have physical BTicino wall switches configured to trigger an SCS group (e.g. `WHERE = #1`):
The integration automatically dispatches a bus event named `myhome_group_light_event` whenever group frames are intercepted on the SCS bus. You can keep your Home Assistant Light Group perfectly synchronized with a simple automation:

```yaml
alias: "Sync MyHOME Group 1 Wall Switch"
trigger:
  - platform: event
    event_type: myhome_group_light_event
    event_data:
      group: 1
action:
  - action: light.turn_{{ trigger.event.data.event }}
    target:
      entity_id: light.living_room_dali_lights
```

### Simultaneous Hardware Broadcasts (Eliminating Sequential Pacing)
If you have a large DALI group and want to broadcast a simultaneous color temperature or dimming level across all ballasts in a single on-wire frame (avoiding sequential command pacing), you can send an OpenWebNet frame directly using `myhome.send_message`:

```yaml
# Example: Broadcast 153 mireds (6500K) to SCS Group #1 via DALI interface
action: myhome.send_message
data:
  message: "*#1*#1*#14*153##"
```
