"""Measured seconds belong to the measured cover, not automatically its profile."""
import copy

import pytest

from custom_components.myhome.cover_calibration import begin
from custom_components.myhome.cover_profiles import ProfileError
from tests.test_cover_calibration_batch import action
from tests.test_cover_calibration_batch import batch as batch_fixture
from tests.test_cover_calibration_batch import measure as measure_batch
from tests.test_cover_calibration_direction import measure
from tests.test_cover_calibration_direction import quick as quick_fixture
from tests.test_panel_cover_calibration import act, bus, measured
from tests.test_panel_cover_calibration import calibration as calibration_fixture
from tests.test_panel_cover_calibration import plant as plant_fixture

plant = plant_fixture
calibration = calibration_fixture
quick = quick_fixture
batch = batch_fixture


@pytest.mark.parametrize("mode", ["new", "cover"])
async def test_measurement_preserves_travel_and_new_profile_records_reference(calibration, mode):
    cal, session = calibration, calibration.session
    session.store.data["covers"][cal.cover.unique_id] = {"travel_cm": 180, "overrides": {}}
    await measured(cal)
    await act(cal, "save", save_mode=mode, name="Measured")
    data = session.store.data
    assert data["covers"][cal.cover.unique_id]["travel_cm"] == 180
    assert cal.cover._travel_time_up == 20.5 and cal.cover._travel_time_down == 40.5
    if mode == "new":
        profile = next(iter(data["profiles"].values()))
        assert profile["reference_travel_cm"] == 180
        assert data["covers"][cal.cover.unique_id]["overrides"] == {}
    else:
        assert data["profiles"] == {}
        assert data["covers"][cal.cover.unique_id]["overrides"]["opening"]["value"] == 20.5


async def test_shared_measurement_normalizes_to_existing_reference_and_preserves_other_direction(hass, quick):
    cal = quick
    cal.session.close()
    store = cal.session.store
    profile = store.data["profiles"][cal.original_id]
    profile["reference_travel_cm"] = 200
    store.data["covers"][cal.cover.unique_id] = {"travel_cm": 100, "overrides": {}}
    store.data["covers"][cal.plant.covers[1].unique_id] = {"travel_cm": 300, "overrides": {}}
    cal.session = await begin(hass, cal.connection, cal.request)
    try:
        before = copy.deepcopy(profile)
        await measure(cal)
        preview = (await act(cal, "preview_save", save_mode="shared"))["save_preview"]
        direction, opposite = cal.session.direction, "closing" if cal.session.direction == "opening" else "opening"
        first = next(row for row in preview["followers"] if row["entity_id"] == cal.cover.entity_id)
        assert first["changes"][direction]["after"] == 12.75
        assert first["changes"][direction]["scaled"]
        assert preview["after"][direction + "_time"] == 25.5
        assert preview["after"]["reference_travel_cm"] == 200
        await act(cal, "save", save_mode="shared", confirmation=preview["confirmation"])
        bus(cal, "*2*0*11##")
        saved = store.data["profiles"][cal.original_id]
        assert saved[direction + "_time"] == 25.5
        assert saved[opposite + "_time"] == before[opposite + "_time"]
        assert saved["provenance"][opposite] == before["provenance"][opposite]
        assert store.data["covers"][cal.cover.unique_id]["travel_cm"] == 100
        assert cal.cover.resolve_cover_settings(store.profile(cal.cover.unique_id))[direction]["value"] == 12.75
        other = cal.plant.covers[1]
        assert other.resolve_cover_settings(store.profile(other.unique_id))[direction]["value"] == 38.25
    finally:
        cal.session.close()


async def test_shared_reference_requires_target_travel_without_discarding_review(quick):
    cal = quick
    cal.session.store.data["profiles"][cal.original_id]["reference_travel_cm"] = 200
    await measure(cal)
    before = copy.deepcopy(cal.session.store.data)
    with pytest.raises(ProfileError, match="profile_reference_required"):
        await act(cal, "preview_save", save_mode="shared")
    assert cal.session.phase == "review" and cal.session.store.data == before
    await act(cal, "save", save_mode="cover")
    bus(cal, "*2*0*11##")
    assert cal.session.store.data["covers"][cal.cover.unique_id]["overrides"][cal.session.direction]["value"] == 12.75


async def test_automatic_batch_references_each_cover_and_preserves_travel(batch):
    for index, cover in enumerate(batch.plant.covers[:2]):
        batch.session.store.data["covers"][cover.unique_id] = {"travel_cm": 100 + 100 * index, "overrides": {}}
    await measure_batch(batch)
    await action(batch, "save", names=["First", "Second"])
    data = batch.session.store.data
    for index, cover in enumerate(batch.plant.covers[:2]):
        profile = data["profiles"][data["assignments"][cover.unique_id]]
        assert profile["reference_travel_cm"] == data["covers"][cover.unique_id]["travel_cm"] == 100 + 100 * index
        assert cover._travel_time_up == 20 + index and cover._travel_time_down == 22 + index


async def test_shared_nonlinear_timing_measurement_preserves_geometry_and_normalizes_curtain_phase(hass, quick):
    cal = quick
    cal.session.close()
    store = cal.session.store
    profile = store.data["profiles"][cal.original_id]
    profile["reference_travel_cm"] = 200
    profile["geometry"] = {"slat_time_s": 2, "opening_roll": 3, "closing_roll": 2}
    store.data["covers"][cal.cover.unique_id] = {"travel_cm": 100, "overrides": {}}
    cal.session = await begin(hass, cal.connection, cal.request)
    try:
        before = copy.deepcopy(profile)
        await measure(cal)
        preview = (await act(cal, "preview_save", save_mode="shared"))["save_preview"]
        direction = cal.session.direction
        first = next(row for row in preview["followers"] if row["entity_id"] == cal.cover.entity_id)
        assert first["changes"][direction]["after"] == pytest.approx(12.75)
        assert preview["after"]["geometry"] == before["geometry"]
        assert first["geometry_changes"]["slat_time_s"]["before"] == first["geometry_changes"]["slat_time_s"]["after"] == 1
        await act(cal, "save", save_mode="shared", confirmation=preview["confirmation"])
        bus(cal, "*2*0*11##")
        saved = store.data["profiles"][cal.original_id]
        assert saved["geometry"] == before["geometry"]
        opposite = "closing" if direction == "opening" else "opening"
        assert saved["provenance"][opposite] == before["provenance"][opposite]
        assert cal.cover.resolve_cover_settings(store.profile(cal.cover.unique_id))[direction]["value"] == pytest.approx(12.75)
    finally:
        cal.session.close()
