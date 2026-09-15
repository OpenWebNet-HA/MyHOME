# MyHOME panel API: implemented reference

Status: **implemented in panel 0.9.0**, reviewed against
[`02ce199`](https://github.com/xtimmy86x/MyHOME/tree/02ce19908787297c1a6e2a65d56a289e766c0695).
This reference describes the prototype on `feat/myhome-sidepanel`, not an upstream
v2.1 commitment. The [shared-contract proposal](panel-shared-contract.md) is separate
and its new endpoints are **not implemented**.

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

Profile storage version **2**, panel version **0.9.0**, and any future API contract
version are different concepts. There is currently no API version negotiation.
This experimental API may evolve through an explicitly documented migration.

## Command map

| Command | Scope and result | Authoritative implementation |
| --- | --- | --- |
| `myhome/panel/inventory` | All configured gateways and native inventory; no `entry_id` parameter | `panel.py`, `ws_panel_inventory` |
| `myhome/cover_profiles/read` | One explicit gateway and native cover; complete profile snapshot | `cover_profiles.py`, `ws_read` |
| `myhome/cover_profiles/write` | Same target; `save`, `assign` or `delete`; updated snapshot | `cover_profiles.py`, `ws_write` |
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
It also serves as the explicit copy operation for a shared profile. There is no
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
back per key to English and finally the key. There is **no texts endpoint**.

The inventory listens to native entity/device/area registry events, debounced by
150 ms, and polls every 15 seconds while visible. It refreshes after visible-tab
resume. HA state updates refresh entity values and the open profile dialog's
effective timing/pending indicators without overwriting its draft.

The profile list/revision is fetched when opening or explicitly reloading the
dialog, and replaced by successful writes from that dialog. **Inventory polling
is not profile polling.** Another tab's profile edit/delete does not refresh the
open profile list automatically; the next write detects the revision conflict.
There is no profile subscribe endpoint, no preview and no undo.

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
