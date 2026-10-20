"""Unit and trace-replay tests for CoverKinematicsStrategy implementations and fade step routing (#730, #739)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import CoverEntityFeature
from OWNd.message import OWNEvent
from probatio import Invalid

from custom_components.myhome.const import (
    CONF_SLAT_TILT,
    CONF_SLAT_TIME,
    CONF_STRATEGY,
    CONF_TRAVEL_TIME,
    DEFAULT_SLAT_TIME,
    DEFAULT_TRANSITION_MODE,
    MAX_SLAT_TIME,
    MIN_SLAT_TIME,
    TRANSITION_MODE_NATIVE,
    TRANSITION_MODE_SOFTWARE,
)
from custom_components.myhome.cover import MyHOMECover
from custom_components.myhome.cover_strategies import (
    BaseCoverStrategy,
    CoverKinematicsStrategy,
    NativePositionStrategy,
    SimpleUpDownStopStrategy,
    TimedCurtainStrategy,
    VenetianTiltStrategy,
    clamp_slat_time,
    resolve_cover_strategy,
)
from custom_components.myhome.light_fade import SoftwareFadeEngine
from custom_components.myhome.validate import cover_schema

TRACES_DIR = Path(__file__).parent / "fixtures" / "traces"


def _make_mock_entity(where: str = "21") -> MagicMock:
    entity = MagicMock()
    entity._full_where = where
    entity._gateway_handler = MagicMock()
    entity._gateway_handler.log_id = "[TestGW]"
    entity._gateway_handler.send = AsyncMock()
    entity._publish_state = MagicMock()
    entity._refresh_travel_attributes = MagicMock()
    entity._attr_is_opening = False
    entity._attr_is_closing = False
    entity._attr_is_closed = False
    entity._attr_current_cover_position = 50
    entity._attr_current_cover_tilt_position = None
    entity._start_position = 50
    entity._move_start_time = None
    entity._travel_time = 20
    entity._travel_time_up = 20.0
    entity._travel_time_down = 20.0
    entity._min_motor_pulse = 0.4
    entity._slat_tilt = False
    entity._slat_time = DEFAULT_SLAT_TIME
    entity._is_tilting = False
    entity._advanced = False
    entity._echo_until = None
    entity._in_echo_window = MagicMock(return_value=False)
    entity._cancel_stop_task = MagicMock()
    entity._end_pulse_for_takeover = MagicMock()
    return entity


def test_default_transition_mode_remains_software_stepped() -> None:
    """Verify DEFAULT_TRANSITION_MODE stays software_stepped so gateways that NACK Dim 1 speed frames still fade (#739)."""
    assert DEFAULT_TRANSITION_MODE == TRANSITION_MODE_SOFTWARE


def test_software_fade_engine_gating() -> None:
    """Verify SoftwareFadeEngine.should_use_software_stepped suppresses stepped mode only when native is selected."""
    mode = TRANSITION_MODE_NATIVE

    engine = SoftwareFadeEngine(
        where="11",
        create_task_cb=MagicMock(),
        send_instant_cb=AsyncMock(),
        apply_state_cb=MagicMock(),
        update_ha_state_cb=MagicMock(),
        get_worker_count_cb=lambda: 1,
        get_transition_mode_cb=lambda: mode,
        is_on_cb=lambda: True,
    )

    assert engine.should_use_software_stepped(5.0) is False
    mode = TRANSITION_MODE_SOFTWARE
    assert engine.should_use_software_stepped(None) is False
    assert engine.should_use_software_stepped(0.0) is False
    assert engine.should_use_software_stepped(5.0) is True


def test_clamp_slat_time() -> None:
    """Verify clamp_slat_time clamps values and handles invalid inputs."""
    assert clamp_slat_time(None) == DEFAULT_SLAT_TIME
    assert clamp_slat_time("invalid", 2.0) == 2.0
    assert clamp_slat_time(0.01) == MIN_SLAT_TIME
    assert clamp_slat_time(99.0) == MAX_SLAT_TIME
    assert clamp_slat_time(2.5) == 2.5


def test_cover_schema_accepts_strategy_and_zero_travel_time() -> None:
    """Verify cover_schema accepts CONF_STRATEGY and travel_time: 0 while rejecting incompatible combinations."""
    # Simple strategy via travel_time: 0
    cfg_zero = cover_schema({"shutter_a": {"where": "21", "name": "A", CONF_TRAVEL_TIME: 0}})
    assert cfg_zero["2-21"][CONF_TRAVEL_TIME] == 0

    # Explicit strategy names
    for strat_name, extra in (
        ("simple", {CONF_TRAVEL_TIME: 0}),
        ("timed", {CONF_TRAVEL_TIME: 20}),
        ("venetian", {CONF_TRAVEL_TIME: 20, CONF_SLAT_TILT: True, CONF_SLAT_TIME: 1.5}),
        ("native", {"advanced": True}),
    ):
        payload: dict[str, Any] = {"where": "21", "name": "A", CONF_STRATEGY: strat_name, **extra}
        validated = cover_schema({"shutter_a": payload})
        assert validated["2-21"][CONF_STRATEGY] == strat_name

    # Reject unknown strategy name
    with pytest.raises(Invalid):
        cover_schema({"shutter_a": {"where": "21", "name": "A", CONF_STRATEGY: "impulse"}})

    # Reject travel_time: 0 when slat_tilt is enabled without advanced
    with pytest.raises(Invalid):
        cover_schema(
            {
                "shutter_a": {
                    "where": "21",
                    "name": "A",
                    CONF_TRAVEL_TIME: 0,
                    CONF_SLAT_TILT: True,
                }
            }
        )

    # Reject strategy: simple when slat_tilt is enabled
    with pytest.raises(Invalid):
        cover_schema(
            {
                "shutter_a": {
                    "where": "21",
                    "name": "A",
                    CONF_STRATEGY: "simple",
                    CONF_SLAT_TILT: True,
                }
            }
        )


def test_resolve_cover_strategy_variants() -> None:
    """Verify resolve_cover_strategy selects the right strategy for all configurations."""
    # Explicit strategy_name
    assert isinstance(resolve_cover_strategy(strategy_name="simple"), SimpleUpDownStopStrategy)
    assert isinstance(resolve_cover_strategy(strategy_name="native"), NativePositionStrategy)
    assert isinstance(
        resolve_cover_strategy(strategy_name="venetian", slat_time=2.0),
        VenetianTiltStrategy,
    )
    assert isinstance(
        resolve_cover_strategy(strategy_name="timed", travel_time=30),
        TimedCurtainStrategy,
    )
    assert isinstance(
        resolve_cover_strategy(strategy_name="unknown", travel_time=25),
        TimedCurtainStrategy,
    )

    # Implicit resolution
    assert isinstance(resolve_cover_strategy(advanced=True), NativePositionStrategy)
    venetian = resolve_cover_strategy(slat_tilt=True, slat_time=1.8, travel_time=20)
    assert isinstance(venetian, VenetianTiltStrategy)
    assert venetian.slat_time == 1.8

    # Uncalibrated / unconfigured (travel_time <= 0 or None) -> SimpleUpDownStopStrategy
    assert isinstance(resolve_cover_strategy(travel_time=0), SimpleUpDownStopStrategy)
    assert isinstance(resolve_cover_strategy(travel_time=-5), SimpleUpDownStopStrategy)
    assert isinstance(resolve_cover_strategy(travel_time=None), SimpleUpDownStopStrategy)
    assert isinstance(resolve_cover_strategy(travel_time="invalid"), SimpleUpDownStopStrategy)  # type: ignore[arg-type]

    # Positive travel_time -> TimedCurtainStrategy
    timed = resolve_cover_strategy(travel_time=25)
    assert isinstance(timed, TimedCurtainStrategy)
    assert isinstance(timed, CoverKinematicsStrategy)
    assert isinstance(timed, BaseCoverStrategy)


def test_simple_up_down_stop_strategy() -> None:
    """Verify SimpleUpDownStopStrategy properties and protocol methods."""
    strategy = SimpleUpDownStopStrategy()
    entity = _make_mock_entity()

    assert strategy.name == "simple"
    assert strategy.assumed_state is True
    assert strategy.supported_features == (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )
    assert strategy.current_position(entity, 100.0) is None
    assert strategy.position_after(50, 5.0, "open") is None
    assert strategy.duration_for(0, 100, "open") == 0.0
    assert strategy.reanchor_on_stop_echo(entity, {}, 100.0) is None
    assert strategy.travel_for_direction(True) == 0.0
    assert strategy.curtain_travel_for(True) == 0.0
    assert strategy.lead_time_for(True) == 0.0
    assert strategy.carry_overshoot_into_curtain(50, 50, 1.0, "open") == 50


def test_native_position_strategy() -> None:
    """Verify NativePositionStrategy quantization and protocol methods."""
    strategy = NativePositionStrategy()
    entity = _make_mock_entity()

    assert strategy.name == "native"
    assert strategy.assumed_state is False
    assert strategy.supported_features == (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )
    assert strategy.position_after(40, 2.0, "open") == 40
    assert strategy.position_after(40, 2.0, "open", entity=entity) == 50
    assert strategy.duration_for(40, 80, "open") == 0.0
    assert strategy.reanchor_on_stop_echo(entity, {}, 100.0) == 50
    assert strategy.current_position(entity, 100.0) == 50

    # quantize_target
    assert strategy.quantize_target(None, 55) == 55
    assert strategy.quantize_target(50, 100) == 100
    assert strategy.quantize_target(50, 0) == 0
    assert strategy.quantize_target(50, 51) == 52
    assert strategy.quantize_target(50, 49) == 48


def test_timed_curtain_strategy() -> None:
    """Verify TimedCurtainStrategy owns travel_time_up/down and min_motor_pulse without reading back from entity."""
    strategy = TimedCurtainStrategy(
        travel_time=20.0,
        travel_time_up=25.0,
        travel_time_down=20.0,
        min_motor_pulse=0.5,
    )
    entity = _make_mock_entity()

    assert strategy.name == "timed"
    assert strategy.assumed_state is False
    assert strategy.travel_for_direction(True) == 25.0
    assert strategy.travel_for_direction(False) == 20.0
    assert strategy.curtain_travel_for(True) == 25.0
    assert strategy.lead_time_for(True) == 0.0
    assert strategy.min_pulse_for() == 0.5
    assert strategy.quantize_target(50, 51) == 51

    # position_after
    assert strategy.position_after(50, 5.0, "stop") == 50
    assert strategy.position_after(50, 5.0, "open") == 70  # 5 / 25 * 100 = 20% -> 70%
    assert strategy.position_after(50, 5.0, "close") == 25  # 5 / 20 * 100 = 25% -> 25%
    assert strategy.position_after(50, 6.0, "open", lead=1.0) == 70

    # travel_time <= 0 edge cases
    strategy.travel_time_up = 0.0
    assert strategy.position_after(40, 5.0, "open", entity=entity) == 50
    entity._attr_current_cover_position = None
    assert strategy.position_after(40, 5.0, "open", entity=entity) == 40
    assert strategy.duration_for(40, 80, "open", entity=entity) == 0.0
    strategy.travel_time_up = 25.0
    entity._attr_current_cover_position = 50

    # duration_for
    assert strategy.duration_for(50, 50) == 0.0
    assert strategy.duration_for(50, 60, "close") == 0.0
    assert strategy.duration_for(50, 40, "open") == 0.0
    # 1% on 25s up = 0.25s, clamped to min_motor_pulse 0.5s
    assert strategy.duration_for(50, 51, "open") == 0.5
    assert strategy.duration_for(50, 70) == 5.0  # 20% of 25s = 5.0s

    # reanchor_on_stop_echo
    assert strategy.reanchor_on_stop_echo(entity, {}, 100.0) == 50
    ctx_open = {
        "run_started_at": 100.0,
        "run_start_position": 50,
        "is_opening": True,
        "is_closing": False,
        "auto_stop_duration": 4.0,
        "lead": 0.0,
    }
    assert strategy.reanchor_on_stop_echo(entity, ctx_open, 105.0) == 70
    assert entity._attr_current_cover_position == 70
    assert entity._last_run["seconds"] == 5.0

    ctx_close = {
        "run_started_at": 100.0,
        "run_start_position": 20,
        "is_opening": False,
        "is_closing": True,
        "auto_stop_duration": None,
        "lead": 0.0,
    }
    assert strategy.reanchor_on_stop_echo(entity, ctx_close, 104.0) == 0
    assert entity._attr_is_closed is True

    # current_position
    entity._advanced = True
    assert strategy.current_position(entity, 100.0) == 0
    entity._advanced = False
    assert strategy.current_position(entity, 100.0) == 0
    entity._start_position = 20
    entity._move_start_time = 100.0
    entity._attr_is_opening = True
    assert strategy.current_position(entity, 105.0) == 40


def test_venetian_tilt_strategy() -> None:
    """Verify VenetianTiltStrategy shared slat+curtain geometry and pulse helpers."""
    strategy = VenetianTiltStrategy(slat_time=2.0, travel_time=22.0, min_motor_pulse=0.4)
    entity = _make_mock_entity()
    entity._slat_tilt = True

    assert strategy.name == "venetian"
    assert strategy.slat_time == 2.0
    assert strategy.supported_features & CoverEntityFeature.SET_TILT_POSITION

    # Shared geometry: curtain travel = 22.0 - 2.0 = 20.0s
    assert strategy.slat_time_for() == 2.0
    assert strategy.curtain_travel_for(True) == 20.0
    # When travel <= slat_time, curtain_travel_for returns travel
    short_strategy = VenetianTiltStrategy(slat_time=5.0, travel_time=3.0)
    assert short_strategy.curtain_travel_for(True) == 3.0

    # lead_time_for
    entity._is_tilting = True
    assert strategy.lead_time_for(True, entity=entity) == 0.0
    entity._is_tilting = False
    assert strategy.lead_time_for(True) == 2.0  # curr=None -> 0 when opening -> 100% of 2.0s
    assert strategy.lead_time_for(False) == 2.0  # curr=None -> 100 when closing -> 100% of 2.0s
    entity._attr_current_cover_tilt_position = 50
    assert strategy.lead_time_for(True, entity=entity) == 1.0

    # turn_slats_after
    assert strategy.turn_slats_after(0, 1.0, "open") == 50
    assert strategy.turn_slats_after(100, 1.0, "close") == 50
    strategy.slat_time = 0.0
    assert strategy.turn_slats_after(0, 0.5, "open") == 100
    strategy.slat_time = 2.0

    # carry_overshoot_into_curtain
    assert strategy.carry_overshoot_into_curtain(50, 50, 0.5, "open") == 50
    # 1.0s to finish slats + 2.0s overshoot on 20.0s curtain = +10% -> 60%
    assert strategy.carry_overshoot_into_curtain(50, 50, 3.0, "open") == 60
    assert strategy.carry_overshoot_into_curtain(50, 50, 3.0, "close") == 40
    strategy.travel_time_up = 0.0
    assert strategy.carry_overshoot_into_curtain(50, 50, 3.0, "open") == 50
    strategy.travel_time_up = 22.0

    # tilt_pulse_duration_for
    assert strategy.tilt_pulse_duration_for(50, 50) == 0.0
    assert strategy.tilt_pulse_duration_for(50, 55) == 0.0  # 0.1s < 0.4s min_motor_pulse
    assert strategy.tilt_pulse_duration_for(0, 50) == 1.0

    # _reanchor_slats_on_stop_echo
    strategy._reanchor_slats_on_stop_echo(entity, {}, 1.0, False, False)
    ctx_venetian = {
        "run_started_at": 100.0,
        "run_start_position": 50,
        "run_start_tilt": 0,
        "is_opening": True,
        "is_closing": False,
        "auto_stop_duration": None,
        "lead": 2.0,
    }
    # 4.0s elapsed: 2.0s slat rotation (0->100%) + 2.0s curtain travel (2/20 = +10% -> 60%)
    assert strategy.reanchor_on_stop_echo(entity, ctx_venetian, 104.0) == 60
    assert entity._attr_current_cover_tilt_position == 100


@pytest.mark.asyncio
async def test_calibrated_geometry_strategy_subclass_override_is_not_bypassed(hass) -> None:
    """Verify a TimedCurtainStrategy subclass overriding travel_for_direction is used by MyHOMECover."""

    class CalibratedGeometryStrategy(TimedCurtainStrategy):
        def travel_for_direction(self, opening: bool) -> float:
            return 10.0 if opening else 40.0

    gw = MagicMock()
    gw.mac = "00:03:50:00:12:34"
    gw.log_id = "[TestGW]"
    gw.send = AsyncMock()

    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name="Calibrated Cover",
            entity_name="Calibrated Cover",
            device_id="21",
            who="2",
            where="21",
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="Shutter",
            gateway=gw,
            travel_time=20,
        )
        cover._strategy = CalibratedGeometryStrategy(travel_time=20.0)
        cover._attr_current_cover_position = 50
        # 20% upward move (50 -> 70) uses overridden 10.0s -> 2.0s
        assert cover._strategy.duration_for(50, 70, "open", entity=cover) == 2.0
        # 20% downward move (50 -> 30) uses overridden 40.0s -> 8.0s
        assert cover._strategy.duration_for(50, 30, "close", entity=cover) == 8.0


@pytest.mark.asyncio
async def test_myhome_cover_simple_strategy_and_calibration_promotion(hass) -> None:
    """Test MyHOMECover with SimpleUpDownStopStrategy and promotion to TimedCurtainStrategy on calibration."""
    gw = MagicMock()
    gw.mac = "00:03:50:00:12:34"
    gw.log_id = "[TestGW]"
    gw.send = AsyncMock()

    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        simple_cover = MyHOMECover(
            hass=hass,
            name="Simple Cover",
            entity_name="Simple Cover",
            device_id="21",
            who="2",
            where="21",
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="Shutter",
            gateway=gw,
            travel_time=0,
        )
        simple_cover.entity_id = "cover.simple"
        simple_cover.async_schedule_update_ha_state = MagicMock()

        assert simple_cover.strategy.name == "simple"
        assert simple_cover.assumed_state is True
        assert simple_cover.current_cover_position is None

        await simple_cover.async_restore_last_state(MagicMock(state="closed"))
        assert simple_cover.is_closed is True
        await simple_cover.async_restore_last_state(MagicMock(state="open"))
        assert simple_cover.is_closed is False

        await simple_cover.async_set_cover_position(position=100)
        assert simple_cover.is_opening is True
        await simple_cover.async_set_cover_position(position=0)
        assert simple_cover.is_closing is True
        await simple_cover.async_set_cover_position(position=50)
        assert simple_cover.is_closing is False

        await simple_cover.async_open_cover()
        assert simple_cover.is_opening is True
        assert simple_cover.is_closed is False
        await simple_cover.async_close_cover()
        assert simple_cover.is_closing is True
        await simple_cover.async_stop_cover()
        assert simple_cover.is_closing is False

        simple_cover.handle_event(OWNEvent.parse("*2*1*21##"))
        assert simple_cover.is_opening is True
        simple_cover.handle_event(OWNEvent.parse("*2*0*21##"))
        assert simple_cover.is_opening is False

        # Promoting to timed strategy when calibration travel_time is applied
        simple_cover._travel_time = 15
        simple_cover._travel_time_down = 15.0
        simple_cover._travel_time_up = 15.0
        simple_cover._promote_to_timed_strategy()
        assert simple_cover.strategy.name == "timed"
        assert simple_cover.assumed_state is False
        assert bool(simple_cover._attr_supported_features & CoverEntityFeature.SET_POSITION)

        # Calling _enable_slat_tilt() upgrades TimedCurtainStrategy to VenetianTiltStrategy
        simple_cover._enable_slat_tilt()
        assert simple_cover.strategy.name == "venetian"

        # Also test promotion to VenetianTiltStrategy when _slat_tilt is True
        simple_venetian = MyHOMECover(
            hass=hass,
            name="Simple Venetian",
            entity_name="Simple Venetian",
            device_id="22",
            who="2",
            where="22",
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="Shutter",
            gateway=gw,
            travel_time=0,
        )
        simple_venetian._slat_tilt = True
        simple_venetian._travel_time_down = 20.0
        simple_venetian._travel_time_up = 20.0
        simple_venetian._promote_to_timed_strategy()
        assert simple_venetian.strategy.name == "venetian"


@pytest.mark.asyncio
async def test_replay_f461_live_shutter_05_trace_on_timed_strategy(hass) -> None:
    """Replay live BTicino F461 run2_shutter_05_travel_and_reversal trace through TimedCurtainStrategy."""
    trace_path = TRACES_DIR / "f461_live_2026-10-18" / "myhome_trace_F461_live_2026-10-18.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    run2_frames = [
        f
        for f in trace["frames"]
        if f.get("run") == "run2_shutter_05_travel_and_reversal" and f.get("direction") == "rx"
    ]
    assert len(run2_frames) == 18

    gw = MagicMock()
    gw.mac = trace["gateway"]["mac"]
    gw.log_id = "[F461]"
    gw.send = AsyncMock()

    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        shutter = MyHOMECover(
            hass=hass,
            name="Shutter 05",
            entity_name="Shutter 05",
            device_id="05",
            who="2",
            where="05",
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="BT-F411/2",
            gateway=gw,
            travel_time=5,
        )
        shutter.entity_id = "cover.shutter_05"
        shutter.async_schedule_update_ha_state = MagicMock()
        # Start closed (0%) before run2 begins
        shutter._attr_current_cover_position = 0
        shutter._attr_is_closed = True

        positions_on_stop: list[int | None] = []
        for frame in run2_frames:
            ts = float(frame["timestamp"])
            raw = frame["raw"]
            msg = OWNEvent.parse(raw)
            assert msg is not None
            with patch("custom_components.myhome.cover.time.monotonic", return_value=ts):
                shutter.handle_event(msg)
                if raw == "*2*0*05##":
                    positions_on_stop.append(shutter.current_cover_position)

        # After first open pulse (22.72 -> 24.852 = 2.132s of 5.0s -> 43%),
        # second open pulse (26.421 -> 28.014 = 1.593s of 5.0s -> +32% = 75%),
        # and full close reversal (28.119 -> 34.341 = 6.222s -> 0% closed):
        assert 43 in positions_on_stop
        assert 75 in positions_on_stop
        assert positions_on_stop[-1] == 0
        assert shutter.is_closed is True


@pytest.mark.asyncio
async def test_replay_issue_466_f454_trace_on_native_strategy(hass) -> None:
    """Replay F454 Dim 10 / WHAT 0 frames from issue_466 trace fixture through NativePositionStrategy."""
    trace_path = TRACES_DIR / "issue_466" / "config_entry-myhome_F454_step_commands.json"
    diag = json.loads(trace_path.read_text(encoding="utf-8"))
    recent_frames = diag["data"]["bus_monitor"]["recent_frames"]
    where13_frames = [f for f in recent_frames if f.get("where") == "13" and f.get("direction") == "rx"]
    assert len(where13_frames) >= 4

    gw = MagicMock()
    gw.mac = "00:03:50:00:04:54"
    gw.log_id = "[F454]"
    gw.send = AsyncMock()

    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name="Cover 13",
            entity_name="Cover 13",
            device_id="13",
            who="2",
            where="13",
            interface=None,
            advanced=True,
            manufacturer="BTicino",
            model="F411U2",
            gateway=gw,
            strategy_name="native",
        )
        cover.entity_id = "cover.cover_13"
        cover.async_schedule_update_ha_state = MagicMock()
        assert isinstance(cover.strategy, NativePositionStrategy)

        for frame in where13_frames[:4]:
            msg = OWNEvent.parse(frame["raw"])
            assert msg is not None
            with patch(
                "custom_components.myhome.cover.time.monotonic",
                return_value=float(frame["timestamp"]),
            ):
                cover.handle_event(msg)

        assert cover.current_cover_position == 100
        assert cover.is_closed is False
        assert cover.is_opening is False
        assert cover.is_closing is False


@pytest.mark.asyncio
async def test_replay_oracle_who2_venetian_tilt_trace_on_venetian_strategy(hass) -> None:
    """Replay oracle_who2_venetian_tilt trace fixture through VenetianTiltStrategy."""
    trace_path = TRACES_DIR / "oracle_who2_venetian_tilt" / "myhome_trace_oracle_who2_venetian_tilt.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    frames = trace["frames"]
    assert len(frames) == 10

    gw = MagicMock()
    gw.mac = "00:03:50:00:04:54"
    gw.log_id = "[F454]"
    gw.send = AsyncMock()

    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        venetian = MyHOMECover(
            hass=hass,
            name="Venetian 31",
            entity_name="Venetian 31",
            device_id="31",
            who="2",
            where="31",
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="F411/2",
            gateway=gw,
            travel_time=22,
            slat_tilt=True,
            slat_time=2.0,
            strategy_name="venetian",
        )
        venetian.entity_id = "cover.venetian_31"
        venetian.async_schedule_update_ha_state = MagicMock()
        assert isinstance(venetian.strategy, VenetianTiltStrategy)
        venetian._attr_current_cover_position = 0
        venetian._attr_current_cover_tilt_position = 0
        venetian._attr_is_closed = True

        # Replay the 1.0s open pulse (frames[1] *2*1*31## -> frames[2] *2*0*31##):
        # 1.0s of 2.0s slat_time rotates slats 0% -> 50% while curtain stays at 0%
        for frame in frames[1:3]:
            msg = OWNEvent.parse(frame["raw"])
            assert msg is not None
            with patch(
                "custom_components.myhome.cover.time.monotonic",
                return_value=float(frame["timestamp"]),
            ):
                venetian.handle_event(msg)
        assert venetian.current_cover_tilt_position == 50
        assert venetian.current_cover_position == 0

        # Replay the 1.0s close pulse (frames[3] *2*2*31## -> frames[4] *2*0*31##):
        # 1.0s of 2.0s slat_time rotates slats 50% -> 0%
        for frame in frames[3:5]:
            msg = OWNEvent.parse(frame["raw"])
            assert msg is not None
            with patch(
                "custom_components.myhome.cover.time.monotonic",
                return_value=float(frame["timestamp"]),
            ):
                venetian.handle_event(msg)
        assert venetian.current_cover_tilt_position == 0
        assert venetian.current_cover_position == 0


@pytest.mark.asyncio
async def test_light_set_brightness_instant_routes_fade_steps(hass) -> None:
    """Verify MyHOMELight._set_brightness_instant uses gateway.send_fade_step during active fade."""
    from custom_components.myhome.light import MyHOMELight

    gw = MagicMock()
    gw.mac = "00:03:50:00:12:34"
    gw.log_id = "[TestGW]"
    gw.send = AsyncMock()
    gw.send_fade_step = AsyncMock()

    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        dimmer = MyHOMELight(
            hass=hass,
            name="Dimmer 11",
            entity_name="Dimmer 11",
            icon=None,
            icon_on=None,
            device_id="11",
            who="1",
            where="11",
            interface=None,
            dimmable=True,
            manufacturer="BTicino",
            model="Dimmer",
            gateway=gw,
        )
        dimmer.entity_id = "light.dimmer_11"

    # When not fading, routes via gateway.send
    await dimmer._set_brightness_instant(50)
    gw.send.assert_awaited_once()
    gw.send_fade_step.assert_not_awaited()

    # When fading and send_fade_step is an async coroutine function, routes via send_fade_step
    gw.send.reset_mock()
    fake_task = MagicMock()
    fake_task.done.return_value = False
    dimmer._fade_engine.fade_task = fake_task
    await dimmer._set_brightness_instant(60)
    gw.send_fade_step.assert_awaited_once()
    gw.send.assert_not_awaited()


def _make_cover(hass, **kwargs: Any) -> MyHOMECover:
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
        "travel_time": 20,
        **kwargs,
    }
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(**args)
    cover.entity_id = "cover.cover_21"
    cover.async_write_ha_state = MagicMock()
    cover.async_schedule_update_ha_state = MagicMock()
    return cover


@pytest.mark.parametrize(
    "extra",
    [
        {CONF_STRATEGY: "timed", "advanced": True},
        {CONF_STRATEGY: "simple", "advanced": True},
        {CONF_STRATEGY: "venetian", "advanced": True},
        {CONF_STRATEGY: "timed", "advanced_shutter": True},
        {CONF_STRATEGY: "timed", CONF_SLAT_TILT: True},
        {CONF_STRATEGY: "timed", CONF_TRAVEL_TIME: 0},
    ],
)
def test_cover_schema_rejects_strategy_contradicting_flags(extra: dict[str, Any]) -> None:
    """An explicit strategy cannot be combined with a flag that selects another one (#739)."""
    with pytest.raises(Invalid):
        cover_schema({"shutter_a": {"where": "21", "name": "A", **extra}})


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_name", ["timed", "simple", "venetian"])
async def test_entity_flags_follow_explicit_strategy(hass, strategy_name: str) -> None:
    """A non-native strategy never runs the advanced code paths, whatever the flags say (#739)."""
    cover = _make_cover(hass, advanced=True, strategy_name=strategy_name)
    assert cover.strategy.name == strategy_name
    assert cover._advanced is False
    if strategy_name == "simple":
        assert cover._slat_tilt is False


@pytest.mark.asyncio
async def test_native_strategy_keeps_slat_tilt(hass) -> None:
    """An advanced blind with slats stays native and keeps its tilt features."""
    cover = _make_cover(hass, advanced=True, slat_tilt=True)
    assert cover.strategy.name == "native"
    assert cover._advanced is True
    assert cover._slat_tilt is True
    assert cover.supported_features & CoverEntityFeature.SET_TILT_POSITION


@pytest.mark.asyncio
async def test_simple_cover_reports_closed_after_closing_run(hass) -> None:
    """Without a position, a closing run leaves the cover closed and an opening run open (#739)."""
    cover = _make_cover(hass, travel_time=0)
    assert cover.strategy.name == "simple"

    cover.handle_event(OWNEvent.parse("*2*2*21##"))
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover.is_closing is False
    assert cover.is_closed is True
    assert cover.current_cover_position is None

    cover.handle_event(OWNEvent.parse("*2*1*21##"))
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover.is_opening is False
    assert cover.is_closed is False

    # A stop with no run behind it leaves the state alone.
    cover.handle_event(OWNEvent.parse("*2*2*21##"))
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover.is_closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("device_config", "strategy_name"),
    [({CONF_TRAVEL_TIME: 0}, None), ({CONF_TRAVEL_TIME: 25, CONF_STRATEGY: "simple"}, "simple")],
)
async def test_reset_travel_time_demotes_promoted_cover(
    hass, device_config: dict[str, Any], strategy_name: str | None
) -> None:
    """Resetting the calibration of a promoted simple cover makes it simple again (#739)."""
    cover = _make_cover(hass, travel_time=device_config[CONF_TRAVEL_TIME], strategy_name=strategy_name)
    cover._device_config = MagicMock(return_value=device_config)
    cover._fire_calibration_event = MagicMock()
    # Promotion is what calibration does on a travel_time: 0 cover.
    cover._strategy_name = None
    cover._promote_to_timed_strategy()
    cover._travel_time_down = cover._travel_time_up = 15.0
    assert cover.strategy.name == "timed"

    await cover.async_reset_travel_time()

    assert cover.strategy.name == "simple"
    assert cover.assumed_state is True
    assert not cover.supported_features & CoverEntityFeature.SET_POSITION
    assert cover.current_cover_position is None


@pytest.mark.asyncio
async def test_reset_travel_time_keeps_timed_cover_timed(hass) -> None:
    """A cover with a positive yaml travel_time stays timed after a reset."""
    cover = _make_cover(hass, travel_time=20)
    cover._device_config = MagicMock(return_value={CONF_TRAVEL_TIME: 20})
    cover._fire_calibration_event = MagicMock()
    await cover.async_reset_travel_time()
    assert cover.strategy.name == "timed"
    assert cover._travel_time_down == 20.0


@pytest.mark.asyncio
async def test_explicit_simple_strategy_is_not_promoted(hass) -> None:
    """strategy: simple stays simple when calibrated, as it does after a restart (#739)."""
    cover = _make_cover(hass, travel_time=25, strategy_name="simple")
    cover._promote_to_timed_strategy()
    assert cover.strategy.name == "simple"
    restarted = _make_cover(
        hass, travel_time=25, strategy_name="simple", calibration={"down": 15.0, "up": 16.0}
    )
    assert restarted.strategy.name == "simple"


@pytest.mark.asyncio
async def test_simple_cover_publishes_no_travel_time(hass) -> None:
    """A simple cover shows zero travel time, a calibrated one its measured times (#739)."""
    cover = _make_cover(hass, travel_time=0)
    attrs = cover._attr_extra_state_attributes
    assert (attrs["travel_time"], attrs["travel_time_down"], attrs["travel_time_up"]) == (0, 0.0, 0.0)
    cover._promote_to_timed_strategy()
    cover._travel_time_down = 15.0
    cover._travel_time_up = 16.5
    cover._refresh_travel_attributes()
    assert (attrs["travel_time"], attrs["travel_time_down"], attrs["travel_time_up"]) == (15, 15.0, 16.5)


@pytest.mark.asyncio
async def test_reset_travel_time_on_uncalibrated_simple_cover(hass) -> None:
    """Resetting a simple cover that was never calibrated leaves it simple."""
    cover = _make_cover(hass, travel_time=0)
    cover._device_config = MagicMock(return_value={CONF_TRAVEL_TIME: 0})
    cover._fire_calibration_event = MagicMock()
    strategy = cover.strategy
    await cover.async_reset_travel_time()
    assert cover.strategy is strategy
    assert cover.assumed_state is True
    assert cover.current_cover_position is None
