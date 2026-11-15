"""What is known of a cover's slats: a profile's slat time (> 0 with, 0 without, no geometry not said; a profile no
longer carries a mark of its own) and the cover's own mark."""
from unittest.mock import patch

import pytest
import voluptuous as vol
from aiohttp.resolver import ThreadedResolver
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome.cover_profile_export import export_profiles
from custom_components.myhome.cover_profiles import (
    STORED,
    WS_WRITE,
    ProfileError,
    get_store,
    read_profile,
    register_api,
    write_profile,
)
from custom_components.myhome.cover_settings import reference_profile, slats_state
from custom_components.myhome.cover_settings_api import overview
from tests.test_cover_calibration_base import act, basic, runs
from tests.test_cover_calibration_base import base as base_fixture
from tests.test_cover_profiles import plant as plant_fixture

plant = plant_fixture
base = base_fixture


def store_of(hass, plant):
    return get_store(hass, plant.entries[0].entry_id)


async def write(hass, plant, index=0, **msg):
    return await write_profile(hass, {"entry_id": plant.entries[0].entry_id, "entity_id": plant.covers[index].entity_id,
                                      "revision": store_of(hass, plant).data["revision"], **msg})


def assigned(cal):
    data = cal.session.store.data
    return data["profiles"][data["assignments"][cal.cover.unique_id]]


async def test_a_basic_calibration_with_slats_and_the_cover_reads_measured(hass, base):
    cal = base
    await basic(cal, lift=2.7)
    await act(cal, "save", name="Window")
    assert "slats" not in assigned(cal) and assigned(cal)["geometry"]["slat_time_s"] == 2.7
    view = await read_profile(hass, cal.session.entry_id, cal.cover.entity_id)
    assert view["slats_state"] == "yes_measured"
    assert [item["slats"] for item in (await overview(hass, cal.session.entry_id))["profiles"]] == [True]
    with pytest.raises(ProfileError, match="slats_measured"):
        await write_profile(hass, {"entry_id": cal.session.entry_id, "entity_id": cal.cover.entity_id,
                                   "revision": cal.session.store.data["revision"], "action": "manual_slats", "slats": False})


@pytest.mark.parametrize("base", [{"slats": False}], indirect=True)
async def test_a_basic_calibration_without_slats_saves_a_slat_time_of_0(hass, base):
    cal = base
    await runs(cal)
    await act(cal, "save", name="Door")
    profile = assigned(cal)
    assert "slats" not in profile and profile["geometry"]["slat_time_s"] == 0.0
    assert [item["slats"] for item in (await overview(hass, cal.session.entry_id))["profiles"]] == [False]
    view = await read_profile(hass, cal.session.entry_id, cal.cover.entity_id)
    assert view["slats_state"] == "no_measured", "the slat time of 0 measured on this cover says so"
    with pytest.raises(ProfileError, match="slats_measured"):  # To change it: calibrate again.
        await write_profile(hass, {"entry_id": cal.session.entry_id, "entity_id": cal.cover.entity_id,
                                   "revision": cal.session.store.data["revision"], "action": "manual_slats", "slats": True})


async def test_the_cards_word_on_a_cover_without_a_measurement(hass, plant):
    unique = plant.covers[0].unique_id
    assert (await read_profile(hass, plant.entries[0].entry_id, plant.covers[0].entity_id))["slats_state"] == "unknown"
    await write(hass, plant, action="manual_slats", slats=True)
    assert store_of(hass, plant).data["covers"][unique] == {"overrides": {}, "slats": True}, "a record for the mark alone"
    assert (await read_profile(hass, plant.entries[0].entry_id, plant.covers[0].entity_id))["slats_state"] == "yes"
    await write(hass, plant, action="manual_slats", slats=False)
    listed = next(item for item in (await overview(hass, plant.entries[0].entry_id))["covers"] if item["entity_id"] == plant.covers[0].entity_id)
    assert listed["slats_state"] == "no"
    await write(hass, plant, action="manual_slats", slats=None)
    assert unique not in store_of(hass, plant).data["covers"], "not said again: nothing left in the record"
    with pytest.raises(vol.Invalid, match="slats is required"):
        await write(hass, plant, action="manual_slats")


async def test_a_follower_inherits_its_profiles_slats_and_cannot_declare_them(hass, plant):
    """A cover that follows a profile knows its slats from it, read only; its own declaration
    (made before, without a profile) stays in its record and counts again once it follows none."""
    first = plant.covers[0].unique_id
    await write(hass, plant, 0, action="manual_slats", slats=False)  # Declared without a profile.
    assert slats_state(store_of(hass, plant).data, first) == "no"
    view = await write(hass, plant, 1, action="save", profile={"name": "Kitchen", "opening_time": 20, "closing_time": 22,
                                                                 "geometry": {"slat_time_s": 2.5, "opening_roll": 1.0, "closing_roll": 1.0}})
    profile_id = view["assigned_profile_id"]
    await write(hass, plant, 0, action="assign", profile_id=profile_id)
    data = store_of(hass, plant).data
    assert slats_state(data, first) == "yes_profile"
    assert data["covers"][first]["slats"] is False, "the declaration stays"
    with pytest.raises(ProfileError, match="slats_profile"):
        await write(hass, plant, 0, action="manual_slats", slats=False)
    catalogue = (await overview(hass, plant.entries[0].entry_id))["profiles"]
    assert [item["slats"] for item in catalogue] == [True]
    # Typed by hand, the slat time is not a measurement: the source's state is its profile's.
    assert slats_state(data, plant.covers[1].unique_id) == "yes_profile"
    listed = {item["entity_id"]: item["slats_state"] for item in (await overview(hass, plant.entries[0].entry_id))["covers"]}
    assert listed[plant.covers[0].entity_id] == "yes_profile"
    await write(hass, plant, 0, action="assign", profile_id=None)
    assert slats_state(store_of(hass, plant).data, first) == "no", "unlinked: its own declaration again"


def test_the_states_from_the_profiles_slat_time():
    guided = {"source": "guided", "recorded_at": "2026-11-02T09:00:00+00:00", "origin_unique_id": "a"}
    unknown = {"source": "unknown", "recorded_at": None, "origin_unique_id": None}

    def data(slat=None, evidence=guided):
        profile = {"name": "P", "opening_time": 20, "closing_time": 20, "provenance": {"opening": guided, "closing": guided}}
        if slat is not None:
            profile["geometry"] = {"slat_time_s": slat, "opening_roll": 1.0, "closing_roll": 1.0}
            profile["geometry_provenance"] = {"slat_time_s": evidence, "opening_roll": unknown, "closing_roll": unknown}
        return {"profiles": {"p": profile}, "assignments": {"a": "p", "b": "p"}, "covers": {"b": {"overrides": {}, "slats": True}}}
    assert [slats_state(data(2.5), unique) for unique in "ab"] == ["yes_measured", "yes_profile"]
    assert [slats_state(data(0.0), unique) for unique in "ab"] == ["no_measured", "no_profile"]
    assert [slats_state(data(2.5, unknown), unique) for unique in "ab"] == ["yes_profile", "yes_profile"]
    assert [slats_state(data(), unique) for unique in "ab"] == ["unknown", "unknown"], "an old profile says nothing"


def test_a_profile_stored_with_the_old_mark_loads_without_it_and_the_covers_mark_stays():
    """Profiles saved by an earlier version carry `slats`: it is dropped in silence."""
    evidence = {"source": "manual", "recorded_at": "2026-11-02T09:00:00+00:00", "origin_unique_id": None}
    stored = STORED({"revision": 1, "assignments": {}, "native_fallbacks": {},
                     "profiles": {"old": {"name": "Old", "opening_time": 20, "closing_time": 20},
                                  "new": {"name": "New", "opening_time": 20, "closing_time": 20, "slats": False,
                                          "provenance": {"opening": evidence, "closing": evidence}},
                                  "odd": {"name": "Odd", "opening_time": 20, "closing_time": 20, "slats": "yes"}},
                     "covers": {"a": {"overrides": {}, "slats": True}}})
    assert all("slats" not in profile for profile in stored["profiles"].values())
    assert stored["covers"]["a"]["slats"] is True
    with pytest.raises(vol.Invalid):
        STORED({**stored, "covers": {"a": {"overrides": {}, "slats": "yes"}}})
    previous = {"name": "New", "opening_time": 20, "closing_time": 20, "slats": False}
    assert "slats" not in reference_profile({"name": "Renamed", "opening_time": 20, "closing_time": 20}, previous)


async def test_a_stored_profile_with_the_old_mark_is_read_and_written_without_it(hass, plant):
    store = store_of(hass, plant)
    unique = plant.covers[0].unique_id
    await write(hass, plant, 0, action="save", profile={"name": "Kitchen", "opening_time": 20, "closing_time": 22})
    data = store.data
    profile_id = data["assignments"][unique]
    data["profiles"][profile_id]["slats"] = True  # As an earlier version stored it.
    view = await read_profile(hass, plant.entries[0].entry_id, plant.covers[0].entity_id)
    assert view["assigned_profile_id"] == profile_id
    await write(hass, plant, 0, action="update_shared", profile_id=profile_id,
                profile={"name": "Kitchen 2", "opening_time": 20, "closing_time": 22, "slats": True},
                confirmation=(await write(hass, plant, 0, action="preview", profile_id=profile_id, profile={
                    "name": "Kitchen 2", "opening_time": 20, "closing_time": 22}))["confirmation"])
    assert "slats" not in store.data["profiles"][profile_id] and store.data["profiles"][profile_id]["name"] == "Kitchen 2"


async def test_the_marks_are_exported(hass, plant):
    await write(hass, plant, 0, action="save", profile={"name": "Kitchen", "opening_time": 20, "closing_time": 22, "slats": False})
    store_of(hass, plant).data["assignments"].clear()  # The cover's own mark: only without a profile.
    await write(hass, plant, 0, action="manual_slats", slats=True)
    exported = await export_profiles(hass, plant.entries[0].entry_id)
    assert "slats" not in exported["profiles"][0]
    assert next(cover for cover in exported["covers"] if cover.get("entity_id") == plant.covers[0].entity_id)["slats"] is True


async def test_websocket_manual_slats(hass, plant, hass_ws_client):
    register_api(hass)
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        client = await hass_ws_client(hass)
    request = {"type": WS_WRITE, "entry_id": plant.entries[0].entry_id, "entity_id": plant.covers[0].entity_id,
               "revision": store_of(hass, plant).data["revision"], "action": "manual_slats", "slats": True}
    try:
        await client.send_json({"id": 1, **request})
        response = await client.receive_json()
        assert response["success"] and response["result"]["slats_state"] == "yes"
        await client.send_json({"id": 2, **request, "revision": request["revision"] + 1, "slats": "maybe"})
        assert (await client.receive_json())["error"]["code"] == "invalid_format"
    finally:
        await client.close()
