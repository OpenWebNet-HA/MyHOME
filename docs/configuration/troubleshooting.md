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

The model decides pacing and which subsystems are queried. See [How the gateway model is identified](gateways.md#-how-the-gateway-model-is-identified): SSDP is authoritative, a manual choice is corrected when the WHO 13 device type contradicts it with an official code, and a repair issue asks you to confirm otherwise. Use the reconfigure flow to set the model explicitly. If the diagnostics show an unknown `who13_code`, please attach the download to an issue so the code can be documented.

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

There is no general status request for WHO 1, so lights are hydrated from bus traffic. Run `myhome.sweep_bus` (or press **Sweep Bus** on the card); an automation on `homeassistant.start` can do this for you. Covers, thermostats and audio zones are queried at startup.

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

### Cover position is wrong

Timed covers estimate position from the travel time. Calibrate it (`myhome.calibrate_cover`, the device's **Calibrate travel time** button, or the card's Covers panel) or measure it with the panel's stopwatch and save it with `myhome.set_cover_travel_time`. A full open or close resynchronises the estimate. Position-reporting actuators (dimension 10) are exact; if yours reports position but the entity does not follow, set `advanced_shutter: true` in `myhome.yaml`.

### Calibration fails with "no stop status from the actuator"

The actuator did not report its stop within 180 s, or an MH200 / MH200N delayed the frame. See [Covers](known_limitations.md#covers-who-2); use the manual travel time instead.

### Music Assistant does not offer the audio zone as a player

The zone only advertises `play_media` when at least one decoder is mapped in the options flow (**Configure → Dynamic Proxy Decoders**). After saving, the zone re-publishes its features; reload Music Assistant's player list. See [Sound System](media_player.md).

### "All audio matrix inputs are currently in use"

Every playing zone claims one decoder; map more decoders or stop playback in another room.

## Bus and gateway behaviour

### `Could not send message *#16*0##` (or `*#2*0##`, `*#4*0##`) at every start

Startup discovery only asks for subsystems the gateway profile advertises. If you still see it, the gateway model is wrong (an MH200N has no audio): correct it with the reconfigure flow.

### The gateway stops answering after a burst of commands

Single-session gateways (MH200 / MH200N / MH201) need the inter-frame pacing of their profile. If you raised the worker count in the options flow, put it back to the default. The card shows the NACKs; the diagnostics download shows the queue depth.

### Frames appear in the card with the wrong time

The card renders in the browser's local time zone; a difference to the logbook means the browser and Home Assistant have different zones (the 2 h offset of 2.0.0b12 was issue #305 and is fixed).

### `Send frame` in the card is greyed out or returns `Unauthorized`

It needs the **I understand the risk** checkbox and an administrator user; a raw frame can arm or disarm the alarm. Kiosk and long-lived tokens created by non-admins are refused by design.

## Getting help

1. Reproduce with debug logging on.
2. Press **Sweep Bus** on the card, then **Download diagnostics** (or **Export Trace** on the card).
3. Open an issue with the structured form and attach the download; mention the gateway model, firmware and the exact frames you expected. For protocol questions, [RFC #248](https://github.com/orgs/OpenWebNet-HA/discussions/248) is the place.
