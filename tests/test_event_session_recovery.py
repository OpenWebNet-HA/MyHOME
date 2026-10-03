"""The event listener recovers from a dead or stalled event session.

Live on an MH200 (2026-09-24): a general lighting frame `*1*0*0##` made the
listener raise, the event session was closed and nothing ever reopened it, so
every entity stayed unavailable until the entry was reloaded.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.myhome.gateway as gw_module
from custom_components.myhome.const import CONF_BROADCAST_RESYNC, DOMAIN
from custom_components.myhome.gateway import MyHOMEGatewayHandler

MAC = "00:03:50:00:00:01"

Step = Callable[["FakeEventSession"], Awaitable[Any]]

# A session script whose connect() never returns.
HANG_CONNECT: list[Step] = []


class FakeEventSession:
    """Stands in for OWNEventSession; each get_next() runs the next scripted step."""

    def __init__(self, steps: list[Step], *, gateway: Any, logger: Any, on_state_change: Callable[[bool], None]) -> None:
        self._connect_hangs = steps is HANG_CONNECT
        self._steps = iter(steps)
        self.on_state_change = on_state_change
        self._stream_reader: object | None = None
        self._stream_writer: object | None = None
        self.closed = False

    async def connect(self) -> dict[str, Any]:
        if self._connect_hangs:
            await asyncio.Event().wait()
        self._stream_reader = self._stream_writer = object()
        self.on_state_change(True)
        return {"Success": True}

    async def get_next(self) -> Any:
        return await next(self._steps)(self)

    async def close(self) -> None:
        self.closed = True
        self._stream_reader = self._stream_writer = None
        self.on_state_change(False)


def frame(raw: str) -> Step:
    async def step(_session: FakeEventSession) -> Any:
        return OWNMessage.parse(raw)

    return step


async def hang_after_gateway_close(session: FakeEventSession) -> Any:
    """The gateway closes the session; OWNd reports it and get_next() never returns."""
    session._stream_reader = session._stream_writer = None
    session.on_state_change(False)
    await asyncio.Event().wait()


async def quiet_bus(session: FakeEventSession) -> Any:
    """A connected but silent bus: nothing to read for a while, then a frame."""
    await asyncio.sleep(0.2)
    return OWNMessage.parse("*1*1*12##")


def reconnect_failed(delay: float) -> Step:
    """OWNd's get_next() after a failed reconnect cycle: disconnected, returns None."""

    async def step(session: FakeEventSession) -> Any:
        session._stream_reader = session._stream_writer = None
        session.on_state_change(False)
        await asyncio.sleep(delay)
        return None

    return step


async def reconnected(session: FakeEventSession) -> Any:
    """OWNd's get_next() after a reconnect cycle that succeeded."""
    session._stream_reader = session._stream_writer = object()
    session.on_state_change(True)
    return None


def terminate(handler: MyHOMEGatewayHandler) -> Step:
    async def step(_session: FakeEventSession) -> Any:
        handler._terminate_listener = True
        return None

    return step


def boom(_session: FakeEventSession) -> Awaitable[Any]:
    raise RuntimeError("socket in a state OWNd did not expect")


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.40", CONF_PORT: 20000, CONF_PASSWORD: "12345", CONF_MAC: MAC},
        options={CONF_BROADCAST_RESYNC: True},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def handler(hass: HomeAssistant, entry: MockConfigEntry) -> AsyncIterator[MyHOMEGatewayHandler]:
    # A real OWNGateway, not a mock: the live failure was an attribute it lacks.
    handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=True)

    async def no_request(_message: Any) -> None:
        return None

    handler.send_status_request = no_request  # type: ignore[method-assign]
    yield handler
    # Cancels the availability grace timer and resync timers a test left pending.
    await handler.close_listener()


@pytest.fixture(autouse=True)
def fast_timers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gw_module, "EVENT_STALL_TIMEOUT", 0.1)
    monkeypatch.setattr(gw_module, "EVENT_RESTART_BACKOFF_MIN", 0)


def script_sessions(*scripts: list[Step]) -> tuple[Any, list[FakeEventSession]]:
    """Patch OWNEventSession so the n-th session created runs ``scripts[n]``."""
    created: list[FakeEventSession] = []
    remaining = iter(scripts)

    def factory(**kwargs: Any) -> FakeEventSession:
        session = FakeEventSession(next(remaining), **kwargs)
        created.append(session)
        return session

    return patch.object(gw_module, "OWNEventSession", side_effect=factory), created


async def run_listener(handler: MyHOMEGatewayHandler) -> None:
    await asyncio.wait_for(handler.listening_loop(), timeout=5)


async def test_general_lighting_frame_with_real_gateway(hass: HomeAssistant, entry: MockConfigEntry, handler: MyHOMEGatewayHandler):
    """`*1*0*0##` sweeps the known light areas instead of raising AttributeError."""
    er.async_get(hass).async_get_or_create("light", DOMAIN, f"{MAC}-1-12", config_entry=entry)

    await handler._process_message(OWNMessage.parse("*1*0*0##"))

    assert list(handler._resync_timers) == ["1"]


async def test_listener_keeps_session_when_a_frame_fails(hass: HomeAssistant, handler: MyHOMEGatewayHandler, caplog: pytest.LogCaptureFixture):
    """One frame the integration cannot handle is logged; the session stays up."""
    patcher, sessions = script_sessions([frame("*1*0*0##"), frame("*1*1*12##"), terminate(handler)])
    processed: list[str] = []
    real_process = handler._process_message

    async def flaky_process(message: Any) -> None:
        processed.append(str(message))
        if str(message) == "*1*0*0##":
            raise AttributeError("'OWNGateway' object has no attribute 'mac'")
        await real_process(message)

    with patcher, patch.object(handler, "_process_message", side_effect=flaky_process):
        await run_listener(handler)

    assert processed == ["*1*0*0##", "*1*1*12##"]
    assert len(sessions) == 1
    assert "Failed to process `*1*0*0##`" in caplog.text


async def test_listener_recreates_session_after_it_raises(hass: HomeAssistant, handler: MyHOMEGatewayHandler, caplog: pytest.LogCaptureFixture):
    """An exception escaping get_next() recreates the session instead of ending the listener."""
    caplog.set_level(logging.INFO)
    patcher, sessions = script_sessions([boom], [frame("*1*1*12##"), terminate(handler)])

    with patcher:
        await run_listener(handler)

    assert len(sessions) == 2
    assert sessions[0].closed
    assert "Event listener failed" in caplog.text
    assert "Recreating the event session" in caplog.text
    assert handler.bus_monitor.get_recent_frames()[-1]["raw"] == "*1*1*12##"


async def test_listener_recovers_when_get_next_hangs_after_close(hass: HomeAssistant, handler: MyHOMEGatewayHandler, caplog: pytest.LogCaptureFixture):
    """The gateway closes the session and get_next() never returns: the watchdog recreates it."""
    caplog.set_level(logging.INFO)
    patcher, sessions = script_sessions(
        [frame("*1*1*12##"), hang_after_gateway_close],
        [frame("*1*0*12##"), terminate(handler)],
    )
    availability: list[bool] = []
    real_change = handler._on_event_connection_state_change

    def record(connected: bool) -> None:
        real_change(connected)
        availability.append(handler.is_connected)

    with patcher, patch.object(handler, "_on_event_connection_state_change", side_effect=record):
        await run_listener(handler)

    assert len(sessions) == 2
    assert sessions[0].closed
    assert "Event session stalled" in caplog.text
    assert "Recreating the event session" in caplog.text
    # Connected, lost when the gateway closed it, connected again on the new session,
    # released when the listener stops.
    transitions = [c for i, c in enumerate(availability) if i == 0 or c != availability[i - 1]]
    assert transitions == [True, False, True, False]
    assert handler.bus_monitor.get_recent_frames()[-1]["raw"] == "*1*0*12##"


async def test_listener_recovers_when_connect_never_returns(hass: HomeAssistant, handler: MyHOMEGatewayHandler, caplog: pytest.LogCaptureFixture):
    """The watchdog is armed before connect(): a connect that hangs is recreated too."""
    patcher, sessions = script_sessions(HANG_CONNECT, [frame("*1*1*12##"), terminate(handler)])

    with patcher:
        await run_listener(handler)

    assert len(sessions) == 2
    assert sessions[0].closed
    assert "Event session stalled" in caplog.text
    assert handler.bus_monitor.get_recent_frames()[-1]["raw"] == "*1*1*12##"


async def test_general_lighting_frame_through_the_listener(
    hass: HomeAssistant, entry: MockConfigEntry, handler: MyHOMEGatewayHandler, caplog: pytest.LogCaptureFixture
):
    """The live failure end to end: `*1*0*0##` through the real listener, dispatch and gateway object."""
    er.async_get(hass).async_get_or_create("light", DOMAIN, f"{MAC}-1-12", config_entry=entry)
    patcher, sessions = script_sessions([frame("*1*0*0##"), terminate(handler)])

    with patcher:
        await run_listener(handler)

    assert len(sessions) == 1
    assert [f["raw"] for f in handler.bus_monitor.get_recent_frames()] == ["*1*0*0##"]
    assert list(handler._resync_timers) == ["1"]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert "Recreating the event session" not in caplog.text


async def test_quiet_bus_does_not_trip_the_watchdog(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """While connected, a read may block far longer than the stall timeout."""
    patcher, sessions = script_sessions([quiet_bus, terminate(handler)])

    with patcher:
        await run_listener(handler)

    assert len(sessions) == 1


async def test_failed_reconnect_cycles_count_as_progress(hass: HomeAssistant, handler: MyHOMEGatewayHandler, caplog: pytest.LogCaptureFixture):
    """OWNd retrying (get_next() returning None each cycle) is not a stall, and each cycle is logged."""
    caplog.set_level(logging.INFO)
    patcher, sessions = script_sessions([reconnect_failed(0.06)] * 4 + [reconnected, terminate(handler)])

    with patcher:
        await run_listener(handler)

    assert len(sessions) == 1
    assert "Event session stalled" not in caplog.text
    # One INFO line when the outage starts and one when it ends, however many cycles it took.
    info = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and "Event session" in r.getMessage()]
    assert info == [
        "[Generic gateway - 192.168.1.40] Event session lost; gateway not reachable, retrying.",
        "[Generic gateway - 192.168.1.40] Event session reconnected.",
    ]


async def test_refused_session_is_not_recreated(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """A password or negotiation refusal still stops the listener, to avoid a lockout."""
    patcher, sessions = script_sessions([])

    async def refused(self: FakeEventSession) -> dict[str, Any]:
        return {"Success": False, "Message": "password_error"}

    with patcher, patch.object(FakeEventSession, "connect", refused):
        await run_listener(handler)

    assert len(sessions) == 1
    assert handler.is_connected is False


async def test_unload_during_restart_backoff_opens_no_new_session(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """An unload that lands in the back-off between two sessions does not open another one."""
    monkeypatch.setattr(gw_module, "EVENT_RESTART_BACKOFF_MIN", 0.05)
    patcher, sessions = script_sessions([boom], [terminate(handler)])

    with patcher:
        task = asyncio.create_task(handler.listening_loop())
        # The warning is logged right before the back-off sleep starts.
        async with asyncio.timeout(5):
            while "Recreating the event session" not in caplog.text:
                await asyncio.sleep(0.005)
        await handler.close_listener()
        await asyncio.wait_for(task, timeout=5)

    assert len(sessions) == 1
    assert sessions[0].closed


async def test_cancel_during_restart_backoff_still_releases_the_gateway(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """Cancelling the listener in the back-off still marks it disconnected and logs its end."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(gw_module, "EVENT_RESTART_BACKOFF_MIN", 5)
    patcher, sessions = script_sessions([boom])

    with patcher:
        task = asyncio.create_task(handler.listening_loop())
        async with asyncio.timeout(5):
            while "Recreating the event session" not in caplog.text:
                await asyncio.sleep(0.005)
        handler._event_session_ready.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(sessions) == 1
    assert not handler._event_session_ready.is_set()
    assert handler._terminate_listener
    assert "Destroying listening worker" in caplog.text
