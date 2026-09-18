# Sensors & Energy (WHO = 18, WHO = 4, WHO = 1)

The **MyHOME** integration provides monitoring for electrical energy meters, ambient temperature probes, and light sensors across OpenWebNet **WHO = 18**, **WHO = 4**, and **WHO = 1**.

In v2, setup and management are **100% UI-first**: sensors are automatically discovered from bus telemetry without manual YAML configuration files.

---

## ⚡ Power & Energy Meters (WHO = 18)

The integration natively interfaces with Legrand / BTicino DIN power management actuators:
* **BTicino F520**: Single-phase power and energy meter (`WHERE = 51` through `5255`).
* **BTicino F522 / F523**: Multi-circuit energy and power management modules (`WHERE = 71` through `7255`).

### Auto-Discovered Entities
When an energy meter is detected on the SCS bus, the integration creates:
1. **Instantaneous Power Sensor** (`device_class: power`): Real-time active power consumption in Watts (**W**).
2. **Total Energy Counter** (`device_class: energy`, `state_class: total_increasing`): Cumulative electrical energy consumption in Watt-hours (**Wh**) or kilowatt-hours (**kWh**).
3. **Periodic Energy Counters**: Daily and monthly energy sub-counters.

### Home Assistant Energy Dashboard Integration
Because energy entities implement standard `state_class: total_increasing` and `device_class: energy`:
1. Navigate to **Settings → Dashboards → Energy**.
2. Under **Electricity Grid → Add Consumption**, select your discovered MyHOME total energy sensor (e.g. `sensor.total_power_energy`).
3. Home Assistant automatically generates hourly, daily, and monthly solar/grid tracking graphs.

### Live High-Frequency Power Streaming
To instruct the gateway to stream high-frequency instantaneous power updates to Home Assistant, call the `myhome.start_sending_instant_power` service action:

```yaml
action: myhome.start_sending_instant_power
data:
  meter_id: 1    # Meter address (e.g. 1 for F520 address 51)
  interval: 10   # Push interval in seconds
```

---

## 🌡️ Temperature Sensors (WHO = 4)

Standalone and secondary temperature probes operating on WHO 4 are automatically exposed as `sensor` entities with `device_class: temperature`:
* **Main Zone Probes (`WHERE = 1..99`)**: Reports ambient room temperature for individual zones.
* **Secondary Radio Probes (`WHERE ≥ 100`)**: Reports battery-powered wireless probes (e.g. BTicino `3455` behind `L4577` radio interfaces). The v2 engine utilizes push-driven listening, eliminating unnecessary bus polling.

---

## ☀️ Illuminance Lux Sensors (WHO = 1)

Light intensity sensors (e.g. Legrand `048822` ceiling detectors configured in scenario mode) operating on WHO 1 report ambient lux levels:
* **Entity**: `sensor.<name>_illuminance`
* **Device Class**: `illuminance` (unit: `lx`)
* **Use Case**: Drive automated curtain/blind closing when solar glare exceeds threshold, or trigger dusk lighting.

---

## 🔄 Legacy YAML Note

> [!NOTE]
> If you are upgrading from legacy v0.9 installations and still have manual `sensor:` blocks in `/config/myhome.yaml`, please refer to the [v0.9.4 Legacy Sensor Documentation](../../0.9.4/configuration/sensors/) or the [Legacy YAML Migration Guide](../migration/legacy-yaml.md). In v2, all sensors are discovered dynamically.