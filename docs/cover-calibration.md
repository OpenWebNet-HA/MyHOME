# Guided and automatic travel measurement — panel 0.22.0

Panel 0.21.0 adds the [shared persistence/resolution backend](cover-settings-backend.md)
(storage v5, export v3). The guided/automatic measurement sequence described here
is unchanged; native timing services now persist in the same store.

Panel 0.22.0 adds personal overrides in the existing editor. Saving a measured new
profile removes that cover's old overrides only after persistence succeeds, so the
cover follows its accepted measurement. Review explains this before saving.
Single-direction measurement retains the other direction's configured value and
evidence, including a previous override. Batch saves apply the same cleanup atomically.
The measurement sequence and session ownership remain unchanged.

This experimental wizard measures **one standard cover's full opening and closing
times**. It extends our linear timing profiles; it is not the calibration fork's
height/roll/slat model or the still-proposed shared API. The operator confirms
physical endpoints in guided mode; automatic mode measures actuator feedback.
Neither mode claims automatic physical endstop detection.

## Using guided measurement

1. With the normal controls, put the cover fully closed and stopped.
2. In the MyHOME panel, expand its WHO 2 device, open **Travel profile**, and choose
   **Guided measurement**, then **Calibrate travel times**. The session starts without moving the cover.
3. Confirm it is fully closed to request opening. The wizard waits for opening
   feedback on the bus; only that feedback starts the backend's monotonic timer.
4. As soon as it is fully open, select **Fully open: record time and request Stop**.
5. Check that it is fully open **and stopped**, then request closing.
6. At the closed endpoint, select **Fully closed: record time and request Stop**.
7. Review both measured times, enter a profile name, and explicitly save. This
   creates and assigns a **new** profile, retaining all existing profiles.

Stop interrupts the measurement and discards provisional times. Cancel closes the
session; restarting requires a new session. Normal HA commands remain usable but
interrupt measurement and request Stop. Unexpected bus reversal/movement or a stop
before endpoint confirmation also interrupts it. A same-direction repeated bus
status during a measured leg does not restart the timer.

**Stop requested is not stop confirmed.** Commands use the existing gateway queue;
the UI reports the request, not a physical acknowledgement. If the queue is full,
the error explicitly says Stop could not be queued and is logged. If the gateway
is unavailable, use the physical control. Do not use other controls while measuring.

The timers exclude time spent waiting in the command queue, but include operator
reaction time, bus feedback latency and the endpoint click's trip to HA. The screen
updates elapsed time with its five-second heartbeat; this display cadence does not
set measurement precision. If the gateway/device does not produce movement feedback,
the wizard times out and the ordinary manual profile editor remains usable.

## Using automatic measurement (0.18.0)

Choose **Automatic measurement** in the Travel profile dialog, then open the
calibration view. Starting the session does not move anything. Read the notice,
check the cover is stopped and the travel area is clear, then explicitly select
**Confirm and start the automatic cycle**. The backend performs three runs:
initial open for positioning, close for the closing time, and open for the opening
time, with one-second pauses between runs. Only the latter two are saved after
review. The panel shows run/phase, backend elapsed time, Stop and Cancel.

This adapts #349's sequence and 59–65-second guard into the existing socket-owned
session and guarded command queue. It does not import #349's config-entry timing
store or native calibration services/buttons. Both panel modes share ownership,
lease, revision checks, profile persistence and interruption/cleanup behavior.

Each run requires matching bus movement feedback within 10 seconds of its request.
There is no command-write or browser-clock fallback. Only a standard actuator
stop status (`state == 0`) completes an automatic run. A stop within 0.15 seconds
of the movement anchor is ignored as a possible echo; the deadline remains active.
A run in the inclusive 59–65-second window interrupts the entire cycle immediately.
No stop within 180 seconds also interrupts and requests Stop. Measured closing and
opening runs must be at least one second; the initial positioning run may be
shorter. All provisional times and provenance are discarded on interruption.

An actuator stop is not physical endpoint proof. A 30-second actuator timer on a
14-second shutter may still produce a 30-second result; the factory-cutoff guard
does not detect arbitrary installer timers. A wall-button stop may be
indistinguishable from an actuator stop. Do not use other controls during the run;
observe the motion and reject implausible results. Advanced covers remain excluded.
No endpoint-confirmation action is accepted in automatic mode, and no physical
0/100 position is asserted by the automatic controller.

After the third run the session enters **review**, with no profile changes yet.
Explicit Save creates and assigns a new profile using the common atomic store.
Each measured direction has `source: automatic` and a UTC date captured on its
stop feedback. Copy/edit/assignment and export retain the existing provenance
rules. Save failure preserves the review. Stop/Cancel or socket/lease loss during
any run or pause invalidates subsequent queued movement and cancels pending pauses.
Before another run, the controller revalidates the gateway/cover and HA state.

For the API, pass `mode: automatic` to the existing `start` request (omission
remains guided), then send `action: run` with the current sequence from
`confirm_automatic`. Intermediate states are `starting_open`, `opening`,
`settling`, `starting_close`, `closing` and finally `review`. `run` is rejected in
guided mode; `open`, `close` and `endpoint` are rejected in automatic mode.
New interruption reasons are `automatic_cutoff`, `automatic_timeout`,
`automatic_invalid` and `target_not_found` if the target disappears during a pause.

Validate on one supervised physical cover: compare both times with a stopwatch,
check that the actuator reports start/stop, cancel during motion and a pause, and
confirm there is no subsequent run or saved profile. Automated simulated-bus tests
do not establish compatibility with a particular actuator's endpoint reporting.

## Lifetime and persistence

A session belongs to one HA WebSocket connection and one config entry/cover. There
is at most one session per gateway. While it is active, ordinary profile writes on
that gateway are rejected with `calibration_busy`. Other gateways remain independent.
The current profile read response still reports normal target writability; the
backend enforces the calibration lock on writes/start requests.

Guided session phases are:

| Phase | Allowed progression |
| --- | --- |
| `confirm_closed` | Explicit `open` confirms the closed, stopped starting endpoint |
| `starting_open` | Await matching bus movement after guarded dispatch |
| `opening` | `endpoint` confirms fully open, records opening seconds and queues Stop |
| `confirm_open` | Explicit `close` confirms fully open/stopped and requests closing |
| `starting_close` | Await matching bus movement after guarded dispatch |
| `closing` | `endpoint` confirms fully closed, records closing seconds and queues Stop |
| `review` | Inspect both values, then `save` with a name |
| `saving` | An accepted configuration write is in progress |
| `saved` | New profile persisted and assigned; session ownership released |
| `interrupted` | Values discarded; Stop can be retried; Cancel releases the session |
| `cancelled` | Ownership and timers released |

Stop/Cancel do not require a current step sequence. The frontend keeps Stop
available while another call is pending. An interrupted session cannot resume a
partly completed measurement. A new start is refused until it has been closed.

The frontend sends a heartbeat every **5 seconds**. The backend cancels after
**20 seconds** without one. Closing/navigating away unsubscribes and requests
cancellation; socket loss, cover unload and HA shutdown also release ownership.
A hidden/suspended browser may lose its lease. Nothing automatically resumes on
reconnect or restart. Unsaved measurements and live sessions are never persisted.

Movement must be dispatched and produce bus start feedback within **10 seconds**
of the request; a guided leg is limited to **600 seconds**, an automatic leg to
**180 seconds**. Movement queue jobs
carry a validity guard checked immediately before sending, after taking a shared
calibration-command lock across workers. Cancelled/expired queued movement is
skipped. Stop queue jobs have a **30-second dispatch expiry** to avoid replaying
old stops after a long backlog/reconnect. This is best-effort physical control,
not a guarantee of delivery or a substitute for the shutter's physical limits.
An already dispatched operation cannot be recalled from the network.

The operator's endpoint confirmations set the runtime position baseline to 0/100.
A bus Stop alone is never treated as proof of an endpoint. Existing travel times
remain unchanged until Save, which reuses the revision-checked atomic profile
store. It sends no movement command and never overwrites a shared profile.
Save/storage errors preserve the review while the session remains connected.
If cancellation races a save that has already entered persistence, that accepted
save may finish; closing the wizard does not roll it back. Reopen the profile
editor to see the authoritative assignment after an uncertain response.

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

## Selected-cover automatic measurement (0.19.0)

**Travel profile → Calibrate a selection** lists this gateway's native covers.
Select 1–20 eligible covers explicitly; no box is initially checked. Continue to
review the selection, then confirm the cycle. Execution follows the displayed
entity-ID order, one cover at a time, using the same automatic open/close/open
cycle and feedback rules described above. There is a one-second pause between
covers as well as between runs. This is a transient selection, not a saved group.

One socket-bound session owns the gateway from confirmation through final review.
Only its current cover is attached to the calibration controller. Every target
is validated before session creation, before its turn, and before final Save;
rebinding a selected entity to another cover instance aborts or refuses the work.
Each queued movement also has a distinct token so an old command cannot become
valid again when another cover reaches the same phase.

A measurement error, Stop, Cancel, lease expiry, socket loss or HA shutdown aborts
the entire group and clears all provisional results. The next cover does not
start. The final review appears only when every cover succeeds. Each result has
its own editable new-profile name; measured times and evidence remain owned by
the backend. One Save validates the whole group, creates and assigns one new
profile per cover, and persists the complete mutation once. Previous profiles
are retained, the revision increments once, and one change notification follows
persistence. There is no partial save. Capacity is checked at start and Save.
Save errors retain the review while connected; an already accepted disk save may
finish after closing, under the common semantics above.

The new [WebSocket extension](panel-websocket-api.md#selected-cover-automatic-extension-0190)
adds target discovery and batch start while reusing actions and session cleanup.
Storage v4 and export v2 are unchanged; only committed results enter exports.

For a supervised physical check, select two covers and confirm that the second
starts only after the first completes. Check both directional times and Save;
reopen both profiles to verify their separate automatic origins and dates. In a
second attempt, use Stop during the first cover or the between-cover pause:
verify that no following cover moves and the previous saved profiles remain.
The user has confirmed both single-cover and selected-cover automatic operation
on their installation.

## Experimental WebSocket contract

The following two commands are implemented in 0.10.0. They are separate from the
unimplemented `myhome/covers/*` names in the [shared proposal](panel-shared-contract.md).
Both require administrator authorization before their handlers run. Single-direction
requests extend `start` as documented in the [0.20.0 API extension](panel-websocket-api.md#single-direction-guided-extension-0200).

### Start and subscribe

```json
{"id":20,"type":"myhome/cover_calibration/start","entry_id":"ENTRY","entity_id":"cover.bedroom","revision":4}
```

The target must be an available, enabled native standard MyHOME cover belonging
to exactly the requested entry, with no movement, pending profile or scheduled
position stop. The profile revision must match. Starting reserves the session,
registers its cleanup under this subscription ID, acknowledges success and emits
its first state. No bus command is queued until an explicit movement action.

Example event:

```json
{"id":20,"type":"event","event":{"entry_id":"ENTRY","entity_id":"cover.bedroom","session_id":"OPAQUE_SESSION_ID","sequence":1,"revision":4,"phase":"confirm_closed","mode":"guided","reason":null,"values":{},"elapsed":null,"stop_requested":false}}
```

Every state includes those fields. `mode` is `guided` or `automatic`; automatic
states also contain zero-based `run_index` (0: positioning open, 1: close, 2: open). `values` progressively contains `opening_time`
and `closing_time`, as finite seconds from 1 to 600. `elapsed` is the current leg's
backend duration or null. `sequence` increases on state notifications; heartbeats
return the latest state without increasing it. `revision` is the profile-store
revision captured at start and updated after successful save. Runtime events do
not modify that configuration revision.

### Actions

```json
{"id":21,"type":"myhome/cover_calibration/action","entry_id":"ENTRY","session_id":"OPAQUE_SESSION_ID","sequence":1,"action":"open"}
```

Actions: `run` (automatic only), `open`, `close`, `endpoint`, `stop`, `cancel`, `save`, `heartbeat`.
`save` additionally accepts `name` for one cover or ordered `names` for a batch;
it does **not** accept browser-supplied times.
The response is the current session state inside HA's ordinary result envelope.
A push may arrive before the response; the client ignores lower sequences.

`run`, `open`, `close`, `endpoint` and `save` require the current `sequence` and valid
phase. The other actions ignore sequence. The same connection must own the session;
knowing its ID from another tab does not grant control. Other administrators can
still use HA's normal cover Stop service. Native entity renames retain the bound
cover/session; the state reports its current entity ID.

Unsubscribe using HA's normal `unsubscribe_events` command for the start
subscription ID. The frontend connection helper returns that unsubscribe function.
Unsubscribe releases the session and invalidates queued movement even if the start
response arrived after navigation. There is no session-resume API.

### Refusals and interruption reasons

| Code | Meaning |
| --- | --- |
| `unauthorized`, `invalid_format` | HA authorization/schema rejection |
| `target_not_found`, `cover_unavailable`, `advanced_cover` | Existing profile target rules |
| `revision_conflict` | Profile store changed since the editor read it |
| `calibration_busy` | A session already owns the gateway, or an ordinary profile write was attempted during active measurement |
| `calibration_moving` | Cover is moving or has pending motion/profile work |
| `calibration_step` | Wrong phase or stale sequence; do not automatically retry movement |
| `calibration_expired` | No matching session owned by this connection |
| `command_queue_full` | Movement could not be queued |
| `invalid_profile`, `profile_limit`, `storage_error` | Existing profile validation/persistence rules during save |

State `reason` explains interruption: `start_timeout`, `travel_timeout`,
`heartbeat_timeout`, `cover_unavailable`, `shutdown`, `external_command`,
`unexpected_movement`, `unexpected_stop`, `invalid_measurement`, `stopped`,
`cancelled`, `stop_queue_full` or `command_queue_full`. Domain errors are translated
with the existing frontend dictionary. A queue error must not be rendered as a
successful motor stop.

## Verification and physical testing

Backend tests cover real cover bus-event handling, timed legs, endpoint baselines,
session ownership, stale steps, revision/profile locks, guarded queue jobs after
cancellation, timeouts, queue saturation, interruption, unload, WebSocket disconnect
and persistence failures. Frontend tests cover subscription lifecycle, backend
elapsed values, stale responses, Stop while busy, heartbeat loss, unsaved review
and explicit save. The gateway worker test checks the validity guard **after**
waiting for the command lock.

Automated tests use HA and OWNd with simulated gateway traffic. Real gateway motion,
feedback timing, browser layout and manual endpoint timing still need validation.
On the F454, first verify that both start-feedback steps arrive, then compare the
saved opening/closing values with a manual stopwatch. Also try Cancel while waiting
for start and during a measured leg; confirm that no profile was created and inspect
the monitor/physical cover for the Stop outcome. Test interruption on one supervised
cover before applying the measured profile elsewhere.

Validation for 0.10.0: **1,428 backend tests passed, one existing skip**, five
snapshots passed on HA 2025.1.4 / Python 3.12.14 / OWNd 2.0.0b6. The final
coverage gate reaches **100% Python line coverage** (5,968 statements), including
rerunning the 29 calibration cases against the final endpoint-baseline adjustment.
All **40 frontend tests** pass. On HA 2026.9.1 / Python 3.14.7, all **128 calibration,
profile, cover, gateway and panel tests** pass. Ruff, architectural checks,
documented request-schema validation and local documentation links pass.

## Measurement evidence (panel 0.16.0)

Each confirmed endpoint records backend UTC wall-clock evidence independently of
the monotonic clock used for elapsed travel time. Explicit profile save persists
both directions with `source: guided` and their original confirmation dates.
Interrupting the session clears unsaved evidence along with measured times.
The profile editor shows that evidence after saving; it is not an automatic
calibration or an automatic-save feature.

The panel 0.17.0 export action downloads saved profiles for the gateway. It may be
used while the wizard is open, but unfinished measurements are excluded and the
active session is left intact. Save the measured profile first to include it.
