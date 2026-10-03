# Installation Guide (v2.0 Beta)

This guide covers installing the **MyHOME** next-generation integration (`v2-phase1-architecture`) in Home Assistant.

---

## Prerequisites

- **Home Assistant**: Home Assistant Core 2026.3 or newer.
- **Python Runtime**: Python 3.14+ (or official Home Assistant container).
- **Physical Gateway**: An OpenWebNet IP gateway (F454, MyHomeServer1, MH200N/201/202, F453AV) connected via local Ethernet, or a 3578 USB/RS232 interface.

---

## Option 1: Manual ZIP / SSH Installation (Recommended for v2 Beta)

> [!IMPORTANT]
> **Why Manual Installation is Recommended for v2 Beta**:
> The v2 architecture is currently published as preview/beta releases from the `v2-phase1-architecture` development branch. HACS tracks the default `master` branch by default and often fails to detect or rejects pre-release beta tags on non-default branches. Installing via ZIP ensures you receive the exact, tested beta build.

### ⚡ 1-Click Terminal Command (Home Assistant Terminal & SSH)

If you have the **Terminal & SSH** add-on installed (or connect via SSH), paste this command directly into your terminal:

```bash
# Set target beta release version (check https://github.com/OpenWebNet-HA/MyHOME/releases)
TAG="2.0.0b14"

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

### 📁 Alternative: Manual Download via Samba / Studio Code Server

1. Download the `myhome.zip` archive from the latest [v2.0 Beta Release](https://github.com/OpenWebNet-HA/MyHOME/releases).
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
> Always ensure integration files reside in `/config/custom_components/myhome/`. Never extract files directly into `/config/custom_components/` root, and never retain backup directories inside `custom_components/` (e.g. `custom_components/myhome_old/`). In modern Home Assistant cores, extra Python packages or stray `__init__.py` files in `custom_components/` can prevent all custom integrations from loading.

5. Restart Home Assistant:
   - Go to **Developer Tools → YAML → Restart** (or **Settings → System → Restart**).

---

## Option 2: Installation via HACS (Custom Repository)

If you prefer managing updates through HACS:

1. Open **HACS** in Home Assistant.
2. Click the three dots (⋮) in the top right and choose **Custom repositories**.
3. Enter the repository URL:
   ```text
   https://github.com/OpenWebNet-HA/MyHOME
   ```
4. Select category: **Integration**.
5. Click **Add**.
6. Search for **MyHOME**. If installing a beta release, toggle **Show beta versions** in the version dropdown, choose the latest `2.0.0bX` release, and click **Download**.
7. Restart Home Assistant.

---

## Next Steps

Once Home Assistant has restarted:

1. Proceed to [Gateways & Connection Setup](../configuration/gateways.md) to add your gateway via Config Flow.
2. If you are upgrading from an existing v0.9.4 installation, review the [Upgrade from 0.9.4 Guide](../migration/upgrade-from-094.md).
3. For uninstallation or clean removal, see the [Removal Guide](removal.md).
