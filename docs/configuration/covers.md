# Covers & Shutters (WHO = 2)

The **MyHOME** integration provides full control for motorized shutters, blinds, venetian blinds, and curtains operating on OpenWebNet **WHO = 2**.

In v2, setup and management are **100% UI-first**: entities are automatically discovered from the SCS bus, and position calibration is handled natively through Home Assistant buttons and services without requiring manual YAML configuration files.

---

## 🚀 Auto-Discovery

When your gateway connects to Home Assistant:

1. **Dynamic Bus Discovery**: The integration listens to OpenWebNet `WHO = 2` frames and scans the bus.
2. **Device Creation**: Each physical cover actuator (`WHERE = 1..99` or area/point addresses) is registered as a Home Assistant `cover` device linked to your MyHOME Gateway.
3. **UI Customization**: You can rename the entity, assign it to an Area (e.g. *Living Room*, *Master Bedroom*), or change its icon directly in the Home Assistant UI (**Settings → Devices & Services → Entities**).

---

## 🎛️ Cover Types: Standard vs. Advanced

The integration distinguishes between two types of MyHOME covers:

| Cover Type | Supported Hardware | Position Control (`set_cover_position`) | How Position is Handled |
| :--- | :--- | :---: | :--- |
| **Advanced Covers** | Legrand Céliane `67557`, Axolute `H4661M2`, Livinglight `LN4661M2`, BTicino `F401` (advanced mode) | ✅ Native | Actuator hardware reports exact physical position back to the bus (`Dimension = 10`). |
| **Standard (Timed) Covers** | Standard relay actuators (`F411/2`, `F411U2`, standard `F401`, older flush-mount units) | ✅ Estimated | Actuator has no position feedback. The v2 runtime estimates position from the elapsed share of the **travel time** (`travel_time_down` and `travel_time_up`). |

> [!NOTE]
> Unconfigured point-to-point covers start as **timed** covers until the actuator reports a calibrated `Dimension = 10` position (`0..100`) on its own point-to-point `WHERE`, at which point they **automatically promote** to `native` (`advanced: true`) and remember that promotion across restarts. Uncalibrated actuators reporting `255`, scope `Dimension = 10` frames, and covers with explicit YAML kinematics or saved travel-time calibration remain `timed`. Every native point-to-point cover also exposes a **Minimum position step** (`1–5 %`) configuration number entity on its device page so `min_position_delta` can be adjusted in the UI without YAML. You can also set `advanced: true` (or its alias `advanced_shutter: true`) explicitly in `/config/myhome.yaml` — for example when pairing `native` with `slat_tilt: true` (see [Troubleshooting → Cover position is wrong](troubleshooting.md#cover-position-is-wrong)). Native / advanced covers refuse `myhome.calibrate_cover` and travel times in `myhome.set_cover_travel_time`, as they do not need a travel time (a Venetian blind with `slat_tilt` still accepts `slat_time` there).

### Choosing the cover model (`strategy`)

Each WHO=2 cover runs one of four position models. Without `strategy`, the flags (or calibrated `Dimension = 10` bus traffic on an unconfigured cover) pick it: `advanced: true` / `advanced_shutter: true` (or auto-detected calibrated `Dimension = 10`) gives `native`, `slat_tilt: true` gives `venetian`, `travel_time: 0` gives `simple`, and anything else gives `timed`. Set `strategy` in `/config/myhome.yaml` to name it explicitly:

| `strategy` | Position | Use for |
| :--- | :--- | :--- |
| `timed` | Estimated from the travel time | Standard relay actuators (the default). Needs a positive `travel_time`. |
| `venetian` | Estimated, plus slat tilt | Venetian blinds; implies `slat_tilt: true` and needs a positive `travel_time` (or `advanced_shutter`). |
| `native` | Reported by the actuator (`Dimension = 10`) | Advanced actuators; implies `advanced_shutter: true`. Can be combined with `slat_tilt`. |
| `simple` | None (open / close / stop only) | Relays with no measured travel time. The entity has no position slider and an assumed state: closed after a closing run, open after an opening one. |

An explicit `strategy` cannot be combined with a flag that selects another model (`advanced_shutter` with anything but `native`, `slat_tilt` with `timed` or `simple`, `travel_time: 0` with `timed`): the configuration is rejected. A cover set up with `travel_time: 0` becomes `timed` once it is calibrated (`myhome.calibrate_cover` or `myhome.set_cover_travel_time`), and `simple` again after `myhome.reset_cover_travel_time`. An explicit `strategy: simple` stays simple even when travel times are stored.

---

## 🏷️ Device class (`class`)

A WHO 2 cover defaults to `shutter` unless `/config/myhome.yaml` or Home Assistant's UI (**Show As**) says otherwise. `class:` (or `device_class:`) accepts all ten Home Assistant `CoverDeviceClass` values (`shutter`, `shade`, `blind`, `curtain`, `awning`, `door`, `garage`, `gate`, `damper`, and `window`), and determines the entity's icon, state wording, and directional control buttons in the Home Assistant UI. It applies to every WHO 2 cover, point-to-point as well as [general, area and group covers](#general-area-and-group-covers). A configured class also wins over the `blind` that `slat_tilt: true` (or `strategy: venetian`) would otherwise set.

### Device Class & Home Assistant Arrow Controls

Home Assistant renders different open/close control icons depending on the cover's `device_class`. Pick the class that matches the physical movement direction of your cover:

| `device_class` | Typical Physical Cover | Home Assistant Controls | Notes |
| :--- | :--- | :--- | :--- |
| `shutter` | Roller shutters, exterior rolling covers *(WHO 2 default)* | Vertical **↑ / ↓** arrows | Default for discovered WHO 2 covers. |
| `shade` | Roller shades, pleated shades, Roman shades (*vouwgordijnen* / *plisségordijnen*) | Vertical **↑ / ↓** arrows | **Recommended** for vertically opening fabric shades and Roman blinds so HA renders vertical ↑ / ↓ arrows. |
| `blind` | Venetian blinds, slatted blinds, external blinds (BSO/EVB) | Vertical **↑ / ↓** arrows *(+ slat tilt controls when `slat_tilt: true`)* | Automatically selected when `slat_tilt: true` or `strategy: venetian` is set; also suitable for vertically opening shades. |
| `curtain` | Side-drawing curtains / drapes | Horizontal **↔** arrows | Renders horizontal open/close arrows for side-drawing curtains; for vertically opening pleated or Roman shades, use `shade` or `blind` instead. |
| `awning` | Retractable patio or window awnings | Inverted vertical **↓ / ↑** (extend / retract) | Direction arrows match outward/downward awning extension. |
| `door` | Motorized doors | Door open / close icons | State wording reflects open/closed door semantics. |
| `garage` | Sectional, tilt, or roller garage doors | Vertical **↑ / ↓** arrows *(garage state icon)* | Typical for `WHO = 1` impulse garage relays (`device_class: garage`). |
| `gate` | Sliding or swing entrance gates | Gate open / close icons | Default for `WHO = 1` impulse covers (`device_class: gate`). |
| `damper` | HVAC air dampers / ventilation louvres | Vertical **↑ / ↓** arrows *(damper state icon)* | For motorized air-flow dampers. |
| `window` | Motorized windows, skylights, roof vents | Vertical **↑ / ↓** arrows *(window state icon)* | State icon and wording reflect open/closed window semantics. |

```yaml
# /config/myhome.yaml
cover:
  living_room_shade:
    where: '32'
    name: Living Room Shade
    class: shade
```

> [!NOTE]
> **Single-Gateway vs Multi-Gateway `myhome.yaml` Layout**:
> Flat top-level `cover:` blocks apply to single-gateway installations. On multi-gateway installations, nest `cover:` (and any other domain blocks) under each gateway's MAC address key (e.g. `"00:03:50:01:02:03":`).

Covers set up in the UI (without `myhome.yaml`) change their device class in Home Assistant with the **"Show As"** feature, like [switches](switches.md) and [binary sensors](binary-sensors.md): **Settings → Devices & Services → Entities**, select the cover entity, click the **Settings (gear)** icon and pick the class under **Show As** (for example, switching a vertically opening pleated/Roman shade from `shutter` to `shade` or `blind`, or a side-drawing curtain to `curtain`).

---

## ⏱️ Timed Cover Position Engine

For standard covers without hardware position feedback, the integration provides a software estimator enabling full `open_cover`, `close_cover`, `stop_cover`, and slider-based `set_cover_position` support:

* **Unknown Until the First Full Run**: A timed actuator cannot report where the curtain is (for an F411/2, the gateway answers `*#2*<WHERE>*10##` with no position frame), so a cover with no restored state starts as `unknown`, with no position, instead of an invented `open` at 50 %. The restored position from the last run of Home Assistant is kept. The position becomes known at an end stop. `open_cover`, `close_cover` and `set_cover_position: 0` / `100` run to the limit switch and settle the end position on the travel time plus 2 s, like the full runs of a known cover, whether the cover is commanded directly or as a member of an area / group / general cover whose one scope frame moves it: the actuator's own stop status may come earlier (an F411/2 whose timer is shorter than `travel_time`), later or not at all; a stop status before the travel time does not end that run, one at or after it anchors the end position at once. A stop sent from Home Assistant or a keypad stop relayed with a WHO 2 command translation (`*2*1000#0*<WHERE>##` / `*2*1000#10…*<WHERE>##`) during such a run leaves the position unknown; a plain `*2*0*<WHERE>##` keypad stop without command translation cannot be told apart from the actuator's own stop and settles at the end position too. A keypad run that is not stopped before the travel time settles on the travel time plus 2 s as well; a keypad stop before the travel time leaves it unknown. The state restored from the last run of Home Assistant is kept: a restored `closed` is 0 %, a restored `open` without a position stays open with an unknown position until an end stop. It is also kept when Home Assistant saved it while the gateway was unreachable (a restart or an entry reload during a network or gateway outage): Home Assistant stores an `unavailable` state without `current_position`, so the cover keeps its position, slat angle and `last_run_*` values in its own restore record. While it is unknown, `set_cover_position` with any other target is refused (*"… the position is unknown until the cover has run fully open or closed once"*) and sends nothing, because there is no position to time the run from.
* **Direction-Aware Travel Times**: Because gravity and motor friction cause shutters to fall faster than they rise, the v2 engine tracks separate `travel_time_down` and `travel_time_up` durations (default: `25.0s`).
* **Frame Anchor Timing**: The run timer starts when the direction frame is **written to the gateway**, not when Home Assistant queues it. While a timed run from a known position is in progress, Home Assistant republishes the interpolated `current_position` roughly once per second whenever the rounded percentage changes, so the UI slider animates smoothly instead of jumping only when the run settles or stops.
* **Echo Suppression & Terminal Stop Detection**: Gateways that synthesize an SCS pre-move stop relay a momentary stop status (~0.1 s) followed by translation and the actual motor start (~0.55 s). The v2 engine recognizes that sequence as a command echo and re-anchors the timer to the true motor start; however, if a plain `*2*0*<WHERE>##` arrives inside the 1.5 s echo window and **no** motor-start status (`*2*1*<WHERE>##` / `*2*2*<WHERE>##`) follows before the window expires, the stop was terminal (e.g. an interlocked relay stopping on an opposite command, or an immediate wall-keypad stop) and the cover reverts to stopped at its pre-command position ([#959](https://github.com/OpenWebNet-HA/MyHOME/issues/959)).
* **Pre-Move Stop on Single-Telegram Gateways (`stop_before_move`)**: Most Linux automation gateways (`bt_luci` on `F453`, `F453AV`, `H4684`, `MH200`, `MH200N` and `coso` `libplug_automation_tr.so` on `F454`, `F459`, `F460`, `F461`, `MyHomeServer1`, `3488`) inject an SCS `STOP` (`12 0A`) ahead of `UP` / `DOWN` (`12 08` / `12 09`), whereas **`MH201`** (verified live on firmware `3.3.6` and `3.6.44`) and **`MH202`** send only the direction telegram. On `MH201` and `MH202`, interlocked timed relay actuators (`L4671/2`, `H4671/2`, `F411/2`, `F411/4`, `F411U2`) treat an opposite command while energized — both mid-travel and while timing out their ~60 s factory `M=0` relay hold after the curtain reached its limit switch — as a stop rather than a reversal ([#959](https://github.com/OpenWebNet-HA/MyHOME/issues/959)); and on an Area, Group, or General broadcast without a preceding stop, the actuator emits no `*2*0*<WHERE>##` frame at all, so Home Assistant also settles an un-pre-stopped opposite-direction scope frame on `MH201` / `MH202` as an interlock stop rather than a reversal ([#977](https://github.com/OpenWebNet-HA/MyHOME/pull/977)). On `MH201` and `MH202` (and automatically on unmeasured gateways once an unfollowed echo-window stop is observed), timed point and local scope covers send `*2*0*<WHERE>##`, wait for socket delivery plus `0.6 s`, and then send the direction frame whenever the relay may still be energized (`65 s` window from motor start, cleared immediately as soon as the actuator's `*2*0*<WHERE>##` cutoff arrives, so idle covers incur zero extra bus traffic or latency). In `/config/myhome.yaml`, optional `stop_before_move: true` or `false` on a `WHO = 2` timed cover overrides this automatic policy (for example `stop_before_move: true` on actuators configured with `M=1..4` multi-minute or infinite run-time hold; rejected on `advanced` / `strategy: native` and `who: 1` impulse covers).
* **Motor Start-Up Floor (`min_motor_pulse`)**: A tubular motor needs a moment after the relay closes before the curtain moves. On an F454 with Somfy Ilmo 50 WT motors (20.4 s travel), 1 % steps (~0.2 s) clicked the relay without moving the curtain, while 2 % steps (~0.4 s) moved it ([#466](https://github.com/OpenWebNet-HA/MyHOME/issues/466)). A position change that would run shorter than the configured floor runs for `min_motor_pulse` seconds (default: `0.4 s`, configurable per cover via YAML `min_motor_pulse` or `myhome.set_cover_travel_time`; `0.0` disables), and the estimate books the movement of that longer run: on a 5 s cover with the default 0.4 s floor, 50 % → 51 % ends at 58 %. Other motors may need more or less.
* **Stop Status Re-Anchoring & Command-Translation Stops**: When a timed run is stopped, the estimate is provisionally frozen at the stop write (or on a WHO 2 stop command translation `*2*1000#0*<WHERE>##` / `*2*1000#10…*<WHERE>##`, so external stops still halt the run when a MyHomeServer1 omits the plain `*2*0*<WHERE>##` status under [#645](https://github.com/OpenWebNet-HA/MyHOME/issues/645)). If the gateway relays the plain stop status (`*2*0*<WHERE>##`) within the echo window (measured at 34–47 ms, median 42 ms on a MyHOMEServer1 and 64–120 ms on an F461), the final position and `last_run_seconds` are re-anchored to `stop status - start anchor` ([#705](https://github.com/OpenWebNet-HA/MyHOME/issues/705)). Gateways that do not relay the plain stop status keep the provisional freeze. By contrast, a bus start seen *only* as `*2*1000#1*` / `*2*1000#2*` without `*2*1*` / `*2*2*` does not start a run, because gateways emit `#1` / `#2` translations even when the target actuator is absent or locked via WHO 14.
* **Full Runs Settle on the Travel Timer**: `open_cover`, `close_cover`, `set_cover_position: 0` / `100`, scope full runs, and uninterrupted wall-switch runs all let the motor stop on its limit switch instead of sending a timed stop frame. Setting position to 0 % or 100 % also runs when the cover already reports 0 % or 100 %, so it puts a drifted estimate back in step, and a short move whose run would reach an end becomes a full run to that end too. Because an actuator's own stop status (`*2*0*<WHERE>##`) can arrive long after the curtain physically arrived — ~60 s at factory `M=0`, 120 s at `M=1` on `F411` actuators driving 60–86 s shutters ([discussion #770](https://github.com/OpenWebNet-HA/MyHOME/discussions/770#discussioncomment-19245565)), or never on `M=4` / gateways that do not relay it — the integration settles the entity to 0 % / 100 % (`closed` / `open`) once the settle timeout (`+ 2 s`) passes, for app and wall runs alike. When the run starts from an intermediate known position whose drift since the last confirmed end stop in this Home Assistant session is bounded, the settle timeout is bounded to `remaining_travel + drift_bound + 2 s` (accumulating `0.5 s + 15 %` of each unconfirmed partial or reversed segment up to `travel_time`), so mid-run reversals and partial moves back to an end stop clear `opening` / `closing` shortly after physical arrival ([#973](https://github.com/OpenWebNet-HA/MyHOME/issues/973)). Runs from `unknown`, restored states after a restart, post-disconnect runs, travel-time edits, saturated drift (`>= travel_time`), and endpoint re-homes (`open_cover` / `100 %` while already at `100 %`, `close_cover` / `0 %` while already at `0 %`) keep the full `travel_time + 2 s` settle window so drifted estimates continue to re-home safely. An actuator's own stop status that arrives up to 0.5 s *before* the stored travel time on such a run is the end stop as well and settles the end position at once (on a live F461 with an F411/2 calibrated at 5.82 s, the limit stop came 5.74–5.76 s after the motor start, which used to leave the estimate at `open 1 %` / `open 98 %`; a yaml `travel_time` is a whole second, so it can be up to 0.5 s off the real travel too). Stopping midway (from Home Assistant, or from the wall on a gateway that relays the WHO 2 command translation `*2*1000#0*<WHERE>##`), or a plain stop more than 0.5 s before the travel time, cancels the settle and freezes the interpolated position; once the timer has settled, any later hardware cutoff or stop frame leaves `0 %` / `100 %` and `last_run_seconds` untouched.
* **Resynchronization**: Running a cover to its full travel limit (fully open or fully closed) automatically resets any minor timing drift to 0% or 100%.

> [!NOTE]
> Small moves can still drift. The estimate assumes the curtain moves for the whole run. A motor's start-up time (measured only on the Somfy Ilmo 50 WT in [#466](https://github.com/OpenWebNet-HA/MyHOME/issues/466); another installation measured none on runs of about 0.76 s) is lost on every run, and the slat phase of a roller shutter with slats is booked as travel (one report: a 40 % request from fully closed lifted the curtain only about 18 % of its travel). A series of short moves can therefore book more travel than the curtain made. Send the cover to 0 % or 100 % now and then to re-sync it.
> For Venetian blinds and motorized louvres with tilting slats, enable `slat_tilt: true` (see [Venetian Blinds & Slat Tilt Positioning](#venetian-blinds-slat-tilt-positioning-timed-slat-inching)).

> [!WARNING]
> **Half the time is not half the height.** The estimate is linear: 50 % means the motor ran for half of the stored travel time. A roller shutter does not move at constant speed — coming down from the top the curtain runs fast on a full roll and is well past the middle at half time; going up from the bottom the first seconds go into gathering the slats and the curtain barely moves. Measured on a 107 cm shutter with exact travel times: `set_cover_position: 50` stopped at 27 cm from the sill coming down and at 37 cm going up, against a true midpoint of about 53 cm, while Home Assistant reported 50 % both times. The end positions (0 % / 100 %) are exact; intermediate positions are approximate and differ by direction. Treat the slider as "roughly there", not as a measurement — this applies to calibrated and manually set times alike.

**Advanced covers** track their own level (`Dimension = 10` at motor start and motor stop; no intermediate frames are emitted while the motor runs), and the actuator turns a `Dimension = 11` level write (`*#2*<WHERE>*#11#001*<LEVEL>##`) into a timed run proportional to its learned travel time. On a short-travel shutter (~20 s, ~0.2 s per percent) a 1 % change is too brief to move the curtain ([#466](https://github.com/OpenWebNet-HA/MyHOME/issues/466)), so by default the integration widens any change smaller than 2 % to 2 % (50 % → 51 % is sent as 52 %), except while the cover is moving (it then reports the level it started from, so the request goes out as asked). Target levels `0 %` and `100 %` — including a move near an end whose widened target clamps to `0 %` or `100 %` (e.g. `98 %` → `99 %` widened to `100 %` at `min_position_delta: 2`) — are sent as plain close / open full runs so the motor stops on its limit switch.

The smallest change is set per native cover directly in the Home Assistant UI via its **Minimum position step** (`number.<cover>_minimum_position_step`, **Configuration**, whole percent `1`–`5`, default `2`) — no `/config/myhome.yaml` required, and works on auto-detected Dimension 10 covers too. A value set in the UI persists in the gateway's config entry options and takes precedence over `min_position_delta` in `/config/myhome.yaml` (which remains supported as an optional fallback; a timed cover with `min_position_delta` in YAML is rejected, it uses `min_motor_pulse` instead). On a long-travel shutter each percent is a longer run: on a 3.75 m F401 shutter behind an F454 (`91.3 s` up / `87.8 s` down, ~0.9 s per percent; [discussion #770](https://github.com/orgs/OpenWebNet-HA/discussions/770#discussioncomment-19144038)), captured `*#2*21*#11#001*<LEVEL>##` 2 % steps ran 1.7–1.8 s each — already past the ~1.4 s mechanical slat pivot (`3` short `H4660M2` keypad pulses all reported `2 %`) — so `1 %` (~0.9 s per 1 % step) is needed to reach intermediate slat angles before the curtain lifts. `1` sends every change as asked; a very fast motor may need `3`–`5`. The active value is shown as the `min_position_delta` attribute.

```yaml
# Optional /config/myhome.yaml fallback (the UI Minimum position step entity takes precedence)
cover:
  living_room_cover:
    where: '31'
    name: Living Room Cover
    advanced_shutter: true
    min_position_delta: 1
```

### 🔒 Actuator Lock (`WHO = 14`) & Echo-Verified Motion

Every point-to-point cover device exposes **Lock** (`*14*0*<WHERE>##`) and **Unlock** (`*14*1*<WHERE>##`) configuration buttons and avoids phantom position drift when locked ([#976](https://github.com/OpenWebNet-HA/MyHOME/issues/976)):

* **Bidirectional `WHO = 14` & `Dimension 10` Lock Tracking**: Because `*#14*<WHERE>##` is NACKed on tested gateways and `MH201` (`3.3.6` / `3.6.44`), 1st-gen `L4671/2` shutters, and `F422` `#4#II` local-bus interfaces never echo `WHO = 14` on `*99*1##`, `actuator_locked` (`extra_state_attributes`, soft-restored across restarts including `unavailable` saves) tracks inbound non-translation `*14*0/1*...##` events, `*#*1##`-acknowledged outbound `*14*0/1*...##` commands (published `WHO_1.pdf` §4.2 point, Area `00` / `1`–`9` / `100`, and General `0` scopes — plus defensive resolution of configured `#G` groups — isolated per `#4#<Int>` local-bus interface per §2.3; `*#*0##` NACKs are ignored), and positioned `Object 218` `Dimension 10` `SHUTTER_INFO` reports (`12` / `13` → `actuator_locked: true`, `0` / `15` → `false`, `14` → command not executed).
* **Echo-Verified Point-to-Point Moves (`*#2*<WHERE>##` Check)**: On timed and `simple` point-to-point moves, the 1 Hz position ticker and `65 s` `M=0` relay-hold window wait for a real motor-start echo (`*2*1/2*<WHERE>##` or moving `Dimension 10` status `11` / `12`) rather than starting at socket write (and when `actuator_locked: true`, `opening` / `closing` and Venetian tilt pulses stay idle until confirmed, and `calibrate_cover` is refused). If `ECHO_WINDOW` (`1.5 s`) expires without motor-start confirmation (and `actuator_locked` is not already `true`), Home Assistant sends a single point-to-point `*#2*<WHERE>##` status request:
    * **Stationary `*2*0*<WHERE>##` reply** (locked 2nd-gen `F411/2`) or **NACK / empty reply** (transceiver-locked 1st-gen `L4671/2` on `MH201` / `F461` / `F454` / `MH202`, or unpowered/unpopulated address): aborts the unconfirmed move with **zero position drift**, zero `last_run_seconds` update, and zero phantom full-travel settle, and (for runs exceeding `ECHO_WINDOW`, where the motor could not already have reached its target) exposes `actuation_unconfirmed: true` in `extra_state_attributes` to exclude the cover from local scope follower timers until confirmed.
    * **Moving `*2*1/2*<WHERE>##` reply** (e.g. an `F455` dropped burst echo or `3578` `#9` ZigBee shutter): confirms the run anchored at `write_ts + 0.55 s` and completes travel normally.
* **Scope Exclusion (`#1` / Area / General) & `#4#II` Fan-Out Self-Heal**: Local scope covers still transmit their broadcast frame (`*2*1/2*#1##`, `*2*1/2*0##`, etc.), while excluding members with `actuator_locked: true` or `actuation_unconfirmed: true` from local follower animation, pre-move stops, and local scope tilt pulses (and ignoring relayed scope echoes on excluded members without mid-run broadcast status storms). Behind an `F422` interface (`#4#II`), paced point-to-point fan-outs still transmit each member's command so an actuator unlocked offline self-heals on its echo while still-locked members stay at `0 %` drift.
* **Mid-Run Lock Freeze & Power-Cycle Self-Healing**: Locking a moving cover immediately interrupts active calibration, freezes an active tilt pulse or timed run at its interpolated angle/position (aborting unstarted pulses and preserving native `Dimension 10` position), and clears the `65 s` relay-hold window because locking opens the physical relay. Outbound commands still transmit while `actuator_locked: true` so an actuator unlocked by a mains power cycle or un-relayed wall scenario responds; `actuator_locked` (and any unconfirmed-move flag) clears strictly on a point-to-point moving frame (`*2*1/2*<WHERE>##` or `Dimension 10` status `11` / `12`, plus `actuation_unconfirmed` clearing on a `WHO_2.pdf` §3.0.1 per-actuator `*2*0*<WHERE>##` end-stop following a scope run; `WHO = 1` impulse covers — with or without `state_sensor` — suppress optimistic motion while locked and clear on a point-to-point relay-ON event).

---

## 📐 Calibrating Travel Time (Zero YAML)

You never need to edit YAML files to calibrate travel times in v2.

> [!IMPORTANT]
> **Actuator Requirements for Automated Calibration**
> Automated on-bus calibration relies entirely on the actuator emitting a stop frame (`*2*0*<WHERE>##`) at the moment the cover physically reaches its end stop.
>
> * **Trimmed Actuators**: If the installer trimmed the actuator's mechanical or potentiometer run-time limit to match the physical cover, automated calibration will measure your cover travel times accurately down to tenths of a second.
> * **Configurator Run-Time Cutoff Actuators (60 s, 2 min, …)**: If the actuator run-time was left at the factory default cutoff (~60 s, `M=0`), the actuator will not stop when the cover reaches the bottom; it will continue running until the 60 s hardware timer expires. The integration detects this condition and **safely rejects the calibration** to prevent saving inaccurate 60 s run times. The same applies to the other whole-minute configurator steps: a run ending from 1 s before a limit to 5 s per minute after it — 59–65 s at 60 s, 119–130 s at 2 min (`M=1`, the setting a 60–86 s cover needs on an `F411`), and so on up to 10 min — is refused too. For these actuators, use the **guided Start → Stop Repair Flow** in **Settings → System → Repairs** (created automatically on cutoff) or [Method 3: Set Explicit Travel Time via Service Action](#method-3-set-explicit-travel-time-via-service-action).
>
> **How to spot your case**: press **Calibrate travel time** once and watch the first (opening) run. If the motor keeps running well past the top until its hardware timer expires (~60 s at factory `M=0`, ~2 min at `M=1`, …), your actuator is stopping on its run-time limit rather than the end stop and cannot be measured automatically over the bus. The button logs a clean `WARNING` (no error traceback), posts a **persistent notification** linking directly to **Settings → System → Repairs**, creates a fixable [`cover_calibration_cutoff`](../diagnostics/repair-issues.md#cover-actuator-run-time-cutoff) Repair Issue with an interactive **Guided Start → Stop** timing dialog, and fires the `myhome_cover_calibration` event (`phase: failed`, `actuator_limit` set to the limit in seconds). Nothing inaccurate is stored. Any pre-existing `~61 s` / whole-minute cutoff records saved before the cutoff guard are also detected on startup via [`cover_legacy_calibration_cutoff`](../diagnostics/repair-issues.md#legacy-cover-cutoff-travel-time-detected) using the same guided Repair Flow.
>
> **Which actuators** (from datasheets and user reports, awaiting full hardware traces under #466): the `F411/2`, `F411/4` and `F411U2` have a configurable timed stop — fixed steps on the physical *M* configurator (`5` / `10` / `15` / `20` / `30` s on `M=7/6/8/5/9`, `1` / `2` / `5` / `10` min on `M=0..3`, until motor limit stop on `M=4`), or `1–60` s and whole minutes `2–10` min in MyHOME_Suite (`MQ00275_e_EN.pdf` p. 3). Setting it a couple of seconds above the real travel in MyHOME_Suite allows covers under ~58 s to be measured; above 60 s only whole-minute steps exist, which calibration refuses, so covers between 60 s and 120 s must use `M=1` (120 s) with the guided Repair Flow or [Method 3](#method-3-set-explicit-travel-time-via-service-action) ([discussion #770](https://github.com/OpenWebNet-HA/MyHOME/discussions/770#discussioncomment-19245565)), and a run ending at ~60 s indicates the factory default `M=0`. The `F401` and the `H4661M2` / `LN4661M2` family learn the real travel themselves (*Push&Learn*), which is the case where the end stop on the bus is genuine.

### Method 1: Automated Calibration Button on Device Page
Every cover device in Home Assistant includes a dedicated configuration button entity (on a position-reporting cover a press is refused, as there is nothing to measure):

* **Entity**: `button.<name>_calibrate_travel_time`
* **Icon**: `mdi:ruler-square-compass`

When you click **Calibrate Travel Time**, the integration performs a fully automated 3-step calibration sequence on the bus:

1. **Full Open (Reference Sync)**: The cover is driven fully **UP** to reach the mechanical top limit, establishing a reliable reference position.
2. **Full Close (Timed)**: After a brief settling pause, the cover is driven fully **DOWN**. The integration monitors the OpenWebNet bus to precisely record `travel_time_down` (from motor start to actuator stop frame).
3. **Full Open (Timed)**: After another pause, the cover is driven fully **UP** back to the top limit, recording `travel_time_up`.
4. **Saved Automatically**: Both measured times are persisted into Home Assistant's config entry (`cover_travel_times`) and shown as the cover's `travel_time_down` / `travel_time_up` attributes, with `calibration_source: measured` and a `calibrated_at` timestamp. The cover ends in the 100% open position, and any prior cutoff Repair Issue or calibration notification for that cover is cleared. Progress is published on the event bus as `myhome_cover_calibration` (`phase`: `queued`, `start`, `run`, `done`, `failed`), so you can follow or automate on it.

> [!NOTE]
>
> * **Clean UI Feedback (Notifications & Guided Repair Flow)**: Calibration runs in a managed background task so the button responds immediately. Pre-flight checks refuse unavailable or unloaded covers, `WHO = 14`-locked actuators, active calibrations, native position-reporting covers, missing stop status after `180 s`, and actuator cutoffs cleanly at `WARNING` level (without raw tracebacks) and surface the outcome directly in the Home Assistant UI via a **persistent notification**. When an actuator hits its `60 s` / `120 s` run-time cutoff, the notification links directly to **Settings → System → Repairs**, where clicking **Fix** launches the interactive **Guided Start → Stop** manual timing dialog (which verifies the cover is available, unlocked, and not already calibrating, drives the cover without settling early under a `180 s` failsafe, stops immediately on **Submit** or if the dialog is closed mid-run, saves `calibration_source: manual`, and automatically clears both the Repair Issue and the cover's calibration notification).
> * **Do NOT stop the cover during automated calibration — neither from the cover entity nor from the wall**: While gateways that emit command-translation frames (`*2*1000#0*<WHERE>##`) recognise an external stop and abort calibration, gateways or controls that put only a plain stop frame (`*2*0*<WHERE>##`) on the bus emit the very same frame the actuator sends when the cover reaches its end stop. When only `*2*0*<WHERE>##` is seen, the integration cannot tell them apart and stores the partial elapsed time (e.g. 5–6 seconds) as the calibrated travel time.
> * **How to Abort Safely**: The one safe way out is the `myhome.stop_cover_calibration` action in **Developer Tools → Actions**: it stops the moving cover, aborts any remaining covers in a batch run (even if called during the one-second settle pause between runs), marks the run as interrupted, and stores nothing. (Driving the cover in the *opposite* direction from the wall is also recognised as an interruption, because it arrives as an explicit open/close command rather than a stop — but prefer the action.)

### Method 2: Calibrate All Covers Sequentially
On your gateway device page, click **Calibrate All Covers** (`button.<gateway name>_calibrate_all_covers`, e.g. `button.f454_gateway_calibrate_all_covers`). It runs a managed background batch targeting every enabled, non-exposed cover of that gateway **one at a time** (and refuses a duplicate press if a batch is already running), because two motors moving on one paced session would make the timings meaningless. Covers still waiting report `phase: queued`. Individual refusals (such as unavailable covers, position-reporting advanced covers, locked actuators, or actuators hitting a `60 s` / `120 s` cutoff) are logged at `WARNING` level and recorded without stopping the rest of the batch; every cutoff cover also gets a per-cover fixable Repair Issue. Calling `myhome.stop_cover_calibration` immediately stops the moving cover and aborts the remaining batch. When the batch finishes (or is stopped), MyHOME posts one summary **persistent notification** listing succeeded, stopped/skipped, and failed covers with reasons and a direct link to **Settings → System → Repairs** whenever any cover hit a cutoff.

### Method 3: Set Explicit Travel Time via Service Action
If your actuators enforce a configurator run-time cutoff (such as 60 s or 120 s) and you prefer not to use the guided Repair Flow, if your gateway is single-session (such as MH200 / MH200N), or if you already know the exact run duration (e.g. from a handheld stopwatch or technical datasheet), set it directly using the `myhome.set_cover_travel_time` action in **Developer Tools → Actions**.

You rarely need a handheld stopwatch: the integration times **every** run of a timed cover, calibration or not, and exposes the result as plain entity attributes — no card required.

1. If your cover takes longer than the `25 s` default (e.g. a `60–90 s` tall cover), first set a temporary larger `travel_time` (such as `180` seconds) via `myhome.set_cover_travel_time` so the cover does not settle to `0 %` / `100 %` before you press **Stop**.
2. Open the cover in Home Assistant (or use its wall switch) and press **Close**.
3. Press **Stop** (entity or wall switch) the moment the cover physically reaches the bottom end stop. *(Never let an `M=0` ~60 s or `M=1` ~120 s actuator run unattended to its hardware timer cutoff and save that number — `last_run_seconds` would record the relay cutoff rather than the cover's physical travel.)*
4. Go to **Developer Tools → States**, filter on the cover entity: `last_run_seconds` is the motor-start-to-stop time of the run you just made and `last_run_direction` says `close`. The same attributes are listed under **Attributes** in the cover's more-info dialog.
5. Repeat with **Open** for the up time, then pass both numbers to the action (or put the attributes and a **Save** button on your dashboard with [Lovelace Recipe 6](lovelace_recipes.md#recipe-6-cover-travel-time-workbench-stock-cards-only)):

```yaml
action: myhome.set_cover_travel_time
target:
  entity_id: cover.living_room_cover
data:
  travel_time_down: 22.5
  travel_time_up: 24.0
  min_motor_pulse: 0.4
```

When setting travel times, the result is stored like a measured calibration, with `calibration_source: manual`, and overwrites whatever a previous calibration attempt may have saved (also visible under `cover_travel_times` in the integration's diagnostics download). When only `min_motor_pulse` is set, existing travel times, source, and calibration timestamps remain untouched. When the numbers you pass were measured on an identical cover, add `copied_from: cover.<other_cover>` to record where they came from; `calibration_source` then reads `copied` and the entity is exposed as a `copied_from` attribute.

To forget the measured or manual times and return to the `travel_time` from `myhome.yaml` (or the 25 s default when none is configured):
```yaml
action: myhome.reset_cover_travel_time
target:
  entity_id: cover.living_room_cover
```

---

## 🪟 Venetian Blinds & Slat Tilt Positioning (Timed Slat Inching)

For Venetian blinds, motorized louvres, and external venetian blinds (EVBs/BSO) with adjustable slat tilt angles, the integration provides timed slat travel (inching):

* **Supported Features**: `SET_TILT_POSITION`, `OPEN_TILT` (100%), `CLOSE_TILT` (0%), and `STOP_TILT`.
* **Device Class**: Sets the entity device class to `blind` (`CoverDeviceClass.BLIND`) unless [`class`](#device-class-class) is set.
* **Attributes**: Exposes `current_cover_tilt_position` (0–100%), `slat_tilt: true`, and `slat_time: <seconds>`.

### Physical Architecture & Slat Travel Time

Physical BTicino / Legrand MyHOME Venetian blind actuators (such as the F411U2 configured in Venetian mode) control slat tilt angle mechanically through brief motor pulses: when the motor runs, the slats first rotate through their full angular range (typically 1.5–2.5 seconds) before linear curtain lifting or lowering commences.

The integration models this behavior with timed travel:

* When a tilt command is issued, the shutter motor runs in the required direction for a calculated duration (`duration = (|target_tilt - current_tilt| / 100) × slat_time`).
* The pulse is timed from the **motor start**, not from the frame write: from the direction status the gateway relays (`*2*1*<WHERE>##` / `*2*2*<WHERE>##`), or, when the gateway relays none, from the write plus the measured 0.55 s motor-start delay (see [Runtime behaviour → Timed covers](runtime_behaviour.md)). Timed from the write, a pulse shorter than about half a second would end before the motor started. Nothing is counted while the frame still waits in the send queue.
* When the target duration elapses, an automatic stop command (`*2*0*<WHERE>##`) halts the motor. The angle is settled when that stop is **written**, not when it was due: the send queue is first-in first-out, so behind other frames (several blinds tilted at once, a busy gateway) the stop leaves late and the slats turn on until then. The angle follows the real motor time at one full rotation per `slat_time`, also past the target, up to 0 % / 100 %. Motor time past a full rotation moves the curtain, and a timed cover's position estimate moves by that time over its curtain travel time (`travel_time - slat_time`; a position-reporting cover reports its own level).
* A new tilt target while the previous pulse's stop still waits in the queue waits for that stop to be written, so it starts from the real angle; the new direction frame could not leave before that stop anyway. Tilt requests run one at a time, and a newer target replaces one still waiting, so a dragged slider sends only where it ended.
* A new target in the **same direction** while the pulse still runs moves the pulse's stop and sends nothing; only a change of direction sends a new direction frame. The reversed pulse starts from the angle the first one reached, and the level the first one leaves (reported when the gateway stops the actuator ahead of the new direction) is still taken as that pulse's own.
* Like any command, a pulse opens an echo window: the stop status the gateway relays right after our direction frame is ignored, so it cannot cancel the pulse's stop and leave the motor running.
* Steps shorter than the cover's `min_motor_pulse` (default **0.4 s**, or 20 % at `slat_time: 2.0`) are skipped: the motor would barely answer while the angle was recorded as changed. On the 3.75 m F401 shutter with tilting slats ([discussion #770](https://github.com/orgs/OpenWebNet-HA/discussions/770#discussioncomment-19144521), `slat_time: 1.4`, `min_motor_pulse: 0.2`), pulses of 0.26–0.39 s (`15 %`–`25 %`) all produced the same visual slat opening while 0.74 s (`50 %`) reached horizontal and 1.44 s (`100 %`) fully opened the slats before the curtain started to lift — matching the default **0.4 s** floor (`Dimension 10` stayed at `0 %`–`1 %` throughout).
* **Unknown until the first full rotation**: a blind with no restored slat angle has none (`current_cover_tilt_position` is not set), as the actuator cannot report it. `set_cover_tilt_position: 0` / `100` (and `open_cover_tilt` / `close_cover_tilt`) run a full `slat_time` against the slat stop and anchor the angle there; any other target is refused until then (*"… the slat angle is unknown until the slats have been tilted fully open or closed once"*) and sends nothing, because there is no angle to time the pulse from. A pulse stopped before the stop leaves the angle unknown. An area / group / general blind takes its members' mean angle and is unknown while none of them knows theirs.
* A tilt command while the curtain itself is running is refused (*"… is moving; stop it before tilting the slats"*). The run turns the slats anyway, and a pulse would leave the curtain's travel estimate behind. Opening, closing or setting the position during a pulse ends the pulse at the angle it reached; the new command takes the motor over, and the pulse's own stop is dropped. Setting the position the blind is already at, while a pulse or a run is going, stops the motor there.
* During a curtain run the slats turn first at one full rotation per `slat_time` (towards 100 % when opening, towards 0 % when closing), and only the remaining motor time moves the curtain estimate over `travel_time - slat_time`. A stop status with no run behind it (such as the relay of a pulse's own stop) leaves the angle alone, so slats tilted open on a lowered blind stay open.
* On an advanced (`Dimension = 10`) actuator a slat pulse also moves the reported level, because the actuator counts the pulse as travel: an F401 behind an F454 reported its stopped level after each of ten pulse stops: 0 % → 1 % after four opening pulses and 1 % → 0 % after four closing ones, while after one opening and one closing pulse it stayed at 0 % ([discussion #770](https://github.com/orgs/OpenWebNet-HA/discussions/770)). A K4672M2S behind a MyHomeServer1 (firmware `2.87.13`) reported it 0.10–0.12 s after each of three pulse stops: 0 % → 1 %, 1 % → 2 % and 2 % → 1 % ([#991](https://github.com/OpenWebNet-HA/MyHOME/pull/991#issuecomment-7097236466)). An F401 behind the same MyHomeServer1, on the same blind model, reported it 0.07–0.12 s after each of three pulse stops: 0 % → 1 %, unchanged at 1 %, and 1 % → 0 % ([#991](https://github.com/OpenWebNet-HA/MyHOME/pull/991#issuecomment-7104500956)). A stopped level that changed the way the pulse turned, within 1.5 s of the pulse's stop, is taken as the pulse's own: it updates `current_position` and leaves the angle the pulse set. That holds for one stopped level: once it has come, or the level was reported unchanged, the next change is not the pulse's. A position or open / close command after the pulse ends the claim as well, whether Home Assistant sent it or another client did (`*#2*<WHERE>*#11#001#1*<LEVEL>##`). Not every pulse changes the reported level.
* A level change that is not a pulse's own is an **external move**, and how the slats follow it depends on what the actuator reported. With motion frames (a wall keypad: a moving level, then the stopped level) the slats turn in proportion to the motor time, at one rotation per `slat_time`: a 0.42 s keypad pulse at `slat_time: 0.8` gave 52 %. A level that jumps with **no** motion frame before it turns them all the way, to 100 % when the level went up and to 0 % when it went down.

### Configuration

Slat tilt and travel duration can be declared in `/config/myhome.yaml`:

```yaml
cover:
  living_room_venetian:
    where: '31'
    name: Living Room Venetian Blind
    slat_tilt: true
    slat_time: 2.0
```

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `slat_tilt` | boolean | `false` | Enables Venetian blind slat tilt controls and sets device class to `blind` unless `class` is set. |
| `slat_time` | float | `2.0` | Full slat rotation travel time in seconds (allowed range: `0.5`–`10.0` s). A value set with `myhome.set_cover_travel_time` is stored and wins over this one after a restart, until `myhome.reset_cover_travel_time`. |

Higher tilt is assumed to be *open* (an opening pulse raises the angle, verified on an F401 roller shutter with tilting slats in [discussion #770](https://github.com/orgs/OpenWebNet-HA/discussions/770#discussioncomment-19144521) with `slat_time: 1.4`). Dedicated Venetian actuators (such as the F411U2 in Venetian mode) still await a keypad / app capture (#492).

### Protocol Note: Dimension 11 is Shutter Level

In OpenWebNet WHO 2, **Dimension 11** (`*#2*<WHERE>*#11#PRIORITY*LEVEL##`) represents an absolute **Go to level / Shutter position** command, **not** slat tilt angle. Gateway firmwares (MH200N, MyHomeServer1) reject query frames (`*#2*<WHERE>*11##`) with a NACK because Dimension 11 is write-only.

When an actuator or external controller broadcasts a Dimension 11 event (such as `*#2*31*#11#001#1*40##` indicating 40% level), the integration does not take its level: the motor needs time to get there and a keypad can stop it midway, so the position only follows the actuator's own status reports (Dimension 10 / status frames). The one thing the frame does is end a slat pulse of that blind: the level command has the motor, so the pulse's stop is not sent into its run and the level that follows is not taken as the pulse's own.

### Travel Time Calibration & Slat Rotation

End-to-end travel time (`travel_time_down` / `travel_time_up`) includes one full slat rotation (`slat_time`) at the start of the run:

* On a slat blind, linear curtain travel is estimated over `travel_time_down - slat_time` and `travel_time_up - slat_time` after the slats finish rotating.
* Mid-tilt stops do not corrupt or reset linear curtain position estimation.
* Tilt commands are safely rejected if the cover is actively running automated travel-time calibration.

---

## 🏠 General, Area and Group Covers

A cover declared in `/config/myhome.yaml` on a **general** (`where: '0'`), **area** (`where: '1'`, `'00'`, `'100'`) or **group** (`where: '#3'`) address is one button for many covers: *open*, *close* and *stop* send a single frame to that address, like the general button on a keypad.

Its state comes from the covers it moves, not from the bus. At the end of a run every actuator reports its own stop (`*2*0*11##`, `*2*0*12##`, …) and none arrives for the general or area address (WHO_2 specification §3.0.1). So the cover shows *opening* / *closing* while any of its covers is moving, *closed* when all are closed, and the average of their positions:

| Address | Covers it follows |
| :--- | :--- |
| General `0` | every point-to-point cover of the gateway |
| Area `1`…`9`, `00`, `100` | the covers of that area on the same bus: area `1` is `11`…`19` and `0110`…`0115` |
| Group `#1`…`#255` | the covers listed under `members:`; group membership is programmed in the actuators and cannot be read from the bus |

Because standard timed actuators emit no per-member direction status when a `GEN`, `A` or `GR` broadcast starts (and actuators configured at `M=4` or behind single-frame gateways emit no per-member stop at the end either), a scope open or close settles every non-excluded matched timed member on that bus/interface on its own `travel_time + 2 s` timer. Members known to be locked (`actuator_locked: true`, including restored state or `Dimension 10` `SHUTTER_INFO` `12`/`13`) or flagged unconfirmed by a prior point-to-point `*#2*<WHERE>##` check are skipped automatically ([#976](https://github.com/OpenWebNet-HA/MyHOME/issues/976)); however, an actuator locked invisibly outside Home Assistant (e.g. an un-relayed wall scenario on `MH201` before any individual move) or a cover listed under `members:` in `myhome.yaml` that is not physically programmed into that group will still settle with the scope. Keep `members:` aligned with your physical actuator group configuration.

```yaml
cover:
  all_shutters:
    where: '1'          # area 1: follows covers 11-19 on its own
    name: All shutters
  bedrooms:
    where: '#3'
    name: Bedrooms
    members: ['11', '13']
```

**Behind an F422 interface** (`interface: '02'`, logical `#4#` addressing), physical actuators reside on an isolated secondary SCS bus segment (e.g. `11#4#02` … `22#4#02`).

### 🏛️ The BTicino Way & Real-World Plant Discovery

In large MyHOME installations, F422 interfaces physically segment the SCS bus (e.g., separating ground floor from upper floors or main house from outbuildings/curtain lines). How commands are routed across an F422 is fundamental to reliability:

#### 1. Why Naive Fan-Out Overwhelms F422 Hardware Buffers
In real-world plant operation with an MH200 gateway and F422 interface, an automation triggering 10 individual shutters at sunset (`action: cover.close_cover` targeting 10 individual entities) encountered reproducible dropped frames:

- Home Assistant dispatched 10 concurrent requests via `asyncio.gather()`.
- When fanning out 10 individual point-to-point OpenWebNet commands (`*2*2*14#4#02##`, `*2*2*19#4#02##`, `*2*2*18#4#02##`, …), even with the gateway's worker queue pacing (e.g., 0.15 s per frame), the burst overwhelmed the physical F422 interface's internal FIFO buffer and secondary SCS bus arbitration.
- By the 6th or 7th consecutive command, the F422 buffer overflowed. The gateway returned NACK (`*#*0##`), causing OWNd to cancel delivery and abort motion on the un-acknowledged curtain (e.g. `13#4#02`, "Oost 1"). The shutter remained stuck open while the rest closed.
- Compounding this, a common legacy automation pattern of placing a `delay: 45s` followed by an explicit `cover.stop_cover` sent `*2*0*13#4#02##` 45 seconds later when the bus was quiet again. The actuator acknowledged the stop frame, permanently cementing the stuck shutter in its open position and masking the failure.

#### 2. Native Broadcast: The BTicino Protocol Architecture
BTicino physical keypads and scenario units (such as `LN4660M2` or CEN/CEN+ controls programmed with `A=GEN` or Area `A=1..9`) never send individual point commands. They emit **native OpenWebNet broadcasts**:

- **Main Bus General**: `*2*WHAT*0##`
- **Secondary Bus General via F422**: `*2*WHAT*0#4#<interface>##` (e.g., `*2*2*0#4#02##` for General Down on interface 02, `*2*1*0#4#02##` for General Up, `*2*0*0#4#02##` for General Stop)
- **Secondary Bus Area via F422**: `*2*WHAT*<area>#4#<interface>##` (e.g., `*2*2*1#4#02##`)

The gateway routes the single broadcast frame through the F422 interface onto the secondary bus once. Every actuator on that bus reads the frame off the physical wire simultaneously. All relays energize in unison with **zero queue latency, zero interface congestion, and 100% mechanical synchronization**.

### ⚙️ How the MyHOME Integration Handles Scope Covers

The MyHOME integration implements a dual-layer architecture adhering to both Home Assistant standards and BTicino protocol engineering:

1. **Native Broadcast for General Scope Covers (`where: '0'`)**:
   When `open_cover`, `close_cover`, or `stop_cover` is called on a General cover behind an interface (`where: '0'`, `interface: '02'`), the integration sends the native OpenWebNet broadcast frame directly (`*2*WHAT*0#4#<bus>##`).

    - Actuators on the interface bus respond simultaneously without dropping frames.
    - When the gateway echoes `*2*WHAT*0#4#<bus>##`, the integration scopes the incoming frame strictly to entities on that interface (`interface == "02"`), preserving total bus isolation.
    - When individual actuators reach their end stops and report status frames (`*2*0*11#4#02##`, etc.), Home Assistant tracks each entity and aggregates the General cover's position automatically.

2. **Paced Member Fan-Out (`PACED_FANOUT_DELAY = 0.3s`) for Percentage Positioning Behind `interface` (`#4#II`)**:
   OpenWebNet has no broadcast protocol for percentage positioning (**Dimension 11** positioning `*#2*WHERE*#11#001*LEVEL##` is write-only and strictly point-to-point, while **Dimension 10** `*#2*WHERE*10*BS*LEVEL*...##` is read-only status feedback).
   When `set_cover_position` is called on an area, group, or general scope cover configured with `interface:` (`#4#II`), or when non-general scopes behind `interface:` execute `open_cover` / `close_cover` / `stop_cover`, `MyHOMEScopeCover` fans out individual point-to-point commands to its member covers staggered by **0.3 s (300 ms)** (`PACED_FANOUT_DELAY = 0.3`). *(On the main bus without `interface:`, `set_cover_position` on a scope cover does not fan out per-member Dimension 11 commands; instead it runs a timed scope run using the scope cover's `travel_time`.)*
   This gives the F422 hardware FIFO buffer adequate time to serialize frames onto the secondary bus, eliminating frame loss during multi-cover repositioning.

3. **Keypad Broadcast Reflection**:
   A general or area command from a physical wall switch or scenario unit also moves the individual covers in Home Assistant. A group or area cover whose shutters are not known yet ends its run after its own `travel_time`, so it never stays stuck on *opening* ([#433](https://github.com/OpenWebNet-HA/MyHOME/issues/433)).

### 📡 Bus Events for Scope Commands (`myhome_*_automation_event`)

A WHO 2 command heard on a **general**, **area** or **group** address — from a keypad, a scenario unit or Home Assistant itself — is mirrored on the Home Assistant event bus, so an automation can react to the physical button without owning a cover entity:

| Address | Event | Extra payload |
| :--- | :--- | :--- |
| General `0` (also `0#4#<interface>`) | `myhome_general_automation_event` | — |
| Area `1`…`9`, `00`, `100` | `myhome_area_automation_event` | `area` |
| Group `#1`…`#255` | `myhome_group_automation_event` | `group` |

Every payload carries `message` (the raw frame), `event` (`open`, `close` or `stop`), `where`, `gateway_mac` and `entry_id`. The general event also drives the gateway device triggers *Centralized cover open / close / stop*.

Only a **command frame that states a motion** fires an event: the standard `WHAT 0` / `1` / `2` (`*2*0*0##`, `*2*1*3##`, `*2*2*#5##`) and the advanced `WHAT 10` / `11` / `12` with their parameters (`*2*10#001#1*0##` stop, `*2*11#100#001#1*0##` up, `*2*12#100#001#1*0##` down, as captured from a MyHomeServer1 + LN4660M2 keypad). Nothing fires for:

* **any WHO 2 dimension frame**, as for the WHO 1 light events: a Dimension 11 level write and its event-session echo (`*#2*#5*#11#001*50##`, echoed by an F461 / MyHomeServer1 as `*#2*#5*#11#001#1*50##`; see [Protocol Note: Dimension 11 is Shutter Level](#protocol-note-dimension-11-is-shutter-level)) sets a level, it does not state a motion;
* a **`DIMENSION 10` status frame on a scope address** (`*#2*#5*10*10*50*001*0##`, `*#2*0*10*10*50*001*0##`), guarded defensively: a physical shutter reports its `DIMENSION 10` status on its own point-to-point address, never on a group, area or general one, so such a frame is parsed, never read as a stop, and never promotes member covers to `native`;
* a `WHAT` outside the WHO 2 table, which states no motion.

```yaml
alias: "Pause the media player on a general shutter stop"
trigger:
  - platform: event
    event_type: myhome_general_automation_event
    event_data:
      event: stop
action:
  - service: media_player.media_pause
    target:
      entity_id: media_player.living_room
```

The lighting counterparts (`myhome_general_light_event`, `myhome_area_light_event`, `myhome_group_light_event`) are described under [Synchronizing Physical Wall Switches](lights.md#synchronizing-physical-wall-switches-scs-group-buttons).

### 💡 Automation Best Practices: The Home Assistant & BTicino Way

To ensure maximum reliability and preserve bus health:

#### Recommended: Target the General Scope Cover
Declare a General scope cover in `/config/myhome.yaml` for each bus interface, and target that single entity in your automations:

```yaml
# /config/myhome.yaml
"00:03:50:01:02:03":
  cover:
    all_shutters_oost:
      where: "0"
      interface: "02"
      name: "All Shutters East"
```

In your automation:
```yaml
alias: Close Windows at Sunset
triggers:
  - trigger: sun
    event: sunset
    offset: "00:30:00"
actions:
  - action: cover.close_cover
    target:
      entity_id: cover.all_shutters_oost
```

#### Retire the Legacy `delay: 45s` + `stop_cover` Anti-Pattern
In legacy configurations, users often placed a `delay: 45s` followed by `cover.stop_cover` to ensure motors stopped. **This pattern is unnecessary and counterproductive**:

- BTicino actuators (F411, F401, LN4661M2) incorporate hardware limit switches and configurable travel timers (e.g. configurator `M` or MyHOME_Suite cutoff).
- The MyHOME integration automatically calculates direction-aware travel times (`travel_time_down` / `travel_time_up`) and handles stop detection.
- Sending a delayed `cover.stop_cover` floods the bus with redundant frames and can accidentally cement a stalled or out-of-sync shutter. Let the hardware and integration manage motion termination naturally.

### 🪟 Slat Tilt on Scope Covers

**Slat tilt on a scope cover** (with `slat_tilt: true`): on the main bus one scope pulse turns the slats of every member with `slat_tilt`. The members follow that pulse (its direction and stop frames turn their slats and do not count as a curtain run), and the scope cover times and settles it for all of them. A scope tilt is refused while one of its covers is running. Behind an F422 each blind gets its own pulse, **one after the other**: queued together, every stop would wait behind the other blinds' direction frames and the slats would turn on. The last blind therefore moves a few seconds after the first.

---

## 🚪 Impulse Covers: Motorized Gates & Garage Doors (`WHO = 1`)

In BTicino MyHOME installations, motorized garage doors and sliding/swing entrance gates are controlled via **monostable impulse relays** on `WHO = 1` (such as `F411/2` or `F411U2`).

Because monostable relays only send a momentary pulse (`open` / `stop` / `close` / `stop`) with no inherent directional knowledge on the bus, Home Assistant implements the specialized **`MyHOMEImpulseCover`** entity.

**By default the cover is a plain hardware interface.** `open_cover`, `close_cover` and `stop_cover` pulse the relay straight away, from a dashboard, a wall tablet, a button, a script or a voice assistant; nobody has to approve anything on a phone. The integration only does what belongs to the hardware:

- It sends the pulse and keeps a deadband (`min_cycle_time`) so a double tap cannot send two pulses. The pulse is hardware-timed (WHAT 18) when `pulse_duration` is `0.5` s; any other duration is ON followed by an OFF that Home Assistant sends `pulse_duration` after the ON was written to the gateway (not after it was queued, so an ON that waited for the command session is not followed at once by its OFF).
- An open or close that was not sent raises an error: inside the deadband, while another pulse of the same cover is being written, when the relay of the pulse before could not be released, when the send queue refused the frame, or when the command session could not connect (or the queued frame was withdrawn before any byte was written). A pulse the gateway did not confirm raises a different error: refused by the gateway (NACK) or not confirmed within 15 s after writing, the relay may or may not have been switched, so look at the door before you try again. A pulse still waiting in the send queue at that point is withdrawn, so the door never moves long after the request.
- `stop_cover` is not held back by the deadband: a stop of the door Home Assistant just started is not a double tap. It waits until the relay of the pulse before is released (`pulse_duration` + 0.2 s) and for a pulse that is still being written, then pulses. A stop within `min_cycle_time` of a confirmed stop, or while a stop is being written, is that same stop and sends nothing: a second pulse would start the door again.
- A stop is withheld only when the contact reports the door closed and nothing says it may be starting: Home Assistant has no pulse on its way, and within `travel_time` no relay actuation was seen (wall button, radio remote) and no pulse of Home Assistant was left unconfirmed. After an unconfirmed pulse a stop is sent because the door may be moving; if that pulse never reached the relay, the stop pulse is the first one the gate controller sees and starts the door.
- With a `state_sensor` it knows the real position: `open_cover` on an open or opening door and `close_cover` on a closed or closing door send nothing, because a pulse would only undo the request. The sensor only tells closed from not closed, so a door stopped part-way counts as open: `open_cover` sends nothing there (the pulse could close it), `close_cover` sends the pulse. Without a sensor the position is assumed and every pulse goes out as asked.

> [!WARNING]
> **`stop_cover` on a door at rest starts it.** A step-by-step motor controller has no stop input on this relay: the pulse that stops a moving door is the same pulse that starts a resting one. Home Assistant only knows that a door is at rest when a `state_sensor` reports it closed. Without a sensor, or with the door open or part-way, `stop_cover` sends the pulse, because the door may be moving after a wall button or a radio remote. Keep impulse covers out of "stop all covers" scripts, groups and voice routines.
- If the OFF that ends a software pulse cannot be delivered it is retried, and the relay is released before the next pulse. The OFF is sent even when the ON was not confirmed: a frame whose acknowledgement was lost may still have closed the relay.

Who may operate the door, from where and with which confirmation is a policy of your installation. Build it with automations or blueprints, or switch on the optional supervised workflow below with an `access:` block.

### 🛡️ Safety Architecture & Threat Model

> [!CAUTION]
> **HOME ASSISTANT IS A SUPERVISORY AUTOMATION SYSTEM, NOT A CERTIFIED SAFETY SYSTEM**
> Primary entrapment and crush protection (e.g. European Standard **EN 12453 / EN 12445** or North American **UL 325**) **must be provided by the motorized gate or garage door hardware itself**: certified infrared photocells, safety contact edges, and mechanical force limiters built into the motor control board.
> The optional `AccessController` is designed exclusively to prevent Home Assistant from *causing* an unintended, accidental, or unattended movement.

#### 📊 Optional Human-in-the-Loop Access Policy (`access:`)

Everything in this part applies **only to a cover that has an `access:` block**. Without it the cover pulses directly, as described above.

```text
  [ User / Dashboard / Widget / Siri / Alexa ]
                       │
                       │ 1. cover.open_cover / close_cover
                       ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                    MyHOME AccessController                     │
  │                                                                 │
  │  [ Check User & Lockout ] ──( unauthorized )──> ❌ AccessDenied │
  │             │ (authorized)                                      │
  │             ▼                                                   │
  │  [ Classify State Sensor ]                                      │
  │       ├─ Sensor = Closed ─────────> Effect: OPEN                │
  │       └─ Sensor = Open / Unknown ──> Effect: MAY_CLOSE          │
  │                                           │                     │
  │                                  [ Safety Verification ]        │
  │                                  - Photocells valid (<31d)?     │
  │                                  - User at home / Camera ok?    │
  │                                  - Close-block switch off?      │
  │                                           │ (all pass)          │
  │             ┌─────────────────────────────┘                     │
  │             ▼                                                   │
  │  [ Generate 128-bit CSPRNG Nonce ]                              │
  │  - Single-use token: secrets.token_urlsafe(16)                  │
  │  - Bound to: user_id + entity_id + effect + 60s timeout         │
  └─────────────────────────────┬───────────────────────────────────┘
                                │ 2. Push Notification
                                ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                 Companion App (iOS / Android)                   │
  │                                                                 │
  │   🔔 "Security Request: Approve garage_door_1 close?"           │
  │   [ ✅ Area Clear - Close ] (authenticationRequired: true)       │
  │                                                                 │
  │   👉 User unlocks phone via FaceID / Fingerprint / Biometrics   │
  └─────────────────────────────┬───────────────────────────────────┘
                                │ 3. mobile_app_notification_action
                                ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                    MyHOME AccessController                     │
  │                                                                 │
  │  [ Validate Nonce & User Context (constant-time HMAC) ]         │
  │             │ (valid)                                           │
  │             ▼                                                   │
  │  [ Pre-Warning Phase (optional 5s flasher) ]                    │
  │       └─( wall switch pressed? )──> ❌ Cancel movement          │
  │             │ (clear)                                           │
  │             ▼                                                   │
  │  [ Bus Impulse ] ──> *1*1*WHERE## (WHO 1 Relay Monostable Pulse)│
  │             │                                                   │
  │             ▼                                                   │
  │  [ Watchdog Timer (travel_time + margin) ]                      │
  │       ├─ Sensor confirms target state ──> ✅ IDLE (Success)     │
  │       └─ Sensor fails to reach state  ──> ⚠️ FAULT (Latched)    │
  │                                           (Zero auto-retry)     │
  └─────────────────────────────────────────────────────────────────┘
```

#### 📊 Authentic BTicino Hardware Sequence (MH200 Trace Capture)

```text
  Entrance Panel (PE1)          MH200 Gateway            Handset (74)         Gate/Garage Relay (71/72)
          │                           │                       │                           │
          │ 1. Bell Pressed           │                       │                           │
          │──────────────────────────>│                       │                           │
          │   *#16*81*1*1##..88##     │                       │                           │
          │   (Audio matrix routing)  │                       │                           │
          │                           │                       │                           │
          │   *8*1#1#4*74##           │                       │                           │
          │   (WHO 8 Video Call)      │                       │                           │
          │                           │─────── Ring ─────────>│                           │
          │                           │                       │                           │
          │                           │<─── Pick Up / Talk ───│                           │
          │                           │   *8*9#1#4*73##       │                           │
          │                           │   (Caller address)    │                           │
          │                           │                       │                           │
          │                           │<── Hang Up / End ─────│                           │
          │                           │   *6*9##              │                           │
          │                           │   (Camera OFF)        │                           │
          │                           │                       │                           │
          │                           │                       │ 2. Gate Button Pressed    │
          │                           │<──────────────────────│                           │
          │                           │   *1*1*72##           │                           │
          │                           │   (WHO 1 Pulse)       │                           │
          │                           │──────────────────────────────────────────────────>│
          │                           │                       │                   [ Gate Moves ]
```

#### Key Principles of the Optional Human-in-the-Loop Policy (with `access:`):

1. **Zero Direct Movement on Open and Close Calls**:
   Calls to `cover.open_cover` or `close_cover` (whether from dashboards, mobile widgets, voice assistants, or automations) **never pulse the relay directly**. Instead, they create an authenticated approval request for an authorized person. Automations and scripts are refused because they have no person behind them. **`stop_cover` is the exception**: while a request is pending, the pre-warning runs or the door moves under supervision, it cancels the request or pulses the relay to stop the door at once, for anyone, without approval. A stop that arrives in the moment the approved pulse is on its way to the gateway waits for that write (at most 15 s). If the door did start, or may have (the gateway did not confirm the pulse), and is supervised (a `state_sensor` is set), the stop then pulses the relay once it is released (`pulse_duration` + 0.2 s after the pulse; `min_cycle_time` does not hold a stop back); a stop that waited for an approved stop shares that stop's fate (done when the gateway confirmed it, an error when it did not) and sends no second pulse. Without a `state_sensor` the pulse is not supervised, and the stop becomes an ordinary request: refused for an automation, a new approval request for an allowed person. Only a pulse that never left Home Assistant (deadband, relay of the pulse before not released, send queue refused, or command session unavailable / frame withdrawn before writing) is reported as "Not executed"; a pulse the gateway did not confirm is supervised like a confirmed one, the phone is told that it was not confirmed, and a door that then does not close is reported without latching a fault.
2. **Cryptographic Single-Use Nonce & User Binding**:
   Each approval request generates a 128-bit CSPRNG token (`secrets.token_urlsafe(16)`), tightly bound to the requesting `user_id`, target entity, and physical direction. Replayed, stale, or forged approval actions are discarded.
3. **Biometric Phone Unlock Requirement**:
   Actionable push notifications to the Companion App enforce `authenticationRequired: true` (iOS FaceID/TouchID, Android biometrics/screen lock). The phone must be actively unlocked by the authorized approver.
4. **Fail-Closed Sensor Direction Checks**:
   A pulse is classified as a pure **OPEN** *only* when the ground-truth state sensor (`state_sensor`, a binary sensor that is `off` when the door is closed and `on` otherwise) proves the door is closed. In all other states, the pulse is classified as **MAY_CLOSE**, requiring:

    - Verified active safety devices within `safety_check_days`.
    - The user to be on site (`person` entity reports `home`) or visual confirmation via a camera snapshot.
    - Any external close-block switch to report `off`.
    - Any missing, unavailable, or non-binary sensor state immediately fails closed.
5. **Audible / Visual Pre-Warning & Watchdog**:
   Before a close pulse, an optional pre-warning flasher (`prewarn_light`) triggers for `prewarn_seconds` and Home Assistant fires a `myhome_access_prewarning` bus event (`entity_id`, `name`, `seconds`) so speakers, sirens, or dashboard alerts can warn bystanders before motion starts. After the pulse, an anti-stuck watchdog monitors motion: if the expected state is not reached within `travel_time + watchdog_margin`, a latching fault is asserted with **zero automatic retries**. A latched fault survives a restart or reload of Home Assistant (including when saved while the gateway is unreachable) and can only be cleared on site by an authorized user using the `myhome.acknowledge_cover_fault` action; an in-flight request or pre-warning can be cancelled at any time using `myhome.cancel_cover_request` (see [Services](services.md#14-myhomecancel_cover_request)). `approval_timeout` is the time the approver has to answer; the pre-warning starts after the approval and is not counted against it. A cancellation that Home Assistant accepts during the pre-warning, up to the moment the warning light is back in its original state, is never followed by the pulse.
6. **A Wall Button Starts the Gate, It Does Not Stop It**:
   A press of the wall button (or any other keypad on the bus) closes the relay and energizes the motor by itself; Home Assistant only sees the resulting `*1*1*WHERE##` frame afterwards. When such a frame arrives during an approval request or the pre-warning countdown, Home Assistant therefore *drops its own pending request* so that no second pulse follows and stops or reverses the gate. The movement the button started is not aborted. Use the hardware's own stop input or the remote control for an emergency stop.

### ⚙️ Configuring an Impulse Cover

An impulse cover needs `who: "1"` (unquoted integer `who: 1` is also coerced to `"1"`) and a single point-to-point `where`. The minimal configuration is a hardware interface with no approval step:

```yaml
# /config/myhome.yaml
"00:03:50:01:02:03":
  cover:
    garage_door:
      who: "1"
      where: "71"
      name: Garage door
      device_class: garage
      state_sensor: binary_sensor.garage_door_closed   # optional: off = closed, on = not closed
```

Add an `access:` block to opt in to the supervised workflow (phone approval, PIN, presence, camera, pre-warning, safety audit). **With `access:` an empty `allowed_users` refuses every open and close request** with `AccessDenied`: that policy is fail-closed. The full example:

```yaml
# /config/myhome.yaml
"00:03:50:01:02:03":
  cover:
    garage_door:
      who: "1"
      type: impulse_relay            # optional; only valid together with who: "1"
      where: "71"                    # one relay, never a general, area or group address
      name: Garage door
      device_class: garage           # garage or gate (default gate)
      state_sensor: binary_sensor.garage_door_closed   # off = closed, on = not closed
      travel_time: 25                # seconds (not 0), for the dashboard estimate, the watchdog and the stop rule
      pulse_duration: 0.5            # seconds the relay is closed (0.2 - 2.0)
      min_cycle_time: 5              # seconds between two pulses (1 - 120)
      pin_code: "1234"               # optional second factor, asked on the phone; needs access:
      access:
        allowed_users:               # Home Assistant user ids
          - 1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6
        approvers:                   # one notify service per allowed user
          1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6: notify.mobile_app_phone
        remote_close: at_home        # never (default), at_home or with_camera
        camera: camera.driveway      # needed for remote_close: with_camera
        safety_devices_verified: 2026-09-01   # date you last tested photocells and force limiter
        safety_check_days: 31
        prewarn_light: light.driveway_flasher
        prewarn_seconds: 5
        close_block_entity: input_boolean.gate_close_block
        watchdog_margin: 10          # seconds added to travel_time before a fault is latched
        approval_timeout: 60
```

| Key | Meaning |
| :--- | :--- |
| `state_sensor` | Binary sensor of the ground-truth position. Without it the position is assumed and, with `access:`, every pulse is treated as one that can close the door. |
| `pulse_duration` | Relay pulse in seconds. `0.5` uses the timed WHAT 18 command (`*1*18*WHERE##`, specified in `WHO_1.pdf` for 0.5 s timed ON, verified on physical `F461` + `BT-F411/4` hardware in `tests/fixtures/traces/issue_667/myhome_trace_F461_impulse_intercom_2026-10-19.json`, where the actuator emits `*1*1*WHERE##` and autonomously turns off with `*1*0*WHERE##` after ~0.53 s, and confirmed in the QEMU plant-mode firmware oracle `plant-f461-bench.tsv` on `F461` `020011` and `MyHomeServer1` `028206` where `*1*18*06##` receives ACK and emits `*1*1*06##` then `*1*0*06##`); any other value sends ON (`*1*1*WHERE##`) and, `pulse_duration` after that frame was written, OFF (`*1*0*WHERE##`). |
| `min_cycle_time` | Deadband for `open_cover` / `close_cover` after a pulse that reached the bus (must be strictly greater than `pulse_duration`); `stop_cover` only waits for the relay to be released. A failed send does not start it. A send the gateway did not confirm is reported as failed although it may have reached the relay: look at the door before you repeat it: the relay may already have been switched, and what another pulse does then depends on the gate controller and on where the door is. |
| `pin_code` | Digit-only PIN asked in the approval notification; only valid together with `access:`. A reply that carries no text is refused without counting as a wrong attempt. |
| `access.*` | Optional. Turns on the supervised workflow, see the flow above: `allowed_users`, `approvers`, `remote_close`, `camera`, `safety_devices_verified`, `safety_check_days`, `prewarn_light`, `prewarn_seconds`, `close_block_entity`, `watchdog_margin`, `approval_timeout`. |

> [!NOTE]
> `type: impulse_relay` together with `who: "2"` (and `type: shutter` or `standard` together with `who: "1"`) is rejected when the configuration is validated; it used to fall back to an ordinary shutter silently.

### 🔒 Configuring a Door Strike Lock

```yaml
# /config/myhome.yaml
"00:03:50:01:02:03":
  lock:
    front_door:
      who: "6"                       # the only value (unquoted who: 6 is also coerced); WHO 1 relays are covers
      where: "4001"                  # 1-99 or 4000-4099 endpoints; append #2 on a riser installation: "4001#2"
      name: Front door
      code: "1234"                   # optional digit-only PIN; asked on every unlock
      pulse_duration: 5.0            # seconds until the lock shows locked again (default 5.0 s)
```

Locks exist only when they are configured here (and are restored from the registry on restart with their YAML configuration intact). Neither a lock release seen on the bus nor a stale registry entry removed from `myhome.yaml` creates a lock entity, because such an entity would carry no code.

### ⚠️ Strict Distinction: Covers vs. Locks

```text
  ┌───────────────────────────────────────┐   ┌───────────────────────────────────────┐
  │    HEAVY MACHINERY (KINETIC RISK)     │   │      PEDESTRIAN ACCESS (LOW MASS)     │
  │                                       │   │                                       │
  │   - Motorized Sliding / Swing Gates   │   │   - Front Door Latch / Strike         │
  │   - Sectional / Roller Garage Doors   │   │   - Pedestrian Wicket Gate Buzzer     │
  │                                       │   │   - Elettroserratura (12V AC/DC)      │
  │                   │                   │   │                   │                   │
  │                   ▼                   │   │                   ▼                   │
  │       PLATFORM: cover (impulse)       │   │            PLATFORM: lock             │
  │                   │                   │   │                   │                   │
  │  - Optional AccessController          │   │  - Direct momentary pulse (1-3s)      │
  │  - Dual binary sensor feedback        │   │  - Optional PIN authentication        │
  │  - Biometric challenge-response nonce │   │  - Automatic re-lock state machine    │
  │  - 5s pre-warning flasher             │   │  - Doorbell one-tap unlock safe       │
  │  - Anti-stuck watchdog (latched fault)│   │                                       │
  │  - Certified EN 12453 safety check    │   │                                       │
  └───────────────────────────────────────┘   └───────────────────────────────────────┘
```

| Physical Device | Required Entity Platform | Why |
| :--- | :--- | :--- |
| **Motorized Gates & Garage Doors** | `cover` (`type: impulse_relay`) | Heavy kinetic machinery with kinetic entrapment/crush risk. Use a `state_sensor`, and the optional `access:` workflow where you want supervision. **Never configure a motorized gate or garage as a `lock`**: its door lock-style unlock would carry no sensor feedback. |
| **Pedestrian Door Strikes** | `lock:` block, `who: 6` only | Low-mass momentary electric door buzzers (elettroserrature) on a WHO 6 entrance panel that release a pedestrian latch. A `lock` cannot be a WHO 1 relay: a motorized gate needs the cover's sensor feedback and stop handling, so `who: 1` is rejected there. |

---

## 🔄 Legacy YAML Note

> [!NOTE]
> If you are upgrading from legacy v0.9 installations and still have manual `cover:` blocks in `/config/myhome.yaml`, please refer to the [v0.9.4 Legacy Cover Documentation](https://openwebnet-ha.github.io/MyHOME/0.9.4/configuration/covers/) or the [Legacy YAML Migration Guide](../migration/legacy-yaml.md). In v2, all covers are managed dynamically via Home Assistant's native registry.

