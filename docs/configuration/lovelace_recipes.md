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

For installations equipped with BTicino WHO 16 sound systems (F441, F441M audio matrices, F500 tuners, 3484 amplifier nodes). This showcase provides three dashboard patterns:

1. **Auto-Collapsing Active Speakers Card**: A dynamic overview panel that automatically stays hidden when music is off and expands into interactive player cards whenever any room starts playing.
2. **Dedicated Multi-Room Audio View (Sections Layout)**: A comprehensive whole-home audio control page organized by floor/zone with 1-tap matrix source selector chips (`Radio`, `Streamer`) and master power toggles.
3. **Stock Lovelace Alternative**: Built-in Home Assistant cards requiring zero custom components.

---

### Pattern A: Dynamic Auto-Collapsing Active Speakers Card

Add this card to your main overview dashboard. It uses `custom:auto-entities` to monitor all MyHOME audio zones and automatically renders a compact tile card per zone, named after the zone, with transport buttons and a live volume slider whenever a zone is `playing` or `on`:

```yaml
type: custom:auto-entities
card:
  type: vertical-stack
  title: 🔊 Active Speakers
card_param: cards
show_empty: false
filter:
  include:
    - integration: myhome
      domain: media_player
      state: '/^(playing|on)$/'
      options:
        type: tile
        icon: mdi:speaker
        state_content:
          - state
          - media_title
          - volume_level
        features_position: bottom
        features:
          - type: media-player-playback
            controls:
              - media_previous_track
              - media_play_pause
              - media_next_track
          - type: media-player-volume-slider
```

> [!TIP]
> **Why tile cards?** Each card is named after its zone (the entity's friendly name), so several playing zones are easy to tell apart. `use_media_info` on the Mushroom media player card replaces the zone name with the track title, so two zones playing the same source look identical. Tile cards and their features are built into Home Assistant, so only `custom:auto-entities` is required. To use your own room names, replace the single `include` entry with one entry per zone (`entity_id: media_player.<zone>`) and add `name:` to its `options`.

> [!TIP]
> **Why `show_empty: false` matters:**  
> When no music is playing throughout your home, this entire card collapses and takes up zero vertical screen space.

---

### Pattern B: Dedicated Multi-Room Audio Control View (Sections Layout)

For a dedicated whole-home music dashboard (e.g. `/lovelace/audio`), Home Assistant's modern **Sections** layout allows organizing zones by floor or area.

Each section includes:
* **Interactive Source Selector Chips**: Tap to route the BTicino matrix to physical sources (e.g. `Radio`, `Streamer`). The chip illuminates amber when that source is active.
* **All-Off Master Chip**: A one-tap red power button to turn off all amplifiers on that floor.
* **Full-Width Room Players**: Independent volume adjustments and transport controls for every amplifier.

```yaml
type: sections
title: Audio
path: audio
icon: mdi:speaker-multiple
max_columns: 3
sections:
  # ==========================================
  # Ground Floor Section
  # ==========================================
  - type: grid
    cards:
      - type: heading
        heading: Ground Floor
        icon: mdi:home-floor-0

      # Quick Source Selector & All-Off Action Bar
      - type: custom:mushroom-chips-card
        alignment: justify
        chips:
          # Matrix Source 1: Tuner / Radio
          - type: template
            entity: media_player.kitchen_sound
            icon: mdi:radio
            content: Radio
            icon_color: >-
              {{ 'amber' if state_attr(entity, 'source') == 'Radio' else 'disabled' }}
            tap_action:
              action: perform-action
              perform_action: media_player.select_source
              target:
                entity_id: media_player.kitchen_sound
              data:
                source: Radio

          # Matrix Source 2: Hi-Fi Streamer
          - type: template
            entity: media_player.kitchen_sound
            icon: mdi:cast-audio
            content: Streamer
            icon_color: >-
              {{ 'amber' if state_attr(entity, 'source') == 'Streamer' else 'disabled' }}
            tap_action:
              action: perform-action
              perform_action: media_player.select_source
              target:
                entity_id: media_player.kitchen_sound
              data:
                source: Streamer

          # Master Power Off for this floor
          - type: template
            icon: mdi:power
            icon_color: red
            tap_action:
              action: perform-action
              perform_action: media_player.turn_off
              target:
                entity_id:
                  - media_player.kitchen_sound
                  - media_player.dining_room_sound
                  - media_player.living_room_sound

      # Ground Floor Room Players
      - type: custom:mushroom-media-player-card
        entity: media_player.kitchen_sound
        name: Kitchen
        icon: mdi:speaker
        use_media_info: true
        show_volume_level: true
        media_controls:
          - on_off
          - previous
          - play_pause_stop
          - next
        volume_controls:
          - volume_mute
          - volume_set
          - volume_buttons
        grid_options:
          columns: full

      - type: custom:mushroom-media-player-card
        entity: media_player.dining_room_sound
        name: Dining Room
        icon: mdi:speaker
        use_media_info: true
        show_volume_level: true
        media_controls:
          - on_off
          - previous
          - play_pause_stop
          - next
        volume_controls:
          - volume_mute
          - volume_set
          - volume_buttons
        grid_options:
          columns: full

      - type: custom:mushroom-media-player-card
        entity: media_player.living_room_sound
        name: Living Room
        icon: mdi:speaker
        use_media_info: true
        show_volume_level: true
        media_controls:
          - on_off
          - previous
          - play_pause_stop
          - next
        volume_controls:
          - volume_mute
          - volume_set
          - volume_buttons
        grid_options:
          columns: full

  # ==========================================
  # First Floor Section
  # ==========================================
  - type: grid
    cards:
      - type: heading
        heading: First Floor
        icon: mdi:home-floor-1

      - type: custom:mushroom-chips-card
        alignment: justify
        chips:
          - type: template
            entity: media_player.master_bedroom_sound
            icon: mdi:radio
            content: Radio
            icon_color: >-
              {{ 'amber' if state_attr(entity, 'source') == 'Radio' else 'disabled' }}
            tap_action:
              action: perform-action
              perform_action: media_player.select_source
              target:
                entity_id: media_player.master_bedroom_sound
              data:
                source: Radio

          - type: template
            entity: media_player.master_bedroom_sound
            icon: mdi:cast-audio
            content: Streamer
            icon_color: >-
              {{ 'amber' if state_attr(entity, 'source') == 'Streamer' else 'disabled' }}
            tap_action:
              action: perform-action
              perform_action: media_player.select_source
              target:
                entity_id: media_player.master_bedroom_sound
              data:
                source: Streamer

          - type: template
            icon: mdi:power
            icon_color: red
            tap_action:
              action: perform-action
              perform_action: media_player.turn_off
              target:
                entity_id:
                  - media_player.master_bedroom_sound
                  - media_player.master_bathroom_sound

      - type: custom:mushroom-media-player-card
        entity: media_player.master_bedroom_sound
        name: Master Bedroom
        icon: mdi:speaker
        use_media_info: true
        show_volume_level: true
        media_controls:
          - on_off
          - previous
          - play_pause_stop
          - next
        volume_controls:
          - volume_mute
          - volume_set
          - volume_buttons
        grid_options:
          columns: full

      - type: custom:mushroom-media-player-card
        entity: media_player.master_bathroom_sound
        name: Master Bathroom
        icon: mdi:speaker
        use_media_info: true
        show_volume_level: true
        media_controls:
          - on_off
          - previous
          - play_pause_stop
          - next
        volume_controls:
          - volume_mute
          - volume_set
          - volume_buttons
        grid_options:
          columns: full
```

---

### Pattern C: Stock Lovelace Alternative (Zero Custom Cards)

If you prefer not to install custom frontend cards via HACS, you can use Home Assistant's built-in cards:

#### Option 1: Native Tile Card with Controls
```yaml
type: tile
entity: media_player.kitchen_sound
name: Kitchen Audio
features:
  - type: media-player-volume-slider
  - type: media-player-playback
```

#### Option 2: Native Media Control Card
```yaml
type: media-control
entity: media_player.kitchen_sound
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

## 🪟 Recipe 6: Shutter Travel-Time Workbench (stock cards only)

Timed covers measure **every** run they make: after any open or close that you stop by hand, the cover exposes `last_run_seconds` and `last_run_direction` as entity attributes (see [Covers → Method 3](covers.md#method-3-set-explicit-travel-time-via-service-action)). This recipe puts those attributes, the stored travel times and a one-click **Save** on the dashboard, using only built-in cards — the natural tool for actuators with the factory 60 s cutoff or for MH200 / MH200N gateways, where on-bus calibration is refused.

Add a small script to `scripts.yaml` once (it saves the last run into the matching direction and keeps the other one):

```yaml
save_shutter_run:
  alias: Save the shutter's last run as its travel time
  fields:
    cover:
      description: The timed MyHOME cover
      selector:
        entity:
          domain: cover
          integration: myhome
  sequence:
    - variables:
        seconds: "{{ state_attr(cover, 'last_run_seconds') }}"
        direction: "{{ state_attr(cover, 'last_run_direction') }}"
    - condition: template
      value_template: "{{ seconds is number and direction in ['open', 'close'] }}"
    - action: myhome.set_cover_travel_time
      target:
        entity_id: "{{ cover }}"
      data:
        travel_time_down: "{{ seconds if direction == 'close' else state_attr(cover, 'travel_time_down') }}"
        travel_time_up: "{{ seconds if direction == 'open' else state_attr(cover, 'travel_time_up') }}"
```

Then one card per shutter you want to time:

```yaml
type: entities
title: 🪟 Living Room Shutter — Travel Time
entities:
  - entity: cover.living_room_shutter          # Open / Stop / Close controls
  - type: attribute
    entity: cover.living_room_shutter
    attribute: last_run_seconds
    name: Last run
    suffix: " s"
    icon: mdi:timer-outline
  - type: attribute
    entity: cover.living_room_shutter
    attribute: last_run_direction
    name: Last run direction
    icon: mdi:swap-vertical
  - type: button
    name: Store last run as travel time
    icon: mdi:content-save
    action_name: Save
    tap_action:
      action: perform-action
      perform_action: script.save_shutter_run
      data:
        cover: cover.living_room_shutter
  - type: divider
  - type: attribute
    entity: cover.living_room_shutter
    attribute: travel_time_down
    name: Stored down time
    suffix: " s"
  - type: attribute
    entity: cover.living_room_shutter
    attribute: travel_time_up
    name: Stored up time
    suffix: " s"
  - type: attribute
    entity: cover.living_room_shutter
    attribute: calibration_source
    name: Source
  - entity: button.living_room_shutter_calibrate_travel_time   # on-bus calibration, when the actuator supports it
```

**Workflow**: press **Close** on the first row, press **Stop** the instant the shutter reaches the bottom, check *Last run* / *Last run direction*, press **Save**. Repeat with **Open**. `calibration_source` flips to `manual` and the position slider follows the new times immediately.

> [!TIP]
> Only want the number at a glance? A Markdown card does it in one line:
> ```yaml
> type: markdown
> content: "Last run: **{{ state_attr('cover.living_room_shutter', 'last_run_seconds') }} s** ({{ state_attr('cover.living_room_shutter', 'last_run_direction') }})"
> ```
> The measurement itself is done by the integration — the clock starts at the real motor start on the bus, so the queue delay is never in the number; only your reaction time on **Stop** is.

---

## 🤝 Join In & Share Your Creations!

Every MyHOME installation is unique! Do you have a custom card layout, Mushroom card setup, floorplan SVG, or automation dashboard that you are proud of?

**We invite all community members to share their setups:**

- 💬 **GitHub Discussions**: Post your screenshot and YAML in the [Discussions Forum](https://github.com/OpenWebNet-HA/MyHOME/discussions)!
- 📝 **Contribute a Recipe**: Open a Pull Request adding your recipe to this page in `docs/configuration/lovelace_recipes.md`.
- 🏷️ **Tag Your Setup**: Share what gateway (F454, MH200N, MyHOMEServer1, USB/Serial 3578) and actuator models you are using.

Let's build the best collection of home automation dashboards together!
