"""A cycle whose owner is away pauses with its measurements and moves again only on `continue`."""
from datetime import datetime
from unittest.mock import patch

import pytest
from aiohttp.resolver import ThreadedResolver
from homeassistant.core import CoreState
from OWNd.message import OWNMessage
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome.cover_calibration import (
    MOVED_LEASE_SECONDS,
    PRESENCE_SECONDS,
    WS_ACTION,
    WS_START,
    register_api,
)
from custom_components.myhome.cover_calibration_batch import begin_batch
from custom_components.myhome.cover_calibration_recovery import WS_RESUME
from custom_components.myhome.cover_profiles import ProfileError, get_store, read_profile
from tests.test_cover_calibration_batch import action as batch_action
from tests.test_cover_calibration_batch import batch as batch_fixture
from tests.test_cover_calibration_batch import run as batch_run
from tests.test_cover_calibration_recovery import (
    call,
    disconnect,
    fire,
    heartbeat,
    reader,
)
from tests.test_cover_calibration_recovery import recovering as recovering_fixture
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_panel_cover_calibration import act, bus
from tests.test_panel_cover_calibration import calibration as calibration_fixture

plant = plant_fixture
calibration = calibration_fixture
batch = batch_fixture
recovering = recovering_fixture

automatic = pytest.mark.parametrize("calibration", ["automatic"], indirect=True)


async def paused_cycle(hass, cal):
    """Run 1 ends with the owner present; run 2 ends after its tab went away: the cycle pauses."""
    session = cal.session
    await call(hass, cal, cal.connection, session.attachment, "run")
    assert cal.queue[-1][1]()
    bus(cal, "*2*1*11##")
    cal.clock[0] += 5
    bus(cal, "*2*0*11##")
    heartbeat(session)
    fire(session.settle)
    assert session.phase == "starting_close" and cal.queue[-1][1]()
    bus(cal, "*2*2*11##")
    disconnect(cal)
    cal.clock[0] += PRESENCE_SECONDS + 1
    bus(cal, "*2*0*11##")  # The run under way reaches its end.
    assert session.phase == "settling" and session.values == {"closing_time": 46.0}
    count, sequence = len(cal.queue), session.sequence
    fire(session.settle)
    assert len(cal.queue) == count and session.sequence == sequence + 1
    return session


async def paused_batch(hass, batch):
    """The first cover is measured; its owner is away when the pause before the second one ends."""
    batch.session.close()
    batch.queue.clear()
    batch.request["client_id"] = "batch-controller"
    session = batch.session = await begin_batch(hass, batch.connection, batch.request)
    await batch_action(batch, "run")
    batch_run(batch, "open", 5)
    fire(session.settle)
    batch_run(batch, "close", 22)
    fire(session.settle)
    disconnect(batch)
    batch_run(batch, "open", PRESENCE_SECONDS)
    assert session.phase == "between_covers" and len(session.results) == 1
    count = len(batch.queue)
    fire(session.settle)
    assert len(batch.queue) == count
    return session


@automatic
async def test_an_unattended_pause_keeps_the_measurements_and_names_the_next_step(hass, recovering):
    cal = recovering
    session = await paused_cycle(hass, cal)
    assert (session.phase, session.reason) == ("paused", "owner_absent")
    assert session.values == {"closing_time": 46.0} and set(session.provenance) == {"closing"}
    assert session.store.calibration is session and session.owner == "first-controller"
    assert not session.closed and not session.stop_requested
    view = session.view()
    assert view["next_step"] == {"step": "opening", "run_index": 2, "entity_id": cal.cover.entity_id}
    assert view["owner_present"] is False and view["recoverable"] is True
    paused_at = datetime.fromisoformat(view["paused_at"])
    expires = datetime.fromisoformat(view["idle_expires_at"])
    # The lease is the one of a session that has moved: it is not lengthened by the pause.
    assert session.lease_seconds == MOVED_LEASE_SECONDS
    assert (expires - paused_at).total_seconds() == pytest.approx(MOVED_LEASE_SECONDS, abs=1)
    # The dialog reads the same pause.
    read = (await read_profile(hass, session.entry_id, cal.cover.entity_id))["calibration"]
    assert read["phase"] == "paused" and read["next_step"] == view["next_step"]


@automatic
async def test_continue_from_the_owner_starts_the_step_that_was_due(hass, recovering):
    cal = recovering
    session = await paused_cycle(hass, cal)
    back = reader(cal, "first-controller", 88)  # The same tab, reopened: still the owner.
    count = len(cal.queue)
    result = await call(hass, cal, back.connection, back.token, "continue")
    assert result["phase"] == "starting_open" and result["owner"] is True
    assert result["reason"] is None and result["next_step"] is None and result["paused_at"] is None
    assert result["values"] == {"closing_time": 46.0} and result["run_index"] == 2
    assert len(cal.queue) == count + 1 and str(cal.queue[-1][0]) == "*2*1*11##"
    assert cal.queue[-1][1]()
    bus(cal, "*2*1*11##")
    cal.clock[0] += 7
    bus(cal, "*2*0*11##")
    assert session.phase == "review" and session.values == {"closing_time": 46.0, "opening_time": 7.0}
    # A second `continue` has nothing to continue.
    assert await call(hass, cal, back.connection, back.token, "continue") == "calibration_step"


@automatic
async def test_continue_from_the_first_pause_starts_the_closing_run(hass, recovering):
    cal, session = recovering, recovering.session
    await call(hass, cal, cal.connection, session.attachment, "run")
    assert cal.queue[-1][1]()
    bus(cal, "*2*1*11##")
    cal.clock[0] += PRESENCE_SECONDS + 1
    bus(cal, "*2*0*11##")
    fire(session.settle)
    assert session.phase == "paused"
    assert session.view()["next_step"] == {"step": "closing", "run_index": 1, "entity_id": cal.cover.entity_id}
    result = await call(hass, cal, cal.connection, session.attachment, "continue")
    assert result["phase"] == "starting_close" and str(cal.queue[-1][0]) == "*2*2*11##"


@automatic
async def test_continue_belongs_to_the_owner_and_a_claim_hands_it_over(hass, recovering):
    cal = recovering
    session = await paused_cycle(hass, cal)
    other = reader(cal, "second-tab", 88)
    count, sequence = len(cal.queue), session.sequence
    assert await call(hass, cal, other.connection, other.token, "continue") == "calibration_owned"
    assert session.phase == "paused" and len(cal.queue) == count and session.sequence == sequence
    # The owner is absent: one claim is enough, then the new owner continues.
    claimed = reader(cal, "second-tab", 89, claim=True, sequence=sequence)
    assert claimed.claimed and session.owner == "second-tab" and session.phase == "paused"
    assert len(cal.queue) == count  # Taking control moves nothing.
    result = await call(hass, cal, claimed.connection, claimed.token, "continue")
    assert result["phase"] == "starting_open" and result["owner"] is True
    assert len(cal.queue) == count + 1


@automatic
async def test_a_refused_continue_keeps_the_pause_and_its_measurements(hass, recovering):
    """Nothing has moved: an old sequence or a failed check is answered, and the pause stays."""
    cal = recovering
    session = await paused_cycle(hass, cal)
    back = reader(cal, "first-controller", 88)
    count, sequence, step = len(cal.queue), session.sequence, session.view()["next_step"]

    def still_paused():
        assert (session.phase, session.reason) == ("paused", "owner_absent")
        assert session.values == {"closing_time": 46.0} and session.view()["next_step"] == step
        assert len(cal.queue) == count and session.sequence == sequence

    assert await call(hass, cal, back.connection, back.token, "continue",
                      sequence=session.sequence - 1) == "calibration_step"
    still_paused()
    cal.plant.gateways[0].available = False  # Seen when the action arrives.
    assert await call(hass, cal, back.connection, back.token, "continue") == "cover_unavailable"
    still_paused()
    cal.plant.gateways[0].available = True
    with patch.object(hass, "state", CoreState.stopping):  # Seen by the check of the next run.
        assert await call(hass, cal, back.connection, back.token, "continue") == "cover_unavailable"
    still_paused()
    result = await call(hass, cal, back.connection, back.token, "continue")
    assert result["phase"] == "starting_open" and len(cal.queue) == count + 1


@automatic
async def test_a_heartbeat_in_a_pause_makes_the_owner_present_and_moves_nothing(hass, recovering):
    cal = recovering
    session = await paused_cycle(hass, cal)
    back = reader(cal, "first-controller", 88)
    count, sequence = len(cal.queue), session.sequence
    result = await call(hass, cal, back.connection, back.token, "heartbeat")
    assert result["phase"] == "paused" and result["owner_present"] is True and result["owner"] is True
    assert len(cal.queue) == count and session.sequence == sequence
    assert session.settle is None  # No timer brings the cycle back by itself.


@automatic
async def test_stop_in_a_pause_keeps_the_measurements_and_the_pause(hass, recovering):
    cal = recovering
    session = await paused_cycle(hass, cal)
    other = reader(cal, "second-tab", 88)
    count, sequence = len(cal.queue), session.sequence
    result = await call(hass, cal, other.connection, other.token, "stop")
    assert result["phase"] == "paused" and result["stop_requested"] is True and result["read_only"] is True
    assert result["values"] == {"closing_time": 46.0} and result["next_step"]["step"] == "opening"
    assert len(cal.queue) == count + 1 and str(cal.queue[-1][0]) == "*2*0*11##"
    assert session.sequence == sequence + 1
    # The owner can still continue after it.
    back = reader(cal, "first-controller", 89)
    assert (await call(hass, cal, back.connection, back.token, "continue"))["phase"] == "starting_open"


@automatic
async def test_cancel_in_a_pause_discards_and_releases(hass, recovering):
    cal = recovering
    session = await paused_cycle(hass, cal)
    back = reader(cal, "first-controller", 88)
    result = await call(hass, cal, back.connection, back.token, "cancel")
    assert result["phase"] == "cancelled" and result["values"] == {} and result["next_step"] is None
    assert session.closed and session.store.calibration is None


@automatic
async def test_a_pause_that_outlives_the_lease_expires_without_stop(hass, recovering):
    cal = recovering
    session = await paused_cycle(hass, cal)
    count = len(cal.queue)
    fire(session.lease)
    assert session.closed and session.reason == "expired" and session.values == {}
    assert len(cal.queue) == count  # Nothing is moving: no Stop.
    assert session.store.calibration is None and cal.cover._calibration is None


@automatic
async def test_a_movement_from_outside_during_a_pause_interrupts_as_today(hass, recovering):
    cal = recovering
    session = await paused_cycle(hass, cal)
    count = len(cal.queue)
    bus(cal, "*2*1*11##")  # Someone opens the cover from a wall switch.
    assert (session.phase, session.reason) == ("interrupted", "unexpected_movement")
    assert session.values == {} and session.view()["next_step"] is None
    assert len(cal.queue) == count + 1 and str(cal.queue[-1][0]) == "*2*0*11##"


@automatic
async def test_the_owner_leaving_a_pause_keeps_it_until_the_lease(hass, recovering):
    cal = recovering
    session = await paused_cycle(hass, cal)
    back = reader(cal, "first-controller", 88)
    await call(hass, cal, back.connection, back.token, "detach")
    assert session.phase == "paused" and not session.closed and session.values == {"closing_time": 46.0}


async def test_a_batch_paused_between_covers_keeps_its_results_and_goes_on_with_the_next(hass, batch):
    session = await paused_batch(hass, batch)
    assert (session.phase, session.reason) == ("paused", "owner_absent")
    assert len(session.results) == 1 and session.cover_index == 0
    second = batch.plant.covers[1]
    assert session.view()["next_step"] == {"step": "next_cover", "cover_index": 1, "entity_id": second.entity_id}
    assert session.view()["results"][0]["values"] == {"closing_time": 22.0, "opening_time": PRESENCE_SECONDS}
    # The pause watches the cover that moves next; the one already measured is let go.
    first = batch.plant.covers[0]
    assert first._calibration is None and second._calibration is session and session.cover is second
    back = reader(batch, "batch-controller", 88)
    count = len(batch.queue)
    result = await call(hass, batch, back.connection, back.token, "continue")
    assert result["phase"] == "starting_open" and result["cover_index"] == 1
    assert len(result["results"]) == 1 and len(batch.queue) == count + 1
    assert str(batch.queue[-1][0]) == "*2*1*12##" and second._calibration is session


async def test_in_a_batch_pause_only_the_next_cover_can_end_it(hass, batch):
    """A wall switch on a cover already measured changes nothing; on the next cover it interrupts."""
    session = await paused_batch(hass, batch)
    first, second = batch.plant.covers[0], batch.plant.covers[1]
    count = len(batch.queue)
    first.handle_event(OWNMessage.parse(f"*2*2*{first._full_where}##"))
    first.handle_event(OWNMessage.parse(f"*2*0*{first._full_where}##"))
    assert session.phase == "paused" and len(session.results) == 1 and len(batch.queue) == count
    second.handle_event(OWNMessage.parse(f"*2*1*{second._full_where}##"))
    assert (session.phase, session.reason) == ("interrupted", "unexpected_movement")
    assert session.results == [] and str(batch.queue[-1][0]) == f"*2*0*{second._full_where}##"


async def test_a_batch_continue_refused_while_the_next_cover_moves_keeps_the_group(hass, batch):
    session = await paused_batch(hass, batch)
    second = batch.plant.covers[1]
    back = reader(batch, "batch-controller", 88)
    count, sequence = len(batch.queue), session.sequence
    second._move_start_time = 1.0  # Moved by Home Assistant without a bus event yet.
    assert await call(hass, batch, back.connection, back.token, "continue") == "calibration_moving"
    assert session.phase == "paused" and len(session.results) == 1 and session.cover_index == 0
    assert len(batch.queue) == count and session.sequence == sequence
    second._move_start_time = None
    assert (await call(hass, batch, back.connection, back.token, "continue"))["cover_index"] == 1


async def test_releasing_a_batch_paused_between_covers_unbinds_the_next_cover(hass, batch):
    session = await paused_batch(hass, batch)
    fire(session.lease)
    assert session.closed and session.reason == "expired"
    assert all(cover._calibration is None for cover in batch.plant.covers)


async def test_a_batch_paused_between_runs_keeps_the_covers_already_measured(hass, batch):
    session = await paused_batch(hass, batch)
    back = reader(batch, "batch-controller", 88)
    await call(hass, batch, back.connection, back.token, "continue")
    batch_run(batch, "open", 5)
    batch.clock[0] += PRESENCE_SECONDS + 1  # The owner goes away again during the second cover.
    fire(session.settle)
    assert session.phase == "paused" and len(session.results) == 1
    assert session.view()["next_step"] == {"step": "closing", "run_index": 1,
                                           "entity_id": batch.plant.covers[1].entity_id}
    result = await call(hass, batch, back.connection, back.token, "cancel")
    assert result["phase"] == "cancelled" and result["results"] == []


@automatic
async def test_clients_without_client_id_never_pause_and_cannot_continue(hass, calibration):
    cal, session = calibration, calibration.session
    assert {"next_step", "paused_at"}.isdisjoint(session.view())
    await act(cal, "run")
    assert cal.queue[-1][1]()
    bus(cal, "*2*1*11##")
    cal.clock[0] += PRESENCE_SECONDS + 5  # No presence for them: the original contract.
    bus(cal, "*2*0*11##")
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "continue")
    fire(session.settle)
    assert session.phase == "starting_close"


async def test_a_guided_session_has_nothing_to_continue(hass, recovering):
    cal, session = recovering, recovering.session
    assert session.view()["next_step"] is None and session.view()["paused_at"] is None
    assert await call(hass, cal, cal.connection, session.attachment, "continue") == "calibration_step"
    assert session.phase == "confirm_closed" and cal.queue == []


async def test_real_websockets_a_paused_cycle_waits_for_continue(hass, plant, hass_ws_client):
    """The owner closes the panel during the first run: the cycle pauses, then its tab continues it."""
    register_api(hass)
    queue = []
    plant.gateways[0].async_queue_calibration = lambda *args: queue.append(args)
    entry_id = plant.entries[0].entry_id
    cover = plant.covers[0]
    clock = [100.0]

    async def answer(client, message_id):
        while (message := await client.receive_json())["id"] != message_id or message["type"] != "result":
            pass
        return message

    async def event(client, subscription):
        while (message := await client.receive_json())["id"] != subscription or message["type"] != "event":
            pass
        return message["event"]

    with (patch("aiohttp.connector.DefaultResolver", ThreadedResolver),
          patch("custom_components.myhome.cover_calibration.monotonic", side_effect=lambda: clock[0])):
        owner = await hass_ws_client(hass)
        await owner.send_json({"id": 1, "type": WS_START, "entry_id": entry_id, "entity_id": plant.records[0].entity_id,
                               "revision": 0, "mode": "automatic", "client_id": "tab-one"})
        assert (await answer(owner, 1))["success"]
        state = await event(owner, 1)
        act_owner = {"type": WS_ACTION, "entry_id": entry_id, "session_id": state["session_id"],
                     "attachment": state["attachment"]}
        await owner.send_json({"id": 2, **act_owner, "action": "run", "sequence": state["sequence"]})
        assert (await answer(owner, 2))["result"]["phase"] == "starting_open"
        assert queue[-1][1]()
        cover.handle_event(OWNMessage.parse("*2*1*11##"))
        await owner.send_json({"id": 3, **act_owner, "action": "detach"})  # The panel is closed.
        assert (await answer(owner, 3))["success"]
        session = get_store(hass, entry_id).calibration
        clock[0] += 5
        cover.handle_event(OWNMessage.parse("*2*0*11##"))
        count = len(queue)
        fire(session.settle)
        assert session.phase == "paused" and len(queue) == count
        await owner.close()
        await hass.async_block_till_done()
        tab = await hass_ws_client(hass)
        await tab.send_json({"id": 1, "type": WS_RESUME, "entry_id": entry_id, "session_id": session.id,
                             "client_id": "tab-one"})
        assert (await answer(tab, 1))["success"]
        view = await event(tab, 1)
        assert view["phase"] == "paused" and view["owner"] is True and view["owner_present"] is False
        assert view["next_step"]["step"] == "closing"
        act_tab = {"type": WS_ACTION, "entry_id": entry_id, "session_id": session.id, "attachment": view["attachment"]}
        await tab.send_json({"id": 2, **act_tab, "action": "heartbeat"})
        beat = (await answer(tab, 2))["result"]
        assert beat["phase"] == "paused" and beat["owner_present"] is True and len(queue) == count
        await tab.send_json({"id": 3, **act_tab, "action": "continue", "sequence": view["sequence"]})
        resumed = (await answer(tab, 3))["result"]
        assert resumed["phase"] == "starting_close" and len(queue) == count + 1
        assert str(queue[-1][0]) == "*2*2*11##"
        await tab.close()
        session.close()
