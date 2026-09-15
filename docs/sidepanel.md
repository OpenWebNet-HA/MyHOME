# MyHOME sidepanel — first version

Home Assistant administrators can open **Configure → MyHOME panel** from a
gateway integration entry, then follow **Open the MyHOME panel**. The link selects
that gateway using `/myhome?entry_id=<config entry ID>`. **Gateway settings** in
the same Configure menu retains the existing connection and decoder Options Flow.

The **MyHOME** sidebar shortcut is shown by default for compatibility. The panel
options page can hide or show it for the whole installation, across all gateways
and administrators. Hiding it leaves the panel URL and Configure link available;
it does not unload a gateway. The panel is installed with the integration;
no Lovelace dashboard resource or `panel_custom` YAML entry is needed for it.

The layout takes inspiration from ha-s7plc: a gateway overview, responsive card
grid, category filters, and editing dialogs. It uses Home Assistant theme colors
and provides English and Italian labels, with English fallback for other languages.

## Panel versioning

The panel has an independent version, currently **0.7.1**, defined by
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
  system, and WHO 18 Energy management. Devices without entities appear in their
  WHO category in this same view.
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
- Within each WHO category, entities are grouped by native device and gateway.
  The device header shows its name, area and shared address once, followed by
  compact entity rows with individual states and actions. Distinct addresses
  and entity area overrides remain visible. Entities without a device are kept
  in a separate group. Search also matches device names; group counts reflect
  the entities matching the active filters.
- Devices start collapsed. Click a device header to expand or collapse its entity list. The selection
  survives inventory refreshes, filtering and switching to the bus monitor
  during the current panel visit. Device editing and the native device link
  remain in the header. This unified view also includes devices without entities
  (for example CEN triggers), so there is no separate Devices tab.
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
| Sidebar shortcut visibility | HA storage `myhome_panel`, native Options Flow |
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

Gateway and device configuration is not duplicated and needs no migration. The
only extra HA-managed store, `myhome_panel` (version 1), contains the global
`show_sidebar` presentation preference. It is saved by the native Options Flow
and retained across restarts and gateway deletion/recreation. WHO view preferences
remain browser-local. Edits submit
only changed fields through native registry APIs. Changes made elsewhere are
reflected through registry events; gateway status also refreshes every 15 seconds
while the browser tab is visible. Background tabs suspend inventory polling and
debounced registry refreshes, then reconcile immediately when visible again.
An active bus stream remains connected while the tab is hidden so its capture
continues. Leaving the bus section or disconnecting the panel closes that stream.
The panel remains accessible while a gateway is unloaded or offline, and is
removed when the last gateway is deleted. Static routes are registered once and
can be reused after reload or reconfiguration. Frontend-less installations skip
sidebar registration.

On its first load, the panel also handles HA properties assigned before its
custom element is defined. Once the JavaScript finishes loading, those values
are replayed through the component setters so the inventory starts immediately.
This avoids a blank first visit that previously required navigating away and back.

All five bus monitor WebSocket commands (`history`, `stream`, `send`, `clear`,
`info`) now require an administrator, enforced by Home Assistant before gateway
lookup or any bus/buffer access. This also applies to the standalone Lovelace
monitor card: non-admin accounts receive `unauthorized`. Normal Home Assistant
entity controls are unaffected.

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
3. Sign in as an administrator and open **Configure → MyHOME panel** on a gateway,
   then follow its panel link. Verify that this gateway is selected. Hide the
   sidebar shortcut, reopen through Configure, and restart HA to confirm the
   choice persists. Re-enable it from either gateway’s panel options.
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

Panel 0.7.0 adds the Configure menu, gateway-specific links and the shared sidebar
preference. A link to a removed gateway shows an error and an empty inventory;
it never silently switches to another gateway. Selecting a gateway updates the
URL, and browser navigation or another Configure link updates the existing panel.

Validation for 0.7.0 on Home Assistant 2025.1.4 / Python 3.12 with OWNd 2.0.0b6:
1,312 Python tests passed, 1 skipped, five snapshots passed, and all 26 integration
modules reached 100% line coverage (5,273 statements). All 18 frontend tests passed,
including native-property upgrade, gateway links and navigation. Ruff and the HA
architectural checks passed. The Configure link and sidebar visibility still need
a visual check in a real Home Assistant frontend.

## CI alignment with the current architecture branch

The sidepanel branch includes `v2-phase1-architecture` through `fea3764` (PR #316).
The gateway-model options regression now enters the native Configure menu before
opening Gateway settings, while retaining model, title and reload assertions.
The new panel labels are also included in upstream's `strings.json`.

PR #316 supplies anonymized plant fixtures, their sanitizer and privacy checks.
The architecture and replay tests now use those upstream fixtures, including all
four capture-specific replay cases. The temporary small synthetic fixture and
its conditional skips have been removed. The Configure-menu regression still
checks the panel's native navigation before testing gateway settings.

Validation after merging PR #316 on HA 2025.1.4 / Python 3.12.14 / OWNd 2.0.0b6:
**1,359 backend tests passed, one existing skip**, five snapshots passed and
**100% line coverage** across all 28 integration modules (5,477 statements).
All **22 frontend tests** and Ruff on the resolved tests pass. The panel remains
at 0.7.1: this merge changes test fixtures and documentation, not its runtime code.


## Panel 0.7.1: foundation for advanced sections

- Preserve keyboard focus on the same inventory action across registry refreshes,
  including devices shared across gateways or WHO sections. Removed actions do
  not transfer focus to another device. Open editor drafts keep their focus.
- Pause background inventory refreshes and reconcile on return. Connection changes
  invalidate outstanding requests and callbacks; remounting starts one set of
  subscriptions.
- Enforce administrator access on all bus monitor WebSocket endpoints.
- Move bus rendering and stream lifecycle into `panel-bus-monitor.js`, with shared
  escaping/focus helpers in `panel-dom.js`. All assets retain the panel's version
  and content hash. The shell still owns navigation, gateway selection and HA
  connection changes; the bus adapter owns only the selected monitor instance.

Validation for 0.7.1 before the PR #316 merge: Home Assistant 2025.1.4, Python 3.12.14, OWNd 2.0.0b6,
pytest-asyncio 0.24.0. The full backend suite passes **1,344 tests**, with the same
five skips (four unavailable capture fixtures and one existing skip), five
snapshots and **100% line coverage** across all 28 modules (5,477 statements).
All **22 frontend tests**, Ruff and the architectural validator pass. The new
WebSocket tests authenticate real admin/read-only clients over loopback and
verify that denied commands have no bus or buffer side effects. Frontend tests
cover focus, hidden-tab refreshes, reconnects and delayed bus-module loading.
Real HA browser behavior and physical gateways still need manual validation.

### Contract for the next sections (design, not implemented APIs)

The target is one MyHOME panel with inventory, bus diagnostics and cover
profiles/calibration sections. This patch does not add cover profile endpoints,
new hardware probes, or calibration commands.

Each advanced section should follow the bus adapter's ownership boundaries:

1. Receive the selected gateway explicitly from the shell. A write needs exactly
   one config entry and must fail if it is unavailable or no longer exists; it
   must never fall back to another gateway. The legacy bus API still uses a MAC,
   translated from the selected entry by its adapter.
2. Use the current HA connection. A gateway/connection change or unmount must
   invalidate pending reads, subscriptions and previews. Closing a section only
   cancels its UI work; it must not imply that an already accepted backend write
   or physical movement was undone.
3. Keep gateway configuration in the existing Config Entry/Options Flow and
   names/areas in native registries. The backend owns profile validation,
   persistence, application and revision numbers. The frontend owns selection,
   draft values and preview presentation.
4. Add administrator checks and gateway/device validation to each backend command,
   independently of the panel route. Return structured results/errors and expose
   only fields needed by that section.
5. For batch profile changes, preview and validate the entire proposed mutation
   before saving. Send assignments and ordering together with an expected
   revision, and commit them atomically. Reject stale revisions. An undo request
   must also check the current revision before restoring previous values.
6. Distinguish stored profile changes from physical calibration. A preview must
   not move covers. Use the existing calibration Options Flow until its backend
   behavior has a supported panel API; avoid private frontend dialog internals.
   Show per-device outcomes for physical operations, which cannot be described
   as an atomic storage transaction.

Reuse and adapt the cover-profile backend after checking these guarantees, then
add its UI inside this shell. WHO 1004/1018 hardware diagnostics need validated
captures and supported OWNd decoding before exposing probe controls. Retire the
standalone bus card only once its current inspection, filtering, sending, sweep
and export functions are available and verified inside the panel; the adapter
allows that migration without coupling the other sections to card internals.
