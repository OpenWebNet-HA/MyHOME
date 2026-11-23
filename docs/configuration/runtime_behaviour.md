# Runtime Behaviour Notes (v2)

How the v2 integration decides *what to poll*, *what to trust from the bus*, and *what it learns at runtime*. These are the behaviours most often mistaken for bugs, with the issue that motivated each one.

---

## 🔌 Startup discovery is gated by the gateway profile

After the event session is up, the integration sends a small set of general status requests to hydrate entities:

| Frame | Subsystem | Supported Gateways |
| :--- | :--- | :--- |
| `*#1*0##` | Lighting | All gateways |
| `*#2*0##` | Automation / covers | All gateways |
| `*#4*0##` | Thermoregulation | All gateways except F450, F454, F455, F459, F460, F461, H4684, MH200, MH201, MH202 and MyHomeServer1, whose firmware drops it (firmware oracle; the MH201 from a physical sweep trace, `tests/fixtures/traces/issue_466`; a physical F455 1.0.76 acknowledged it after about 9 s with no frame). In its place the sweep asks every actuator's status, `*#4*0#0*20##`, on F450, F454, F455, F459, F460, F461 and MyHomeServer1, and the central unit's status, `*#4*#0##`, on F461 and MyHomeServer1. F454, MH202, F460, F461 and MyHomeServer1 poll `*#4*<WHERE>##` per zone (#629, #649, #714, #866), and the zones of the remaining SCS-only models (F450, F455, F459, H4684, MH200, MH201) ask `*#4*<WHERE>*0##` |
| `*#5*0##` | Burglar alarm | MH202, H4890 |
| `*#16*0*5##` | Sound system (dimension 5; lists every amplifier) | Gateways whose profile advertises WHO 16, except F450, F459, F460, F461, MH202 and MyHomeServer1, whose firmware drops it (firmware oracle) |
| `*#18*51*51##` | Energy management (meter 51 total energy) | F454, F455, MH201, MH202, MyHomeServer1, H4890 |
| `*#18*51*113##` | Energy management (meter 51 active power) | F454, F455, MH201, MH202, MyHomeServer1, H4890 |

Each request is only sent when the gateway's OWNd **profile** advertises that WHO (`GatewayProfile.supported_who`). Unknown gateways keep the full set.

The requests go out one at a time. The gateway ACKs a general request at once, but the replies (one frame per device, on the event session) keep coming for seconds on a large plant, and a second request sent in that window cuts the first reply short ([#578](https://github.com/OpenWebNet-HA/MyHOME/issues/578)). So after each request is written, the next one waits until the event session has been quiet for 0.75 s, at most 15 s. A request that is NACKed or never written still gets that wait. `myhome.sweep_bus` paces its general requests the same way and, unless called with `force: true`, leaves out the ones the firmware drops just like the startup sweep, so a sweep on a large plant takes a few seconds per subsystem. Entities restored at startup poll their own state only after the whole sweep has finished.

> OWNd 2.0.0b8 and earlier give an **MH200** the MH200N profile, which does not advertise WHO 16, so amplifiers behind an MH200 only appear once they send bus traffic. A live MH200 answers `*#16*0*5##` with every amplifier and source; [OWNd#53](https://github.com/OpenWebNet-HA/OWNd/issues/53) gives it its own profile.

> The WHO 16 status request is `*#16*WHERE*5##` (spec section 1.5.2). Gateways NACK the bare `*#16*0##` for every address, whether or not the plant has audio, so an old NACK on that frame never meant "no audio".

### Day 0 Blank-Start Discovery Matrix (Auto-discovered vs Manual Configuration)

On a fresh installation with no `myhome.yaml` file (Day 0 blank start), the integration automatically queries and hydrates standard residential hardware subsystems according to gateway capabilities:

| Subsystem / Feature | Auto-Discovered on Blank Start | Requires `myhome.yaml` or UI Configuration | Notes |
| :--- | :---: | :---: | :--- |
| **Lighting (WHO 1)** | ✅ Yes (`*#1*0##`) | Optional (custom names / classes) | Point-to-point lights (`11`–`99`) and routed addresses. Area/general broadcasts (`WHERE="0"` / `"00"`) do not create entities. |
| **Automation / Covers (WHO 2)** | ✅ Yes (`*#2*0##`) | Optional (`travel_time`, general cover) | Point-to-point standard covers. Venetian tilt and impulse mode require manual configuration. |
| **Thermoregulation (WHO 4)** | ✅ Yes (`*#4*0##`), per gateway | Optional (`cooling`, `fan`) | Standard zones (`1`–`99`) and central units (`#0`, `#0#1`). Area broadcasts (`00`) and probe broadcasts do not create climate entities. On F450, F454, F455, F459, F460, F461, H4684, MH200, MH201, MH202 and MyHomeServer1 the firmware drops `*#4*0##` (see the table above). Where the sweep asks every actuator instead (F450, F454, F455, F459, F460, F461, MyHomeServer1), a zone with no registry entry and no `myhome.yaml` is found at start: one with a fan coil from its actuator's answer, one with a plain valve by a read of its temperature. On H4684, MH200, MH201 and MH202 such a zone is still discovered from its first bus frame. |
| **Burglar Alarm (WHO 5)** | ✅ Yes (`*#5*0##`) | Optional | Discovered on supported gateways (MH202, H4890). Panel tracks the central unit. |
| **Sound System (WHO 16)** | ✅ Yes (`*#16*0*5##`), per gateway | Optional (streamer mapping) | Discovers all amplifier zones and sources via dimension 5 query where the profile advertises WHO 16. On F450, F459, F460, F461, MH202 and MyHomeServer1 the firmware drops `*#16*0*5##`, so amplifiers there are discovered from their first bus frame. |
| **Energy Management (WHO 18)** | ✅ First Meter 51 only | Secondary meters (`52`–`59`) | Factory default residential meter `51` is queried at startup for power and total energy. Secondary meters are discovered dynamically upon bus emission or via `myhome.sweep_bus`. |
| **Scenario Control (WHO 15 / 25)** | ❌ Stateless | N/A (Device Triggers / Events) | Pushbuttons emit events and device triggers; no entities are created. |
| **Light Groups (`where: '#G'`)** | ❌ Bus limitation | Required (`members: [...]`) | OpenWebNet groups have no bus membership read-back command. |

---

## 🔁 Broadcast re-sync (group / area / general)

A group (`*1*x*#G##`), area (`*1*x*A##`) or general (`*1*x*0##`) command comes from a
physical wall switch or scene, not from Home Assistant, so no single actuator's own
status reply is guaranteed to follow it. Depending on the gateway model and firmware,
member status replies may arrive either *before* the broadcast frame (e.g. ~0.9 s prior on
MyHomeServer1) or *after* it (e.g. trailing ~60–200 ms on F454 and other installations).
Sweeping on every broadcast frame regardless would double that traffic for no benefit.
Instead, the integration debounces across a bidirectional window:

1. A group/area/general frame checks for **leading echoes** received in the preceding 1.5 s
   (`RESYNC_LEADING_WINDOW_S`). If members already reported their status, no sweep is scheduled.
2. Otherwise, a 0.5 s timer (`RESYNC_DEBOUNCE_S`) is armed for that target.
3. If member point-to-point status frames arrive before the timer fires (**trailing echoes**),
   the sweep is cancelled:

    - For an **area**, point-to-point echoes in that matching area only skip or cancel its
      timer once *all* known `light`/`switch` entities registered in that area have echoed
      with matching polarity (preventing partial CEN/Scenario bursts such as `MH200N`
      scenarios from starving non-echoing actuators in the same area); unrelated areas stay
      armed.
    - For a **group**, receiving multiple member echoes (`≥ 2`) in the window cancels its
      timer, protecting against unrelated bus frames.
4. Otherwise, one status request is sent:

    - **Group** `#G`: `*#1*#G##` (the group's own status).
    - **Area** `A`: `*#1*A##`, using the frame's own `WHERE` (`"00"`, `"1"`.."9", `"100"`)
      - never a value re-derived from an integer, which would risk emitting the banned
      `*#1*0##`.
    - **General**: one `*#1*A##` per area that has at least one known WHO=1 actuator
      (`light` or `switch` in the entity registry), never `*#1*0##`.

Disable this with **Sweep group/area/general light addresses for status** in the
Options Flow if your gateway lags on repeated status requests.

---

## 🧱 Every platform follows the same life cycle

Every entity platform shares one setup skeleton (`custom_components/myhome/discovery.py`). For each gateway a platform runs, in order:

| Step | What happens | Why it matters |
| :--- | :--- | :--- |
| **Restore** | Entities already in the entity registry are re-created immediately. | Your names, areas and entity ids exist before the gateway has said a word; a restart never shows an empty dashboard. |
| **Configure** | Devices declared in `myhome.yaml` that are not in the registry yet are created. | The configuration is the source of truth for names, device classes and options. |
| **Discover** | The first frame from an unknown address creates the entity. | New actuators appear on their own; nothing needs a restart. |
| **Route** | Every frame is delivered to the entities that own the address, through the gateway's frame router (`router.py`). | An entity is subscribed the moment it is created, under every spelling of its address, so no frame of a burst is lost while Home Assistant adds it. |

Addresses follow the OpenWebNet `WHERE` conventions - point-to-point `APL` (`12`), area `A` (`1`), group `#G` (`#5`), general `0` - plus the F422 bus-routing form `APL#4#<bus>` (`0311#4#01`). An area-0 point with PL 1..9 is spelled in two digits everywhere (`04`, not `0004`): `myhome.yaml` devices, `expose:` entries and the UI expose flow resolve `0004` to `04`, the form the gateways accept and report (#147). Area, group and general frames never create an entity: they are broadcasts, not devices (the alarm central unit is the one subsystem where `WHERE = 0` is a real device). Translation frames (`*1*1000#1*14##`) never create an entity either, and are ignored by every platform except WHO 2 covers, which route them to known covers and scope covers so stop translations (`*2*1000#0*<WHERE>##` / `*2*1000#10…*<WHERE>##`) halt active runs (#645; a start translation `*2*1000#1*` / `*2*1000#2*` that no status of that shutter follows within one second reaches its existing cover as the movement it stands for, dated when it arrived, because a MyHOMEServer1 now and then relays only the translation while the motor runs, unless the bus showed that shutter locked via WHO 14 - see [Covers](covers.md#timed-cover-position-engine)).

Platforms only add what differs: which `WHO` they serve, how a device is built, and a few hooks - the light platform hands WHO 1 frames for configured switches and motion / illuminance sensors to those platforms instead of creating a light, the cover platform relays a general `*2*x*0##` to every cover, the media player maps stereo-module pseudo zones (`10x`-`14x`) to amplifier `x`, the climate platform reads the zone a heating frame concerns from its parameters (`*4*4001#5*0##` is about zone 5), and the sensor and binary-sensor platforms run one such cycle per subsystem they serve (energy meters, illuminance and temperature probes; dry contacts, auxiliary channels and motion sensors). A meter address owns one entity per measurement it reports. Contacts and probes remember every spelling of their address (`0021` and `21`) so a renamed entity is never duplicated after a restart.

Buttons have no bus address: lock / unlock and calibrate buttons are created for the actuators the other platforms announce.

---

## 🔁 Reconnect cycles are silent

OWNd's event session returns *no message* for the read cycle in which it transparently reconnects (gateway-side idle close, keep-alive timeout, cable pulled). The integration skips that cycle at `DEBUG` level. The `Event connection lost, reconnecting...` line that accompanies it is OWNd's own log and is expected on gateways that close idle event sockets (MH200/MH201). *(#304)*

---

## 🔐 Authentication failures use Home Assistant's reauth

If the gateway rejects the OpenWebNet password during setup, the integration raises `ConfigEntryAuthFailed`. Home Assistant then shows the entry as **Reauthentication required** with a repair prompt, instead of *Failed to set up*, and opens the reauth flow itself. Any other connection-test failure raises `ConfigEntryNotReady` so Home Assistant retries with backoff.

---

## 🌡️ Zones that have not reported ask once, 30 s after the sweep

What a zone is asked when it is added depends on the gateway: the zone status request `*#4*<WHERE>##` (F454, F460, F461, MH202, MyHomeServer1 and any model that is in no table), its temperature and its knob (F450, F459, H4684, MH200, MH201), its knob only (F452, F453, F453AV, F458, MH200N), or nothing (F455). Where none of that brings the setpoint, nothing else asked for it: a standalone thermostat on an F455 could stay `unknown` for hours (#466), and the zones of an MH201 had no `temperature` after every restart until a thermostat published a change (#932). Whether a gateway answers `*#4*0##` or `*#4*<WHERE>##` with the state of a zone is known for the models in the request tables and unknown for any other, so the delayed poll has no model list.

Behaviour, on every gateway:

- 30 s after the startup sweep, and not before its own startup poll has finished (the zones of a large plant are polled one after another, which can take longer than that), a zone that has not reported its temperature, setpoint and mode asks for what is missing, one read at a time: `*#4*<zone>*0##` (only when the temperature is missing), `*#4*<zone>*13##` (the knob), `*#4*<zone>*14##`, `*#4*<zone>*12##` (the F455 follows it with the zone's season frame) and `*#4*<zone>#1*20##`. A value restored from before the restart does not count as reported; it is shown until the zone's own value arrives.
- It goes by the result: a zone that got everything from the sweep, from its status request or from its thermostat sends nothing. A zone that answered neither of its last two status requests (`unresponsive_zone` repair) is left alone until it is probed again.
- The zones are asked one after another, and each read waits for the previous answer and then for the bus to be quiet (no frame for 0.75 s, the pacing of the startup polls, #886). A request a zone makes on its own while the poll runs (dimension 19 and the fan speed, once an actuator reported a fan coil) is sent first, with the same quiet time, and the poll's next read follows it. A refused or unanswered read ends the poll, so a zone without a probe (an actuator-only channel) costs one read. Unanswered means a NACK, no reply at all within 60 s, or an ACK that came without a frame while the zone itself sent nothing within one more second: an ACK alone is not an answer (a physical F455 ACKs `*#4*0##` after about 9 s and sends nothing). A read that was dropped because it collected only another zone's frame (the guard of #886) says nothing about its own zone: it is sent once more after the quiet time, unless the zone's own answer arrived on the event session in the meantime (for the read of an actuator, that actuator's own report; an answer of dimension 19 does not stand in for it). On a physical F455 plant the state of an actuator came twice, 0.09 to 0.27 s apart, after one read, and the next read collected the second copy (`tests/fixtures/traces/issue_932`); the quiet time lets such a frame pass before the next read opens. On an F455 that read is refused only after about 9 s and holds a command session for those 9 s, once per zone added or reloaded (`F455_direct_requests_zone3_2026-11-06.jsonl`).
- What is asked when a zone is added does not change. The knob read is sent then (#466 / #902). The poll does not repeat a knob read that was answered; one that brought nothing is repeated once, as the last read of a zone that is still missing its temperature, setpoint or mode, so that its refusal cannot hold back the setpoint reads. The poll does not ask for a knob that a zone left out of its answer to `*#4*<WHERE>##`. Only the F455 keeps the knob read for the poll (`KNOB_READ_IN_SILENT_ZONE_POLL`): its zones ask nothing when they are added (`*#4*0##` returns nothing and `*#4*<WHERE>##` is ACKed only after about 9 s; sixteen of them timing out one after another was #674).
- `*#4*<zone>#1*20##` is not asked on F453, F453AV, H4684, MH200, MH200N and MH202, which drop the actuator request (`ACTUATOR_REQUEST_REFUSED`). A zone that misses nothing but its actuators was already asked for them when it was added (`*#4*<WHERE>*20##`), so it asks for actuator 1 here only on an F455, where that request is not sent; thermo actuator switches (`*#4*<WHERE>#<N>*20##`) still ask, and the F455 answers them. After a restart the poll asks for every actuator the zone had reported before it (the numbers are kept, not the states) instead of actuator 1 alone.
- On an F455 the last read is `*#4*<zone>*19##`. The F455 refuses the forms that would list one zone's actuators (`*#4*<zone>*20##` and `*#4*#0*20##` at once, `*#4*<zone>#0*20##` and the read of an absent actuator after about 9 s) and answers the published request for all actuators, `*#4*0#0*20##` (live F455 1.0.76, 2026-11-18: the five actuators of a three-zone plant within 0.4 s, ACK after about 1.4 s). That request is sent once when the zones are added and after a reconnect; a zone whose actuators it reported is not asked for `*#4*<zone>#<N>*20##` here. Dimension 19 carries the state of both sides of the zone in one frame: `hvac_action`, the fan coil's side and the running fan speed do not depend on which actuators are known. It is sent after the actuator reads because the most recent report drives `hvac_action`, and it is not sent when the zone already asked for it (an actuator that reported a fan coil) or has its answer and no actuator took the lead after it. The zone's own request can leave before the read of a later actuator; when that actuator's first report then took the lead, the poll still ends with `*#4*<zone>*19##`.
- After dimension 19 reported an active side, an "off" of an actuator that was already known to be off changes nothing (every gateway): the actuator that runs is one that has not reported. On the physical F455 plant another client read actuator 1 of zone 4 through the F461, 23 s after the zone had answered `*#4*4*19*8*0##`, and the answer `*#4*4#1*20*0##` set the zone to `idle` for 3 s while the fan coil on actuator 2 ran (2026-11-17, `tests/fixtures/traces/issue_932`). Everything else changes the zone as before: the first report of an actuator, an actuator that starts, the "off" of one that was on, and the next dimension 19.
- On a shared bus the gateway that serves WHO 4 decides the knob and the actuator read, and a passive standby or secondary asks nothing.

Evidence:

- F455 1.0.76, physical (`tests/fixtures/traces/issue_466/F455_direct_requests_2026-11-06.jsonl`): each read is answered in about 0.1 s.
- MH201 3.6.43, physical, four standalone zones (#932): explicit reads of dimensions 12 and 14 were answered, and with the poll all four zones showed their setpoint after a full restart.
- F450, F453, F453AV, F459, H4684, MH200 and MH200N: in the firmware oracle each of `*#4*<zone>*0##`, `*12##`, `*13##` and `*14##` goes out to the bus; the poll has not yet run on a physical plant. F452 and F458 are not in the oracle.

---

## 🌡️ Temperature probes (`WHERE ≥ 100`) are push-driven

Slave / external probes use the `ZPP` address form (zone `Z`, probe `PP`, e.g. `101`). Devices such as a **3455** behind an **L4577** radio interface push `*#4*ZPP*0*T*3##` unsolicited every few seconds and **NACK** the explicit `*#4*ZPP*15##` poll.

Behaviour:

- A probe entity starts **receive-only**: no request is sent when it is added.
- The periodic update (every 5 minutes) only sends a poll if **no reading arrived within the last 15 minutes**, so a probe that goes silent (battery, radio) still recovers. Not every probe reports every few seconds: one on a physical F455 / F461 plant reported every 10 minutes, and with a 5-minute window every second update sent a read that the F455 NACKed only after about 9 s.
- A read the gateway **refused** (a NACK, or no reply before the command timeout, while the gateway was connected and the probe sent nothing during the read) is **not sent again** until the entry is reloaded; the probe's own reports are still shown. A read that was not answered at all because the command session could not be opened or lost its connection is not a refusal, and neither is one that collected only another zone's frames (the guard of #886, which covers the read of a probe too).
- Zone sensors (`WHERE < 100`) keep polling `*#4*Z*0##` as before.

*(#308)*

---

## 🌡️ Central heating units (`#0` / `#0#1`) are broadcast event-driven

Central thermoregulation units (such as the BTicino **3550** at address `#0` and the **4695** 4-zone unit at `#0#1`) manage subordinate zones and set plant-wide seasonal modes (heating, cooling, off, auto).

Central units handle status updates differently from subordinate zones (`1..99`):

- **Dimension 14 queries rejected**: Gateways reject Dimension 14 status queries (`*#4*#0*14##`) with fast NACK, as Dimension 14 status queries are only defined for zone thermostats `1..99` (#629).
- **Plain status requests supported**: Authentic physical gateway traces (#629) confirm that 99-zone central units (`#0`) reliably answer the plain status request `*#4*#0##` within ~0.17 s, returning their operating mode (`*4*202*#0##` for conditional OFF) and status flags (`*4*21*#0##`, `*4*22*#0##`, `*4*24*#0##`).
- **Startup Polling**: On startup and poll intervals, the integration queries central unit `#0` with `*#4*#0##`. Answering populates the seasonal mode and coordinates subordinate zones. This poll is informational: an unanswered `*#4*#0##` never raises an `unresponsive_zone` repair (only an F454 has been captured so far). 4-zone central units (`#0#1`) remain exempt to prevent gateway timeouts.

Central units coordinate with the plant as follows:

1. **Startup Hydration & Polling**: On restart, the central unit entity is restored from its previous state (`async_restore_last_state()`) and sends canonical status request `*#4*#0##`, updating its mode and subordinate zones upon answer.
2. **Outbound Mode Commands**: When commanded from Home Assistant, the central unit receives canonical seasonal mode commands (`OWNHeatingCommand.set_central_mode`):

    - `*4*303*#0##` (Central unit OFF command)
    - `*4*1*#0##` (Heating season command)
    - `*4*0*#0##` (Conditioning season command)
    - `*4*3100*#0##` (Auto / restore scheduled programming command)
    - `*#4*#0*#14*<temp>*<mode>##` (Target temperature setpoint command)

    *(Note: `WHAT = 103` and `102` — Heating OFF and Antifreeze protection — are inbound status reports emitted by thermostats/central units on the bus, never outbound `#0` mode-switch commands.)*
3. **Inbound Operating Mode & Status Broadcasts**: The physical central unit broadcasts events across the bus whenever its state changes:

    - Operating mode status: `*4*110*#0##` (Manual Heating status), `*4*210*#0##` (Manual Cooling / Conditioning status), `*4*311*#0##` (Programmed/automatic generic status; mapped to `HVACMode.AUTO` in Home Assistant), `*4*303*#0##` / `*4*103*#0##` (OFF status), `*4*202*#0##` (Conditional OFF status), `*4*102*#0##` (Antifreeze status).
    - Target temperature setpoints: `*4*110#<temp>*#0##` (Heating setpoint), `*4*210#<temp>*#0##` (Cooling setpoint).
    - Remote control / local adjustments: `*4*20*#0##`, `*4*21*#0##`, `*4*40*#0##`.
    - Programs & scenarios: `*4*1101*#0##` .. `*4*1103*#0##` (Weekly heating programs 1..3), `*4*1201*#0##` .. `*4*1216*#0##` (Scenarios 1..16).
4. **Canonical Status Polling**: Central unit `#0` is queried with plain status request `*#4*#0##`, avoiding the rejected Dimension 14 query (`*#4*#0*14##`) while ensuring the central unit's initial state is populated at startup.
5. **Subordinate Coordination**: When the central unit changes seasonal mode (either via command or inbound broadcast event), it dispatches an internal event (`myhome_central_mode_<mac>`) so non-standalone subordinate zones synchronize their operating mode immediately.
6. **Bus Discovery Isolation**: All broadcast frames emitted by a 3550 central unit carry address `#0`. They never resolve to or invent phantom zone `99` (commercial name *"Centrale termoregolazione 99 zone"*). If an installation physically includes a Zone 99 thermostat, on-wire frames directed to zone 99 (`*4*...*99##`) will discover and manage it normally.

*(#582, #629)*

---

## 🛡️ `WHO = 4` Firmware Refusal Guards

Several BTicino / Legrand gateway firmwares deterministically NACK, drop, or crash on specific `WHO = 4` requests. The runtime enforces model-specific protocol guards in `gateway.py`, `climate.py`, and `services.py` before any frame reaches the TCP command queue:

- **`GENERAL_ZONE_REQUEST_REFUSED`**: Broadcast `*#4*0##` status sweeps are suppressed on `F450`, `F454`, `F455`, `F459`, `F460`, `F461`, `H4684`, `MH200`, `MH201`, `MH202`, and `MyHomeServer1`, where firmware returns immediate NACK or silent timeout (a physical `F455` 1.0.76: an ACK after about 9 s and no frame).
- **`ZONE_ACTUATOR_REQUEST_REFUSED` (`F455`)**: On `F455`, `*#4*0##` returns nothing, `*#4*<zone>*20##` is refused immediately, and unanswered `*#4*<zone>##` polls stall the command socket for ~9 s. Climate zones skip startup polling and zone-wide `*#4*<zone>*20##` watches.
- **`KNOB_READ_IN_SILENT_ZONE_POLL` (`F455`)**: The delayed sequential direct-read recovery described above (30 s after the sweep: `*#4*<zone>*0##`, `*13##`, `*14##`, `*12##`, `*#4*<zone>#<N>*20##` for each actuator the zone had reported before the restart, or for actuator 1 when there is none, and on `F455` `*#4*<zone>*19##` last, for what a zone has not reported) runs on every gateway and has no model table. Only on `F455` is the knob read `*#4*<zone>*13##` part of that sequence instead of being sent when the zone is added (#466, #674).
- **Local-bus `#4#II` write refusal**: Gateway translators do not implement `#4#<interface>` sub-bus routing on `WHO = 4` thermoregulation writes; outbound `WHO = 4` frames carrying `#4#II` are refused rather than stalling or NACKing on the gateway.
- **`HUMIDITY_REQUEST_REFUSED`**: Explicit Dimension 60 (`*#4*<zone>*60##`) humidity polls are suppressed on `F450`, `F453`, `F453AV`, `F454`, `F455`, `H4684`, `MH200`, `MH200N`, and `MH202` (zones still decode unsolicited Dimension 60 reports whenever broadcast on the bus).
- **`ACTUATOR_REQUEST_REFUSED`**: Explicit Dimension 20 (`*#4*<zone>*20##`) actuator status polls are suppressed on `F453`, `F453AV`, `H4684`, `MH200`, `MH200N`, and `MH202`.
- **Non-split Dimension 22 crash guard (`is_refused_dimension_22`)**: Non-split `WHO = 4` Dimension 22 queries such as `*#4*1*22##` or `*#4*0*22##` crash the thermostat daemon on `F461`, `F454`, and `MH200N` and are refused in `myhome.send_message` (split addressing `*#4*3#Z#N*22##` remains permitted).

---

## 🪟 Timed covers: clock starts at the write, echoes are not keypad presses

Covers without position feedback (`advanced: false`) estimate their position from `travel_time`. Measured on a MyHOMEServer1, this is what the bus does after Home Assistant queues a direction command:

```
enqueue ─(queue wait: ~1.5 s per queued frame, up to ~12 s with twelve covers)─▶ frame written
   +0.10 s  gateway relays a real stop status   *2*0*<where>##
   +0.15 s  translation                          *2*1000#<dir>*<where>##
   +0.55 s  motor starts, direction status       *2*<dir>*<where>##
stop:       motor stops ~0.08 s after the stop frame is written
```

The v2 model therefore:

1. **Starts the clock at the write, not at enqueue.** The gateway's send queue reports when each frame actually left the socket (after any reconnect of the command session, and only when the gateway acknowledged it); with several covers commanded together the last one can leave many seconds after it was queued. A frame that is never delivered (connection lost, NACK) cancels its report, and the cover falls back to a run timed from now.
2. **Opens an echo window** for each command, from enqueue until 1.5 s after the write (bounded: it closes at once when delivery fails, and 30 s after enqueue at the latest). Inside it, a relayed stop status is deferred while our own direction status **re-anchors the clock to the real motor start** — no gateway-specific latency constant needed. A gateway that relays neither stop nor direction status gets the measured 0.55 s motor-start delay added to the write time instead; if a plain `*2*0*<where>##` stop arrived inside the window and no `*2*1*` / `*2*2*` motor-start status followed before the window expired, that stop was terminal and ends the optimistic run (`#959`). On gateways that omit the synthetic SCS pre-move stop (`MH201`, `MH202`), timed covers whose relay may still be energized (`65 s` window, cleared on `*2*0*<where>##`) automatically send `*2*0*<where>##` and wait `0.6 s` after socket write before sending the direction frame (`#959`). An opposite-direction frame, or any frame after the window, is a genuine command (wall switch, scenario) and is handled normally.
3. **Times `set_position` from that anchor**, then re-anchors at the target when the timer fires and lets the stop frame's own write time settle the estimate (target plus whatever the queue delay added).
4. **Freezes the estimate for a stop provisionally at its write**, not at enqueue, and repaints the entity at every write so the estimate is responsive without waiting for a status frame. Gateways that never relay stop status keep this write-time freeze.
5. **Re-anchors at the stop status echo**, when the gateway relays it within the echo window (34–47 ms, median 42 ms on MyHOMEServer1; 64–120 ms on F461). When the stop status arrives, the final position and `last_run_seconds` are re-anchored from `stop status - start anchor`.
6. **Prioritises cover stops ahead of queued commands** (`#645`), while preserving FIFO order relative to earlier stops and never overtaking pending motion frames for the same cover or overlapping scopes (such as area or general broadcasts). Across multi-worker gateways, workers acquire sequential tickets at dequeue to keep wire delivery strictly FIFO regardless of session pacing.

Trade-off: on gateways that do not relay a stop status on keypad presses, a wall-switch stop pressed in the ≤ 0.6 s between the write and the motor start is not distinguishable until the motor fails to start; when `*2*0*<where>##` is relayed and no motor-start frame follows inside the 1.5 s window, the cover settles as stopped when the window expires (`#959`). Once the motor-start status arrives the window closes and wall-switch stops are honoured immediately.

**Timing edits and panel measurements.** After a native `myhome.set_cover_travel_time` edit is saved, the running cover uses the new travel times and recomputes its pending full-run timer. A failed save leaves the current timings unchanged. Profile assignments and geometry edits in the panel still wait until the cover stops. While a panel calibration session is measuring a run, the stored travel timer never declares an endpoint: the session uses the operator's confirmation or its own measurement flow. A completed full-run timer releases its task so the stationary cover can immediately enter guided calibration.

**Calibration.** Because the actuator reports its motor start and stop, a timed cover can measure its own travel: `myhome.calibrate_cover` (or the *Calibrate travel time* button on the cover's device, or *Calibrate all covers* on the gateway) drives the cover fully up, then fully down (timed), then fully up (timed), one cover at a time per gateway, and stores the up and down times in the config entry options. The model then uses the up time when opening and the down time when closing. Measured values are the actuator's run times — equal to the physical travel on an installer-calibrated actuator; otherwise an upper bound. A run that ends at a whole-minute configurator limit (60 s, 120 s, …) is refused and nothing is stored; a run-time parameter the installer set longer than the travel (30 s on a 14 s shutter) is not detectable on the bus and is stored as is — a run stopped by hand (`last_run_seconds`) is the only check. See [Services → `myhome.calibrate_cover`](services.md#7-myhomecalibrate_cover).

*(#302, #645, #705, #959, measurements by @Interstellar0verdrive, @gdluck and @ermanno00)*

---

## 💡 A light that does not report after a command is read back once

A `WHO = 1` light or switch takes its state from the actuator's status frame on the event session, never from the command or its `ACK`. Some gateways leave that status out: a physical `F455` (firmware `1.0.76`) acknowledges a command in about 4 ms and, when several commands follow each other within tens of milliseconds (a scene, "all off", tiles tapped in a row), its event session omits some of the statuses although the lights switched. Home Assistant then kept the old state until the light changed again, and the header toggle of an entities card, which only commands the entities not yet shown in the target state, left such a light on at the next "all off" (live 2026-11-20, six lights in one call: the `F455` left out 1 or 2 statuses in 5 of 8 steps and a light stayed on in 3 of 4 on / off cycles; through the `F461` on the same bus none was left out).

The gateway handler (`gateway_confirm.py`) therefore notes every acknowledged point-to-point `WHO = 1` command. If no matching status of that light arrived `1.5 s` (`ECHO_CONFIRM_DELAY_S`) after the `ACK`, it sends one status request `*#1*<where>##`; the answer comes back on the command session and updates the entity.

- **Every gateway model.** The read-back does not look at the model: each gateway entry notes its own acknowledged commands, and the `F455` is where the loss was measured, not a condition. A gateway that drops a status under load (a large plant, a slower model) gets the same read-back.
- **No extra traffic where the status arrives.** On a physical `F461` and in the real-user traces of `F454`, `F461`, `MH200N`, `MH201`, `MH202` and `MyHomeServer1` every status followed its command within `0.33 s`, so nothing is sent there. On a physical `MH200` (firmware `2.1.0`) the same six-light bursts lost no status in 192 commands, with one or two command sessions (measured by a maintainer in the review of #964): that gateway acknowledges a command only after the bus telegram, where the `F455` acknowledges in about 4 ms.
- **One request at most per command.** A status request never arms the read-back, so an address that does not answer costs one request and nothing more.
- **A status of the other polarity is not the answer.** After `ON` then `OFF` in quick succession, a late `ON` status does not cancel the read-back of the `OFF`. A level write says its polarity too (`*#1*<where>*#1*<100 + level>*<speed>##` and the `#4` form: level `0` is off; `*#1*<where>*#12*<H>*<S>*<V>##`: `V = 0` is off), so the late `OFF` of the command before does not answer it (seen on an `MH200` with an `F418U2`).
- **Only a status that says on or off counts.** A request or a write that the gateway relays on its event session (an `MH202` relays a level write about 30 ms before the dimmer's status), a frame with no on / off meaning (colour, toggle, motion, anomaly) and a dim step (`*1*30*<where>##` / `*1*31*<where>##`, which the light entity ignores) neither end the wait nor replace the status remembered for the light.
- **The read-back carries the whole `WHERE`**: `09#4#01` behind an interface, `<id>#9` on a Zigbee gateway.
- **Only for commands this gateway entry sent.** When another client switches several lights at once (another gateway, a scenario module), the `F455` leaves statuses out in the same way and nothing here notices: the light keeps its old state until its next status.
- **Not used for** group / area / general commands (see *Broadcast re-sync* above), frames sent for exposed (virtual) addresses, and `WHO = 2` covers, whose position model is anchored on the write time.

---

## 🎨 Lights learn colour capabilities additively

Colour modes are promoted from bus frames, and never removed:

| Frame received | Capability added |
| :--- | :--- |
| dimension `12` (HSV) | `hs` |
| dimension `14` (colour temperature) | `color_temp` |
| dimension `1` / brightness preset | `brightness` (only if no colour mode yet) |

A DALI DT8 driver behind an F461 reports both `12` and `14`; the entity ends up with `supported_color_modes: [hs, color_temp]` and its active `color_mode` follows the last frame. Previously each frame *replaced* the set, flipping the entity between colour-picker-only and tunable-white-only. State restoration keeps the full set and the last active mode. *(#307 part 1)*

> Explicit `myhome.yaml` capability locks (`rgb:` / `color_temp:` as authoritative) and ignoring the gateway's default `*12*511*127*255` / `*14*1` values are planned follow-ups.

---

## 🧭 Device links

Entity devices and CEN / CEN+ scenario units are linked to their parent gateway device via `via_device_id` (`MyHOME` v2 requires Home Assistant 2026.8+, and legacy `via_device` tuples are no longer used). Identifiers are unchanged, so existing devices are matched in place. *(#310)*

---

## 📦 OWNd version

The installed OWNd version is resolved **once, in the executor**, when the integration is set up and cached; nothing reads package metadata from the event loop. `manifest.json` is the single source of truth for the pin — Home Assistant installs it, and the integration no longer tries to (re)install or reload OWNd at runtime. *(#309)*
