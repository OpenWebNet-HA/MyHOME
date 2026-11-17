# MH202 + F441M Intercom Audio Ducking, Visitor Doorbell Call & Wall Control Traces

Authentic passive bus traces captured on a physical plant with an MH202 gateway, F441M audio matrix, and video intercom entrance panel ([Issue #669](https://github.com/OpenWebNet-HA/MyHOME/issues/669)):
- **Gateway**: BTicino MH202 (Firmware 1.0)
- **Audio Matrix**: Legrand / BTicino F441M (dual-dialect WHO=16 / WHO=22)
- **Amplifiers in Home**: `31`, `41`, `51`, `61`, `71`, `81`
- **Video Intercom**: External entrance panel (`WHERE=4000`, caller ID `20`) and indoor video station (`WHERE=11` / unit `1`)

---

## Trace Fixtures

### 1. `myhome_trace_MH202_sound_intercom_ducking.json` (30 frames)
- **Scenario**: Indoor unit activates entrance panel camera preview while all 6 amplifiers are `OFF`, then closes camera (`*6*9**##`).
- **Validated Behavior**:
  - Automatic matrix audio ducking (`*#16*<EA>*1*1##` + `*#22*3#<E>#<A>*1*1##`) and post-call volume restoration (`*#16*<EA>*1*<vol>##`) update `_attr_volume_level` while keeping all powered-off amplifiers strictly in `MediaPlayerState.OFF`.

### 2. `myhome_trace_MH202_all_2026-10-18T10-27-48.json` (61 frames)
- **Scenario**: Visitor presses the outdoor doorbell button -> indoor unit rings -> call is answered -> door lock is released during conversation -> call ends (`*6*9**##`).
- **On-Wire Sequence**:
  1. **Audio Ducking (`10:27:00.492Z`–`10:27:01.368Z`)**: F441M matrix ducks all 6 amplifiers (`31`, `61`, `51`, `41`, `71`, `81`) to volume `1` via paired WHO 16 (`*#16*<EA>*1*1##`) and WHO 22 (`*#22*3#<E>#<A>*1*1##`) Dimension 1 frames.
  2. **Visitor Doorbell Call (`10:27:01.281Z`–`10:27:01.285Z`)**:
     - `*8*1#1#4#20*11##`: 4-component WHO 8 call initiation (`WHAT=1#1#4#20`, `Kind=1` Entrance Panel PE1, `MMType=4` Audio+Video, `Caller=20`, target indoor station `WHERE=11`).
     - `*8*9#1#4*20##` (+4 ms): WHO 8 Caller Address Notification (`WHAT=9#1#4`, `WHERE=20`).
  3. **Door Lock Release in Conversation (`10:27:25.455Z`–`10:27:26.201Z`)**:
     - `*6*22*1##` (`WHAT=22`, L4686SDK *"Open lock with external unit in conversation"* at unit `1`) followed +26 ms later by `*6*10*4000##` (`WHAT=10`, *"Open lock"* at entrance panel `4000`).
     - ~717 ms later, lock relay pulse release frames `*6*24*1##` (`WHAT=24` at `1`) and `*6*23*4000##` (`WHAT=23` at `4000`).
  4. **Call End, Dry Contact & Volume Restoration (`10:27:30.852Z`–`10:27:33.268Z`)**:
     - `*6*9**##`: Camera OFF (`WHO=6, WHAT=9, WHERE=*`).
     - `*25*32#1*31##`: Dry-contact / IR sensor OFF event (`WHO=25, WHAT=32#1` where `32` = contact OFF / IR not detected and `1` = unsolicited event, `WHERE=31`, `is_on=False`).
     - Matrix restores pre-call amplifier volumes (`31` -> `0`, `41` -> `6`, `61` -> `7`, `51` -> `6`; `71` and `81` remain at `1`).

### 3. `myhome_trace_MH202_all_2026-10-18T10-34-13.json` (146 frames)
- **Scenario**: User operates the physical wall switch in Zone `41` (Area 4, Point 1): turns amplifier ON -> ramps volume UP and DOWN via physical wall rocker -> turns amplifier OFF.
- **On-Wire Sequence**:
  1. **Dual-Dialect Power-On & Matrix Routing (`10:33:32.011Z`–`10:33:35.201Z`)**:
     - WHO 22 commands (`*22*0#4#4*6##` All Sources `6` OFF in Stereo Area 4, `*22*1#4#4*2#3##` Source `2#3` ON in Stereo Area 4, `*22*21#4#4*5#2#3##` and `*22*2#4#4*5#2#3##` General Source `5#2#3` activation in Area 4, `*22*1#4#4*3#4#1##` Speaker `3#4#1` ON in Stereo Area 4) interleaved with WHO 16 matrix routing `*16*3*143##` (Environment `4` -> Source `3`), amplifier ON `*16*3*41##`, and source ON `*16*3*103##`.
     - WHO 22 Dimension 12 status frames (`*#22*3#4#1*12*1*4##`, `*#22*2#3*12*1*4##`) and full DSP status dump on `3#4#1` (Dim 1 Volume=`6`, Dim 2 High=`30`, Dim 4 Low=`30`, Dim 17 Balance=`10`, Dim 20 Loudness=`1`, Dim 18 3D=`0`, Dim 19 Preset=`3`).
  2. **Wall Rocker Volume Up & Down (`10:33:34.822Z`–`10:33:50.997Z`)**:
     - Wall control sends WHO 22 step commands `*22*3#1*3#4#1##` (Volume UP +1) and `*22*4#1*3#4#1##` (Volume DOWN -1).
     - Gateway broadcasts paired `*#16*41*1*<vol>##` and `*#22*3#4#1*1*<vol>##` volume reports (`6 -> 15 -> 5 -> 12 -> 7`).
  3. **Wall Power-Off (`10:33:51.905Z`–`10:33:52.610Z`)**:
     - `*22*0#4#4*3#4#1##` (WHO 22 Speaker `3#4#1` OFF), `*16*13*41##` (WHO 16 Amplifier `41` OFF), and `*#22*3#4#1*12*0*10##` (WHO 22 Dim 12 standby state `0`).
