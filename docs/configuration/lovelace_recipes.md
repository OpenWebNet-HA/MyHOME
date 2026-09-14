# 🎨 Lovelace Dashboard Recipes & Showcase

A curated collection of production-tested Home Assistant dashboard recipes, dynamic cards, and templates designed specifically for Legrand & BTicino **MyHOME (OpenWebNet)** installations.

Because MyHOME bus installations typically encompass dozens of lighting actuators, shutter interfaces, heating zones, and multiroom audio zones, maintaining clean, mobile-friendly dashboards is critical. These recipes showcase modern UI patterns like auto-collapsing active groups, auto-entities filtering, and live diagnostic monitoring.

---

## 💡 Recipe 1: Dynamic Auto-Collapsing Active Lights Card

When you have 30 to 80+ light actuators across your home, showing a static list of all lights clutters your screen. This card automatically stays hidden when all lights are off, and dynamically expands to show **only the lights currently turned ON**.

```yaml
type: conditional
conditions:
  - condition: state
    entity: light.all_lights   # Or any group representing all lights
    state_not: 'off'
card:
  type: custom:auto-entities
  card:
    type: entities
    title: 💡 Active Lights
    show_header_toggle: true
    state_color: true
  filter:
    include:
      # Automatically captures ANY light created by the MyHOME integration that is ON
      - integration: myhome
        domain: light
        state: 'on'
    exclude:
      - entity_id: light.all_lights
      - entity_id: light.group_*
  show_empty: false
```

> [!TIP]
> **Why `integration: myhome` is best practice:**  
> Using `integration: myhome` makes the card completely independent of entity naming. Whether your lights are named `light.sdomoticabticino2`, `light.keuken_spots`, or renamed in the UI, this card never breaks!

---

## 🔊 Recipe 2: Dynamic Multiroom Audio Zone Player

For installations equipped with BTicino WHO 16 sound systems (F441, F500 audio matrices, 3484 amplifier nodes). This card dynamically displays only the audio zones that are currently **playing or active**:

```yaml
type: custom:auto-entities
card:
  type: entities
  title: 🔊 Active Speakers & Audio Zones
  show_header_toggle: false
  state_color: true
filter:
  include:
    - integration: myhome
      domain: media_player
      state: playing
    - integration: myhome
      domain: media_player
      state: 'on'
show_empty: false
```

### Dedicated In-Room Media Controller
Pair the active list above with dedicated zone controllers for high-traffic rooms (e.g. Kitchen, Living room):
```yaml
type: media-control
entity: media_player.audio_zone_22
```

---

## 🚪 Recipe 3: Active Perimeter & Safety Status Center

Consolidate all door/window magnetic contacts (F428 / 3477 interfaces) and motion sensors into an auto-collapsing status center:

```yaml
type: vertical-stack
cards:
  # Green "All Secure" indicator shown when everything is closed
  - type: conditional
    conditions:
      - condition: state
        entity: binary_sensor.deur_en_raam_contacten_group
        state: 'off'
      - condition: state
        entity: binary_sensor.pir_sensor_group
        state: 'off'
    card:
      type: markdown
      title: Security Status
      content: "🟢 **All Perimeter Contacts Closed & Secure**"

  # Dynamic alert card expanding when any window or door is opened
  - type: conditional
    conditions:
      - condition: state
        entity: binary_sensor.deur_en_raam_contacten_group
        state_not: 'off'
    card:
      type: custom:auto-entities
      card:
        type: entities
        title: 🚪 Open Doors & Windows
        show_header_toggle: false
      filter:
        include:
          - domain: binary_sensor
            attributes:
              device_class: door
            state: 'on'
          - domain: binary_sensor
            attributes:
              device_class: window
            state: 'on'
        exclude:
          - entity_id: binary_sensor.*_group

  # Dynamic alert card expanding when motion is detected
  - type: conditional
    conditions:
      - condition: state
        entity: binary_sensor.pir_sensor_group
        state_not: 'off'
    card:
      type: custom:auto-entities
      card:
        type: entities
        title: 🔔 Active Motion Sensors
        show_header_toggle: false
      filter:
        include:
          - domain: binary_sensor
            attributes:
              device_class: motion
            state: 'on'
        exclude:
          - entity_id: binary_sensor.pir_sensor_group
```

---

## 🔍 Recipe 4: Live OpenWebNet Diagnostic Bus Monitor

Every MyHOME installation includes the native, in-band **OpenWebNet Bus Monitor** card. It connects directly to the gateway streaming engine without external dependencies:

```yaml
type: custom:myhome-openwebnet-bus-monitor
title: MyHOME SCS Bus Monitor
max_frames: 200
```

### Features
- Real-time frame inspection (timestamp, direction, raw syntax, human-readable translation).
- Subsystem filtering (Lighting `*1*`, Automation `*2*`, Heating `*4*`, CEN/CEN+ `*15*`/`*25*`, Sound `*16*`).
- Built-in diagnostic frame injector: directly transmit test frames (e.g. `*#13**0##` or `*1*1*12##`) with instant ACK feedback.

---

## 📈 Recipe 5: Equipment Runtime & History Tracker (ApexCharts)

For auxiliary bus switches controlling hot water recirculation pumps, pond pumps, or garden irrigation relays:

```yaml
type: vertical-stack
cards:
  - type: entities
    title: Warmwater Recirculation Pump
    entities:
      - entity: switch.sdomoticabticino62
        name: Recirculation Pump
        icon: mdi:pump
      - entity: sensor.pomp_automatiseringstoestand
        name: Automation Mode

  - type: custom:apexcharts-card
    header:
      title: Pump Duty Cycle (24 Hours)
      show: true
    graph_span: 24h
    span:
      start: hour
    series:
      - entity: switch.sdomoticabticino62
        name: Pump Active
        type: area
        color: '#FF7F00'
        group_by:
          func: max
          duration: 5min
        transform: "return x === 'on' ? 1 : 0;"
```

---

## 🤝 Join In & Share Your Creations!

Every MyHOME installation is unique! Do you have a custom card layout, Mushroom card setup, floorplan SVG, or automation dashboard that you are proud of?

**We invite all community members to share their setups:**
- 💬 **GitHub Discussions**: Post your screenshot and YAML in the [Discussions Forum](https://github.com/OpenWebNet-HA/MyHOME/discussions)!
- 📝 **Contribute a Recipe**: Open a Pull Request adding your recipe to this page in `docs/configuration/lovelace_recipes.md`.
- 🏷️ **Tag Your Setup**: Share what gateway (F454, MH200N, MyHOMEServer1, USB/Serial 3578) and actuator models you are using.

Let's build the best collection of home automation dashboards together!
