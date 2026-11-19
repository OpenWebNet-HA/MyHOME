"""Tests for Issue #959: ECHO_WINDOW terminal stops and pre-move stops on MH201 / MH202."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import ATTR_CURRENT_POSITION, ATTR_POSITION
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from OWNd.message import OWNAutomationEvent, OWNEvent
from OWNd.profiles import GenericGatewayProfile, get_gateway_profile
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from voluptuous import Invalid

from custom_components.myhome.const import (
    CONF_EXPLICIT_KINEMATICS,
    CONF_STOP_BEFORE_MOVE,
    DOMAIN,
    EXPLICIT_COVER_KINEMATICS_KEYS,
)
from custom_components.myhome.cover import CoverFamily, CoverScope, MyHOMECover
from custom_components.myhome.cover_motion import (
    ECHO_WINDOW,
    END_SETTLE_MARGIN,
    M0_RELAY_HOLD_WINDOW,
    PRE_MOVE_STOP_SETTLE_DELAY,
)
from custom_components.myhome.cover_scope import MyHOMEScopeCover
from custom_components.myhome.cover_strategies import NativePositionStrategy
from custom_components.myhome.gateway import (
    COVER_PRE_MOVE_STOP_EMITTED,
    COVER_PRE_MOVE_STOP_OMITTED,
    MyHOMEGatewayHandler,
    gateway_emits_cover_pre_move_stop,
)
from custom_components.myhome.validate import cover_schema

# Authentic 10-frame bus capture from BTicino MH201 (fw 3.3.6) + BTicino L4671/2
# timed shutter actuator on WHERE=41 (travel_time=15 s), reported by @ermanno00 in #959.
MH201_L4671_WHERE41_TRACE: tuple[tuple[float, str, str], ...] = (
    (1795036001.764133, "rx", "*2*1*41##"),
    (1795036012.879290, "tx", "*#2*41##"),
    (1795036030.373658, "rx", "*2*0*41##"),
    (1795036136.008613, "tx", "*2*2*41##"),
    (1795036136.071633, "rx", "*2*2*41##"),
    (1795036141.096229, "tx", "*2*1*41##"),
    (1795036141.151487, "rx", "*2*0*41##"),
    (1795036145.104210, "tx", "*2*1*41##"),
    (1795036145.171416, "rx", "*2*1*41##"),
    (1795036206.550313, "rx", "*2*0*41##"),
)


_REAL_SLEEP = asyncio.sleep


class _Clock:
    def __init__(self, initial: float = 1000.0) -> None:
        self.now = initial


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def fake_clock(clock: _Clock):
    real_sleep = asyncio.sleep
    sleeps: list[float] = []

    async def _fake_sleep(delay: float, *args: Any, **kwargs: Any) -> None:
        sleeps.append(delay)
        if delay > 0:
            clock.now += delay
        await real_sleep(0)

    with (
        patch("custom_components.myhome.cover.time") as cover_time,
        patch("custom_components.myhome.cover_scope.time") as scope_time,
        patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_fake_sleep),
        patch("custom_components.myhome.cover_scope.asyncio.sleep", side_effect=_fake_sleep),
    ):
        cover_time.monotonic.side_effect = lambda: clock.now
        scope_time.monotonic.side_effect = lambda: clock.now
        yield sleeps


async def _yield_loop(turns: int = 6) -> None:
    for _ in range(turns):
        await _REAL_SLEEP(0)


def _make_gateway(
    *,
    model: str | None = "MH201",
    emits_pre_stop: bool | None | object = ...,
    immediate_write: bool = True,
    clock: _Clock | None = None,
) -> MagicMock:
    gw = MagicMock()
    gw.mac = "00:03:50:aa:bb:cc"
    gw.unique_id = gw.mac
    gw.log_id = f"[{model or 'Mock'} gateway]"
    gw.model = model
    if emits_pre_stop is not ...:
        gw.emits_cover_pre_move_stop = emits_pre_stop
    else:
        del gw.emits_cover_pre_move_stop
    gw.config_entry = None
    gw.send_status_request = AsyncMock()
    gw.sent: list[tuple[str, asyncio.Future[float]]] = []

    async def _send(cmd: object, **kwargs: Any) -> asyncio.Future[float]:
        fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        gw.sent.append((str(cmd), fut))
        if immediate_write:
            fut.set_result(clock.now if clock is not None else time.monotonic())
        return fut

    gw.send = AsyncMock(side_effect=_send)
    return gw


def _make_cover(
    hass: HomeAssistant,
    gateway: Any,
    where: str = "41",
    *,
    travel_time: int = 15,
    stop_before_move: bool | None = None,
    pos: int | None = 100,
    advanced: bool = False,
    allow_auto_advanced: bool = False,
    slat_tilt: bool = False,
    strategy_name: str | None = None,
) -> MyHOMECover:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name=f"Cover {where}",
            entity_name=None,
            device_id=where,
            who="2",
            where=where,
            interface=None,
            advanced=advanced,
            allow_auto_advanced=allow_auto_advanced,
            slat_tilt=slat_tilt,
            strategy_name=strategy_name,
            manufacturer="BTicino",
            model="L4671/2",
            gateway=gateway,
            travel_time=travel_time,
            stop_before_move=stop_before_move,
        )
    cover.hass = hass
    cover.entity_id = f"cover.cover_{where}"
    cover.async_write_ha_state = MagicMock()
    cover.async_schedule_update_ha_state = MagicMock()
    if pos is not None and not advanced and not cover.strategy.assumed_state:
        cover._attr_current_cover_position = pos
        cover._start_position = pos
        cover._attr_is_closed = pos == 0
    return cover


def _make_scope_cover(
    hass: HomeAssistant,
    gateway: Any,
    where: str,
    members: list[MyHOMECover],
    *,
    member_wheres: tuple[str, ...] = (),
    stop_before_move: bool | None = None,
    slat_tilt: bool = False,
    travel_time: int = 15,
) -> MyHOMEScopeCover:
    family = CoverFamily()
    for member in members:
        member.async_on_remove = MagicMock()
        family.add(member)
    scope = CoverScope.of(where, None, member_wheres)
    assert scope is not None
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        sc = MyHOMEScopeCover(
            hass=hass,
            name=f"Scope {where}",
            entity_name=None,
            device_id=where,
            who="2",
            where=where,
            interface=None,
            advanced=False,
            slat_tilt=slat_tilt,
            manufacturer="BTicino",
            model="Group Cover",
            gateway=gateway,
            travel_time=travel_time,
            stop_before_move=stop_before_move,
            scope=scope,
        )
    sc.hass = hass
    sc.entity_id = f"cover.scope_{where.lstrip('#')}"
    sc.async_on_remove = MagicMock()
    sc.async_write_ha_state = MagicMock()
    sc.async_schedule_update_ha_state = MagicMock()
    family.add(sc)
    return sc


def test_firmware_oracle_and_gateway_pre_move_stop_matrix(hass: HomeAssistant) -> None:
    """Verify gateway_emits_cover_pre_move_stop against firmware_oracle.json and delegation."""
    assert CONF_STOP_BEFORE_MOVE in EXPLICIT_COVER_KINEMATICS_KEYS

    oracle_path = Path(__file__).resolve().parent / "golden" / "firmware_oracle.json"
    verdicts = json.loads(oracle_path.read_text(encoding="utf-8"))["verdicts"]

    for frame in ("*2*1*31##", "*2*2*31##"):
        for entry in verdicts[frame]:
            prod = entry["product"]
            if entry["verdict"] != "out":
                assert gateway_emits_cover_pre_move_stop(prod) is None
                continue
            bus = [bytes.fromhex(b).decode("ascii").strip() for b in entry["bus_frames"]]
            has_pre_stop = len(bus) == 2 and bus[0].endswith("120A")
            if has_pre_stop:
                assert prod.upper() in COVER_PRE_MOVE_STOP_EMITTED
                assert gateway_emits_cover_pre_move_stop(prod) is True
            else:
                assert prod == "MH202"
                assert prod in COVER_PRE_MOVE_STOP_OMITTED
                assert gateway_emits_cover_pre_move_stop(prod) is False

    # Live-captured MH201, brand-prefixed aliases, unmeasured F455/Generic, and profile flag override
    assert gateway_emits_cover_pre_move_stop("MH201", profile=get_gateway_profile("MH201")) is False
    assert gateway_emits_cover_pre_move_stop("BTicino MH202") is False
    assert gateway_emits_cover_pre_move_stop("BTicino F461") is True
    assert gateway_emits_cover_pre_move_stop("BTicino 3488") is True
    assert gateway_emits_cover_pre_move_stop("F455", profile=get_gateway_profile("F455")) is None
    assert gateway_emits_cover_pre_move_stop(None, profile=GenericGatewayProfile()) is None
    assert (
        gateway_emits_cover_pre_move_stop(
            "F455",
            profile=SimpleNamespace(emits_cover_pre_move_stop=True, model_name="F455"),
        )
        is True
    )
    assert (
        gateway_emits_cover_pre_move_stop(
            "F455",
            profile=SimpleNamespace(emits_cover_pre_move_stop=False, model_name="F455"),
        )
        is False
    )

    # Real MyHOMEGatewayHandler property + WHO=2 delegation / failover target resolution
    entry_mh201 = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: "00:03:50:00:01:01",
            CONF_NAME: "MH201",
            CONF_FRIENDLY_NAME: "MH201",
        },
    )
    entry_f461 = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: "00:03:50:00:01:02",
            CONF_NAME: "F461",
            CONF_FRIENDLY_NAME: "F461",
        },
    )
    gw_mh201 = MyHOMEGatewayHandler(hass, entry_mh201)
    gw_f461 = MyHOMEGatewayHandler(hass, entry_f461)
    assert gw_mh201.emits_cover_pre_move_stop is False
    assert gw_f461.emits_cover_pre_move_stop is True

    with patch.object(gw_f461, "_delegated_target", return_value=gw_mh201):
        assert gw_f461.emits_cover_pre_move_stop is False

    cov_adv = _make_cover(hass, gw_mh201, "99", advanced=True)
    cov_adv._arm_relay_hold(1000.0, opening=True)
    assert cov_adv._relay_hold_until is None


def test_stop_before_move_yaml_validation() -> None:
    """Validate stop_before_move schema acceptance, cosmetic kinematics preservation, and rejections."""
    valid = cover_schema(
        {
            "cosmetic_only": {"where": "41", "name": "Cosmetic"},
            "timed_true": {"where": "42", "name": "Timed True", "stop_before_move": True},
            "timed_false": {"where": "43", "name": "Timed False", "stop_before_move": False},
            "group_true": {
                "where": "#1",
                "name": "Group 1",
                "members": ["41", "42"],
                "stop_before_move": True,
            },
            "area_false": {"where": "4", "name": "Area 4", "stop_before_move": False},
        }
    )
    assert valid["2-41"][CONF_EXPLICIT_KINEMATICS] is False
    assert CONF_STOP_BEFORE_MOVE not in valid["2-41"]
    assert valid["2-42"][CONF_EXPLICIT_KINEMATICS] is True
    assert valid["2-42"][CONF_STOP_BEFORE_MOVE] is True
    assert valid["2-43"][CONF_EXPLICIT_KINEMATICS] is True
    assert valid["2-43"][CONF_STOP_BEFORE_MOVE] is False
    assert valid["2-#1"][CONF_STOP_BEFORE_MOVE] is True
    assert valid["2-4"][CONF_STOP_BEFORE_MOVE] is False

    for bad_cfg in (
        {"c": {"where": "41", "name": "Adv", "advanced": True, "stop_before_move": True}},
        {"c": {"where": "41", "name": "Adv2", "advanced_shutter": True, "stop_before_move": False}},
        {"c": {"where": "41", "name": "Nat", "strategy": "native", "stop_before_move": True}},
        {"c": {"where": "71", "who": "1", "name": "Gate", "stop_before_move": True}},
    ):
        with pytest.raises(Invalid, match="stop_before_move"):
            cover_schema(bad_cfg)


@pytest.mark.asyncio
async def test_bug_b_mh201_trace_echo_window_terminal_stop_without_pre_stop(
    hass: HomeAssistant,
    clock: _Clock,
    fake_clock: list[float],
) -> None:
    """Replay #959's 10-frame MH201 trace with stop_before_move=False: *2*0*41## at +55 ms stops HA."""
    t0 = MH201_L4671_WHERE41_TRACE[0][0]
    clock.now = 0.0
    gw = _make_gateway(model="MH201", immediate_write=False, clock=clock)
    cover = _make_cover(hass, gw, "41", travel_time=15, stop_before_move=False, pos=100)

    # Frame 0..2: initial UP run and manual stop at 100% open
    cover.handle_event(OWNEvent.parse(MH201_L4671_WHERE41_TRACE[0][2]))
    clock.now = MH201_L4671_WHERE41_TRACE[2][0] - t0
    cover.handle_event(OWNEvent.parse(MH201_L4671_WHERE41_TRACE[2][2]))
    assert cover.is_opening is False
    assert cover.current_cover_position == 100

    # Frame 3..4: 21:08:56.008 tx *2*2*41## -> +63 ms rx *2*2*41## (closing from 100%)
    clock.now = MH201_L4671_WHERE41_TRACE[3][0] - t0
    await cover.async_close_cover()
    _, close_written = gw.sent[-1]
    close_written.set_result(clock.now)
    await _yield_loop()

    clock.now = MH201_L4671_WHERE41_TRACE[4][0] - t0
    cover.handle_event(OWNEvent.parse(MH201_L4671_WHERE41_TRACE[4][2]))
    assert cover.is_closing is True

    # Frame 5: 21:09:01.096 (~5.025 s into 15 s close, ~67%) tx *2*1*41##
    clock.now = MH201_L4671_WHERE41_TRACE[5][0] - t0
    await cover.async_open_cover()
    _, open_written = gw.sent[-1]
    open_written.set_result(clock.now)
    # Pause at motor_started.wait() inside _await_motion_anchor before ECHO_WINDOW expires
    await asyncio.sleep(0)
    pre_open_pos = cover._start_position
    assert pre_open_pos == 67

    # Frame 6: 21:09:01.151 (+55.26 ms) rx *2*0*41## — actuator STOPPED; no *2*1*41## follows!
    clock.now = MH201_L4671_WHERE41_TRACE[6][0] - t0
    cover.handle_event(OWNEvent.parse(MH201_L4671_WHERE41_TRACE[6][2]))
    assert cover._echo_stop_at == pytest.approx(clock.now)

    # Let ECHO_WINDOW expire: _await_motion_anchor detects unfollowed *2*0*41## and aborts run!
    assert cover._stop_task is not None
    await cover._stop_task
    assert cover.is_opening is False
    assert cover.is_closing is False
    assert cover.is_closed is False
    assert cover.current_cover_position == 67

    # Frame 7..8: 21:09:05.104 second tx *2*1*41## -> +67.2 ms rx *2*1*41## starts UP cleanly
    clock.now = MH201_L4671_WHERE41_TRACE[7][0] - t0
    await cover.async_open_cover()
    _, open2_written = gw.sent[-1]
    open2_written.set_result(clock.now)
    await asyncio.sleep(0)

    clock.now = MH201_L4671_WHERE41_TRACE[8][0] - t0
    cover.handle_event(OWNEvent.parse(MH201_L4671_WHERE41_TRACE[8][2]))
    assert cover._stop_task is not None
    await cover._stop_task
    assert cover.is_opening is False
    assert cover.current_cover_position == 100
    assert cover._relay_hold_until is not None

    # Frame 9: 21:10:06.550 (+61.38 s) rx *2*0*41## cutoff clears relay hold while keeping 100%
    clock.now = MH201_L4671_WHERE41_TRACE[9][0] - t0
    cover.handle_event(OWNEvent.parse(MH201_L4671_WHERE41_TRACE[9][2]))
    assert cover._relay_hold_until is None
    assert cover.current_cover_position == 100


@pytest.mark.asyncio
async def test_bug_b_f461_pre_stop_followed_by_motor_start_and_partial_position_abort(
    hass: HomeAssistant,
    clock: _Clock,
    fake_clock: list[float],
) -> None:
    """Pre-STOP gateways (*2*0* followed by *2*<dir>*) keep moving; unfollowed *2*0* aborts _auto_stop."""
    # 1. F461 / MyHomeServer1: *2*1000#0* + *2*0* at +80 ms followed by *2*2* at +450 ms succeeds
    gw_f461 = _make_gateway(model="F461", immediate_write=False, clock=clock)
    cov_f461 = _make_cover(hass, gw_f461, "05", travel_time=10, pos=100)
    await cov_f461.async_close_cover()
    _, written = gw_f461.sent[-1]
    written.set_result(clock.now)
    await asyncio.sleep(0)

    clock.now += 0.04
    cov_f461.handle_event(OWNEvent.parse("*2*1000#0*05##"))
    assert cov_f461._echo_stop_at is None  # translation never arms _echo_stop_at

    clock.now += 0.04
    cov_f461.handle_event(OWNEvent.parse("*2*0*05##"))
    assert cov_f461._echo_stop_at == pytest.approx(clock.now)

    clock.now += 0.37
    motor_start = clock.now
    cov_f461.handle_event(OWNEvent.parse("*2*2*05##"))
    assert cov_f461._echo_stop_at is None
    assert cov_f461._move_start_time == pytest.approx(motor_start)
    assert cov_f461._stop_task is not None
    await cov_f461._stop_task
    assert cov_f461.current_cover_position == 0
    assert cov_f461.is_closed is True

    # External wall keypad stop inside ECHO_WINDOW on F461 stops cover without setting _learned_stop_before_move
    await cov_f461.async_open_cover()
    _, written_open = gw_f461.sent[-1]
    written_open.set_result(clock.now)
    await asyncio.sleep(0)
    clock.now += 0.1
    cov_f461.handle_event(OWNEvent.parse("*2*0*05##"))
    assert cov_f461._stop_task is not None
    await cov_f461._stop_task
    assert cov_f461.is_opening is False
    assert cov_f461.current_cover_position == 0
    assert cov_f461._learned_stop_before_move is False

    # 2. Unknown gateway (F455) + partial set_cover_position(50): unfollowed *2*0* aborts _auto_stop
    # without sending a delayed stop AND sets _learned_stop_before_move = True!
    gw_f455 = _make_gateway(model="F455", immediate_write=False, clock=clock)
    cov_f455 = _make_cover(hass, gw_f455, "41", travel_time=15, pos=0)
    await cov_f455.async_set_cover_position(**{ATTR_POSITION: 50})
    assert [f for f, _ in gw_f455.sent] == ["*2*1*41##"]
    _, pos_written = gw_f455.sent[-1]
    pos_written.set_result(clock.now)
    await asyncio.sleep(0)

    clock.now += 0.055
    cov_f455.handle_event(OWNEvent.parse("*2*0*41##"))
    assert cov_f455._stop_task is not None
    await cov_f455._stop_task
    assert cov_f455.is_opening is False
    assert cov_f455.is_closed is True
    assert cov_f455.current_cover_position == 0
    assert cov_f455._learned_stop_before_move is True
    assert [f for f, _ in gw_f455.sent] == ["*2*1*41##"]  # no delayed *2*0*41## sent!

    # Once learned, while energized on F455, reversing automatically sends pre-move stop!
    cov_f455.handle_event(OWNEvent.parse("*2*1*41##"))
    assert cov_f455.is_opening is True
    gw_f455.sent.clear()
    close_task = asyncio.create_task(cov_f455.async_close_cover())
    await _yield_loop()
    assert [f for f, _ in gw_f455.sent] == ["*2*0*41##"]
    gw_f455.sent[0][1].set_result(clock.now)
    await _yield_loop()
    assert [f for f, _ in gw_f455.sent] == ["*2*0*41##", "*2*2*41##"]
    gw_f455.sent[1][1].set_result(clock.now)
    await close_task
    if cov_f455._stop_task is not None:
        cov_f455._stop_task.cancel()
        await cov_f455._stop_task


@pytest.mark.asyncio
async def test_bug_b_echo_stop_fallback_timer_for_simple_strategy_and_hass_none_guards(
    hass: HomeAssistant,
) -> None:
    """Verify _arm_echo_stop_timer settles SimpleUpDownStopStrategy covers with no _stop_task."""
    gw = _make_gateway(model="F455", immediate_write=True)
    cov_simple = _make_cover(
        hass,
        gw,
        "44",
        strategy_name="simple",
        stop_before_move=False,
        pos=None,
    )
    cov_simple._attr_is_closed = True
    await cov_simple.async_open_cover()
    await _yield_loop()
    assert cov_simple.is_opening is True
    assert cov_simple._stop_task is None

    # First stop arms timer; second stop replaces previous timer cleanly (_cancel_echo_stop_timer)
    cov_simple.handle_event(OWNEvent.parse("*2*0*44##"))
    assert cov_simple._echo_stop_unsub is not None
    cov_simple.handle_event(OWNEvent.parse("*2*0*44##"))
    assert cov_simple._echo_stop_unsub is not None

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=ECHO_WINDOW + 0.2))
    await hass.async_block_till_done()
    assert cov_simple.is_opening is False
    assert cov_simple.is_closed is True
    assert cov_simple._learned_stop_before_move is True

    # Stop arriving BEFORE write completion re-arms timer to write_ts + ECHO_WINDOW in _on_written
    gw_delayed = _make_gateway(model="F455", immediate_write=False)
    cov_pre_write = _make_cover(hass, gw_delayed, "48", strategy_name="simple", stop_before_move=False, pos=None)
    await cov_pre_write.async_open_cover()
    cov_pre_write.handle_event(OWNEvent.parse("*2*0*48##"))
    gw_delayed.sent[0][1].set_result(time.monotonic())
    await _yield_loop()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=ECHO_WINDOW + 0.5))
    await hass.async_block_till_done()
    assert cov_pre_write.is_opening is False

    # Timer callback no-ops when superseded before firing
    cov_sup = _make_cover(hass, gw, "45", strategy_name="simple", stop_before_move=False, pos=None)
    await cov_sup.async_open_cover()
    await _yield_loop()
    cov_sup.handle_event(OWNEvent.parse("*2*0*45##"))
    cov_sup._run_generation += 1
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=ECHO_WINDOW + 1.0))
    await hass.async_block_till_done()
    assert cov_sup.is_opening is True

    # hass=None guard in _arm_echo_stop_timer + async_will_remove_from_hass timer cleanup
    cov_no_hass = _make_cover(hass, gw, "46", strategy_name="simple", stop_before_move=False, pos=None)
    cov_no_hass.hass = None  # type: ignore[assignment]
    await cov_no_hass.async_open_cover()
    cov_no_hass.handle_event(OWNEvent.parse("*2*0*46##"))
    assert cov_no_hass._echo_stop_unsub is None


@pytest.mark.asyncio
async def test_bug_a_mh201_mh202_automatic_pre_stop_and_65s_relay_hold_lifecycle(
    hass: HomeAssistant,
    clock: _Clock,
    fake_clock: list[float],
) -> None:
    """MH201 & MH202 pre-stop mid-run and within 65 s post-arrival hold, and skip when idle or after *2*0*."""
    for model in ("MH201", "MH202"):
        gw = _make_gateway(model=model, immediate_write=True, clock=clock)
        cover = _make_cover(hass, gw, "41", travel_time=15, pos=100)

        # 1. Idle at startup -> NO pre-move stop, NO 0.6 s delay!
        fake_clock.clear()
        await cover.async_close_cover()
        assert [f for f, _ in gw.sent] == ["*2*2*41##"]
        assert PRE_MOVE_STOP_SETTLE_DELAY not in fake_clock

        # 2. Mid-run reversal at +5 s -> automatic pre-STOP + 0.6 s + *2*1*41##!
        gw.sent.clear()
        clock.now += 5.0
        await cover.async_open_cover()
        assert [f for f, _ in gw.sent] == ["*2*0*41##", "*2*1*41##"]
        assert PRE_MOVE_STOP_SETTLE_DELAY in fake_clock

        # Settle opening run at 100% (15 + 2.0 = 17 s): physical shutter arrived, relay STILL energized!
        assert cover._stop_task is not None
        await cover._stop_task
        assert cover.is_opening is False
        assert cover.current_cover_position == 100

        # 3. Close during the ~44 s post-limit relay hold (e.g. +15 s after settle = ~32 s from motor start)
        gw.sent.clear()
        fake_clock.clear()
        clock.now += 15.0
        await cover.async_close_cover()
        assert [f for f, _ in gw.sent] == ["*2*0*41##", "*2*2*41##"]
        assert PRE_MOVE_STOP_SETTLE_DELAY in fake_clock
        assert cover._stop_task is not None
        await cover._stop_task
        assert cover.current_cover_position == 0

        # 4a. Past 65 s without cutoff frame -> relay hold expires naturally -> zero pre-stop!
        gw.sent.clear()
        fake_clock.clear()
        clock.now += M0_RELAY_HOLD_WINDOW + 5.0
        await cover.async_open_cover()
        assert [f for f, _ in gw.sent] == ["*2*1*41##"]
        assert PRE_MOVE_STOP_SETTLE_DELAY not in fake_clock
        assert cover._stop_task is not None
        await cover._stop_task

        # 4b. Early *2*0*41## cutoff frame clears relay hold immediately -> zero pre-stop 1 s later!
        cover.handle_event(OWNEvent.parse("*2*0*41##"))
        assert cover._relay_hold_until is None
        gw.sent.clear()
        fake_clock.clear()
        clock.now += 1.0
        await cover.async_close_cover()
        assert [f for f, _ in gw.sent] == ["*2*2*41##"]
        assert PRE_MOVE_STOP_SETTLE_DELAY not in fake_clock
        if cover._stop_task is not None:
            await cover._stop_task

    # Long-travel shutter (travel_time=80 > 65 s), legacy non-Future send(), and restore_last_state edges
    gw_legacy = MagicMock()
    gw_legacy.mac = "00:03:50:aa:bb:dd"
    gw_legacy.log_id = "[MH201 legacy]"
    gw_legacy.model = "MH201"
    del gw_legacy.emits_cover_pre_move_stop
    gw_legacy.send = AsyncMock(return_value=None)
    cov_long = _make_cover(hass, gw_legacy, "49", travel_time=80, pos=100)
    await cov_long.async_close_cover()
    assert cov_long._relay_hold_until == pytest.approx(clock.now + 80 + END_SETTLE_MARGIN)
    if cov_long._stop_task is not None:
        cov_long._stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cov_long._stop_task

    # Restore state: "opening" arms hold; recent motion_started_at (<65s) arms remaining hold; old (>65s) stays None
    cov_rest_moving = _make_cover(hass, gw_legacy, "50", pos=None)
    await cov_rest_moving.async_restore_last_state(State("cover.cover_50", "opening", {ATTR_CURRENT_POSITION: 40}))
    assert cov_rest_moving._relay_hold_until is not None

    cov_rest_recent = _make_cover(hass, gw_legacy, "51", pos=None)
    recent_iso = (dt_util.utcnow() - timedelta(seconds=20)).isoformat()
    await cov_rest_recent.async_restore_last_state(
        State("cover.cover_51", "closed", {ATTR_CURRENT_POSITION: 0, "last_run_ended_at": recent_iso})
    )
    assert cov_rest_recent._relay_hold_until is not None

    cov_rest_old = _make_cover(hass, gw_legacy, "52", pos=None)
    old_iso = (dt_util.utcnow() - timedelta(seconds=120)).isoformat()
    await cov_rest_old.async_restore_last_state(
        State("cover.cover_52", "closed", {ATTR_CURRENT_POSITION: 0, "last_run_ended_at": old_iso})
    )
    assert cov_rest_old._relay_hold_until is None

    # Explicit stop_before_move=True on SimpleUpDownStopStrategy + failed pre-stop write delivery
    gw_fail = _make_gateway(model="F461", immediate_write=False, clock=clock)
    cov_force = _make_cover(
        hass,
        gw_fail,
        "53",
        strategy_name="simple",
        stop_before_move=True,
        pos=None,
    )
    open_task = asyncio.create_task(cov_force.async_open_cover())
    await _yield_loop()
    assert [f for f, _ in gw_fail.sent] == ["*2*0*53##"]
    # Fail the pre-stop write: skips 0.6 s sleep and lets _async_move queue direction & abort on failed write
    gw_fail.sent[0][1].set_exception(RuntimeError("socket lost"))
    await _yield_loop()
    assert [f for f, _ in gw_fail.sent] == ["*2*0*53##", "*2*1*53##"]
    gw_fail.sent[1][1].set_exception(RuntimeError("socket lost"))
    await open_task
    await _yield_loop()
    assert cov_force.is_opening is False

    # Slat tilt pulse never sends pre-move stop even with stop_before_move=True
    gw_tilt = _make_gateway(model="MH201", immediate_write=True, clock=clock)
    cov_tilt = _make_cover(hass, gw_tilt, "54", slat_tilt=True, stop_before_move=True, pos=50)
    cov_tilt._attr_current_cover_tilt_position = 0
    await cov_tilt.async_open_cover_tilt()
    assert [f for f, _ in gw_tilt.sent] == ["*2*1*54##"]
    if cov_tilt._stop_task is not None:
        cov_tilt._stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cov_tilt._stop_task


@pytest.mark.asyncio
async def test_supersession_wall_switch_user_commands_removal_and_dim10_promotion_during_pause(
    hass: HomeAssistant,
    clock: _Clock,
) -> None:
    """Verify _run_generation supersession on wall switch, user stop, write-wait, removal, and Dim 10 promotion."""
    # Use controlled real event-loop sleeps (monkeypatched PRE_MOVE_STOP_SETTLE_DELAY=0.05) to interleave events mid-pause
    with patch("custom_components.myhome.cover.PRE_MOVE_STOP_SETTLE_DELAY", 0.05):
        # 1. Wall-switch press (*2*1*41##) during the 0.6 s pause (after *2*0*41## closed echo window!)
        gw1 = _make_gateway(model="MH201", immediate_write=True, clock=clock)
        cov1 = _make_cover(hass, gw1, "41", stop_before_move=True, pos=50)
        close_task = asyncio.create_task(cov1.async_close_cover())
        await asyncio.sleep(0.01)
        assert [f for f, _ in gw1.sent] == ["*2*0*41##"]
        # Pre-stop echo closes stop echo window; wall switch then starts opening!
        cov1.handle_event(OWNEvent.parse("*2*0*41##"))
        cov1.handle_event(OWNEvent.parse("*2*1*41##"))
        await close_task
        # Superseded! *2*2*41## was NEVER sent into the running UP motor!
        assert [f for f, _ in gw1.sent] == ["*2*0*41##"]
        assert cov1.is_opening is True
        cov1._cancel_stop_task()

        # 2. Wall-switch close (*2*2*42##) during open_cover pause on SimpleUpDownStopStrategy
        gw2 = _make_gateway(model="MH201", immediate_write=True, clock=clock)
        cov2 = _make_cover(hass, gw2, "42", strategy_name="simple", stop_before_move=True, pos=None)
        open_task = asyncio.create_task(cov2.async_open_cover())
        await asyncio.sleep(0.01)
        cov2.handle_event(OWNEvent.parse("*2*0*42##"))
        cov2.handle_event(OWNEvent.parse("*2*2*42##"))
        await open_task
        assert [f for f, _ in gw2.sent] == ["*2*0*42##"]
        assert cov2.is_closing is True

        # 3. Supersession during send() yield AND during write-wait (before written resolves)
        gw3 = _make_gateway(model="MH201", immediate_write=False, clock=clock)
        cov3 = _make_cover(hass, gw3, "43", strategy_name="simple", stop_before_move=True, pos=None)
        close_wait_task = asyncio.create_task(cov3.async_close_cover())
        await asyncio.sleep(0.01)
        assert [f for f, _ in gw3.sent] == ["*2*0*43##"]
        # User hits stop while first pre-stop is still waiting in queue -> bumps _run_generation
        await cov3.async_stop_cover()
        gw3.sent[0][1].set_result(clock.now)
        await close_wait_task
        assert [f for f, _ in gw3.sent] == ["*2*0*43##", "*2*0*43##"]

        # Supersession inside send() yield itself
        gw_send_sup = _make_gateway(model="MH201", immediate_write=True, clock=clock)
        cov_send_sup = _make_cover(
            hass, gw_send_sup, "44", strategy_name="simple", stop_before_move=True, pos=None
        )

        async def _superseding_send(cmd: object, **kwargs: Any) -> None:
            gw_send_sup.sent.append((str(cmd), None))  # type: ignore[arg-type]
            cov_send_sup._run_generation += 1

        gw_send_sup.send = AsyncMock(side_effect=_superseding_send)
        await cov_send_sup.async_close_cover()
        assert [f for f, _ in gw_send_sup.sent] == ["*2*0*44##"]

        # 4. Mid-pause Dimension 10 auto-promotion during async_set_cover_position(40)
        # AND during _async_run_to_end(True)!
        gw_prom = _make_gateway(model="MH201", immediate_write=True, clock=clock)
        cov_prom = _make_cover(
            hass,
            gw_prom,
            "0110",
            allow_auto_advanced=True,
            pos=80,
        )
        # Start closing so automatic pre-stop activates on MH201 while allow_auto_advanced=True
        cov_prom.handle_event(OWNEvent.parse("*2*2*0110##"))
        assert cov_prom.is_closing is True
        gw_prom.sent.clear()

        pos_task = asyncio.create_task(cov_prom.async_set_cover_position(**{ATTR_POSITION: 40}))
        await asyncio.sleep(0.01)
        assert [f for f, _ in gw_prom.sent] == ["*2*0*0110##"]

        # Stationary Dim 10 frame arrives during the 0.6 s pause -> promotes and hands off Dim 11 40%!
        cov_prom.handle_event(OWNAutomationEvent.parse("*#2*0110*10*10*65*001*0##"))
        await pos_task
        await hass.async_block_till_done()
        assert isinstance(cov_prom.strategy, NativePositionStrategy)
        assert [f for f, _ in gw_prom.sent] == ["*2*0*0110##", "*#2*0110*#11#001*40##"]

        # Inline promotion inside _async_run_to_end -> _async_move skips _settle_run_to_end
        gw_prom2 = _make_gateway(model="MH201", immediate_write=True, clock=clock)
        cov_prom2 = _make_cover(hass, gw_prom2, "0111", allow_auto_advanced=True, pos=50)

        async def _promote_on_open(cmd: object, **kwargs: Any) -> None:
            gw_prom2.sent.append((str(cmd), None))  # type: ignore[arg-type]
            if str(cmd) == "*2*1*0111##":
                cov_prom2.handle_event(OWNAutomationEvent.parse("*#2*0111*10*11*50*001*0##"))

        gw_prom2.send = AsyncMock(side_effect=_promote_on_open)
        await cov_prom2.async_open_cover()
        assert cov_prom2._advanced is True
        assert cov_prom2._stop_task is None

        # Entity removal during pause cleans up cleanly
        gw_rem = _make_gateway(model="MH201", immediate_write=True, clock=clock)
        cov_rem = _make_cover(hass, gw_rem, "47", stop_before_move=True, pos=50)
        cov_rem.async_on_remove = MagicMock()
        rem_task = asyncio.create_task(cov_rem.async_open_cover())
        await asyncio.sleep(0.01)
        await cov_rem.async_will_remove_from_hass()
        await rem_task
        assert [f for f, _ in gw_rem.sent] == ["*2*0*47##"]


@pytest.mark.asyncio
async def test_post_stop_kinematics_in_async_set_cover_position(
    hass: HomeAssistant,
    clock: _Clock,
    fake_clock: list[float],
) -> None:
    """Verify set_cover_position recomputes duration/direction after pre-stop write & echo."""
    # 1. Busy send queue: cover closes from 100% -> at t+3s (70%) user asks for 80%,
    # pre-stop sits queued 2s until t+5s (50%) -> opening run_duration must cover 50%->80% (3.0 s on 10 s travel), not 70%->80% (1.0 s)!
    gw = _make_gateway(model="MH201", immediate_write=False, clock=clock)
    cover = _make_cover(hass, gw, "41", travel_time=10, pos=100)

    # Start closing from wall/bus at clock.now (avoids auto-advancing settle sleep during setup)
    cover.handle_event(OWNEvent.parse("*2*2*41##"))
    cover._cancel_wall_settle()

    clock.now += 3.0  # cover at 70%
    gw.sent.clear()
    retarget_task = asyncio.create_task(cover.async_set_cover_position(**{ATTR_POSITION: 80}))
    await _yield_loop()
    assert [f for f, _ in gw.sent] == ["*2*0*41##"]

    # Stop waits 2.0 s in queue -> cover reaches 50% when stop is written!
    clock.now += 2.0
    gw.sent[0][1].set_result(clock.now)
    cover.handle_event(OWNEvent.parse("*2*0*41##"))
    await _yield_loop()

    assert cover._start_position == 50
    assert [f for f, _ in gw.sent] == ["*2*0*41##", "*2*1*41##"]
    open_write_ts = clock.now
    gw.sent[1][1].set_result(open_write_ts)
    await retarget_task
    cover.handle_event(OWNEvent.parse("*2*1*41##"))

    fake_clock.clear()
    assert cover._stop_task is not None
    # Auto-stop will enqueue *2*0*41## after 3.0 s (for 50% -> 80%), NOT 1.0 s!
    gw.send = AsyncMock(return_value=None)
    await cover._stop_task
    assert fake_clock[0] == pytest.approx(3.0, abs=0.01)
    assert cover.current_cover_position == 80

    # 2. Cover reaches exact target_position while pre-stop waits in queue -> diff == 0 exits with NO move!
    gw_exact = _make_gateway(model="MH201", immediate_write=False, clock=clock)
    cov_exact = _make_cover(hass, gw_exact, "42", travel_time=10, pos=100)
    cov_exact.handle_event(OWNEvent.parse("*2*2*42##"))
    cov_exact._cancel_wall_settle()

    clock.now += 2.0  # at 80%, ask for 60%
    gw_exact.sent.clear()
    exact_task = asyncio.create_task(cov_exact.async_set_cover_position(**{ATTR_POSITION: 60}))
    await asyncio.sleep(0)
    clock.now += 2.0  # reaches 60% right when pre-stop writes!
    gw_exact.sent[0][1].set_result(clock.now)
    cov_exact.handle_event(OWNEvent.parse("*2*0*42##"))
    await exact_task
    assert [f for f, _ in gw_exact.sent] == ["*2*0*42##"]  # no direction frame needed!
    assert cov_exact.current_cover_position == 60

    # 3. Post-stop achieved_position clamps to 100% / 0% via min_motor_pulse -> runs to end with NO second stop!
    gw_clamp = _make_gateway(model="MH201", immediate_write=True, clock=clock)
    cov_clamp = _make_cover(hass, gw_clamp, "43", travel_time=5, stop_before_move=True, pos=98)
    await cov_clamp.async_set_cover_position(**{ATTR_POSITION: 99})
    assert [f for f, _ in gw_clamp.sent] == ["*2*0*43##", "*2*1*43##"]
    if cov_clamp._stop_task is not None:
        await cov_clamp._stop_task
    assert cov_clamp.current_cover_position == 100

    gw_clamp.sent.clear()
    cov_clamp._attr_current_cover_position = 2
    cov_clamp._start_position = 2
    await cov_clamp.async_set_cover_position(**{ATTR_POSITION: 1})
    assert [f for f, _ in gw_clamp.sent] == ["*2*0*43##", "*2*2*43##"]
    if cov_clamp._stop_task is not None:
        await cov_clamp._stop_task
    assert cov_clamp.current_cover_position == 0


@pytest.mark.asyncio
async def test_scope_cover_pre_move_stop_reversal_and_options(
    hass: HomeAssistant,
    clock: _Clock,
    fake_clock: list[float],
) -> None:
    """Verify local group/area/general scope covers send one scope stop + 0.6 s before moving members."""
    gw = _make_gateway(model="MH201", immediate_write=True, clock=clock)
    m41 = _make_cover(hass, gw, "41", travel_time=15, pos=100)
    m42 = _make_cover(hass, gw, "42", travel_time=15, pos=100)
    group = _make_scope_cover(hass, gw, "#1", [m41, m42], member_wheres=("41", "42"))

    # 1. Idle members -> single *2*2*#1## broadcast, zero pre-stop
    fake_clock.clear()
    await group.async_close_cover()
    assert [f for f, _ in gw.sent] == ["*2*2*#1##"]
    assert PRE_MOVE_STOP_SETTLE_DELAY not in fake_clock
    assert m41.is_closing is True and m42.is_closing is True

    # 2. Mid-run scope reversal -> ONE *2*0*#1## + 0.6 s + ONE *2*1*#1##, and all members open!
    gw.sent.clear()
    fake_clock.clear()
    clock.now += 5.0
    await group.async_open_cover()
    assert [f for f, _ in gw.sent] == ["*2*0*#1##", "*2*1*#1##"]
    assert PRE_MOVE_STOP_SETTLE_DELAY in fake_clock
    assert m41.is_opening is True and m42.is_opening is True
    for m in (m41, m42):
        if m._stop_task is not None:
            await m._stop_task
    assert m41.current_cover_position == 100
    assert m42.current_cover_position == 100

    # 3. Scope set_cover_position(0) during members' 65 s post-limit relay hold -> pre-stops cleanly!
    gw.sent.clear()
    fake_clock.clear()
    clock.now += 10.0
    await group.async_set_cover_position(**{ATTR_POSITION: 0})
    assert [f for f, _ in gw.sent] == ["*2*0*#1##", "*2*2*#1##"]
    for m in (m41, m42):
        if m._stop_task is not None:
            await m._stop_task

    # 4. Scope explicit stop_before_move=False opt-out, stop_before_move=True force, memberless scope, and slat tilt
    group._stop_before_move = False
    gw.sent.clear()
    await group.async_open_cover()
    assert [f for f, _ in gw.sent] == ["*2*1*#1##"]
    for m in (m41, m42):
        if m._stop_task is not None:
            await m._stop_task

    # Clear member holds via *2*0*, then force group._stop_before_move = True -> pre-stops even when members idle
    m41.handle_event(OWNEvent.parse("*2*0*41##"))
    m42.handle_event(OWNEvent.parse("*2*0*42##"))
    group._stop_before_move = True
    gw.sent.clear()
    await group.async_close_cover()
    assert [f for f, _ in gw.sent] == ["*2*0*#1##", "*2*2*#1##"]
    for m in (m41, m42):
        if m._stop_task is not None:
            await m._stop_task

    # Memberless scope cover + scope slat tilt skip
    memberless = _make_scope_cover(hass, gw, "#9", [], stop_before_move=None)
    assert memberless._stops_before_move(False) is False
    assert memberless._stops_before_move(True) is False
