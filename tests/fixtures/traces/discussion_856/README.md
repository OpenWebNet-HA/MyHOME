# Discussion #856 BTicino F453 (FW 2.0.10) Gateway Sweep & Timed Shutter Diagnostics Trace

- **Gateway Model**: BTicino `F453` (Enhanced Web Server, single-worker legacy ETLinux family; UPnP `modelName = "F453"`, `manufacturer = "BTicino S.p.A."`, `WHO=13` `Device Type = 16`, `WHO=1013` `OBJECT_MODEL = 16` on firmware `2.0.10` vs `42` in `MHCatalogue.db`)
- **Firmware Version**: `2.0.10` (Linux kernel `2.3.8`, ETLinux File System `3.0.1`)
- **Contributed by**: **@flow68000** in [Discussion #856 (comment 19587072)](https://github.com/orgs/OpenWebNet-HA/discussions/856#discussioncomment-19587072)
- **Environment**: Home Assistant 2026.11.1, MyHOME 2.0.0b17, OWNd 2.0.0b13

| File | Content | Frame Count |
|---|---|:---:|
| `config_entry-myhome_F453_fw2_0_10.json` | Sanitized Home Assistant diagnostics export with two `myhome.sweep_bus` captures and 12 timed `WHO=2` shutters | 327 (`195 rx`, `132 tx`) |

---

## 1. Hardware & In-Band Identity Fingerprint

This is the first physical plain **BTicino `F453`** (distinct from `F453AV`) trace in the `MyHOME` corpus. Across two `myhome.sweep_bus` runs (`2026-11-13T09:29:08Z` and `09:38:06Z`), the gateway answered every `WHO=13` and `WHO=1013` diagnostic query locally in `6.7–13.7 ms`:

| Query | Reply | Meaning |
|---|---|---|
| `*#13**0##` | `*#13**0*10*47*05*001##` / `*#13**0*10*56*02*001##` | Gateway RTC time with valid `UTC+1` timezone (`001`) |
| `*#13**15##` | `*#13**15*16##` | `WHO=13` Dimension 15 device type `16` (`F453`, corroborating Nmap's 2017 table on real hardware) |
| `*#13**16##` | `*#13**16*2*0*10##` | Firmware `2.0.10` |
| `*#13**23##` | `*#13**23*2*3*8##` | Linux kernel `2.3.8` (`BTPXA 2.3.8`) |
| `*#13**24##` | `*#13**24*3*0*1##` | ETLinux distribution `3.0.1` |
| `*#1013*0*1##` | `*#1013**1*16*0*1*0##` | `OBJECT_MODEL = 16`, `N_CONF = 0`, `BRAND = 1` (`BTicino`), `LINE = 0` (`Undefined`) |

### Why `2.0.0b17` raised `unknown_gateway_model` on `myhome.sweep_bus`

On normal startup, `WHO=13` code `16` is unambiguous (not in `WHO13_SHARED_DEVICE_TYPES`), so `*#1013**1##` is not requested. When `myhome.sweep_bus` explicitly queried `*#1013*0*1##`, the `F453` (`2.0.10`) echoed its `WHO=13` device type `16` (`*#1013**1*16*0*1*0##`) instead of `MHCatalogue.db`'s `OBJECT_MODEL = 42`, and reported `BRAND = 1` (`BTicino` in `EN_BRAND.brand_modobj`) rather than `5` (`Legrand BTicino`). Prior to this fixture, `1013-1-16` was unmapped and raised `unknown_gateway_model`.

---

## 2. Bus Monitor Traffic & Shutter-Only Plant Topology

The installation is a pure **12-shutter `WHO=2` plant** using BTicino `F411` timed shutter actuators (labelled generically as `67249` in Home Assistant's `WHO=2` device registry) at `WHERE` addresses `11, 12, 21, 22, 23, 24, 25, 34, 35, 36, 37, 89`:

- **Cross-domain SCS general status echo (`frames[0..25]`)**: Sending `*#1*0##` (`WHO=1` general lighting status) onto the SCS bus causes all 12 `WHO=2` shutter actuators to report their `WHO=2` resting state `*2*0*<WHERE>##` (`11, 21, 12, 22, 23, 24, 34, 25, 35, 36, 37, 89`), followed by the identical 12-frame `WHO=2` burst on `*#2*0##`.
- **Broadcast & area shutter movements (`frames[54..162]`)**: Captures live general (`*2*1*0##`, `*2*0*0##`) and Area `2` (`*2*1*2##`, `*2*0*2##`) automation broadcasts alongside per-shutter `UP` (`*2*1*<WHERE>##`), `DOWN` (`*2*2*<WHERE>##`), and `STOP` (`*2*0*<WHERE>##`) cycles and targeted `*#2*<WHERE>##` re-sync polls.
- **Measured `cover_travel_times`**: 11 of 12 shutters calibrated between `13.85–27.70 s` down and `15.99–31.02 s` up; shutter `24` recorded an interrupted/partial calibration (`"down": 2.97`, `"up": 10.56`).
