# Upgrading from v0.9.4 to v2.0

This guide provides a step-by-step roadmap for migrating an existing **MyHOME v0.9.4** plant to the modern **MyHOME v2.0** architecture.

---

## 🧭 What Changes in v2.0?

| Aspect | Legacy v0.9.4 | MyHOME v2.0 |
| :--- | :--- | :--- |
| **Setup Model** | Manual YAML (`/config/myhome.yaml`) | **100% UI-First** (Config Flow & Auto-Discovery) |
| **Entity Discovery** | Strict YAML parsing at startup | Dynamic on-wire SCS bus discovery (`WHO = 1, 2, 4, 5, 16, 18, 25` entities + `WHO = 15 / 25` device triggers; `WHO = 9` via `myhome.yaml`, `WHO = 13` diagnostics) |
| **Cover Calibration** | Manual `travel_time` integers in YAML | Automated on-bus calibration (button & service action), direction-aware up/down times |
| **Light Capabilities** | Manual `dimmable: true` YAML flags | Dynamically learned from bus telemetry (dimming, HSV, color temp, DALI DT8) |
| **Entity Naming** | Configured exclusively in `myhome.yaml` | Managed natively in Home Assistant Device & Entity Registry |
| **Diagnostics** | Basic logs | Lovelace Bus Monitor Card, Home Assistant Repairs, and telemetry downloads |

---

## 🛡️ Step 1: Pre-Upgrade Preparation & Backup

> [!IMPORTANT]
> **Always take a full Home Assistant backup before upgrading any custom integration.**
>
> 1. Go to **Settings → System → Backups**.
> 2. Click **Create Backup**, select **Full backup**, and wait for completion.
> 3. Download the backup file to your local computer.

### Document Current Plant Configuration
1. Take note of your gateway IP address and OpenWebNet password.
2. Keep your existing `/config/myhome.yaml` intact. In v2.0, this file acts as a non-destructive transitional overlay for entity names, custom options, and physical SCS groups (`#G`).

---

## 📦 Step 2: Installing MyHOME v2.0

### Option A: Home Assistant Marketplace (`2026.11+`) / HACS (`2026.8 – 2026.10`)

- **Home Assistant 2026.11+ (Marketplace)**: Open **Settings → Marketplace**, open **MyHOME** *(or add `https://github.com/OpenWebNet-HA/MyHOME` via **Add from link** → **Integration**)*, install or update to **v2.0+**, and restart Home Assistant.
- **Home Assistant 2026.8 – 2026.10 (HACS)**: Open **HACS → Integrations → MyHOME**, update/download **v2.0+**, and restart Home Assistant.

*(Note: To install a `2.0.0bX` pre-release tag published ahead of stable `v2.0.0` on `master`, use Option B below.)*

### Option B: Terminal & SSH or Manual ZIP Archive

#### ⚡ 1-Click Terminal Command (Home Assistant Terminal & SSH)
If you have the **Terminal & SSH** add-on installed (or connect via SSH), paste this command directly into your terminal:

```bash
# Set target release version (check https://github.com/OpenWebNet-HA/MyHOME/releases)
TAG="2.0.1"

# Download, extract cleanly, verify, and restart Home Assistant
mkdir -p /config/custom_components && cd /config/custom_components && \
wget -O myhome.zip "https://github.com/OpenWebNet-HA/MyHOME/releases/download/${TAG}/myhome.zip" && \
rm -rf myhome && \
unzip -q myhome.zip -d myhome && \
rm myhome.zip && \
grep '"version"' myhome/manifest.json && \
ha core restart
```

> [!TIP]
> **What this command does step-by-step**:
>
> 1. `mkdir -p /config/custom_components && cd /config/custom_components`: Ensures the directory exists and enters your custom components folder.
> 2. `wget -O myhome.zip ...`: Downloads the official pre-packaged release zip directly from GitHub.
> 3. `rm -rf myhome`: Removes previous files to prevent orphaned legacy modules from colliding with v2.
> 4. `unzip -q myhome.zip -d myhome`: Extracts the integration files directly into `/config/custom_components/myhome/` (avoiding the common nested folder pitfall).
> 5. `rm myhome.zip`: Cleans up the temporary archive.
> 6. `grep '"version"' myhome/manifest.json`: Prints the installed version to the terminal for immediate confirmation.
> 7. `ha core restart`: Issues a core restart command via the Home Assistant supervisor CLI.

#### 📁 Manual Download via Samba / Studio Code Server
If you prefer not using the command line:

1. Download `myhome.zip` from [GitHub Releases](https://github.com/OpenWebNet-HA/MyHOME/releases).
2. Connect to your Home Assistant host via **Samba Share** or the **Studio Code Server** add-on.
3. Navigate to `/config/custom_components/myhome/` (create the folders if they don't exist).
4. Extract the contents of `myhome.zip` directly into `/config/custom_components/myhome/`.
5. Verify that `manifest.json` is located at `/config/custom_components/myhome/manifest.json`.
6. Restart Home Assistant (**Settings → System → Restart** or **Developer Tools → YAML → Restart**).

---

## 🔌 Step 3: Gateway Setup & Device Onboarding

1. Once Home Assistant restarts, navigate to **Settings → Devices & Services**.
2. If your gateway is discovered automatically via **SSDP / UPnP**, click **Configure**.
3. Otherwise, click **Add Integration**, search for **MyHOME**, and enter:
    - **Address**: IPv4 address of your gateway (e.g. `192.168.1.50` — strict IPv4; `.local` mDNS hostnames are rejected).
    - **Port**: `20000` (`1–65535`).
    - *(If UPnP probing does not respond on the host, a manual fallback step prompts for the gateway **MAC Address** and **Device Type**, which are refined automatically after authentication via `WHO = 13` / `WHO = 1013` queries.)*
    - **Password**: OpenWebNet numeric password (or alphanumeric HMAC password on `F454`, `F455`, `F461`, `MH202`, `MyHomeServer1`).
4. Home Assistant connects to the gateway, starts the command and event sessions, and automatically discovers your connected bus actuators and sensors.

### Are Existing Entity IDs Preserved?
**Yes.** Entity unique IDs in v2.0 are deterministically derived from the gateway MAC address and OpenWebNet address (`<mac>-<who>-<where>`). Existing dashboards, automations, and entity IDs (`light.living_room`, `cover.kitchen_shutter`) are preserved seamlessly.

---

## ⚠️ Known Breaking Changes & Upstream Fixes

Before upgrading, review these key architectural changes:

### 1. Gateway Identity Verification & `myhome.yaml` MAC Matching (`#370`)
* **Change**: v2 queries and validates the gateway MAC address, firmware (`*#13**16##`), and hardware model (`*#13**15##` / `*#1013**1##` / `*#13**12##`) via `WHO = 13` / `WHO = 1013` dimension queries.
* **Impact**:
    - If `/config/myhome.yaml` defines gateway sections under a placeholder or mismatched MAC address that matches no configured gateway, v2 **logs a warning** (`myhome.yaml has no section for gateway ...; Rename the section to this gateway's MAC to load it`) and falls back to any previously imported options snapshot rather than raising a Repair Issue.
    - If a gateway's configured hardware `model` is contradicted by live `WHO = 13` device-type frames, v2 raises a `gateway_identity_mismatch` / `gateway_identity_corrected` **Repair Issue** (resolved under **Settings → Devices & Services → MyHOME → Configure**).

### 2. MyHomeServer1 Session Concurrency & Pacing (`#397`)
* **Change**: `MyHomeServer1` firmware supports up to **4** concurrent command sessions (`default = 2` command workers, `max = 4`, with a **`20 ms`** inter-frame delay). v2 enforces profile-specific worker caps and per-cover ordering of overlapping frames (see [runtime behaviour](../configuration/runtime_behaviour.md)) so `MyHomeServer1` stays responsive alongside the mobile app without exhausting its socket pool.

### 3. Core & Python Requirements
* **Change**: v2 requires Home Assistant Core ≥ 2026.8 running on Python 3.14+ (or compatible container). Legacy Python 3.11 environments cannot import the modern async core.

---

## 🧹 Step 4: What Happens to `/config/myhome.yaml`?

* **You do NOT need to delete `myhome.yaml` immediately**: The v2 runtime reads existing `myhome.yaml` files on startup to preserve custom entity names and map physical SCS lighting groups (`WHERE = #G`).
* **Transitioning to UI**: You can gradually move custom friendly names and Area assignments directly into the Home Assistant UI (**Settings → Devices & Services → Entities**). Once your entities are organized in the UI, `myhome.yaml` can be removed.
* **A copy is kept in the gateway's options**: the first start that reads a valid `myhome.yaml` section copies it into the gateway's config entry and raises the [Legacy YAML Configuration Imported](../diagnostics/repair-issues.md#legacy-yaml-configuration-imported) notice. The copy follows later edits of the file and takes its place once the file is removed or unreadable, so devices keep settings such as travel times and feature flags.

---

## ⏪ Rollback Procedure

If you encounter an unexpected issue after upgrading and wish to revert to v0.9.4:

1. Restore your pre-upgrade Home Assistant backup via **Settings → System → Backups**.
2. Alternatively, re-download the [v0.9.4 Release Archive](https://github.com/OpenWebNet-HA/MyHOME/releases/tag/0.9.4), extract it into `/config/custom_components/myhome/`, and restart Home Assistant.
