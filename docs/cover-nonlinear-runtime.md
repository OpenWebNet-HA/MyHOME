# Optional slat/roll profiles — panel 0.30.0

Panel 0.31.0 adds [guided geometry measurement](cover-guided-geometry.md). The runtime and storage described here remain in use.

The [mathematical foundation](cover-nonlinear-model.md) is now connected to profile
storage, the shared resolver, impact previews and timed-cover commands. This
implements the profile/runtime part of the
[agreed calibration contract](https://github.com/Interstellar0verdrive/MyHOME-stability/blob/51ffaf73a3da3ac09246ee199751e613217e0a78/docs/calibration-contract-proposal.md).
Existing profiles retain their linear behavior. Nonlinear guided measurement,
fitting and independent accuracy checks are still future work.

## Opt-in profile geometry

The existing profile edit section and catalogue editor offer a model selector.
`Linear` leaves existing timing-only profiles unchanged. `Slats and roll` requires
three explicitly supplied values in the optional `geometry` object:

| Key | Meaning | Stored input validation |
| --- | --- | --- |
| `slat_time_s` | Common slat phase, included in each full run | Nonnegative and shorter than both full runs |
| `opening_roll` | Full/empty roll ratio in opening | 1–5 |
| `closing_roll` | Full/empty roll ratio in closing | 1–5 |

Values must be finite numbers; booleans and strings are rejected. The runtime
may produce larger effective rolls when adapting to a taller shutter. Complete
and effective directional times must remain in [1, 600] seconds, including after
personal timing overrides. Validation rejects the whole mutation before saving.
Unused profiles and orphan followers are also validated.

No coefficients are fabricated for existing profiles. The current timing-only
measurement flow does not measure slats or rolls, so the advanced fields require
values already established by measurement. The WHO shell, three collapsible cover
sections and separate profile catalogue are retained. The UI distinguishes
linear and slat/roll models and continues to report accuracy as not measured.

## API and precedence

Existing `save`, shared `preview`/`update_shared`, and catalogue `preview`/`update`
accept `profile.geometry`. For example, the shape is:

```json
{"name":"Measured profile","opening_time":26,"closing_time":20,
 "reference_travel_cm":150,
 "geometry":{"slat_time_s":2,"opening_roll":3,"closing_roll":2}}
```

These are test values, not recommended settings for an installation. Omitting
`geometry` when editing or copying retains the source geometry; explicit null
removes it and selects linear timing. Duplication retains all geometry and its
per-key evidence. Backend-owned `geometry_provenance` follows the same privacy
projection as directional provenance: only changed manual keys acquire a new
manual timestamp, while unchanged/unknown evidence stays unchanged. A browser
cannot submit measurement evidence.

`configured` and `effective` add the three geometry keys when applicable, each
with `value`, `origin`, `provenance` and `scaled`. The runtime consumes those exact
resolved values. Personal opening/closing times retain precedence and are never
scaled. Geometry overrides per cover are not yet exposed (`geometry_overrides:
false`); geometry currently follows the assigned profile.

With both travel distances present, the backend scales slats by the travel ratio,
each roll by its own geometric formula, and both curtain durations by the closing
roll's scale. Missing either dimension leaves the profile unscaled. A shared
**timing-only** measurement removes the target slat phase, undoes the curtain
scale and adds the reference slat phase before saving its directional time.
Geometry and the opposite direction's evidence are preserved.

Reads report the active `model` (`linear_time` or `slat_roll`), separate
`configured_model`, `position_known` and `pending`. An unbound cover has no active
model/effective values. The gateway overview declares `model: per_cover`, the
supported `models`, `nonlinear: true`, and `nonlinear_calibration: false`.
Accuracy remains `not_measured`. Profile rows identify their own model.

Shared-profile previews contain `geometry_changes` and `model_before`/
`model_after` for the reference profile and each affected follower. Assignment
previews include them per target. A roll-only edit is visible even when full-run
times stay identical. Revision-bound confirmation includes the exact geometry;
changing a model or coefficient invalidates the UI preview. Existing admin,
availability, gateway, calibration ownership and revision guards still apply.

## Runtime and restart

A running cover keeps an immutable effective model until it stops, including its
scheduled target duration. Geometry, height, assignment and timing edits expose
configured versus active values and defer activation until Stop.

The tracker keeps bottom-edge height and slat separation separately. Stops and
reversals preserve the fractional phase. A new queued direction retains the old
movement until its write/motor anchor. A failed new direction does not claim that
an already-running motor stopped. External direction/Stop frames use the same
tracker. Position duration is computed after the motor anchor; a queued target
already passed results in an immediate Stop request. The estimate continues
until Stop is actually written, including queue delay, rather than snapping to
the requested percentage.

After first enabling geometry or restarting HA, a saved integer percentage does
not establish the hidden slat state. Intermediate position requests are refused
with an actionable message until a complete opening/closing run establishes an
estimated endpoint, or an explicit calibration endpoint/actual position report
establishes a state. Open, Close and Stop remain available. An early Stop during
an unknown run leaves the state unknown. There is no automatic homing movement
on startup, model edits or assignment.

The existing bus-write/movement echo anchoring and Stop delivery behavior remain
in use. Profile-specific start delay and stop-latency settings are not exposed
in this increment; the mathematical model's fields alone do not imply those
measurements or compensation are implemented.

## Storage, export and rollback

Storage advances to **v8**. Migration from v7 backs up the exact original payload
as `myhome.cover_profiles.<entry_id>.pre_nonlinear` before replacing the main file;
revision, assignments, timings, overrides and dimensions are preserved. Backup
failure aborts and can be retried. Earlier migration paths remain available.
Entry removal also removes the new backup.

Export advances to **format 5**, adding geometry and sanitized per-key geometry
evidence to committed profiles. Internal MAC-bearing identities, credentials and
provisional calibration values are not exported. Export is not a restore API.
For rollback, stop HA and restore a compatible full backup or the original v7
`.pre_nonlinear` payload under the main-store key; subsequent edits are lost.
Fresh v8 installations have no pre-upgrade backup to restore.

## Checks

Automated checks exercise real HA storage/WebSockets, migration failure/retry,
public evidence, per-key retention, shared and assignment previews, nonlinear
normalization of partial measurements, invalid/atomic writes, gateway isolation,
Stop delay, in-flight edits, unknown state, restart, slat-phase reversal, external
frames and command failure. Frontend tests cover manual values, validation,
explicit opt-out and geometry-only preview invalidation in both editors.

Installation checks:

1. Existing profiles remain linear, with the same times and assignments.
2. With previously measured geometry, save and reopen the profile; both editors
   and the profile list show the model and values consistently.
3. Complete one opening or closing run before commanding an intermediate position.
   Check Stop/resume and confirm that returning to Linear restores timing-only
   behavior. A profile changed during motion becomes active only after Stop.

Physical accuracy needs the forthcoming guided readings and an independent
check. Passing simulation tests is not a centimetre-accuracy claim.
