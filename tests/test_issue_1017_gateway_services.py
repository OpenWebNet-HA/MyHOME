"""Regression tests for issue #1017 gateway, session, reauth, light, services, and websocket fixes."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from OWNd.exceptions import (
    OWNAuthError,
    OWNConnectionError,
    OWNError,
    OWNNackError,
    OWNProtocolError,
    OWNTimeoutError,
)
from OWNd.message import OWNCommand
from OWNd.profiles import get_gateway_profile
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.myhome.config_flow as cf
import custom_components.myhome.config_flow_discovery as cfd
import custom_components.myhome.gateway as gw_module
from custom_components.myhome.bus_monitor import BusMonitor
from custom_components.myhome.const import ATTR_GATEWAY, CONF_OWN_PASSWORD, DOMAIN
from custom_components.myhome.gateway import (
    COVER_PRE_MOVE_STOP_EMITTED,
    MyHOMEGatewayHandler,
    gateway_emits_cover_pre_move_stop,
)
from custom_components.myhome.gateway_sessions import (
    _POOL_TERMINATED,
    AUTH_REFUSALS,
    CommandWorkerPool,
    _PooledCommandSession,
)
from custom_components.myhome.services import SERVICE_SYNC_TIME, async_setup_services
from custom_components.myhome.websocket import _get_gateway_and_monitor
from tests.mock_gateway_harness import MockGatewayHarness
from tests.test_dimmer_on_at_level_0_2026_11_20 import LEVEL_0, ON_AT_2, _feed, _light


def _make_entry(
    hass: HomeAssistant,
    *,
    mac: str = "00:03:50:10:17:01",
    model: str = "F461",
    port: int = 20000,
    password: str | None = "dummy-pass",
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: port,
            CONF_PASSWORD: password,
            CONF_MAC: mac,
            CONF_NAME: model,
        },
        unique_id=mac,
    )
    entry.add_to_hass(hass)
    return entry


# ── Item 2 (#1012): split-auth runtime reauth via test_connection() + retry ──


async def test_split_auth_reauth_retries_command_session_once_on_connection_closed(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Command-session verification retries once after connection_closed and succeeds."""
    entry = _make_entry(hass, mac="00:03:50:10:17:02")
    fake_runtime = MagicMock()
    fake_runtime.gateway._auth_refused_session = "command"
    entry.runtime_data = fake_runtime

    calls = 0

    class _FlakyCmdSession:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def test_connection(self) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"Success": False, "Message": "connection_closed"}
            return {"Success": True}

        async def close(self) -> None:
            return None

    sleep_mock = AsyncMock()
    monkeypatch.setattr(cf.OWNSession, "test_connection", AsyncMock(return_value={"Success": True}))
    monkeypatch.setattr(cfd, "OWNCommandSession", _FlakyCmdSession)
    monkeypatch.setattr(cf.asyncio, "sleep", sleep_mock)
    monkeypatch.setattr(hass.config_entries, "async_reload", AsyncMock(return_value=True))

    init_res = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert init_res["type"] is FlowResultType.FORM
    res = await hass.config_entries.flow.async_configure(
        init_res["flow_id"], {CONF_OWN_PASSWORD: "new-pass"}
    )
    await hass.async_block_till_done()

    assert calls == 2
    assert [c.args for c in sleep_mock.await_args_list].count((cf.TEST_CONNECTION_RETRY_DELAY,)) == 1
    assert res["type"] is FlowResultType.ABORT
    assert res["reason"] == "reauth_successful"


async def test_split_auth_reauth_classifies_password_retry_and_non_dict_result(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """test_connection() surfaces password_retry (HMAC lockout) and handles non-dict responses."""
    entry = _make_entry(hass, mac="00:03:50:10:17:03")
    fake_runtime = MagicMock()
    fake_runtime.gateway._auth_refused_session = "command"
    entry.runtime_data = fake_runtime

    outcomes: list[Any] = [
        {"Success": False, "Message": "password_retry"},
        None,
    ]

    class _ScriptedCmdSession:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def test_connection(self) -> Any:
            return outcomes.pop(0)

        async def close(self) -> None:
            return None

    monkeypatch.setattr(cf.OWNSession, "test_connection", AsyncMock(return_value={"Success": True}))
    monkeypatch.setattr(cfd, "OWNCommandSession", _ScriptedCmdSession)

    init_res = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    # 1. password_retry stays on the password form with the password_retry error key.
    retry_res = await hass.config_entries.flow.async_configure(
        init_res["flow_id"], {CONF_OWN_PASSWORD: "locked-out"}
    )
    assert retry_res["type"] is FlowResultType.FORM
    assert retry_res["step_id"] == "password"
    assert retry_res["errors"].get(CONF_OWN_PASSWORD) == "password_retry"

    # 2. Non-dict test_connection() return falls back cleanly to cannot_connect abort.
    abort_res = await hass.config_entries.flow.async_configure(
        init_res["flow_id"], {CONF_OWN_PASSWORD: "still-bad"}
    )
    assert abort_res["type"] is FlowResultType.ABORT
    assert abort_res["reason"] == "cannot_connect"


# ── Item 6 (#1012): fast unload shutdown & _PooledCommandSession guards ──


async def test_pooled_command_session_fast_exits_on_terminate_and_auth_refused(
    hass: HomeAssistant,
) -> None:
    """_PooledCommandSession skips TCP/negotiation once terminated or auth-refused."""
    entry = _make_entry(hass, mac="00:03:50:10:17:04")
    handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    pool = handler._command_pool
    session = _PooledCommandSession(pool, gateway=handler.gateway)

    # 1. Pool terminated -> connect() returns _POOL_TERMINATED immediately without opening TCP.
    pool._terminate_sender = True
    with patch("asyncio.open_connection", side_effect=AssertionError("must not open socket")):
        assert await session.connect() == {"Success": False, "Message": _POOL_TERMINATED}

    # 2. Pool auth-refused (without terminate) -> connect() sets last_error and respects raise_on_error.
    pool._terminate_sender = False
    pool._auth_refused = True
    pool._auth_refused_reason = "password_required"
    with patch("asyncio.open_connection", side_effect=AssertionError("must not open socket")):
        res = await session.connect(raise_on_error=False)
        assert res == {"Success": False, "Message": "password_required"}
        assert isinstance(session.last_error, OWNAuthError)
        assert session.last_error.reason == "password_required"

        with pytest.raises(OWNAuthError) as exc_info:
            await session.connect(raise_on_error=True)
        assert exc_info.value.reason == "password_required"

    # 3. _negotiate() under _auth_refused (without _terminate_sender) returns the refused reason directly.
    assert await session._negotiate() == {"Success": False, "Message": "password_required"}


async def test_pool_close_while_waiting_for_connect_lock_exits_fast_without_lockout_log(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Closing the pool while a worker waits on _connect_lock exits immediately via _PoolTerminated."""
    caplog.set_level(logging.ERROR)
    harness = MockGatewayHarness()
    await harness.start()
    try:
        entry = _make_entry(hass, mac="00:03:50:10:17:05", port=harness.port)
        handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
        pool = handler._command_pool
        handler._event_session_ready.set()

        # Hold _connect_lock so the worker opens TCP and blocks inside _PooledCommandSession._negotiate().
        await pool._connect_lock.acquire()
        worker = asyncio.create_task(handler.sending_loop(0))
        try:
            async with asyncio.timeout(3):
                while not harness.connected_clients:
                    await asyncio.sleep(0.01)
            # Let the worker reach `async with self._pool._connect_lock:`.
            await asyncio.sleep(0.05)

            pool.close()
            pool._connect_lock.release()

            # Must finish immediately without OWNd's 5-attempt ~13.3 s retry loop.
            await asyncio.wait_for(worker, timeout=1.5)
        finally:
            if pool._connect_lock.locked():
                pool._connect_lock.release()
            if not worker.done():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

        assert "prevent gateway lockout" not in caplog.text
    finally:
        await harness.stop()


async def test_pool_terminate_during_reconnect_cancels_prewire_without_lockout_error(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Terminating the pool during reconnect connect() cancels pre-wire without _connect_refused error."""
    caplog.set_level(logging.ERROR)
    entry = _make_entry(hass, mac="00:03:50:10:17:06")
    handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    pool = handler._command_pool
    handler._event_session_ready.set()

    attempts = 0

    class _ClosingOnReconnectSession:
        def __init__(self, **_kwargs: Any) -> None:
            self._stream_reader: Any = None
            self._stream_writer: Any = None
            self.last_error: Any = None

        async def connect(self) -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return {"Success": False, "Message": "connection_unavailable"}
            # Pool terminates while reconnect is in flight and connect() returns connection_refused.
            pool._terminate_sender = True
            return {"Success": False, "Message": "connection_refused"}

        async def close(self) -> None:
            return None

    with patch.object(gw_module, "OWNCommandSession", _ClosingOnReconnectSession):
        worker = asyncio.create_task(handler.sending_loop(0))
        written = await handler.send(OWNCommand.parse("*1*1*21##"))
        await asyncio.wait_for(worker, timeout=3.0)

    assert written.cancelled()
    assert getattr(written, "_not_written", False) is True
    assert "prevent gateway lockout" not in caplog.text


async def test_negotiation_error_is_protocol_error_and_does_not_trigger_reauth(
    hass: HomeAssistant,
) -> None:
    """OWNd negotiation_error is an OWNProtocolError, not an auth refusal triggering reauth."""
    assert "negotiation_error" not in AUTH_REFUSALS
    entry = _make_entry(hass, mac="00:03:50:10:17:07")
    handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    handler._on_auth_refused = MagicMock()  # type: ignore[method-assign]
    pool = CommandWorkerPool(handler)
    assert pool._connect_refused({"Success": False, "Message": "negotiation_error"}, 0) is False
    handler._on_auth_refused.assert_not_called()


# ── Item 13 (#1011): pre-wire not_written=True inside send() ──


async def test_send_auth_error_marks_written_future_not_written(hass: HomeAssistant) -> None:
    """OWNAuthError returned inside send() sets _not_written=True before cancelling."""
    entry = _make_entry(hass, mac="00:03:50:10:17:08")
    handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    handler._event_session_ready.set()

    session = MagicMock()
    session.connect = AsyncMock(return_value={"Success": True})
    session._stream_reader = session._stream_writer = object()
    session.last_error = None
    session.close = AsyncMock()

    async def _auth_failed(**_kwargs: Any) -> None:
        session._stream_reader = session._stream_writer = None
        session.last_error = OWNAuthError("auth refused", reason="password_error")

    session.send = AsyncMock(side_effect=_auth_failed)

    with patch.object(gw_module, "OWNCommandSession", MagicMock(return_value=session)):
        worker = asyncio.create_task(handler.sending_loop(0))
        written = await handler.send(OWNCommand.parse("*1*1*21##"))
        await asyncio.wait_for(worker, timeout=3.0)
    await hass.async_block_till_done()

    assert written.cancelled()
    assert getattr(written, "_not_written", False) is True


@pytest.mark.parametrize(
    ("error", "expected_not_written"),
    [
        (OWNConnectionError("unavailable", reason="connection_unavailable"), True),
        (OWNConnectionError("closed", reason="connection_closed"), True),
        (OWNTimeoutError("neg timeout", reason="negotiation_timeout"), True),
        (OWNProtocolError("destructive", reason="destructive_frame"), True),
        (OWNProtocolError("invalid", reason="invalid_frame"), True),
        (OWNNackError("nack", reason="nack"), False),
        (OWNTimeoutError("cmd timeout", reason="command_timeout"), False),
        (OWNConnectionError("lost", reason="connection_lost"), False),
        (OWNProtocolError("unexpected", reason="unexpected_response"), False),
        (OWNError("crash", reason="session_crash"), False),
    ],
)
async def test_send_prewire_vs_postwire_last_error_reasons(
    hass: HomeAssistant,
    error: OWNError,
    expected_not_written: bool,
) -> None:
    """Only pre-wire last_error.reason values set _not_written=True on send() failure."""
    entry = _make_entry(hass, mac="00:03:50:10:17:09")
    handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    handler._event_session_ready.set()

    session = MagicMock()
    session.connect = AsyncMock(return_value={"Success": True})
    session._stream_reader = session._stream_writer = object()
    session.last_error = None
    session.close = AsyncMock()

    async def _fail_send(**_kwargs: Any) -> None:
        session.last_error = error

    session.send = AsyncMock(side_effect=_fail_send)

    with patch.object(gw_module, "OWNCommandSession", MagicMock(return_value=session)):
        worker = asyncio.create_task(handler.sending_loop(0))
        try:
            written = await handler.send(OWNCommand.parse("*1*1*21##"))
            await asyncio.wait({written}, timeout=3.0)
        finally:
            handler._command_pool.close()
            await asyncio.wait_for(worker, timeout=3.0)

    assert written.cancelled()
    assert getattr(written, "_not_written", False) is expected_not_written
    if error.reason == "nack":
        assert getattr(written, "_gateway_nack", False) is True
    if error.reason == "command_timeout":
        assert getattr(written, "_no_answer", False) is True


# ── Item 14 (#962): F455 emits cover pre-move STOP ──


def test_f455_emits_cover_pre_move_stop(hass: HomeAssistant) -> None:
    """Live-verified F455 1.0.76 is included in COVER_PRE_MOVE_STOP_EMITTED."""
    assert "F455" in COVER_PRE_MOVE_STOP_EMITTED
    assert gateway_emits_cover_pre_move_stop("F455", profile=get_gateway_profile("F455")) is True
    assert gateway_emits_cover_pre_move_stop("F452", profile=get_gateway_profile("F452")) is None

    entry = _make_entry(hass, mac="00:03:50:10:17:10", model="F455")
    handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    assert handler.emits_cover_pre_move_stop is True


# ── Item 11 (gateway.py): zone_temperature_poll_needed delegates via _who4_target() ──


def test_zone_temperature_poll_needed_delegates_through_who4_target(hass: HomeAssistant) -> None:
    """zone_temperature_poll_needed follows _who4_target() when WHO=4 is delegated or failed over."""
    entry_f461 = _make_entry(hass, mac="00:03:50:10:17:11", model="F461")
    entry_mh201 = _make_entry(hass, mac="00:03:50:10:17:12", model="MH201")
    gw_f461 = MyHOMEGatewayHandler(hass, entry_f461, generate_events=False, broadcast_resync=False)
    gw_mh201 = MyHOMEGatewayHandler(hass, entry_mh201, generate_events=False, broadcast_resync=False)

    assert gw_f461.zone_temperature_poll_needed is False
    assert gw_mh201.zone_temperature_poll_needed is True

    with patch.object(gw_f461, "_who4_target", return_value=gw_mh201):
        assert gw_f461.zone_temperature_poll_needed is True

    with patch.object(gw_mh201, "_who4_target", return_value=gw_f461):
        assert gw_mh201.zone_temperature_poll_needed is False


# ── Item 7 (#988): sync_time skips WHO 13 DIM 0 on MYHOMESERVER1 / 3488 + follower-only guard ──


@pytest.mark.parametrize(
    ("model_attr", "expected_prefixes"),
    [
        ("MYHOMESERVER1", ("*#13**#22*",)),
        ("MyHomeServer1", ("*#13**#22*",)),
        ("3488", ("*#13**#22*",)),
        ("BTicino 3488", ("*#13**#22*",)),
        ("F461", ("*#13**#22*", "*#13**#0*")),
    ],
)
async def test_sync_time_skips_dim0_on_myhomeserver1_and_3488_only(
    hass: HomeAssistant,
    attach_gateway: Any,
    model_attr: str,
    expected_prefixes: tuple[str, ...],
) -> None:
    """myhome.sync_time sends only DIM 22 on MYHOMESERVER1 and 3488, and DIM 22 + DIM 0 on F461."""
    await async_setup_services(hass)
    handler = SimpleNamespace(
        model=model_attr,
        is_follower=False,
        send=AsyncMock(),
    )
    mac = "00:03:50:10:17:13"
    attach_gateway(mac, handler)

    # Call both with omitted gateway and explicit gateway MAC.
    await hass.services.async_call(DOMAIN, SERVICE_SYNC_TIME, {}, blocking=True)
    frames_default = [str(c.args[0]) for c in handler.send.await_args_list]
    assert len(frames_default) == len(expected_prefixes)
    for frame, prefix in zip(frames_default, expected_prefixes, strict=True):
        assert frame.startswith(prefix)

    handler.send.reset_mock()
    await hass.services.async_call(DOMAIN, SERVICE_SYNC_TIME, {ATTR_GATEWAY: mac}, blocking=True)
    frames_explicit = [str(c.args[0]) for c in handler.send.await_args_list]
    assert len(frames_explicit) == len(expected_prefixes)
    for frame, prefix in zip(frames_explicit, expected_prefixes, strict=True):
        assert frame.startswith(prefix)


async def test_sync_time_without_gateway_raises_when_only_followers_loaded(
    hass: HomeAssistant, attach_gateway: Any
) -> None:
    """Calling sync_time with no gateway raises no_gateway_loaded when only followers are loaded."""
    await async_setup_services(hass)
    follower = SimpleNamespace(model="F461", is_follower=True, send=AsyncMock())
    attach_gateway("00:03:50:10:17:14", follower)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, SERVICE_SYNC_TIME, {}, blocking=True)

    assert err.value.translation_key == "no_gateway_loaded"
    follower.send.assert_not_awaited()


# ── Item 8: websocket _get_gateway_and_monitor prefers primary over follower ──


def test_websocket_get_gateway_and_monitor_prefers_primary_and_falls_back_to_follower(
    hass: HomeAssistant, attach_gateway: Any
) -> None:
    """When mac is omitted, _get_gateway_and_monitor prefers a primary over earlier followers."""
    follower_1 = SimpleNamespace(is_follower=True)
    mon_f1 = BusMonitor(maxlen=5)
    follower_2 = SimpleNamespace(is_follower=True)
    mon_f2 = BusMonitor(maxlen=5)

    attach_gateway("00:03:50:10:17:20", follower_1, mon_f1)
    attach_gateway("00:03:50:10:17:21", follower_2, mon_f2)

    # Follower-only setup: falls back to the first loaded follower.
    assert _get_gateway_and_monitor(hass) == (follower_1, mon_f1)
    assert _get_gateway_and_monitor(hass, "00:03:50:10:17:21") == (follower_2, mon_f2)

    # Add a primary after the followers: omitted mac now selects the primary, while explicit mac keeps selecting followers.
    primary = SimpleNamespace(is_follower=False)
    mon_pri = BusMonitor(maxlen=5)
    attach_gateway("00:03:50:10:17:22", primary, mon_pri)

    assert _get_gateway_and_monitor(hass) == (primary, mon_pri)
    assert _get_gateway_and_monitor(hass, "00:03:50:10:17:20") == (follower_1, mon_f1)


# ── Item 18 (#970): _async_level_zero_check exception guard & eager-done cleanup ──


async def test_level_zero_check_eager_and_post_yield_queue_shutdown_clean_up(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Eager and post-yield QueueShutDown during unload clear pending without unretrieved exceptions."""
    caplog.set_level(logging.DEBUG)
    loop = asyncio.get_running_loop()
    loop_errors: list[dict[str, Any]] = []
    orig_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, ctx: loop_errors.append(ctx))

    try:
        # 1. Eager completion before first suspension (send_status_request raises before any await).
        light_eager, gw_eager = _light(hass, where="11")

        async def _eager_shutdown(_cmd: Any) -> Any:
            raise asyncio.QueueShutDown

        gw_eager.send_status_request = _eager_shutdown
        orig_factory = loop.get_task_factory()
        loop.set_task_factory(asyncio.eager_task_factory)
        try:
            _feed(light_eager, ON_AT_2, LEVEL_0)
        finally:
            loop.set_task_factory(orig_factory)
        await hass.async_block_till_done()

        assert light_eager._level_zero_pending is False
        assert light_eager._level_zero_check_task is None
        assert light_eager.is_on is False
        assert "status check aborted" in caplog.text

        # Subsequent check is NOT blocked once the queue works again.
        recovered = AsyncMock(return_value=None)
        gw_eager.send_status_request = recovered
        _feed(light_eager, ON_AT_2, LEVEL_0)
        await hass.async_block_till_done()
        assert recovered.await_count == 1

        # 2. Post-yield QueueShutDown during unload (suspends once, then raises).
        caplog.clear()
        light_yield, gw_yield = _light(hass, where="12")

        async def _post_yield_shutdown(_cmd: Any) -> Any:
            await asyncio.sleep(0)
            raise asyncio.QueueShutDown

        gw_yield.send_status_request = _post_yield_shutdown
        _feed(light_yield, ON_AT_2, LEVEL_0)
        assert light_yield._level_zero_check_task is not None
        await hass.async_block_till_done()

        assert light_yield._level_zero_pending is False
        assert light_yield._level_zero_check_task is None
        assert light_yield.is_on is False
        assert "status check aborted" in caplog.text
        assert loop_errors == []
    finally:
        loop.set_exception_handler(orig_handler)
