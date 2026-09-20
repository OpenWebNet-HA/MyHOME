# Upgrading from v0.9.4 to v2.0 (Beta)

This guide provides a step-by-step roadmap for migrating an existing **MyHOME v0.9.4** production plant to the modern **v2.0 architecture** (`v2-phase1-architecture`).

---

## 🧭 What Changes in v2.0?

| Aspect | Legacy v0.9.4 | Next-Gen v2.0 (Beta) |
| :--- | :--- | :--- |
| **Setup Model** | Manual YAML (`/config/myhome.yaml`) | **100% UI-First** (Config Flow & Auto-Discovery) |
| **Entity Discovery** | Strict YAML parsing at startup | Dynamic on-wire SCS bus discovery (`WHO = 1, 2, 4, 13, 15, 18, 25`) |
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

## 📦 Step 2: Installing the v2.0 Beta Release

### Why Manual ZIP / SSH is Recommended for Beta
While stable releases (like v0.9.4) are distributed through standard HACS tracks on `master`, v2.0 is currently developed on the `v2-phase1-architecture` branch. HACS often fails to track non-default development branches or rejects pre-release beta builds.

### Installation via ZIP Archive

#### ⚡ 1-Click Terminal Command (Home Assistant Terminal & SSH)
If you have the **Terminal & SSH** add-on installed (or connect via SSH), paste this command directly into your terminal:

```bash
# Set target beta release version (check https://github.com/OpenWebNet-HA/MyHOME/releases)
TAG="2.0.0b13"

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

#### 📁 Alternative: Manual Download via Samba / Studio Code Server
If you prefer not using the command line:

1. Download `myhome.zip` from the latest [v2.0 Beta GitHub Release](https://github.com/OpenWebNet-HA/MyHOME/releases).
2. Connect to your Home Assistant host via **Samba Share** or the **Studio Code Server** add-on.
3. Navigate to `/config/custom_components/myhome/` (create the folders if they don't exist).
4. Extract the contents of `myhome.zip` directly into `/config/custom_components/myhome/`.
5. Verify that `manifest.json` is located at `/config/custom_components/myhome/manifest.json`.
6. Restart Home Assistant (**Settings → System → Restart** or **Developer Tools → YAML → Restart**).

---

## 🔌 Step 3: Gateway Setup & Device Onboarding

1. Once Home Assistant restarts, navigate to **Settings → Devices & Services**.
2. If your gateway is discovered automatically via SSDP/mDNS, click **Configure**.
3. Otherwise, click **Add Integration**, search for **MyHOME**, and enter:
   - **Host**: IP address of your gateway.
   - **Port**: `20000`.
   - **Password**: OpenWebNet password (or HMAC password for MyHomeServer1).
4. Home Assistant connects to the gateway, starts the command and event sessions, and automatically discovers your connected bus actuators and sensors.

### Are Existing Entity IDs Preserved?
**Yes.** Entity unique IDs in v2.0 are deterministically derived from the gateway MAC address and OpenWebNet address (`<mac>-<who>-<where>`). Existing dashboards, automations, and entity IDs (`light.living_room`, `cover.kitchen_shutter`) are preserved seamlessly.

---

## ⚠️ Known Breaking Changes & Upstream Fixes

Before upgrading, review these key architectural changes:

### 1. Gateway Identity Strict Verification (`#370`)
* **Change**: v2 strictly queries and validates the gateway MAC address and hardware model via `WHO = 13` dimension queries.
* **Impact**: If a gateway was previously configured with an incorrect or spoofed MAC address in `myhome.yaml`, v2 creates a Home Assistant **Repair Issue** alerting you to update the configuration to match the true hardware identity.

### 2. MyHomeServer1 Session Management (`#397`)
* **Change**: MyHomeServer1 firmware enforces single-command session limits. v2 introduces dedicated session serialization and worker pacing specifically tailored for Linux-based MHS1 gateways to prevent session lockups.

### 3. Core & Python Requirements
* **Change**: v2 requires Home Assistant Core ≥ 2026.3 running on Python 3.14+ (or compatible container). Legacy Python 3.11 environments cannot import the modern async core.

---

## 🧹 Step 4: What Happens to `/config/myhome.yaml`?

* **You do NOT need to delete `myhome.yaml` immediately**: The v2 runtime reads existing `myhome.yaml` files on startup to preserve custom entity names and map physical SCS lighting groups (`WHERE = #G`).
* **Transitioning to UI**: You can gradually move custom friendly names and Area assignments directly into the Home Assistant UI (**Settings → Devices & Services → Entities**). Once your entities are organized in the UI, `myhome.yaml` can be removed.

---

## ⏪ Rollback Procedure

If you encounter an unexpected issue during beta testing and wish to revert to v0.9.4:

1. Restore your pre-upgrade Home Assistant backup via **Settings → System → Backups**.
2. Alternatively, re-download the [v0.9.4 Release Archive](https://github.com/OpenWebNet-HA/MyHOME/releases/tag/0.9.4), extract it into `/config/custom_components/myhome/`, and restart Home Assistant.
