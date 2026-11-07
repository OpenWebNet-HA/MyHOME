"""Restart of a modelled timed cover: restore the model state or report unknown, never open."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers.restore_state import RestoredExtraData

from custom_components.myhome.cover import _motion_fingerprint
from custom_components.myhome.cover_motion import END_SETTLE_MARGIN, MotionPosition
from tests.test_cover_end_of_run import CLOSE, at_end, state, until, written_and_started
from tests.test_cover_end_of_run import modelled as modelled_fixture
from tests.test_cover_nonlinear_backend import nonlinear
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_cover_travel_scaling import write

plant = plant_fixture
modelled = modelled_fixture


def saved(cover):
    """What Home Assistant's restore cache keeps: the JSON round trip of the extra data."""
    extra = cover.extra_restore_state_data
    return json.loads(json.dumps(extra.as_dict())) if extra is not None else None


async def restart(cover, extra, last_state="open", position=None):
    cover.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData(extra) if extra is not None else None)
    attributes = {"current_position": position} if position is not None else {}
    await cover.async_restore_last_state(SimpleNamespace(state=last_state, attributes=attributes))


@pytest.mark.parametrize("position,expected", [
    (MotionPosition(0.5, 1), ("open", 50)),
    (MotionPosition(0, 0.4), ("open", 0)),  # on the sill with the slats still open: not closed
    (MotionPosition(0, 0), ("closed", 0)),
    (MotionPosition(1, 1), ("open", 100)),
])
async def test_valid_model_state_restores_height_and_slat_phase(modelled, position, expected):
    m = modelled
    at_end(m, 0)
    m.cover._motion.position = position
    extra = saved(m.cover)
    assert extra["motion"]["model"] == _motion_fingerprint(m.cover._motion.model)
    m.cover._motion.position = None
    await restart(m.cover, extra)
    assert m.cover._motion.position == position
    assert state(m) == expected
    assert m.cover._start_position == expected[1]


async def test_restored_position_allows_an_intermediate_target(modelled):
    m = modelled
    await restart(m.cover, {"motion": {"height": 0.5, "slats": 1.0,
                                       "model": _motion_fingerprint(m.cover._motion.model)}})
    await m.cover.async_set_cover_position(position=25)  # not refused as "position unknown"
    assert m.sent[-1][0] == CLOSE
    m.cover._cancel_stop_task()


async def test_restored_estimate_is_re_anchored_by_the_next_full_run(modelled):
    m = modelled
    await restart(m.cover, {"motion": {"height": 0.5, "slats": 1.0,
                                       "model": _motion_fingerprint(m.cover._motion.model)}})
    await m.cover.async_close_cover()
    start = await written_and_started(m, CLOSE)
    remaining = m.cover._motion.model.duration(MotionPosition(0.5, 1), MotionPosition(0, 0), opening=False)
    await until(m, start, remaining + END_SETTLE_MARGIN + 0.1)
    assert state(m) == ("closed", 0)
    assert m.cover._motion.position == MotionPosition(0, 0)
    assert saved(m.cover)["motion"]["height"] == 0


@pytest.mark.parametrize("extra,last_state,position", [
    (None, "open", None),  # no data at all
    (None, "open", 50),  # an old HA percentage without the model state
    (None, "closed", 0),
    ({"strategy": "timed", "state": {}}, "open", 50),  # extra data without a model state
    ({"motion": {"height": 0.5, "slats": 0.3}}, "open", 50),  # no model recorded
    ({"motion": "garbage"}, "open", 50),
], ids=["nothing", "percentage_only", "closed_only", "no_motion", "no_model", "garbage"])
async def test_missing_model_state_reports_unknown_never_open(modelled, extra, last_state, position):
    m = modelled
    await restart(m.cover, extra, last_state, position)
    assert state(m) == (None, None)
    assert m.cover.is_closed is None and m.cover._motion.position is None


@pytest.mark.parametrize("motion", [
    {"height": 0.5, "slats": 0.3},  # a raised curtain with closed slats is not a physical state
    {"height": 1.5, "slats": 1.0},
    {"height": True, "slats": 1.0},
    {"slats": 1.0},
], ids=["impossible", "out_of_range", "not_a_number", "missing_height"])
async def test_invalid_model_state_reports_unknown(modelled, motion):
    m = modelled
    await restart(m.cover, {"motion": {**motion, "model": _motion_fingerprint(m.cover._motion.model)}})
    assert state(m) == (None, None)


async def test_restore_cache_without_the_entity_reports_unknown(modelled):
    m = modelled
    m.cover.async_get_last_extra_data = AsyncMock(side_effect=RuntimeError("not registered"))
    await m.cover.async_restore_last_state(SimpleNamespace(state="closed", attributes={"current_position": 0}))
    assert state(m) == (None, None)


async def test_a_moving_cover_saves_no_model_state(modelled):
    m = modelled
    at_end(m, 100)
    await m.cover.async_close_cover()
    await written_and_started(m, CLOSE)
    assert saved(m.cover) is None
    m.cover._cancel_stop_task()


@pytest.mark.parametrize("change", [
    {"action": "overrides", "overrides": {"closing": 18}},  # personal timing on the same profile
    {"profile": nonlinear(opening_time=20, closing_time=17, reference_travel_cm=None,
                          geometry={"slat_time_s": 3, "opening_roll": 3, "closing_roll": 2})},
    {"action": "travel", "travel_cm": 300},  # a different travel on a scaled profile
], ids=["override", "profile", "travel"])
async def test_state_saved_with_another_model_reports_unknown(hass, plant, change):
    data = await write(hass, plant, profile=nonlinear(opening_time=20, closing_time=17))
    cover = plant.covers[0]
    cover._motion.confirm(50)
    extra = saved(cover)
    assert extra["motion"]["height"] == 0.5
    if "profile" in change:
        await write(hass, plant, profile_id=data["assigned_profile_id"], **change)
    else:
        await write(hass, plant, **change)
    assert _motion_fingerprint(cover._motion.model) != extra["motion"]["model"]
    await restart(cover, extra, "open", 50)
    assert (cover.state, cover.current_cover_position, cover._motion.position) == (None, None, None)
    # The same data under the model it was saved with restores.
    cover._motion.model = type(cover._motion.model)(**{
        key: value for key, value in extra["motion"]["model"].items()})
    await restart(cover, extra, "open", 50)
    assert cover.current_cover_position == 50
