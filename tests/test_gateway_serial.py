"""Tests for serial OpenZigBee transport runtime and serial gateway session adapters."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_MAC, CONF_NAME
from OWNd.message import OWNGatewayEvent, OWNLightingEvent, OWNMessage
from OWNd.profiles import Legrand3578Profile
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_BAUDRATE,
    CONF_SERIAL_PORT,
    DEFAULT_BAUDRATE,
    DOMAIN,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_serial import SharedSerialRuntime
from custom_components.myhome.gateway_sessions import (
    _TIER_OTHERS,
    CommandWorkerPool,
    EventSessionRunner,
    FrameScheduler,
    _classify_tier,
    _session_is_open,
)

TEST_EUI64 = "00:03:50:00:01:02:03:04"
TEST_PORT = "/dev/serial/by-id/usb-BTicino_3578-if00"


class _FakeSerialTransport:
    """Minimal AsyncSerialTransport double for SharedSerialRuntime unit tests."""

    def __init__(
        self,
        *,
        port: str,
        baudrate: int,
        log_id: str,
        logger,
        gateway,
        on_state_change,
        raw_frame_callback,
        busy_retry: bool,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.log_id = log_id
        self.gateway = gateway
        self.on_state_change = on_state_change
        self.raw_frame_callback = raw_frame_callback
        self.busy_retry = busy_retry
        self.is_connected = False
        self.last_error = None
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.connect_results: list[bool] = []
        self.send_calls: list[tuple] = []
        self.send_result: object = True
        self._listeners: list = []

    async def connect(self) -> bool:
        self.connect_calls += 1
        ok = self.connect_results.pop(0) if self.connect_results else True
        self.is_connected = ok
        return ok

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.is_connected = False

    def register_listener(self, cb):
        self._listeners.append(cb)

        def _unsub() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return _unsub

    async def send(
        self,
        message,
        *,
        is_status_request: bool = False,
        timeout: float | None = None,
        raise_on_error: bool = False,
    ):
        self.send_calls.append((message, is_status_request, timeout, raise_on_error))
        return self.send_result

    def emit_message(self, msg) -> None:
        for cb in list(self._listeners):
            cb(msg)


def _make_runtime_handler() -> SimpleNamespace:
    bus_monitor = MagicMock()
    bus_monitor.record_frame = MagicMock()
    return SimpleNamespace(
        log_id=f"[MyHOME {TEST_EUI64}]",
        profile=Legrand3578Profile(),
        bus_monitor=bus_monitor,
    )


async def test_shared_serial_runtime_preflight_handoff_and_release() -> None:
    """Preflight keeps the USB port open for SerialEventAdapter without re-probing."""
    handler = _make_runtime_handler()
    with patch(
        "custom_components.myhome.gateway_serial.AsyncSerialTransport",
        side_effect=lambda **kw: _FakeSerialTransport(**kw),
    ):
        runtime = SharedSerialRuntime(handler, TEST_PORT, DEFAULT_BAUDRATE, MagicMock())

    transport = runtime.transport
    assert isinstance(transport, _FakeSerialTransport)
    assert transport.busy_retry is False

    # Preflight test_connection opens port once and sets _preflight_held.
    res1 = await runtime.test_connection()
    assert res1 == {"Success": True, "Message": "connected"}
    assert transport.connect_calls == 1
    assert runtime._preflight_held is True
    assert runtime._refcount == 1

    # Second preflight call while already connected does not re-connect.
    res2 = await runtime.test_connection()
    assert res2 == {"Success": True, "Message": "connected"}
    assert transport.connect_calls == 1

    # SerialEventAdapter adopts the preflight connection without dropping it.
    states: list[bool] = []
    ev_adapter = runtime.create_event_session(on_state_change=states.append)
    ev_res = await ev_adapter.connect()
    assert ev_res == {"Success": True, "Message": "connected"}
    assert transport.connect_calls == 1
    assert transport.disconnect_calls == 0
    assert runtime._preflight_held is False
    assert ev_adapter.is_connected is True
    assert ev_adapter.is_session_open is True

    # Command adapter shares the open transport and increments refcount.
    cmd_adapter = runtime.create_command_session()
    assert await cmd_adapter.connect() == {"Success": True, "Message": "connected"}
    assert runtime._refcount == 2
    assert await cmd_adapter.connect() == {"Success": True, "Message": "connected"}
    assert runtime._refcount == 2
    assert cmd_adapter.is_connected is True
    assert cmd_adapter.is_session_open is True

    # Releasing command adapter leaves transport open while event adapter remains.
    await cmd_adapter.close()
    assert runtime._refcount == 1
    assert transport.disconnect_calls == 0

    # Calling close again when not held is a no-op.
    await cmd_adapter.close()
    assert runtime._refcount == 1

    # Releasing event adapter drops refcount to 0 and disconnects.
    await ev_adapter.close()
    assert runtime._refcount == 0
    assert transport.disconnect_calls == 1
    assert ev_adapter.is_session_open is False

    # Closing again when not held leaves disconnect count unchanged.
    await ev_adapter.close()
    assert transport.disconnect_calls == 1


async def test_shared_serial_runtime_preflight_drop_and_errors() -> None:
    """Dropped preflight reconnects cleanly, and failures surface last_error.reason."""
    handler = _make_runtime_handler()
    with patch(
        "custom_components.myhome.gateway_serial.AsyncSerialTransport",
        side_effect=lambda **kw: _FakeSerialTransport(**kw),
    ):
        runtime = SharedSerialRuntime(handler, TEST_PORT, DEFAULT_BAUDRATE, MagicMock())

    transport = runtime.transport
    transport.connect_results = [False]
    transport.last_error = SimpleNamespace(reason="port_busy")
    fail_res = await runtime.test_connection()
    assert fail_res == {"Success": False, "Message": "port_busy"}

    # Simulate preflight held, then USB disconnects before event adapter connects.
    transport.connect_results = [True, True]
    assert await runtime.test_connection() == {"Success": True, "Message": "connected"}
    transport.is_connected = False
    ev_adapter = runtime.create_event_session()
    assert await ev_adapter.connect() == {"Success": True, "Message": "connected"}
    assert transport.connect_calls == 3

    # Shutdown clears everything and disconnects.
    await runtime.shutdown()
    assert runtime._refcount == 0
    assert runtime._preflight_held is False
    assert transport.disconnect_calls == 1

    # Failed event and command connects return last_error.reason or fallback.
    transport.connect_results = [False, False]
    transport.last_error = None
    assert await ev_adapter.connect() == {"Success": False, "Message": "cannot_connect"}
    cmd_adapter = runtime.create_command_session()
    transport.last_error = SimpleNamespace(reason="device_missing")
    assert await cmd_adapter.connect() == {"Success": False, "Message": "device_missing"}
    assert cmd_adapter.last_error is transport.last_error


async def test_serial_adapters_raw_frames_queue_and_reconnect(monkeypatch) -> None:
    """Raw callback records frames once; get_next dequeues, handles disconnect & reconnect."""
    handler = _make_runtime_handler()
    with patch(
        "custom_components.myhome.gateway_serial.AsyncSerialTransport",
        side_effect=lambda **kw: _FakeSerialTransport(**kw),
    ):
        runtime = SharedSerialRuntime(handler, TEST_PORT, DEFAULT_BAUDRATE, MagicMock())

    transport = runtime.transport
    # 1-arg raw frame callback (valid ZigBee frame) + 2-arg unparseable frame
    runtime._on_raw_frame("*1*1*700123401#9##")
    runtime._on_raw_frame("tx", "*99*invalid##")
    assert len(runtime.unparsed_raw_frames) == 1
    assert runtime.unparsed_raw_frames[0] == "*99*invalid##"
    assert handler.bus_monitor.record_frame.call_count == 2
    assert handler.bus_monitor.record_frame.call_args_list[0].kwargs["direction"] == "rx"
    assert handler.bus_monitor.record_frame.call_args_list[1].kwargs["direction"] == "tx"

    states: list[bool] = []
    ev_adapter = runtime.create_event_session(on_state_change=states.append)
    await ev_adapter.connect()

    msg = OWNMessage.parse("*1*1*700123401#9##", gateway=handler.profile)
    transport.emit_message(msg)
    assert await ev_adapter.get_next() is msg

    # Transport disconnects -> state callback pushes None sentinel.
    transport.is_connected = False
    runtime._fanout_state_change(False)
    assert states == [False]
    assert await ev_adapter.get_next() is None

    # Disconnected with empty queue triggers backoff & acquire when not held.
    monkeypatch.setattr("custom_components.myhome.gateway_serial.asyncio.sleep", AsyncMock())
    ev_adapter._held = False
    assert await ev_adapter.get_next() is None
    assert states == [False, True]
    assert ev_adapter._held is True

    # Closed adapter immediately returns None from get_next.
    await ev_adapter.close()
    assert await ev_adapter.get_next() is None

    # SerialCommandAdapter delegates send arguments verbatim.
    cmd_adapter = runtime.create_command_session()
    transport.send_result = [msg]
    res = await cmd_adapter.send(
        "*#1*700123401#9##", is_status_request=True, timeout=35.0, raise_on_error=True
    )
    assert res == [msg]
    assert transport.send_calls == [("*#1*700123401#9##", True, 35.0, True)]


async def test_session_is_open_and_scheduler_helpers() -> None:
    """_session_is_open checks is_session_open and FrameScheduler detects Tier 0 work."""
    assert _session_is_open(SimpleNamespace(is_session_open=False, is_connected=True)) is False
    assert _session_is_open(SimpleNamespace(is_session_open=True, is_connected=False)) is True

    assert _classify_tier({"tier_override": _TIER_OTHERS, "is_status_request": True}) == _TIER_OTHERS
    assert _classify_tier({"tier_override": _TIER_OTHERS, "is_status_request": False}) == _TIER_OTHERS

    scheduler = FrameScheduler()
    assert scheduler.has_tier0_commands() is False

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    cmd_item = {"message": "*1*1*700123401#9##", "is_status_request": False, "written": fut}
    await scheduler.put(cmd_item)
    assert scheduler.has_tier0_commands() is True

    acquired = await scheduler.acquire_next()
    assert acquired is cmd_item
    assert scheduler.has_tier0_commands() is False
    scheduler.complete_task(acquired)

    # Status request (Tier 2) does not trigger has_tier0_commands.
    status_item = {"message": "*#1*700123401#9##", "is_status_request": True, "written": fut}
    await scheduler.put(status_item)
    assert scheduler.has_tier0_commands() is False


async def test_serial_gateway_handler_and_session_pipeline(hass) -> None:
    """Serial EventSessionRunner & CommandWorkerPool share serial runtime cleanly."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MAC: TEST_EUI64,
            CONF_NAME: "3578",
            CONF_SERIAL_PORT: TEST_PORT,
            CONF_BAUDRATE: 19200,
        },
        unique_id=TEST_EUI64,
    )
    entry.add_to_hass(hass)

    fake_transport_holder: list[_FakeSerialTransport] = []

    def _make_transport(**kw):
        t = _FakeSerialTransport(**kw)
        fake_transport_holder.append(t)
        return t

    with patch(
        "custom_components.myhome.gateway_serial.AsyncSerialTransport",
        side_effect=_make_transport,
    ):
        handler = MyHOMEGatewayHandler(
            hass=hass,
            config_entry=entry,
        )

    assert handler.is_serial_transport is True
    assert handler.is_zigbee_address_family is True
    assert handler.serial_runtime is not None
    assert handler.zigbee_inventory is not None
    assert handler.command_session_idle_timeout is None
    assert isinstance(handler.gateway.profile, Legrand3578Profile)

    # Profile advertising transport_type='serial' also disables idle close; TCP None uses default.
    tcp_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: "00:11:22:33:44:55", "host": "192.168.1.99", "port": 20000},
        unique_id="00:11:22:33:44:55",
    )
    tcp_entry.add_to_hass(hass)
    tcp_handler = MyHOMEGatewayHandler(hass=hass, config_entry=tcp_entry)
    tcp_handler.gateway.profile = SimpleNamespace(transport_type="serial")
    assert tcp_handler.is_serial_transport is True
    assert tcp_handler.command_session_idle_timeout is None
    tcp_handler.gateway.profile = SimpleNamespace(transport_type="tcp", command_session_idle_timeout=None)
    assert tcp_handler.command_session_idle_timeout == 15.0

    # Mock test() goes through serial_runtime.
    assert await handler.test() == {"Success": True, "Message": "connected"}

    # _process_message marks reachable ZigBee nodes live.
    handler.zigbee_inventory._data["products"]["7001234"] = {
        "addr": "7001234",
        "index": 0,
        "reachable": False,
        "last_seen": None,
        "units": {},
    }
    zb_event = OWNMessage.parse("*1*1*700123401#9##", gateway=handler.profile)
    assert isinstance(zb_event, OWNLightingEvent)
    with patch("custom_components.myhome.gateway.async_dispatcher_send"):
        await handler._process_message(zb_event)
    assert handler.zigbee_inventory.is_reachable("7001234") is True

    # EventSessionRunner creates serial event session and skips record_frame & identity probe.
    handler._request_gateway_identity = MagicMock()
    handler._on_event_connection_state_change = MagicMock()
    handler.bus_monitor.record_frame = MagicMock()
    handler._process_message = AsyncMock()
    event_runner = EventSessionRunner(handler)
    fake_ev_session = MagicMock()
    fake_ev_session.connect = AsyncMock(return_value={"Success": True, "Message": "connected"})
    fake_ev_session.is_connected = True
    fake_ev_session.close = AsyncMock()
    calls = 0

    async def _next_msg():
        nonlocal calls
        calls += 1
        if calls == 1:
            return zb_event
        event_runner._terminate_listener = True
        return None

    fake_ev_session.get_next = AsyncMock(side_effect=_next_msg)
    handler.serial_runtime.create_event_session = MagicMock(return_value=fake_ev_session)

    await event_runner._run_event_session()
    handler._request_gateway_identity.assert_not_called()
    handler.bus_monitor.record_frame.assert_not_called()
    handler._process_message.assert_awaited_once_with(zb_event)


async def test_serial_command_worker_dispatch_and_busy_nack(hass) -> None:
    """CommandWorkerPool on serial skips BusMonitor RX/TX, passes timeout, and sets busy_nack."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: TEST_EUI64, CONF_SERIAL_PORT: TEST_PORT, CONF_BAUDRATE: 19200},
        unique_id=TEST_EUI64,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.myhome.gateway_serial.AsyncSerialTransport",
        side_effect=lambda **kw: _FakeSerialTransport(**kw),
    ):
        handler = MyHOMEGatewayHandler(
            hass=hass,
            config_entry=entry,
        )

    handler.bus_monitor.record_frame = MagicMock()
    handler._handle_gateway_diagnostics = MagicMock()
    handler._bridge_to_primary = MagicMock()
    handler.zigbee_inventory._data["products"]["7001234"] = {
        "addr": "7001234",
        "index": 0,
        "reachable": False,
        "last_seen": None,
        "units": {},
    }

    fake_cmd_session = MagicMock()
    fake_cmd_session.connect = AsyncMock(return_value={"Success": True, "Message": "connected"})
    fake_cmd_session.is_session_open = True
    fake_cmd_session.is_connected = True
    fake_cmd_session.close = AsyncMock()
    fake_cmd_session.last_error = None

    dim67 = OWNMessage.parse("*#13**67*3##", gateway=handler.profile)
    zb_light = OWNMessage.parse("*1*1*700123401#9##", gateway=handler.profile)
    assert isinstance(dim67, OWNGatewayEvent)

    send_outcomes: list[object] = [
        [dim67, zb_light, "raw-unparsed"],
        None,
    ]

    async def _fake_send(message, is_status_request=False, **kwargs):
        out = send_outcomes.pop(0)
        if out is None:
            fake_cmd_session.last_error = SimpleNamespace(reason="busy_nack", busy=True)
        return out

    fake_cmd_session.send = AsyncMock(side_effect=_fake_send)
    handler.serial_runtime.create_command_session = MagicMock(return_value=fake_cmd_session)

    ready_event = asyncio.Event()
    ready_event.set()
    pool = CommandWorkerPool(handler, event_session_ready=ready_event)
    handler._command_pool = pool
    # command_session_idle_timeout=None triggers direct send_buffer acquire without wait_for.
    assert pool.command_session_idle_timeout is None

    dispatched: list = []
    with patch(
        "custom_components.myhome.gateway.async_dispatcher_send",
        side_effect=lambda _hass, _sig, msg: dispatched.append(msg),
    ):
        worker_task = asyncio.create_task(pool.sending_loop(0))

        loop = asyncio.get_running_loop()
        fut1 = loop.create_future()
        await handler.send_buffer.put(
            {
                "message": "*#13**67##",
                "is_status_request": True,
                "tier_override": _TIER_OTHERS,
                "timeout": 35.0,
                "written": fut1,
            }
        )
        await fut1
        assert getattr(fut1, "_collected_responses", None) == [dim67, zb_light, "raw-unparsed"]
        assert fake_cmd_session.send.call_args.kwargs["timeout"] == 35.0
        # Serial worker never double-records frames into BusMonitor.
        handler.bus_monitor.record_frame.assert_not_called()
        handler._handle_gateway_diagnostics.assert_called_once_with(dim67)
        assert handler.zigbee_inventory.is_reachable("7001234") is True
        assert dim67 in dispatched and zb_light in dispatched

        # Second command gets busy_nack -> sets _gateway_nack and _gateway_busy, cancels fut2.
        fut2 = loop.create_future()
        await handler.send_buffer.put(
            {
                "message": "*#13**66*0##",
                "is_status_request": True,
                "tier_override": _TIER_OTHERS,
                "written": fut2,
            }
        )
        with pytest.raises(asyncio.CancelledError):
            await fut2
        assert getattr(fut2, "_gateway_nack", False) is True
        assert getattr(fut2, "_gateway_busy", False) is True
        assert "timeout" not in fake_cmd_session.send.call_args.kwargs

        # ZigBee handler bypasses TCP desync backoff and dimension-where claim.
        pool._in_flight_messages.append("*#1*700123401#9*5##")
        assert pool.claim_in_flight_dimension_where(1, 5) is None
        pool._pause_status_after_desync()
        assert pool.send_buffer._status_resume_at == 0.0

        pool.close()
        await worker_task
