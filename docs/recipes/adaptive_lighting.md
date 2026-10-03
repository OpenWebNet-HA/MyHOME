# Adaptive Lighting with MyHOME (SCS & DALI-2)

This recipe describes how to seamlessly integrate the popular [Adaptive Lighting](https://github.com/basnijholt/adaptive-lighting) component with **MyHOME** for natural circadian lighting (smoothly synchronizing brightness and color temperature with the sun's cycle).

---

## Overview & Architecture

BTicino MyHOME lighting systems communicate over a physical SCS two-wire bus operating at 9,600 baud. Circadian lighting controllers like Adaptive Lighting send frequent periodic adjustments throughout the day.

Starting in MyHOME v2, the integration incorporates specific bus-protection and optimization mechanisms designed for adaptive lighting:

1. **Small-Delta Instant Dispatch & Step Clamping**: For dimmers configured with `software_stepped` transitions, brightness adjustments \<= 1% execute instantly without spawning a background fade task, and identical brightness writes on lights that are already on are skipped entirely. For adjustments \>= 2%, calculation steps are clamped to `delta_pct` and duplicate integer percentage frames are deduplicated. (For fixtures configured in `native` transition mode, such as DALI gateways, commands already send a single hardware target frame per adaptation tick). This eliminates bus flooding during periodic circadian adaptations (saving up to ~90% on-wire traffic compared to unclamped 25-step transitions).
2. **Unified Parameter Dispatch**: When Adaptive Lighting dispatches `brightness` and `color_temp_kelvin` together in a single service call, MyHOME dispatches both dimensions (Dimension 1 for dimming, Dimension 14 for tunable white) smoothly.
3. **Graceful Group Transitions**: Both Home Assistant native `light.group` helpers and MyHOME SCS hardware groups (`MyHOMELightGroup`) tolerate `transition` parameters gracefully without raising configuration errors.
4. **Optimistic State Tracking**: Native transition commands update Home Assistant's internal entity state immediately, preventing bouncing or visual flicker in circadian loops.

---

## Supported Hardware

- **DALI-2 Tunable White / DT8 Fixtures**: Controlled via BTicino DALI gateways (e.g., **F429**, **F429G**). Operates using OpenWebNet **Dimension 14** (Color Temperature in Mireds / Kelvin).
- **SCS Dimmers**: BTicino standard dimmers (e.g., **F418**, **F418U2**, **F413**, **002611**, **067557**). Operates using OpenWebNet **Dimension 1** (Dimming percentage 0–100%).
- **On/Off Actuators**: Standard relays (e.g., **F411**). Adaptive Lighting will manage on/off state or 'sleep mode' brightness if linked with dimmable fixtures.

---

## Recommended Adaptive Lighting Settings

When configuring an Adaptive Lighting instance for MyHOME lights, use the following recommended parameters:

| Setting | Recommended Value | Rationale |
| :--- | :--- | :--- |
| **`transition`** | `1` or `2` seconds | Prevents long software step chains from queuing across the 9600-baud SCS bus. |
| **`interval`** | `90` seconds (min floor `60`s) | A 90s interval is imperceptible to the human eye while keeping bus load minimal. |
| **`min_color_temp`** | `2000` K (or fixture min) | Sets the warmest evening/night color temperature. |
| **`max_color_temp`** | `6535` K (or fixture max) | OpenWebNet Dimension 14 supports up to 6535 K (153 mireds). |
| **`separate_turn_on_commands`** | `false` | MyHOME handles simultaneous brightness and color temperature in a unified call. |
| **`detect_non_ha_changes`** | `false` (or test carefully) | Disabling prevents physical wall switch dimming from falsely triggering "manual control override" locks due to bus round-trip latency. **Trade-off note**: with `manual_control_on_external_turn_on: true`, toggling a light on or off at the physical wall switch still pauses circadian adaptation as expected, but physical dimmer adjustments made while the light is already on will not pause adaptation until the light is toggled off and back on. |
| **`manual_control_on_external_turn_on`** | `true` | Allows physical BTicino wall switches to pause circadian adaptation when toggled manually. |
| **`take_over_control`** | `true` | Resets manual override when turning on via Home Assistant UI or automations. |

---

## Example YAML Configuration

If configuring via `configuration.yaml` (or via the UI Integration Options):

```yaml
adaptive_lighting:
  - name: "Living Room Circadian"
    lights:
      - light.living_room_dali_pendant
      - light.living_room_spots
    transition: 2
    interval: 90
    min_color_temp: 2200
    max_color_temp: 6535
    min_brightness: 5
    max_brightness: 100
    sleep_color_temp: 2000
    sleep_brightness: 3
    separate_turn_on_commands: false
    detect_non_ha_changes: false
    manual_control_on_external_turn_on: true
    take_over_control: true
```

---

## Dimmer Transition Mode Selection

In **Settings → Devices & Services → MyHOME → Configure**:

- **`software_stepped` (Default)**: Best for standard dimmers (F418) when you want uniform, stepped fading. The integration's deduplication logic prevents redundant bus frames.
- **`native`**: Best if your fixtures or DALI drivers have hardware-configured fade times (fade rate programmed via DALI tool or MyHOME Suite). Commands send a single hardware target frame.

---

## Working with Groups

### Option A: Home Assistant Light Groups (Recommended)
Create a Light Group helper (**Settings → Devices & Services → Helpers → Create Helper → Light Group**) combining your MyHOME fixtures. Point Adaptive Lighting directly to the `light.group_...` entity. Home Assistant will distribute the target brightness and color temperature across all members. This is the recommended default because it maintains 100% accurate per-entity state tracking regardless of physical actuator model.

### Option B: MyHOME SCS Hardware Groups (`WHERE = '#G'`)
If you define SCS groups in `/config/myhome.yaml` under your gateway MAC address:
```yaml
00:03:50:xx:xx:xx:  # Your gateway MAC address
  light:
    living_room_dali_group:
      where: '#1'
      name: Living Room DALI Group
      dimmable: true
      color_temp: true
      members:
        - '11'
        - '12'
        - '13'
```
Adaptive Lighting can control `light.living_room_dali_group` directly. MyHOME dispatches both brightness and color temperature across the group without transition errors. Targeting an SCS hardware group issues a single pair of frames on the bus for all ballasts, significantly cutting bus traffic compared to individually addressing many fixtures.

---

## Troubleshooting & Best Practices

1. **Slow Reaction or Bus Queuing**:
   If you have many fixtures (e.g. >10 dimmers in a single zone), do not set `interval` lower than `60` seconds. Spreading updates across an interval reduces peak message bursts on the SCS bus.
2. **Tunable White Range**:
   BTicino DALI interfaces support color temperatures down to 153 mireds (~6535 K). Ensure `max_color_temp` does not exceed your driver's rated limits.
3. **Manual Override Reset**:
   If someone turns on a light via an SCS wall switch and Adaptive Lighting marks it as "manually controlled", toggle the switch off and on or call `adaptive_lighting.set_manual_control` (with `manual_control: false`) in your bedtime/morning routines.
