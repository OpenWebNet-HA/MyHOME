# Sound System / Media Player (`WHO = 16`)

This guide explains how to configure and automate the BTicino / Legrand **Diffusione Sonora** (Sound System) in Home Assistant using the MyHOME integration.

---

## 🎵 Subsystem Architecture

In MyHOME systems, multi-room audio is managed by dedicated hardware analog matrices and room amplifiers communicating over the SCS bus using **OpenWebNet WHO = 16**.

### Supported Hardware
- **Audio Matrix**: F441, F441M (4 audio input sources, up to 8 independent stereo room amplifier outputs)
- **Room Amplifiers**: 3484, 3484/1, 3487, F500
- **Audio Controls**: L/N/NT4684, 3529

> [!IMPORTANT]
> **Hardware-Only Analog Matrix**: The BTicino F441 / F441M is a purely analog matrix switcher. It does not contain an Ethernet port or digital audio decoder and cannot stream IP audio by itself. It routes line-level analog signals from physical source inputs (Source 1 to Source 4) to its room outputs, one output per **environment**. Amplifiers are addressed `EA` (`01`–`99`): environment digit, then amplifier number within it.

---

## 🔀 The Dynamic Proxy Architecture

To bridge modern streaming platforms (such as **Music Assistant**, **Spotify Connect**, or **Squeezelite / LMS**) into the analog BTicino matrix without audio artifacts, the MyHOME integration implements the **Dynamic Proxy** pattern.

```
┌──────────────────────────────────────────────┐
│  Home Assistant / Music Assistant (Player)   │
│  "media_player.living_room_sound"            │
└──────────────────────┬───────────────────────┘
                       │ (Proxy Layer)
                       ▼
         ┌───────────────────────────┐
         │ Decoder Pool Management   │
         │ - Dynamic claim / release │
         │ - Gain staging (+12 dB)   │
         │ - State & metadata mirror │
         └─────┬───────────────┬─────┘
               │               │
      [IP Service Call]   [OpenWebNet SCS Bus]
               │               │
               ▼               ▼
       ┌───────────────┐ ┌───────────────┐
       │ Audio Streamer│ │ F441M Matrix  │
       │ (Squeezelite /│ │ & Amplifiers  │
       │  WiiM / Pi)   │ │ (01..99)      │
       └───────┬───────┘ └───────▲───────┘
               │ Analog Line-In  │
               └─────────────────┘
```

### Source switching

Selecting a source sends the same two frames a wall panel puts on the bus:

| Frame | Meaning |
| :--- | :--- |
| `*16*3*10S##` | Activate source device `S` |
| `*16*3*1ES##` | Route environment `E` to source `S` |

The routing address carries the **environment** digit of the amplifier
address, not the amplifier digit. Amplifier addresses are `EA` — environment
followed by amplifier — so zone `23` lives in environment 2 and is routed with
`121` (source 1) or `122` (source 2). The F441M switches per output and an
output serves a whole environment, so **every amplifier in that environment
follows the switch**. Zones 22 and 23 cannot play different sources; that is
matrix hardware, not an integration limitation. The integration holds to it:

- **One stream per environment.** While zone 22 streams from a decoder,
  `play_media` or a source change on zone 23 is refused with an error naming
  zone 22, instead of silently switching zone 22 off its stream.
- **Environment 0 cannot be switched.** Amplifiers `01`–`09` would be routed
  with `10S`, which is the source device address itself. Source selection is
  refused there and no default can be set for it; use a wall panel.
- **Only two-digit amplifier addresses are routed.** The WHO=16 address table
  lists amplifiers as `01`–`99`. A single-digit address such as `1` in a YAML
  configuration does not say which environment it belongs to (`01` or `11`?),
  so it is never routed. Write the address with both digits.

> Earlier releases refused to send these frames, on the assumption that they
> caused relay clicks on MH200-class gateways. Bus captures on an MH200 show
> clean switching. The real problem was a routing address built from the wrong
> digit, which addressed an environment that did not exist.

### Naming your sources

Each F441M input (S1–S4) has a name field in the integration Options. Fill in
what is physically wired to it and **leave the rest blank**.

- Only named sources are offered in the Home Assistant source list.
- A zone routed to a blank input — typically by someone pressing a stale
  button on a wall panel — is labelled `Source N (not configured)` and logged
  once, so amplified silence or tuner hiss has a visible cause.
- Routing chosen at a wall panel is never overridden. The user pressed a
  button in the room, and silently switching it back would be its own
  surprise. Select a configured source to recover.

If no names are configured the legacy `Source 1`–`Source 4` list is used and
nothing is flagged, so existing installations are unaffected.

### Default source per environment

For every environment that has audio zones, the Options offer a **default
source**. It is set per environment, not per zone, because two amplifiers in
one room share a matrix output and cannot sit on different inputs.

- The default is applied when a zone is switched **on from Home Assistant**,
  so a room left on a stale input comes back on the right source.
- It is not applied to a zone that is already on, nor while another zone of
  the environment is streaming.
- It never overrides routing announced by a wall panel.
- *Leave routing as it is* (the default) keeps the existing routing.

Environment 0 is not offered: it has no routing address.

### Routing when streaming

Once sources are named, or a default source is set for an environment,
`play_media` routes the zone's environment to the input its decoder is wired
to, and turning a zone on routes it to its environment default. A zone that is
already on is not re-routed when it is turned on again, and a default is not
applied while another zone in the environment is streaming.

Without either setting the integration does not route while streaming and
relies on the "Hardware Routing First" model below, as earlier releases did.

### The "Hardware Routing First" Model

For installations that have not named their sources, `play_media` leaves the
matrix routing to the wall panels.

**Recommended Practice**:

1. **Physical Cabling**: Connect the analog output of your network streamer (e.g. Raspberry Pi running Squeezelite, WiiM Pro, Cambridge Audio) into physical Source 1 on the F441M matrix.
2. **Matrix Configuration**: Configure your room amplifiers (or physical wall panels) to stay routed to Source 1.
3. **Automated Power Sequence**: When a stream starts, the integration proxy:
   - Claims an idle decoder from the shared **Decoder Pool**.
   - Wakes the decoder if it is in standby.
   - Powers the BTicino amplifier on with an OFF → ON sequence (`*16*13*<WHERE>##`, then `*16*3*<WHERE>##`) if it is not already on.
   - Forwards the stream URL to the streaming decoder.
   - Mirrors track metadata (title, artist, album art) and state back onto the Home Assistant room entity.
4. **Shutdown & Release**: When the zone is turned off, the amplifier powers off (`*16*13*<WHERE>##`), playback on the decoder is stopped and the decoder is released back to the idle pool.

---

## 🎛️ Gain Staging & Bus Noise Elimination

Analog SCS audio matrices can suffer from faint ground-loop hum or bus hiss if the input signal level is too low.

The MyHOME integration features **hardware gain staging**:
$$\text{Decoder Volume} = \text{Zone Volume} + \text{Pre-Gain Offset}$$

- Setting `pre_gain` (e.g., `+10%` to `+20%`) drives the network streamer at maximum undistorted line level.
- The room amplifier then operates at lower amplification, pushing the analog noise floor below audibility.

---

## ⚙️ Configuration via Home Assistant UI

Sources, defaults and the Dynamic Proxy are all configured in the integration's **Options Flow**:

1. Go to **Settings** -> **Devices & Services** -> **MyHOME**.
2. Click **Configure**.
3. **Source names** (*Source 1*–*Source 4*): name what is wired to each F441M input and leave unused inputs blank. See [Naming your sources](#naming-your-sources).
4. **Default source for environment N** (one field per environment with audio zones): pick a source, or keep *Leave routing as it is*. See [Default source per environment](#default-source-per-environment).
5. **Decoder mapping**, one row per streaming decoder (up to 4):
   - **Media player entity**: your backend player (e.g. `media_player.squeezelite_salon`).
   - **Source input**: the F441M input it is wired to, chosen from a list that shows your source names (e.g. `S1 — Streamer`).
   - **Pre-gain offset**: percentage added to the decoder volume (0–50 %, e.g. `15`). See [Gain Staging](#gain-staging-bus-noise-elimination).
6. Click **Submit**.

Naming a source or setting a default is what switches on automatic routing for
streaming (see [Routing when streaming](#routing-when-streaming)); leaving both
empty keeps the wall-panel routing in charge.

> [!WARNING]
> **Avoid Recursive Loops**: Do NOT select a Music Assistant virtual player as the backend decoder entity. The backend decoder must be the actual hardware device (e.g. `media_player.squeezelite_salon`, `media_player.wiim_dining`), while Music Assistant targets the MyHOME zone entity.

---

## 👥 Multi-Room Audio Grouping (Music Assistant & Home Assistant)

The MyHOME integration implements native Home Assistant player grouping (`MediaPlayerEntityFeature.GROUPING`). This enables synchronized multi-room playback across BTicino audio zones without playing separate concurrent audio streams.

### How Grouping Works with the Analog Matrix

When using **Music Assistant (MA)** or Home Assistant's `media_player.join` service:
1. **Single Backend Stream**: Only the group **leader** claims a network decoder from the decoder pool and requests the audio stream (e.g. from Spotify, Tidal, or local FLAC).
2. **Matrix Route Sharing**: Each joined **member** zone routes its physical environment output to the leader's matrix source input (`*16*3*1ES##`) and powers on its room amplifier (`*16*3*<WHERE>##`). Routing follows the same opt-in as the rest of the integration: until you name a source or set an environment default, the wall-panel routing is left alone and only the member amplifiers are switched on. A member only shows the leader's track while it is actually on the leader's input.
3. **Cross-Environment & Same-Environment Synchrony**:
   - Zones in different environments (e.g. Environment 2 living room and Environment 3 kitchen) are bridged to the same analog source input, guaranteeing **zero latency** and perfectly aligned analog audio across rooms.
   - Zones within the same environment share the matrix output physically.
4. **Environment Isolation Protection**:
   - The F441 / F441M matrix routes an entire environment to one input. If an attempt is made to join a zone whose environment is already actively streaming from another decoder, the operation is rejected with an `environment_busy` error to prevent cutting off an active listener in that environment. The check runs for every requested member before anything is switched, so a refused join changes nothing. A group that is not playing yet does not block its environments.
5. **Dynamic Disbanding & Member Lifecycle**:
   - **Leader turned off or unjoined**: When the group leader turns off or calls `unjoin`, the entire group is disbanded, and all member amplifiers are powered off.
   - **Member leaves the group**: A member calling `unjoin` or turning off powers off its own amplifier and leaves the group; the leader and any other members continue streaming uninterrupted.
   - **Physical Wall Switch Interaction**: Pressing OFF on a physical wall control sends a bus OFF frame which immediately cleans up group membership in Home Assistant. The OFF → ON wake sequence the integration sends itself is recognised and ignored for 3 seconds; a wall-switch OFF inside that window is corrected by the zone's next status report.
   - **Reloading the integration** (or renaming an entity) clears the groups in Home Assistant but sends nothing to the bus: rooms keep playing.

---

## 📡 Backend Stream Compatibility & DLNA DMR (Cambridge Audio, WiiM, Squeezelite)

When using the Dynamic Proxy, Home Assistant sends direct HTTP streaming URLs to the configured backend decoder.

### The Cambridge Audio Dilemma
The native Home Assistant `cambridge_audio` integration (for CXN, CXN V2, Edge NQ, Evo 75/150, MXN10, AXN10) uses the Cambridge StreamMagic API. By design, it only accepts built-in presets, Airable, and internet radio — it **does not accept raw HTTP stream URLs** from Music Assistant or Home Assistant, raising an `unsupported_media_type` exception.

### Solution: Configure via DLNA Digital Media Renderer (DMR)
To stream seamlessly to Cambridge Audio network players:
1. Enable UPnP / DLNA in the Cambridge StreamMagic app settings.
2. In Home Assistant, install the **DLNA Digital Media Renderer** integration. It will automatically discover your Cambridge Audio streamer (e.g. `media_player.cxn_v2_dlna`).
3. In **Settings** -> **Devices & Services** -> **MyHOME** -> **Configure**, map the DLNA DMR entity as your decoder instead of the native `cambridge_audio` entity.

> [!NOTE]
> **Automatic Diagnostic & Repair**:
> If you select a `cambridge_audio` entity in MyHOME Options, the integration issues a **Home Assistant Repair Issue** as soon as the options are saved, explaining that DLNA DMR is required and linking to the documentation.
> The `cambridge_audio` decoder stays in the pool: it still provides passive track mirroring and plays presets, Airable and internet radio. A stream URL (Music Assistant, Spotify) skips it and goes to another idle decoder.

---

## 🎧 Passive Source & Metadata Tracking (Streamer-First Workflow)

You do not need to initiate playback through Home Assistant or Music Assistant to see track metadata:
- If you start Spotify Connect, TIDAL Connect, AirPlay, or internet radio directly in the Cambridge StreamMagic or WiiM mobile app, or via a physical matrix source (CD player, tuner):
- Any BTicino zone turned ON and routed to that physical source automatically mirrors track title, artist name, album art, and transport state (`PLAYING`, `PAUSED`).
- Transport controls (`media_play`, `media_pause`, `media_next_track`, `media_previous_track`) operated from the Home Assistant zone card are automatically forwarded to the active source decoder.

---

## 📻 Standalone Fallback Mode (No Decoders)

If you do not configure any streaming decoders in the Options Flow, the room amplifier entities operate in **Native WHO = 16 Mode**:

- **On / Off**: Toggles the physical amplifier power.
- **Volume**: Steps the volume up and down, or sets it directly on the amplifier's 0–31 scale.
- **Source Selection**: Switches the zone's environment between the physical sources (named ones, or `Source 1`–`Source 4` when none are named).

Play, pause, stop and next / previous track are only offered once a decoder is
configured; they are forwarded to that decoder, not sent on the bus.

---

## 📜 OpenWebNet WHO = 16 Reference Frames

`<WHERE>` is an amplifier (`01`–`99`), an environment (`#0`–`#9`) or `0` for
all amplifiers. The integration addresses individual amplifiers.

| Action | OpenWebNet Frame | Description |
| :--- | :--- | :--- |
| **Amplifier ON** | `*16*3*<WHERE>##` | Stereo channel ON. `*16*0*<WHERE>##` is the base-band form. |
| **Amplifier OFF** | `*16*13*<WHERE>##` | Stereo channel OFF. `*16*10*<WHERE>##` is the base-band form. |
| **Volume UP** | `*16*1001*<WHERE>##` | One step up; `1001`–`1015` step +1 to +15. |
| **Volume DOWN** | `*16*1101*<WHERE>##` | One step down; `1101`–`1115` step −1 to −15. |
| **Set Exact Volume** | `*#16*<WHERE>*#1*<LEVEL>##` | Writes the volume, `<LEVEL>` 0–31. |
| **Volume Report** | `*#16*<WHERE>*1*<LEVEL>##` | Amplifier reporting its volume, 0–31. |
| **Activate Source `S`** | `*16*3*10S##` | Switches source device `S` on (`101`–`109`). |
| **Route Environment to Source** | `*16*3*1ES##` | Routes every amplifier of environment `E` to source `S`. Not in `WHO_16.pdf`; established from bus captures on two installations. |

> Released OWNd builds volume down as `*16*1000*<WHERE>##`, which the
> specification does not define; the library fix is pending.
