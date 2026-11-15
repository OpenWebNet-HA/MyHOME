"""The precision class of a cover's calibration: derived from provenance and the kept check, never stored."""
import pytest

from custom_components.myhome.cover_profiles import read_profile, write_profile
from custom_components.myhome.cover_settings import precision_class
from custom_components.myhome.cover_settings_api import overview
from tests.test_cover_calibration_base import act, basic, seed
from tests.test_cover_calibration_base import base as base_fixture
from tests.test_cover_calibration_geometry import geometry as geometry_fixture
from tests.test_cover_calibration_geometry import review
from tests.test_cover_profiles import message
from tests.test_cover_profiles import plant as plant_fixture

plant = plant_fixture
base = base_fixture
geometry = geometry_fixture


def evidence(source):
    return {"provenance": {"source": source}}


def settings(times="guided", slat=None, opening_roll=None, closing_roll=None):
    result = {"opening": evidence(times), "closing": evidence("unknown")}
    for key, source in (("slat_time_s", slat), ("opening_roll", opening_roll), ("closing_roll", closing_roll)):
        if source:
            result[key] = evidence(source)
    return result


@pytest.mark.parametrize(("values", "record", "expected"), [
    (None, None, "none"),
    (settings("manual"), {"check_cm": 2}, "none"),  # As the panel: no measured time, nothing to verify.
    (settings("unknown", "guided", "guided", "guided"), None, "none"),
    (settings("guided"), None, "basic"),  # Times only, the profiles saved before basic calibration.
    (settings("automatic"), {"overrides": {}}, "basic"),
    (settings("guided", "guided", "unknown", "unknown"), None, "basic"),  # Times and lift-off.
    (settings("guided", "guided", "guided", "unknown"), None, "basic"),
    (settings("guided", "guided", "guided", "guided"), None, "refined"),
    (settings("guided", "manual", "manual", "guided"), {"check_cm": None}, "refined"),
    (settings("guided", "guided", "guided", "guided"), {"check_cm": 0}, "verified"),
    (settings("guided"), {"check_cm": 3}, "verified"),
    (settings("guided"), {"check_cm": 0.5}, "verified"),  # The gap is kept to a tenth: a decimal check is a check.
    (settings("guided"), {"check_cm": 2.0}, "verified"),
    (settings("guided"), {"check_cm": True}, "basic"),  # A boolean is not a gap.
])
def test_precision_class(values, record, expected):
    assert precision_class(values, record) == expected


async def listed(hass, cal):
    covers = (await overview(hass, cal.session.entry_id))["covers"]
    return next(cover for cover in covers if cover["entity_id"] == cal.cover.entity_id)


async def test_a_basic_save_turns_a_verified_cover_back_into_a_basic_estimate(hass, base):
    cal = base
    seed(cal, travel_cm=110.0, check_cm=3, check_measured_cm=52.0, check_expected_cm=55.0,
         check_at="2026-10-01T09:00:00+00:00")
    assert (await listed(hass, cal))["precision"] == "none"  # The default times: never measured.
    await basic(cal, 2.69, opening_s=14.26, closing_s=14.03)
    await act(cal, "save", name="Window")
    item = await listed(hass, cal)
    assert (item["precision"], item["check_cm"], item["model"]) == ("basic", None, "slat_roll")
    assert (await read_profile(hass, cal.session.entry_id, cal.cover.entity_id))["precision"] == "basic"


async def test_a_fine_tuned_cover_is_refined_and_verified_with_its_check(hass, geometry):
    cal = geometry
    await review(cal)
    await act(cal, "save", name="Fine-tuned")
    assert (await read_profile(hass, cal.session.entry_id, cal.cover.entity_id))["precision"] == "refined"
    store = cal.session.store
    store.data["covers"][cal.cover.unique_id]["check_cm"] = 1
    assert (await read_profile(hass, cal.session.entry_id, cal.cover.entity_id))["precision"] == "verified"


async def test_typed_times_are_not_a_calibration(hass, plant):
    view = await read_profile(hass, plant.entries[0].entry_id, plant.records[0].entity_id)
    assert view["precision"] == "none"
    view = await write_profile(hass, message(plant))
    assert view["precision"] == "none"


# A refinement whose rolls came out exactly 1 without slats: the linear model, yet refined.
LINEAR = {"slat_time_s": 0.0, "opening_roll": 1.0, "closing_roll": 1.0}
TAPE = {"source": "guided", "recorded_at": "2026-11-02T09:00:00+00:00", "origin_unique_id": None}


def linear_profile(rolls_source):
    rolls = {**TAPE, "source": rolls_source}
    return {"geometry": dict(LINEAR), "geometry_provenance": {"slat_time_s": dict(TAPE), "opening_roll": dict(rolls),
                                                              "closing_roll": dict(rolls)}}


@pytest.mark.parametrize(("rolls_source", "expected"), [("guided", "refined"), ("manual", "refined"), ("unknown", "basic")])
def test_rolls_of_1_without_slats_keep_what_measured_them(rolls_source, expected):
    """Resolved as linear (no roll in the settings), the profile's own evidence of its rolls still counts."""
    assert precision_class(settings("guided"), None, linear_profile(rolls_source)) == expected
    assert precision_class(settings("guided"), {"check_cm": 1.0}, linear_profile(rolls_source)) == "verified"


async def test_a_cover_following_a_refined_linear_profile_is_refined_and_linear(hass, plant):
    from custom_components.myhome.cover_profiles import get_store

    store = get_store(hass, plant.entries[0].entry_id)
    unique = plant.covers[0].unique_id
    times = {**TAPE, "origin_unique_id": unique}
    store.data["profiles"]["door"] = {"name": "Door", "opening_time": 20.0, "closing_time": 20.0,
                                      "provenance": {"opening": dict(times), "closing": dict(times)},
                                      **linear_profile("guided")}
    store.data["assignments"][unique] = "door"
    plant.covers[0].async_apply_cover_profile(store.profile(unique))
    view = await read_profile(hass, plant.entries[0].entry_id, plant.records[0].entity_id)
    assert (view["precision"], view["model"]) == ("refined", "linear_time")
    assert next(item for item in view["profiles"] if item["id"] == "door")["model"] == "linear_time"
    listed = await overview(hass, plant.entries[0].entry_id)
    assert next(item for item in listed["covers"] if item["entity_id"] == plant.records[0].entity_id)["precision"] == "refined"
    assert next(item for item in listed["profiles"] if item["id"] == "door")["model"] == "linear_time"
