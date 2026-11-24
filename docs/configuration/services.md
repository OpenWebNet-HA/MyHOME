# Integration Services Reference

This document provides a comprehensive reference for all custom services registered by the **MyHOME** integration in Home Assistant.

---

## 📋 Services Summary

<!-- SERVICES_TABLE_START -->
| Service | Target | Description |
| :--- | :--- | :--- |
| [`myhome.send_message`](#1-myhomesend_message) | Gateway | Send an arbitrary (but valid) OpenWebNet message to the gateway. |
| [`myhome.turn_on_timed`](#2-myhometurn_on_timed) | `light`, `switch` | Turn on a light or switch with a hardware-offloaded SCS bus timer that turns off automatically even if Home Assistant restarts. |
| [`myhome.sync_time`](#3-myhomesync_time) | Gateway | Synchronize gateway's time to HA local time. |
| [`myhome.start_sending_instant_power`](#4-myhomestart_sending_instant_power) | `sensor` | Get automatic instant power draw updates for a sensor. |
| [`myhome.stop_sending_instant_power`](#5-myhomestop_sending_instant_power) | `sensor` | Stop automatic instant power draw updates for a sensor. |
| [`myhome.sweep_bus`](#6-myhomesweep_bus) | Gateway | Triggers an active read-only status query across all bus subsystems (lighting, covers, thermoregulation, clock, and gateway diagnostics) to populate the diagnostic bus monitor ring buffer. |
| [`myhome.calibrate_cover`](#7-myhomecalibrate_cover) | `cover` | Measures a timed cover's up and down travel times on the SCS bus and stores them. The cover is driven fully up, then fully down (timed), then fully up again (timed). Covers are calibrated one at a time per gateway. Do not run while the shutter must stay put. |
| [`myhome.stop_cover_calibration`](#8-myhomestop_cover_calibration) | Gateway | Stops the running travel-time calibration and cancels the queued ones. The moving cover is stopped; nothing is stored. Without a gateway every gateway is stopped. |
| [`myhome.set_cover_travel_time`](#9-myhomeset_cover_travel_time) | `cover` | Stores the physical travel times of a timed cover by hand (measured with a stopwatch) instead of calibrating on the bus. Values are kept in the config entry, survive restarts and apply to discovered covers without YAML. |
| [`myhome.reset_cover_travel_time`](#10-myhomereset_cover_travel_time) | `cover` | Forgets the measured or manually set travel times of a timed cover and returns to the myhome.yaml travel_time or the 25 s default. |
| [`myhome.tuner_seek_up`](#11-myhometuner_seek_up) | `media_player` | Seek forward to the next receivable FM radio frequency on an F500 tuner source. |
| [`myhome.tuner_seek_down`](#12-myhometuner_seek_down) | `media_player` | Seek backward to the previous receivable FM radio frequency on an F500 tuner source. |
| [`myhome.send_scenario_plus_command`](#13-myhomesend_scenario_plus_command) | Gateway | Send a WHO 25 scenario plus command (on, off, increase, decrease, stop) to an addressed scenario plus object. |
| [`myhome.cancel_cover_request`](#14-myhomecancel_cover_request) | `cover` | Cancels a pending approval request or a running pre-warning of an impulse gate or garage door. Cancelling is always safe; nothing moves. |
| [`myhome.acknowledge_cover_fault`](#15-myhomeacknowledge_cover_fault) | `cover` | Clears the fault latched when a gate or garage door did not reach the expected state after a pulse (possible obstacle or reversal). Only an allowed user who is at home may acknowledge it; until then closing from Home Assistant stays blocked. |
| [`myhome.tuner_set_frequency`](#16-myhometuner_set_frequency) | `media_player` | Tune directly to a manual FM radio frequency (87.50–108.00 MHz) on an F500 / F500N tuner source. |
| [`myhome.tuner_store_station`](#17-myhometuner_store_station) | `media_player` | Memorise the currently tuned FM radio frequency into a preset station slot (1–15) on an F500 / F500N tuner source. |
<!-- SERVICES_TABLE_END -->

---

## 1. `myhome.send_message`

Sends an arbitrary, valid OpenWebNet message through the gateway command session. The integration validates syntax, dispatches the frame, and logs the transaction to the Bus Monitor.

> [!WARNING]
> **`WHO = 4` Dimension 22 Firmware Crash Guard (`is_refused_dimension_22`)**:
> Non-split `WHO = 4` Dimension 22 frames whose `WHERE` carries no actuator token (such as a zone `*#4*1*22##`, the general address `*#4*0*22##`, or the central unit `*#4*#0*22##`) kill the thermostat daemon on Linux IP gateways (`F461`, `F454`, `MH200N`, etc.) and are **strictly refused** before reaching the socket. Published split-unit forms (`*#4*3#Z#N*22##` and `*#4*3#Z#N*#22*MOD*SP*VEL*SWING##`) are safe across all twelve emulated gateway firmwares and are permitted.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `gateway` | string | No | The MAC address of the target gateway (defaults to the `is_primary` gateway, or the first loaded gateway if none is primary). Unknown keys such as `gw_mac` are rejected. | `"00:03:50:20:00:01"` |
| `message` | string | **Yes** | Valid OpenWebNet frame ending with `##`. | `"*1*0*0##"` |

> [!NOTE]
> **Nothing sent means an error, not a log line.** An empty `message` is refused by the service schema. The action raises `ServiceValidationError` and queues nothing when `message` is not a frame (characters other than `0-9`, `*` and `#`, no leading `*` or trailing `##`, or not parsable: `send_message_invalid_frame`), when it is a refused destructive/crash frame (`WHO = 4` Dimension 22 crash forms, `WHO = 18` Dimension 52 crash forms, or bare `WHO = 22` power frames: `send_message_refused_frame`), when `gateway` matches no loaded gateway (`gateway_not_found`), or when no MyHOME gateway is set up (`no_gateway_loaded`). A frame that was queued and that the gateway later answers with `NACK` is still only logged.

### Example YAML Call
```yaml
action: myhome.send_message
data:
  gateway: "00:03:50:20:00:01"
  message: "*1*0*0##" # General turn off all lights
```

---

## 2. `myhome.turn_on_timed`

Turns on an individual `WHO = 1` light or switch actuator with a **hardware-offloaded SCS timer**.

> [!TIP]
> **Why use this service?** Standard Home Assistant timers (e.g. `delay: 00:02:00` followed by `light.turn_off`) will fail if Home Assistant restarts or crashes during the delay. `myhome.turn_on_timed` programs the hardware actuator itself to count down and power off autonomously on the physical bus.

> [!NOTE]
> **Supported Entities**: `myhome.turn_on_timed` applies only to individual point-to-point `WHO = 1` light and switch actuators (`0.5–918,000` seconds via preset `WHAT` codes or `Dimension 2` `*#1*WHERE*#2*H*M*S##`). Calling it on an SCS group light (`#G`, `group_no_timer`), a `WHO = 4` probe-less thermo switch (`thermo_switch_no_timer`), or a `WHO = 9/25` **Invert state** configuration switch (`invert_switch_no_timer`) raises a `ServiceValidationError`.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `duration` | float | No | Total duration in seconds (`0.5` to `918,000` s — `0.5` s impulse preset up to `255` hours via `Dimension 2`). | `120` |
| `hours` | integer | No | Hours component (`0`–`255`). | `0` |
| `minutes` | integer | No | Minutes component (`0`–`59`). | `5` |
| `seconds` | float | No | Seconds component (`0`–`59`). | `30` |
| `brightness` | integer | No | Brightness level (`1`–`255`) for dimmable lights. | `200` |
| `brightness_pct`| integer | No | Brightness percentage (`1`–`100%`) for dimmable lights. | `80` |

*(Note: Specify `duration` directly, or specify custom `hours`/`minutes`/`seconds`.)*

### Example YAML Call
```yaml
action: myhome.turn_on_timed
target:
  entity_id: light.hallway_staircase
data:
  minutes: 3
  seconds: 30
  brightness_pct: 70
```

---

## 3. `myhome.sync_time`

Asks the gateway to set its real-time clock (RTC) to Home Assistant's local time: it queues the `WHO = 13` dimension frames on the named gateway, or on every primary gateway when `gateway` is omitted. It does not wait for the gateway's answer and does not verify the clock afterwards. The clock matters for scheduled events programmed directly inside physical gateways (e.g. MH200N/MH202 schedules).

> [!NOTE]
> - **Shared-Bus Topology**: When `gateway` is omitted, follower (`secondary` / `standby`) gateways on a shared SCS bus are automatically skipped so only primary gateways broadcast clock updates.
> - **Unknown or Missing Gateway**: A `gateway` MAC that matches no loaded gateway raises `ServiceValidationError` (`gateway_not_found`), and calling the action while no MyHOME gateway is set up raises `no_gateway_loaded`; no frame is queued in either case.
> - **Whole-Hour UTC Offset Requirement**: Because the OpenWebNet `WHO = 13` timezone offset field (`0HH` / `1HH`) only encodes whole hours, calling `myhome.sync_time` when Home Assistant is configured in a fractional-hour timezone (such as `Asia/Kolkata` `+05:30`) raises `ServiceValidationError` (`sync_time_unsupported_offset`) rather than transmitting a truncated offset.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `gateway` | string | No | Target gateway MAC address (defaults to all primary gateways). | `"00:03:50:20:00:01"` |

### Example YAML Call
```yaml
action: myhome.sync_time
data:
  gateway: "00:03:50:20:00:01"
```

---

## 4. `myhome.start_sending_instant_power`

By default, MyHOME energy meters (`F520`, `F521`, `F522`, `F523`, `3522`) transmit energy readings periodically to conserve bus bandwidth. Calling this entity service instructs the targeted power sensor's meter to stream high-frequency instant power updates (`Dimension 113`) for a bounded window (`1–255` minutes, default `65`). To stop streaming early, call [`myhome.stop_sending_instant_power`](#5-myhomestop_sending_instant_power) (`duration: 0` is rejected by schema validation).

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `entity_id` | target | **Yes** | Target MyHOME `sensor` entity (`device_class: power`). | `sensor.general_power` |
| `duration` | integer | No | Duration in minutes (`1`–`255`, default `65`) to keep streaming instant power; call `myhome.stop_sending_instant_power` to stop early. | `65` |

### Example YAML Call
```yaml
action: myhome.start_sending_instant_power
target:
  entity_id: sensor.heat_pump_power
data:
  duration: 65
```

---

## 5. `myhome.stop_sending_instant_power`

Stops active high-frequency instant power streaming from an energy meter, returning the meter to its default periodic transmission mode.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `entity_id` | target | **Yes** | Target MyHOME `sensor` entity (`device_class: power`). | `sensor.general_power` |

### Example YAML Call
```yaml
action: myhome.stop_sending_instant_power
target:
  entity_id: sensor.heat_pump_power
```

---

## 6. `myhome.sweep_bus`

Actively queries status across all configured subsystems (lighting, automation, thermoregulation, and gateway diagnostics). It is used to refresh entity states and populate the in-band **Bus Monitor** with fresh data for troubleshooting.

> [!NOTE]
> With the default `force: false` the service sends the same general requests as the startup sweep, so on the gateways whose firmware drops `*#4*0##` before the bus (F450, F454, F459, F460, F461, H4684, MH200, MH201, MH202 and MyHomeServer1) it sends **no WHO 4 frame at all**: the general request is left out and the service triggers no per-zone `*#4*<WHERE>##` or `*#4*<WHERE>*0##` poll (those belong to the climate entities' own polling, see [climate](climate.md)). `*#16*0*5##` is left out the same way on F450, F459, F460, F461, MH202 and MyHomeServer1. With `force: true` the requests are sent, and the firmware still drops them before they reach the bus (a NACK on the command session, nothing on the event session).

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `gateway` | string | No | Target gateway MAC address (defaults to all gateways). | `"00:03:50:20:00:01"` |
| `force` | boolean | No | Force probe across all subsystems regardless of gateway profile restrictions, including the general requests the gateway firmware drops (`*#4*0##`, `*#16*0*5##` on most models; see [runtime behaviour](runtime_behaviour.md)). Defaults to `false`, which sends the same general requests as the startup sweep. | `true` |

### Example YAML Call
```yaml
action: myhome.sweep_bus
```

---

## 7. `myhome.calibrate_cover`

Measures a timed cover's travel times **on the bus** and stores them, replacing the guessed `travel_time`. The cover is driven fully **up** (so its position is known), then fully **down** (timed), then fully **up** again (timed). Covers of one gateway are calibrated **one at a time** — a single-session gateway cannot drive two motors reliably and overlapping runs would confuse the timing. The shutter moves for about three full travels; do not run it while the shutter must stay put.

The measured values are the actuator's run times (motor start → actuator stop status). They equal the physical travel when the installer calibrated the actuator's run-time parameter to closely match the real travel (e.g. by setting the `M` configurator or virtual configuration in MyHOME_Suite). An actuator left at its factory default **60 s run-time limit** (reported with `M=0`, `last_run_seconds` ~60–61 s) stops itself, not at the end stop: such a run ends at about 60–61 s and is **refused** (`failed`, nothing stored) as soon as it ends, whoever called the service. For that shutter, you can either adjust the actuator's timed stop setting in MyHOME_Suite (to a couple of seconds above the real travel when the shutter takes under ~58 s; above 60 s only whole-minute steps exist), or without touching the actuator, stop a run by hand (wall switch or the cover's Stop) once it reaches the end stop, read `last_run_seconds` from the cover's attributes and store it with `myhome.set_cover_travel_time`.

The guard catches **only the configurator's whole-minute limits**: a run ending from 1 s before a limit to 5 s per minute after it — 59–65 s at 60 s, 119–130 s at 120 s (the `M=1` setting a 60–86 s shutter needs on an `F411/2` or `F411/4`, and ~125.5–127.1 s observed on `4672` actuators in #433), 179–195 s at 180 s, and so on up to 10 min. The failed event then carries `actuator_limit` (the limit in seconds). Limits above 3 min (and a 3 min stop arriving after the 180 s wait) fail first on the 180 s wait for the stop status. An actuator whose run-time parameter the installer set significantly longer than the physical travel stops itself when its timer expires, and that duration is stored as the travel time: nothing on the bus tells it apart from a real end stop, only a stopwatch can. If the stored time is longer than the shutter visibly takes to move, time a run stopped by hand (`last_run_seconds`) or set `travel_time` manually with `myhome.set_cover_travel_time`.

Results are stored in the config entry options (`cover_travel_times`), survive restarts and reinstalls, apply to discovered covers without any YAML, and show up as entity attributes: `travel_time_down`, `travel_time_up`, `calibration_source` (`measured` / `manual` / `copied` / `yaml` / `default`), `calibrated_at` (and `copied_from` when copied). Every run of a timed cover is measured the same way, calibration or not: `motion_started_at` is the motor-start anchor of the current run and `last_run_seconds` / `last_run_direction` / `last_run_ended_at` describe the last completed run (motor start → stop frame written, re-anchored to the stop status when relayed, or actuator stop status), so a run stopped by hand at the end stop gives the physical travel without the calibration service. Advanced covers (which report their position) are refused. Progress is published on the event bus as `myhome_cover_calibration` (`phase`: `queued`, `start`, `run`, `done`, `failed`).

The same action sits behind the **Calibrate travel time** button on every timed cover's device page (`button.<cover name>_calibrate_travel_time`), and the **Calibrate all covers** button on the gateway device (`button.<gateway name>_calibrate_all_covers`), which targets every enabled cover of that gateway at once and lets the lock serialise them.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `entity_id` | target | Yes | One or more MyHOME cover entities (`all` for every cover). | `cover.bedroom_shutter` |

### Example YAML Call
```yaml
action: myhome.calibrate_cover
target:
  entity_id:
    - cover.bedroom_shutter
    - cover.kitchen_shutter
```

---

## 8. `myhome.stop_cover_calibration`

Stops the calibration that is running and cancels every cover still queued behind it. The moving cover receives a stop command, its calibration event reports `phase: failed` with *Calibration stopped by user*, and nothing is stored. Without a `gateway` every gateway's queue is cleared. Also available as an entity service on any cover (targets that cover's gateway).

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `gateway` | string | No | Gateway MAC address; all gateways when omitted. | `00:03:50:20:00:01` |

### Example YAML Call
```yaml
action: myhome.stop_cover_calibration
```

---

## 9. `myhome.set_cover_travel_time`

Stores the physical travel times of a timed cover **by hand** — the manual alternative to `calibrate_cover` for gateways that cannot calibrate reliably (MH200 / MH200N single-session pacing, or actuators at a configurator run-time limit such as 60 s or 120 s). Measure the closing and opening runs with a stopwatch and pass them here. `travel_time` fills whichever direction has no explicit value; note that `travel_time_down` on its own also sets the up time (the two are assumed equal unless `travel_time_up` is given), whereas `travel_time_up` on its own leaves the stored down time untouched — pass both when you only want to change one. Travel times must lie between 1 s and 300 s. The motor start-up floor (`min_motor_pulse`) can also be configured here (0.0 to 5.0 s, 0.0 disables). When setting travel times, the result is stored like a measured calibration (`calibration_source: manual`); when only `min_motor_pulse` is specified, existing travel times, source, and calibration timestamps remain untouched.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `entity_id` | target | Yes | One or more MyHOME timed cover entities. | `cover.bedroom_shutter` |
| `travel_time` | float | No* | Seconds for a full travel, both directions. | `24.5` |
| `travel_time_down` | float | No* | Seconds for a full closing run. | `24.5` |
| `travel_time_up` | float | No* | Seconds for a full opening run. | `26.0` |
| `slat_time` | float | No* | Seconds a Venetian blind (`slat_tilt: true`) needs for a full slat rotation, 0.5-10 s. Stored on its own it does not touch the stored travel times, and it also works on covers that report their position. It survives restarts, also for covers declared in `myhome.yaml` (where it wins over the yaml `slat_time`); `reset_cover_travel_time` returns it to the yaml value or the 2 s default. | `2.0` |
| `copied_from` | entity id | No | The cover the times were taken from; `calibration_source` becomes `copied` and `copied_from` is exposed as an attribute. | `cover.living_room_west` |
| `min_motor_pulse` | float | No | Minimum duration in seconds (0.0–5.0) of a motor pulse to overcome mechanical deadband and brake inertia. Defaults to 0.4 s (0.0 disables). | `0.4` |

\* at least one of the time fields or `min_motor_pulse` is required.

### Example YAML Call
```yaml
action: myhome.set_cover_travel_time
target:
  entity_id: cover.bedroom_shutter
data:
  travel_time_down: 24.5
  travel_time_up: 26.0
  min_motor_pulse: 0.3
```

---

## 10. `myhome.reset_cover_travel_time`

Forgets the measured or manually set travel times of a timed cover. The cover returns to the `travel_time` from `myhome.yaml` when one is configured, otherwise to the 25 s default, and `calibration_source` reports `yaml` / `default` again. A stored `slat_time` returns to the yaml value or the 2 s default as well. On a position-reporting Venetian blind (`advanced_shutter` and `slat_tilt`) the service only resets the `slat_time`; other position-reporting covers refuse it.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `entity_id` | target | Yes | One or more MyHOME timed cover entities. | `cover.bedroom_shutter` |

### Example YAML Call
```yaml
action: myhome.reset_cover_travel_time
target:
  entity_id: cover.bedroom_shutter
```

---

## 11. `myhome.tuner_seek_up`

Commands an F500 / F500N sound source tuner entity (`WHERE` 101–109) to seek forward (`*16*5000*10S##`) to the next receivable FM radio frequency on the SCS bus.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `entity_id` | target | Yes | Target MyHOME tuner source `media_player` entity. | `media_player.audio_source_1` |

### Example YAML Call
```yaml
action: myhome.tuner_seek_up
target:
  entity_id: media_player.audio_source_1
```

---

## 12. `myhome.tuner_seek_down`

Commands an F500 / F500N sound source tuner entity (`WHERE` 101–109) to seek backward (`*16*5100*10S##`) to the previous receivable FM radio frequency on the SCS bus.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `entity_id` | target | Yes | Target MyHOME tuner source `media_player` entity. | `media_player.audio_source_1` |

### Example YAML Call
```yaml
action: myhome.tuner_seek_down
target:
  entity_id: media_player.audio_source_1
```

---

## 13. `myhome.send_scenario_plus_command`

Sends a WHO 25 scenario plus command (`on`, `off`, `increase`, `decrease`, or `stop`) to an addressed scenario plus object on the SCS bus.

> [!NOTE]
> For `increase` and `decrease` actions, step and speed parameters are fixed to standard Legrand protocol defaults (`#0#5`, as confirmed by physical gateway traces); they are not user-configurable in this service.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `gateway` | string | No | The MAC address of the target gateway (defaults to primary). | `"00:03:50:20:00:01"` |
| `where` | string | **Yes** | The scenario plus object address on the bus. | `"11"` |
| `action` | string | **Yes** | The scenario plus action (`on`, `off`, `increase`, `decrease`, `stop`). Step and speed for `increase`/`decrease` are fixed to standard defaults (`#0#5`). | `"on"` |

### Example YAML Call
```yaml
action: myhome.send_scenario_plus_command
data:
  where: "11"
  action: "on"
```

---

## 14. `myhome.cancel_cover_request`

Cancels an active or pending approval request or a running 5-second pre-warning countdown of an impulse gate or garage door (`type: impulse_relay`). Cancelling is always safe: no bus impulse is sent, no relay energizes, and nothing moves. Only meaningful for a cover with an `access:` block; on a cover without one the service raises an error.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `entity_id` | target | Yes | Target MyHOME impulse cover entity. | `cover.garage_door_1` |

### Example YAML Call
```yaml
action: myhome.cancel_cover_request
target:
  entity_id: cover.garage_door_1
```

---

## 15. `myhome.acknowledge_cover_fault`

Clears the safety fault latched when a gate or garage door did not reach its expected state after a pulse (indicating a physical obstacle, safety trip, or reversal). Only an allowed user who is physically at home may clear the fault. Until acknowledged, all remote and automated closing commands from Home Assistant remain strictly blocked. The fault survives a restart or reload of Home Assistant. Only meaningful for a cover with an `access:` block.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `entity_id` | target | Yes | Target MyHOME impulse cover entity currently in fault. | `cover.garage_door_1` |

### Example YAML Call
```yaml
action: myhome.acknowledge_cover_fault
target:
  entity_id: cover.garage_door_1
```

---

## 16. `myhome.tuner_set_frequency`

Tunes an F500 / F500N sound source tuner entity (`WHERE` 101–109 / `WHO 22` `2#S`) directly to an explicit FM radio frequency (`87.50–108.00 MHz`, `0.05 MHz` step), dispatching `*#16*10S*#6*0*<kHz>##` on `WHO 16` gateways or `*#22*2#S*#5*1*<10kHz>##` on `WHO 22`-only gateways (`MyHomeServer1`, `F461`). You can also adjust this directly in the UI via the companion `number.<source>_frequency` entity.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `entity_id` | target | Yes | Target MyHOME tuner source `media_player` entity. | `media_player.audio_source_1` |
| `frequency` | float | **Yes** | Target FM radio frequency in MHz (`87.5`–`108.0`, `0.05` MHz step). | `105.3` |

### Example YAML Call
```yaml
action: myhome.tuner_set_frequency
target:
  entity_id: media_player.audio_source_1
data:
  frequency: 105.3
```

---

## 17. `myhome.tuner_store_station`

Memorises the currently tuned FM radio frequency into a hardware preset slot (`1–5` on F500, up to `1–15` on F500N), dispatching `*#16*10S*#10*<station>##` for slots `1–5` on `WHO 16` gateways (`bt_difson` ceiling) and `*22*33#<station>*2#S##` for extended slots `6–15` on dual-stack gateways or all slots `1–15` on `WHO 22`-only gateways (`WHO 16`-only gateways such as `MH200` reject slots `6–15`).

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `entity_id` | target | Yes | Target MyHOME tuner source `media_player` entity. | `media_player.audio_source_1` |
| `station` | integer | **Yes** | Preset memory slot number (`1`–`5` on `WHO 16`-only gateways, `1`–`15` when `WHO 22` is available). | `5` |

### Example YAML Call
```yaml
action: myhome.tuner_store_station
target:
  entity_id: media_player.audio_source_1
data:
  station: 5
```
