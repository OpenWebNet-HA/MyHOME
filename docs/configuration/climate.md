# Climate & Heating (WHO = 4)

The **MyHOME** integration provides full control for heating, cooling, central thermoregulation units, and zone thermostats operating on OpenWebNet **WHO = 4**.

In v2, setup and management are **100% UI-first**: climate zones and central units are automatically discovered over the SCS bus without manual YAML configuration files.

---

## 🚀 Auto-Discovery

When your gateway connects to Home Assistant:
1. **Dynamic Bus Discovery**: The integration listens to OpenWebNet `WHO = 4` temperature and setpoint messages broadcast on the SCS bus.
2. **Entity Creation**: Each active thermoregulation zone (`WHERE = 1..99` or central unit `#0`) is registered as a Home Assistant `climate` entity linked to your gateway.
3. **UI Customization**: You can rename the zone, assign it to an Area (e.g. *Master Bedroom*, *Living Room*), and adjust temperature step increments directly in the Home Assistant UI.

---

## 🏛️ Supported Plant Architectures

MyHOME installations typically deploy one of three climate topologies, all natively supported by the v2 discovery engine:

### 1. 99-Zone Central Unit Architecture
* **Hardware**: Central thermoregulation unit (BTicino `3550`, MyHOME_Screen 10, or MyHOME_Screen 3.5) operating at master address `WHERE = #0`.
* **Subordinate Zones**: Individual room zones (`WHERE = 1..99`) driven by `F430/2` or `F430/4` flush/DIN actuators.
* **Operation**: The integration discovers the central unit as the master climate entity and each individual zone as a controllable climate device. Commands issued to room zones are synchronized through the central unit.

### 2. 4-Zone Central Unit Architecture
* **Hardware**: BTicino `HC4695`, `L4695`, or `LN4695` 4-zone master display panel.
* **Operation**: The master panel acts as Zone 1 (or its assigned zone number) while coordinating up to three subordinate zones.

### 3. Standalone Probes (No Central Unit)
* **Hardware**: Autonomous room thermostats (BTicino `H4691`, `LN4691`, `L4692`) acting as independent temperature controllers without a master central display.
* **Operation**: Each thermostat is exposed directly as an independent `climate` entity with full target setpoint and mode control.

---

## 🌡️ Push-Driven Secondary Probes (`WHERE ≥ 100`)

In plants with secondary temperature sensors (e.g. BTicino `3455` radio probes connected via `L4577` radio interfaces), sensor addresses have 3 digits (e.g. `WHERE = 105` corresponds to the first secondary probe of Zone 5).

* **Push-Driven Telemetry**: These battery-powered or radio sensors broadcast their temperature updates periodically on the SCS bus and actively reject direct poll requests.
* **v2 Quiet Listening**: The v2 climate engine recognizes secondary probe addresses, operates in receive-only mode, and suppresses redundant polling to prevent gateway NACKs.

---

## 🎛️ Supported Features & HVAC Modes

MyHOME climate entities support standard Home Assistant climate controls:
* **HVAC Modes**:
  - `heat`: System active in heating mode.
  - `cool`: System active in cooling / air-conditioning mode.
  - `auto`: Following scheduled setpoint or central program.
  - `off`: System frost-protection or thermal shutdown.
* **Target Temperature**: Adjustable setpoint slider in 0.5 °C increments.
* **Current Temperature**: Real-time ambient reading reported by the probe.

---

## 🔄 Legacy YAML Note

> [!NOTE]
> If you are upgrading from legacy v0.9 installations and still have manual `climate:` blocks in `/config/myhome.yaml`, please refer to the [v0.9.4 Legacy Climate Documentation](../../0.9.4/configuration/climate/) or the [Legacy YAML Migration Guide](../migration/legacy-yaml.md). In v2, climate zones are auto-discovered dynamically.