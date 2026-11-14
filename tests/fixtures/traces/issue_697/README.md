# #697 F454 with 99-zone Central Unit and Subordinate Zones

Bus frames and command exchanges captured on a physical **BTicino F454** gateway (firmware 1.0.34)
connected to an SCS heating plant with a **99-zone Central Unit** (`WHERE #0`) and 13 subordinate zones.
All zones and the central unit are discovered from the bus (no manual YAML configuration).

Contributed by **@ricdijk** on [#697](https://github.com/OpenWebNet-HA/MyHOME/issues/697). Thank you!

| File | Content |
|---|---|
| `f454_99zone_central_subordinate_zones.json` | Setpoint writes, OFF/AUTO mode writes, central unit protection switches (`202`/`102`), and zone response frames |

## What it shows

### 1. Zone addressing behind a central unit
On a plant with a central unit (`WHERE #0`), subordinate zones strictly require the `#` prefix (`#Z`)
when receiving commands:
- **OFF write**: `*4*303*#3##` is accepted and the zone answers `*4*303*3##` and `*4*103*#3##` ("Zone 3's mode is set to 'off'"). Without `#`, `*4*303*3##` fails every time on the F454 (`Could not send message... No more retries`).
- **AUTO write**: `*4*311*#3##` is accepted and confirmed by `*4*110*#3##`.
- **Setpoint write**: `*#4*#3*#14*0145*1##` is accepted and confirmed by `*#4*3*12*0145*3##` ("Zone 3's local target temperature is set to 14.5°C"). Without `#`, `*#4*3*#14*...##` fails.
- **Local offsets**: Zone 11 has a local offset of -3.0°C; when setting 14.0°C, the integration sends `0170` (17.0°C), and the zone confirms `0140` (14.0°C).

### 2. Central unit season switch and protection modes
Switching the season switch on the physical central control panel between "Estate" (summer) and "Inverno" (winter):
- **Estate (Summer)**: Central emits `*4*202*#0##` (thermal protection). Within ~14 seconds, subordinate zones follow with `*4*202*Z##` and setpoint reports `*#4*Z*12*0350*3##` (cooling protection setpoint 35.0°C).
- **Inverno (Winter)**: Central emits `*4*102*#0##` (antifreeze). Subordinate zones follow with `*4*102*Z##` and setpoint reports `*#4*Z*12*0070*3##` (antifreeze setpoint 7.0°C; zone 1 reports 10.0°C).
- Central unit target temperature writes from Home Assistant (`*#4*#0*#14*0165*1##`) are accepted and confirmed by `*4*110#0165*#0##`.
