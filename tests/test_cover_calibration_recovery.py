"""Sessions read from several tabs: one owner, a lost socket changes nothing, no replayed motion."""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.resolver import ThreadedResolver
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import Unauthorized
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome.cover_calibration import (
    IDLE_LEASE_SECONDS,
    LEASE_SECONDS,
    MOVED_LEASE_SECONDS,
    PRESENCE_SECONDS,
    WS_ACTION,
    WS_START,
    begin,
    register_api,
    ws_action,
)
from custom_components.myhome.cover_calibration_batch import begin_batch
from custom_components.myhome.cover_calibration_recovery import WS_RESUME, ws_resume
from custom_components.myhome.cover_profiles import (
    ProfileError,
    get_store,
    read_profile,
    remove_entry,
)
from tests.test_cover_calibration_batch import action as batch_action
from tests.test_cover_calibration_batch import advance as batch_advance
from tests.test_cover_calibration_batch import batch as batch_fixture
from tests.test_cover_calibration_batch import measure
from tests.test_cover_calibration_batch import run as batch_run
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_panel_cover_calibration import act, bus, measured
from tests.test_panel_cover_calibration import calibration as calibration_fixture

plant = plant_fixture
calibration = calibration_fixture
batch = batch_fixture


@pytest.fixture
async def recovering(hass, calibration):
    cal = calibration
    cal.session.close()
    cal.queue.clear()
    cal.request["client_id"] = "first-controller"
    cal.session = await begin(hass, cal.connection, cal.request)
    yield cal
    cal.session.close()


def disconnect(cal):
    cal.connection.subscriptions[77]()


def heartbeat(session):
    """The owner's heartbeat through `perform`, delivered between two synchronous bus events."""
    owner = next(item for item in session.subscribers.values() if item.client_id == session.owner)
    beat = session.perform(owner, {"action": "heartbeat"})
    with pytest.raises(StopIteration):
        beat.send(None)  # A heartbeat completes without awaiting anything.


def beating(advance):
    """The panel beats every 15 s: before each pause of a cycle ends, the owner has been seen."""
    def step(batch):
        heartbeat(batch.session)
        advance(batch)
    return step


def fire(handle):
    callback, args = handle._callback, handle._args
    handle.cancel()
    callback(*args)


def reader(cal, client_id, subscription_id=88, *, claim=False, sequence=None):
    """A second tab or socket subscribing with `resume`; returns its connection and token."""
    connection = MagicMock(subscriptions={}, user=SimpleNamespace(is_admin=True))
    subscriber, claimed = cal.session.attach(connection, subscription_id, client_id, claim=claim, sequence=sequence)
    return SimpleNamespace(connection=connection, token=subscriber.token, claimed=claimed)


async def call(hass, cal, connection, token, action, **extra):
    """One `action` over the websocket handler; returns the result or the error code."""
    connection.send_result.reset_mock()
    connection.send_error.reset_mock()
    ws_action(hass, connection, {"id": 9, "entry_id": cal.session.entry_id, "session_id": cal.session.id,
                                 "attachment": token, "action": action, "sequence": cal.session.sequence, **extra})
    await hass.async_block_till_done()
    if connection.send_error.called:
        return connection.send_error.call_args.args[1]
    return connection.send_result.call_args.args[1]


async def running(cal):
    """The owner has started a movement and the bus reports it."""
    await act(cal, "open")
    assert cal.queue[-1][1]()
    bus(cal, "*2*1*11##")
    assert cal.session.phase == "opening" and cal.session.reservation.pending


@pytest.mark.parametrize("checkpoint", ["initial", "half", "review"])
async def test_lost_socket_keeps_checkpoint_owner_and_evidence_without_commands(hass, recovering, checkpoint):
    cal, session = recovering, recovering.session
    if checkpoint == "review":
        await measured(cal)
    elif checkpoint == "half":
        await act(cal, "open")
        assert cal.queue[-1][1]()
        bus(cal, "*2*1*11##")
        cal.clock[0] += 22
        await act(cal, "endpoint")
    phase, values, evidence = session.phase, dict(session.values), copy.deepcopy(session.provenance)
    before, sequence = len(cal.queue), session.sequence
    assert (await call(hass, cal, cal.connection, session.attachment, "heartbeat"))["owner"] is True
    old_cleanup = cal.connection.subscriptions[77]
    old_token = session.attachment
    disconnect(cal)
    assert session.store.calibration is session and session.subscribers == {}
    assert (session.phase, session.values, session.provenance, session.sequence) == (phase, values, evidence, sequence)
    assert len(cal.queue) == before
    view = await read_profile(hass, session.entry_id, cal.cover.entity_id)
    assert view["calibration"]["session_id"] == session.id
    assert view["calibration"]["attached"]  # The owner is still present for 45 s.
    assert "attachment" not in view["calibration"]
    cal.clock[0] += PRESENCE_SECONDS + 1
    view = await read_profile(hass, session.entry_id, cal.cover.entity_id)
    assert not view["calibration"]["attached"] and view["calibration"]["recoverable"]
    # The same tab on a new socket: still the owner, and the old token is powerless.
    again = reader(cal, "first-controller", 88)
    assert not again.claimed and session.owner == "first-controller" and not session.present()
    assert (await call(hass, cal, again.connection, again.token, "heartbeat"))["owner_present"] is True
    old_cleanup()
    assert again.token in session.subscribers
    for action in ("heartbeat", "stop", "cancel", "detach"):
        assert await call(hass, cal, cal.connection, old_token, action) == "calibration_expired"
    assert len(cal.queue) == before and session.sequence == sequence
    if checkpoint == "review":
        result = await call(hass, cal, again.connection, again.token, "save", name="Recovered")
        assert result["phase"] == "saved" and result["owner"] is True
        assert session.store.data["revision"] == 1


@pytest.mark.parametrize("phase", ["starting_open", "opening", "closing"])
async def test_lost_socket_during_cycle_keeps_measurement_and_writes_no_stop(recovering, phase):
    """A movement already under way runs to its end; the pauses of a cycle are tested below."""
    cal, session = recovering, recovering.session
    await act(cal, "open")
    guard = cal.queue[-1][1]
    session.phase = phase
    session.values["opening_time"] = 22
    count = len(cal.queue)
    disconnect(cal)
    assert guard() is (phase == "starting_open")  # The queued Open is still the owner's.
    cal.clock[0] += 120  # Well past presence: a running movement does not depend on it.
    assert session.phase == phase and session.values == {"opening_time": 22}
    assert not session.deadline.cancelled()
    assert len(cal.queue) == count and not session.closed
    session.attach(cal.connection, 78, "first-controller")
    assert len(cal.queue) == count


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
@pytest.mark.parametrize("owner", ["absent", "replayed", "back"])
async def test_without_its_owner_an_automatic_cycle_starts_no_new_movement(hass, recovering, owner):
    """The maintainer's invariant: with nobody guiding, the pause never ends in a new run.

    The cycle waits in `paused` instead, keeping what it measured. A start replayed after a
    reconnection (for instance, after a Cancel pressed offline was lost) only reads the
    session: it does not make the owner present again.
    """
    cal, session = recovering, recovering.session
    await call(hass, cal, cal.connection, session.attachment, "run")
    assert cal.queue[-1][1]()
    bus(cal, "*2*1*11##")
    disconnect(cal)
    cal.clock[0] += PRESENCE_SECONDS + 5
    if owner == "replayed":
        assert await begin(hass, cal.connection, {**cal.request, "session_id": session.id}) is session
        assert not session.present()
    elif owner == "back":
        again = reader(cal, "first-controller", 88)  # The same tab on a new socket, beating again.
        await call(hass, cal, again.connection, again.token, "heartbeat")
    bus(cal, "*2*0*11##")  # The run under way reaches its end, as before.
    assert session.phase == "settling" and not session.reservation.pending
    count = len(cal.queue)
    fire(session.settle)
    if owner == "back":
        assert session.phase == "starting_close" and len(cal.queue) == count + 1
        return
    assert session.phase == "paused" and session.reason == "owner_absent"
    assert len(cal.queue) == count  # No new movement, and no Stop: nothing is moving.
    assert session.store.calibration is session and session.owner == "first-controller"
    assert session.settle is None  # Nothing brings the cycle back but an explicit `continue`.


async def test_without_its_owner_a_batch_never_moves_to_the_next_cover(hass, batch):
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
    batch_run(batch, "open", PRESENCE_SECONDS)  # The run under way reaches its end.
    assert session.phase == "between_covers" and len(session.results) == 1
    count = len(batch.queue)
    fire(session.settle)
    assert session.phase == "paused" and session.reason == "owner_absent"
    assert len(session.results) == 1 and session.cover_index == 0 and len(batch.queue) == count
    assert session.settle is None


async def test_idle_lease_ends_session_without_stop_when_nothing_moves(recovering):
    cal, session = recovering, recovering.session
    lease = session.lease
    assert session.lease_seconds == IDLE_LEASE_SECONDS
    disconnect(cal)
    assert session.lease is lease  # A lost socket does not touch the lease.
    fire(lease)
    assert session.closed and session.reason == "expired" and cal.queue == []
    assert session.store.calibration is None and cal.cover._calibration is None
    with pytest.raises(ProfileError, match="calibration_expired"):
        session.attach(cal.connection, 90, "too-late")


@pytest.mark.parametrize("cleanup", ["cancel", "unload", "shutdown", "remove"])
async def test_unattended_session_releases_gateway_on_explicit_lifecycle_end(hass, recovering, cleanup):
    cal, session = recovering, recovering.session
    disconnect(cal)
    lease = session.lease
    if cleanup == "cancel":
        again = reader(cal, "first-controller", 80)
        assert (await call(hass, cal, again.connection, again.token, "cancel"))["phase"] == "cancelled"
    elif cleanup == "unload":
        with patch("custom_components.myhome.myhome_device.MyHOMEEntity.async_will_remove_from_hass", new=AsyncMock()):
            await cal.cover.async_will_remove_from_hass()
    elif cleanup == "remove":
        await remove_entry(hass, session.entry_id)
    else:
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()
    session.close()
    assert session.closed and lease.cancelled()
    assert session.store.calibration is None and cal.cover._calibration is None


async def test_review_save_failure_after_lost_socket_remains_recoverable(hass, recovering):
    cal, session = recovering, recovering.session
    await measured(cal)
    values = dict(session.values)
    async def fail(_data):
        disconnect(cal)
        raise OSError("disk")
    with patch.object(session.store.store, "async_save", side_effect=fail):
        with pytest.raises(OSError):
            await act(cal, "save", name="Retry")
    assert session.phase == "review" and session.values == values and session.subscribers == {}
    again = reader(cal, "first-controller", 80)
    assert (await call(hass, cal, again.connection, again.token, "save", name="Retry"))["phase"] == "saved"
    assert session.store.data["revision"] == 1


async def test_accepted_save_completes_after_lost_socket(recovering):
    cal, session = recovering, recovering.session
    await measured(cal)
    original = session.store.store.async_save
    async def save(data):
        disconnect(cal)
        await original(data)
    with patch.object(session.store.store, "async_save", side_effect=save):
        await act(cal, "save", name="Accepted")
    assert session.closed and session.phase == "saved" and session.lease.cancelled()
    assert session.store.data["revision"] == 1 and session.store.calibration is None


async def test_replayed_start_reads_its_session_and_never_takes_it(hass, recovering):
    cal, session = recovering, recovering.session
    await running(cal)
    count, sequence = len(cal.queue), session.sequence
    claimed = reader(cal, "second-tab", claim=True, sequence=session.sequence)
    assert claimed.claimed and session.owner == "second-tab"
    sequence = session.sequence
    # Home Assistant replays the first tab's start after a reconnection.
    assert await begin(hass, cal.connection, cal.request) is session
    assert session.owner == "second-tab" and session.sequence == sequence
    for changes in ({"client_id": "other"}, {"mode": "automatic"}, {"direction": "opening"},
                    {"entity_id": cal.plant.records[1].entity_id}):
        with pytest.raises(ProfileError, match="calibration_busy"):
            await begin(hass, cal.connection, {**cal.request, **changes})
    disconnect(cal)
    assert await begin(hass, cal.connection, cal.request) is session
    assert len(cal.queue) == count and session.phase == "opening"


async def test_batch_review_survives_recovery_and_replay(hass, batch):
    batch.session.close()
    batch.queue.clear()
    batch.request["client_id"] = "batch-controller"
    batch.session = await begin_batch(hass, batch.connection, batch.request)
    # measure() drives only the bus; the owner's tab sends its heartbeat meanwhile.
    with patch("tests.test_cover_calibration_batch.advance", beating(batch_advance)):
        await measure(batch)
    results = copy.deepcopy(batch.session.results)
    disconnect(batch)
    assert await begin_batch(hass, batch.connection, batch.request) is batch.session
    assert batch.session.results == results
    assert len(batch.queue) == 6
    await batch.session.action({"action": "save", "sequence": batch.session.sequence, "names": ["One", "Two"]})
    assert batch.session.store.data["revision"] == 2


async def test_resume_endpoint_reads_live_sessions_and_rejects_missing_stale_and_legacy(hass, recovering):
    cal, session = recovering, recovering.session
    request = {"id": 90, "entry_id": session.entry_id, "session_id": session.id, "client_id": "resume"}
    for changes in ({"entry_id": "missing"}, {"session_id": "stale"}):
        ws_resume(hass, cal.connection, {**request, **changes})
        await hass.async_block_till_done()
        assert cal.connection.send_error.call_args.args[1] == "calibration_expired"
    sequence = session.sequence
    ws_resume(hass, cal.connection, request)
    await hass.async_block_till_done()
    cal.connection.send_result.assert_called_with(90)
    event = cal.connection.send_event.call_args.args
    assert event[0] == 90 and event[1]["read_only"] and not event[1]["owner"]
    assert session.owner == "first-controller" and session.sequence == sequence
    session.client_id = None
    with pytest.raises(ProfileError, match="calibration_expired"):
        session.attach(cal.connection, 90, "legacy")
    session.close()
    ws_resume(hass, cal.connection, request)
    await hass.async_block_till_done()
    assert cal.connection.send_error.call_args.args[1] == "calibration_expired"


def test_resume_requires_admin(hass):
    with pytest.raises(Unauthorized):
        ws_resume(hass, MagicMock(user=SimpleNamespace(is_admin=False)), {"id": 1})


async def test_real_websocket_lost_socket_read_only_resume_and_attachment_authorization(hass, plant, hass_ws_client):
    register_api(hass)
    queue = []
    plant.gateways[0].async_queue_calibration = lambda *args: queue.append(args)
    entry_id = plant.entries[0].entry_id
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        first = await hass_ws_client(hass)
        await first.send_json({"id": 1, "type": WS_START, "entry_id": entry_id,
            "entity_id": plant.records[0].entity_id, "revision": 0, "client_id": "browser-one"})
        assert (await first.receive_json())["success"]
        state = (await first.receive_json())["event"]
        assert state["owner"] and not state["read_only"] and state["attached"]
        await first.close()
        await hass.async_block_till_done()
        session = get_store(hass, entry_id).calibration
        assert session and not session.closed and session.subscribers == {} and session.owner == "browser-one"
        second = await hass_ws_client(hass)
        await second.send_json({"id": 1, "type": WS_RESUME, "entry_id": entry_id,
            "session_id": state["session_id"], "client_id": "browser-two"})
        assert (await second.receive_json())["success"]
        resumed = (await second.receive_json())["event"]
        assert resumed["phase"] == state["phase"] and resumed["attachment"] != state["attachment"]
        assert resumed["read_only"] and not resumed["owner"] and resumed["sequence"] == state["sequence"]
        action = {"type": WS_ACTION, "entry_id": entry_id, "session_id": session.id, "action": "heartbeat"}
        await second.send_json({"id": 2, **action})
        assert (await second.receive_json())["error"]["code"] == "calibration_expired"
        await second.send_json({"id": 3, **action, "attachment": resumed["attachment"]})
        answer = await second.receive_json()
        assert answer["success"] and answer["result"]["owner"] is False
        await second.send_json({"id": 4, **action, "action": "detach", "attachment": resumed["attachment"]})
        answer = await second.receive_json()
        assert answer["id"] == 4 and answer["result"]["attached"] is False
        assert not session.closed and session.owner == "browser-one"
        await second.close()
        assert queue == []
        session.close()


async def test_close_releases_all_ownership_even_if_stop_queue_unexpectedly_fails(recovering):
    cal, session = recovering, recovering.session
    with patch.object(cal.cover._gateway_handler, "async_queue_calibration", side_effect=RuntimeError("broken queue")):
        with pytest.raises(RuntimeError, match="broken queue"):
            session.close()
    assert session.closed and not session.listener and session.lease.cancelled()
    assert session.store.calibration is None and cal.cover._calibration is None


# ---------------------------------------------------------------------------------
# Ownership per tab, presence and lease (maintainer decisions of 30 September).
# ---------------------------------------------------------------------------------
async def test_attach_only_reads_and_ownership_moves_only_on_an_explicit_claim(hass, recovering):
    """Decision 1: another tab reads; a verb is refused until it claims."""
    cal, session = recovering, recovering.session
    other = reader(cal, "second-tab")
    assert not other.claimed and session.owner == "first-controller"
    for action, extra in (("open", {}), ("cancel", {}), ("save", {"name": "Other"}), ("preview_save", {"save_mode": "shared"})):
        assert await call(hass, cal, other.connection, other.token, action, **extra) == "calibration_owned"
    assert cal.queue == [] and session.phase == "confirm_closed"
    # Presence lapsing does not hand the session over either.
    cal.clock[0] += PRESENCE_SECONDS + 1
    assert not session.present()
    assert await call(hass, cal, other.connection, other.token, "open") == "calibration_owned"
    stale = reader(cal, "second-tab", 89, claim=True, sequence=session.sequence - 1)
    assert not stale.claimed and session.owner == "first-controller"
    sequence = session.sequence
    claimant = MagicMock(subscriptions={}, user=SimpleNamespace(is_admin=True))
    ws_resume(hass, claimant, {"id": 90, "entry_id": session.entry_id, "session_id": session.id,
                               "client_id": "second-tab", "claim": True, "sequence": sequence})
    await hass.async_block_till_done()
    assert session.owner == "second-tab" and session.sequence == sequence + 1
    assert session.present()  # A successful claim is an explicit act of the new owner.
    old_owner = cal.connection.send_event.call_args.args[1]
    assert old_owner["read_only"] and not old_owner["owner"]
    new_owner = claimant.send_event.call_args.args[1]
    assert new_owner["owner"] and not new_owner["read_only"]
    assert await call(hass, cal, cal.connection, session.attachment, "open") == "calibration_owned"
    result = await call(hass, cal, claimant, new_owner["attachment"], "open")
    assert result["phase"] == "starting_open" and len(cal.queue) == 1


async def test_replayed_subscription_with_an_old_sequence_never_takes_control(hass, recovering):
    """Decision 1: a reconnection replays `resume {claim}` with the sequence it last read."""
    cal, session = recovering, recovering.session
    first = reader(cal, "second-tab", claim=True, sequence=session.sequence)
    assert first.claimed
    session.emit()
    back = reader(cal, "first-controller", 91, claim=True, sequence=session.sequence - 1)
    assert not back.claimed and session.owner == "second-tab"
    # Nor does the first tab's replayed start.
    assert await begin(hass, cal.connection, cal.request) is session
    assert session.owner == "second-tab"


@pytest.mark.parametrize("checkpoint", ["initial", "starting", "running", "review"])
async def test_attach_claim_and_replay_never_send_a_command(hass, recovering, checkpoint):
    """Decision 2: after attach, claim or reconnection nothing reaches the bus by itself."""
    cal, session = recovering, recovering.session
    if checkpoint == "starting":
        await act(cal, "open")
    elif checkpoint == "running":
        await running(cal)
    elif checkpoint == "review":
        await measured(cal)
    before = len(cal.queue)
    phase = session.phase
    disconnect(cal)
    assert await begin(hass, cal.connection, cal.request) is session
    ws_resume(hass, cal.connection, {"id": 92, "entry_id": session.entry_id, "session_id": session.id,
                                     "client_id": "second-tab"})
    await hass.async_block_till_done()
    ws_resume(hass, cal.connection, {"id": 93, "entry_id": session.entry_id, "session_id": session.id,
                                     "client_id": "second-tab", "claim": True, "sequence": session.sequence})
    await hass.async_block_till_done()
    assert session.owner == "second-tab"
    assert len(cal.queue) == before and session.phase == phase


async def test_stop_is_accepted_from_every_reader_and_nothing_else_is(hass, recovering):
    """Decision 3: Stop is a safety control; its behaviour is still #374's (L2 changes it)."""
    cal, session = recovering, recovering.session
    await running(cal)
    other = reader(cal, "second-tab")
    for action in ("endpoint", "cancel", "save"):
        assert await call(hass, cal, other.connection, other.token, action) == "calibration_owned"
    count = len(cal.queue)
    result = await call(hass, cal, other.connection, other.token, "stop")
    assert result["phase"] == "interrupted" and result["reason"] == "stopped" and result["read_only"]
    assert len(cal.queue) == count + 1 and str(cal.queue[-1][0]) == "*2*0*11##"
    assert session.owner == "first-controller"  # Stop did not take the session.


async def test_stop_during_review_keeps_the_completed_measurements(hass, recovering):
    """Behaviour decision: in review nothing moves; Stop is written, the values stay, Cancel discards them."""
    cal, session = recovering, recovering.session
    await measured(cal)
    assert session.phase == "review" and not session.reservation.pending
    values = dict(session.values)
    other = reader(cal, "second-tab")
    for token, connection in ((other.token, other.connection), (session.attachment, cal.connection)):
        count, sequence = len(cal.queue), session.sequence
        result = await call(hass, cal, connection, token, "stop")
        assert result["phase"] == "review" and result["reason"] is None and result["values"] == values
        assert result["stop_requested"] is True and result["sequence"] == sequence + 1
        assert len(cal.queue) == count + 1 and str(cal.queue[-1][0]) == "*2*0*11##"
    assert session.owner == "first-controller"
    saved = await call(hass, cal, cal.connection, session.attachment, "save", name="Kept after Stop")
    assert saved["phase"] == "saved" and session.store.data["revision"] == 1


async def test_stop_during_review_keeps_the_measurements_before_the_bus_confirms_the_last_stop(hass, recovering):
    cal, session = recovering, recovering.session
    await measured(cal)
    values = dict(session.values)
    session.reservation.dispatched()  # The bus has not reported the motor stopped yet.
    count = len(cal.queue)
    result = await call(hass, cal, cal.connection, session.attachment, "stop")
    assert result["phase"] == "review" and result["reason"] is None and result["values"] == values
    assert len(cal.queue) == count + 1 and str(cal.queue[-1][0]) == "*2*0*11##"


async def test_stop_during_a_run_still_invalidates_it(hass, recovering):
    cal, session = recovering, recovering.session
    await running(cal)
    result = await call(hass, cal, cal.connection, session.attachment, "stop")
    assert result["phase"] == "interrupted" and result["reason"] == "stopped" and result["values"] == {}


async def test_stop_during_batch_review_keeps_every_result_until_cancel(hass, batch):
    await measure(batch)
    session = batch.session
    assert session.phase == "review" and len(session.results) == 2
    count = len(batch.queue)
    await batch_action(batch, "stop")
    assert session.phase == "review" and len(session.results) == 2 and len(batch.queue) == count + 1
    await batch_action(batch, "cancel")
    assert session.phase == "cancelled" and session.results == []


async def test_heartbeat_never_takes_the_session_and_moves_nothing(hass, recovering):
    """Fork test_a_heartbeat_never_takes_the_session_over, and the lease is not renewed."""
    cal, session = recovering, recovering.session
    other = reader(cal, "second-tab")
    cal.clock[0] += PRESENCE_SECONDS + 5
    lease, sequence = session.lease, session.sequence
    for _ in range(3):
        beat = await call(hass, cal, other.connection, other.token, "heartbeat")
        assert beat["owner"] is False and beat["read_only"] is True
    assert session.owner == "first-controller" and not session.present()
    beat = await call(hass, cal, cal.connection, session.attachment, "heartbeat")
    assert beat["owner"] is True and session.present()
    assert session.lease is lease and session.sequence == sequence


async def test_presence_lapsing_during_a_run_stops_nothing_and_the_same_tab_carries_on(hass, recovering):
    """Acceptance: 120 s without heartbeat during a run; no Stop; the same tab records it."""
    cal, session = recovering, recovering.session
    await running(cal)
    count = len(cal.queue)
    disconnect(cal)
    cal.clock[0] += 120
    assert session.phase == "opening" and not session.closed and len(cal.queue) == count
    other = reader(cal, "second-tab")
    assert await call(hass, cal, other.connection, other.token, "endpoint") == "calibration_owned"
    again = reader(cal, "first-controller", 94)
    result = await call(hass, cal, again.connection, again.token, "endpoint")
    assert result["owner"] and result["values"] == {"opening_time": 120}
    assert len(cal.queue) == count + 1  # The Stop that ends every guided run, sent by the verb.


async def test_lease_is_half_an_hour_idle_and_ten_minutes_after_a_movement(hass, recovering):
    """Fork test_the_lease_is_the_dialog_s_watchdog_and_gives_the_shutter_back."""
    cal, session = recovering, recovering.session
    view = session.view()
    assert view["recovery_seconds"] == IDLE_LEASE_SECONDS == 1800
    assert session.lease.when() - hass.loop.time() == pytest.approx(IDLE_LEASE_SECONDS, abs=1)
    assert view["idle_expires_at"] is not None
    await act(cal, "open")
    assert session.view()["recovery_seconds"] == MOVED_LEASE_SECONDS == 600
    assert session.lease.when() - hass.loop.time() == pytest.approx(MOVED_LEASE_SECONDS, abs=1)
    fire(session.lease)
    view = session.view()
    assert session.closed and view["reason"] == "expired" and view["idle_expires_at"] is None
    assert not view["recoverable"] and not view["attached"]


@pytest.mark.parametrize("state", ["untouched", "measured_and_stopped", "moving"])
async def test_lease_expiry_writes_stop_only_while_a_movement_may_run(recovering, state):
    """Fork test_the_lease_stops_a_shutter_that_is_still_running_when_it_runs_out."""
    cal, session = recovering, recovering.session
    if state == "moving":
        await running(cal)
    elif state == "measured_and_stopped":
        await running(cal)
        cal.clock[0] += 22
        await act(cal, "endpoint")
        bus(cal, "*2*0*11##")
        assert not session.reservation.pending
    count = len(cal.queue)
    fire(session.lease)
    assert session.closed and session.reason == "expired"
    assert len(cal.queue) == count + (state == "moving")
    if state == "moving":
        assert str(cal.queue[-1][0]) == "*2*0*11##"
    assert (session.store.calibration is None) is (state != "moving")


async def test_every_transition_moves_the_sequence_and_rearms_the_lease_and_reads_move_neither(hass, recovering):
    """Fork test_every_transition_moves_the_revision_and_restarts_the_lease."""
    cal, session = recovering, recovering.session
    token = session.attachment

    async def movement_feedback():
        assert cal.queue[-1][1]()
        bus(cal, "*2*1*11##")

    for step in (lambda: call(hass, cal, cal.connection, token, "open"), movement_feedback,
                 lambda: call(hass, cal, cal.connection, token, "endpoint")):
        lease, sequence = session.lease, session.sequence
        await step()
        assert session.sequence == sequence + 1 and session.lease is not lease and lease.cancelled()
    lease, sequence = session.lease, session.sequence
    await call(hass, cal, cal.connection, token, "heartbeat")
    other = reader(cal, "second-tab")
    await call(hass, cal, other.connection, other.token, "heartbeat")
    assert await begin(hass, cal.connection, cal.request) is session
    await read_profile(hass, session.entry_id, cal.cover.entity_id)
    assert session.lease is lease and session.sequence == sequence


async def test_leave_from_a_reader_that_is_not_the_owner_does_nothing(hass, recovering):
    """Fork test_leave_from_a_client_that_is_not_the_owner_does_nothing."""
    cal, session = recovering, recovering.session
    other = reader(cal, "second-tab")
    result = await call(hass, cal, other.connection, other.token, "detach")
    assert result["attached"] is False and not session.closed and session.owner == "first-controller"
    cal.clock[0] += PRESENCE_SECONDS + 1
    late = reader(cal, "second-tab", 95)
    await call(hass, cal, late.connection, late.token, "detach")
    assert not session.closed and session.owner == "first-controller" and cal.queue == []


@pytest.mark.parametrize("moved", [False, True])
async def test_owner_leaving_ends_an_untouched_session_and_keeps_a_measured_one(hass, recovering, moved):
    cal, session = recovering, recovering.session
    if moved:
        await running(cal)
        cal.clock[0] += 22
        await act(cal, "endpoint")
    count = len(cal.queue)
    result = await call(hass, cal, cal.connection, session.attachment, "detach")
    assert result["attached"] is False and len(cal.queue) == count
    if not moved:
        assert session.closed and session.reason == "left" and session.store.calibration is None
        return
    assert not session.closed and session.owner == "first-controller" and not session.present()
    assert session.values == {"opening_time": 22}
    again = reader(cal, "first-controller", 96)
    assert (await call(hass, cal, again.connection, again.token, "heartbeat"))["owner"] is True


async def test_owner_leaving_an_interrupted_session_releases_it_without_stop(hass, recovering):
    cal, session = recovering, recovering.session
    await running(cal)
    bus(cal, "*2*2*11##")  # Unexpected movement: the #374 interruption, unchanged.
    assert session.phase == "interrupted"
    count = len(cal.queue)
    await call(hass, cal, cal.connection, session.attachment, "detach")
    assert session.closed and session.reason == "left" and len(cal.queue) == count


async def test_clients_without_client_id_keep_the_original_contract(hass, calibration):
    """Decision 4: no owner keys, heartbeat lease, and `detach` or a lost socket cancels."""
    cal, session = calibration, calibration.session
    assert {"owner", "read_only", "idle_expires_at", "recoverable", "owner_present"}.isdisjoint(session.view())
    assert session.lease.when() - hass.loop.time() == pytest.approx(LEASE_SECONDS, abs=1)
    await act(cal, "open")
    assert cal.queue[-1][1]()
    bus(cal, "*2*1*11##")
    ws_action(hass, cal.connection, {"id": 2, "entry_id": session.entry_id, "session_id": "stale", "action": "detach"})
    await hass.async_block_till_done()
    assert cal.connection.send_error.call_args.args[1] == "calibration_expired" and not session.closed
    ws_action(hass, cal.connection, {"id": 3, "entry_id": session.entry_id, "session_id": session.id, "action": "detach"})
    await hass.async_block_till_done()
    assert session.closed and session.reason == "disconnected"
    assert str(cal.queue[-1][0]) == "*2*0*11##"
    count = len(cal.queue)
    session.detach(session.attachment, "heartbeat_timeout")  # A late lease cannot close it twice.
    assert session.reason == "disconnected" and len(cal.queue) == count


async def test_real_websockets_two_tabs_one_owner_and_a_lost_socket(hass, plant, hass_ws_client):
    """Fork websocket tests 906 and 978: every reader is told who holds the session; a socket closing ends nothing."""
    register_api(hass)
    queue = []
    plant.gateways[0].async_queue_calibration = lambda *args: queue.append(args)
    entry_id = plant.entries[0].entry_id
    start = {"type": WS_START, "entry_id": entry_id, "entity_id": plant.records[0].entity_id,
             "revision": 0, "client_id": "tab-one"}

    async def answer(client, message_id):
        while (message := await client.receive_json())["id"] != message_id or message["type"] != "result":
            pass
        return message

    async def event(client, subscription):
        while (message := await client.receive_json())["id"] != subscription or message["type"] != "event":
            pass
        return message["event"]

    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        one = await hass_ws_client(hass)
        await one.send_json({"id": 1, **start})
        assert (await answer(one, 1))["success"]
        state = await event(one, 1)
        two = await hass_ws_client(hass)
        await two.send_json({"id": 1, "type": WS_RESUME, "entry_id": entry_id, "session_id": state["session_id"],
                             "client_id": "tab-two"})
        assert (await answer(two, 1))["success"]
        seen = await event(two, 1)
        assert seen["read_only"] and seen["sequence"] == state["sequence"]
        act_one = {"type": WS_ACTION, "entry_id": entry_id, "session_id": state["session_id"],
                   "attachment": state["attachment"]}
        await one.send_json({"id": 2, **act_one, "action": "open", "sequence": state["sequence"]})
        assert (await answer(one, 2))["result"]["owner"] is True
        moved = await event(two, 1)
        assert moved["phase"] == "starting_open" and moved["read_only"]
        await two.send_json({"id": 2, "type": WS_RESUME, "entry_id": entry_id, "session_id": state["session_id"],
                             "client_id": "tab-two", "claim": True, "sequence": moved["sequence"]})
        assert (await answer(two, 2))["success"]
        taken = await event(one, 1)
        assert taken["read_only"] and not taken["owner"] and taken["sequence"] == moved["sequence"] + 1
        await one.send_json({"id": 3, **act_one, "action": "cancel"})
        assert (await answer(one, 3))["error"]["code"] == "calibration_owned"
        await one.send_json({"id": 4, **act_one, "action": "stop"})
        assert (await answer(one, 4))["result"]["reason"] == "stopped"
        session = get_store(hass, entry_id).calibration
        count = len(queue)
        await two.close()
        await hass.async_block_till_done()
        assert not session.closed and session.owner == "tab-two" and len(queue) == count
        # Home Assistant replays the first tab's start after its own reconnection.
        three = await hass_ws_client(hass)
        await three.send_json({"id": 1, **start})
        assert (await answer(three, 1))["success"]
        replayed = await event(three, 1)
        assert replayed["read_only"] and replayed["sequence"] == session.sequence and len(queue) == count
        # A replay naming its session reads it while it lives and never creates another one.
        await three.send_json({"id": 2, **start, "session_id": session.id})
        assert (await answer(three, 2))["success"]
        assert (await event(three, 2))["session_id"] == session.id
        await one.close()
        await three.close()
        session.close()
        four = await hass_ws_client(hass)
        await four.send_json({"id": 1, **start, "session_id": session.id})
        assert (await answer(four, 1))["error"]["code"] == "calibration_expired"
        assert get_store(hass, entry_id).calibration is None
        await four.close()


async def test_open_session_reads_for_another_tab_and_gives_a_reloaded_owner_its_view_at_once(
    hass, plant, hass_ws_client
):
    """The panel offers Open session even while the owner is present: `resume` without claim decides by tab."""
    register_api(hass)
    queue = []
    plant.gateways[0].async_queue_calibration = lambda *args: queue.append(args)
    entry_id = plant.entries[0].entry_id

    async def answer(client, message_id):
        while (message := await client.receive_json())["id"] != message_id or message["type"] != "result":
            pass
        return message

    async def event(client, subscription):
        while (message := await client.receive_json())["id"] != subscription or message["type"] != "event":
            pass
        return message["event"]

    def resume(message_id, session_id, client_id):
        return {"id": message_id, "type": WS_RESUME, "entry_id": entry_id, "session_id": session_id, "client_id": client_id}

    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        owner = await hass_ws_client(hass)
        await owner.send_json({"id": 1, "type": WS_START, "entry_id": entry_id, "entity_id": plant.records[0].entity_id,
                               "revision": 0, "client_id": "tab-one"})
        assert (await answer(owner, 1))["success"]
        state = await event(owner, 1)
        await owner.send_json({"id": 2, "type": WS_ACTION, "entry_id": entry_id, "session_id": state["session_id"],
                               "attachment": state["attachment"], "action": "open", "sequence": state["sequence"]})
        assert (await answer(owner, 2))["result"]["owner_present"] is True
        session = get_store(hass, entry_id).calibration
        # A full reload: the socket goes, the same tab opens the session again at once, without a claim.
        await owner.close()
        await hass.async_block_till_done()
        reloaded = await hass_ws_client(hass)
        await reloaded.send_json(resume(1, session.id, "tab-one"))
        assert (await answer(reloaded, 1))["success"]
        back = await event(reloaded, 1)
        assert back["owner"] is True and back["read_only"] is False and back["owner_present"] is True
        assert back["sequence"] == session.sequence and session.owner == "tab-one"
        await reloaded.send_json({"id": 2, "type": WS_ACTION, "entry_id": entry_id, "session_id": session.id,
                                  "attachment": back["attachment"], "action": "heartbeat"})
        assert (await answer(reloaded, 2))["result"]["owner"] is True
        # Another tab opens it while the owner is present: it reads, and Stop is available.
        other = await hass_ws_client(hass)
        await other.send_json(resume(1, session.id, "tab-two"))
        assert (await answer(other, 1))["success"]
        seen = await event(other, 1)
        assert seen["read_only"] is True and seen["owner"] is False and seen["owner_present"] is True
        count = len(queue)
        act_other = {"type": WS_ACTION, "entry_id": entry_id, "session_id": session.id, "attachment": seen["attachment"]}
        await other.send_json({"id": 2, **act_other, "action": "cancel"})
        assert (await answer(other, 2))["error"]["code"] == "calibration_owned"
        await other.send_json({"id": 3, **act_other, "action": "stop"})
        stopped = (await answer(other, 3))["result"]
        assert stopped["reason"] == "stopped" and stopped["read_only"] is True
        assert len(queue) == count + 1 and session.owner == "tab-one"
        await reloaded.close()
        await other.close()
        session.close()


async def test_every_reader_is_told_whether_the_owner_is_present(hass, recovering):
    """`attached` is this reader's own subscription; `owner_present` is the owner's presence."""
    cal, session = recovering, recovering.session
    other = reader(cal, "second-tab")
    beat = await call(hass, cal, other.connection, other.token, "heartbeat")
    assert beat["owner_present"] is True and beat["attached"] is True and beat["read_only"]
    disconnect(cal)
    cal.clock[0] += PRESENCE_SECONDS + 1
    beat = await call(hass, cal, other.connection, other.token, "heartbeat")
    assert beat["owner_present"] is False and beat["attached"] is True
    session.emit()
    assert other.connection.send_event.call_args.args[1]["owner_present"] is False
    view = (await read_profile(hass, session.entry_id, cal.cover.entity_id))["calibration"]
    assert view["owner_present"] is False and view["attached"] is False
    back = reader(cal, "first-controller", 96)
    assert (await call(hass, cal, other.connection, other.token, "heartbeat"))["owner_present"] is False
    await call(hass, cal, back.connection, back.token, "heartbeat")
    assert (await call(hass, cal, other.connection, other.token, "heartbeat"))["owner_present"] is True
    session.close()
    assert session.view()["owner_present"] is False


async def replay_naming_its_session(hass, cal, starter):
    """The panel's replayed start names its session; a deliberate start does not."""
    cal.session.close()
    cal.request["client_id"] = "first-controller"
    session = cal.session = await starter(hass, cal.connection, cal.request)
    replayed = {**cal.request, "session_id": session.id}
    assert await starter(hass, cal.connection, replayed) is session
    with pytest.raises(ProfileError, match="calibration_expired"):
        await starter(hass, cal.connection, {**replayed, "session_id": "another"})
    session.close()
    with pytest.raises(ProfileError, match="calibration_expired"):
        await starter(hass, cal.connection, replayed)
    assert session.store.calibration is None
    fresh = cal.session = await starter(hass, cal.connection, cal.request)
    assert fresh is not session and fresh.owner == "first-controller" and fresh.sequence == 0


async def test_a_replayed_start_naming_an_ended_session_never_creates_a_new_one(hass, recovering):
    await replay_naming_its_session(hass, recovering, begin)


async def test_a_replayed_batch_start_naming_an_ended_session_never_creates_a_new_one(hass, batch):
    await replay_naming_its_session(hass, batch, begin_batch)


async def test_an_attachment_token_acts_only_on_its_own_websocket(hass, recovering):
    cal, session = recovering, recovering.session
    other = reader(cal, "second-tab")
    assert await call(hass, cal, cal.connection, other.token, "heartbeat") == "calibration_expired"
    for action in ("stop", "open", "cancel"):
        assert await call(hass, cal, other.connection, session.attachment, action) == "calibration_expired"
    assert cal.queue == [] and session.phase == "confirm_closed" and not session.closed


async def test_an_accepted_verb_renews_the_lease_even_without_a_transition(hass, recovering):
    cal, session = recovering, recovering.session
    other = reader(cal, "second-tab")
    lease, sequence = session.lease, session.sequence
    with patch.object(session, "action", AsyncMock(return_value=session.view())):
        assert await call(hass, cal, other.connection, other.token, "open") == "calibration_owned"
        assert session.lease is lease
        result = await call(hass, cal, cal.connection, session.attachment, "open")
    assert result["owner"] is True and session.sequence == sequence
    assert session.lease is not lease and lease.cancelled()


async def test_a_claim_from_the_current_owner_changes_nothing(hass, recovering):
    cal, session = recovering, recovering.session
    other = reader(cal, "second-tab")
    sequence = session.sequence
    again = reader(cal, "first-controller", 97, claim=True, sequence=sequence)
    assert not again.claimed and session.owner == "first-controller" and session.sequence == sequence
    other.connection.send_event.assert_not_called()


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
async def test_an_unattended_step_writes_stop_while_a_movement_may_still_run(hass, recovering):
    """At the end of a run nothing is pending; if feedback says otherwise, Stop is written."""
    cal, session = recovering, recovering.session
    await call(hass, cal, cal.connection, session.attachment, "run")
    assert cal.queue[-1][1]()
    bus(cal, "*2*1*11##")
    cal.clock[0] += 5
    bus(cal, "*2*0*11##")
    session.reservation.dispatched()  # A movement the gateway may still be carrying out.
    cal.clock[0] += PRESENCE_SECONDS
    count = len(cal.queue)
    fire(session.settle)
    assert session.reason == "owner_absent" and len(cal.queue) == count + 1
    assert str(cal.queue[-1][0]) == "*2*0*11##"


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
async def test_a_socket_lost_during_the_pause_does_not_stop_a_present_owner(hass, recovering):
    cal, session = recovering, recovering.session
    await call(hass, cal, cal.connection, session.attachment, "run")
    assert cal.queue[-1][1]()
    bus(cal, "*2*1*11##")
    cal.clock[0] += 5
    await call(hass, cal, cal.connection, session.attachment, "heartbeat")
    bus(cal, "*2*0*11##")
    assert session.phase == "settling"
    disconnect(cal)  # Exactly in the pause; the tab comes back within the second.
    reader(cal, "first-controller", 88)
    count = len(cal.queue)
    fire(session.settle)
    assert session.phase == "starting_close" and len(cal.queue) == count + 1


async def test_with_its_owner_present_a_batch_moves_to_the_next_cover(hass, batch):
    batch.session.close()
    batch.queue.clear()
    batch.request["client_id"] = "batch-controller"
    session = batch.session = await begin_batch(hass, batch.connection, batch.request)
    await batch_action(batch, "run")
    for direction, duration in (("open", 5), ("close", 22), ("open", PRESENCE_SECONDS)):
        batch_run(batch, direction, duration)
        heartbeat(session)
        fire(session.settle)
    assert session.cover_index == 1 and session.phase == "starting_open" and len(session.results) == 1
    assert str(batch.queue[-1][0]) == "*2*1*12##"
