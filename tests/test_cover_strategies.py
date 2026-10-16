"""Unit tests for pluggable cover kinematics strategies and native fade defaults (#730)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.cover import CoverEntityFeature
from OWNd.message import OWNAutomationEvent

from custom_components.myhome.const import (
    DEFAULT_TRANSITION_MODE,
    TRANSITION_MODE_NATIVE,
)
from custom_components.myhome.cover import MyHOMECover
from custom_components.myhome.cover_strategies import (
    CoverKinematicsStrategy,
    ImpulseDoorStrategy,
    NativePositionStrategy,
    SimpleUpDownStopStrategy,
    TimedCurtainStrategy,
    VenetianTiltStrategy,
    resolve_cover_strategy,
)
from custom_components.myhome.light import MyHOMELight


def test_default_transition_mode_is_native() -> None:
    """Default light transition mode is native hardware speed."""
    assert DEFAULT_TRANSITION_MODE == TRANSITION_MODE_NATIVE


async def test_light_software_fade_routes_to_send_fade_step(hass) -> None:
    """When a software fade is active, intermediate steps use gateway.send_fade_step."""
    gateway = MagicMock()
    gateway.log_id = "[gw]"
    gateway.config_entry = None
    gateway.send = AsyncMock()
    gateway.send_fade_step = AsyncMock()

    light = MyHOMELight(
        hass=hass,
        name="Dimmer",
        entity_name="Dimmer",
        icon=None,
        icon_on=None,
        device_id="21",
        who="1",
        where="21",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="Dimmer",
        gateway=gateway,
    )
    # Not fading -> uses gateway.send
    await light._set_brightness_instant(60)
    gateway.send.assert_awaited_once()
    gateway.send_fade_step.assert_not_awaited()

    # Fading -> uses gateway.send_fade_step
    gateway.send.reset_mock()
    fake_task = MagicMock()
    fake_task.done.return_value = False
    light._fade_engine.fade_task = fake_task
    await light._set_brightness_instant(40)
    gateway.send_fade_step.assert_awaited_once()
    gateway.send.assert_not_awaited()


def test_resolve_cover_strategy_variants() -> None:
    """Factory resolves all five cover kinematics strategies by name or flags."""
    assert isinstance(resolve_cover_strategy(advanced=True), NativePositionStrategy)
    assert isinstance(resolve_cover_strategy(advanced=False), TimedCurtainStrategy)
    assert isinstance(resolve_cover_strategy(tilt_time=1.2), VenetianTiltStrategy)
    assert isinstance(resolve_cover_strategy(pulse_duration=0.4), ImpulseDoorStrategy)

    assert isinstance(resolve_cover_strategy(strategy_name="native"), NativePositionStrategy)
    assert isinstance(resolve_cover_strategy(strategy_name="simple"), SimpleUpDownStopStrategy)
    assert isinstance(resolve_cover_strategy(strategy_name="venetian"), VenetianTiltStrategy)
    assert isinstance(resolve_cover_strategy(strategy_name="impulse"), ImpulseDoorStrategy)
    assert isinstance(resolve_cover_strategy(strategy_name="timed"), TimedCurtainStrategy)
    assert isinstance(resolve_cover_strategy(strategy_name="unknown"), TimedCurtainStrategy)


def _make_entity() -> MagicMock:
    entity = MagicMock()
    entity._full_where = "12"
    entity._attr_current_cover_position = 50
    entity._start_position = 50
    entity._move_start_time = None
    entity._attr_is_opening = False
    entity._attr_is_closing = False
    entity._attr_is_closed = False
    entity._travel_for = MagicMock(return_value=20.0)
    entity._gateway_handler = MagicMock()
    entity._gateway_handler.send = AsyncMock(return_value="written")
    entity._async_move = AsyncMock(return_value="moved")
    entity.async_stop_cover = AsyncMock()
    entity.async_set_cover_position = AsyncMock()
    entity.handle_event = MagicMock()
    entity._publish_state = MagicMock()
    return entity


async def test_simple_up_down_stop_strategy() -> None:
    """SimpleUpDownStopStrategy exposes OPEN|CLOSE|STOP without position slider."""
    strat = SimpleUpDownStopStrategy()
    assert isinstance(strat, CoverKinematicsStrategy)
    assert strat.name == "simple"
    assert strat.supported_features == (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    entity = _make_entity()
    assert strat.current_position(entity, 100.0) is None

    await strat.async_open(entity)
    assert entity._attr_is_opening is True
    assert entity._attr_is_closing is False

    await strat.async_close(entity)
    assert entity._attr_is_opening is False
    assert entity._attr_is_closing is True

    await strat.async_stop(entity)
    assert entity._attr_is_opening is False
    assert entity._attr_is_closing is False

    await strat.async_set_position(entity, 0)
    assert entity._attr_is_closing is True
    await strat.async_set_position(entity, 100)
    assert entity._attr_is_opening is True
    await strat.async_set_position(entity, 50)
    assert entity._attr_is_opening is False

    ev_open = OWNAutomationEvent("*2*1*12##")
    strat.handle_event(entity, ev_open, 100.0)
    assert entity._attr_is_opening is True

    ev_close = OWNAutomationEvent("*2*2*12##")
    strat.handle_event(entity, ev_close, 101.0)
    assert entity._attr_is_closing is True

    ev_stop = OWNAutomationEvent("*2*0*12##")
    strat.handle_event(entity, ev_stop, 102.0)
    assert entity._attr_is_opening is False
    assert entity._attr_is_closing is False

    ev_stop_closed = MagicMock(is_opening=False, is_closing=False, is_closed=True)
    strat.handle_event(entity, ev_stop_closed, 103.0)
    assert entity._attr_is_closed is True


async def test_native_position_strategy() -> None:
    """NativePositionStrategy uses Dimension 10/11 hardware level commands."""
    strat = NativePositionStrategy()
    assert isinstance(strat, CoverKinematicsStrategy)
    assert strat.name == "native"
    assert strat.supported_features == (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    entity = _make_entity()
    assert strat.current_position(entity, 100.0) == 50

    await strat.async_open(entity)
    entity._async_move.assert_awaited_once_with("open")
    await strat.async_close(entity)
    entity._async_move.assert_awaited_with("close")
    await strat.async_stop(entity)
    await strat.async_set_position(entity, 0)
    await strat.async_set_position(entity, 65)

    ev_pos = OWNAutomationEvent("*#2*12*10*10*40*0*0##")
    strat.handle_event(entity, ev_pos, 100.0)
    assert entity._attr_current_cover_position == 40
    assert entity._attr_is_closed is False

    ev_open = OWNAutomationEvent("*#2*12*10*11*40*0*0##")
    strat.handle_event(entity, ev_open, 101.0)
    assert entity._attr_is_opening is True

    ev_close = OWNAutomationEvent("*#2*12*10*12*40*0*0##")
    strat.handle_event(entity, ev_close, 102.0)
    assert entity._attr_is_closing is True

    ev_unknown = OWNAutomationEvent("*#2*12*10*10*255*0*0##")
    strat.handle_event(entity, ev_unknown, 103.0)
    assert entity._attr_current_cover_position is None
    assert entity._attr_is_closed is None


async def test_timed_curtain_strategy_and_myhome_cover_binding(hass) -> None:
    """TimedCurtainStrategy interpolates travel and binds to MyHOMECover.strategy."""
    strat = TimedCurtainStrategy()
    assert isinstance(strat, CoverKinematicsStrategy)
    assert strat.name == "timed"

    entity = _make_entity()
    assert strat.current_position(entity, 100.0) == 50

    await strat.async_open(entity)
    await strat.async_close(entity)
    await strat.async_stop(entity)
    await strat.async_set_position(entity, 75)

    ev = OWNAutomationEvent("*2*0*12##")
    strat.handle_event(entity, ev, 100.0)
    entity.handle_event.assert_called_once_with(ev)

    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name="Shutter",
            entity_name="Shutter",
            device_id="12",
            who="2",
            where="12",
            interface=None,  # type: ignore[arg-type]
            advanced=False,
            manufacturer="BTicino",
            model="Shutter",
            gateway=entity._gateway_handler,
        )
        assert isinstance(cover.strategy, TimedCurtainStrategy)


async def test_venetian_tilt_strategy() -> None:
    """VenetianTiltStrategy exposes slat-tilt features and pulses motor for tilt."""
    strat = VenetianTiltStrategy(tilt_time=1.0)
    assert isinstance(strat, CoverKinematicsStrategy)
    assert strat.name == "venetian"
    assert strat.supported_features & CoverEntityFeature.SET_TILT_POSITION

    entity = _make_entity()
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await strat.async_open_tilt(entity)
        assert strat.tilt_position == 100
        mock_sleep.assert_awaited()

        # No-op when already at target
        entity._gateway_handler.send.reset_mock()
        await strat.async_set_tilt_position(entity, 100)
        entity._gateway_handler.send.assert_not_awaited()

        await strat.async_close_tilt(entity)
        assert strat.tilt_position == 0

        await strat.async_stop_tilt(entity)


async def test_impulse_door_strategy() -> None:
    """ImpulseDoorStrategy pulses relay for monostable gates and garage doors."""
    strat = ImpulseDoorStrategy(pulse_duration=0.3)
    assert isinstance(strat, CoverKinematicsStrategy)
    assert strat.name == "impulse"
    assert strat.supported_features == (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    entity = _make_entity()
    assert strat.current_position(entity, 100.0) is None

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await strat.async_open(entity)
        assert entity._attr_is_opening is False
        assert entity._attr_is_closed is False

        await strat.async_close(entity)
        assert entity._attr_is_closing is False
        assert entity._attr_is_closed is True

        await strat.async_stop(entity)
        await strat.async_set_position(entity, 0)
        await strat.async_set_position(entity, 80)

    strat.handle_event(entity, OWNAutomationEvent("*2*1*12##"), 100.0)
    assert entity._attr_is_opening is True
    strat.handle_event(entity, OWNAutomationEvent("*2*2*12##"), 101.0)
    assert entity._attr_is_closing is True
    strat.handle_event(entity, OWNAutomationEvent("*2*0*12##"), 102.0)
    assert entity._attr_is_opening is False
    assert entity._attr_is_closing is False
