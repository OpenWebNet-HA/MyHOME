"""Regression and trace-backed tests for Issue #886 (MyHomeServer1 with 41 climate zones).

Covers all six protocol & scheduling root causes identified from the 41-zone MyHomeServer1
3.87.13 trace (`tests/fixtures/traces/issue_886/myhome_trace_MyHomeServer1_41_zones_2026-11-09.json`):
1. Autonomous Dimension 7 (`*#4*Z*7*1*1*TTTT##` / `*#4*Z*7*3##`) + Dimension 0 (`*#4*Z*0*TTTT##`)
   broadcasts clear `_hvac_mode_restored` / `_target_temperature_restored` and set
   `_bus_state_complete = True`, skipping redundant post-discovery `*#4*Z##` polls.
2. Coalesced `ALL_ZONE_REQUESTS` humidity sweep (`*#4*0*60##` on F459, F460, F461, MyHomeServer1)
   and startup ordering: `["*#4*#0*20##", "*#4*0*60##"]` run before any per-zone `*#4*Z##` poll,
   eliminating 24 per-zone `*#4*Z*60##` queries and 60 s starvation of `*#4*#0*20##`, while pacing
   remaining silent zones one at a time.
3. `FrameScheduler` caps concurrent in-flight `_TIER_STATUS` items to 1 (`max_in_flight_status=1`),
   keeping the second `MyHomeServer1` worker free so user setpoint commands (`*#4*59*#14*0105*3##`)
   dispatch immediately without waiting behind slow SCS status reads.
4. `CommandWorkerPool` pauses `_TIER_STATUS` (`STATUS_TIMEOUT_BACKOFF`) after a status
   `command_timeout` while still dispatching `_TIER_COMMANDS` immediately even on 1-worker gateways.
5. Cross-session WHO 4 FIFO reply bleed (`*#4*60##` receiving Zone 34's 5-frame reply burst, or
   `*#4*60*60##` receiving Zone 28's frames) logs a FIFO-desync warning, marks `written` with
   `_gateway_desync=True` so `PollHealth` does not falsely penalize Zone 60, triggers status
   backoff, and still dispatches the collected frames to update the foreign zone.
6. Stray `*#*1##` / `*#*0##` gateway ACK/NACK frames leaked onto the `*99*1##` event session are
   ignored cleanly at DEBUG without logging `Unsupported message type` or dispatching events.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import State
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from OWNd.message import OWNCommand, OWNHeatingEvent, OWNMessage
from OWNd.profiles import get_gateway_profile

from custom_components.myhome.bus_monitor import BusMonitor
from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.const import CONF_FIRMWARE
from custom_components.myhome.gateway import (
    ALL_ZONE_REQUESTS,
    STATUS_TIMEOUT_BACKOFF,
    WHO4_RECOVERY_QUIET_WINDOWS,
    FrameScheduler,
    MyHOMEGatewayHandler,
)
from custom_components.myhome.gateway_events import GatewayEventDispatcher
from custom_components.myhome.gateway_sessions import (
    CommandWorkerPool,
    _mismatched_who4_reply_zones,
    _who4_zone_token,
)

TRACE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "traces"
    / "issue_886"
    / "myhome_trace_MyHomeServer1_41_zones_2026-11-09.json"
)
SYNTHETIC_MAC = "00:03:50:00:08:86"


@pytest.fixture(scope="module")
def issue_886_trace() -> dict[str, Any]:
    """Load the sanitized 41-zone MyHomeServer1 trace from Issue #886."""
    return json.loads(TRACE_PATH.read_text(encoding="utf-8"))


def _make_handler(hass: Any, model: str = "MyHomeServer1") -> MyHOMEGatewayHandler:
    entry = MagicMock()
    entry.entry_id = f"entry-886-{model}"
    entry.data = {
        CONF_HOST: "192.0.2.86",
        CONF_PORT: 20000,
        CONF_PASSWORD: "12345",
        CONF_NAME: model,
        CONF_FIRMWARE: "3.87.13",
        CONF_MAC: SYNTHETIC_MAC,
    }
    entry.runtime_data = None
    handler = MyHOMEGatewayHandler(hass, entry)
    handler.gateway.profile = get_gateway_profile(model)
    return handler


def _make_zone(
    hass: Any,
    where: str,
    gateway: Any,
    *,
    humidity: bool = False,
    fan: bool = False,
) -> MyHOMEClimate:
    zone = MyHOMEClimate(
        hass=hass,
        name=f"Zone {where}",
        device_id=f"4-{where}",
        who="4",
        where=where,
        heating=True,
        cooling=True,
        fan=fan,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    zone.hass = hass
    zone.entity_id = f"climate.myhome_zone_{where}"
    zone.async_write_ha_state = MagicMock()
    if humidity:
        zone._enable_humidity()
    return zone


def _feed(zone: MyHOMEClimate, *raw_frames: str) -> None:
    for raw in raw_frames:
        parsed = OWNMessage.parse(raw)
        assert isinstance(parsed, OWNHeatingEvent), f"Expected OWNHeatingEvent for {raw}"
        zone.handle_event(parsed)


# ── 1. Autonomous Dimension 7 + Dimension 0 bus reports skip startup poll ──────


async def test_dimension_7_clears_restored_flags_and_marks_bus_state_complete(hass: Any) -> None:
    """Autonomous `*#4*Z*7*1*1*TTTT##` + `*#4*Z*0*TTTT##` marks `_bus_state_complete` (#886)."""
    handler = _make_handler(hass, "MyHomeServer1")
    zone = _make_zone(hass, "7", handler)

    # Restore previous HA state so both `_hvac_mode_restored` and `_target_temperature_restored` are True
    await zone.async_restore_last_state(
        State(
            "climate.myhome_zone_7",
            HVACMode.HEAT,
            {"temperature": 19.5, "current_temperature": 18.0},
        )
    )
    assert zone._hvac_mode_restored is True
    assert zone._target_temperature_restored is True
    assert zone._current_temperature_restored is True
    assert zone._bus_state_complete is False

    # Replay the exact autonomous frames for Zone 7 from the Issue #886 trace:
    # `*#4*7*7*1*1*0100##` (Dim 7 heating manual 10.0°C) + `*#4*7*0*0141##` (14.1°C)
    _feed(zone, "*#4*7*7*1*1*0100##", "*#4*7*0*0141##")

    assert zone._hvac_mode_restored is False
    assert zone._target_temperature_restored is False
    assert zone._current_temperature_restored is False
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.target_temperature == 10.0
    assert zone.current_temperature == 14.1
    assert zone._bus_state_complete is True


async def test_dimension_7_off_state_plus_temperature_marks_bus_state_complete(hass: Any) -> None:
    """Autonomous Dimension 7 protection/OFF (`*#4*Z*7*1*2*0070##`) + Dimension 0 satisfies `_core_zone_state_reported`."""
    handler = _make_handler(hass, "MyHomeServer1")
    zone = _make_zone(hass, "8", handler)
    await zone.async_restore_last_state(
        State("climate.myhome_zone_8", HVACMode.HEAT, {"temperature": 20.0})
    )
    assert zone._hvac_mode_restored is True

    _feed(zone, "*#4*8*7*1*2*0070##", "*#4*8*0*0165##")

    assert zone._hvac_mode_restored is False
    assert zone.hvac_mode == HVACMode.OFF
    assert zone._bus_state_complete is True


async def test_humidity_capable_zone_waits_for_live_humidity_before_marking_complete(
    hass: Any,
) -> None:
    """Restored `current_humidity` does not set `_humidity_reported` until a live Dim 60 frame arrives."""
    handler = _make_handler(hass, "MyHomeServer1")
    zone = _make_zone(hass, "60", handler)
    await zone.async_restore_last_state(
        State(
            "climate.myhome_zone_60",
            HVACMode.HEAT,
            {"temperature": 10.0, "current_temperature": 14.6, "current_humidity": 64.0},
        )
    )
    assert zone._humidity is True
    assert zone._humidity_reported is False

    _feed(zone, "*#4*60*7*1*1*0100##", "*#4*60*0*0146##")
    assert zone._core_zone_state_reported() is True
    assert zone._bus_state_complete is False

    _feed(zone, "*#4*60*60*64##")
    assert zone._humidity_reported is True
    assert zone._bus_state_complete is True


# ── 2. Coalesced `*#4*0*60##` + startup ordering across 41 zones ───────────────


@pytest.mark.parametrize("model", ["F459", "F460", "F461", "MyHomeServer1"])
def test_all_zone_humidity_sweep_enabled_for_oracle_verified_gateways(
    hass: Any, model: str
) -> None:
    """F459, F460, F461, and MyHomeServer1 coalesce Dim 60 into `*#4*0*60##`."""
    assert ALL_ZONE_REQUESTS[model.upper()][60] == "*#4*0*60##"
    handler = _make_handler(hass, model)
    assert handler.all_zone_request(60) == "*#4*0*60##"
    if model in ("F461", "MyHomeServer1"):
        assert handler.all_zone_request(20) == "*#4*#0*20##"


def test_humidity_refusing_gateway_skips_zone_dimension_60(hass: Any) -> None:
    """Gateways whose firmware refuses humidity (`F454`, `MH202`) never emit `*#4*0*60##` or `*#4*Z*60##`."""
    handler = _make_handler(hass, "F454")
    assert handler.humidity_request_refused is True
    assert handler.all_zone_request(60) is None


async def test_41_zone_myhomeserver1_startup_coalesces_sweeps_and_paces_only_silent_zones(
    hass: Any, issue_886_trace: dict[str, Any]
) -> None:
    """Replaying the 41-zone / 24-humidity #886 topology replaces 66 burst polls with 2 sweeps + paced silent polls."""
    zone_ids: list[int] = issue_886_trace["plant"]["zone_ids"]
    humidity_zone_ids = set(issue_886_trace["plant"]["humidity_capable_zone_ids"])
    assert len(zone_ids) == 41
    assert len(humidity_zone_ids) == 24

    handler = _make_handler(hass, "MyHomeServer1")
    handler._initial_discovery_done.clear()

    wire_log: list[str] = []
    in_flight_per_zone_polls = 0
    max_concurrent_per_zone_polls = 0

    async def fake_send_paced(frames: list[Any]) -> None:
        for f in frames:
            raw = str(f)
            wire_log.append(raw)
            if raw == "*#4*0*60##":
                # Simulate the 21 zones with physical humidity probes answering during the sweep
                for zid in sorted(humidity_zone_ids)[:21]:
                    _feed(zones[zid], f"*#4*{zid}*60*58##")

    async def fake_send_status_request(message: OWNCommand) -> asyncio.Future[float]:
        nonlocal in_flight_per_zone_polls, max_concurrent_per_zone_polls
        in_flight_per_zone_polls += 1
        max_concurrent_per_zone_polls = max(
            max_concurrent_per_zone_polls, in_flight_per_zone_polls
        )
        wire_log.append(str(message))
        await asyncio.sleep(0)
        in_flight_per_zone_polls -= 1
        fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        fut.set_result(1.0)
        return fut

    handler.send_paced = AsyncMock(side_effect=fake_send_paced)  # type: ignore[method-assign]
    handler.send_status_request = AsyncMock(side_effect=fake_send_status_request)  # type: ignore[method-assign]
    handler._wait_for_bus_quiet = AsyncMock()  # type: ignore[method-assign]

    zones: dict[int, MyHOMEClimate] = {}
    silent_zones = {34, 35, 36}  # 38 zones chatter autonomously before discovery ends; 3 stay silent
    startup_tasks: list[asyncio.Task[None]] = []

    for zid in zone_ids:
        z = _make_zone(hass, str(zid), handler, humidity=(zid in humidity_zone_ids))
        zones[zid] = z
        handler.watch_zone_actuators(str(zid))
        if zid not in silent_zones:
            _feed(z, f"*#4*{zid}*7*1*1*0100##", f"*#4*{zid}*0*0150##")
        startup_tasks.append(asyncio.create_task(z._async_poll_after_discovery()))

    await asyncio.sleep(0)
    # Nothing sent while initial discovery is still running
    assert wire_log == []

    # Complete initial discovery: all-zone sweeps MUST run FIRST, then ONLY the 3 silent zones
    handler._initial_discovery_done.set()
    await asyncio.gather(*startup_tasks)
    if handler._zone_request_task is not None:
        await handler._zone_request_task

    assert wire_log[:2] == ["*#4*#0*20##", "*#4*0*60##"]
    # Zero per-zone `*#4*Z*60##` queries (even for the 3 humidity-restored zones that lacked a physical probe!)
    assert not any(f.endswith("*60##") and f != "*#4*0*60##" for f in wire_log)
    # Only the 3 silent zones needed point-to-point `*#4*Z##` polls (down from 66 WHO 4 startup queries to 5!)
    assert sorted(wire_log[2:]) == ["*#4*34##", "*#4*35##", "*#4*36##"]
    assert max_concurrent_per_zone_polls == 1


# ── 3. Single in-flight `_TIER_STATUS` cap in `FrameScheduler` ─────────────────


async def test_frame_scheduler_caps_in_flight_status_to_one_leaving_second_worker_for_setpoints() -> None:
    """With 2 workers and 40 queued status polls, Worker 1 stays free and sends `*#4*59*#14*0105*3##` immediately."""
    scheduler = FrameScheduler(max_in_flight_status=1)
    for zid in range(30, 70):
        await scheduler.put({"message": OWNCommand(f"*#4*{zid}##"), "is_status_request": True})

    # Worker 0 acquires the first status poll (`*#4*30##`)
    first_status = await scheduler.acquire_next()
    assert str(first_status["message"]) == "*#4*30##"
    assert scheduler.in_flight_status == 1

    # Worker 1 attempts to acquire from the queue while 39 status polls remain queued:
    # it MUST wait rather than taking a second concurrent `_TIER_STATUS` item!
    worker_1_get = asyncio.create_task(scheduler.acquire_next())
    await asyncio.sleep(0)
    assert not worker_1_get.done()

    # User changes Zone 59's setpoint (`*#4*59*#14*0105*3##` in `_TIER_COMMANDS`):
    # Worker 1 wakes up immediately (0 ms wait behind Worker 0's slow status poll)!
    await scheduler.put(
        {"message": OWNCommand("*#4*59*#14*0105*3##"), "is_status_request": False}
    )
    popped_by_worker_1 = await asyncio.wait_for(worker_1_get, timeout=0.5)
    assert str(popped_by_worker_1["message"]) == "*#4*59*#14*0105*3##"
    scheduler.complete_task(popped_by_worker_1)

    # Once Worker 0 completes `*#4*30##`, the next `_TIER_STATUS` item becomes eligible again
    scheduler.complete_task(first_status)
    assert scheduler.in_flight_status == 0
    second_status = await asyncio.wait_for(scheduler.acquire_next(), timeout=0.5)
    assert str(second_status["message"]) == "*#4*31##"
    scheduler.complete_task(second_status)


# ── 4. Status timeout cool-down (`STATUS_TIMEOUT_BACKOFF`) ─────────────────────


async def test_status_timeout_pauses_status_tier_without_blocking_user_commands() -> None:
    """A status `command_timeout` pauses `_TIER_STATUS` while allowing `_TIER_COMMANDS` and sentinels immediately."""
    assert STATUS_TIMEOUT_BACKOFF > 0
    scheduler = FrameScheduler()
    try:
        await scheduler.put({"message": OWNCommand("*#4*63*60##"), "is_status_request": True})
        await scheduler.put({"message": OWNCommand("*#4*64*60##"), "is_status_request": True})

        # Pop and complete `*#4*63*60##`, then simulate timeout cool-down
        first = await scheduler.acquire_next()
        scheduler.complete_task(first)
        scheduler.pause_status(0.05)
        assert scheduler.status_paused is True

        # While paused, a single worker waiting on `acquire_next()` will NOT pop `*#4*64*60##`,
        # YET immediately dispatches a user setpoint command!
        waiter = asyncio.create_task(scheduler.acquire_next())
        await asyncio.sleep(0)
        assert not waiter.done()

        await scheduler.put(
            {"message": OWNCommand("*#4*36*#14*0105*3##"), "is_status_request": False}
        )
        cmd_item = await asyncio.wait_for(waiter, timeout=0.5)
        assert str(cmd_item["message"]) == "*#4*36*#14*0105*3##"
        scheduler.complete_task(cmd_item)

        # After the short backoff expires, `*#4*64*60##` automatically unblocks
        resumed_status = await asyncio.wait_for(scheduler.acquire_next(), timeout=0.5)
        assert str(resumed_status["message"]) == "*#4*64*60##"
        assert scheduler.status_paused is False
        scheduler.complete_task(resumed_status)
    finally:
        scheduler.clear_status_pause()


# ── 5. Cross-session WHO 4 FIFO reply-bleed detection & PollHealth safety ──────


def test_who4_zone_token_and_mismatched_reply_zones_on_all_11_issue_886_bleed_batches(
    issue_886_trace: dict[str, Any],
) -> None:
    """Every real cross-session FIFO bleed batch from #886 is detected; valid and all-zone replies are not."""
    batches = issue_886_trace["fifo_bleed_batches"]
    assert len(batches) == 11
    for batch in batches:
        detected = _mismatched_who4_reply_zones(batch["request"], batch["collected"])
        assert detected == set(batch["foreign_zones"]), f"Mismatch on {batch}"

    # Matching zone replies (including #4#IF local-bus routing and Z#N actuators) return None
    assert _who4_zone_token("34#4#01") == "34"
    assert _who4_zone_token("35#1") == "35"
    assert _who4_zone_token("#0") is None
    assert _who4_zone_token("0") is None
    assert (
        _mismatched_who4_reply_zones(
            "*#4*34##",
            [
                "*#4*34*0*0146##",
                "*#4*34*12*0100*3##",
                "*4*1*34##",
                "*#4*34*14*0100*3##",
                "*#4*34*13*00##",
            ],
        )
        is None
    )
    # All-zone sweeps (`*#4*0*60##`, `*#4*#0*20##`) legitimately return multiple zones and never trigger bleed
    assert (
        _mismatched_who4_reply_zones("*#4*0*60##", ["*#4*42*60*58##", "*#4*60*60*64##"])
        is None
    )
    assert (
        _mismatched_who4_reply_zones("*#4*#0*20##", ["*#4*35#1*20*0##", "*#4*60#1*20*1##"])
        is None
    )


async def test_worker_fifo_bleed_protects_poll_health_dispatches_foreign_zone_and_pauses_status(
    hass: Any, caplog: pytest.LogCaptureFixture, issue_886_trace: dict[str, Any]
) -> None:
    """When `*#4*60##` collects Zone 34's 5 frames (#886), Zone 60 PollHealth is unharmed and Zone 34 updates."""
    handler = _make_handler(hass, "MyHomeServer1")
    handler.is_connected = True
    zone_60 = _make_zone(hass, "60", handler)
    zone_34 = _make_zone(hass, "34", handler)

    dispatched_raw: list[str] = []

    def _on_dispatch(msg: OWNMessage) -> None:
        dispatched_raw.append(str(msg))
        if isinstance(msg, OWNHeatingEvent):
            if str(getattr(msg, "where", "")).split("#")[0] == "34":
                zone_34.handle_event(msg)
            elif str(getattr(msg, "where", "")).split("#")[0] == "60":
                zone_60.handle_event(msg)

    unsub = async_dispatcher_connect(hass, f"myhome_message_{SYNTHETIC_MAC}", _on_dispatch)
    try:
        pool = CommandWorkerPool(handler)
        pool._event_session_ready.set()

        # First real FIFO bleed batch from #886: `*#4*60##` -> Zone 34's 5 frames
        first_bleed = issue_886_trace["fifo_bleed_batches"][0]
        assert first_bleed["request"] == "*#4*60##"
        bleed_messages = [OWNMessage.parse(raw) for raw in first_bleed["collected"]]

        mock_session = MagicMock()
        mock_session.connect = AsyncMock(return_value={"Success": True})
        mock_session.is_connected = True
        mock_session.close = AsyncMock()
        mock_session._stream_reader = object()
        mock_session._stream_writer = object()
        mock_session.last_error = None

        call_idx = 0

        async def mock_send(message: Any, is_status_request: bool = False) -> list[Any] | None:
            nonlocal call_idx
            call_idx += 1
            if str(message) == "*#4*60##":
                return bleed_messages
            if str(message) == "*#4*63*60##":
                mock_session.last_error = SimpleNamespace(reason="command_timeout", responses=None)
                return None
            return []

        mock_session.send = AsyncMock(side_effect=mock_send)

        caplog.set_level(logging.WARNING)
        with (
            patch("custom_components.myhome.gateway.OWNCommandSession", return_value=mock_session),
            patch("custom_components.myhome.gateway.STATUS_TIMEOUT_BACKOFF", 0.02),
        ):
            frames_before = zone_60._poll_health.frames
            written_60 = await pool.send_status_request(OWNCommand("*#4*60##"))
            assert isinstance(written_60, asyncio.Future)
            written_60.add_done_callback(
                lambda fut: zone_60._poll_answered(fut, frames_before)
            )
            written_timeout = await pool.send_status_request(OWNCommand("*#4*63*60##"))
            await pool.send_buffer.put(None)

            await pool.sending_loop(0)

        # 1. FIFO desync warning logged with foreign zone 34
        assert any(
            "Gateway FIFO desync on `*#4*60##`" in rec.message and "34" in rec.message
            for rec in caplog.records
        )
        # 2. `written_60` cancelled with `_gateway_desync=True`, so Zone 60 PollHealth has 0 failed polls!
        assert written_60.cancelled()
        assert getattr(written_60, "_gateway_desync", False) is True
        assert zone_60._poll_health.failed_polls == 0
        # 3. Zone 34 still received its 5 delayed frames and updated its live state!
        assert len(dispatched_raw) == 5
        assert zone_34.current_temperature == 14.6
        assert zone_34.hvac_mode == HVACMode.HEAT
        assert zone_34.target_temperature == 10.0
        # 4. `written_timeout` on `command_timeout` was also cancelled cleanly with `_command_timeout=True`
        assert isinstance(written_timeout, asyncio.Future) and written_timeout.cancelled()
        assert getattr(written_timeout, "_command_timeout", False) is True
    finally:
        unsub()


# ── 6. Stray `*#*1##` / `*#*0##` ACK/NACK frames on the event session ──────────


async def test_event_dispatcher_ignores_stray_ack_and_nack_without_unsupported_log(
    hass: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Stray `*#*1##` / `*#*0##` on the `*99*1##` event socket log cleanly at DEBUG (#886)."""
    handler = MagicMock()
    handler.hass = hass
    handler.mac = SYNTHETIC_MAC
    handler.log_id = "[MyHomeServer1 gateway - 192.0.2.86]"
    handler.bus_monitor = BusMonitor()

    dispatched: list[Any] = []
    unsub = async_dispatcher_connect(
        hass, f"myhome_message_{SYNTHETIC_MAC}", lambda msg: dispatched.append(msg)
    )
    try:
        dispatcher = GatewayEventDispatcher(handler)
        caplog.set_level(logging.DEBUG)

        for raw in ("*#*1##", "*#*0##"):
            parsed = OWNMessage.parse(raw)
            assert parsed is not None
            await dispatcher.process_message(parsed)

        assert dispatched == []
        assert not any("Unsupported message type" in r.message for r in caplog.records)
        debug_msgs = [
            r.message
            for r in caplog.records
            if "Ignored stray gateway ACK/NACK on event session" in r.message
        ]
        assert len(debug_msgs) == 2
    finally:
        unsub()


# ── 7. Edge-case guards (refusing gateways, mock fallback, timer extensions) ───


async def test_humidity_refusing_gateway_drops_pending_zone_dimension_60_and_status_consumer_guards(
    hass: Any,
) -> None:
    """Refusing gateway skips queued Dim 60 requests; `pace_startup_status_write` guards idle vs active."""
    handler = _make_handler(hass, "F454")
    handler._initial_discovery_done.set()
    handler.send_paced = AsyncMock()  # type: ignore[method-assign]
    handler.request_zone_dimension("2", 60)
    await handler.wait_for_zone_requests()
    handler.send_paced.assert_not_awaited()

    # Idle handler (`sending_workers == []`, no delegation, default `send_status_request`) returns immediately
    msg = OWNCommand("*#4*2##")
    assert handler._has_status_consumer(msg) is False
    await handler.pace_startup_status_write(None, msg)

    # Active `sending_workers` or delegated secondary handler returns True
    with patch.object(handler, "_delegated_target", return_value=MagicMock()):
        assert handler._has_status_consumer(msg) is True


async def test_poll_after_discovery_falls_back_to_super_for_mock_gateway(hass: Any) -> None:
    """Entities bound to a lightweight MagicMock gateway fall back to `super()._async_poll_after_discovery()`."""
    mock_gw = MagicMock()
    mock_gw.mac = SYNTHETIC_MAC
    mock_gw.send = AsyncMock()
    mock_gw.send_status_request = AsyncMock()
    mock_gw.wait_for_initial_discovery = AsyncMock()
    mock_gw.supports_zone_status = True
    mock_gw.humidity_request_refused = False

    zone = _make_zone(hass, "5", mock_gw)
    await zone._async_poll_after_discovery()
    mock_gw.send_status_request.assert_awaited_once()


async def test_pause_status_edge_cases_and_mixed_non_who4_collected_frames(
    hass: Any,
) -> None:
    """Verify zero-duration pause, shorter/longer timer replacement, terminated worker guard, and mixed frames."""
    # Mixed non-WHO4 + foreign WHO4 frame in `collected` ignores the non-WHO4 frame and detects foreign zone
    assert (
        _mismatched_who4_reply_zones("*#4*60##", ["*1*1*11##", "*#4*34*0*0146##"])
        == {"34"}
    )
    # Multi-segment WHERE (`#4#01`) and linear-time rejection of malformed `#`-repeated input (CodeQL py/redos)
    assert (
        _mismatched_who4_reply_zones("*#4*60#4#01*60##", ["*#4*34#4#01*60*55##"])
        == {"34"}
    )
    assert (
        _mismatched_who4_reply_zones("*#4*1" + "#a" * 40 + "!", ["*#4*34*0*0146##"])
        is None
    )

    scheduler = FrameScheduler()
    try:
        scheduler.pause_status(0.0)
        assert scheduler.status_paused is False

        scheduler.pause_status(2.0)
        first_handle = scheduler._status_resume_handle
        assert first_handle is not None

        # Shorter pause does not shorten existing deadline
        scheduler.pause_status(0.05)
        assert scheduler._status_resume_handle is first_handle

        # Longer pause cancels and replaces existing timer
        scheduler.pause_status(5.0)
        assert scheduler._status_resume_handle is not first_handle
        assert first_handle.cancelled()

        # `clear_status_pause()` cancels the active handle cleanly
        active_handle = scheduler._status_resume_handle
        scheduler.clear_status_pause()
        assert active_handle is not None and active_handle.cancelled()
        assert scheduler.status_paused is False
    finally:
        scheduler.clear_status_pause()

    # Terminated worker pool ignores `_pause_status_after_desync()`
    handler = _make_handler(hass, "MyHomeServer1")
    pool = CommandWorkerPool(handler)
    pool._terminate_sender = True
    pool._pause_status_after_desync()
    assert handler.send_buffer.status_paused is False


# ── 8. Restored state without live bus reports still polls `*#4*Z##` (#185 / #466 / #886) ──


async def test_restored_zones_without_live_reports_still_poll_while_live_reported_zones_skip(
    hass: Any, issue_886_trace: dict[str, Any]
) -> None:
    """Restored state alone does not suppress `*#4*Z##`; only live post-restart bus reports skip (#886)."""
    zone_ids: list[int] = issue_886_trace["plant"]["zone_ids"]
    humidity_zone_ids = set(issue_886_trace["plant"]["humidity_capable_zone_ids"])

    handler = _make_handler(hass, "MyHomeServer1")
    handler._initial_discovery_done.set()

    wire_log: list[str] = []

    async def fake_send_paced(frames: list[Any]) -> None:
        for f in frames:
            wire_log.append(str(f))

    async def fake_send_status_request(message: OWNCommand) -> asyncio.Future[float]:
        wire_log.append(str(message))
        fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        fut.set_result(1.0)
        return fut

    handler.send_paced = AsyncMock(side_effect=fake_send_paced)  # type: ignore[method-assign]
    handler.send_status_request = AsyncMock(side_effect=fake_send_status_request)  # type: ignore[method-assign]
    handler._wait_for_bus_quiet = AsyncMock()  # type: ignore[method-assign]

    zones: dict[int, MyHOMEClimate] = {}
    for zid in zone_ids:
        z = _make_zone(hass, str(zid), handler, humidity=(zid in humidity_zone_ids))
        zones[zid] = z
        handler.watch_zone_actuators(str(zid))
        if zid == 34:
            # Zone 34 has restored HEAT state only (no live frames since restart) -> must poll `*#4*34##`
            await z.async_restore_last_state(
                State(
                    f"climate.myhome_zone_{zid}",
                    HVACMode.HEAT,
                    {"temperature": 10.0, "current_temperature": 14.5},
                )
            )
            assert z._core_zone_state_reported() is False
        elif zid == 35:
            # Zone 35 has restored state + live temperature only (mode/setpoint still restored) -> must poll `*#4*35##`
            await z.async_restore_last_state(
                State(
                    f"climate.myhome_zone_{zid}",
                    HVACMode.HEAT,
                    {"temperature": 10.0, "current_temperature": 14.5},
                )
            )
            _feed(z, f"*#4*{zid}*0*0185##")
            assert z._hvac_mode_restored is True
            assert z._core_zone_state_reported() is False
        elif zid == 36:
            # Zone 36 has live temperature + live mode (`*4*110*36##`), but setpoint is still restored -> must poll `*#4*36##`
            await z.async_restore_last_state(
                State(
                    f"climate.myhome_zone_{zid}",
                    HVACMode.HEAT,
                    {"temperature": 10.0, "current_temperature": 14.5},
                )
            )
            _feed(z, f"*#4*{zid}*0*0185##", f"*4*110*{zid}##")
            assert z._hvac_mode_restored is False
            assert z._target_temperature_restored is True
            assert z._core_zone_state_reported() is False
        elif zid == 37:
            # Zone 37 reported live OFF on the bus (`*4*303*37##`) + live temperature -> complete!
            _feed(z, "*4*303*37##", "*#4*37*0*0150##")
            assert z._attr_hvac_mode == HVACMode.OFF and z._hvac_mode_restored is False
            assert z._core_zone_state_reported() is True
        else:
            # Remaining zones reported live Dimension 7 + Dimension 0 since startup -> complete!
            _feed(z, f"*#4*{zid}*7*1*1*0100##", f"*#4*{zid}*0*0150##")
            assert z._core_zone_state_reported() is True

    await asyncio.gather(*(z._async_poll_after_discovery() for z in zones.values()))
    if handler._zone_request_task is not None:
        await handler._zone_request_task

    assert wire_log[:2] == ["*#4*#0*20##", "*#4*0*60##"]
    # The 3 zones whose core state was only restored (or partially reported) polled `*#4*Z##`;
    # the 38 zones with live post-restart bus reports skipped `*#4*Z##`.
    assert sorted(wire_log[2:]) == ["*#4*34##", "*#4*35##", "*#4*36##"]


# ── 9. Sweep-aware `_send_zone_requests` & `_wait_for_who4_recovery` (`FRIEND.log` Part 2) ──


async def test_send_zone_requests_waits_for_active_sweep_and_who4_recovery_drains_timeout(
    hass: Any,
) -> None:
    """`_send_zone_requests`, `send_paced`, and `pace_startup_status_write` wait for active sweeps after timeout/desync."""
    handler = _make_handler(hass, "MyHomeServer1")
    handler._initial_discovery_done.set()

    # 1. `_send_zone_requests` waits for `_wait_for_bus_quiet(self._last_event_frame_at)` when `_last_event_frame_at > 0`
    handler.note_event_frame()
    quiet_calls: list[float] = []

    async def record_quiet(since: float) -> None:
        quiet_calls.append(since)

    handler._wait_for_bus_quiet = AsyncMock(side_effect=record_quiet)  # type: ignore[method-assign]
    handler.send_paced = AsyncMock()  # type: ignore[method-assign]

    handler.request_zone_dimension("7", 20)
    await handler.wait_for_zone_requests()
    assert quiet_calls == [handler._last_event_frame_at]
    handler.send_paced.assert_awaited_once_with(["*#4*#0*20##"])

    # 2. `_wait_for_who4_recovery` guards: non-Future, normal Future, and idle bus return immediately
    handler2 = _make_handler(hass, "MyHomeServer1")
    recovery_waits: list[float] = []

    async def fake_wait_quiet(since: float) -> None:
        recovery_waits.append(since)
        # Simulate the sweep ending after the second recovery window
        if len(recovery_waits) >= 3:
            handler2._last_event_frame_at = 0.0

    handler2._wait_for_bus_quiet = AsyncMock(side_effect=fake_wait_quiet)  # type: ignore[method-assign]

    await handler2._wait_for_who4_recovery(None)
    normal_fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    normal_fut.set_result(1.0)
    await handler2._wait_for_who4_recovery(normal_fut)
    assert recovery_waits == []

    # Timed-out status request while bus is already idle (`_last_event_frame_at == 0.0`) -> 0 extra waits
    idle_timeout_fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    setattr(idle_timeout_fut, "_command_timeout", True)
    idle_timeout_fut.cancel()
    await handler2._wait_for_who4_recovery(idle_timeout_fut)
    assert recovery_waits == []

    # 3. Timed-out WHO 4 status request in `send_paced` while event frames keep streaming
    # waits additional `_wait_for_bus_quiet` windows until the sweep finishes
    handler2.note_event_frame()
    active_timeout_fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    setattr(active_timeout_fut, "_command_timeout", True)
    active_timeout_fut.cancel()
    handler2.send_status_request = AsyncMock(return_value=active_timeout_fut)  # type: ignore[method-assign]

    await handler2.send_paced(["*#4*#0*20##"])
    # 1 initial `_wait_for_bus_quiet` + 2 recovery windows before `_last_event_frame_at` cleared
    assert len(recovery_waits) == 3

    # 4. If an external sweep continues past all recovery windows, `_wait_for_who4_recovery` caps at WHO4_RECOVERY_QUIET_WINDOWS
    assert WHO4_RECOVERY_QUIET_WINDOWS == 4
    cap_waits: list[float] = []

    async def always_busy_quiet(since: float) -> None:
        cap_waits.append(since)
        handler2.note_event_frame()

    handler2._wait_for_bus_quiet = AsyncMock(side_effect=always_busy_quiet)  # type: ignore[method-assign]
    handler2.note_event_frame()
    desync_fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    setattr(desync_fut, "_gateway_desync", True)
    desync_fut.cancel()
    await handler2.pace_startup_status_write(desync_fut, OWNCommand("*#4*59##"))
    # 1 initial `_wait_for_bus_quiet` in `pace_startup_status_write` + WHO4_RECOVERY_QUIET_WINDOWS = 5 total
    assert len(cap_waits) == 1 + WHO4_RECOVERY_QUIET_WINDOWS


