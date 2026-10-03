# Troubleshooting

Symptoms first, then what to check. Every item names the log line or bus frame that identifies it, because the fastest diagnosis is almost always a look at the [bus-monitor card](bus_monitor.md) or the diagnostics download (**Settings → Devices & services → MyHOME → ⋮ → Download diagnostics**). When you open an issue, attach that download: it carries the gateway identity, profile, queue state and the last 500 frames, with credentials redacted.

Enable debug logging while you investigate:

```yaml
logger:
  logs:
    custom_components.myhome: debug
    OWNd: debug
```

## Setup and connection

### "Failed to set up" / entry keeps retrying

Log: `Gateway could not be reached or connection failed at <host>` or `Gateway connection test failed at <host>: <reason>`.

- The gateway must answer on TCP port `20000` (OpenWebNet). Check with `nc -vz <host> 20000` from the Home Assistant host; a router firewall between VLANs is the usual cause.
- Only one client can hold a command session on MH200 / MH200N / MH201. Close the BTicino configuration software (MyHOME_Suite) or another OpenWebNet client, then reload the entry.
- The gateway's OpenWebNet "IP range" setting must include the Home Assistant address, or the gateway silently drops the connection after the handshake.

### "Reauthentication required"

Log: `Gateway rejected the OpenWebNet password (password_error|password_required)`.

Home Assistant opens the reauth flow itself; enter the OpenWebNet password configured on the gateway (not the web-UI password). MH200 / MH200N / MH201 / AM4890 / 3578 only support the numeric password; HMAC (alphanumeric) is available on F454 / F455 / MH202 / MyHOMEServer1. If the gateway has no password, leave the field empty and make sure its IP-range whitelist includes Home Assistant.

### The gateway was discovered with the wrong model, or a repair issue says the model was corrected

The model decides pacing and which subsystems are queried. See [How the gateway model is identified](gateway-identification.md): SSDP is authoritative, a manual choice is corrected when the WHO 13 device type contradicts it with an official code, and a repair issue asks you to confirm otherwise. Use the reconfigure flow to set the model explicitly. If the diagnostics show an unknown `who13_code`, please attach the download to an issue so the code can be documented.

### `No module named 'custom_components.myhome.backup'`

A backup folder inside `/config/custom_components/` is loaded as an integration. Move it out:

```bash
mv /config/custom_components/myhome.backup /config/myhome_backup
ha core restart
```

The same applies to any stray `*.py` file placed directly in `/config/custom_components/`.

### The bus-monitor card does not appear in the card picker

The resource `/myhome_static/myhome-bus-card.js?v=<hash>` is registered automatically. If the picker spins or the card is missing: hard-refresh the browser (`Ctrl+F5`); check **Settings → Dashboards → Resources** for the entry; and look in the browser console (`F12`) for another custom card throwing a `CustomElementRegistry` error before ours loads — a duplicate card (for example two schedule cards) blocks every card after it.

## Entities

### Lights are `unknown` after a restart

Lighting states are queried at startup via `*#1*0##`. If a specific light remains `unknown`, it may not have answered the general status request or is on an unrouted bus interface. Run `myhome.sweep_bus` (or press **Sweep Bus** on the card), or operate the light from its physical switch to report state.

### Entities go unavailable for about a minute, then recover

Log: `Event connection lost, reconnecting...` (OWNd) followed by a reconnect. This is normal on gateways that close idle event sockets (MH200 / MH201); the 60 s grace period keeps entities available across short drops. If it happens every few minutes, check the network path (Wi-Fi bridge, DHCP lease renewals, a second client stealing the session).

### A wall switch turned a light on, but Home Assistant still shows it off

Watch the card: if the actuator's own status frame (`*1*1*<where>##`) is missing after the press, the command was a group / area / general command that this actuator does not echo. See [Known Limitations](known_limitations.md#lighting-who-1) for the workaround.

### A light or cover shows up twice, or as the wrong platform

Discovery creates a `light` for every WHO 1 actuator unless the address is configured as a `switch`, `binary_sensor` or `sensor` in `myhome.yaml`, and a `cover` for every WHO 2 address. If you have a relay driving a socket, declare it under `switch:` so no light is created; the stale light entity can then be removed from its device page.

### A device I deleted came back

Devices are discovered from bus traffic. Deleting is meant for devices that are physically gone; one that still exists reappears on its next status frame.

### Temperature sensor shows `NACK` errors every 5 minutes

Log: `Could not send message *#4*<ZPP>*15##`. Probe addresses (`WHERE ≥ 100`) refuse the explicit poll. Probes are receive-only and are only polled when no reading arrived in the last interval (fixed after 2.0.0b12, issue #308); if you still see it, update the integration.

### "Heating zone unresponsive" repair alert on central unit (3550 / 4695) or phantom "Climate Zone 99"

- **Central Unit (`#0` / `#0#1`) false-positive**: Central units were previously polled with Dimension 14 status requests (`*#4*#0*14##`), which central units and gateways reject with NACK because OpenWebNet specifies Dimension 14 status reads only for subordinate zone addresses `1..99`. Central units receive their setpoints and modes via commands (`*#4*#0*#14*T*M##`), autonomous broadcast events, or restored state, but do not answer point-to-point status queries. This triggered a false-positive `"unresponsive zone"` repair alert. Central units are now exempt from point-to-point status polling and any stale repair alert is cleared on startup.
- **"Centrale termoregolazione 99 zone" vs "Climate Zone 99" / "Climate Zone 0"**: The BTicino 3550 is commercially marketed as the *"Centrale termoregolazione 99 zone"* because it can manage up to 99 subordinate zones, but its OpenWebNet central unit address is strictly `#0`.
  - **Ghost Zone 99**: If a user configured `zone: 99` or `zone: "99"` in `myhome.yaml` based on the commercial product name, Home Assistant created and persisted `<mac>-4-99` in the Entity Registry. Because zone 99 does not physically exist on that bus, its Dimension 14 query (`*#4*99*14##`) failed, raising an `unresponsive_zone` repair alert.
  - **Resolution**: Delete the phantom entity in **Settings → Devices & services → Entities**. Removing the entity deletes its entry from Home Assistant's Entity Registry; Home Assistant then invokes `async_will_remove_from_hass()`, which confirms the registry entry is gone and permanently clears the repair issue from the Issue Registry. Because physical 3550 traffic is addressed to `#0`, bus sweeps will never resurrect or invent Zone 99. (If an installation physically contains a 99th zone thermostat, genuine traffic addressed to zone 99 will discover and manage it normally).
  - **YAML & Bus Coalescing**: When configured in `myhome.yaml` (e.g. `zone: "#0"` named `Centrale termoregolazione`), the YAML device and bus discovery coalesce cleanly on address `#0`, routing all broadcast traffic to the configured entity without creating a duplicate `Climate Zone 0` entity.

### Cover position is wrong

Timed covers estimate position from the travel time. Calibrate it (`myhome.calibrate_cover` or the device's **Calibrate travel time** button) or measure it with a stopwatch and save it with `myhome.set_cover_travel_time`. A full open or close resynchronises the estimate. Position-reporting actuators (dimension 10) are exact; if yours reports position but the entity does not follow, set `advanced_shutter: true` in `myhome.yaml`.

### Calibration fails with "no stop status from the actuator"

The actuator did not report its stop within 180 s, or an MH200 / MH200N delayed the frame. See [Covers](known_limitations.md#covers-who-2); use the manual travel time instead.

### Music Assistant does not offer the audio zone as a player

Two conditions must be met for a MyHOME room to appear and accept playback in Music Assistant:

1. **Home Assistant Player Provider in Music Assistant:** Music Assistant does not expose Home Assistant media players automatically. In Music Assistant, navigate to **Settings → Providers → Add Provider → Home Assistant (Player Provider)**, connect to your Home Assistant instance, and ensure the MyHOME room amplifier entities (`media_player.<room>`) are selected and enabled.
2. **Dynamic Proxy Decoders mapped in MyHOME:** A MyHOME room entity only advertises `play_media` (streaming support) when at least one streaming decoder is mapped in the integration options (**Settings → Devices & Services → MyHOME → Configure → Decoders**). Without a decoder, zones operate in standalone WHO 16 mode (power, volume, source only). After mapping a decoder, reload Music Assistant's player list. See [Sound System](media_player.md).

### Playing music to a room vs. backend streamer (Why you should never group them)

- **Question / Misconception:** *"The streamer plugged into the matrix is `Livingroom 1_3519`, but I want to hear music in `Bathroom`. Should I create a group containing `Livingroom 1_3519` and `Bathroom` so both play?"*
- **Solution:** **No! Never group your backend streamer with a destination room.**
  - Map `Livingroom 1_3519` as a **Decoder** in MyHOME options (**Configure → Decoders**).
  - In Music Assistant, target and play directly to **`Bathroom`**.
  - Behind the scenes, MyHOME automatically claims the streamer from the pool, wakes the Bathroom amplifier, routes the F441/F441M matrix to that input, and forwards the stream URL to the streamer.
  - Grouping the streamer and the room causes Music Assistant to stream to both the physical streamer and the virtual proxy at the same time, leading to stream collisions, desynchronization, or audio loops.
  - Groups in Music Assistant are **only** for multi-room playback across **multiple destination rooms** (e.g., Bathroom + Living Room). Never add the backend streamer to that group.

### How can you have stereo with only 2 wires? / Do I need an L4561N interface?

- **The 2-wire SCS bus carries both control and audio.** In BTicino MyHOME *Diffusion Sonore 2 Fils*, the 2-wire SCS bus simultaneously carries 27V DC power, OpenWebNet digital control frames (WHO 16: power, volume, input routing), and high-frequency modulated stereo audio over the same pair of conductors. There is no separate analog audio cabling running to room amplifiers.
- **Why you need an audio source interface for external streamers.** External streamers, DACs, or phones output standard baseband analog stereo line-level audio (via RCA or 3.5mm jack). They cannot connect directly to SCS bus terminals. An audio source interface module—such as the Legrand / BTicino **L4561N** (4 DIN stereo source interface with RCA inputs and IR control), **L4560 / HS4560 / N4560 / NT4560 / HC4560** (modular flush-mount RCA sockets), or **3482** (auxiliary line preamplifier)—is required to modulate the line-level audio onto the 2-wire SCS bus into one of the matrix source inputs (S1–S4).
- **Ground isolation (art. 3495):** When connecting external Class I (earthed) equipment or multiple audio sources, install the **3495** source isolator between the streamer and the interface to provide 1500 Vrms galvanic isolation, preserving SELV bus compliance and eliminating ground hum.
- **Stereo vs. mono:** The F441/F441M matrix and L4561N interface support true stereo (L/R) distribution. Whether you hear stereo in a given room depends on the amplifier installed in that room (e.g. H4562, F502, or 3484/3487 stereo amplifiers) and whether two speakers (L + R) are wired to it. See the [official wiring schematic in the Sound System Guide](media_player.md#official-bticino-2-wire-sound-system-wiring-schematic).

### "All audio matrix inputs are currently in use"

Every playing zone claims one decoder; map more decoders or stop playback in another room.

### Reporting an audio problem

Download the MyHOME diagnostics (*Devices & services → MyHOME → ⋮ → Download diagnostics*) and attach it to the issue; its `audio` block shows every zone, decoder and group without room names. Add the Music Assistant log only if the problem is on the Music Assistant side. See [Reporting an audio problem](media_player.md#reporting-an-audio-problem).

## Bus and gateway behaviour

### `Could not send message *#16*0##` (or `*#2*0##`, `*#4*0##`) at every start

Startup discovery only asks for subsystems the gateway profile advertises. The bare `*#16*0##` was the wrong frame and every gateway NACKs it; current versions send `*#16*0*5##`. If a status request is still rejected, check that the gateway model is right and correct it with the reconfigure flow.

### The gateway stops answering after a burst of commands

Single-session gateways (MH200 / MH200N / MH201) need the inter-frame pacing of their profile. If you raised the worker count in the options flow, put it back to the default. The card shows the NACKs; the diagnostics download shows the queue depth.

### Frames appear in the card with the wrong time

The card renders in the browser's local time zone; a difference to the logbook means the browser and Home Assistant have different zones (the 2 h offset of 2.0.0b12 was issue #305 and is fixed).

### `Send frame` in the card is greyed out or returns `Unauthorized`

It needs the **I understand the risk** checkbox and an administrator user; a raw frame can arm or disarm the alarm. Kiosk and long-lived tokens created by non-admins are refused by design.

### Multi-Gateway & Shared-Bus Behaviour

#### Unconfigured Shared Bus Detected (`shared_bus_detected`)
- **Symptoms**: Repair issue `shared_bus_detected` appears in **Settings → System → Repairs**, duplicate entities are created, or startup sweeps trigger bus collisions.
- **Log lines**:
  `Recorded shared bus TX echo #1/3 between <mac_a> and <mac_b> (delta <N>ms)`
- **What to check**:
  1. Open the Repair issue and review the automated recommendation.
  2. Click **Submit** to apply the inferred topology automatically (1-click repair).
  3. If configuring manually in **Configure**, set `bus_topology: shared` on both gateways, designate one as `primary`, and point the follower at the primary.

#### How to inspect automated topology inference decisions
- **Log lines**:
  `Evaluating shared bus topology between <Model A> and <Model B>...`
  `Inferred shared bus topology: Primary=<Model> (<MAC>), Follower=<Model> (<MAC>, role=<role>, delegated=<whos>)...`
- **What it tells you**:
  The log details the exact hardware tiers, supported WHOs, capability delta formula ($\Delta = S_{\text{sec}} \setminus S_{\text{pri}}$), and tie-breaking rule used to assign the primary and follower roles.

#### Gateway Failover Active (`gateway_failover_active`)
- **Symptoms**: The primary gateway is offline, but entities remain controllable and dashboard states still update.
- **Log lines**:
  `Primary gateway event session offline (> 60s); engaging warm standby failover via <standby_mac>`
- **What it tells you**:
  The primary gateway has been offline past the 60-second grace period. The standby gateway has seamlessly assumed outbound command routing and inbound frame bridging. Inspect the primary gateway's network connectivity or power supply.

## Getting help

1. Reproduce with debug logging on.
2. Press **Sweep Bus** on the card, then **Download diagnostics** (or **Export Trace** on the card).
3. Open an issue with the structured form and attach the download; mention the gateway model, firmware and the exact frames you expected. For protocol questions, [RFC #248](https://github.com/orgs/OpenWebNet-HA/discussions/248) is the place.
