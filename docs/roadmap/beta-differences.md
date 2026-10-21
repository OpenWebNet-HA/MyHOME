# Beta Releases, Architectural Milestones & Delta Explorer

This guide provides a comprehensive comparison matrix and chronological delta log across all **MyHOME v2** beta releases, tracking architectural additions, gateway options, on-wire protocol behaviors, and version differences.

---

## 📊 Cross-Beta Capability Matrix

The table below outlines feature availability, gateway support, and protocol capabilities across all release milestones:

| Feature / Subsystem | v2.0.0b1 – b4 | v2.0.0b5 – b8 | v2.0.0b9 – b10 | v2.0.0b11 | v2.0.0b13 | v2.0.0b14 | v2.0.0b15 | v2.0.0b16 (Latest) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Protocol Engine (`OWNd`)** | Embedded `0.7.48` | `2.0.0b5` (PyPI) | `2.0.0b5` (PyPI) | `2.0.0b6` (PyPI) | `2.0.0b8` (HMAC refactor) | `2.0.0b9` | `2.0.0b10` | `2.0.0b11` |
| **Command Worker Concurrency** | Static (1 worker) | Static (1 worker) | Static (1 worker) | Dynamic (1–4) | Dynamic (1–4, model-capped) | Dynamic (1–4, model-capped) | Per-profile session defaults (#606) | User commands ahead of polls, frame scheduler (#685, #738) |
| **Gateway In-Place Reconfiguration** | ❌ Delete & Re-add | ❌ Delete & Re-add | ❌ Delete & Re-add | ❌ Delete & Re-add | ✅ IP, Password, Model in Options | ✅ IP, Password, Model in Options | ✅ IP, Password, Model in Options | ✅ IP, Password, Model in Options |
| **Dimmer Transitions** | `software_stepped` | `software_stepped` | `software_stepped` (shielded) | `software_stepped` / `native` | `software_stepped` / `native` | `software_stepped` / `native` | `software_stepped` / `native` | Fade steps sent at low priority (#739) |
| **Command Translation (`WHAT=1000`)** | State flicker | State flicker | ✅ Filtered / Preserved | ✅ Filtered / Preserved | ✅ Filtered / Preserved | ✅ Filtered / Preserved | ✅ Filtered / Preserved | ✅ Filtered / Preserved |
| **DALI Color & Tunable White** | ❌ | ❌ | ❌ | ✅ Native HSV (Dim 12) + DT8 (Dim 14) | ✅ Native HSV (Dim 12) + DT8 (Dim 14) | ✅ Native HSV (Dim 12) + DT8 (Dim 14) | ✅ Native HSV (Dim 12) + DT8 (Dim 14) | ✅ Native HSV (Dim 12) + DT8 (Dim 14) |
| **Thermoregulation (`WHO = 4`)** | Basic zones | Basic zones | Basic zones | Central Units 3550 / 4695 | Fan mode restore (#404) | Climate zone state & sweep (#457) | Central mode commands aligned (#630) | Seasons, holiday programs, humidity (#657, #689, #712) |
| **Cover Concurrency Isolation** | Standard | Standard | Standard | Dedicated queue | Dedicated queue | Full run-stop lock (#433) | Full run-stop lock (#433) | Stop overtakes the queue (#694) |
| **Cover Positioning (`WHO = 2`)** | Travel time | Travel time | Travel time | Travel time | Travel time | Travel time | Travel time | Start-up floor, full runs, slat tilt (#702, #708, #508) |
| **Sound System (`WHO = 16 / 22`)** | ❌ | Basic Matrix | Basic Matrix | Volume normalization & Proxy | Dynamic Proxy Decoders | FM Tuner stepping + Matrix names | FM Tuner stepping + Matrix names | WHO 22 tuner RDS, source transport (#695, #651) |
| **CEN / CEN+ Triggers (`WHO = 15/25`)** | Basic events | Basic events | Duplicate ID fix (#247) | UI Device Triggers (all 8 actions) | Long-press repeat debouncing | Centralized control triggers (#466) | 4-digit addresses, fail-closed triggers (#601, #624) | WHO 25 Scenario Plus (#671) |
| **Expose HA Entities to the Bus** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ WHO 1/2/4/13/18 (#750) |
| **Lovelace Bus Monitor Card** | ❌ CLI only | ✅ In-band card | Scoped registry fix (#277) | Export trace / Report issue | Filter & style refinements | Offline bus replay tab | Offline bus replay tab | Offline bus replay tab |
| **Trace Replay CI Engine** | ❌ Unit tests only | ❌ Unit tests only | Golden frame checks | ✅ Synthetic plant fixtures | Plant #247 & MH200 fixtures | Multi-plant matrix (F454, MH202, H4890) | F455, MH200 + F422, Living Now captures | Live F461 plants, firmware oracle checks |
| **Test Suite Statements Covered** | ~850 tests | 984 tests (100%) | 1,140 tests (100%) | 1,294 tests (100%) | 1,320 tests (100%) | 1,350+ tests (100%) | 3,464 tests (100%) | 4,393 tests (100%) |

---

## 🔍 Release-by-Release Delta Log & GitHub Compares

### 🚀 v2.0.0b16 (Expose Engine, Venetian Slat Tilt & Cover Start-Up Floor)
- **Direct GitHub Compare**: [`Compare v2.0.0b15...v2.0.0b16`](https://github.com/OpenWebNet-HA/MyHOME/compare/2.0.0b15...2.0.0b16)
- **Key Enhancements** (83 pull requests, on OWNd 2.0.0b11):
  - **Expose HA Entities to the Bus (#750)**: Declarative `expose` engine projecting Home Assistant entities and the clock onto free SCS addresses: virtual WHO 1 / WHO 2 actuators driven by wall controls, WHO 4 virtual temperature probes, WHO 18 virtual energy meters and the WHO 13 bus clock.
  - **Cover Motor Start-Up Floor & Endpoint Full Runs (#466, #702, #707, #708)**: Timed covers enforce a per-cover `min_motor_pulse` floor (default `0.4 s`, `0` disables) and book the stretched run; timed and advanced covers send plain `UP` / `DOWN` (`*2*1*WHERE##` / `*2*2*WHERE##`) full runs at `0%` and `100%` (advanced covers widen sub-2% moves when stationary); timed endpoint runs settle at `travel_time + 2 s`, and timed runs end at the actuator's stop status.
  - **Venetian Blind Slat Tilt (#492, #508)** and **Impulse Gate / Garage Covers with Intercom Events (#667)**.
  - **Thermoregulation**: WHO 4 season frames, holiday programs, zone humidity, and F461 zone polling (#657, #689, #712, #714).
  - **Sound & Scenarios**: WHO 22 tuner frequency, preset and RDS (#695); WHO 25 Scenario Plus (#671).
  - **Behaviour changes**: `myhome.sync_time` refuses half-hour time zones (#764); `advanced: true` on a standard F411 logs a warning (#762).

---

### 🚀 v2.0.0b15 (Device Fault Repairs & Ignored Addresses)
- **Release Date**: 7 October 2026
- **Direct GitHub Compare**: [`Compare v2.0.0b14...v2.0.0b15`](https://github.com/OpenWebNet-HA/MyHOME/compare/2.0.0b14...2.0.0b15)
- **Key Enhancements** (41 pull requests, on OWNd 2.0.0b10):
  - **Device Fault Repairs (#593)**: Actuator hardware anomalies surface as self-clearing Home Assistant repair issues (`actuator_fault`).
  - **Ignored Addresses (#621)**: Silence faulty or unwanted devices and their repair issues from the Options Flow.
  - **Dimmers**: Dimmer no-load state (`WHAT 19`) handled without dropping light entities (#622); brightness 0 % routes to `switch_off` (#635).
  - **Thermoregulation**: Central unit mode commands aligned with `libqtdevices`; invalid fan speeds caught as service errors; central units exempt from status polling (#630, #636, #641).
  - **Burglar Alarm & Scenarios**: Empty-`WHERE` routing to the central panel and read-only auxiliary panel mode (#609, #616); 4-digit CEN addresses and fail-closed device triggers (#601, #624).
  - **Connection Resilience**: Event session inactivity timeouts disabled on quiet plants (#637, #640); per-profile default session counts (#606).

---

### 🚀 v2.0.0b14 (Multi-Gateway Routing & Climate Zone State)
- **Release Date**: 30 September 2026
- **Direct GitHub Compare**: [`Compare v2.0.0b13...v2.0.0b14`](https://github.com/OpenWebNet-HA/MyHOME/compare/2.0.0b13...2.0.0b14)
- **Key Enhancements**:
  - **Multiple Gateway Routing Architecture (#453)**: Full multi-gateway plant isolation with namespaced dispatchers and cross-gateway bus protection.
  - **Climate Zone State Diagnostics (#457)**: Dynamic HVAC action and climate zone status sweeps resolving restart desynchronization.
  - **F500 Tuner Dimension Reports**: Tuner frequency stepping, RDS station name decoding, and FM presets for Legrand sound systems.
  - **Touchscreen Gateway Profiles**: Authoritative profile matching and SSDP discovery for `AM4890`, `H4890`, and `LN4890` Livinglight / Axolute 3.5" displays.
  - **WHO=2 Centralized Control Triggers**: Added cover and shutter group/general automation triggers.

---

### 🚀 v2.0.0b13 (HMAC Authentication Refactor & Fan Mode Persistence)
- **Release Date**: 18 September 2026
- **Direct GitHub Compare**: [`Compare v2.0.0b11...v2.0.0b13`](https://github.com/OpenWebNet-HA/MyHOME/compare/2.0.0b11...2.0.0b13)
- **Key Enhancements**:
  - **Engine Upgrade to OWNd 2.0.0b8**: Integrated upstream OWNd HMAC authentication refactor with SHA-1 and SHA-256 session handshakes.
  - **Climate Fan Mode Persistence (#404, #405)**: Restored fan mode state across Home Assistant restarts and added golden sample tests.
  - **CI Modernization**: 100% green CI across all 9 GitHub Actions workflows on Python 3.14 and Home Assistant Core 2026.9.2.
  - **Quality Scale Platinum Alignment**: Strict typing and single-source `entry.runtime_data` across all 9 entity platforms.

---

### 🚀 v2.0.0b11 (Trace Replay CI Engine & Native DALI HSV Color)
- **Release Date**: 11 September 2026
- **Direct GitHub Compare**: [`Compare v2.0.0b10...v2.0.0b11`](https://github.com/OpenWebNet-HA/MyHOME/compare/2.0.0b10...2.0.0b11)
- **Key Enhancements**:
  - **Trace Replay Test Engine**: Automated discovery and deterministic replay of authentic on-wire physical plant fixtures (`tests/test_trace_replay.py`).
  - **Native DALI HSV Color (Dimension 12)**: Encoded `*#1*WHERE*#12*H*S*V*T##` aligned with F429/F461 DALI gateways.
  - **Dynamic Diagnostic Bus Sweep (`myhome.sweep_bus`)**: One-click safe bus discovery service with automated credential redaction.
  - **Hardware Gateway Pacing Profiles**: Dedicated pacing and queue limits for `F454`, `F455`, `F461`, `MH200`, `MH200N`, `MH201`, `MH202`, and `MyHomeServer1`.

---

### 🚀 v2.0.0b10 (Dry Contact Suffix Stripping & ID Normalization)
- **Release Date**: 11 September 2026
- **Direct GitHub Compare**: [`Compare v2.0.0b9...v2.0.0b10`](https://github.com/OpenWebNet-HA/MyHOME/compare/2.0.0b9...2.0.0b10)
- **Key Enhancements**:
  - **Dynamic Suffix Stripping (#247, #286)**: Dynamic stripping of `BinarySensorDeviceClass` suffixes from dry contact addresses, preventing invalid bus query frames (`*#25*331-moving##`).
  - **Normalized Single-WHO Identifiers**: Resolved duplicate device registry entries (`mac-25-31` instead of `mac-25-25-31`).
  - **CEN/CEN+ Pruning Exemption**: Startup cleanup exempts active CEN scenario units while cleanly pruning ghost auxiliary devices.

---

### 🚀 v2.0.0b9 (Command Translation WHAT=1000 Frame Filtering)
- **Release Date**: 11 September 2026
- **Direct GitHub Compare**: [`Compare v2.0.0b8...v2.0.0b9`](https://github.com/OpenWebNet-HA/MyHOME/compare/2.0.0b8...2.0.0b9)
- **Key Enhancements**:
  - **Translation Frame Filtering (#283, #284)**: Platform message dispatchers ignore auxiliary translation frames (`*1*1000#1*WHERE##`), preventing light and switch entities from flickering to `unknown` before actuator feedback arrives.
  - **Software Fade Protection**: Active stepped brightness fades are protected against premature cancellation by translation frames.

---

### 🚀 v2.0.0b8 & v2.0.0b7 (Lovelace Bus Card & Scoped Registry Self-Healing)
- **Release Date**: 10 September 2026
- **Direct GitHub Compare**: [`Compare v2.0.0b6...v2.0.0b8`](https://github.com/OpenWebNet-HA/MyHOME/compare/2.0.0b6...2.0.0b8)
- **Key Enhancements**:
  - **Scoped Registry Self-Healing (#277)**: Dynamic getter and 45-second watchdog interval restoring `<myhome-bus-card>` elements if overwritten by polyfills (such as `scheduler-card`).
  - **Lovelace Card Picker Integration**: Implemented `getStubConfig()` and `getConfigForm()` for native visual card configuration in Home Assistant dashboards.
  - **Dual-Route Static Serving**: Registered both `/myhome_static` and automatic sync to `/config/www/` for fail-safe frontend loading.

---

### 🚀 v2.0.0b6 & v2.0.0b5 (Decoupled OWNd Engine & Master WHO Catalog)
- **Release Date**: 10 September 2026
- **Direct GitHub Compare**: [`Compare v2.0.0b4...v2.0.0b6`](https://github.com/OpenWebNet-HA/MyHOME/compare/2.0.0b4...2.0.0b6)
- **Key Enhancements**:
  - **Decoupled OWNd PyPI Engine**: Pinned integration engine to [`OWNd==2.0.0b5`](https://pypi.org/project/OWNd/2.0.0b5/).
  - **Master WHO Catalog**: Embedded complete OpenWebNet subsystem dictionary (`WHO = 0` through `WHO = 1013`) into the Bus Monitor card with dynamic auto-registration for custom firmware frames.
  - **Burglar Alarm Subsystem Filter**: Dedicated badge styling and filtering for `WHO = 5`.

---

### 🚀 v2.0.0b1 through v2.0.0b4 (Phase 1 Dual-Session Architecture Beta)
- **Release Date**: 10 September 2026
- **Key Enhancements**:
  - Modernized asynchronous dual-session architecture: dedicated Command Session (`*99*0##`) and Event Stream Session (`*99*1##`).
  - Full removal of legacy blocking socket calls.
  - Native Home Assistant UI Config Flow with SSDP auto-discovery.
