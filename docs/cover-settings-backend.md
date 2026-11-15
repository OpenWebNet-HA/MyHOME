# Shared cover settings

> Panel 0.31.0 adds [guided slat/roll measurement](cover-guided-geometry.md): basic new-profile path, backend tape fitting and atomic review/save.

> Runtime baseline — panel 0.30.0: [optional slat/roll profiles and runtime](cover-nonlinear-runtime.md)
> add storage v8, export v5, explicit geometry, model-aware previews and motor
> tracking. Timing-only profiles remain linear; guided geometry measurement and
> physical accuracy checks are still pending. Older release notes below are historical.


**Current: panel 0.29.0**, storage v7, export v4. Optional reference/per-cover
travel enables linear height scaling through the shared resolver. Personal times
retain priority; existing profiles remain unscaled until both dimensions exist.
See [current schema, API, migration and runtime behavior](cover-travel-scaling.md).

## Historical backend baseline — 0.22.0–0.26.0

Panel 0.26.0 extends the shared store to **v6** for manual profile edits without an
origin cover. Existing v5 data is preserved with a `.pre_catalogue` backup before
migration. Export remains v3. See [profile management and rollback](sidepanel.md#profile-management-0260).
The v5 details below document the preceding backend version; resolution order and
native-service behavior are unchanged.

This implements shared persistence, individual overrides and shared-edit previews
from discussion #270, against
[Interstellar0verdrive's proposal at 51ffaf7](https://github.com/Interstellar0verdrive/MyHOME-stability/blob/51ffaf73a3da3ac09246ee199751e613217e0a78/docs/calibration-contract-proposal.md).
Current implementation: [`166b923`](https://github.com/xtimmy86x/MyHOME/commit/166b92324b714ad588f280ed1bd816d8e73c6017).
Previous implementation (0.21.0): [`d40f02f`](https://github.com/xtimmy86x/MyHOME/commit/d40f02ff11d9bdbd34adcd870afa12cef0d71f0d).
The preceding panel baseline is `e472ff4`; the integrated v2 base is now `c2e6424` (previously `3f5e791`).
The WHO navigation, device grouping and calibration screens retain their organisation.

## Supported contract

The model is `linear_time`. Profiles are explicitly `unscaled`; measured accuracy
is unknown (`accuracy.kind = not_measured`). No height, roll, slat, fitting or
precision-in-centimetres field is invented. Existing timing-only measurements
continue creating and assigning profiles without per-cover overrides.

`cover_settings.resolve` is the shared per-direction resolver used by the cover
runtime and the API. Precedence is:

1. A stored per-direction override, with its own provenance.
2. An assigned profile, applied without scaling.
3. A native timing imported from v2, or subsequently written by a native service.
4. The cover's YAML/default timing.

A native fallback retains its own source and date, if recorded; migration does not
claim that unknown evidence is a new measurement. The fallback remains below the
profile, including after restart. Individual overrides and shared profile changes
are now writable through the authenticated API documented below.

## Storage and migration

The existing HA store `myhome.cover_profiles.<entry_id>` advances to version **5**.
It contains `revision`, `profiles`, `assignments`, `covers` and `native_fallbacks`.
Assignments remain keyed by native cover unique ID. `covers` contains per-key
`overrides` with a validated value and provenance. The fallback map retains the
native device keys, including entries for devices not currently loaded.

Version 1–4 profiles migrate without changing IDs, assignments, revision or times.
The exact old payload and its major version are first backed up to
`myhome.cover_profiles.<entry_id>.pre_shared`. Native `cover_travel_times` options
are validated and copied into `native_fallbacks`, never into overrides. The
original Config Entry options remain unchanged as a pre-migration copy.
Only after the atomic write succeeds does binding publish the new runtime values.
Invalid native timings, missing assigned profiles, backup failure or main-store
write failure abort migration. The original data is retained so migration can be
retried. A completed version-5 store is authoritative and never reimports options;
reset values therefore cannot reappear on restart.

New native calibration/set/reset operations use the same store lock, revision and
atomic persistence as profile writes. They no longer update Config Entry options.
Native timing writes are still refused while a panel profile/session owns the
cover; panel mutations remain blocked by native calibration ownership. Persistence
failure leaves saved revision and active runtime timings unchanged. Changes made
while a cover moves become pending until the movement stops.

For a downgrade, stop Home Assistant and restore a compatible pre-upgrade backup.
The `.pre_shared` payload can restore the original profile file, and unchanged
options preserve the original native fallback. This restores the pre-migration
state, not settings edited since then. First-time version-5 stores have no legacy
profile file to back up. Export format **3** includes native fallbacks and overrides
with document-local cover references; export is not an implemented restore API.
Removing a Config Entry removes the main store and its `.pre_shared` copy.

## Read API

Existing `myhome/cover_profiles/read`, writes, subscriptions and calibration
endpoints remain available. Read adds `model`, `scaling`, `accuracy`, `configured`
and `effective`. Profile rows are marked with their model and scaling behaviour.

The new administrator-only request reads one gateway under the store lock:

```json
{"id": 1, "type": "myhome/cover_profiles/overview", "entry_id": "gateway-entry-id"}
```

The response has `schema_version: 1`, `storage_version: 5`, `revision`, model,
scaling, accuracy, `profiles`, `covers` and explicit capability flags.
`override_write` and `shared_profile_write` are true. `height_scaling` and
`nonlinear` remain false.
Profile rows list follower entity IDs; removed followers remain null entries.
Cover rows carry native names/entity IDs, profile assignment, availability,
advanced/pending flags, and directional `configured` and `effective` values.
Each directional value includes `value`, `origin`, `scaled` and public provenance.
Internal unique IDs and native device keys are not serialized.

`configured` is what the current committed settings resolve to; `effective` is
what the current movement actually uses. They can differ during pending updates.
For unbound covers there is no active runtime: `effective` is null. Assigned
profiles and identifiable native fallbacks remain readable, but unavailable
YAML/default values are null with origin `unavailable`. Advanced hardware-position
covers have no timing estimate. Registry changes affect names on the next read;
they are never copied into a parallel name store.

Use the existing gateway revision subscription to invalidate reads. Native writes
also increment that revision, so open editors reject stale writes. This subscription
is not a stream of physical movement or registry changes; reread for runtime state.

## Individual overrides and shared edits (0.22.0)

All operations use administrator-only `myhome/cover_profiles/write`, with the
existing `entry_id`, `entity_id` and optimistic `revision`. The common guards
still reject foreign/disabled/advanced/unavailable targets, shutdown and active
native/panel calibration ownership. Values are finite seconds in [1, 600]; clients
cannot supply provenance. Successful mutations persist once, increment the gateway
revision once and then update runtime/subscribers. Storage errors publish nothing.
Storage remains v5 and export remains v3; no new migration is required.

### Personal values

```json
{"id": 2, "type": "myhome/cover_profiles/write", "entry_id": "gateway-entry-id",
 "entity_id": "cover.bedroom", "revision": 12, "action": "overrides",
 "overrides": {"opening": 22.5, "closing": null}}
```

This patches opening only and removes any closing override. Omitted directions
are untouched; `null` removes that key and reveals the assigned profile, then
native/YAML/default fallback. An empty patch is invalid. Each changed value gets
backend-generated manual provenance; identical values keep their evidence.
Assignments and profiles are untouched, including when the last override is
removed. Personal values also survive assignment changes and restarts. Read and
overview already expose their values, origin and per-direction public evidence;
legacy cover `calibration_source` now reports `panel_override` when applicable.

The editor places these controls in its existing Advanced section. Check only the
directions to personalize; uncheck and save to resume inheritance. A status note
makes precedence explicit when assigning another profile. These controls are
independent of the profile name/timing fields in the Edit section.

### Shared profile preview and confirmation

```json
{"id": 3, "type": "myhome/cover_profiles/write", "entry_id": "gateway-entry-id",
 "entity_id": "cover.bedroom", "revision": 13, "action": "preview",
 "profile_id": "assigned-profile-id",
 "profile": {"name": "Shared profile", "opening_time": 24, "closing_time": 28}}
```

A preview is read-only. The selected cover must be assigned to this profile.
The response includes `revision`, `profile_id`, profile `before`/`after`, a
`confirmation` string and every stored `follower`. Each follower has a native
`entity_id`/`name` (null if removed), `available`, and directional `changes`:
`before`, `after`, `overridden`. These are **configured** times, not an assertion
of current movement timing. Masked directions explicitly retain the personal
value; unavailable and orphan assignments are included rather than omitted.
Native unique IDs are never returned.

After reviewing, send the identical target, revision, profile ID and profile,
with `action: "update_shared"` and the returned `confirmation`. The backend
recomputes confirmation for that exact proposal. Missing/mismatched confirmation
returns `preview_required`; intervening writes return `revision_conflict`.
The confirmation is a proposal checksum, not an authorization credential.
Administrator, target and ownership checks still run independently on every call.
Native calibration on **any** loaded follower blocks the shared commit.

Confirmation updates the existing profile and all its followers in one atomic
transaction, preserves per-cover overrides and preserves provenance on unchanged
profile directions. Stopped covers apply immediately; moving covers defer timing
until stopping. Unloaded covers resolve the updated profile when they bind.
The old `save` action still refuses globally editing a profile with multiple
followers; callers must use the explicit preview/confirmation path.

The editor shows the impact under the existing profile Edit section. Editing the
proposal or cancelling hides and invalidates confirmation. A remote revision
preserves the draft and requires reload; a late response cannot reopen a closed
or replaced editor. WHO navigation and the three collapsible sections are unchanged.

### Calibration interaction

The current measurement save still creates and assigns a new profile. Review
explicitly explains that prior personal values are removed **only after a successful
save**, so they cannot mask the accepted measurement. This also applies to batch
measurement. For a single-direction session, the other direction retains its
configured value and provenance, including a pre-existing personal override.
The original shared profile and its other followers remain unchanged.

## Verification and next steps

Local validation on Python 3.14 / Home Assistant 2026.9.1: **1,829 Python tests
passed, one existing skip, five snapshots passed; 100% line coverage (7,065
statements across 39 modules). All 98 frontend tests passed.** Ruff, the coverage
enforcer, HA architecture checks and the strict typing ratchet passed without
raising the baseline. The discovery regression test incorporates upstream’s `@callback` listeners,
removing the executor scheduling race.
After merging official v2 `c2e6424` (command-session/F454 identification fixes),
the complete suite passes **1,845 Python tests**, one skip and five snapshots,
with **100% line coverage (7,084 statements across 39 modules)**. The 98 frontend
tests remain passing; the merge does not change frontend files. Coverage enforcement,
Ruff, HA architecture and the strict typing ratchet pass.
GitHub Actions validates the published branch separately.

The preceding release passed all 10 official PR workflows; the user reported
successful installation testing before this step. The new override/shared-edit
flows still need physical installation testing.

Regression coverage now includes override patch/clear/restart and evidence,
native fallback restoration, shared impact with masked/offline/orphan followers,
exact-proposal confirmation, revision conflicts, durable-write failures, gateway
and administrator isolation, moving-cover deferral, and single-direction
calibration with pre-existing overrides. Frontend coverage exercises personal
values, previews, confirmation, dirty drafts and delayed responses.

Panel 0.24.0 retains a detached session and its gateway reservation for 10 minutes,
with [checkpoint recovery](sidepanel.md#session-recovery-0240). Unattended movement
is interrupted rather than supervised to completion. Geometry/scaling and nonlinear
calibration remain separate steps. No numerical physical accuracy is claimed.


Panel 0.23.0 implements the additional single-cover measurement save choices:
[personal values or a confirmed assigned-profile update](sidepanel.md#measurement-save-destinations-0230).
Both persist session evidence atomically through the same store. A partial
measurement updates only its measured direction in these destinations.
