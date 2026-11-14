# #746 MH200N General OFF Scenario vs Area 00 Switch Re-Sync

Bus frames and command exchanges captured on a physical **BTicino MH200N** gateway (`00:03:50`, Home Assistant `2026.9.4`, integration `2.0.0b15`, `OWNd 2.0.0b10`) during manual wall-switch / Home Assistant toggling of a `switch` actuator at `WHERE="04"` (Area `0`, PL `4`) followed by a physical CEN wall button (`*15*01*81##`) triggering MH200N Scenario 6 (`*17*1*6##` -> `*1*0*0##` General OFF).

Contributed by **@wave68runner** on [#746](https://github.com/OpenWebNet-HA/MyHOME/issues/746). Thank you!

| File | Content |
|---|---|
| `myhome_trace_MH200N_all_2026-10-17T14-28-20.json` | 117 frames across `WHO=1`, `WHO=13`, `WHO=15`, `WHO=16`, `WHO=17`, `WHO=22`, and `WHO=1001` |

## What it shows

### 1. Area `0` (`04`) switch toggling from wall switch and Home Assistant
1. **Wall switch ON (`14:27:48.446`–`14:27:48.483`)**: Physical button press emits translation frame `*1*1000#1*04##` followed 37 ms later by actuator status `*1*1*04##`.
2. **Home Assistant OFF / ON (`14:27:56.959`–`14:27:58.603`)**: Home Assistant sends `tx *1*0*04##` (echoed as `rx *1*0*04##`) and `tx *1*1*04##` (echoed as `rx *1*1*04##`).
3. **Wall switch OFF / ON (`14:28:00.796`–`14:28:02.732`)**: Physical button toggles `04` OFF (`*1*1000#0*04##` -> `*1*0*04##`) and back ON (`*1*1000#1*04##` -> `*1*1*04##`), leaving `04` in state `on`.

### 2. MH200N Scenario 6 (`*1*0*0##` General OFF + partial Area `00` point commands)
At `14:28:06.136`, pressing CEN button `*15*01*81##` starts MH200N Scenario 6 (`*17*1*6##`):
- `14:28:06.502` (`+0 ms`): `rx *1*0*0##` (**General OFF**). Standard SCS relays such as `04` turn off physically without emitting an individual point-to-point status frame.
- `14:28:06.538` (`+35 ms`): `rx *1*0*01##`
- `14:28:06.603` (`+101 ms`): `rx *1*0#1*0##`
- `14:28:06.636` (`+134 ms`): `rx *1*1*03##`
- `14:28:06.704` (`+201 ms`): `rx *17*2*6##` (Scenario 6 end)
- `14:28:06.875` (`+372 ms`): `rx *1*0*01##`
- `14:28:06.936` (`+434 ms`): `rx *1*0*09##`
- `14:28:06.994` (`+492 ms`): `rx *1*1*0011##`

### 3. Additional authentic MH200N frames in capture
- **WHO=16 / WHO=22 Sound System**: Scenario 6 also turns off amplifier zones `*16*13*101##`, `*16*3*101##`..`*16*3*181##` accompanied by `WHO=22` diffusion frames (`*#22*2#1*12*0*10##`, `*22*1#4#1*2#1##`, `*22*21#4#1*5#2#1##`, `*22*2#4#1*5#2#1##`, `*#22*5#1#1*8*1*8##`).
- **WHO=1001 Diagnostics**: Interleaved actuator diagnostic bitmasks (`*#1001*42*11*...##`, `*#1001*72*11*...##`) during the area status sweeps.
