# Removal & Clean Uninstall Guide

This guide details how to completely remove the **MyHOME** integration and clean up all associated entities, dashboard cards, and persistent storage files from Home Assistant.

---

## Step 1: Remove Gateway Integration Entry

1. In Home Assistant, navigate to **Settings → Devices & Services → Integrations**.
2. Locate the **MyHOME** integration card.
3. Click the three dots (⋮) on the integration entry and select **Delete**.
4. When prompted to confirm deletion, click **Delete**.
5. Home Assistant will remove the gateway connection, terminate background event listening sessions, and remove all associated discovered devices from the Device Registry.

---

## Step 2: Clean Up Storage & Persistent Data

The MyHOME integration stores non-volatile calibration data (such as measured cover travel times and device options) in Home Assistant's internal storage:

1. Connect to your Home Assistant host via SSH, Terminal, or the Studio Code Server addon.
2. Navigate to your configuration directory:
   ```bash
   cd /config/.storage
   ```
3. Remove any integration storage files:
   ```bash
   rm -f myhome.*
   ```
   *(This removes stored cover travel time calibrations and cached hardware profiles).*

---

## Step 3: Remove Custom Lovelace Bus Monitor Card (If Installed)

If you installed the custom Lovelace Bus Monitor card (`myhome-bus-card`):

1. Navigate to **Settings → Dashboards → Three dots (⋮) in top right → Resources**.
2. Locate the resource referencing:
   ```text
   /local/myhome-bus-card.js
   ```
3. Click on the resource and select **Delete**.
4. In your Home Assistant `/config/www/` folder, remove the card JavaScript file if present:
   ```bash
   rm -f /config/www/myhome-bus-card.js
   ```

---

## Step 4: Remove Integration Files & Custom Component

To completely remove the integration code from Home Assistant:

1. Using your terminal or file manager, delete the custom component folder:
   ```bash
   rm -rf /config/custom_components/myhome
   ```
2. If you maintain a legacy `/config/myhome.yaml` file, you can safely archive or delete it:
   ```bash
   rm -f /config/myhome.yaml
   ```

---

## Step 5: Restart Home Assistant

Restart Home Assistant to flush cached Python bytecode and finalize clean removal:
* Navigate to **Settings → System → Restart**, or run:
  ```bash
  ha core restart
  ```
