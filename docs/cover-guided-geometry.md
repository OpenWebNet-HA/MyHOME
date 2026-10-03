# Guided slat and roll measurement — panel 0.31.0

Inside the existing cover **Calibration** section, select **Roll measurement — guided**.
The timing-only and automatic paths remain available. This release implements the
basic new-profile path; it does not implement the thorough continuation, independent
40% check, personal geometry overrides or measured shared-profile replacement.
Those limitations are explicit in capabilities and the review screen.

## Operator workflow

Every movement starts from its own briefing. The backend never chains a new
movement merely because a reading was submitted or a client reconnected.

1. Close completely and confirm the physical bottom end stop, slats closed.
2. Start the lift-off ascent. Press the large button as the bottom edge leaves its
   rest. Wait for Stop feedback, then enter the actual gap above the rest. From
   panel 0.38.5 a gap below 1 cm, 0 included, means the edge is still resting: the
   run is discarded and repeated from the bottom; from 10 cm the gap is accepted
   with a warning that offers Repeat, since the slat time gets less precise; above
   20 cm the reading is refused (see [the API](panel-websocket-api.md#guided-geometry-lift-off-gap-and-covers-without-slats-0385)).
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
8. Optionally, before Save, check the measured model (backend only; the panel does
   not offer it yet): the cover returns to an end stop, runs towards a height for
   the seconds the model needs, stops on time, and the tape says whether the edge
   is within 4 cm of where the model puts it. See
   [Check of the measured model](#check-of-the-measured-model).

A cover without slats (panel 0.38.5, switch "This cover has no slats") skips step 2
and the return to the bottom at the start of step 3: the full ascent follows step 1,
the intermediate ascent is scheduled for half the opening time, and the slat time is
saved as zero.

Repeat is available after each reading and after the full closing measurement.
It returns to the required endpoint with an explicit briefing, then repeats the
selected measurement. Repeating an earlier step traverses subsequent dependent
measurements again; existing committed settings never change before Save.
The final tape reading seeds the new runtime position after successful Save.

## Measurement and fitting

The motor start anchor is the actuator's moving status after the guarded command
is dispatched. This path requires movement and Stop feedback: absent feedback
interrupts rather than fabricating a duration or assuming a safe stationary state.
Direction delivery failures also interrupt, but only for the movement still current:
a frame refused or acknowledged after a later movement was queued (the next step, a new
check, or a check cut short) is ignored. Existing one-session gateway ownership,
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
`independent_check: false`. From panel 0.41.0 an intermediate reading shows the range
of heights the fit accepts for that stop (`reading_range`, roll 5 to roll 1) instead
of a halfway reference; it is neither a target nor a tolerance, and a reading outside
it is refused with `reading_out_of_range` (see [the API](panel-websocket-api.md#guided-geometry-intermediate-reading-range-0410)).

## Check of the measured model

From `review`, `action: "check"` (optional `direction`, `"opening"` by default, and
`target_cm`, three quarters of the measured travel by default) verifies the model just
fitted. It returns to the bottom end stop through the `home` briefing (or to the top
through `top` for a downward check), then runs from there for the motor seconds the
model needs to bring the bottom edge to the target, and stops on time. By default the
run rises from the bottom, because an ascent crosses the slat phase and the opening
roll, a descent from the top neither; and it aims at three quarters of the travel,
away from the two intermediate readings, which land near 40 %. A target outside 10 %
to 90 % of the travel, or a run no longer than the slat time plus 1 s, is refused.

The reading after it (from 0.1 cm) is compared with where the model puts the edge
after the motor seconds really run, to a tenth of a centimetre. The difference is
rounded to the whole centimetre with ties away from zero, and the check passes within
4 cm (`CHECK_THRESHOLD_CM`). The verdict comes back to `review` in the `check` key of
the view and stays there until another check, a repeated measurement step or the end
of the session; Save remains available whatever it says, the verdict is not stored,
and the check reading seeds the runtime position after Save.

A check never costs the measurement. Stop in its briefings or at its reading returns
to `review` with everything, the previous verdict included. Stop while it moves, an
early stop on the bus or a movement nobody asked for returns to `review` without a
verdict; Stop is written, and since nobody read where the edge stopped, Save leaves
the runtime position unknown instead of seeding it. Other interruptions end the
session as at any other step. Contract: [the API](panel-websocket-api.md#guided-geometry-check-of-the-measured-model).

## Additive WebSocket protocol

Use existing `myhome/cover_calibration/start` with `mode: "geometry"` and no
single-direction scope. Owned/revision-bound `.../action` adds:

- `next`: explicitly start the briefed movement.
- `lift`: request Stop during the lift-off ascent.
- `endpoint`: confirm a physical endpoint during a full run or positioning.
- `reading`, `reading_cm`: one tape observation; backend validates and fits.
- `repeat`: return through the required positioning briefing.
- `check`, optional `direction` and `target_cm`: from `review`, check the measured
  model (see above).

Existing stop/cancel/heartbeat/detach/resume/save actions retain their semantics.
`save_modes` is only `["new"]` for this mode; clients cannot submit fitted values,
provenance or geometry through calibration actions.
New transient phases are `briefing`, `geometry_wait_stop` and `reading`; existing
starting/opening/closing/review/saving/terminal phases remain. `step` identifies the
instruction, and `reading_kind`, `can_repeat`, `expected_cm`, `reading_range`, `geometry`, `samples`
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
