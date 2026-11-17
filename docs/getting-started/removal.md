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

## Step 2: Automatic Storage & Repair Cleanup

Deleting the MyHOME integration entry in Step 1 cleans up persistent integration state automatically (`async_remove_entry`):

- **Cover calibration times (`cover_travel_times`) & imported YAML snapshots**: Stored directly inside the gateway config entry's `options` and deleted by Home Assistant as part of removing the config entry.
- **Decoder pool state (`.storage/myhome.decoder_pool.<entry_id>`)**: Automatically removed by `async_remove_entry`.
- **Integration Repair Issues**: All `myhome` domain Repair Issues linked to that gateway entry are automatically cleared from Home Assistant's Issue Registry (and any remaining secondary/standby gateways linked to a removed primary are flagged automatically).

No manual deletion inside `/config/.storage/` is required. *(If you previously ran an early pre-v2 build and want to verify no orphaned files remain, you can inspect `ls -la /config/.storage/myhome.*` via SSH or Terminal.)*

---

## Step 3: Lovelace Bus Monitor Card Resource Cleanup

In v2, the bundled Bus Monitor card is served from `/myhome_static/myhome-bus-card.js?v=...` and its Lovelace dashboard resource is **removed automatically** when you delete the final MyHOME gateway entry.

1. Remove any `custom:myhome-openwebnet-bus-monitor` (or `custom:myhome-bus-card`) cards from your Lovelace dashboard views.
2. Optionally open **Settings → Dashboards → Three dots (⋮) in top right → Resources** to confirm no `/myhome_static/myhome-bus-card.js` resource remains.
3. If you manually copied a pre-v2 standalone card into `/config/www/` (`/local/myhome-bus-card.js`), delete that manual resource entry in **Resources** and remove the file:
   ```bash
   rm -f /config/www/myhome-bus-card.js
   ```

---

## Step 4: Remove Integration Files & Custom Component

To completely remove the integration code from Home Assistant:

1. **If installed via Home Assistant Marketplace (2026.11+) or HACS (`< 2026.11`):**
    - In **Settings → Marketplace** (`2026.11+`), open **MyHOME**, open the three dots (`⋮`) menu in the upper-right corner, and select **Uninstall**.
    - In **HACS** (`< 2026.11`), open **MyHOME**, open the three dots (`⋮`) menu, and select **Remove**.
2. **If installed manually or via SSH one-liner:** delete the custom component folder using your terminal or file manager:
    ```bash
    rm -rf /config/custom_components/myhome
    ```
3. If you maintain a legacy `/config/myhome.yaml` file, you can safely archive or delete it:
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
