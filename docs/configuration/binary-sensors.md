# Binary Sensors & Contacts (WHO = 25, WHO = 1, WHO = 9)

The **MyHOME** integration provides monitoring for dry contact interfaces, PIR motion sensors, and security auxiliary contacts across OpenWebNet **WHO = 25**, **WHO = 1**, and **WHO = 9**.

In v2, setup and management are **100% UI-first**: binary sensors are automatically discovered from SCS bus events, and device presentation (such as choosing between a door sensor, window contact, or motion detector) is configured directly in Home Assistant's UI settings.

---

## 🚀 Supported Binary Sensor Types

### 1. Dry Contact Interfaces (WHO = 25)
* **Hardware**: BTicino `3477` flush-mounted contact interface, magnetic reed switches, mechanical window switches, technical alarm contacts.
* **Addresses**: `WHERE = 31` through `3201`.
* **Auto-Discovery**: As soon as a dry contact changes state on the SCS bus, the integration automatically creates the corresponding binary sensor entity.

### 2. Motion / PIR Sensors (WHO = 1)
* **Hardware**: Legrand `048822`, BTicino `BMSE1001` or standard SCS ceiling/wall motion sensors configured in scenario mode.
* **Operation**: When movement is detected, the sensor broadcasts an event frame on WHO 1 that sets the binary sensor to `on` (Detected), returning to `off` (Clear) when timeout expires.

### 3. Auxiliary Alarm Sensors (WHO = 9)
* **Hardware**: Auxiliary sensors, technical transmitters (water leak, methane gas), or peripheral contacts connected to the burglar alarm central unit.
* **Addresses**: `WHERE = 0` through `9`.

---

## 🚪 Selecting Device Classes in the UI ("Show As")

In legacy versions, specifying whether a contact was a door, garage door, or window required manual YAML `class:` keys. In v2, this is configured directly in Home Assistant's UI:

1. Navigate to **Settings → Devices & Services → Entities**.
2. Select your binary sensor (e.g. `binary_sensor.garage_entry_door`).
3. Click the **Settings (gear)** icon.
4. Under **Show As**, select the appropriate device class:
   - **Door**: Entry doors, interior doors.
   - **Window**: Opening windows, skylights.
   - **Garage Door**: Motorized or monitored garage gates.
   - **Motion**: PIR motion and occupancy detectors.
   - **Moisture**: Water leak detectors.
   - **Gas / Smoke**: Technical safety sensors.
   - **Lock / Tamper**: Anti-tampering switches on enclosures.
5. Click **Update**. Home Assistant immediately applies appropriate dynamic icons (e.g. open/closed doors, motion waves) and integrates the sensor into Area security summaries.

---

## 🔄 Legacy YAML Note

> [!NOTE]
> If you are upgrading from legacy v0.9 installations and still have manual `binary_sensor:` blocks in `/config/myhome.yaml`, please refer to the [v0.9.4 Legacy Binary Sensor Documentation](../../0.9.4/configuration/binary-sensors/) or the [Legacy YAML Migration Guide](../migration/legacy-yaml.md). In v2, all binary sensors are discovered dynamically.