"""#466: what a heating zone asks an F455, backed by the physical F455 1.0.76 capture of 2026-11-06.

``tests/fixtures/traces/issue_466/F455_direct_requests_2026-11-06.jsonl`` records every status request
sent to the F455 with its ACK, elapsed time and replies. On that gateway:

- ``*#4*0##`` returns no frame and ACKs after 9.1 s, and ``*#4*Z##`` ACKs only after ~9 s (its five
  frames arrive on the event session), one second short of OWNd's 10 s response window;
- ``*#4*Z*0##`` and ``*#4*Z*14##`` are answered on the command session in ~0.1 s;
- ``*#4*Z*20##`` is NACKed in under 20 ms while ``*#4*Z#1*20##`` is answered in ~0.09 s.

So the startup sweep leaves ``*#4*0##`` out, a zone asks its temperature and setpoint, and the
actuator read goes out in the per-actuator form.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from OWNd.profiles import get_gateway_profile

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.const import CONF_FIRMWARE
from custom_components.myhome.gateway import (
    ACTUATOR_REQUEST_PER_ACTUATOR,
    ZONE_SETPOINT_POLL,
    MyHOMEGatewayHandler,
)

DIRECT = (
    Path(__file__).resolve().parent
    / "fixtures/traces/issue_466/F455_direct_requests_2026-11-06.jsonl"
)
MAC = "00:03:50:00:11:06"


def _sent() -> dict[str, dict]:
    records = [
        json.loads(line) for line in DIRECT.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
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


# ------------------------------------------------------------------ the capture behind each table entry


def test_capture_backs_leaving_out_the_general_and_zone_status_requests() -> None:
    sent = _sent()
    assert sent["*#4*0##"]["replies"] is True and 8.5 < sent["*#4*0##"]["dt"] < 9.5
    for frame in ("*#4*2##", "*#4*4##"):
        assert sent[frame]["replies"] is True and 8.5 < sent[frame]["dt"] < 9.5


def test_capture_backs_the_temperature_and_setpoint_reads() -> None:
    sent = _sent()
    for frame, reply in (
        ("*#4*2*0##", "*#4*2*0*0239##"),
        ("*#4*4*0##", "*#4*4*0*0255##"),
        ("*#4*2*14##", "*#4*2*14*0220*3##"),
        ("*#4*4*14##", "*#4*4*14*0215*3##"),
    ):
        assert (
            sent[frame]["ack"] is True
            and sent[frame]["replies"] == [reply]
            and sent[frame]["dt"] < 0.2
        )


def test_capture_backs_the_per_actuator_form() -> None:
    sent = _sent()
    for zone, reply in (("2", "*#4*2#1*20*7##"), ("4", "*#4*4#1*20*0##")):
        assert sent[f"*#4*{zone}*20##"]["ack"] is False and sent[f"*#4*{zone}*20##"]["dt"] < 0.05
        assert sent[f"*#4*{zone}#1*20##"]["replies"] == [reply]


# ------------------------------------------------------------------ the gateway handler


def test_f455_handler_takes_the_temperature_setpoint_and_per_actuator_polls(hass) -> None:
    handler = _handler(hass, "F455")
    assert handler.supports_zone_status is False
    assert handler.general_zone_request_refused is True
    assert handler.zone_temperature_poll_needed is True
    assert handler.zone_setpoint_poll_needed is True
    assert handler.actuator_request_refused is True
    assert handler.actuator_request_per_actuator is True
    assert "*#4*0##" not in handler._discovery_frames()
    assert ZONE_SETPOINT_POLL == {"F455"} and ACTUATOR_REQUEST_PER_ACTUATOR == {"F455"}


@pytest.mark.parametrize("model", ["F450", "F454", "F459", "H4684", "MH200", "MH201", "MH202"])
def test_other_temperature_poll_gateways_do_not_ask_the_setpoint(hass, model) -> None:
    handler = _handler(hass, model)
    assert handler.zone_temperature_poll_needed is True
    assert handler.zone_setpoint_poll_needed is False


@pytest.mark.parametrize("model", ["F453", "F453AV", "H4684", "MH200", "MH200N", "MH202"])
def test_other_refusing_gateways_still_skip_the_actuator_request(hass, model) -> None:
    assert _handler(hass, model).actuator_request_per_actuator is False


# ------------------------------------------------------------------ what goes on the wire


async def test_f455_zone_asks_temperature_then_setpoint(hass) -> None:
    handler = _handler(hass, "F455")
    handler.send_status_request = AsyncMock()
    zone = _zone(hass, handler)
    await zone.async_update()
    assert [str(call.args[0]) for call in handler.send_status_request.await_args_list] == [
        "*#4*2*0##",
        "*#4*2*14##",
    ]


async def test_mh202_zone_still_asks_only_its_temperature(hass) -> None:
    handler = _handler(hass, "MH202")
    handler.send_status_request = AsyncMock()
    await _zone(hass, handler, "3").async_update()
    assert [str(call.args[0]) for call in handler.send_status_request.await_args_list] == [
        "*#4*3*0##"
    ]


async def test_f455_actuator_watch_sends_the_per_actuator_form(hass, fast_bus_pacing) -> None:
    handler = _handler(hass, "F455")
    handler._initial_discovery_done.set()
    handler.send_paced = AsyncMock()
    handler.watch_zone_actuators("2")
    handler.watch_zone_actuators("4")
    await hass.async_block_till_done(wait_background_tasks=True)
    handler.send_paced.assert_awaited_once_with(["*#4*2#1*20##", "*#4*4#1*20##"])


async def test_mh202_actuator_watch_still_sends_nothing(hass, fast_bus_pacing) -> None:
    handler = _handler(hass, "MH202")
    handler._initial_discovery_done.set()
    handler.send_paced = AsyncMock()
    handler.watch_zone_actuators("2")
    await hass.async_block_till_done(wait_background_tasks=True)
    handler.send_paced.assert_not_awaited()
