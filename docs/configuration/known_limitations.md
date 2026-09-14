# Known Limitations

Things the integration does not do, or does with a caveat, and the reason. Where a limitation is a design decision it links to the discussion; where it is a gap it names the trace or hardware needed to close it (see the [Roadmap](../../ROADMAP.md) and [RFC #248](https://github.com/orgs/OpenWebNet-HA/discussions/248)). The full feature matrix is in [Supported Functions](supported_functions.md).

## Bus and gateway

| Limitation | Why | Workaround |
| :--- | :--- | :--- |
| **One command session per gateway on MH200 / MH200N / MH201.** Commands are queued and paced (150 / 80 / 60 ms). | Those gateways refuse or drop overlapping sessions; the pacing is what keeps them alive. | Keep the worker count at the profile default. F454 / F455 / MyHOMEServer1 / MH202 take more workers. |
| **Lights are not hydrated at startup.** State appears when the actuator first reports, or after `myhome.sweep_bus`. | `*#1*0##` is not a valid OpenWebNet request; there is no general status query for WHO 1. | Call `myhome.sweep_bus` from an automation on `homeassistant.start`, or turn something on. |
| **Entities can be unavailable for up to 60 s after a gateway drop.** | The availability grace period hides short reconnects instead of flapping every entity. | Nothing needed; a longer outage marks entities unavailable and they recover on reconnect. |
| **A gateway's model can be mislabelled.** | Only the WHO 13 device-type reply is available in-band, and its official table stops at 2006 hardware. | The [identification rules](gateways.md#-how-the-gateway-model-is-identified) correct manual choices and raise a repair issue when evidence contradicts SSDP; use the reconfigure flow to set the model explicitly. |
| **Serial (Legrand 3578) gateways are not discovered.** | No SSDP on a USB port. | Add the integration manually and pick the serial transport. |
| **No HMAC on MH200 / MH200N / MH201 / AM4890 / 3578.** | Those gateways only implement the numeric (SHA-less) password. | Configure the numeric OpenWebNet password on the gateway. |

## Lighting (WHO 1)

| Limitation | Why | Workaround |
| :--- | :--- | :--- |
| **Group, area and general commands from wall switches do not update entities unless each actuator reports.** (P7, RFC #248) | Whether actuators echo an individual status after `*1*1*#group##` or `*1*0*0##` depends on the actuator and gateway; most modern plants do, older ones do not. A community trace is still needed to design the fallback. | `myhome.sweep_bus` after the group command, or a `myhome_event` automation that sends `*#1*<where>##` for the members. |
| **Colour modes are learned, not configured.** A DALI DT8 light shows colour temperature only after its first dimension 14 frame. | The bus does not describe an actuator's capabilities; it only reports what it does. | Set `color_temp: true` / `rgb: true` / `hs: true` in `myhome.yaml` to declare the mode up front. |
| **Native transitions depend on the actuator.** | Some dimmers ignore the fade parameter. | Keep the default `software_stepped` transition mode. |

## Covers (WHO 2)

| Limitation | Why | Workaround |
| :--- | :--- | :--- |
| **Timed covers report a calculated position.** It drifts if the motor runs at a different speed than assumed, or after a manual stop mid-travel. | The actuator has no encoder; only position-reporting actuators (dimension 10) know where the shutter is. | Calibrate with `myhome.calibrate_cover` (or the card's stopwatch); a full open or close resynchronises the estimate. |
| **Calibration on an MH200 / MH200N can fail** with *no stop status from the actuator*. | The single-session gateway delays or drops the actuator's stop status; some actuators also enforce a 60 s safety cut-off that ends the run early. | Use `myhome.set_cover_travel_time` with a stopwatch value. The card's Covers panel preselects manual mode on those gateways. |
| **One calibration at a time per gateway.** | Two motors on one paced session make the timings meaningless. | Queue is automatic; `myhome.stop_cover_calibration` cancels it. |
| **No slat / tilt control.** | Not implemented; the frames exist in OWNd. | `myhome.send_message` with the tilt frame. |

## Thermoregulation (WHO 4)

| Limitation | Why | Workaround |
| :--- | :--- | :--- |
| **Weekly programs, holiday and scenario modes are not exposed.** | Only manual set-point, auto, off and the seasonal modes are modelled on the climate entity. | `myhome.send_message` with the central-unit mode frame (`*4*<mode>*#0##`). |
| **4-zone central unit 4695 (`#0#1`) is verified on synthetic frames only.** | No community trace from a 4695 plant yet. | Please attach a trace to RFC #248 if you own one. |
| **Probes (`WHERE ≥ 100`) are read-only and push-driven.** | They NACK the explicit poll (`*#4*ZPP*15##`) and push readings on their own schedule. | Nothing needed; a probe that goes silent is polled again after 5 min. |

## Burglar alarm (WHO 5)

| Limitation | Why | Workaround |
| :--- | :--- | :--- |
| **Zones are not entities; the panel follows the central unit.** Some plants (F454 + 3486) surface several zone objects with no arm / disarm frames answered (#311). | WHO 5 zone and sensor discovery needs a bus trace from a physical central unit to model correctly; the current implementation is built on golden frames. | Zone events are available as `myhome_event`; a trace attached to #311 unblocks the design. |
| **Arming requires the central unit to accept the command.** | Central units reject arming while a zone is open or the engineer code is active; the bus only reports the refusal. | Watch the bus-monitor card for the NACK. |

## Sound system (WHO 16)

| Limitation | Why | Workaround |
| :--- | :--- | :--- |
| **Streaming needs a network decoder per concurrent stream.** With two decoders mapped, a third room asks for *All audio matrix inputs are currently in use*. | The F441 matrix routes one physical input per source; the integration claims one decoder per playing zone. | Map more decoders in the options flow, or group rooms on the decoder side. |
| **The legacy FM tuner (WHO 22) is not supported.** | Deferred in RFC #248; streaming replaces it. | — |

## Scenario buttons (WHO 15 / 25)

| Limitation | Why | Workaround |
| :--- | :--- | :--- |
| **CEN / CEN+ devices are stateless.** They have no entity, only device triggers. | Pushbuttons report presses, not a state. | Use the device triggers (see [CEN & CEN+](cen_cenplus.md)) or the [community blueprint](https://community.home-assistant.io/t/myhome-cen-commands/260345). |
| **Multi-click bursts are verified on synthetic frames only.** | No physical wall-switch burst trace yet. | Please attach one to RFC #248. |

## Home Assistant integration surface

| Limitation | Why | Workaround |
| :--- | :--- | :--- |
| **Entity names are legacy (`has_entity_name = False`).** New installs get `light.<name>` ids built from the configured name rather than device + entity naming. | Changing it renames entities on new installs and is being decided in the community RFC (quality-scale rule `has-entity-name`). | Existing installs keep their entity ids either way. |
| **Entity names are not translated.** | Follows the naming decision above. | — |
| **Strict typing is incomplete.** | `mypy --strict` still reports errors in older modules; being ratcheted down. | — |
| **`myhome.yaml` is a compatibility path, not the primary configuration.** Devices are discovered from the bus; YAML only adds names, device classes and options. | v2 is UI-first. | Keep `myhome.yaml` for names and `travel_time`; delete devices you no longer want from the device page. |
| **Deleting a device does not stop it from coming back.** | Devices are re-discovered from bus traffic; a device that still exists reappears on its next status frame. | Only delete devices that are physically gone. |
| **Home Assistant 2026.3 or newer is required** (`hacs.json`). | Current cores need Python 3.14 and dropped the pre-2026 device-registry and static-path APIs; carrying shims for cores nobody can test any more is not honest support. | Stay on integration 2.0.0b12 on older cores. |
