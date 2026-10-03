# MH200 + F441M Multiroom Sound Traces

Authentic on-wire bus frames and diagnostic configuration captured on a live physical MH200 plant with F441/F441M 4-input audio matrix:
- **Gateway**: BTicino MH200
- **Audio Matrix**: Legrand / BTicino F441M (WHO=16 / WHO=22)
- **Sources**:
  - Source 1: Tuner / Radio (`*16*3*101##`)
  - Source 2: Digital Streamer / Cambridge Audio CXN (`*16*3*102##`)
- **Environments & Sound Zones**:
  - Environment 1: zones 14, 17, 18
  - Environment 2: zones 21, 22, 23
  - Environment 3: zones 35, 36 (Eetkamer)

### Validated Protocol Behaviors
1. **Multi-Environment Routing**:
   - `*16*3*122##` -> routes Environment 2 to Source 2 (Streamer).
   - `*16*3*132##` -> routes Environment 3 to Source 2 (Streamer).
2. **Zone Power & Volume Status**:
   - `*16*13*36##` -> zone 36 unmuted / active.
   - `*#16*23*1*19##` -> zone 23 reporting volume level 19.
   - `*#16*35*1*26##` -> zone 35 reporting volume level 26.
3. **Multiroom Grouping & Leader Handover**:
   - Grouping across zones 35 & 36 under Environment 3.
   - Unjoining a group leader without tearing down playback on remaining member zones.
4. **Gain Staging (Option B)**:
   - Locking streamer source output to 100% (0 dBFS line level) to maximize signal-to-noise ratio into the F441 analog matrix, eliminating high-gain bus hissing.

### Live group lifecycle, 2026-09-29 (`live_2026-09-29_group_park_and_resume.json`)
Three rooms (badkamer leads Bureau and Eetkamer) on the Audio Decoder decoder: pause, the anti-hiss switch-off after 60 s that keeps the group, and the resume. The fixture holds the bus frames, the decoder states that were seen, and what Music Assistant did; `tests/test_audio_group_live_replay.py` replays it. Its `findings` list is why each assertion exists.

### Group wake by play, 2026-09-29 (`live_2026-09-29_group_wake_by_play.json`)
The WHO=16 frames the integration wrote when Music Assistant played into three parked rooms, from the diagnostics bus monitor (build 86a8678c). `test_play_writes_the_same_frames_as_the_live_wake` compares them with a replayed play.

### Rooms joined one by one, 2026-09-29 (`live_2026-09-29_group_joins_one_by_one.json`)
The frames written while Music Assistant added three rooms to a playing leader, before routing frames were shared between joins. Kept as the evidence for `test_joins_one_after_another_route_once`.

### Rooms turned on and joined one by one, 2026-09-29 (`live_2026-09-29_group_turn_on_and_join_one_by_one.json`)
The frames written with the join fix alone: each room is also turned on (default-source route) before it joins. Evidence for `test_rooms_turned_on_and_joined_one_by_one_route_once`.

### Members leaving and joining a playing group, 2026-09-29 (`live_2026-09-29_group_members_leave_and_join.json`)
Two rooms unchecked and one added in Music Assistant while the leader keeps playing: one OFF per room that leaves, OFF/ON plus source and route for the room that joins.

### Restart, park, join and play, 2026-09-29 (`live_2026-09-29_group_restart_park_join_and_play.json`)
Build with the routing memory: two rooms joined and the parked group played; shows the one remaining repeat (a leaving room cleared the memory) that the follow-up commit removes.
