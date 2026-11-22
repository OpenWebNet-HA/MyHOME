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
from OWNd.connection import event_session as ownd_event_session
from OWNd.exceptions import OWNAuthError
from OWNd.message import OWNCommand
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.myhome.gateway as gw_module
from custom_components.myhome.const import CONF_OWN_PASSWORD, DOMAIN
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_sessions import CommandWorkerPool, EventSessionRunner
from tests.mock_gateway_harness import MockGatewayHarness

MAC = "00:03:50:00:00:61"
DUMMY_PASSWORD = "dummy-test-pass"
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
    monkeypatch.setattr(ownd_event_session, "RECONNECT_PAUSE_FATAL", 0.05)
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
    flows = _reauth_flows(hass)
    assert len(flows) == 1
    assert flows[0]["context"]["entry_id"] == entry.entry_id
    assert "refused the OpenWebNet password (password_error) on the event session" in caplog.text
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
    await asyncio.gather(handler.listening_worker, return_exceptions=True)
    await hass.async_block_till_done()

    # Two workers opened their session at the same moment; neither tries again.
    assert 1 <= gateway.proofs <= 2
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

    The password typed into the form is the stored one: the refusal was temporary
    (or the password was changed and changed back). The reload must still happen.
    """
    monkeypatch.setattr(ownd_event_session, "RECONNECT_PAUSE_FATAL", 0.05)
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
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_OWN_PASSWORD: DUMMY_PASSWORD})
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
        patcher.setattr(gw_module, "OWNCommandSession", MagicMock(return_value=session))
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
