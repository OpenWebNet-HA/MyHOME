# Beta Releases, Architectural Milestones & Delta Explorer

This guide provides a comprehensive comparison matrix and chronological delta log across all **MyHOME v2** beta releases, tracking architectural additions, gateway options, on-wire protocol behaviors, and version differences.

---

## 📊 Cross-Beta Capability Matrix

The table below outlines feature availability, gateway support, and protocol capabilities across all release milestones:

| Feature / Subsystem | v2.0.0b1 – b4 | v2.0.0b5 – b8 | v2.0.0b9 – b10 | v2.0.0b11 | v2.0.0b13 | v2.0.0b14 (Latest) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Protocol Engine (`OWNd`)** | Embedded `0.7.48` | `2.0.0b5` (PyPI) | `2.0.0b5` (PyPI) | `2.0.0b6` (PyPI) | `2.0.0b8` (HMAC refactor) | `2.0.0b8` (HMAC refactor) |
| **Command Worker Concurrency** | Static (1 worker) | Static (1 worker) | Static (1 worker) | Dynamic (1–4) | Dynamic (1–4, model-capped) | Dynamic (1–4, model-capped) |
| **Gateway In-Place Reconfiguration** | ❌ Delete & Re-add | ❌ Delete & Re-add | ❌ Delete & Re-add | ❌ Delete & Re-add | ✅ IP, Password, Model in Options | ✅ IP, Password, Model in Options |
| **Dimmer Transitions** | `software_stepped` | `software_stepped` | `software_stepped` (shielded) | `software_stepped` / `native` | `software_stepped` / `native` | `software_stepped` / `native` |
| **Command Translation (`WHAT=1000`)** | State flicker | State flicker | ✅ Filtered / Preserved | ✅ Filtered / Preserved | ✅ Filtered / Preserved | ✅ Filtered / Preserved |
| **DALI Color & Tunable White** | ❌ | ❌ | ❌ | ✅ Native HSV (Dim 12) + DT8 (Dim 14) | ✅ Native HSV (Dim 12) + DT8 (Dim 14) | ✅ Native HSV (Dim 12) + DT8 (Dim 14) |
| **Thermoregulation (`WHO = 4`)** | Basic zones | Basic zones | Basic zones | Central Units 3550 / 4695 | Fan mode restore (#404) | Climate zone state & sweep (#457) |
| **Cover Concurrency Isolation** | Standard | Standard | Standard | Dedicated queue | Dedicated queue | Full run-stop lock (#433) |
| **Sound System (`WHO = 16 / 22`)** | ❌ | Basic Matrix | Basic Matrix | Volume normalization & Proxy | Dynamic Proxy Decoders | FM Tuner stepping + Matrix names |
| **CEN / CEN+ Triggers (`WHO = 15/25`)** | Basic events | Basic events | Duplicate ID fix (#247) | UI Device Triggers (all 8 actions) | Long-press repeat debouncing | Centralized control triggers (#466) |
| **Lovelace Bus Monitor Card** | ❌ CLI only | ✅ In-band card | Scoped registry fix (#277) | Export trace / Report issue | Filter & style refinements | Offline bus replay tab |
| **Trace Replay CI Engine** | ❌ Unit tests only | ❌ Unit tests only | Golden frame checks | ✅ Synthetic plant fixtures | Plant #247 & MH200 fixtures | Multi-plant matrix (F454, MH202, H4890) |
| **Test Suite Statements Covered** | ~850 tests | 984 tests (100%) | 1,140 tests (100%) | 1,294 tests (100%) | 1,320 tests (100%) | 1,350+ tests (100%) |

---

## 🔍 Release-by-Release Delta Log & GitHub Compares

### 🚀 v2.0.0b14 (Latest Development Release)
- **Direct GitHub Compare**: [`Compare v2.0.0b13...v2.0.0b14 (Development Branch)`](https://github.com/OpenWebNet-HA/MyHOME/compare/2.0.0b13...v2-phase1-architecture)
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
