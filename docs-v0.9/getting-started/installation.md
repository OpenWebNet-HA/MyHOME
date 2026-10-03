# Installation (v0.9.4 Production Release)

This guide covers installing the stable **v0.9.4** release of the MyHOME integration from the `master` branch.

---

> [!WARNING]
> **Production Stability Notice**:
> If you are running a stable production home automation setup, **stay on release 0.9.4** on the `master` branch. **Do not install the v2 beta zip** on a production system without first reviewing the [**v2 Upgrade Guide**](../../beta/migration/upgrade-from-094/).

---

## Prerequisites

- **Home Assistant**: Compatible with Home Assistant 2024.x through 2025.x.
- **OpenWebNet Gateway**: Connected to your local LAN with a fixed IP address.
- **Configuration File**: A dedicated `/config/myhome.yaml` file (or `configuration.yaml` include).

---

## Option 1: Installation via HACS (Recommended)

HACS provides the easiest installation method for production v0.9.4:

1. Open **HACS** from your Home Assistant sidebar.
2. Navigate to **Integrations**.
3. Search for **MyHome**.
4. Click **Download** and select version **`0.9.4`**.
5. Restart Home Assistant:
   - Navigate to **Developer Tools** → **YAML** → **Restart**.

---

## Option 2: Manual ZIP Installation

1. Download the `myhome-0.9.4.zip` asset from the [v0.9.4 GitHub Release](https://github.com/OpenWebNet-HA/MyHOME/releases/tag/0.9.4).
2. On your Home Assistant host, open your configuration folder (`/config`).
3. Ensure the folder `/config/custom_components/myhome/` exists.
4. Extract the release archive into `/config/custom_components/myhome/`.
5. Restart Home Assistant.

---

## Next Steps

After restarting Home Assistant, proceed to the [**Configuration Overview**](../configuration/index.md) to set up your `/config/myhome.yaml` file with your gateway IP, MAC address, and device definitions.
