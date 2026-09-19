# Lights & Dimmers (WHO = 1)

The **MyHOME** integration provides full control for on/off actuators, dimmers, and tunable white / RGB fixtures operating on OpenWebNet **WHO = 1**.

In v2, setup and management are **100% UI-first**: lighting fixtures are automatically discovered over the SCS bus, and capabilities such as dimming, colour temperature, and colour modes are learned dynamically from bus telemetry.

---

## 🚀 Auto-Discovery

When your gateway connects to Home Assistant:

1. **Dynamic Bus Discovery**: The integration listens to OpenWebNet `WHO = 1` frames and scans active addresses.
2. **Device & Entity Creation**: Each physical lighting actuator (`WHERE = 1..99` or area/point addresses `WHERE = 01..9999`) is registered as a Home Assistant `light` entity linked to the gateway device.
3. **UI Customization**: Rename lights, assign them to Areas (e.g. *Kitchen*, *Living Room*), or customize icons directly in the Home Assistant UI (**Settings → Devices & Services → Entities**).

---

## 🎨 Dynamic Capability Learning (v2 Engine)

Unlike legacy configurations that required manual flags (such as `dimmable: True`), v2 dynamically learns each fixture's capabilities directly from OpenWebNet bus traffic:

* **On / Off**: All discovered fixtures initially support standard switching (`turn_on`, `turn_off`).
* **Brightness / Dimming**: When the actuator reports a brightness frame (`Dimension = 1`), the integration unlocks the brightness slider in Home Assistant.
* **Colour Temperature (Tunable White)**: When a DALI or SCS gateway reports colour temperature telemetry (`Dimension = 14`), Home Assistant exposes mireds/Kelvin colour temperature control.
* **HSV / RGB Colour**: Telemetry reporting hue and saturation (`Dimension = 12`) dynamically enables the Home Assistant colour picker.
* **Additive Retention**: Capabilities are **added, never removed**. A DALI DT8 driver reporting both dimensions `12` and `14` ends up with `supported_color_modes: [hs, color_temp]`, and its active `color_mode` smoothly tracks incoming bus frames.

---

## ⚙️ Dimmer Transition Modes (UI Options)

You can customize how dimming transitions are executed globally via the integration's UI Options:

1. Navigate to **Settings → Devices & Services → MyHOME**.
2. Click **Configure**.
3. Select your preferred **Dimmer Transition Mode**:
   - **`software_stepped`** (*Default / Recommended*): Home Assistant manages incremental step fading, providing smooth transitions across all actuators.
   - **`native`**: Lets the physical hardware dimmer module perform its internal hardware fade ramp.

---

## ⏱️ Native Bus Timers (Temporized Lights)

MyHOME light actuators feature built-in hardware timers on the SCS bus. Running timers directly in hardware guarantees lights turn off even if Home Assistant restarts, reloads, or experiences network interruptions.

You can trigger native hardware timers using the `myhome.turn_on_timed` service in automations or Developer Tools:

```yaml
action: myhome.turn_on_timed
target:
  entity_id: light.hallway_light
data:
  time: 120   # Turn on and automatically turn off after 2 minutes
```

---

## 💡 Lighting Groups (SCS & DALI Groups)

In MyHOME systems, lighting actuators can be grouped physically via configurators or MyHOME Suite into **SCS Groups** (`WHERE = #1` through `#255`), which is also the standard mechanism used to group fixtures on DALI gateway interfaces such as the **F429G**.

### Approach 1: Home Assistant Native Light Groups (Recommended)
For Home Assistant installations, **managing lighting groups software-side using Home Assistant's native Light Group helper (`light.group`) is the officially recommended approach**:

1. **No Protocol Discovery**: The OpenWebNet protocol provides no mechanism to query the gateway for group memberships (there is no command to ask *"which lights belong to Group #1?"*).
2. **Preventing Desynchronization**: On many physical gateways and area configurations, individual actuators do not emit status updates after executing group commands on the bus. Exposing hardware groups directly would cause individual entity states in Home Assistant to drift out of sync. Home Assistant Light Groups avoid this by maintaining 100% accurate aggregate state tracking across all members.
3. **Cross-Technology Support**: Home Assistant Light Groups allow combining DALI fixtures, standard F411 relays, F418 dimmers, and third-party smart bulbs (Zigbee, Hue, etc.) into a unified group entity.

#### How to Configure in Home Assistant
1. In Home Assistant, go to **Settings → Devices & Services → Helpers**.
2. Click **Create Helper → Group → Light Group**.
3. Name your group (e.g. *Living Room DALI Lights*) and select all discovered member lights.

---

### Approach 2: Physical SCS Hardware Groups (`where: '#G'`) via `myhome.yaml`
If your actuators are physically configured into an SCS group (e.g. `WHERE = #1` programmed via physical configurators or MyHOME_Suite) and you prefer Home Assistant to transmit single group frames directly on the bus, you can declare the group in `/config/myhome.yaml`:

```yaml
# /config/myhome.yaml
groups:
  living_room_group:
    where: '#1'
    name: Living Room Group
    members:
      - '11'
      - '12'
      - '13'
```

* **With `members:` list**: Home Assistant tracks individual member states and calculates an accurate aggregate state (on/off and brightness).
* **Without `members:` list**: The entity operates in `assumed_state: true`, rendering separate On and Off buttons.

---

### Synchronizing Physical Wall Switches (SCS Group Buttons)
If you have physical BTicino wall switches configured to trigger an SCS group (e.g. `WHERE = #1`), the integration automatically dispatches a bus event named `myhome_group_light_event` whenever group frames are intercepted on the SCS bus. You can keep your Home Assistant Light Group perfectly synchronized with a simple automation:

```yaml
alias: "Sync MyHOME Group 1 Wall Switch"
trigger:
  - platform: event
    event_type: myhome_group_light_event
    event_data:
      group: 1
action:
  - service: light.turn_{{ trigger.event.data.event }}
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

---

## 🔄 Legacy YAML Note

> [!NOTE]
> If you are upgrading from legacy v0.9 installations and still have manual `light:` blocks in `/config/myhome.yaml`, please refer to the [v0.9.4 Legacy Light Documentation](../../0.9.4/configuration/lights/) or the [Legacy YAML Migration Guide](../migration/legacy-yaml.md). In v2, all lights are managed dynamically via Home Assistant's native registry.
