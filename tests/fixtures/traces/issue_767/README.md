# #767 MH200N Pre-09W29 F414 Dimmer (`WHERE="61"`, `SPEED = 240` / `0xF0` Coarse-Only Profile)

Bus frames and command exchanges captured on a physical **BTicino MH200N** gateway (`00:03:50`, firmware `1.0`, Home Assistant `2026.9.4`, integration `2.0.0b15`, `OWNd 2.0.0b10`) during physical wall-switch / iMyHome stepping and Home Assistant dimming of a pre-`09W29` **BTicino F414** dimmer actuator at `WHERE="61"` (Area `6`, PL `1`).

Contributed by **@wave68runner** on [#767](https://github.com/OpenWebNet-HA/MyHOME/issues/767). Thank you!

| File | Content |
|---|---|
| `myhome_trace_MH200N_all_2026-10-21T18-12-44.json` | 43 frames across `WHO=1` and `WHO=13` (`35 rx`, `8 tx`) |

## What it shows

### 1. Physical / iMyHome step-up and step-down via `WHAT 2..10` coarse presets (`18:11:09.762`–`18:11:17.717`, frames `0..25`)
1. **Switch ON (`18:11:09.762`–`18:11:09.796`)**: Physical button press emits translation frame `*1*1000#1*61##` followed 34 ms later by actuator preset status `*1*9*61##` (`90%`).
2. **Step-up (`18:11:11.615`–`18:11:13.972`)**: Seven step-up presses (`*1*1000#30*61##`) answered by the F414 with `*1*10*61##` (`100%` ceiling).
3. **Step-down (`18:11:15.656`–`18:11:17.717`)**: Five step-down presses (`*1*1000#31*61##`) stepping the actuator down in 10% coarse increments:
   - `*1*9*61##` (`90%`)
   - `*1*8*61##` (`80%`)
   - `*1*7*61##` (`70%`)
   - `*1*6*61##` (`60%`)
   - `*1*5*61##` (`50%`)

### 2. Home Assistant OFF / ON toggle and `DIMENSION 1` query (`18:11:38.610`–`18:11:40.358`, frames `27..32`)
1. **Home Assistant OFF (`18:11:38.610`–`18:11:38.698`)**: Home Assistant sends `tx *1*0*61##` (echoed on bus as `rx *1*0*61##`).
2. **Home Assistant ON + `DIMENSION 1` query (`18:11:40.236`–`18:11:40.358`)**:
   - `18:11:40.236`: `tx *1*1*61##` (switch ON)
   - `18:11:40.320`: `rx *1*5*61##` (actuator resumes at memorized `WHAT 5` / `50%` preset)
   - `18:11:40.320`: `tx *#1*61*1##` (`DIMENSION 1` level/speed status request)
   - `18:11:40.358`: `rx *#1*61*1*130*240##` (`level = 130` -> `30%`, `speed = 240` / `0xF0` factory unprogrammed / coarse-only speed sentinel of pre-`09W29` F414 firmware)

### 3. Home Assistant `DIMENSION 1` fine-percentage writes (`18:11:41.636`–`18:11:48.784`, frames `33..42`)
Home Assistant sends four `DIMENSION 1` instant brightness writes (`*#1*61*#1*<level>*0##`):
- `18:11:41.636`: `tx *#1*61*#1*178*0##` (`78%`) -> `18:11:41.737`: `rx *#1*61*1*178*240##`
- `18:11:43.259`: `tx *#1*61*#1*146*0##` (`46%`) -> `18:11:43.368`: `rx *#1*61*1*146*240##`
- `18:11:45.647`: `tx *#1*61*#1*181*0##` (`81%`) -> `18:11:45.756`: `rx *#1*61*1*181*240##`
- `18:11:47.229`: `tx *#1*61*#1*140*0##` (`40%`) -> `18:11:47.336`: `rx *#1*61*1*140*240##`

On pre-`09W29` F414 hardware (`speed = 240`), `DIMENSION 1` writes (`0xD2` extended SCS frame) update the internal level register and echo `*#1*61*1*<level>*240##` on the bus without driving the physical TRIAC phase-angle output; only standard short `WHAT 0..10` commands (`0xB8` SCS frame) actuate the physical lamp output. At `18:11:48.702`, `tx *1*0*61##` -> `rx *1*0*61##` turns the dimmer physically OFF.
