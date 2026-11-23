"""Measurement destinations preserve scope, evidence and atomic transactions."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp.resolver import ThreadedResolver
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome import cover_profiles as profiles
from custom_components.myhome.cover_calibration import WS_ACTION, WS_START, begin, register_api
from custom_components.myhome.cover_calibration_save import save_measurement
from tests.test_cover_calibration_direction import measure
from tests.test_cover_calibration_direction import quick as quick_fixture
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_panel_cover_calibration import act, automatic_next, automatic_run, measured
from tests.test_panel_cover_calibration import calibration as calibration_fixture

plant = plant_fixture
quick = quick_fixture
calibration = calibration_fixture


async def test_a_calibration_no_longer_saves_on_the_cover(hass, quick):
    """Always a profile; the cover's own values stay for the manual overrides."""
    await measure(quick)
    before = copy.deepcopy(quick.session.store.data)
    assert quick.session.view()["save_modes"] == ["new", "shared"]
    with pytest.raises(profiles.ProfileError, match="invalid_profile"):
        await act(quick, "save", save_mode="cover")
    with pytest.raises(profiles.ProfileError, match="invalid_profile"):
        await save_measurement(quick.session, {"save_mode": "cover"})
    assert (quick.session.phase, quick.session.store.data) == ("review", before)


@pytest.mark.parametrize("mode", ["shared"])
async def test_partial_destination_persists_only_measured_direction(hass, quick, mode):
    await measure(quick)
    session, cover = quick.session, quick.cover
    before = copy.deepcopy(session.store.data)
    direction = session.direction
    opposite = "closing" if direction == "opening" else "opening"
    kwargs = {"save_mode": mode}
    queued = len(quick.queue)
    preview = (await act(quick, "preview_save", **kwargs))["save_preview"]
    assert session.store.data == before
    assert len(preview["followers"]) == 2
    kwargs["confirmation"] = preview["confirmation"]
    with patch.object(session.store.store, "async_save", wraps=session.store.store.async_save) as save:
        await act(quick, "save", **kwargs)
    save.assert_awaited_once()
    data = session.store.data
    assert data["revision"] == before["revision"] + 1
    assert data["assignments"] == before["assignments"]
    assert len(data["profiles"]) == len(before["profiles"])
    assert len(quick.queue) == queued
    result = data["profiles"][quick.original_id]
    assert result[f"{direction}_time"] == 12.75
    assert result["provenance"][direction] == session.provenance[direction]
    assert result[f"{opposite}_time"] == before["profiles"][quick.original_id][f"{opposite}_time"]
    assert result["provenance"][opposite] == before["profiles"][quick.original_id]["provenance"][opposite]
    hass.data[profiles.DATA_KEY].pop(session.entry_id)
    await profiles.bind_cover(hass, cover)
    assert profiles.get_store(hass, session.entry_id).data == data


async def test_without_a_profile_a_new_one_named_after_the_cover_keeps_backend_evidence(hass, calibration):
    await measured(calibration)
    session = calibration.session
    assert session.view()["save_modes"] == ["new"]
    await act(calibration, "save")  # No name: the cover's.
    data = session.store.data
    profile = data["profiles"][data["assignments"][calibration.cover.unique_id]]
    assert profile["name"] == calibration.session.profile_name({}) == calibration.cover.entity_id  # No state, no name.
    hass.states.async_set(calibration.cover.entity_id, "closed", {"friendly_name": "Hall door"})
    assert calibration.session.profile_name({}) == "Hall door"  # The name Home Assistant shows.
    assert calibration.session.profile_name({"name": "Mine"}) == "Mine"
    assert data["covers"].get(calibration.cover.unique_id, {}).get("overrides", {}) == {}
    assert profile["provenance"] == {direction: session.provenance[direction] for direction in ("opening", "closing")}
    assert (profile["opening_time"], profile["closing_time"]) == (20.5, 40.5)


async def test_shared_preview_matches_override_removal_and_preserves_other_overrides(hass, quick):
    store, cover = quick.session.store, quick.cover
    direction = quick.session.direction
    opposite = "closing" if direction == "opening" else "opening"
    # Seed pre-existing personal values in the shared store, not browser drafts.
    for target_cover in quick.plant.covers[:2]:
        store.data["covers"][target_cover.unique_id] = {"overrides": {
            key: {"value": 55.0, "provenance": {"source": "unknown", "recorded_at": None, "origin_unique_id": None}}
            for key in (direction, opposite)}}
    await measure(quick)
    preview = (await act(quick, "preview_save", save_mode="shared"))["save_preview"]
    first = next(row for row in preview["followers"] if row["entity_id"] == cover.entity_id)
    other = next(row for row in preview["followers"] if row["entity_id"] != cover.entity_id)
    assert first["changes"][direction] == {"before": 55, "after": 12.75, "overridden": False, "override_removed": True, "scaled": False}
    assert other["changes"][direction]["after"] == 55
    assert other["changes"][direction]["overridden"]
    await act(quick, "save", save_mode="shared", confirmation=preview["confirmation"])
    assert list(store.data["covers"][cover.unique_id]["overrides"]) == [opposite]
    assert store.data["covers"][quick.plant.covers[1].unique_id]["overrides"][direction]["value"] == 55


@pytest.mark.parametrize("mode", ["new", "shared"])
async def test_failed_save_keeps_review_and_all_committed_data(hass, quick, mode):
    await measure(quick)
    session = quick.session
    before = copy.deepcopy(session.store.data)
    options = {"save_mode": mode}
    if mode == "shared":
        options["confirmation"] = (await act(quick, "preview_save", **options))["save_preview"]["confirmation"]
    with patch.object(session.store.store, "async_save", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            await act(quick, "save", **options)
    assert session.phase == "review"
    assert session.store.data == before
    assert session.values[session.direction + "_time"] == 12.75
    await act(quick, "save", **options)
    assert session.phase == "saved"


async def test_shared_confirmation_is_bound_to_session_proposal_and_revision(hass, quick):
    with pytest.raises(profiles.ProfileError, match="calibration_step"):
        await act(quick, "preview_save", save_mode="shared")
    await measure(quick)
    for mode in ("cover", "new"):
        with pytest.raises(profiles.ProfileError, match="calibration_step"):
            await act(quick, "preview_save", save_mode=mode)
    for confirmation in (None, "other-session-token"):
        with pytest.raises(profiles.ProfileError, match="preview_required"):
            await act(quick, "save", save_mode="shared", confirmation=confirmation)
    preview = (await act(quick, "preview_save", save_mode="shared"))["save_preview"]
    quick.session.store.data["revision"] += 1
    with pytest.raises(profiles.ProfileError, match="revision_conflict"):
        await act(quick, "save", save_mode="shared", confirmation=preview["confirmation"])


async def test_new_modes_revalidate_session_and_target_after_lock_wait(hass, quick):
    await measure(quick)
    store = quick.session.store
    async with store.lock:
        pending = asyncio.create_task(act(quick, "save", save_mode="new", name="Late"))
        await asyncio.sleep(0)
        quick.session.close()
    with pytest.raises(profiles.ProfileError, match="calibration_expired"):
        await pending
    assert store.data["revision"] == 2


async def test_shared_destination_and_busy_follower_are_revalidated(hass, quick):
    await measure(quick)
    session = quick.session
    preview = (await act(quick, "preview_save", save_mode="shared"))["save_preview"]
    with patch.object(quick.plant.covers[1], "native_calibration_busy", return_value=True):
        with pytest.raises(profiles.ProfileError, match="calibration_busy"):
            await act(quick, "save", save_mode="shared", confirmation=preview["confirmation"])
    with patch("custom_components.myhome.cover_calibration_save.ready_cover", return_value=object()):
        with pytest.raises(profiles.ProfileError, match="cover_unavailable"):
            await act(quick, "save", save_mode="shared", confirmation=preview["confirmation"])
    session.store.data["assignments"].pop(quick.cover.unique_id)
    assert session.view()["save_modes"] == ["new"]
    with pytest.raises(profiles.ProfileError, match="profile_not_source"):  # No profile, nothing to update.
        await act(quick, "save", save_mode="shared")
    with pytest.raises(profiles.ProfileError, match="profile_not_found"):
        await save_measurement(session, {"save_mode": "shared"}, preview=True)


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
@pytest.mark.parametrize("mode", ["new", "shared"])
async def test_automatic_destinations_keep_measured_evidence(hass, calibration, mode):
    cal = calibration
    cal.session.close()
    await profiles.write_profile(hass, {**cal.request, "action": "save", "profile": {
        "name": "Existing", "opening_time": 30, "closing_time": 40}})
    cal.session = await begin(hass, cal.connection, {**cal.request, "revision": 1})
    await act(cal, "run")
    automatic_run(cal, "open", 5)
    automatic_next(cal)
    automatic_run(cal, "close", 22.5)
    automatic_next(cal)
    automatic_run(cal, "open", 20.5)
    options = {"save_mode": mode}
    if mode == "shared":
        options["confirmation"] = (await act(cal, "preview_save", **options))["save_preview"]["confirmation"]
    await act(cal, "save", **options)
    assert cal.cover._travel_time_up == 20.5 and cal.cover._travel_time_down == 22.5
    for item in cal.cover.resolve_cover_settings(cal.session.store.profile(cal.cover.unique_id)).values():
        assert item["provenance"]["source"] == "automatic"


async def test_real_websocket_saves_a_new_profile_and_rejects_browser_timings(hass, plant, hass_ws_client):
    register_api(hass)
    queue, clock = [], [100.0]
    plant.gateways[0].async_queue_calibration = lambda *args: queue.append(args)
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        client = await hass_ws_client(hass)
    try:
        await client.send_json({"id": 1, "type": WS_START, "entry_id": plant.entries[0].entry_id,
                                "entity_id": plant.covers[0].entity_id, "revision": 0})
        assert (await client.receive_json())["success"]
        state = (await client.receive_json())["event"]
        assert state["save_modes"] == ["new"]
        session = profiles.get_store(hass, state["entry_id"]).calibration
        cal = SimpleNamespace(session=session, cover=plant.covers[0], queue=queue, clock=clock)
        with patch("custom_components.myhome.cover_calibration.monotonic", side_effect=lambda: clock[0]):
            await measured(cal)
        request = {"type": WS_ACTION, "entry_id": session.entry_id, "session_id": session.id,
                   "sequence": session.sequence, "action": "save", "save_mode": "new"}
        await client.send_json({"id": 2, **request, "values": {"opening_time": 999}})
        while (response := await client.receive_json()).get("id") != 2:
            pass
        assert response["error"]["code"] == "invalid_format"
        await client.send_json({"id": 3, **request})
        while (response := await client.receive_json()).get("id") != 3:
            pass
        assert response["success"] and response["result"]["phase"] == "saved"
        assert plant.covers[0]._travel_time_up == 20.5
    finally:
        await client.close()
