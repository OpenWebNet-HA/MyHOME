"""The CoverKinematicsStrategy contract driven through MyHOMECover (#374, #739).

A piecewise, stateful strategy (a slat phase the cover cannot tilt, a slow
lift-off and a fast roll, a position unknown until a full run) is plugged
into an unmodified MyHOMECover and driven through set-position, keypad runs,
the stop echo, the 0/100 settle, run lifecycle hooks and a restart.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.helpers.restore_state import RestoredExtraData
from homeassistant.util import dt as dt_util
from OWNd.message import OWNEvent
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.myhome.cover import CoverScope, MyHOMECover, MyHOMEScopeCover
from custom_components.myhome.cover_motion import END_SETTLE_MARGIN
from custom_components.myhome.cover_strategies import (
    RUN_END_REASONS,
    BaseCoverStrategy,
    SimpleUpDownStopStrategy,
    TimedCurtainStrategy,
)

SLAT_PHASE = 3.0  # seconds the motor turns slats before the curtain lifts
CURTAIN = 10.0  # curtain seconds from closed to open after the slat phase


def _time_at(position: float) -> float:
    """Curtain seconds from closed to ``position``: 0-20 % in 5 s, 20-100 % in 5 s."""
    position = max(0.0, min(100.0, position))
    return position / 20.0 * 5.0 if position <= 20 else 5.0 + (position - 20.0) / 80.0 * 5.0


def _position_at(seconds: float) -> float:
    seconds = max(0.0, min(CURTAIN, seconds))
    return 20.0 * seconds / 5.0 if seconds <= 5.0 else 20.0 + (seconds - 5.0) * 80.0 / 5.0


class PiecewiseGeometryStrategy(TimedCurtainStrategy):
    """A non-linear, stateful model of the kind #374 calibrates."""

    def __init__(self, *, known: bool = True) -> None:
        super().__init__(travel_time=SLAT_PHASE + CURTAIN)
        self.known = known
        self.events: list[tuple[Any, ...]] = []

    @property
    def name(self) -> str:
        return "piecewise"

    def curtain_travel_for(self, opening: bool) -> float:
        return CURTAIN

    def lead_time_for(self, opening: bool, *, entity: Any = None) -> float:
        return SLAT_PHASE

    def position_after(
        self, start: int, elapsed: float, direction: str, *, entity: Any = None, lead: float | None = None
    ) -> int | None:
        if not self.known:
            return None
        curtain = max(0.0, elapsed - (SLAT_PHASE if lead is None else lead))
        moved = _time_at(start) + (curtain if direction == "open" else -curtain)
        return int(round(_position_at(moved)))

    def duration_for(
        self, start: int, target: int, direction: str | None = None, *, entity: Any = None
    ) -> float:
        if start == target:
            return 0.0
        return SLAT_PHASE + abs(_time_at(target) - _time_at(start))

    def on_run_started(self, entity: Any, direction: str, start_position: int | None, at: float) -> None:
        self.events.append(("started", direction, start_position, at))

    def on_run_ended(self, entity: Any, reason: str, position: int | None, at: float) -> None:
        assert reason in RUN_END_REASONS
        self.events.append(("ended", reason, position, at))

    def on_position_confirmed(self, entity: Any, position: int, source: str) -> None:
        self.known = True
        self.events.append(("confirmed", position, source))

    def export_state(self) -> dict[str, Any] | None:
        return {"known": self.known}

    def restore_state(self, state: dict[str, Any]) -> None:
        self.known = bool(state.get("known"))


def _cover(hass, strategy: Any = None, **kwargs: Any) -> MyHOMECover:
    gw = MagicMock()
    gw.mac = "00:03:50:00:12:34"
    gw.log_id = "[TestGW]"
    gw.send = AsyncMock()
    args: dict[str, Any] = {
        "hass": hass,
        "name": "Cover 21",
        "entity_name": "Cover 21",
        "device_id": "21",
        "who": "2",
        "where": "21",
        "interface": None,
        "advanced": False,
        "manufacturer": "BTicino",
        "model": "Shutter",
        "gateway": gw,
        "travel_time": 13,
        **kwargs,
    }
    cls = MyHOMEScopeCover if "scope" in args else MyHOMECover
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = cls(**args)
    cover.entity_id = "cover.cover_21"
    cover.hass = hass
    cover.async_write_ha_state = MagicMock()
    cover.async_schedule_update_ha_state = MagicMock()
    if strategy is not None:
        cover._strategy = strategy
    return cover


class _Clock:
    """Monotonic time the patched asyncio.sleep advances."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += max(0.0, seconds)


def _event(cover: MyHOMECover, frame: str) -> None:
    cover.handle_event(OWNEvent.parse(frame))


@pytest.mark.asyncio
async def test_piecewise_strategy_drives_cover_without_cover_py_changes(hass) -> None:
    """Set-position, keypad runs, the stop echo and the 0/100 settle all follow the strategy."""
    strategy = PiecewiseGeometryStrategy()
    cover = _cover(hass, strategy)
    clock = _Clock()
    cover._attr_current_cover_position = cover._start_position = 0
    cover._attr_is_closed = True

    with (
        patch("custom_components.myhome.cover.time.monotonic", side_effect=clock.monotonic),
        patch("asyncio.sleep", side_effect=clock.sleep),
    ):
        # A 3 s keypad run from closed only turns the slats: still 0, not 30.
        _event(cover, "*2*1*21##")
        clock.now += 3.0
        _event(cover, "*2*0*21##")
        assert cover.current_cover_position == 0
        assert cover.is_closed is True

        # 3 s of slats plus 5 s of slow lift-off reach 20 %.
        clock.now += 10.0
        _event(cover, "*2*1*21##")
        clock.now += 8.0
        _event(cover, "*2*0*21##")
        assert cover.current_cover_position == 20

        # Back to closed, then set-position 0 -> 20: runs the strategy's 8 s
        # and reports 20, not 50.
        clock.now += 10.0
        _event(cover, "*2*2*21##")
        clock.now += 13.0
        _event(cover, "*2*0*21##")
        assert cover.current_cover_position == 0
        clock.now += 10.0
        await cover.async_set_cover_position(position=20)
        _event(cover, "*2*1*21##")  # the gateway's direction echo anchors the run
        await cover._stop_task
        assert 8.0 in clock.slept
        assert str(cover._gateway_handler.send.await_args_list[-1].args[0]) == "*2*0*21##"
        assert cover.current_cover_position == 20

        # Stop echo: the relayed stop re-anchors with the strategy's lead.
        clock.now += 10.0
        await cover.async_open_cover()
        _event(cover, "*2*1*21##")
        clock.now += 4.0
        await cover.async_stop_cover()
        clock.now += 0.3
        _event(cover, "*2*0*21##")
        assert cover.current_cover_position == strategy.position_after(20, 4.3, "open", lead=SLAT_PHASE) == 41

        # 0/100: a full run settles on travel + END_SETTLE_MARGIN and confirms the end.
        clock.now += 10.0
        await cover.async_set_cover_position(position=100)
        _event(cover, "*2*1*21##")
        await cover._stop_task
        assert SLAT_PHASE + CURTAIN + END_SETTLE_MARGIN in clock.slept
        assert cover.current_cover_position == 100

    started = [e for e in strategy.events if e[0] == "started"]
    ended = [e[1:3] for e in strategy.events if e[0] == "ended"]
    assert started[0][1:3] == ("open", 0)
    assert ended == [
        ("stop", 0),
        ("stop", 20),
        ("end_stop", 0),
        ("stop", 20),
        ("stop", 36),  # frozen at the stop write; the relayed echo then re-anchors to 41
        ("end_stop", 100),
    ]
    assert [e for e in strategy.events if e[0] == "confirmed"] == [
        ("confirmed", 0, "end_stop"),
        ("confirmed", 100, "end_stop"),
    ]


@pytest.mark.asyncio
async def test_run_end_hooks_for_takeover_reversal_and_abort(hass) -> None:
    """Every way a run can end reaches the strategy."""
    strategy = PiecewiseGeometryStrategy()
    cover = _cover(hass, strategy)
    clock = _Clock()
    cover._attr_current_cover_position = cover._start_position = 0

    with patch("custom_components.myhome.cover.time.monotonic", side_effect=clock.monotonic):
        # Opposite direction on the bus.
        _event(cover, "*2*1*21##")
        clock.now += 8.0
        _event(cover, "*2*2*21##")
        # A new command takes the motor over.
        clock.now += 1.0
        await cover.async_open_cover()
        cover._cancel_stop_task()
        _event(cover, "*2*1*21##")
        # A command that never reached the bus.
        clock.now += 1.0
        cover._abort_motion()

    reasons = [e[1] for e in strategy.events if e[0] == "ended"]
    assert reasons == ["reversal", "takeover", "abort"]
    assert ("ended", "reversal", 20, 1008.0) in strategy.events


@pytest.mark.asyncio
async def test_position_unknown_until_a_full_run(hass) -> None:
    """A strategy may leave the position unknown until a run confirms an end."""
    strategy = PiecewiseGeometryStrategy(known=False)
    cover = _cover(hass, strategy)
    clock = _Clock()
    cover._attr_current_cover_position = None

    with (
        patch("custom_components.myhome.cover.time.monotonic", side_effect=clock.monotonic),
        patch("asyncio.sleep", side_effect=clock.sleep),
    ):
        _event(cover, "*2*1*21##")
        clock.now += 6.0
        _event(cover, "*2*0*21##")
        assert cover.current_cover_position is None
        assert strategy.known is False

        # A keypad run that runs for the full travel time hits the limit switch
        # and confirms the end stop even though the bus only sends *2*0*WHERE##.
        clock.now += 10.0
        _event(cover, "*2*2*21##")
        clock.now += 13.0
        _event(cover, "*2*0*21##")
        assert strategy.known is True
        assert cover.current_cover_position == 0
        assert ("ended", "end_stop", 0, clock.now) in strategy.events
        assert ("confirmed", 0, "end_stop") in strategy.events

        strategy.known = False
        cover._attr_current_cover_position = None
        clock.now += 10.0
        await cover.async_set_cover_position(position=100)
        _event(cover, "*2*1*21##")
        await cover._stop_task

    assert strategy.known is True
    assert cover.current_cover_position == 100
    assert ("confirmed", 100, "end_stop") in strategy.events

    # A memberless scope cover's run timeout also confirms the end stop.
    scope_strategy = PiecewiseGeometryStrategy(known=False)
    scope_cover = _cover(hass, scope_strategy, device_id="#7", where="#7", scope=CoverScope.of("#7"))
    scope_cover._attr_current_cover_position = None
    _event(scope_cover, "*2*1*#7##")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=14))
    await hass.async_block_till_done()
    assert scope_strategy.known is True
    assert scope_cover.current_cover_position == 100
    assert [e[:3] for e in scope_strategy.events if e[0] in ("ended", "confirmed")] == [
        ("ended", "end_stop", 100),
        ("confirmed", 100, "end_stop"),
    ]

    # And when the timeout fires before a write anchor is set.
    scope_strategy.known = False
    scope_cover._attr_is_closing = True
    scope_cover._run_timeout_direction = "close"
    scope_cover._end_run(None)
    assert scope_strategy.known is True
    assert scope_cover.current_cover_position == 0
    assert scope_strategy.events[-1] == ("confirmed", 0, "end_stop")


@pytest.mark.asyncio
async def test_strategy_state_survives_a_restart(hass) -> None:
    """export_state goes into the restore data and comes back to the same strategy only."""
    strategy = PiecewiseGeometryStrategy(known=True)
    cover = _cover(hass, strategy)
    stored = cover.extra_restore_state_data
    assert stored is not None
    assert stored.as_dict() == {"strategy": "piecewise", "state": {"known": True}}

    last_state = MagicMock(state="open", attributes={})

    restarted = _cover(hass, PiecewiseGeometryStrategy(known=False))
    restarted.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData(stored.as_dict()))
    await restarted.async_restore_last_state(last_state)
    assert restarted.strategy.known is True

    # Another strategy's state is ignored, and so is a missing record.
    other = _cover(hass, PiecewiseGeometryStrategy(known=False))
    other.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData({"strategy": "timed", "state": {"known": True}})
    )
    await other.async_restore_last_state(last_state)
    assert other.strategy.known is False
    other.async_get_last_extra_data = AsyncMock(return_value=None)
    await other.async_restore_last_state(last_state)
    assert other.strategy.known is False

    # Not registered with the restore cache: nothing to restore, no error.
    other.async_get_last_extra_data = AsyncMock(side_effect=RuntimeError("no restore cache"))
    await other.async_restore_last_state(last_state)
    assert other.strategy.known is False

    # Built-in strategies are stateless: no restore data at all.
    assert _cover(hass).extra_restore_state_data is None


@pytest.mark.asyncio
async def test_custom_strategies_survive_strategy_transitions(hass) -> None:
    """Slat tilt, calibration and its reset keep a custom strategy and its parameters."""
    strategy = PiecewiseGeometryStrategy()
    strategy.min_motor_pulse = 0.7
    cover = _cover(hass, strategy)

    cover._enable_slat_tilt()
    assert cover.strategy is strategy
    assert cover.supported_features & CoverEntityFeature.SET_TILT_POSITION

    cover._device_config = MagicMock(return_value={"travel_time": 0})
    cover._fire_calibration_event = MagicMock()
    await cover.async_reset_travel_time()
    assert cover.strategy is strategy
    assert cover.supported_features & CoverEntityFeature.SET_POSITION

    class CustomSimple(SimpleUpDownStopStrategy):
        pass

    simple = CustomSimple()
    uncalibrated = _cover(hass, simple, travel_time=0)
    uncalibrated._promote_to_timed_strategy()
    assert uncalibrated.strategy is simple


def test_builtin_transitions_carry_the_timing_parameters() -> None:
    """Built-in successors keep every timing parameter exactly, a zero travel time included."""
    simple = SimpleUpDownStopStrategy()
    simple.travel_time_down, simple.travel_time_up = 21.5, 23.0
    simple.min_motor_pulse, simple.slat_time = 0.6, 1.5

    timed = simple.promoted(slat_tilt=False)
    venetian = simple.promoted(slat_tilt=True)
    assert (type(timed).__name__, type(venetian).__name__) == ("TimedCurtainStrategy", "VenetianTiltStrategy")
    assert type(timed.with_slat_tilt()).__name__ == "VenetianTiltStrategy"
    assert venetian.with_slat_tilt() is venetian
    assert timed.promoted(slat_tilt=True) is timed  # already calibrated: nothing to promote
    for successor in (timed, venetian, timed.with_slat_tilt()):
        assert (successor.travel_time_down, successor.travel_time_up) == (21.5, 23.0)
        assert (successor.min_motor_pulse, successor.slat_time) == (0.6, 1.5)

    timed.travel_time_down = timed.travel_time_up = 0.0
    demoted = timed.demoted()
    assert isinstance(demoted, SimpleUpDownStopStrategy)
    assert (demoted.travel_time_down, demoted.travel_time_up) == (0.0, 0.0)
    assert venetian.demoted() is venetian
    assert demoted.demoted() is demoted

    base = BaseCoverStrategy()
    assert base.export_state() is None
    base.restore_state({})
    base.on_run_started(None, "open", 0, 0.0)
    base.on_run_ended(None, "stop", 0, 0.0)
    base.on_position_confirmed(None, 0, "end_stop")
