# Switches & Relays (WHO = 1 & WHO = 4)

The **MyHOME** integration provides full control for on/off relay actuators, socket outlets, contactors, and appliance relays operating on OpenWebNet **WHO = 1**, as well as probe-less thermoregulation relays / towel warmers (`F430/2`, `F430/4`) operating on **WHO = 4**.

In v2, setup and management are **100% UI-first**: switch relays are automatically discovered over the SCS bus, and entity types (such as distinguishing between a wall switch and a power outlet) are configured natively through Home Assistant's UI settings without manual YAML.

---

## 🚀 Auto-Discovery

When your gateway connects to Home Assistant:

1. **Dynamic Bus Discovery**: The integration listens to OpenWebNet `WHO = 1` relay commands and active addresses.
2. **Device Creation**: Each physical relay actuator (`WHERE = 1..99` or area/point addresses `WHERE = 01..9999`) is registered as a Home Assistant entity linked to your gateway device.
3. **UI Customization**: You can rename the switch, assign it to an Area (e.g. *Kitchen*, *Utility Room*), and select a custom icon directly in the Home Assistant UI.

---

## 🔌 Displaying Relays as Outlets, Valves, or Appliances

In legacy configurations, distinguishing between an outlet and a switch required manual `class: outlet` YAML entries. In Home Assistant v2, this is handled natively in the UI using Home Assistant's **"Show As"** feature:

1. Open Home Assistant and navigate to **Settings → Devices & Services → Entities**.
2. Select your switch entity (e.g. `switch.kitchen_counter_socket`).
3. Click the **Settings (gear)** icon.
4. Under **Show As**, choose how you want the entity presented across your dashboards:
   - **Switch** (*Default*): Standard relay, appliance, or heater.
   - **Outlet**: Wall socket / plug load.
   - **Light**: If the relay controls an external transformer or ballast.
   - **Valve**: Irrigation or water shutoff valve.
5. Click **Update**. Home Assistant immediately converts the entity representation across Lovelace dashboards, voice assistants (HomeKit, Alexa, Google Assistant), and automations.

---

## ⏱️ Native Hardware Bus Timers

Like MyHOME lights, physical SCS switch relays can run native hardware timers directly on the bus. This ensures that pumps, heaters, or ventilation relays turn off after the elapsed time regardless of whether Home Assistant is running:

```yaml
action: myhome.turn_on_timed
target:
  entity_id: switch.bed_heating_pad
data:
  time: 1800   # Turn on and automatically turn off after 30 minutes (1800s)
```

---

## 🔥 Probe-less Thermo Actuators & Towel Warmers (`WHO = 4`)

Bathroom towel warmers and electric radiant relays are often wired to `F430/2` or `F430/4` thermoregulation actuators (`WHO = 4`, e.g. Zone `7`, Actuator `1` → `WHERE = 7#1`) **without** a room temperature probe.

* **No Phantom Climate Entities (Safe Coexistence with Real Thermostats)**: Actuator-only Dimension 20 status frames (`*#4*7#1*20*0/1##`), pure-valve Dimension 19 status frames (`*#4*7*19*0*0/1##` on configured `Z#N` zones), and circulation-pump demand/release notifications (`*4*4001*...##` / `*4*4002*...##`) no longer spawn broken temperature-less `climate` entities. Configuring a per-actuator switch (`WHERE = 7#1`) prunes any temperature-less ghost `climate` entity on that zone while preserving any real thermostat that reports temperature or mode on the same zone; configuring a zone-wide switch (`WHERE = 7`) claims the entire zone unless `climate:` is also explicitly configured.
* **UI Configuration (Recommended)**: Navigate to **Settings → Devices & Services → MyHome**, click **Add thermo actuator / towel-warmer switch (WHO 4)** on your gateway, and enter `WHERE` (e.g. `7#1` for actuator 1 of zone 7, `7` for the whole zone, or `7#1#4#01` behind an F422 bus interface).
* **Direct Dimension 20 Control (`turn_on_command` / `turn_off_command` left blank)**: When left blank, Home Assistant sends `*#4*7#1*#20*1##` / `*#4*7#1*#20*0##` and tracks live relay state in real time from both `*#4*7#1*#20*0/1##` bus writes and `*#4*7#1*20*...##` status replies (plus state restoration across restarts). Although the public OpenWebNet `WHO = 4` specification lists Dimension 20 as read-only, emulated gateway firmwares (`F454`, `MH200N`, `F459`, `F460`, `F461`) and real `MyHomeServer1` hardware translate `#20` writes onto the SCS bus (`$0496813100\r` / `$0496813101\r`; `H4684` and `MH200` drop Dimension 20 frames). If your gateway or actuator ignores direct `#20` writes, bind paired CEN+ or `WHO = 1` scenario triggers instead.
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

## 🔄 Legacy YAML Note

> [!NOTE]
> If you are upgrading from legacy v0.9 installations and still have manual `switch:` blocks in `/config/myhome.yaml`, please refer to the [v0.9.4 Legacy Switch Documentation](../../0.9.4/configuration/switches/) or the [Legacy YAML Migration Guide](../migration/legacy-yaml.md). In v2, all switches are managed dynamically via Home Assistant's native registry.
