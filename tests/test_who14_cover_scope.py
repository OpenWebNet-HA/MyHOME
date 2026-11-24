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
            # aborts cleanly without setting _actuation_unconfirmed, P2P stop_cover() inside
            # ECHO_WINDOW on an unconfirmed move keeps 0% position drift, while a confirmed move freezes!
            cov_05._actuation_unconfirmed = False
            cov_05._begin_run("open")
            cov_05._echo_until = time.monotonic() - 1.0
            cov_05.handle_event(OWNEvent.parse("*2*1000#0*05##"))
            assert cov_05.is_opening is False
            assert cov_05._actuation_unconfirmed is False
            assert cov_05.current_cover_position == 80

            # Unconfirmed cover stopped inside ECHO_WINDOW before motor-start echo arrives -> 0% drift!
            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 1.5)
            session.sent_frames.clear()
            await cov_05.async_close_cover()
            await _wait_for_tx(handler, session, 1, hass)
            assert cov_05._provisional_write_ts is not None
            cov_05._provisional_write_ts = time.monotonic() - 2.0
            await cov_05.async_stop_cover()
            await _wait_for_tx(handler, session, 2, hass)
            assert cov_05.is_closing is False
            assert cov_05.current_cover_position == 80

            # Confirmed cover (*2*2*05## echoed) stopped inside ECHO_WINDOW freezes at interpolated %
            session.sent_frames.clear()
            await cov_05.async_close_cover()
            await _wait_for_tx(handler, session, 1, hass)
            cov_05.handle_event(OWNEvent.parse("*2*2*05##"))
            assert cov_05._move_confirmed is True
            cov_05._move_start_time = cov_05._run_started_at = time.monotonic() - 2.0
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
            # Immediate *2*0*W## inside ECHO_WINDOW (e.g. locked L4671/2 64 answering *2*2*6## on MH201)
            # must NOT clear _actuation_unconfirmed!
            cov_short.handle_event(OWNEvent.parse("*2*0*35##"))
            assert cov_short._actuation_unconfirmed is True
            await asyncio.sleep(0.06)
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

                # Mid-pulse WHO=14 / Dim10 SHUTTER_INFO=12 lock freezes interpolated angle and
                # claims the actuator's stopped-level report (0 -> 1) without snapping tilt to 100%!
                handler.send = orig_send  # type: ignore[method-assign]
                cov_f401 = MyHOMECover(
                    hass=hass,
                    name="Venetian F401 38",
                    entity_name=None,
                    device_id="38",
                    who="2",
                    where="38",
                    interface=None,
                    device_class=None,
                    advanced=True,
                    manufacturer="BTicino",
                    model="F401",
                    gateway=handler,
                    travel_time=20.0,
                    slat_tilt=True,
                    slat_time=2.0,
                )
                cov_f401.entity_id = "cover.venetian_f401_38"
                cov_f401._register_availability_listener()
                cov_f401.async_write_ha_state = MagicMock()
                cov_f401.async_schedule_update_ha_state = MagicMock()
                cov_f401.handle_event(OWNEvent.parse("*#2*38*10*10*0*001*15##"))
                assert cov_f401.current_cover_position == 0
                cov_f401._attr_current_cover_tilt_position = 0

                # (a) Unstarted pulse locked before _tilt_start_time is set -> aborts at 0%
                await cov_f401._async_start_tilt(50)
                cov_f401._tilt_start_time = None
                cov_f401.handle_actuator_lock(True)
                assert cov_f401._is_tilting is False
                assert cov_f401.current_cover_tilt_position == 0
                cov_f401.handle_actuator_lock(False)

                # (b) Started pulse locked 1.0s into a 2.0s slat rotation -> freezes at ~50%
                #     and claims *#2*38*10*10*1*001*12## (0 -> 1) instead of snapping to 100%!
                await cov_f401._async_start_tilt(80)
                cov_f401._tilt_start_time = time.monotonic() - 1.0
                cov_f401.handle_event(OWNEvent.parse("*#2*38*10*10*1*001*12##"))
                assert cov_f401.actuator_locked is True
                assert cov_f401._is_tilting is False
                assert cov_f401.current_cover_position == 1
                assert 35 <= (cov_f401.current_cover_tilt_position or 0) <= 65

                # (c) Scope-follower pulse locked mid-run picks up leader._tilt_start_time!
                cov_f401.handle_actuator_lock(False)
                cov_f401._attr_current_cover_tilt_position = 0
                fake_leader = SimpleNamespace(_tilt_start_time=time.monotonic() - 1.0, _pulse_written=None)
                cov_f401._follow_pulse(fake_leader, "open")  # type: ignore[arg-type]
                assert cov_f401._tilt_start_time is None
                cov_f401.handle_actuator_lock(True)
                assert 35 <= (cov_f401.current_cover_tilt_position or 0) <= 65

                # (d) _is_from_cover_poll ignores unmarked *99*1## *2*0*W## during in-flight *#2*W## poll or standby sweep
                cov_race._actuation_unconfirmed = True
                cov_race._note_scope_run_for_unconfirmed(time.monotonic(), True)
                handler._command_pool._in_flight_messages.append(OWNMessage.parse("*#2*37##"))
                try:
                    cov_race.handle_event(OWNEvent.parse("*2*0*37##"))
                finally:
                    handler._command_pool._in_flight_messages.clear()
                assert cov_race._actuation_unconfirmed is True
                fake_stb_cov = SimpleNamespace(sweep_active=True, _command_pool=SimpleNamespace(_in_flight_messages=[]))
                with patch.object(handler, "_get_standby_gateway", return_value=fake_stb_cov):
                    cov_race._note_scope_run_for_unconfirmed(time.monotonic(), True)
                    cov_race.handle_event(OWNEvent.parse("*2*0*37##"))
                assert cov_race._actuation_unconfirmed is True
                await cov_f401.async_will_remove_from_hass()
            finally:
                handler.send = orig_send  # type: ignore[method-assign]
                await cov_tilt.async_will_remove_from_hass()
                await cov_race.async_will_remove_from_hass()
        finally:
            handler._command_pool.close()
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker


@pytest.mark.asyncio
async def test_premerge_audit_cover_scope_unavailable_probe_autostop_venetian_dim10_and_access_gate(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify STATE_UNAVAILABLE restore, unconfirmed->confirmed _auto_stop, mid-await scope lock, unconfirmed stop/tilt, scope polls, Dim 10 INFO=14/12, and access: locked gate."""
    from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
    from homeassistant.core import Context
    from homeassistant.helpers.restore_state import RestoredExtraData

    from custom_components.myhome.access_policy import Phase
    from custom_components.myhome.cover_scope import CoverFamily, CoverScope, MyHOMEScopeCover

    entry, handler = _make_real_gateway(hass, mac="00:03:50:97:60:ee", model="MH201")
    handler._event_session_ready.set()

    # 1. CoverScope interface zero-padding ("2" vs "02")
    cov_if02 = MyHOMECover(
        hass=hass,
        name="Shutter 11#4#02",
        entity_name=None,
        device_id="11#4#02",
        who="2",
        where="11",
        interface="02",
        advanced=False,
        manufacturer="BTicino",
        model="F411/2",
        gateway=handler,
        travel_time=10,
    )
    sc_area_2 = CoverScope.of("1", "2")
    sc_grp_2 = CoverScope.of("#1", "2", ("11",))
    sc_gen_2 = CoverScope.of("0", "2")
    assert sc_area_2 is not None and sc_area_2.contains(cov_if02) is True
    assert sc_grp_2 is not None and sc_grp_2.contains(cov_if02) is True
    assert sc_gen_2 is not None and sc_gen_2.contains(cov_if02) is True
    fam_if = CoverFamily()
    cov_if02.async_on_remove = MagicMock()
    fam_if.add(cov_if02)
    grp_cov_2 = MyHOMEScopeCover(
        hass=hass,
        name="Group #1 if 2",
        entity_name=None,
        device_id="#1#4#02",
        who="2",
        where="#1",
        interface="2",
        advanced=False,
        manufacturer="BTicino",
        model="Group",
        gateway=handler,
        travel_time=10,
        scope=sc_grp_2,
    )
    grp_cov_2.async_on_remove = MagicMock()
    fam_if.add(grp_cov_2)
    assert fam_if.keys_moved_by("#1", "02") == ["11#4#02"]
    assert CoverScope.of("1", "") is not None
    assert CoverScope.of("1", True) is not None  # type: ignore[arg-type]

    # 2. STATE_UNAVAILABLE persistence on timed cover (suppressing MH201 _relay_hold_until),
    #    advanced cover (+ last_state is None vs unavailable), and MyHOMEImpulseCover!
    cov_timed = MyHOMECover(
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
        gateway=handler,
        travel_time=10,
    )
    cov_timed._poll_on_add = False
    cov_timed.entity_id = "cover.shutter_56"
    cov_timed._attr_current_cover_position = 40
    cov_timed._start_position = 40
    cov_timed._attr_is_closed = False
    cov_timed._motion_started_at = dt_util.utcnow().isoformat(timespec="milliseconds")
    cov_timed._register_availability_listener()
    handler._actuator_locks.set_point_lock("56", None, True, observed=True)
    saved_timed = cov_timed.extra_restore_state_data
    assert saved_timed is not None
    assert saved_timed.as_dict()["actuator_locked"] is True
    assert saved_timed.as_dict()["position"] == 40

    handler._actuator_locks.clear()
    cov_timed_restart = MyHOMECover(
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
        gateway=handler,
        travel_time=10,
    )
    cov_timed_restart._poll_on_add = False
    cov_timed_restart.entity_id = "cover.shutter_56"
    with (
        patch.object(
            cov_timed_restart,
            "async_get_last_state",
            AsyncMock(return_value=State("cover.shutter_56", STATE_UNAVAILABLE, {})),
        ),
        patch.object(
            cov_timed_restart,
            "async_get_last_extra_data",
            AsyncMock(return_value=RestoredExtraData(saved_timed.as_dict())),
        ),
    ):
        await cov_timed_restart.async_added_to_hass()
    assert cov_timed_restart.current_cover_position == 40
    assert cov_timed_restart.actuator_locked is True
    assert cov_timed_restart._relay_hold_until is None
    assert cov_timed_restart._stops_before_move(False) is False

    adv_57 = MyHOMECover(
        hass=hass,
        name="Advanced 57",
        entity_name=None,
        device_id="57",
        who="2",
        where="57",
        interface=None,
        advanced=True,
        manufacturer="BTicino",
        model="F401",
        gateway=handler,
    )
    adv_57._poll_on_add = False
    adv_57.entity_id = "cover.adv_57"
    with (
        patch.object(
            adv_57,
            "async_get_last_state",
            AsyncMock(return_value=State("cover.adv_57", STATE_UNAVAILABLE, {})),
        ),
        patch.object(
            adv_57,
            "async_get_last_extra_data",
            AsyncMock(
                return_value=RestoredExtraData(
                    {
                        "actuator_locked": True,
                        "last_run": {"seconds": 8.5, "direction": "open", "ended_at": "2026-11-24T12:00:00Z"},
                    }
                )
            ),
        ),
    ):
        await adv_57.async_added_to_hass()
    assert adv_57.actuator_locked is True
    assert adv_57.current_cover_position is None
    assert adv_57.extra_state_attributes["last_run_seconds"] == 8.5

    adv_58 = MyHOMECover(
        hass=hass,
        name="Advanced 58",
        entity_name=None,
        device_id="58",
        who="2",
        where="58",
        interface=None,
        advanced=True,
        manufacturer="BTicino",
        model="F401",
        gateway=handler,
    )
    adv_58._poll_on_add = False
    adv_58.entity_id = "cover.adv_58"
    with (
        patch.object(adv_58, "async_get_last_state", AsyncMock(return_value=None)),
        patch.object(
            adv_58,
            "async_get_last_extra_data",
            AsyncMock(
                return_value=RestoredExtraData(
                    {"last_run": {"seconds": 4.2, "direction": "close", "ended_at": "2026-11-24T12:00:00Z"}}
                )
            ),
        ),
    ):
        await adv_58.async_added_to_hass()
    assert adv_58.extra_state_attributes["last_run_seconds"] == 4.2

    # 3. Post-ECHO_WINDOW *#2*W## probe confirming motion (open AND close, plus sync non-Future probe)
    #    on a previously _actuation_unconfirmed cover preserves set_cover_position's _auto_stop!
    monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.05)
    monkeypatch.setattr(cover_module, "MOTOR_START_DELAY", 0.01)
    entry_pos, handler_pos = _make_real_gateway(
        hass,
        mac="00:03:50:97:60:ef",
        model="F455",
        platforms={
            PLATFORM: {
                "61": {CONF_WHERE: "61", CONF_NAME: "Pos 61", CONF_TRAVEL_TIME: 2},
            }
        },
    )
    entities_pos = await _setup_covers_on_gateway(hass, entry_pos, handler_pos)
    cov_pos = entities_pos["61"]
    assert isinstance(cov_pos, MyHOMECover)
    cov_pos._min_motor_pulse = 0.05

    probe_dir = "1"

    def _probe_moving_responder(raw: str, is_req: bool) -> Any:
        if is_req and raw == "*#2*61##":
            return [OWNMessage.parse(f"*2*{probe_dir}*61##")]
        return True

    sess_pos = _FakeCommandSession(_probe_moving_responder)
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=sess_pos):
        worker_pos = asyncio.create_task(handler_pos.sending_loop(0))
        try:
            cov_pos._attr_current_cover_position = 20
            cov_pos._start_position = 20
            cov_pos._actuation_unconfirmed = True
            probe_dir = "1"
            sess_pos.sent_frames.clear()
            await cov_pos.async_set_cover_position(**{ATTR_POSITION: 60})
            assert cov_pos._attr_is_opening is False
            assert cov_pos._stop_task is not None
            await asyncio.wait_for(cov_pos._stop_task, timeout=3.0)
            await _wait_for_tx(handler_pos, sess_pos, 3, hass)
            assert [f for f, _ in sess_pos.sent_frames] == ["*2*1*61##", "*#2*61##", "*2*0*61##"]
            assert cov_pos._actuation_unconfirmed is False
            assert 50 <= (cov_pos.current_cover_position or 0) <= 75

            cov_pos._clear_relay_hold()
            cov_pos._attr_current_cover_position = 70
            cov_pos._start_position = 70
            cov_pos._actuation_unconfirmed = True
            probe_dir = "2"
            sess_pos.sent_frames.clear()
            await cov_pos.async_set_cover_position(**{ATTR_POSITION: 30})
            assert cov_pos._attr_is_closing is False
            assert cov_pos._stop_task is not None
            await asyncio.wait_for(cov_pos._stop_task, timeout=3.0)
            await _wait_for_tx(handler_pos, sess_pos, 3, hass)
            assert [f for f, _ in sess_pos.sent_frames] == ["*2*2*61##", "*#2*61##", "*2*0*61##"]
            assert 15 <= (cov_pos.current_cover_position or 0) <= 40

            cov_pos._clear_relay_hold()
            orig_sr = handler_pos.send_status_request
            handler_pos.send_status_request = lambda _cmd: None  # type: ignore[method-assign]
            try:
                cov_pos._attr_current_cover_position = 30
                cov_pos._start_position = 30
                cov_pos._actuation_unconfirmed = True
                sess_pos.sent_frames.clear()
                await cov_pos.async_set_cover_position(**{ATTR_POSITION: 60})
                assert cov_pos._stop_task is not None
                await asyncio.wait_for(cov_pos._stop_task, timeout=3.0)
                await _wait_for_tx(handler_pos, sess_pos, 2, hass)
                assert [f for f, _ in sess_pos.sent_frames] == ["*2*1*61##", "*2*0*61##"]
            finally:
                handler_pos.send_status_request = orig_sr  # type: ignore[method-assign]
        finally:
            worker_pos.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker_pos

    sess_sc = _FakeCommandSession(lambda _raw, _is_req: True)
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=sess_sc):
        worker_pos = asyncio.create_task(handler.sending_loop(0))
        try:

            # 4. Mid-await scope lock & supersede races in MyHOMEScopeCover & MyHOMECover._async_stop
            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 1.5)
            m_a = MyHOMECover(
                hass=hass,
                name="Member 71",
                entity_name=None,
                device_id="71",
                who="2",
                where="71",
                interface=None,
                advanced=False,
                manufacturer="BTicino",
                model="F411/2",
                gateway=handler,
                travel_time=5.0,
            )
            m_b = MyHOMECover(
                hass=hass,
                name="Member 72",
                entity_name=None,
                device_id="72",
                who="2",
                where="72",
                interface=None,
                advanced=False,
                manufacturer="BTicino",
                model="F411/2",
                gateway=handler,
                travel_time=5.0,
            )
            fam_sc = CoverFamily()
            sc_7 = CoverScope.of("#7", None, ("71", "72"))
            assert sc_7 is not None
            grp_7 = MyHOMEScopeCover(
                hass=hass,
                name="Group #7",
                entity_name=None,
                device_id="#7",
                who="2",
                where="#7",
                interface=None,
                advanced=False,
                manufacturer="BTicino",
                model="Group",
                gateway=handler,
                travel_time=5.0,
                scope=sc_7,
            )
            for c_ent, eid in ((m_a, "cover.m_71"), (m_b, "cover.m_72"), (grp_7, "cover.grp_7")):
                c_ent.hass = hass
                c_ent.entity_id = eid
                c_ent.async_write_ha_state = MagicMock()
                c_ent.async_schedule_update_ha_state = MagicMock()
                c_ent._register_availability_listener()
                fam_sc.add(c_ent)
            m_a._attr_current_cover_position = m_a._start_position = 0
            m_b._attr_current_cover_position = m_b._start_position = 0

            orig_send_sc = handler.send

            async def _send_lock_71_mid_await(_cmd: Any) -> asyncio.Future[float]:
                handler._actuator_locks.set_point_lock("71", None, True, observed=True)
                fut = hass.loop.create_future()
                fut.set_result(time.monotonic())
                return fut

            handler.send = _send_lock_71_mid_await  # type: ignore[method-assign]
            await grp_7.async_open_cover()
            assert m_a.actuator_locked is True
            assert m_a._following_scope is False
            assert m_a._stop_task is None
            assert m_a.current_cover_position == 0
            assert m_b._following_scope is True
            assert m_b._stop_task is not None
            m_b._cancel_stop_task()
            m_b._abort_motion()
            m_b._clear_relay_hold()

            handler._actuator_locks.set_point_lock("71", None, False, observed=True)

            async def _send_lock_71_then_fail(_cmd: Any) -> asyncio.Future[float]:
                handler._actuator_locks.set_point_lock("71", None, True, observed=True)
                raise RuntimeError("scope send failed")

            handler.send = _send_lock_71_then_fail  # type: ignore[method-assign]
            with pytest.raises(RuntimeError, match="scope send failed"):
                await grp_7.async_open_cover()
            assert m_b.is_opening is False

            handler._actuator_locks.set_point_lock("71", None, False, observed=True)

            async def _send_supersede_scope(_cmd: Any) -> asyncio.Future[float]:
                grp_7._run_generation += 1
                grp_7._pending_cmd = None
                fut = hass.loop.create_future()
                fut.set_result(time.monotonic())
                return fut

            handler.send = _send_supersede_scope  # type: ignore[method-assign]
            await grp_7._async_run_to_end(True)
            assert m_a.is_opening is False
            assert grp_7._stop_task is None

            m_a._begin_run("open")
            m_a._move_confirmed = True
            m_a._anchor_run(time.monotonic())
            m_b._begin_run("open")
            m_b._move_confirmed = True
            m_b._anchor_run(time.monotonic())

            async def _send_supersede_m_a_on_scope_stop(_cmd: Any) -> asyncio.Future[float]:
                m_a._begin_run("close")
                fut = hass.loop.create_future()
                fut.set_result(time.monotonic())
                return fut

            handler.send = _send_supersede_m_a_on_scope_stop  # type: ignore[method-assign]
            await grp_7.async_stop_cover()
            await asyncio.sleep(0)
            assert m_a._pending_cmd == "close"
            assert m_b.is_opening is False
            m_a._abort_motion()
            m_a._clear_relay_hold()
            m_b._clear_relay_hold()

            m_b._begin_run("open")

            async def _send_fail_scope_stop(_cmd: Any) -> asyncio.Future[float]:
                raise RuntimeError("stop fail")

            handler.send = _send_fail_scope_stop  # type: ignore[method-assign]
            with pytest.raises(RuntimeError, match="stop fail"):
                await grp_7.async_stop_cover()

            m_b._begin_run("open")
            m_b._move_confirmed = True
            m_b._anchor_run(time.monotonic())

            async def _send_supersede_p2p_stop(_cmd: Any) -> None:
                m_b._begin_run("close")
                return None

            handler.send = _send_supersede_p2p_stop  # type: ignore[method-assign]
            await m_b.async_stop_cover()
            assert m_b._pending_cmd == "close"
            m_b._abort_motion()
            m_b._clear_relay_hold()

            # 5. Unconfirmed stop (_settle_unconfirmed_stop) across Venetian slats, simple cover,
            #    non-Future send(), and pre-write *2*1000#0*, *2*0*W##, and *2*0*#7## echoes!
            ven_73 = MyHOMECover(
                hass=hass,
                name="Venetian 73",
                entity_name=None,
                device_id="73",
                who="2",
                where="73",
                interface=None,
                advanced=False,
                manufacturer="BTicino",
                model="F411/2",
                gateway=handler,
                travel_time=10.0,
                slat_tilt=True,
                slat_time=1.5,
            )
            ven_73.hass = hass
            ven_73.entity_id = "cover.ven_73"
            ven_73.async_write_ha_state = MagicMock()
            ven_73.async_schedule_update_ha_state = MagicMock()
            ven_73._register_availability_listener()
            ven_73._attr_current_cover_position = ven_73._start_position = 50
            ven_73._attr_current_cover_tilt_position = 0

            handler.send = AsyncMock(return_value=None)  # type: ignore[method-assign]
            await ven_73.async_open_cover()
            await ven_73.async_stop_cover()
            assert ven_73.current_cover_position == 50
            assert ven_73.current_cover_tilt_position == 0

            simp_74 = MyHOMECover(
                hass=hass,
                name="Simple 74",
                entity_name=None,
                device_id="74",
                who="2",
                where="74",
                interface=None,
                advanced=False,
                manufacturer="BTicino",
                model="F411/2",
                gateway=handler,
                travel_time=0,
            )
            simp_74.hass = hass
            simp_74.entity_id = "cover.simp_74"
            simp_74.async_write_ha_state = MagicMock()
            simp_74.async_schedule_update_ha_state = MagicMock()
            simp_74._register_availability_listener()
            simp_74._attr_is_closed = True
            pending_stop_fut: asyncio.Future[float] = hass.loop.create_future()
            handler.send = AsyncMock(return_value=pending_stop_fut)  # type: ignore[method-assign]
            await simp_74.async_open_cover()
            await simp_74.async_stop_cover()
            simp_74.handle_event(OWNEvent.parse("*2*1000#0*74##"))
            assert simp_74.is_closed is True
            assert simp_74._ran_since_start is False

            simp_74._begin_run("open")
            simp_74._begin_stop()
            simp_74.handle_event(OWNEvent.parse("*2*0*74##"))
            assert simp_74.is_closed is True
            simp_74._begin_run("open")
            simp_74._begin_stop(scope_where="#7")
            simp_74.handle_event(OWNEvent.parse("*2*0*#7##"))
            assert simp_74.is_closed is True

            # 6. Unconfirmed Venetian slat pulse locked AFTER write_ts + MOTOR_START_DELAY -> 0% tilt advance!
            handler.send = orig_send_sc  # type: ignore[method-assign]
            ven_73._clear_relay_hold()
            ven_73._attr_current_cover_tilt_position = 0
            await ven_73._async_start_tilt(60)
            ven_73._tilt_start_time = time.monotonic() - 0.8
            ven_73.handle_actuator_lock(True)
            assert ven_73._is_tilting is False
            assert ven_73.current_cover_tilt_position == 0
            assert ven_73._pulse_ended_at is None
            ven_73.handle_actuator_lock(False)

            ven_73._begin_run("open", is_tilting=True)
            ven_73._tilt_direction = "open"
            ven_73._echo_until = time.monotonic() - 1.0
            ven_73.handle_event(OWNEvent.parse("*2*1*73##"))
            assert ven_73._move_confirmed is True
            ven_73._abort_tilt()
            assert ven_73._move_confirmed is False

            ven_73._begin_run("close", is_tilting=True)
            ven_73._tilt_direction = "close"
            ven_73._echo_until = time.monotonic() - 1.0
            ven_73.handle_event(OWNEvent.parse("*2*2*73##"))
            assert ven_73._move_confirmed is True
            ven_73._apply_tilt_stop(time.monotonic())
            assert ven_73._move_confirmed is False

            ven_73._move_confirmed = True
            ven_73._follow_pulse(grp_7, "open")
            assert ven_73._move_confirmed is False
            ven_73.handle_event(OWNEvent.parse("*#2*73*10*10*55*001*14##"))
            assert ven_73._is_tilting is False
            assert ven_73.current_cover_position == 55

            # 7. Dim 10 SHUTTER_INFO=14 preserves live position (is_closed branch + 255 position_unknown +
            #    non-advanced allow_auto_advanced=False + is_closed=None), and SHUTTER_INFO=12 on status=11!
            m_a._allow_auto_advanced = False
            m_a._attr_is_opening = True
            m_a._move_confirmed = False
            fake_info14_nonadv = SimpleNamespace(
                who=2,
                is_general=False,
                is_area=False,
                is_group=False,
                is_translation=False,
                where="71",
                interface=None,
                dimension=10,
                dimension_value=["10", "35", "001", "14"],
                current_position=35,
                is_position_unknown=False,
                is_opening=False,
                is_closing=False,
                is_closed=None,
                _what=None,
                human_readable_log="dim10 info 14 nonadv",
            )
            m_a.handle_event(fake_info14_nonadv)  # type: ignore[arg-type]
            assert m_a.current_cover_position == 35
            assert m_a._start_position == 35
            assert m_a._position_drift_bound == 0.0
            assert m_a.is_closed is False

            simp_74._begin_run("open")
            simp_74._arm_unconfirmed_move_timer(time.monotonic() - 2.0)
            probe_74_fut: asyncio.Future[float] = hass.loop.create_future()

            async def _async_sr_simp(_cmd: Any) -> asyncio.Future[float]:
                await asyncio.sleep(0)
                simp_74.handle_event(OWNEvent.parse("*2*1*74##"))
                probe_74_fut.set_result(time.monotonic())
                return probe_74_fut

            orig_sr_simp = handler.send_status_request
            handler.send_status_request = _async_sr_simp  # type: ignore[method-assign]
            try:
                async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
                if simp_74._unconfirmed_move_task is not None:
                    await asyncio.wait_for(simp_74._unconfirmed_move_task, timeout=2.0)
            finally:
                handler.send_status_request = orig_sr_simp  # type: ignore[method-assign]
            assert simp_74._unconfirmed_move_task is None
            simp_74._abort_motion()
            simp_74._clear_relay_hold()

            # Eager synchronous probe completion leaves _unconfirmed_move_task as None too!
            simp_74._begin_run("open")
            simp_74._arm_unconfirmed_move_timer(time.monotonic() - 2.0)
            handler.send_status_request = lambda _cmd: None  # type: ignore[method-assign]
            try:
                async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
                await asyncio.sleep(0)
            finally:
                handler.send_status_request = orig_sr_simp  # type: ignore[method-assign]
            assert simp_74._unconfirmed_move_task is None
            simp_74._abort_motion()
            simp_74._clear_relay_hold()

            adv_75 = MyHOMECover(
                hass=hass,
                name="Adv 75",
                entity_name=None,
                device_id="75",
                who="2",
                where="75",
                interface=None,
                advanced=True,
                manufacturer="BTicino",
                model="F401",
                gateway=handler,
            )
            adv_75.hass = hass
            adv_75.entity_id = "cover.adv_75"
            adv_75._register_availability_listener()
            adv_75._attr_current_cover_position = 80
            adv_75._attr_is_opening = True
            adv_75._move_confirmed = False
            info14_msg = OWNEvent.parse("*#2*75*10*10*50*001*14##")
            adv_75.handle_event(info14_msg)
            assert adv_75.is_opening is False
            assert adv_75.current_cover_position == 50
            assert adv_75.is_closed is False

            adv_75._attr_is_opening = True
            adv_75._move_confirmed = False
            fake_info14 = SimpleNamespace(
                is_general=False,
                is_area=False,
                is_group=False,
                is_translation=False,
                where="75",
                interface=None,
                dimension=10,
                dimension_value=["10", "0", "001", "14"],
                current_position=0,
                is_position_unknown=False,
                is_opening=False,
                is_closing=False,
                is_closed=True,
                _what=None,
                human_readable_log="dim10 info 14",
            )
            adv_75.handle_event(fake_info14)  # type: ignore[arg-type]
            assert adv_75.current_cover_position == 0
            assert adv_75.is_closed is True

            adv_75._attr_is_opening = True
            adv_75._move_confirmed = False
            adv_75.handle_event(OWNEvent.parse("*#2*75*10*10*255*001*14##"))
            assert adv_75.current_cover_position is None
            assert adv_75.is_closed is None

            adv_75.handle_event(OWNEvent.parse("*#2*75*10*11*40*001*12##"))
            assert adv_75.actuator_locked is True

            # 8. Post-scope *2*0*W## guards: own P2P stop_cover, *#2*0## / *#2*#7## scope polls,
            #    follower gateway poll, and post-_empty_reply *99*1## reply!
            m_a._actuation_unconfirmed = True
            m_a._note_scope_run_for_unconfirmed(time.monotonic(), True)
            await m_a.async_stop_cover()
            await _wait_for_tx(handler, sess_pos, len(sess_pos.sent_frames), hass)
            m_a.handle_event(OWNEvent.parse("*2*0*71##"))
            assert m_a._actuation_unconfirmed is True

            m_a._note_scope_run_for_unconfirmed(time.monotonic() - 2.0, True)
            fake_bad_scope_poll = SimpleNamespace(
                who=2,
                is_status_request=True,
                is_dimension_request=False,
                is_general=False,
                is_area=True,
                is_group=False,
                where="999",
                interface=None,
            )
            handler._command_pool._in_flight_messages.extend(
                [
                    OWNMessage.parse("*#1*71##"),
                    OWNMessage.parse("*#2*72##"),
                    fake_bad_scope_poll,
                    OWNMessage.parse("*#2*0##"),
                ]
            )
            try:
                m_a.handle_event(OWNEvent.parse("*2*0*71##"))
            finally:
                handler._command_pool._in_flight_messages.clear()
            assert m_a._actuation_unconfirmed is True

            m_a._note_scope_run_for_unconfirmed(time.monotonic() - 2.0, True)
            handler._command_pool._in_flight_messages.append(OWNMessage.parse("*#2*#7##"))
            try:
                m_a.handle_event(OWNEvent.parse("*2*0*71##"))
            finally:
                handler._command_pool._in_flight_messages.clear()
            assert m_a._actuation_unconfirmed is True

            m_a._note_scope_run_for_unconfirmed(time.monotonic() - 2.0, True)
            sess_sc._responder = lambda _raw, _is_req: True
            poll_fut = await handler.send_status_request(OWNMessage.parse("*#2*0##"))
            await asyncio.wait_for(poll_fut, timeout=2.0)
            follower_cov_gw = SimpleNamespace(
                is_follower=True,
                sweep_active=False,
                _command_pool=SimpleNamespace(_in_flight_messages=[], _recent_who2_polls=[]),
                _get_primary_gateway=lambda: handler,
                log_id="[FOL]",
                mac=handler.mac,
            )
            orig_m_a_gw = m_a._gateway_handler
            m_a._gateway_handler = follower_cov_gw  # type: ignore[assignment]
            try:
                m_a.handle_event(OWNEvent.parse("*2*0*71##"))
            finally:
                m_a._gateway_handler = orig_m_a_gw
            assert m_a._actuation_unconfirmed is True

            # 9. MyHOMEImpulseCover with access: while actuator_locked is True!
            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.08)
            handler.send = orig_send_sc  # type: ignore[method-assign]
            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.05)

            notified: list[tuple[str, dict[str, Any]]] = []

            async def _record_notify(call: Any) -> None:
                notified.append((call.service, dict(call.data)))

            hass.services.async_register("notify", "mobile_app_owner", _record_notify)
            hass.states.async_set("binary_sensor.gate_81_closed", STATE_ON)
            hass.states.async_set("binary_sensor.gate_81_beam", STATE_OFF)
            hass.states.async_set("person.owner", "home", {"user_id": "u_owner"})

            gate_81 = MyHOMEImpulseCover(
                hass=hass,
                name="Gate 81",
                entity_name=None,
                device_id="1-81",
                who="1",
                where="81",
                interface=None,
                device_class=CoverDeviceClass.GATE,
                travel_time=5.0,
                pulse_duration=0.5,
                min_cycle_time=0.0,
                pin_code=None,
                state_sensor="binary_sensor.gate_81_closed",
                manufacturer="BTicino",
                model="F411/2",
                gateway=handler,
                access_config={
                    "allowed_users": ["u_owner"],
                    "approvers": {"u_owner": "notify.mobile_app_owner"},
                    "remote_close": "at_home",
                    "safety_devices_verified": dt_util.now().date(),
                    "close_block_entity": "binary_sensor.gate_81_beam",
                    "prewarn_seconds": 0.02,
                    "watchdog_margin": 0.5,
                },
            )
            gate_81.hass = hass
            gate_81.entity_id = "cover.gate_81"
            gate_81.async_write_ha_state = MagicMock()
            gate_81.async_schedule_update_ha_state = MagicMock()
            with patch.object(gate_81, "async_get_last_state", AsyncMock(return_value=None)):
                await gate_81.async_added_to_hass()

            handler._actuator_locks.set_point_lock("81", None, True, observed=True)
            assert gate_81.actuator_locked is True

            gate_81._context = Context(user_id="u_owner")
            await gate_81.async_close_cover()
            assert gate_81._access is not None
            assert gate_81._access.phase is Phase.PENDING
            approve_action = notified[-1][1]["data"]["actions"][0]["action"]
            hass.bus.async_fire(
                "mobile_app_notification_action",
                {"action": approve_action},
                context=Context(user_id="u_owner"),
            )
            await asyncio.sleep(0.01)
            assert gate_81._access._tasks
            await asyncio.wait_for(
                asyncio.gather(*list(gate_81._access._tasks)), timeout=3.0
            )
            await hass.async_block_till_done()
            assert gate_81._access.phase is Phase.IDLE
            assert gate_81._access.fault_reason is None
            assert any("actuator is locked" in d.get("message", "") for _, d in notified)
            assert gate_81.extra_restore_state_data is not None
            assert gate_81.extra_restore_state_data.as_dict() == {"actuator_locked": True}

            notified.clear()
            await gate_81.async_close_cover()
            approve_action_2 = notified[-1][1]["data"]["actions"][0]["action"]
            hass.bus.async_fire(
                "mobile_app_notification_action",
                {"action": approve_action_2},
                context=Context(user_id="u_owner"),
            )
            for _ in range(50):
                if gate_81._locked_pulse_echo is not None:
                    break
                await asyncio.sleep(0.01)
            gate_81.handle_event(OWNEvent.parse("*1*1*81##"))
            if gate_81._access._tasks:
                await asyncio.wait_for(
                    asyncio.gather(*list(gate_81._access._tasks)), timeout=3.0
                )
            await hass.async_block_till_done()
            assert gate_81.actuator_locked is False
            assert gate_81._access.phase is Phase.MONITORING

            handler._actuator_locks.set_point_lock("81", None, True, observed=True)
            with pytest.raises(HomeAssistantError, match="stop pulse was not sent"):
                await gate_81.async_stop_cover()
            assert gate_81._access.phase is Phase.MONITORING

            hass.states.async_set("binary_sensor.gate_81_closed", STATE_OFF)
            await hass.async_block_till_done()
            assert gate_81._access.phase is Phase.IDLE
            gate_81._access.restore_fault("beam fault")
            both_extra = gate_81.extra_restore_state_data
            assert both_extra is not None
            assert both_extra.as_dict() == {"fault_reason": "beam fault", "actuator_locked": True}
            await gate_81.async_will_remove_from_hass()
            await ven_73.async_will_remove_from_hass()
            await simp_74.async_will_remove_from_hass()
            await adv_75.async_will_remove_from_hass()
            await m_a.async_will_remove_from_hass()
            await m_b.async_will_remove_from_hass()
            await grp_7.async_will_remove_from_hass()
            await cov_pos.async_will_remove_from_hass()
        finally:
            handler._command_pool.close()
            worker_pos.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker_pos


@pytest.mark.asyncio
async def test_ermanno00_mh201_area6_opposite_scope_interlock_stop_and_immediate_locked_stop(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live MH201 (3.6.44) + L4671/2 Area 6 (61-64) report (#977#issuecomment-7141618737):
    - 3a: locked 64 answers *2*2*6## and *2*1*6## immediately with *2*0*64## (stays locked / unconfirmed at 100%).
    - 3b: unlocked 61-63 closing on *2*2*6## stop physically at ~80% on un-pre-stopped *2*1*6## 5 s later
      (interlocked relay trips to STOP with zero bus frames); a second *2*1*6## >= 0.6 s later opens to 100%.
    - Also covers post-arrival M=0 relay-hold opposite scope interlock stop (open->close & close->open),
      same-direction scope continuation, Venetian tilt opposite scope interlock stop, STATE_UNAVAILABLE
      last_run direction restore, and F461 / stop_before_move=False / advanced=True non-interlock.
    """
    from homeassistant.const import STATE_UNAVAILABLE
    from homeassistant.helpers.restore_state import RestoredExtraData

    now_val = 1000.0
    monkeypatch.setattr("custom_components.myhome.cover.time.monotonic", lambda: now_val)
    monkeypatch.setattr("custom_components.myhome.cover_scope.time.monotonic", lambda: now_val)

    entry, handler = _make_real_gateway(
        hass,
        mac="00:03:50:97:60:f6",
        model="MH201",
        platforms={
            PLATFORM: {
                "61": {CONF_WHERE: "61", CONF_NAME: "Shutter 61", CONF_TRAVEL_TIME: 25},
                "62": {CONF_WHERE: "62", CONF_NAME: "Shutter 62", CONF_TRAVEL_TIME: 25},
                "63": {
                    CONF_WHERE: "63",
                    CONF_NAME: "Venetian 63",
                    CONF_TRAVEL_TIME: 25,
                    CONF_SLAT_TILT: True,
                    CONF_SLAT_TIME: 1.5,
                },
                "64": {CONF_WHERE: "64", CONF_NAME: "Shutter 64", CONF_TRAVEL_TIME: 25},
                "6": {CONF_WHERE: "6", CONF_NAME: "Area 6", CONF_TRAVEL_TIME: 25},
            }
        },
    )
    entities = await _setup_covers_on_gateway(hass, entry, handler)
    c61 = entities["61"]
    c62 = entities["62"]
    c63 = entities["63"]
    c64 = entities["64"]
    area6 = entities["6"]
    assert isinstance(c61, MyHOMECover)
    assert isinstance(c62, MyHOMECover)
    assert isinstance(c63, MyHOMECover)
    assert isinstance(c64, MyHOMECover)
    assert isinstance(area6, MyHOMEScopeCover)

    for c in (c61, c62, c63, c64):
        c._attr_current_cover_position = 100
        c._start_position = 100
        c._attr_is_closed = False
    c63._attr_current_cover_tilt_position = 100

    # Lock 64 (*14*0*64## ACKed -> gateway relays only *2*0*64##)
    assert handler._actuator_locks.observe(OWNActuatorLockCommand.parse("*14*0*64##")) is True
    c64.handle_event(OWNEvent.parse("*2*0*64##"))
    assert c64.actuator_locked is True
    assert c64.current_cover_position == 100

    # Step 3: send *2*2*6## at t=1000.0; gateway answers immediately (+0.1 s) with *2*0*64##
    for ent in (c61, c62, c63, c64, area6):
        ent.handle_event(OWNEvent.parse("*2*2*6##"))
    now_val += 0.1
    c64.handle_event(OWNEvent.parse("*2*0*64##"))
    assert c64.current_cover_position == 100
    assert c64.is_closing is False
    assert c61.is_closing is True and c62.is_closing is True and c63.is_closing is True
    assert area6.is_closing is True

    # +4.9 s later (total 5.0 s into 25 s travel): send *2*1*6## without a stop!
    # Physical L4671/2s 61-63 trip their interlocked relay to STOP (~80%) and emit 0 frames;
    # 64 answers *2*0*64## and stays at 100%.
    now_val += 4.9
    for ent in (c61, c62, c63, c64, area6):
        ent.handle_event(OWNEvent.parse("*2*1*6##"))
    now_val += 0.1
    c64.handle_event(OWNEvent.parse("*2*0*64##"))

    assert c64.current_cover_position == 100
    assert c61.is_opening is False and c61.is_closing is False
    assert c62.is_opening is False and c62.is_closing is False
    assert c63.is_opening is False and c63.is_closing is False
    assert c61.current_cover_position == 80
    assert c62.current_cover_position == 80
    assert 80 <= (c63.current_cover_position or 0) <= 86
    assert area6.is_opening is False and area6.is_closing is False
    assert area6._stop_task is None and c61._stop_task is None

    # Follow-up *2*1*6## after 0.6 s capacitor settle now opens 61-63 cleanly to 100%!
    now_val += 1.0
    for ent in (c61, c62, c63, c64, area6):
        ent.handle_event(OWNEvent.parse("*2*1*6##"))
    assert c61.is_opening is True and c62.is_opening is True and c63.is_opening is True
    assert c64.is_opening is False and c64.current_cover_position == 100

    # Opposite *2*2*6## mid-OPEN also trips interlock to STOP on MH201!
    now_val += 2.5
    for ent in (c61, c62, c63):
        ent.handle_event(OWNEvent.parse("*2*2*6##"))
    assert c61.is_opening is False and c61.is_closing is False
    assert c61.current_cover_position == 90

    # Post-arrival M=0 relay hold (61 finishes opening to 100%, UP relay still held at +10 s):
    # same-direction *2*1*6## does NOT trip interlock, while opposite *2*2*6## DOES trip interlock at 100%!
    now_val += 1.0
    c61._finish_end_of_run_settle(True)
    c61._arm_relay_hold(now_val, opening=True)
    assert c61.current_cover_position == 100
    now_val += 5.0
    c61.handle_event(OWNEvent.parse("*2*1*6##"))  # same direction -> allowed
    assert c61.is_opening is True
    c61._cancel_stop_task()
    c61._finish_end_of_run_settle(True)
    c61._arm_relay_hold(now_val, opening=True)
    now_val += 5.0
    c61.handle_event(OWNEvent.parse("*2*2*6##"))  # opposite direction during UP hold -> trips hold at 100%!
    assert c61.is_closing is False and c61.current_cover_position == 100

    # Once the 0.6 s settle expires, *2*2*6## closes 61!
    now_val += 1.0
    c61.handle_event(OWNEvent.parse("*2*2*6##"))
    assert c61.is_closing is True
    c61._cancel_stop_task()
    c61._abort_motion()
    c61._clear_relay_hold()

    # Venetian tilt pulse: unconfirmed pulse does NOT false-stop on *2*1*6##, while confirmed pulse DOES!
    c63._is_tilting = True
    c63._move_confirmed = False
    c63._tilt_direction = "close"
    c63._tilt_start_time = now_val - 0.3
    c63._tilt_initial_position = 100
    c63.handle_event(OWNEvent.parse("*2*1*6##"))
    assert c63.is_opening is True
    c63._cancel_stop_task()
    c63._abort_motion()
    c63._clear_relay_hold()

    c63._is_tilting = True
    c63._move_confirmed = True
    c63._tilt_direction = "close"
    c63._tilt_start_time = now_val - 0.3
    c63._tilt_initial_position = 100
    c63.handle_event(OWNEvent.parse("*2*1*6##"))
    assert c63._is_tilting is False
    assert c63.is_opening is False and c63.is_closing is False

    # Unconfirmed P2P close_cover (_move_confirmed=False) on MH201 followed by *2*1*6## opens to 100%
    # rather than falsely freezing as an interlock stop; and _apply_scope_interlock_stop closes any
    # open P2P echo window so a subsequent external *2*2*61## arms _wall_settle_unsub!
    c61._clear_relay_hold()
    c61._attr_current_cover_position = c61._start_position = 50
    c61._begin_run("close")
    assert c61._move_confirmed is False
    now_val += 0.2
    c61.handle_event(OWNEvent.parse("*2*1*6##"))
    assert c61.is_opening is True
    assert c61._wall_settle_unsub is not None
    # Now c61 is confirmed opening; open a P2P echo window and hit opposite *2*2*6## -> interlock stop
    c61._pending_cmd = "open"
    c61._echo_until = now_val + 1.5
    now_val += 1.0
    c61.handle_event(OWNEvent.parse("*2*2*6##"))
    assert c61.is_opening is False and c61.is_closing is False
    assert c61._pending_cmd is None and c61._echo_until is None
    now_val += 0.7
    c61.handle_event(OWNEvent.parse("*2*1*61##"))
    assert c61.is_opening is True and c61._wall_settle_unsub is not None
    c61._cancel_stop_task()
    c61._abort_motion()
    c61._clear_relay_hold()

    # Cold-boot unseen lock on 64 (_actuation_unconfirmed=True): immediate *2*0*64## on *2*2*6## keeps it excluded!
    handler._actuator_locks.clear()
    c64._actuator_locked = None
    c64._actuation_unconfirmed = True
    c64.handle_event(OWNEvent.parse("*2*2*6##"))
    now_val += 0.1
    c64.handle_event(OWNEvent.parse("*2*0*64##"))
    assert c64._actuation_unconfirmed is True

    # Explicit stop_before_move=False, stop_before_move=True, advanced=True, and F461 non-interlock
    c62._stop_before_move = False
    c62.handle_event(OWNEvent.parse("*2*2*6##"))
    now_val += 2.5
    c62.handle_event(OWNEvent.parse("*2*1*6##"))
    assert c62.is_opening is True  # explicit opt-out reverses without interlock stop
    c62._cancel_stop_task()
    c62._abort_motion()
    c62._clear_relay_hold()

    from unittest.mock import PropertyMock

    with patch.object(
        type(handler), "emits_cover_pre_move_stop", new_callable=PropertyMock, return_value=True
    ):
        c62._stop_before_move = None
        c62.handle_event(OWNEvent.parse("*2*2*6##"))
        now_val += 2.5
        c62.handle_event(OWNEvent.parse("*2*1*6##"))
        assert c62.is_opening is True
        c62._cancel_stop_task()
        c62._abort_motion()
        c62._clear_relay_hold()

        c62._stop_before_move = True  # explicit opt-in even on F461
        c62.handle_event(OWNEvent.parse("*2*2*6##"))
        now_val += 2.5
        c62.handle_event(OWNEvent.parse("*2*1*6##"))
        assert c62.is_opening is False and c62.is_closing is False

    # STATE_UNAVAILABLE restore preserves last_run.direction into _relay_hold_opening
    ended_iso = dt_util.utcnow().isoformat(timespec="milliseconds")
    c_rest = MyHOMECover(
        hass=hass,
        name="Restored 65",
        entity_name=None,
        device_id="65",
        who="2",
        where="65",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="L4671/2",
        gateway=handler,
        travel_time=25,
    )
    c_rest._poll_on_add = False
    c_rest.entity_id = "cover.restored_65"
    with (
        patch.object(
            c_rest,
            "async_get_last_state",
            AsyncMock(return_value=State("cover.restored_65", STATE_UNAVAILABLE, {})),
        ),
        patch.object(
            c_rest,
            "async_get_last_extra_data",
            AsyncMock(
                return_value=RestoredExtraData(
                    {
                        "position": 0,
                        "is_closed": True,
                        "last_run": {"seconds": 25.0, "direction": "close", "ended_at": ended_iso},
                    }
                )
            ),
        ),
    ):
        await c_rest.async_added_to_hass()
    assert c_rest._relay_hold_opening is False
    c_rest.handle_event(OWNEvent.parse("*2*1*6##"))  # opposite direction during restored close hold -> stops at 0%!
    assert c_rest.is_opening is False and c_rest.current_cover_position == 0

    # Mid-motion STATE_UNAVAILABLE save (opening, with a 2-hour-old closing last_run) persists
    # motion_direction="open" and NEVER pairs motion_started_at with stale last_run.direction!
    c_mid_src = MyHOMECover(
        hass=hass,
        name="MidRun 66",
        entity_name=None,
        device_id="66",
        who="2",
        where="66",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="L4671/2",
        gateway=handler,
        travel_time=25,
    )
    c_mid_src._attr_current_cover_position = c_mid_src._start_position = 20
    c_mid_src._attr_is_opening = True
    c_mid_src._motion_started_at = ended_iso
    stale_ended = (dt_util.utcnow() - timedelta(hours=2)).isoformat(timespec="milliseconds")
    c_mid_src._last_run = {"seconds": 25.0, "direction": "close", "ended_at": stale_ended}
    mid_saved = c_mid_src.extra_restore_state_data
    assert mid_saved is not None
    assert mid_saved.as_dict()["motion_direction"] == "open"

    c_mid_rest = MyHOMECover(
        hass=hass,
        name="MidRun 66",
        entity_name=None,
        device_id="66",
        who="2",
        where="66",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="L4671/2",
        gateway=handler,
        travel_time=25,
    )
    c_mid_rest._poll_on_add = False
    c_mid_rest.entity_id = "cover.midrun_66"
    with (
        patch.object(
            c_mid_rest,
            "async_get_last_state",
            AsyncMock(return_value=State("cover.midrun_66", STATE_UNAVAILABLE, {})),
        ),
        patch.object(
            c_mid_rest,
            "async_get_last_extra_data",
            AsyncMock(return_value=RestoredExtraData(mid_saved.as_dict())),
        ),
    ):
        await c_mid_rest.async_added_to_hass()
    assert c_mid_rest._relay_hold_opening is True
    c_mid_rest.handle_event(OWNEvent.parse("*2*1*6##"))  # same direction as restored open -> continues opening!
    assert c_mid_rest.is_opening is True
    c_mid_rest._cancel_stop_task()
    c_mid_rest._abort_motion()
    c_mid_rest._clear_relay_hold()

    for ent in (*entities.values(), c_rest, c_mid_rest):
        await ent.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_scheduler_stop_coalesce_active_probe_cancel_and_locked_impulse_deadband_safety(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify #1014 stop cancel-coalescing on unconfirmed cover, _begin_stop probe cancel, and #1011 locked impulse deadband/STOP safety."""
    from homeassistant.const import STATE_OFF, STATE_ON
    from homeassistant.core import Context

    from custom_components.myhome.access_policy import Phase

    entry, handler = _make_real_gateway(
        hass,
        mac="00:03:50:97:60:f9",
        model="F461",
        platforms={
            PLATFORM: {
                "41": {CONF_WHERE: "41", CONF_NAME: "Coalesce 41", CONF_TRAVEL_TIME: 10},
                "42": {CONF_WHERE: "42", CONF_NAME: "Simple 42", CONF_TRAVEL_TIME: 0},
            }
        },
    )
    entities = await _setup_covers_on_gateway(hass, entry, handler)
    cov_41 = entities["41"]
    cov_42 = entities["42"]
    assert isinstance(cov_41, MyHOMECover)
    assert isinstance(cov_42, MyHOMECover)
    cov_41._attr_current_cover_position = cov_41._start_position = 50
    cov_41._arm_relay_hold(time.monotonic(), opening=True)
    assert cov_41._relay_hold_opening is True

    # 1. #1014 implicit WHO=2 stop cancel-coalescing while open_cover is still unwritten in pool:
    #    open_cover future is cancelled pre-wire, stop_cover settles at 50% with zero drift and
    #    clears _relay_hold_opening!
    await cov_41.async_open_cover()
    await cov_41.async_stop_cover()
    sess = _FakeCommandSession(lambda _raw, _is_req: True)
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=sess):
        worker = asyncio.create_task(handler.sending_loop(0))
        try:
            await _wait_for_tx(handler, sess, 1, hass)
            await hass.async_block_till_done()
            assert [f for f, _ in sess.sent_frames] == ["*2*0*41##"]
            assert cov_41.current_cover_position == 50
            assert cov_41.is_opening is False and cov_41.is_closing is False
            assert cov_41._actuation_unconfirmed is False
            assert cov_41._relay_hold_opening is None

            # 2. _begin_stop immediately cancels an active _unconfirmed_move_task probe!
            blocked_probe: asyncio.Future[float] = hass.loop.create_future()
            with patch.object(handler, "send_status_request", AsyncMock(return_value=blocked_probe)):
                cov_42._begin_run("open")
                cov_42._arm_unconfirmed_move_timer(time.monotonic() - 2.0)
                async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
                await asyncio.sleep(0)
                active_probe = cov_42._unconfirmed_move_task
                assert active_probe is not None and not active_probe.done()
                await cov_42.async_stop_cover()
                await asyncio.gather(active_probe, return_exceptions=True)
                assert active_probe.cancelled() or active_probe.done()
                assert cov_42._unconfirmed_move_task is None
                blocked_probe.cancel()

            # 3. MyHOMEImpulseCover with min_cycle_time=10.0s:
            #    (a) Under access: concurrent stop_cover during ECHO_WINDOW wait on locked CLOSE -> IDLE (no fault),
            #        and during MONITORING: locked STOP #1 fails -> unlock -> STOP #2 inside min_cycle_time SENDS pulse!
            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 0.08)
            notified: list[tuple[str, dict[str, Any]]] = []

            async def _rec_notify(call: Any) -> None:
                notified.append((call.service, dict(call.data)))

            hass.services.async_register("notify", "mobile_app_guard", _rec_notify)
            hass.states.async_set("binary_sensor.gate_91_closed", STATE_ON)
            hass.states.async_set("binary_sensor.gate_91_beam", STATE_OFF)
            hass.states.async_set("person.guard", "home", {"user_id": "u_guard"})

            gate_91 = MyHOMEImpulseCover(
                hass=hass,
                name="Gate 91",
                entity_name=None,
                device_id="1-91",
                who="1",
                where="91",
                interface=None,
                device_class=CoverDeviceClass.GATE,
                travel_time=5.0,
                pulse_duration=0.5,
                min_cycle_time=10.0,
                pin_code=None,
                state_sensor="binary_sensor.gate_91_closed",
                manufacturer="BTicino",
                model="F411/2",
                gateway=handler,
                access_config={
                    "allowed_users": ["u_guard"],
                    "approvers": {"u_guard": "notify.mobile_app_guard"},
                    "remote_close": "at_home",
                    "safety_devices_verified": dt_util.now().date(),
                    "close_block_entity": "binary_sensor.gate_91_beam",
                    "prewarn_seconds": 0.01,
                    "watchdog_margin": 1.0,
                },
            )
            gate_91.hass = hass
            gate_91.entity_id = "cover.gate_91"
            gate_91.async_write_ha_state = MagicMock()
            gate_91.async_schedule_update_ha_state = MagicMock()
            with patch.object(gate_91, "async_get_last_state", AsyncMock(return_value=None)):
                await gate_91.async_added_to_hass()
            gate_91._context = Context(user_id="u_guard")
            handler._actuator_locks.set_point_lock("91", None, True, observed=True)

            # Concurrent stop_cover arriving DURING the ECHO_WINDOW wait on approved locked CLOSE!
            sess.sent_frames.clear()
            await gate_91.async_close_cover()
            assert gate_91._access is not None
            act_1 = notified[-1][1]["data"]["actions"][0]["action"]
            hass.bus.async_fire(
                "mobile_app_notification_action",
                {"action": act_1},
                context=Context(user_id="u_guard"),
            )
            for _ in range(50):
                if gate_91._locked_pulse_echo is not None:
                    break
                await asyncio.sleep(0.005)
            assert gate_91._locked_pulse_echo is not None
            await gate_91.async_stop_cover()
            if gate_91._access._tasks:
                await asyncio.wait_for(asyncio.gather(*list(gate_91._access._tasks)), timeout=2.0)
            await hass.async_block_till_done()
            assert gate_91._access.phase is Phase.IDLE
            assert gate_91._access.fault_reason is None

            # Unlock, approve CLOSE -> MONITORING, then lock mid-run:
            # STOP #1 while locked fails -> unlock -> STOP #2 within 10s min_cycle_time MUST transmit *1*18*91##!
            handler._actuator_locks.set_point_lock("91", None, False, observed=True)
            notified.clear()
            await gate_91.async_close_cover()
            act_2 = notified[-1][1]["data"]["actions"][0]["action"]
            hass.bus.async_fire(
                "mobile_app_notification_action",
                {"action": act_2},
                context=Context(user_id="u_guard"),
            )
            if gate_91._access._tasks:
                await asyncio.wait_for(asyncio.gather(*list(gate_91._access._tasks)), timeout=2.0)
            await hass.async_block_till_done()
            assert gate_91._access.phase is Phase.MONITORING

            handler._actuator_locks.set_point_lock("91", None, True, observed=True)
            sess.sent_frames.clear()
            with pytest.raises(HomeAssistantError, match="stop pulse was not sent"):
                await gate_91.async_stop_cover()
            assert [f for f, _ in sess.sent_frames] == ["*1*18*91##"]
            assert gate_91._access.phase is Phase.MONITORING

            # Unlock and immediately call stop_cover #2 inside min_cycle_time=10.0s!
            handler._actuator_locks.set_point_lock("91", None, False, observed=True)
            sess.sent_frames.clear()
            await gate_91.async_stop_cover()
            assert [f for f, _ in sess.sent_frames] == ["*1*18*91##"]
            assert gate_91._access.phase is Phase.IDLE

            # (b) Direct impulse cover (no access:, min_cycle_time=10.0s): locked STOP does not swallow
            #     post-unlock STOP or OPEN, while offline-unlocked echo on STOP restores _last_pulse_intent!
            gate_92 = MyHOMEImpulseCover(
                hass=hass,
                name="Gate 92",
                entity_name=None,
                device_id="1-92",
                who="1",
                where="92",
                interface=None,
                device_class=CoverDeviceClass.GATE,
                travel_time=5.0,
                pulse_duration=0.5,
                min_cycle_time=10.0,
                pin_code=None,
                state_sensor=None,
                manufacturer="BTicino",
                model="F411/2",
                gateway=handler,
            )
            gate_92.hass = hass
            gate_92.entity_id = "cover.gate_92"
            gate_92.async_write_ha_state = MagicMock()
            gate_92.async_schedule_update_ha_state = MagicMock()
            with patch.object(gate_92, "async_get_last_state", AsyncMock(return_value=None)):
                await gate_92.async_added_to_hass()
            handler._actuator_locks.set_point_lock("92", None, True, observed=True)
            sess.sent_frames.clear()
            await gate_92.async_stop_cover()
            assert gate_92._last_pulse_intent is None
            handler._actuator_locks.set_point_lock("92", None, False, observed=True)
            await gate_92.async_open_cover()
            assert [f for f, _ in sess.sent_frames] == ["*1*18*92##", "*1*18*92##"]
            gate_92._cancel_travel_timer()

            # Offline-unlocked echo on locked direct STOP sets _last_pulse_intent = Intent.STOP
            gate_92._last_pulse_time = -1e9
            handler._actuator_locks.set_point_lock("92", None, True, observed=True)
            monkeypatch.setattr(cover_module, "ECHO_WINDOW", 1.5)
            await gate_92.async_stop_cover()
            gate_92.handle_event(OWNEvent.parse("*1*18*92##"))
            assert gate_92.actuator_locked is False
            assert gate_92._last_pulse_intent is cover_module.Intent.STOP

            hass.states.async_set("binary_sensor.gate_91_closed", STATE_OFF)
            await gate_91.async_will_remove_from_hass()
            await gate_92.async_will_remove_from_hass()
            await cov_41.async_will_remove_from_hass()
            await cov_42.async_will_remove_from_hass()
        finally:
            handler._command_pool.close()
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
