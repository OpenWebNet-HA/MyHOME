# MH200 + L4561N Stereo Control Trace

Bus frames recorded with the MyHOME bus monitor on the plant of
`mh200_sound_f441m/`:
- **Gateway**: BTicino MH200, firmware 2.0.32
- **Source 102**: Legrand / BTicino L4561N stereo control interface (source 2 of the F441M matrix)
- **Zone 21**: amplifier in environment 2
- **Recorded**: 2026-10-07, 20:17:42 to 20:18:22 (CEST), 44 frames (30 tx, 14 rx)

`tests/test_l4561n_source_trace_replay.py` replays it.

### What was sent
- Source on (`*16*0*102##`, `*16*3*102##`) and off (`*16*10*102##`, `*16*13*102##`)
- Station/track up `*16*6001*102##`, `*16*6003*102##`, `*16*6005*102##`
- Station/track down `*16*6101*102##`, `*16*6102*102##`, `*16*6104*102##`
- Preset write `*#16*102*#7*1##`, `*#16*102*#7*2##`, `*#16*102*#7*5##`
- WHAT 102 (stop sending RDS) to zone 21: `*16*102*21##`
- Zone 21 volume up `*16*1002*21##`, `*16*1003*21##`, zone on `*16*0*21##`

### What it shows and what it does not
- The capture is filtered on WHO 16 and WHERE 102/21, so the gateway's
  `*#*1##` / `*#*0##` replies are not in it. It does not say whether a
  command was ACKed or NACKed.
- None of the commands changed what was reported:
  - Source 102 kept answering `*16*3*102##` (on), even after the two off commands.
  - Zone 21 stayed at `*16*13*21##` (off) after `*16*0*21##`, and its volume stayed `0`.
  - No frequency, preset or RDS frame came back after the station/track steps or the preset writes.
- The L4561N is an auxiliary stereo input, not a tuner, so station steps and
  presets may have no meaning for it.

The same file is in own-firmware-oracle#25 (`tests/fixtures/traces/`) as the
live counterpart of its emulated WHO 16 suite. A repeat recording without
the WHERE filter would show the ACK/NACK for each command.
