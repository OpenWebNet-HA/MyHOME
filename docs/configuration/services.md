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
