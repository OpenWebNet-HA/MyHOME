# Optional cover travel scaling — panel 0.29.0

Implements the linear case of sections 1.2–1.7 of the
[agreed calibration contract](https://github.com/Interstellar0verdrive/MyHOME-stability/blob/51ffaf73a3da3ac09246ee199751e613217e0a78/docs/calibration-contract-proposal.md).
The model remains `linear_time`, with accuracy `not_measured`. Slat/roll fitting
and out-of-sample physical accuracy checks remain future work.

Panel 0.30.0 adds [optional nonlinear profiles](cover-nonlinear-runtime.md).
The linear behavior below remains valid for profiles without geometry. The user
confirmed the 0.29.0 linear adaptation on the test installation.

## Data and runtime

Profiles optionally store `reference_travel_cm`; covers optionally store
`travel_cm`. These are actual vertical travel distances of the bottom edge
between fully closed and fully open, not necessarily window/frame heights.
Identity remains gateway + native cover unique ID. No dimensions are invented.

When both dimensions exist, inherited times are
`profile_time * travel_cm / reference_travel_cm`, rounded to four decimal places.
Otherwise profile times apply unchanged. Personal per-direction overrides take
priority and are never scaled. Native fallbacks and YAML/default timings keep
their meaning. Dimension edits do not fabricate timing provenance.

The same `cover_settings.resolve` supplies runtime, API and preview values,
including offline/orphan followers. Dimensions must be finite numbers in
[0.1, 10000] cm; booleans and numeric strings are rejected. Stored and effective
profile times must stay in [1, 600] seconds. Invalid scaling rejects the complete
mutation before persistence, without clipping. Moving covers retain their active
values and scheduled Stop until movement ends; geometry edits send no bus frames.

## API

Existing admin, gateway, revision, availability and calibration guards apply.
Save a cover's travel independently of its assignment and personal times:

```json
{"id":1,"type":"myhome/cover_profiles/write","entry_id":"gateway",
 "entity_id":"cover.bedroom","revision":12,"action":"travel","travel_cm":150.5}
```

`travel_cm` is required for this action; null removes it. Existing profile save,
shared preview/update and catalogue preview/update accept `reference_travel_cm`
inside `profile`. Omission preserves an existing reference on edits/copies;
explicit null removes it. Duplication retains it. Shared edits still need an
exact-proposal confirmation; follower changes now include `scaled` per direction.

`read` adds `travel_cm` and `height_scaling: true`. Its `scaling` describes the
assigned profile's applicability (`height` when both dimensions exist, otherwise
`unscaled`). `configured`/`effective` contain the definitive directional values
and flags, including personal overrides. Profile rows report `height` when they
have a reference. `overview` keeps schema version 1 (additive fields), reports
storage version 7 and `scaling: optional_height`, advertises `height_scaling: true`,
`nonlinear: false`, and includes dimensions on cover/profile rows. The browser
does not calculate timings. Unbound covers have no effective runtime values.

## Measurements and UI

New guided/automatic profiles use the cover's saved travel as their reference
when present. Clearing old overrides preserves travel. Batch profiles use each
cover's own travel. Review shows the reference before Save. Timing-only covers
still create unscaled profiles; personal measured seconds are never scaled.

Updating a referenced shared profile converts the measured seconds back to its
reference: `reference_time = measured_time * reference_travel_cm / travel_cm`.
This avoids double scaling. Partial measurements preserve the other direction's
reference time and evidence. Missing cover travel returns
`profile_reference_required`, retaining review and alternative save destinations.
An unscaled shared profile remains unscaled until its reference is explicitly
entered; no reference is inferred for an untouched direction.

WHO layout and the existing sections are retained. Travel and a separate Save
button are in the assignment section; reference travel is in the profile editor
and catalogue. Empty fields opt out of scaling. Unsaved travel prevents starting
calibration until saved or restored. Summary, list and previews distinguish
adapted, personal and unscaled times. Assignment explains both-dimension
requirements and unknown linear accuracy. Dirty drafts survive remote edits;
runtime completion refreshes effective values without discarding a draft.

## Migration, export and rollback

Storage advances from v6 to v7. Migration first backs up the exact v6 payload in
`myhome.cover_profiles.<entry_id>.pre_travel`. Existing profiles, overrides,
native fallbacks, assignments and revision remain unchanged; dimensions remain
absent. Backup/main-write failure aborts and permits retry. Earlier migration
paths retain their existing backups. Entry removal also removes `.pre_travel`.

Export format 4 includes reference travel on profiles and travel on document-local
cover records when present, including orphan records. It contains committed data,
not draft/session state or credentials, and is not a restore API. To downgrade,
stop HA and restore a compatible complete backup or the v6 `.pre_travel` payload
under the original main-store key. That copy is the pre-upgrade state and loses
subsequent edits. Fresh v7 installs have no pre-upgrade file to restore.

## Installation checks

1. Existing profiles retain their times and assignments after upgrading.
2. A test profile with 200 cm reference and 20/30-second times, applied to a cover
   with 150 cm travel, resolves to 15/22.5 s. Example dimensions must not replace
   real installation measurements.
3. A personal opening time stays unchanged while closing is inherited. Clearing
   either dimension restores unscaled inheritance.
4. Shared-edit and assignment previews match follower geometry and overrides;
   save/reload preserves dimensions.
5. Calibration review shows the saved travel. A shared update preserves actual
   measured times on the measuring cover after reference normalization.

Automated checks cover migration failure/retry, real HA storage and WebSockets,
preview/runtime parity, restart, validation, gateway isolation, pending Stop
timing, single/batch measurement saves and frontend draft/review behavior.
Physical accuracy still requires installation measurements; linear scaling does
not claim the nonlinear model's precision.

Local validation on Python 3.14 / HA 2026.9.1: 2,081 backend tests passed (one
skipped), 161 frontend tests passed, and all 46 integration modules reached 100%
line coverage (7,843 statements). Strict typing reports zero errors; Ruff and
Home Assistant architecture checks pass. The user subsequently tested the 0.29.0
linear adaptation on the installation and reported that it works correctly;
this does not establish nonlinear positioning accuracy.
