"""Comprehensive WHO=14 Actuator Lock & Echo-Verified Cover/Scope tests (#976).

Covers all Wave 2 cover & scope scenarios through real ``CommandWorkerPool``,
``GatewayEventDispatcher``, and ``FrameRouter`` pipelines:
- S1: ``@ermanno00`` ``MH201`` (3.3.6) + ``L4671/2`` ``56`` + ``#1`` 17-member scope,
  mid-run ``*14*0*W##`` freeze, and ``*14*1*W##`` unlock.
- S2: ``MH201`` cold-boot unseen lock -> ``*#2*56##`` NACK -> ``0%`` drift +
  ``_actuation_unconfirmed`` -> ``#1`` skips ``56``, plus ``RestoreEntity`` restart.
- S4: ``F461`` ``05`` ``*2*1000#0/2*`` translations + stationary ``*#2*05## -> *2*0*05##``
  probe abort with ``0%`` drift, ``0 s`` ``last_run_seconds``, and ``0`` relay hold.
- S5: Offline power-cycle self-healing on ``open_cover``, ``set_cover_position(50)``,
  Venetian slat tilt, and ``MyHOMEImpulseCover`` (``WHO=1``).
- S6: ``Dimension 10`` ``SHUTTER_INFO`` ``12/13/14/0/15``, mid-run lock on ``advanced=True``
  covers preserving hardware level, and ``async_calibrate`` lock guards.
- S7: ``F454``/``MH202`` ``_empty_reply=True``, ``F455`` burst ``*#2*W## -> *2*1|2*W##``
  recovery keeping ``_stop_task`` alive, and ``SimpleUpDownStopStrategy`` unconfirmed abort.
- Behind-``F422`` scope fan-out exclusion (partial and all-locked members).
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
)
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from OWNd.connection import OWNNackError
from OWNd.message import (
    OWNActuatorLockCommand,
    OWNAutomationEvent,
    OWNEvent,
    OWNMessage,
)
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

import custom_components.myhome.cover as cover_module
from custom_components.myhome.const import (
    CONF_ADVANCED_SHUTTER,
    CONF_DEVICE_MODEL,
    CONF_MEMBERS,
    CONF_PLATFORMS,
    CONF_SLAT_TILT,
    CONF_SLAT_TIME,
    CONF_TRAVEL_TIME,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
)
from custom_components.myhome.cover import (
    PLATFORM,
    CalibrationInterrupted,
    CoverFamily,
    CoverScope,
    MyHOMECover,
    MyHOMEImpulseCover,
    MyHOMEScopeCover,
)
from custom_components.myhome.cover import (
    async_setup_entry as async_setup_cover,
)
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from tests.conftest import attach_platform, bind_entity

MAC = "00:03:50:97:60:01"


def _make_real_gateway(
    hass: HomeAssistant,
    *,
    model: str = "MH201",
    mac: str = MAC,
    platforms: dict[str, dict[str, Any]] | None = None,
) -> tuple[MockConfigEntry, MyHOMEGatewayHandler]:
    """Build a real MyHOMEGatewayHandler wired to dispatcher_send -> _process_message."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.50",
            CONF_PORT: 20000,
            CONF_PASSWORD: "secret",
            CONF_MAC: mac,
            CONF_NAME: model,
        },
        options={},
        unique_id=mac,
    )
    entry.add_to_hass(hass)
    handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=False)
    handler.is_connected = True
    handler._event_session_ready.set()
    handler._initial_discovery_done.set()
    runtime = MyHOMERuntimeData(
        gateway=handler,
        platforms=platforms or {},
    )
    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[mac] = {
        "entity": handler,
        CONF_PLATFORMS: runtime.platforms,
    }
    return entry, handler


class _FakeCommandSession:
    """Programmable OWNCommandSession responder driving real CommandWorkerPool.sending_loop."""

    def __init__(self, responder: Callable[[str, bool], Any]) -> None:
        self._responder = responder
        self.is_open = True
        self.is_connected = True
        self._stream_reader: object | None = object()
        self._stream_writer: object | None = object()
        self.last_error: Any = None
        self.sent_frames: list[tuple[str, bool]] = []
        self._tx_cond = asyncio.Condition()

    async def connect(self) -> dict[str, Any]:
        self.is_open = True
        self.is_connected = True
        self._stream_reader = object()
        self._stream_writer = object()
        return {"Success": True}

    async def close(self) -> None:
        self.is_open = False
        self._stream_reader = None
        self._stream_writer = None

    async def disconnect(self) -> None:
        await self.close()

    async def send(self, message: Any, is_status_request: bool = False) -> Any:
        raw = str(message)
        self.last_error = None
        outcome = self._responder(raw, bool(is_status_request))
        if inspect.iscoroutine(outcome):
            outcome = await outcome
        if isinstance(outcome, OWNNackError):
            self.last_error = outcome
            result = None
        else:
            result = outcome
        self.sent_frames.append((raw, bool(is_status_request)))
        async with self._tx_cond:
            self._tx_cond.notify_all()
        return result


async def _wait_for_tx(
    handler: MyHOMEGatewayHandler,
    session: _FakeCommandSession,
    expected_count: int,
    hass: HomeAssistant,
) -> None:
    """Wait deterministically until sending_loop delivers expected_count frames and callbacks run."""
    async with asyncio.timeout(2.0):
        async with session._tx_cond:
            await session._tx_cond.wait_for(
                lambda: len(session.sent_frames) >= expected_count
            )
        await handler.send_buffer.join()
    await hass.async_block_till_done()
    await asyncio.sleep(0)


async def _setup_covers_on_gateway(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    handler: MyHOMEGatewayHandler,
) -> dict[str, MyHOMECover | MyHOMEImpulseCover]:
    """Run cover.async_setup_entry and bind entities to hass & FrameRouter."""
    added: list[MyHOMECover | MyHOMEImpulseCover] = []
    with (
        patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()),
        patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]),
        patch.object(handler, "send_status_request", AsyncMock(return_value=True)),
    ):
        await async_setup_cover(hass, entry, added.extend)
        for ent in added:
            ent.hass = hass
            slug = str(ent._device_id).replace("#", "_")
            ent.entity_id = f"cover.c_{slug}"
            ent.async_write_ha_state = MagicMock()
            ent.async_schedule_update_ha_state = MagicMock()
            attach_platform(ent, entry)
            await ent.async_added_to_hass()

    # Bridge CommandWorkerPool's `dispatcher_send(hass, f"myhome_message_{mac}", resp)`
    # through GatewayEventDispatcher.process_message when replies are returned on the command session,
    # while avoiding double-routing when tests call handler._process_message directly.
    by_where: dict[str, MyHOMECover | MyHOMEImpulseCover] = {}
    for ent in added:
        key = f"{ent._where}#4#{ent._interface}" if getattr(ent, "_interface", None) else str(ent._where)
        by_where[key] = ent
    return by_where


# ---------------------------------------------------------------------------
# 1. Class-level defaults & extra_state_attributes on MyHOMECover / Impulse
# ---------------------------------------------------------------------------


def test_bare_new_defaults_and_extra_state_attributes(hass: HomeAssistant) -> None:
    """Verify object.__new__(MyHOMECover) safety and actuator_locked extra_state_attributes."""
    bare = object.__new__(MyHOMECover)
    assert bare._provisional_write_ts is None
    assert bare._move_confirmed is False
    assert bare._actuation_unconfirmed is False
    assert bare._following_scope is False
    assert bare._is_scope_excluded() is False
    assert bare.extra_state_attributes == {}

    _, gw = _make_real_gateway(hass)
    cov = MyHOMECover(
        hass=hass,
        name="Shutter 56",
        entity_name=None,
        device_id="56",
        who="2",
        where="56",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="L4671/2",
        gateway=gw,
    )
    assert "actuator_locked" not in cov.extra_state_attributes
    cov.handle_actuator_lock(True)
    assert cov.extra_state_attributes["actuator_locked"] is True
    assert cov._is_scope_excluded() is True
    cov.handle_actuator_lock(True)  # idempotent
    cov.handle_actuator_lock(False)
    assert cov.extra_state_attributes["actuator_locked"] is False
    assert cov._is_scope_excluded() is False
    cov.handle_actuator_lock(False)  # idempotent

    # Impulse cover without and with access_config
    imp_plain = MyHOMEImpulseCover(
        hass=hass,
        name="Gate 21",
        entity_name=None,
        device_id="21",
        who="1",
        where="21",
        interface=None,
        device_class=CoverDeviceClass.GATE,
        travel_time=10.0,
        pulse_duration=0.5,
        min_cycle_time=1.0,
        pin_code=None,
        state_sensor=None,
        manufacturer="BTicino",
        model="Impulse",
        gateway=gw,
        access_config=None,
    )
    assert "actuator_locked" not in imp_plain.extra_state_attributes
    imp_plain.handle_actuator_lock(True)
    assert imp_plain.extra_state_attributes["actuator_locked"] is True

    imp_access = MyHOMEImpulseCover(
        hass=hass,
        name="Gate 22",
        entity_name=None,
        device_id="22",
        who="1",
        where="22",
        interface=None,
        device_class=CoverDeviceClass.GATE,
        travel_time=10.0,
        pulse_duration=0.5,
        min_cycle_time=1.0,
        pin_code="1234",
        state_sensor=None,
        manufacturer="BTicino",
        model="Impulse",
        gateway=gw,
        access_config={},
    )
    assert imp_access.extra_state_attributes["pin_protected"] is True
    assert "actuator_locked" not in imp_access.extra_state_attributes
    imp_access.handle_actuator_lock(False)
    assert imp_access.extra_state_attributes["actuator_locked"] is False


# ---------------------------------------------------------------------------
# 2. S1: @ermanno00 MH201 + L4671/2 `56` + `#1` 17-member scope + mid-run lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s1_mh201_locked_member_56_and_17_member_scope_isolation(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S1: Outbound *14*0*56## on MH201 locks 56; P2P & #1 scope runs keep 56 at 0% drift."""
    monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.10)

    member_wheres = [str(w) for w in range(41, 58)]  # 17 shutters: 41..57 (includes 56)
    cover_cfgs: dict[str, Any] = {
        w: {
            CONF_WHERE: w,
            CONF_NAME: f"Shutter {w}",
            CONF_DEVICE_MODEL: "L4671/2",
            CONF_TRAVEL_TIME: 20,
        }
        for w in member_wheres
    }
    cover_cfgs["#1"] = {
        CONF_WHERE: "#1",
        CONF_NAME: "All Shutters #1",
        CONF_MEMBERS: member_wheres,
        CONF_TRAVEL_TIME: 20,
    }

    entry, handler = _make_real_gateway(
        hass,
        model="MH201",
        platforms={PLATFORM: cover_cfgs},
    )
    entities = await _setup_covers_on_gateway(hass, entry, handler)
    for w in member_wheres:
        cov = entities[w]
        assert isinstance(cov, MyHOMECover)
        cov._attr_current_cover_position = 100
        cov._start_position = 100
        cov._attr_is_closed = False

    cov_56 = entities["56"]
    cov_41 = entities["41"]
    scope_1 = entities["#1"]
    assert isinstance(cov_56, MyHOMECover)
    assert isinstance(cov_41, MyHOMECover)
    assert isinstance(scope_1, MyHOMEScopeCover)

    session = _FakeCommandSession(lambda _raw, _is_req: True)
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        worker = asyncio.create_task(handler.sending_loop(0))
        try:
            # 1. Press HA Lock on 56 -> *14*0*56## ACKed on MH201 (no *99*1## WHO=14 echo!),
            # followed 54 ms later by *2*0*56## STOP on event session.
            fut_lock = await handler.send(OWNActuatorLockCommand.lock("56"))
            await asyncio.wait_for(fut_lock, timeout=2.0)
            await handler._process_message(OWNMessage.parse("*2*0*56##"))
            await hass.async_block_till_done()

            assert cov_56.actuator_locked is True
            assert cov_56.extra_state_attributes["actuator_locked"] is True

            # 2. P2P close_cover(56) and set_cover_position(56, 40):
            # 0.0 s UI badge flicker, 0 status probes, 0 stop frames, 0% position drift!
            session.sent_frames.clear()
            await cov_56.async_close_cover()
            assert cov_56.is_closing is False
            assert cov_56.is_opening is False
            assert cov_56._live_refresh_unsub is None
            assert cov_56._relay_hold_until is None
            assert cov_56._stop_task is not None
            await asyncio.wait_for(cov_56._stop_task, timeout=2.0)
            await _wait_for_tx(handler, session, 1, hass)
            assert cov_56.current_cover_position == 100
            assert session.sent_frames == [("*2*2*56##", False)]

            session.sent_frames.clear()
            await cov_56.async_set_cover_position(**{ATTR_POSITION: 40})
            assert cov_56.is_closing is False
            assert cov_56._stop_task is not None
            await asyncio.wait_for(cov_56._stop_task, timeout=2.0)
            await _wait_for_tx(handler, session, 1, hass)
            assert cov_56.current_cover_position == 100
            assert session.sent_frames == [("*2*2*56##", False)]

            # Restore full 1.5 s echo window for echoed scope & P2P runs below
            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 1.5)

            # 3. Scope #1 close: 56 has a simulated stale relay_hold_until? Verify _stops_before_move
            # ignores excluded 56 so no pre-move stop is sent when only 56 had a hold!
            cov_56._relay_hold_until = time.monotonic() + 60.0
            assert scope_1._stops_before_move(False) is False
            cov_56._relay_hold_until = None

            session.sent_frames.clear()
            await scope_1.async_close_cover()
            await _wait_for_tx(handler, session, 1, hass)
            # Relay *2*2*#1## on event session (just as MH201/F461/MHS1 does)
            await handler._process_message(OWNMessage.parse("*2*2*#1##"))
            await hass.async_block_till_done()

            # 56 MUST stay unlocked-proof (actuator_locked=True, is_closing=False, pos=100%),
            # while all other 16 shutters are closing!
            assert cov_56.actuator_locked is True
            assert cov_56.is_closing is False
            assert cov_56.current_cover_position == 100
            assert cov_56._stop_task is None
            for w in member_wheres:
                if w != "56":
                    peer = entities[w]
                    assert isinstance(peer, MyHOMECover)
                    assert peer.is_closing is True
                    assert peer._following_scope is True

            # Stop #1 via HA: stationary excluded 56 is skipped in _async_stop_local_scope,
            # while the 16 moving peers freeze cleanly!
            session.sent_frames.clear()
            await scope_1.async_stop_cover()
            await _wait_for_tx(handler, session, 1, hass)
            assert session.sent_frames == [("*2*0*#1##", False)]
            assert cov_56.current_cover_position == 100
            assert cov_56._pending_cmd is None
            for w in member_wheres:
                peer = entities[w]
                assert isinstance(peer, MyHOMECover)
                assert peer.is_closing is False

            # 4. Mid-run WHO=14 lock on moving shutter 41 freezes position & clears relay hold!
            cov_41._attr_current_cover_position = 100
            cov_41._start_position = 100
            cov_41._clear_relay_hold()
            session.sent_frames.clear()
            await cov_41.async_close_cover()
            await _wait_for_tx(handler, session, 1, hass)
            t_start = time.monotonic() - 5.0  # simulate 5 s of 20 s close (100% -> 75%)
            await handler._process_message(OWNMessage.parse("*2*2*41##"))
            cov_41._move_start_time = t_start
            cov_41._run_started_at = t_start
            assert cov_41._relay_hold_until is not None

            fut_lock_41 = await handler.send(OWNActuatorLockCommand.lock("41"))
            await asyncio.wait_for(fut_lock_41, timeout=2.0)
            await handler._process_message(OWNMessage.parse("*2*0*41##"))

            assert cov_41.actuator_locked is True
            assert cov_41.is_closing is False
            assert cov_41._relay_hold_until is None
            assert 65 <= (cov_41.current_cover_position or 0) <= 76

            # 5. Unlock 56 via *14*1*56## -> immediate P2P run works again!
            fut_unlock = await handler.send(OWNActuatorLockCommand.unlock("56"))
            await asyncio.wait_for(fut_unlock, timeout=2.0)
            assert cov_56.actuator_locked is False

            session.sent_frames.clear()
            await cov_56.async_close_cover()
            await _wait_for_tx(handler, session, 1, hass)
            await handler._process_message(OWNMessage.parse("*2*2*56##"))
            assert cov_56.is_closing is True
            assert cov_56._move_confirmed is True
            cov_56._cancel_stop_task()
        finally:
            handler._command_pool.close()
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker


# ---------------------------------------------------------------------------
# 3. S2: Cold-boot unseen lock (*#2*56## NACK -> _actuation_unconfirmed) + RestoreEntity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s2_unseen_lock_probe_nack_marks_unconfirmed_and_restore_persists(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S2: Cold-boot unseen lock NACKs *#2*56## -> 0% drift + #1 skips 56; RestoreEntity persists."""
    monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.10)

    cover_cfgs = {
        "55": {CONF_WHERE: "55", CONF_NAME: "Shutter 55", CONF_TRAVEL_TIME: 20},
        "56": {CONF_WHERE: "56", CONF_NAME: "Shutter 56", CONF_TRAVEL_TIME: 20},
        "#1": {
            CONF_WHERE: "#1",
            CONF_NAME: "Group 1",
            CONF_MEMBERS: ["55", "56"],
            CONF_TRAVEL_TIME: 20,
        },
    }
    entry, handler = _make_real_gateway(
        hass,
        model="MH201",
        platforms={PLATFORM: cover_cfgs},
    )
    entities = await _setup_covers_on_gateway(hass, entry, handler)
    cov_55 = entities["55"]
    cov_56 = entities["56"]
    scope_1 = entities["#1"]
    assert isinstance(cov_55, MyHOMECover)
    assert isinstance(cov_56, MyHOMECover)
    assert isinstance(scope_1, MyHOMEScopeCover)

    for c in (cov_55, cov_56):
        c._attr_current_cover_position = 100
        c._start_position = 100
        c._attr_is_closed = False

    def _responder(raw: str, is_req: bool) -> Any:
        if is_req and raw == "*#2*56##":
            return OWNNackError(raw)
        return True

    session = _FakeCommandSession(_responder)
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        worker = asyncio.create_task(handler.sending_loop(0))
        try:
            assert cov_56.actuator_locked is None
            await cov_56.async_close_cover()
            # Even if a concurrent group frame *2*2*#1## arrives during ECHO_WINDOW,
            # it MUST NOT confirm 56's P2P move!
            cov_56.handle_event(OWNEvent.parse("*2*2*#1##"))
            assert cov_56._move_confirmed is False

            assert cov_56._stop_task is not None
            await asyncio.wait_for(cov_56._stop_task, timeout=2.0)
            await _wait_for_tx(handler, session, 2, hass)

            assert ("*2*2*56##", False) in session.sent_frames
            assert ("*#2*56##", True) in session.sent_frames
            assert cov_56.is_closing is False
            assert cov_56.current_cover_position == 100
            assert cov_56.extra_state_attributes["last_run_seconds"] is None
            assert cov_56._relay_hold_until is None
            assert cov_56._actuation_unconfirmed is True
            assert cov_56._is_scope_excluded() is True

            # Subsequent #1 scope run skips 56 completely!
            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 1.5)
            session.sent_frames.clear()
            await scope_1.async_close_cover()
            await _wait_for_tx(handler, session, 1, hass)
            await handler._process_message(OWNMessage.parse("*2*2*#1##"))
            assert cov_56.is_closing is False
            assert cov_56.current_cover_position == 100
            assert cov_55.is_closing is True
            cov_55._cancel_stop_task()

            # Genuine P2P motor start clears _actuation_unconfirmed
            await handler._process_message(OWNMessage.parse("*2*2*56##"))
            assert cov_56._actuation_unconfirmed is False
            assert cov_56.is_closing is True
            cov_56._cancel_stop_task()
            cov_56._cancel_wall_settle()
            cov_56._stop_live_refresh()
        finally:
            handler._command_pool.close()
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker

    # Soft RestoreEntity persistence across HA restart (including recent last_run_ended_at
    # and mid-run "closing" state: locking opened the relay, so _relay_hold_until stays None!)
    _, handler_r = _make_real_gateway(hass, mac="00:03:50:97:60:02", model="MH201")
    cov_restored = MyHOMECover(
        hass=hass,
        name="Restored 56",
        entity_name=None,
        device_id="56",
        who="2",
        where="56",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="L4671/2",
        gateway=handler_r,
    )
    cov_restored._poll_on_add = False
    cov_restored.entity_id = "cover.restored_56"
    with patch.object(
        cov_restored,
        "async_get_last_state",
        AsyncMock(
            return_value=State(
                "cover.restored_56",
                "open",
                {
                    ATTR_CURRENT_POSITION: 85,
                    "actuator_locked": True,
                    "last_run_ended_at": dt_util.utcnow().isoformat(timespec="milliseconds"),
                },
            )
        ),
    ):
        await cov_restored.async_added_to_hass()
    assert cov_restored.current_cover_position == 85
    assert cov_restored.actuator_locked is True
    assert cov_restored.extra_state_attributes["actuator_locked"] is True
    assert cov_restored._relay_hold_until is None
    assert cov_restored._stops_before_move(False) is False
    assert handler_r._actuator_locks.is_locked("56") is True

    cov_restored_moving = MyHOMECover(
        hass=hass,
        name="Restored 57",
        entity_name=None,
        device_id="57",
        who="2",
        where="57",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="L4671/2",
        gateway=handler_r,
    )
    cov_restored_moving._poll_on_add = False
    cov_restored_moving.entity_id = "cover.restored_57"
    with patch.object(
        cov_restored_moving,
        "async_get_last_state",
        AsyncMock(
            return_value=State(
                "cover.restored_57",
                "closing",
                {ATTR_CURRENT_POSITION: 50, "actuator_locked": True},
            )
        ),
    ):
        await cov_restored_moving.async_added_to_hass()
    assert cov_restored_moving.actuator_locked is True
    assert cov_restored_moving._relay_hold_until is None
    assert cov_restored_moving._stops_before_move(False) is False


# ---------------------------------------------------------------------------
# 4. S4: F461 + F411/2 `05` translations + stationary `*#2*05## -> *2*0*05##` abort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s4_f461_translations_and_stationary_probe_reply_zero_drift(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S4: F461 *2*1000#0/2*05## + stationary *#2*05## -> *2*0*05## aborts with 0% drift."""
    monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.10)

    entry, handler = _make_real_gateway(
        hass,
        model="F461",
        platforms={
            PLATFORM: {
                "05": {
                    CONF_WHERE: "05",
                    CONF_NAME: "Shutter 05",
                    CONF_DEVICE_MODEL: "F411/2",
                    CONF_TRAVEL_TIME: 10,
                }
            }
        },
    )
    entities = await _setup_covers_on_gateway(hass, entry, handler)
    cov_05 = entities["05"]
    assert isinstance(cov_05, MyHOMECover)
    cov_05._attr_current_cover_position = 80
    cov_05._start_position = 80
    cov_05._attr_is_closed = False

    def _responder(raw: str, is_req: bool) -> Any:
        if is_req and raw == "*#2*05##":
            return [OWNMessage.parse("*2*0*05##")]
        return True

    session = _FakeCommandSession(_responder)
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        worker = asyncio.create_task(handler.sending_loop(0))
        try:
            await cov_05.async_set_cover_position(**{ATTR_POSITION: 30})
            # F461 emits *2*1000#0*05## + *2*1000#2*05## translations on locked F411/2
            await handler._process_message(OWNMessage.parse("*2*1000#0*05##"))
            await handler._process_message(OWNMessage.parse("*2*1000#2*05##"))

            assert cov_05._stop_task is not None
            await asyncio.wait_for(cov_05._stop_task, timeout=2.0)
            await _wait_for_tx(handler, session, 2, hass)

            assert [f for f, _ in session.sent_frames] == ["*2*2*05##", "*#2*05##"]
            assert cov_05.is_closing is False
            assert cov_05.current_cover_position == 80
            assert cov_05.extra_state_attributes["last_run_seconds"] is None
            assert cov_05._relay_hold_until is None
            assert cov_05._actuation_unconfirmed is True

            # Also verify translation stop (*2*1000#0*05##) after ECHO_WINDOW on an unconfirmed run
            # aborts cleanly without setting _actuation_unconfirmed, AND P2P stop_cover() inside
            # ECHO_WINDOW on an unlocked cover anchors from _provisional_write_ts!
            cov_05._actuation_unconfirmed = False
            cov_05._begin_run("open")
            cov_05._echo_until = time.monotonic() - 1.0
            cov_05.handle_event(OWNEvent.parse("*2*1000#0*05##"))
            assert cov_05.is_opening is False
            assert cov_05._actuation_unconfirmed is False
            assert cov_05.current_cover_position == 80

            # Unlocked cover stopped inside ECHO_WINDOW before motor-start echo arrives
            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 1.5)
            session.sent_frames.clear()
            await cov_05.async_close_cover()
            await _wait_for_tx(handler, session, 1, hass)
            assert cov_05._provisional_write_ts is not None
            cov_05._provisional_write_ts = time.monotonic() - 2.0  # 2 s of 10 s = 20%
            await cov_05.async_stop_cover()
            await _wait_for_tx(handler, session, 2, hass)
            assert cov_05.is_closing is False
            assert 50 <= (cov_05.current_cover_position or 0) <= 62
        finally:
            handler._command_pool.close()
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker


# ---------------------------------------------------------------------------
# 5. S5: Offline power-cycle self-healing (curtain, partial, Venetian tilt, Impulse)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s5_offline_power_cycle_self_heal_curtain_tilt_and_impulse(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S5: Offline-unlocked actuators self-heal on first P2P echo across timed, tilt, and impulse."""
    cover_cfgs = {
        "56": {CONF_WHERE: "56", CONF_NAME: "Shutter 56", CONF_TRAVEL_TIME: 1},
        "31": {
            CONF_WHERE: "31",
            CONF_NAME: "Venetian 31",
            CONF_TRAVEL_TIME: 10,
            CONF_SLAT_TILT: True,
            CONF_SLAT_TIME: 1.0,
        },
        "1-22": {
            CONF_WHERE: "22",
            CONF_WHO: "1",
            CONF_NAME: "Gate 22",
            CONF_TRAVEL_TIME: 5,
        },
    }
    entry, handler = _make_real_gateway(
        hass,
        model="F461",
        platforms={PLATFORM: cover_cfgs},
    )
    entities = await _setup_covers_on_gateway(hass, entry, handler)
    cov_56 = entities["56"]
    blind_31 = entities["31"]
    gate_22 = entities["22"]
    assert isinstance(cov_56, MyHOMECover)
    assert isinstance(blind_31, MyHOMECover)
    assert isinstance(gate_22, MyHOMEImpulseCover)

    cov_56._attr_current_cover_position = 0
    cov_56._start_position = 0
    cov_56._attr_is_closed = True
    blind_31._attr_current_cover_position = 0
    blind_31._start_position = 0
    blind_31._attr_current_cover_tilt_position = 0

    # Lock all three
    handler._actuator_locks.set_point_lock("56", None, True, observed=True)
    handler._actuator_locks.set_point_lock("31", None, True, observed=True)
    handler._actuator_locks.set_point_lock("22", None, True, observed=True)
    assert cov_56.actuator_locked is True
    assert blind_31.actuator_locked is True
    assert gate_22.actuator_locked is True

    session = _FakeCommandSession(lambda _raw, _is_req: True)
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        worker = asyncio.create_task(handler.sending_loop(0))
        try:
            # 1. Partial set_cover_position(50) on offline-unlocked 56 (1.0 s travel -> 0.5 s run)
            session.sent_frames.clear()
            await cov_56.async_set_cover_position(**{ATTR_POSITION: 50})
            assert cov_56.is_opening is False  # suppressed until echo!
            await _wait_for_tx(handler, session, 1, hass)
            await handler._process_message(OWNMessage.parse("*2*1*56##"))
            assert cov_56.actuator_locked is False
            assert handler._actuator_locks.is_locked("56") is False
            assert cov_56.is_opening is True
            assert cov_56._attr_is_closed is False
            assert cov_56._stop_task is not None
            await asyncio.wait_for(cov_56._stop_task, timeout=2.0)
            await _wait_for_tx(handler, session, 2, hass)
            assert cov_56.is_opening is False
            assert cov_56.is_closed is False
            assert 45 <= (cov_56.current_cover_position or 0) <= 85

            # 2. Venetian tilt: (a) still locked on bus -> 0% tilt change, 0 stop frames
            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.15)
            session.sent_frames.clear()
            await blind_31.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
            assert blind_31.is_opening is False
            assert blind_31._stop_task is not None
            await asyncio.wait_for(blind_31._stop_task, timeout=2.0)
            await _wait_for_tx(handler, session, 1, hass)
            assert blind_31.current_cover_tilt_position == 0
            assert session.sent_frames == [("*2*1*31##", False)]

            # Also cover generation supersede inside locked tilt wait & already-elapsed write_ts
            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 1.5)
            fut_old: asyncio.Future[float] = asyncio.get_running_loop().create_future()
            fut_old.set_result(time.monotonic() - 5.0)
            blind_31._is_tilting = True
            blind_31._start_pulse_auto_stop(fut_old, blind_31._run_generation, asyncio.Event())
            assert blind_31._stop_task is not None
            await asyncio.wait_for(blind_31._stop_task, timeout=2.0)
            assert blind_31._is_tilting is False

            fut_sup: asyncio.Future[float] = asyncio.get_running_loop().create_future()
            fut_sup.set_result(time.monotonic())
            blind_31._is_tilting = True
            ev_sup = asyncio.Event()
            blind_31._start_pulse_auto_stop(fut_sup, blind_31._run_generation, ev_sup)
            sup_task = blind_31._stop_task
            assert sup_task is not None
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            blind_31._run_generation += 1
            ev_sup.set()
            await asyncio.wait_for(sup_task, timeout=2.0)
            blind_31._is_tilting = False

            # (b) Venetian blind 31 power-cycled offline -> echo *2*1*31## self-heals & tilts to 50%!
            session.sent_frames.clear()
            await blind_31.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
            await _wait_for_tx(handler, session, 1, hass)
            await handler._process_message(OWNMessage.parse("*2*1*31##"))
            assert blind_31.actuator_locked is False
            assert blind_31.is_opening is True
            assert blind_31._stop_task is not None
            await asyncio.wait_for(blind_31._stop_task, timeout=2.0)
            await _wait_for_tx(handler, session, 2, hass)
            assert blind_31.current_cover_tilt_position is not None
            assert blind_31.current_cover_tilt_position > 0

            # 3. MyHOMEImpulseCover:
            # (a) WHAT 30/31 dim steps ignored
            gate_22.handle_event(OWNEvent.parse("*1*30*22##"))
            gate_22.handle_event(OWNEvent.parse("*1*31*22##"))
            assert gate_22.actuator_locked is True
            assert gate_22.is_opening is False

            # (b) Still locked -> open_cover sends pulse without optimistic opening/timer
            gate_22._min_cycle_time = 0.0
            await gate_22.async_open_cover()
            assert gate_22.is_opening is False
            assert gate_22._travel_timer_cancel is None

            # (c) Power-cycled offline -> open_cover + *1*18*22## echo inside 1.0 s guard self-heals & opens!
            gate_22._last_pulse_time = -1e9
            await gate_22.async_open_cover()
            assert gate_22.is_opening is False
            gate_22.handle_event(OWNEvent.parse("*1*18*22##"))
            assert gate_22.actuator_locked is False
            assert handler._actuator_locks.is_locked("22") is False
            assert gate_22.is_opening is True
            assert gate_22.is_closed is False
            assert gate_22._travel_timer_cancel is not None
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
            await hass.async_block_till_done()
            assert gate_22.is_opening is False
            assert gate_22.is_closed is False

            # (d) Locked impulse cover receiving external wall button press (no pending HA pulse)
            handler._actuator_locks.set_point_lock("22", None, True, observed=True)
            gate_22._locked_pulse_intent = None
            gate_22._last_pulse_time = -1e9
            gate_22.handle_event(OWNEvent.parse("*1*1*22##"))
            assert gate_22.actuator_locked is False
            if gate_22._travel_timer_cancel is not None:
                gate_22._cancel_travel_timer()
        finally:
            handler._command_pool.close()
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker


# ---------------------------------------------------------------------------
# 6. S6: Dim 10 SHUTTER_INFO 12/13/14/0/15, mid-run lock variants & calibration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s6_dim10_shutter_info_midrun_lock_and_calibration_guards(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S6: Dim 10 SHUTTER_INFO 12/13/14/0/15, advanced mid-run lock, and calibration guards."""
    monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.10)

    entry, handler = _make_real_gateway(
        hass,
        model="F461",
        platforms={
            PLATFORM: {
                "21": {
                    CONF_WHERE: "21",
                    CONF_NAME: "F401 Native 21",
                    CONF_ADVANCED_SHUTTER: True,
                    CONF_SLAT_TILT: True,
                },
                "22": {
                    CONF_WHERE: "22",
                    CONF_NAME: "Timed 22",
                    CONF_TRAVEL_TIME: 10,
                    CONF_SLAT_TILT: True,
                },
            }
        },
    )
    entities = await _setup_covers_on_gateway(hass, entry, handler)
    nat_21 = entities["21"]
    tim_22 = entities["22"]
    assert isinstance(nat_21, MyHOMECover)
    assert isinstance(tim_22, MyHOMECover)

    # 1. Cold-boot Dim 10 SHUTTER_INFO 12 / 13 locks, 0 / 15 unlocks
    nat_21.handle_event(OWNAutomationEvent.parse("*#2*21*10*10*45*001*12##"))
    assert nat_21.current_cover_position == 45
    assert nat_21.actuator_locked is True
    assert handler._actuator_locks.is_locked("21") is True

    nat_21.handle_event(OWNAutomationEvent.parse("*#2*21*10*10*45*001*15##"))
    assert nat_21.actuator_locked is False

    nat_21.handle_event(OWNAutomationEvent.parse("*#2*21*10*10*45*001*13##"))
    assert nat_21.actuator_locked is True

    nat_21.handle_event(OWNAutomationEvent.parse("*#2*21*10*11*45*001*0##"))
    assert nat_21.actuator_locked is False
    assert nat_21.is_opening is True

    # Mid-run lock on advanced=True preserves exact hardware Dim 10 position (45%)
    nat_21.handle_actuator_lock(True)
    assert nat_21.is_opening is False
    assert nat_21.current_cover_position == 45
    nat_21.handle_actuator_lock(False)

    # Fallback Dim 10 SHUTTER_INFO when gateway has no ActuatorLockManager
    stub_gw = MagicMock()
    stub_gw.mac = MAC
    stub_gw.unique_id = MAC
    stub_gw.log_id = "[stub]"
    stub_gw._actuator_locks = None
    stub_gw.actuator_lock_owner = lambda: None
    stub_gw.send = AsyncMock()
    stub_gw.send_status_request = AsyncMock()
    cov_nomgr = MyHOMECover(
        hass=hass,
        name="NoMgr 29",
        entity_name=None,
        device_id="29",
        who="2",
        where="29",
        interface=None,
        advanced=True,
        manufacturer="BTicino",
        model="F401",
        gateway=stub_gw,
    )
    cov_nomgr.handle_event(OWNAutomationEvent.parse("*#2*29*10*10*60*001*12##"))
    assert cov_nomgr.actuator_locked is True
    cov_nomgr.handle_event(OWNAutomationEvent.parse("*#2*29*10*10*60*001*0##"))
    assert cov_nomgr.actuator_locked is False

    # 2. SHUTTER_INFO == 14 (Command not executed) aborts unconfirmed tilt & unconfirmed curtain move
    nat_21._attr_current_cover_tilt_position = 0
    nat_21._begin_run("open", is_tilting=True)
    assert nat_21._is_tilting is True
    nat_21.handle_event(OWNAutomationEvent.parse("*#2*21*10*10*45*001*14##"))
    assert nat_21._is_tilting is False
    assert nat_21.current_cover_tilt_position == 0

    tim_22._attr_current_cover_position = 70
    tim_22._start_position = 70
    tim_22._begin_run("close")
    assert tim_22.is_closing is True
    tim_22.handle_event(OWNAutomationEvent.parse("*#2*22*10*10*255*001*14##"))
    assert tim_22.is_closing is False
    assert tim_22.current_cover_position == 70

    # 3. handle_actuator_lock(True) on active tilt & on unconfirmed timed move
    tim_22._begin_run("open", is_tilting=True)
    tim_22.handle_actuator_lock(True)
    assert tim_22._is_tilting is False
    tim_22.handle_actuator_lock(False)

    tim_22._begin_run("open")
    assert tim_22._move_confirmed is False
    tim_22.handle_actuator_lock(True)
    assert tim_22.is_opening is False
    assert tim_22.current_cover_position == 70

    # 4. Calibration guards:
    # (a) Refused while actuator_locked is True
    with pytest.raises(HomeAssistantError, match="cannot calibrate while actuator is locked"):
        await tim_22.async_calibrate()

    # (b) Interrupted mid-calibration when WHO=14 lock arrives
    tim_22.handle_actuator_lock(False)
    session = _FakeCommandSession(
        lambda raw, is_req: OWNNackError(raw) if is_req else True
    )
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        worker = asyncio.create_task(handler.sending_loop(0))
        try:
            # Unseen lock during _calibration_run -> *#2*22## NACK -> _MoveSuperseded -> HomeAssistantError
            session.sent_frames.clear()
            with pytest.raises(HomeAssistantError, match="did not start moving"):
                await tim_22.async_calibrate()
            await _wait_for_tx(handler, session, 2, hass)
            tim_22._actuation_unconfirmed = False
            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 1.5)
            session.sent_frames.clear()

            # Mid-calibration WHO=14 lock -> CalibrationInterrupted
            async def _lock_once_started() -> None:
                await _wait_for_tx(handler, session, 1, hass)
                assert tim_22._calibrating is True
                await handler._process_message(OWNMessage.parse("*2*1*22##"))
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                tim_22.handle_actuator_lock(True)

            locker = asyncio.create_task(_lock_once_started())
            with pytest.raises(CalibrationInterrupted, match="actuator locked"):
                await tim_22.async_calibrate()
            await locker
        finally:
            handler._command_pool.close()
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker


# ---------------------------------------------------------------------------
# 7. S7: F454 _empty_reply, F455 burst recovery (open & close), SimpleUpDownStopStrategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s7_empty_reply_f455_burst_recovery_and_simple_strategy_abort(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S7: F454 _empty_reply aborts; F455 *#2*W## -> *2*1|2*W## keeps _stop_task alive; simple aborts."""
    monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.10)
    monkeypatch.setattr(cover_module, "_EMPTY_PROBE_GRACE_S", 0.02)

    entry, handler = _make_real_gateway(
        hass,
        model="F455",
        platforms={
            PLATFORM: {
                "31": {CONF_WHERE: "31", CONF_NAME: "F454 Empty 31", CONF_TRAVEL_TIME: 10},
                "32": {CONF_WHERE: "32", CONF_NAME: "F455 Burst 32", CONF_TRAVEL_TIME: 1},
                "33": {CONF_WHERE: "33", CONF_NAME: "Simple 33", CONF_TRAVEL_TIME: 0},
            }
        },
    )
    entities = await _setup_covers_on_gateway(hass, entry, handler)
    cov_empty = entities["31"]
    cov_burst = entities["32"]
    cov_simple = entities["33"]
    assert isinstance(cov_empty, MyHOMECover)
    assert isinstance(cov_burst, MyHOMECover)
    assert isinstance(cov_simple, MyHOMECover)
    assert cov_simple.assumed_state is True

    cov_empty._attr_current_cover_position = 60
    cov_empty._start_position = 60
    cov_burst._attr_current_cover_position = 0
    cov_burst._start_position = 0
    cov_burst._attr_is_closed = True
    cov_simple._attr_is_closed = True

    burst_reply = "*2*1*32##"

    def _responder(raw: str, is_req: bool) -> Any:
        if is_req and raw == "*#2*31##":
            return True  # bare *#*1## -> _empty_reply=True!
        if is_req and raw == "*#2*32##":
            return [OWNMessage.parse(burst_reply)]
        if is_req and raw == "*#2*33##":
            return OWNNackError(raw)
        return True

    session = _FakeCommandSession(_responder)
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        worker = asyncio.create_task(handler.sending_loop(0))
        try:
            # 1. F454 / MH202 _empty_reply=True -> aborts with 0% drift and _actuation_unconfirmed=True
            session.sent_frames.clear()
            await cov_empty.async_open_cover()
            assert cov_empty._stop_task is not None
            await asyncio.wait_for(cov_empty._stop_task, timeout=2.0)
            await _wait_for_tx(handler, session, 2, hass)
            assert cov_empty.is_opening is False
            assert cov_empty.current_cover_position == 60
            assert cov_empty._actuation_unconfirmed is True

            # 2. F455 burst drop recovery on OPEN (set_cover_position 50 from 0%):
            # initial event echo dropped -> *#2*32## returns *2*1*32## -> _stop_task stays alive & sends *2*0*32##!
            session.sent_frames.clear()
            await cov_burst.async_set_cover_position(**{ATTR_POSITION: 50})
            stop_task_open = cov_burst._stop_task
            assert stop_task_open is not None
            await asyncio.wait_for(stop_task_open, timeout=2.0)
            await _wait_for_tx(handler, session, 3, hass)
            assert ("*#2*32##", True) in session.sent_frames
            assert ("*2*0*32##", False) in session.sent_frames
            assert cov_burst._actuation_unconfirmed is False

            # F455 burst drop recovery on CLOSE (set_cover_position 50 from 100% -> *#2*32## returns *2*2*32##)
            burst_reply = "*2*2*32##"
            cov_burst._attr_current_cover_position = 100
            cov_burst._start_position = 100
            cov_burst._attr_is_closed = False
            session.sent_frames.clear()
            await cov_burst.async_set_cover_position(**{ATTR_POSITION: 50})
            stop_task_close = cov_burst._stop_task
            assert stop_task_close is not None
            await asyncio.wait_for(stop_task_close, timeout=2.0)
            await _wait_for_tx(handler, session, 3, hass)
            assert ("*#2*32##", True) in session.sent_frames
            assert ("*2*0*32##", False) in session.sent_frames

            # External opposite-direction takeover while probe is in flight (generation != _run_generation
            # with _move_confirmed == True, and unconfirmed supersede raising _MoveSuperseded)
            probe_gate: asyncio.Future[float] = asyncio.get_running_loop().create_future()
            with patch.object(
                handler,
                "send_status_request",
                AsyncMock(return_value=probe_gate),
            ):
                gen_before = cov_burst._run_generation
                verify_task = asyncio.create_task(
                    cov_burst._verify_unconfirmed_p2p_move(gen_before, asyncio.Event(), time.monotonic())
                )
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                cov_burst.handle_event(OWNEvent.parse("*2*1*32##"))  # external wall open sets _move_confirmed=True
                probe_gate.set_result(time.monotonic())
                anchor_res = await asyncio.wait_for(verify_task, timeout=2.0)
                assert isinstance(anchor_res, float)
                cov_burst._cancel_wall_settle()
                cov_burst._stop_live_refresh()

            probe_unconf: asyncio.Future[float] = asyncio.get_running_loop().create_future()
            probe_unconf.set_result(time.monotonic())
            with patch.object(
                handler,
                "send_status_request",
                AsyncMock(return_value=probe_unconf),
            ):
                cov_burst._move_confirmed = False
                with pytest.raises(cover_module._MoveSuperseded):
                    await cov_burst._verify_unconfirmed_p2p_move(
                        cov_burst._run_generation - 1,
                        asyncio.Event(),
                        time.monotonic(),
                    )

            # Direct _handle_echo self-heal when actuator_locked is True
            cov_burst.handle_actuator_lock(True)
            cov_burst._begin_run("open")
            assert cov_burst._handle_echo(OWNEvent.parse("*2*1*32##"), time.monotonic()) is True
            assert cov_burst.actuator_locked is False
            cov_burst._stop_live_refresh()
            cov_burst._abort_motion()

            # 3. SimpleUpDownStopStrategy (assumed_state=True, no _stop_task):
            # _arm_unconfirmed_move_timer fires after ECHO_WINDOW, probes *#2*33## -> NACK -> aborts!
            session.sent_frames.clear()
            assert cov_simple.is_closed is True
            await cov_simple.async_open_cover()
            assert cov_simple.is_opening is True
            await _wait_for_tx(handler, session, 1, hass)
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
            await _wait_for_tx(handler, session, 2, hass)
            if cov_simple._unconfirmed_move_task is not None:
                await asyncio.wait_for(cov_simple._unconfirmed_move_task, timeout=2.0)
            await hass.async_block_till_done()
            assert ("*#2*33##", True) in session.sent_frames
            assert cov_simple.is_opening is False
            assert cov_simple.is_closed is True
            assert cov_simple._actuation_unconfirmed is True

            # Cover remaining _arm_unconfirmed_move_timer branches:
            # - hass is None guard
            cov_simple.hass = None
            cov_simple._arm_unconfirmed_move_timer(time.monotonic())
            assert cov_simple._unconfirmed_move_unsub is None
            cov_simple.hass = hass

            # - _echo_stop_at branch inside _on_expired
            cov_simple._actuation_unconfirmed = False
            cov_simple._begin_run("open")
            cov_simple._echo_stop_at = time.monotonic()
            cov_simple._arm_unconfirmed_move_timer(time.monotonic())
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
            await hass.async_block_till_done()
            assert cov_simple.is_opening is False

            # - generation mismatch inside _on_expired
            cov_simple._begin_run("open")
            cov_simple._arm_unconfirmed_move_timer(time.monotonic())
            unsub_ref = cov_simple._unconfirmed_move_unsub
            cov_simple._unconfirmed_move_unsub = None
            cov_simple._run_generation += 1
            assert unsub_ref is not None
            # Fire timer -> returns cleanly on generation mismatch
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
            await hass.async_block_till_done()

            # - cancelling an armed _unconfirmed_move_unsub BEFORE timer fires
            cov_simple._begin_run("open")
            cov_simple._arm_unconfirmed_move_timer(time.monotonic() + 10.0)
            assert cov_simple._unconfirmed_move_unsub is not None
            cov_simple._cancel_unconfirmed_move_unsub()
            assert cov_simple._unconfirmed_move_unsub is None

            # - cancelling an in-flight _unconfirmed_move_task on entity removal
            slow_probe: asyncio.Future[float] = asyncio.get_running_loop().create_future()
            with patch.object(handler, "send_status_request", AsyncMock(return_value=slow_probe)):
                cov_simple._begin_run("open")
                cov_simple._arm_unconfirmed_move_timer(time.monotonic())
                async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
                await asyncio.sleep(0)
                probe_task = cov_simple._unconfirmed_move_task
                assert probe_task is not None
                cov_simple._cancel_unconfirmed_move_timer()
                await asyncio.gather(probe_task, return_exceptions=True)
                assert cov_simple._unconfirmed_move_task is None
                slow_probe.cancel()

            # - non-Future legacy gateway send() when cover is excluded skips _arm_relay_hold
            cov_empty._relay_hold_until = None
            cov_empty._pending_cmd = "open"
            cov_empty._track_write(True)
            assert cov_empty._relay_hold_until is None
        finally:
            handler._command_pool.close()
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker


# ---------------------------------------------------------------------------
# 8. Behind-F422 Scope Fan-Out & Local Venetian Scope Pulse Exclusion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f422_scope_fan_out_and_local_venetian_pulse_exclude_locked_members(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify F422 scope fan-out skips locked members and never falls through when all are locked."""
    monkeypatch.setattr("custom_components.myhome.cover_scope.PACED_FANOUT_DELAY", 0.0)
    _, handler = _make_real_gateway(hass, model="F461")

    family = CoverFamily()

    def _mk_member(where: str, iface: str | None) -> MyHOMECover:
        key = f"{where}#4#{iface}" if iface else where
        c = MyHOMECover(
            hass=hass,
            name=f"Member {key}",
            entity_name=None,
            device_id=key,
            who="2",
            where=where,
            interface=iface,
            advanced=False,
            slat_tilt=True,
            manufacturer="BTicino",
            model="F411/2",
            gateway=handler,
            travel_time=10,
        )
        c.hass = hass
        c.entity_id = f"cover.m_{key.replace('#', '_')}"
        c.async_on_remove = MagicMock()
        c.async_write_ha_state = MagicMock()
        c.async_schedule_update_ha_state = MagicMock()
        bind_entity(hass, c, gateway=handler)
        family.add(c)
        return c

    # 1. Behind F422 (#1#4#02): m11 locked (position None/unknown!), m12 unlocked (position 100, tilt 0)
    m11 = _mk_member("11", "02")
    m12 = _mk_member("12", "02")
    m12._attr_current_cover_position = 100
    m12._start_position = 100
    m12._attr_current_cover_tilt_position = 0
    m11.handle_actuator_lock(True)

    scope_if02 = CoverScope.of("#1", "02", ("11", "12"))
    assert scope_if02 is not None
    grp_if02 = MyHOMEScopeCover(
        hass=hass,
        name="Group #1 Bus 02",
        entity_name=None,
        device_id="#1#4#02",
        who="2",
        where="#1",
        interface="02",
        advanced=False,
        slat_tilt=True,
        manufacturer="BTicino",
        model="Group",
        gateway=handler,
        travel_time=10,
        scope=scope_if02,
    )
    grp_if02.hass = hass
    grp_if02.entity_id = "cover.grp_1_02"
    grp_if02.async_on_remove = MagicMock()
    grp_if02.async_write_ha_state = MagicMock()
    grp_if02.async_schedule_update_ha_state = MagicMock()
    family.add(grp_if02)

    monkeypatch.setattr("custom_components.myhome.cover_scope.PACED_FANOUT_DELAY", 0.001)
    m11._attr_current_cover_tilt_position = 0
    with (
        patch.object(m11, "async_open_cover", AsyncMock()) as m11_open,
        patch.object(m12, "async_open_cover", AsyncMock()) as m12_open,
        patch.object(m11, "async_close_cover", AsyncMock()) as m11_close,
        patch.object(m12, "async_close_cover", AsyncMock()) as m12_close,
        patch.object(m11, "async_set_cover_position", AsyncMock()) as m11_pos,
        patch.object(m12, "async_set_cover_position", AsyncMock()) as m12_pos,
        patch.object(m11, "async_stop_cover", AsyncMock()) as m11_stop,
        patch.object(m12, "async_stop_cover", AsyncMock()) as m12_stop,
        patch.object(m11, "async_set_cover_tilt_position", AsyncMock()) as m11_tilt,
        patch.object(m12, "async_set_cover_tilt_position", AsyncMock()) as m12_tilt,
        patch.object(m11, "_async_wait_pulse_end", AsyncMock()),
        patch.object(m12, "_async_wait_pulse_end", AsyncMock()),
        patch.object(m11, "async_stop_cover_tilt", AsyncMock()) as m11_stop_tilt,
        patch.object(m12, "async_stop_cover_tilt", AsyncMock()) as m12_stop_tilt,
    ):
        # Audit #977 item 3a: #4#II fan-outs DO transmit point commands to locked members
        # so offline-unlocked actuators self-heal (only partial 0<pos<100 skips an unknown-pos
        # excluded member so unlocked peers are not blocked by ServiceValidationError).
        await grp_if02.async_open_cover()
        await grp_if02.async_close_cover()
        await grp_if02.async_set_cover_position(**{ATTR_POSITION: 50})
        await grp_if02.async_stop_cover()
        await grp_if02.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
        await grp_if02.async_stop_cover_tilt()

        m11_open.assert_awaited_once()
        m12_open.assert_awaited_once()
        m11_close.assert_awaited_once()
        m12_close.assert_awaited_once()
        m11_pos.assert_not_awaited()  # unknown pos on partial 50% skipped so m12 runs!
        m12_pos.assert_awaited_once()
        m11_stop.assert_awaited_once()
        m12_stop.assert_awaited_once()
        m11_tilt.assert_awaited_once()
        m12_tilt.assert_awaited_once()
        m11_stop_tilt.assert_awaited_once()
        m12_stop_tilt.assert_awaited_once()

        # With known position on m11, partial set_cover_position(50) fans out to BOTH m11 and m12!
        m11._attr_current_cover_position = 80
        m11._start_position = 80
        await grp_if02.async_set_cover_position(**{ATTR_POSITION: 50})
        m11_pos.assert_awaited_once()
        assert m12_pos.await_count == 2

        # Unknown tilt on excluded m11: partial set_cover_tilt_position(50) skips m11 so m12 tilts,
        # while full-end 100 fans out to BOTH m11 and m12!
        m11_tilt.reset_mock()
        m12_tilt.reset_mock()
        m11._attr_current_cover_tilt_position = None
        await grp_if02.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
        m11_tilt.assert_not_awaited()
        m12_tilt.assert_awaited_once()
        await grp_if02.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 100})
        m11_tilt.assert_awaited_once()
        assert m12_tilt.await_count == 2
        m11._attr_current_cover_tilt_position = 0

        # Mid-fanout gateway unload (_terminate_sender=True) aborts paced loops after member 0!
        def _abort_on_first(*_a: Any, **_k: Any) -> None:
            handler._terminate_sender = True

        for fn_call, first_mock, second_mock in (
            (grp_if02.async_open_cover, m11_open, m12_open),
            (grp_if02.async_close_cover, m11_close, m12_close),
            (grp_if02.async_stop_cover, m11_stop, m12_stop),
            (lambda: grp_if02.async_set_cover_position(**{ATTR_POSITION: 50}), m11_pos, m12_pos),
            (lambda: grp_if02.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50}), m11_tilt, m12_tilt),
        ):
            handler._terminate_sender = False
            first_mock.reset_mock()
            second_mock.reset_mock()
            first_mock.side_effect = _abort_on_first
            await fn_call()
            first_mock.assert_awaited_once()
            second_mock.assert_not_awaited()
            first_mock.side_effect = None
        handler._terminate_sender = False

    # Lock m12 too: ALL behind-F422 members send point commands (*2*.*11#4#02## / 12#4#02##)
    # with 0% drift while locked, and NEVER emit a local/scope #1 frame!
    monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.05)
    m12.handle_actuator_lock(True)
    sent_raw: list[str] = []

    async def _rec_send(cmd: Any, **_kw: Any) -> asyncio.Future[float]:
        sent_raw.append(str(cmd))
        f: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        f.set_result(time.monotonic())
        return f

    with patch.object(handler, "send", AsyncMock(side_effect=_rec_send)):
        await grp_if02.async_close_cover()
        for m in (m11, m12):
            if m._stop_task is not None:
                await asyncio.wait_for(m._stop_task, timeout=2.0)
        assert sent_raw == ["*2*2*11#4#02##", "*2*2*12#4#02##"]
        assert m11.current_cover_position == 80
        assert m12.current_cover_position == 100

        # Offline power-cycle unlock on m11: #1#4#02 fan-out self-heals m11 on *2*2*11#4#02## echo!
        monkeypatch.setattr(cover_module, "ECHO_WINDOW", 1.5)
        sent_raw.clear()
        await grp_if02.async_close_cover()
        m11.handle_event(OWNEvent.parse("*2*2*11#4#02##"))
        assert m11.actuator_locked is False
        assert m11.is_closing is True
        for m in (m11, m12):
            m._abort_motion()

    # 2. Local Venetian scope pulse (_pulse_followers) skips locked member 21 while tilting unlocked 22
    loc21 = _mk_member("21", None)
    loc22 = _mk_member("22", None)
    loc21._attr_current_cover_tilt_position = 0
    loc22._attr_current_cover_tilt_position = 0
    loc21.handle_actuator_lock(True)

    scope_loc = CoverScope.of("2", None)
    assert scope_loc is not None
    area_2 = MyHOMEScopeCover(
        hass=hass,
        name="Area 2",
        entity_name=None,
        device_id="2",
        who="2",
        where="2",
        interface=None,
        advanced=False,
        slat_tilt=True,
        manufacturer="BTicino",
        model="Area",
        gateway=handler,
        travel_time=10,
        scope=scope_loc,
    )
    area_2.hass = hass
    area_2.entity_id = "cover.area_2"
    area_2.async_on_remove = MagicMock()
    area_2.async_write_ha_state = MagicMock()
    area_2.async_schedule_update_ha_state = MagicMock()
    family.add(area_2)

    fut_w: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    fut_w.set_result(time.monotonic())
    with (
        patch.object(handler, "send", AsyncMock(return_value=fut_w)),
        patch("custom_components.myhome.cover.asyncio.sleep", new_callable=AsyncMock),
    ):
        await area_2.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
        assert loc21._is_tilting is False
        assert loc21._tilt_follows is None
        assert loc22._is_tilting is True
        assert loc22._tilt_follows is area_2
        if area_2._stop_task is not None:
            await area_2._stop_task
    assert loc21.current_cover_tilt_position == 0
    assert loc22.current_cover_tilt_position == 50


@pytest.mark.asyncio
async def test_audit_items_6_8_9_10_area0_canonical_advanced_restore_short_runs_and_sensor_impulse(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit #977 items 6, 8, 9, 10: 0004/04, advanced restore, short runs & scope end-stop, state_sensor gate."""
    _entry, handler = _make_real_gateway(hass, mac="00:03:50:97:60:99", model="F461")

    # 1. Item 6: Cover configured as '0004' matches '04' bus frames & CoverScope ('00' and '#1')
    cov_0004 = MyHOMECover(
        hass=hass,
        name="Area0 Shutter",
        entity_name=None,
        device_id="0004",
        who="2",
        where="0004",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="F411/2",
        gateway=handler,
        travel_time=10,
    )
    cov_0004.hass = hass
    cov_0004.entity_id = "cover.area0_04"
    cov_0004.async_write_ha_state = MagicMock()
    cov_0004.async_schedule_update_ha_state = MagicMock()
    assert cov_0004._is_own_point_message(OWNEvent.parse("*2*1*04##")) is True
    assert cov_0004._is_own_point_message(OWNEvent.parse("*2*1*0##")) is False
    assert cov_0004._is_own_point_message(OWNEvent.parse("*2*1*#1##")) is False
    assert cov_0004._is_own_point_message(MagicMock(is_general=False, is_area=False, is_group=False, where="0", interface=None)) is False
    assert cov_0004._is_own_point_message(MagicMock(is_general=False, is_area=False, is_group=False, where="#1", interface=None)) is False
    sc_grp = CoverScope.of("#1", None, ("0004",))
    sc_area = CoverScope.of("00", None)
    assert sc_grp is not None and sc_grp.contains(cov_0004) is True
    assert sc_area is not None and sc_area.contains(cov_0004) is True

    # 2. Item 8: advanced=True cover restores actuator_locked without restoring stale position
    adv_cov = MyHOMECover(
        hass=hass,
        name="Advanced 21",
        entity_name=None,
        device_id="21",
        who="2",
        where="21",
        interface=None,
        advanced=True,
        manufacturer="BTicino",
        model="F401",
        gateway=handler,
    )
    adv_cov._poll_on_add = False
    adv_cov.entity_id = "cover.adv_21"
    with patch.object(
        adv_cov,
        "async_get_last_state",
        AsyncMock(return_value=State("cover.adv_21", "open", {ATTR_CURRENT_POSITION: 75, "actuator_locked": True})),
    ):
        await adv_cov.async_added_to_hass()
    assert adv_cov.actuator_locked is True
    assert adv_cov.current_cover_position is None
    assert handler._actuator_locks.is_locked("21") is True

    # 3. Item 9: Short (<= ECHO_WINDOW) cover runs do NOT latch _actuation_unconfirmed,
    # actuation_unconfirmed is surfaced in extra_state_attributes, and clears on WHO_2 §3.0.1 scope end-stop!
    monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.50)
    cov_short = MyHOMECover(
        hass=hass,
        name="Short 35",
        entity_name=None,
        device_id="35",
        who="2",
        where="35",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="F411/2",
        gateway=handler,
        travel_time=10,
        min_motor_pulse=0.1,
    )
    cov_short.hass = hass
    cov_short.entity_id = "cover.short_35"
    cov_short.async_write_ha_state = MagicMock()
    cov_short.async_schedule_update_ha_state = MagicMock()
    bind_entity(hass, cov_short, gateway=handler)
    cov_short._register_availability_listener()

    session = _FakeCommandSession(
        lambda raw, is_req: [OWNMessage.parse("*2*0*35##")] if is_req else True
    )
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        worker = asyncio.create_task(handler.sending_loop(0))
        try:
            cov_short._attr_current_cover_position = 50
            cov_short._start_position = 50
            await cov_short.async_set_cover_position(**{ATTR_POSITION: 53})
            assert cov_short._stop_task is not None
            await asyncio.wait_for(cov_short._stop_task, timeout=2.0)
            await _wait_for_tx(handler, session, 2, hass)
            assert cov_short._actuation_unconfirmed is False
            assert "actuation_unconfirmed" not in cov_short.extra_state_attributes

            session.sent_frames.clear()
            cov_short._attr_current_cover_position = 97
            cov_short._start_position = 97
            await cov_short.async_open_cover()
            assert cov_short._stop_task is not None
            await asyncio.wait_for(cov_short._stop_task, timeout=2.0)
            await _wait_for_tx(handler, session, 2, hass)
            assert cov_short._actuation_unconfirmed is False

            session.sent_frames.clear()
            cov_short._attr_current_cover_position = None
            cov_short._start_position = None
            cov_short._travel_time_up = 0.4
            await cov_short.async_open_cover()
            assert cov_short._stop_task is not None
            await asyncio.wait_for(cov_short._stop_task, timeout=2.0)
            await _wait_for_tx(handler, session, 2, hass)
            assert cov_short._actuation_unconfirmed is False
            cov_short._travel_time_up = 10.0

            monkeypatch.setattr(cover_module, "_EMPTY_PROBE_GRACE_S", 0.01)
            session._responder = lambda _raw, _is_req: True
            session.sent_frames.clear()
            cov_short._attr_current_cover_position = 99
            cov_short._start_position = 99
            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.20)
            await cov_short.async_open_cover()
            assert cov_short._stop_task is not None
            await asyncio.wait_for(cov_short._stop_task, timeout=2.0)
            assert cov_short._actuation_unconfirmed is False

            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.05)
            cov_short._attr_current_cover_position = 0
            cov_short._start_position = 0
            await cov_short.async_open_cover()
            assert cov_short._stop_task is not None
            await asyncio.wait_for(cov_short._stop_task, timeout=2.0)
            assert cov_short._actuation_unconfirmed is True
            assert cov_short.extra_state_attributes["actuation_unconfirmed"] is True

            cov_short._scope_run_active_until = time.monotonic() - 1.0
            cov_short.handle_event(OWNEvent.parse("*2*0*35##"))
            assert cov_short._actuation_unconfirmed is True

            cov_short.handle_event(OWNEvent.parse("*2*1*0##"))
            assert cov_short._scope_run_active_until is not None
            handler._active_sweeps = 1
            cov_short.handle_event(OWNEvent.parse("*2*0*35##"))
            assert cov_short._actuation_unconfirmed is True
            handler._active_sweeps = 0
            poll_stop_35 = OWNEvent.parse("*2*0*35##")
            setattr(poll_stop_35, "_from_status_request", True)
            cov_short.handle_event(poll_stop_35)
            assert cov_short._actuation_unconfirmed is True
            cov_short.handle_event(OWNEvent.parse("*2*0*35##"))
            assert cov_short._actuation_unconfirmed is False
            assert "actuation_unconfirmed" not in cov_short.extra_state_attributes

            # When a cover already has _actuator_locked == False (e.g. from a prior Unlock or
            # RestoreEntity) and is then locked externally by an un-relayed wall scenario on MH201,
            # an un-echoed P2P move sets _actuation_unconfirmed = True while _actuator_locked stays False;
            # subsequent explicit WHO=14 Unlock (*14*1*35##) or Dim 10 SHUTTER_INFO=0/15 MUST clear
            # _actuation_unconfirmed / _scope_run_active_until and publish updated state!
            assert handler._actuator_locks.observe(OWNActuatorLockCommand.parse("*14*1*35##")) is True
            assert cov_short.actuator_locked is False
            await cov_short.async_open_cover()
            assert cov_short._stop_task is not None
            await asyncio.wait_for(cov_short._stop_task, timeout=2.0)
            assert cov_short.actuator_locked is False
            assert cov_short._actuation_unconfirmed is True
            assert cov_short.extra_state_attributes["actuation_unconfirmed"] is True
            assert cov_short._is_scope_excluded() is True
            cov_short.handle_event(OWNEvent.parse("*2*1*0##"))
            assert cov_short._scope_run_active_until is not None
            cov_short.async_schedule_update_ha_state.reset_mock()

            assert handler._actuator_locks.observe(OWNActuatorLockCommand.parse("*14*1*35##")) is True
            assert cov_short.actuator_locked is False
            assert cov_short._actuation_unconfirmed is False
            assert cov_short._scope_run_active_until is None
            assert "actuation_unconfirmed" not in cov_short.extra_state_attributes
            assert cov_short._is_scope_excluded() is False
            assert cov_short.async_schedule_update_ha_state.call_count == 1
            # Duplicate unlock when already cleared is idempotent (0 extra publishes)
            assert handler._actuator_locks.observe(OWNActuatorLockCommand.parse("*14*1*35##")) is True
            assert cov_short.async_schedule_update_ha_state.call_count == 1

            # Same recovery via Dim 10 SHUTTER_INFO=0 -> set_point_lock(..., False, observed=True)
            await cov_short.async_open_cover()
            assert cov_short._stop_task is not None
            await asyncio.wait_for(cov_short._stop_task, timeout=2.0)
            assert cov_short.actuator_locked is False
            assert cov_short._actuation_unconfirmed is True
            cov_short.async_schedule_update_ha_state.reset_mock()
            cov_short.handle_event(OWNEvent.parse("*#2*35*10*10*50*001*0##"))
            assert cov_short.actuator_locked is False
            assert cov_short._actuation_unconfirmed is False
            assert "actuation_unconfirmed" not in cov_short.extra_state_attributes
            assert cov_short._is_scope_excluded() is False
            assert cov_short.async_schedule_update_ha_state.call_count >= 1
            cov_short._attr_current_cover_position = 0
            cov_short._start_position = 0

            # Short partial set_cover_position(53) where *2*0*35## arrives AFTER ECHO_WINDOW while
            # awaiting handler.send() neither latches _actuation_unconfirmed nor spawns _auto_stop!
            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.50)
            cov_short._attr_current_cover_position = 50
            cov_short._start_position = 50

            async def _slow_send_with_stop(_cmd: Any) -> asyncio.Future[float]:
                cov_short._echo_until = time.monotonic() - 1.0
                cov_short.handle_event(OWNEvent.parse("*2*0*35##"))
                fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
                fut.set_result(time.monotonic())
                return fut

            with patch.object(handler, "send", AsyncMock(side_effect=_slow_send_with_stop)):
                await cov_short.async_set_cover_position(**{ATTR_POSITION: 53})
            assert cov_short._actuation_unconfirmed is False
            assert cov_short._stop_task is None

            # 4. Item 10: MyHOMEImpulseCover WITH state_sensor suppresses optimistic motion when locked
            hass.states.async_set("binary_sensor.gate_closed", "off")
            gate_s = MyHOMEImpulseCover(
                hass=hass,
                name="Sensor Gate 71",
                entity_name=None,
                device_id="71",
                who="1",
                where="71",
                interface=None,
                device_class=CoverDeviceClass.GATE,
                travel_time=5.0,
                pulse_duration=0.5,
                min_cycle_time=0.0,
                pin_code=None,
                state_sensor="binary_sensor.gate_closed",
                manufacturer="BTicino",
                model="Impulse",
                gateway=handler,
            )
            gate_s.hass = hass
            gate_s.entity_id = "cover.sensor_gate_71"
            gate_s.async_write_ha_state = MagicMock()
            gate_s.async_schedule_update_ha_state = MagicMock()
            await gate_s.async_added_to_hass()
            assert gate_s.is_closed is True

            handler._actuator_locks.set_point_lock("71", None, True, observed=True)
            assert gate_s.actuator_locked is True

            await gate_s.async_open_cover()
            assert gate_s.is_opening is False
            assert gate_s.is_closed is True
            assert gate_s._travel_timer_cancel is None

            gate_s.handle_event(OWNEvent.parse("*1*1*0##"))
            gate_s.handle_event(OWNEvent.parse("*1*1*#1##"))
            gate_s.handle_event(MagicMock(is_translation=False, what=1, is_on=True, is_general=False, is_area=False, is_group=False, who=13, where=None))
            assert gate_s.actuator_locked is True
            assert gate_s.is_opening is False

            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 1.5)
            gate_s._last_pulse_time = -1e9
            await gate_s.async_open_cover()
            gate_s.handle_event(OWNEvent.parse("*1*18*71##"))
            assert gate_s.actuator_locked is False
            assert gate_s.is_opening is True
            assert gate_s.is_closed is False
            gate_s._cancel_travel_timer()

            # 5. F422 MyHOMEImpulseCover configured with 1-digit bus_interface ("1") canonicalizes
            # #4#1 <-> #4#01 (and 0004 <-> 04) so locked P2P *1*1*04#4#01## echo self-heals!
            gate_f422 = MyHOMEImpulseCover(
                hass=hass,
                name="Riser Gate 04",
                entity_name=None,
                device_id="0004#4#1",
                who="1",
                where="0004",
                interface="1",
                device_class=CoverDeviceClass.GATE,
                travel_time=5.0,
                pulse_duration=0.5,
                min_cycle_time=0.0,
                pin_code=None,
                state_sensor=None,
                manufacturer="BTicino",
                model="Impulse",
                gateway=handler,
            )
            gate_f422.hass = hass
            gate_f422.entity_id = "cover.riser_gate_04"
            gate_f422.async_write_ha_state = MagicMock()
            gate_f422.async_schedule_update_ha_state = MagicMock()
            await gate_f422.async_added_to_hass()

            assert handler._actuator_locks.observe(OWNActuatorLockCommand.parse("*14*0*04#4#01##")) is True
            assert gate_f422.actuator_locked is True

            gate_f422._last_pulse_time = -1e9
            await gate_f422.async_open_cover()
            assert gate_f422.is_opening is False

            # Wrong interface, main bus, and #4#01 scope broadcasts stay ignored while locked
            for non_matching in (
                "*1*1*04#4#02##",
                "*1*1*04##",
                "*1*1*0#4#01##",
                "*1*1*00#4#01##",
                "*1*1*#1#4#01##",
            ):
                gate_f422.handle_event(OWNEvent.parse(non_matching))
            assert gate_f422.actuator_locked is True
            assert gate_f422.is_opening is False

            # Own P2P wire echo *1*1*04#4#01## (2-digit #4#01 vs 1-digit interface="1") self-heals!
            gate_f422.handle_event(OWNEvent.parse("*1*1*04#4#01##"))
            assert gate_f422.actuator_locked is False
            assert handler._actuator_locks.is_locked("04", "01") is False
            assert gate_f422.is_opening is True
            gate_f422._cancel_travel_timer()

            # Reverse direction: 2-digit interface="01" (with legacy 1- prefix) registers with
            # ActuatorLockManager, transmits canonical 04#4#01, matches *1*1*04#4#1##, and handles
            # event objects providing .interface without where_param!
            assert gate_f422._full_where == "04#4#01"
            assert gate_f422.extra_state_attributes["interface"] == "1"
            gate_f422_rev = MyHOMEImpulseCover(
                hass=hass,
                name="Riser Gate Rev",
                entity_name=None,
                device_id="1-04#4#01",
                who="1",
                where="1-04",
                interface="01",
                device_class=CoverDeviceClass.GATE,
                travel_time=5.0,
                pulse_duration=0.5,
                min_cycle_time=0.0,
                pin_code=None,
                state_sensor=None,
                manufacturer="BTicino",
                model="Impulse",
                gateway=handler,
            )
            gate_f422_rev.hass = hass
            gate_f422_rev.entity_id = "cover.riser_gate_rev"
            gate_f422_rev.async_write_ha_state = MagicMock()
            gate_f422_rev.async_schedule_update_ha_state = MagicMock()
            await gate_f422_rev.async_added_to_hass()
            assert gate_f422_rev._full_where == "04#4#01"
            assert handler._actuator_locks.observe(OWNActuatorLockCommand.parse("*14*0*04#4#01##")) is True
            assert gate_f422_rev.actuator_locked is True
            assert gate_f422_rev._is_own_impulse_event(OWNEvent.parse("*1*1*04#4#1##")) is True
            assert (
                gate_f422_rev._is_own_impulse_event(
                    SimpleNamespace(who=1, where="04", interface="1", where_param=[])
                )
                is True
            )
            assert (
                gate_f422_rev._is_own_impulse_event(
                    SimpleNamespace(who=1, where="04", interface="02", where_param=[])
                )
                is False
            )
            gate_f422_rev.handle_event(OWNEvent.parse("*1*1*04#4#1##"))
            assert gate_f422_rev.actuator_locked is False
            gate_f422_rev._cancel_travel_timer()

            # 6. handle_actuator_lock(True) when _actuator_locked is ALREADY True (e.g. soft-restored)
            # immediately aborts an in-flight Venetian slat tilt / calibration / motion, while staying
            # a 0-publish idempotent no-op when completely idle!
            cov_tilt = MyHOMECover(
                hass=hass,
                name="Venetian 36",
                entity_name="Venetian 36",
                device_id="36",
                who="2",
                where="36",
                interface=None,
                advanced=False,
                slat_tilt=True,
                slat_time=1.5,
                manufacturer="BTicino",
                model="F411/2",
                gateway=handler,
                travel_time=10,
            )
            cov_tilt.hass = hass
            cov_tilt.entity_id = "cover.venetian_36"
            cov_tilt.async_write_ha_state = MagicMock()
            cov_tilt.async_schedule_update_ha_state = MagicMock()
            bind_entity(hass, cov_tilt, gateway=handler)
            await cov_tilt.async_added_to_hass()
            cov_tilt._attr_current_cover_position = 50
            cov_tilt._start_position = 50
            cov_tilt._attr_current_cover_tilt_position = 0
            assert handler._actuator_locks.observe(OWNActuatorLockCommand.parse("*14*0*36##")) is True
            assert cov_tilt.actuator_locked is True
            cov_tilt.async_schedule_update_ha_state.reset_mock()
            # Duplicate idle lock is a 0-publish no-op
            assert handler._actuator_locks.observe(OWNActuatorLockCommand.parse("*14*0*36##")) is True
            assert cov_tilt.async_schedule_update_ha_state.call_count == 0
            # Start tilt while locked -> mid-ECHO_WINDOW duplicate *14*0*36## aborts tilt immediately!
            await cov_tilt.async_set_cover_tilt_position(tilt_position=100)
            assert cov_tilt._is_tilting is True
            assert cov_tilt._stop_task is not None
            cov_tilt.async_schedule_update_ha_state.reset_mock()
            assert handler._actuator_locks.observe(OWNActuatorLockCommand.parse("*14*0*36##")) is True
            assert cov_tilt._is_tilting is False
            assert cov_tilt._attr_current_cover_tilt_position == 0
            assert cov_tilt.async_schedule_update_ha_state.call_count == 1
            # Also interrupts active calibration when _actuator_locked is already True
            cov_tilt._calibrating = True
            cov_tilt._calibration_interrupted = None
            cov_tilt.async_schedule_update_ha_state.reset_mock()
            cov_tilt.handle_actuator_lock(True)
            assert cov_tilt._calibration_interrupted == "actuator locked (WHO=14)"
            assert cov_tilt.async_schedule_update_ha_state.call_count == 1
            cov_tilt._calibrating = False

            # 7. Concurrent async_set_cover_position / _async_run_to_end / _async_start_tilt calls
            # isolate each call's generation via _gen_out instead of a shared _last_move_generation!
            cov_race = MyHOMECover(
                hass=hass,
                name="Race 37",
                entity_name="Race 37",
                device_id="37",
                who="2",
                where="37",
                interface=None,
                advanced=False,
                slat_tilt=True,
                slat_time=1.5,
                manufacturer="BTicino",
                model="F411/2",
                gateway=handler,
                travel_time=10,
            )
            cov_race.hass = hass
            cov_race.entity_id = "cover.race_37"
            cov_race.async_write_ha_state = MagicMock()
            cov_race.async_schedule_update_ha_state = MagicMock()
            cov_race._attr_current_cover_position = 0
            cov_race._start_position = 0
            cov_race._attr_current_cover_tilt_position = 0
            orig_send = handler.send
            gate_send_a = asyncio.Event()
            entered_send_a = asyncio.Event()
            call_idx = 0

            async def _interleaved_send(cmd: Any) -> Any:
                nonlocal call_idx
                call_idx += 1
                if call_idx == 1:
                    entered_send_a.set()
                    await gate_send_a.wait()
                fut: asyncio.Future[float] = hass.loop.create_future()
                fut.set_result(time.monotonic())
                return fut

            handler.send = _interleaved_send  # type: ignore[method-assign]
            try:
                task_a = asyncio.create_task(cov_race.async_set_cover_position(position=40))
                await entered_send_a.wait()
                await cov_race.async_set_cover_position(position=70)
                assert cov_race._timed_target_position == 70
                task_b_stop = cov_race._stop_task
                gate_send_a.set()
                await task_a
                # Call A must NOT overwrite Call B's _timed_target_position or _stop_task!
                assert cov_race._timed_target_position == 70
                assert cov_race._stop_task is task_b_stop
                cov_race._cancel_stop_task()

                # Same supersede guard in _async_run_to_end and _async_start_tilt
                call_idx = 0
                gate_send_a = asyncio.Event()
                entered_send_a = asyncio.Event()
                task_end_a = asyncio.create_task(cov_race._async_run_to_end(True, pre_stopped=True))
                await entered_send_a.wait()
                cov_race._run_generation += 1
                gate_send_a.set()
                await task_end_a
                assert cov_race._stop_task is None

                call_idx = 0
                gate_send_a = asyncio.Event()
                entered_send_a = asyncio.Event()
                cov_race._attr_is_opening = False
                cov_race._attr_is_closing = False
                task_tilt_a = asyncio.create_task(cov_race._async_start_tilt(100))
                await entered_send_a.wait()
                cov_race._run_generation += 1
                gate_send_a.set()
                await task_tilt_a
                assert cov_race._stop_task is None

                # Superseded _async_start_tilt raising an exception must NOT abort a newer active tilt!
                fail_tilt_a = False

                async def _interleaved_tilt_fail(cmd: Any) -> Any:
                    nonlocal call_idx
                    call_idx += 1
                    if call_idx == 1:
                        entered_send_a.set()
                        await gate_send_a.wait()
                        if fail_tilt_a:
                            raise HomeAssistantError("superseded tilt send failed")
                    fut2: asyncio.Future[float] = hass.loop.create_future()
                    fut2.set_result(time.monotonic())
                    return fut2

                handler.send = _interleaved_tilt_fail  # type: ignore[method-assign]
                call_idx = 0
                fail_tilt_a = True
                gate_send_a = asyncio.Event()
                entered_send_a = asyncio.Event()
                cov_race._abort_tilt()
                cov_race._attr_current_cover_tilt_position = 0
                task_tilt_err = asyncio.create_task(cov_race._async_start_tilt(100))
                await entered_send_a.wait()
                # Newer tilt supersedes Call 1 before Call 1's send raises
                await cov_race._async_start_tilt(50)
                assert cov_race._is_tilting is True
                active_tilt_stop = cov_race._stop_task
                assert active_tilt_stop is not None
                gate_send_a.set()
                with pytest.raises(HomeAssistantError, match="superseded tilt send failed"):
                    await task_tilt_err
                assert cov_race._is_tilting is True
                assert cov_race._stop_task is active_tilt_stop
                cov_race._cancel_stop_task()
                cov_race._abort_tilt()
            finally:
                handler.send = orig_send  # type: ignore[method-assign]
                await cov_tilt.async_will_remove_from_hass()
                await cov_race.async_will_remove_from_hass()
        finally:
            handler._command_pool.close()
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
