# Synthetic Trace: Venetian Blind Slat Tilt Inching & Dimension 11 Shutter Level under WHO 2

Synthetic trace fixture capturing Venetian blind slat tilt inching, direction echo timing, motor pulse auto-stop, curtain position decoupling, and Dimension 11 absolute level write under OpenWebNet WHO 2.

## Provenance: Synthetic from Actuator Firmware & Gateway Oracle Contracts

> [!NOTE]
> **Why this trace is synthetic**:
> Real-world bus captures of physical Venetian blind actuators (`H4661M2`, `LN4661M2`, `AM5861M2`, `067557`) coupled to motorized Venetian blinds (which feature mechanical slat tilt ladders rather than standard rolling shutters) are absent from the community capture archive.
>
> Live testing on real blinds requires physical installations with limit switch calibration and specialized actuator mode configuration (`SHUTTER_TYPE = 3`), which was flagged as an open blocker in PR #508.
>
> This trace is **fully synthetic**, deterministically synthesized from:
> 1. **Actuator firmware configuration database** in OpenWebNet Encyclopedia [`OWN-DEV-0045`](https://github.com/OpenWebNet-HA/OpenWebNet-Encyclopedia/blob/main/devices/definitions/own-dev-0045-shutter-actuator-bus.md) (Object `218` on firmware `192` for `H4661M2` item `1586`, `LN4661M2`, `AM5861M2`):
>    - Mode: `SHUTTER_TYPE = 3` (*Standard con lamelle* / Standard with slats).
>    - Calibration timing: `SLATS_ROTATION_TIME_DOWN_H / L` (2000 ms calibration: high byte `0x07`, low byte `0xD0`).
>    - Inching step division: `SLATS_ROTATION_STEP_NUMBER = 4` (discrete 500 ms / 25% inching pulses).
>    - Local button blade control: `LOCAL_BUTTON = 2` (*Comando lamelle e bistabile*: short press <1.5s = slat inching; long press >1.5s = continuous curtain travel).
>    - Relay contact threshold: `min_motor_pulse` (default `0.4s`) corresponding to physical motor deadband (`UP_OR_DOWN_PULSE_DURATION` minimum unit).
> 2. **Gateway daemon translation contracts** in Legrand/BTicino gateway firmware (`bt_autom` across all 10 emulated automation gateways in `own-firmware-oracle`):
>    - All 10 automation gateways (`F453AV`, `F454`, `F459`, `F460`, `F461`, `H4684`, `MH200`, `MH200N`, `MH202`, `MyHomeServer1`) translate basic motor commands `*2*1*WHERE##` (Up), `*2*2*WHERE##` (Down), `*2*0*WHERE##` (Stop), and `*#2*WHERE##` (status request) — proving timed Venetian slat pulses work universally across all 10 gateways.
>    - Pre-direction STOP synthesis: on 9 of 10 automation gateways (`F453AV`, `F454`, `F459`, `F460`, `F461`, `H4684`, `MH200`, `MH200N`, `MyHomeServer1`), `bt_autom` translates `*2*1*31##` and `*2*2*31##` into two consecutive SCS bus frames (`$033100120A` STOP followed by `$0331001208` UP or `$0331001209` DOWN) and emits `*2*1000#0*31##` (`is_translation == True`) before `*2*1000#1*31##` / `*2*1000#2*31##`. `MyHOMECover` ignores `is_translation == True` frames and suppresses premature `*2*0*31##` stop echoes within `_PULSE_ECHO_IGNORE_WINDOW` (0.3s) during `_is_tilting`.
>    - Dimension 11 absolute level write (`*#2*31*#11#001*40##`) and Dimension 10 status query (`*#2*31*10##`) are accepted and forwarded (`verdict == "out"`) on the 6 advanced WHO 2 gateways (`F454`, `F459`, `F460`, `F461`, `MH202`, `MyHomeServer1`), where `bt_autom` normalizes `*#2*31*#11#001*40##` into `emitted_own == ["*#2*31*#11#001#1*40##"]` (`bus_frames == ["$06D131014209012800"]`), while legacy `bt_autom` builds (`MH200`, `MH200N`, `H4684`, `F453AV`) reject them with `nack`/`silent`.
>    - Dimension 11 status query rejection: `*#2*WHERE*11##` is strictly **NACKed** (`*#*0##`), proving Dimension 11 is write-only.
>    - Frame `*#2*31*#11#001#1*40##` is verified both in `own-firmware-oracle` (`emitted_own` for `*#2*31*#11#001*40##`) and in the authentic F454 sweep trace (`myhome_sweep_F454_all_2026-09-26T16-59-13.json` in #491).
> 3. **Client-side physical inching algorithm (PR #508)**:
>    - Pulse duration formula:
>      $$\text{duration} = \frac{|\text{target\_tilt} - \text{current\_tilt}|}{100} \times \text{slat\_time} = \frac{|50 - 0|}{100} \times 2.0\text{ s} = 1.0\text{ s}$$
>    - Motor stopwatch anchored to the actuator direction echo `*2*1*31##`.
>    - Mechanical coupling: Slats rotate first; motor time under `slat_time` leaves linear curtain height unchanged.

## Plant Profile

| Property | Value |
|---|---|
| Gateway Model | F454 (firmware 2.0.51, MAC prefix `00:03:50`, WHO 13 code `200`, WHO 1013 code `13`) |
| Subsystem | WHO 2 (Automation / Covers) |
| Target Device | Living Room Venetian Blind (`WHERE = 31`, `slat_tilt: true`, `slat_time: 2.0s`) |

## Captured Sequence (10 Frames)

1. **Initial Baseline State**:
   - `*#2*31*10*10*0*001*0##`: Shutter stopped at level 0 (fully closed), slats closed (tilt 0%).
2. **Open Tilt Inching Pulse (0% &rarr; 50%)**:
   - `*2*1*31##`: Actuator direction echo: motor starts moving UP to rotate slats towards open ($t = 1.0\text{s}$). Slat stopwatch starts.
   - `*2*0*31##`: Actuator stop echo: motor stops after 1.0s ($t = 2.0\text{s}$). Slat tilt settles to 50% ($1.0\text{s} / 2.0\text{s} = 50\%$). Linear curtain position remains at 0% (closed).
3. **Close Tilt Inching Pulse (50% &rarr; 0%)**:
   - `*2*2*31##`: Actuator direction echo: motor starts moving DOWN to rotate slats towards closed ($t = 10.0\text{s}$). Slat stopwatch starts.
   - `*2*0*31##`: Actuator stop echo: motor stops after 1.0s ($t = 11.0\text{s}$). Slat tilt settles to 0%. Linear curtain position remains at 0%.
4. **Dimension 11 Level Command Write (Clarification)**:
   - `*#2*31*#11#001#1*40##`: Relayed Dimension 11 command: priority `001`, selector `1`, target level 40%. Verified as absolute position command, leaving pending target without immediate position snap.
5. **Actuator Movement & Mid-run Telemetry**:
   - `*2*1*31##`: Actuator movement starts ($t = 20.1\text{s}$). Curtain starts lifting.
   - `*#2*31*10*11*20*001*0##`: Mid-run Dimension 10 report: actuator moving UP, reached 20% level.
6. **Target Level Reached & Slat Follower Alignment**:
   - `*2*0*31##`: Actuator stop echo: target position reached ($t = 30.0\text{s}$).
   - `*#2*31*10*10*40*001*0##`: Final Dimension 10 state confirmation: stopped at 40% level. Slat tilt follows linear opening run to 100% (fully open).

## Replay Verification

Replayed by `tests/test_who2_venetian_tilt_trace.py`, verifying that slat tilt inching, linear curtain decoupling, Dimension 11 level repositioning, and slat run-following execute deterministically with 0 device health faults.
