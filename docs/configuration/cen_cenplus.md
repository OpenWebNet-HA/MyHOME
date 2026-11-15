# Scenario Controls & Pushbuttons (`WHO = 15` / `WHO = 25`)

This guide explains how to integrate physical MyHOME pushbuttons and scenario interfaces (CEN and CEN+) into Home Assistant automations using **Native Device Triggers**.

---

## 🔘 CEN vs. CEN+ Overview

BTicino / Legrand pushbuttons operate in either **CEN** (`WHO = 15`) or **CEN+** (`WHO = 25`) mode depending on physical or virtual configurators.

| Feature | **CEN (`WHO = 15`)** | **CEN+ (`WHO = 25`)** |
| :--- | :--- | :--- |
| **Typical Hardware** | L/N/NT4652, 067552, F420 | L/N/NT4652/2, 067554, 3477 (Dry Contacts), F428 |
| **Buttons per Device (UI Device Triggers)** | `0` to `31` (`00..31` on wire) | `0` to `31` (`button_0`..`button_31` in UI; protocol supports `0..31` per CEN+ object) |
| **Addressing Syntax** | `*15*<BB>[#<PHASE>]*<WHERE>##` | `*25*<WHAT>#<BUTTON>*<WHERE>##` |
| **Short Press Event** | `*15*<BB>*<WHERE>##` (no phase qualifier) | `WHAT = 21` (`*25*21#<BUTTON>*<WHERE>##`) |
| **Short Release Event** | `#1` (`*15*<BB>#1*<WHERE>##`) | N/A (CEN-only; CEN+ has no short-release frame) |
| **Extended / Long Press** | `#3` (`*15*<BB>#3*<WHERE>##`, repeated ~0.5 s while held) | `WHAT = 22` (start) & `WHAT = 23` (~0.5 s hold repeat) |
| **Release Long Press** | `#2` (`*15*<BB>#2*<WHERE>##`) | `WHAT = 24` (`*25*24#<BUTTON>*<WHERE>##`) |
| **Rotary Dials** | ❌ Not supported on CEN | ✅ Supported (`WHAT = 25..28`: CW/CCW slow & fast) |
| **Dry Contact Status** | N/A | `WHAT = 31` (Closed), `WHAT = 32` (Opened) |
| **Scenario Plus Actions** | N/A | `WHAT = 11..15` (`on`, `off`, `increase`, `decrease`, `stop`) |

---

## 🪄 Native Home Assistant Device Triggers

In MyHOME v2.0, physical pushbuttons are automatically discovered and registered as **Home Assistant Devices**. You do **not** need to write complex template sensors or manual event listeners to automate them!

> [!IMPORTANT]
> **Coexistence with Native MyHome Scenarios & Actuator Addresses (`MyHome_Up` / `MyHOME_Suite` / `TiMH200N`)**  
> Home Assistant passively observes `WHO = 15` / `WHO = 25` frames on the SCS bus; it does **not** intercept or suppress them. Keep two plant-level rules in mind:
>
> 1. **Do not bind the same button to both a native MyHome scenario and a Home Assistant automation (unless intended)**:  
>    If a pushbutton is already configured in the **MyHome_Up** app (`MyHomeServer1`) or **MyHOME_Suite / TiMH200N** (`MH200N`, `MH201`, `MH202`, `F420`) to trigger a native bus scenario or automation, pressing that button will execute the native MyHome scenario **and** any Home Assistant automation attached to it at the same time ([Discussion #770](https://github.com/orgs/OpenWebNet-HA/discussions/770#discussioncomment-19121695)).
>    - Only bind a button in both places when you intentionally want Home Assistant to **extend** a native bus scenario (for example, switching non-SCS Zigbee/Matter devices alongside an existing SCS scene).
>    - For a **Home Assistant–only button**, keep the CEN/CEN+ virtual object configured on the physical control so it emits frames on the bus, but remove or leave empty any scenario action bound to that button in **MyHome_Up** / **MyHOME_Suite**.
> 2. **Never share a Basic CEN (`WHO = 15`) `A`/`PL` address with a physical actuator (`WHO = 1` / `WHO = 2`)**:  
>    Per BTicino's scenario-programmer specifications, a control configured in Basic CEN mode (`M = CEN`) must use a dedicated `A`/`PL` address that **does not duplicate any light or shutter actuator address**. If a CEN control shares an address with a light actuator (e.g. `WHERE = 61`), toggling or polling that light from Home Assistant (`*1*1*61##` or `*#1*61##`) causes the CEN control at `61` to emit `*15*<BUTTON>*61##` on the bus and fire whatever scenario is bound to it in the scenario programmer ([#612](https://github.com/OpenWebNet-HA/MyHOME/issues/612), [#773](https://github.com/OpenWebNet-HA/MyHOME/issues/773)).

### Supported Trigger Types
- `pushbutton_short_press`: Fired immediately upon a quick tap (CEN & CEN+).
- `pushbutton_short_release` (CEN only): Fired when a short tap is released (`*15*<BB>#1*<WHERE>##`).
- `pushbutton_long_press`: Fired when the button is held down (exceeding ~400 ms; CEN `#3` / CEN+ `WHAT = 22`). On CEN+ it fires once per hold.
- `pushbutton_long_press_repeat` (CEN+ only): Fired about every 0.5 s while the button stays held (`WHAT = 23`). Use it for "hold to dim"; use `pushbutton_long_press` for actions that should run once.
- `pushbutton_long_release`: Fired when a held button is finally released (CEN `#2` / CEN+ `WHAT = 24`).
- `rotary_cw_slow` (CEN+ only): Clockwise rotation at normal speed.
- `rotary_cw_fast` (CEN+ only): Clockwise rotation at fast speed.
- `rotary_ccw_slow` (CEN+ only): Counter-clockwise rotation at normal speed.
- `rotary_ccw_fast` (CEN+ only): Counter-clockwise rotation at fast speed.

### Button Subtypes
- `button_0` through `button_31` (`0..31` for both CEN and CEN+ devices in `device_trigger.py`) corresponding to physical button keys or rocker positions on the faceplate.

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
> For physical CEN (`WHO = 15`) devices configured with 4-digit SCS addresses (`PL ≥ 10`, e.g. `0512` where Area = 5, Point-Light = 12):
>
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

### WHO 25 Scenario Plus Action Events (`myhome_scenario_plus_event`)

When a CEN+ Scenario Plus control sends an action frame (`WHO = 25`, `WHAT = 11..15`), the integration automatically registers a **`CEN+ Scenario Plus Control`** device in the Home Assistant Device Registry (`model="CEN+ Scenario Plus Control"`) and fires `myhome_scenario_plus_event`:

- `object`: Scenario Plus object identifier (where available)
- `where`: Wire Scenario Plus address (e.g. `"11"` or `"205"`)
- `action`: Canonical action string — `on` (`WHAT = 11`), `off` (`WHAT = 12`), `increase` (`WHAT = 13`), `decrease` (`WHAT = 14`), or `stop` (`WHAT = 15`)
- `gateway_mac`: Active gateway MAC address
- `entry_id`: Config entry identifier

You can also transmit outbound Scenario Plus commands from Home Assistant using [`myhome.send_scenario_plus_command`](services.md#13-myhomesend_scenario_plus_command).

```yaml
trigger:
  - platform: event
    event_type: myhome_scenario_plus_event
    event_data:
      where: "11"
      action: "on"
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
