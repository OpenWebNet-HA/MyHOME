# MyHOME sidepanel — first version

The **MyHOME** sidebar is available to Home Assistant administrators after the
integration starts. It opens at `/myhome` and is installed with the integration;
no Lovelace dashboard resource or `panel_custom` YAML entry is needed for it.

The layout takes inspiration from ha-s7plc: a gateway overview, responsive card
grid, category filters, and editing dialogs. It uses Home Assistant theme colors
and provides English and Italian labels, with English fallback for other languages.

## Available now

- Select one gateway or view the whole installation. Gateway setup errors,
  retries, disabled entries, and lost connections remain visible.
- Browse devices and entities, including disabled entities and CEN/CEN+ devices
  which have device triggers but no entities.
- Search names, entity IDs, and the integration's OpenWebNet identifiers. Filter
  by entity category and area; entity area filtering respects device inheritance.
- Edit device/entity names and areas. An empty name restores the original name;
  an empty entity area inherits its device's area.
- Open the native device page, entity details, advanced entity settings, and
  the MyHOME integration settings page.
- Open the existing bus monitor for one selected, loaded gateway. Switching
  gateways creates a separate card instance and closes the previous stream.

Discovery and manual `myhome.yaml` configuration continue to provide the devices.
This first version does not implement an OpenWebNet device/address editor,
configuration import/export, or a replacement gateway Options Flow. The native
integration settings remain the place to add and configure gateways.

## Configuration ownership

| Information | Source / write API |
| --- | --- |
| Gateway connection configuration | Existing MyHOME Config Entry and Options Flow |
| Device names and areas | Home Assistant device registry, `config/device_registry/update` |
| Entity names and area overrides | Home Assistant entity registry, `config/entity_registry/update` |
| Entity values and availability | Home Assistant frontend state updates |
| Bus traffic | Existing `myhome/bus_monitor/*` APIs |

The only new backend command, `myhome/panel/inventory`, is an admin-only read
operation. It returns an explicit allowlist of gateway, device, entity, and area
fields, including IP/MAC information needed by administrators. It does not return
gateway passwords, full Config Entry data/options, or runtime objects.

There is no additional configuration file/store and no migration. Edits submit
only changed fields through native registry APIs. Changes made elsewhere are
reflected through registry events; gateway status also refreshes every 15 seconds.
The panel remains accessible while a gateway is unloaded or offline, and is
removed when the last gateway is deleted. Static routes are registered once and
can be reused after reload or reconfiguration. Frontend-less installations skip
sidebar registration.

The bus API now returns “not found” when an explicitly requested gateway does
not exist, rather than silently selecting another bus. Requests with no gateway
selection retain their existing default behavior.

## Try this branch

1. Install `custom_components/myhome` from `feat/myhome-sidepanel` over the
   integration files in a test Home Assistant instance.
2. Restart Home Assistant and refresh the browser page.
3. Sign in as an administrator and open **MyHOME** in the sidebar.
4. Compare the gateway/device/entity lists with Home Assistant's native settings.
5. Change a device name and area; verify them on its native device page. Change an
   entity name and area override, then clear the override to check inheritance.
6. Check an offline gateway and a disabled entity. Their configuration should
   remain visible and editable.
7. With two gateways, switch the bus monitor between them and confirm the title
   and traffic always match the selected gateway. Reload a gateway while the
   panel is open, then return to the monitor.
8. Check the dialog Save/Cancel buttons on desktop, a narrow viewport, and Safari.
   A wide touch desktop should keep the multi-column layout.

This branch is a first implementation for on-site testing, not a published release.
Visual validation within a real Home Assistant frontend and testing on a physical
gateway are still required.

## Development checks

Use the repository's Python test environment with the OWNd version required by
the manifest. The existing test bootstrap may install OWNd; if the exact engine
is already installed, `OWND_SMOKE_TEST=1` skips that installation bootstrap.

```sh
pytest tests/test_panel.py tests/test_websocket.py tests/test_init.py
npm ci
npm run test:panel
python scripts/verify_ha_standards.py
```

The frontend suite uses Node.js 24 and jsdom. It covers gateway/area/category
filtering, native writes, concurrent area changes, errors, escaping, live states,
and cleanup of delayed subscriptions. It runs separately in `panel-tests.yml`.
Browser layout and real hardware checks remain manual.

Validation during development on Home Assistant 2026.9.1: 47 Python checks passed
and four existing `test_init.py` checks failed on deprecated device-registry
access in the tests. Running `test_init.py` on the unchanged starting commit
`195b6acf9a4699ad35993d0d3fd9b320dfd47344` reproduced the same four failures.
All eight frontend tests passed.

On the available Home Assistant 2025.1.4 / Python 3.12 environment, the 25
panel/bus API checks not requiring an HTTP client also passed. The WebSocket
round-trip itself succeeded, but HTTP fixture teardown reported a lingering
`_run_safe_shutdown_loop` thread. A plain WebSocket ping test on the unchanged
starting commit reproduced this environment issue; it is not suppressed by the
new tests.
