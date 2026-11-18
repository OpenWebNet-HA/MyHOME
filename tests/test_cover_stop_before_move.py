"""``stop_before_move``: a stop before every timed open / close run.

A BTicino L4671/2 behind an MH201 (fw 3.3.6) only stops when it receives the
opposite direction while it runs, also while it times out its ~60 s run-time
cutoff after a 15 s shutter reached the end stop; a second command then starts
it. Captured on WHERE 41 (``tests/fixtures/traces/mh201_l4671_reversal_2026-11-18``):
``*2*2*41##`` down, ``*2*1*41##`` five seconds later answered by ``*2*0*41##``
(stopped, not reversed), ``*2*1*41##`` again answered by ``*2*1*41##`` (up), and
the actuator's own ``*2*0*41##`` 61 s after that start.

With ``stop_before_move: true`` the cover sends ``*2*0*WHERE##``, waits for its
write plus ``STOP_BEFORE_MOVE_DELAY`` and only then the direction frame. Off by
default; never before a slat pulse, on a native cover, during calibration or on
a scope cover. A stop or run asked for during the pause replaces the pending run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.components.cover import ATTR_POSITION, ATTR_TILT_POSITION
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from OWNd.message import OWNAutomationCommand, OWNEvent, OWNMessage

from custom_components.myhome.const import (
    CONF_PLATFORMS,
    CONF_STOP_BEFORE_MOVE,
    CONF_WHERE,
    DOMAIN,
)
from custom_components.myhome.cover import (
    PLATFORM,
    MyHOMECover,
    MyHOMEScopeCover,
    async_setup_entry,
)
from custom_components.myhome.cover_motion import STOP_BEFORE_MOVE_DELAY
from custom_components.myhome.validate import cover_schema
from scripts.anonymize_plant_fixture import findings
from tests.conftest import attach_runtime, bind_entity

TRACE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "mh201_l4671_reversal_2026-11-18"
    / "myhome_trace_MH201_where41_reversal_2026-11-18.json"
)
TRACE_SHA256 = "8a6dfcaa82e36011a47ab867f1d35c9548ede5a02642e8c1a49de4f163821c98"

STOP = "*2*0*41##"
UP = "*2*1*41##"
DOWN = "*2*2*41##"


@pytest.fixture
def mock_gateway() -> MagicMock:
    gw = MagicMock()
    gw.mac = "00:03:50:00:12:34"
    gw.unique_id = "00:03:50:00:12:34"
    gw.log_id = "[Test Gateway]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


def _cover(hass: HomeAssistant, gateway: MagicMock, **kwargs: Any) -> MyHOMECover:
    params: dict[str, Any] = {
        "hass": hass,
        "name": "Shutter 41",
        "entity_name": None,
        "device_id": "41",
        "who": "2",
        "where": "41",
        "interface": None,
        "advanced": False,
        "manufacturer": "BTicino",
        "model": "L4671/2",
        "gateway": gateway,
        "travel_time": 15,
    }
    params.update(kwargs)
    cover = MyHOMECover(**params)
    bind_entity(hass, cover)
    return cover


def _sent(gateway: MagicMock) -> list[str]:
    return [str(call.args[0]) for call in gateway.send.await_args_list]


class _Clock:
    """Fake monotonic clock; the stop's status arrives during the settle pause, as on the bus."""

    def __init__(self, cover: MyHOMECover, start: float = 1000.0) -> None:
        self.cover = cover
        self.now = start
        self.sleeps: list[float] = []
        self.position_after_stop: list[int | None] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float, *_args: Any) -> None:
        self.sleeps.append(delay)
        if delay == STOP_BEFORE_MOVE_DELAY:
            self.now += 0.055  # rx *2*0*41## 55 ms after the write (frames[5..6])
            self.cover.handle_event(OWNEvent.parse(STOP))
            self.position_after_stop.append(self.cover.current_cover_position)
            assert not (self.cover.is_opening or self.cover.is_closing)
            self.now += delay - 0.055


# --- Fixture: the capture itself -------------------------------------------------


def _load_trace() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(TRACE_PATH.read_text(encoding="utf-8")))


def test_trace_provenance_privacy_and_parser_coverage() -> None:
    """The capture is verbatim, carries no host address and every frame parses in OWNd."""
    raw_text = TRACE_PATH.read_text(encoding="utf-8")
    assert hashlib.sha256(raw_text.encode("utf-8")).hexdigest() == TRACE_SHA256
    assert findings(raw_text, data_file=True) == []

    doc = _load_trace()
    assert doc["synthetic"] is False
    gw = doc["gateway"]
    assert (gw["model"], gw["firmware"], gw["host"], gw["port"]) == ("MH201", "3.3.6", None, None)
    assert gw["identification"]["who1013_model"] == "MH201"
    assert doc["actuator"] == {"model": "L4671/2", "where": "41", "strategy": "timed", "travel_time": 15}

    frames = doc["frames"]
    assert len(frames) == 10
    assert sum(1 for f in frames if f["direction"] == "tx") == 4
    for entry in frames:
        assert entry["who"] == "2" and entry["where"] == "41"
        assert OWNMessage.parse(entry["raw"]) is not None
        if entry["direction"] == "rx":
            assert OWNEvent.parse(entry["raw"]) is not None


def test_trace_actuator_stops_on_the_opposite_direction() -> None:
    """frames[3..9]: the opposite direction stops the run; only a second command reverses it."""
    frames = _load_trace()["frames"][3:]
    assert [(f["direction"], f["raw"]) for f in frames] == [
        ("tx", DOWN),
        ("rx", DOWN),  # moving down
        ("tx", UP),  # up while moving down ...
        ("rx", STOP),  # ... stops it, no reversal
        ("tx", UP),  # up again ...
        ("rx", UP),  # ... moves it up
        ("rx", STOP),  # the actuator's own stop
    ]
    t = [f["timestamp"] for f in frames]
    assert 0.0 < t[1] - t[0] < 0.1 and 0.0 < t[3] - t[2] < 0.1 and 0.0 < t[5] - t[4] < 0.1
    assert 4.9 < t[2] - t[0] < 5.2  # reversed five seconds into the 15 s run
    assert 3.9 < t[4] - t[3] < 4.1
    # Own stop 61 s after the up start on a 15 s shutter: the run-time cutoff,
    # inside which the next opposite command would only stop it again.
    assert 61.0 < t[6] - t[5] < 61.6


# --- Commands --------------------------------------------------------------------


async def test_option_off_sends_only_the_direction_frame(hass: HomeAssistant, mock_gateway: MagicMock) -> None:
    cover = _cover(hass, mock_gateway)
    cover._attr_current_cover_position = 0
    cover._attr_is_closed = True
    with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
        await cover.async_open_cover()
    assert _sent(mock_gateway) == [UP]
    assert all(call.args[0] != STOP_BEFORE_MOVE_DELAY for call in sleep.await_args_list)
    cover._cancel_stop_task()


@pytest.mark.parametrize(
    ("start", "service", "kwargs", "expected"),
    [
        (0, "async_open_cover", {}, UP),
        (100, "async_close_cover", {}, DOWN),
        (100, "async_set_cover_position", {ATTR_POSITION: 0}, DOWN),
        (0, "async_set_cover_position", {ATTR_POSITION: 100}, UP),
        (50, "async_set_cover_position", {ATTR_POSITION: 30}, DOWN),
        (50, "async_set_cover_position", {ATTR_POSITION: 70}, UP),
    ],
)
async def test_option_on_sends_stop_then_direction(
    hass: HomeAssistant, mock_gateway: MagicMock, start: int, service: str, kwargs: dict[str, Any], expected: str
) -> None:
    """Open, close and every set_cover_position run: stop, the settle pause, then the direction."""
    cover = _cover(hass, mock_gateway, stop_before_move=True)
    cover._attr_current_cover_position = start
    cover._start_position = start
    cover._attr_is_closed = start == 0
    order: list[str] = []
    mock_gateway.send.side_effect = lambda cmd: order.append(str(cmd))

    async def fake_sleep(delay: float, *_args: Any) -> None:
        order.append(f"sleep {delay}")

    with patch("asyncio.sleep", side_effect=fake_sleep):
        await getattr(cover, service)(**kwargs)

    assert order[:3] == [STOP, f"sleep {STOP_BEFORE_MOVE_DELAY}", expected]
    assert order.count(STOP) == 1  # an auto-stop task, if any, has not run yet
    assert cover.is_opening is (expected == UP)
    assert cover.is_closing is (expected == DOWN)
    cover._cancel_stop_task()


async def test_partial_run_auto_stop_still_follows(hass: HomeAssistant, mock_gateway: MagicMock) -> None:
    """The timed partial run keeps its own auto-stop after the leading stop."""
    cover = _cover(hass, mock_gateway, stop_before_move=True)
    cover._attr_current_cover_position = 50
    cover._start_position = 50
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await cover.async_set_cover_position(**{ATTR_POSITION: 20})
        assert cover._stop_task is not None
        await cover._stop_task
    assert _sent(mock_gateway) == [STOP, DOWN, STOP]
    assert cover.current_cover_position == 20


async def test_stop_waits_for_its_write_before_the_pause(hass: HomeAssistant, mock_gateway: MagicMock) -> None:
    """With a delivery future the pause counts from the stop's write, not from the enqueue."""
    cover = _cover(hass, mock_gateway, stop_before_move=True)
    cover._attr_current_cover_position = 0
    cover._attr_is_closed = True
    loop = asyncio.get_running_loop()
    futures: list[asyncio.Future[Any]] = []

    async def send(cmd: Any) -> asyncio.Future[Any]:
        fut: asyncio.Future[Any] = loop.create_future()
        futures.append(fut)
        return fut

    mock_gateway.send.side_effect = send
    real_sleep = asyncio.sleep
    task = hass.async_create_task(cover.async_open_cover())
    for _ in range(5):
        await real_sleep(0)
    assert len(futures) == 1  # the stop is queued, its write is still pending
    with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
        futures[0].set_result(loop.time())
        for _ in range(5):
            await real_sleep(0)
            if len(futures) == 2:
                break
        futures[1].set_result(loop.time())
        await task
    sleep.assert_any_await(STOP_BEFORE_MOVE_DELAY)
    assert _sent(mock_gateway) == [STOP, UP]
    cover._cancel_stop_task()


async def test_no_stop_before_a_slat_pulse(hass: HomeAssistant, mock_gateway: MagicMock) -> None:
    """A slat pulse goes out as before: a stop first would eat the short pulse."""
    cover = _cover(hass, mock_gateway, stop_before_move=True, slat_tilt=True, slat_time=2.0)
    cover._attr_current_cover_position = 0
    cover._attr_current_cover_tilt_position = 0
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await cover.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
    assert _sent(mock_gateway)[0] == UP
    # The curtain run of the same blind does stop first.
    mock_gateway.send.reset_mock()
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await cover.async_close_cover()
    assert _sent(mock_gateway)[:2] == [STOP, DOWN]
    cover._cancel_stop_task()


async def test_no_stop_on_a_native_cover(hass: HomeAssistant, mock_gateway: MagicMock) -> None:
    cover = _cover(hass, mock_gateway, stop_before_move=True, advanced=True, model="Shutter")
    assert cover._stops_before_move(False) is False
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await cover.async_open_cover()
    assert _sent(mock_gateway) == [UP]


async def test_no_stop_during_calibration(hass: HomeAssistant, mock_gateway: MagicMock) -> None:
    """Calibration times the actuator's stop status: a stop of ours would end the measured run."""
    cover = _cover(hass, mock_gateway, stop_before_move=True)
    cover._calibrating = True
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await cover._async_move("open")
    assert _sent(mock_gateway) == [UP]
    cover._calibrating = False
    assert cover._stops_before_move(False) is True
    assert cover._stops_before_move(True) is False


async def test_scope_cover_ignores_the_option(hass: HomeAssistant, mock_gateway: MagicMock) -> None:
    scope = MyHOMEScopeCover(
        hass=hass,
        name="Area 4",
        entity_name=None,
        device_id="4",
        who="2",
        where="4",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="Shutter",
        gateway=mock_gateway,
        travel_time=15,
        stop_before_move=True,
        scope=MagicMock(kind="area", interface=None),
    )
    assert scope._stops_before_move(False) is False


def _interrupt_first_pause(cover: MyHOMECover, newer: str, **kwargs: Any) -> Any:
    """A sleep stand-in that runs ``newer`` on the cover during the first settle pause."""
    fired: list[bool] = []

    async def fake_sleep(delay: float, *_args: Any) -> None:
        if delay == STOP_BEFORE_MOVE_DELAY and not fired:
            fired.append(True)
            await getattr(cover, newer)(**kwargs)

    return fake_sleep


@pytest.mark.parametrize(
    ("travel_time", "start", "service", "kwargs"),
    [
        (15, 0, "async_open_cover", {}),  # full run to the end stop
        (15, 100, "async_close_cover", {}),
        (0, 0, "async_open_cover", {}),  # strategy simple: plain direction frame
        (0, 100, "async_close_cover", {}),
        (15, 50, "async_set_cover_position", {ATTR_POSITION: 30}),  # timed partial run
    ],
)
async def test_stop_during_the_pause_drops_the_run(
    hass: HomeAssistant, mock_gateway: MagicMock, travel_time: int, start: int, service: str, kwargs: dict[str, Any]
) -> None:
    """A stop pressed during the pause is the latest intent: the direction frame is not sent."""
    cover = _cover(hass, mock_gateway, stop_before_move=True, travel_time=travel_time)
    cover._attr_current_cover_position = start
    cover._start_position = start
    with patch("asyncio.sleep", side_effect=_interrupt_first_pause(cover, "async_stop_cover")):
        await getattr(cover, service)(**kwargs)
    assert _sent(mock_gateway) == [STOP, STOP]
    assert cover.is_opening is False and cover.is_closing is False
    assert cover._stop_task is None
    assert cover._pending_timed_target is None


async def test_newer_run_during_the_pause_wins(hass: HomeAssistant, mock_gateway: MagicMock) -> None:
    """Open, then close within the pause: only the close's direction goes out."""
    cover = _cover(hass, mock_gateway, stop_before_move=True)
    cover._attr_current_cover_position = 50
    cover._start_position = 50
    with patch("asyncio.sleep", side_effect=_interrupt_first_pause(cover, "async_close_cover")):
        await cover.async_open_cover()
    assert _sent(mock_gateway) == [STOP, STOP, DOWN]
    assert cover.is_closing is True and cover.is_opening is False
    cover._cancel_stop_task()


# --- State across the reversal ---------------------------------------------------


async def test_reversal_mid_run_freezes_then_runs_the_other_way(hass: HomeAssistant, mock_gateway: MagicMock) -> None:
    """Up five seconds into a 15 s close: frozen at the stop, then a full opening run from there."""
    cover = _cover(hass, mock_gateway, stop_before_move=True)
    cover._attr_current_cover_position = 100
    cover._start_position = 100
    cover._attr_is_closed = False
    clock = _Clock(cover)
    with patch("time.monotonic", side_effect=clock.monotonic), patch("asyncio.sleep", side_effect=clock.sleep):
        await cover.async_close_cover()
        assert _sent(mock_gateway) == [STOP, DOWN]
        assert clock.position_after_stop == [100]
        clock.now += 0.063
        cover.handle_event(OWNEvent.parse(DOWN))  # motor start
        assert cover.is_closing is True

        clock.now += 5.0
        mock_gateway.send.reset_mock()
        await cover.async_open_cover()
        assert _sent(mock_gateway) == [STOP, UP]
        # 5 s of a 15 s close from 100 %, frozen at the stop and kept by its status
        frozen = clock.position_after_stop[-1]
        assert frozen is not None and 64 <= frozen <= 70
        assert cover.is_opening is True and cover.is_closing is False
        assert cover._start_position == frozen
        clock.now += 0.067
        cover.handle_event(OWNEvent.parse(UP))
        assert cover.is_opening is True
    cover._cancel_stop_task()


async def test_open_right_after_a_settled_close_starts_from_closed(
    hass: HomeAssistant, mock_gateway: MagicMock
) -> None:
    """HA settled the close on the travel time; the actuator still times out its cutoff.

    The leading stop ends that cutoff; its status leaves the closed cover where it
    is, and the open run starts from 0 %.
    """
    cover = _cover(hass, mock_gateway, stop_before_move=True)
    cover._attr_current_cover_position = 0
    cover._start_position = 0
    cover._attr_is_closed = True
    clock = _Clock(cover)
    with patch("time.monotonic", side_effect=clock.monotonic), patch("asyncio.sleep", side_effect=clock.sleep):
        await cover.async_open_cover()
        assert _sent(mock_gateway) == [STOP, UP]
        assert clock.position_after_stop == [0]
        assert cover._start_position == 0
        clock.now += 0.067
        cover.handle_event(OWNEvent.parse(UP))
        assert cover.is_opening is True
    cover._cancel_stop_task()


# --- Configuration ---------------------------------------------------------------


def test_schema_default_and_accepts_the_option() -> None:
    assert cover_schema({"k": {"where": "41", "name": "K"}})["2-41"][CONF_STOP_BEFORE_MOVE] is False
    validated = cover_schema({"k": {"where": "41", "name": "K", "strategy": "timed", "stop_before_move": True}})
    assert validated["2-41"][CONF_STOP_BEFORE_MOVE] is True
    assert cover_schema({"k": {"where": "41", "name": "K", "slat_tilt": True, "stop_before_move": "yes"}})["2-41"][
        CONF_STOP_BEFORE_MOVE
    ] is True


@pytest.mark.parametrize(
    ("cfg", "message"),
    [
        ({"where": "41", "advanced": True}, "only applies to a timed cover"),
        ({"where": "41", "strategy": "native"}, "only applies to a timed cover"),
        ({"where": "4"}, "point-to-point WHO 2 cover"),
        ({"where": "#3"}, "point-to-point WHO 2 cover"),
        ({"where": "41", "who": "1"}, "point-to-point WHO 2 cover"),
    ],
)
def test_schema_rejects_the_option_where_it_cannot_apply(cfg: dict[str, Any], message: str) -> None:
    with pytest.raises(vol.Invalid, match=message):
        cover_schema({"k": {"name": "K", "stop_before_move": True, **cfg}})


async def test_yaml_option_reaches_the_entity(hass: HomeAssistant, mock_gateway: MagicMock) -> None:
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {
                    PLATFORM: {
                        "41": {CONF_WHERE: "41", CONF_NAME: "Shutter 41", CONF_STOP_BEFORE_MOVE: True},
                        "42": {CONF_WHERE: "42", CONF_NAME: "Shutter 42"},
                    }
                },
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "test_entry"
    added: list[MyHOMECover] = []
    with patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
         patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()):
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)
    by_where = {cover._where: cover for cover in added}
    assert by_where["41"]._stop_before_move is True
    assert by_where["42"]._stop_before_move is False


def test_frames_come_from_ownd_builders() -> None:
    """OWNd first: the frames come from OWNd's builders."""
    assert str(OWNAutomationCommand.stop_shutter("41")) == STOP
    assert str(OWNAutomationCommand.raise_shutter("41")) == UP
    assert str(OWNAutomationCommand.lower_shutter("41")) == DOWN


async def test_option_off_replay_leaves_ha_believing_the_cover_opens(
    hass: HomeAssistant, mock_gateway: MagicMock
) -> None:
    """frames[3..6] without the option: the actuator stopped, the cover reads as opening.

    The ``*2*0*41##`` that answers ``*2*1*41##`` 55 ms later falls in the
    direction frame's echo window, where gateways relay a momentary stop before
    the motor start, so the entity keeps its opening run.
    """
    cover = _cover(hass, mock_gateway)
    cover._attr_current_cover_position = 100
    cover._start_position = 100
    cover._attr_is_closed = False
    now = [1000.0]
    with patch("time.monotonic", side_effect=lambda: now[0]), patch("asyncio.sleep", new_callable=AsyncMock):
        await cover.async_close_cover()
        now[0] += 0.063
        cover.handle_event(OWNEvent.parse(DOWN))
        now[0] += 5.025
        await cover.async_open_cover()
        now[0] += 0.055
        cover.handle_event(OWNEvent.parse(STOP))
        assert _sent(mock_gateway) == [DOWN, UP]
        assert cover.is_opening is True  # while the shutter stands still
    cover._cancel_stop_task()
