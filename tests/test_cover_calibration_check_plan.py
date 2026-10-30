"""L-78: the check offered after a base calibration can always start, whatever accepted readings fitted the model."""
import pytest

from custom_components.myhome.cover_calibration_check import (
    MAX_TARGET_FRACTION,
    MIN_CURTAIN_SECONDS,
    MIN_TARGET_FRACTION,
    CalibrationCheck,
)
from custom_components.myhome.cover_motion import CoverMotionModel
from custom_components.myhome.cover_profiles import ProfileError
from tests.test_cover_calibration_geometry import endpoint, start, stopped
from tests.test_cover_calibration_geometry import geometry as geometry_fixture
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_panel_cover_calibration import act
from tests.test_panel_cover_calibration import calibration as calibration_fixture

geometry = geometry_fixture
plant = plant_fixture
calibration = calibration_fixture


async def planned_half(cal):
    """A half-time run stopped by the session at its own planned time (its deadline), as on the bus."""
    await start(cal)
    planned = cal.session.deadline.when() - cal.session.hass.loop.time()
    cal.clock[0] += planned - .2
    callback = cal.session.deadline._callback
    cal.session.deadline.cancel()
    callback()
    await stopped(cal, elapsed=.2)
    assert cal.session.phase == "reading"


def within(cal, where):
    """A reading at `where` (0 the lowest, 1 the highest) of the range the session accepts now."""
    band = cal.session.view().get("reading_range") or {"min_cm": 1, "max_cm": cal.session.view()["travel_cm"]}
    return round(band["min_cm"] + (band["max_cm"] - band["min_cm"]) * where, 1)


async def measured(cal, *, lift_s, gap, up, down, travel, half_open, half_close):
    """The base calibration with these readings, up to its review (as the panel drives it). `half_*`: a height in cm,
    or a share of the accepted range."""
    await endpoint(cal, 9)  # Home.
    await start(cal)
    cal.clock[0] += lift_s - .5
    await act(cal, "lift")
    await stopped(cal, elapsed=.5)
    await act(cal, "reading", reading_cm=gap)
    await endpoint(cal, 8)  # Reset.
    await endpoint(cal, up)
    await act(cal, "reading", reading_cm=travel)
    await endpoint(cal, down)
    await planned_half(cal)
    await act(cal, "reading", reading_cm=half_open if half_open > 1 else within(cal, half_open))
    await endpoint(cal, 7)
    await planned_half(cal)
    await act(cal, "reading", reading_cm=half_close if half_close > 1 else within(cal, half_close))
    assert cal.session.phase == "review"


async def test_the_garden_door_of_the_tenth_of_october_checks_down_to_half(geometry):
    """The refusal met live: lift-off at 8.1 s for 4 cm fits 7.5 s of slats; down from the top to half the travel is
    5.7 s of curtain, refused as if the slats came first (`invalid_check`, shown as a connection failure)."""
    cal = geometry
    await measured(cal, lift_s=8.1, gap=4, up=22.3, down=23.8, travel=198, half_open=77, half_close=66)
    assert cal.session.geometry["slat_time_s"] == pytest.approx(7.5, abs=.1)
    view = await act(cal, "check", direction="closing", target_cm=99)
    assert view["check"]["direction"] == "closing" and view["check"]["target_cm"] == 99
    assert view["check"]["planned_seconds"] == pytest.approx(5.68, abs=.05)
    assert (view["phase"], view["step"]) == ("briefing", "top")


# R-11: every combination the session accepts, and only those (the one it refuses is tested on its own below).
ACCEPTED = [(lift_s, gap) for lift_s in (1.0, 4.0, 8.1, 10.0) for gap in (1, 4, 10) if (lift_s, gap) != (1.0, 10)]


@pytest.mark.parametrize(("lift_s", "gap"), ACCEPTED)
@pytest.mark.parametrize(("half_open", "half_close"), [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (0.0, 1.0), (1.0, 0.0)])
async def test_no_accepted_readings_leave_the_check_impossible(geometry, lift_s, gap, half_open, half_close):
    cal = geometry
    await measured(cal, lift_s=lift_s, gap=gap, up=22.3, down=23.8, travel=198, half_open=half_open, half_close=half_close)
    travel = cal.session.view()["travel_cm"]
    view = await act(cal, "check", direction="closing", target_cm=travel / 2)
    check = view["check"]
    assert MIN_TARGET_FRACTION <= check["target_cm"] / travel <= MAX_TARGET_FRACTION
    assert check["planned_seconds"] > MIN_CURTAIN_SECONDS


async def test_a_ten_centimetre_gap_after_one_second_of_lift_off_is_refused_before_any_check(geometry):
    """The one combination of the sweep the session does not accept: the edge cannot rise 10 cm in a second."""
    cal = geometry
    with pytest.raises(ProfileError):
        await measured(cal, lift_s=1.0, gap=10, up=22.3, down=23.8, travel=198, half_open=0.5, half_close=0.5)


def test_a_run_too_short_moves_to_the_nearest_check_the_model_can_make():
    # Ten seconds of slats and two of curtain: up to half is 11 s, one of curtain only: the ascent goes higher.
    slow = CoverMotionModel(opening_time_s=12, closing_time_s=12, slat_time_s=10)
    with pytest.raises(ProfileError, match="invalid_check"):
        CalibrationCheck.plan(slow, 200, "opening", 100)
    check = CalibrationCheck.plan_possible(slow, 200, "opening", 100)
    assert check.direction == "opening" and check.target_cm > 100 and check.planned_seconds - 10 > MIN_CURTAIN_SECONDS
    # Outside the contract it is still refused, not clamped.
    for direction, target in (("sideways", None), ("closing", 250), ("closing", float("nan"))):
        with pytest.raises(ProfileError, match="invalid_check"):
            CalibrationCheck.plan_possible(slow, 200, direction, target)


def test_a_model_that_can_make_no_check_at_all_is_refused_in_every_direction():
    """L-78: a full run of about a second (up and down) leaves no fraction of 10-90 % with a second of curtain either
    way: no nearest check exists, so `invalid_check` stays, and the session lets the calibration be saved without one."""
    brief = CoverMotionModel(opening_time_s=1.0, closing_time_s=1.0, slat_time_s=0)
    for direction in (None, "opening", "closing"):
        with pytest.raises(ProfileError, match="invalid_check"):
            CalibrationCheck.plan_possible(brief, 200, direction)
