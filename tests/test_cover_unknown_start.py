"""A timed cover with no restored state starts unknown, not "open 50 %" (F411/2 on a physical F461 2.0.11)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import ATTR_CURRENT_POSITION, ATTR_CURRENT_TILT_POSITION
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import restore_state
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.helpers.restore_state import RestoredExtraData
from homeassistant.util import dt as dt_util
from OWNd.message import OWNEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome import cover as cover_platform
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.cover import CoverFamily, CoverScope, MyHOMECover, MyHOMEScopeCover


class Clock:
    def __init__(self):
        self.now = 0.0


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def fake_time(clock):
    with patch("custom_components.myhome.cover.time") as mock_time:
        mock_time.monotonic.side_effect = lambda: clock.now
        yield mock_time


@pytest.fixture
def gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:00:05"
    gw.log_id = "[F461 gateway - test]"
    gw.availability_signal = "myhome_avail"
    gw.available = True
    gw.device_registry_id = None
    gw.send_status_request = AsyncMock()
    gw.config_entry = MagicMock()
    gw.config_entry.options = {}
    gw.deliveries = []

    async def _send(message):
        fut = asyncio.get_running_loop().create_future()
        gw.deliveries.append((str(message), fut))
        return fut

    gw.send = AsyncMock(side_effect=_send)
    return gw


async def _yield(n=4):
    for _ in range(n):
        await asyncio.sleep(0)


def _make_cover(hass, gateway, **kwargs):
    """A timed WHO 2 cover as the F461 plant has it: F411/2 at WHERE 05, yaml travel_time 6."""
    kwargs.setdefault("name", "Shutter 05")
    kwargs.setdefault("entity_name", None)
    kwargs.setdefault("device_id", "05")
    kwargs.setdefault("who", "2")
    kwargs.setdefault("where", "05")
    kwargs.setdefault("interface", None)
    kwargs.setdefault("advanced", False)
    kwargs.setdefault("manufacturer", "BTicino")
    kwargs.setdefault("model", "F411/2")
    kwargs.setdefault("travel_time", 6)
    cover = MyHOMECover(hass=hass, gateway=gateway, **kwargs)
    cover.hass = hass
    cover.entity_id = "cover.shutter_05"
    cover.async_write_ha_state = MagicMock()
    cover.async_schedule_update_ha_state = MagicMock()
    return cover


async def _close_from_unknown(cover, gateway, clock):
    """close_cover on a cover with no position, with the F461's direction echo 0.12 s after the write."""
    await cover.async_close_cover()
    raw, written = gateway.deliveries[-1]
    assert raw == "*2*2*05##"
    written.set_result(clock.now)
    await _yield()
    clock.now += 0.12
    cover.handle_event(OWNEvent.parse("*2*2*05##"))  # direction echo (F461: 0.10-0.15 s)
    assert cover.is_closing is True
    assert cover.current_cover_position is None  # still unknown while it runs


async def test_timed_cover_without_history_is_unknown(hass, gateway) -> None:
    """No restored state: no invented 50 %, and HA shows the state as unknown."""
    cover = _make_cover(hass, gateway)
    cover.async_get_last_state = AsyncMock(return_value=None)
    await cover.async_added_to_hass()
    assert cover.current_cover_position is None
    assert cover.is_closed is None
    assert cover.state is None  # rendered "unknown"


async def test_close_from_unknown_reports_closed_after_the_end_stop(hass, gateway, clock, fake_time) -> None:
    """close_cover from an unknown position, the actuator's stop after a full travel: 0 % (closed)."""
    cover = _make_cover(hass, gateway)
    await _close_from_unknown(cover, gateway, clock)
    clock.now += 6.5
    cover.handle_event(OWNEvent.parse("*2*0*05##"))  # the actuator stops at its end stop
    assert cover.current_cover_position == 0
    assert cover.is_closed is True


async def test_stop_from_ha_during_a_run_from_unknown_stays_unknown(hass, gateway, clock, fake_time) -> None:
    """stop_cover before the end stop: the run is measured, but no position is invented from it."""
    cover = _make_cover(hass, gateway)
    await _close_from_unknown(cover, gateway, clock)
    clock.now += 2.0
    await cover.async_stop_cover()
    raw, stop_written = gateway.deliveries[-1]
    assert raw == "*2*0*05##"
    stop_written.set_result(clock.now)
    await _yield()
    assert cover.current_cover_position is None
    assert cover.is_closed is None
    clock.now += 0.05
    cover.handle_event(OWNEvent.parse("*2*0*05##"))  # the relayed stop status of our own stop
    assert cover.current_cover_position is None
    assert cover.is_closed is None
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(2.05)
    assert cover.extra_state_attributes["last_run_direction"] == "close"


async def test_partial_run_from_unknown_stays_unknown(hass, gateway, clock, fake_time) -> None:
    """A keypad stop before a full travel leaves nothing to anchor: still unknown, not a guess."""
    cover = _make_cover(hass, gateway)
    cover.handle_event(OWNEvent.parse("*2*1*05##"))  # opened from a wall switch
    clock.now += 2.0
    cover.handle_event(OWNEvent.parse("*2*0*05##"))
    assert cover.current_cover_position is None
    assert cover.is_closed is None
    # A full run anchors it.
    cover.handle_event(OWNEvent.parse("*2*1*05##"))
    clock.now += 7.0
    cover.handle_event(OWNEvent.parse("*2*0*05##"))
    assert cover.current_cover_position == 100
    assert cover.is_closed is False


async def test_set_position_before_the_position_is_known(hass, gateway, clock, fake_time) -> None:
    """A partial target is refused without a frame; 0 % and 100 % run to the end stop."""
    cover = _make_cover(hass, gateway)
    with pytest.raises(ServiceValidationError) as err:
        await cover.async_set_cover_position(position=40)
    assert err.value.translation_key == "cover_position_unknown"
    assert gateway.deliveries == []

    await cover.async_set_cover_position(position=0)
    assert [raw for raw, _ in gateway.deliveries] == ["*2*2*05##"]
    cover._cancel_stop_task()
    await cover.async_set_cover_position(position=100)
    assert [raw for raw, _ in gateway.deliveries] == ["*2*2*05##", "*2*1*05##"]
    cover._cancel_stop_task()


async def test_restored_position_is_kept(hass, gateway) -> None:
    """Restore from the last state still wins over unknown."""
    cover = _make_cover(hass, gateway)
    cover.async_get_last_state = AsyncMock(return_value=State("cover.x", "open", {ATTR_CURRENT_POSITION: 30}))
    await cover.async_added_to_hass()
    assert cover.current_cover_position == 30
    assert cover.is_closed is False


async def test_partial_run_from_a_known_position_keeps_a_known_position(hass, gateway, clock, fake_time) -> None:
    """Regression guard: a known start (50 %) is still timed; only an unknown start stays unknown."""
    cover = _make_cover(hass, gateway)
    cover._attr_current_cover_position = 50
    cover._attr_is_closed = False
    cover.handle_event(OWNEvent.parse("*2*1*05##"))  # opened from a wall switch
    assert cover.is_opening is True
    assert cover.current_cover_position is not None  # timed from the known 50 while it runs
    clock.now += 2.0
    cover.handle_event(OWNEvent.parse("*2*0*05##"))  # stopped before the end stop
    assert cover.current_cover_position is not None
    assert 50 < cover.current_cover_position < 100
    assert cover.is_closed is False


async def test_delivery_failure_from_unknown_keeps_the_state_unknown(hass, gateway) -> None:
    """A close whose frame never reached the bus leaves the cover unknown, not open (review 5639677952)."""
    cover = _make_cover(hass, gateway)
    await cover.async_close_cover()
    raw, written = gateway.deliveries[-1]
    assert raw == "*2*2*05##"
    assert cover.is_closing is True
    written.set_exception(ConnectionError("gateway connection lost"))
    await _yield()
    assert cover.is_closing is False
    assert cover.is_closed is None
    assert cover.current_cover_position is None
    assert cover.state is None  # rendered "unknown"
    assert len(gateway.deliveries) == 1  # nothing else was sent


async def test_delivery_timeout_from_unknown_keeps_the_state_unknown(hass, gateway) -> None:
    """set_cover_position 0 whose frame is never written: the settle task aborts to unknown."""
    cover = _make_cover(hass, gateway)
    with patch("custom_components.myhome.cover.WRITE_TIMEOUT", 0.01):
        await cover.async_set_cover_position(position=0)
        raw, written = gateway.deliveries[-1]
        assert raw == "*2*2*05##"
        assert cover.is_closing is True
        await asyncio.wait_for(cover._stop_task, 1.0)  # the never-written frame times out
    assert cover.is_closing is False
    assert cover.is_closed is None
    assert cover.current_cover_position is None
    assert cover.state is None
    assert len(gateway.deliveries) == 1
    written.cancel()


async def test_delivery_failure_from_a_restored_closed_state_restores_it(hass, gateway) -> None:
    """A simple cover restored as closed goes back to closed when its open frame is lost."""
    cover = _make_cover(hass, gateway, travel_time=0)
    cover.async_get_last_state = AsyncMock(return_value=State("cover.x", "closed", {}))
    await cover.async_added_to_hass()
    assert cover.is_closed is True
    await cover.async_open_cover()
    raw, written = gateway.deliveries[-1]
    assert raw == "*2*1*05##"
    assert cover.is_closed is False  # optimistic while the frame is queued
    written.set_exception(ConnectionError("gateway connection lost"))
    await _yield()
    assert cover.is_opening is False
    assert cover.is_closed is True


async def test_promotion_from_simple_forgets_the_optimistic_state(hass, gateway, clock, fake_time) -> None:
    """travel_time 0 pulsed closed, then given a travel time: timed and unknown, not closed with no position."""
    cover = _make_cover(hass, gateway, travel_time=0)
    assert cover.strategy.name == "simple"
    cover.handle_event(OWNEvent.parse("*2*2*05##"))  # a brief keypad pulse down
    clock.now += 0.5
    cover.handle_event(OWNEvent.parse("*2*0*05##"))
    assert cover.is_closed is True  # the optimistic state of an assumed-state cover
    assert cover.current_cover_position is None
    cover._persist_calibration = MagicMock()
    await cover.async_set_travel_time(6.0, 6.0)
    assert cover.strategy.name == "timed"
    assert cover.assumed_state is False
    assert cover.current_cover_position is None
    assert cover.is_closed is None  # nothing says where the 0.5 s pulse left it
    assert cover.state is None
    with pytest.raises(ServiceValidationError) as err:
        await cover.async_set_cover_position(position=40)
    assert err.value.translation_key == "cover_position_unknown"
    assert gateway.deliveries == []


async def test_stop_echo_after_the_travel_time_anchors_the_end_stop(hass, gateway, clock, fake_time, caplog) -> None:
    """stop_cover written just before the travel time, its status relayed just after: the end stop anchors 0."""
    caplog.set_level("DEBUG", logger="custom_components.myhome")
    cover = _make_cover(hass, gateway)
    await _close_from_unknown(cover, gateway, clock)
    clock.now += 5.85  # 5.85 s into a 6.0 s travel: the provisional freeze knows no position
    await cover.async_stop_cover()
    raw, stop_written = gateway.deliveries[-1]
    assert raw == "*2*0*05##"
    stop_written.set_result(clock.now)
    await _yield()
    assert cover.current_cover_position is None
    clock.now += 0.2  # the relayed stop status lands at 6.05 s, past the travel time
    cover.handle_event(OWNEvent.parse("*2*0*05##"))
    assert cover.current_cover_position == 0
    assert cover.is_closed is True
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(6.05)
    assert "re-anchored to 0%" in caplog.text
    assert "None%" not in caplog.text


async def test_stop_echo_of_a_partial_run_from_unknown_logs_no_position(hass, gateway, clock, fake_time, caplog) -> None:
    """The debug line of a stop echo that anchors nothing says so instead of printing None%."""
    caplog.set_level("DEBUG", logger="custom_components.myhome")
    cover = _make_cover(hass, gateway)
    await _close_from_unknown(cover, gateway, clock)
    clock.now += 2.0
    await cover.async_stop_cover()
    gateway.deliveries[-1][1].set_result(clock.now)
    await _yield()
    clock.now += 0.05
    cover.handle_event(OWNEvent.parse("*2*0*05##"))
    assert cover.current_cover_position is None
    assert "position still unknown" in caplog.text
    assert "None%" not in caplog.text


def _make_blind(hass, gateway):
    """The same actuator as a Venetian blind: slat_tilt with the default 2.0 s slat time."""
    return _make_cover(hass, gateway, name="Blind 05", slat_tilt=True, slat_time=2.0)


async def test_fresh_blind_refuses_a_partial_tilt_and_runs_an_end_to_the_slat_stop(hass, gateway, clock, fake_time) -> None:
    """No slat angle to time a pulse from: 50 % / 20 % are refused with no frame, 100 % runs the full slat_time."""
    cover = _make_blind(hass, gateway)
    assert cover.current_cover_tilt_position is None
    for target in (50, 20):
        with pytest.raises(ServiceValidationError) as err:
            await cover.async_set_cover_tilt_position(tilt_position=target)
        assert err.value.translation_key == "cover_tilt_position_unknown"
    assert gateway.deliveries == []  # 50 % was not a silent 0-frame no-op either: it was refused
    assert cover.current_cover_tilt_position is None

    with patch("asyncio.sleep", new=AsyncMock()):
        await cover.async_set_cover_tilt_position(tilt_position=100)
        raw, written = gateway.deliveries[-1]
        assert raw == "*2*1*05##"
        assert cover._tilt_duration == pytest.approx(2.0)  # a full rotation to the slat stop
        assert cover.current_cover_tilt_position is None  # unknown until the stop
        written.set_result(clock.now)
        await _yield()
        cover.handle_event(OWNEvent.parse("*2*1*05##"))  # motor start echo
        await cover._stop_task
    raw, stop_written = gateway.deliveries[-1]
    assert raw == "*2*0*05##"
    clock.now += 2.0
    stop_written.set_result(clock.now)
    await _yield()
    assert cover.current_cover_tilt_position == 100
    assert cover.current_cover_position is None  # the slat pulse says nothing about the curtain


async def test_fresh_blind_pulse_stopped_before_the_slat_stop_stays_unknown(hass, gateway, clock, fake_time) -> None:
    """A full pulse towards 0 % stopped halfway anchors nothing; the next full pulse does."""
    cover = _make_blind(hass, gateway)
    await cover.async_set_cover_tilt_position(tilt_position=0)
    raw, written = gateway.deliveries[-1]
    assert raw == "*2*2*05##"
    written.set_result(clock.now)
    await _yield()
    cover.handle_event(OWNEvent.parse("*2*2*05##"))
    clock.now += 1.0
    await cover.async_stop_cover_tilt()
    raw, stop_written = gateway.deliveries[-1]
    assert raw == "*2*0*05##"
    stop_written.set_result(clock.now)
    await _yield()
    assert cover._is_tilting is False
    assert cover.current_cover_tilt_position is None  # 1.0 s of a 2.0 s rotation from nowhere
    with pytest.raises(ServiceValidationError):
        await cover.async_set_cover_tilt_position(tilt_position=50)

    await cover.async_set_cover_tilt_position(tilt_position=0)
    raw, written = gateway.deliveries[-1]
    assert raw == "*2*2*05##"
    written.set_result(clock.now)
    await _yield()
    cover.handle_event(OWNEvent.parse("*2*2*05##"))
    clock.now += 2.0
    await cover.async_stop_cover_tilt()
    gateway.deliveries[-1][1].set_result(clock.now)
    await _yield()
    assert cover.current_cover_tilt_position == 0


def test_stop_echo_end_stop_from_unknown_is_measured_on_the_real_run() -> None:
    """A scheduled auto-stop duration is no evidence that the motor ran that long: no end stop is fabricated."""
    from custom_components.myhome.cover_strategies.timed import TimedCurtainStrategy

    strategy = TimedCurtainStrategy(travel_time=6.0, travel_time_up=6.0, travel_time_down=6.0)
    entity = MagicMock()
    entity._attr_current_cover_position = None
    entity._gateway_handler.log_id = "[test]"
    ctx = {
        "run_started_at": 100.0,
        "run_start_position": None,
        "is_opening": False,
        "is_closing": True,
        "auto_stop_duration": 6.0,  # the stop that was scheduled
        "lead": 0.0,
    }
    # Stopped from the bus 2 s in: the status echo arrives at 102.05 s.
    assert strategy.reanchor_on_stop_echo(entity, dict(ctx), 102.05) is None
    assert entity._attr_current_cover_position is None
    assert entity._last_run["seconds"] == pytest.approx(6.0)  # the booked run, as before
    # The motor really ran the travel time: the end stop anchors 0.
    assert strategy.reanchor_on_stop_echo(entity, dict(ctx), 106.05) == 0
    assert entity._attr_current_cover_position == 0
    assert entity._attr_is_closed is True


async def test_full_tilt_from_unknown_that_runs_long_moves_a_known_curtain(hass, gateway, clock, fake_time) -> None:
    """A blind whose position is known but whose angle is not: a late stop past the rotation lifts the curtain."""
    cover = _make_blind(hass, gateway)
    cover._attr_current_cover_position = cover._start_position = 50  # restored position, no restored angle
    assert cover.current_cover_tilt_position is None
    await cover.async_set_cover_tilt_position(tilt_position=100)
    raw, written = gateway.deliveries[-1]
    assert raw == "*2*1*05##"
    written.set_result(clock.now)
    await _yield()
    cover.handle_event(OWNEvent.parse("*2*1*05##"))
    clock.now += 3.0  # the 2.0 s rotation, then 1.0 s of the 4.0 s curtain travel (travel_time 6 - slat_time 2)
    await cover.async_stop_cover_tilt()
    gateway.deliveries[-1][1].set_result(clock.now)
    await _yield()
    assert cover.current_cover_tilt_position == 100
    assert cover.current_cover_position == 75


def _controlled_sleep(release: asyncio.Event):
    """asyncio.sleep stand-in: zero-length sleeps yield, longer ones wait for ``release``."""
    real_sleep = asyncio.sleep

    async def fake_sleep(duration, *args, **kwargs):
        if duration <= 0:
            await real_sleep(0)
            return
        await release.wait()

    return fake_sleep


async def test_close_from_unknown_settles_when_the_actuator_stops_early(hass, gateway, clock, fake_time) -> None:
    """The F411/2 stops itself at 5 s while travel_time is 6: the end position settles on the timer (review 5642333408)."""
    cover = _make_cover(hass, gateway)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            await _close_from_unknown(cover, gateway, clock)
            clock.now += 5.0
            cover.handle_event(OWNEvent.parse("*2*0*05##"))  # the actuator's own timer, before travel_time
            assert cover.is_closing is True  # the run is still settling
            assert cover.current_cover_position is None
            assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(5.0)  # measured anyway
            clock.now += 3.1  # travel_time + END_SETTLE_MARGIN passed
        finally:
            release.set()
        await asyncio.wait_for(cover._stop_task, 1.0)
    assert cover.is_closing is False
    assert cover.current_cover_position == 0
    assert cover.is_closed is True
    assert [raw for raw, _ in gateway.deliveries] == ["*2*2*05##"]  # no stop frame was sent


async def test_open_from_unknown_settles_on_the_timer_without_a_stop_status(hass, gateway, clock, fake_time) -> None:
    """open_cover from unknown on a gateway that relays no stop status: 100 % after travel_time + 2 s."""
    cover = _make_cover(hass, gateway)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            await cover.async_open_cover()
            raw, written = gateway.deliveries[-1]
            assert raw == "*2*1*05##"
            written.set_result(clock.now)
            await _yield()
            clock.now += 0.12
            cover.handle_event(OWNEvent.parse("*2*1*05##"))  # direction echo, then silence
            await _yield()
            assert cover.is_opening is True
            assert cover.current_cover_position is None
            clock.now += 8.1
        finally:
            release.set()
        await asyncio.wait_for(cover._stop_task, 1.0)
    assert cover.current_cover_position == 100
    assert cover.is_closed is False
    assert cover.is_opening is False


async def test_early_actuator_stop_of_a_known_run_still_freezes_the_estimate(hass, gateway, clock, fake_time) -> None:
    """Regression guard: from a known position the early stop still ends the run at the timed estimate."""
    cover = _make_cover(hass, gateway)
    cover._attr_current_cover_position = 100
    cover._start_position = 100
    cover._attr_is_closed = False
    await cover.async_close_cover()
    raw, written = gateway.deliveries[-1]
    assert raw == "*2*2*05##"
    written.set_result(clock.now)
    await _yield()
    clock.now += 0.12
    cover.handle_event(OWNEvent.parse("*2*2*05##"))
    clock.now += 3.0
    cover.handle_event(OWNEvent.parse("*2*0*05##"))  # stopped halfway
    assert cover.is_closing is False
    assert cover.current_cover_position == 50


async def test_restored_open_without_a_position_stays_open_and_unknown(hass, gateway) -> None:
    """A last state "open" with no position says nothing about the height: open, position unknown (review 5642333408)."""
    cover = _make_cover(hass, gateway)
    cover.async_get_last_state = AsyncMock(return_value=State("cover.x", "open", {}))
    await cover.async_added_to_hass()
    assert cover.current_cover_position is None
    assert cover.is_closed is False
    assert cover.state == "open"
    with pytest.raises(ServiceValidationError) as err:
        await cover.async_set_cover_position(position=50)
    assert err.value.translation_key == "cover_position_unknown"
    assert gateway.deliveries == []


async def test_restored_closed_without_a_position_is_still_closed(hass, gateway) -> None:
    """"closed" is a height: a restored closed state without a position is 0 %."""
    cover = _make_cover(hass, gateway)
    cover.async_get_last_state = AsyncMock(return_value=State("cover.x", "closed", {}))
    await cover.async_added_to_hass()
    assert cover.current_cover_position == 0
    assert cover.is_closed is True


async def test_reversal_during_a_settling_run_from_unknown_ends_at_the_next_stop(hass, gateway, clock, fake_time) -> None:
    """A keypad reversal cancels the settle; the stop that follows ends the reversed run (no stuck closing)."""
    cover = _make_cover(hass, gateway)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            await _close_from_unknown(cover, gateway, clock)
            clock.now += 2.0
            cover.handle_event(OWNEvent.parse("*2*1*05##"))  # somebody reversed it from the keypad
            assert cover.is_opening is True
            clock.now += 1.0
            cover.handle_event(OWNEvent.parse("*2*0*05##"))  # and stopped it
        finally:
            release.set()
    assert cover.is_opening is False
    assert cover.is_closing is False
    assert cover.current_cover_position is None  # still unknown: no end stop was reached


# ------------------------------------------------------------------ members of a scope cover on the main bus


def _make_area(hass, gateway, members):
    """Area 1 on the main bus (no F422): one scope frame moves every member."""
    family = CoverFamily()
    area = MyHOMEScopeCover(
        hass=hass, gateway=gateway, name="Area 1", entity_name=None, device_id="1", who="2", where="1",
        interface=None, advanced=False, manufacturer="BTicino", model="Area", travel_time=6,
        scope=CoverScope.of("1"),
    )
    area.hass = hass
    area.entity_id = "cover.area_1"
    area.async_write_ha_state = MagicMock()
    area.async_schedule_update_ha_state = MagicMock()
    for cover in (*members, area):
        family.add(cover)
    return area


def _make_members(hass, gateway):
    return [
        _make_cover(hass, gateway, name=f"Shutter {where}", device_id=where, where=where)
        for where in ("11", "12")
    ]


async def _open_area_from_unknown(area, members, gateway, clock):
    """open_cover on the area: one scope frame, relayed by the gateway to the scope cover and its members."""
    await area.async_open_cover()
    raw, written = gateway.deliveries[-1]
    assert raw == "*2*1*1##"
    assert all(m.is_opening and m._end_run_from_unknown for m in members)
    written.set_result(clock.now)
    await _yield()
    clock.now += 0.12
    for cover in (area, *members):
        cover.handle_event(OWNEvent.parse("*2*1*1##"))
    assert area.is_opening is True
    assert all(m.is_opening and m.current_cover_position is None for m in members)


async def test_scope_open_on_the_main_bus_settles_members_whose_actuator_stops_early(hass, gateway, clock, fake_time) -> None:
    """The area's one frame moves members with no position: an F411/2 stop at 5 s settles them on the timer, as a direct open does."""
    members = _make_members(hass, gateway)
    area = _make_area(hass, gateway, members)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            await _open_area_from_unknown(area, members, gateway, clock)
            clock.now += 5.0
            for member in members:
                member.handle_event(OWNEvent.parse(f"*2*0*{member._where}##"))  # each actuator's own timer
            assert all(m.is_opening and m.current_cover_position is None for m in members)  # still settling
            assert members[0].extra_state_attributes["last_run_seconds"] == pytest.approx(5.0)
            clock.now += 3.1  # travel_time + END_SETTLE_MARGIN passed
        finally:
            release.set()
        await asyncio.wait_for(asyncio.gather(*(m._stop_task for m in members)), 1.0)
    assert [m.current_cover_position for m in members] == [100, 100]
    assert all(m.is_closed is False and m.is_opening is False for m in members)
    assert area.current_cover_position == 100
    assert area.is_closed is False
    assert area.is_opening is False
    assert [raw for raw, _ in gateway.deliveries] == ["*2*1*1##"]  # no per-member frame, no stop frame


async def test_scope_close_on_the_main_bus_leaves_a_known_member_to_the_actuator_stop(hass, gateway, clock, fake_time) -> None:
    """An early stop freezes a known member's interpolated position while an unknown member keeps settling."""
    members = _make_members(hass, gateway)
    known, unknown = members
    known._attr_current_cover_position = known._start_position = 100
    known._attr_is_closed = False
    area = _make_area(hass, gateway, members)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            await area.async_close_cover()
            raw, written = gateway.deliveries[-1]
            assert raw == "*2*2*1##"
            assert known._end_run_from_unknown is False and known._stop_task is not None
            assert unknown._end_run_from_unknown is True
            written.set_result(clock.now)
            await _yield()
            clock.now += 0.12
            for cover in (area, *members):
                cover.handle_event(OWNEvent.parse("*2*2*1##"))
            clock.now += 3.0
            for member in members:
                member.handle_event(OWNEvent.parse(f"*2*0*{member._where}##"))
            assert known._stop_task is None
            assert known.is_closing is False
            assert known.current_cover_position == 50  # halfway down a 6 s travel from 100
            assert unknown.is_closing is True
            assert unknown.current_cover_position is None
            clock.now += 5.1
        finally:
            release.set()
        await asyncio.wait_for(unknown._stop_task, 1.0)
    assert unknown.current_cover_position == 0
    assert unknown.is_closed is True
    assert area.current_cover_position == 25
    assert area.is_closed is False


async def test_scope_frame_that_fails_at_once_leaves_the_members_as_they_were(hass, gateway, clock, fake_time) -> None:
    """A scope frame refused on queueing: the members were not moved and go back to unknown, nothing settles."""
    members = _make_members(hass, gateway)
    area = _make_area(hass, gateway, members)

    async def _refuse(message):
        fut = asyncio.get_running_loop().create_future()
        fut.set_exception(ConnectionError("gateway connection lost"))
        gateway.deliveries.append((str(message), fut))
        return fut

    gateway.send = AsyncMock(side_effect=_refuse)
    with pytest.raises(HomeAssistantError):
        await area.async_open_cover()
    assert [raw for raw, _ in gateway.deliveries] == ["*2*1*1##"]
    for member in members:
        assert member.is_opening is False
        assert member.is_closed is None
        assert member.current_cover_position is None
        assert member._end_run_from_unknown is False
        assert member._stop_task is None
    assert area.is_opening is False


async def test_scope_frame_lost_after_queueing_aborts_the_members_runs(hass, gateway, clock, fake_time) -> None:
    """The scope frame's delivery fails later: each member's tracked write aborts its run, as an own command would."""
    members = _make_members(hass, gateway)
    area = _make_area(hass, gateway, members)
    await area.async_open_cover()
    raw, written = gateway.deliveries[-1]
    assert raw == "*2*1*1##"
    assert all(m.is_opening for m in members)
    written.set_exception(ConnectionError("gateway connection lost"))
    await _yield()
    for member in members:
        assert member.is_opening is False
        assert member.is_closed is None
        assert member.current_cover_position is None
        assert member._end_run_from_unknown is False
    assert area.is_opening is False


async def test_scope_set_position_0_on_the_main_bus_settles_the_members_too(hass, gateway, clock, fake_time) -> None:
    """`set_cover_position: 0` on the area is a full run of the scope: its members with no position settle like its own run."""
    members = _make_members(hass, gateway)
    area = _make_area(hass, gateway, members)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            await area.async_set_cover_position(position=0)
            raw, written = gateway.deliveries[-1]
            assert raw == "*2*2*1##"
            assert all(m.is_closing and m._end_run_from_unknown for m in members)
            assert area._stop_task is not None  # the scope's own settle, as before
            written.set_result(clock.now)
            await _yield()
            clock.now += 0.12
            for cover in (area, *members):
                cover.handle_event(OWNEvent.parse("*2*2*1##"))
            clock.now += 5.0
            for member in members:
                member.handle_event(OWNEvent.parse(f"*2*0*{member._where}##"))
            assert all(m.is_closing and m.current_cover_position is None for m in members)
            clock.now += 3.1
        finally:
            release.set()
        await asyncio.wait_for(asyncio.gather(area._stop_task, *(m._stop_task for m in members)), 1.0)
    assert [m.current_cover_position for m in members] == [0, 0]
    assert area.current_cover_position == 0
    assert area.is_closed is True


async def test_scope_stop_on_the_main_bus_leaves_the_settling_members_unknown(hass, gateway, clock, fake_time) -> None:
    """stop_cover on the area before the end stop: the relayed stop ends each member's run, no position is invented."""
    members = _make_members(hass, gateway)
    area = _make_area(hass, gateway, members)
    await _open_area_from_unknown(area, members, gateway, clock)
    clock.now += 2.0
    await area.async_stop_cover()
    raw, stop_written = gateway.deliveries[-1]
    assert raw == "*2*0*1##"
    assert all(m._end_run_from_unknown is False and m._stop_task is None for m in members)
    stop_written.set_result(clock.now)
    await _yield()
    clock.now += 0.05
    for member in members:
        member.handle_event(OWNEvent.parse(f"*2*0*{member._where}##"))  # the relayed stop status
        assert member.is_opening is False
        assert member.current_cover_position is None
        assert member.is_closed is None
        assert member.extra_state_attributes["last_run_seconds"] == pytest.approx(2.05)
    assert area.is_opening is False
    assert area.current_cover_position is None


# -- A state saved while the gateway is unreachable has no attributes (CL-8 of the 2026-11-18 audit) --
#
# Home Assistant writes "unavailable" without the entity's state attributes
# (homeassistant/helpers/entity.py, Entity.__async_calculate_state: `if available:`), and
# that is the state the restore cache keeps at the 15-minute dump, on an entry reload and
# at a stop. Seen on a live F461 2.0.11: four covers restored with their positions, the
# gateway became unreachable, the entry was reloaded, and all four came back `unknown`.


def _restore_gateway():
    """A gateway stand-in whose availability the test switches, usable on a real platform."""
    gw = MagicMock()
    gw.mac = gw.unique_id = "00:03:50:00:00:05"
    gw.log_id = "[F461 gateway - test]"
    gw.availability_signal = "myhome_avail"
    gw.device_registry_id = None
    gw.device_health = None
    gw.send_status_request = AsyncMock()
    gw.config_entry = MagicMock()
    gw.config_entry.options = {}
    gw.reachable = True
    gw.is_who_available = MagicMock(side_effect=lambda who: gw.reachable)
    gw.sweep_active = False
    gw.initial_discovery_pending = False
    return gw


def _cover_platform(hass) -> EntityPlatform:
    platform = EntityPlatform(
        hass=hass,
        logger=logging.getLogger(__name__),
        domain="cover",
        platform_name=DOMAIN,
        platform=cover_platform,
        scan_interval=timedelta(seconds=30),
        entity_namespace=None,
    )
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": "00:03:50:00:00:05"}, unique_id="00:03:50:00:00:05")
    entry.add_to_hass(hass)
    platform.config_entry = entry
    return platform


def _platform_cover(hass, gw, **kwargs) -> MyHOMECover:
    kwargs.setdefault("travel_time", 6)
    return MyHOMECover(
        hass=hass, gateway=gw, name="Shutter 05", entity_name=None, device_id="05", who="2", where="05",
        interface=None, advanced=False, manufacturer="BTicino", model="F411/2", **kwargs,
    )


async def test_position_survives_a_reload_while_the_gateway_is_unreachable(hass) -> None:
    """Through Home Assistant's own state machine and restore cache: 40 % stays 40 %."""
    gw = _restore_gateway()
    platform = _cover_platform(hass)
    cover = _platform_cover(hass, gw, slat_tilt=True, slat_time=1.4)
    cover._attr_current_cover_position = cover._start_position = 40
    cover._attr_current_cover_tilt_position = 15
    cover._attr_is_closed = False
    cover._last_run = {"seconds": 2.05, "direction": "close", "ended_at": "2026-11-18T02:19:18.000+00:00"}
    cover._refresh_travel_attributes()
    await platform.async_add_entities([cover])
    await hass.async_block_till_done()
    entity_id = cover.entity_id
    assert hass.states.get(entity_id).attributes[ATTR_CURRENT_POSITION] == 40

    gw.reachable = False
    cover.async_write_ha_state()
    unavailable = hass.states.get(entity_id)
    assert unavailable.state == "unavailable"
    assert ATTR_CURRENT_POSITION not in unavailable.attributes  # what Home Assistant keeps of it
    assert "last_run_seconds" not in unavailable.attributes

    await platform.async_remove_entity(entity_id)  # the entry reloads, or Home Assistant stops
    restore_data = restore_state.async_get(hass)
    # HA 2026.11+ (core#183906) exposes async_get_stored_state(entity_id); HA <= 2026.10 uses .last_states.
    get_stored = getattr(restore_data, "async_get_stored_state", None)
    stored = get_stored(entity_id) if callable(get_stored) else restore_data.last_states[entity_id]
    assert stored is not None
    assert stored.state.state == "unavailable"
    assert stored.extra_data.as_dict() == {
        "position": 40,
        "is_closed": False,
        "tilt": 15,
        "last_run": {"seconds": 2.05, "direction": "close", "ended_at": "2026-11-18T02:19:18.000+00:00"},
    }

    gw.reachable = True
    restarted = _platform_cover(hass, gw, slat_tilt=True, slat_time=1.4)
    await platform.async_add_entities([restarted])
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "open"
    assert state.attributes[ATTR_CURRENT_POSITION] == 40
    assert state.attributes[ATTR_CURRENT_TILT_POSITION] == 15
    assert state.attributes["last_run_seconds"] == 2.05
    assert state.attributes["last_run_direction"] == "close"
    assert state.attributes["last_run_ended_at"] == "2026-11-18T02:19:18.000+00:00"
    restarted._check_position_target(50)  # a partial target is no longer refused
    await platform.async_remove_entity(entity_id)


async def test_last_run_survives_a_restart_with_the_gateway_reachable(hass) -> None:
    """B3-4: `last_run_*` came back empty after every restart; the state attributes were never read."""
    gw = _restore_gateway()
    platform = _cover_platform(hass)
    cover = _platform_cover(hass, gw)
    cover._attr_current_cover_position = cover._start_position = 0
    cover._attr_is_closed = True
    cover._last_run = {"seconds": 15.54, "direction": "open", "ended_at": "2026-11-18T01:02:03.000+00:00"}
    cover._refresh_travel_attributes()
    await platform.async_add_entities([cover])
    await hass.async_block_till_done()
    entity_id = cover.entity_id
    await platform.async_remove_entity(entity_id)

    restarted = _platform_cover(hass, gw)
    await platform.async_add_entities([restarted])
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert (state.state, state.attributes[ATTR_CURRENT_POSITION]) == ("closed", 0)
    assert state.attributes["last_run_seconds"] == 15.54
    assert state.attributes["last_run_direction"] == "open"
    # A run measured since the restart is not overwritten by a late restore.
    restarted._last_run = {"seconds": 3.0, "direction": "close", "ended_at": None}
    restarted._restore_last_run({"last_run": {"seconds": 15.54, "direction": "open", "ended_at": None}})
    assert restarted._last_run["seconds"] == 3.0
    await platform.async_remove_entity(entity_id)


@pytest.mark.parametrize(
    ("record", "position", "is_closed", "state"),
    [
        ({"position": 40, "is_closed": False}, 40, False, "open"),
        ({"position": 0, "is_closed": True}, 0, True, "closed"),
        ({"is_closed": False}, None, False, "open"),  # open at an unknown height (review of #795)
        ({"is_closed": True}, 0, True, "closed"),  # "closed" is a height
        ({"position": "n/a", "is_closed": False}, None, False, "open"),
        ({}, None, None, None),
        (None, None, None, None),
    ],
)
async def test_unavailable_last_state_restores_from_the_entity_record(
    hass, gateway, record, position, is_closed, state
) -> None:
    cover = _make_cover(hass, gateway)
    cover.async_get_last_state = AsyncMock(return_value=State("cover.shutter_05", "unavailable", {}))
    cover.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData(record) if record is not None else None
    )
    await cover.async_added_to_hass()
    assert cover.current_cover_position == position
    assert cover.is_closed is is_closed
    assert cover.state == state


async def test_entity_record_never_overrides_a_state_that_has_its_attributes(hass, gateway) -> None:
    """The record only stands in for an `unavailable` state; what Home Assistant saved wins otherwise."""
    cover = _make_cover(hass, gateway)
    cover.async_get_last_state = AsyncMock(return_value=State("cover.x", "open", {ATTR_CURRENT_POSITION: 30}))
    cover.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData({"position": 80, "is_closed": False}))
    await cover.async_added_to_hass()
    assert cover.current_cover_position == 30

    unknown = _make_cover(hass, gateway)
    unknown.async_get_last_state = AsyncMock(return_value=State("cover.x", "unknown", {}))
    unknown.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData({"position": 80, "is_closed": False}))
    await unknown.async_added_to_hass()
    assert unknown.current_cover_position is None
    assert unknown.is_closed is None


async def test_simple_cover_keeps_its_closed_state_across_an_unavailable_save(hass, gateway) -> None:
    """An assumed-state cover (travel_time 0) has only open / closed to lose."""
    cover = _make_cover(hass, gateway, travel_time=0)
    assert cover._strategy.assumed_state is True
    cover._attr_is_closed = True
    assert cover.extra_restore_state_data.as_dict() == {"is_closed": True}
    restarted = _make_cover(hass, gateway, travel_time=0)
    restarted.async_get_last_state = AsyncMock(return_value=State("cover.x", "unavailable", {}))
    restarted.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData({"is_closed": True}))
    await restarted.async_added_to_hass()
    assert restarted.is_closed is True
    assert restarted.state == "closed"


async def test_advanced_cover_stores_no_position_and_restores_its_last_run(hass, gateway) -> None:
    """An advanced cover reads its level from the actuator; only the measured run is kept."""
    cover = _make_cover(hass, gateway, advanced=True, model="F401")
    assert cover.extra_restore_state_data is None
    cover._attr_current_cover_position = 75
    cover._last_run = {"seconds": 15.36, "direction": "close", "ended_at": "2026-11-05T12:13:06.444+00:00"}
    assert cover.extra_restore_state_data.as_dict() == {"last_run": cover._last_run}
    restarted = _make_cover(hass, gateway, advanced=True, model="F401")
    restarted.async_get_last_extra_data = AsyncMock(return_value=cover.extra_restore_state_data)
    with patch.object(restarted, "_async_poll_if_needed", AsyncMock()):
        await restarted.async_added_to_hass()
    assert restarted.current_cover_position is None
    assert restarted.extra_state_attributes["last_run_seconds"] == 15.36
    assert restarted.extra_state_attributes["last_run_direction"] == "close"


async def test_closed_by_actuator_before_restore(hass, gateway, clock, fake_time):
    cover = _make_cover(hass, gateway)
    # A live run happens while startup is still fetching the previous state (the close and stop
    # frames of the tests above).
    cover.handle_event(OWNEvent.parse("*2*2*05##"))
    clock.now += 20
    cover.handle_event(OWNEvent.parse("*2*0*05##"))
    assert cover.current_cover_position == 0 and cover.is_closed
    cover.async_get_last_state = AsyncMock(
        return_value=State("cover.shutter_05", "unavailable", {})
    )
    cover.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData({"position": 40, "is_closed": False})
    )
    await cover.async_added_to_hass()
    assert (cover.current_cover_position, cover.is_closed) == (0, True)


async def test_unavailable_reload_preserves_recent_relay_hold(hass, gateway, clock, fake_time):
    gateway.emits_cover_pre_move_stop = False
    c = _make_cover(hass, gateway)
    ts = (dt_util.utcnow() - timedelta(seconds=20)).isoformat()
    record = {
        "position": 0,
        "is_closed": True,
        "last_run": {"ended_at": ts, "direction": "close", "seconds": 6},
    }
    c.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData(record))
    await c.async_restore_last_state(State("cover.x", "unavailable", {}))
    assert c.current_cover_position == 0 and c.extra_state_attributes["last_run_ended_at"] == ts
    assert c._stops_before_move(False), (
        "same recent stop would arm relay interlock after a reachable save"
    )


async def test_live_tilt_before_restore_is_not_overwritten_and_empty_last_run_ignored(hass, gateway) -> None:
    """A tilt established before restore survives an unavailable restore record, and empty last_run is ignored."""
    cover = _make_cover(hass, gateway, slat_tilt=True, slat_time=1.5)
    cover._attr_current_cover_tilt_position = 80
    cover.async_get_last_state = AsyncMock(return_value=State("cover.shutter_05", "unavailable", {}))
    cover.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData({"position": 40, "is_closed": False, "tilt": 20, "last_run": {}})
    )
    await cover.async_added_to_hass()
    assert cover.current_cover_tilt_position == 80
    assert not cover._last_run


async def test_unanchored_moving_cover_saved_unavailable_stays_unknown_and_keeps_relay_hold(
    hass, gateway, clock, fake_time
) -> None:
    """A cover closing from unknown (or a closing simple cover) does not export transient is_closed=False as 'open',
    while preserving motion_started_at for M=0 relay-hold protection across a mid-run unavailable reload."""
    gateway.emits_cover_pre_move_stop = False
    closing_from_unknown = _make_cover(hass, gateway)
    closing_from_unknown.handle_event(OWNEvent.parse("*2*2*05##"))
    assert closing_from_unknown.is_closing is True
    assert closing_from_unknown.current_cover_position is None
    saved_data = closing_from_unknown.extra_restore_state_data
    assert saved_data is not None
    record = saved_data.as_dict()
    assert "is_closed" not in record
    assert "position" not in record
    assert isinstance(record.get("motion_started_at"), str)

    restarted = _make_cover(hass, gateway)
    restarted.async_get_last_state = AsyncMock(return_value=State("cover.shutter_05", "unavailable", {}))
    restarted.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData(record))
    await restarted.async_added_to_hass()
    assert restarted.current_cover_position is None
    assert restarted.is_closed is None
    assert restarted.state is None  # unknown, not fabricated "open"
    assert restarted._stops_before_move(False) is True

    # Assumed-state (simple) cover closing when saved unavailable also stays unknown rather than "open".
    simple_closing = _make_cover(hass, gateway, travel_time=0)
    simple_closing.handle_event(OWNEvent.parse("*2*2*05##"))
    assert simple_closing.is_closing is True
    simple_saved = simple_closing.extra_restore_state_data
    assert simple_saved is not None
    assert "is_closed" not in simple_saved.as_dict()

    simple_restarted = _make_cover(hass, gateway, travel_time=0)
    simple_restarted.async_get_last_state = AsyncMock(return_value=State("cover.shutter_05", "unavailable", {}))
    simple_restarted.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData(simple_saved.as_dict()))
    await simple_restarted.async_added_to_hass()
    assert simple_restarted.is_closed is None
    assert simple_restarted.state is None


async def test_partial_run_before_restore_is_not_overwritten_by_unavailable_record(
    hass, gateway, clock, fake_time
) -> None:
    """A partial wall run completed before async_restore_last_state leaves position unknown rather than restoring pre-move height."""
    cover = _make_cover(hass, gateway, slat_tilt=True, slat_time=1.5)
    cover.handle_event(OWNEvent.parse("*2*1*05##"))
    clock.now += 0.8  # shorter than travel_time (6 s): position stays None, slats turn to 53 %
    cover.handle_event(OWNEvent.parse("*2*0*05##"))
    assert cover.current_cover_position is None
    assert cover.current_cover_tilt_position == 53
    assert cover.is_closed is None

    cover.async_get_last_state = AsyncMock(return_value=State("cover.shutter_05", "unavailable", {}))
    cover.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData({"position": 40, "is_closed": False, "tilt": 20})
    )
    await cover.async_added_to_hass()
    assert cover.current_cover_position is None
    assert cover.current_cover_tilt_position == 53
    assert cover.is_closed is None

    # A slat pulse from an unknown angle stopped midway before restore also stays unknown (not overwritten by record tilt).
    pulse_cover = _make_cover(hass, gateway, slat_tilt=True, slat_time=1.5)
    await pulse_cover.async_open_cover_tilt()
    _, pulse_written = gateway.deliveries[-1]
    pulse_written.set_result(clock.now)
    await _yield()
    clock.now += 0.12
    pulse_cover.handle_event(OWNEvent.parse("*2*1*05##"))
    clock.now += 0.4  # < 1.5 s slat_time from unknown: tilt stays None
    pulse_cover.handle_event(OWNEvent.parse("*2*0*05##"))
    assert pulse_cover.current_cover_tilt_position is None
    pulse_cover.async_get_last_state = AsyncMock(return_value=State("cover.shutter_05", "unavailable", {}))
    pulse_cover.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData({"position": 40, "is_closed": False, "tilt": 20})
    )
    await pulse_cover.async_added_to_hass()
    assert pulse_cover.current_cover_position is None
    assert pulse_cover.current_cover_tilt_position is None

    # Also covers the non-position was_closed fallback when a run occurred since start.
    cover2 = _make_cover(hass, gateway)
    cover2.handle_event(OWNEvent.parse("*2*1*05##"))
    clock.now += 1.0
    cover2.handle_event(OWNEvent.parse("*2*0*05##"))
    cover2.async_get_last_state = AsyncMock(return_value=State("cover.shutter_05", "unavailable", {}))
    cover2.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData({"is_closed": True}))
    await cover2.async_added_to_hass()
    assert cover2.current_cover_position is None
    assert cover2.is_closed is None


async def test_ran_since_start_ignores_idle_stop_poll_and_tracks_write_settled_stops(
    hass, gateway, clock, fake_time
) -> None:
    """Static idle *2*0*WHERE## polls do not block restore, while write-settled stops without bus echoes do."""
    # Idle *2*0*05## poll reply before restore sets _last_stop_at without any real motion.
    idle_cover = _make_cover(hass, gateway, slat_tilt=True, slat_time=1.5)
    idle_cover.handle_event(OWNEvent.parse("*2*0*05##"))
    assert idle_cover._last_stop_at is not None
    assert idle_cover._ran_since_start is False
    idle_cover.async_get_last_state = AsyncMock(return_value=State("cover.shutter_05", "unavailable", {}))
    idle_cover.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData({"position": 40, "is_closed": False, "tilt": 20})
    )
    await idle_cover.async_added_to_hass()
    assert idle_cover.current_cover_position == 40
    assert idle_cover.is_closed is False
    assert idle_cover.current_cover_tilt_position == 20

    # Write-settled partial curtain stop (no bus *2*0*05## echo) still blocks stale restore.
    curtain = _make_cover(hass, gateway)
    curtain.handle_event(OWNEvent.parse("*2*1*05##"))
    clock.now += 1.0
    await curtain.async_stop_cover()
    _, stop_written = gateway.deliveries[-1]
    stop_written.set_result(clock.now)
    await _yield()
    assert curtain._last_stop_at is None
    assert curtain._ran_since_start is True
    curtain.async_get_last_state = AsyncMock(return_value=State("cover.shutter_05", "unavailable", {}))
    curtain.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData({"position": 40, "is_closed": False})
    )
    await curtain.async_added_to_hass()
    assert curtain.current_cover_position is None
    assert curtain.is_closed is None

    # Write-settled partial slat pulse stop (no bus *2*0*05## echo) blocks stale tilt/position restore.
    pulse_cover = _make_cover(hass, gateway, slat_tilt=True, slat_time=1.5)
    await pulse_cover.async_open_cover_tilt()
    _, pulse_written = gateway.deliveries[-1]
    pulse_written.set_result(clock.now)
    await _yield()
    clock.now += 0.12
    pulse_cover.handle_event(OWNEvent.parse("*2*1*05##"))
    clock.now += 0.4
    await pulse_cover.async_stop_cover()
    _, pulse_stop_written = gateway.deliveries[-1]
    pulse_stop_written.set_result(clock.now)
    await _yield()
    assert pulse_cover._last_stop_at is None
    assert pulse_cover._ran_since_start is True
    pulse_cover.async_get_last_state = AsyncMock(return_value=State("cover.shutter_05", "unavailable", {}))
    pulse_cover.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData({"position": 40, "is_closed": False, "tilt": 20})
    )
    await pulse_cover.async_added_to_hass()
    assert pulse_cover.current_cover_position is None
    assert pulse_cover.current_cover_tilt_position is None


async def test_advanced_cover_restores_custom_native_strategy_state(hass, gateway) -> None:
    """A custom NativePositionStrategy subclass receives its exported state back on an advanced cover."""
    from custom_components.myhome.cover_strategies import NativePositionStrategy

    class StatefulNativeStrategy(NativePositionStrategy):
        name = "stateful_native"

        def __init__(self, marker: int = 0) -> None:
            super().__init__()
            self.marker = marker

        def export_state(self) -> dict[str, int]:
            return {"marker": self.marker}

        def restore_state(self, state: dict[str, int]) -> None:
            self.marker = int(state["marker"])

    cover = _make_cover(hass, gateway, advanced=True, model="F401")
    cover._strategy = StatefulNativeStrategy(marker=7)
    stored = cover.extra_restore_state_data
    assert stored is not None
    assert stored.as_dict() == {"strategy": "stateful_native", "state": {"marker": 7}}

    restarted = _make_cover(hass, gateway, advanced=True, model="F401")
    restarted._strategy = StatefulNativeStrategy(marker=0)
    restarted.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData(stored.as_dict()))
    with patch.object(restarted, "_async_poll_if_needed", AsyncMock()):
        await restarted.async_added_to_hass()
    assert restarted.strategy.marker == 7


async def test_scope_cover_moving_unanchored_member_and_failed_scope_tilt(
    hass, gateway, clock, fake_time
) -> None:
    """A moving unanchored member omits scope is_closed, and a failed scope tilt leaves members un-ran."""
    members = _make_members(hass, gateway)
    area = _make_area(hass, gateway, members)
    members[0].handle_event(OWNEvent.parse("*2*2*11##"))
    assert area.is_closing is True
    assert area._attr_is_closing is False
    assert area.current_cover_position is None
    saved = area.extra_restore_state_data
    assert saved is None or "is_closed" not in saved.as_dict()

    slat_members = [
        _make_cover(hass, gateway, name=f"Blind {where}", device_id=where, where=where, slat_tilt=True, slat_time=1.5)
        for where in ("11", "12")
    ]
    for member in slat_members:
        member._attr_current_cover_tilt_position = 50
    family = CoverFamily()
    area_tilt = MyHOMEScopeCover(
        hass=hass,
        gateway=gateway,
        name="Area 1",
        entity_name=None,
        device_id="1",
        who="2",
        where="1",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="Area",
        travel_time=6,
        slat_tilt=True,
        slat_time=1.5,
        scope=CoverScope.of("1"),
    )
    area_tilt.hass = hass
    area_tilt.entity_id = "cover.area_1"
    area_tilt.async_write_ha_state = MagicMock()
    area_tilt.async_schedule_update_ha_state = MagicMock()
    for cover in (*slat_members, area_tilt):
        family.add(cover)

    await area_tilt.async_open_cover_tilt()
    _, pulse_written = gateway.deliveries[-1]
    pulse_written.cancel()
    # Stopping/taking over before the background task aborts clears _pulse_written on super().
    area_tilt._apply_tilt_stop(clock.now)
    await _yield()
    assert area_tilt._ran_since_start is False
    assert all(member._ran_since_start is False for member in slat_members)


async def test_reachable_restore_respects_ran_since_start_and_moving_anchor_and_dim10_clears_motion_started_at(
    hass, gateway, clock, fake_time
) -> None:
    """Reachable restore does not clobber pre-restore runs or moving anchors; Dim 10 stops clear motion_started_at."""
    # Reachable restore after a partial wall run keeps live post-run position and slat angle.
    cover = _make_cover(hass, gateway, slat_tilt=True, slat_time=1.5)
    cover.handle_event(OWNEvent.parse("*2*1*05##"))
    clock.now += 0.8
    cover.handle_event(OWNEvent.parse("*2*0*05##"))
    assert cover._ran_since_start is True
    assert cover.current_cover_position is None
    assert cover.current_cover_tilt_position == 53
    await cover.async_restore_last_state(
        State("cover.shutter_05", "open", {ATTR_CURRENT_POSITION: 40, ATTR_CURRENT_TILT_POSITION: 20})
    )
    assert cover.current_cover_position is None
    assert cover.current_cover_tilt_position == 53

    # Reachable closed restore after an assumed-state run keeps live open state.
    simple = _make_cover(hass, gateway, travel_time=0)
    simple.handle_event(OWNEvent.parse("*2*1*05##"))
    clock.now += 0.5
    simple.handle_event(OWNEvent.parse("*2*0*05##"))
    assert simple._ran_since_start is True
    assert simple.is_closed is False
    await simple.async_restore_last_state(State("cover.shutter_05", "closed", {}))
    assert simple.is_closed is False

    # Reachable "open" without current_position does not clobber an already anchored moving cover.
    moving = _make_cover(hass, gateway)
    moving._attr_current_cover_position = moving._start_position = 50
    moving.handle_event(OWNEvent.parse("*2*1*05##"))
    assert moving.is_opening is True
    assert moving._start_position == 50
    await moving.async_restore_last_state(State("cover.shutter_05", "open", {}))
    assert moving._start_position == 50
    assert moving.current_cover_position == 50

    # Non-advanced cover stopped by a Dimension 10 frame clears motion_started_at.
    dim10 = _make_cover(hass, gateway, allow_auto_advanced=False)
    dim10.handle_event(OWNEvent.parse("*2*2*05##"))
    assert dim10._motion_started_at is not None
    dim10.handle_event(OWNEvent.parse("*#2*05*10*10*40*001*0##"))
    assert dim10._motion_started_at is None
    assert dim10.extra_state_attributes["motion_started_at"] is None
    dim10_saved = dim10.extra_restore_state_data
    assert dim10_saved is not None
    assert "motion_started_at" not in dim10_saved.as_dict()

    # Stopping an unwritten queued command (_run_started_at is None) does not set _ran_since_start.
    queued = _make_cover(hass, gateway)
    queued._begin_run("open")
    assert queued._run_started_at is None
    queued._freeze_position(clock.now)
    assert queued._ran_since_start is False
