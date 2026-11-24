# Sensors & Energy (WHO = 18, WHO = 4, WHO = 1)

The **MyHOME** integration provides monitoring for electrical energy meters, ambient temperature probes, and light sensors across OpenWebNet **WHO = 18**, **WHO = 4**, and **WHO = 1**.

In v2, setup and management are **100% UI-first**: sensors are automatically discovered from bus telemetry without manual YAML configuration files.

---

## ⚡ Power & Energy Meters (WHO = 18)

The integration natively interfaces with Legrand / BTicino DIN power management actuators:

* **BTicino F520**: Single-phase power and energy meter (`WHERE = 51` through `5255`).
* **BTicino F522 / F523**: Multi-circuit energy and power management modules (`WHERE = 71` through `7255`).

### Auto-Discovered Entities
When an energy meter is detected on the SCS bus, the integration creates entities only for the measurements the meter actually reports:

1. **Instantaneous Power Sensor** (`device_class: power`): Real-time active power consumption in Watts (**W**).
2. **Total Energy Counter** (`device_class: energy`, `state_class: total_increasing`): Cumulative electrical energy consumption in Watt-hours (**Wh**).
3. **Periodic Energy Counters**: Daily and monthly energy sub-counters (created disabled by default in the entity registry; enable them in Home Assistant if needed).

### Home Assistant Energy Dashboard Integration
Because energy entities implement standard `state_class: total_increasing` and `device_class: energy`:

1. Navigate to **Settings → Dashboards → Energy**.
2. Under **Electricity Grid → Add Consumption**, select your discovered MyHOME total energy sensor (e.g. `sensor.total_power_energy`).
3. Home Assistant automatically generates hourly, daily, and monthly solar/grid tracking graphs.

### Live High-Frequency Power Streaming
To instruct a power meter to stream high-frequency instantaneous power updates (`Dimension 113`) to Home Assistant, call `myhome.start_sending_instant_power` targeting the power sensor entity (optional `duration`: `1–255` minutes, default `65`), and call `myhome.stop_sending_instant_power` to stop streaming early:

```yaml
action: myhome.start_sending_instant_power
target:
  entity_id: sensor.general_power
data:
  duration: 65   # Optional: 1–255 minutes (default: 65)
```

```yaml
action: myhome.stop_sending_instant_power
target:
  entity_id: sensor.general_power
```

> [!TIP]
> **Projecting Smart Meters or Solar Inverters onto the SCS Bus**:
> To publish non-MyHOME power and energy measurements (such as Shelly EM, SolarEdge, Enphase, or utility smart meters) onto SCS meter addresses for display on wall touchscreens, see the [Expose to Bus Guide](expose.md).

---

## 🌡️ Temperature, Humidity & 0–10 V Proportional Actuator Output Sensors (WHO = 4)

Temperature, humidity, and actuator-level telemetry on `WHO = 4` are exposed as standard `sensor` entities:

* **Standalone & Secondary Probes (`WHERE ≥ 100`, i.e. `0101`–`0999` / `101`–`899`, or `Dimension 15`)**: Auto-discovered passively from SCS bus traffic (including battery-powered wireless probes such as BTicino `3455` behind `L4577` radio interfaces, and outdoor/slave probes `3475`/`3476`). To prevent a single bit-flipped zone frame on the 2-wire SCS bus (for example zone `1` corrupted to `129`) from registering a permanent phantom probe sensor, an unconfigured bus probe is created only on its **second independent reading** — either a distinct frame/value or a repeated reading received at least `5 s` later and within `1 h` (`3600 s`) — with **zero active polling** (`#308`, `#938`; real probes push periodically on their own and NACK explicit status requests). Probes configured in `/config/myhome.yaml` or already present in the Home Assistant entity registry are created immediately at startup.
* **Climate Zone Companion Temperature Sensors (`WHERE = 1..99`)**: Every confirmed non-central `WHO = 4` climate zone automatically exposes an enabled, strictly passive (`should_poll = False`) `sensor.<zone>_temperature` entity (`device_class: temperature`, `state_class: measurement`, native unit `°C`, `RestoreSensor` native-unit preservation) attached to the same Home Assistant device as its `climate` entity — unlocking Home Assistant Long-Term Statistics without adding any bus polling. Central units (`#0`, `#0#1..#0#4`), probe-less thermo switches, exposed virtual probes, ignored addresses, and unconfirmed zones are excluded. Existing `sensor:` entries for zones `1..99` in `/config/myhome.yaml` continue to take precedence without duplicates; removing a legacy `where: "<Z>"` YAML temperature entry seamlessly transfers the same `{mac}-4-<Z>-temperature` entity ID and its Long-Term Statistics history to the passive companion sensor.
* **Climate Zone Companion Humidity Sensors (`Dimension 60`)**: Dynamically created as an enabled, passive (`should_poll = False`) `sensor.<zone>_humidity` entity (`device_class: humidity`, `state_class: measurement`, unit `%`) on the zone's Home Assistant device only when the zone reports `Dimension 60` humidity (`*#4*<WHERE>*60*<HH>##`), restores prior humidity state, or declares `humidity: true` in `myhome.yaml`.
* **0–10 V Proportional Actuator Output Level (`*#4*Z#N*20*100..200##`, `1 <= Z <= 99`, `1 <= N <= 9`)**: Whenever an explicit zone actuator address `Z#N` (`1 <= Z <= 99`, `1 <= N <= 9`) reports a proportional 0–10 V output code (`Dimension 20` values `100..200` → `0..100 %`, e.g. Sabiana 0–10 V fan coils or modulating valves), the integration automatically creates an enabled `sensor` entity (`MyHOMEActuatorOutputSensor` — `Actuator <N> output level`, unit `%`, `state_class: measurement`, `mdi:gauge`) attached to the same Home Assistant Device as the zone's `climate` entity — with `generate_events` disabled. Bare zone (`Z`) and `Z#0` Dimension 20 frames never create or spread output levels onto proportional sensors, and standard discrete `0..22` valve and 3-speed fan-coil actuators never spawn extra entities.
  * **Restart behaviour (`SensorEntity`, no stale state restoration)**: `MyHOMEActuatorOutputSensor` inherits directly from `SensorEntity` (not `RestoreSensor`), so its state starts `unknown` after a Home Assistant restart until the first bus report arrives rather than replaying stale values alongside the live `climate` entity.
  * **When `*#4*Z#N*20##` is polled**: Each sensor registers its `Z#N` address with `watch_zone_actuators` when added (unless routed behind a local interface `#4#IF` where `interface_zone_request_refused` is `True`), triggering polls after startup discovery completes and after every event-session reconnect. Depending on the gateway, that poll is coalesced into `*#4*0#0*20##` on `F461` and `MyHomeServer1`, sent as `*#4*Z#N*20##` on `3488`, `F450`, `F454`, `F455`, `F459`, and `F460`, and skipped on `ACTUATOR_REQUEST_REFUSED` gateways (`F453`, `F453AV`, `H4684`, `MH200`, `MH200N`, `MH202`) as well as behind `#4#IF` (`interface_zone_request_refused`). Calling `homeassistant.update_entity` (`async_update`) also sends `*#4*Z#N*20##` whenever neither `actuator_request_refused` nor `interface_zone_request_refused` is `True`.

---

## ☀️ Illuminance Lux Sensors (WHO = 1)

Light intensity sensors (e.g. Legrand `048822` ceiling detectors configured in scenario mode) operating on WHO 1 report ambient lux levels:

* **Entity**: `sensor.<name>_illuminance`
* **Device Class**: `illuminance` (unit: `lx`)
* **Use Case**: Drive automated curtain/blind closing when solar glare exceeds threshold, or trigger dusk lighting.
* **Address**: a WHO 1 point address; an area-0 point with PL 1..9 is spelled in two digits (`04`), so `where: "0004"` in `myhome.yaml` is resolved to `04` and a `…-1-0004-illuminance` entity is moved to `…-1-04-illuminance` at startup (#147).

---

## 🔄 Legacy YAML Note

> [!NOTE]
> `WHO = 18` energy meters, `WHO = 4` standalone probes (`WHERE ≥ 100` or `Dimension 15`), passive `WHO = 4` climate zone companion temperature & dynamic humidity sensors, and `WHO = 1` illuminance sensors are discovered dynamically from the bus; explicit `sensor:` entries in `/config/myhome.yaml` remain supported for custom naming or polling standalone zone sensors without duplicating companion entities. For migrating pre-v0.9 `configuration.yaml` files, see the [Legacy YAML Migration Guide](../migration/legacy-yaml.md).