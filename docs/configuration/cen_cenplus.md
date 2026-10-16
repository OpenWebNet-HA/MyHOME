# Scenario Controls & Pushbuttons (`WHO = 15` / `WHO = 25`)

This guide explains how to integrate physical MyHOME pushbuttons and scenario interfaces (CEN and CEN+) into Home Assistant automations using **Native Device Triggers**.

---

## 🔘 CEN vs. CEN+ Overview

BTicino / Legrand pushbuttons operate in either **CEN** (`WHO = 15`) or **CEN+** (`WHO = 25`) mode depending on physical or virtual configurators.

| Feature | **CEN (`WHO = 15`)** | **CEN+ (`WHO = 25`)** |
| :--- | :--- | :--- |
| **Typical Hardware** | L/N/NT4652, 067552, F420 | L/N/NT4652/2, 067554, 3477 (Dry Contacts), F428 |
| **Buttons per Device** | 1 to 32 | 0 to 255 |
| **Addressing Syntax** | `*15*<WHAT>*<WHERE>#<BUTTON>##` | `*25*<WHAT>#<BUTTON>*<WHERE>##` |
| **Short Press Event** | `WHAT = 1` | `WHAT = 21` |
| **Start Long Press** | `WHAT = 0` | `WHAT = 22` |
| **Release Long Press** | `WHAT = 2` | `WHAT = 24` |
| **Rotary Dials** | Supported via vendor extensions | Supported (CW/CCW slow & fast) |
| **Dry Contact Status** | N/A | `WHAT = 31` (Closed), `WHAT = 32` (Opened) |
| **Scenario Plus Actions** | N/A | `WHAT = 11..15` (`on`, `off`, `increase`, `decrease`, `stop`) |

---

## 🪄 Native Home Assistant Device Triggers

In MyHOME v2.0, physical pushbuttons are automatically discovered and registered as **Home Assistant Devices**. You do **not** need to write complex template sensors or manual event listeners to automate them!

### Supported Trigger Types
- `pushbutton_short_press`: Fired immediately upon a quick tap.
- `pushbutton_short_release`: Fired when a short tap is released.
- `pushbutton_long_press`: Fired when the button is held down (exceeding ~400ms). On CEN+ it fires once per hold.
- `pushbutton_long_press_repeat` (CEN+ only): Fired about every 0.5 s while the button stays held (`WHAT = 23`). Use it for "hold to dim"; use `pushbutton_long_press` for actions that should run once.
- `pushbutton_long_release`: Fired when a held button is finally released.
- `rotary_cw_slow`: Clockwise rotation at normal speed.
- `rotary_cw_fast`: Clockwise rotation at fast speed.
- `rotary_ccw_slow`: Counter-clockwise rotation at normal speed.
- `rotary_ccw_fast`: Counter-clockwise rotation at fast speed.

### Button Subtypes
- `button_0` through `button_31` corresponding to physical button keys or rocker positions on the faceplate.

---

## 🛠️ Automation Examples

### 1. UI Automation Builder
1. Go to **Settings** -> **Automations & Scenes** -> **Create Automation**.
2. Click **Add Trigger** -> **Device**.
3. Select your physical MyHOME control (e.g. `Living Room CEN Switch`).
4. Select the trigger (e.g. `Short press on button_1`).
5. Add your desired action (e.g. toggle a light, activate a scene, or announce TTS).

---

### 2. YAML Automation: Short Press vs. Long Press
Below is an example YAML automation showing how to use native device triggers to toggle a light on short press and turn off the entire house on long press:

```yaml
alias: "Living Room Button 1 Actions"
description: "Short press toggles chandelier; long press triggers whole house goodnight"
trigger:
  - platform: device
    domain: myhome
    device_id: 3c9b7410de884218a4521400e2345678
    type: pushbutton_short_press
    subtype: button_1
    id: short_tap

  - platform: device
    domain: myhome
    device_id: 3c9b7410de884218a4521400e2345678
    type: pushbutton_long_press
    subtype: button_1
    id: hold

action:
  - choose:
      - conditions:
          - condition: trigger
            id: short_tap
        sequence:
          - target:
              entity_id: light.living_room_chandelier
            action: light.toggle

      - conditions:
          - condition: trigger
            id: hold
        sequence:
          - target:
              entity_id: all
            action: light.turn_off
```

---

### 3. YAML Automation: Rotary Dimmer Dial
If you have a digital rotary encoder (such as Legrand 067554):

```yaml
alias: "Dining Room Rotary Dimmer"
trigger:
  - platform: device
    domain: myhome
    device_id: 3c9b7410de884218a4521400e2345678
    type: rotary_cw_slow
    subtype: button_1
    id: brighten
  - platform: device
    domain: myhome
    device_id: 3c9b7410de884218a4521400e2345678
    type: rotary_ccw_slow
    subtype: button_1
    id: dim

action:
  - choose:
      - conditions:
          - condition: trigger
            id: brighten
        sequence:
          - target:
              entity_id: light.dining_room_table
            action: light.turn_on
            data:
              brightness_step_pct: 10

      - conditions:
          - condition: trigger
            id: dim
        sequence:
          - target:
              entity_id: light.dining_room_table
            action: light.turn_on
            data:
              brightness_step_pct: -10
```

---

## 📡 Advanced: Listening to the Event Bus

If you prefer listening to the Home Assistant event bus directly (e.g. in AppDaemon or custom automations), every button event is fired as `myhome_cen_event` (CEN) or `myhome_cenplus_event` (CEN+). No option needs to be enabled. The event data holds:

- `object`: the CEN object, or the CEN+ object without its leading `2` (WHERE `21` is object `1`)
- `pushbutton`: the button number
- `event`: one of the trigger types above (`pushbutton_short_press`, `rotary_cw_slow`, …)
- `where`, `gateway_mac` and `entry_id`, to tell plants and gateways apart

> [!NOTE]
> For physical CEN (`WHO = 15`) devices configured with 4-digit SCS addresses ($PL \ge 10$, e.g. `0512` where Area = 5, Point-Light = 12):
> - `object` is exposed as an integer (`512`), preserving backward compatibility for existing automations.
> - `where` provides the literal wire address string (`"0512"`).
> - Event-bus listeners (in YAML automations, AppDaemon, or Node-RED) should filter on `where: "0512"` or `object: 512`. Outside of Python in this process, string `"0512"` does not equal the integer `object`.
> - Home Assistant **Native Device Triggers** match seamlessly whether referenced by Device ID, integer `512`, or wire string `"0512"`.

```yaml
trigger:
  - platform: event
    event_type: myhome_cenplus_event
    event_data:
      object: 1
      pushbutton: 1
      event: pushbutton_short_press
```

### WHO 17 Scenario Programmer Events (`myhome_scene_event`)

When a physical scenario programmer (e.g. MH200N, MH201, F420) executes or updates a stored scenario on the OpenWebNet bus, the integration fires `myhome_scene_event`:

- `scenario`: Integer scenario identifier (e.g. `1`)
- `where`: Wire scenario address
- `state`: Numeric state (`1` = started, `2` = stopped, `3` = enabled, `4` = disabled)
- `is_on`: `True` when started, `False` when stopped, `None` for enable/disable
- `is_enabled`: `True` when enabled, `False` when disabled, `None` for start/stop
- `gateway_mac`: Active gateway MAC address (with standby failover support)
- `entry_id`: Config entry identifier

> [!IMPORTANT]
> **Scenario Lifecycle vs. Load Power State**:  
> In OpenWebNet WHO 17 semantics, `is_on: true` denotes that the **scenario routine has started executing**, not that electrical circuits are turned on. For example, a "Leave Home" scene programmed to switch all lights OFF will emit `is_on: true` upon start, followed by actuator OFF commands, and `is_on: false` when execution finishes.

```yaml
trigger:
  - platform: event
    event_type: myhome_scene_event
    event_data:
      scenario: 1
      is_on: true
```

Enabling **Generate Events** in the **Options Flow** additionally fires every bus frame as `myhome_message_event`.

---

## 📜 OpenWebNet Frame Reference

| Action | WHO | Frame Format | Example |
| :--- | :---: | :--- | :--- |
| **CEN Press** | 15 | `*15*<BUTTON>*<WHERE>##` | `*15*02*11##` (Btn 2 on addr 11) |
| **CEN Short Release** | 15 | `*15*<BUTTON>#1*<WHERE>##` | `*15*02#1*11##` |
| **CEN Long Press** (repeats while held) | 15 | `*15*<BUTTON>#3*<WHERE>##` | `*15*02#3*11##` |
| **CEN Long Release** | 15 | `*15*<BUTTON>#2*<WHERE>##` | `*15*02#2*11##` |
| **CEN+ Short Press** | 25 | `*25*21#<BUTTON>*<WHERE>##` | `*25*21#1*21##` (Btn 1 of object 1: WHERE is `2` + object) |
| **CEN+ Start Long** | 25 | `*25*22#<BUTTON>*<WHERE>##` | `*25*22#1*21##` |
| **CEN+ Still Held** (repeats ~0.5 s) | 25 | `*25*23#<BUTTON>*<WHERE>##` | `*25*23#1*21##` |
| **CEN+ Release** | 25 | `*25*24#<BUTTON>*<WHERE>##` | `*25*24#1*21##` |
| **Scenario Plus On** | 25 | `*25*11#0*<WHERE>##` | `*25*11#0*11##` |
| **Scenario Plus Off** | 25 | `*25*12*<WHERE>##` | `*25*12*11##` |
| **Scenario Plus Increase** | 25 | `*25*13#0#5*<WHERE>##` | `*25*13#0#5*11##` |
| **Scenario Plus Decrease** | 25 | `*25*14#0#5*<WHERE>##` | `*25*14#0#5*11##` |
| **Scenario Plus Stop** | 25 | `*25*15*<WHERE>##` | `*25*15*11##` |
| **Scene Start** | 17 | `*17*1*<WHERE>##` | `*17*1*1##` (Scenario 1 started) |
| **Scene Stop** | 17 | `*17*2*<WHERE>##` | `*17*2*1##` (Scenario 1 stopped) |
| **Scene Enable** | 17 | `*17*3*<WHERE>##` | `*17*3*1##` (Scenario 1 schedule enabled) |
| **Scene Disable** | 17 | `*17*4*<WHERE>##` | `*17*4*1##` (Scenario 1 schedule disabled) |
