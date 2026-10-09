# #667 MH200 Video Door Entry, Intercom & Impulse Gate/Garage Trace

Verbatim physical bus trace recorded on a real-world BTicino MyHOME installation with an **MH200** scenario gateway (Firmware 2.1.0).

> **Privacy & Anonymization Guarantee**:
> In accordance with project security standards, all physical bus addresses (`WHERE`), station numbers, and device identifiers have been deterministically mapped to fictitious mock addresses (`71` for garage relay, `72` for gate relay, `73` for outdoor entrance panel, `74` for indoor handset, and `81`–`88` for audio matrix zones). The mock addresses are 100% disjoint from the real plant's physical topology. No private addresses, credentials, or network identifiers are present.

## Hardware Profile

- **Gateway**: BTicino MH200
- **Firmware**: 2.1.0
- **WHO 13 Device Type Code**: `44`
- **Subsystems Verified**:
  - `WHO 1` (Lighting / Impulse Relay Actuators): Garage and gate pulse operations.
  - `WHO 8` (Advanced Video Door Entry & Intercom): Entrance panel call initiation and caller address notifications.
  - `WHO 6` (Door Entry): Video camera session termination (`*6*9##`).
  - `WHO 16` (Sound Diffusion / Audio Routing): Multi-zone audio matrix setup during intercom speech.
  - `WHO 13` (Gateway Management): Date/time broadcasts and keepalive heartbeats.

## Contributed Files

| File | Type | Description |
|---|---|---|
| `myhome_trace_MH200_intercom_bell_gate_2026-10-09.json` | Bus Card Export (34 frames) | Verbatim on-wire capture via `<myhome-bus-card>` across a complete physical door entry and access cycle. |

## Sequence of Actions Recorded

1. **Garage & Gate Impulse Open (from Garage Wall Switch)**:
   - User walks outside; presses the garage wall switch:
     - `*1*1*71##` (Pulsed garage door motor contact via WHO 1)
     - `*1*1*72##` (Pulsed entrance gate motor contact via WHO 1)
2. **Gate Closed via Outdoor Keypad Code**:
   - At the entrance gate keypad, user enters code to close:
     - `*1*1*72##` (Impulse close pulse)
3. **Gate Opened via Outdoor Keypad Code (Retry after rapid input)**:
   - First attempt entered too rapidly; second attempt triggers gate opening:
     - `*1*1*72##` (Impulse open pulse)
4. **Outdoor Entrance Panel Bell Pressed (Aanbellen)**:
   - User presses the call button on the external entrance panel (PE1):
     - `*#16*81*1*1##` through `*#16*88*1*1##`: Audio matrix activates routing on sound system zones.
     - `*8*1#1#4*74##`: **WHO 8 Video Door Entry Call initiated** (`Kind=1` PE1, `MMType=4` Audio/Video) directed to indoor handset `74`.
5. **Call Answered & Completed on Indoor Handset (Keuken Parlefoon)**:
   - Handset picked up / speak button pressed; brief speech; hung up:
     - `*8*9#1#4*73##`: Caller address notification from entrance panel (`Kind=1`, `MMType=4`, address `73`).
     - `*6*9##`: Camera session terminated (Camera OFF).
6. **Gate Closed from Secondary Handset (Woonkamer Parlefoon)**:
   - User operates gate button from the living room intercom:
     - `*1*1*72##` (Impulse close pulse).

## Protocol Insights

1. **WHO 8 Call Initiation**:
   Real-world BTicino video entrance panels broadcast `*8*1#Kind#MMType*WHERE##` (e.g. `*8*1#1#4*74##` for Entrance Panel, Audio/Video to Handset 74). Home Assistant's doorbell event dispatcher listens for both WHO 6 (`*6*6*...` / `*6*20*...`) and WHO 8 (`*8*1#...`) to guarantee 100% interoperability with both audio-only and video intercom systems.
2. **Session Teardown**:
   When the video call terminates, the MH200 emits the short-form WHO 6 camera-off frame `*6*9##`.
3. **Impulse Relays on WHO 1**:
   Automated gates and garage doors in BTicino SCS installations are universally wired to WHO 1 relay actuators configured for monostable pulse actuation.
