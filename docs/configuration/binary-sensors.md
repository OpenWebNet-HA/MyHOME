# Binary Sensors & Contacts (WHO = 25, WHO = 1, WHO = 9)

The **MyHOME** integration provides monitoring for dry contact interfaces, PIR motion sensors, and security auxiliary contacts across OpenWebNet **WHO = 25**, **WHO = 1**, and **WHO = 9**.

`WHO = 25` dry contacts (`3xxx`) and `WHO = 1` motion detectors are **automatically discovered from SCS bus traffic**, whereas `WHO = 9` auxiliary channels (`WHERE = 0..9`) require declaration in `/config/myhome.yaml`. Device presentation (such as choosing between a door sensor, window contact, or motion detector) can be configured directly in Home Assistant's UI settings.

---

## 🚀 Supported Binary Sensor Types

### 1. Dry Contact Interfaces (WHO = 25)
* **Hardware**: BTicino `3477` / `F428` / `3480` contact interfaces, magnetic reed switches, mechanical window switches, technical alarm contacts.
* **Addresses**: `WHERE = 31` through `3201` (or `3<A><PL>` alarm/IR contacts).
* **Auto-Discovery**: Automatically discovered from SCS bus events and registered `3xxx` startup sweeps (`*#25*3xxx##`).
* **Address spelling**: an F455 (firmware 1.0.76) writes contacts 1 and 2 as `301` / `302` where an F461 (firmware 2.0.11) on the same bus and the WHO 25 specification write `31` / `32`; captures of an F454, an MH200N and an MH202 carry the short form, and the other gateway models have no capture of these contacts. `30N` is read as `3N`: new contacts are created as `31`, and a contact an F455 registered earlier as `301` keeps its entity and follows both spellings. On a shared bus a standby F455 therefore updates the primary's contact during a failover, and a follower's `301` that duplicates the primary's `31` is pruned at startup like any other duplicate. The status request is always written with the short spelling (`*#25*31##`), because the F461 answered the padded one with NACK while the F455 relays the short one.

### 2. Motion / PIR Sensors (WHO = 1)
* **Hardware**: Legrand `048822` / `067226`, BTicino `BMSE1001` / `K4659`, or standard SCS ceiling/wall motion sensors.
* **Auto-Discovery**: Automatically discovered when movement broadcasts an event frame on `WHO = 1` (`*1*34*WHERE##` / `*1*39*WHERE##`).
* **Occupancy Clearing**:
  * **Stand-alone mode (`WHAT = 39`)**: Detectors that emit end-of-movement (`*1*39*WHERE##`, #777) clear immediately on `WHAT = 39` and use a 10-minute safety fallback during continuous occupancy so multi-minute gaps between `WHAT = 34` frames do not drop the state early.
  * **Scenarios mode (no `WHAT = 39`)**: Detectors configured in Scenarios mode (such as the BTicino `K4659` Object `128`/`165`, #941) repeat `*1*34*WHERE##` once per configured `DIMENSION 7` time delay and go silent when movement stops; they clear via an exact timer at `DIMENSION 7 + 3 s` (default `303 s` before `DIMENSION 7` is reported) and adapt automatically if a longer repeat cycle is observed.
* **Address**: a WHO 1 point address; an area-0 point with PL 1..9 is spelled in two digits (`04`), so `where: "0004"` in `myhome.yaml` is resolved to `04` and a `…-1-0004-motion` entity is moved to `…-1-04-motion` at startup (#147).

### 3. Auxiliary Alarm Channels (WHO = 9 — Requires `myhome.yaml`)
* **Hardware**: Auxiliary channels (`AUX 0`–`AUX 9`), technical transmitters (water leak, methane gas), or peripheral contacts linked to the burglar alarm central unit.
* **Addresses**: `WHERE = 0` through `9`.
* **Configuration**: `WHO = 9` frames do **not** auto-discover entities from bus traffic (`WHO = 13` also never dynamically discovers entities). Declare auxiliary channels under `binary_sensor:` in `/config/myhome.yaml`:

```yaml
# /config/myhome.yaml
binary_sensor:
  aux_water_leak:
    who: "9"
    where: "1"
    name: Utility Water Leak
    class: moisture
```

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

## 🔁 Inverting Normally-Closed Contacts in the UI ("Invert state")

Third-party wired motion sensors, alarm reed switches, and twilight switches connected to a dry contact interface (`F428` / `3477` / `3480` / `F482`, **WHO = 25**) or auxiliary channel (**WHO = 9**) frequently use **normally-closed (NC)** contacts: the loop stays closed (`WHAT = 31`, `on`) at rest and opens (`WHAT = 32`, `off`) on detection or tamper.

Every WHO = 25 dry contact and WHO = 9 auxiliary device exposes a per-device **Invert state** toggle under **Configuration** (`EntityCategory.CONFIG`), with no `myhome.yaml` needed:

1. Navigate to **Settings → Devices & Services → MyHOME → Devices** and open your dry contact or auxiliary device.
2. Under **Configuration**, toggle **Invert state** on.
3. The binary sensor flips its live state immediately (without reloading the gateway or polling the SCS bus) and persists the setting in Home Assistant's config entry options across restarts.

> [!TIP]
> If an existing `myhome.yaml` entry specifies `inverted: true`, **Invert state** starts enabled automatically, and toggling it off or on from the UI takes precedence over `myhome.yaml`.

---

## 🔄 Legacy YAML Note

> [!NOTE]
> `WHO = 25` dry contacts and `WHO = 1` motion sensors are discovered dynamically from the SCS bus, while `WHO = 9` auxiliary channels are declared under `binary_sensor:` in `/config/myhome.yaml` (and cached into the gateway's config entry options on startup). For migrating pre-v0.9 `configuration.yaml` files, see the [Legacy YAML Migration Guide](../migration/legacy-yaml.md).