# Supported Functions

What the integration does — and does not — do for each OpenWebNet subsystem (`WHO`) and Home Assistant platform. Hardware and gateway models are listed in the README's [Supported Hardware](../../README.md#%EF%B8%8F-supported-hardware) section; the wire-level conformance corpus is in the [Protocol Conformance Matrix](../protocol_conformance_matrix.md). Things that are deliberately *not* supported, and why, are in [Known Limitations](known_limitations.md).

Legend: ✅ supported · 👁️ read-only · ⚙️ via a service, not an entity · ❌ not supported

## Subsystems

| WHO | Subsystem | Platform(s) | Status | Notes |
| :---: | :--- | :--- | :---: | :--- |
| 0 | Scenarios (scenario modules) | — | ⚙️ | Send `*0*<what>*<where>##` with `myhome.send_message`; no entity is created. |
| 1 | Lighting | `light`, `switch`, `binary_sensor`, `sensor` | ✅ | See the platform table below. |
| 2 | Automation (shutters, blinds) | `cover` | ✅ | Timed and position-reporting actuators. |
| 4 | Thermoregulation | `climate`, `sensor` | ✅ | Zone thermostats, central units 3550 (`#0`) and 4695 (`#0#1`), probes. |
| 5 | Burglar alarm | `alarm_control_panel` | ✅ / 👁️ | Central unit status and arm/disarm; zones are read through the event stream — see limitations. |
| 9 | Auxiliary channels | `binary_sensor` | 👁️ | AUX 1–9 as binary sensors. |
| 13 | Gateway management | — | ⚙️ | Clock sync (`myhome.sync_time`), firmware / model / identity for the device registry and diagnostics. |
| 14 | Actuator lock | `button` | ✅ | Lock / unlock buttons on every light, switch and cover device. |
| 15 | CEN scenario pushbuttons | device triggers | ✅ | Short / long press and release, rotary dials. |
| 16 | Sound system | `media_player` | ✅ | F441 / F441M matrices; streaming proxy for network decoders. |
| 18 | Energy management | `sensor` | ✅ | Instantaneous power, total / daily / monthly energy. |
| 22 | FM tuner (legacy sound system) | — | ❌ | Deferred in RFC #248; the F441 streaming proxy replaces it. |
| 24 | Lighting management (commercial) | — | ❌ | BMNE500 / BMview controllers; out of residential scope. |
| 25 | CEN+ pushbuttons and dry contacts | device triggers, `binary_sensor` | ✅ | Short / long press, release; F482 / 3477 dry contacts. |

## Platforms

### `light` (WHO 1)

| Function | Status | Notes |
| :--- | :---: | :--- |
| On / off | ✅ | Relay and dimmer actuators, including F422 private-bus addresses (`#4#<interface>`). |
| Brightness | ✅ | Dimmers (`dimmable: true` or learned from a level frame). |
| Transitions | ✅ | `software_stepped` (default) or the actuator's native fade — gateway option. |
| Flash | ✅ | |
| Colour temperature (DALI DT8) | ✅ | Dimension 14, 2000–6535 K; learned from the first colour frame. |
| HS / RGB colour | ✅ | Learned from the first colour frame; modes accumulate, they never replace each other. |
| Hardware timer | ✅ | `myhome.turn_on_timed`: the actuator switches off by itself. |
| Group / area / general commands from wall switches | 👁️ | Frames are seen, but entities only update when the actuator reports its own status — see limitations. |

### `switch` (WHO 1)

| Function | Status | Notes |
| :--- | :---: | :--- |
| On / off | ✅ | Device class `switch` or `outlet` from `myhome.yaml`. |
| Hardware timer | ✅ | `myhome.turn_on_timed`. |

### `cover` (WHO 2)

| Function | Status | Notes |
| :--- | :---: | :--- |
| Open / close / stop | ✅ | |
| Position (position-reporting actuators) | ✅ | `advanced_shutter: true`, or learned from a dimension 10 frame. |
| Position (timed actuators) | ✅ | Virtual position from the travel time: `travel_time` in YAML, measured with `myhome.calibrate_cover`, or set by hand with `myhome.set_cover_travel_time`. |
| Tilt | ❌ | Slat commands are parsed by OWNd but not exposed. |
| Echo suppression | ✅ | The gateway's relay of our own command is not mistaken for a keypad press. |

### `climate` (WHO 4)

| Function | Status | Notes |
| :--- | :---: | :--- |
| Current temperature | ✅ | |
| Target temperature | ✅ | Manual set-point. |
| HVAC modes | ✅ | `heat`, `cool`, `auto`, `off`; heating / cooling support per zone from YAML. |
| Fan speed (3-speed fancoil) | ✅ | `fan: true`. |
| Central unit modes | ✅ | Central unit 3550 (`#0`) and 4695 (`#0#1`) as `central: true` zones; seasonal propagation. |
| Weekly programs / scenarios | ❌ | Program selection frames (`*4*11xx*#0##`) can be sent with `myhome.send_message`. |
| Temperature-only probes | 👁️ | `sensor` platform (see below). |

### `alarm_control_panel` (WHO 5)

| Function | Status | Notes |
| :--- | :---: | :--- |
| Arm away / arm home / disarm | ✅ | Central units 3485 / 3486. |
| Trigger (panic) | ✅ | |
| Zone status | 👁️ | The panel follows the central unit and the zone-0 broadcast; individual zones are not entities (limitations). |

### `binary_sensor` (WHO 1 / 9 / 25)

| Function | Status | Notes |
| :--- | :---: | :--- |
| Dry contacts (CEN+, WHO 25) | ✅ | F482 / 3477; device class from YAML. |
| Auxiliary channels (WHO 9) | ✅ | AUX 1–9. |
| Motion / presence (WHO 1) | ✅ | Motion frames of lighting sensors; a timeout restores `off`. |
| Inverted contacts | ✅ | `inverted: true`. |

### `sensor` (WHO 1 / 4 / 18)

| Function | Status | Notes |
| :--- | :---: | :--- |
| Instantaneous power (W) | ✅ | Push-driven; `myhome.start_sending_instant_power` requests a burst of readings. |
| Energy total / today / month (Wh) | ✅ | Today and month are **disabled by default**. |
| Temperature (zones and probes) | ✅ | Zones are polled every 5 min; probes (`WHERE ≥ 100`) are push-driven and only polled when silent. |
| Illuminance (lux) | ✅ | WHO 1 light sensors. |
| Humidity | 👁️ | Shown as the climate entity's `current_humidity` when the zone reports it; no separate sensor. |

### `button` (WHO 14 / 2)

| Function | Status | Notes |
| :--- | :---: | :--- |
| Lock / unlock actuator | ✅ | On every light, switch and cover device (`EntityCategory.CONFIG`). |
| Calibrate travel time | ✅ | On every timed cover device, plus *Calibrate all covers* on the gateway device. |

### `media_player` (WHO 16)

| Function | Status | Notes |
| :--- | :---: | :--- |
| On / off, volume, mute, source 0–4 | ✅ | Per zone / amplifier. |
| Streaming (play, pause, next, previous) | ✅ | Only when decoders are mapped in the options flow: the zone becomes a Music Assistant / Spotify target and routes the matrix to a free decoder. |
| Media metadata | 👁️ | Mirrored from the decoder while a stream is active. |
| Speaker groups | ❌ | Use Home Assistant / Music Assistant grouping on the decoders. |

### Device triggers (WHO 15 / 25)

| Trigger | CEN | CEN+ |
| :--- | :---: | :---: |
| Short press / short release | ✅ | ✅ |
| Long press / long release | ✅ | ✅ |
| Rotary dial clockwise / counter-clockwise, slow and fast | ✅ | ✅ |

Buttons 0–31 per device; every trigger carries the gateway MAC so multi-gateway plants do not cross-fire.

## Services

All services are documented in the [Services Reference](services.md): `send_message`, `turn_on_timed`, `sync_time`, `start_sending_instant_power`, `sweep_bus`, `calibrate_cover`, `stop_cover_calibration`, `set_cover_travel_time`, `reset_cover_travel_time`.

## Diagnostics and tooling

| Function | Status | Notes |
| :--- | :---: | :--- |
| Config-entry diagnostics download | ✅ | Gateway identity, profile, queue depth, bus-monitor ring buffer; credentials redacted. |
| Bus monitor Lovelace card | ✅ | Live trace, sweep, export, raw send (administrators only). |
| Repair issues | ✅ | Gateway model contradicted by the WHO 13 device type; password rejected (native reauth). |
| Gateway diagnostic entities (latency, reconnects) | ❌ | Not exposed as entities; use the diagnostics download and the card. |
