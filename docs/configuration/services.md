# Integration Services Reference

This document provides a comprehensive reference for all custom services registered by the **MyHOME** integration in Home Assistant.

---

## 📋 Services Summary

| Service | Target | Description |
| :--- | :--- | :--- |
| [`myhome.send_message`](#myhomesend_message) | Gateway | Send an arbitrary, validated OpenWebNet frame to the SCS bus. |
| [`myhome.turn_on_timed`](#myhometurn_on_timed) | `light`, `switch` | Turn on an actuator with a hardware-offloaded SCS timer that turns off automatically even if Home Assistant reboots. |
| [`myhome.sync_time`](#myhomesync_time) | Gateway | Synchronize the gateway internal clock with Home Assistant's local time. |
| [`myhome.start_sending_instant_power`](#myhomestart_sending_instant_power) | `sensor` | Request a temporary continuous stream of instant power readings from an energy meter. |
| [`myhome.sweep_bus`](#myhomesweep_bus) | Gateway | Actively poll status across all subsystems to populate diagnostic buffers. |
| [`myhome.calibrate_cover`](#myhomecalibrate_cover) | `cover` | Measure a timed cover's up and down travel times on the bus and store them. |
| [`myhome.stop_cover_calibration`](#myhomestop_cover_calibration) | Gateway | Stop the running calibration and cancel queued ones. |
| [`myhome.set_cover_travel_time`](#myhomeset_cover_travel_time) | `cover` | Store stopwatch-measured travel times without driving the cover. |
| [`myhome.reset_cover_travel_time`](#myhomereset_cover_travel_time) | `cover` | Forget measured / manual travel times; back to YAML or the default. |

---

## 1. `myhome.send_message`

Sends an arbitrary, valid OpenWebNet message through the gateway command session. The integration validates syntax, dispatches the frame, and logs the transaction to the Bus Monitor.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `gateway` | string | **Yes** | The MAC address of the target gateway. | `"00:03:50:20:00:01"` |
| `message` | string | **Yes** | Valid OpenWebNet frame ending with `##`. | `"*1*0*0##"` |

### Example YAML Call
```yaml
action: myhome.send_message
data:
  gateway: "00:03:50:20:00:01"
  message: "*1*0*0##" # General turn off all lights
```

---

## 2. `myhome.turn_on_timed`

Turns on a light or switch with a **hardware-offloaded SCS timer**. 

> [!TIP]
> **Why use this service?** Standard Home Assistant timers (e.g. `delay: 00:02:00` followed by `light.turn_off`) will fail if Home Assistant restarts or crashes during the delay. `myhome.turn_on_timed` programs the hardware actuator itself to count down and power off autonomously on the physical bus.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `duration` | float | No | Total duration in seconds (0.5 to 918,000s). | `120` |
| `hours` | integer | No | Hours component (0–255). | `0` |
| `minutes` | integer | No | Minutes component (0–59). | `5` |
| `seconds` | float | No | Seconds component (0–59). | `30` |
| `brightness` | integer | No | Brightness level (1–255) for dimmable lights. | `200` |
| `brightness_pct`| integer | No | Brightness percentage (1–100%) for dimmable lights. | `80` |

*(Note: You can specify `duration` directly, or specify custom `hours`/`minutes`/`seconds`).*

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

Synchronizes the gateway's real-time clock (RTC) with Home Assistant's local time using `WHO = 13` dimension frames. This ensures scheduled events programmed directly inside physical gateways (e.g. MH200N/MH202 schedules) run in lockstep with real time.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `gateway` | string | No | Target gateway MAC address (defaults to all gateways). | `"00:03:50:20:00:01"` |

### Example YAML Call
```yaml
action: myhome.sync_time
data:
  gateway: "00:03:50:20:00:01"
```

---

## 4. `myhome.start_sending_instant_power`

By default, MyHOME energy meters (F520, F521, F522, F523) transmit energy readings periodically to conserve bus bandwidth. Calling this service causes the meter to continuously stream high-frequency instant power updates for a defined duration.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `entity_id` | string | **Yes** | The power sensor entity ID. | `"sensor.general_power"` |
| `duration` | integer | **Yes** | Duration in seconds to keep streaming. | `60` |

### Example YAML Call
```yaml
action: myhome.start_sending_instant_power
data:
  entity_id: "sensor.heat_pump_power"
  duration: 120
```

---

## 5. `myhome.sweep_bus`

Actively queries status across all configured subsystems (lighting, automation, thermoregulation, and gateway diagnostics). It is used to refresh entity states and populate the in-band **Bus Monitor** with fresh data for troubleshooting.

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `gateway` | string | No | Target gateway MAC address (defaults to all gateways). | `"00:03:50:20:00:01"` |

### Example YAML Call
```yaml
action: myhome.sweep_bus
```

---

## 6. `myhome.calibrate_cover`

Measures a timed cover's travel times **on the bus** and stores them, replacing the guessed `travel_time`. The cover is driven fully **up** (so its position is known), then fully **down** (timed), then fully **up** again (timed). Covers of one gateway are calibrated **one at a time** — a single-session gateway cannot drive two motors reliably and overlapping runs would confuse the timing. The shutter moves for about three full travels; do not run it while the shutter must stay put.

The measured values are the actuator's run times (motor start → actuator stop status). They equal the physical travel when the installer calibrated the actuator's run-time parameter, which is the usual case. An actuator left at its factory **60 s run-time limit** stops itself, not at the end stop: such a run ends at about 61 s and is **refused** (`failed`, nothing stored) as soon as it ends, whoever called the service. For that shutter, stop a run by hand (wall switch or the cover's Stop) once it reaches the end stop, read `last_run_seconds` from the cover's attributes and store it with `myhome.set_cover_travel_time`.

The guard catches **only that factory cutoff** (a run ending in the 59–65 s window). An actuator whose run-time parameter the installer set longer than the physical travel — 30 s on a 14 s shutter, say — stops itself at 30 s just the same, and that 30 s is stored as the travel time: nothing on the bus tells it apart from a real end stop, only a stopwatch can. If the stored time is longer than the shutter visibly takes to move, time a run stopped by hand (`last_run_seconds`) or set `travel_time` manually with `myhome.set_cover_travel_time`.

Results are stored in the config entry options (`cover_travel_times`), survive restarts and reinstalls, apply to discovered covers without any YAML, and show up as entity attributes: `travel_time_down`, `travel_time_up`, `calibration_source` (`measured` / `manual` / `yaml` / `default`), `calibrated_at`. Every run of a timed cover is measured the same way, calibration or not: `motion_started_at` is the motor-start anchor of the current run and `last_run_seconds` / `last_run_direction` / `last_run_ended_at` describe the last completed run (motor start → stop frame written or actuator stop status), so a run stopped by hand at the end stop gives the physical travel without the calibration service. Advanced covers (which report their position) are refused. Progress is published on the event bus as `myhome_cover_calibration` (`phase`: `start`, `run`, `done`, `failed`).

The same action sits behind the **Calibrate travel time** button on every timed cover's device page, and the **Calibrate all covers** button on the gateway device.

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

## 7. `myhome.stop_cover_calibration`

Stops the calibration that is running and cancels every cover still queued behind it. The moving cover receives a stop command, its calibration event reports `phase: failed` with *Calibration stopped by user*, and nothing is stored. Without a `gateway` every gateway's queue is cleared. Also available as the **Stop** button in the card's Covers panel and as an entity service on any cover (targets that cover's gateway).

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `gateway` | string | No | Gateway MAC address; all gateways when omitted. | `00:03:50:20:00:01` |

### Example YAML Call
```yaml
action: myhome.stop_cover_calibration
```

---

## 8. `myhome.set_cover_travel_time`

Stores the physical travel times of a timed cover **by hand** — the manual alternative to `calibrate_cover` for gateways that cannot calibrate reliably (MH200 / MH200N single-session pacing, or actuators with the 60 s safety cut-off). Measure the closing and opening runs with a stopwatch (the card's Covers panel has one built in) and pass them here. `travel_time` fills whichever direction has no explicit value. Values must lie between 1 s and 180 s; anything else is rejected before the entity is touched. The result is stored exactly like a measured calibration (`calibration_source: manual`).

### Fields
| Parameter | Type | Required | Description | Example |
| :--- | :---: | :---: | :--- | :--- |
| `entity_id` | target | Yes | One or more MyHOME timed cover entities. | `cover.bedroom_shutter` |
| `travel_time` | float | No* | Seconds for a full travel, both directions. | `24.5` |
| `travel_time_down` | float | No* | Seconds for a full closing run. | `24.5` |
| `travel_time_up` | float | No* | Seconds for a full opening run. | `26.0` |
| `copied_from` | entity id | No | The cover the times were taken from; `calibration_source` becomes `copied` and `copied_from` is exposed as an attribute. | `cover.living_room_west` |

\* at least one of the time fields is required.

### Example YAML Call
```yaml
action: myhome.set_cover_travel_time
target:
  entity_id: cover.bedroom_shutter
data:
  travel_time_down: 24.5
  travel_time_up: 26.0
```

---

## 9. `myhome.reset_cover_travel_time`

Forgets the measured or manually set travel times of a timed cover. The cover returns to the `travel_time` from `myhome.yaml` when one is configured, otherwise to the 25 s default, and `calibration_source` reports `yaml` / `default` again.

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
