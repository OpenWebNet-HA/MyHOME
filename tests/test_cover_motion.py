"""Physical invariants and contract examples for the pure nonlinear motion model."""
from dataclasses import FrozenInstanceError, astuple

import pytest

from custom_components.myhome.cover_motion import CoverMotionModel, MotionPosition

CLOSED = MotionPosition(0, 0)
LIFT_OFF = MotionPosition(0, 1)
OPEN = MotionPosition(1, 1)


def test_linear_limit_matches_existing_position_and_duration_in_both_directions():
    model = CoverMotionModel(30, 20)
    for start_height in (0, 0.1, 0.25, 0.5, 0.75, 0.9, 1):
        for target_height in (0, 0.1, 0.25, 0.5, 0.75, 0.9, 1):
            opening = target_height >= start_height
            start, target = map(MotionPosition.at_height, (start_height, target_height))
            seconds = model.duration(start, target, opening=opening)
            assert seconds == pytest.approx(abs(target_height - start_height) * (30 if opening else 20))
            actual = model.advance(start, opening=opening, motor_seconds=seconds)
            assert actual.height == pytest.approx(target_height)


def test_known_roll_curve_is_faster_at_top_and_has_distinct_direction_times():
    model = CoverMotionModel(26, 20, slat_time_s=2, opening_roll=2, closing_roll=3)
    # Opening: 2 s slats + half of 24 s curtain -> (2*.5 + .5**2)/3.
    assert model.advance(CLOSED, opening=True, motor_seconds=14).height == pytest.approx(5 / 12)
    # Closing: halfway through 18 s curtain -> remaining height (1+2*.25)/4.
    assert model.advance(OPEN, opening=False, motor_seconds=9).height == pytest.approx(3 / 8)
    assert model.duration(CLOSED, OPEN, opening=True) == 26
    assert model.duration(OPEN, CLOSED, opening=False) == 20
    assert model.duration(OPEN, LIFT_OFF, opening=False) == 18
    low, middle, high = map(MotionPosition.at_height, (0.25, 0.5, 0.75))
    assert model.duration(low, middle, opening=True) > model.duration(middle, high, opening=True)
    assert model.duration(high, middle, opening=False) < model.duration(middle, low, opening=False)


def test_stop_resume_and_reversal_inside_slats_do_not_restart_the_whole_phase():
    model = CoverMotionModel(26, 20, slat_time_s=2, opening_roll=2, closing_roll=3)
    stopped = model.advance(CLOSED, opening=True, motor_seconds=0.5)
    assert stopped == MotionPosition(0, 0.25)
    assert model.duration(stopped, OPEN, opening=True) == 25.5
    assert model.duration(stopped, CLOSED, opening=False) == 0.5
    assert model.advance(stopped, opening=False, motor_seconds=0.25) == MotionPosition(0, 0.125)
    assert model.advance(stopped, opening=True, motor_seconds=1.5) == LIFT_OFF
    # Bottom edge has reached the sill, but closure still owes the slat phase.
    stopped = model.advance(OPEN, opening=False, motor_seconds=19)
    assert stopped == MotionPosition(0, 0.5)
    assert model.duration(stopped, CLOSED, opening=False) == 1
    assert model.advance(stopped, opening=True, motor_seconds=1) == LIFT_OFF


@pytest.mark.parametrize("roll", [1, 1 + 1e-12, 1.5, 2.29, 5, 100])
def test_inverse_round_trip_partial_runs_reversal_and_endpoints(roll):
    model = CoverMotionModel(24.1, 23.4, slat_time_s=2.74, opening_roll=roll, closing_roll=roll + 0.2)
    positions = [CLOSED, MotionPosition(0, 0.3), LIFT_OFF,
                 *(MotionPosition.at_height(value) for value in (1e-9, 0.1, 0.4, 0.5, 0.9, 1))]
    for first, start in enumerate(positions):
        for last, target in enumerate(positions):
            opening = last >= first
            duration = model.duration(start, target, opening=opening)
            reached = model.advance(start, opening=opening, motor_seconds=duration)
            assert reached.height == pytest.approx(target.height, abs=1e-12)
            assert reached.slats == pytest.approx(target.slats, abs=1e-12)
            # Simulated Stop and resume, including a queue delay before Stop
            # reaches the bus: integration must supply the actual motor time.
            halfway = model.advance(start, opening=opening, motor_seconds=duration / 2)
            resumed = model.advance(halfway, opening=opening, motor_seconds=duration / 2)
            assert resumed.height == pytest.approx(target.height, abs=1e-12)
            reverse = model.duration(reached, start, opening=not opening)
            returned = model.advance(reached, opening=not opening, motor_seconds=reverse)
            assert returned.height == pytest.approx(start.height, abs=1e-12)
            assert returned.slats == pytest.approx(start.slats, abs=1e-12)


def test_elapsed_time_saturates_at_endstops_and_zero_retains_exact_state():
    model = CoverMotionModel(30, 20, slat_time_s=2, opening_roll=2, closing_roll=2)
    partial = MotionPosition(0, 0.75)
    assert model.advance(partial, opening=True, motor_seconds=0) is partial
    assert model.advance(partial, opening=True, motor_seconds=100000) == OPEN
    assert model.advance(OPEN, opening=False, motor_seconds=100000) == CLOSED
    assert model.advance(CLOSED, opening=False, motor_seconds=1) == CLOSED
    assert model.advance(OPEN, opening=True, motor_seconds=1) == OPEN
    for opening in (True, False):
        with pytest.raises(ValueError, match="opposite"):
            model.duration(OPEN if opening else CLOSED, CLOSED if opening else OPEN, opening=opening)


def test_contract_scaling_uses_closing_geometry_for_both_curtain_times():
    model = CoverMotionModel(26, 20, slat_time_s=2, opening_roll=3, closing_roll=2,
                             start_delay_s=0.55, stop_latency_s=0.3)
    # H/H_ref = 8/3 => closing roll 3, curtain scale 2, slats 16/3.
    scaled = model.scaled(reference_travel_cm=150, travel_cm=400)
    assert scaled.opening_roll == pytest.approx((67 / 3) ** 0.5)
    assert scaled.closing_roll == 3
    assert scaled.slat_time_s == pytest.approx(16 / 3)
    assert scaled.opening_time_s == pytest.approx(48 + 16 / 3)
    assert scaled.closing_time_s == pytest.approx(36 + 16 / 3)
    assert scaled.start_delay_s == 0.55
    assert scaled.stop_latency_s == 0.3
    # Recover the original reference without baking an override into geometry.
    assert astuple(scaled.scaled(reference_travel_cm=400, travel_cm=150)) == pytest.approx(astuple(model))


@pytest.mark.parametrize("reference,travel", [(None, None), (None, 150), (200, None), (200, 200)])
def test_missing_dimensions_or_equal_travel_do_not_change_the_model(reference, travel):
    model = CoverMotionModel(26, 20, slat_time_s=2, opening_roll=2, closing_roll=3)
    assert model.scaled(reference_travel_cm=reference, travel_cm=travel) == model


def test_linear_scaling_limit_and_near_linear_numerical_stability():
    for roll in (1, 1 + 1e-12):
        model = CoverMotionModel(30, 20, slat_time_s=2, opening_roll=roll, closing_roll=roll)
        scaled = model.scaled(reference_travel_cm=200, travel_cm=150)
        assert scaled.opening_time_s == pytest.approx(22.5, abs=1e-10)
        assert scaled.closing_time_s == pytest.approx(15, abs=1e-10)
        assert scaled.slat_time_s == 1.5


def test_personal_keys_are_applied_after_scaling_without_mutating_either_input():
    model = CoverMotionModel(30, 20, slat_time_s=2, opening_roll=2, closing_roll=2)
    patch = {"opening_time_s": 40, "opening_roll": 1.5, "slat_time_s": 1,
             "start_delay_s": 0.4, "stop_latency_s": 0.2}
    resolved = model.scaled(reference_travel_cm=200, travel_cm=150, overrides=patch)
    assert resolved.opening_time_s == 40
    assert resolved.opening_roll == 1.5
    assert resolved.slat_time_s == 1
    assert resolved.start_delay_s == 0.4 and resolved.stop_latency_s == 0.2
    inherited = model.scaled(reference_travel_cm=200, travel_cm=150)
    assert resolved.closing_time_s == inherited.closing_time_s
    assert resolved.closing_roll == inherited.closing_roll
    assert model.opening_time_s == 30 and patch["opening_time_s"] == 40
    with pytest.raises(FrozenInstanceError):
        resolved.opening_time_s = 5
    with pytest.raises(FrozenInstanceError):
        CLOSED.slats = 1
    # Validate only the resolved model: a personal value may replace an
    # inherited duration that would otherwise be outside supported limits.
    assert CoverMotionModel(400, 200).scaled(reference_travel_cm=100, travel_cm=200,
                                            overrides={"opening_time_s": 500}).opening_time_s == 500
    with pytest.raises(ValueError, match="Unknown"):
        model.scaled(reference_travel_cm=None, travel_cm=None, overrides={"typo": 1})


@pytest.mark.parametrize("value", [True, "2", None, float("nan"), float("inf"), -float("inf"), 10**1000])
def test_reject_nonfinite_and_nonnumeric_configuration(value):
    for field in ("opening_time_s", "closing_time_s", "slat_time_s", "opening_roll", "closing_roll",
                  "start_delay_s", "stop_latency_s"):
        with pytest.raises(ValueError):
            CoverMotionModel(**({"opening_time_s": 30, "closing_time_s": 20} | {field: value}))
    with pytest.raises(ValueError):
        MotionPosition(value, 1)
    with pytest.raises(ValueError):
        MotionPosition(0, value)
    with pytest.raises(ValueError):
        CoverMotionModel(30, 20).advance(CLOSED, opening=True, motor_seconds=value)


@pytest.mark.parametrize("values", [
    {"opening_time_s": 0.999}, {"closing_time_s": 600.001}, {"slat_time_s": -0.1},
    {"slat_time_s": 20}, {"slat_time_s": 25}, {"opening_roll": 0.999},
    {"closing_roll": 10001}, {"start_delay_s": -0.1}, {"stop_latency_s": 600.001},
])
def test_invalid_geometry_and_time_limits_fail_atomically(values):
    with pytest.raises(ValueError):
        CoverMotionModel(**({"opening_time_s": 30, "closing_time_s": 20} | values))


def test_reject_impossible_position_direction_elapsed_time_and_scaling():
    model = CoverMotionModel(30, 20, slat_time_s=2)
    for height, slats in ((-0.1, 1), (1.1, 1), (0, -0.1), (0, 1.1), (0.5, 0.5)):
        with pytest.raises(ValueError):
            MotionPosition(height, slats)
    with pytest.raises(ValueError):
        model.advance(CLOSED, opening=True, motor_seconds=-0.1)
    with pytest.raises(ValueError):
        model.advance(CLOSED, opening="open", motor_seconds=0)
    for value in (True, "200", 0.09, 10000.1, float("nan"), float("inf"), 10**1000):
        for key in ("reference_travel_cm", "travel_cm"):
            with pytest.raises(ValueError):
                model.scaled(**({"reference_travel_cm": None, "travel_cm": None} | {key: value}))
    with pytest.raises(ValueError):
        model.scaled(reference_travel_cm=1, travel_cm=10000)
    with pytest.raises(ValueError):
        model.scaled(reference_travel_cm=10000, travel_cm=0.1)
    with pytest.raises(ValueError):
        model.scaled(reference_travel_cm=200, travel_cm=150, overrides={"opening_time_s": 1})


def test_plant_delays_are_not_part_of_motor_motion_or_target_duration():
    plain = CoverMotionModel(26, 20, slat_time_s=2, opening_roll=2, closing_roll=3)
    delayed = CoverMotionModel(26, 20, slat_time_s=2, opening_roll=2, closing_roll=3,
                               start_delay_s=0.55, stop_latency_s=0.3)
    middle = MotionPosition.at_height(0.5)
    for opening, start in ((True, CLOSED), (False, OPEN)):
        assert plain.duration(start, middle, opening=opening) == delayed.duration(start, middle, opening=opening)
        assert plain.advance(start, opening=opening, motor_seconds=5) == delayed.advance(start, opening=opening, motor_seconds=5)
