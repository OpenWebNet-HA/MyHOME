# MyHOME panel API: implemented reference

> Current update — panel 0.25.0: [shared cover settings](cover-settings-backend.md)
> specifies storage v5, export v3 and `myhome/cover_profiles/overview` (schema v1).
> Partial guided calibration now uses resolved cover settings without requiring
> an assigned profile (see the single-direction extension below).
> Write actions `overrides`, `preview` and `update_shared` add personal values
> and revision-bound impact confirmation; their complete contract is linked above.
> Existing endpoints remain available; native writes use the shared store and revision. Earlier release descriptions below remain historical where superseded.


## Shared profile overview UI (0.25.0)

The WHO 2 **Profiles** view consumes the existing `myhome/cover_profiles/overview`
response and `myhome/cover_profiles/subscribe` notifications. It joins each
association's entity ID with the gateway-scoped panel inventory for native names,
areas and A-PL. Effective/configured values and provenance are taken directly from
the backend, never recalculated from the displayed profile. Missing runtime values
are shown as unknown. No new endpoint, persisted field or write operation is added.
See [behavior and scope](sidepanel.md#shared-profile-view-0250).

## Session recovery (0.24.0)

`start` and `batch_start` accept optional `client_id` (nonempty string, at most
64 characters). Panel 0.24.0 generates a fresh random ID per controller. Supplying
it opts into [bounded recovery](sidepanel.md#session-recovery-0240); omitting it
preserves legacy cancellation-on-disconnect behavior. Replaying the same start
subscription after a websocket reconnect reattaches its detached session. It
cannot replace an active controller. If the session has expired, a replayed start
creates a fresh confirmation step, never a movement.

`cover_profiles/read` includes `calibration`: null, or the gateway session view
without its controller `attachment` token. It describes the current target, phase,
values, mode/direction and batch targets/results where applicable. `attached`
indicates whether it is controlled; this transient view does not increment the
persisted profile revision. The editor can refresh it without replacing drafts.

To claim a retained session, an administrator subscribes to:

```json
{"id": 50, "type": "myhome/cover_calibration/resume", "entry_id": "ENTRY",
 "session_id": "SESSION", "client_id": "NEW-RANDOM-CONTROLLER-ID"}
```

The result acknowledges the subscription, followed by the current authoritative
view. `calibration_busy` rejects a live owner; `calibration_expired` rejects an
absent, ended or legacy session. Resume does not accept browser timings or issue
movement commands. It can also reconnect to interrupted status for Stop/Cancel.

Recoverable subscription views add `recoverable: true`, `attached`,
`recovery_seconds: 600` and `attachment`. Every `action`, including heartbeat,
Stop, Cancel and the new `detach`, must provide that attachment token in addition
to the session ID and use the owning websocket. The token rotates on each attach;
old actions and old unsubscribe callbacks cannot affect the new controller.
Existing sequence validation still applies to state-changing steps and saves.
Sequence remains monotonic across detach and attach notifications.

`detach` and unsubscribe retain safe checkpoints for 600 seconds, or interrupt
and discard an in-progress cycle before requesting Stop. Neither detachment nor
reattachment changes the measurement provenance. Cancel releases ownership
immediately. Storage v5 and export v3 are unchanged; transient sessions are not
included in export or restored after HA restart.

## Measurement destinations (0.23.0)

Session views include `save_modes`: `new`, `cover`, and (when assigned) `shared`.
Batch sessions advertise only `new`. The existing `myhome/cover_calibration/action`
endpoint accepts optional `save_mode` (default `new`) and `confirmation`.

- `save` + `new`: existing `name`/batch `names` behaviour.
- `save` + `cover`: personal values for measured directions; no name required.
- `preview_save` + `shared`: only in review; returns the unchanged session view
  plus `save_preview` containing `before`, `after`, every `followers[]` entry and
  `confirmation`. No persistence, motion or sequence transition occurs.
- `save` + `shared` + `confirmation`: requires that exact proposal token. The
  backend revalidates ownership, revision, target and followers under its lock.

Each preview follower includes safe entity ID/name (nullable for missing entries),
availability, and directional `{before, after, overridden, override_removed}`.
Times/evidence/profile ID never come from the action payload. The assigned profile
keeps its name and unmeasured direction; target overrides for measured directions
are removed in the same transaction. Other personal values are retained.
A storage error returns `storage_error` and restores review. Invalid confirmation
returns `preview_required`; revision changes return `revision_conflict`. The
frontend discards failed/stale previews and requires a fresh preview before retry.
Attached-controller ownership, administrator checks and explicit final Save remain required.


Status: **implemented through panel 0.20.0**. The original profile contract was reviewed against
[`02ce199`](https://github.com/xtimmy86x/MyHOME/tree/02ce19908787297c1a6e2a65d56a289e766c0695).
Panel 0.10.0 adds a [guided-measurement session API](cover-calibration.md) and
`calibration_busy` refusals on profile writes while a measurement is active. The
profile payload/storage format below remains the 0.9.0 contract. Panel 0.11.0 adds
`myhome/cover_profiles/subscribe` invalidations, documented below. Panel 0.12.0
loads the bus monitor as a native view; bus endpoint payloads remain unchanged.
Panel 0.14.0 uses the existing `entity_category` and `domain` inventory fields
to present compact secondary entities; no API payload changes are needed.

This reference describes the prototype on `feat/myhome-sidepanel`, not an upstream
v2.1 commitment. The [shared-contract proposal](panel-shared-contract.md) is separate
and any proposed features not listed in the current update remain unimplemented.

## Transport, ownership and scope

The panel uses Home Assistant's authenticated WebSocket connection. The examples
below include HA's integer message `id`; `hass.callWS` supplies that transport ID
for the frontend. Successful calls use HA's result envelope:

```json
{"id":1,"type":"result","success":true,"result":{}}
```

All MyHOME inventory, cover-profile and bus-monitor commands used by this panel
require an administrator independently of the panel route. Native registry writes
use HA's own authorization and schemas. The backend owns validation, persistence
and motion timing. Names and areas remain in HA registries; a profile name is the
label of a reusable timing configuration, not an entity name.

Profile storage version **2**, panel version **0.13.0**, and any future API contract
version are different concepts. There is currently no API version negotiation.
This experimental API may evolve through an explicitly documented migration.

## Command map

| Command | Scope and result | Authoritative implementation |
| --- | --- | --- |
| `myhome/panel/inventory` | All configured gateways and native inventory; no `entry_id` parameter | `panel.py`, `ws_panel_inventory` |
| `myhome/cover_profiles/read` | One explicit gateway and native cover; complete profile snapshot | `cover_profiles.py`, `ws_read` |
| `myhome/cover_profiles/overview` | `entry_id`; profiles and cover values/origins in one gateway read | `cover_settings_api.py`, `ws_overview` |
| `myhome/cover_profiles/export` | `entry_id`; versioned saved profiles, provenance and assignments | `cover_profile_export.py`, `ws_export` |
| `myhome/cover_profiles/write` | Same target; `save`, `assign` or `delete`; updated snapshot | `cover_profiles.py`, `ws_write` |
| `myhome/cover_profiles/subscribe` | Explicit gateway; initial/persisted revision invalidations | `cover_profiles.py`, `ws_subscribe` |
| `config/device_registry/update` | Native device name/area changes | HA; called by `myhome-panel.js` |
| `config/entity_registry/update` | Native entity name/area changes | HA; called by `myhome-panel.js` |
| `myhome/bus_monitor/info`, `/history`, `/stream`, `/send`, `/clear` | Existing bus diagnostics; panel adapter supplies selected gateway MAC | `websocket.py` |

The last row abbreviates the five full command names with the same prefix.
This document specifies inventory and profiles; bus schemas remain in
[`websocket.py`](../custom_components/myhome/websocket.py). Sending a bus frame
is a physical operation and is not governed by the profile-write guarantees below.

## Inventory

```json
{"id":1,"type":"myhome/panel/inventory"}
```

The result has `version` (integration), `panel_version`, `gateways`, `devices`,
`entities` and `areas`. It includes unloaded/disabled entries without requiring a
connection to their gateways. It does not contain profile configurations or a
profile revision.

| Collection | Fields |
| --- | --- |
| `gateways[]` | `entry_id`, `title`, `mac`, `host`, `port`, `serial_port`, `model`, `firmware`, `state`, `disabled_by`, `connected`, `monitor_available`, `device_id` |
| `devices[]` | `id`, `entry_ids`, `name`, `name_by_user`, `area_id`, `manufacturer`, `model`, `disabled_by`, `who`, `address`, `identifiers` |
| `entities[]` | `entity_id`, `entry_id`, `device_id`, `domain`, `name`, `original_name`, `area_id`, `disabled_by`, `hidden_by`, `entity_category`, `unique_id`, `who`, `address` |
| `areas[]` | `id`, `name` |

Optional metadata can be null. Devices shared across entries have multiple
`entry_ids`; conflicting WHO/address metadata is null rather than attributed to
the wrong gateway. `identifiers` includes only MyHOME identifiers. WHO/address
metadata is recorded information, not a physical-hardware discovery result.
Credentials and entire Config Entry data/options are never serialized.

The shell may display all gateways, but a profile dialog always receives exactly
one `entry_id`. An explicit missing gateway is an error in the shell; it does not
select a replacement. The legacy bus API accepts an omitted MAC and has a fallback;
the panel adapter always passes the selected MAC and an explicit unknown MAC does
not fall back.

## Read a cover's profiles

```json
{"id":2,"type":"myhome/cover_profiles/read","entry_id":"ENTRY","entity_id":"cover.bedroom"}
```

Both target fields are required. The entity must be a native MyHOME `cover` whose
registry `config_entry_id` matches the requested MyHOME entry. Persistence uses
its native unique ID internally, so an entity rename retains its assignment.
Clients must use the new entity ID after a rename; the old request target is invalid.

Example `result` (illustrative IDs):

```json
{
  "entry_id":"ENTRY",
  "entity_id":"cover.bedroom",
  "revision":4,
  "assigned_profile_id":"profile-a",
  "profiles":[{
    "id":"profile-a",
    "name":"Bedroom",
    "opening_time":32.5,
    "closing_time":30.5,
    "uses":1,
    "assigned_to":[{"entity_id":"cover.bedroom","name":"Bedroom shutter"}]
  }],
  "writable":true,
  "reason":null,
  "default_travel_time":30,
  "effective_travel_time":32.5,
  "effective_opening_time":32.5,
  "effective_closing_time":30.5,
  "pending":false
}
```

| Field | Meaning |
| --- | --- |
| `revision` | Nonnegative persisted revision of this gateway's entire profile store |
| `assigned_profile_id` | Stored assignment for the target cover, or null |
| `profiles` | All stored profiles of this gateway; no sorting guarantee |
| `profiles[].id` | Opaque stable ID; newly created IDs are UUID hex strings |
| `profiles[].name` | Display label; not an identifier; duplicates are permitted |
| `opening_time`, `closing_time` | Stored full-travel seconds, before any pending runtime application |
| `uses` | Number of persisted assignments, including removed registry entities |
| `assigned_to` | Native entity IDs and names; both null for a missing registry entity |
| `writable`, `reason` | Target's current ability to accept a write and refusal reason |
| `default_travel_time` | Original YAML/default runtime time; used for both directions on reset; null without a bound cover |
| `effective_opening_time`, `effective_closing_time` | Times currently applied to the bound cover; null if it is not bound |
| `effective_travel_time` | Compatibility alias of `effective_opening_time` |
| `pending` | A saved assignment/edit/reset is waiting for the target cover to stop |

An unavailable bound cover may still expose timing values; non-null timing does
not imply writability. Advanced covers are readable but return
`writable:false, reason:"advanced_cover"`; their displayed timed values do not
control hardware position feedback. Offline, unloaded and disabled targets are
read-only with `cover_unavailable`.

## Per-direction provenance (0.16.0)

Each profile includes `provenance.opening` and `provenance.closing`. Each contains:

| Field | Meaning |
| --- | --- |
| `source` | `guided`, `automatic`, `manual`, or `unknown` |
| `recorded_at` | Backend UTC ISO timestamp; null for unknown evidence |
| `origin_entity_id`, `origin_name` | Current registry identity of the original cover, or null if missing |
| `inherited` | Whether the recorded origin differs from this read request's target cover |

The wizard timestamps each confirmed endpoint. Manual creation or a changed
numeric time records the backend save time for that direction. Renames,
assignments, and unchanged numeric values preserve evidence, including unknown
legacy evidence. These fields describe saved profile values, not pending runtime
values or unsaved edits. The client cannot submit provenance inside `profile`.
Internal stable origin unique IDs are stored but never exposed in this response.

Store major version 4 migrates versions 1, 2 and 3, preserving revision, IDs,
assignments and times, and adding unknown evidence with null dates/origins.
Version 3 evidence is preserved. Older integration versions cannot read version 4; downgrade requires restoring
a compatible backup. This is a local panel contract extension, not an assertion
of interoperability with the proposed automatic calibration backend.

## Write actions

Every write requires `entry_id`, `entity_id`, `revision` and `action`.
`profile_id` is optional; omission is treated as null. `profile` is required only
for `save`. Extra unknown keys are rejected by the schema. A valid `profile` field
on an `assign` or `delete` request is accepted but ignored; clients should omit it.

### Create or copy and assign

```json
{"id":3,"type":"myhome/cover_profiles/write","entry_id":"ENTRY","entity_id":"cover.bedroom","revision":4,"action":"save","profile_id":null,"profile":{"name":"Bedroom copy","opening_time":33.5,"closing_time":31}}
```

This creates a new profile and assigns it to the target in one persisted mutation.
To preserve evidence when copying a selected profile, include the optional
top-level `copy_from_profile_id` with an existing profile ID from the same gateway.
Only unchanged direction values inherit that profile's evidence; changed values
become manual evidence originating at the target cover. Omit this field for a
fresh manual profile. It is invalid with a non-null `profile_id` or any action
other than `save`. Missing/cross-gateway source IDs return `profile_not_found`. There is no
create-without-assignment action. Maximum: 200 stored profiles per gateway.

### Update an exclusive profile

```json
{"id":4,"type":"myhome/cover_profiles/write","entry_id":"ENTRY","entity_id":"cover.bedroom","revision":5,"action":"save","profile_id":"NEW_ID_FROM_PREVIOUS_RESULT","profile":{"name":"Bedroom revised","opening_time":34,"closing_time":31.5}}
```

The profile must already be assigned to this target and have at most one stored
assignment. Otherwise the server returns `profile_shared`, including when the
profile is unused or assigned only to another cover. This action replaces the
profile's complete name and timing values, retaining its ID. It is not a patch.

Validation: names are trimmed, 1–64 characters; times are finite JSON numbers,
1–600 seconds inclusive. Fractions are accepted; booleans and numeric strings
are refused. Both directional fields are required together. The legacy form
`{"name":"Bedroom","travel_time":32.5}` remains accepted and is normalized to
two equal times. Mixing legacy and directional fields is invalid.

### Assign or restore defaults

```json
{"id":5,"type":"myhome/cover_profiles/write","entry_id":"ENTRY","entity_id":"cover.bedroom","revision":6,"action":"assign","profile_id":"profile-a"}
```

```json
{"id":6,"type":"myhome/cover_profiles/write","entry_id":"ENTRY","entity_id":"cover.bedroom","revision":7,"action":"assign","profile_id":null}
```

A non-null ID must exist on this gateway. Null removes the target assignment and
restores its original YAML/default time in both directions. Neither action deletes
a profile. The UI does not calculate a height-scaled profile or retain per-cover
calibration overrides: those concepts do not exist in this model.

### Delete an unused profile

```json
{"id":7,"type":"myhome/cover_profiles/write","entry_id":"ENTRY","entity_id":"cover.bedroom","revision":8,"action":"delete","profile_id":"UNUSED_PROFILE_ID"}
```

The profile must exist and have no assignments. Any remaining assignment produces
`profile_in_use`; null produces `profile_not_found`. The target cover is still
required and must be writable, even though deletion changes only the gateway's
profile list. The target's current assignment, effective timings and pending change
are untouched. The UI confirms the selected profile's name before issuing this call.
There is no undo endpoint. Removed-registry assignments remain protective; they
cannot currently be cleared through this editor without restoring the entity.

### Persistence, revisions and motion

All three actions return the same complete snapshot as `read` after saving. The
revision increases by one for every accepted write, **including a no-op assignment
or an identical save**. Rejected writes do not increase it. A waiting write takes
the gateway lock and then compares the submitted revision with current storage;
two clients submitting the same revision cannot both succeed.

The backend validates the target before allocating its store and again after
waiting for load/lock; it refuses writes during HA shutdown. It copies the data,
validates the mutation and atomically persists the complete store before publishing
it to memory. Disk write failures do not apply runtime configuration. The store is
`myhome.cover_profiles.<entry_id>`; version-1 records migrate by duplicating their
single travel time, preserving IDs, assignments and revision. Storage migration is
an internal write that can happen on a read/bind and does not increment revision.

Profile operations send no bus commands and do not reload the gateway. A moving
cover retains its current directional timing and scheduled stop. At stop, the
latest pending assignment/reset applies. If the cover unloads during persistence,
the successful response may be read-only; its next bind resolves the saved values
before initial status requests. Persistence success does not mean immediate
runtime application or physical calibration.

## Errors

Example:

```json
{"id":3,"type":"result","success":false,"error":{"code":"revision_conflict","message":"revision_conflict"}}
```

| Code | Condition / client action |
| --- | --- |
| `unauthorized` | HA administrator check failed; do not retry as a configuration error |
| `invalid_format` | HA WebSocket schema rejected the payload, including invalid times before the handler |
| `target_not_found` | Missing/foreign entry or entity, wrong platform/domain, or renamed target; refresh inventory |
| `cover_unavailable` | Target not writable, or HA stopping/stopped; reconnect/load/enable as appropriate |
| `advanced_cover` | Hardware-position cover; timed profile writes unsupported |
| `revision_conflict` | Gateway profile store changed; retain draft and explicitly reload before another write |
| `profile_not_found` | Unknown ID on this gateway, or null delete ID |
| `profile_shared` | Update is not of a profile exclusively assigned to this target; create a copy |
| `profile_in_use` | Delete still has assignments; show usage and remove assignments first |
| `profile_limit` | Creating would exceed 200 profiles |
| `invalid_profile` | Missing save profile or validation failure inside the operation/load |
| `storage_error` | An `OSError` while loading/migrating/saving; do not announce success |

Domain refusals currently repeat the error token in `message`; `invalid_profile`
and `storage_error` have short English messages. There are no MyHOME translation
metadata fields in these profile errors. Other unexpected failures are handled by
HA's WebSocket layer and are not normalized by this module.

## Current texts and refresh behavior

`panel-translations.js` contains English and Italian texts. The shell takes the
user's `hass.language`, selects its primary hyphen-separated subtag, then falls
back per key to English and finally the key. The shared monitor has its own
`panel-bus-translations.js` English/Italian UI catalog from 0.13.0, also selected
from `hass.language`, with regional-tag and per-key English fallback. There is
**no texts endpoint**. Diagnostic exports retain their existing support format.

The inventory listens to native entity/device/area registry events, debounced by
150 ms, and polls every 15 seconds while visible. It refreshes after visible-tab
resume. HA state updates refresh entity values and the open profile dialog's
effective timing/pending indicators without overwriting its draft.

Panel 0.11.0 subscribes before reading the profile snapshot. A successful save,
assignment, reset or deletion (including a guided-calibration save) invalidates
other open editors on that gateway. Clean editors fetch the current snapshot and
refresh their choices, assignments and usage counts. If a draft, changed selection
or deletion confirmation is present, it stays in place with a translated warning;
write/calibration actions require **Reload saved data (discard draft)** first.
The draft's inputs, focus and selection are preserved; no automatic merge occurs.

Events during a save are reconciled after its response. Duplicate/older revisions
are ignored, events arriving during a read trigger another read when necessary,
and profile refresh cannot replace an active calibration wizard. A new initial
event after HA reconnect and a visible-tab resume reconcile missed changes.
If subscription setup fails, a visible notice explains the fallback: check profiles
every 15 seconds while the tab is visible. A failed refresh preserves the current
view and offers explicit reload. Closing the dialog clears subscriptions, fallback
timers and visibility listeners, including late subscription completions.
**Inventory polling is separate from profile refresh.** No preview or undo exists.

## Profile revision subscription (0.11.0)

This is an implemented experimental endpoint, separate from the proposed shared
`myhome/covers/subscribe`. It uses the existing authenticated HA socket, requires
an administrator and an explicit MyHOME config-entry ID, and creates no bus traffic:

```json
{"id":20,"type":"myhome/cover_profiles/subscribe","entry_id":"ENTRY"}
```

The backend validates the entry, loads storage under the same lock as writes,
registers the listener, acknowledges the subscription, and sends an initial event:

```json
{"id":20,"type":"result","success":true,"result":null}
```

```json
{"id":20,"type":"event","event":{"entry_id":"ENTRY","revision":4,"kind":"ready"}}
```

Subsequent events have the same envelope, with `kind: "changed"` and the persisted
revision. They carry no profiles, entity objects, credentials or timing samples.
A client reads `myhome/cover_profiles/read` for its selected cover to reconcile.
Notifications are sent only after storage succeeds, never on rejected/failed
writes. Do not assume ordering between a writer's response and its notification.
The revision is shared by all covers on that gateway; a different gateway cannot
invalidate this subscription. Ordinary cover state changes use native HA updates.

Entry removal emits `kind: "removed"` with the last in-memory revision; process
this terminal event regardless of revision equality and close the editor.
HA's `unsubscribe_events` command and socket closure release the listener. A
placeholder cleanup callback also handles disconnect while waiting for storage.

```json
{"id":21,"type":"unsubscribe_events","subscription":20}
```

Setup errors are `target_not_found` for missing/non-MyHOME entries (also rechecked
after loading), `invalid_profile` for invalid stored data, and `storage_error` for
storage failures. HA handles authentication and schema validation. A config-entry
reload does not reset this stored revision or transfer the subscription to another
gateway. There is no event history or API version negotiation; initial `ready`
plus a fresh read provides synchronization.

Gateway changes, connection replacement and unmount invalidate outstanding dialog
responses. Closing the dialog does not cancel an accepted storage write. Native
registry subscriptions and the bus stream have their own cleanup functions.

## Evidence and verification

The source of truth for this reference is
[`cover_profiles.py`](../custom_components/myhome/cover_profiles.py),
[`panel.py`](../custom_components/myhome/panel.py) and the
[profile editor](../custom_components/myhome/frontend/panel/panel-cover-profiles.js).
Existing executable examples and regressions are in
[`test_cover_profiles.py`](../tests/test_cover_profiles.py),
[`test_panel.py`](../tests/test_panel.py) and
[`panel.test.mjs`](../tests/frontend/panel.test.mjs).

They cover authenticated WebSocket validation, actual storage migration/failure,
gateway ownership, revision races, profile deletion, rename/restart persistence,
directional timing, in-flight stops and editor drafts/lifecycle. They do not yet
constitute a machine-readable shared-contract parity suite. That is a proposed
acceptance gate in the companion document, not a capability added by these docs.


## Calibration export (0.17.0)

Admin-only request (extra fields are rejected):

```json
{"id":20,"type":"myhome/cover_profiles/export","entry_id":"ENTRY"}
```

The result is the JSON document to download, not a file URL. Its envelope is:

```json
{
  "format": "myhome.cover_calibration",
  "format_version": 2,
  "exported_at": "2026-09-15T12:00:00+00:00",
  "gateway": {"entry_id": "ENTRY", "name": "Home"},
  "revision": 4,
  "covers": [{"id": "cover-1", "registry_id": "REGISTRY_ID", "entity_id": "cover.kitchen", "name": "Kitchen"}],
  "profiles": [{
    "id": "PROFILE_ID", "name": "Kitchen", "opening_time": 25, "closing_time": 31,
    "provenance": {
      "opening": {"source": "guided", "recorded_at": "2026-09-14T10:00:00+00:00", "origin_cover_id": "cover-1"},
      "closing": {"source": "manual", "recorded_at": "2026-09-15T11:00:00+00:00", "origin_cover_id": "cover-1"}
    }
  }],
  "assignments": [{"cover_id": "cover-1", "profile_id": "PROFILE_ID"}]
}
```

- Format version 2 adds `automatic` provenance to version 1; document structure is
  unchanged. It is independent of profile storage version 4 and panel releases.
- Times are full-travel seconds. Sources are `guided`, `automatic`, `manual` or `unknown`;
  unknown evidence has null `recorded_at` and `origin_cover_id`.
- `covers[].id` references are local to this document and must not be treated as
  stable identifiers across exports. `registry_id` is the current HA registry ID;
  entity IDs and names are resolved at export time. Removed covers keep a distinct
  local reference with null registry ID, entity ID and name.
- All assignments and all saved profiles (including unused profiles) are included.
  Only covers referenced by assignments or provenance appear in `covers`.
  Inheritance can be determined by comparing assignment and provenance cover IDs;
  the target-dependent UI `inherited` flag is not serialized.
- One gateway lock covers load, entry revalidation and snapshot creation. The
  export uses the most recent committed revision after acquiring the lock. It
  does not increment revision, send notifications or modify runtime settings.
  Existing storage migration may still occur during initial load.
- Live availability, effective/pending timings, session values, editor drafts,
  credentials, network settings and internal cover unique IDs are excluded.
- Empty stores export empty arrays. Unloaded/disabled/offline gateways can export
  stored data. Unknown, foreign-domain or removed config entries return
  `target_not_found`; invalid storage returns `invalid_profile`; load failures
  return `storage_error`. Non-admin callers are rejected before store access.
- Import and restore are not implemented. This document is not an HA backup or an
  import format agreed with the separate automatic calibration backend.


## Automatic session extension (0.18.0)

The existing calibration `start` accepts optional `mode: automatic` (default:
`guided`). Its initial state is `confirm_automatic`; explicit action `run` with
the current sequence starts open/close/open. Events include `mode` and automatic
`run_index` (0, 1, 2), with `settling` between runs. Manual `open`/`close`/`endpoint`
actions are rejected for automatic sessions. Shared Stop, Cancel, heartbeat and
explicit Save semantics remain unchanged. See [the calibration contract](cover-calibration.md#using-automatic-measurement-0180).

Automatic dates describe receipt of actuator stop feedback, not proof of physical
endpoints. Their values remain provisional until saved into the same profile store;
export excludes those unsaved session values. No #349 options-store import, native
service adapter is introduced by this extension. Selected-cover execution is
added separately in 0.19.0 below.


## Selected-cover automatic extension (0.19.0)

Both new commands require administrator authorization before store access.

### Discover eligible targets

```json
{"id":30,"type":"myhome/cover_calibration/targets","entry_id":"ENTRY"}
```

The result is a fresh gateway snapshot:

```json
{"entry_id":"ENTRY","revision":4,"max_batch":20,"targets":[{"entity_id":"cover.bedroom","name":"Bedroom","reason":null},{"entity_id":"cover.kitchen","name":"Kitchen","reason":"calibration_moving"}]}
```

Only native MyHOME covers belonging to that entry are returned, sorted by entity
ID. `reason: null` means currently eligible; other reasons use the existing
profile/calibration error codes. This read reserves nothing and sends no commands.
The UI starts with all boxes unchecked. A missing/non-MyHOME entry is rejected.

### Start and subscribe to a selection

```json
{"id":31,"type":"myhome/cover_calibration/batch_start","entry_id":"ENTRY","entity_ids":["cover.bedroom","cover.kitchen"],"revision":4}
```

`entity_ids` must contain 1–20 distinct eligible covers of that gateway; duplicates
produce `invalid_selection`. The backend validates the entire selection, revision,
profile capacity and gateway ownership before creating a session. Request order
is execution order. Acknowledgement and subscription cleanup follow `start`.
The initial phase is `confirm_automatic`; only explicit `run` starts motion.

Events retain the ordinary session fields and add:

| Field | Meaning |
| --- | --- |
| `batch` | Always `true` for this session |
| `cover_index` | Zero-based current cover in the selected order |
| `targets` | Ordered `{entity_id, name}` labels from the current registry |
| `results` | Completed `{index, entity_id, values}` records; values contain opening/closing seconds |

Top-level `entity_id`, `values`, `elapsed` and `run_index` describe the current
cover. `between_covers` is the one-second pause before moving to the next target.
A single gateway owner, socket and heartbeat lease cover the entire selection;
there is no independent child session. Only the current cover is controlled.
Targets are revalidated before their turn and Save. A measurement failure or
interruption clears all provisional results and prevents later covers starting.

### Review and save together

```json
{"id":32,"type":"myhome/cover_calibration/action","entry_id":"ENTRY","session_id":"OPAQUE_SESSION_ID","sequence":19,"action":"save","names":["Bedroom measured","Kitchen measured"]}
```

Save requires `review` after every selected cover succeeds, the current sequence,
and exactly one valid name per cover in selection order. It accepts no target
changes or browser-supplied measurements. All profiles and assignments are
validated before one disk write; existing profiles remain. Revision increases
once and subscribers receive one committed invalidation. A validation/storage
error applies none of the proposed changes and retains review while connected.
Closing during an accepted disk write does not roll it back. Stop/Cancel, socket
cleanup, HA shutdown and lease behavior otherwise use the shared session contract.
Storage remains v4 and export remains v2, containing only committed profiles.


## Single-direction guided extension (0.20.0)

The existing admin-only `myhome/cover_calibration/start` accepts optional
`direction: opening` or `direction: closing` in guided mode:

```json
{"id":40,"type":"myhome/cover_calibration/start","entry_id":"ENTRY","entity_id":"cover.bedroom","revision":4,"direction":"closing"}
```

Omitting `direction` retains the full two-direction wizard. Combining it with
`mode: automatic` is refused with `invalid_profile`; batch start does not accept
this field. Under the profile lock, start validates the revision and target and
resolves the retained opposite time from committed cover settings. Since 0.22.2,
an assigned profile is optional: overrides, native timings and YAML/default values
also support a partial measurement. The retained value must be a valid travel time.
No browser-supplied profile ID, time or evidence determines the retained value.
Starting reserves the session and emits its initial state without any movement.

Single-direction states add `direction` to the ordinary session fields. Initial
`values` contains only the retained opposite time from the backend resolver.
Profile/override evidence is preserved; native evidence is preserved when complete
and compatible with the profile schema, otherwise represented as unknown in the
new profile. The original native fallback and metadata are never modified. `closing` starts in `confirm_open`;
`opening` starts in `confirm_closed`. The matching `close` or `open` action confirms
the physical starting endpoint, bus feedback starts the clock, and `endpoint`
records guided evidence and requests Stop. The next phase is immediately `review`.
The other leg cannot be started from review. The UI identifies which value was
measured and which was retained.

Save uses the existing action with `name` and current sequence. It creates and
assigns a new profile with both times, preserving supported opposite evidence
(including inherited origins or unknown metadata). Existing/shared profiles remain
unchanged. Storage v4, export v2, revision/ownership checks, error handling and
session cancellation semantics are unchanged. Unsaved values never enter export.
