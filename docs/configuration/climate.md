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
  The slider shows the setpoint the thermostat regulates to (dimension 12, knob offset included) whenever the zone has reported it; the commanded setpoint (dimension 14) is only shown until then. On an F461 the two can differ (21.0 commanded, 23.5 in force on a live capture), and the attribute `local_target_temperature` carries the same value. When the zone leaves `off`, the value reported while it was off is taken as the protection setpoint (#383), so the commanded setpoint is shown until the zone reports dimension 12 again; after a `set_temperature` call the next report, dimension 12 or 14, is shown. The value restored after a restart of Home Assistant already carries the saved `local_offset`, so the first dimension 13 report does not add it again.
* **Current Temperature**: Real-time ambient reading reported by the probe.
  On SCS-only gateways whose firmware drops the general request `*#4*0##` (F450, F454, F459, H4684, MH200, MH201, MH202), each local zone asks for its own temperature with `*#4*<WHERE>*0##` after the startup sweep, so a standalone thermostat is not `unknown` until it next reports (#185). F460, F461 and MyHomeServer1 drop `*#4*0##` as well but answer the zone status request `*#4*<WHERE>##` themselves (the F460 and F461 expand it into the four dimension requests, the MyHomeServer1 replies from its cache), so their local zones keep that poll. A zone behind an F422 bus interface (`WHERE = <zone>#4#<interface>`) is never polled unless the OWNd profile sets `supports_interface_zone_requests = True` (see below), so it shows its restored reading until it reports by itself. After a restart the last reading is shown with the attribute `current_temperature_restored: true` until the zone reports again.
* **Current Humidity**: Ambient relative humidity percentage for zones equipped with humidity sensors (e.g. Living Now thermostats or humidity probes).
* **HVAC Action and `actuators`**: a local zone asks for the state of its actuators (`*#4*<WHERE>*20##`, one `*#4*#0*20##` for every local zone on an F461 or a MyHomeServer1) once the startup sweep is over and again after the event session reconnects, so `hvac_action` (`heating` / `cooling` / `idle`) is known right after start instead of after the first actuator switch. The attribute `actuators` lists each actuator as `on` or `off` (`{"1": "off", "2": "off"}`) and stays while the zone is off (every actuator `off`). F453, F453AV, H4684, MH200, MH200N and MH202 drop the request (firmware oracle), so their zones are not asked and wait for the actuators' own frames; on a shared bus the gateway that serves WHO 4 (delegated secondary or warm standby) decides. Secondary probes (`WHERE >= 100`) are never asked. Probe-less `F430/2` / `F430/4` relays (such as bathroom towel warmers that only emit Dimension 20 actuator status frames without a temperature probe) do not create phantom `climate` entities and can be controlled as `switch` entities via **Add thermo actuator / towel-warmer switch (WHO 4)** (see [Switches & Relays](switches.md)).
* **Zones Behind an `#4#II` Interface (`F422`)**: across all twelve emulated gateway firmwares in `own-firmware-oracle` (`findings/thermo-zone-requests.md`), **zero** gateways emit `$06EC`/`$06E4` interface-routed SCS telegrams for `WHO = 4`: gateways either NACK/drop `#4#II` queries, truncate `Z#4#II` at `#` and cross-poll local zone `Z` on the main bus (`F450`, `F454`, `F459`, `F460`, `F461`, `H4684`, `MH200`, `MyHomeServer1`), or on `MH202` (`1.0.24`) misparse trailing `#4#01` as local zone 4 actuator 1 and crash `coso` (`SIGSEGV`) on `*#4*Z#4#01##`. Unless an `OWNd` gateway profile explicitly declares `supports_interface_zone_requests = True`, zones configured with an `#4#II` interface are passive: neither polls (`*#4*Z#4#II##`, `*0##`, `*11##`, `*19##`, `*20##`, `*60##`) nor writes that would strip `#4#II` onto local main-bus zone `Z` are sent, while incoming event-session broadcasts update state normally (note that 1-digit inline shorthand `3#4#1`..`3#4#9` is normalized as zone `3` behind interface `01`..`09`; wire frames always use two-digit `#4#01`..`#4#15`). Dimensionless and zone-dimension polls on actuator or split addresses (`Z#N`, `Z#0`, `0#N`, `3#Z#N`) are likewise never sent.

### 💧 Humidity Support & Startup Behavior

Climate zones equipped with integrated humidity sensors (such as BTicino Living Now or specialized probes) report relative humidity via OpenWebNet `WHO = 4` Dimension `60` frames (`*#4*<WHERE>*60*<HUMIDITY>##`):

* **Dynamic Auto-Detection**: When a climate zone emits a humidity reading on the SCS bus, the integration dynamically enables humidity tracking on that entity.
* **State Restoration Across Restarts**: The zone remembers that it supports humidity across Home Assistant restarts and restores its last known humidity value immediately.
* **Targeted Startup Query**: After startup (once the initial discovery sweep finishes), only climate zones that support humidity are queried with `*#4*<WHERE>*60##` to refresh the current reading. This avoids unnecessary bus traffic on zones without humidity sensors. A live F461 and a live MyHomeServer1 answer the request (F459 and F460 forward it in the firmware emulation); on F450, F453, F453AV, F454, H4684, MH200, MH200N and MH202 the emulated firmware did not forward it (own-firmware-oracle `findings/thermo-zone-requests.md`, a `firmware_observed` verdict), so until the OWNd gateway profile states `supports_heating_humidity_request` their zones are not queried. Whether those gateways put a probe's humidity on the event session has not been traced; any `*#4*<WHERE>*60*<HH>##` event the gateway delivers still updates `current_humidity` without polling.
* **Optional YAML Configuration**: In advanced setups using legacy `/config/myhome.yaml`, humidity polling can be explicitly pre-enabled on a zone by setting `humidity: true` under the zone configuration.

---

## 🔄 Legacy YAML Note

> [!NOTE]
> If you are upgrading from legacy v0.9 installations and still have manual `climate:` blocks in `/config/myhome.yaml`, please refer to the [v0.9.4 Legacy Climate Documentation](../../0.9.4/configuration/climate/) or the [Legacy YAML Migration Guide](../migration/legacy-yaml.md). In v2, climate zones are auto-discovered dynamically.