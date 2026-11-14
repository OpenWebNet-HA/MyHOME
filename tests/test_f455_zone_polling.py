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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import State
from OWNd.message import OWNHeatingEvent
from OWNd.profiles import get_gateway_profile

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.const import CONF_FIRMWARE
from custom_components.myhome.gateway import (
    HUMIDITY_REQUEST_REFUSED,
    SILENT_ZONE_GRACE,
    SILENT_ZONE_POLL,
    ZONE_ACTUATOR_REQUEST_REFUSED,
    MyHOMEGatewayHandler,
)

DIRECT = Path(__file__).resolve().parent / "fixtures/traces/issue_466/F455_direct_requests_2026-11-06.jsonl"
ZONE3 = Path(__file__).resolve().parent / "fixtures/traces/issue_466/F455_direct_requests_zone3_2026-11-06.jsonl"
MAC = "00:03:50:00:11:06"
SILENT_READS = ["*#4*2*0##", "*#4*2*13##", "*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]


def _sent(path: Path = DIRECT) -> dict[str, dict]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
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


def _zone(hass, handler, where: str = "2", interface: str | None = None) -> MyHOMEClimate:
    zone = MyHOMEClimate(
        hass=hass,
        name=f"Zone {where}",
        device_id=f"4-{where}",
        who="4",
        where=where,
        interface=interface,
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


def _drain(handler) -> list[str]:
    """The frames the paced sender put on the send buffer."""
    queued = []
    while not handler.send_buffer.empty():
        queued.append(str(handler.send_buffer.get_nowait()["message"]))
    return queued


def _restored(mode: str = "heat", **attributes) -> State:
    """What Home Assistant saved for zone 2 before the restart."""
    return State("climate.zone_2", mode, {"temperature": 21.0, "current_temperature": 22.1, **attributes})


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


def test_capture_backs_the_cost_of_a_zone_without_a_probe() -> None:
    """Zone 3 has an actuator and no probe: each single-dimension read is refused only after ~9 s."""
    sent = _sent(ZONE3)
    for frame in ("*#4*3*0##", "*#4*3*14##", "*#4*3*12##", "*#4*3*19##"):
        assert sent[frame]["ack"] is False and sent[frame]["replies"] is None, frame
        assert 8.5 < sent[frame]["dt"] < 9.5, frame  # the poll's first read, and its only one
    assert sent["*#4*3#1*20##"]["ack"] is True and sent["*#4*3#1*20##"]["replies"] == ["*#4*3#1*20*0##"]
    assert sent["*#4*3#1*20##"]["dt"] < 0.2
    assert sent["*#4*3*20##"]["ack"] is False and sent["*#4*3*20##"]["dt"] < 0.05
    for frame in ("*#4*2*0##", "*#4*4*0##"):  # the populated zones, same run
        assert sent[frame]["ack"] is True and sent[frame]["dt"] < 0.2, frame


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


def test_capture_backs_the_f455_refusing_the_zone_actuator_and_humidity_reads() -> None:
    """``*#4*Z*20##`` and ``*#4*Z*60##`` are NACKed at once; ``*#4*Z#1*20##`` is answered."""
    sent = _sent()
    for frame in ("*#4*2*20##", "*#4*4*20##", "*#4*0*20##", "*#4*2*60##"):
        assert sent[frame]["ack"] is False and sent[frame]["replies"] is None and sent[frame]["dt"] < 0.05, frame
    for frame in ("*#4*2#1*20##", "*#4*4#1*20##"):
        assert sent[frame]["ack"] is True and sent[frame]["dt"] < 0.2, frame
    assert "F455" in HUMIDITY_REQUEST_REFUSED and ZONE_ACTUATOR_REQUEST_REFUSED == {"F455"}
    assert _sent(ZONE3)["*#4*3*20##"]["ack"] is False and _sent(ZONE3)["*#4*3#1*20##"]["ack"] is True


async def test_f455_asks_its_actuators_not_its_zones_for_dimension_20(hass, fast_bus_pacing) -> None:
    """A climate zone's watch is not sent to the F455; a thermo actuator switch (``Z#N``) still asks."""
    only_zone = _handler(hass, "F455")
    only_zone._initial_discovery_done.set()
    only_zone.watch_zone_actuators("2")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(only_zone) == []

    handler = _handler(hass, "F455")
    handler._initial_discovery_done.set()
    assert handler.zone_actuator_request_refused is True and handler.humidity_request_refused is True
    handler.watch_zone_actuators("2")
    handler.watch_zone_actuators("2#1")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == ["*#4*2#1*20##"]
    assert handler._actuator_zones == {"2": 1, "2#1": 1}  # kept: the model may still be corrected in band


async def test_a_forwarding_gateway_still_asks_its_zones_for_dimension_20(hass, fast_bus_pacing) -> None:
    handler = _handler(hass, "F454")
    handler._initial_discovery_done.set()
    assert handler.zone_actuator_request_refused is False
    handler.watch_zone_actuators("2")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == ["*#4*2*20##"]


def test_the_gateway_that_serves_who_4_decides(hass, monkeypatch) -> None:
    """A primary whose WHO 4 goes through an F455 (delegation, failover) polls; the reverse does not."""
    primary = _handler(hass, "F461")
    other = _handler(hass, "F455")
    monkeypatch.setattr(primary, "_who4_target", lambda: other)
    assert primary.silent_zone_poll_needed is True
    assert primary.zone_actuator_request_refused is True
    monkeypatch.setattr(primary, "_who4_target", lambda: primary)
    monkeypatch.setattr(other, "_who4_target", lambda: primary)
    assert other.silent_zone_poll_needed is False
    assert other.zone_actuator_request_refused is False


def test_a_passive_follower_polls_nothing(hass, monkeypatch) -> None:
    """A standby, or a secondary without WHO 4, has no zones of its own to ask."""
    handler = _handler(hass, "F455")
    monkeypatch.setattr(MyHOMEGatewayHandler, "is_follower", property(lambda self: True))
    monkeypatch.setattr(MyHOMEGatewayHandler, "delegated_whos", property(lambda self: set()))
    assert handler.silent_zone_poll_needed is False
    monkeypatch.setattr(MyHOMEGatewayHandler, "delegated_whos", property(lambda self: {4}))
    assert handler.silent_zone_poll_needed is True


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


async def test_a_zone_that_reported_everything_by_itself_is_not_asked(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler)
    for frame in ("*#4*2*0*0238##", "*#4*2*14*0220*3##", "*4*0*2##", "*#4*2#1*20*7##", "*#4*2*13*00##"):
        zone.handle_event(OWNHeatingEvent(frame))
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == []


async def test_only_what_the_zone_did_not_report_is_asked(hass) -> None:
    """The live zone 4 pushed its temperature but no setpoint: the temperature is not asked again."""
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler, "4")
    zone.handle_event(OWNHeatingEvent("*#4*4*0*0255##"))
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*4*13##", "*#4*4*14##", "*#4*4*12##", "*#4*4#1*20##"]


async def test_a_zone_with_temperature_and_setpoint_still_asks_for_mode_and_actuator(hass) -> None:
    """Dimension 12 brings the zone's season frame (its mode); ``hvac_action`` needs an actuator."""
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler)
    zone.handle_event(OWNHeatingEvent("*#4*2*0*0238##"))
    zone.handle_event(OWNHeatingEvent("*#4*2*14*0220*3##"))
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*2*13##", "*#4*2*12##", "*#4*2#1*20##"]


# ------------------------------------------------------------------ restored state (review of 195fd13, 1)


async def test_a_restored_setpoint_and_mode_do_not_count_as_reported(hass) -> None:
    """After a restart the saved setpoint and mode are shown, but the zone has not said them.

    Zone 2 pushed only its temperature during the grace period: without the flags the poll
    would take the restored setpoint for a report and never ask for the setpoint, mode or actuator.
    """
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler)
    await zone.async_restore_last_state(_restored("heat"))
    assert zone.target_temperature == 21.0 and zone.hvac_mode == "heat"
    assert zone._target_temperature_restored is True and zone._hvac_mode_restored is True
    zone.handle_event(OWNHeatingEvent("*#4*2*0*0238##"))
    assert zone._current_temperature_restored is False
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*2*13##", "*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]


async def test_a_restored_zone_that_reported_nothing_is_asked_everything(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler)
    await zone.async_restore_last_state(_restored("cool"))
    assert zone.current_temperature == 22.1 and zone._current_temperature_restored is True
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == SILENT_READS


@pytest.mark.parametrize(
    ("frame", "cleared"),
    [
        ("*#4*2*14*0220*3##", "_target_temperature_restored"),  # commanded setpoint
        ("*#4*2*12*0220*3##", "_target_temperature_restored"),  # setpoint in force
        ("*4*110*2##", "_hvac_mode_restored"),  # manual heating
        ("*4*210#0220*2##", "_hvac_mode_restored"),  # manual cooling with its setpoint
        ("*4*303*2##", "_hvac_mode_restored"),  # off
    ],
)
async def test_the_zone_s_own_frames_replace_the_restored_values(hass, frame, cleared) -> None:
    zone = _zone(hass, _handler(hass, "F455"))
    await zone.async_restore_last_state(_restored("heat"))
    zone.handle_event(OWNHeatingEvent(frame))
    assert getattr(zone, cleared) is False
    other = "_hvac_mode_restored" if cleared == "_target_temperature_restored" else "_target_temperature_restored"
    assert getattr(zone, other) is (frame != "*4*210#0220*2##")  # a mode frame with a setpoint gives both


async def test_a_season_frame_gives_a_mode_to_a_zone_restored_off(hass) -> None:
    """The F455 follows dimension 12 with ``*4*0*Z##``; a zone restored ``off`` takes that mode."""
    zone = _zone(hass, _handler(hass, "F455"))
    await zone.async_restore_last_state(_restored("off"))
    zone.handle_event(OWNHeatingEvent("*4*0*2##"))
    assert zone.hvac_mode == "cool" and zone._hvac_mode_restored is False


async def test_the_restored_setpoint_is_shown_until_the_zone_reports(hass) -> None:
    """The flag changes what the poll asks, not what the entity shows (#185 for the temperature)."""
    zone = _zone(hass, _handler(hass, "F455"))
    await zone.async_restore_last_state(_restored("heat"))
    assert zone.target_temperature == 21.0 and zone.current_temperature == 22.1 and zone.hvac_mode == "heat"


# ------------------------------------------------------------------ addresses (review of 195fd13, 3)


async def test_a_hash_zone_of_myhome_yaml_is_asked_by_its_number(hass) -> None:
    """``zone: "#2"`` names zone 2; ``*#4*#2*…##`` would ask through the central unit instead."""
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler, "#2")
    assert zone._central is False
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == SILENT_READS


async def test_behind_an_interface_the_actuator_comes_before_the_routing(hass) -> None:
    """``Z#N#4#II`` is the actuator's address; ``Z#4#II#N`` is not. Only when the profile routes WHO 4."""
    handler = _handler(hass, "F455")
    handler.gateway.profile = SimpleNamespace(model_name="F455", supports_interface_zone_requests=True)
    _ready(handler)
    zone = _zone(hass, handler, "2", interface="1")
    assert zone._interface_poll_refused is False
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*2#4#01*0##", "*#4*2#4#01*13##", "*#4*2#4#01*14##", "*#4*2#4#01*12##", "*#4*2#1#4#01*20##"]


async def test_behind_an_interface_the_default_profile_asks_nothing(hass, monkeypatch) -> None:
    handler = _handler(hass, "F455")
    zone = _zone(hass, handler, "2", interface="01")
    assert zone._interface_poll_refused is True
    names: list[str] = []

    def _capture(coro, name=None, **_kwargs):
        names.append(name)
        coro.close()
        return MagicMock()

    monkeypatch.setattr(hass, "async_create_background_task", _capture)
    handler.watch_zone_actuators = MagicMock(return_value=lambda: None)
    await zone.async_added_to_hass()
    assert names == []


# ------------------------------------------------------------------ when the gate is read (review of 195fd13, 2)


async def test_the_gate_is_read_when_the_poll_runs_not_when_the_zone_is_added(hass) -> None:
    """The in-band identity (WHO 13 / WHO 1013) can correct the model after the entities were added."""
    handler = _handler(hass, "F454")  # the label at setup; the sweep has not run yet
    assert handler.initial_discovery_pending is True
    zone = _zone(hass, handler)
    names: list[str] = []

    def _capture(coro, name=None, **_kwargs):
        names.append(name)
        coro.close()
        return MagicMock()

    hass.async_create_background_task = _capture
    zone._poll_on_add = False  # not the F455's constructor here: the F454 polls on add
    handler.watch_zone_actuators = MagicMock(return_value=lambda: None)
    await zone.async_added_to_hass()
    assert names == ["myhome_climate.zone_2_silent_zone_poll"]  # scheduled while the model may still change

    handler.gateway.profile = get_gateway_profile("F455")  # WHO 1013 says F455
    _ready(handler)
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == SILENT_READS


async def test_a_zone_labelled_f455_but_corrected_in_band_asks_nothing(hass) -> None:
    handler = _handler(hass, "F455")
    zone = _zone(hass, handler)
    handler.gateway.profile = get_gateway_profile("F454")  # corrected before the poll runs
    _ready(handler)
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == []


async def test_a_zone_added_after_the_sweep_on_another_gateway_schedules_nothing(hass, monkeypatch) -> None:
    """Once the sweep is over the model has settled: a non-F455 zone found on the bus schedules no poll."""
    handler = _handler(hass, "F454")
    handler._initial_discovery_done.set()
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
    assert names == []


async def test_a_transport_error_ends_the_poll_and_is_logged(hass, caplog) -> None:
    """Like ``_send_zone_requests`` (#593): the task must not die with an unhandled exception."""
    handler = _handler(hass, "F455")
    _ready(handler, side_effect=OSError("connection lost"))
    await _zone(hass, handler)._poll_if_silent(grace=0)
    assert _queued(handler) == SILENT_READS[:1]
    assert "could not be sent" in caplog.text


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
    """A zone without a probe: the refused read cancels its future, nothing more is asked.

    The F455 refuses ``*#4*3*0##`` only after ~9 s (F455_direct_requests_zone3_2026-11-06.jsonl), so
    this one read is what a probe-less zone costs: one command session for ~9 s, once.
    """
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

    task = MagicMock()

    def _capture(coro, name=None, **_kwargs):
        names.append(name)
        coro.close()
        return task

    monkeypatch.setattr(hass, "async_create_background_task", _capture)
    assert zone._poll_on_add is False  # the F455 constructor: nothing is asked on add (#674)
    handler.watch_zone_actuators = MagicMock(return_value=lambda: None)
    await zone.async_added_to_hass()
    assert names == ["myhome_climate.zone_2_silent_zone_poll"]
    # Removing the entity cancels a poll still waiting out its grace period.
    for remove in list(zone._on_remove or []):
        remove()
    task.cancel.assert_called_once()


async def test_an_mh202_zone_s_poll_asks_nothing(hass) -> None:
    """Scheduled while the sweep is pending (the model may still change), it then asks nothing."""
    handler = _handler(hass, "MH202")
    _ready(handler)
    await _zone(hass, handler, "3")._poll_if_silent(grace=0)
    assert _queued(handler) == []
