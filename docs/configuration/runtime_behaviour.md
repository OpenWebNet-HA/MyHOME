# Runtime Behaviour Notes (v2)

How the v2 integration decides *what to poll*, *what to trust from the bus*, and *what it learns at runtime*. These are the behaviours most often mistaken for bugs, with the issue that motivated each one.

---

## 🔌 Startup discovery is gated by the gateway profile

After the event session is up, the integration sends a small set of general status requests to hydrate entities:

| Frame | Subsystem |
| :--- | :--- |
| `*#1*0##` | Lighting |
| `*#2*0##` | Automation / covers |
| `*#4*0##` | Thermoregulation |
| `*#16*0*5##` | Sound system (dimension 5; lists every amplifier) |

Each request is only sent when the gateway's OWNd **profile** advertises that WHO (`GatewayProfile.supported_who`). Unknown gateways keep the full set.

The requests go out one at a time. The gateway ACKs a general request at once, but the replies (one frame per device, on the event session) keep coming for seconds on a large plant, and a second request sent in that window cuts the first reply short ([#578](https://github.com/OpenWebNet-HA/MyHOME/issues/578)). So after each request is written, the next one waits until the event session has been quiet for 0.75 s, at most 15 s. A request that is NACKed or never written still gets that wait. `myhome.sweep_bus` paces its general requests the same way, so a sweep on a large plant takes a few seconds per subsystem. Entities restored at startup poll their own state only after the whole sweep has finished.

> OWNd 2.0.0b8 and earlier give an **MH200** the MH200N profile, which does not advertise WHO 16, so amplifiers behind an MH200 only appear once they send bus traffic. A live MH200 answers `*#16*0*5##` with every amplifier and source; [OWNd#53](https://github.com/OpenWebNet-HA/OWNd/issues/53) gives it its own profile.

> The WHO 16 status request is `*#16*WHERE*5##` (spec section 1.5.2). Gateways NACK the bare `*#16*0##` for every address, whether or not the plant has audio, so an old NACK on that frame never meant "no audio".

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

   - For an **area**, only point-to-point echoes in that matching area cancel its timer;
     unrelated areas stay armed.
   - For a **group**, receiving multiple member echoes ($\ge 2$) in the window cancels its
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

Addresses follow the OpenWebNet `WHERE` conventions - point-to-point `APL` (`12`), area `A` (`1`), group `#G` (`#5`), general `0` - plus the F422 bus-routing form `APL#4#<bus>` (`0311#4#01`). Area, group and general frames never create an entity: they are broadcasts, not devices (the alarm central unit is the one subsystem where `WHERE = 0` is a real device). Translation frames (`*1*1000#1*14##`) are ignored as well.

Platforms only add what differs: which `WHO` they serve, how a device is built, and a few hooks - the light platform hands WHO 1 frames for configured switches and motion / illuminance sensors to those platforms instead of creating a light, the cover platform relays a general `*2*x*0##` to every cover, the media player maps stereo-module pseudo zones (`10x`-`14x`) to amplifier `x`, the climate platform reads the zone a heating frame concerns from its parameters (`*4*4001#5*0##` is about zone 5), and the sensor and binary-sensor platforms run one such cycle per subsystem they serve (energy meters, illuminance and temperature probes; dry contacts, auxiliary channels and motion sensors). A meter address owns one entity per measurement it reports. Contacts and probes remember every spelling of their address (`0021` and `21`) so a renamed entity is never duplicated after a restart.

Buttons have no bus address: lock / unlock and calibrate buttons are created for the actuators the other platforms announce.

---

## 🔁 Reconnect cycles are silent

OWNd's event session returns *no message* for the read cycle in which it transparently reconnects (gateway-side idle close, keep-alive timeout, cable pulled). The integration skips that cycle at `DEBUG` level. The `Event connection lost, reconnecting...` line that accompanies it is OWNd's own log and is expected on gateways that close idle event sockets (MH200/MH201). *(#304)*

---

## 🔐 Authentication failures use Home Assistant's reauth

If the gateway rejects the OpenWebNet password during setup, the integration raises `ConfigEntryAuthFailed`. Home Assistant then shows the entry as **Reauthentication required** with a repair prompt, instead of *Failed to set up*, and opens the reauth flow itself. Any other connection-test failure raises `ConfigEntryNotReady` so Home Assistant retries with backoff.

---

## 🌡️ Temperature probes (`WHERE ≥ 100`) are push-driven

Slave / external probes use the `ZPP` address form (zone `Z`, probe `PP`, e.g. `101`). Devices such as a **3455** behind an **L4577** radio interface push `*#4*ZPP*0*T*3##` unsolicited every few seconds and **NACK** the explicit `*#4*ZPP*15##` poll.

Behaviour:

- A probe entity starts **receive-only**: no request is sent when it is added.
- The periodic update (every 5 minutes) only sends a poll if **no reading arrived within the last interval**, so a probe that goes silent (battery, radio) still recovers.
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
2. **Outbound Mode Commands**: When commanded from Home Assistant, the central unit receives seasonal mode commands:
   - `*4*100*#0##` (Conditioning OFF command)
   - `*4*101*#0##` (Manual Heating command)
   - `*4*102*#0##` (Manual Cooling command; integration command mapping. Note that in WHO 4 status grammar, WHAT 102 denotes Antifreeze protection)
   - `*4*103*#0##` (Auto Heating/Cooling command; integration command mapping. Note that in WHO 4 status grammar, WHAT 103 denotes Heating OFF)
   - `*#4*#0*#14*<temp>*<mode>##` (Target temperature setpoint command)
3. **Inbound Operating Mode & Status Broadcasts**: The physical central unit broadcasts events across the bus whenever its state changes:
   - Operating mode status: `*4*110*#0##` (Manual Heating status), `*4*210*#0##` (Manual Cooling status), `*4*311*#0##` (Programmed/automatic generic status; mapped to `HVACMode.AUTO` in Home Assistant), `*4*303*#0##` (Generic OFF status), `*4*202*#0##` (Conditional OFF status).
   - Target temperature setpoints: `*4*110#<temp>*#0##` (Heating setpoint), `*4*210#<temp>*#0##` (Cooling setpoint).
   - Remote control / local adjustments: `*4*20*#0##`, `*4*21*#0##`, `*4*40*#0##`.
   - Programs & scenarios: `*4*1101*#0##` .. `*4*1103*#0##` (Weekly heating programs 1..3), `*4*1201*#0##` .. `*4*1216*#0##` (Scenarios 1..16).
4. **Canonical Status Polling**: Central unit `#0` is queried with plain status request `*#4*#0##`, avoiding the rejected Dimension 14 query (`*#4*#0*14##`) while ensuring the central unit's initial state is populated at startup.
5. **Subordinate Coordination**: When the central unit changes seasonal mode (either via command or inbound broadcast event), it dispatches an internal event (`myhome_central_mode_<mac>`) so non-standalone subordinate zones synchronize their operating mode immediately.
6. **Bus Discovery Isolation**: All broadcast frames emitted by a 3550 central unit carry address `#0`. They never resolve to or invent phantom zone `99` (commercial name *"Centrale termoregolazione 99 zone"*). If an installation physically includes a Zone 99 thermostat, on-wire frames directed to zone 99 (`*4*...*99##`) will discover and manage it normally.

*(#582, #629)*

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
2. **Opens an echo window** for each command, from enqueue until 1.5 s after the write (bounded: it closes at once when delivery fails, and 30 s after enqueue at the latest). Inside it, the relayed stop status is ignored and our own direction status **re-anchors the clock to the real motor start** — no gateway-specific latency constant needed. A gateway that never relays that status gets the measured 0.55 s motor-start delay added to the write time instead. An opposite-direction frame, or any frame after the window, is a genuine command (wall switch, scenario) and is handled normally.
3. **Times `set_position` from that anchor**, then re-anchors at the target when the timer fires and lets the stop frame's own write time settle the estimate (target plus whatever the queue delay added).
4. **Freezes the estimate for a stop at its write**, not at enqueue, and repaints the entity at every write so the estimate is right without waiting for a status frame.

Trade-off: a wall-switch stop pressed in the ≤ 0.6 s between the write and the motor start is treated as an echo. The motor has not moved yet, so nothing is lost; once the motor-start status arrives the window closes and wall-switch stops are honoured immediately.

**Calibration.** Because the actuator reports its motor start and stop, a timed cover can measure its own travel: `myhome.calibrate_cover` (or the *Calibrate travel time* button on the cover's device, or *Calibrate all covers* on the gateway) drives the cover fully up, then fully down (timed), then fully up (timed), one cover at a time per gateway, and stores the up and down times in the config entry options. The model then uses the up time when opening and the down time when closing. Measured values are the actuator's run times — equal to the physical travel on an installer-calibrated actuator; otherwise an upper bound. A run that ends at the factory 60 s cutoff is refused and nothing is stored; a run-time parameter the installer set longer than the travel (30 s on a 14 s shutter) is not detectable on the bus and is stored as is — a run stopped by hand (`last_run_seconds`) is the only check. See [Services → `myhome.calibrate_cover`](services.md#6-myhomecalibrate_cover).

*(#302, measurements by @Interstellar0verdrive)*

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

CEN / CEN+ scenario units are linked to their gateway with `via_device_id` on Home Assistant cores that support it (2026.x+), with `via_device` as the fallback on older cores. Identifiers are unchanged, so existing devices are matched in place. *(#310)*

---

## 📦 OWNd version

The installed OWNd version is resolved **once, in the executor**, when the integration is set up and cached; nothing reads package metadata from the event loop. `manifest.json` is the single source of truth for the pin — Home Assistant installs it, and the integration no longer tries to (re)install or reload OWNd at runtime. *(#309)*
