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
