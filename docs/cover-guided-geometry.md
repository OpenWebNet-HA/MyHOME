# Guided slat and roll measurement — panel 0.31.0

Inside the existing cover **Calibration** section, select **Slats and roll — guided**.
The timing-only and automatic paths remain available. This release implements the
basic new-profile path; it does not implement the thorough continuation, independent
40% check, personal geometry overrides or measured shared-profile replacement.
Those limitations are explicit in capabilities and the review screen.

## Operator workflow

Every movement starts from its own briefing. The backend never chains a new
movement merely because a reading was submitted or a client reconnected.

1. Close completely and confirm the physical bottom end stop, slats closed.
2. Start the lift-off ascent. Press the large button as the bottom edge leaves its
   rest. Wait for Stop feedback. If still resting, repeat; otherwise enter the
   actual gap above the rest (0 only for a just-lifted, unmeasurable gap).
3. Return to the bottom, then explicitly start the timed full ascent. Confirm the
   physical top end stop. Measure travel from the rest to the lower curtain edge.
4. Start the timed full descent and confirm the bottom end stop, slats closed.
5. Start the intermediate ascent, wait for automatic Stop, then enter height above
   the same rest. The run lasts halfway between the observed lift-off and full
   opening motor times; its actual duration includes the Stop queue delay.
6. Return to the top and confirm. Start an intermediate descent, wait for automatic
   Stop and enter height. Its scheduled duration is half the fitted curtain-only
   closing time, with actual Stop write time used for the fit.
7. Review opening/closing times, travel, slat time and both roll coefficients.
   Explicit Save creates and assigns a new profile, saves this cover's measured
   travel, removes its former timing overrides and records per-key guided evidence
   in one storage transaction. Other profiles and their followers are untouched.

Repeat is available after each reading and after the full closing measurement.
It returns to the required endpoint with an explicit briefing, then repeats the
selected measurement. Repeating an earlier step traverses subsequent dependent
measurements again; existing committed settings never change before Save.
The final tape reading seeds the new runtime position after successful Save.

## Measurement and fitting

The motor start anchor is the actuator's moving status after the guarded command
is dispatched. This path requires movement and Stop feedback: absent feedback
interrupts rather than fabricating a duration or assuming a safe stationary state.
Direction delivery failures also interrupt. Existing one-session gateway ownership,
lease, reservation, shutdown, guard and recovery rules apply.

A lift-off or intermediate stop measures from the motor anchor to the worker's
Stop frame write timestamp, exposed only after the send is acknowledged. Queue wait
before movement is excluded; queue wait before Stop is included. Readings unlock
only after both confirmed delivery and actuator Stop feedback. A physical endpoint
confirmation instead ends timing on receipt by the backend; the subsequent relay
release Stop is excluded. No browser clock is used.

For normalized height `h` and roll `k`, use the runtime's winding fraction:

```
u(h,k) = h*(k+1)/(sqrt(1+(k*k-1)*h)+1)
t_up   = S + (T_up-S)*u(h,k_up)
t_down = (T_down-S)*(1-u(h,k_down))
```

The lift-off gap and the intermediate ascent jointly solve `S` and `k_up`:
`S=(t_lift-T_up*u(gap/H,k))/(1-u(gap/H,k))`, followed by bounded bisection for
`k` in [1,5]. Thus correction uses the same nonlinear model as runtime instead of
approximating the gap with constant speed. The descent reading determines `k_down`
with the corrected `S`. Full timings remain the operator-observed endpoint times.
Impossible readings are rejected without advancing or saving. There is no silent
clamp to plausible geometry (apart from numerical endpoint rounding).

One point per direction fits the roll; it cannot verify accuracy. The summary
explicitly says precision is unverified, and the API returns `accuracy: null` and
`independent_check: false`. The halfway centimetre hint is only a rough reference,
labelled as neither a target nor a tolerance.

## Additive WebSocket protocol

Use existing `myhome/cover_calibration/start` with `mode: "geometry"` and no
single-direction scope. Owned/revision-bound `.../action` adds:

- `next`: explicitly start the briefed movement.
- `lift`: request Stop during the lift-off ascent.
- `endpoint`: confirm a physical endpoint during a full run or positioning.
- `reading`, `reading_cm`: one tape observation; backend validates and fits.
- `repeat`: return through the required positioning briefing.

Existing stop/cancel/heartbeat/detach/resume/save actions retain their semantics.
`save_modes` is only `["new"]` for this mode; clients cannot submit fitted values,
provenance or geometry through calibration actions.
New transient phases are `briefing`, `geometry_wait_stop` and `reading`; existing
starting/opening/closing/review/saving/terminal phases remain. `step` identifies the
instruction, and `reading_kind`, `can_repeat`, `expected_cm`, `geometry`, `samples`
and `readings` let the frontend render backend state without fitting calculations.
Briefings, readings and review are recoverable stationary checkpoints. Leaving
while moving or waiting for Stop interrupts and requests Stop; reconnect never
replays movement. Gateway reservation survives client closure until Stop feedback
or the existing 600-second guard. Provisional session data is lost at restart.

Overview capabilities advertise `nonlinear_calibration: true`,
`nonlinear_calibration_modes: ["basic_new_profile"]`,
`independent_calibration_check: false`, `geometry_overrides: false`.
Storage remains v8 and export v5; no migration is needed.

## Verification

Backend tests exercise the full real-cover event path, delayed Stop delivery,
echo before acknowledgement, save failure and retry, physical-position seeding,
per-key evidence, repeat/recovery, invalid tape values, stale actions, direction
failure, full queues, Stop timeout and disconnect during motion. Fitting tests
recover known parameters including a late lift-off and the roll boundaries.
Frontend tests exercise briefings, the single primary movement action, tape drafts,
backend-only observations, review/save restrictions and recovery without motion.

Installation check: complete one measurement, reopen the saved profile and check
all values and the measured travel. Test repeat and a cancelled run before Save.
Then command 50% from each physical endpoint and compare height with a tape. Report
those real-world gaps separately: automated tests cannot establish centimetre
accuracy on an installation.
