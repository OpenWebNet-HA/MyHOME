# Hardware-Coupled Sensors & Twilight Curfew Guide

This guide explains how to identify, manage, and decouple physical **BTicino MyHOME sensors** (such as external twilight photocells, contact interfaces, or motion sensors) that share an SCS bus address with a lighting actuator relay.

---

## Understanding Hardware Coupling

In classic BTicino MyHOME installations, point-to-point automation is established through **physical configurator plugs** (`A` and `PL`). Installers frequently configure a control interface with the **identical `A` and `PL` address** as an actuator relay:

```text
[External Twilight Switch] (Photocell)
         │ dry contact
         ▼
[BTicino 3477 Contact Interface]  (Model 129, Configured: A=9, PL=8)
         │ SCS Bus Event: *1*1*98##
         ▼
[BTicino F411/4 Relay Actuator]   (Channel 4: A=9, PL4=8)
         │ relay closure
         ▼
[Outdoor Light Fixture]
```

### Diagnostic Investigation via WHO 1001

OpenWebNet diagnostics (`WHO 1001`) can help identify which device sits behind an address, with caveats:

1. **Identity request (`DIMENSION 1`)**: send `*#1001*WHERE*1##` (for address `98`: `*#1001*98*1##`). A device answers with `*#1001*WHERE*1*OBJECT_MODEL*N_CONF*BRAND*LINE##`: its item/model value, its number of physical configurator positions, and brand and product-line codes. The MyHOME_Suite catalogue lists object model `129` for the 3477 contact interface.
2. **Which devices answer**: a device appears to answer diagnostics at the address of its *first* channel. An F411/4 configured `A=9`, `PL1=5` … `PL4=8` would then answer at `95`, not `98`, so a reply at `98` normally comes from the 3477 (or a single-channel actuator), not from the F411/4 channel that shares the address.
3. **Configurator readback (`DIMENSION 4`)**: MyHOME_Suite can read the inserted configurators with `*#1001*WHERE*4##`, but gateways may NACK it.

> [!NOTE]
> These diagnostic replies have not been captured on a test plant for this guide yet. If you can capture them for a 3477 and the actuator it drives, please attach the trace to [issue #631](https://github.com/OpenWebNet-HA/MyHOME/issues/631).
>
> The practical signal is simpler: if `light.light_98` turns on by itself at dusk and a 3477 is installed with `A=9`, `PL=8`, the address is hardware-coupled.

### What Happens on the Wire

1. **At Dusk**: As ambient light drops, the external twilight switch closes its dry contact.
2. **SCS Broadcast**: The **BTicino 3477** senses the transition and broadcasts an unsolicited lighting command:
   `*1*1*98##`
3. **Actuator Switching**: Relay Channel 4 on the **F411/4** receives this command directly on the SCS bus and closes, illuminating the outdoor light.
4. **Edge-Triggered Behavior & Manual Control**: The 3477 is expected to act on state transitions only: it sends one command when the contact closes and does not repeat it while the contact stays closed. When you turn off the light from Home Assistant, an app, or an SCS wall switch, `*1*0*98##` opens the relay and it stays off for the rest of the evening. Watch the bus for an evening to confirm this on your plant; the curfew approach depends on it.
5. **At Dawn**: When daylight returns, the twilight switch contact opens, causing the 3477 to broadcast `*1*0*98##`.

---

## Architectural Challenges in Home Assistant

1. **Hardware-Level Activation**: The initial turn-on at dusk occurs directly on the SCS wire before Home Assistant can intercept or filter it.
2. **Shadowed Sensor State**: Because the 3477 transmits standard `WHO = 1` (Lighting) frames to address `98`, Home Assistant discovers address `98` as a single `Light` entity. The physical state of the photocell contact is obscured by the light relay state.
3. **No Native Curfew**: Without an automation layer, the light remains on all night until dawn unless manually switched off.

---

## Solution Strategy 1: Non-Invasive Dusk Curfew (Blueprint)

If you cannot easily modify physical configurator plugs in electrical panels, use Home Assistant to add an intelligent curfew and companion synchronization layer on top of the hardware behavior.

### 🌟 Using the Dusk Curfew Blueprint

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FOpenWebNet-HA%2FMyHOME%2Fblob%2Fv2-phase1-architecture%2Fblueprints%2Fautomation%2Fmyhome%2Fdusk_curfew.yaml)

> [!TIP]
> During the v2 beta development cycle, the badge above points to the `v2-phase1-architecture` branch. You can also import directly from this URL in **Settings → Automations & Scenes → Blueprints → Import Blueprint**.

* **Source File**: `blueprints/automation/myhome/dusk_curfew.yaml`
* **Manual Installation**:
  Download `dusk_curfew.yaml` and save it to `<config>/blueprints/automation/myhome/dusk_curfew.yaml` in your Home Assistant configuration directory.
* **Key Features**:
  - **Bedtime Curfew**: Enforces a strict cutoff time (`curfew_time`, e.g. `23:00:00`) to turn off the hardware-coupled light and all companion lights.
  - **Configurable Morning Curfew End**: Configurable `curfew_end_time` (default `06:00:00`), after which daytime behavior resumes.
  - **Max Run Duration**: Optional timeout (e.g. 180 minutes) to guarantee the light turns off even on dark winter afternoons when dusk occurs early.
  - **Companion Synchronization**: When the hardware twilight light turns on at dusk, automatically illuminates additional outdoor lights (e.g. pathway spots, facade accents) and turns them off together at curfew.
  - **Presence Gating**: Supports `zone.*` (evaluates as away when occupant count is 0), `person.*`, `device_tracker.*`, `group.*`, `binary_sensor.*`, or `alarm_control_panel.*` (away when `armed_away` or `armed_vacation`). When occupants are away, reduces the runtime to a configurable `away_timeout`. Dynamic presence tracking automatically turns off lights if occupants leave mid-evening.
  - **Optional Daylight Guard**: Optional `after_sunset_only` (default `false`) gates companion activation to astronomical night. Keep disabled if your twilight photocell trips before sunset on overcast or winter afternoons. While it is disabled, switching the light on by hand during the day also turns the companion lights on.
  - **Service & Maintenance Overrides**:
    - **Service Power (Gardener / Power Tools)**: Energizes the circuit immediately on demand during daylight hours, suspends curfew and presence shutoffs, and turns everything off again after `service_timeout_hours` (default 4 hours). A Home Assistant restart re-arms the full timeout.
    - **Maintenance Hold**: Turns the circuit off and switches any photocell or wall switch turn-on straight back off. It is a convenience, not electrical isolation; see the warning below.
    - **Pause Automation**: Leaves lights under manual control, temporarily bypassing all curfew and timer logic.
  - **Reconnection Resilience**: Uses `from: "off"` to ensure temporary gateway drops or Home Assistant restarts (`unavailable -> on`) do not re-run curfew sequences in the middle of the night.
  - **Turn-Ons During Curfew**: A turn-on between `curfew_time` and `curfew_end_time` is turned off after 2 minutes. This includes deliberate turn-ons from a wall switch; use the `pause_automation` override if you need the light for longer at night.
  - **Restarts**: Home Assistant does not keep a running `max_duration` timer across a restart; the curfew still applies afterwards.

### Example Automation Configuration

> [!NOTE]
> By default, the MyHOME integration discovers lights with names reflecting their bus address (e.g. `light.light_98`). If you have renamed your entity in Home Assistant (e.g. `light.outdoor_facade_light`), provide that entity ID in the `target_light` field.

```yaml
alias: "Outdoor Light 98 - Dusk Curfew & Companion Sync"
use_blueprint:
  path: myhome/dusk_curfew.yaml
  input:
    target_light: light.light_98
    curfew_time: "23:00:00"
    curfew_end_time: "06:00:00"
    max_duration: 180
    sync_lights:
      entity_id:
        - light.garden_pathway
        - light.driveway_spots
    presence_entity: zone.home
    away_timeout: 15
    after_sunset_only: false
    # Optional Gardener / Maintenance Override Helper
    override_entity: input_boolean.gardener_power
    override_mode: service_power
    service_timeout_hours: 4
```

### Temporary Overrides: Gardener Power & Maintenance Hold

Outdoor lighting circuits frequently double as power lines for garden sockets (lawnmowers, hedge trimmers, pumps) or need maintenance:

1. **Gardener Service Power**: Create a helper (`input_boolean.gardener_power` in **Settings → Devices & Services → Helpers**). When turned on (via a dashboard button or NFC tag by the shed), the automation energizes `light.light_98` immediately and suspends curfew/presence turn-offs. After `service_timeout_hours` (e.g., 4 hours), it automatically turns off the circuit and resets the helper. Turning the helper off earlier ends the session and cancels the timeout.
2. **Maintenance Hold**: Configure `override_mode: maintenance_hold` with a helper such as `input_boolean.lighting_maintenance_hold`. While it is on, the automation turns the light off and switches any photocell or wall switch turn-on straight back off, so lamps stay dark while you replace bulbs or tidy the garden.

> [!WARNING]
> **Maintenance hold is not electrical isolation.** The 3477 switches the relay directly on the bus; Home Assistant only turns it back off afterwards, so the circuit is briefly live on every dusk trigger or switch press. If Home Assistant, the gateway or the network is down, nothing turns it off at all. Before touching wiring, fixtures or lamp holders, switch off and lock the circuit breaker.


---

## Solution Strategy 2: Clean Hardware Decoupling (Full HA Authority)

If you prefer Home Assistant to hold **100% software authority** over whether and when the outdoor lights illuminate, decouple the 3477 contact interface from the lighting relay.

### Option A: Move 3477 to an Unused Address (Physical Decoupling)

1. Locate the **BTicino 3477** module in your electrical panel.
2. Replace the `PL` configurator with a point in Area 9 that no actuator uses (e.g. change from `PL=8` to `PL=9` if nothing answers at `99`).
3. **Result**: At dusk, the 3477 broadcasts `*1*1*99##`. No physical relay clicks or turns on.
4. In Home Assistant, address `99` appears as `light.light_99` once the integration has seen it on the bus.
5. Create a standard Home Assistant automation triggered by `light.light_99` turning on to evaluate weather, presence, and schedule before commanding the actual fixture `light.light_98`.

> [!NOTE]
> A light point of 10 or more uses the four-digit address form: `A=9`, `PL=10` is `0910`, not `910`.

### Option B: Configure 3477 as a Dry Contact Interface (`WHO = 25`)

1. Using **MyHOME_Suite** (Virtual Configuration), reconfigure the 3477 contact interface into **Dry Contact Mode** (`WHO = 25`, virtual address range 1..201).
2. **Result**: The interface transmits OpenWebNet dry contact frames where `WHAT` is `31` for contact closed and `32` for contact open:
   - Contact closed (dusk): `*25*31#1*WHERE##`
   - Contact opened (dawn): `*25*32#1*WHERE##`
3. In Home Assistant, the contact is discovered automatically the first time it changes state (see [Binary Sensors](../configuration/binary-sensors.md)). The contact closes at dusk, so the sensor reads `on` when it is dark. If you want Home Assistant's **Light** device class (`on` = light detected), declare it in `myhome.yaml` under your gateway with `inverted: true`, using its configured address (for example `15`):
   ```yaml
   00:03:50:81:22:33:
     binary_sensor:
       twilight_sensor:
         who: "25"
         where: "15"
         name: "Twilight Sensor"
         device_class: light
         inverted: true
   ```
4. You now have full separation of concerns:
   - `binary_sensor.twilight_sensor` accurately mirrors daylight/darkness in real time.
   - `light.light_98` remains a pure actuator controlled exclusively by Home Assistant schedules, presence logic, and automations.
