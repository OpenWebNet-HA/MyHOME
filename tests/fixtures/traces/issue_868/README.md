# Authentic Bus Monitor Trace: MyHomeServer1 & BTicino F401 Actuator – Venetian Blind Slat Tilt (Issue #868)

Authentic on-wire bus monitor trace captured from a physical BTicino MyHomeServer1 gateway connected to a BTicino F401 advanced shutter actuator controlling a Venetian blind (contributed by Stefan Vitz in GitHub issue #868).

## Provenance

- **Source**: Authentic user trace exported from MyHOME Bus Monitor in Home Assistant
- **Origin Issue**: https://github.com/OpenWebNet-HA/MyHOME/issues/868
- **Attachment**: `myhome_trace_MyHomeServer1_all_2026-11-05T12-14-21.json`
- **Gateway**: BTicino MyHomeServer1 (Server MyHOME_Up, FW 3.87.13, MAC prefix `00:03:50`)
- **Actuator**: BTicino F401 advanced automation actuator on WHO 2 address `0115`
- **Environment**: Home Assistant 2026.10.4, MyHOME 2.0.0b17, OWNd 2.0.0b13

## Scenario Under Test

1. Blind starts fully raised at 100% height and slats open (tilt 100%).
2. User commands 75% height via Dimension 11 (`*#2*0115*#11#001*75##`).
3. Actuator responds with reflection (`*#2*0115*#11#001#1*75##`), moving DOWN Dimension 10 report (`*#2*0115*10*12*100*001*0##`), and direction frame (`*2*2*0115##`).
4. Slat blades physically close as the curtain descends towards 75%.
5. Actuator stops and reports Dimension 10 stopped (`status 10`) at level 75 (`*#2*0115*10*10*75*001*0##`), followed by stop frame (`*2*0*0115##`).
6. Slat tilt updates to 0% (closed) as a result of the downward travel.
7. Subsequent `open_cover_tilt` command drives the motor to physically open the slats.

## Captured Frames (6 Frames)

| Index | Timestamp | Direction | Raw Frame | Description |
|---|---|---|---|---|
| 0 | 1793880770.933 | TX | `*#2*0115*#11#001*75##` | Command cover position to 75% |
| 1 | 1793880771.005 | RX | `*#2*0115*#11#001#1*75##` | Gateway reflection of position command |
| 2 | 1793880771.086 | RX | `*#2*0115*10*12*100*001*0##` | Dimension 10: status 12 (DOWN), initial level 100% |
| 3 | 1793880771.148 | RX | `*2*2*0115##` | Direction confirmation: DOWN |
| 4 | 1793880786.444 | RX | `*#2*0115*10*10*75*001*0##` | Dimension 10: status 10 (STOP), final level 75% |
| 5 | 1793880786.501 | RX | `*2*0*0115##` | Stop status confirmation: STOP |
