# Home Assistant Repair Issues Guide

Home Assistant integrations use the **Repairs Framework** (*Settings → System → Repairs*) to present actionable issues directly on the user's dashboard rather than burying them in log files.

The MyHOME integration monitors gateway health, OpenWebNet frames, and hardware consistency. When an issue requires user attention, a Repair issue is raised. When conditions normalize, the integration **automatically resolves and withdraws** the issue.

---

## Unconfigured Timezone

**Repair Key**: `unconfigured_timezone`  
**Severity**: `WARNING`  
**Auto-Resolving**: Yes

### What it means
OpenWebNet gateways maintain an internal real-time clock (RTC) queried via WHO=13 dimension 0 (`*#13**0##`) or dimension 22 (`*#13**22##`). When a gateway has never had its timezone configured (or following a firmware factory reset), it emits a sentinel placeholder value `999` in the timezone field:

- Dimension 0: `*#13**0*<HH>*<MM>*<SS>*999##`
- Dimension 22: `*#13**22*<HH>*<MM>*<SS>*999*<DAY>*<MONTH>*<YEAR>##`

### Why it matters
1. **Clock & Timestamp Drift**: Without a configured timezone, scheduled scenario triggers and time broadcasts on the SCS bus may diverge from local daylight saving time or Home Assistant's clock.
2. **Diagnostic Reliability**: Underlying protocol parsers expect a standard UTC offset format (such as `+01:00` or numeric minute offsets).

### How to resolve
1. **Web Interface (F454, F455, MyHomeServer1)**:
   - Log into the gateway web admin interface (`http://<gateway_ip>`).
   - Navigate to **System Configuration** → **Date & Time** (or **Device Settings** → **Clock**).
   - Set the local timezone and enable NTP network time synchronization (e.g. `pool.ntp.org`).
   - Save and reboot.
2. **MyHOME_Suite / TiMyHome (MH200N, MH201, MH202, F452)**:
   - Connect to the gateway via USB or Ethernet.
   - Open **Gateway Settings** → **Date & Time**.
   - Configure local time, timezone, and automatic DST.
   - Send the configuration and reboot.

### How it clears
Once the gateway broadcasts an updated WHO=13 frame with a valid timezone offset, Home Assistant automatically removes this repair issue. You can also trigger an immediate check via **Developer Tools → Actions** using `myhome.sync_time`.

---

## Unknown Gateway Model

**Repair Key**: `unknown_gateway_model`  
**Severity**: `WARNING`  
**Auto-Resolving**: Yes

### What it means
The gateway reported a hardware model code that is unrecognized by the official BTicino specification, by this project's field evidence and by the third-party table taken from Nmap: either a WHO=13 dimension 15 device type (`*#13**15*<code>##`, shown as the bare code) or a WHO=1013 dimension 1 OBJECT_MODEL (`*#1013**1*<code>##`, shown as `1013-1-<code>`; this question is only asked after WHO=13 answered a code shared by several models).

### How to resolve
Click **Learn More** on the repair issue. This will open a pre-filled GitHub issue template (`device_request.yml`). Attach an exported diagnostic trace (*Settings → Devices & Services → MyHOME → ⋮ → Download diagnostics*) so the community can identify the hardware and add native profiling support.

---

## Gateway Model Mismatch

**Repair Key**: `gateway_identity_mismatch`  
**Severity**: `WARNING`  
**Auto-Resolving**: Yes

### What it means
The hardware model reported by the gateway over the bus — a WHO=13 device type, or a WHO=1013 OBJECT_MODEL (code `1013-1-<value>`) when WHO=13 answered a shared code — contradicts the model discovered over SSDP or fixed by the serial transport, or a manual choice questioned by field evidence only, and the integration does not overwrite what the device itself announced.

### How to resolve
1. Check the physical hardware label on your gateway unit in the electrical cabinet.
2. Open **Settings → Devices & Services → MyHOME → ⋮ → Reconfigure**.
3. Select the correct physical model.

---

## Gateway Model Corrected

**Repair Key**: `gateway_identity_corrected`  
**Severity**: `WARNING`  
**Auto-Resolving**: Informational

### What it means
A manually selected model was contradicted by an authoritative code: an official 2006 BTicino device code (e.g. you selected `F454` but the gateway reported code `6`, which is an `F452`), or a WHO=1013 OBJECT_MODEL (e.g. you selected `F454`, WHO=13 answered the shared code `200`, and WHO=1013 answered `67`, which is a `MyHomeServer1`; shown as `1013-1-67`). The integration automatically updated your gateway profile and device registry to match.

### How to resolve
No action is required unless the correction was incorrect. If your physical hardware truly differs, verify the model label on the device.

---

## Gateway Authentication Failed

**Repair Key**: `gateway_authentication_failed`  
**Severity**: `ERROR`  
**Auto-Resolving**: Yes

### What it means
The gateway rejected the OpenWebNet password or HMAC SHA-negotiation credentials.

### How to resolve
1. Open **Settings → Devices & Services → MyHOME**.
2. Click **Reconfigure** on the gateway card.
3. Enter the correct OpenWebNet numeric password or HMAC secret.

---

## High SCS Bus Collision Rate

**Repair Key**: `bus_collision_storm`  
**Severity**: `WARNING`  
**Auto-Resolving**: Yes

### What it means
An unusually high rate of NACK frames or bus collisions was detected on the SCS physical bus.

### How to resolve
1. Verify physical bus wiring and ensure proper line termination (line end-resistors).
2. Ensure multiple command sessions or third-party gateways are not flooding the bus simultaneously.
3. Check the **Lovelace Bus Monitor Card** to identify which device address (`WHERE`) is generating frequent NACKs.

---

## Unconfigured Shared Bus Detected

**Repair Key**: `shared_bus_detected_{gw1}_{gw2}`  
**Severity**: `WARNING`  
**Auto-Resolving**: Yes (when configured or when secondary role is saved)

### What it means
The integration passively detected that two configured OpenWebNet gateways share the same physical SCS bus wiring, but are not configured on the same bus (neither points at the other as its primary). Three correlated frames within 10 minutes raise the issue; each one is either:
1. **TX-to-RX Echoes**: A gateway received a frame on its event session that another gateway transmitted on its command session within 1.5 seconds.
2. **Concurrent RX Frames**: Both gateways received identical physical bus frames within 0.3 seconds.

A frame a gateway transmitted itself never counts, so an automation that sends the same command to two separate buses does not trigger this issue. Gateways configured on the same bus (a primary and the secondaries or standby pointing at it) are expected to see the same frames and are not compared.

### Why it matters
Without configuration, each gateway discovers the same physical devices and registers duplicate entities in Home Assistant (e.g. `light.kitchen_light` and `light.kitchen_light_2`), and simultaneous startup sweeps cause SCS bus collisions and NACK storms.

### How to resolve
1. Navigate to **Settings → Devices & Services → MyHOME**.
2. Identify which gateway should serve as the **Primary Gateway** (usually the newest or most capable gateway, e.g. MH201 or F454) and which as **Secondary** (e.g. MH200N).
3. On the primary gateway card, click **Configure** and set **Bus Topology** to `shared` and **Gateway Role** to `primary`.
4. On the secondary gateway card, click **Configure**:
   - Set **Bus Topology** to `shared`.
   - Set **Gateway Role** to `secondary`.
   - Select the primary gateway under **Primary Gateway**.
   - (Optional) If the secondary gateway is dedicated to specific subsystems (e.g. Burglar Alarm WHO=5 or Audio WHO=16), select them under **Delegated Subsystems**.
5. Click **Submit**. The gateway reloads with its new role, prunes the duplicate entities the primary already has, and stops its redundant sweeps.

---

## Gateway Failover Active (Warm Standby High Availability)

**Repair Key**: `gateway_failover_active_{primary_mac}`  
**Severity**: `WARNING`  
**Auto-Resolving**: Yes (clears when the primary gateway reconnects, when the standby goes offline too, or when either unloads)

### What it means
The primary OpenWebNet gateway (e.g. an F454) has been offline for longer than the 60-second reconnect grace period, and Home Assistant has automatically failed over all bus operations to its warm-standby gateway (e.g. an MH202). A brief reconnect of the primary's event session does not raise this issue, although commands sent during those seconds already go through the standby.

While failover is active:
- Outbound device commands and status polls are seamlessly routed through the standby gateway.
- Inbound physical bus frames received by the standby gateway are bridged to primary entities, keeping your dashboards, states, and automations fully functional.
- The primary gateway's entities remain available in Home Assistant UI.

If the standby goes offline as well, this issue clears and the primary's entities become unavailable.

### How to resolve
1. Check the network connectivity and power supply of the offline primary gateway.
2. Once the primary gateway re-establishes its event session with Home Assistant, the integration automatically fails back to the primary gateway and resolves this repair issue.

---

## Primary Gateway Missing

**Repair Key**: `primary_gateway_missing_{entry_id}`
**Severity**: `WARNING`
**Auto-Resolving**: Yes (when the gateway points at a valid primary again, or is set to standalone)

### What it means
A gateway configured as **Secondary** or **Warm Standby** points at a primary gateway that is no longer configured as a shared primary: it was removed from Home Assistant, or reconfigured. The secondary keeps suppressing discovery of the subsystems it leaves to that primary, so devices on those subsystems get no new entities.

### How to resolve
1. Navigate to **Settings → Devices & Services → MyHOME** and click **Configure** on the gateway named in the issue.
2. Either select another gateway (configured as **Shared** / **Primary**) under **Primary Gateway**, or set **Bus Topology** to `standalone` if it is now the only gateway on its bus.
