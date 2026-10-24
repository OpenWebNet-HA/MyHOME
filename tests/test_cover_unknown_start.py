"""A timed cover with no restored state starts unknown, not "open 50 %" (F411/2 on a physical F461 2.0.11)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import ATTR_CURRENT_POSITION
from homeassistant.core import State
from homeassistant.exceptions import ServiceValidationError
from OWNd.message import OWNEvent

from custom_components.myhome.cover import MyHOMECover


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
