# Switches & Relays (WHO = 1, WHO = 4, WHO = 9 & WHO = 25)

The **MyHOME** integration provides `switch` entities across three OpenWebNet families:

1. **On/Off Relay Actuators (`WHO = 1`)**: Socket outlets, contactors, pumps, and appliance relays declared under `switch:` in `/config/myhome.yaml`.
2. **Probe-less Thermo Actuators & Towel Warmers (`WHO = 4`)**: `F430/2` and `F430/4` thermoregulation relays configured via the UI Config Subentry flow (**Add thermo actuator / towel-warmer switch (WHO 4)**) or `switch:` in `/config/myhome.yaml`.
3. **Contact Polarity Toggle (`WHO = 9` & `WHO = 25`)**: Per-device **Invert state** configuration switches (`EntityCategory.CONFIG`) created automatically alongside every `WHO = 25` dry contact and `WHO = 9` auxiliary binary sensor.

---

## 🚀 How `WHO = 1` Relays Are Discovered vs Declared as Switches

OpenWebNet `WHO = 1` bus frames (`*1*0*WHERE##` / `*1*1*WHERE##`) carry no hardware metadata distinguishing a ceiling light relay from a wall socket or extractor fan relay. Because `switch.py` sets `listen=False`, **unconfigured `WHO = 1` addresses seen on the SCS bus are auto-discovered as `light.*` entities**, never automatically as `switch.*`.

To expose a `WHO = 1` relay as a `switch.*` entity (suppressing the duplicate `light.*` entity on that address), declare it under `switch:` in `/config/myhome.yaml`:

```yaml
# /config/myhome.yaml
switch:
  kitchen_counter_socket:
    where: "14"
    name: Kitchen Counter Socket
    class: outlet
```

> [!NOTE]
> **Single-Gateway vs Multi-Gateway `myhome.yaml` Layout**:
> Flat top-level `switch:` blocks apply to single-gateway installations. On multi-gateway installations, nest `switch:` (and any other domain blocks) under each gateway's MAC address key (e.g. `"00:03:50:01:02:03":`).

---

## 🔌 Displaying Relays as Outlets, Valves, or Fans ("Show As")

Once a relay is declared under `switch:` (with optional `class: outlet` or `class: switch`), you can also refine how Home Assistant presents it in the UI using **"Show As"**:

1. Open Home Assistant and navigate to **Settings → Devices & Services → Entities**.
2. Select your switch entity (e.g. `switch.kitchen_counter_socket`).
3. Click the **Settings (gear)** icon.
4. Under **Show As**, choose how you want the entity presented across your dashboards:
    - **Switch** (*Default*): Standard relay, appliance, or heater.
    - **Outlet**: Wall socket / plug load.
    - **Fan / Valve / Light**: Helper wrapper around the underlying switch.
5. Click **Update**. Home Assistant immediately converts the entity representation across Lovelace dashboards, voice assistants (HomeKit, Alexa, Google Assistant), and automations.

---

## ⏱️ Native Hardware Bus Timers

Like MyHOME lights, physical SCS `WHO = 1` switch relays can run native hardware timers directly on the bus (`myhome.turn_on_timed` applies to individual `WHO = 1` relays and refuses `WHO = 4` thermo switches and `WHO = 9/25` invert switches). This ensures that pumps, heaters, or ventilation relays turn off after the elapsed time regardless of whether Home Assistant is running:

```yaml
action: myhome.turn_on_timed
target:
  entity_id: switch.bed_heating_pad
data:
  duration: 1800   # Turn on and automatically turn off after 30 minutes (1800 s)
```

---

## 🔀 Master / Slave (`M=SLA`) Actuators vs Decoupled Independent Control

In traditional physical SCS configuration, auxiliary loads like bathroom extractor fans or secondary contactors are often configured as slaves by inserting `M=SLA` (or delayed-off configurators `M=1..4`) and duplicating the master actuator's `A` and `PL` address.

> [!WARNING]
> **OpenWebNet Limitation with `M=SLA`**:
> On the physical bus, an actuator in `M=SLA` does not have its own independent OpenWebNet address. It listens to the identical `A*PL` broadcast telegram as its master. Sending an OpenWebNet frame (`*1*1*APL##`) turns on both the master and slave simultaneously. It is **not possible** to control an `M=SLA` slave independently from Home Assistant without also triggering the master.

### Recommended Pattern: Decouple the Address (Single Relay Contact)
Instead of wiring two relays in parallel or keeping `M=SLA`:

1. **Remove `M=SLA`**: Configure the auxiliary relay (e.g. Channel 2 of an `F411/2` or `F411/4`) with its own distinct `PL` address (e.g. Light = `11`, Fan = `12`, with `M` left empty).
2. **Declare as Switch**: Declare address `12` under `switch:` in `/config/myhome.yaml` (`switch.bathroom_fan`) and optionally wrap it as a Fan via Home Assistant's **Show As** setting.
3. **Automate the Overrun in HA**: Use an automation to turn on the fan when the light turns on, and run an overrun timer (`myhome.turn_on_timed`) or humidity purge when the light turns off.

See the complete guide and YAML configuration in [Use Cases — Decoupled Bathroom Extractor Fan](use_cases.md#11-decoupled-bathroom-extractor-fan-with-smart-overrun-humidity-boost-single-f411-contact).

---

## 🔥 Probe-less Thermo Actuators & Towel Warmers (`WHO = 4`)

Bathroom towel warmers and electric radiant relays are often wired to `F430/2` or `F430/4` thermoregulation actuators (`WHO = 4`, e.g. Zone `7`, Actuator `1` → `WHERE = 7#1`) **without** a room temperature probe.

* **No Phantom Climate Entities (Safe Coexistence with Real Thermostats)**: Actuator-only Dimension 20 status frames (`*#4*7#1*20*0/1##`), pure-valve Dimension 19 status frames (`*#4*7*19*0*0/1##` on configured `Z#N` zones), and circulation-pump demand/release notifications (`*4*4001*...##` / `*4*4002*...##`) no longer spawn broken temperature-less `climate` entities. Configuring a per-actuator switch (`WHERE = 7#1`) prunes any temperature-less ghost `climate` entity on that zone while preserving any real thermostat that reports temperature or mode on the same zone; configuring a zone-wide switch (`WHERE = 7`) claims the entire zone unless `climate:` is also explicitly configured.
* **UI Configuration (Recommended)**: Navigate to **Settings → Devices & Services → MyHome**, click **Add thermo actuator / towel-warmer switch (WHO 4)** on your gateway, and enter `WHERE` (e.g. `7#1` for actuator 1 of zone 7, `7` for the whole zone, or `7#1#4#01` behind an F422 bus interface). Behind an interface only a switch with `turn_on_command` / `turn_off_command` can be operated: no gateway routes `WHO = 4` across an interface (the address is cut at `#` and the frame acts on the local bus), so the direct Dimension 20 write is refused with an error there, as the status reads already are.
* **Direct Dimension 20 Control (`turn_on_command` / `turn_off_command` left blank)**: When left blank, Home Assistant sends `*#4*7#1*#20*1##` / `*#4*7#1*#20*0##` and tracks live relay state in real time from both `*#4*7#1*#20*0/1##` bus writes and `*#4*7#1*20*...##` status replies (plus state restoration across restarts; if an explicit `Z#N` proportional `100..200` 0–10 V actuator with `1 <= Z <= 99`, `1 <= N <= 9` reports on `Dimension 20`, `output_level` on a point `Z#N` switch or `actuator_levels` on a whole-zone `Z` switch exposes `0..100 %` — `Z#0` or bare `Z` frames never spread percentages, and both `output_level` and `actuator_levels` are excluded from the Home Assistant recorder via `_unrecorded_attributes` so ramp steps record history only on the `sensor` entity). A zone-wide switch (`WHERE = 7`) writes each actuator of the zone that has reported its state, in the same form (`*#4*7#1*#20*1##`, then `*#4*7#2*#20*1##`, ...); the bare-zone `*#4*7*#20*1##` is in no published table or capture and is sent only while no actuator of the zone is known yet. Although the public OpenWebNet `WHO = 4` specification lists Dimension 20 as read-only, emulated gateway firmwares (`F454`, `MH200N`, `F459`, `F460`, `F461`) and real `MyHomeServer1` hardware translate `#20` writes onto the SCS bus (`$0496813100\r` / `$0496813101\r`; `H4684` and `MH200` drop Dimension 20 frames). If your gateway or actuator ignores direct `#20` writes, bind paired CEN+ or `WHO = 1` scenario triggers instead.
* **`MyHomeServer1` (Home + Project "Towel Warmer")**: On `MyHomeServer1`, `bt_thermo_manager` actively locks Home + Project *Towel Warmer* (`BNTR`) objects and immediately reverts direct `*#4*Z#N*#20*1/0##` bus writes unless `towel.setSync` is invoked by a Home + Project / Home + Control Scenario or Automation. Create **two** Scenarios (*Turn Towel Warmer ON* and *Turn Towel Warmer OFF*): **important — when adding the Towel Warmer to a Scenario's action list in Home + Project or Home + Control, its default action state is `OFF` (`*#4*Z#N*#20*0##`); tap the Towel Warmer item inside the ON scenario's action list to toggle its target state to `ON` (or select a boost duration) before saving.** Because app-only scenarios do not allocate an OpenWebNet `Scene_Address` entry in `MyHomeServer1`, bind the two scenarios to any of the following triggers (even when every visible wall switch plate is already in use) and enter the corresponding frames in `turn_on_command` and `turn_off_command`:
    1. **Unpopulated button slots on existing SCS modules (`WHO = 25` CEN+, zero wall keys lost)**: 2-module pushbuttons (`H4652/2`, `L4652/2`, `K4652M2`, `LN4652`, `KW8010/8011/13`) fitted with a single-rocker keycap and flush 2-channel actuator-controls (`H4672M2`, `LN4672M2`, `K4672M2L`) mounted behind blind covers or in junction boxes expose unused button slots in *Home + Project* that can be bound to CEN+ scenarios (e.g. `*25*21#1*21##` for ON and `*25*21#2*21##` for OFF) without changing any physical wall keycap.
    2. **A single unwired relay terminal (`WHO = 1`, only 1 channel needed)**: Register one unwired output terminal on any multi-channel actuator (`F411/4`, `F411/2`, `K8002L`, etc.) as a light/socket in *Home + Project* (e.g. `WHERE = 09` in a technical room) and trigger the ON scenario when `09` turns ON (`*1*1*09##`) and the OFF scenario when `09` turns OFF (`*1*0*09##`).
    3. **Zero-SCS-slot fallback (`emulated_hue`)**: If every button slot and relay terminal on the SCS bus is occupied, pair Home Assistant's built-in `emulated_hue` bridge as a local Philips Hue light (`BN3L`) in *Home + Project* and trigger the ON/OFF scenarios from that virtual light.

```yaml
# Optional YAML equivalent in myhome.yaml:
switch:
  bathroom_towel_warmer:
    who: "4"
    where: "7#1"
    name: Bathroom Towel Warmer
    # Optional — required on MyHomeServer1 Home + Project BNTR Towel Warmers
    # (or on gateways like H4684 / MH200 that drop Dimension 20 writes):
    turn_on_command: "*25*21#1*21##"
    turn_off_command: "*25*21#2*21##"
```

---

## 🔄 Legacy YAML Import & Multi-Gateway Note

> [!NOTE]
> Validated `switch:` entries in `/config/myhome.yaml` are automatically imported and cached into the gateway's config entry options (`migrated_yaml_platforms`) so `WHO = 1` relays remain `switch.*` entities even if `/config/myhome.yaml` is later removed. On multi-gateway setups, place `switch:` under each gateway's `<MAC>:` header. For migrating pre-v0.9 `configuration.yaml` files, see the [Legacy YAML Migration Guide](../migration/legacy-yaml.md).
