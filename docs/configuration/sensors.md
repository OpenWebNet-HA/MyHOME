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

## 🌡️ Temperature Sensors (WHO = 4)

Standalone and secondary temperature probes operating on `WHO = 4` are automatically discovered as `sensor` entities with `device_class: temperature`:

* **Standalone & Secondary Probes (`WHERE ≥ 100`, i.e. `0101`–`0999` / `101`–`899`, or `Dimension 15`)**: Auto-discovered from SCS bus traffic (including battery-powered wireless probes such as BTicino `3455` behind `L4577` radio interfaces, and outdoor/slave probes `3475`/`3476`).
* **Thermostat Zones (`WHERE = 1..99`)**: Zone addresses `1..99` belong to `climate.*` thermostat entities by default and do **not** spawn duplicate standalone temperature `sensor` entities unless explicitly declared under `sensor:` in `/config/myhome.yaml`.

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
> `WHO = 18` energy meters, `WHO = 4` standalone probes (`WHERE ≥ 100` or `Dimension 15`), and `WHO = 1` illuminance sensors are discovered dynamically from the bus; `sensor:` entries in `/config/myhome.yaml` remain supported for custom naming or exposing thermostat zone (`1..99`) temperatures as standalone `sensor` entities. For migrating pre-v0.9 `configuration.yaml` files, see the [Legacy YAML Migration Guide](../migration/legacy-yaml.md).