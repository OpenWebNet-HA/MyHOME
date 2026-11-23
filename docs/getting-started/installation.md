# Installation Guide

This guide covers installing and updating the **MyHOME** integration in Home Assistant.

---

## Prerequisites

- **Home Assistant**: Home Assistant Core 2026.8 or newer.
- **Python Runtime**: Python 3.14+ (or official Home Assistant OS / Container).
- **Physical Gateway**: An OpenWebNet IP gateway (F454, F455, F461, MyHomeServer1, MH200, MH200N, MH201, MH202, H4890/AM4890, H/L4684, F452/F453/F453AV) connected via local Ethernet.

---

## Option 1: Home Assistant Marketplace / HACS (Recommended for Stable Releases)

From **Home Assistant 2026.11** on, HACS is integrated directly into Home Assistant Core as the **Home Assistant Marketplace** (**Settings → Marketplace**); existing HACS installations and custom repositories migrate automatically on upgrade. On earlier versions (**Home Assistant 2026.8 – 2026.10**), the same steps apply in **[HACS](https://hacs.xyz)**.

- **Home Assistant 2026.11+ (Marketplace):**
    1. Open **Settings → Marketplace** in Home Assistant *(accept the community content warning if opening it for the first time)*.
    2. Search for **MyHOME** in the catalog, or if adding manually before catalog publication, select **Add from link** in the upper-right corner, enter `https://github.com/OpenWebNet-HA/MyHOME`, and select **Add** *(if prompted for **Type**, select **Integration** and select **Add** again)*.
    3. Select **MyHOME** to open its page, then select **Install** *(to pick a specific release, open `⋮` → **Install another version**)*.
    4. Restart Home Assistant when prompted.
- **Home Assistant 2026.8 – 2026.10 (HACS):**
    1. Open **HACS** in Home Assistant.
    2. Search for **MyHOME**, or click the three dots (`⋮`) in the top right → **Custom repositories** and add `https://github.com/OpenWebNet-HA/MyHOME` with category **Integration**.
    3. Open **MyHOME**, select the desired release version, and click **Download**.
    4. Restart Home Assistant when prompted.

> [!NOTE]
> **Installing `2.0.0bX` Pre-Releases Prior to Stable `v2.0.0`:**
> Pre-release `2.0.0bX` builds published ahead of the `v2.0.0` merge on `master` should be installed via **Option 2 (Terminal & SSH or Manual Archive)** below, as Marketplace and HACS track the default branch by default.

---

## Option 2: Terminal & SSH or Manual ZIP Archive

### ⚡ 1-Click Terminal Command (Home Assistant Terminal & SSH)

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

---

### 📁 Manual Download via Samba / Studio Code Server

1. Download the `myhome.zip` archive from [GitHub Releases](https://github.com/OpenWebNet-HA/MyHOME/releases).
2. Connect to your Home Assistant host using **SSH**, **Samba share**, or the **Studio Code Server** add-on.
3. Open the Home Assistant configuration folder (the directory containing `configuration.yaml`).
4. Ensure the `custom_components/` folder exists, and extract the archive so that files are placed at:
   ```text
   /config/custom_components/myhome/__init__.py
   /config/custom_components/myhome/manifest.json
   /config/custom_components/myhome/config_flow.py
   ...
   ```

> [!CAUTION]
> **Folder Structure Warning**:
> Always ensure integration files reside in `/config/custom_components/myhome/`. Never extract files directly into `/config/custom_components/` root, and never retain backup directories inside `custom_components/` (e.g. `custom_components/myhome.backup` or `custom_components/myhome_old/`). Home Assistant scans every subdirectory under `custom_components/`, so stray backup folders inside `custom_components/` can prevent custom integrations from loading. Keep any safety backups outside (e.g. `/config/myhome_backup/`).

5. Restart Home Assistant (**Settings → System → Restart**).

---

## Next Steps

Once Home Assistant has restarted:

1. Proceed to [Gateways & Connection Setup](../configuration/gateways.md) to add your gateway via Config Flow.
2. If you are upgrading from an existing v0.9.4 installation, review the [Upgrade from 0.9.4 Guide](../migration/upgrade-from-094.md).
3. For uninstallation or clean removal, see the [Removal Guide](removal.md).
