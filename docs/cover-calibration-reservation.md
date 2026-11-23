# Calibration contract alignment — panel 0.28.0

This change addresses gateway reservation lifetime and the assignment notice in
[Interstellar0verdrive's calibration contract](https://github.com/Interstellar0verdrive/MyHOME-stability/blob/51ffaf73a3da3ac09246ee199751e613217e0a78/docs/calibration-contract-proposal.md).
It does not implement the proposed geometry/nonlinear calibration model.

## Gateway reservation

- Closing/cancelling a client still interrupts its measurement and requests Stop.
  Already accepted saves retain their existing semantics. Nothing restarts when
  a client reconnects.
- A movement that has passed a gateway worker guard may have been transmitted,
  even if no opening/closing feedback arrives. The session retains the gateway
  reservation after client closure until Stop feedback or a full safety guard.
- The guard is 600 seconds from the latest possible dispatch or observed movement.
  This is the maximum supported travel duration, used for guided and automatic
  runs because calibration must not trust the old profile's estimated timings.
  The shorter automatic-run timeout still interrupts the run and requests Stop;
  it does not release the gateway reservation early.
- A queued Stop, successful save, or operator endpoint confirmation alone does
  not release a potentially moving actuator. Guided sessions can still advance
  to the next direction after explicit endpoint confirmation within their own
  reservation. Automatic sessions keep their existing short relay-echo filter.
- Queued movement is invalidated on closure. A queued Stop expires after 30
  seconds and belongs to its session and movement generation, so it cannot
  interrupt a later direction or a new calibration owner. The gateway's existing
  shared command lock continues to serialize calibration commands.
- The reservation blocks another panel calibration and native timing/calibration
  services on the same gateway. Other gateways remain independent. Ordinary
  physical controls remain usable; new movement feedback extends the guard.
- The profile editor shows a closed session waiting for Stop and hides Resume.
  Refresh checks the backend again; no authoritative state lives in the browser.
- Entity unload retains the in-memory guard. If the removed entity can no longer
  receive feedback, release falls back to the timer. HA shutdown cancels runtime
  timers and leaves the existing best-effort Stop eligible during shutdown.
  Reservation and unsaved measurements are not persisted across HA restarts.

Guard expiry permits a new session; it is not evidence of a physical endpoint or
successful Stop delivery. If Stop could not be queued, the existing warning to
use the physical control remains applicable and the reservation still holds.

## Assignment notice

The multi-cover assignment dialog now shows the same notice as the single-cover
editor, both before selection and during preview: linear profile times apply as
saved, without height scaling, and accuracy has not been measured. Per-direction
overrides remain preserved and visible in the assignment preview.

## Verification

Regression tests exercise dispatch with/without bus feedback, guided/automatic
cancellation, gateway isolation, native timing ownership, Stop delivery, guard
expiry, subsequent movement, queue overflow, unload/shutdown, partial-save
completion, stale queued commands, and the profile-editor waiting state. The
existing guided-measurement fixture now includes explicit bus Stop events; a
separate test checks Save before Stop feedback.

Local validation: 2,057 backend tests passed (one skipped), 153 frontend tests
passed, all 46 integration modules have 100% line coverage, strict mypy reports
zero errors, and Ruff plus the Home Assistant architecture checks pass.

Installation check: cancel a moving measurement and open its profile editor.
If Stop feedback has not arrived, the editor must show the reservation notice
and disable a new measurement. After Stop, Refresh should restore availability.
Also verify the unscaled notice in the multi-cover assignment dialog. Do not
disconnect or obstruct a moving shutter merely to exercise the fallback timer.
