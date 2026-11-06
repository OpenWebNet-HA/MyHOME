"""#466: what a heating zone asks an F455, backed by the physical F455 1.0.76 capture of 2026-11-06.

``tests/fixtures/traces/issue_466/F455_direct_requests_2026-11-06.jsonl`` records every status request
sent to the F455 with its ACK, elapsed time and replies. On that gateway:

- ``*#4*0##`` returns no frame and ACKs after 9.1 s, and ``*#4*Z##`` ACKs only after ~9 s (its five
  frames arrive on the event session), one second short of OWNd's 10 s response window;
- ``*#4*Z*0##``, ``*#4*Z*14##`` and ``*#4*Z*12##`` are answered on the command session in ~0.1 s
  (the dimension 12 reply is followed by the zone's mode frame ``*4*0*Z##``);
- ``*#4*Z*20##`` is NACKed in under 20 ms while ``*#4*Z#1*20##`` is answered in ~0.09 s.

So the startup sweep leaves ``*#4*0##`` out, a zone asks its temperature, then its setpoint and
mode once the temperature was answered, and the
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
    for zone, setpoint in (("2", "0220"), ("4", "0225")):
        frame = f"*#4*{zone}*12##"
        assert sent[frame]["replies"] == [f"*#4*{zone}*12*{setpoint}*3##", f"*4*0*{zone}##"]
        assert sent[frame]["dt"] < 0.2


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


def _queued(handler) -> list[str]:
    return [str(call.args[0]) for call in handler.send_status_request.await_args_list]


async def test_f455_zone_asks_temperature_then_setpoint_and_mode(hass) -> None:
    handler = _handler(hass, "F455")
    handler.send_status_request = AsyncMock()
    handler.request_zone_dimension = MagicMock()
    zone = _zone(hass, handler)
    await zone.async_update()
    assert _queued(handler) == ["*#4*2*0##", "*#4*2*14##", "*#4*2*12##"]
    # The actuator is asked again once the mode is known, so hvac_action can be derived.
    handler.request_zone_dimension.assert_called_once_with("2", 20)


async def test_hvac_action_needs_the_mode_before_the_actuator_report(hass) -> None:
    """Why the actuator is re-asked: the same report gives no action before the mode, and one after."""
    from OWNd.message import OWNHeatingEvent

    zone = _zone(hass, _handler(hass, "F455"))
    zone.handle_event(OWNHeatingEvent("*#4*2#1*20*1##"))
    assert zone.hvac_action is None
    zone.handle_event(OWNHeatingEvent("*4*0*2##"))
    zone.handle_event(OWNHeatingEvent("*#4*2#1*20*1##"))
    assert zone.hvac_mode == "cool" and zone.hvac_action == "cooling"


async def test_f455_setpoint_and_mode_wait_for_the_temperature_answer(hass) -> None:
    """With the real queue future: asked after the ACK of the temperature read, not with it."""
    handler = _handler(hass, "F455")
    written = hass.loop.create_future()
    handler.send_status_request = AsyncMock(side_effect=[written, None, None])
    handler.request_zone_dimension = MagicMock()
    zone = _zone(hass, handler)
    await zone.async_update()
    assert _queued(handler) == ["*#4*2*0##"]
    written.set_result(1.0)  # the F455 answered *#4*2*0*0238## and ACKed
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _queued(handler) == ["*#4*2*0##", "*#4*2*14##", "*#4*2*12##"]


async def test_f455_zone_without_a_probe_is_not_asked_further(hass) -> None:
    """A refused temperature read (live zone 3: NACK after ~9 s) stops the setpoint and mode reads."""
    handler = _handler(hass, "F455")
    written = hass.loop.create_future()
    handler.send_status_request = AsyncMock(side_effect=[written])
    handler.request_zone_dimension = MagicMock()
    zone = _zone(hass, handler, "3")
    await zone.async_update()
    written.cancel()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _queued(handler) == ["*#4*3*0##"]
    handler.request_zone_dimension.assert_not_called()


async def test_the_mode_frame_after_the_dimension_12_reply_sets_hvac_mode(hass) -> None:
    from OWNd.message import OWNHeatingEvent

    zone = _zone(hass, _handler(hass, "F455"))
    for frame in ("*#4*2*0*0238##", "*#4*2*14*0220*3##"):
        zone.handle_event(OWNHeatingEvent(frame))
    assert zone.hvac_mode is None and zone.target_temperature == 22.0
    for frame in ("*#4*2*12*0220*3##", "*4*0*2##"):  # the *#4*2*12## reply of the capture
        zone.handle_event(OWNHeatingEvent(frame))
    assert zone.hvac_mode == "cool"


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
