# Shared cover settings — first implementation (panel 0.21.0)

This implements the first backend slice agreed in discussion #270, against
[Interstellar0verdrive's proposal at 51ffaf7](https://github.com/Interstellar0verdrive/MyHOME-stability/blob/51ffaf73a3da3ac09246ee199751e613217e0a78/docs/calibration-contract-proposal.md).
The preceding panel baseline is `e472ff4`; the integrated v2 base is `3f5e791`.
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
profile, including after restart. The override schema/resolver exists; a public
API for creating overrides and shared-profile edits is a subsequent step.

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
`height_scaling`, `nonlinear`, `override_write` and `shared_profile_write` are false.
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

## Verification and next steps

Regression coverage includes all four legacy profile versions, native fallback
precedence, backup/failure/retry, reset/restart, orphan native records, native
write failures, moving-cover parity, gateway isolation, public provenance,
authenticated WebSockets, and version-3 exports. Frontend coverage checks the
unscaled notice inside the existing assignment section.

The physical timing model and session exit behaviour are not changed by this
slice. Detached session supervision and gateway reservation, individual override
writes, shared-edit previews, geometry/scaling and nonlinear calibration remain
separate implementation steps. No numerical physical accuracy is claimed.
