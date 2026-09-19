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

---

## 🪄 Native Home Assistant Device Triggers

In MyHOME v2.0, physical pushbuttons are automatically discovered and registered as **Home Assistant Devices**. You do **not** need to write complex template sensors or manual event listeners to automate them!

### Supported Trigger Types
- `short_press`: Fired immediately upon a quick tap.
- `short_release`: Fired when a short tap is released.
- `long_press`: Fired when the button is held down (exceeding ~400ms).
- `long_release`: Fired when a held button is finally released.
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
    type: short_press
    subtype: button_1
    id: short_tap

  - platform: device
    domain: myhome
    device_id: 3c9b7410de884218a4521400e2345678
    type: long_press
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

## 📡 Advanced: The `myhome_event` Bus Stream

If you prefer listening to the global Home Assistant event bus directly (e.g. in AppDaemon or custom automations):

1. Enable **Generate Events** in the integration **Options Flow**.
2. Listen for events of type `myhome_event`:

```yaml
trigger:
  - platform: event
    event_type: myhome_event
    event_data:
      who: 25
      address: "12"
      button: 1
      type: short_press
```

---

## 📜 OpenWebNet Frame Reference

| Action | WHO | Frame Format | Example |
| :--- | :---: | :--- | :--- |
| **CEN Short Press** | 15 | `*15*1*<WHERE>#<BUTTON>##` | `*15*1*11#2##` (Btn 2 on addr 11) |
| **CEN Start Long** | 15 | `*15*0*<WHERE>#<BUTTON>##` | `*15*0*11#2##` |
| **CEN Release** | 15 | `*15*2*<WHERE>#<BUTTON>##` | `*15*2*11#2##` |
| **CEN+ Short Press** | 25 | `*25*21#<BUTTON>*<WHERE>##` | `*25*21#1*12##` (Btn 1 on addr 12) |
| **CEN+ Start Long** | 25 | `*25*22#<BUTTON>*<WHERE>##` | `*25*22#1*12##` |
| **CEN+ Release** | 25 | `*25*24#<BUTTON>*<WHERE>##` | `*25*24#1*12##` |
