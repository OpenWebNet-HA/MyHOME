# #434 Classic CEN (WHO 15) Short and Long Press Bus Traces

Authentic on-wire bus traces contributed by **@wave68runner** on
[#434 (comment 5896344634)](https://github.com/OpenWebNet-HA/MyHOME/issues/434#issuecomment-5896344634).
Both gateways sit on the same physical SCS bus, capturing real CEN keypad presses on object `73`.

## Hardware Profile

- **Gateways**:
  - BTicino MH200N (Firmware 1.0, SSDP identified)
  - BTicino MyHomeServer1
- **Subsystems**: WHO 15 (CEN Scenario Control), WHO 1 (Lighting), WHO 13 (Gateway Time/Date), WHO 1001 (Diagnostics)
- **Home Assistant Version**: 2026.9.3
- **Integration Version**: 2.0.0b13
- **OWNd Version**: 2.0.0b8

## Contributed Files

| File | Gateway | Total Frames | WHO 15 Frames | Description |
|---|---|---|---|---|
| `myhome_trace_MH200N_all_2026-09-29T18-31-59.json` | MH200N | 81 | 14 | Complete CEN lifecycle on buttons 1 and 2: short press & release, and long press with repeat hold (`#3`) and long release (`#2`). |
| `myhome_trace_MyHomeServer1_all_2026-09-29T16-34-38.json` | MyHomeServer1 | 22 | 6 | CEN button 1 long press: initial pressure, 4 repeat hold frames (`#3`) spaced ~0.6 s apart, and long release (`#2`), amidst gateway clock broadcasts. |

## Recorded Sequence & Protocol Discoveries

### 1. MH200N Capture (`..._18-31-59.json`)
Recorded on CEN object `73`:
- **Button 1 Short Press**:
  - `*15*01*73##` at `18:31:44.517` (pressure)
  - `*15*01#1*73##` at `18:31:44.655` (+0.14 s: release after short press)
- **Button 1 Long Press**:
  - `*15*01*73##` at `18:31:46.846` (pressure)
  - `*15*01#3*73##` at `18:31:47.414` (+0.57 s: extended pressure / held)
  - `*15*01#3*73##` at `18:31:48.454` (+1.04 s: extended pressure / held)
  - `*15*01#3*73##` at `18:31:48.976` (+0.52 s: extended pressure / held)
  - `*15*01#2*73##` at `18:31:49.053` (+0.08 s: release after extended pressure)
- **Button 2 Short Press**:
  - `*15*02*73##` at `18:31:49.797` (pressure)
  - `*15*02#1*73##` at `18:31:49.914` (+0.12 s: release after short press)
- **Button 2 Long Press**:
  - `*15*02*73##` at `18:31:51.163` (pressure)
  - `*15*02#3*73##` at `18:31:51.704` (+0.54 s: extended pressure / held)
  - `*15*02#3*73##` at `18:31:52.221` (+0.52 s: extended pressure / held)
  - `*15*02#3*73##` at `18:31:53.334` (+1.11 s: extended pressure / held)
  - `*15*02#2*73##` at `18:31:53.710` (+0.38 s: release after extended pressure)

### 2. MyHomeServer1 Capture (`..._16-34-38.json`)
Recorded on CEN object `73`:
- **Button 1 Long Press**:
  - `*15*01*73##` at `16:34:29.579` (pressure)
  - `*15*01#3*73##` at `16:34:30.196` (+0.62 s: held)
  - `*15*01#3*73##` at `16:34:30.811` (+0.61 s: held)
  - `*15*01#3*73##` at `16:34:31.660` (+0.85 s: held)
  - `*15*01#3*73##` at `16:34:32.710` (+1.05 s: held)
  - `*15*01#2*73##` at `16:34:32.929` (+0.22 s: release after extended pressure)

## Protocol Conclusions

- Fully verifies the OpenWebNet specification for WHO 15:
  - Initial pressure emits `*15*BUTTON*WHERE##`.
  - Short release emits `*15*BUTTON#1*WHERE##`.
  - Holding the button emits periodic `*15*BUTTON#3*WHERE##` frames (~0.5–0.6 s intervals).
  - Extended release emits `*15*BUTTON#2*WHERE##`.
- Settles Item 1 of #434 with authentic on-wire physical hardware captures.
