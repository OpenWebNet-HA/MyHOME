# MyHOME sidepanel — first version

> Current update — panel 0.30.0: [optional slat/roll profiles and runtime](cover-nonlinear-runtime.md)
> add storage v8, export v5, explicit geometry, model-aware previews and motor
> tracking. Timing-only profiles remain linear; guided geometry measurement and
> physical accuracy checks are still pending. Older release notes below are historical.


Panel **0.29.0** adds optional cover travel and profile reference travel within the
existing sections. Both dimensions enable linear timing adaptation; personal
values retain priority. See [setup, migration and installation checks](cover-travel-scaling.md).

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

## Cover flow review (0.27.1)

This maintenance update preserves the current WHO 2 layout and calibration steps.
It fixes four issues found while reviewing the device editor and profile catalogue:

- Calibration mode and direction are part of the unsaved draft. A remote profile
  update cannot silently reset a selected single-direction or automatic mode.
- Cover availability transitions trigger a backend read. Even at the same saved
  revision, a pristine editor reflects updated writability. An edited form retains
  its draft, hides invalid previews and requires explicit reload before writing.
- Multi-cover assignment search accepts multiple name/address terms and both
  `A:1 PL:2` and `A: 1 PL: 2`, independently of rendered address markup.
- Enter in the assignment search field only stays in search; confirmation uses
  the explicit action button. Filtering retains the selection and valid preview.

No storage migration, API or timing changes are required. See the
[review record](cover-flow-review.md) for scope, findings and validation.

## Assign a profile to multiple covers (0.27.0)

Expanded WHO 2 profile cards offer **Assign to covers** in the existing profile
management dialog. Select covers from that profile's gateway, search by name or
A-PL, and preview the complete selection before confirming once. Already assigned
covers remain marked and disabled. Unavailable, disabled, advanced-position and
calibrating covers show why they cannot be selected. The selection starts empty;
filtering the list retains checked covers and their count.

The preview shows each cover's current profile, destination profile and configured
opening/closing times before and after assignment. Personal directional values
remain in place, with an explicit indication when they mask the profile. Changing
the selection invalidates the preview. Covers not selected retain their current
assignments; this action does not remove associations or edit the profile itself.

The backend validates the entire selection (1–200 distinct timed covers on the
same loaded gateway) during both preview and confirmation. One unavailable,
removed, disabled, advanced or calibrating member rejects the entire request.
Confirmation binds the exact selection, registry identities, resolved values and
saved revision. A concurrent change requires a fresh preview. All associations
are saved in one atomic transaction and publish one revision; a failed save
publishes none. Moving covers apply pending timing only after stopping. No bus
movement commands are sent. Storage remains v6 and export v3.

## Profile management (0.26.0)

Expanded WHO 2 profile cards now offer **Edit**, **Duplicate** and **Delete**.
They open a gateway-scoped dialog, independent of any selected or available cover.
The cover editor and calibration entry points are unchanged.

- **Edit**: change the name and opening/closing times, preview all affected covers,
  then confirm that exact proposal. Personal directional overrides remain in place.
  Moving covers retain their current runtime timing until stopped; unavailable and
  missing followers are included in the impact list. Even an unused profile can be
  edited. Changed times become manual entries dated by the backend with no invented
  origin cover. Renaming or keeping a time preserves its original evidence.
- **Duplicate**: choose a name for an independent copy of the saved times/evidence.
  The copy is unassigned; the source and all existing assignments remain untouched.
- **Delete**: available only for unused profiles and requires a separate confirmation.
  The backend checks every stored association again, including orphan references.

All mutations require administrator access and an exact saved revision, use the
same gateway transaction/atomic storage and reject a reserved panel calibration.
Shared updates also reject native calibration on affected covers. A failed save
keeps the editor draft. Concurrent changes invalidate preview/confirmation and
require an explicit reload; closing a dialog does not roll back an accepted write.
No operation sends movement commands.

### Storage v6 and rollback

Version 6 permits a manual profile value with `origin_unique_id: null`. Guided and
automatic evidence still require an origin. This avoids fabricating a cover or
misrepresenting a manual edit as a physical measurement. Export remains v3 with
its existing nullable origin reference; no artificial cover record is exported.

On first load, the backend validates the complete v5 payload, saves an exact v5
copy to `.storage/myhome.cover_profiles.<entry_id>.pre_catalogue`, then migrates
without changing profiles, assignments, overrides, native fallbacks or revision.
A failed backup prevents migration. Older v1–v4 stores retain the existing
`.pre_shared` backup and migration path. Entry removal deletes both backup files.

Older panel builds using storage v5 cannot read v6. To roll back, stop Home
Assistant, restore the pre-update backup (including its v5 version metadata) as
`.storage/myhome.cover_profiles.<entry_id>`, install the older integration and
restart. That backup is the pre-upgrade snapshot: later edits are not included.
Keep an independent installation backup when switching versions.

## Shared profile view (0.25.0)

WHO 2 now has a local **Devices | Profiles** selector. Devices remains the default
view and still opens each cover's existing editor/calibration directly. Profiles
uses the same section shell, responsive card grid and collapsed-by-default cards.

Each card shows its name, opening/closing times and complete association count.
Expanding it shows the source/date of each directional value and all associated
covers, with native names, A-PL addresses, backend effective times, availability,
personal overrides and pending configured times. Missing entities/origins are
explicit; unknown effective times are not replaced with the profile's times.
Unused profiles remain visible. An association's **Manage cover** action opens
the current cover editor for assignment, overrides, profile editing or calibration.

The original 0.25.0 increment provided overview/navigation, with writes through
the existing cover editor and its shared-change preview/confirmation. Version
0.26.0 adds the direct profile operations described above. Version 0.27.0 adds multi-cover assignment; no second calibration flow is introduced.

With all gateways selected, profile groups are separate and keyed by gateway plus
profile ID. Search matches profile names, cover names/IDs, areas and A-PL. Area
and entity-category filters select matching profiles; every matched profile keeps
its **complete** association list, including followers outside the filter.

The view reads `myhome/cover_profiles/overview` and subscribes to the existing
profile revision notifications only while visible. The panel's 15-second visible
refresh reconciles runtime values and missed events. Registry updates refresh
native names/addresses. Scope/connection changes invalidate pending responses and
release subscriptions; failures are shown per gateway and can be retried. Only
expansion/navigation state is held in the browser; saved values remain in HA.
Storage v5, export v3 and the websocket schemas are unchanged.

## Session recovery (0.24.0)

The existing calibration section now offers **Resume session** when a detached
session exists for the selected gateway. It shows an occupied-session notice while
another panel still controls it; **Refresh session status** updates availability
without replacing profile form drafts. All authenticated administrators can claim
a detached session. An attached controller cannot be displaced by another panel.

Closing the dialog, changing page or losing the websocket detaches the controller.
The backend retains the session for **10 minutes after detachment**. At
`confirm_closed`, `confirm_open`, `confirm_automatic` and `review`, collected times
and their original evidence survive. Single-direction and complete batch reviews
use the same recovery flow. Recovery only attaches a controller: it never starts,
replays or continues a movement. Profile names and unsent form drafts are not saved
as session configuration.

Leaving during queued/running motion, an automatic settling pause or a pause
between covers interrupts the whole measurement, clears provisional results,
invalidates queued motion and requests Stop. A queued Stop is not proof of a
physical stop. The interrupted session can be reopened to inspect its reason or
retry Stop, but must be cancelled before starting a new measurement. External
movement still invalidates a detached checkpoint.

The attached controller heartbeats every 5 seconds; its **20-second lease** expires
into the same detach behavior. **Cancel** explicitly ends the session. Retention
expiry, cover unload, entry removal and HA shutdown release the gateway. A restart
loses unsaved sessions. An already accepted durable save can finish after detach;
a storage failure leaves its review recoverable. Only one session owns a gateway;
other gateways remain independent. Existing save destinations and WHO layout are
unchanged. Older API clients without `client_id` retain cancellation on disconnect.

## Measurement save destinations (0.23.0)

Single-cover guided and automatic review offers three explicit destinations:

- **New profile** (default): the existing named copy/assignment operation. Both
  reviewed values are saved and the measured cover's old personal overrides are
  removed. Partial measurement retains the other configured value/evidence.
- **Only this cover**: save measured directions as personal values with their
  measurement evidence; keep the existing assignment, profile and unmeasured
  direction unchanged. No profile name or existing assignment is required.
- **Assigned profile**: preview then separately confirm changes to the measured
  directions of the assigned profile. Include every saved follower, even offline
  or orphaned ones, and show values masked by personal overrides. Remove only the
  measured directions' personal overrides on the measured cover; retain all
  other personal values and the profile name. The unmeasured shared direction
  keeps its original value/evidence, even when this cover has a personal value.

All values and evidence come from the backend session. One durable transaction
increments one revision. A failed write retains the review and measurements.
Confirmation binds to the exact proposal, gateway, target, revision and session;
followers performing native calibration block a shared commit, while moving
followers apply changes after stopping. The WHO layout and collapsible sections
are unchanged. Batch review retains its existing atomic new-profile save.

Validation: 1,982 Python tests passed (one existing skip, five snapshots),
106 frontend tests passed, and 100% line coverage (7,535 statements in 42 modules).
Strict typing reports zero errors; Ruff and HA architecture checks pass. Physical
installation verification of the new destinations remains separate.

## Current v2 alignment (2026-09-17)

The branch integrates official `v2-phase1-architecture` through `1dc4b3e`
(integration 2.0.0b12, OWNd 2.0.0b7). This includes FrameRouter event delivery,
declared lighting groups and debounced broadcast resync, profile-driven command
session idle timeouts, cover delivery-failure handling and the antifreeze target
fix. The panel remains at 0.22.2; its frontend assets have not changed.

Cover profiles bind before the initial status request while FrameRouter owns bus
subscriptions. A failed cover command clears motion and applies any pending
profile without advancing position. Calibration queue locks and cancellation
guards, atomic shared storage, single-direction measurement without an assigned
profile, admin-only monitor access and Configure navigation are retained.
Gateway settings expose the upstream broadcast-resync option. Area lookup uses
the handler's normalized MAC property, which is also tested with a real OWNd gateway.

Validation: 101 frontend tests pass; backend line coverage is 100% (7,473
statements across 41 modules). Strict mypy reports zero errors without raising
the upstream zero-error baseline; Ruff and HA architectural checks pass.

## Partial measurement without an assigned profile (0.22.2)

Opening-only and closing-only no longer require a saved profile assignment.
The previous fix left this second restriction in both the options and the start
handler. The backend now resolves the retained opposite time from committed
settings: personal override, assigned profile, native timing, then YAML/default.
Unsaved browser edits are never used. The retained value is validated before a
session starts; starting alone never requests movement. Mode, stationary-cover,
revision and gateway ownership checks continue to apply.

Existing profile/override evidence is retained. Native evidence is retained when
it satisfies the profile evidence schema; incomplete/legacy evidence is reported
as unknown in the new profile, without inventing an origin or measurement date.
The original native fallback and its metadata remain unchanged. Review labels the
opposite time as a retained configured value. Save and persistence failures follow
the same atomic semantics as the complete measurement.

Validation: 1,853 Python tests passed (one skip, five snapshots), 101 frontend
tests passed, 100% line coverage (7,083 statements across 39 modules). The new
regressions reproduce both the disabled options and backend refusal in 0.22.1.

## Partial measurement selector (0.22.1)

The direction selector remains available after selecting automatic measurement.
Choosing opening only or closing only switches the visible mode to guided, as
explained beside the selector. Selecting automatic again resets the scope to both
directions. Nothing moves until the existing measurement confirmation. A saved
profile must still be assigned; when absent, the reason is now visible next to
the selector. Backend measurement, override and persistence semantics are unchanged.
Validated with 100 frontend tests and 35 targeted panel/partial-calibration Python
tests. The two selector regressions fail before this fix and pass afterwards.

## Previous v2 alignment (0.22.0)

The branch integrates official v2 through `c2e6424`, including the public OWNd
`send(message, is_status_request)` contract, callback-safe tests and ambiguous
WHO=13 device-type 200 handling for F454/MyHomeServer1. The worker retains panel
calibration command locks and cancellation guards while leaving retry policy to
OWNd. This supersedes the signature-probing compatibility described for 0.20.1.

## Personal cover values and shared profiles (0.22.0)

The existing Advanced section lets you customize opening and closing independently.
Uncheck a direction and save to inherit from the assigned profile or native/default
settings again. Personal values take precedence even when assigning another profile.
The Edit section now previews shared-profile changes, including every follower and
any personal values that mask a change, before explicit confirmation. Concurrent
changes require a fresh read/preview. Moving covers apply new timings after stopping.

Measurement review explains that saving a new profile removes old personal values.
Single-direction measurement retains the other direction's configured value and
provenance. WHO navigation, device grouping and the three collapsible sections stay
in place. See the [backend/API contract](cover-settings-backend.md) for details.

## Shared cover backend (0.21.0)

The first backend slice from discussion #270 is implemented. See the
[shared cover settings contract](cover-settings-backend.md) for the version-5
storage migration, native fallback precedence, atomic native service writes,
gateway overview API and version-3 export. Existing timing-only profiles remain
unscaled, and the assignment section explicitly explains that limitation.
WHO navigation, device grouping and collapsible sections retain their layout.
The following version sections describe earlier releases.

## Current v2 alignment (0.20.1)

The panel integrates the official `v2-phase1-architecture` through `3f5e791`
(2026-09-16), retaining the compact, collapsible profile editor from `8edd201`.
Gateway inventory reads `entry.runtime_data`; cover movement uses v2's real-write
and motor-start timing, echo handling and directional travel times.

An assigned panel profile takes precedence over native saved timings. Removing
its assignment restores the native saved opening/closing values, or YAML/defaults
when none are stored. Native timing/calibration services reject changes while a
panel profile owns that cover or a panel measurement/save owns that gateway.
Panel measurements and profile writes likewise reject active or queued native
calibration. Existing profile storage v4 and export v2 remain unchanged; this is
coexistence with explicit ownership, not the proposed shared-backend migration.

The command worker supports the two-argument `send()` API in the pinned
OWNd 2.0.0b6. It passes `retry_after_lost_ack=True` only when the installed
engine explicitly exposes that parameter; an internal `TypeError` never
triggers a second send. The OWNd smoke matrix exercises the real send API.

The shared native/legacy monitor includes upstream Start/Stop Trace, fresh Sweep
captures, capture-specific exports, local timestamps and explicit arming before
raw frame transmission. Exported diagnostics retain upstream privacy filtering.

## Measure one direction (0.20.0)

In **Travel profile**, select **Guided measurement**, then **Opening only** or
**Closing only** under **Directions to measure**. A saved profile must already be
assigned to this cover. Save any intended profile edits before opening the wizard;
the backend takes the opposite direction from the assigned, committed profile.
The single-direction options are disabled without an assignment. Automatic mode
uses the complete cycle and resets the direction selector to both directions.

For opening, first position the cover fully closed and stopped; for closing,
start fully open and stopped. Confirm the starting endpoint to request movement,
then record the opposite physical endpoint with the existing guided control.
Timing starts at matching bus feedback and includes operator reaction delay at
the endpoint. The wizard requests Stop and goes directly to review after that
one leg. It never starts the other direction. The review labels the measured time
and the retained time separately.

The retained value keeps its original source, date and origin, including inherited
or unknown evidence. Only the measured direction gets new guided provenance.
Explicit Save creates and assigns a new profile copy; the original profile and
other covers using it are untouched. Stop, Cancel, errors or connection loss
before Save discard the provisional copy. Shared ownership, revision checks,
lease, queue guards and persistence semantics remain in force. A save error keeps
review available while connected; an accepted disk write may finish after closing.
Storage v4 and export v2 are unchanged.

For the physical check, note the assigned closing time and its origin/date, measure
opening only, and save. Verify that only opening changed, that closing evidence
is identical, and that another cover using the old profile is unchanged. Repeat
with closing only starting fully open; also cancel an attempt before saving.
Single-direction physical validation is pending.

## Automatic calibration of a selection (0.19.0)

In a standard cover's **Travel profile**, choose **Calibrate a selection**
(**Calibra una selezione** in Italian). The list contains only native covers of
that gateway. Every checkbox starts unchecked; unavailable, moving and advanced
covers show a reason and cannot be selected. Choose 1–20 covers, then continue.
The displayed entity-ID order is the execution order. Confirm the automatic cycle
on the next screen before any movement starts.

Each selected cover runs open/close/open in sequence, with a one-second pause
between covers. The panel shows the current cover and completed opening/closing
measurements. Stop, Cancel, a measurement error or loss of the session aborts the
whole group and discards its provisional results. No following cover starts.
Only the current cover is under calibration control; targets are checked again
before their turn and all are checked again before saving.

After every cover succeeds, review the times and edit each profile name. One
explicit Save creates and assigns a new profile for each selected cover in one
atomic write, increasing the gateway revision once. Existing profiles remain in
the store. A validation or disk-write failure applies no part of the group and
keeps the review available while the session remains connected. An already
accepted disk save may finish if the browser closes; reopening shows saved data.

This uses the existing session, lease, provenance, storage v4 and export v2.
The user has confirmed both single-cover and selected-cover automatic measurement
on their installation.
The standalone card remains supported.

## Optional automatic calibration (0.18.0)

In **Travel profile**, choose Guided or Automatic measurement, then open calibration.
Automatic mode waits for explicit confirmation before running open/close/open, with
one-second pauses. It requires bus start feedback and actuator stop feedback,
rejects the 59–65-second factory-timer signature and uses a 180-second run timeout.
Stop and Cancel remain available. Results enter review and are saved only when you
explicitly create/assign a profile. Provenance identifies these as automatic
actuator-feedback measurements. All session, lease and persistence protections are
shared with the guided wizard; there is no second timing store or automatic save.

Actuator run time may differ from physical travel. Observe the cover and check
both results before saving. See [the calibration guide](cover-calibration.md) for
limits and the supervised physical test procedure. Selected-cover batches are
available in 0.19.0; guided single-direction measurement is available in 0.20.0.

Storage migrates versions 1/2/3 to version 4, preserving recorded evidence and
assignments, to support the new automatic source. Older integration versions
cannot read this version; a downgrade needs a compatible backup. The export now
uses format version 2 to advertise the additional source; its structure is unchanged.

## Calibration export (0.17.0)

Open a cover's **Travel profile** dialog and choose **Export calibration** in the
footer. The JSON file contains all saved profiles and assignments for that cover's
gateway, including unused profiles and references to removed covers. Opening and
closing provenance is included separately. The file name identifies the config
entry and saved revision: `myhome-calibration-<entry_id>-r<revision>.json`.

Every click requests a fresh consistent backend snapshot. Unsaved editor changes,
unfinished wizard measurements, YAML/default times and pending runtime state are
not exported. A saved profile awaiting runtime application is exported with its
saved values. Downloads do not modify profiles, interrupt calibration, or move
covers, and remain available when the cover is offline or read-only. Navigating
away before the response arrives cancels the download.

The document has its own format version, independent of storage and panel versions.
It contains user-defined names and entity IDs, but no gateway credentials, network
settings, or internal MAC-bearing cover unique IDs. It is a calibration record;
there is no import/restore action yet and it does not replace a Home Assistant
backup. See [the export contract](panel-websocket-api.md#calibration-export-0170).

## Origin and date of saved travel times (0.16.0)

The profile editor shows opening and closing evidence separately: guided wizard
measurement, manually entered value, or unknown origin for older profiles.
Dates come from the backend in UTC and are displayed in the browser's local time.
A wizard measurement is dated when its endpoint is confirmed, not when saved.

Renaming or assigning a profile preserves its evidence. Copying a selected profile
preserves evidence for unchanged directions; editing a time records a new manual
value for that direction only. Inherited times show the original cover when it
still exists. Assigning a kitchen profile to a bedroom never claims that the
bedroom was measured. These labels describe saved profile values; edits remain
drafts until saved, and pending runtime timings still apply only after stopping.

Existing profiles migrate without invented dates or origins. Without a profile,
the editor labels the time as YAML/default because those two sources cannot be
distinguished reliably from the current configuration. This feature introduced storage
version 3; panel 0.18.0 now migrates it to version 4. Older integration versions
cannot read that store. Back up before
upgrading if a downgrade may be needed. The standalone card remains supported.

## Live state in device headers (0.15.0)

Device headers show the current state of their primary entities even while
collapsed, using the same Home Assistant state formatting as the expanded rows.
If multiple primary entities match the current filters, each state is labeled
with its entity name. Buttons and registry config/diagnostic entities stay in the
secondary section and are not promoted to the header. Trigger-only devices and
entities without a registered device have no device-state summary. Updates keep
the card's open/closed state and keyboard focus intact.

## Compact secondary entities (0.14.0)

Within each device and gateway, main entities appear first. Buttons and entities
marked `config` or `diagnostic` in the Home Assistant entity registry follow in a
compact section. Their names open Home Assistant's native entity details and
controls; the pencil still opens the name/area editor. Button timestamps are not
rendered or updated. Other entities keep their live state, including diagnostic
sensors. Names wrap on narrow screens; search, filters and disabled/hidden flags
continue to include secondary entities even when no main entity matches.

## Monitor language (0.13.0)

The native monitor and existing card follow `hass.language`, with Italian and
English UI catalogs and English fallback for unsupported languages or missing
keys. Regional tags such as `it-IT` and `it_IT` select Italian. Controls, tooltips,
WHO labels, connection states, empty views, progress messages and feedback are
translated. Custom titles, raw frames and protocol filter syntax stay intact.

Changing the shared view's language updates text in place, preserving filters,
paused state, input focus/selection and unsent command text. The panel's translated
shell keeps the same monitor view and capture; reattaching it renews the stream
and resets invalidated action indicators. Late operations cannot leave a retained
button permanently busy. Layout wrapping accommodates longer translated labels.

JSON field names and copied diagnostic Markdown retain their existing support
format (English) so reports remain directly usable in GitHub discussions. There is
no new backend translation endpoint; the versioned module can later consume the
agreed shared contract.

The standalone card remains supported. Its removal is explicitly deferred to a
separate future PR, as requested; this PR does not remove its registrations or
existing dashboard support.

## Native bus monitor (0.12.0)

The panel now imports `panel-bus-monitor-view.js` directly through its versioned
bundle. It no longer imports `myhome-bus-card.js`, creates a Lovelace card, or
uses the legacy `bus_card_url` panel configuration field. The view module has no
card-picker registration, global card class or registry watchdog.

`panel-bus-monitor.js` owns one view for the selected loaded gateway. A missing
MAC cannot fall back to another gateway. Gateway/connection changes and navigation
invalidate old subscriptions, history and pending export/report responses. Closing
the view clears retries and feedback timers. Clearing capture also invalidates any
history request that was already in flight.

The former card implementation is now a small compatibility adapter importing the
same view. Existing `myhome-openwebnet-bus-monitor` and `myhome-bus-card` dashboard
configurations, settings and automatic resource registration remain supported
through this transition. Its `/local/myhome-bus-card.js` fallback imports the view
from `/myhome_static/panel/`, so it needs no second copy of the module in `www`.
The adapter's dependency URL version must follow panel releases that change the
shared view; the integration still hashes the adapter for Lovelace cache busting.

| Function | Native panel behavior |
| --- | --- |
| Capture and initial history | Existing WebSocket stream, bounded buffer and deduplication |
| Filtering | WHO, WHERE/WHAT/DIM/raw text, RX/TX and ACK/NACK |
| Pause/resume | Existing local display pause behavior |
| Clear | Clear local capture and the selected gateway's backend buffer |
| Frame send | Existing backend validation and selected gateway MAC |
| Sweep | Existing `myhome.sweep_bus` service with explicit gateway |
| Export Trace | JSON download, preserving frame RX/TX and description; gateway credentials omitted |
| Copy Trace | Diagnostic Markdown and existing GitHub issue-form link |

No protocol commands, discovery behavior or backend API schemas change. The monitor gained Italian UI texts in 0.13.0; common translation API work remains
separate.

Before removing the compatibility card, validate the native view on a real gateway:
compare live capture and filters, pause/resume and clear; check an exported JSON
file and copied diagnostic report; verify gateway switching and repeated entry/exit
leave a single active stream. Verify frame send and sweep only on the intended
selected gateway. Removal of automatic Lovelace registration and the transition for dashboards
referencing the old card will be handled in a separate PR. The card is retained
in this PR even after the parity check.

Automated validation uses the actual shared view and compatibility adapter: native
imports without Lovelace registration, capture/filters/actions/export, stale
responses, retries, missing gateway identity, late module loads and both legacy
card names. No real gateway/browser layout test is claimed by these DOM tests.

## Calibration cleanup fix (0.11.1)

A calibration session now releases its shutdown listener only once. Home Assistant
removes a one-shot listener before invoking it; the session no longer attempts to
remove it again from inside the shutdown callback. Repeated socket/entity cleanup
is also idempotent, avoiding duplicate events and preserving the original close
reason. This fixes the `Unable to remove unknown job listener` log during shutdown.

## Profile synchronization (0.11.0)

Open profile editors now follow saves, assignments and unused-profile deletions
made in other tabs on the same gateway. An untouched editor refreshes automatically.
An unsaved draft stays intact, with a warning and an explicit reload before further
writes. The same protection applies to changed selections and deletion confirmations.
Guided calibration remains in place while a measurement is active.

If live subscription setup is unavailable, the dialog explains that it will check
saved profiles every 15 seconds while visible. Reopening or returning to a visible
tab also reconciles saved data. See the [implemented API](panel-websocket-api.md).

Manual check: open the same cover in two tabs, change/save a profile in one and
verify the other refreshes. Then type an unsaved name/time in the second tab and
save another change in the first: the second must retain its draft, show the
warning and block writes until explicit reload. Repeat with another gateway to
verify isolation, and close/reopen the dialog to check subscription cleanup.

## Guided travel measurement (0.10.0)

The WHO 2 **Travel profile** dialog now opens **Guided travel measurement** for one
standard cover. It measures separate opening/closing times from bus movement
feedback, requires physical endpoint confirmations and an explicit save to a new
profile. Stop and Cancel remain available; timeouts/disconnects discard provisional
measurements. A queued Stop is shown as a request, not a confirmed motor stop.
See the [wizard and session API guide](cover-calibration.md) for the workflow,
requirements, limits and real-gateway validation steps.

## Developer contracts

- [Implemented panel/profile API (0.11.0)](panel-websocket-api.md): current commands,
  snapshots, validation, revisions, errors and revision subscriptions.
- [Shared panel contract proposal](panel-shared-contract.md): pinned comparison with
  the calibration fork, proposed gateway/texts/refresh/error contracts, review
  decisions and the sequence before porting guided calibration. Draft only.

## Panel versioning

The panel has an independent version, currently **0.22.2**, defined by
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
- Open the native bus monitor for one selected, loaded gateway. Switching
  gateways creates a separate monitor view and closes the previous stream.
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

The inventory command, `myhome/panel/inventory`, is an admin-only read
operation. It returns an explicit allowlist of gateway, device, entity, and area
fields, including IP/MAC information needed by administrators. It does not return
gateway passwords, full Config Entry data/options, or runtime objects.

Gateway and device configuration is not duplicated and needs no migration. The
HA-managed store `myhome_panel` (version 1) contains the global
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
profiles/calibration sections. The 0.7.1 foundation did not add profile endpoints;
0.8.0 below introduces timed profiles. Hardware probes and guided calibration
remain future work.

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
   not move covers. The calibration fork has a guided Options Flow; our 0.9.0
   branch does not. Port its behavior only through an agreed backend session API;
   avoid private frontend dialog internals.
   Show per-device outcomes for physical operations, which cannot be described
   as an atomic storage transaction.

Reuse and adapt the cover-profile backend after checking these guarantees, then
add its UI inside this shell. WHO 1004/1018 hardware diagnostics need validated
captures and supported OWNd decoding before exposing probe controls. Retire the
standalone bus card only once its current inspection, filtering, sending, sweep
and export functions are available and verified inside the panel; the adapter
allows that migration without coupling the other sections to card internals.


## Panel 0.9.0: directional travel profiles and deletion in WHO 2

Expand a WHO 2 device and choose **Travel profile** on its cover row. The dialog
shows the assigned profile and current full opening and closing times. Each time
accepts values from 1 to 600 seconds, including fractions. The original YAML/default
`travel_time` supplies both directions when no profile is assigned. Position
estimation, command/bus reversals and scheduled position stops use the relevant
direction. This remains a linear estimate without physical calibration.

- **Save new profile and assign** creates a profile for this gateway and assigns it
  to this cover. Its name can contain spaces (maximum 64 characters).
- **Apply selected profile** reuses an existing profile. Selecting **Use YAML /
  default settings** removes the assignment and restores the original runtime value.
- **Update this profile** edits a profile assigned exclusively to this cover. Shared
  profiles require an explicit new copy, so a single-cover edit cannot change another
  cover. Profiles are scoped to their gateway, with a limit of 200 stored profiles.
- **Delete selected profile** is available for unused profiles. Select the profile,
  choose Delete, and confirm its name in the dialog. This does not change the current
  assignment or runtime timing. Profiles in use show their assigned covers and
  instructions: choose another profile or restore defaults on each of those covers,
  then return and delete the now-unused profile. The server rejects deletion while
  any assignment remains, including an assignment to a removed registry entity.
  Registry-removed assignments currently require restoring that entity to unassign
  it; deleting the gateway removes its entire profile store.
- Offline, unloaded or disabled covers are read-only. Covers configured for advanced
  hardware position feedback show an explanation; timed profiles do not apply.
- Saving never sends a bus command or reloads the gateway. A moving cover keeps its
  current opening/closing times and scheduled stop. The saved change is pending until a stop
  or stationary-position event, and survives restart even if the entity unloads
  during the save. The next mount resolves the stored assignment before status reads.
- Registry renames keep assignments because storage uses the native unique ID,
  together with the config entry. Removing the config entry deletes its profile store.

The authoritative store is `myhome.cover_profiles.<entry_id>` (version 5), containing
`revision`, `profiles`, `assignments`, `covers` and `native_fallbacks`.
See [current migration and rollback semantics](cover-settings-backend.md#storage-and-migration).
The profile migration history follows. Version 1 profiles migrate automatically:
`travel_time` becomes both `opening_time` and `closing_time`, preserving IDs,
assignments and revision. Migration validates and persists the upgraded store before
binding the runtime; failures do not silently replace saved data. Version 1 and 2
profiles gain unknown provenance with null dates and origin IDs. Version 3 evidence is preserved. Version 4 storage
requires the updated integration (older integration versions cannot read that format).
An explicit assignment overrides
native fallback timings and `travel_time` from YAML/defaults; removing it restores
the native fallback when present, otherwise YAML/defaults. Nothing rewrites
`myhome.yaml`, native names/areas, or gateway credentials. Writes use one gateway
lock, an expected revision and atomic storage; failed persistence is reported and
never published to the running cover. A stale editor keeps its draft and asks for an
explicit reload before saving again. The frontend module owns its dialog lifetime
and ignores late responses after navigation, disconnects or connection replacement.

### WebSocket contract

Both commands require an administrator and explicit `entry_id` / `entity_id`.
The entity must be a native MyHOME cover belonging to exactly that entry.

```json
{"type":"myhome/cover_profiles/read","entry_id":"ENTRY","entity_id":"cover.shutter"}
```

Create and assign a new profile:

```json
{"type":"myhome/cover_profiles/write","entry_id":"ENTRY","entity_id":"cover.shutter","revision":0,"action":"save","profile_id":null,"profile":{"name":"Bedroom","opening_time":32.5,"closing_time":30.5}}
```

Use `action: "assign"` with an existing `profile_id`, or `null` to restore defaults.
Use `action: "save"` with the currently assigned exclusive profile ID to update it.
Use `action: "delete"` with an unused profile ID to delete it. This uses the same
administrator, exact gateway/cover, writability and revision checks as other writes.
An assignment racing a deletion cannot leave a dangling reference: both operations
hold the gateway lock and only one can succeed at the expected revision.

Responses include the new revision, assigned profile ID, profile list with usage
counts and `assigned_to` (native entity IDs/names), default/effective times, pending
status, writability and a reason code. Profiles contain `name`, `opening_time` and
`closing_time`. Legacy `{name, travel_time}` writes remain accepted and set both
directions equally. `effective_travel_time` and the entity's `travel_time` attribute
remain opening-time aliases for compatibility; use `effective_opening_time` /
`effective_closing_time` and state attributes `opening_time` / `closing_time` for
direction-aware clients.

This adapts the persistence/resolution separation of
[Interstellar0verdrive's calibration store](https://github.com/Interstellar0verdrive/MyHOME-stability/blob/229b1eb30558012674e1e7f5c2059a58300f09df/custom_components/myhome/calibration_store.py)
to the existing MyHOME cover runtime. It does **not** import that fork's height,
roll or slat mechanics; directional timing extends our own linear runtime. Its
storage/API is deliberately separate from `myhome.calibration.*`. At the 0.9.0
baseline, guided calibration and batch editing were later steps. Guided and
selected-cover measurement are now available as described above; their evidence
does not assert automatic physical endpoint detection.


Validation for 0.9.0: **1,398 backend tests passed, one existing skip**, five
snapshots passed on HA 2025.1.4 / Python 3.12.14 / OWNd 2.0.0b6. The final
coverage gate passes with **100% Python line coverage** (5,684 statements), after
rerunning the 39 profile cases against the final source. All **32 frontend tests**
pass, as do **67 profile, cover and panel tests** on HA 2026.9.1 / Python 3.14.7.
New cases cover real version-1 storage migration, distinct timing after restart,
command and bus reversals, both directional scheduled stops during a pending reset,
atomic deletion/persistence failures, assignment/deletion races, authenticated
WebSocket deletion, UI confirmation/cancellation and errors. Ruff and architecture
checks pass. Version 0.9.0 still needs testing on a physical gateway and in the real
HA frontend.

Historical validation for 0.8.0: **1,379 backend tests passed, one existing skip**, five
snapshots passed and **100% Python line coverage** (5,660 statements) on HA
2025.1.4 / Python 3.12.14 / OWNd 2.0.0b6. All **28 frontend tests** pass. On HA
2026.9.1 / Python 3.14.7, all **48 profile, cover and panel tests** pass, including
the real storage implementation and authenticated WebSocket transport. The profile
suite verifies failed disk writes, concurrent edits, disabled/offline covers,
rename/restart persistence, shared-profile isolation, and an already scheduled
position stop retaining its original duration. Ruff and architecture checks pass.
Physical-gateway behavior and layout in a real HA browser still need manual testing.
