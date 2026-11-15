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
