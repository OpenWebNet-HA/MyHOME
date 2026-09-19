# Use Cases

End-to-end scenarios that show what the integration is for, each with the configuration or automation that makes it work. Dashboard-side examples are in [Lovelace Recipes](lovelace_recipes.md); the service reference is in [Services](services.md).

## 1. Bring an existing MyHOME plant into Home Assistant without reprogramming it

The usual starting point: a house wired with SCS actuators and a gateway (F454, MyHOMEServer1, MH200N…) that was configured years ago by an installer.

1. Add the integration; the gateway is found by SSDP or entered by IP. Enter the OpenWebNet password.
2. Lights, covers, thermostats and audio zones are created from the bus as they report. Press **Sweep Bus** on the [bus-monitor card](bus_monitor.md) to hydrate everything at once.
3. Give the discovered entities their names in `myhome.yaml` (optional): the file only adds names, device classes and options — it never replaces discovery.

```yaml
# /config/myhome.yaml
00:03:50:81:22:33:
  light:
    living_room:
      where: "12"
      name: "Living Room"
      dimmable: true
  switch:
    garden_socket:
      where: "34"
      name: "Garden socket"
      device_class: outlet
  cover:
    kitchen_blind:
      where: "25"
      name: "Kitchen Blind"
      travel_time: 22
```

Nothing on the gateway changes; the installer's scenarios keep working alongside Home Assistant.

## 2. Physical scenario buttons drive non-MyHOME devices

A CEN+ wall pushbutton (for example an L4652/2 with a 3477 interface) toggles a Zigbee lamp and, on a long press, starts a vacuum. CEN / CEN+ devices are stateless, so this uses **device triggers** — no YAML beyond the automation.

```yaml
automation:
  - alias: Hall button - short press toggles the floor lamp
    trigger:
      - platform: device
        domain: myhome
        device_id: <device id of the CEN+ module>
        type: short_press
        subtype: button_1
    action:
      - service: light.toggle
        target:
          entity_id: light.floor_lamp_zigbee

  - alias: Hall button - long press starts the vacuum
    trigger:
      - platform: device
        domain: myhome
        device_id: <device id of the CEN+ module>
        type: long_press
        subtype: button_1
    action:
      - service: vacuum.start
        target:
          entity_id: vacuum.ground_floor
```

Rotary dials (`rotary_cw_slow`, `rotary_ccw_fast`, …) work the same way — see [CEN & CEN+](cen_cenplus.md) for the eight trigger types and the dimming example.

## 3. Shutters that report a real position

Timed shutter actuators (F411/4, LN4661M2 without encoder) do not know where the shutter is. The integration estimates the position from the travel time, so the estimate is only as good as the number.

- Press **Calibrate travel time** on the cover's device page, or run `myhome.calibrate_cover`. The shutter goes fully up, then down (timed), then up (timed); the two values are stored on the config entry and shown as `travel_time_down` / `travel_time_up` attributes.
- On an MH200 / MH200N, or when the actuator has a 60 s safety cut-off, measure the travel times with a stopwatch and save the numbers with `myhome.set_cover_travel_time`.

With the times right, `cover.set_cover_position` and the position slider behave like a positional actuator:

```yaml
automation:
  - alias: Sun shading - half close the south blinds at noon
    trigger:
      - platform: sun
        event: sunrise
        offset: "06:00:00"
    condition:
      - condition: numeric_state
        entity_id: sensor.outdoor_temperature
        above: 26
    action:
      - service: cover.set_cover_position
        target:
          entity_id:
            - cover.living_room_south
            - cover.kitchen_south
        data:
          position: 50
```

Position-reporting actuators (dimension 10, `advanced_shutter: true`) skip all of this: they are exact out of the box.

## 4. Lights that switch off by themselves — even if Home Assistant is down

`myhome.turn_on_timed` programs the actuator's own timer, so the light goes out on the bus without a Home Assistant `delay:` that a restart would lose.

```yaml
automation:
  - alias: Cellar light - 10 minutes after the door opens
    trigger:
      - platform: state
        entity_id: binary_sensor.cellar_door
        to: "on"
    action:
      - service: myhome.turn_on_timed
        target:
          entity_id: light.cellar
        data:
          minutes: 10
```

The same service accepts `switch` entities (a socket, a fan).

## 5. Whole-house multiroom audio with Music Assistant

The F441 / F441M analog matrix plays whatever is on its inputs. Map one or more network decoders (a Squeezelite / Raspberry Pi per input) in the options flow (**Configure → Dynamic Proxy Decoders**, one row per input: decoder entity, source number, pre-gain). Every audio zone then advertises `play_media`, becomes a Music Assistant player, and on playback claims a free decoder, routes the matrix to it and mirrors playback state and metadata.

- The wall panels keep working: source 0–4 selection and volume still come from the bus.
- Two decoders = two different streams at once; a third room gets *All audio matrix inputs are currently in use* until one stops.

Details, the gain-staging notes (why a pre-gain removes the hiss) and the reference frames are in [Sound System](media_player.md).

## 6. Heating with a central unit, controlled from Home Assistant

A 3550 (`#0`) or 4695 (`#0#1`) central unit is exposed as a `climate` entity with `central: true`; its zones are individual climates. Set the season or switch everything off from an automation while the wall unit keeps its weekly program:

```yaml
automation:
  - alias: Heating off when everyone has left
    trigger:
      - platform: state
        entity_id: group.family
        to: not_home
        for: "00:30:00"
    action:
      - service: climate.set_hvac_mode
        target:
          entity_id: climate.central_unit
        data:
          hvac_mode: "off"

  - alias: Bathroom warmer before the alarm clock
    trigger:
      - platform: time
        at: "06:15:00"
    action:
      - service: climate.set_temperature
        target:
          entity_id: climate.bathroom
        data:
          temperature: 22.5
```

External probes (3455 behind an L4577, `WHERE ≥ 100`) appear as temperature sensors and push their readings on their own; nothing polls them.

## 7. Arm the alarm when the house is empty

With a 3485 / 3486 central unit the `alarm_control_panel` entity supports arm away / arm home / disarm and a panic trigger; the OpenWebNet commands carry no user code, the central unit's own code stays on the keypad. Central units refuse to arm with an open zone; watch the bus-monitor card for the NACK if an automation seems to do nothing.

```yaml
automation:
  - alias: Arm away when the last person leaves
    trigger:
      - platform: state
        entity_id: group.family
        to: not_home
        for: "00:10:00"
    action:
      - service: alarm_control_panel.alarm_arm_away
        target:
          entity_id: alarm_control_panel.myhome
```

## 8. Energy dashboard from the F520 / F521 meters

Energy meters (WHO 18) create an instantaneous power sensor and a total energy sensor (today / month counters exist but are disabled by default). Add the total energy sensor to **Settings → Dashboards → Energy** as grid consumption; the state class `total_increasing` gives the dashboard everything it needs. For a live power gauge, `myhome.start_sending_instant_power` requests a burst of readings from a meter that otherwise reports slowly.

## 9. Diagnose a plant remotely

Someone reports "the kitchen light does not react". Ask them for:

1. **Sweep Bus** then **Export Trace** on the card, or **Download diagnostics** on the integration — both carry the last 500 frames, the gateway identity and the queue state, with passwords redacted.
2. A raw command sent from the card's **Send frame** bar (administrators only) — `*1*1*12##` — while watching whether the actuator answers with `*1*1*12##` or the gateway with a NACK.

The [troubleshooting guide](troubleshooting.md) maps the usual symptoms to what the trace shows.
