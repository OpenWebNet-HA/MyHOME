"""One guided leg preserves the other direction's saved evidence and shared profile."""

import copy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome import cover_profiles as profiles
from custom_components.myhome.cover_calibration import WS_START, begin, register_api
from custom_components.myhome.cover_profile_provenance import unknown_provenance
from tests.test_cover_calibration import act, bus
from tests.test_cover_profiles import plant as plant_fixture

plant = plant_fixture


@pytest.fixture(params=["opening", "closing"])
async def quick(hass, plant, request):
    entry_id = plant.entries[0].entry_id
    source = await profiles.write_profile(
        hass,
        {
            "entry_id": entry_id,
            "entity_id": plant.covers[1].entity_id,
            "revision": 0,
            "action": "save",
            "profile": {"name": "Shared", "opening_time": 25.5, "closing_time": 32.25},
        },
    )
    profile_id = source["assigned_profile_id"]
    await profiles.write_profile(
        hass,
        {
            "entry_id": entry_id,
            "entity_id": plant.covers[0].entity_id,
            "revision": 1,
            "action": "assign",
            "profile_id": profile_id,
        },
    )
    queue, clock = [], [100.0]
    plant.gateways[0].async_queue_calibration = lambda message, guard, lock: queue.append(
        (message, guard, lock)
    )
    connection = MagicMock(subscriptions={})
    msg = {
        "id": 77,
        "entry_id": entry_id,
        "entity_id": plant.covers[0].entity_id,
        "revision": 2,
        "direction": request.param,
    }
    with patch(
        "custom_components.myhome.cover_calibration.monotonic", side_effect=lambda: clock[0]
    ):
        session = await begin(hass, connection, msg)
        yield SimpleNamespace(
            session=session,
            connection=connection,
            request=msg,
            queue=queue,
            clock=clock,
            cover=plant.covers[0],
            plant=plant,
            original_id=profile_id,
        )
        session.close()


async def measure(quick):
    opening = quick.session.direction == "opening"
    await act(quick, "open" if opening else "close")
    assert quick.queue[-1][1]()
    quick.clock[0] += 3  # Queue/feedback latency is excluded.
    bus(quick, "*2*1*11##" if opening else "*2*2*11##")
    quick.clock[0] += 12.75
    await act(quick, "endpoint")


@pytest.mark.parametrize("retained_source", ["manual", "automatic", "unknown"])
async def test_one_leg_preserves_saved_evidence_shared_profile_and_restart(
    hass, quick, retained_source
):
    session = quick.session
    retained = "closing" if session.direction == "opening" else "opening"
    # Seed each supported legacy/evidence shape before restarting the session.
    session.close()
    original = session.store.data["profiles"][quick.original_id]
    if retained_source == "unknown":
        original["provenance"][retained] = unknown_provenance()[retained]
    else:
        original["provenance"][retained]["source"] = retained_source
    await session.store.store.async_save(session.store.data)
    quick.queue.clear()
    quick.session = session = await begin(hass, quick.connection, quick.request)
    before = copy.deepcopy(session.store.data)
    evidence = copy.deepcopy(original["provenance"][retained])
    assert quick.queue == []
    assert session.view()["direction"] == session.direction
    assert session.values == {f"{retained}_time": original[f"{retained}_time"]}
    assert session.provenance[retained] is not original["provenance"][retained]
    with patch(
        "custom_components.myhome.cover_profile_provenance.dt_util.utcnow",
        return_value=datetime(2026, 9, 16, 10, tzinfo=UTC),
    ):
        await measure(quick)
    assert session.phase == "review"
    assert session.values[f"{session.direction}_time"] == 12.75
    assert session.store.data == before
    assert quick.cover.current_cover_position == (100 if session.direction == "opening" else 0)
    with pytest.raises(profiles.ProfileError, match="calibration_step"):
        await act(quick, "close" if session.direction == "opening" else "open")
    with patch.object(
        session.store.store, "async_save", wraps=session.store.store.async_save
    ) as save:
        await act(quick, "save", name="One direction")
    save.assert_awaited_once()
    profile = session.store.profile(quick.cover.unique_id)
    assert profile["id"] != quick.original_id
    assert (
        session.store.data["profiles"][quick.original_id] == before["profiles"][quick.original_id]
    )
    assert session.store.profile(quick.plant.covers[1].unique_id)["id"] == quick.original_id
    assert session.store.data["revision"] == 3
    assert profile["provenance"][retained] == evidence
    assert profile["provenance"][session.direction] == {
        "source": "guided",
        "recorded_at": "2026-09-16T10:00:00+00:00",
        "origin_unique_id": quick.cover.unique_id,
    }
    assert [str(item[0]) for item in quick.queue] == [
        "*2*1*11##" if session.direction == "opening" else "*2*2*11##",
        "*2*0*11##",
    ]
    persisted = copy.deepcopy(session.store.data)
    hass.data[profiles.DATA_KEY].pop(session.entry_id)
    await profiles.bind_cover(hass, quick.cover)
    assert profiles.get_store(hass, session.entry_id).data == persisted


@pytest.mark.parametrize("failure", ["cancel", "unexpected_stop", "storage_error"])
async def test_quick_failure_preserves_original_configuration(quick, failure):
    before = copy.deepcopy(quick.session.store.data)
    if failure == "unexpected_stop":
        await act(quick, "open" if quick.session.direction == "opening" else "close")
        quick.queue[-1][1]()
        bus(quick, "*2*1*11##" if quick.session.direction == "opening" else "*2*2*11##")
        bus(quick, "*2*0*11##")
        assert quick.session.phase == "interrupted"
    else:
        await measure(quick)
        if failure == "cancel":
            await act(quick, "cancel")
        else:
            with patch.object(quick.session.store.store, "async_save", side_effect=OSError("disk")):
                with pytest.raises(OSError):
                    await act(quick, "save", name="Retry")
            assert quick.session.phase == "review"
            assert len(quick.session.values) == len(quick.session.provenance) == 2
    assert quick.session.store.data == before
    if failure != "storage_error":
        assert quick.session.values == quick.session.provenance == {}


async def test_quick_requires_saved_profile_and_guided_valid_direction(hass, plant):
    entry_id = plant.entries[0].entry_id
    msg = {
        "id": 1,
        "entry_id": entry_id,
        "entity_id": plant.covers[0].entity_id,
        "revision": 0,
        "direction": "opening",
    }
    connection = MagicMock(subscriptions={})
    for extra, error, code in [
        ({}, profiles.ProfileError, "calibration_profile_required"),
        ({"mode": "automatic"}, profiles.ProfileError, "invalid_profile"),
        ({"direction": "sideways"}, vol.Invalid, "value must be one of"),
    ]:
        with pytest.raises(error, match=code):
            await begin(hass, connection, {**msg, **extra})
        assert profiles.get_store(hass, entry_id).calibration is None
        assert plant.covers[0]._calibration is None
    assert connection.subscriptions == {}


async def test_quick_stale_revision_cannot_copy_outdated_assignment(hass, quick):
    quick.session.close()
    await profiles.write_profile(hass, {**quick.request, "action": "assign", "profile_id": None})
    with pytest.raises(profiles.ProfileError, match="revision_conflict"):
        await begin(hass, quick.connection, quick.request)
    assert quick.session.store.calibration is None


async def test_quick_real_websocket_schema_requires_profile_before_session(
    hass, plant, hass_ws_client
):
    from aiohttp.resolver import ThreadedResolver

    register_api(hass)
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": WS_START,
            "entry_id": plant.entries[0].entry_id,
            "entity_id": plant.covers[0].entity_id,
            "revision": 0,
            "direction": "closing",
        }
    )
    response = await client.receive_json()
    assert response["error"]["code"] == "calibration_profile_required"
    await client.close()
