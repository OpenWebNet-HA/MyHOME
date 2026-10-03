# Advanced Uses & Automations (v0.9.x)

> [!NOTE]
> Advanced automations, OpenWebNet events, custom message sending, and hardware bus timers.

---

## Events

### CEN / CEN+ Scenario Pushbutton Events
A powerful feature is assigning CEN or CEN+ commands to physical wall switches and using the generated bus events in Home Assistant to trigger automations. CEN/CEN+ switches do not require explicit entity configuration in Home Assistant; all received messages dispatch bus events automatically.

```yaml
alias: "Trigger Scene from CEN+ Wall Switch"
trigger:
  - platform: event
    event_type: myhome_cenplus_event
    event_data:
      event: pushbutton_short_press
      object: 33
      pushbutton: 7
action:
  - action: light.toggle
    target:
      entity_id: light.living_room
```

```yaml
alias: "Trigger Scene from CEN Wall Switch"
trigger:
  - platform: event
    event_type: myhome_cen_event
    event_data:
      event: pushbutton_long_release
      object: 10
      pushbutton: 1
action:
  - action: media_player.media_play_pause
    target:
      entity_id: media_player.sonos_living_room
```

- **CEN Events**:
  - `pushbutton_short_press`
  - `pushbutton_short_release`
  - `pushbutton_long_press`
  - `pushbutton_long_release`
- **CEN+ Events**:
  - `pushbutton_short_press`
  - `pushbutton_long_press`
  - `pushbutton_long_release`

---

## Bus Broadcast Events

When group, area, or general commands occur on the physical bus, the integration dispatches corresponding Home Assistant events:

### Light Events
- `myhome_general_light_event`
- `myhome_area_light_event`
- `myhome_group_light_event`

Attributes:
- `event`: `'on'` or `'off'`
- `area`: Area number (for area events)
- `group`: Group ID (for group events, without leading `#`)
- `message`: Full raw OpenWebNet frame

*Example*: Synchronizing a Home Assistant Light Group with an SCS group button:

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
      entity_id: light.living_room_group
```

### Automation (Cover) Events
- `myhome_general_automation_event`
- `myhome_area_automation_event`
- `myhome_group_automation_event`

Attributes:
- `event`: `'open'`, `'close'`, or `'stop'`
- `area`: Area number
- `group`: Group ID

---

## Services

### Instant Power Sampling (`myhome.start_sending_instant_power`)
WHO 18 power meters only report live power when explicitly polled for a set window (up to 255 minutes). You can create an automation running periodically:

```yaml
action: myhome.start_sending_instant_power
data:
  duration: 120
  entity_id: sensor.general_power
```

### Gateway Time Synchronization (`myhome.sync_time`)
Writes the Home Assistant system clock to the gateway's real-time clock:

```yaml
action: myhome.sync_time
data: {}
```

### Raw OpenWebNet Command Injection (`myhome.send_message`)
Send arbitrary OpenWebNet frames directly to the SCS bus:

```yaml
action: myhome.send_message
data:
  message: "*1*0*0##"  # General Off for all lights
```

---

## ⏱️ Native Bus Timers (Temporized Lights)

When automating lights that should turn off automatically after a set duration (corridors, staircases, pantries), software delays in Home Assistant risk leaving lights on if Home Assistant restarts.

The BTicino / Legrand MyHOME bus features **actuator-level hardware timers (`WHO = 1`)**: the physical relay executes the countdown internally on the SCS bus.

### 1. Pre-set Timers (`WHAT = 11..18`)

| `WHAT` Code | Duration | Frame Syntax | Typical Application |
|:---:|:---:|:---|:---|
| **18** | 0.5 s | `*1*18*<WHERE>##` | Door buzzer pulse, gate trigger |
| **17** | 30 s | `*1*17*<WHERE>##` | Entrance courtesy light |
| **11** | 1 min | `*1*11*<WHERE>##` | Pantry, walk-through hallway |
| **12** | 2 min | `*1*12*<WHERE>##` | Staircase landing |
| **13** | 3 min | `*1*13*<WHERE>##` | Garage entrance |
| **14** | 4 min | `*1*14*<WHERE>##` | Storage room |
| **15** | 5 min | `*1*15*<WHERE>##` | Garden walkway |
| **16** | 15 min | `*1*16*<WHERE>##` | Utility room, carport |

```yaml
action: myhome.send_message
data:
  message: "*1*13*21##"  # Turn light 21 on for 3 minutes
```

### 2. Custom Duration Timers (Dimension `2`)

```text
*#1*<WHERE>*#2*<HOURS>*<MINUTES>*<SECONDS>##
```

```yaml
# Turn light 21 ON for 20 minutes:
action: myhome.send_message
data:
  message: "*#1*21*#2*0*20*0##"
```
