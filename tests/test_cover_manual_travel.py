"""The curtain travel entered on a cover's card (`manual_travel`), by what the cover is to its profile."""
from unittest.mock import patch

import pytest
from aiohttp.resolver import ThreadedResolver
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome.cover_profiles import (
    WS_WRITE,
    ProfileError,
    get_store,
    read_profile,
    register_api,
    write_profile,
)
from custom_components.myhome.cover_settings_api import overview
from tests.test_cover_profiles import plant as plant_fixture

plant = plant_fixture
CHECK = {"check_cm": 2, "check_measured_cm": 50.0, "check_expected_cm": 52.0, "check_at": "2026-11-01T09:00:00+00:00"}


def store_of(hass, plant):
    return get_store(hass, plant.entries[0].entry_id)


async def write(hass, plant, index=0, **msg):
    return await write_profile(hass, {"entry_id": plant.entries[0].entry_id, "entity_id": plant.covers[index].entity_id,
                                      "revision": store_of(hass, plant).data["revision"], **msg})


async def shared(hass, plant, *, source=0, reference=None):
    """A profile measured on cover `source` (the origin of its times), followed by the other cover."""
    view = await write(hass, plant, source, action="save", profile={
        "name": "Kitchen", "opening_time": 20, "closing_time": 22,
        **({"reference_travel_cm": reference} if reference else {})})
    await write(hass, plant, 1 - source, action="assign", profile_id=view["assigned_profile_id"])
    return view["assigned_profile_id"]


def checked(hass, plant, *indexes):
    """A check kept for these covers, as loaded from storage."""
    for index in indexes:
        store_of(hass, plant).data["covers"].setdefault(plant.covers[index].unique_id, {"overrides": {}}).update(CHECK)


async def edit(hass, plant, index):
    return (await read_profile(hass, plant.entries[0].entry_id, plant.covers[index].entity_id))["travel_edit"]


async def test_on_the_cover_the_profile_was_measured_on_the_travel_becomes_the_reference(hass, plant):
    profile_id = await shared(hass, plant)
    assert (await edit(hass, plant, 0), await edit(hass, plant, 1)) == ("reference", "display")
    follower = plant.covers[1].unique_id
    await write(hass, plant, 1, action="manual_travel", travel_cm=150)  # `display`: kept, for positions in cm only.
    assert plant.covers[1]._travel_time_up == 20
    checked(hass, plant, 0, 1)
    view = await write(hass, plant, 0, action="manual_travel", travel_cm=100)
    data = store_of(hass, plant).data
    # `reference`: the profile's reference travel too, and the follower's times adapt to its 150 cm.
    assert (data["profiles"][profile_id]["reference_travel_cm"], view["travel_cm"]) == (100, 100)
    assert (plant.covers[0]._travel_time_up, plant.covers[1]._travel_time_up) == (20, 30)
    assert await edit(hass, plant, 1) == "scaled"
    # The source's times are the same, but its check gave the gap on the other travel: it goes; the
    # follower's times changed, its check goes too.
    assert "check_cm" not in data["covers"][plant.covers[0].unique_id]
    assert "check_cm" not in data["covers"][follower]
    assert (await read_profile(hass, plant.entries[0].entry_id, plant.covers[0].entity_id))["precision"] == "none"
    # Removed: back to no travel on either, the follower's times back to the profile's.
    checked(hass, plant, 1)
    await write(hass, plant, 0, action="manual_travel", travel_cm=None)
    data = store_of(hass, plant).data
    assert "reference_travel_cm" not in data["profiles"][profile_id]
    assert "travel_cm" not in data["covers"].get(plant.covers[0].unique_id, {})
    assert (plant.covers[1]._travel_time_up, data["covers"][follower]["travel_cm"]) == (20, 150)
    assert "check_cm" not in data["covers"][follower]
    assert await edit(hass, plant, 1) == "display"


async def test_a_follower_of_a_profile_with_a_reference_adapts_its_times(hass, plant):
    profile_id = await shared(hass, plant, source=1, reference=100)
    assert await edit(hass, plant, 0) == "scaled"
    checked(hass, plant, 0)
    await write(hass, plant, 0, action="manual_travel", travel_cm=120)
    data = store_of(hass, plant).data
    record = data["covers"][plant.covers[0].unique_id]
    # `scaled`: the cover's own travel, its times scaled to it; the profile's reference stays.
    assert (record, data["profiles"][profile_id]["reference_travel_cm"]) == ({"overrides": {}, "travel_cm": 120}, 100)
    assert (plant.covers[0]._travel_time_up, plant.covers[0]._travel_time_down) == (24, 26.4)
    # Removed: the profile's travel again, and its times.
    await write(hass, plant, 0, action="manual_travel", travel_cm=None)
    assert plant.covers[0].unique_id not in store_of(hass, plant).data["covers"]
    assert plant.covers[0]._travel_time_up == 20


async def test_a_follower_of_a_profile_without_a_reference_keeps_its_times_and_check(hass, plant):
    await shared(hass, plant, source=1)
    checked(hass, plant, 0)
    view = await write(hass, plant, 0, action="manual_travel", travel_cm=135.5)
    assert (view["travel_cm"], view["travel_edit"], view["effective_opening_time"]) == (135.5, "display", 20)
    assert store_of(hass, plant).data["covers"][plant.covers[0].unique_id]["check_cm"] == 2
    listed = next(item for item in (await overview(hass, plant.entries[0].entry_id))["covers"]
                  if item["entity_id"] == plant.covers[0].entity_id)
    assert (listed["travel_edit"], listed["travel_cm"], listed["check_cm"]) == ("display", 135.5, 2)


async def test_a_cover_without_a_profile_keeps_the_travel_for_positions(hass, plant):
    assert await edit(hass, plant, 0) == "display"
    view = await write(hass, plant, 0, action="manual_travel", travel_cm=90)
    assert (view["travel_cm"], view["effective_opening_time"]) == (90, 30)


async def test_a_travel_the_followers_times_cannot_take_is_refused(hass, plant):
    await shared(hass, plant)
    await write(hass, plant, 1, action="manual_travel", travel_cm=5000)
    before = store_of(hass, plant).data
    with pytest.raises(Exception, match="travel_time"):
        await write(hass, plant, 0, action="manual_travel", travel_cm=1)  # 22 s × 5000 / 1: out of range.
    assert store_of(hass, plant).data == before
    with pytest.raises(Exception, match="travel_cm is required"):
        await write(hass, plant, 0, action="manual_travel")


async def test_not_while_a_follower_is_calibrated_natively(hass, plant):
    await shared(hass, plant)
    with patch.object(type(plant.covers[1]), "native_calibration_busy", lambda cover: cover is plant.covers[1]):
        with pytest.raises(ProfileError, match="calibration_busy"):
            await write(hass, plant, 0, action="manual_travel", travel_cm=100)


async def test_websocket_manual_travel(hass, plant, hass_ws_client):
    await shared(hass, plant)
    register_api(hass)
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        client = await hass_ws_client(hass)
    request = {"type": WS_WRITE, "entry_id": plant.entries[0].entry_id, "entity_id": plant.covers[0].entity_id,
               "revision": store_of(hass, plant).data["revision"], "action": "manual_travel", "travel_cm": 110}
    try:
        await client.send_json({"id": 1, **request})
        response = await client.receive_json()
        assert response["success"] and response["result"]["travel_edit"] == "reference"
        assert response["result"]["profiles"][0]["reference_travel_cm"] == 110
        await client.send_json({"id": 2, **request, "revision": request["revision"] + 1, "travel_cm": 0})
        assert (await client.receive_json())["error"]["code"] == "invalid_format"  # The schema refuses it.
    finally:
        await client.close()
