# Live Capture: BTicino F461 (Firmware 2.0.11 / WHO 1013 `134.15.5.0`), 2026-10-18

Authentic multi-session OpenWebNet capture from the expanded physical BTicino F461 test plant (`F411/4` lights with Group `#1` & General `0`, `F411/2` 5-second timed shutter at `WHERE=05`, `KW4691` standalone thermostat + `F430/2` + `F430/4` at `Zone 2`) and live bus verification of the declarative `expose` engine ([PR #750](https://github.com/OpenWebNet-HA/MyHOME/pull/750) / `OWNd#129`).

---

## Plant Topology

| Subsystem | Hardware & Addressing |
|---|---|
| **Gateway (`WHO=13` / `WHO=1013`)** | BTicino **F461**, firmware `2.0.11` (`*#13**16*2*0*11##`), kernel `5.10.35`, `WHO=13` device type `200` (`*#13**15*200##`), spontaneous `WHO=1013` object model `*#1013**1*134*15*5*0##`, HMAC-SHA256 authentication on both command and event sessions |
| **Lighting (`WHO=1`)** | **BT-F411/4** 4-channel relay actuator at Area 0 Point 6 (`WHERE="06"`, *"Light 1"*) and Area 0 Point 7 (`WHERE="07"`, *"Light 2"*), linked in **Group `#1`** (`*1*1*#1##` / `*1*0*#1##`) and responding to **General `0`** (`*1*1*0##` / `*1*0*0##`). Previous `01` / `04` addresses are now unpopulated (`NACK`). |
| **Automation / Shutter (`WHO=2`)** | **BT-F411/2** configured as an advanced shutter actuator at Area 0 Point 5 (`WHERE="05"`, *"Shutter"*) with a **5-second hardware travel time** |
| **Thermoregulation (`WHO=4`)** | **Living Now KW4691** standalone thermostat at `Zone 2` (no central unit), driving a **BT-F430/2** heating valve (`actuator 1`) and a **BT-F430/4** cooling fan-coil (`actuator 2`) |

---

## File Format: `myhome_trace_F461_live_2026-10-18.json`

Standard `MyHOME` derived trace archive containing:
- `capture` & `gateway`: metadata with RFC 5737 documentation IP `192.0.2.92` and synthetic MAC `00:03:50:00:04:97`.
- `frames`: all event-session `rx` frames (`direction: "rx"`, `raw: "..."`) replayed by the corpus health checks (`test_device_health_corpus.py`) and parser resilience suite (`test_trace_oracle_validation.py`).
- `rows`: the complete chronological session log (`tx`, `rx` on `cmd`/`evt`, and `result` with `ack`, `reply_time_s`, `replies`, `events_after`, and `note`) across four structured runs:

| `run` | Scenario |
|---|---|
| `run1_lights_group_and_general` | `WHO=13`/`1013` identification, `F411/4` point switching (`06`, `07`), Group `#1` queries/commands (`*#1*#1##`, `*1*1*#1##`, `*1*0*#1##`), General `0` broadcasts (`*1*1*0##`, `*1*0*0##`) with post-broadcast resync polls (`*#1*06##`, `*#1*07##`), and unpopulated `01`/`04` checks |
| `run2_shutter_05_travel_and_reversal` | `F411/2` shutter (`WHERE=05`): initial status (`*#2*05##`) and Dim 10 (`*#2*05*10##`), 2.0 s partial `UP` + `STOP` (`*2*1*05##` -> `*2*0*05##`), and mid-flight direction reversal (`*2*1*05##` `UP` -> 1.5 s -> `*2*2*05##` `DOWN`) through the full 5.0 s hardware auto-stop (`*2*0*05##`) |
| `run3_climate_zone2` | `KW4691` standalone `Zone 2` status poll (`*#4*2##` -> 5 frames), temperature (`*#4*2*0##`), fan-coil/valve status (`*#4*2*19##`), and actuator status (`*#4*2*20##` -> `2#1` and `2#2`) |
| `run4_expose_virtual_bus_verification` | End-to-end physical F461 verification of `ExposeManager` (`WHO=1`, `WHO=2`, `WHO=4`, `WHO=13`, `WHO=18`) across populated (`06`, `2`) and unpopulated virtual (`08`, `25`, `50`, `000`, `102`, `51`) addresses |

---

## Empirical Hardware Findings

### 1. Physical `F411/4` (`06`, `07`), Group `#1` Aggregate Dimension 15, and General `0`
- **Group `#1` aggregate status (`*#1*#1*15*0##` / `*#1*#1*15*1##`)**: Whenever any member of Group `#1` (`06` or `07`) transitions Group `#1` from all-OFF to any-ON, the plant broadcasts `*#1*#1*15*1##` on the event session; when the last member turns OFF, it broadcasts `*#1*#1*15*0##`.
- **Group `#1` vs General `0` individual status reporting**:
  - Group `#1` poll `*#1*#1##` is ACKed in ~1.2 s and returns both individual channel states (`*1*0*06##`, `*1*0*07##`).
  - Group `#1` commands `*1*1*#1##` and `*1*0*#1##` emit `*1*1*#1##`, individual status frames `*1*1*06##` and `*1*1*07##`, and `*#1*#1*15*1##`.
  - General `0` commands `*1*1*0##` and `*1*0*0##` switch both physical relays (`06` and `07`) and emit `*1*1*0##` + `*#1*#1*15*1##` **without** individual point-to-point status frames; subsequent point polls (`*#1*06##`, `*#1*07##`) confirm `06` and `07` switched ON/OFF.

### 2. Physical `F411/2` 5-Second Timed Shutter (`WHERE=05`)
- **Command translation + actuator status sequence**:
  - Raising from idle (`*2*1*05##`) emits four event-session frames in ~170 ms: gateway pre-move `STOP` translation (`*2*1000#0*05##`), actuator `STOP` confirmation (`*2*0*05##`), gateway `UP` translation (`*2*1000#1*05##`), and actuator `UP` status (`*2*1*05##`).
  - Stopping (`*2*0*05##`) emits gateway `STOP` translation (`*2*1000#0*05##`) followed by actuator `STOP` status (`*2*0*05##`).
  - Reversing direction mid-flight (`*2*1*05##` -> 1.5 s -> `*2*2*05##`) emits `*2*1000#0*05##`, `*2*0*05##`, `*2*1000#2*05##`, `*2*2*05##`, and **5.0 seconds later** the physical `F411/2` relay automatically emits end-of-travel `*2*0*05##` on the event monitor session.

### 3. `Expose` Virtual Device & Protocol Behaviour on Physical `F461` ([PR #750](https://github.com/OpenWebNet-HA/MyHOME/pull/750))
1. **`WHO=2` Virtual Cover (`WHERE=25`) — On-Wire Pre-Move `STOP` + `NACK` Verification**:
   - Sending `s:*2*1*25##`, `s:*2*2*25##`, or `s:*2*0*25##` for an unpopulated virtual cover address `25` **immediately broadcasts the translated command sequence `['*2*1000#0*25##', '*2*1000#1*25##']`, `['*2*1000#0*25##', '*2*1000#2*25##']`, and `['*2*1000#0*25##']` onto the event monitor session** before `bt_fls` returns `*#*0##` (`NACK`) on the command session after ~0.55 s (because no physical shutter relay answers on the 2-wire bus).
   - This directly validates PR #750's two key `WHO=2` mechanisms on physical `F461` hardware:
     - `CommandWorkerPool` sending exposed frames with `is_status_request=True` (`no retry`) and resolving the `written` future on `NACK` when the command session is open.
     - `ExposeManager`'s `_pre_move_stop_guard[(2, "25")]` and `_tx_echo_guard["*2*1000#1*25##"]` absorbing the gateway's 2-frame translation echo on `evt` without triggering a self-oscillating `stop_cover` / `open_cover` loop.
2. **`WHO=1` Virtual Light/Switch/Dimmer (`WHERE=08` vs Populated `06`)**:
   - When an address is populated on the bus (`06`), `s:*1*1*06##` and `s:*1*0*06##` ACK in ~180 ms and echo `*1*1*06##` / `*1*0*06##` on `evt`.
   - When no physical actuator exists at `08`, `bt_fls` transmits `*1*1*08##` / `*1*0*08##` / `*1*5*08##` on the 2-wire SCS bus, waits 2.06 s for a physical actuator confirmation, and returns `NACK` on the TCP command session without echoing on `evt`. Conversely, Dimension `#1` brightness write `*#1*08*#1*150*0##` is **ACKed in 177 ms**.
3. **`WHO=4` Virtual Temperature Probe (`*#4*W*0*T##` vs `*#4*W*#0*T##`)**:
   - On physical `F461` (`bt_termo`), a dimension frame without `#` before the dimension number (`*#4*W*0*TTTT##`) is parsed by `bt_termo` as a **Dimension 0 READ request (`*#4*W*0##`)**: sending `*#4*2*0*0999##` to populated `Zone 2` returns the thermostat's actual measured temperature `*#4*2*0*0250##` (`25.0 °C`), and sending `*#4*50*0*0215##` / `*#4*000*0*0215##` / `*#4*102*0*0215##` to unpopulated addresses times out after 2.1 s with `NACK`.
   - Conversely, `OWNHeatingCommand.write_temperature("50", 21.5)` (`*#4*50*#0*0215##`, with `#0` on a 2-digit address) is **ACKed in 134 ms** by the physical `F461`.
4. **`WHO=13` Clock / Date Synchronization**:
   - `*#13**0##`, `*#13**1##`, and `*#13**22##` ACK in ~180 ms and emit the clock/date on both `cmd` and `evt`.
   - `OWNGatewayCommand.set_time` (`*#13**#0*HH*MM*SS*999##`) ACKs in ~70 ms and broadcasts `*#13**#0*HH*MM*SS*999##` on `evt`, whereas `*#13**0*HH*MM*SS*999##` (without `#0`) is parsed by `F461` as a Dimension 0 read request that returns the gateway's current time.
5. **`WHO=18` Virtual Energy Meter (`WHERE=51`)**:
   - `bt_energy` on the physical `F461` ACKs `*#18*51*113*450##` (Active Power), `*#18*51*51*12345##` (Total Energy), `*#18*51*54*320##` (Partial Day Energy), `*#18*51*#1200#1*1##` (Enable Streaming), `*#18*51*#1200#1*0##` (Disable Streaming), and `*#18*51*113##` (Active Power Query).
