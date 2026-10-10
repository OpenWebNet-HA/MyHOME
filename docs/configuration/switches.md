# Switches & Relays (WHO = 1)

The **MyHOME** integration provides full control for on/off relay actuators, socket outlets, contactors, and appliance relays operating on OpenWebNet **WHO = 1**.

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

## 🔄 Legacy YAML Note

> [!NOTE]
> If you are upgrading from legacy v0.9 installations and still have manual `switch:` blocks in `/config/myhome.yaml`, please refer to the [v0.9.4 Legacy Switch Documentation](../../0.9.4/configuration/switches/) or the [Legacy YAML Migration Guide](../migration/legacy-yaml.md). In v2, all switches are managed dynamically via Home Assistant's native registry.