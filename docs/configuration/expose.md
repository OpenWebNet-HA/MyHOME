# Exposing Home Assistant Entities & Clock to SCS Bus

The **Expose to Bus** engine allows Home Assistant to project external smart home devices, sensors, energy meters, and the system clock directly onto the Legrand / BTicino OpenWebNet SCS bus on unused addresses.

By emulating virtual OpenWebNet devices, physical wall controls, touchscreens (such as the Legrand `067292`, BTicino `H4890`, `LN4890`, `AM4890`, or `10"` touchscreens), and bus supervisors can interact with non-MyHOME devices (e.g. Philips Hue, Zigbee/Matter lamps, Sonos players, Somfy blinds, Shelly relays, and ESPHome sensors) as if they were native SCS hardware.

---

## 🌟 Common Use Cases

1. **Touchscreen Control of Non-SCS Devices (`WHO = 1` & `WHO = 2`)**:
   Tap an icon or slider on a Legrand `067292` or BTicino `H4890` wall screen to control a Zigbee, Matter, or Wi-Fi device in Home Assistant. Changes made in Home Assistant or voice assistants instantly update the status icon and LED on the wall screen.
2. **Unpopulated Physical Wall Switches (`WHO = 1`)**:
   Use an unused rocker on an `H4652/2`, `H4652/3`, `L4652/2`, or `K4652M2` physical wall control configured as a point-to-point light (`WHO = 1`) to toggle or dim a TV, media player, smart bulb, or relay in Home Assistant. *(Note: On an unpopulated 2-wire address without a physical SCS actuator, basic 2-wire pushbuttons cannot latch steady load-status LEDs and flash briefly on press due to the missing hardware link-layer ACK — see [Wall-Switch LEDs & TCP Client Limitations](#2-hardware-limitations-on-unpopulated-who-1-addresses-wall-switch-leds-tcp-clients) below.)*
3. **Virtual Temperature Probes (`WHO = 4`, gateway-dependent)**:
   On gateways whose `bt_termo` daemon translates `#0` temperature writes onto the SCS bus (verified on `F461`), publish an outdoor weather service temperature, Zigbee room sensor, or ESPHome probe onto an unused zone master address (`1..99`, `DIMENSION 0`). *(Rejected by `F454`, `MH200N`, `MH202`, and wall touchscreens such as `067292` / `H4890` — see [Gateway Compatibility](#gateway-compatibility-firmware-verification) below.)*
4. **Virtual Energy & Power Meters (`WHO = 18`, gateway-dependent)**:
   On gateways verified for `WHO = 18` dimension reports (`F461`), publish active power (`DIMENSION 113`, Watts) or energy totalizers (`DIMENSION 51..54`, Wh) onto OpenWebNet monitor sessions. *(Cannot inject synthetic power/energy onto a physical 2-wire SCS bus for `067292` / `H4890` wall touchscreens — see [Gateway Compatibility](#gateway-compatibility-firmware-verification) below.)*
5. **Bus Clock & Calendar Synchronization (`WHO = 13`)**:
   Keep all wall screens (`067292`, `H4890`, `10"` touchscreens), thermostats, and chronothermostats on the SCS bus synchronized to Home Assistant's local time, date, and daylight saving time (DST) across all gateway models.

---

## 🛠️ How to Configure

The integration supports two methods for configuring exposed bindings: **UI Config Subentries** (recommended) and **Declarative YAML** (`myhome.yaml`).

### Method 1: Home Assistant UI (Config Subentries)

In Home Assistant, exposed bindings are configured as **Config Subentries** on your gateway entry using a **2-step `WHO`-tailored wizard** (with zero breaking changes — Home Assistant still stores the exact same canonical `WHERE` string internally):

1. In Home Assistant, navigate to **Settings → Devices & Services → Integrations → MyHOME**.
2. Click on your gateway card (e.g. `F454`, `067292`, or `MyHomeServer1`).
3. Scroll down to the **Subentries** section (or click the three dots `⋮` menu in the upper corner).
4. Click **+ Add exposed entity** (or **Expose entity to SCS bus**).
5. **Step 1 — Choose `WHO` subsystem**: Select the OpenWebNet domain (`WHO 1`, `WHO 2`, `WHO 4`, `WHO 13`, or `WHO 18`; subsystems whose injection is unsupported by your gateway firmware are hidden automatically) and click **Next**.
6. **Step 2 — Configure subsystem fields**: Only the fields that apply to the chosen `WHO` are shown:
   - **`WHO = 1` (Lighting / Switch) & `WHO = 2` (Automation / Cover)**: Pick **`Area (A, 0–10)`** and **`Point (PL, 1–15)`** plus an optional **Local-bus interface (`#4#01–#4#09` or `#4#11–#4#15`)** directly from *MyHOME_Suite* — or leave them blank and paste a canonical **Raw `WHERE` override** (`88`, `08`, `0812`, `0012`, `1008`, `88#4#01`) from Bus Monitor traces. On reconfigure, existing bindings automatically pre-fill `A`, `PL`, and `interface`.
   - **`WHO = 4` (Temperature Probe)**: Enter a probe `WHERE` address (`1–99` zone master probe, `100–900` external probe, or `101–899` `PZZ`), source entity, and deadband/interval options (`dimension` is fixed to `0`; `WHO 1/2`-only options and custom actions are hidden).
   - **`WHO = 18` (Energy / Power Meter)**: Enter a meter `WHERE` (`5N` or `7N#0` with `N = 1..255`, i.e. `51..59`, `510..599`, `5100..5255`, or `71#0..79#0`, `710#0..799#0`, `7100#0..7255#0`) and pick an **OpenWebNet energy/power dimension** (`113`, `51–54`) from the dropdown.
   - **`WHO = 13` (Bus Clock & DST Sync)**: Pick the **Clock dimension** (`22`, `0`, or `1`), broadcast interval (`0` for query-only), and read-response toggle (no `WHERE` or entity required).

| Field | Applies to | Description | Example |
| :--- | :--- | :--- | :--- |
| **WHO Subsystem** *(Step 1)* | All | The OpenWebNet domain to emulate (`1`, `2`, `4`, `13`, or `18`). | `WHO 1 — Lighting / Switch / Virtual Actuator` |
| **Area (`A`, `0–10`) & Point (`PL`, `1–15`)** | `WHO = 1, 2` | *MyHOME_Suite* area and point selectors; automatically encoded to canonical `WHERE` on save. | `A = 8, PL = 12` → `0812` |
| **Local-bus Interface** *(Optional)* | `WHO = 1, 2` | Optional F422 routing interface (`Main SCS bus`, `01–09`, or `11–15` → `#4#01`..`#4#09`, `#4#11`..`#4#15`). | `01` → `#4#01` |
| **Raw `WHERE` Override** *(Optional on `1, 2`; required on `4, 18`)* | `WHO = 1, 2, 4, 18` | Canonical OpenWebNet `WHERE` string; when entered on `WHO = 1 / 2`, overrides `A` / `PL` / interface. *(Omitted on `WHO = 13`)* | `88`, `0812#4#01`, `50`, `51` |
| **Source Entity** *(Optional on `1, 2` with actions)* | `WHO = 1, 2, 4, 18` | The Home Assistant entity providing state and commands. | `light.hue_living_room` |
| **State Attribute** *(Optional)* | `WHO = 1, 2, 4, 18` | Entity attribute providing the value (`brightness` for `WHO = 1` dimmers; `current_position` for `WHO = 2` covers). | `brightness` |
| **OpenWebNet Dimension** | `WHO = 13, 18` | Dropdown selector (`22`, `0`, `1` for clock; `113`, `51..54` for power/energy). *(Fixed to `0` on `WHO = 4`; hidden on `1, 2`)* | `113` |
| **Cooldown** *(Optional)* | `WHO = 1, 2, 4, 18` | Rate-limiting debounce window in seconds (default: `1.0` s). | `1.0` |
| **Minimum Delta** *(Optional)* | `WHO = 4, 18` | Deadband threshold; changes smaller than this value are not sent. | `0.5` |
| **Periodic Broadcast Interval** *(Optional)* | All | Interval in seconds for recurring heartbeat broadcasts (`0` disables proactive clock sync on `WHO = 13`). | `60` |
| **Respond to Bus Queries** | All | Reply to incoming `*#WHO*WHERE...##` status and dimension read requests (default: `true`). | `true` |
| **Bidirectional** | `WHO = 1, 2` | Act on incoming `WHO = 1` / `WHO = 2` bus commands and wall-switch presses (default: `true`). | `true` |
| **Brightness Step (%)** | `WHO = 1` | Relative step size (`1..100%`) applied per `WHO = 1` dimmer step-up / step-down command (default: `10%`). | `25` |
| **Custom 'On' States** *(Optional)* | `WHO = 1` | Comma-separated list of states considered ON (e.g. `on, playing, open`). | `on, playing` |
| **Value Template** *(Optional)* | `WHO = 1, 2, 4, 18` | Jinja2 template to extract or transform values before publishing. | `{{ value \| round(1) }}` |
| **Turn-on / Turn-off / Step-up / Step-down Actions** *(Optional)* | `WHO = 1, 2` | Visual Home Assistant action sequences overriding default entity service calls (`turn_on` / `turn_off` for `WHO = 1` & `WHO = 2`; `step_up` / `step_down` for `WHO = 1`). | `media_player.volume_up` |

Click **Submit**. The binding activates immediately without requiring a Home Assistant restart.

---

### Method 2: Declarative YAML (`myhome.yaml`)

You can also define exposed bindings in `/config/myhome.yaml` under your gateway's MAC address (on single-gateway installations, `expose:` can also sit directly at the top level of `/config/myhome.yaml`):

```yaml
# /config/myhome.yaml
"00:03:50:01:02:03":   # Your Gateway MAC address (optional on single-gateway setups)
  expose:
    # -----------------------------------------------------------------
    # 1. Virtual Light / Dimmer (WHO = 1)
    # Short press on WHERE "83" turns the smart bulb ON/OFF; holding up/down
    # steps brightness by 25%. Setting 'attribute: brightness' also mirrors
    # brightness level changes back onto the bus.
    # -----------------------------------------------------------------
    - who: 1
      where: "83"
      entity_id: light.smart_bedroom
      attribute: brightness
      bidirectional: true
      brightness_step_pct: 25
      cooldown: 0.5

    # -----------------------------------------------------------------
    # 2. Virtual Cover / Shutter (WHO = 2)
    # Tapping up/down/stop on wall screen at WHERE "31" controls pergola curtain.
    # -----------------------------------------------------------------
    - who: 2
      where: "31"
      entity_id: cover.pergola_curtain
      bidirectional: true

    # -----------------------------------------------------------------
    # 3. Virtual Temperature Probe (WHO = 4, Dimension 0)
    # Publishes outdoor temperature onto unused zone master address 50
    # (1..99 required on F461 / bt_termo; 100..900 only on reply-injection gateways).
    # -----------------------------------------------------------------
    - who: 4
      where: "50"
      dimension: 0
      entity_id: sensor.outdoor_temperature
      cooldown: 5.0
      min_delta: 0.2
      periodic_send: 60

    # -----------------------------------------------------------------
    # 4. Virtual Active Power Meter (WHO = 18, Dimension 113)
    # Publishes grid active power (W) onto meter address 51.
    # Auto-converts kW -> W if the sensor unit of measurement is kW.
    # -----------------------------------------------------------------
    - who: 18
      where: "51"
      dimension: 113
      entity_id: sensor.grid_active_power
      cooldown: 2.0
      min_delta: 10.0

    # -----------------------------------------------------------------
    # 5. Virtual Energy Counter / Totalizer (WHO = 18, Dimension 51)
    # Publishes cumulative grid energy totalizer (Wh) onto meter address 51.
    # Auto-converts kWh -> Wh if the sensor unit of measurement is kWh.
    # -----------------------------------------------------------------
    - who: 18
      where: "51"
      dimension: 51
      entity_id: sensor.grid_energy_consumed
      cooldown: 5.0
      min_delta: 100.0   # Broadcast on 100 Wh increments

    # -----------------------------------------------------------------
    # 6. Bus Clock & DST Sync (WHO = 13, Dimension 22)
    # Broadcasts atomic time, date, and calendar once every hour.
    # Note: Set 'periodic_send: 0' for query-only mode (answers *#13** reads
    # without proactive pushes, ideal for NTP-synchronized gateways).
    # -----------------------------------------------------------------
    - who: 13
      dimension: 22
      periodic_send: 3600
      respond_to_read: true
```

---

## 🔢 OpenWebNet Addressing Reference (`A` / `PL` → `WHERE`)

When configuring `WHO = 1` (Lighting / Switch) or `WHO = 2` (Automation / Cover) in **MyHOME_Suite** (`Object 400` / `401`) or on physical configurators, point-to-point addresses consist of **`A` (Area / Ambiente, `0–10`)** and **`PL` (Point / Punto Luce, `1–15`)** *(note: Expose enforces the published OpenWebNet point-to-point range `PL = 1–15`; `PL = 0` is the unconfigured default in MyHOME_Suite)*.

On the OpenWebNet wire and in `myhome.yaml` (`where:`), leading zeros are **not** uniformly 2-digit or 4-digit — they depend on whether either half has two digits:

| `A` (`0–10`) | `PL` (`1–15`) | Canonical `WHERE` | Example | Common Mistake (Rejected) |
| :---: | :---: | :---: | :---: | :--- |
| `1–9` | `1–9` | **2 digits** (`APL`) | `A = 8, PL = 8` → **`88`** | `0808` *(4-digit `01..09` is valid only when `PL = 10–15`)* |
| `0` | `1–9` | **2 digits** (`0PL`) | `A = 0, PL = 8` → **`08`** | `8` / `008` *(area `8` / 3-digit; note: legacy `0008` is auto-normalized to `08`)* |
| `1–9` | `10–15` | **4 digits** (`0APL`) | `A = 8, PL = 12` → **`0812`** | `812` *(3-digit strings are invalid)* |
| `0` | `10–15` | **4 digits** (`00PL`) | `A = 0, PL = 12` → **`0012`** | `012` / `12` |
| `10` | `1–9` | **4 digits** (`100PL`) | `A = 10, PL = 8` → **`1008`** | `108` |
| `10` | `10–15` | **4 digits** (`10PL`) | `A = 10, PL = 12` → **`1012`** | `001012` |

- **Optional local-bus routing (`F422` interface)**: Append `#4#01`..`#4#09` or `#4#11`..`#4#15` (for example `88#4#01` or `0812#4#01`), or pick the interface in the UI **Local-bus interface** dropdown.
- **Non-`A/PL` subsystems**:
  - **`WHO = 4` (Temperature Probe)**: Zone master probe `1–99` (required on `F461` / `bt_termo`), external probe `100–900`, or individual probe `PZZ` (`101–899`).
  - **`WHO = 18` (Energy / Power Meter)**: Meter address `5N` or `7N#0` with `N = 1..255` (i.e. `51..59`, `510..599`, `5100..5255`, or `71#0..79#0`, `710#0..799#0`, `7100#0..7255#0`).
  - **`WHO = 13` (Bus Clock)**: Bus-wide broadcast; no `WHERE` address is used.

---

## 💡 Virtual Dimmers & `MyHOME_Suite` Wall Control Modes (`WHO = 1` vs `CEN+`)

When using a physical 2-wire SCS wall control (such as BTicino `H4652/2`, `L4652/2`, `AM5832/2`, `K4652M2`, or Legrand `067552`) to dim a Home Assistant light on an **unpopulated** SCS address (an address with no physical BTicino actuator wired on the 2-wire bus):

### 1. Recommended `MyHOME_Suite` Modes for `WHO = 1` Expose
In **MyHOME_Suite**, configure the wall control channel as **Light control** (`Object 400`, *Beleuchtung/schalten*) with **Point-to-point** addressing (`A` / `PL`, e.g. `A = 8, PL = 3` → `WHERE = 83`) and either of the two standard two-button (`O/I`) dimmer modalities:

- **`ON/OFF and point to point dimming`** (`M = 9` — German: *"ON/OFF und Regelung nur Punkt-Punkt"*; identical to physical configurator `M = O/I`)
- **`ON/OFF and dimming`** (`M = 3` — German: *"EIN/AUS und Dimmerregelung"*)

Both modalities work identically for a point-to-point virtual dimmer:

- **Short press UP / DOWN**: Sends `*1*1000#1*WHERE##` / `*1*1000#0*WHERE##` to turn the Home Assistant entity ON or OFF and mirrors `*1*1*WHERE##` / `*1*0*WHERE##` onto the bus (updating `WHO = 1` tiles on wall touchscreens such as `067292`, `H4890`, and `L4684`).
- **Hold DOWN**: Sends `*1*1000#31*WHERE##` and steps brightness down by `brightness_step_pct` (or runs `step_down_action`).
- **Hold UP**: Because no physical SCS dimmer hardware-ACKs the unpopulated address on the 2-wire bus, `H4652/2` repeats `*1*1000#1*WHERE##` every **~2 seconds** (with a ~200 ms SCS link-layer retry per step) instead of switching to `*1*1000#30*WHERE##`. `ExposeManager` automatically deduplicates the ~200 ms SCS retries and treats consecutive held `*1*1000#1*WHERE##` frames (`1.6–3.0 s` apart while the bound entity is already on) as `step_up` (`+brightness_step_pct` or `step_up_action`), while keeping single taps, quick double-presses, and turn-on from off idempotent.

> [!TIP]
> **Want faster (~0.5 s) smooth dimming without the ~2 s unacknowledged SCS timeout?**
> On `H4652/2` / `L4652/2` / `067552` / `K4652M2`, you can alternatively configure the module in **MyHOME_Suite** as **Scheduled scenario PLUS (`CEN+`, `WHO = 25`, `Object 406`)** instead of `WHO = 1` Expose. `CEN+` frames are broadcast events (`*25*21#<btn>*2<addr>##` short press, `22` hold start, `23` ~0.5 s hold repeat, `24` hold release) that require no physical actuator ACK on the 2-wire bus and appear directly as Home Assistant device triggers — at the trade-off that `CEN+` buttons do not track `WHO = 1` load-state LEDs or Area/General light commands.

### 2. Hardware Limitations on Unpopulated `WHO = 1` Addresses (Wall-Switch LEDs & TCP Clients)

When `WHERE` is a purely virtual `WHO = 1` address with **no physical SCS actuator** wired on the 2-wire bus, two hardware/firmware constraints of the 2-wire SCS bus and OpenWebNet gateways (`MH201`, `MH200N`, `F454`, `F453`, `F461`, `MyHomeServer1`, `H4684`) apply:

1. **Basic 2-Wire Pushbutton LEDs (`H4652/2`, `H4652/3`, `L4652/2`, `AM5832/2`)**:
   - **Missing ~35 ms 2-wire link-layer hardware ACK**: When a physical `WHO = 1` pushbutton sends a unicast command (`A8 <WHERE> 00 12 <00|01> <xor> A3`), it waits ~35 ms for the target physical SCS actuator to assert a hardware link-layer ACK (`0xA5` / PIC `$08`). Because no physical actuator occupies `WHERE` (and OpenWebNet IP gateways are passive bus peers that do not synthesize link-layer ACKs for arbitrary unpopulated addresses), the wall switch retransmits once ~200 ms later and **flashes its LED** to indicate a delivery fault / unpopulated address (on multi-channel controls such as `H4652/3` with shared indicator logic, this fault flash can also blank an adjacent channel's LED until that native actuator next reports its status).
   - **Command telegram (`<WHERE> 00 12 <00|01>`) vs. Actuator Status broadcast (`B8 <WHERE> 12 <00|01>`)**: Although OpenWebNet TCP uses `*1*1*WHERE##` / `*1*0*WHERE##` for both commands and status events, on the 2-wire SCS bus they are distinct telegram opcodes. Every OpenWebNet gateway firmware translates outbound TCP `*1*1*WHERE##` / `*1*0*WHERE##` into an SCS **command** telegram (`$03 <WHERE> 00 12 <00|01>\r`), never into a `0xB8` **actuator status broadcast** (`B8 <WHERE> 12 <00|01>`), which is only emitted by a physical SCS actuator. While wall touchscreens (`067292`, `H4890`, `L4684`, `10"` screens) update their UI tiles from command/translation frames, basic 2-wire pushbuttons only latch steady load-status LEDs from `0xB8` actuator status broadcasts.
   - **How to avoid the LED error flash**: Configure the pushbutton channel as **`CEN` (`WHO = 15`)** or **`CEN+` (`WHO = 25`, `Object 406`)** instead of `WHO = 1`. Scenario frames are connectionless broadcast events that expect no point-to-point actuator ACK on the 2-wire bus, so pressing the button never triggers an error flash (at the trade-off that `CEN`/`CEN+` buttons do not maintain stateful load LEDs).
2. **External OpenWebNet TCP Clients (`iMyHome`, Third-Party Apps) & Bus Monitor Injection**:
   - Physical 2-wire devices (wall switches and 2-wire wall screens) transmit on the 2-wire SCS wire, entering the gateway from the **bus transceiver side** (`$17 ...`) and forwarding to Home Assistant's TCP event monitor session (`*99*1##`) as `*1*1000#1*WHERE##` or `*#1*WHERE##`.
   - By contrast, frames sent on a **TCP command session (`*99*0##`)** by external TCP clients (such as the `iMyHome` app) or by Home Assistant's **Bus Monitor** transmit tool (`ws_bus_monitor_send`) are written **out** to the 2-wire transceiver (`$03 <WHERE> 00 15 00\r` for `*#1*WHERE##`, `$03 <WHERE> 00 12 00\r` for `*1*1*WHERE##`). OpenWebNet gateways (`MH201`, `MH200N`, `F454`, `F461`, `MyHomeServer1`) do **not** loop TCP command-session status reads (`*#1*WHERE##`) or unpopulated `WHO = 1` commands back onto TCP event monitor sessions (`*99*1##`) unless a physical actuator on the 2-wire bus replies with `B8 <WHERE> 12 <state>`. Consequently, `iMyHome` receives `NACK (*#*0##)` on `*#1*WHERE##` (displaying the unpopulated light as greyed out), and Bus Monitor command-session injections (`*1*1*WHERE##`, `*#1*WHERE##`) record `TX` without reaching `ExposeManager`.

---

## 🎛️ Advanced: Custom Script Actions

For complex integrations (such as cycling media playlists, executing sequences, or calling third-party services with conditions), you can configure `turn_on_action`, `turn_off_action`, `step_up_action`, and `step_down_action` either directly in the **UI Config Subentry** action pickers or in `myhome.yaml` using Home Assistant's native `Script` syntax:

```yaml
# /config/myhome.yaml
"00:03:50:01:02:03":
  expose:
    - who: 1
      where: "95"
      entity_id: media_player.kitchen_sonos
      on_states:
        - playing
      turn_on_action:
        - action: media_player.play_media
          target:
            entity_id: media_player.kitchen_sonos
          data:
            media_content_id: "http://icecast.radiofrance.fr/fip-midfi.mp3"
            media_content_type: "audio/mp3"
      turn_off_action:
        - action: media_player.media_pause
          target:
            entity_id: media_player.kitchen_sonos
      step_up_action:
        - action: media_player.volume_up
          target:
            entity_id: media_player.kitchen_sonos
      step_down_action:
        - action: media_player.volume_down
          target:
            entity_id: media_player.kitchen_sonos
```

### Action Script Variables & Templates

During script execution, the following template variables are available:

- `{{ this.state }}`: The current state of the source entity.
- `{{ entity_id }}`: The entity ID of the bound entity.
- `{{ who }}`: The OpenWebNet WHO subsystem integer (`1` or `2`).
- `{{ where }}`: The bound SCS WHERE address string.

> [!TIP]
> `{{ who }}` and `{{ where }}` are especially useful when sharing a single reusable Home Assistant script or automation action across multiple exposed wall keys or virtual actuators.

---

## 🔬 How the Expose Engine Works

```mermaid
flowchart TD
    subgraph HA["Home Assistant"]
        State["State Change Event<br>(e.g. Light ON / Temp 21.5°C)"]
        Script["Custom Action Script<br>(turn_on_action)"]
    end

    subgraph Engine["ExposeManager & Echo Guard"]
        Filter{"Deadband & Cooldown<br>|Δ| >= min_delta?"}
        Scheduler["FrameScheduler<br>(Write Future Bound)"]
        EchoGuard["Echo Guard (1.5s TTL)<br>Absorbs translations & loops"]
    end

    subgraph Bus["OpenWebNet SCS Bus"]
        GW["Gateway Socket<br>(TCP port 20000)"]
        Screen["Legrand 067292 / BTicino H4890<br>(Wall Screen / Physical Key)"]
    end

    State --> Filter
    Filter -->|Pass| Scheduler
    Scheduler -->|Dispatch Frame| GW
    GW --> Screen
    GW -.->|Bus Echo| EchoGuard
    EchoGuard -.->|Absorb Loopback| Engine

    Screen -->|Button Press Frame| GW
    GW --> Engine
    Engine -->|Trigger Action| Script
    Engine -->|Update Target| State
```

### Key Architectural Invariants

1. **Write-Future-Bound Echo Guarding**:
   When Home Assistant sends a state mirror frame to the bus (e.g. `*1*1*25##` or `*#2*25*#11#001*50##`), gateways broadcast multi-frame translation echoes back onto the monitor session (`*1*1000#1*25##`, `*2*1000#0*25##`, `*#2*25*#11#001#1*50##`). The Expose engine tracks pending write futures and arms an echo-guard with a `1.5` s TTL to absorb these loops, preventing self-oscillations.
2. **Trailing-Edge Debouncing**:
   Rapid state changes (such as slider dragging in Home Assistant) are debounced within the `cooldown` window. Once the cooldown timer expires, the latest state is sent (eventual consistency).
3. **Queue Superseding**:
   A high-priority user command or wall-button press immediately supersedes any pending mirror frame for the same target, ensuring that an outdated state change never arrives after a button press.

---

## 🏛️ Gateway Compatibility & Firmware Verification

Different gateway models handle bus frame injection differently:

| OpenWebNet Domain | Gateway Compatibility | Required Frame Form | Notes |
| :--- | :---: | :--- | :--- |
| **`WHO = 1` (Lights / Switches)** | **All Gateways** | `*#1*WHERE*#1*LEVEL*0##` (or `*1*1*WHERE##` / `*1*0*WHERE##`) | Universal support for wall touchscreens (`067292`, `H4890`, `L4684`) and physical 2-wire wall-switch input (`*1*1000#1/0*WHERE##`) across F454, F455, F459, MH200N, MH201, MH202, MHS1, F461, 067292, and H4890. On unpopulated addresses without a physical SCS actuator, basic 2-wire pushbuttons (`H4652/2`, `H4652/3`) do not receive `0xB8` actuator status / link-layer ACKs for steady LEDs, and TCP command-session clients (`iMyHome`) are not looped back to event sessions. |
| **`WHO = 2` (Covers / Shutters)** | **All Gateways** | `*#2*WHERE*#11#001*POS##` (or `*2*1/2/0*WHERE##`) | Universal support; translation echoes are cleanly absorbed. |
| **`WHO = 13` (Clock & DST)** | **All Gateways** | `*#13**#22*H*M*S*T*W*D*M*Y##` | Synchronizes on-screen clocks across all models (`067292`, `H4890`, `F454`, etc.). Set `periodic_send: 0` for query-only mode. |
| **`WHO = 4` (Virtual Temperature)** | Verified on **`F461`** (`WHERE` `1..99`) | `*#4*WHERE*#0*TTTT##` | `bt_termo` (`F461`) translates unused zone master addresses `1..99` (e.g. `"50"`) onto the SCS bus (`D1 <ZZ> 03 02 F0 <TT> <TT> 00`), but rejects 3-digit probe addresses (`100..900`). Rejected (`NACK`) for all addresses on `F454`, `F459`, `MH200N`, `MH202`, `067292`, `H4890`, and `H4684`. |
| **`WHO = 18` (Virtual Energy Meter)** | Verified on **`F461`** (TCP session only) | `*#18*WHERE*113*WATT##` | Rejected (`NACK`) by wall touchscreens (`067292`, `H4890`, `H4684`). `bt_supervisione` / `bt_energy` gateways (`F454`, `F459`, `MH200N`, `MH202`, `MHS1`, `F461`) `ACK` on TCP but emit an SCS active-power **read** request (`D1 A1 02 33 00 1D 00 00`), discarding the value — so synthetic `WHO = 18` readings cannot reach physical 2-wire SCS wall screens. |

> [!NOTE]
> For physical wall touchscreens (Legrand `067292`, Arteor `573958`, BTicino `H4890`, `LN4890`), configure virtual light/switch (`WHO = 1`), shutter (`WHO = 2`), or scenario tiles with unused point-to-point addresses in **MyHOME_Suite**, and add clock sync (`WHO = 13`). On a shared SCS bus with a primary gateway (e.g. `F454` + `067292`), create the expose subentries on the **primary** gateway and keep `WHO = 4` / `WHO = 18` undelegated on the primary gateway.

---

## 🔍 Diagnostics & Troubleshooting

- **Check Active Bindings**: Open **Settings → Devices & Services → MyHOME → Download Diagnostics**. The JSON file contains an `"expose"` section detailing all active bindings, throttles, and last-sent timestamps.
- **Unsupported / Unverified Gateway Warning**: The UI Config Subentry flow hides `WHO = 4` and `WHO = 18` on gateways whose firmware cannot inject them onto the SCS bus, and rejects 3-digit `WHO = 4` probe addresses (`100..900`) on `#0`-only gateways (`F461`). If a legacy YAML or pre-existing binding targets an unsupported gateway or address, a single warning is logged at startup (*"Expose WHO=... is disabled: gateway ... Nothing is sent for this binding"*).
- **Field-Level `WHERE` Validation Hints**: Both the UI raw `WHERE` override field and `myhome.yaml` report specific corrective hints for common `A` / `PL` mistakes (e.g. `0808` suggests `88`, `812` suggests `0812`, `108` suggests `1008`, and `PL = 0` explains that point addresses start at `1`).
- **Duplicate Address Error (`already_configured`)**: Each `(who, where, dimension)` triplet must be unique per gateway.
