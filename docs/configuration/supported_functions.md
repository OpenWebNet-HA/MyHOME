# Supported Functions

What the integration does — and does not — do for each OpenWebNet subsystem (`WHO`) and Home Assistant platform. Hardware and gateway models are listed in [Hardware Compatibility](../getting-started/hardware-compatibility.md); the wire-level conformance corpus is in the [Protocol Conformance Matrix](../protocol_conformance_matrix.md). Things that are deliberately *not* supported, and why, are in [Known Limitations](known_limitations.md).

Legend: ✅ supported · 👁️ read-only · ⚙️ via a service, not an entity · ❌ not supported

## Subsystems

| WHO | Subsystem | Platform(s) | Status | Notes |
| :---: | :--- | :--- | :---: | :--- |
| 0 | Scenarios (scenario modules) | — | ⚙️ | Send `*0*<what>*<where>##` with `myhome.send_message`; no entity is created. |
| 1 | Lighting | `light`, `switch`, `cover`, `binary_sensor`, `sensor` | ✅ | Lighting actuators, declared relay switches & impulse covers, motion/lux sensors; see the platform tables below. |
| 2 | Automation (shutters, blinds) | `cover` | ✅ | Timed, position-reporting, and Venetian slat-tilt actuators. |
| 3 | Load control (legacy) | — | ❌ | F421 load disconnection; not implemented. |
| 4 | Thermoregulation | `climate`, `switch`, `sensor` | ✅ | Zone thermostats, central units 3550 (`#0`) and 4695 (`#0#1`), probe-less thermo actuator switches, probes. |
| 5 | Burglar alarm | `alarm_control_panel` | 👁️ | Central unit status (read-only) plus `myhome_alarm_event` on the event bus; arm/disarm through installer-programmed AUX frames — see [alarm](alarm.md#arming-and-disarming). |
| 6 | Door entry | `lock`, event bus | ✅ | Door strikes (`*6*10*<where>##`), lock release status, and `myhome_doorbell_event`. Requires a gateway whose firmware routes `WHO 6` and that sees the door-entry riser: `MH200`, `MH202`, `F453` (firmware-verified); `F453AV`, `F452`, `F452V`, `H4684`, `H4890`, `MH201`, `MH200N` (unverified). Not routed by `F454` / `F459` (`WHO 8` only) or by `MyHomeServer1`, `F455`, `F461`, `F450`, and `F460`. |
| 7 | Video door entry / multimedia | — | ❌ | F453AV video session routing; not implemented. |
| 8 | Video door entry / intercom | event bus | 👁️ | Entrance panel `myhome_doorbell_event` (`*8*1#<1..4>#<mm>*<where>##`) from a gateway that sees the door-entry riser: `F454`, `F459`; `F453AV`, `F452`, `F452V` (unverified); or `MH200` / `MH200N` / `MH202` when the riser reaches their bus (e.g. through an `F422`). |
| 9 | Auxiliary channels | `binary_sensor`, `switch` | ✅ | AUX 0–9 binary sensors (configured in `myhome.yaml`) and per-device **Invert state** configuration switches. |
| 13 | Gateway management | — | ⚙️ | Clock sync (`myhome.sync_time`), firmware / model / identity for the device registry and diagnostics. |
| 14 | Actuator lock | `button` | ✅ | Lock / unlock buttons on every light, switch and cover device. |
| 15 | CEN scenario pushbuttons | device triggers | ✅ | Short / long press and short / long release (buttons `00..31`), `myhome_cen_event`. |
| 16 | Sound system | `media_player`, `number`, `button` | ✅ | F441 / F441M matrices, F500 / F500N FM radio tuners, streaming proxy for network decoders. |
| 17 | Scenario programmer (scenes) | — | 👁️ | Fires `myhome_scene_event` on scene start/stop/enable/disable; no entity is created. |
| 18 | Energy management | `sensor` | ✅ | Instantaneous active power (W) and total / daily / monthly energy counters (Wh). |
| 22 | Sound diffusion (audio matrix & FM tuner) | `media_player`, `number`, `button` | ✅ | F500 / F500N FM tuner control and telemetry (`5#2#S` / `2#S`) on dual-stack gateways (including `H/L4684` FW ≥ `6.0.0`) and `WHO 22`-only gateways (`MyHomeServer1`, `F461`), plus active-source area routing (`4#<area>`) on dual-stack plants (room amplifier discovery and zone commands require `WHO 16`). |
| 24 | Lighting management (commercial) | — | ❌ | BMNE500 / BMview controllers; not implemented. |
| 25 | CEN+ pushbuttons and dry contacts | device triggers, `binary_sensor`, `switch` | ✅ | Short / long press, hold repeat, release, rotary dials (`myhome_cenplus_event`), Scenario Plus actions `WHAT = 11..15` (`myhome_scenario_plus_event` + `CEN+ Scenario Plus Control` devices), F482 / 3477 dry contacts, and per-device **Invert state** configuration switches. |

## Platforms

### `light` (WHO 1)

| Function | Status | Notes |
| :--- | :---: | :--- |
| On / off | ✅ | Relay and dimmer actuators, including F422 private-bus addresses (`#4#<interface>`). |
| Brightness | ✅ | Dimmers (`dimmable: true` or learned from a level frame). |
| Transitions | ✅ | `software_stepped` (default, up to 25 steps) or the actuator's native fade speed parameter — gateway option. |
| Flash | ✅ | |
| Colour temperature (DALI DT8) | ✅ | Dimension 14, 2000–6535 K; learned from the first colour frame. |
| HS / RGB colour | ✅ | Learned from the first colour frame; modes accumulate, they never replace each other. |
| Hardware timer | ✅ | `myhome.turn_on_timed`: the actuator switches off by itself. |
| Group / area / general commands from wall switches | ✅ | `LightingResyncManager` runs a `500 ms` debounced status sweep (`broadcast_resync`, enabled by default), updates declared `light:` group entities (`where: '#1'`..`'#255'` with optional `members:`), and fires `myhome_group_light_event` / `myhome_area_light_event` / `myhome_general_light_event`. |

### `switch` (WHO 1 / 4 / 9 / 25)

| Function | Status | Notes |
| :--- | :---: | :--- |
| On / off (lighting relays, WHO 1) | ✅ | Declared under `switch:` in `myhome.yaml` (`class: switch` or `outlet`; `WHO 1` bus traffic otherwise auto-discovers as `light`). |
| Thermoregulation zone / actuator power switch (WHO 4) | ✅ | Probe-less thermo actuator / towel-warmer relays (`Z` or `Z#N` in `thermo_switch.py`, configured via subentry flow or `myhome.yaml`). |
| Auxiliary / CEN+ contact invert switch (WHO 9 / 25) | ✅ | Per-device **Invert state** configuration switch (`EntityCategory.CONFIG`) paired with WHO 9 and WHO 25 binary sensors. |
| Hardware timer (WHO 1) | ✅ | `myhome.turn_on_timed` (`duration` or `hours` / `minutes` / `seconds`, individual `WHO 1` point addresses only). |

### `cover` (WHO 1 / WHO 2)

| Function | Status | Notes |
| :--- | :---: | :--- |
| Open / close / stop | ✅ | Standard WHO 2 shutter actuators. |
| Impulse cover (WHO 1) | ✅ | Pulse-driven gates and garage doors: direct pulses with deadband and sensor feedback by default, and an optional `access:` workflow with phone approval, pre-warning (`myhome_access_prewarning`), and watchdog. |
| Position (position-reporting actuators) | ✅ | Auto-detected from calibrated `Dimension 10` frames (`0..100`), or explicit `advanced: true` / `advanced_shutter: true` in `myhome.yaml` (commands via `Dimension 11`, status via `Dimension 10`; area/general `set_cover_position` requires `interface` `#4#II` or configured `members:`). |
| Position (timed actuators) | ✅ | Virtual position from the travel time: `travel_time` in YAML, measured with `myhome.calibrate_cover`, or set by hand with `myhome.set_cover_travel_time`. |
| Tilt (Venetian blinds) | ✅ | Timed slat rotation (`slat_tilt: true` in `myhome.yaml`, calibrated with `slat_time` in `myhome.set_cover_travel_time`; estimated from `slat_time` on every actuator, including `advanced_shutter: true`, as `WHO 2` carries no slat-angle field on the bus). |
| Echo suppression | ✅ | The gateway's relay of our own command is not mistaken for a keypad press. |

### `lock` (WHO 6)

| Function | Status | Notes |
| :--- | :---: | :--- |
| Door entry lock (WHO 6) | ✅ | Door entry strikes from `myhome.yaml` with optional constant-time PIN authentication and auto-relock. Never created from bus traffic; `who: "1"` relays are rejected (motorized gates and garage doors are impulse covers). Requires a gateway that routes `WHO 6` and sees the door-entry riser, or **Shared Bus → Secondary** `WHO 6` delegation to one (such as an `MH202` on the riser); `F454` / `F459` route only `WHO 8`. If the gateway rejects `*6*10*<where>##` with `*#*0##` (e.g. `MyHomeServer1`, `F455`, `F461`, `F454`), `lock.unlock` raises `HomeAssistantError` and leaves the entity locked; an unlock not confirmed within 15 s is withdrawn if it has not been sent yet. |
| Doorbell event (WHO 6 / WHO 8) | ✅ | `myhome_doorbell_event` for an entrance panel ringing (WHO 8 call kinds 1-4, WHO 6 call), not for handset-to-handset calls or pager broadcasts. Fired by the gateway that owns the subsystem only (delegatable via `WHO 6` / `WHO 8` on a shared bus). The WHO 6 chime (WHAT 20) is unverified: it is not in the published WHO 6 table and has no capture. |

### `climate` (WHO 4)

| Function | Status | Notes |
| :--- | :---: | :--- |
| Current temperature | ✅ | |
| Target temperature | ✅ | Manual set-point. |
| HVAC modes | ✅ | `heat`, `cool`, `auto`, `off`; heating / cooling support per zone from YAML. |
| Fan speed (3-speed fancoil) | ✅ | Auto-detected from dimension 11/20 frames, or explicit `fan: true`. |
| Proportional 0–10 V output level (`100..200`) | 👁️ | Exposed on `Dimension 20` strictly for explicit `Z#N` (`1 <= Z <= 99`, `1 <= N <= 9`) point actuators as `actuator_levels` (`0..100 %`, excluded from recorder history via `_unrecorded_attributes`) while keeping `actuators` `"on"` / `"off"`, alongside an auto-discovered `%` `sensor` entity per proportional actuator. |
| Current humidity | ✅ | Auto-detected from dimension 60 frames, restored across restarts, or explicit `humidity: true`. |
| Central unit modes | ✅ | Central unit 3550 (`#0`) and 4695 (`#0#1`..`#0#4`) as `central: true` zones; seasonal propagation. 99-zone `#0` is polled via `*#4*#0##`, while 4-zone `#0#1`..`#0#4` units remain exempt from point-to-point status polling. |
| Weekly programs / scenarios | ❌ | Program selection frames (`*4*11xx*#0##`) can be sent with `myhome.send_message`. |
| Temperature-only probes | 👁️ | `sensor` platform (see below). |

### `alarm_control_panel` (WHO 5)

| Function | Status | Notes |
| :--- | :---: | :--- |
| State (disarmed / armed away / triggered) | 👁️ | Central units 3485 / 3486. |
| Event bus notifications | ✅ | Every `WHO 5` status and zone frame fires `myhome_alarm_event` (`where`, `state`, `state_code`, `is_alarm`, `message`). |
| Arm away / arm home / disarm | ❌ | The central unit rejects WHO 5 arm/disarm over SCS (#564); send the AUX frames it is programmed for with `myhome.send_message`, e.g. from a template alarm panel ([alarm](alarm.md#arming-and-disarming)). |
| Trigger (panic) | ❌ | Not sent; no capture shows the central unit accepting it. |
| Zone status | 👁️ | The panel follows the central unit and the zone-0 broadcast; individual zones are not entities (limitations). |

### `binary_sensor` (WHO 1 / 9 / 25)

| Function | Status | Notes |
| :--- | :---: | :--- |
| Dry contacts (CEN+, WHO 25) | ✅ | F428 / 3477 / 3480 / F482 (`3xxx` addresses auto-discover); device class selectable in UI (**Show As**) or YAML. |
| Auxiliary channels (WHO 9) | ✅ | AUX 0–9 (declared under `binary_sensor:` in `myhome.yaml`; not auto-discovered from bus traffic). |
| Motion / presence (WHO 1) | ✅ | Motion frames of lighting sensors (auto-discovered); a timeout restores `off`. |
| Inverted contacts | ✅ | Per-device **Invert state** configuration switch (`EntityCategory.CONFIG`) on WHO 25 and WHO 9 devices, or `inverted: true` in `myhome.yaml`. |

### `sensor` (WHO 1 / 4 / 18)

| Function | Status | Notes |
| :--- | :---: | :--- |
| Instantaneous power (W) | ✅ | Push-driven; `myhome.start_sending_instant_power` requests a burst of readings (`myhome.stop_sending_instant_power` stops it). |
| Energy total / today / month (Wh) | ✅ | Today and month are **disabled by default**. |
| Temperature (zones and probes) | ✅ | Standalone probes (`WHERE ≥ 100` or Dimension 15) auto-discover and are push-driven (polled only when silent); thermostat zones `1..99` belong to `climate` unless declared under `sensor:` in `myhome.yaml` (polled every 5 min). |
| Proportional 0–10 V actuator output level (`%`) | ✅ | Auto-discovered strictly for explicit `Z#N` (`1 <= Z <= 99`, `1 <= N <= 9`) `Dimension 20` reports (`*#4*Z#N*20*100..200##`) and attached to the zone's `climate` device; inherits `SensorEntity` (`unknown` after restart until the first bus report) and registers `watch_zone_actuators` (startup/reconnect) plus `homeassistant.update_entity` where supported. Discrete `0..22` actuators create no extra entities. |
| Illuminance (lux) | ✅ | WHO 1 light sensors. |
| Humidity | 👁️ | Shown as the climate entity's `current_humidity` when the zone reports it; no separate sensor. |

### `button` (WHO 14 / 2 / 16 / 22)

| Function | Status | Notes |
| :--- | :---: | :--- |
| Lock / unlock actuator | ✅ | On every light, switch and cover device (`EntityCategory.CONFIG`). |
| Calibrate travel time | ✅ | On every timed cover device, plus *Calibrate all covers* on the gateway device. |
| FM tuner seek up / down | ✅ | Companion *Seek up* and *Seek down* buttons on every declared F500 / F500N `Audio Source` device (`WHO 16` `*16*5000/5100*10S##` or `WHO 22` `*22*5/6*2#S##`). |

### `number` (WHO 16 / 22)

| Function | Status | Notes |
| :--- | :---: | :--- |
| FM tuner manual frequency | ✅ | `number.<source>_frequency` (`87.50–108.00 MHz`, `0.05 MHz` step, `NumberMode.BOX`) on every declared F500 / F500N `Audio Source` device (`*#16*10S*#6*0*<kHz>##` or `*#22*2#S*#5*1*<10kHz>##`). |

### `media_player` (WHO 16 / 22)

| Function | Status | Notes |
| :--- | :---: | :--- |
| On / off, volume, mute, source 1–4 | ✅ | Per zone / amplifier. |
| FM radio tuner sources (F500 / F500N) | ✅ | Power on/off, stored presets (`Station 1`–`5` by default, dynamically expanding up to `Station 1`–`15` on `WHO 22`), RDS programme text, custom `tuner_pictures` (`MHz=URL`), Media Browser (`BROWSE_MEDIA`), direct frequency tuning (`myhome.tuner_set_frequency` / `play_media`), hardware seek (`myhome.tuner_seek_up` / `tuner_seek_down`), and preset storage (`myhome.tuner_store_station`). |
| Streaming (play, pause, next, previous) | ✅ | Only when decoders are mapped in the options flow: the zone becomes a Music Assistant / Spotify target and routes the matrix to a free decoder. |
| Media metadata | 👁️ | Mirrored from the decoder while a stream is active, or decoded from live FM RDS on tuner sources. |
| Speaker groups | ✅ | Home Assistant `join` / `unjoin`, optional `auto_join_streaming`, and Music Assistant sync groups: rooms of a group share one decoder and one source (Environment `0` zones `01`–`09` omit `GROUPING`). See [Multi-Room Audio Grouping](media_player.md#multi-room-audio-grouping-music-assistant-home-assistant). |

### Device triggers (WHO 15 / 25)

| Trigger | CEN (`WHO = 15`) | CEN+ (`WHO = 25`) |
| :--- | :---: | :---: |
| Short press (`pushbutton_short_press`) | ✅ | ✅ |
| Short release (`pushbutton_short_release`) | ✅ | ❌ |
| Long press (`pushbutton_long_press`) | ✅ | ✅ |
| Long press repeat while held (`pushbutton_long_press_repeat`) | ❌ | ✅ |
| Long release (`pushbutton_long_release`) | ✅ | ✅ |
| Rotary dial clockwise / counter-clockwise, slow & fast (`rotary_cw_slow`, `rotary_cw_fast`, `rotary_ccw_slow`, `rotary_ccw_fast`) | ❌ | ✅ |

Buttons `0`–`31` (`button_0`..`button_31`) per device for both CEN and CEN+; every trigger carries the gateway MAC so multi-gateway plants do not cross-fire.

## Services

All services are documented in the [Services Reference](services.md): `send_message`, `turn_on_timed`, `sync_time`, `start_sending_instant_power`, `stop_sending_instant_power`, `sweep_bus`, `calibrate_cover`, `stop_cover_calibration`, `set_cover_travel_time`, `reset_cover_travel_time`, `tuner_seek_up`, `tuner_seek_down`, `tuner_set_frequency`, `tuner_store_station`, `send_scenario_plus_command`, `cancel_cover_request`, `acknowledge_cover_fault`.

## Diagnostics and tooling

| Function | Status | Notes |
| :--- | :---: | :--- |
| Config-entry diagnostics download | ✅ | Gateway identity, profile, queue depth, bus-monitor ring buffer; credentials redacted. |
| Bus monitor Lovelace card | ✅ | Live trace, sweep, export, raw send (administrators only). |
| Repair issues | ✅ | Gateway model contradicted by the WHO 13 device type; password rejected (native reauth). |
| Gateway diagnostic entities (latency, reconnects) | ❌ | Not exposed as entities; use the diagnostics download and the card. |
