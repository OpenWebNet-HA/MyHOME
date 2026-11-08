"""#466: an F455 zone that stays silent after the startup sweep asks its own state once.

What stays as it was (#674): F455 zones ask nothing on add, the F455 stays off the zone status poll
(``*#4*Z##``) and the temperature poll, and the startup sweep is unchanged.

What is added, backed by the physical F455 1.0.76 capture
``tests/fixtures/traces/issue_466/F455_direct_requests_2026-11-06.jsonl`` (and its debug log, where the
standalone zone 2 reported nothing on its own): SILENT_ZONE_GRACE seconds after the sweep, a zone
still without a temperature or setpoint of its own asks ``*#4*Z*0##``, then ``*#4*Z*14##``, ``*#4*Z*12##`` (followed by the
zone's mode frame) and ``*#4*Z#1*20##``, each only once the previous read was answered.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from OWNd.message import OWNHeatingEvent
from OWNd.profiles import get_gateway_profile

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.const import CONF_FIRMWARE
from custom_components.myhome.gateway import (
    SILENT_ZONE_GRACE,
    SILENT_ZONE_POLL,
    MyHOMEGatewayHandler,
)

DIRECT = Path(__file__).resolve().parent / "fixtures/traces/issue_466/F455_direct_requests_2026-11-06.jsonl"
MAC = "00:03:50:00:11:06"
SILENT_READS = ["*#4*2*0##", "*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]


def _sent() -> dict[str, dict]:
    records = [json.loads(line) for line in DIRECT.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {r["frame"]: r for r in records if r["kind"] == "sent"}


def _handler(hass, model: str) -> MyHOMEGatewayHandler:
    entry = MagicMock()
    entry.entry_id = f"entry-{model}"
    entry.data = {
        CONF_HOST: "192.0.2.55",
        CONF_PORT: 20000,
        CONF_PASSWORD: "x",
        CONF_NAME: model,
        CONF_FIRMWARE: "1.0.76",
        CONF_MAC: MAC,
    }
    entry.runtime_data = None
    handler = MyHOMEGatewayHandler(hass, entry)
    handler.gateway.profile = get_gateway_profile(model)
    return handler


def _zone(hass, handler, where: str = "2") -> MyHOMEClimate:
    zone = MyHOMEClimate(
        hass=hass,
        name=f"Zone {where}",
        device_id=f"4-{where}",
        who="4",
        where=where,
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=handler,
    )
    zone.hass = hass
    zone.entity_id = f"climate.zone_{where}"
    zone.async_write_ha_state = MagicMock()
    return zone


def _queued(handler) -> list[str]:
    return [str(call.args[0]) for call in handler.send_status_request.await_args_list]


async def _spin() -> None:
    """Let the poll task run up to its next wait (not ``async_block_till_done``: it waits on it)."""
    for _ in range(10):
        await asyncio.sleep(0)


def _ready(handler, side_effect=None) -> None:
    handler._initial_discovery_done.set()
    handler.send_status_request = AsyncMock(side_effect=side_effect)


# ------------------------------------------------------------------ the capture behind it


def test_capture_backs_each_read_of_the_silent_zone_poll() -> None:
    sent = _sent()
    for frame, replies in (
        ("*#4*2*0##", ["*#4*2*0*0239##"]),
        ("*#4*2*14##", ["*#4*2*14*0220*3##"]),
        ("*#4*2*12##", ["*#4*2*12*0220*3##", "*4*0*2##"]),
        ("*#4*2#1*20##", ["*#4*2#1*20*7##"]),
        ("*#4*4*0##", ["*#4*4*0*0255##"]),
        ("*#4*4*14##", ["*#4*4*14*0215*3##"]),
        ("*#4*4*12##", ["*#4*4*12*0225*3##", "*4*0*4##"]),
        ("*#4*4#1*20##", ["*#4*4#1*20*0##"]),
    ):
        assert sent[frame]["ack"] is True and sent[frame]["replies"] == replies, frame
        assert sent[frame]["dt"] < 0.2, frame


def test_capture_backs_staying_off_the_zone_status_poll() -> None:
    sent = _sent()
    for frame in ("*#4*2##", "*#4*4##"):
        assert sent[frame]["replies"] is True and 8.5 < sent[frame]["dt"] < 9.5


# ------------------------------------------------------------------ what stays as it was


def test_f455_keeps_the_issue_674_gates(hass) -> None:
    handler = _handler(hass, "F455")
    assert handler.silent_zone_poll_needed is True
    assert handler.supports_zone_status is False
    assert handler.zone_temperature_poll_needed is False
    assert handler.general_zone_request_refused is False
    assert handler.actuator_request_refused is False
    assert "*#4*0##" in handler._discovery_frames()
    assert SILENT_ZONE_POLL == {"F455"} and SILENT_ZONE_GRACE == 120.0


@pytest.mark.parametrize("model", ["F450", "F454", "F459", "F461", "MH200", "MH200N", "MH202", "MyHomeServer1"])
def test_other_gateways_have_no_silent_zone_poll(hass, model) -> None:
    assert _handler(hass, model).silent_zone_poll_needed is False


async def test_f455_zone_still_asks_nothing_on_add_or_update(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler)
    assert zone._poll_on_add is False
    await zone.async_update()
    assert _queued(handler) == []


# ------------------------------------------------------------------ the silent-zone poll


async def test_silent_zone_asks_temperature_setpoint_mode_and_actuator(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    await _zone(hass, handler)._poll_if_silent(grace=0)
    assert _queued(handler) == SILENT_READS


async def test_a_restored_temperature_still_counts_as_silent(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler)
    zone._attr_native_current_temperature = 23.9
    zone._current_temperature_restored = True
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == SILENT_READS


async def test_a_zone_that_reported_by_itself_is_not_asked(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler)
    zone.handle_event(OWNHeatingEvent("*#4*2*0*0238##"))
    zone.handle_event(OWNHeatingEvent("*#4*2*14*0220*3##"))
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == []


async def test_a_zone_with_a_temperature_but_no_setpoint_is_asked(hass) -> None:
    """The live zone 4 pushed its temperature but no setpoint: it is asked like a silent one."""
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler, "4")
    zone.handle_event(OWNHeatingEvent("*#4*4*0*0255##"))
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*4*0##", "*#4*4*14##", "*#4*4*12##", "*#4*4#1*20##"]


async def test_each_read_waits_for_the_previous_answer(hass) -> None:
    handler = _handler(hass, "F455")
    futures = [hass.loop.create_future() for _ in SILENT_READS]
    _ready(handler, side_effect=futures)
    task = asyncio.ensure_future(_zone(hass, handler)._poll_if_silent(grace=0))
    await _spin()
    assert _queued(handler) == SILENT_READS[:1]
    futures[0].set_result(1.0)  # the F455 answered and ACKed
    await _spin()
    assert _queued(handler) == SILENT_READS[:2]
    for future in futures[1:]:
        future.set_result(1.0)
        await _spin()
    await task
    assert _queued(handler) == SILENT_READS


async def test_a_refused_temperature_read_ends_the_poll(hass) -> None:
    """A zone without a probe: the refused read cancels its future, nothing more is asked."""
    handler = _handler(hass, "F455")
    refused = hass.loop.create_future()
    _ready(handler, side_effect=[refused])
    task = asyncio.ensure_future(_zone(hass, handler, "3")._poll_if_silent(grace=0))
    await _spin()
    refused.cancel()
    await task
    assert _queued(handler) == ["*#4*3*0##"]


async def test_the_replies_in_poll_order_give_mode_and_action(hass) -> None:
    """The capture's replies, in the order the poll asks for them, fill every attribute."""
    zone = _zone(hass, _handler(hass, "F455"))
    for frame in ("*#4*2*0*0239##", "*#4*2*14*0220*3##", "*#4*2*12*0220*3##", "*4*0*2##", "*#4*2#1*20*1##"):
        zone.handle_event(OWNHeatingEvent(frame))
    assert zone.current_temperature == 23.9 and zone.target_temperature == 22.0
    assert zone.hvac_mode == "cool" and zone.hvac_action == "cooling"


async def test_added_f455_zone_schedules_the_silent_poll(hass, monkeypatch) -> None:
    handler = _handler(hass, "F455")
    zone = _zone(hass, handler)
    names: list[str] = []

    def _capture(coro, name=None, **_kwargs):
        names.append(name)
        coro.close()
        return MagicMock()

    monkeypatch.setattr(hass, "async_create_background_task", _capture)
    zone._poll_on_add = False
    handler.watch_zone_actuators = MagicMock(return_value=lambda: None)
    await zone.async_added_to_hass()
    assert names == ["myhome_climate.zone_2_silent_zone_poll"]


async def test_added_mh202_zone_schedules_nothing(hass, monkeypatch) -> None:
    handler = _handler(hass, "MH202")
    zone = _zone(hass, handler, "3")
    names: list[str] = []

    def _capture(coro, name=None, **_kwargs):
        names.append(name)
        coro.close()
        return MagicMock()

    monkeypatch.setattr(hass, "async_create_background_task", _capture)
    zone._poll_on_add = False
    handler.watch_zone_actuators = MagicMock(return_value=lambda: None)
    await zone.async_added_to_hass()
    assert names == []
