# MyHOME sidepanel — first version

The **MyHOME** sidebar is available to Home Assistant administrators after the
integration starts. It opens at `/myhome` and is installed with the integration;
no Lovelace dashboard resource or `panel_custom` YAML entry is needed for it.

The layout takes inspiration from ha-s7plc: a gateway overview, responsive card
grid, category filters, and editing dialogs. It uses Home Assistant theme colors
and provides English and Italian labels, with English fallback for other languages.

## Panel versioning

The panel has an independent version, currently **0.4.3**, defined by
`PANEL_VERSION` in `custom_components/myhome/panel.py`. Its version appears under
the MyHOME header; the integration version is shown separately at the bottom.
The label uses the version of the JavaScript module actually loaded by the tab.

Every panel change should increment this version (patch for fixes, minor for new
features during the preview). The module URL includes both `v=<panel version>`
and `build=<bundle content hash>`; imported JavaScript and CSS retain both query
parameters. This refreshes assets independently from integration releases.

## Available now

- Select one gateway or view the whole installation. Gateway setup errors,
  retries, disabled entries, and lost connections remain visible.
- Browse devices and entities, including disabled entities and CEN/CEN+ devices
  which have device triggers but no entities.
- The initial **Entities** view groups cards into numbered WHO sections, for
  example WHO 1 Lighting, WHO 2 Automation, WHO 4 Thermoregulation, WHO 16 Sound
  system, and WHO 18 Energy management. The **Devices** view uses the same grouping.
  Each section shows its item count.
- WHO buttons above the lists open the selected category directly. **Show all**
  restores all WHO sections; **Show selected category** returns to the last
  selection. The initial layout shows all categories, and the browser remembers
  the chosen layout and WHO. These preferences do not change HA configuration.
  Buttons wrap on wide screens and scroll horizontally on narrow windows (up to
  900 px), including by touch swipe. The entity-type, area and search filters
  remain active in both layouts. A category with no filter matches stays selected
  and shows the empty state; if it disappears from the gateway/view inventory,
  the panel selects the first available WHO. Category controls are hidden in the
  bus monitor and when no categories are available.
- Search names, entity IDs, and the integration's OpenWebNet identifiers. Filter
  by entity type and area; entity area filtering respects device inheritance.
- Every device/entity card includes its recorded address. Point-to-point lighting,
  automation and CEN addresses also show **A** and **PL**, plus the bus interface
  when present. Leading zeros remain significant: `15` is A `1` / PL `5`, whereas
  `0015` is A `00` / PL `15`. Search accepts full addresses and `A:00 PL:15`.
  Short area-zero addresses already accepted by the integration also show their
  parts: `01` → A `0` / PL `1`, `02` → A `0` / PL `2`, through `09`.
- Edit device/entity names and areas. An empty name restores the original name;
  an empty entity area inherits its device's area.
- Open the native device page, entity details, advanced entity settings, and
  the MyHOME integration settings page.
- Open the existing bus monitor for one selected, loaded gateway. Switching
  gateways creates a separate card instance and closes the previous stream.
  **Sweep Bus** queries only that gateway; **Export Trace** downloads the trace
  displayed in its monitor as JSON.

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
| WHO and recorded address / A / PL / interface | MyHOME identifiers in the native device registry; read only |
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

On its first load, the panel also handles HA properties assigned before its
custom element is defined. Once the JavaScript finishes loading, those values
are replayed through the component setters so the inventory starts immediately.
This avoids a blank first visit that previously required navigating away and back.

The bus API now returns “not found” when an explicitly requested gateway does
not exist, rather than silently selecting another bus. Requests with no gateway
selection retain their existing default behavior.

WHO classification comes from each device's canonical `MAC-WHO-device` registry
identifier, including when the gateway is offline. It is not inferred from the
Home Assistant entity type or from legacy sensor entity IDs, which may omit WHO.
Unclassified/ambiguous items remain visible in **No WHO category**; newly seen WHO
numbers remain visible even if no translated label has been added yet.

Address metadata uses the same native device identifiers, so it also works for
disabled entities, offline gateways and auxiliary lock/unlock buttons. A repeated
WHO in YAML-generated device keys is removed once; the WHO 16 media-player `#16`
registry suffix is not part of its zone address. Ambiguous, missing and unsupported
identifiers display **Address: Not available**, without guessing from entity IDs.
Entities use the identifiers matching their own gateway MAC. Shared devices with
different addresses across gateways show an unknown device address; their entities
retain the address for their respective gateway.

A/PL splitting is limited to WHO 1, 2, 14, 15 and 1001. For display, the panel
accepts `01`–`09` in addition to the existing `is_apl_address` formats, matching
addresses already accepted by the integration. It preserves the raw address and
does not change discovery, validation or commands. `00` remains an area address;
WHO 4 zones and WHO 25 objects remain unsplit even when they contain `01`–`09`.
Other categories, general/area/group commands, and unrecognized address formats
retain their recorded address without invented A/PL fields. The canonical
protocol formats are documented in the Legrand
[lighting/actuator addressing](https://static.developer.legrand.com/files/2024/05/WHO_1.pdf)
and [CEN/CEN+ specifications](https://developer.legrand.com/uploads/2019/12/WHO_15-25.pdf).
Recorded CEN+ object IDs are displayed as addresses, not expanded into wire frames.

## Try this branch

1. Install `custom_components/myhome` from `feat/myhome-sidepanel` over the
   integration files in a test Home Assistant instance.
2. Restart Home Assistant and refresh the browser page.
3. Sign in as an administrator and open **MyHOME** in the sidebar.
   On the first visit after a browser reload, check that the inventory appears
   without navigating away and back.
4. Check the panel version in the header, then compare the gateway/device/entity
   lists with Home Assistant's native settings. Verify WHO grouping, especially
   temperature sensors (WHO 4), energy sensors (WHO 18), and CEN devices (WHO 15/25).
   Select a WHO button, switch between **Show all** and **Show selected category**,
   and reopen the panel to verify the saved preference. Check the category row
   on a wide desktop and a narrow touch screen.
5. Change a device name and area; verify them on its native device page. Change an
   entity name and area override, then clear the override to check inheritance.
6. Check an offline gateway and a disabled entity. Their configuration should
   remain visible and editable. Compare address/A/PL/interface values on a device
   and its entities, including `0015`, `15`, and an address routed through `#4#02`.
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

The frontend suite uses Node.js 24 and jsdom. It covers gateway/area/type/WHO
filtering, WHO grouping/navigation, layout preferences, panel version display,
address rendering/search, native writes, concurrent area changes, errors,
escaping, live states, and cleanup of delayed subscriptions.
It runs separately in `panel-tests.yml`.
Browser layout and real hardware checks remain manual.

Panel 0.4.2: all 29 panel/WebSocket tests and 13 frontend tests passed. The address
regression covers `01`–`09`, routed addresses and extended `0001`, while retaining
unsplit area/group addresses, WHO 4 zones and WHO 25 objects.

Panel 0.4.1: all 13 frontend tests passed. The isolated startup regression first
reproduced the empty first mount on 0.4.0, then passed with the fix. It covers
properties set before definition, detached elements, delayed HA data, subsequent
state/menu updates, and disconnect/reconnect without duplicate subscriptions.

Panel 0.4.0 validation on Home Assistant 2026.9.1: all 29 panel/WebSocket tests
passed, including WHO/address metadata for legacy sensor IDs, offline gateways,
bus interfaces and ambiguous identifiers. All twelve frontend tests passed,
including address rendering/search, category navigation, layout and selection
persistence, changing inventories, and blocked browser storage.
The changed Python files pass Ruff.

Initial implementation validation on Home Assistant 2026.9.1: 47 Python checks passed
and four existing `test_init.py` checks failed on deprecated device-registry
access in the tests. Running `test_init.py` on the unchanged starting commit
`195b6acf9a4699ad35993d0d3fd9b320dfd47344` reproduced the same four failures.
All eight frontend tests available at that point passed.

For the initial implementation on Home Assistant 2025.1.4 / Python 3.12, the 25
panel/bus API checks not requiring an HTTP client also passed. The WebSocket
round-trip itself succeeded, but HTTP fixture teardown reported a lingering
`_run_safe_shutdown_loop` thread. A plain WebSocket ping test on the unchanged
starting commit reproduced this environment issue; it is not suppressed by the
new tests.
