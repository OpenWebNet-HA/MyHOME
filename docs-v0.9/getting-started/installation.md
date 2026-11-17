# Installation (v0.9.4 Legacy Archive)

This archived guide covers installing the legacy **v0.9.4** YAML release of the MyHOME integration for older Home Assistant installations (`2024.x – 2025.x`).

---

> [!WARNING]
> **Legacy Archive Notice**:
> For Home Assistant **2026.8 or newer**, install **MyHOME v2.0+** via the [**v2.0 Installation Manual**](https://openwebnet-ha.github.io/MyHOME/latest/getting-started/installation/). If you are upgrading an existing v0.9.4 setup, review the [**v2.0 Upgrade Guide**](https://openwebnet-ha.github.io/MyHOME/latest/migration/upgrade-from-094/) first.

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
