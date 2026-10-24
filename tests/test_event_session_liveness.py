"""End-to-end event-session liveness probe and a non-blocking event read loop (#123, #135, #158, #164)."""
import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.message import OWNMessage

from custom_components.myhome import gateway_sessions
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_sessions import FrameScheduler

PROBE_ECHO = "*#13**0*20*42*07*001##"


def _handler(model: str) -> MyHOMEGatewayHandler:
    entry = MagicMock()
    entry.entry_id = "entry_probe"
    entry.title = f"{model} Gateway"
    entry.data = {
        "host": "192.0.2.92",
        "port": 20000,
        "password": "12345",
        "mac": "00:03:50:00:07:77",
        "name": model,
        "manufacturer": "BTicino",
        "firmware": "2.0.11",
    }
    entry.options = {}
    hass = MagicMock()
    hass.data = {}
    return MyHOMEGatewayHandler(hass, entry)


@pytest.fixture
def fast_probe(monkeypatch):
    monkeypatch.setattr(gateway_sessions, "EVENT_PROBE_IDLE", 0.05)
    monkeypatch.setattr(gateway_sessions, "EVENT_PROBE_TIMEOUT", 0.1)
    monkeypatch.setattr(gateway_sessions, "EVENT_PROBE_REPAIR_AFTER", 2)
    monkeypatch.setattr("custom_components.myhome.gateway.EVENT_RESTART_BACKOFF_MIN", 0.01)
    monkeypatch.setattr("custom_components.myhome.gateway.EVENT_RESTART_BACKOFF_MAX", 0.02)


def _silent_session(frames: asyncio.Queue | None = None) -> MagicMock:
    """A session that connects and then never delivers a frame unless one is queued."""
    session = MagicMock()
    session.connect = AsyncMock(return_value={"Success": True})
    session.close = AsyncMock()
    queue = frames if frames is not None else asyncio.Queue()

    async def get_next():
        return await queue.get()

    session.get_next = AsyncMock(side_effect=get_next)
    return session


def _written_now(*_args, **_kwargs):
    fut = asyncio.get_running_loop().create_future()
    fut.set_result(time.monotonic())
    return fut


async def _run_listener_until(handler, condition, timeout=3.0):
    task = asyncio.create_task(handler.listening_loop())
    try:
        async with asyncio.timeout(timeout):
            while not condition():
                await asyncio.sleep(0.01)
    finally:
        handler._event_runner.close()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_connected_but_silent_session_is_recycled_and_repaired(fast_probe, caplog):
    """No echo of `*#13**0##` on a gateway known to echo it: the session is recreated, then a repair."""
    handler = _handler("F461")
    sessions = []

    def new_session(**_kwargs):
        sessions.append(_silent_session())
        return sessions[-1]

    probe_send = MagicMock(side_effect=_written_now)
    with (
        patch("custom_components.myhome.gateway.OWNEventSession", side_effect=new_session),
        patch.object(handler._command_pool, "send_status_request_nowait", probe_send),
        patch("custom_components.myhome.repairs.async_create_event_session_silent_issue") as create_issue,
        caplog.at_level(logging.WARNING, logger="custom_components.myhome"),
    ):
        await _run_listener_until(handler, lambda: create_issue.called and len(sessions) >= 3)

    assert str(probe_send.call_args_list[0].args[0]) == "*#13**0##"
    assert len(sessions) >= 3
    for session in sessions[:2]:
        session.close.assert_awaited()
    assert any("was not echoed" in r.getMessage() for r in caplog.records)
    assert not any("stalled" in r.getMessage() for r in caplog.records)
    create_issue.assert_called_with(handler.hass, "entry_probe", "F461 Gateway", 2)


async def test_echoed_probe_keeps_the_session(fast_probe):
    """The echo arrives on the event session: no recycle, and the gateway is marked as echoing."""
    handler = _handler("MH202")
    frames: asyncio.Queue = asyncio.Queue()
    sessions = []

    def new_session(**_kwargs):
        sessions.append(_silent_session(frames))
        return sessions[-1]

    def send_and_echo(message):
        frames.put_nowait(OWNMessage.parse(PROBE_ECHO))
        return _written_now()

    probe_send = MagicMock(side_effect=send_and_echo)
    with (
        patch("custom_components.myhome.gateway.OWNEventSession", side_effect=new_session),
        patch.object(handler._command_pool, "send_status_request_nowait", probe_send),
    ):
        await _run_listener_until(handler, lambda: probe_send.call_count >= 3)

    assert len(sessions) == 1
    assert handler._event_runner._probe_echo_seen is True
    assert handler._event_runner._probe_misses == 0


async def test_unrelated_traffic_keeps_the_session_but_does_not_prove_an_echo(fast_probe):
    """A light frame during the probe window proves the event path, not that the gateway echoes `*#13**0##`."""
    handler = _handler("MH202")
    frames: asyncio.Queue = asyncio.Queue()
    sessions = []

    def new_session(**_kwargs):
        sessions.append(_silent_session(frames))
        return sessions[-1]

    def send_and_light(message):
        frames.put_nowait(OWNMessage.parse("*1*1*06##"))
        return _written_now()

    probe_send = MagicMock(side_effect=send_and_light)
    with (
        patch("custom_components.myhome.gateway.OWNEventSession", side_effect=new_session),
        patch.object(handler._command_pool, "send_status_request_nowait", probe_send),
    ):
        await _run_listener_until(handler, lambda: probe_send.call_count >= 2)

    assert len(sessions) == 1
    assert handler._event_runner._probe_echo_seen is False


async def test_gateway_that_never_echoed_is_not_recycled(fast_probe):
    """Without evidence that this gateway echoes the probe, a missing echo proves nothing."""
    handler = _handler("MH202")
    sessions = []

    def new_session(**_kwargs):
        sessions.append(_silent_session())
        return sessions[-1]

    probe_send = MagicMock(side_effect=_written_now)
    with (
        patch("custom_components.myhome.gateway.OWNEventSession", side_effect=new_session),
        patch.object(handler._command_pool, "send_status_request_nowait", probe_send),
    ):
        await _run_listener_until(handler, lambda: probe_send.call_count >= 3)

    assert len(sessions) == 1


async def test_keepalive_acks_do_not_hide_a_dead_bus(fast_probe):
    """`*#*1##` ACKs to the profile keepalive keep TCP alive but are not bus traffic."""
    handler = _handler("F461")
    frames: asyncio.Queue = asyncio.Queue()
    sessions = []

    def new_session(**_kwargs):
        sessions.append(_silent_session(frames))
        return sessions[-1]

    async def ack_pump():
        while True:
            frames.put_nowait(OWNMessage.parse("*#*1##"))
            await asyncio.sleep(0.01)

    pump = asyncio.create_task(ack_pump())
    try:
        with (
            patch("custom_components.myhome.gateway.OWNEventSession", side_effect=new_session),
            patch.object(handler._command_pool, "send_status_request_nowait", MagicMock(side_effect=_written_now)),
            patch("custom_components.myhome.repairs.async_create_event_session_silent_issue"),
        ):
            await _run_listener_until(handler, lambda: len(sessions) >= 2)
    finally:
        pump.cancel()

    assert len(sessions) >= 2


async def test_unsent_probe_is_not_a_miss(fast_probe):
    """A probe the command session could not write says nothing about the event session."""
    handler = _handler("F461")
    sessions = []

    def new_session(**_kwargs):
        sessions.append(_silent_session())
        return sessions[-1]

    def not_written(*_args):
        fut = asyncio.get_running_loop().create_future()
        fut.cancel()
        return fut

    probe_send = MagicMock(side_effect=not_written)
    with (
        patch("custom_components.myhome.gateway.OWNEventSession", side_effect=new_session),
        patch.object(handler._command_pool, "send_status_request_nowait", probe_send),
    ):
        await _run_listener_until(handler, lambda: probe_send.call_count >= 3)

    assert len(sessions) == 1
    assert handler._event_runner._probe_misses == 0


async def test_full_send_queue_does_not_block_the_event_reader(caplog):
    """A setpoint echo (`*#4*1*#14*0210*3##`) polls the zone without waiting for room in a full queue."""
    handler = _handler("F461")
    handler.send_buffer = FrameScheduler(maxsize=1)
    handler.send_buffer.put_nowait({"message": "*1*1*06##", "is_status_request": False, "written": None})

    with caplog.at_level(logging.DEBUG, logger="custom_components.myhome"):
        async with asyncio.timeout(1):
            await handler._process_message(OWNMessage.parse("*#4*1*#14*0210*3##"))

    assert handler.send_buffer.qsize() == 1
    assert any("status poll skipped" in r.getMessage() for r in caplog.records)


async def test_event_triggered_poll_is_queued_when_there_is_room():
    handler = _handler("F461")
    await handler._process_message(OWNMessage.parse("*#4*1*#14*0210*3##"))

    queued = handler.send_buffer.get_nowait()
    assert str(queued["message"]) == "*#4*1##"
    assert queued["is_status_request"] is True


@pytest.mark.parametrize(
    ("model", "flag", "expected"),
    [("MH202", True, True), ("F461", False, False), ("F461", None, True), ("MH202", None, False)],
)
def test_profile_flag_decides_before_the_model_table(model, flag, expected):
    """`echoes_event_session_probe` on the OWNd profile wins; `None` (unverified) falls back to the table."""
    handler = _handler(model)
    profile = MagicMock()
    profile.echoes_event_session_probe = flag
    with patch.object(type(handler), "profile", new_callable=lambda: property(lambda self: profile)):
        assert handler._event_runner._probe_echo_expected() is expected


async def test_probe_waits_while_the_session_is_disconnected(monkeypatch):
    """A lost socket is OWNd's and the stall watchdog's business: no probe goes out."""
    monkeypatch.setattr(gateway_sessions, "EVENT_PROBE_IDLE", 0.01)
    handler = _handler("F461")
    runner = handler._event_runner
    runner.is_connected = False
    runner._last_rx_at = time.monotonic() - 1
    probe_once = AsyncMock()
    with patch.object(runner, "_probe_once", probe_once):
        task = asyncio.create_task(runner._liveness_probe())
        await asyncio.sleep(0.05)
        runner.close()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    probe_once.assert_not_awaited()
    assert runner._last_probe_at > runner._last_rx_at


async def test_probe_failure_is_logged_and_the_probe_keeps_running(monkeypatch, caplog):
    """An exception inside one probe does not end the probe for the life of the session."""
    monkeypatch.setattr(gateway_sessions, "EVENT_PROBE_IDLE", 0.01)
    handler = _handler("F461")
    runner = handler._event_runner
    runner.is_connected = True
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        runner._last_probe_at = time.monotonic()
        if calls == 1:
            raise RuntimeError("boom")
        runner._terminate_listener = True
        return True

    with patch.object(runner, "_probe_once", flaky), caplog.at_level(logging.ERROR, logger="custom_components.myhome"):
        async with asyncio.timeout(2):
            await runner._liveness_probe()

    assert calls == 2
    assert any("liveness probe failed" in r.getMessage() for r in caplog.records)


async def test_echo_after_a_miss_withdraws_the_repair(fast_probe, caplog):
    """The first echo after a miss logs the recovery and deletes the repair issue."""
    handler = _handler("F461")
    frames: asyncio.Queue = asyncio.Queue()
    sessions = []

    def new_session(**_kwargs):
        sessions.append(_silent_session(frames))
        return sessions[-1]

    def send_and_echo(message):
        if len(sessions) >= 2:
            frames.put_nowait(OWNMessage.parse(PROBE_ECHO))
        return _written_now()

    probe_send = MagicMock(side_effect=send_and_echo)
    with (
        patch("custom_components.myhome.gateway.OWNEventSession", side_effect=new_session),
        patch.object(handler._command_pool, "send_status_request_nowait", probe_send),
        patch("custom_components.myhome.repairs.async_create_event_session_silent_issue"),
        patch("custom_components.myhome.repairs.async_delete_event_session_silent_issue") as delete_issue,
        caplog.at_level(logging.INFO, logger="custom_components.myhome"),
    ):
        await _run_listener_until(handler, lambda: delete_issue.called)

    assert len(sessions) == 2
    delete_issue.assert_called_with(handler.hass, "entry_probe")
    assert handler._event_runner._probe_misses == 0
    assert any("answers the liveness probe again" in r.getMessage() for r in caplog.records)


async def test_event_session_silent_repair_is_created_and_deleted(hass):
    """The repair issue carries the gateway name and miss count, and clears on delete."""
    from homeassistant.helpers import issue_registry as ir

    from custom_components.myhome.repairs import (
        async_create_event_session_silent_issue,
        async_delete_event_session_silent_issue,
    )

    async_create_event_session_silent_issue(hass, "entry_probe", "F461 Gateway", 3)
    issue = ir.async_get(hass).async_get_issue("myhome", "event_session_silent_entry_probe")
    assert issue is not None
    assert issue.translation_key == "event_session_silent"
    assert issue.translation_placeholders == {"gateway": "F461 Gateway", "misses": "3"}

    async_delete_event_session_silent_issue(hass, "entry_probe")
    assert ir.async_get(hass).async_get_issue("myhome", "event_session_silent_entry_probe") is None


def test_nowait_status_request_follows_the_delegated_and_standby_routing():
    """The non-blocking path routes like send_status_request: delegate, then standby, then own pool."""
    handler = _handler("F461")
    command = OWNMessage.parse("*#4*1##")
    delegate = MagicMock()
    standby = MagicMock()

    with patch.object(handler, "_delegated_target", return_value=delegate):
        assert handler.send_status_request_nowait(command) is delegate.send_status_request_nowait.return_value
    with (
        patch.object(handler, "_delegated_target", return_value=None),
        patch.object(handler, "_failover_target", return_value=standby),
    ):
        assert handler.send_status_request_nowait(command) is standby.send_status_request_nowait.return_value
    delegate.send_status_request_nowait.assert_called_once_with(command)
    standby.send_status_request_nowait.assert_called_once_with(command)


def test_a_seen_echo_outweighs_a_profile_flag_and_the_table():
    """Once this gateway has echoed the probe, a missing echo is a verdict whatever the profile says."""
    handler = _handler("MH202")
    handler._event_runner._probe_echo_seen = True
    profile = MagicMock()
    profile.echoes_event_session_probe = False
    with patch.object(type(handler), "profile", new_callable=lambda: property(lambda self: profile)):
        assert handler._event_runner._probe_echo_expected() is True


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        ("*#13**0*20*42*07*001##", True),  # parsed: OWNGatewayEvent, dimension 0
        ("*#13**1*06*24*10*2026##", False),  # parsed: OWNGatewayEvent, the date (dimension 1)
        ("*1*1*12##", False),  # a light event
    ],
)
def test_time_echo_is_matched_on_the_parsed_gateway_event(frame, expected):
    """The echo is the WHO 13 dimension 0 reply as OWNd parses it, not a raw-string prefix."""
    message = OWNMessage.parse(frame)
    assert message is not None
    assert gateway_sessions._is_time_echo(message) is expected


def test_time_echo_falls_back_to_the_raw_string():
    """A reply the pinned OWNd hands back unparsed still counts by its `*#13**0*` prefix."""
    assert gateway_sessions._is_time_echo("*#13**0*20*42*07*0530##") is True
    assert gateway_sessions._is_time_echo("*#13**27*70#52#54#49##") is False

