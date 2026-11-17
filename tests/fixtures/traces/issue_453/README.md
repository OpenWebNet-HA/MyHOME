# #453 Multiple Gateway Traces (F454 Main + MH202 Warm Standby on Shared Bus)

Verbatim bus traces contributed on [#453](https://github.com/OpenWebNet-HA/MyHOME/issues/453) by [@anotherjulien](https://github.com/anotherjulien) ([comment 5820748067](https://github.com/OpenWebNet-HA/MyHOME/issues/453#issuecomment-5820748067)). They are facts: never edit a frame.

## Plant Topology & Hardware Setup
- **Main Gateway**: BTicino F454 (firmware 2.0.51, TCP queue pacing 0.05 s)
- **Secondary / Warm Standby Gateway**: BTicino MH202 (firmware 1.0.21, TCP queue pacing 0.1 s)
- **Bus Topology**: Both gateways connected to the same physical SCS automation bus and the same Ethernet network switch.
- **Scenario Functionality**: Standby gateway is used strictly as a warm standby for high availability and shared bus observation.

## Capture Details

| File | Gateway | Total RX | Total TX | Captured Frames | Comment |
|---|---|---|---|---|---|
| `myhome_trace_F454_all_2026-09-24T19-12-29.json` | F454 (Main) | 16 | 2 | 18 | [5820748067](https://github.com/OpenWebNet-HA/MyHOME/issues/453#issuecomment-5820748067) |
| `myhome_trace_MH202_all_2026-09-24T19-12-34.json` | MH202 (Secondary) | 23 | 2 | 24 | [5820748067](https://github.com/OpenWebNet-HA/MyHOME/issues/453#issuecomment-5820748067) |
| `myhome_trace_MyHomeServer1_all_2026-09-25T19-40-15.json` | MyHomeServer1 (Primary) | 196 | 4 | 200 | [5838720872](https://github.com/OpenWebNet-HA/MyHOME/issues/453#issuecomment-5838720872) |
| `myhome_trace_H4890_all_2026-09-25T19-40-19.json` | H4890 (Secondary) | 200 | 0 | 200 | [5838720872](https://github.com/OpenWebNet-HA/MyHOME/issues/453#issuecomment-5838720872) |

## Sequence of Actions Recorded

### F454 + MH202 Pairing (@anotherjulien)
1. **Light `16`**: Switched ON with physical wall switch (`*1*1000#1*16##`, `*1*1*16##`), then switched OFF (`*1*1000#0*16##`, `*1*0*16##`).
2. **Light `14` (Dimmable)**: Switched ON with wall switch (`*1*1000#1*14##`, `*1*5*14##`, `*#1*14*1*130*5##`), then switched OFF (`*1*1000#0*14##`, `*1*0*14##`).
3. **CEN+ Pushbutton `1` on Control `1`**: Short button press (`*25*21#1*21##`).
4. **Light `33` via F454**: Switched ON from Home Assistant via F454 (F454 TX `*1*1*33##`, echo `*1*1000#1*33##`, `*1*1*33##` on both gateways), then switched OFF.
5. **Light `33` via MH202**: Switched ON from Home Assistant via MH202 (MH202 TX `*1*1*33##`, echo `*1*1000#1*33##`, `*1*1*33##` on both gateways), then switched OFF.

### MyHomeServer1 + H4890 Pairing (@nicolacavallo84)
1. **Automation (WHO 2)**: Opened and closed roller shutters (`*2*1*WHERE##`, `*2*2*WHERE##`, `*2*0*WHERE##`).
2. **Burglar Alarm (WHO 5)**: Armed and disarmed alarm system partition (`*5*1*0##`, `*5*9*0##`).
3. **Sound & Audio Diffusion (WHO 16 & WHO 22)**: Multi-room audio turned ON and OFF across 6 zones.
4. **Thermoregulation (WHO 4)**: Heating enabled in winter manual mode (20.0 °C), then disabled (summer mode / off).
5. **Lighting & Energy (WHO 1 & WHO 18)**: Dimmable light brightness adjusted and cumulative energy meter reports observed concurrently.

## Verification & Architecture Observations

1. **Shared Bus Echoes**: Every actuator state change (`*1*...##`) and CEN+ event (`*25*...##`) is received simultaneously on both gateways with millisecond-level timestamp parity, confirming shared physical medium.
2. **Passive Bus Concurrence (No False Positives)**: In the MHS1 + H4890 capture, 87 concurrent RX events occur across both gateways when the user interacts with wall switches and alarm panels without triggering false-positive shared bus detections, verifying that shared bus detection strictly requires TX-to-RX cross-gateway echoes.
3. **Gateway-Local Filtering**: The MH202 emits periodic internal datetime frames (`*#13**22*...##`) on its own OWN connection that do not appear on the F454. This validates the requirement that WHO=13 and WHO=1013 frames must NOT be bridged between gateways on a shared bus.
