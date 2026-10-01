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

## Heating Zone No Longer Answers

**Repair Key**: `unresponsive_zone`  
**Severity**: `WARNING`  
**Auto-Resolving**: Yes

### What it means
A heating zone (or its central unit) did not answer its startup status request on two restarts in a row, so MyHOME stopped asking for it at startup. A request for a zone that is not on the bus keeps the command queue waiting (about 6 s per request in a MyHomeServer1 capture), and the queue is serial, so a few such zones delay every light command sent during startup.

### How to resolve
If the zone no longer exists (for example a leftover entity), remove its device or entity in Home Assistant; the issue disappears with it. If it exists, no action is needed: the issue clears as soon as the zone sends any frame, and the zone is asked again after a week.

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

## Incompatible Streaming Decoder Platform

**Repair Key**: `incompatible_decoder_platform`  
**Severity**: `WARNING`  
**Auto-Resolving**: Yes  
**Fixable via UI**: Yes (Interactive Repair Flow)

### What it means
A configured streaming decoder belongs to an integration that does not accept direct HTTP streaming URLs via Home Assistant's `media_player.play_media` service. This prevents multi-room streaming engines from routing audio to that player.

### How to resolve
1. **Interactive Repair Flow (Recommended)**:
   - Click **Submit** on the Repair issue card.
   - If Home Assistant detects an active **DLNA Digital Media Renderer (DMR)** companion entity for the same physical player, the repair flow automatically updates your MyHOME decoder configuration to use the DLNA companion entity in 1 click.
2. **Manual Configuration**:
   - If the DLNA companion is not yet discovered or configured:
     - Enable UPnP / DLNA in the streamer's mobile app settings.
     - In Home Assistant, go to **Settings → Devices & Services → Add Integration** and add the **DLNA Digital Media Renderer** integration for your device.
     - Return to the Repair issue and click **Submit**, or update the entity mapping in **Settings → Devices & Services → MyHOME → Configure**.

### How it clears
The repair issue automatically withdraws and deletes once the decoder entity is updated to a streaming-compatible entity (e.g. DLNA DMR), or when the decoder is removed from the MyHOME Options Flow.

---

## Unconfigured Shared Bus Detected

**Repair Key**: `shared_bus_detected_{gw1}_{gw2}`  
**Severity**: `WARNING`  
**Auto-Resolving**: Yes (when configured or when secondary role is saved)  
**Fixable via UI**: Yes (1-Click Repair Flow)

### What it means
The integration passively detected that two configured OpenWebNet gateways share the same physical SCS bus wiring, but are not configured on the same bus (neither points at the other as its primary). Three correlated point-to-point frames within the evidence window raise the issue via strict **TX-to-RX Echoes** (a gateway received a frame on its event session that another gateway transmitted on its command session within 1.5 seconds).

### Automated Capability Inference
Home Assistant automatically inspects the hardware models, command concurrency, queue pacing, and supported OpenWebNet subsystems (`WHO` dimensions) of both gateways:
- **Tier 1 (High Throughput / Multi-Session)**: `F454`, `F455`, `F461`, `MyHomeServer1`.
- **Tier 2 (Linux / Touchscreen Gateways)**: `MH201`, `MH202`, `H4890` / `AM4890` / `LN4890`.
- **Tier 3 (Legacy Microcontroller Gateways)**: `MH200N`, `MH200`, `F452`, `F453`.

The integration designates the higher-tier or broader-subsystem-coverage gateway as **Primary** (ranking by hardware tier, supported WHO count, and deterministic MAC address tie-break), and calculates the capability delta for the follower:
$$\Delta = S_{\text{sec}} \setminus S_{\text{pri}}$$
- **Empty Delta ($\Delta = \emptyset$)**: Both gateways have equivalent or subordinate capability (e.g. F454 + MH202). The follower is configured as **Warm Standby** for high-availability failover without entity duplication.
- **Non-Empty Delta ($\Delta \neq \emptyset$)**: The follower gateway provides specialized hardware subsystems not supported by the primary (e.g. MyHomeServer1 + H4890, where H4890 provides Burglar Alarm WHO 5, Auxiliary WHO 9, and Multi-room Sound WHO 16/22). The follower is assigned as **Secondary** with delegated subsystems $\Delta$.

### Why it matters
Without configuration, each gateway discovers the same physical devices and registers duplicate entities in Home Assistant (e.g. `light.kitchen_light` and `light.kitchen_light_2`), and simultaneous startup sweeps cause SCS bus collisions and NACK storms.

### How to resolve
1. **1-Click Repair Flow (Recommended)**:
   - Click **Submit** on the Repair issue card (*Settings → System → Repairs*).
   - Home Assistant displays the inferred topology dialog showing the assigned Primary, Follower, Role, and Delegated Subsystems with full rationale.
   - Click **Submit** to apply the configuration automatically and reload both gateways.
2. **Manual Configuration (Options Flow)**:
   - Navigate to **Settings → Devices & Services → MyHOME**.
   - On the primary gateway card, click **Configure** and set **Bus Topology** to `shared` and **Gateway Role** to `primary`.
   - On the follower gateway card, click **Configure**:
     - Set **Bus Topology** to `shared`.
     - Select the primary gateway under **Primary Gateway**.
     - Notice that **Gateway Role** and **Delegated Subsystems** are automatically pre-populated with the inferred smart defaults.
     - Click **Submit**. The gateway reloads with its new role, prunes duplicate entities, and stops redundant sweeps.

> [!TIP]
> **Diagnostic Logging**: To inspect the exact hardware tier comparison, supported WHOs, and capability delta calculated by Home Assistant, enable debug logging for `custom_components.myhome.topology` and `custom_components.myhome.repairs`. When opening a GitHub issue, attach the diagnostics download (**Settings → Devices & Services → MyHOME → ⋮ → Download diagnostics**) which captures the topology state and recent bus echoes.

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

---

## Decoder Is A MyHOME Or Music Assistant Entity

**Repair Key**: `invalid_decoder`  
**Severity**: `WARNING`  
**Auto-Resolving**: Yes  
**Fixable via UI**: No

### What it means
A decoder slot points at a `myhome` zone or a Music Assistant (`mass`) player. Routing a zone back into itself would loop the audio, so MyHOME leaves that slot out of the decoder pool. The options flow already refuses these entities; this issue catches a slot saved before that check, imported, or whose entity later changed platform.

### How to resolve
Open **Settings → Devices & Services → MyHOME → Configure** and pick the physical streamer that is plugged into the matrix (Squeezelite, WiiM, Cambridge Audio, Cast, ...).

### How it clears
Automatically, on the next reload after the slot points at a hardware entity or is emptied.

---

## Several Streaming Companions Found

**Repair Key**: `ambiguous_companion`  
**Severity**: `WARNING`  
**Auto-Resolving**: Yes  
**Fixable via UI**: No

### What it means
A decoder whose integration refuses stream URLs (Cambridge Audio) needs a DLNA / UPnP / Cast renderer of the same box, and more than one device matched at the same detection step. MyHOME does not guess, because gluing the wrong streamer to the wrong box plays the wrong room. Until you choose, the decoder does not take streams.

### How to resolve
Open **Settings → Devices & Services → MyHOME → Configure** and set **Streaming companion** for that decoder slot to the right entity.

### How it clears
Automatically, once the slot has an explicit companion or detection finds a single candidate.

---

## More Than One Gateway Serves Sound Zones

**Repair Key**: `multiple_audio_gateways`  
**Severity**: `WARNING`  
**Auto-Resolving**: Yes  
**Fixable via UI**: No

### What it means
Sound zones exist on several gateways. Sound addressing does not yet carry the bus interface (issue [#426](https://github.com/OpenWebNet-HA/MyHOME/issues/426)), so zones of two audio matrices can collide on the same room number, entity and environment.

### How to resolve
Keep the audio matrix on one gateway until #426 is fixed, and remove the sound zones of the other gateway.

### How it clears
Automatically, when only one gateway has sound zones.

---

## Actuator Hardware Fault

**Repair Key**: `actuator_hardware_fault`  
**Severity**: `WARNING`  
**Auto-Resolving**: Yes  
**Fixable via UI**: No

### What it means
A lighting actuator on the bus reported an unmapped OpenWebNet status code outside the standard WHO=1 table (`WHAT 0..10, 11..18, 20..31`).

On modular dimmer actuators such as the **BTicino F414**, an unmapped code like `WHAT 19` co-occurs with an autodiagnostic bitmask on `WHO 1001` indicating an internal protection lockout (typically an open-circuit load due to a missing or disconnected load wire, a burnt bulb, or an open T5H 250V internal fuse).

### How to resolve
1. Locate the physical actuator module in the electrical cabinet.
2. Inspect the module status LED (a red blinking LED indicates an active anomaly lockout).
3. Verify that all load wires are securely connected to the output terminals.
4. Check the internal cartridge fuse (T5H 250V) on the module and replace it if blown.
5. Verify the connected bulb / load is compatible with the dimmer (e.g. incandescent/ferromagnetic for F414).

### How it clears
Automatically, once the fault condition is resolved and the actuator responds with a standard lighting status (ON, OFF, or level).

