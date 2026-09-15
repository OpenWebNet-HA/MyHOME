# Guided travel measurement — panel 0.10.0

This experimental wizard measures **one standard cover's full opening and closing
times**. It extends our linear timing profiles; it is not the calibration fork's
height/roll/slat model or the still-proposed shared API. The operator confirms
physical endpoints; no automatic endstop detection is claimed.

## Using it

1. With the normal controls, put the cover fully closed and stopped.
2. In the MyHOME panel, expand its WHO 2 device, open **Travel profile**, and choose
   **Guided travel measurement**. The session starts without moving the cover.
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

## Lifetime and persistence

A session belongs to one HA WebSocket connection and one config entry/cover. There
is at most one session per gateway. While it is active, ordinary profile writes on
that gateway are rejected with `calibration_busy`. Other gateways remain independent.
The current profile read response still reports normal target writability; the
backend enforces the calibration lock on writes/start requests.

Session phases are:

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
reconnect or restart. Measurements and sessions are never persisted.

Movement must be dispatched and produce bus start feedback within **10 seconds**
of the request; a measured leg is limited to **600 seconds**. Movement queue jobs
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

## Experimental WebSocket contract

The following two commands are implemented in 0.10.0. They are separate from the
unimplemented `myhome/covers/*` names in the [shared proposal](panel-shared-contract.md).
Both require administrator authorization before their handlers run.

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
{"id":20,"type":"event","event":{"entry_id":"ENTRY","entity_id":"cover.bedroom","session_id":"OPAQUE_SESSION_ID","sequence":1,"revision":4,"phase":"confirm_closed","reason":null,"values":{},"elapsed":null,"stop_requested":false}}
```

Every state includes those fields. `values` progressively contains `opening_time`
and `closing_time`, as finite seconds from 1 to 600. `elapsed` is the current leg's
backend duration or null. `sequence` increases on state notifications; heartbeats
return the latest state without increasing it. `revision` is the profile-store
revision captured at start and updated after successful save. Runtime events do
not modify that configuration revision.

### Actions

```json
{"id":21,"type":"myhome/cover_calibration/action","entry_id":"ENTRY","session_id":"OPAQUE_SESSION_ID","sequence":1,"action":"open"}
```

Actions: `open`, `close`, `endpoint`, `stop`, `cancel`, `save`, `heartbeat`.
`save` additionally accepts `name`; it does **not** accept browser-supplied times.
The response is the current session state inside HA's ordinary result envelope.
A push may arrive before the response; the client ignores lower sequences.

`open`, `close`, `endpoint` and `save` require the current `sequence` and valid
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
