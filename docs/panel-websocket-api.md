# MyHOME panel API: implemented reference

> Panel 0.40.0 [pauses an automatic or batch cycle whose owner is absent](#pause-without-the-owner-0400) and keeps its measurements until the owner sends `continue`.

> Panel 0.41.0 adds the [accepted range of intermediate tape readings](#guided-geometry-intermediate-reading-range-0410) to guided roll measurement, shows profile values rounded and locks the cover's travel and personal values while a measurement runs.

> Panel 0.42.0: the backend adds a [check of the measured model](#guided-geometry-check-of-the-measured-model) to guided slat/roll measurement; the panel does not offer it yet, and shows a check run like an intermediate run.

> Panel 0.38.5 changes the [lift-off gap rule and adds covers without slats](#guided-geometry-lift-off-gap-and-covers-without-slats-0385) to guided slat/roll measurement.

> Panel 0.31.0 adds [guided slat/roll measurement](cover-guided-geometry.md): basic new-profile path, backend tape fitting and atomic review/save.

> Runtime baseline — panel 0.30.0: [optional slat/roll profiles and runtime](cover-nonlinear-runtime.md)
> add storage v8, export v5, explicit geometry, model-aware previews and motor
> tracking. Timing-only profiles remain linear; guided geometry measurement and
> physical accuracy checks are still pending. Older release notes below are historical.


> Current update — panel 0.29.0: [travel scaling](cover-travel-scaling.md)
> specifies storage v7, export v4, optional dimensions, the `travel` write action,
> and `myhome/cover_profiles/overview` (schema v1 with height scaling capability).
> Partial guided calibration now uses resolved cover settings without requiring
> an assigned profile (see the single-direction extension below).
> Write actions `overrides`, `preview` and `update_shared` add personal values
> and revision-bound impact confirmation; their complete contract is linked above.
> Existing endpoints remain available; native writes use the shared store and revision. Earlier release descriptions below remain historical where superseded.


## Guided geometry: check of the measured model

A check runs the cover from an end stop towards a height for the motor seconds the
model needs to get there, stops it on time, and compares the tape reading of the
bottom edge with where the model puts it. In a `mode: "geometry"` session it is
offered in `review` only: there the measurement is complete and nothing moves. The
panel does not send it yet; the screens arrive with the profile path that makes it
mandatory. From panel 0.42.0 a session that reaches a check is shown like an
intermediate run: no elapsed time during the check run, the positioning text and the
automatic Stop illustration, and the height illustration at its reading. The check's
own briefing and reading texts are not written yet.

`action: "check"` takes two optional keys, with the same owner, `sequence` and
writability rules as every other verb (`calibration_owned`, `calibration_step`,
`cover_unavailable`):

| Key | Meaning |
| --- | --- |
| `direction` | `"opening"` (default): from the bottom end stop upwards; `"closing"`: from the top end stop downwards |
| `target_cm` | Height of the bottom edge above its rest; default three quarters of the measured travel |

The default target is three quarters of the travel because the two intermediate
readings the roll measurement fits land near 40 % of it: a check at half the travel
would mostly repeat them. The default direction rises from the bottom, so that the
run crosses the slat phase and the opening roll. The check primitive itself
(`CalibrationCheck.plan`) aims at half the travel by default, for the check of an
assigned profile.

A value that is not one of the two directions, or not a number, fails the schema
(`invalid_format`). A check that says nothing about the curtain is refused with
`invalid_check`: a target outside 10 % to 90 % of the travel, not finite, or whose
planned run lasts no more than the slat time plus 1 s (a descent from the top to
90 % on a slow motor, for instance). Nothing is sent, the session stays in `review`,
and a verdict already there is kept. Any other mode refuses `check` with
`calibration_step`.

The check follows the rules of the measurement. Each movement has its own briefing
and starts only on `next`: `check` leads to the `home` briefing (upward check) or
the `top` briefing (downward check), whose run is confirmed with `endpoint`; then the
`check` briefing, whose run the backend stops after the planned seconds, measured
from the motor start as for the intermediate runs; then `phase: "reading"` with
`step` and `reading_kind` `"check"`. The `reading` action takes `reading_cm` from 0.1,
like every other tape reading, to the measured travel; anything else is refused with
`invalid_reading` and the step stays. The reading brings the session back to `review`
(`step: "half_close"`) with the verdict. Repeat on the check reading runs the same
check again, through its end stop briefing, keeping nothing of the previous run.
Save is offered as before, whatever the verdict.

**A check that ends early returns to `review`; the measurement is never lost to it.**

- Stop in one of its briefings, or at its reading, returns to `review` with the whole
  measurement and the verdict there was before the check (`null` if there was none).
  Stop is written, as Stop in `review` is.
- Stop while the cover returns to its end stop or runs the check (from the moment the
  movement is queued), an early stop reported by the bus (`unexpected_stop`) or a
  movement nobody asked for (`unexpected_movement`, also during a briefing) returns to
  `review` without a verdict (`check: null`). Stop is written, and the session no
  longer knows where the bottom edge is: Save then leaves the runtime position unknown,
  as after a restart, instead of seeding it from an earlier reading.
- `check_interrupted` gives the reason (`stopped`, `unexpected_stop`,
  `unexpected_movement`) until the next check or a repeated measurement; it is `null`
  otherwise and in every terminal phase.
- Every other interruption (unavailable cover, undelivered command, Stop not
  confirmed, timeouts, lease) ends the session as in the other geometry steps.
- Deliveries belong to their movement: a frame of the check (its run or its Stop)
  refused or acknowledged after the check ended, or after a later movement was queued,
  changes nothing, in any phase.

Geometry views add `check_threshold_cm` (`4`), `check_interrupted` and `check`:

| Key of `check` | Meaning |
| --- | --- |
| `direction`, `target_cm` | As requested, or the defaults |
| `check_seconds` | Motor seconds of the check run, from the motor start to the Stop write; `null` until the run has stopped |
| `expected_cm` | Where the model puts the bottom edge after `check_seconds`, to a tenth of a centimetre; `null` until then |
| `measured_cm` | The tape reading; `null` until it is entered |
| `deviation_cm` | `measured_cm − expected_cm` rounded to the whole centimetre, ties away from zero (4.5 → 5, −4.5 → −5); positive means above |
| `passed` | `true` when the absolute value of `deviation_cm` is at most `check_threshold_cm` |

`check` is `null` until a check is requested. It becomes a new object, without
results, when a check starts. After the reading it stays filled in `review`, Stop
there included, until the next check starts, a measurement step is repeated from
`review` (the model changes), a check is cut short while moving, or the session ends;
it is `null` in every terminal phase, the saved one included.

`expected_cm` follows the seconds the motor really ran, so a late Stop write is not
held against the model; `target_cm` is where the run was aimed. The deviation is
computed from the published `expected_cm`, so the two numbers on a screen never
disagree. The verdict uses an explicit half-up rounding: Python's `round()` rounds
half to even and would pass 4.5 cm. The verdict is not stored with the profile;
`accuracy` and `independent_check` keep their values. After a completed check, Save
seeds the runtime position from the check reading, the last tape observation.

The constants are in `cover_calibration_check.py`: `CHECK_THRESHOLD_CM`,
`DEFAULT_CHECK_DIRECTION`, `GEOMETRY_CHECK_FRACTION` (0.75), `HALF_TRAVEL_FRACTION`
(0.5), `MIN_TARGET_FRACTION` (0.1), `MAX_TARGET_FRACTION` (0.9),
`MIN_CURTAIN_SECONDS` (1.0) and `MIN_READING_CM` (0.1); the reasons that return to
`review` are `CHECK_RETURNS` in `cover_calibration_geometry.py`.

## Guided geometry: lift-off gap and covers without slats (0.38.5)

`start` with `mode: "geometry"` accepts an optional `slats` boolean, `true` by
default. With any other mode, `slats` is refused with `invalid_profile`; a value
that is not a boolean fails schema validation. A client that omits it keeps the
slat path and its steps, and its geometry views only add the keys below; the
lift-off gap rule below applies to it as well.

| Key | Meaning |
| --- | --- |
| `slats` | `false` for a cover measured without slats |
| `lift_attempts` | Number of the current lift-off run, starting at 1: every repeat of the run, automatic or with Repeat, adds one |
| `still_resting` | `true` in the `reset` briefing that follows a lift-off run discarded because the edge was still resting, until `next` starts the return to the bottom, Repeat is used or the session is interrupted |
| `touching_cm`, `max_gap_cm` | Lift-off gap limits in cm, `1.0` and `20.0` |
| `gap_warn_cm` | Gap from which a reading is accepted with a warning, `10.0` |
| `gap_warning` | `true` after a gap from `gap_warn_cm` to `max_gap_cm` is accepted, until `next`, Repeat or an interruption |
| `saved_travel_cm` | The travel already saved for the cover, or `null`; `travel_cm` stays the travel measured in this session |
| `lift_repeat` | `true` when a gap below `touching_cm` repeats the lift-off run, `false` when it is refused |

**Lift-off gap.** The `reading` that follows the lift-off run is the gap between the
bottom edge and its rest, in cm. From `touching_cm` to `max_gap_cm` it is accepted.
From `gap_warn_cm` on, the joint fit gives a less precise slat time: the gap is kept,
`gap_warning` becomes `true` and `can_repeat` is offered at the `reset` briefing that
follows. Repeat there discards the lift-off run (sample and gap), adds an attempt and
returns to the bottom before the lift-off runs again; `next` continues with the gap.
Below `touching_cm`, 0 included, the edge is still resting: the whole lift-off run is
discarded (`samples.lift` is removed and no `readings.gap` is kept), `lift_attempts`
grows by one, `still_resting` becomes `true`, and the session returns to the `reset`
briefing, which brings the cover back to the bottom before the lift-off run starts
again. Each of these movements still waits for its own `next`. There is no limit on
attempts; Cancel ends the session. Above `max_gap_cm` the reading is refused with the
new error `invalid_gap` and the step does not change; if the edge really rose
further, Repeat is the only way on and runs the lift-off again. Negative or non-finite readings, a missing reading and
a boolean keep `invalid_reading`; a string is still refused by the message schema
(`invalid_format`). Before 0.38.5 a reading of 0 meant "just
lifted, no measurable gap"; it now means "still resting".

The limits and the repeat are constants in `cover_calibration_geometry.py`:
`TOUCHING_CM`, `GAP_WARN_CM`, `MAX_GAP_CM` and `LIFT_REPEAT_BELOW_TOUCHING`. With
`LIFT_REPEAT_BELOW_TOUCHING = False`, a reading below `touching_cm` is refused with
`invalid_gap` and nothing is repeated. The 1 cm limit, the 10 cm warning and the
20 cm limit are still to be validated on real installations.

**Without slats.** With `slats: false` there is no `lift` and no `reset` step: the full
ascent follows the first bottom end stop. The intermediate ascent is scheduled to stop
at half the opening time, and its reading fits the opening roll alone with a slat phase of zero
(`opening_roll_fit`, the mirror of the closing fit: same 1–5 roll range, same
refusals; from 0.41.0 a reading outside `reading_range` is refused with
`reading_out_of_range`). Repeating the travel or the intermediate ascent returns
to the bottom through the `home` briefing; repeating the full descent, the
intermediate descent or the review still goes through `top`. Review and save carry `slat_time_s: 0`. The joint lift-off fit used with
slats is unchanged. Storage and export formats are unchanged.

Panel 0.38.5 shows a "This cover has no slats" switch in the calibration section
when **Roll measurement — guided** is selected, and sends `slats: false` only when it is
on. The lift-off text takes its limit from `touching_cm`, a status line gives the
attempt after an automatic repeat or a non-blocking warning after a wide gap, with
the values from the view, `invalid_gap` names both limits and points to Repeat, the briefings and illustration captions of a cover without slats do not
mention slats, and the review shows "No slats" instead of a slat time. From 0.38.5 the
tape reading field is a text field with a decimal keypad: it takes a comma or a point
as decimal separator, and an empty or non-numeric entry is never sent; the field is
marked invalid and a message asks for a number. From 0.38.5 the lift-off text says to
enter 0 when the edge did not rise at least `touching_cm`; a lift-off gap outside 0 to
`max_gap_cm` keeps "Use this reading" disabled and shows the `invalid_gap` text under
the field before anything is sent (Home Assistant still validates every reading); the
travel reading is prefilled with `saved_travel_cm` when known, and is sent only when
confirmed; review values are displayed rounded, tenths of a second and of a
centimetre and rolls to two decimals, while stored and sent values keep full precision. From 0.38.5 the runs that only bring
the cover to an end stop (`home`, `reset`, `top`) show no elapsed time, since nothing is
measured, and their text says which end stop to confirm; the measured runs keep it.

## Guided geometry: intermediate reading range (0.41.0)

While an intermediate reading is asked (`phase: "reading"`, `step` `half_open` or
`half_close`), geometry views add `reading_range: {"min_cm": …, "max_cm": …}`: the
heights for which the fit finds a roll from 1 to 5 for the stop just measured.
`min_cm` is the height with roll 5, `max_cm` the height with roll 1. For the
intermediate descent and for an ascent without slats the ends invert the winding
model in closed form; for the ascent with slats (joint lift-off fit) the rolls are
first limited to those that keep the fitted slat time from 0 to below both full
times, found by bisection, and the range follows from them. In every other phase
and step, and when no height fits (timings that contradict each other),
`reading_range` is `null`.

A reading outside the range is refused with the new error `reading_out_of_range`;
the step, samples and readings do not change, and Repeat stays available. Readings
inside the range are fitted as before; a reading that is not a number in cm, or a
reading for which `reading_range` is `null` and the fit fails, keeps
`invalid_reading`. `expected_cm` stays in the view for existing clients; panel
0.41.0 no longer shows it, because it matched the highest accepted reading (roll 1)
and was refused as soon as the stop did not fall exactly at half the time.

Panel 0.41.0 shows under an intermediate reading "For this cover the reading should
be between … and … cm. Enter what the tape says.", with the ends rounded to a tenth
of a centimetre inside the range, so that every value shown is accepted. A reading
typed outside the range keeps "Use this reading" disabled and shows the
`reading_out_of_range` text under the field before anything is sent; Home Assistant
still validates every reading, and its refusal names the reading sent and the same
range. The intermediate runs and the wait for their Stop show no elapsed time:
Home Assistant stops the cover at a computed time and nothing is pressed, so the
panel says that the cover is positioning itself (the runs are still measured).

**Profile dialog (0.41.0).** Times are displayed to a tenth of a second, the slat
phase to a tenth and the roll ratios to two decimals, with the user's decimal
separator; the calibration view's rounded values follow the language too. Stored
values keep full precision: a personal value field shows the rounded value and Save
sends back the saved value unless the field was edited. `write_profile` refuses
those writes with `calibration_busy` while a session of the same gateway is active
or its Stop is pending. While the profile read reports a calibration session whose
`phase` is not terminal (`interrupted`, `cancelled`, `saved`), or one that is
`waiting_for_stop`, the cover's travel, its personal values and their Save buttons
are disabled with a line explaining why. A `paused` session (0.40.0) is still
active and locks them too; an interrupted session that is still listed, so that it
can be opened, does not.

Known limitations of the lock:

- Sessions started without a `client_id` are not reported by the profile read, and a
  native calibration (`native_calibration_busy`) is not reported either: the fields
  stay enabled and Home Assistant still refuses the write with `calibration_busy`.
- An interrupted session whose Stop is still pending before the session closes is
  not reported as `waiting_for_stop`; the fields unlock and a write in those seconds
  is refused the same way.
- The end of a session changes no revision and is not pushed to the dialog: the
  fields unlock at the next read, that is "Refresh session status", returning to
  the browser tab (or the fallback polling), or a new revision.

## Multi-cover profile assignment (0.27.0)

The existing admin-only `myhome/cover_profiles/manage` endpoint adds two actions:

| Action | Additional fields | Result |
| --- | --- | --- |
| `preview_assign` | `entity_ids`: 1–200 distinct cover entity IDs | `entry_id`, `revision`, `profile_id`, `profile_name`, `targets`, `confirmation`; no mutation |
| `assign` | The same `entity_ids` plus `confirmation` | One atomic write, returning `entry_id`, new `revision`, `profile_id`, sorted `entity_ids` |

Both require the existing common `entry_id`, `revision`, `profile_id` fields.
Each preview target includes `entity_id`, registry `name`, nullable
`previous_profile_id` / `previous_profile_name`, and directional `changes`
(`before`, `after`, `overridden`). Values are resolved by the backend, including
native fallbacks, configured defaults and retained personal overrides. The token
binds the sorted selection, registry unique identities, preview and saved revision.
Reordering the same selection is harmless; changing targets or resolved values
requires a fresh preview. Internal identities are not exposed in the response.

Selection membership, gateway ownership, loaded/enabled state, availability,
standard timed-cover support and calibration eligibility are checked again before
commit. There are no partial successes. Missing/foreign targets use
`target_not_found`; malformed or repeated selections use `invalid_selection`
(or WebSocket `invalid_format` when rejected by the command schema). Eligibility
uses `cover_unavailable`, `advanced_cover` or `calibration_busy`. A panel calibration
reservation blocks all catalogue writes. Existing revision/storage errors apply.
No override, provenance, native fallback, unselected assignment or profile data is
modified. Running motion retains existing pending-application behavior.

Overview advertises `capabilities.profile_assignment: true` and adds nullable
`assignment_reason` to each cover. Null means currently eligible; the backend
always rechecks rather than relying on this UI hint. Storage v6 and export v3
remain unchanged.

## Gateway profile management (0.26.0)

`myhome/cover_profiles/manage` requires administrator access. Common fields are
`entry_id`, `revision` (nonnegative integer), `profile_id` and `action`:

| Action | Additional fields | Result |
| --- | --- | --- |
| `preview` | `profile`: name, opening_time, closing_time | Before/after values, every follower with directional override masking, exact-proposal confirmation token; no mutation |
| `update` | The same `profile` plus `confirmation` | Atomically updates the existing profile and applies/defer timings to its followers |
| `duplicate` | `name` | Independent backend copy of the saved times/evidence, no assignments |
| `delete` | None | Deletes only if no stored association references the profile |

Names are trimmed, nonempty and at most 64 characters. Times use the existing
finite 1–600 second validation. Action-specific extra fields are rejected: the
browser cannot inject associations, provenance, timings into a duplicate or a
cover identity into a catalogue operation. Write results contain `entry_id`,
`revision` and `profile_id` (the new ID for duplication). The existing overview
and revision subscription refresh the UI. Overview now advertises
`capabilities.profile_management: true` and `storage_version: 6`.

`revision_conflict`, `preview_required`, `profile_not_found`, `profile_in_use`,
`profile_limit`, `calibration_busy`, `invalid_profile`, `target_not_found`,
`cover_unavailable` and `storage_error` reuse the existing error vocabulary.
The token is bound to the gateway, saved revision, profile ID and normalized
proposal, with a null cover context; it cannot reuse a cover-editor confirmation.
No availability requirement is imposed on an arbitrary reference cover. HA shutdown
and entry removal/replacement are revalidated under the gateway lock.

Only changed times get new manual provenance with a backend date and null origin.
Unchanged directions and duplicates preserve original evidence. Guided/automatic
measurements still require an origin. See [migration/rollback](sidepanel.md#storage-v6-and-rollback).

## Shared profile overview UI (0.25.0)

The WHO 2 **Profiles** view consumes the existing `myhome/cover_profiles/overview`
response and `myhome/cover_profiles/subscribe` notifications. It joins each
association's entity ID with the gateway-scoped panel inventory for native names,
areas and A-PL. Effective/configured values and provenance are taken directly from
the backend, never recalculated from the displayed profile. Missing runtime values
are shown as unknown. No new endpoint, persisted field or write operation is added.
See [behavior and scope](sidepanel.md#shared-profile-view-0250).

## Session recovery (0.24.0) and ownership per tab (0.39.0)

`start` and `batch_start` accept optional `client_id` (nonempty string, at most
64 characters). Omitting it preserves the legacy contract unchanged: one
controller bound to its websocket, a 20 s heartbeat lease, and cancellation
(with Stop) when the subscription ends. Everything below applies only to
clients that send `client_id`, except Stop during `review` (see **Actions**).

**Identity.** Since panel 0.39.0 the `client_id` is kept per browser tab in
`sessionStorage`, so the same tab is recognised after a reconnection, a reload
or when the view is reopened. The client that starts a session owns it. A
browser's "Duplicate tab" copies `sessionStorage`, so before using an identity
it did not create the panel asks the other tabs, over a `BroadcastChannel`,
whether one of them holds it. An answer within 300 ms means the tab is a copy:
it takes a new `client_id`, so it opens sessions read-only until it takes
control. No answer means a reload of the same tab: it keeps the identity and
recovers its sessions as their owner. The check runs when the panel loads, so a
tab answers for its identity before any calibration view is opened; two copies
checking at once settle on exactly one keeper. An answer that arrives after the
wait still counts: the copy then takes a new identity and reads its open session
again, read-only. Without `BroadcastChannel`, or when it cannot be opened, the
identity is kept as before. Only a tab running the MyHOME panel answers: if the
original tab has been closed, frozen by the browser, or reloaded on another page
of Home Assistant, nobody answers, the copy keeps the identity and ownership
passes to the copy; the original, once it loads the panel again, finds the
identity held and takes a new one.

**Readers.** Any number of subscriptions can read one session: the owner's, a
replayed one after a reconnection, and other tabs. Each subscription receives
its own `attachment` token. `sequence` grows only at transitions of the session
(including a change of owner), never at a subscription, a heartbeat or a read.

**A lost socket does not interrupt the movement under way.** When a subscription
ends (socket closed, unsubscribe), that reader is removed: no Stop is written and
no value is discarded. An automatic or batch cycle still needs its owner present
to start its next movement (see below).

**Presence and lease.** The owner is *present* while its last heartbeat, action
or successful claim is less than 45 s old; the panel sends a heartbeat every 15 s,
and at once when its subscription is replayed after a reconnection. A subscription
alone (`resume`, a replayed start) never renews presence. Every view reports it as
`owner_present` (panel 0.39.0). Presence never decides who may act, but an
automatic or batch cycle starts a new movement by itself only while the owner is
present: when a pause ends (next run, next cover) without the owner, the session
enters `paused` (panel 0.40.0, see [Pause without the owner](#pause-without-the-owner-0400)):
every measured value is kept, results of covers already measured in a batch
included, nothing moves, and Stop is written only if a movement may still be
running. A movement already under way runs to its end whether or not the owner
is present. The Home Assistant frontend closes the websocket of a tab hidden for
five minutes (unless suspension is turned off), and at once when the browser
freezes the page, so a cycle left in a background tab pauses this way.
The *lease* ends the
session as `expired` after 1800 s without a transition or an accepted action
of the owner, or 600 s once a movement has been sent; Stop is written at that
moment only while a movement may still be running. A heartbeat never renews the
lease and never takes ownership.

**Ownership changes only on an explicit claim.** A replayed `start`/`batch_start`
from a known `client_id`, for the same target, mode and direction, and a
`resume` without `claim`, only add a reader: an owner is never displaced by a
reconnection or a repeated subscription.

**Replayed starts.** Home Assistant repeats a subscription message after its
websocket reconnects. `start` and `batch_start` accept an optional `session_id`
(1 to 64 characters): with it, the message only reads that live session and is refused with
`calibration_expired` when the session has ended or another one holds the
gateway; it never creates a session. Panel 0.39.0 adds `session_id` to its start
message when the connection drops or the view closes, so a replay after a session
has ended (lease expiry, Cancel from another tab) shows the error instead of
starting a new measurement. A replay without `session_id` (for instance, when the
connection dropped before the first view arrived) behaves as before: it reads the
live session when there is one, and otherwise creates a fresh confirmation step,
never a movement. A start sent deliberately by the user carries no `session_id`
and creates a session as usual. Clients without `client_id` never send
`session_id`; if one did, the start would be refused with `calibration_expired`
(before this key existed it was refused as unknown).

**Cancel from the panel.** Panel 0.39.0 closes the view on Cancel only once Home
Assistant confirms it, or confirms that the session no longer exists. When the
connection is down the view stays, says that the cancellation did not arrive, and
sends it again first as soon as its subscription is replayed. A
`calibration_expired` answer may only mean that the token died with its socket, so
the panel then subscribes with `resume` (no claim, no command) and closes the view
only if that is refused with `calibration_expired` too.

To take control, a client subscribes with:

```json
{"id": 50, "type": "myhome/cover_calibration/resume", "entry_id": "ENTRY",
 "session_id": "SESSION", "client_id": "TAB-ID", "claim": true, "sequence": 12}
```

The claim takes effect only when `sequence` is the current one, so an automatic
replay of an older claim cannot take the session back. The result acknowledges
the subscription, followed by the view (after a claim, a transition sent to every
reader). `calibration_expired` rejects an absent, ended or legacy session.
Neither `resume`, a claim nor a replayed start sends any command to the bus: a
recovered session never restarts a movement by itself.

**Actions.** Every `action` must carry the `attachment` of a subscription on the
same websocket. `stop` is accepted from every reader, read-only included: it is
a safety control. During a run, and in every phase but `review` and `paused`, its
effect is unchanged: the session is interrupted and its values are discarded. In
`paused` Stop is written and the session stays paused with its measurements. In `review`
the endpoints are already fixed by the user's taps: Stop is still written, and
the session always stays in `review` with its completed measurements (batch
results included), also in the short window before the bus confirms the last
stop; `cancel` is the only way to discard them. This applies to clients without
`client_id` too. For clients with `client_id` a Stop in `review` or `paused`,
like every accepted action, renews the inactivity lease. Every other action except
`heartbeat` and `detach`, `save` and `continue` included, is refused with
`calibration_owned` unless it comes from the owner.
`heartbeat` answers the view and refreshes presence only for the owner. `detach`
removes that reader; from the owner it ends the session as `left` (without Stop)
when nothing has moved yet or the session was interrupted, and otherwise keeps
the owner and the measurements.

**Views.** Subscription and action views add `owner` (this reader owns the
session), `read_only`, `attachment`, `attached` (this subscription is live),
`owner_present` (the owner is present, the same for every reader; `false` once
closed), `idle_expires_at` (ISO time the lease will run out, `null` when
closed), and since 0.40.0 `next_step` and `paused_at` (both `null` unless the
session is `paused`); `recoverable` and `recovery_seconds` (the current lease
length) remain.
A reader learns that presence has lapsed from its own heartbeat answers, since a
lapse is not a transition. A claim sent with an old `sequence` is answered with
a read-only view, not an error, so that a replayed claim stays harmless; the
panel shows that the session changed and asks the user to try again. After a
`calibration_expired` answer the panel no longer offers to resume the session.

**Panel entry points (0.39.0).** The profile dialog offers "Open session" for
every session that reports `owner_present`, whether the owner is present or not:
the subscription is a `resume` without claim, so the owner's tab, after a reload
for instance, gets its own view back at once, and any other tab gets the
read-only view with Stop available. In a read-only view, "Take control" claims
with one tap when the owner is absent; while `owner_present` is true the first
tap only turns the button into "Confirm take control" and shows what follows
(the guiding tab becomes read-only; a run under way continues and its endpoint
is then confirmed from the new owner), and the second tap claims. Nothing is
sent before that second tap.

`cover_profiles/read` includes `calibration`: null, or the same view without
`attachment`, where `attached` equals `owner_present` and `owner` is false.
This transient view does not increment the persisted profile revision. Storage
v5 and export v3 are unchanged; transient sessions are not included in export or
restored after HA restart.

## Pause without the owner (0.40.0)

Applies only to automatic and batch sessions started with `client_id`. When the
one-second pause of a cycle ends (`settling` before the next run, `between_covers`
before the next cover) and the owner is not present, the next movement does not
start and the session enters the phase **`paused`**. It is not terminal:

- `reason` is `owner_absent`; `values`, `run_index` and, in a batch, `results`
  and `cover_index` keep what was measured.
- `next_step` names the step that was due:
  `{"step": "closing" | "opening", "run_index": 1 | 2, "entity_id": ...}` for the
  next run of the current cover (`run_index` zero-based, as in the view), or
  `{"step": "next_cover", "cover_index": n, "entity_id": ...}` for the next
  cover of a batch (`cover_index` zero-based, as in the view, so `n` is the
  index of the next cover in `targets`). `paused_at` is the ISO time of the pause.
- Nothing moves and no timer brings the cycle back. Stop is written at the pause
  only if a movement may still be running, as before.
- The pause is a transition: `sequence` grows and the lease, unchanged at 600 s
  for a session that has moved, restarts from it. It is not lengthened. When it
  runs out the session ends as `expired`, without Stop since nothing moves, and
  the values are discarded as for every end. Like every accepted action, a Stop
  in `paused` renews the lease, and so does a successful claim (a transition);
  a claim replayed after a reconnection carries an old `sequence` and does not.
- Only the cover named by `next_step.entity_id` can end the pause. In a batch
  paused between covers the session is bound to the next cover instead of the one
  just measured, so the top-level `entity_id` of the view is already the next
  cover while `cover_index`, `values` and `results` still describe what was
  measured. Movements of covers already measured do not reach the session.

The action **`continue`** (with the current `sequence`) is accepted only from
the owner and only in `paused`; anywhere else it is refused with
`calibration_step`, from a reader with `calibration_owned`. It starts the step
named by `next_step` with the same checks that step always has: the target is
revalidated (in a batch with the eligibility checks of the next cover:
available, writable, not moving), then the movement is queued with its guard
and start timeout. When a check fails nothing has moved: the error code is
returned (`cover_unavailable`, `calibration_moving`, ...) and the session stays
`paused` with its measurements, so the owner can try again. Only a full command
queue (`command_queue_full`), found while queuing the movement, interrupts the
session as before. The answer is the new view
(`starting_open` or `starting_close`, `reason`, `next_step` and `paused_at`
back to `null`). A reader that wants to continue first takes control with
`resume {claim: true, sequence}`; the owner is absent, so the panel claims with
one tap.

| Action in `paused` | From | Effect |
| --- | --- | --- |
| `continue` | owner | Starts `next_step`; the cycle goes on as if the pause had ended with the owner present |
| `continue` | owner, a check fails | The error code; the session stays `paused` with its measurements |
| `continue` | reader | `calibration_owned`; nothing changes |
| `heartbeat` | owner | Answers the view; the owner is present again (`owner_present: true`) but nothing resumes |
| `stop` | any reader | Stop is written; the session stays `paused` with its measurements (`stop_requested: true`) |
| `cancel` | owner | Ends the session as `cancelled` and discards every value and batch result |
| `detach` | owner | Removes that reader; the session stays `paused` until the lease runs out |
| `run`, `open`, `close`, `endpoint`, `save` | owner | `calibration_step` |

A movement reported by the bus during the pause (a wall switch, an automation)
on the cover named by `next_step` interrupts the session as
`unexpected_movement`, as it does in every waiting phase: Stop is written to that
cover and the values are discarded. A Home Assistant command on that cover
interrupts it as `external_command`. As in every phase, a gateway or cover that
becomes unavailable during the pause interrupts the session as
`cover_unavailable`; the pause makes that window longer. Clients without
`client_id` have no presence and never pause; for them the cycle goes on after
each pause as before, and `continue` is refused with `calibration_step`.

**Panel.** The paused view says that the measurement stopped because no tab was
following it, that the measurements already taken are kept, what "Continue"
will start (the run, or the next cover by name) and when the lease ends, in the
browser's local time (not the time zone chosen in the Home Assistant profile),
with the date when it falls on another day. A refused `continue` shows its error
and leaves "Continue" available. The owner gets "Continue" as the primary button, "Cancel the measurement"
and Stop. A reader sees the same information and "Take control", then
"Continue". The profile dialog shows that the session is paused and offers
"Open session". Opening the view sends no command.

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

The shell displays all gateway cards and selects one gateway at a time. A profile
dialog always receives exactly one `entry_id`. An explicit missing gateway is an error in the shell; it does not
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
