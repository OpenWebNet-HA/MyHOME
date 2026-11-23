"""Thermostats are found at start on a gateway that drops the general request ``*#4*0##``.

A freshly added F461 sent no WHO 4 frame at start (``*#4*0##`` is dropped by its firmware and was
left out with nothing in its place) and had no climate entity two minutes later; twelve seconds
after another client read ``*#4*#0##`` and the zones, it had its central unit and three zones
(2026-11-18). The sweep now asks what such a gateway does answer, and a zone known only from a
plain valve actuator is asked for its temperature before it is created (#785).

Frames of the plant used below (BTicino 3550, zones 2, 3 and 4), read through an F461 2.0.11 and
an F455 1.0.76 on 2026-11-18: the answer to ``*#4*0#0*20##`` and each zone's answer to
``*#4*Z*0##``. The MyHomeServer1 3.87.13 answered the same request with 43 actuators.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from OWNd.message import OWNHeatingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome import climate_discovery
from custom_components.myhome.climate import MyHOMEClimate, async_setup_entry
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.gateway import ALL_ACTUATORS_REQUEST, ALL_ZONE_REQUESTS
from tests.conftest import attach_runtime
from tests.test_f455_zone_polling import MAC, _handler

# The answer to *#4*0#0*20## on the plant: zone 3 has two plain valves, zones 2 and 4 a fan coil.
ACTUATOR_ANSWER = ["*#4*2#1*20*8##", "*#4*3#1*20*0##", "*#4*3#2*20*0##", "*#4*4#2*20*5##", "*#4*4#1*20*0##"]
ZONE_3_TEMPERATURE = "*#4*3*0*0255##"


# ---------------------------------------------------------------- the sweep


@pytest.mark.parametrize(
    ("model", "who4"),
    [
        ("F461", ["*#4*#0##", "*#4*0#0*20##"]),
        ("MyHomeServer1", ["*#4*#0##", "*#4*0#0*20##"]),
        ("F455", ["*#4*0#0*20##"]),  # no central status: it holds an unanswered WHO 4 read for about 9 s
        ("F454", ["*#4*0#0*20##"]),
        ("F450", ["*#4*0#0*20##"]),
        ("F459", ["*#4*0#0*20##"]),
        ("F460", ["*#4*0#0*20##"]),
        ("H4684", []),  # the oracle shows the request dropped
        ("MH200", []),
        ("MH202", []),
        ("MH201", []),  # not in the oracle, no capture
        ("MH200N", ["*#4*0##"]),  # forwards the general request: nothing changes
        ("F453", ["*#4*0##"]),
    ],
)
def test_what_replaces_the_general_request_at_start(hass: HomeAssistant, model: str, who4: list[str]) -> None:
    frames = _handler(hass, model)._discovery_frames()
    assert [frame for frame in frames if frame.startswith("*#4*")] == who4


def test_every_all_actuator_request_is_the_published_form() -> None:
    assert ALL_ACTUATORS_REQUEST == "*#4*0#0*20##"
    assert {model: dims[20] for model, dims in ALL_ZONE_REQUESTS.items() if 20 in dims} == {
        "F455": "*#4*0#0*20##",
        "F461": "*#4*0#0*20##",
        "MYHOMESERVER1": "*#4*0#0*20##",
    }


def _drain(handler: Any) -> list[str]:
    queued = []
    while not handler.send_buffer.empty():
        queued.append(str(handler.send_buffer.get_nowait()["message"]))
    return queued


async def test_the_sweep_serves_the_zones_that_waited_for_it(hass: HomeAssistant, fast_bus_pacing: None) -> None:
    """One actuator burst at start, not two; a routed zone and a thermo switch's actuator still ask."""
    handler = _acknowledged(_handler(hass, "F455"))
    handler.watch_zone_actuators("2")
    handler.watch_zone_actuators("3#4#01")
    handler.watch_zone_actuators("7#1")
    handler.request_zone_dimension("2", 0)
    await hass.async_block_till_done()
    await handler.initial_discovery()
    await hass.async_block_till_done(wait_background_tasks=True)
    queued = _drain(handler)
    assert queued.count("*#4*0#0*20##") == 1
    assert "*#4*2*0##" in queued
    assert "*#4*7#1*20##" in queued
    assert "*#4*2*20##" not in queued


async def test_a_zone_found_from_the_sweep_s_answers_does_not_ask_every_actuator_again(
    hass: HomeAssistant, fast_bus_pacing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live F461: the zones created from the answers repeated the request 18 s after the sweep."""
    handler = _acknowledged(_handler(hass, "F461"))
    await handler.initial_discovery()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler).count("*#4*0#0*20##") == 1

    handler.watch_zone_actuators("2")  # the zone the answers have just created
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == []

    monkeypatch.setattr("custom_components.myhome.gateway.ALL_ACTUATORS_SWEEP_FRESH", 0.0)
    handler.watch_zone_actuators("4")  # later: a zone that spoke by itself
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == ["*#4*0#0*20##"]


async def test_a_gateway_that_refuses_the_zone_form_and_has_no_all_actuator_request_asks_only_actuators(
    hass: HomeAssistant, fast_bus_pacing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule the F455 had before it got the all-actuator request, kept for a model without one."""
    monkeypatch.delitem(ALL_ZONE_REQUESTS, "F455")
    handler = _handler(hass, "F455")
    handler._initial_discovery_done.set()
    handler.watch_zone_actuators("2")
    handler.watch_zone_actuators("2#1")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == ["*#4*2#1*20##"]


# ---------------------------------------------------------------- one zone's temperature read


def _written(loop: asyncio.AbstractEventLoop, *, mark: str | None = None, done: bool = True) -> asyncio.Future[float]:
    written: asyncio.Future[float] = loop.create_future()
    if mark is not None:
        setattr(written, mark, True)
        written.cancel()
    elif done:
        written.set_result(loop.time())
    return written


@pytest.mark.parametrize(
    ("mark", "expected"),
    [(None, True), ("_gateway_nack", False), ("_command_timeout", None), ("_gateway_desync", None)],
)
async def test_read_zone_temperature_tells_an_answer_from_a_refusal_and_from_a_busy_gateway(
    hass: HomeAssistant, fast_bus_pacing: None, mark: str | None, expected: bool | None
) -> None:
    handler = _handler(hass, "MyHomeServer1")
    handler._last_event_frame_at = 1.0  # frames were seen: the read waits for the bus to be quiet
    handler.send_status_request = AsyncMock(return_value=_written(asyncio.get_running_loop(), mark=mark))
    assert await handler.read_zone_temperature("47") is expected
    assert str(handler.send_status_request.await_args.args[0]) == "*#4*47*0##"


async def test_read_zone_temperature_without_delivery_information_is_not_known(hass: HomeAssistant) -> None:
    handler = _handler(hass, "F461")
    handler.send_status_request = AsyncMock(return_value=None)
    assert await handler.read_zone_temperature("3") is None


# ---------------------------------------------------------------- zones known only from their actuators


def _acknowledged(handler: Any) -> Any:
    """As a connected gateway does: every status request written is acknowledged at once."""
    send = handler.send_status_request

    async def _send(message: Any) -> Any:
        written = await send(message)
        if isinstance(written, asyncio.Future) and not written.done():
            written.set_result(0.0)
        return written

    handler.send_status_request = _send
    return handler


async def _plant(
    hass: HomeAssistant, read: Any, *, model: str = "F461", **options: Any
) -> tuple[Any, list[MyHOMEClimate]]:
    """An entry with no climate configured (an F461 unless said): every zone comes from the bus."""
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, options=options, unique_id=MAC)
    entry.add_to_hass(hass)
    handler = _handler(hass, model)
    handler.send = AsyncMock()
    handler.send_status_request = AsyncMock()
    handler.read_zone_temperature = read
    runtime = attach_runtime(hass, entry, MAC, handler)
    runtime.platforms["climate"] = {}
    zones: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, zones.extend)
    return handler, zones


async def _receive(hass: HomeAssistant, handler: Any, frames: list[str]) -> None:
    for raw in frames:
        message = OWNMessage.parse(raw)  # as the event session parses it; a command seen on the bus is not an event
        if isinstance(message, OWNHeatingEvent):
            await handler._event_dispatcher.process_message(message)
    await hass.async_block_till_done()


async def _after_the_sweep(hass: HomeAssistant, handler: Any) -> None:
    handler._initial_discovery_done.set()
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_a_valve_only_zone_is_created_once_its_thermostat_answers(hass: HomeAssistant) -> None:
    asked: list[str] = []
    handler: Any = None

    async def read(where: str) -> bool | None:
        asked.append(where)
        await _receive(hass, handler, [ZONE_3_TEMPERATURE])
        return True

    handler, zones = await _plant(hass, read)
    await _receive(hass, handler, ACTUATOR_ANSWER)
    assert sorted(zone._where for zone in zones) == ["2", "4"]  # the fan coils; zone 3 is kept back (#785)

    await _after_the_sweep(hass, handler)
    assert asked == ["3"]
    assert sorted(zone._where for zone in zones) == ["2", "3", "4"]
    zone3 = next(zone for zone in zones if zone._where == "3")
    assert zone3.current_temperature == 25.5
    assert zone3.extra_state_attributes["actuators"] == {"1": "off", "2": "off"}  # the kept frames


async def test_an_address_with_no_thermostat_stays_as_it_is_and_two_in_a_row_end_the_run(hass: HomeAssistant) -> None:
    """A towel warmer answers nothing: no zone, and after two such addresses the rest is left alone."""
    asked: list[str] = []

    async def read(where: str) -> bool | None:
        asked.append(where)
        return False  # NACK: nothing answers for that zone

    handler, zones = await _plant(hass, read, model="F455")  # an unanswered read holds it for about 9 s
    await _receive(hass, handler, ["*#4*7#1*20*0##", "*#4*8#1*20*1##", "*#4*9#1*20*0##"])  # BUILT: valves of zones with no thermostat
    await _after_the_sweep(hass, handler)
    assert asked == ["7", "8"]
    assert zones == []
    assert climate_discovery.ACTUATOR_ONLY_ZONE_MISSES == 2


async def test_where_an_unanswered_read_is_cheap_every_actuator_only_zone_is_asked(hass: HomeAssistant) -> None:
    """Two towel warmers must not hide a thermostat with plain valves behind them (review of #955, N4)."""
    asked: list[str] = []

    async def read(where: str) -> bool | None:
        asked.append(where)
        if where == "9":
            await _receive(hass, handler, ["*#4*9*0*0255##"])  # built from *#4*3*0*0255##: zone 9's thermostat
            return True
        return False  # NACK after 2.1 s on an F461

    handler, zones = await _plant(hass, read)
    handler._who4_startup_poll_lock = None  # a gateway double without the start-up poll lock
    await _receive(hass, handler, ["*#4*7#1*20*0##", "*#4*8#1*20*1##", "*#4*9#1*20*0##"])  # BUILT: valves of zones with no thermostat
    await _after_the_sweep(hass, handler)
    assert asked == ["7", "8", "9"]
    assert [zone._where for zone in zones] == ["9"]
    assert handler.unanswered_zone_read_is_cheap is True
    assert _handler(hass, "F455").unanswered_zone_read_is_cheap is False


async def test_a_sweep_request_the_gateway_did_not_take_is_not_counted_as_asked(
    hass: HomeAssistant, fast_bus_pacing: None
) -> None:
    """N5: with no acknowledgement of the sweep's request, the waiting zone still asks for its actuators."""
    handler = _handler(hass, "F461")  # nothing acknowledges
    handler.watch_zone_actuators("2")
    await hass.async_block_till_done()
    await handler.initial_discovery()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler).count("*#4*0#0*20##") == 2  # the sweep's, then the zone's own


async def test_an_acknowledged_read_that_brings_no_frame_counts_as_no_answer(hass: HomeAssistant) -> None:
    asked: list[str] = []

    async def read(where: str) -> bool | None:
        asked.append(where)
        return True

    handler, zones = await _plant(hass, read, model="F455")
    await _receive(hass, handler, ["*#4*7#1*20*0##", "*#4*8#1*20*1##", "*#4*9#1*20*0##"])  # BUILT: valves of zones with no thermostat
    await _after_the_sweep(hass, handler)
    assert asked == ["7", "8"]
    assert zones == []


async def test_a_read_lost_in_the_gateway_s_own_sweep_is_repeated_once(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """MyHomeServer1, 2026-11-18: no ACK within 12.5 s during its sweep, 0.14 s once the bus was quiet."""
    asked: list[str] = []
    handler: Any = None

    async def read(where: str) -> bool | None:
        asked.append(where)
        if asked.count(where) == 1:
            return None  # timed out behind the gateway's sweep
        await _receive(hass, handler, [ZONE_3_TEMPERATURE])
        return True

    handler, zones = await _plant(hass, read)
    await _receive(hass, handler, ["*#4*3#1*20*0##"])
    await _after_the_sweep(hass, handler)
    assert asked == ["3", "3"]
    assert [zone._where for zone in zones] == ["3"]


async def test_a_gateway_that_stays_busy_ends_the_run_without_a_verdict(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    asked: list[str] = []

    async def read(where: str) -> bool | None:
        asked.append(where)
        return None

    caplog.set_level("DEBUG", logger="custom_components.myhome")
    handler, zones = await _plant(hass, read)
    await _receive(hass, handler, ["*#4*3#1*20*0##", "*#4*5#1*20*0##"])
    await _after_the_sweep(hass, handler)
    assert asked == ["3", "3"]  # asked twice, then the remaining zones are left to their own reports
    assert zones == []
    assert "could not be read while the gateway was busy" in caplog.text


async def test_a_zone_behind_an_interface_is_not_asked(hass: HomeAssistant) -> None:
    read = AsyncMock(return_value=False)
    handler, zones = await _plant(hass, read)
    await _receive(hass, handler, ["*#4*3#1#4#01*20*0##"])  # BUILT: a valve of zone 3 behind interface 01
    await _after_the_sweep(hass, handler)
    read.assert_not_awaited()
    assert zones == []


async def test_nothing_is_asked_when_every_zone_came_with_its_own_frames(hass: HomeAssistant) -> None:
    read = AsyncMock(return_value=False)
    handler, zones = await _plant(hass, read)
    await _receive(hass, handler, ["*#4*2#1*20*8##", "*#4*4#2*20*5##"])
    await _after_the_sweep(hass, handler)
    read.assert_not_awaited()
    assert sorted(zone._where for zone in zones) == ["2", "4"]
