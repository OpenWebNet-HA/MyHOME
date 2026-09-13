# Runtime Behaviour Notes (v2)

How the v2 integration decides *what to poll*, *what to trust from the bus*, and *what it learns at runtime*. These are the behaviours most often mistaken for bugs, with the issue that motivated each one.

---

## 🔌 Startup discovery is gated by the gateway profile

After the event session is up, the integration sends a small set of general status requests to hydrate entities:

| Frame | Subsystem |
| :--- | :--- |
| `*#2*0##` | Automation / covers |
| `*#4*0##` | Thermoregulation |
| `*#16*0##` | Sound system |

Each request is only sent when the gateway's OWNd **profile** advertises that WHO (`GatewayProfile.supported_who`). An **MH200N**, for example, has no audio subsystem and NACKs `*#16*0##`; previously that produced a `Could not send message … Retrying` error on every boot. Unknown gateways keep the full set.

> WHO=1 has no valid general status request (`*#1*0##` is not OpenWebNet), so lights are hydrated from their own status replies and bus traffic.

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
