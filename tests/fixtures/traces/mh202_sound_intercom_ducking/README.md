# MH202 + F441M Intercom Video Stream Audio Ducking & Volume Restoration Trace

Authentic passive bus trace captured on a physical plant with an MH202 gateway, F441M audio matrix, and video intercom entrance panel (Issue #669):
- **Gateway**: BTicino MH202 (Firmware 1.0)
- **Audio Matrix**: Legrand / BTicino F441M (WHO=16 / WHO=22)
- **Amplifiers in Home**: 31, 41, 51, 61, 71, 81 (all powered OFF when trace starts)
- **Video Intercom Event**: Video stream starts from entrance panel, ducking all audio; video stream stops (`*6*9**##`), restoring configured volumes.

### Validated Protocol Behaviors

1. **Hardware Audio Ducking**:
   - Starting a video intercom stream triggers automatic audio ducking on the SCS bus.
   - The matrix broadcasts Dimension 1 volume reduction frames across all amplifiers:
     - `*#16*41*1*1##`, `*#16*31*1*1##`, `*#16*61*1*1##`, `*#16*51*1*1##`, `*#16*71*1*1##`, `*#16*81*1*1##`
     - Interleaved with WHO 22 mirror status reports: `*#22*3#4#1*1*1##`, `*#22*3#3#1*1*1##`, `*#22*3#6#1*1*1##`, `*#22*3#5#1*1*1##`, `*#22*3#7#1*1*1##`, `*#22*3#8#1*1*1##`
   - Amplifiers that are currently `MediaPlayerState.OFF` must retain their `OFF` state and **never** wake or auto-join streaming sessions (#669).

2. **Camera Off & Volume Restoration**:
   - When the camera stream ends (`*6*9**##`), the matrix restores individual amplifier volume levels to their pre-intercom configurations (e.g. 6/31, 7/31, 1/31, 0/31).
   - Powered-off amplifiers update their stored volume level (`_attr_volume_level`) but remain strictly `MediaPlayerState.OFF`.
