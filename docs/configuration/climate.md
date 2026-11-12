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

### 1. 99-Zone Central Unit Architecture (`BTicino 3550` / `#0`)
* **Hardware**: Central thermoregulation unit (BTicino `3550`, MyHOME_Screen 10, or MyHOME_Screen 3.5) operating at master address `WHERE = #0`.
* **Subordinate Zones**: Individual room zones (`WHERE = 1..99`) with local probes (`L4693`, `H4693`, `LN4693`, etc.) and `F430/2` or `F430/4` flush/DIN actuators.
* **Operation**: The integration discovers the central unit (`#0`) as the master `climate` entity and each individual zone (`1..99`) as a controllable `climate` device. Subordinate zone setpoint and mode commands use the central-unit-routed `#Z` address format (`*#4*#Z*#14*TTTT*M##`, `*4*311*#Z##`, `*4*303*#Z##`).
* **Central unit modes**: `off` sends `*4*303*#0##`; `auto` asks the central unit for its program and `heat` / `cool` for the season (OWNd's `set_central_mode` builds the frames). With OWNd 2.0.0b13 `auto` is `*4*311*#0##`, which a live `3550` behind an F461 did not act on (ACKed and forwarded, central unit still OFF 8 s later, 2026-11-12); the OWNd change proposed with this (`set_central_mode('#0', 'auto')` -> the published last-weekly-program command `*4*3100*#0##`, WHO 4 PDF §1.6.8) takes effect once the pin moves to a release that carries it; the `3550` answered that frame within 0.15 s with `*4*2101*#0##` (weekly program 1, conditioning). `off` / `auto` are shown at once and then follow the central unit's report (after a program restore the entity shows the reported season and `program`); `heat` / `cool` wait for the central unit's answer (the mode it is in, or nothing when the season does not change; live F461 + F455, 2026-11-08).
* **Startup & Reconnect Status Burst (`*#4*#0##`)**: On startup (including on SCS-only gateways such as `MH200N` and `F454`), the central unit entity queries `*#4*#0##`. The `3550` responds with its operating mode frame (e.g. `*4*1101*#0##` for Heating Weekly Program 1, `*4*110#0200*#0##` for Manual 20.0 °C, or `*4*102*#0##` for Antifreeze/Protection) followed by diagnostic status frames (`WHAT 20..24, 30, 31`), exposed on the `#0` entity as state attributes:
    - `remote_control`: `"enabled"` (`*4*21*#0##`) or `"disabled"` (`*4*20*#0##`, raises the self-clearing `central_remote_control_disabled` repair issue).
    - `at_least_one_probe_off`: `true` when `*4*22*#0##` is reported in the current burst.
    - `at_least_one_probe_antifreeze`: `true` when `*4*23*#0##` is reported in the current burst.
    - `at_least_one_probe_manual`: `true` when `*4*24*#0##` is reported in the current burst.
    - `failure_detected`: `true` when `*4*30*#0##` (`Failure discovered in a zone`) is reported in the current burst.
    - `battery_fault`: `true` when `*4*31*#0##` (`Central unit battery KO`) is reported in the current burst (raises the self-clearing `central_battery_fault` repair issue).
* **Remote Control (*Telegestione*) Requirement**: On a physical BTicino `3550`, **Remote Control (*Telegestione*)** must be set to **Enabled (*Abilitata*)** in the `3550` menu (`*4*21*#0##`) for the central unit to accept remote commands over the bus. When `*4*20*#0##` is active, the gateway still replies `*#*1##` (ACK), but the `3550` ignores remote mode and setpoint writes.
* **Subordinate Zone Control Patterns (`Auto` vs `Manual`)**:

| Control Pattern | Central Unit (`#0`) Mode | Subordinate Zone (`1..99`) Mode | How Setpoint & Schedule Changes Work |
| --- | --- | --- | --- |
| **1. `3550` as Schedule Master + HA Temporary Overrides** | `auto` (Weekly Program `1..3` or Scenario `1..16`) | `auto` (`*4*111*#Z##` / `*4*211*#Z##`) | Calling `climate.set_temperature` on a zone writes `*#4*#Z*#14*TTTT*3##`, placing that zone into **manual setpoint override** (`*4*110*#Z##`, `hvac_mode: heat`/`cool`, `#0` reports `at_least_one_probe_manual: true`). Setting the zone's `hvac_mode` back to `auto` sends `*4*311*#Z##` and returns the zone to the `3550`'s active weekly schedule. |
| **2. Home Assistant as Full Schedule Master** | `heat`, `cool`, or `auto` | `heat` / `cool` (Manual setpoint per room) | Each zone is driven directly by Home Assistant automations or the HA [Schedule](https://www.home-assistant.io/integrations/schedule/) integration calling `climate.set_temperature` (`*#4*#Z*#14*TTTT*3##`) or `climate.set_hvac_mode` (`heat`, `cool`, `off`) on each room entity independently. |

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

> [!TIP]
> **Projecting External Temperatures onto the Bus**:
> To publish Home Assistant temperature sensors (e.g. Zigbee room thermometers, weather forecasts, or ESPHome probes) onto an SCS probe address for display on wall screens and thermostats, use the [Expose to Bus Guide](expose.md).

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
  On single-session or uncaptured SCS-only gateways whose firmware drops the general request `*#4*0##` (F450, F459, H4684, MH200, MH201), each local zone asks for its own temperature with `*#4*<WHERE>*0##` after the startup sweep, so a standalone thermostat is not `unknown` until it next reports (#185). F454, MH202, F460, F461 and MyHomeServer1 drop `*#4*0##` as well but answer the zone status request `*#4*<WHERE>##` themselves (the F454 supports up to 4 command sessions and answers in ~1.3 s on both central-unit #629 and standalone #866 plants, the MH202 answers in ~160-250 ms, #866, the F460 and F461 expand it into one request, and the MyHomeServer1 replies from its cache), so their local zones keep that poll. A zone behind an F422 bus interface (`WHERE = <zone>#4#<interface>`) is never polled unless the OWNd profile sets `supports_interface_zone_requests = True` (see below), so it shows its restored reading until it reports by itself. After a restart the last reading is shown with the attribute `current_temperature_restored: true` until the zone reports again.
* **Current Humidity**: Ambient relative humidity percentage for zones equipped with humidity sensors (e.g. Living Now thermostats or humidity probes).
* **HVAC Action and `actuators`**: a local zone asks for the state of its actuators (`*#4*<WHERE>*20##`, one `*#4*#0*20##` for every local zone on an F461 or a MyHomeServer1) once the startup sweep is over and again after the event session reconnects, so `hvac_action` (`heating` / `cooling` / `idle`) is known right after start instead of after the first actuator switch. The attribute `actuators` lists each actuator as `on` or `off` (`{"1": "off", "2": "off"}`) and stays while the zone is off (every actuator `off`). F453, F453AV, H4684, MH200, MH200N and MH202 drop the request (firmware oracle), so their zones are not asked and wait for the actuators' own frames; the F455 refuses the zone form at once but answers the actuator form `*#4*<WHERE>#<N>*20##` (live F455 1.0.76), so only its thermo actuator switches ask it; on a shared bus the gateway that serves WHO 4 (delegated secondary or warm standby) decides. Secondary probes (`WHERE >= 100`) are never asked. Probe-less `F430/2` / `F430/4` relays (such as bathroom towel warmers that only emit Dimension 20 actuator status frames without a temperature probe) do not create phantom `climate` entities and can be controlled as `switch` entities via **Add thermo actuator / towel-warmer switch (WHO 4)** (see [Switches & Relays](switches.md)).
* **Zones on an F455 (Basic Gateway)**: the F455 returns nothing to `*#4*0##`, ACKs the zone status request `*#4*<WHERE>##` only after about 9 s and refuses `*#4*<WHERE>*20##` at once (live F455 1.0.76, `tests/fixtures/traces/issue_466`), so its zones ask nothing when they are added (#674). 120 s after the startup sweep, a zone that has not reported its temperature, setpoint, mode and an actuator by itself (a value restored from before the restart does not count) asks for what is still missing, one read at a time: `*#4*<zone>*0##`, `*#4*<zone>*14##`, `*#4*<zone>*12##` (the F455 follows it with the zone's season frame) and `*#4*<zone>#1*20##`, each answered in about 0.1 s (#466). A zone without a probe (an actuator-only channel) refuses the first read after about 9 s and is not asked anything else; that one read holds a command session for those 9 s, once per zone added or reloaded. On a shared bus the gateway that serves WHO 4 decides, and a passive standby or secondary asks nothing.
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