"""Discussion #770 (#discussioncomment-19245565): a full run from a known position settles at the end stop.

The actuator's own limit stop can come a little before the stored travel time. On the live
F461 2.0.11 / F411/2 plant (report 20, 2026-10-26, travel calibrated at 5.82 s down /
5.85 s up) a ``close_cover`` from 100 % ended with the actuator's ``*2*0*05##`` 5.76 s after
the motor start and the entity showed ``open 1`` (100 - 5.76 / 5.82 * 100) while the shutter
sat on its lower limit; an ``open_cover`` whose stop came at 5.74 s (report 18) showed
``open 98``. Within END_STOP_TOLERANCE (0.5 s) before the travel time such a stop is now the
end stop: 0 % / 100 % at once, the run measured as it was. Stops HA or a keypad commanded
(an own stop frame, a WHO 2 command translation) and stops further from the end still freeze
the timed estimate; a run from an unknown position keeps settling on its timer.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.message import OWNEvent

from custom_components.myhome.cover import (
    END_SETTLE_MARGIN,
    END_STOP_TOLERANCE,
    CoverFamily,
    CoverScope,
    MyHOMECover,
    MyHOMEScopeCover,
)

# Report 20: cover 05 behind the F461, travel calibrated by the on-bus stopwatch.
TRAVEL_DOWN = 5.82
TRAVEL_UP = 5.85
# The F461 relays the direction status 0.10-0.15 s after the write (report 18 / 20).
DIRECTION_ECHO = 0.12


class Clock:
    def __init__(self):
        self.now = 1000.0


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def fake_time(clock):
    with (
        patch("custom_components.myhome.cover.time") as mock_time,
        patch("custom_components.myhome.cover_scope.time") as mock_scope_time,
    ):
        mock_time.monotonic.side_effect = lambda: clock.now
        mock_scope_time.monotonic.side_effect = lambda: clock.now
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


_REAL_SLEEP = asyncio.sleep


async def _yield(n=4):
    for _ in range(n):
        await _REAL_SLEEP(0)


def _controlled_sleep(release: asyncio.Event):
    async def _sleep(delay, *args, **kwargs):
        if delay > 0:
            await release.wait()
        await _REAL_SLEEP(0)

    return _sleep


def _make_cover(hass, gateway, *, position=None, travel_down=TRAVEL_DOWN, travel_up=TRAVEL_UP, **kwargs):
    """Cover 05 of the F461 plant: F411/2, travel 5.82 s down / 5.85 s up (report 20)."""
    kwargs.setdefault("name", "Shutter 05")
    kwargs.setdefault("entity_name", None)
    kwargs.setdefault("device_id", "05")
    kwargs.setdefault("who", "2")
    kwargs.setdefault("where", "05")
    kwargs.setdefault("interface", None)
    kwargs.setdefault("advanced", False)
    kwargs.setdefault("manufacturer", "BTicino")
    kwargs.setdefault("model", "F411/2")
    kwargs.setdefault("travel_time", int(round(travel_down)))
    cover = MyHOMECover(hass=hass, gateway=gateway, **kwargs)
    cover.hass = hass
    cover.entity_id = f"cover.shutter_{kwargs['where']}"
    cover.async_write_ha_state = MagicMock()
    cover.async_schedule_update_ha_state = MagicMock()
    cover._travel_time_down = travel_down
    cover._travel_time_up = travel_up
    if position is not None:
        cover._attr_current_cover_position = position
        cover._start_position = position
        cover._attr_is_closed = position == 0
    return cover


async def _start_own_run(cover, gateway, clock, direction: str) -> float:
    """open_cover / close_cover from HA with the F461's direction echo; returns the motor start."""
    if direction == "open":
        await cover.async_open_cover()
    else:
        await cover.async_close_cover()
    raw, written = gateway.deliveries[-1]
    assert raw == f"*2*{1 if direction == 'open' else 2}*{cover._where}##"
    assert cover._full_run_to_end is True
    assert cover._stop_task is not None  # the #834 settle timer (travel + 2 s)
    written.set_result(clock.now)
    await _yield()
    clock.now += DIRECTION_ECHO
    cover.handle_event(OWNEvent.parse(raw))
    return clock.now


async def test_close_from_100_with_the_actuator_stop_60_ms_early_is_closed(hass, gateway, clock, fake_time) -> None:
    """Report 20, 19:09:06: close from 100, actuator stop 5.76 s after the start on a 5.82 s travel -> closed 0."""
    cover = _make_cover(hass, gateway, position=100)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            started = await _start_own_run(cover, gateway, clock, "close")
            clock.now = started + 5.76
            cover.handle_event(OWNEvent.parse("*2*0*05##"))  # the F411/2's own limit stop
        finally:
            release.set()
    assert cover.is_closing is False
    assert cover.current_cover_position == 0  # was "open 1" before the tolerance
    assert cover.is_closed is True
    assert cover._stop_task is None  # the settle timer is not needed any more
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(5.76)  # measured as it was
    assert cover.extra_state_attributes["last_run_direction"] == "close"
    assert [raw for raw, _ in gateway.deliveries] == ["*2*2*05##"]  # no stop frame was sent


async def test_open_from_0_with_the_actuator_stop_80_ms_early_is_open_100(hass, gateway, clock, fake_time) -> None:
    """Report 18: open from 0, actuator stop at 5.74 s on a 5.85 s up travel -> open 100 (was 98)."""
    cover = _make_cover(hass, gateway, position=0)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            started = await _start_own_run(cover, gateway, clock, "open")
            clock.now = started + 5.74
            cover.handle_event(OWNEvent.parse("*2*0*05##"))
        finally:
            release.set()
    assert cover.is_opening is False
    assert cover.current_cover_position == 100
    assert cover.is_closed is False
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(5.74)


async def test_a_stop_at_the_travel_time_is_still_the_end_stop(hass, gateway, clock, fake_time) -> None:
    """Report 20, 19:08:55: the open that matched the 5.82 s travel settled at 100 before and still does."""
    cover = _make_cover(hass, gateway, position=0, travel_up=5.82)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            started = await _start_own_run(cover, gateway, clock, "open")
            clock.now = started + 5.82
            cover.handle_event(OWNEvent.parse("*2*0*05##"))
        finally:
            release.set()
    assert cover.current_cover_position == 100
    assert cover.is_opening is False


async def test_a_stop_further_than_the_tolerance_from_the_end_freezes_the_estimate(hass, gateway, clock, fake_time) -> None:
    """A plain stop 0.51 s before the travel time is a mid-run stop: the timed estimate, not the end."""
    cover = _make_cover(hass, gateway, position=100)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            started = await _start_own_run(cover, gateway, clock, "close")
            clock.now = started + TRAVEL_DOWN - END_STOP_TOLERANCE - 0.01
            cover.handle_event(OWNEvent.parse("*2*0*05##"))
        finally:
            release.set()
    assert cover.is_closing is False
    assert cover.current_cover_position == 9  # 100 - 5.31 / 5.82 * 100
    assert cover.is_closed is False


async def test_a_stop_exactly_at_the_tolerance_is_the_end_stop(hass, gateway, clock, fake_time) -> None:
    """The tolerance is inclusive: a stop END_STOP_TOLERANCE before the travel time settles at the end."""
    cover = _make_cover(hass, gateway, position=100)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            started = await _start_own_run(cover, gateway, clock, "close")
            clock.now = started + TRAVEL_DOWN - END_STOP_TOLERANCE
            cover.handle_event(OWNEvent.parse("*2*0*05##"))
        finally:
            release.set()
    assert cover.current_cover_position == 0
    assert cover.is_closed is True


async def test_a_translated_keypad_stop_near_the_end_freezes_the_estimate(hass, gateway, clock, fake_time) -> None:
    """Somebody pressed stop: the F461 relays *2*1000#0*05## before *2*0*05##, and the estimate is kept."""
    cover = _make_cover(hass, gateway, position=100)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            started = await _start_own_run(cover, gateway, clock, "close")
            clock.now = started + 5.60  # 0.22 s before the travel time, within the tolerance
            cover.handle_event(OWNEvent.parse("*2*1000#0*05##"))  # command translation of the stop
            assert cover.is_closing is False
            assert cover.current_cover_position == 4  # 100 - 5.60 / 5.82 * 100
            clock.now += 0.03
            cover.handle_event(OWNEvent.parse("*2*0*05##"))  # the stop status that follows it
        finally:
            release.set()
    assert cover.current_cover_position == 3  # re-anchored on the stop status (5.63 s), not snapped to 0
    assert cover.is_closed is False


async def test_an_ha_stop_near_the_end_freezes_the_estimate(hass, gateway, clock, fake_time) -> None:
    """stop_cover from HA 0.3 s before the end: HA asked for a stop, so the estimate is kept."""
    cover = _make_cover(hass, gateway, position=100)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            started = await _start_own_run(cover, gateway, clock, "close")
            clock.now = started + 5.52
            await cover.async_stop_cover()
            raw, written = gateway.deliveries[-1]
            assert raw == "*2*0*05##"
            written.set_result(clock.now)
            await _yield()
            clock.now += 0.08
            cover.handle_event(OWNEvent.parse("*2*0*05##"))  # the stop echo (F461: 64-120 ms)
        finally:
            release.set()
    assert cover.is_closing is False
    assert 0 < cover.current_cover_position < 100
    assert cover.is_closed is False


async def test_a_plain_stop_during_a_partial_run_near_the_end_freezes_the_estimate(hass, gateway, clock, fake_time) -> None:
    """set_cover_position 5 from 100 is a partial run HA times itself: a plain stop near its end is not an end stop."""
    cover = _make_cover(hass, gateway, position=100)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            await cover.async_set_cover_position(position=5)
            raw, written = gateway.deliveries[-1]
            assert raw == "*2*2*05##"
            assert cover._full_run_to_end is False
            written.set_result(clock.now)
            await _yield()
            clock.now += DIRECTION_ECHO
            cover.handle_event(OWNEvent.parse("*2*2*05##"))
            clock.now += 5.40  # a keypad stop before HA's own stop was due (5.53 s)
            cover.handle_event(OWNEvent.parse("*2*0*05##"))
        finally:
            release.set()
    assert cover.is_closing is False
    assert cover.current_cover_position == 7  # 100 - 5.40 / 5.82 * 100
    assert cover.is_closed is False


async def test_a_wall_switch_close_from_100_settles_at_the_early_actuator_stop(hass, gateway, clock, fake_time) -> None:
    """A keypad close (no HA command) from 100 whose actuator stop comes 60 ms early is closed too."""
    cover = _make_cover(hass, gateway, position=100)
    cover.handle_event(OWNEvent.parse("*2*2*05##"))  # closed from the keypad
    assert cover.is_closing is True
    assert cover._full_run_to_end is True  # the wall settle (#834) is armed
    clock.now += 5.76
    cover.handle_event(OWNEvent.parse("*2*0*05##"))
    assert cover.is_closing is False
    assert cover.current_cover_position == 0
    assert cover.is_closed is True
    assert cover._wall_settle_unsub is None
    assert gateway.deliveries == []


async def test_a_run_from_unknown_keeps_settling_on_the_timer(hass, gateway, clock, fake_time) -> None:
    """From an unknown position the early actuator stop still waits for the timer (#795 behaviour kept)."""
    cover = _make_cover(hass, gateway)
    assert cover.current_cover_position is None
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            started = await _start_own_run(cover, gateway, clock, "close")
            assert cover._end_run_from_unknown is True
            clock.now = started + 5.76
            cover.handle_event(OWNEvent.parse("*2*0*05##"))
            assert cover.is_closing is True  # still settling
            assert cover.current_cover_position is None
            assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(5.76)
            clock.now = started + TRAVEL_DOWN + END_SETTLE_MARGIN + 0.01
        finally:
            release.set()
        await asyncio.wait_for(cover._stop_task, 1.0)
    assert cover.is_closing is False
    assert cover.current_cover_position == 0
    assert cover.is_closed is True


async def test_the_timer_still_settles_a_run_whose_actuator_never_stops(hass, gateway, clock, fake_time) -> None:
    """No stop status at all (M=4, or a gateway that relays none): the #834 timer settles the known run."""
    cover = _make_cover(hass, gateway, position=0)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            started = await _start_own_run(cover, gateway, clock, "open")
            clock.now = started + TRAVEL_UP + END_SETTLE_MARGIN + 0.01
        finally:
            release.set()
        await asyncio.wait_for(cover._stop_task, 1.0)
    assert cover.current_cover_position == 100
    assert cover.is_opening is False
    assert cover._full_run_to_end is False


@pytest.mark.parametrize(
    ("name", "where", "travel", "stop_after"),
    [
        # #770 comment 19245565: F411 at M=0 (60 s) on the 60 s "Essen Fixglas" stopped right at the
        # end and was not reported as closed; the actuator's cutoff a little before the stored time.
        ("Essen Fixglas", "22", 60.0, 59.6),
        ("Stiege oben", "21", 86.0, 85.7),
    ],
)
async def test_discussion_770_f411_cutoff_just_before_the_stored_travel_time_is_closed(
    hass, gateway, clock, fake_time, name, where, travel, stop_after
) -> None:
    cover = _make_cover(
        hass, gateway, position=100, travel_down=travel, travel_up=travel, name=name, device_id=where, where=where
    )
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            started = await _start_own_run(cover, gateway, clock, "close")
            clock.now = started + stop_after
            cover.handle_event(OWNEvent.parse(f"*2*0*{where}##"))
        finally:
            release.set()
    assert cover.is_closed is True
    assert cover.current_cover_position == 0
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(stop_after)


# ------------------------------------------------------------------ members of a scope cover on the main bus


def _make_area(hass, gateway, members):
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


async def test_a_known_scope_member_settles_at_its_early_actuator_stop(hass, gateway, clock, fake_time) -> None:
    """An area close moves a known member; its own limit stop 60 ms early closes it (scope runs too, #770)."""
    known = _make_cover(hass, gateway, position=100, name="Shutter 11", device_id="11", where="11")
    unknown = _make_cover(hass, gateway, name="Shutter 12", device_id="12", where="12")
    area = _make_area(hass, gateway, [known, unknown])
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            await area.async_close_cover()
            raw, written = gateway.deliveries[-1]
            assert raw == "*2*2*1##"
            assert known._full_run_to_end is True and known._end_run_from_unknown is False
            assert unknown._full_run_to_end is True and unknown._end_run_from_unknown is True
            written.set_result(clock.now)
            await _yield()
            clock.now += DIRECTION_ECHO
            for cover in (area, known, unknown):
                cover.handle_event(OWNEvent.parse("*2*2*1##"))
            started = clock.now
            clock.now = started + 5.76
            known.handle_event(OWNEvent.parse("*2*0*11##"))
            unknown.handle_event(OWNEvent.parse("*2*0*12##"))
            assert known.is_closing is False
            assert known.current_cover_position == 0  # the end stop, not "open 1"
            assert unknown.is_closing is True  # from unknown: the timer still settles it
            clock.now = started + TRAVEL_DOWN + END_SETTLE_MARGIN + 0.01
        finally:
            release.set()
        await asyncio.wait_for(unknown._stop_task, 1.0)
    assert unknown.current_cover_position == 0
    assert known.is_closed is True and unknown.is_closed is True
    assert area.current_cover_position == 0
    assert area.is_closed is True
