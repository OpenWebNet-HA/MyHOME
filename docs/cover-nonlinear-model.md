# Nonlinear cover backend — mathematical foundation

This increment implements the **pure mathematical model** from sections 1.2 and
1.4 of the [shared calibration contract](https://github.com/Interstellar0verdrive/MyHOME-stability/blob/51ffaf73a3da3ac09246ee199751e613217e0a78/docs/calibration-contract-proposal.md).
It is the first backend step after the user validated the optional linear
adaptation in panel 0.29.0. The contract remains the reference; this is not a new
calibration proposal.

## What is active

`custom_components/myhome/cover_motion.py` supplies an immutable,
Home-Assistant-independent engine for geometry scaling, elapsed-motor-time
position estimates and motor durations between arbitrary positions. It has no
clock, storage, WebSocket or bus side effects.

**The engine is not yet connected to live cover control or profile writes.**
Existing runtime calculations, profile storage v7, export format 4, panel
0.29.0 and API capabilities are unchanged: `model: linear_time`,
`nonlinear: false`, accuracy `not_measured`. Installing this commit requires no
recalibration or data migration and does not turn existing timings into measured
roll coefficients. There is no new user-facing control to test yet.

## Physical coordinates and partial movements

`MotionPosition(height, slats)` keeps two fractions in [0, 1]:

| State | Height of bottom edge | Fraction of separated slats |
| --- | --- | --- |
| Fully closed | 0 | 0 |
| Partly separated slats | 0 | Between 0 and 1 |
| Lift-off | 0 | 1 |
| Curtain raised | Between 0 and 1 | 1 |
| Fully open | 1 | 1 |

The percentage alone cannot distinguish a fully closed curtain from a curtain
whose slats have partly separated. Retaining both fractions makes Stop, resume
and reversal within this phase well-defined. `at_height(0)` is an explicit
fully-closed target, including slats. Intermediate states keep floating-point
precision; rounding to an HA percentage belongs at the presentation boundary.

For each direction, let `T` be its complete motor run, `S` the common slat time,
`k` its roll ratio, `h` the fraction of physical height, and `u` the fraction of
winding time measured from the bottom:

```text
h(u) = u * (2 + (k - 1) * u) / (k + 1)
u(h) = h * (k + 1) / (sqrt(1 + (k*k - 1) * h) + 1)
c(h, slats) = S * slats + (T - S) * u(h)
```

The rationalized inverse avoids cancellation near `k = 1` and near the bottom.
Opening adds motor seconds to coordinate `c`; closing subtracts them, using
the closing curve. Saturation occurs at the physical end stops. Duration is
the difference between the two coordinates on the requested direction's curve,
not a fraction of total time based only on the height difference.

`slat_time_s = 0`, `opening_roll = closing_roll = 1` is exactly the linear case.
Constructor defaults represent this degenerate model; they are not provenance
and must never be saved as measurements of an existing profile.

## Scaling and personal values

`CoverMotionModel.scaled(reference_travel_cm=..., travel_cm=..., overrides=...)`
returns a new validated model. A missing dimension disables all scaling. When
both are provided, it follows the contract:

```text
ratio = travel_cm / reference_travel_cm
k_effective = sqrt(1 + (k_reference*k_reference - 1) * ratio)
curtain_scale = ratio * (closing_roll_reference + 1) / (closing_roll_effective + 1)
slat = slat_reference * ratio
opening = slat + (opening_reference - slat_reference) * curtain_scale
closing = slat + (closing_reference - slat_reference) * curtain_scale
```

The curtain-scale expression is equivalent to `(k_effective - 1) /
(k_reference - 1)` and is also defined at exactly `k_reference = 1`, where it
returns `ratio`. Each roll transforms independently; both curtain durations use
the closing roll's geometry. Start delay and stop latency remain unchanged.

Personal overrides replace the requested keys **after** scaling, without being
scaled themselves. The complete effective model is then validated. An invalid
inherited duration may therefore be replaced by a valid personal value, but a
slat phase longer than an effective full run is always refused. Scaling does
not mutate either the original model or the override mapping.

Full runs must be in [1, 600] seconds; slat time must be nonnegative and strictly
shorter than both runs. Dimensions use the existing [0.1, 10000] cm range. Rolls
must be in [1, 10000]; the broad upper bound is a numerical guard for effective
scaled geometry, **not** an acceptable fitting range or a claim about hardware.
Delays must be in [0, 600] seconds. All inputs reject booleans, numeric strings,
NaN and infinity. Impossible states, unknown override keys and targets opposite
to the selected direction fail explicitly. Excess elapsed time saturates at an
end stop, while negative elapsed time is rejected.

## Integration work still required

The next adapter must map persisted keys and per-key provenance into this engine,
expose the same resolved model to previews and runtime, and retain a frozen model
through an active run. It must preserve the fractional slat state at Stop and
reversal; a rounded percentage restored after a restart does not establish that
state. Restart/re-anchoring policy must be explicit before activating slat-aware
control. Existing profiles must continue using linear geometry until explicitly
configured from measurements.

`advance` and `duration` use **motor seconds** only. Bus write/movement anchors,
queue delay, start-delay and stop-latency compensation, Stop delivery failures,
session reservations and in-flight configuration updates remain the runner's
responsibility. Merely subtracting latency from an arbitrary sleep would not
provide a safe runtime adapter. None of these behaviors is changed here.

The guided flow still needs lift-off measurement, physical travel readings,
roll fitting, a separate out-of-sample check and review before atomic Save.
This increment does not implement the Basic/Thorough/Correction paths or claim
physical accuracy. The WHO layout is unchanged.

## Verification

Tests cover the exact linear limit, independently computed roll-curve examples,
full and partial runs in both directions, Stop/resume and reversal within slats,
inverse round trips, numerical stability near roll 1, physical saturation,
contract scaling and inverse scaling, per-key overrides, immutability and invalid
inputs. These checks establish mathematical consistency, not centimetre accuracy
on an installation; that requires the guided readings and independent check.
