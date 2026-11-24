"""A password the gateway refuses while the entry is loaded starts the reauth flow.

Live on a local Home Assistant against a stand-in gateway that refuses the HMAC
proof (2026-11-18): the command worker stopped "to prevent gateway lockout", the
event session presented the same password again at 02:25:45 (twice), 02:26:45,
02:27:45 and 02:28:45, the entry stayed loaded with every entity unavailable,
and no reauth flow appeared. In a second run (03:39-03:41) the gateway took the
password again: the event session logged in, the entities became available,
and `light.turn_on` was "successfully queued" for a worker that no longer
existed, until the entry was reloaded. Setup already answers a refused password
with ``ConfigEntryAuthFailed``; these tests cover the same refusal at runtime.

The stand-in speaks the negotiation of an HMAC gateway as OWNd reads it
(``OWNd/connection/session.py``): greeting ``*#*1##``, ``*98*2##`` after the
session selector, a random string after the client's ``*#*1##``, and ``*#*0##``
to the client's proof. The password is a dummy.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from OWNd.connection import OWNCommandSession, OWNEventSession, event_session
from OWNd.connection import session as own_session
from OWNd.exceptions import OWNAuthError
from OWNd.message import OWNCommand
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.myhome.gateway as gw
from custom_components.myhome.const import CONF_OWN_PASSWORD, CONF_SSDP_LOCATION, DOMAIN
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_sessions import CommandWorkerPool, EventSessionRunner
from tests.mock_gateway_harness import MockGatewayHarness
from tests.test_issue_947_ssdp_firmware_reload_loop import _announce, _Announcement

MAC = "00:03:50:00:00:61"
DUMMY_PASSWORD = "dummy-test-pass"
ROTATED_PASSWORD = "dummy-test-pass-2"
# Server random string of an HMAC-SHA2 handshake: 128 decimal digits.
SERVER_RANDOM = "*#" + "".join(f"{i % 16:02d}" for i in range(64)) + "##"


class RefusingGateway:
    """Negotiation script for ``MockGatewayHarness``: open sessions first, then HMAC with every proof refused."""

    def __init__(self, harness: MockGatewayHarness, *, protect: tuple[str, ...]) -> None:
        self.harness = harness
        # Session selectors that are answered with the HMAC declaration.
        self.protect = protect
        self.proofs = 0
        harness.set_custom_handler(self._reply)

    def _reply(self, msg: str, _writer: asyncio.StreamWriter) -> str | None:
        if msg in ("*99*0##", "*99*1##"):
            return "*98*2##" if msg in self.protect else "*#*1##"
        if msg == "*#*1##":
            return SERVER_RANDOM
        if msg.startswith("*#") and len(msg) > 100:
            self.proofs += 1
            return "*#*0##"
        return "*#*1##"


@pytest.fixture
async def harness() -> AsyncIterator[MockGatewayHarness]:
    harness = MockGatewayHarness()
    await harness.start()
    yield harness
    await harness.stop()


@pytest.fixture
def entry(hass: HomeAssistant, harness: MockGatewayHarness) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: harness.port,
            CONF_PASSWORD: DUMMY_PASSWORD,
            CONF_MAC: MAC,
            CONF_NAME: "F461",
        },
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def handler(
    hass: HomeAssistant, entry: MockConfigEntry, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[MyHOMEGatewayHandler]:
    # OWNd waits RECONNECT_PAUSE_FATAL (60 s) between two refused logins; shortened so a
    # listener that keeps presenting the password shows it within the test.
    monkeypatch.setattr(event_session, "RECONNECT_PAUSE_FATAL", 0.05)
    handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    handler._request_gateway_identity = MagicMock()  # type: ignore[method-assign]
    yield handler
    await handler.close_listener()
    for task in (handler.listening_worker, *handler.sending_workers):
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _until(condition: Any, what: str) -> None:
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.01)
    assert condition(), what


def _reauth_flows(hass: HomeAssistant) -> list[dict[str, Any]]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_event_session_refused_on_reconnect_starts_reauth_and_stops(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    handler: MyHOMEGatewayHandler,
    harness: MockGatewayHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The gateway takes the session, drops it, then refuses the password on the reconnect."""
    caplog.set_level(logging.DEBUG)
    gateway = RefusingGateway(harness, protect=())
    handler.listening_worker = asyncio.create_task(handler.listening_loop())
    await _until(lambda: handler.is_connected and handler.available, "event session up")

    gateway.protect = ("*99*0##", "*99*1##")
    await harness.disconnect_all_clients()

    await asyncio.wait_for(handler.listening_worker, timeout=5)
    await hass.async_block_till_done()
    # Long enough for several RECONNECT_PAUSE_FATAL cycles of a listener that went on.
    await asyncio.sleep(0.4)

    assert gateway.proofs == 1, "the refused password was presented again"
    assert handler.available is False
    assert handler._cancel_listener_handle is None
    flows = _reauth_flows(hass)
    assert len(flows) == 1
    assert flows[0]["context"]["entry_id"] == entry.entry_id
    assert "refused the OpenWebNet password (password_error) on the event session" in caplog.text
    assert "Gateway unavailable (outage exceeded" not in caplog.text
    assert DUMMY_PASSWORD not in caplog.text

    # Nothing is queued for a worker that will never come back.
    written = await handler.send(OWNCommand.parse("*1*1*21##"))
    assert written.cancelled()
    assert handler.send_buffer.qsize() <= len(handler.sending_workers) + 1


async def test_command_session_refused_stops_the_event_listener_too(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    handler: MyHOMEGatewayHandler,
    harness: MockGatewayHarness,
) -> None:
    """Open monitor session, protected command session: the worker's refusal ends both."""
    gateway = RefusingGateway(harness, protect=("*99*0##",))
    handler.listening_worker = asyncio.create_task(handler.listening_loop())
    handler.sending_workers.append(asyncio.create_task(handler.sending_loop(0)))
    handler.sending_workers.append(asyncio.create_task(handler.sending_loop(1)))

    await asyncio.wait_for(asyncio.gather(*handler.sending_workers), timeout=5)
    await asyncio.wait_for(asyncio.gather(handler.listening_worker, return_exceptions=True), timeout=5)
    await hass.async_block_till_done()

    # Handshakes serialize across the worker pool: only the first worker presents the password.
    assert gateway.proofs == 1
    proofs = gateway.proofs
    assert handler.listening_worker.done()
    assert handler.available is False
    assert len(_reauth_flows(hass)) == 1

    # The gateway takes the password again (or only refused it for a while). Nothing
    # logs in behind the user's back, so the entities cannot come back "available"
    # over a queue that no worker reads.
    gateway.protect = ()
    selectors = [m for m in harness.received_messages if m in ("*99*0##", "*99*1##")]
    await harness.disconnect_all_clients()
    await asyncio.sleep(0.3)
    assert gateway.proofs == proofs
    assert [m for m in harness.received_messages if m in ("*99*0##", "*99*1##")] == selectors
    assert handler.available is False
    written = await handler.send(OWNCommand.parse("*1*1*21##"))
    assert written.cancelled()
    assert "*1*1*21##" not in harness.received_messages


async def test_frames_already_queued_are_failed_not_left(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """A frame waiting in the queue when the password is refused is reported as not written."""
    handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    command = await handler.send(OWNCommand.parse("*1*1*21##"))
    status = await handler.send_status_request(OWNCommand.parse("*#1*21##"))
    assert not command.done() and not status.done()

    handler._on_auth_refused("password_error", "command")
    await hass.async_block_till_done()

    assert command.cancelled() and status.cancelled()
    assert (await handler.send(OWNCommand.parse("*1*0*21##"))).cancelled()
    assert (await handler.send_status_request(OWNCommand.parse("*#1*21##"))).cancelled()


async def test_reauth_reloads_the_entry_and_commands_are_written_again(
    hass: HomeAssistant, entry: MockConfigEntry, harness: MockGatewayHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whole path on a set-up entry: refusal, reauth form, reload, new workers, a frame on the wire.

    The password typed into the form is a new one (the password was changed on the
    gateway): the reload must store it and the reloaded runtime gateway must use it.
    """
    monkeypatch.setattr(event_session, "RECONNECT_PAUSE_FATAL", 0.05)
    gateway = RefusingGateway(harness, protect=())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    first = entry.runtime_data.gateway
    await _until(lambda: first.is_connected and first.available, "event session up")

    gateway.protect = ("*99*0##", "*99*1##")
    await harness.disconnect_all_clients()
    await _until(lambda: len(_reauth_flows(hass)) == 1, "reauth flow started")
    await asyncio.gather(*first.sending_workers, return_exceptions=True)
    assert entry.state is ConfigEntryState.LOADED
    assert first.available is False
    assert (await first.send(OWNCommand.parse("*1*1*21##"))).cancelled()
    refused = gateway.proofs
    assert 1 <= refused <= 1 + len(first.sending_workers)

    gateway.protect = ()
    (flow,) = _reauth_flows(hass)
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_OWN_PASSWORD: ROTATED_PASSWORD})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"

    assert entry.state is ConfigEntryState.LOADED
    second = entry.runtime_data.gateway
    assert second is not first
    await _until(lambda: second.is_connected and second.available, "event session up after the reload")
    assert second.sending_workers and not any(worker.done() for worker in second.sending_workers)
    written = await second.send(OWNCommand.parse("*1*1*21##"))
    await asyncio.wait_for(written, timeout=5)
    assert "*1*1*21##" in harness.received_messages
    assert gateway.proofs == refused
    assert _reauth_flows(hass) == []

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    # The reloaded runtime gateway uses the password typed into the form (checked after
    # the unload, so that a failure here does not leave the entry loaded).
    assert entry.data[CONF_PASSWORD] == ROTATED_PASSWORD
    assert second.gateway.password == ROTATED_PASSWORD


async def test_refusal_inside_send_stops_the_worker(
    hass: HomeAssistant, entry: MockConfigEntry, handler: MyHOMEGatewayHandler
) -> None:
    """OWNd reopens a dropped socket inside send(); a refusal there is not followed by a connect()."""
    session = MagicMock()
    session.connect = AsyncMock(return_value={"Success": True})
    session._stream_reader = session._stream_writer = object()
    session.last_error = None

    async def refused_in_send(**_kwargs: Any) -> None:
        session._stream_reader = session._stream_writer = None
        session.last_error = OWNAuthError("Gateway authentication failed (password_error)", reason="password_error")

    session.send = AsyncMock(side_effect=refused_in_send)
    session.close = AsyncMock()
    handler._event_session_ready.set()
    handler._on_event_connection_state_change(True)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(gw, "OWNCommandSession", MagicMock(return_value=session))
        worker = asyncio.create_task(handler.sending_loop(0))
        written = await handler.send(OWNCommand.parse("*1*1*21##"))
        await asyncio.wait_for(worker, timeout=5)
    await hass.async_block_till_done()

    assert written.cancelled()
    assert session.connect.await_count == 1
    assert session.send.await_count == 1
    assert len(_reauth_flows(hass)) == 1


async def test_refusal_is_reported_once_and_not_while_unloading(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Two sessions reporting the same refusal start one flow; an unloading entry starts none."""
    handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    handler._on_auth_refused("password_error", "command")
    handler._on_auth_refused("password_error", "event")
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1

    other = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    other.config_entry = MagicMock()
    await other.close_listener()
    other._on_auth_refused("password_error", "event")
    other.config_entry.async_start_reauth.assert_not_called()


async def test_a_refused_connection_is_not_an_authentication_failure(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """`connection_refused` / `negotiation_refused` still stop the session but ask for no password."""
    handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    handler._on_auth_refused = MagicMock()  # type: ignore[method-assign]

    pool = CommandWorkerPool(handler)
    assert pool._connect_refused({"Success": False, "Message": "negotiation_refused"}, 0)
    session = MagicMock()
    session.connect = AsyncMock(return_value={"Success": False, "Message": "connection_refused"})
    await EventSessionRunner(handler)._read_event_session(session)
    handler._on_auth_refused.assert_not_called()

    assert pool._connect_refused({"Success": False, "Message": "password_required"}, 0)
    handler._on_auth_refused.assert_called_once_with("password_required", "command")


async def test_other_login_stops_even_inside_connect_retry(
    handler: MyHOMEGatewayHandler, harness: MockGatewayHarness
) -> None:
    """An event refusal cancels a command worker already inside its connect() retry loop."""
    script = RefusingGateway(harness, protect=())
    monitor: list[asyncio.StreamWriter] = []

    def reply(msg: str, writer: asyncio.StreamWriter) -> str | None:
        if msg == "*99*1##":
            monitor.append(writer)
        elif msg == "*99*0##" and harness.received_messages.count(msg) == 1:
            # Lost command connection mid-negotiation; connect() is about to retry.
            writer.close()
            return None
        return script._reply(msg, writer)

    harness.set_custom_handler(reply)
    handler.listening_worker = asyncio.create_task(handler.listening_loop())
    worker = asyncio.create_task(handler.sending_loop(0))
    handler.sending_workers.append(worker)
    await _until(lambda: "*99*0##" in harness.received_messages, "command connection attempted")
    script.protect = ("*99*1##", "*99*0##")
    monitor[0].close()
    await _until(lambda: handler._auth_refused, "event password refused")
    assert script.proofs == 1
    at_stop = len(harness.received_messages)
    await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), 3)
    assert "*99*0##" not in harness.received_messages[at_stop:], (
        "command worker presented password again after event refusal"
    )


async def test_dequeued_command_not_written_after_terminal_refusal(
    handler: MyHOMEGatewayHandler, harness: MockGatewayHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command already dequeued while reopening an idle socket is dropped once refusal lands."""
    # The normal idle close, only accelerated to avoid waiting for the real timeout.
    monkeypatch.setattr(type(handler), "command_session_idle_timeout", property(lambda _: 0.05))
    script = RefusingGateway(harness, protect=())
    monitor: list[asyncio.StreamWriter] = []
    reopening: list[asyncio.StreamWriter] = []

    def reply(msg: str, writer: asyncio.StreamWriter) -> str | None:
        if msg == "*99*1##":
            monitor.append(writer)
        if msg == "*99*0##" and harness.received_messages.count(msg) == 2:
            reopening.append(writer)
            return None
        return script._reply(msg, writer)

    harness.set_custom_handler(reply)
    handler.listening_worker = asyncio.create_task(handler.listening_loop())
    worker = asyncio.create_task(handler.sending_loop(0))
    handler.sending_workers.append(worker)
    await _until(
        lambda: "*99*0##" in harness.received_messages and len(harness.connected_clients) == 1,
        "idle command socket closed",
    )
    cmd = "*1*1*21##"
    written = await handler.send(OWNCommand.parse(cmd))
    await _until(lambda: bool(reopening), "worker reopening for the dequeued command")
    script.protect = ("*99*1##",)
    monitor[0].close()
    await _until(lambda: handler._auth_refused, "event login refused")
    # The delayed acceptance of the earlier command session selector arrives last.
    if not reopening[0].is_closing():
        reopening[0].write(b"*#*1##")
    await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), 4)
    assert cmd not in harness.received_messages and written.cancelled(), (
        "command was still transmitted after shutdown"
    )


async def test_blocked_sender_is_failed(
    handler: MyHOMEGatewayHandler, harness: MockGatewayHarness
) -> None:
    """Multiple producers blocked in a full send_buffer all unblock cleanly with zero leaked items."""
    # Size a real scheduler to force backpressure with one queued user command.
    handler.send_buffer._maxsize = 1
    command = await handler.send(OWNCommand.parse("*1*1*21##"))
    waiters = [
        asyncio.create_task(handler.send_status_request(OWNCommand.parse(frame)))
        for frame in ("*#1*21##", "*#1*22##", "*#1*23##")
    ]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert all(not w.done() for w in waiters)
    RefusingGateway(harness, protect=("*99*1##",))
    try:
        await handler.listening_loop()
        results = await asyncio.wait_for(asyncio.gather(*waiters), timeout=2.0)
        assert command.cancelled()
        assert all(fut.cancelled() for fut in results), (
            "producer blocked in put was not failed on refusal"
        )
        assert handler.send_buffer.qsize() == 0
        assert handler.send_buffer._unfinished_tasks == 0
    finally:
        for w in waiters:
            if not w.done():
                w.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)


async def test_event_recycle_does_not_swallow_stop_from_command_worker(
    handler: MyHOMEGatewayHandler, harness: MockGatewayHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command-session refusal still stops the event listener during keepalive teardown."""
    # Use a real keepalive-enabled profile (F454).
    handler.gateway.profile = gw._profile_for_model("F454", "")
    assert handler.gateway.profile.event_keepalive_interval
    command_has_result, event_recycling = asyncio.Event(), asyncio.Event()

    class AlignResult(OWNCommandSession):
        async def connect(self, **kwargs: Any) -> dict[str, Any]:
            result = await super().connect(**kwargs)
            if result and result.get("Message") == "password_error":
                command_has_result.set()
                await event_recycling.wait()  # deliver real result when event teardown is underway
            return result

    class ObserveRecycle(OWNEventSession):
        async def _stop_keepalive(self) -> None:
            if self._keepalive_task is not None:
                event_recycling.set()
            await super()._stop_keepalive()

    monkeypatch.setattr(gw, "OWNCommandSession", AlignResult)
    monkeypatch.setattr(gw, "OWNEventSession", ObserveRecycle)
    script = RefusingGateway(harness, protect=("*99*0##",))
    handler.listening_worker = asyncio.create_task(handler.listening_loop())
    worker = asyncio.create_task(handler.sending_loop(0))
    handler.sending_workers.append(worker)
    await asyncio.wait_for(command_has_result.wait(), 3)
    assert script.proofs == 1
    script.protect = ("*99*1##",)
    await harness.disconnect_all_clients()
    await asyncio.wait_for(
        asyncio.gather(worker, handler.listening_worker, return_exceptions=True), 3
    )
    assert handler._auth_refused
    assert harness.received_messages.count("*99*1##") == 1, (
        "listener submitted a new event selector after command refusal"
    )


async def test_auth_on_second_reconnect_ends_listener_without_retry_pause(
    handler: MyHOMEGatewayHandler, harness: MockGatewayHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A password refusal on a later reconnect exits immediately instead of sleeping 60 s."""
    # Collapse only the transient-failure retry burst; leave the fatal delay longer
    # than the assertion. The parent fixture normally shortens it to 50 ms.
    monkeypatch.setattr(own_session, "MAX_CONNECT_ATTEMPTS", 1)
    monkeypatch.setattr(event_session, "RECONNECT_PAUSE_FATAL", 60)
    script = RefusingGateway(harness, protect=())

    def reply(msg: str, writer: asyncio.StreamWriter) -> str | None:
        if msg == "*99*1##":
            n = harness.received_messages.count(msg)
            if n == 2:  # dropped reconnect: get_next returns with no streams
                writer.close()
                return None
            if n == 3:
                script.protect = (msg,)
        return script._reply(msg, writer)

    harness.set_custom_handler(reply)
    handler.listening_worker = asyncio.create_task(handler.listening_loop())
    await _until(lambda: handler.available, "connected")
    await harness.disconnect_all_clients()
    await _until(lambda: handler._auth_refused, "refusal handled")
    done, _ = await asyncio.wait([handler.listening_worker], timeout=0.2)
    assert done, "listener still sleeping in fatal reconnect delay after its terminal refusal"


async def test_late_refusal_while_platforms_are_unloading(
    hass: HomeAssistant,
    harness: MockGatewayHarness,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An auth refusal arriving while platforms are unloading does not start a reauth flow."""
    script = RefusingGateway(harness, protect=())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    gateway = entry.runtime_data.gateway
    await _until(lambda: gateway.available, "connected")
    real = hass.config_entries.async_unload_platforms
    arrived, release = asyncio.Event(), asyncio.Event()

    async def slow_unload(*args: Any) -> bool:
        arrived.set()
        await release.wait()
        return await real(*args)

    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", slow_unload)
    unload = asyncio.create_task(hass.config_entries.async_unload(entry.entry_id))
    try:
        await asyncio.wait_for(arrived.wait(), 5)
        assert entry.state == ConfigEntryState.UNLOAD_IN_PROGRESS
        script.protect = ("*99*1##",)
        await harness.disconnect_all_clients()
        await _until(lambda: gateway.listening_worker.done(), "refused listener finished")
        await hass.async_block_till_done()
        assert not _reauth_flows(hass), "password prompt for an unloading entry"
    finally:
        release.set()
        await unload


async def test_ssdp_fills_missing_metadata_while_waiting_for_password(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    harness: MockGatewayHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSDP announcements while waiting for reauth update entry data without reloading."""
    ad = _Announcement(
        host=entry.data[CONF_HOST],
        modelName="F454",
        friendlyName="F454",
        serialNumber=entry.unique_id,
    )
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "name": "F454", CONF_SSDP_LOCATION: ad.ssdp_location}
    )
    monkeypatch.setattr(event_session, "RECONNECT_PAUSE_FATAL", 0.01)
    gateway = RefusingGateway(harness, protect=())
    assert await hass.config_entries.async_setup(entry.entry_id)
    try:
        await hass.async_block_till_done()
        first = entry.runtime_data.gateway
        await _until(lambda: first.available, "event session up")
        gateway.protect = ("*99*0##", "*99*1##")
        await harness.disconnect_all_clients()
        await _until(lambda: bool(_reauth_flows(hass)), "prompt")
        await asyncio.gather(first.listening_worker, *first.sending_workers, return_exceptions=True)
        proofs = gateway.proofs
        assert proofs > 0
        result = await _announce(hass, ad)
        assert result["reason"] == "already_configured"
        assert gateway.proofs == proofs, (
            "SSDP used the refused password again without any form submission"
        )
    finally:
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)


async def test_a_refusal_reported_only_by_get_next_ends_the_listener(
    hass: HomeAssistant, entry: MockConfigEntry, handler: MyHOMEGatewayHandler
) -> None:
    """A session that reports the refusal only through ``get_next()`` (no hook call) ends the listener too."""

    class _Session:
        is_connected = True
        last_error: Any = None

        async def connect(self) -> dict[str, Any]:
            return {"Success": True}

        async def get_next(self) -> None:
            self.last_error = OWNAuthError("password refused", reason="password_error")

    await asyncio.wait_for(handler._event_runner._read_event_session(_Session()), timeout=5)  # type: ignore[arg-type]
    await hass.async_block_till_done()

    assert handler._auth_refused is True
    assert handler.available is False
    assert len(_reauth_flows(hass)) == 1


async def test_the_listener_cancel_is_repeated_until_the_task_has_ended(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler
) -> None:
    """A listener that swallows cancellations (OWNd does while it recycles a socket) is cancelled again."""
    swallowed = 0

    async def _stubborn() -> None:
        nonlocal swallowed
        while swallowed < 12:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                swallowed += 1

    listener = asyncio.create_task(_stubborn())
    await asyncio.sleep(0)

    handler._cancel_listener(listener)
    await asyncio.wait_for(listener, timeout=5)

    assert swallowed == 12
    assert handler._cancel_listener_handle is None  # cleared immediately by done callback
    handler._cancel_listener(listener)  # ended: nothing more is scheduled


async def test_late_connected_callback_after_auth_refusal_keeps_gateway_unavailable(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler
) -> None:
    """A late on_state_change(True) arriving after _on_auth_refused must not mark the gateway available."""
    handler._on_event_connection_state_change(True)
    assert handler.available is True
    handler._on_auth_refused("password_error", "command")
    assert handler.available is False

    handler._on_event_connection_state_change(True)
    assert handler.is_connected is False
    assert handler.available is False


async def test_multiple_blocked_producers_all_unblock_on_auth_refusal(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler
) -> None:
    """Two producers blocked in a full send_buffer both cancel cleanly without leaking or deadlocking."""
    from custom_components.myhome.gateway_sessions import FrameScheduler

    pool = handler._command_pool
    pool.send_buffer = FrameScheduler(maxsize=1)
    first = await pool.send(OWNCommand.parse("*1*1*11##"))
    prod_a = asyncio.create_task(pool.send(OWNCommand.parse("*1*1*12##")))
    prod_b = asyncio.create_task(pool.send(OWNCommand.parse("*1*1*13##")))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not prod_a.done() and not prod_b.done()

    pool.stop_after_auth_refusal()
    fut_a, fut_b = await asyncio.wait_for(asyncio.gather(prod_a, prod_b), timeout=2.0)
    assert first.cancelled() and fut_a.cancelled() and fut_b.cancelled()
    assert pool.send_buffer.empty()


async def test_cancel_listener_stops_when_event_session_is_closing(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler
) -> None:
    """Once the event runner enters finally-cleanup, _cancel_listener stops cancelling it."""
    cancels = 0

    async def _closing() -> None:
        nonlocal cancels
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancels += 1
            handler._event_runner._closing_session = True
            await asyncio.sleep(0.05)
            handler._event_runner._closing_session = False

    listener = asyncio.create_task(_closing())
    await asyncio.sleep(0)
    handler._cancel_listener(listener)
    await asyncio.wait_for(listener, timeout=2.0)
    assert cancels == 1


async def test_split_auth_reauth_rejects_wrong_command_session_password(
    hass: HomeAssistant, entry: MockConfigEntry, harness: MockGatewayHarness
) -> None:
    """When command session (*99*0##) triggered reauth while monitor (*99*1##) stays open,
    entering a wrong password in the reauth form must fail instead of reloading into a loop.
    """
    script = RefusingGateway(harness, protect=())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    handler = entry.runtime_data.gateway
    try:
        await _until(
            lambda: handler.available and "*99*0##" in harness.received_messages,
            "event and command sessions up",
        )
        script.protect = ("*99*0##",)
        await harness.disconnect_all_clients()
        await handler.send(OWNCommand.parse("*1*1*21##"))
        await _until(lambda: bool(_reauth_flows(hass)), "reauth flow started")
        assert handler._auth_refused_session == "command"
        (flow,) = _reauth_flows(hass)
        # Wrong password: monitor (*99*1##) still accepts, command (*99*0##) refuses
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {CONF_OWN_PASSWORD: "still_wrong"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "password"
        assert result["errors"].get(CONF_OWN_PASSWORD) == "password_error"
    finally:
        script.protect = ()
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)


async def test_split_auth_reauth_handles_command_session_connect_and_close_exceptions(
    hass: HomeAssistant, entry: MockConfigEntry, harness: MockGatewayHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If verifying the command session raises on connect() and close(), reauth reports cannot_connect."""
    import custom_components.myhome.config_flow_discovery as cfd

    class _ExplodingCommandSession:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def connect(self) -> dict[str, Any]:
            raise OSError("network down")

        async def close(self) -> None:
            raise OSError("close failed")

    script = RefusingGateway(harness, protect=())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    handler = entry.runtime_data.gateway
    try:
        await _until(
            lambda: handler.available and "*99*0##" in harness.received_messages,
            "event and command sessions up",
        )
        script.protect = ("*99*0##",)
        await harness.disconnect_all_clients()
        await handler.send(OWNCommand.parse("*1*1*21##"))
        await _until(lambda: bool(_reauth_flows(hass)), "reauth flow started")
        monkeypatch.setattr(cfd, "OWNCommandSession", _ExplodingCommandSession)
        (flow,) = _reauth_flows(hass)
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {CONF_OWN_PASSWORD: "still_wrong"}
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "cannot_connect"
    finally:
        script.protect = ()
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)


async def test_close_listener_cancels_pending_repeat_cancel_handle(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler
) -> None:
    """close_listener() cancels any scheduled _cancel_listener repeat timer."""
    handle = MagicMock()
    handler._cancel_listener_handle = handle
    await handler.close_listener()
    handle.cancel.assert_called_once()
    assert handler._cancel_listener_handle is None


async def test_enqueue_handles_non_refusal_put_error_and_post_put_refusal_race(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler
) -> None:
    """A non-refusal put() error propagates; a refusal landing while put() yields drains the queue."""
    pool = handler._command_pool

    class _FailingPutBuffer:
        async def put(self, _item: Any) -> None:
            raise RuntimeError("boom")

    orig_buf = pool.send_buffer
    pool.send_buffer = _FailingPutBuffer()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="boom"):
        await pool.send(OWNCommand.parse("*1*1*11##"))

    # Refusal races with put() succeeding: item is drained and written future cancelled.
    pool.send_buffer = orig_buf
    orig_put = orig_buf.put

    async def _racing_put(item: Any) -> None:
        await orig_put(item)
        pool._auth_refused = True

    pool.send_buffer.put = _racing_put  # type: ignore[method-assign]
    fut = await pool.send(OWNCommand.parse("*1*1*12##"))
    assert fut.cancelled()
    assert pool.send_buffer.empty()


async def test_reauth_refreshes_host_after_mid_reauth_ssdp_ip_change(
    hass: HomeAssistant, entry: MockConfigEntry, harness: MockGatewayHarness
) -> None:
    """If SSDP updates CONF_HOST while the reauth dialog is open, test_connection uses the new host."""
    script = RefusingGateway(harness, protect=())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    first = entry.runtime_data.gateway
    try:
        await _until(
            lambda: first.available and "*99*0##" in harness.received_messages,
            "event and command sessions up",
        )
        script.protect = ("*99*0##", "*99*1##")
        await harness.disconnect_all_clients()
        await _until(lambda: bool(_reauth_flows(hass)), "reauth flow started")
        # Simulate stale snapshot on the entry before SSDP updates it to the live harness IP.
        (flow,) = _reauth_flows(hass)
        flow_obj = hass.config_entries.flow._progress[flow["flow_id"]]
        flow_obj.gateway_handler.address = "192.0.2.99"  # stale pre-DHCP IP
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_HOST: "127.0.0.1"})
        script.protect = ()
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {CONF_OWN_PASSWORD: ROTATED_PASSWORD}
        )
        await hass.async_block_till_done()
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.data[CONF_HOST] == "127.0.0.1"
    finally:
        script.protect = ()
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)


async def test_ended_by_auth_refusal_scoped_to_managed_workers_and_class_default(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler
) -> None:
    """_ended_by_auth_refusal does not uncancel external tasks, and __new__ exposes _auth_refused."""
    bare = MyHOMEGatewayHandler.__new__(MyHOMEGatewayHandler)
    assert bare._auth_refused is False
    handler._auth_refused = True
    # Current test task is neither listening_worker nor in sending_workers.
    assert handler._ended_by_auth_refusal() is False


async def test_multi_worker_pool_serializes_handshakes_avoiding_hmac_lockout(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    harness: MockGatewayHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M-12: 4 concurrent workers serialize handshakes on initial connect and in-send() reconnect.

    Presenting a changed password concurrently across 3+ workers would trip the
    gateway's 3-failures-in-60s HMAC lockout before the first refusal returned.
    """
    monkeypatch.setattr(event_session, "RECONNECT_PAUSE_FATAL", 0.05)

    # 1. Initial startup with 4 command workers on split-auth (*99*0## protected): exactly 1 proof.
    script = RefusingGateway(harness, protect=("*99*0##",))
    first = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    first._request_gateway_identity = MagicMock()  # type: ignore[method-assign]
    first.listening_worker = asyncio.create_task(first.listening_loop())
    for wid in range(4):
        first.sending_workers.append(asyncio.create_task(first.sending_loop(wid)))
    try:
        await asyncio.wait_for(asyncio.gather(*first.sending_workers), timeout=5)
        await asyncio.wait_for(asyncio.gather(first.listening_worker, return_exceptions=True), timeout=5)
        await hass.async_block_till_done()
        assert script.proofs == 1
    finally:
        await first.close_listener()

    for flow in _reauth_flows(hass):
        hass.config_entries.flow.async_abort(flow["flow_id"])

    # 2. All 4 workers hold open sockets, peer closes command sockets after password rotation,
    #    and 4 commands fire concurrently (including an unmanaged worker hitting _connect_lock
    #    inside OWNCommandSession.send()): still only 1 proof on the wire.
    script.proofs = 0
    script.protect = ()
    command_writers: list[asyncio.StreamWriter] = []

    def track_command_writers(msg: str, writer: asyncio.StreamWriter) -> str | None:
        if msg == "*99*0##" and writer not in command_writers:
            command_writers.append(writer)
        return script._reply(msg, writer)

    harness.set_custom_handler(track_command_writers)
    second = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    second._request_gateway_identity = MagicMock()  # type: ignore[method-assign]
    second.listening_worker = asyncio.create_task(second.listening_loop())
    for wid in range(3):
        second.sending_workers.append(asyncio.create_task(second.sending_loop(wid)))
    # Unmanaged 4th worker exercises the post-lock _auth_refused short-circuit inside send()
    # without being task-cancelled via sending_workers.
    unmanaged_worker = asyncio.create_task(second.sending_loop(3))
    try:
        await _until(lambda: second.available and len(command_writers) == 4, "4 command sessions open")
        script.protect = ("*99*0##",)
        for writer in list(command_writers):
            writer.close()
        await asyncio.sleep(0.05)

        futures = [
            await second.send(OWNCommand.parse(f"*1*1*{11 + i}##"))
            for i in range(4)
        ]
        await asyncio.wait_for(
            asyncio.gather(*second.sending_workers, unmanaged_worker, return_exceptions=True),
            timeout=5,
        )
        await asyncio.wait_for(
            asyncio.gather(second.listening_worker, return_exceptions=True),
            timeout=5,
        )
        await hass.async_block_till_done()
        assert all(fut.cancelled() for fut in futures)
        assert script.proofs == 1
    finally:
        await second.close_listener()
        if not unmanaged_worker.done():
            unmanaged_worker.cancel()
            await asyncio.gather(unmanaged_worker, return_exceptions=True)

    for flow in _reauth_flows(hass):
        hass.config_entries.flow.async_abort(flow["flow_id"])

    # 3. All 4 workers close their idle command sockets, password rotates, and 4 commands
    #    arrive simultaneously: reopen connect() serializes to 1 proof and cancels all 4 futures.
    monkeypatch.setattr(type(second), "command_session_idle_timeout", property(lambda _: 0.03))
    script.proofs = 0
    script.protect = ()
    harness.set_custom_handler(script._reply)
    third = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    third._request_gateway_identity = MagicMock()  # type: ignore[method-assign]
    third.listening_worker = asyncio.create_task(third.listening_loop())
    for wid in range(4):
        third.sending_workers.append(asyncio.create_task(third.sending_loop(wid)))
    try:
        await _until(
            lambda: third.available and len(harness.connected_clients) == 1,
            "all 4 command sockets closed after idle timeout",
        )
        script.protect = ("*99*0##",)
        idle_futures = [
            await third.send(OWNCommand.parse(f"*1*1*{21 + i}##"))
            for i in range(4)
        ]
        await asyncio.wait_for(
            asyncio.gather(*third.sending_workers, return_exceptions=True),
            timeout=5,
        )
        await asyncio.wait_for(
            asyncio.gather(third.listening_worker, return_exceptions=True),
            timeout=5,
        )
        await hass.async_block_till_done()
        assert all(fut.cancelled() for fut in idle_futures)
        assert script.proofs == 1
    finally:
        await third.close_listener()


async def test_split_auth_reauth_checks_command_session_when_runtime_data_is_none(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    harness: MockGatewayHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Split-auth (*99*1## open, *99*0## protected) still verifies *99*0## when runtime_data is None."""
    import custom_components.myhome.config_flow as cf
    import custom_components.myhome.config_flow_discovery as cfd

    # Case 1: entry was never loaded (entry.runtime_data is None from the start).
    assert getattr(entry, "runtime_data", None) is None
    script = RefusingGateway(harness, protect=("*99*0##",))
    init_res = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert init_res["type"] is FlowResultType.FORM
    assert init_res["step_id"] == "password"

    wrong_res = await hass.config_entries.flow.async_configure(
        init_res["flow_id"], {CONF_OWN_PASSWORD: "wrong_password"}
    )
    assert wrong_res["type"] is FlowResultType.FORM
    assert wrong_res["step_id"] == "password"
    assert wrong_res["errors"].get(CONF_OWN_PASSWORD) == "password_error"

    script.protect = ()
    monkeypatch.setattr(hass.config_entries, "async_reload", AsyncMock(return_value=True))
    ok_res = await hass.config_entries.flow.async_configure(
        init_res["flow_id"], {CONF_OWN_PASSWORD: ROTATED_PASSWORD}
    )
    await hass.async_block_till_done()
    assert ok_res["type"] is FlowResultType.ABORT
    assert ok_res["reason"] == "reauth_successful"

    # Case 2: entry had runtime_data when reauth started, then runtime_data became None before submit.
    fake_runtime = MagicMock()
    fake_runtime.gateway._auth_refused_session = "command"
    entry.runtime_data = fake_runtime
    init_res2 = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    entry.runtime_data = None

    class _RefusingCmdSession:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def connect(self) -> dict[str, Any]:
            return {"Success": False, "Message": "password_error"}

        async def close(self) -> None:
            return None

    monkeypatch.setattr(cf.OWNSession, "test_connection", AsyncMock(return_value={"Success": True}))
    monkeypatch.setattr(cfd, "OWNCommandSession", _RefusingCmdSession)
    wrong_res2 = await hass.config_entries.flow.async_configure(
        init_res2["flow_id"], {CONF_OWN_PASSWORD: "still_wrong"}
    )
    assert wrong_res2["type"] is FlowResultType.FORM
    assert wrong_res2["errors"].get(CONF_OWN_PASSWORD) == "password_error"
    hass.config_entries.flow.async_abort(init_res2["flow_id"])


async def test_auth_refusal_detached_entry_log_and_reported_by_listener_handle(
    hass: HomeAssistant,
    handler: MyHOMEGatewayHandler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Detached config_entry=None is safe, normal outage still logs, and call_soon handle is tracked."""
    caplog.set_level(logging.WARNING)

    # Normal grace-period outage still logs the outage warning.
    handler._on_event_connection_state_change(True)
    assert handler.available is True
    handler.is_connected = False
    handler._mark_unavailable(None)
    assert "Gateway unavailable (outage exceeded" in caplog.text
    caplog.clear()

    # Auth refusal with config_entry=None and reported inside listening_worker tracks _cancel_listener_handle
    # and suppresses the misleading outage warning.
    handler._on_event_connection_state_change(True)
    handler.config_entry = None  # type: ignore[assignment]
    observed_handle: list[asyncio.Handle | None] = []
    hold = asyncio.Event()

    async def _inside_listener() -> None:
        handler._on_auth_refused("password_error", "event")
        observed_handle.append(handler._cancel_listener_handle)
        await hold.wait()

    handler.listening_worker = asyncio.create_task(_inside_listener())
    await _until(lambda: bool(observed_handle), "refusal invoked inside listener")
    assert observed_handle[0] is not None
    # Closing listener before the loop finishes cancels and clears the tracked handle.
    await handler.close_listener()
    assert handler._cancel_listener_handle is None
    assert "Gateway unavailable (outage exceeded" not in caplog.text
    assert not _reauth_flows(hass)
    hold.set()
    await asyncio.gather(handler.listening_worker, return_exceptions=True)
