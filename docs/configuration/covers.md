# Covers & Shutters (WHO = 2)

The **MyHOME** integration provides full control for motorized shutters, blinds, venetian blinds, and curtains operating on OpenWebNet **WHO = 2**.

In v2, setup and management are **100% UI-first**: entities are automatically discovered from the SCS bus, and position calibration is handled natively through Home Assistant buttons and services without requiring manual YAML configuration files.

---

## 🚀 Auto-Discovery

When your gateway connects to Home Assistant:

1. **Dynamic Bus Discovery**: The integration listens to OpenWebNet `WHO = 2` frames and scans the bus.
2. **Device Creation**: Each physical shutter actuator (`WHERE = 1..99` or area/point addresses) is registered as a Home Assistant `cover` device linked to your MyHOME Gateway.
3. **UI Customization**: You can rename the entity, assign it to an Area (e.g. *Living Room*, *Master Bedroom*), or change its icon directly in the Home Assistant UI (**Settings → Devices & Services → Entities**).

---

## 🎛️ Cover Types: Standard vs. Advanced

The integration distinguishes between two types of MyHOME covers:

| Cover Type | Supported Hardware | Position Control (`set_cover_position`) | How Position is Handled |
| :--- | :--- | :---: | :--- |
| **Advanced Covers** | Legrand Céliane `67557`, Axolute `H4661M2`, Livinglight `LN4661M2`, BTicino `F401` (advanced mode) | ✅ Native | Actuator hardware reports exact physical position back to the bus (`Dimension = 10`). |
| **Standard (Timed) Covers** | Standard relay actuators (`F411/2`, `F411U2`, standard `F401`, older flush-mount units) | ✅ Estimated | Actuator has no position feedback. The v2 runtime estimates position from the elapsed share of the **travel time** (`travel_time_down` and `travel_time_up`). |

> [!NOTE]
> Every discovered cover starts as a **timed** cover. Position-reporting hardware is not detected automatically: set `advanced_shutter: true` for that cover in `/config/myhome.yaml` (see [Troubleshooting → Cover position is wrong](troubleshooting.md#cover-position-is-wrong)). Advanced covers refuse `myhome.calibrate_cover` and `myhome.set_cover_travel_time`, as they do not need a travel time.

---

## ⏱️ Timed Cover Position Engine

For standard covers without hardware position feedback, the integration provides a software estimator enabling full `open_cover`, `close_cover`, `stop_cover`, and slider-based `set_cover_position` support:

* **Direction-Aware Travel Times**: Because gravity and motor friction cause shutters to fall faster than they rise, the v2 engine tracks separate `travel_time_down` and `travel_time_up` durations (default: `25.0s`).
* **Frame Anchor Timing**: The run timer starts when the direction frame is **written to the gateway**, not when Home Assistant queues it.
* **Echo Suppression**: The gateway relays a momentary stop status (~0.1 s) followed by translation and the actual motor start (~0.55 s). The v2 engine recognizes these as command echoes, re-anchoring the timer to the true motor start rather than falsely treating them as manual stop commands.
* **Resynchronization**: Running a cover to its full travel limit (fully open or fully closed) automatically resets any minor timing drift to 0% or 100%.

> [!WARNING]
> **Half the time is not half the height.** The estimate is linear: 50 % means the motor ran for half of the stored travel time. A roller shutter does not move at constant speed — coming down from the top the curtain runs fast on a full roll and is well past the middle at half time; going up from the bottom the first seconds go into gathering the slats and the curtain barely moves. Measured on a 107 cm shutter with exact travel times: `set_cover_position: 50` stopped at 27 cm from the sill coming down and at 37 cm going up, against a true midpoint of about 53 cm, while Home Assistant reported 50 % both times. The end positions (0 % / 100 %) are exact; intermediate positions are approximate and differ by direction. Treat the slider as "roughly there", not as a measurement — this applies to calibrated and manually set times alike.

---

## 📐 Calibrating Travel Time (Zero YAML)

You never need to edit YAML files to calibrate travel times in v2.

> [!IMPORTANT]
> **Actuator Requirements for Automated Calibration**
> Automated on-bus calibration relies entirely on the actuator emitting a stop frame (`*2*0*<WHERE>##`) at the moment the shutter physically reaches its end stop.
>
> * **Trimmed Actuators**: If the installer trimmed the actuator's mechanical or potentiometer run-time limit to match the physical shutter, automated calibration will measure your shutter travel times accurately down to tenths of a second.
> * **Factory 60 s Cutoff Actuators**: If the actuator run-time was left at the factory default cutoff (~60 s), the actuator will not stop when the shutter reaches the bottom; it will continue running until the 60 s hardware timer expires. The integration detects this condition and **safely rejects the calibration** to prevent saving inaccurate 60 s run times. For these actuators, use [Method 3: Set Explicit Travel Time via Service Action](#method-3-set-explicit-travel-time-via-service-action) instead.
>
> **How to spot your case**: press the button once and watch the first (opening) run. If the motor keeps running for about a minute after the shutter is fully up, your actuator is at its factory limit and cannot be measured over the bus — the button will refuse every time. You will not see the refusal in the UI (the button does not wait for the result): it is logged, and the `myhome_cover_calibration` event reports `phase: failed`. Nothing is stored.
>
> **Which actuators** (from datasheets and user reports, awaiting full hardware traces under #466): the `F411/2`, `F411/4` and `F411U2` have a configurable timed stop — the physical *M* configurator, or 1–60 s in MyHOME_Suite. Setting it a couple of seconds above the real travel allows these to be measured; a run that ends at ~60 s indicates it was left at its factory default `M=0`. The `F401` and the `H4661M2` / `LN4661M2` family learn the real travel themselves (*Push&Learn*), which is the case where the end stop on the bus is genuine.

### Method 1: Automated Calibration Button on Device Page
Every cover device in Home Assistant includes a dedicated configuration button entity (on a position-reporting cover a press is refused, as there is nothing to measure):

* **Entity**: `button.<name>_calibrate_travel_time`
* **Icon**: `mdi:ruler-square-compass`

When you click **Calibrate Travel Time**, the integration performs a fully automated 3-step calibration sequence on the bus:

1. **Full Open (Reference Sync)**: The shutter is driven fully **UP** to reach the mechanical top limit, establishing a reliable reference position.
2. **Full Close (Timed)**: After a brief settling pause, the shutter is driven fully **DOWN**. The integration monitors the OpenWebNet bus to precisely record `travel_time_down` (from motor start to actuator stop frame).
3. **Full Open (Timed)**: After another pause, the shutter is driven fully **UP** back to the top limit, recording `travel_time_up`.
4. **Saved Automatically**: Both measured times are persisted into Home Assistant's config entry (`cover_travel_times`) and shown as the cover's `travel_time_down` / `travel_time_up` attributes, with `calibration_source: measured` and a `calibrated_at` timestamp. The shutter ends in the 100% open position. Progress is published on the event bus as `myhome_cover_calibration` (`phase`: `queued`, `start`, `run`, `done`, `failed`), so you can follow or automate on it.

> [!NOTE]
>
> * **Do not press the button a second time**: The sequence is fully automated. A second press while the run is active is refused (*"… is already being calibrated"*); the button fires the action without waiting for it, so the refusal only shows up in the Home Assistant log, not in the UI. Each run also gives up with *no stop status from the actuator* if no stop frame arrives within 180 s.
> * **Do NOT stop the shutter during calibration — neither from the cover entity nor from the wall**: A stop press from either place puts a plain stop frame (`*2*0*<WHERE>##`) on the bus, which is the very same frame the actuator emits when the shutter reaches its end stop. The integration cannot tell them apart, so it treats the stop as the end of the run and stores the partial elapsed time (e.g. 5–6 seconds) as the calibrated travel time.
> * **How to Abort Safely**: The one safe way out is the `myhome.stop_cover_calibration` action in **Developer Tools → Actions**: it marks the run as interrupted and stores nothing. Call it while the shutter is moving — a call that lands in the one-second pause between two runs is not seen and the next run starts. (Driving the shutter in the *opposite* direction from the wall is also recognised as an interruption, because it arrives as an explicit open/close command rather than a stop — but prefer the action.)

### Method 2: Calibrate All Covers Sequentially
On your gateway device page, click **Calibrate All Covers** (`button.<gateway name>_calibrate_all_covers`, e.g. `button.f454_gateway_calibrate_all_covers`). It calls `myhome.calibrate_cover` for every enabled cover of that gateway; a per-gateway lock runs them **one at a time**, because two motors moving on one paced session would make the timings meaningless. Covers still waiting report `phase: queued`. Position-reporting (advanced) covers are refused with an error in the log and the others continue. `myhome.stop_cover_calibration` stops the running cover and drops the whole queue.

### Method 3: Set Explicit Travel Time via Service Action
If your actuators enforce the 60-second factory cutoff, if your gateway is single-session (such as MH200 / MH200N), or if you already know the exact run duration (e.g. from a handheld stopwatch or technical datasheet), set it directly using the `myhome.set_cover_travel_time` action in **Developer Tools → Actions**.

You rarely need a handheld stopwatch: the integration times **every** run of a timed cover, calibration or not, and exposes the result as plain entity attributes — no card required.

1. Open the cover in Home Assistant (or use its wall switch) and press **Close**.
2. Press **Stop** (entity or wall switch) the moment the shutter reaches the bottom end stop.
3. Go to **Developer Tools → States**, filter on the cover entity: `last_run_seconds` is the motor-start-to-stop time of the run you just made and `last_run_direction` says `close`. The same attributes are listed under **Attributes** in the cover's more-info dialog.
4. Repeat with **Open** for the up time, then pass both numbers to the action (or put the attributes and a **Save** button on your dashboard with [Lovelace Recipe 6](lovelace_recipes.md#recipe-6-shutter-travel-time-workbench-stock-cards-only)):

```yaml
action: myhome.set_cover_travel_time
target:
  entity_id: cover.living_room_shutter
data:
  travel_time_down: 22.5
  travel_time_up: 24.0
```

The result is stored exactly like a measured calibration, with `calibration_source: manual`, and overwrites whatever a previous calibration attempt may have saved (also visible under `cover_travel_times` in the integration's diagnostics download). When the numbers you pass were measured on an identical shutter, add `copied_from: cover.<other_shutter>` to record where they came from; `calibration_source` then reads `copied` and the entity is exposed as a `copied_from` attribute.

To forget the measured or manual times and return to the `travel_time` from `myhome.yaml` (or the 25 s default when none is configured):
```yaml
action: myhome.reset_cover_travel_time
target:
  entity_id: cover.living_room_shutter
```

---

## 🏠 General, Area and Group Covers

A cover declared in `/config/myhome.yaml` on a **general** (`where: '0'`), **area** (`where: '1'`, `'00'`, `'100'`) or **group** (`where: '#3'`) address is one button for many shutters: *open*, *close* and *stop* send a single frame to that address, like the general button on a keypad.

Its state comes from the shutters it moves, not from the bus. At the end of a run every actuator reports its own stop (`*2*0*11##`, `*2*0*12##`, …) and none arrives for the general or area address (WHO_2 specification §3.0.1). So the cover shows *opening* / *closing* while any of its shutters is moving, *closed* when all are closed, and the average of their positions:

| Address | Shutters it follows |
| :--- | :--- |
| General `0` | every point-to-point cover of the gateway |
| Area `1`…`9`, `00`, `100` | the covers of that area on the same bus: area `1` is `11`…`19` and `0110`…`0115` |
| Group `#1`…`#255` | the covers listed under `members:`; group membership is programmed in the actuators and cannot be read from the bus |

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

**Behind an F422 interface** (`bus_interface: '02'`, logical `#4#` addressing), physical actuators reside on an isolated secondary SCS bus segment (e.g. `11#4#02` … `22#4#02`).

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
   When `open_cover`, `close_cover`, or `stop_cover` is called on a General cover behind an interface (`where: '0'`, `bus_interface: '02'`), the integration sends the native OpenWebNet broadcast frame directly (`*2*WHAT*0#4#<bus>##`).
   - Actuators on the interface bus respond simultaneously without dropping frames.
   - When the gateway echoes `*2*WHAT*0#4#<bus>##`, the integration scopes the incoming frame strictly to entities on that interface (`interface == "02"`), preserving total bus isolation.
   - When individual actuators reach their end stops and report status frames (`*2*0*11#4#02##`, etc.), Home Assistant tracks each entity and aggregates the General cover's position automatically.

2. **Paced Member Fan-Out (`PACED_FANOUT_DELAY = 0.3s`) for Percentage Positioning**:
   OpenWebNet has no broadcast protocol for percentage positioning (Dimension 10 positioning `*#2*WHERE*#10*LEVEL##` is strictly point-to-point; there is no broadcast level command).
   When `set_cover_position` is called on a scope/group cover, or when non-general scopes must address individual actuators, the integration staggers member commands with a safety interval of **0.3 s (300 ms)**.
   This gives the F422 hardware FIFO buffer adequate time to serialize frames onto the secondary bus, completely eliminating frame loss during multi-cover repositioning.

3. **Keypad Broadcast Reflection**:
   A general or area command from a physical wall switch or scenario unit also moves the individual covers in Home Assistant. A group or area cover whose shutters are not known yet ends its run after its own `travel_time`, so it never stays stuck on *opening* ([#433](https://github.com/OpenWebNet-HA/MyHOME/issues/433)).

### 💡 Automation Best Practices: The Home Assistant & BTicino Way

To ensure maximum reliability and preserve bus health:

#### Recommended: Target the General Scope Cover
Declare a General scope cover in `/config/myhome.yaml` for each bus interface, and target that single entity in your automations:

```yaml
# /config/myhome.yaml
00:03:50:XX:XX:XX:
  cover:
    all_shutters_oost:
      where: "0"
      bus_interface: "02"
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

---

## 🚪 Impulse Covers: Motorized Gates & Garage Doors (`WHO = 1`)

In BTicino MyHOME installations, motorized garage doors and sliding/swing entrance gates are controlled via **monostable impulse relays** on `WHO = 1` (such as `F411/2` or `F411U2`).

Because monostable relays only send a momentary pulse (`open` / `stop` / `close` / `stop`) with no inherent directional knowledge on the bus, Home Assistant implements the specialized **`MyHOMEImpulseCover`** entity equipped with the **`AccessController`** safety architecture.

### 🛡️ Safety Architecture & Threat Model

> [!CAUTION]
> **HOME ASSISTANT IS A SUPERVISORY AUTOMATION SYSTEM, NOT A CERTIFIED SAFETY SYSTEM**
> Primary entrapment and crush protection (e.g. European Standard **EN 12453 / EN 12445** or North American **UL 325**) **must be provided by the motorized gate or garage door hardware itself**: certified infrared photocells, safety contact edges, and mechanical force limiters built into the motor control board.
> Home Assistant's `AccessController` is designed exclusively to prevent Home Assistant from *causing* an unintended, accidental, or unattended movement.

#### 📊 Human-in-the-Loop Access Flow Architecture

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

#### Key Principles of the Human-in-the-Loop Policy:

1. **Zero Direct Movement on Service Calls**:
   Calls to `cover.open_cover`, `close_cover`, or `stop_cover` (whether from dashboards, mobile widgets, voice assistants, or automations) **never pulse the relay directly**. Instead, they create an authenticated approval request for an authorized person.
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
   Before a close pulse, an optional pre-warning flasher (`prewarn_light`) triggers for `prewarn_seconds`. After the pulse, an anti-stuck watchdog monitors motion: if the expected state is not reached within `travel_time + watchdog_margin`, a latching fault is asserted with **zero automatic retries**. A latched fault can only be cleared on site by an authorized user using the `myhome.acknowledge_cover_fault` action; an in-flight request or pre-warning can be cancelled at any time using `myhome.cancel_cover_request` (see [Services](services.md#14-myhomecancel_cover_request)).
6. **A Wall Button Starts the Gate, It Does Not Stop It**:
   A press of the wall button (or any other keypad on the bus) closes the relay and energizes the motor by itself; Home Assistant only sees the resulting `*1*1*WHERE##` frame afterwards. When such a frame arrives during an approval request or the pre-warning countdown, Home Assistant therefore *drops its own pending request* so that no second pulse follows and stops or reverses the gate. The movement the button started is not aborted. Use the hardware's own stop input or the remote control for an emergency stop.

### ⚙️ Configuring an Impulse Cover

An impulse cover needs `who: 1`, a single point-to-point `where` and, to be of any use, an `access:` block. **Without `access:` (or with an empty `allowed_users`) every open, close and stop request is refused** with `AccessDenied` and a warning is logged at start-up: the policy is fail-closed.

```yaml
# /config/myhome.yaml
00:03:50:XX:XX:XX:
  cover:
    garage_door:
      who: 1
      type: impulse_relay            # optional; only valid together with who: 1
      where: "71"                    # one relay, never a general, area or group address
      name: Garage door
      device_class: garage           # garage or gate (default gate)
      state_sensor: binary_sensor.garage_door_closed   # off = closed, on = not closed
      travel_time: 25                # seconds, for the dashboard estimate and the watchdog
      pulse_duration: 0.5            # seconds the relay is closed (0.2 - 2.0)
      min_cycle_time: 5              # seconds between two pulses (1 - 120)
      pin_code: "1234"               # optional second factor, asked on the phone
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
| `state_sensor` | Binary sensor of the ground-truth position. Without it every pulse is treated as one that can close the door. |
| `pulse_duration` | Relay pulse in seconds. `0.5` uses the timed WHAT 18 command where the installed OWNd has it, and ON followed by OFF otherwise. |
| `min_cycle_time` | Deadband after a pulse that reached the bus. A failed send does not start it. |
| `pin_code` | PIN asked in the notification. A reply that carries no text is refused without counting as a wrong attempt. |
| `access.*` | See the flow above: `allowed_users`, `approvers`, `remote_close`, `camera`, `safety_devices_verified`, `safety_check_days`, `prewarn_light`, `prewarn_seconds`, `close_block_entity`, `watchdog_margin`, `approval_timeout`. |

> [!NOTE]
> `type: impulse_relay` together with `who: 2` (and `type: shutter` or `standard` together with `who: 1`) is rejected when the configuration is validated; it used to fall back to an ordinary shutter silently.

### 🔒 Configuring a Door Strike Lock

```yaml
00:03:50:XX:XX:XX:
  lock:
    front_door:
      who: 6                         # the only value; WHO 1 relays are covers
      where: "4001"                  # 4000-4095 style endpoints; append #2 on a riser installation: "4001#2"
      name: Front door
      code: "1234"                   # optional; asked on every unlock
      pulse_duration: 0.5            # seconds until the lock shows locked again
```

Locks exist only when they are configured here (or were created earlier and are restored from the registry). A lock release seen on the bus never creates a lock entity, because such an entity would carry no code.

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
  │  - Enforces AccessController          │   │  - Direct momentary pulse (1-3s)      │
  │  - Dual binary sensor feedback        │   │  - Optional PIN authentication        │
  │  - Biometric challenge-response nonce │   │  - Automatic re-lock state machine    │
  │  - 5s pre-warning flasher             │   │  - Doorbell one-tap unlock safe       │
  │  - Anti-stuck watchdog (latched fault)│   │                                       │
  │  - Certified EN 12453 safety check    │   │                                       │
  └───────────────────────────────────────┘   └───────────────────────────────────────┘
```

| Physical Device | Required Entity Platform | Why |
| :--- | :--- | :--- |
| **Motorized Gates & Garage Doors** | `cover` (`type: impulse_relay`) | Heavy kinetic machinery with kinetic entrapment/crush risk. Must use `AccessController` with sensor validation and pre-warning. **Never configure a motorized gate or garage as a `lock`**. |
| **Pedestrian Door Strikes** | `lock:` block, `who: 6` only | Low-mass momentary electric door buzzers (elettroserrature) on a WHO 6 entrance panel that release a pedestrian latch. A `lock` cannot be a WHO 1 relay: that would bypass the access policy of a gate, so `who: 1` is rejected there. |

---

## 🔄 Legacy YAML Note

> [!NOTE]
> If you are upgrading from legacy v0.9 installations and still have manual `cover:` blocks in `/config/myhome.yaml`, please refer to the [v0.9.4 Legacy Cover Documentation](../../0.9.4/configuration/covers/) or the [Legacy YAML Migration Guide](../migration/legacy-yaml.md). In v2, all covers are managed dynamically via Home Assistant's native registry.

