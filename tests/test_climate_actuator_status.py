"""The actuators (dimension 20) of a heating zone are asked for on add and after a reconnect.

Without a dimension 20 request a zone's ``hvac_action`` stayed unknown until an actuator
happened to switch (#44); the per-actuator state was not exposed (#25). On a live F461
2.0.11 plant (standalone zone 2, actuator 1 heating valve, actuator 2 fan coil) ``*#4*2*20##``
and the all-zone ``*#4*#0*20##`` are answered with ``*#4*2#1*20*0##`` and
``*#4*2#2*20*5##`` (2.1 s), after which the entity reports ``idle``.

Gateways that drop ``*#4*Z*20##`` (own-firmware-oracle ``thermo-actuator`` rows for
``*#4*1*20##``: F453, F453AV, H4684, MH200, MH200N, MH202 "silent") are never asked.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import State
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNHeatingEvent, OWNMessage
from OWNd.profiles import GenericGatewayProfile, get_gateway_profile

from custom_components.myhome.climate import MyHOMEClimate, _zone_address
from custom_components.myhome.const import (
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_PLATFORMS,
    CONF_WHERE,
    DOMAIN,
)
from custom_components.myhome.discovery import Address
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from tests.conftest import attach_platform, attach_runtime

MAC = "00:03:50:00:00:44"

F461_POLL_REPLY = (
    "*#4*2*0*0239##",
    "*#4*2*12*0235*3##",
    "*4*1*2##",
    "*#4*2*14*0210*3##",
    "*#4*2*13*00##",
)


def _zone(hass, where="2", gateway=None, fan=False, **kwargs):
    if gateway is None:
        gateway = MagicMock()
        gateway.mac = MAC
        gateway.send = AsyncMock()
        gateway.send_status_request = AsyncMock()
        gateway.has_central_unit = False
    zone = MyHOMEClimate(
        hass=hass, name=f"Zone {where}", device_id=f"4-{where}", who="4", where=where,
        heating=True, cooling=True, fan=fan, standalone=True, central=False,
        manufacturer="BTicino", model="Heating Zone", gateway=gateway, **kwargs,
    )
    zone.hass = hass
    zone.entity_id = f"climate.zone_{where}"
    zone.async_write_ha_state = MagicMock()
    return zone


def _feed(zone, *frames):
    for frame in frames:
        zone.handle_event(OWNHeatingEvent(frame))


def _entry(model: str) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = f"entry-{model}"
    entry.data = {
        CONF_HOST: "192.0.2.44", CONF_PORT: 20000, CONF_PASSWORD: "x",
        CONF_NAME: model, CONF_FIRMWARE: "2.0.11", CONF_MAC: MAC,
    }
    entry.runtime_data = None
    return entry


def _handler(hass, model: str) -> MyHOMEGatewayHandler:
    handler = MyHOMEGatewayHandler(hass, _entry(model))
    handler.gateway.profile = get_gateway_profile(model)
    return handler


def _drain(handler: MyHOMEGatewayHandler) -> list[str]:
    queued = []
    while not handler.send_buffer.empty():
        queued.append(str(handler.send_buffer.get_nowait()["message"]))
    return queued


async def _settle(hass) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)


# ------------------------------------------------------------------ entity: hvac_action and actuators

async def test_two_actuators_off_make_the_zone_idle(hass):
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY)
    assert zone.hvac_action is None
    _feed(zone, "*#4*2#1*20*0##", "*#4*2#2*20*5##")
    assert zone.hvac_action == HVACAction.IDLE
    assert zone.extra_state_attributes["actuators"] == {"1": "off", "2": "off"}


async def test_one_open_actuator_of_two_heats(hass):
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY)
    _feed(zone, "*#4*2#2*20*5##", "*#4*2#1*20*1##")
    assert zone.hvac_action == HVACAction.HEATING
    assert zone.extra_state_attributes["actuators"] == {"1": "on", "2": "off"}
    _feed(zone, "*#4*2#1*20*0##")
    assert zone.hvac_action == HVACAction.IDLE


async def test_no_actuator_report_means_no_actuators_attribute(hass):
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY)
    assert "actuators" not in zone.extra_state_attributes


async def test_zone_asks_for_its_actuators_when_added(hass):
    zone = _zone(hass)
    unwatch = MagicMock()
    zone._gateway_handler.watch_zone_actuators = MagicMock(return_value=unwatch)
    zone._gateway_handler.initial_discovery_pending = False
    zone._gateway_handler.sweep_active = False
    zone._gateway_handler.supports_zone_status = True
    await zone.async_added_to_hass()
    zone._gateway_handler.watch_zone_actuators.assert_called_once_with("2")
    zone._call_on_remove_callbacks()  # what removing the entity runs
    unwatch.assert_called_once()


async def test_central_unit_does_not_ask_for_actuators(hass):
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.send_status_request = AsyncMock()
    gateway.watch_zone_actuators = MagicMock()
    central = MyHOMEClimate(
        hass=hass, name="Central", device_id="4-#0", who="4", where="#0",
        heating=True, cooling=True, fan=False, standalone=False, central=True,
        manufacturer="BTicino", model="Central Unit", gateway=gateway,
    )
    central.hass = hass
    central.entity_id = "climate.central"
    central.async_write_ha_state = MagicMock()
    await central.async_added_to_hass()
    gateway.watch_zone_actuators.assert_not_called()


# ------------------------------------------------------------------ gateway: the request per profile

async def test_f461_asks_every_zone_s_actuators_with_one_frame(hass, fast_bus_pacing):
    handler = _handler(hass, "F461")
    handler._initial_discovery_done.set()
    handler.watch_zone_actuators("2")
    handler.watch_zone_actuators("5")
    await _settle(hass)
    assert _drain(handler) == ["*#4*#0*20##"]


async def test_routed_zone_is_asked_on_its_own(hass, fast_bus_pacing):
    handler = _handler(hass, "F461")
    handler._initial_discovery_done.set()
    handler.watch_zone_actuators("2")
    handler.watch_zone_actuators("3#4#01")
    await _settle(hass)
    assert _drain(handler) == ["*#4*#0*20##", "*#4*3#4#01*20##"]


async def test_forwarding_gateway_asks_each_zone(hass, fast_bus_pacing):
    handler = _handler(hass, "F454")
    handler._initial_discovery_done.set()
    handler.watch_zone_actuators("2")
    handler.watch_zone_actuators("1")
    await _settle(hass)
    assert _drain(handler) == ["*#4*1*20##", "*#4*2*20##"]


@pytest.mark.parametrize("model", ["MH202", "MH200", "MH200N", "F453", "F453AV", "H4684"])
async def test_gateway_that_refuses_the_request_is_not_asked(hass, fast_bus_pacing, model):
    handler = _handler(hass, model)
    handler._initial_discovery_done.set()
    handler.watch_zone_actuators("2")
    await _settle(hass)
    assert _drain(handler) == []
    assert handler._actuator_zones == {"2": 1}  # kept: the model may still be corrected in band


@pytest.mark.parametrize(
    ("model", "refused"),
    [("F461", False), ("F454", False), ("MyHomeServer1", False), ("MH202", True), ("F453", True), ("Generic", False)],
)
def test_refusal_table_matches_the_oracle(hass, model, refused):
    assert _handler(hass, model).actuator_request_refused is refused


@pytest.mark.parametrize("flag", [True, False])
def test_profile_flag_wins_over_the_table(hass, flag):
    """An OWNd profile that states the flag is believed, whatever the fallback table says."""
    handler = _handler(hass, "MH202" if flag else "F461")
    handler.gateway.profile = SimpleNamespace(model_name=handler.gateway.profile.model_name, supports_heating_actuator_status=flag)
    assert handler.actuator_request_refused is (not flag)


async def test_actuators_are_asked_after_the_startup_sweep(hass, fast_bus_pacing, monkeypatch):
    # A quiet bus never waits for the cap; it only sizes how long a request waits for the sweep.
    monkeypatch.setattr("custom_components.myhome.gateway.BUS_QUIET_CAP", 5.0)
    handler = _handler(hass, "F461")
    handler.watch_zone_actuators("2")
    await hass.async_block_till_done()
    assert _drain(handler) == []  # waiting for the sweep
    await handler.initial_discovery()
    await _settle(hass)
    queued = _drain(handler)
    assert queued[-1] == "*#4*#0*20##"
    assert queued.count("*#4*#0*20##") == 1


async def test_reconnect_asks_the_actuators_again(hass, fast_bus_pacing):
    handler = _handler(hass, "F454")
    handler._initial_discovery_done.set()
    handler.is_connected = True
    unwatch = handler.watch_zone_actuators("2")
    await _settle(hass)
    assert _drain(handler) == ["*#4*2*20##"]

    handler._on_event_connection_state_change(True)  # already connected: nothing new
    await _settle(hass)
    assert _drain(handler) == []

    handler._on_event_connection_state_change(False)
    handler._on_event_connection_state_change(True)
    await _settle(hass)
    assert _drain(handler) == ["*#4*2*20##"]

    unwatch()
    handler._on_event_connection_state_change(False)
    handler._on_event_connection_state_change(True)
    await _settle(hass)
    assert _drain(handler) == []
    await handler.close_listener()


async def test_two_entities_on_one_zone_unwatch_separately(hass, fast_bus_pacing):
    handler = _handler(hass, "F454")
    handler._initial_discovery_done.set()
    first = handler.watch_zone_actuators("2")
    second = handler.watch_zone_actuators("2")
    await _settle(hass)
    assert _drain(handler) == ["*#4*2*20##"]
    first()
    assert handler._actuator_zones == {"2": 1}
    second()
    assert handler._actuator_zones == {}


async def test_close_listener_drops_waiting_requests(hass, fast_bus_pacing):
    handler = _handler(hass, "F454")
    handler.watch_zone_actuators("2")  # waits for the sweep
    await hass.async_block_till_done()
    assert handler._zone_requests == {("2", 20)}
    await handler.close_listener()
    assert handler._zone_requests == set()
    assert handler._zone_request_task is None


def test_dimension_22_is_never_requested(hass):
    handler = _handler(hass, "F454")
    with pytest.raises(ValueError):
        handler.request_zone_dimension("2", 22)
    assert handler._zone_requests == set()


async def test_a_transport_error_is_logged_and_the_next_request_still_goes_out(hass, fast_bus_pacing, caplog):
    handler = _handler(hass, "F454")
    handler._initial_discovery_done.set()
    handler.send_paced = AsyncMock(side_effect=OSError("socket closed"))
    handler.watch_zone_actuators("2")
    await _settle(hass)
    assert "Zone requests ['*#4*2*20##'] could not be sent" in caplog.text
    assert handler._zone_request_task is not None and handler._zone_request_task.done()
    assert handler._zone_request_task.exception() is None
    handler.send_paced = AsyncMock()
    handler.request_zone_dimension("2", 20)
    await _settle(hass)
    handler.send_paced.assert_awaited_once_with(["*#4*2*20##"])


# ------------------------------------------------------------------ entity: dimension 19 before the fan speed poll (review 5639409551, 1)

def _status_frames(zone) -> list[str]:
    return [str(call.args[0]) for call in zone._gateway_handler.send_status_request.await_args_list]


async def test_fan_coil_revealed_by_dimension_20_asks_its_side_once(hass):
    """``*#4*2#2*20*5##`` reveals a fan coil: one ``*#4*2*19##`` is queued, not a second one."""
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2#1*20*0##")
    zone._gateway_handler.request_zone_dimension.assert_not_called()
    _feed(zone, "*#4*2#2*20*5##", "*#4*2#2*20*5##")
    zone._gateway_handler.request_zone_dimension.assert_called_once_with("2", 19)


async def test_no_fan_speed_poll_in_the_valve_s_season(hass):
    """F461 zone 2 in heating season (heating valve, cooling fan coil): ``*#4*2*11##`` NACKs in 2.1 s."""
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2#1*20*0##", "*#4*2#2*20*5##")
    await zone.async_update()  # dimension 19 still on its way
    assert "*#4*2*11##" not in _status_frames(zone)
    _feed(zone, "*#4*2*19*5*0##")  # cooling: fan coil, heating: valve
    assert zone.extra_state_attributes["fan_coil_cooling"] is True
    assert zone.extra_state_attributes["fan_coil_heating"] is False
    await zone.async_update()
    assert "*#4*2*11##" not in _status_frames(zone)
    _feed(zone, "*4*0*2##")  # cooling season: the fan coil's side
    await zone.async_update()
    assert _status_frames(zone)[-1] == "*#4*2*11##"


async def test_known_sides_without_a_season_still_poll_the_fan_speed(hass):
    """The side is known but the season is not yet: ask, as before."""
    zone = _zone(hass)
    _feed(zone, "*#4*2#2*20*5##", "*#4*2*19*5*0##")
    assert zone._season is None
    await zone.async_update()
    assert _status_frames(zone)[-1] == "*#4*2*11##"


async def test_fan_coil_side_is_asked_again_only_if_the_first_answer_never_came(hass):
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2#2*20*5##", "*#4*2*19*5*0##")
    assert zone._fan_coil_sides_asked_at is None
    _feed(zone, "*#4*2#2*20*5##")  # the side is known: nothing more to ask
    zone._gateway_handler.request_zone_dimension.assert_called_once_with("2", 19)


def _added_at_once(zone) -> None:
    """A zone added once the sweep is over polls right away (``async_update`` on add)."""
    zone._gateway_handler.initial_discovery_pending = False
    zone._gateway_handler.sweep_active = False
    zone._gateway_handler.supports_zone_status = True


async def test_restored_fan_mode_without_the_side_asks_for_it_on_add(hass):
    """Dimension 19 is queued before the poll on add, so the poll does not ask ``*#4*2*11##`` blindly."""
    zone = _zone(hass)
    _added_at_once(zone)
    zone._enable_fan_mode()  # what a restored ``fan_mode`` attribute does
    await zone.async_added_to_hass()
    zone._gateway_handler.request_zone_dimension.assert_called_once_with("2", 19)
    assert _status_frames(zone) == ["*#4*2##"]
    assert "*#4*2*11##" not in _status_frames(zone)


async def test_restoring_the_fan_mode_asks_the_side_before_the_first_poll(hass):
    """``async_restore_last_state`` runs inside ``async_added_to_hass``, right before the poll."""
    zone = _zone(hass)
    _added_at_once(zone)
    zone.async_get_last_state = AsyncMock(return_value=State("climate.zone_2", "heat", {"fan_mode": "low"}))
    await zone.async_added_to_hass()
    assert zone.fan_mode == "low"
    zone._gateway_handler.request_zone_dimension.assert_called_once_with("2", 19)
    assert "*#4*2*11##" not in _status_frames(zone)


async def test_restored_sides_are_not_asked_again(hass):
    zone = _zone(hass)
    _added_at_once(zone)
    zone.async_get_last_state = AsyncMock(
        return_value=State("climate.zone_2", "cool", {"fan_mode": "auto", "fan_coil_cooling": True, "fan_coil_heating": False, "season": "cooling"})
    )
    await zone.async_added_to_hass()
    zone._gateway_handler.request_zone_dimension.assert_not_called()
    assert _status_frames(zone)[-1] == "*#4*2*11##"  # the side is known and it is the fan coil's season


async def test_a_configured_fan_coil_asks_the_side_before_the_first_poll(hass):
    """``fan: true`` in the configuration: the side is asked before ``super().async_added_to_hass()`` polls."""
    zone = _zone(hass, fan=True)
    _added_at_once(zone)
    await zone.async_added_to_hass()
    zone._gateway_handler.request_zone_dimension.assert_called_once_with("2", 19)
    assert "*#4*2*11##" not in _status_frames(zone)


async def test_dimension_19_in_the_fan_coil_s_season_asks_the_fan_speed_once(hass):
    """The entity is not polled after add: the speed must be asked when the side becomes known.

    Dimension 20 value 5 (fan off) carries no speed, so ``fan_mode`` would stay on the
    synthetic ``auto`` until the next poll, which never comes (``should_poll`` is False).
    """
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*4*0*2##", "*#4*2#2*20*5##")  # cooling season, fan coil revealed
    assert zone.fan_mode == "auto"
    zone._gateway_handler.request_zone_dimension.assert_called_once_with("2", 19)
    _feed(zone, "*#4*2*19*5*0##", "*#4*2*19*5*0##")  # cooling: fan coil (the season's side)
    assert zone._gateway_handler.request_zone_dimension.call_args_list[1:] == [call("2", 11)]
    _feed(zone, "*#4*2*11*2##")
    assert zone.fan_mode == "medium"


async def test_dimension_19_in_the_valve_s_season_does_not_ask_the_fan_speed(hass):
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2#2*20*5##", "*#4*2*19*5*0##")  # heating season, cooling fan coil
    assert [c.args for c in zone._gateway_handler.request_zone_dimension.call_args_list] == [("2", 19)]


async def test_dimension_19_revealing_the_fan_coil_itself_asks_the_fan_speed(hass):
    """A zone-level report (``*#4*2*19*0*5##``) is the first sign of a fan coil: side and speed in one go."""
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2*19*0*5##")  # heating season, heating fan coil
    assert zone._fan is True
    assert [c.args for c in zone._gateway_handler.request_zone_dimension.call_args_list] == [("2", 11)]


async def test_a_fan_coil_revealed_after_its_side_asks_the_fan_speed(hass):
    """Dimension 19 before dimension 20: the side is known when the fan coil shows up; ask the speed then."""
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*4*0*2##", "*#4*2*19*5*0##")  # cooling season; the valve side's report
    assert zone._fan is False
    zone._gateway_handler.request_zone_dimension.assert_not_called()
    _feed(zone, "*#4*2#2*20*5##", "*#4*2#2*20*5##")
    assert [c.args for c in zone._gateway_handler.request_zone_dimension.call_args_list] == [("2", 11)]


async def test_a_request_held_by_the_sweep_starts_its_wait_at_the_first_check_after_it(hass, monkeypatch):
    """Nothing checks the entity during the hold: the stale stamp must not count as the wait."""
    monkeypatch.setattr("custom_components.myhome.climate.FAN_COIL_SIDES_WAIT", 0.5)
    zone = _zone(hass)
    zone._gateway_handler.initial_discovery_pending = True
    zone._gateway_handler.sweep_active = False
    _feed(zone, *F461_POLL_REPLY, "*#4*2#2*20*5##")
    zone._fan_coil_sides_asked_at -= 1.0  # a sweep longer than the wait, no poll meanwhile
    zone._gateway_handler.initial_discovery_pending = False
    await zone.async_update()
    assert "*#4*2*11##" not in _status_frames(zone)  # the wait starts at this check
    zone._fan_coil_sides_asked_at -= 1.0
    await zone.async_update()
    assert _status_frames(zone)[-1] == "*#4*2*11##"


async def test_a_long_startup_sweep_does_not_expire_the_fan_coil_side_wait(hass, monkeypatch):
    """The request waits for the startup sweep; the wait for its answer starts once the sweep is over."""
    monkeypatch.setattr("custom_components.myhome.climate.FAN_COIL_SIDES_WAIT", 0.5)
    zone = _zone(hass)
    zone._gateway_handler.initial_discovery_pending = True
    zone._gateway_handler.sweep_active = False
    _feed(zone, *F461_POLL_REPLY, "*#4*2#2*20*5##")
    zone._fan_coil_sides_asked_at -= 1.0  # a sweep longer than the wait
    await zone.async_update()
    assert "*#4*2*11##" not in _status_frames(zone)  # still pending: nothing has left the gateway
    zone._gateway_handler.initial_discovery_pending = False
    await zone.async_update()
    assert "*#4*2*11##" not in _status_frames(zone)  # the wait starts now
    zone._fan_coil_sides_asked_at -= 1.0
    await zone.async_update()
    assert _status_frames(zone)[-1] == "*#4*2*11##"


async def test_a_gateway_without_zone_requests_keeps_polling_the_fan_speed(hass):
    """An older gateway handler (no ``request_zone_dimension``) behaves as before."""
    zone = _zone(hass)
    del zone._gateway_handler.request_zone_dimension
    _feed(zone, *F461_POLL_REPLY, "*#4*2#2*20*5##")
    assert zone._fan_coil_sides_asked_at is None
    await zone.async_update()
    assert _status_frames(zone)[-1] == "*#4*2*11##"


# ------------------------------------------------------------------ entity: dimension 20 and 19 kept apart (review 5639409551, 2)

async def test_zone_level_report_then_actuator_off_does_not_stick_heating(hass):
    """``*#4*2*19*0*1##`` (heating valve open) must not pin ``heating`` once ``*#4*2#1*20*0##`` says off."""
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2*19*0*1##")
    assert zone.hvac_action == HVACAction.HEATING
    assert "actuators" not in zone.extra_state_attributes  # dimension 19 is the zone, not an actuator
    _feed(zone, "*#4*2#1*20*0##")
    assert zone.hvac_action == HVACAction.IDLE
    assert zone.extra_state_attributes["actuators"] == {"1": "off"}


async def test_actuator_on_then_zone_level_all_off_goes_idle(hass):
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2#1*20*1##")
    assert zone.hvac_action == HVACAction.HEATING
    _feed(zone, "*#4*2*19*0*0##")
    assert zone.hvac_action == HVACAction.IDLE
    assert zone.extra_state_attributes["actuators"] == {"1": "off"}
    _feed(zone, "*#4*2*19*0*1##")
    assert zone.hvac_action == HVACAction.HEATING
    assert zone.extra_state_attributes["actuators"] == {"1": "off"}  # which actuator is unknown


async def test_valve_never_appears_among_the_actuators(hass):
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2*19*5*1##", "*#4*2#1*20*1##", "*#4*2#2*20*5##")
    assert zone.extra_state_attributes["actuators"] == {"1": "on", "2": "off"}
    assert zone._zone_active is True


async def test_actuators_attribute_survives_off_and_heat(hass):
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2#1*20*1##", "*#4*2#2*20*5##")
    assert zone.hvac_action == HVACAction.HEATING
    _feed(zone, "*4*303*2##")
    assert zone.hvac_action == HVACAction.OFF
    assert zone.extra_state_attributes["actuators"] == {"1": "off", "2": "off"}
    _feed(zone, "*4*1*2##")
    assert zone.hvac_action == HVACAction.IDLE
    assert zone.extra_state_attributes["actuators"] == {"1": "off", "2": "off"}
    _feed(zone, "*#4*2#1*20*1##")
    assert zone.hvac_action == HVACAction.HEATING


async def test_central_off_keeps_the_actuators_attribute(hass):
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2#1*20*1##", "*#4*2*19*0*1##")
    zone._handle_central_mode_update(HVACMode.OFF)
    assert zone.hvac_action == HVACAction.OFF
    assert zone.extra_state_attributes["actuators"] == {"1": "off"}
    assert zone._zone_active is False


# ------------------------------------------------------------------ gateway: invalid zone addresses (review 5639409551, 3)

@pytest.mark.parametrize("where", ["", "2*22", "2 ", "a", "*#0", "2#1*20", "#", "2##", "##2", "2#", "2#4#", "2#1#2#3", "3#4#001"])
def test_an_invalid_zone_address_is_refused_at_once(hass, where):
    """``2*22`` would make ``*#4*2*22*20##``, a dimension 22 write that reboots the F461."""
    handler = _handler(hass, "F454")
    with pytest.raises(ValueError, match="Invalid zone address"):
        handler.request_zone_dimension(where, 20)
    assert handler._zone_requests == set()
    assert handler._zone_request_task is None


@pytest.mark.parametrize("where", ["2", "#2", "01", "3#4#01", "#3#4#01", "2#1#4#01", "2#1", "0#0"])
def test_a_plain_or_routed_zone_address_is_accepted(hass, where):
    handler = _handler(hass, "F454")
    handler.request_zone_dimension(where, 20)
    assert handler._zone_requests == {(where, 20)}


async def test_a_one_digit_interface_is_asked_with_two_digits(hass, fast_bus_pacing):
    """``3#4#1`` is zone 3 behind interface 01: the frame says ``#4#01``, as OWNd#140's builder requires."""
    handler = _handler(hass, "F454")
    handler._initial_discovery_done.set()
    unwatch = handler.watch_zone_actuators("3#4#1")
    assert handler._actuator_zones == {"3#4#01": 1}
    assert handler._zone_requests == {("3#4#01", 20)}
    await _settle(hass)
    assert _drain(handler) == ["*#4*3#4#01*20##"]
    unwatch()
    assert handler._actuator_zones == {}


def test_a_routed_frame_names_its_interface_with_two_digits(hass):
    """A one-digit interface in a WHO 4 frame and in the configuration both make ``Z#4#0N``."""
    assert _zone_address(OWNHeatingEvent("*#4*3#4#1*14*0215##")) == Address("3", "01")
    assert _zone_address(OWNHeatingEvent("*#4*3#4#01*14*0215##")) == Address("3", "01")
    assert _zone(hass, where="3", interface="1")._full_where == "3#4#01"
    assert _zone(hass, where="3", interface="01")._full_where == "3#4#01"


async def test_one_rejected_zone_does_not_drop_the_batch(hass, fast_bus_pacing, caplog):
    """A zone the frame builder rejects is logged and the other zones are still asked."""
    handler = _handler(hass, "F454")
    handler._initial_discovery_done.set()
    handler.watch_zone_actuators("1")
    handler._zone_requests.add(("2*22", 20))  # what a builder would raise on
    handler.watch_zone_actuators("3")
    await _settle(hass)
    assert _drain(handler) == ["*#4*1*20##", "*#4*3*20##"]
    assert "Zone 2*22: dimension 20 not requested: Invalid zone address: '2*22'" in caplog.text


async def test_a_refusing_gateway_does_not_run_an_empty_sweep(hass, fast_bus_pacing, monkeypatch):
    """Every item skipped: no ``send_paced([])``, so the sweep flags are not toggled for nothing."""
    handler = _handler(hass, "MH202")
    handler._initial_discovery_done.set()
    send_paced = AsyncMock(wraps=handler.send_paced)
    monkeypatch.setattr(handler, "send_paced", send_paced)
    handler.watch_zone_actuators("2")
    await _settle(hass)
    send_paced.assert_not_awaited()
    assert handler._sweep_idle.is_set()
    assert _drain(handler) == []


# ------------------------------------------------------------------ gateway: identity settles after the entities were added (review 5639409551, 4)

async def test_a_gateway_identified_late_as_refusing_is_not_asked(hass, fast_bus_pacing, monkeypatch):
    """Entities are added as ``Generic``; WHO 13 / WHO 1013 then says MH202 before the sweep ends."""
    monkeypatch.setattr("custom_components.myhome.gateway.BUS_QUIET_CAP", 5.0)  # the wait for the sweep must not time out
    handler = _handler(hass, "Generic")
    handler.watch_zone_actuators("2")
    await hass.async_block_till_done()
    handler.gateway.profile = get_gateway_profile("MH202")  # what _apply_model does
    await handler.initial_discovery()
    await _settle(hass)
    assert "*#4*2*20##" not in _drain(handler)
    assert handler._actuator_zones == {"2": 1}

    handler.is_connected = True
    handler._on_event_connection_state_change(False)
    handler._on_event_connection_state_change(True)  # reconnect: still refused
    await _settle(hass)
    assert _drain(handler) == []


async def test_a_gateway_identified_late_as_supported_is_asked(hass, fast_bus_pacing, monkeypatch):
    """Entities are added as a misconfigured MH200N; WHO 13 then corrects it to F461."""
    monkeypatch.setattr("custom_components.myhome.gateway.BUS_QUIET_CAP", 5.0)  # the wait for the sweep must not time out
    handler = _handler(hass, "MH200N")
    handler.watch_zone_actuators("2")
    await hass.async_block_till_done()
    handler.gateway.profile = get_gateway_profile("F461")
    await handler.initial_discovery()
    await _settle(hass)
    assert _drain(handler)[-1] == "*#4*#0*20##"

    handler.is_connected = True
    handler._on_event_connection_state_change(False)
    handler._on_event_connection_state_change(True)
    await _settle(hass)
    assert _drain(handler) == ["*#4*#0*20##"]


# ------------------------------------------------------------------ gateway: profile, topology and probes (review 5639409551, 5)

def test_a_generic_profile_with_a_vendor_prefixed_name_still_finds_its_tables(hass):
    """``get_gateway_profile("BTicino F461")`` is a GenericGatewayProfile named "BTicino F461"."""
    handler = _handler(hass, "BTicino F461")
    assert isinstance(handler.gateway.profile, GenericGatewayProfile)
    assert handler._model_key == "F461"
    assert handler.all_zone_request(20) == "*#4*#0*20##"
    assert handler.actuator_request_refused is False


@pytest.mark.parametrize(
    ("model", "key", "refused"),
    [("Legrand MH200N", "MH200N", True), ("MH200", "MH200", True), ("BTicino F453AV", "F453AV", True), ("Unknown", "UNKNOWN", False)],
)
def test_model_key_reads_the_longest_known_model(hass, model, key, refused):
    handler = _handler(hass, model)
    handler.gateway.profile = GenericGatewayProfile(model_name=model)
    assert handler._model_key == key
    assert handler.actuator_request_refused is refused


async def test_who4_delegated_to_a_secondary_uses_the_secondary_s_tables(hass, fast_bus_pacing):
    """A primary MH202 (refuses) delegating WHO 4 to a secondary F454: the F454 is asked, per zone."""
    primary = _handler(hass, "MH202")
    secondary = _handler(hass, "F454")
    primary._delegated_target = MagicMock(return_value=secondary)
    primary._initial_discovery_done.set()
    primary.watch_zone_actuators("2")
    await _settle(hass)
    assert _drain(primary) == []
    assert _drain(secondary) == ["*#4*2*20##"]


async def test_failover_to_a_standby_that_refuses_sends_nothing(hass, fast_bus_pacing):
    """A primary F461 failed over to a standby MH202: *#4*#0*20## would be dropped by the MH202."""
    primary = _handler(hass, "F461")
    standby = _handler(hass, "MH202")
    primary._failover_target = MagicMock(return_value=standby)
    primary._initial_discovery_done.set()
    primary.watch_zone_actuators("2")
    await _settle(hass)
    assert _drain(primary) == []
    assert _drain(standby) == []


async def test_failover_to_a_forwarding_standby_asks_per_zone(hass, fast_bus_pacing):
    """A primary F461 failed over to a standby F454: the F454 form, not the F461's all-zone one."""
    primary = _handler(hass, "F461")
    standby = _handler(hass, "F454")
    primary._failover_target = MagicMock(return_value=standby)
    primary._initial_discovery_done.set()
    primary.watch_zone_actuators("2")
    primary.watch_zone_actuators("5")
    await _settle(hass)
    assert _drain(primary) == []
    assert _drain(standby) == ["*#4*2*20##", "*#4*5*20##"]


async def test_a_secondary_probe_is_never_asked_for_actuators(hass):
    """WHERE >= 100 is a push-only secondary probe (*#4*105*...## NACKs in 2.1 s on the F461)."""
    probe = _zone(hass, where="105")
    probe._gateway_handler.initial_discovery_pending = False
    probe._gateway_handler.sweep_active = False
    probe._gateway_handler.supports_zone_status = True
    await probe.async_added_to_hass()
    probe._gateway_handler.watch_zone_actuators.assert_not_called()


async def test_myhomeserver1_asks_every_zone_s_actuators_with_one_frame(hass, fast_bus_pacing):
    """MyHomeServer1 3.87.13 answered *#4*#0*20## in 0.39 s with 43 actuator frames (34 zones)."""
    handler = _handler(hass, "MyHomeServer1")
    handler._initial_discovery_done.set()
    handler.watch_zone_actuators("35")
    handler.watch_zone_actuators("36")
    await _settle(hass)
    assert _drain(handler) == ["*#4*#0*20##"]


# ------------------------------------------------------------------ gateway: coalescing after the sweep (review 5639409551, 6)

async def test_zones_discovered_after_the_sweep_share_one_all_zone_request(hass, fast_bus_pacing, monkeypatch):
    """Dynamically discovered zones arrive a few tens of ms apart: one *#4*#0*20##, not one each."""
    monkeypatch.setattr("custom_components.myhome.gateway.ZONE_REQUEST_COALESCE", 0.2)
    handler = _handler(hass, "F461")
    handler._initial_discovery_done.set()
    handler.watch_zone_actuators("2")
    await asyncio.sleep(0.05)
    handler.watch_zone_actuators("5")
    await asyncio.sleep(0.05)
    handler.watch_zone_actuators("7#4#01")
    await _settle(hass)
    assert _drain(handler) == ["*#4*#0*20##", "*#4*7#4#01*20##"]


async def test_the_startup_run_does_not_wait_the_coalesce_window(hass, fast_bus_pacing, monkeypatch):
    """Requests queued before the sweep ended already waited for it: no extra delay."""
    monkeypatch.setattr("custom_components.myhome.gateway.BUS_QUIET_CAP", 5.0)  # the wait for the sweep must not time out
    monkeypatch.setattr("custom_components.myhome.gateway.ZONE_REQUEST_COALESCE", 5.0)
    handler = _handler(hass, "F461")
    handler.watch_zone_actuators("2")
    await hass.async_block_till_done()
    await handler.initial_discovery()
    await _settle(hass)
    assert _drain(handler)[-1] == "*#4*#0*20##"


# ------------------------------------------------------------------ cross-check follow-ups

async def test_an_unanswered_dimension_19_does_not_disable_the_fan_speed_poll_for_good(hass, monkeypatch):
    """The one-shot request may be refused or lost with the session: after the wait, ask as before."""
    monkeypatch.setattr("custom_components.myhome.climate.FAN_COIL_SIDES_WAIT", 0.5)
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2#2*20*5##")
    await zone.async_update()
    assert "*#4*2*11##" not in _status_frames(zone)  # pending
    zone._fan_coil_sides_asked_at -= 1.0  # the wait is over, no answer came
    await zone.async_update()
    assert _status_frames(zone)[-1] == "*#4*2*11##"
    _feed(zone, "*#4*2#2*20*5##")  # the next fan coil report may ask again
    assert zone._gateway_handler.request_zone_dimension.call_count == 2


def test_an_invalid_zone_is_not_watched_either(hass):
    handler = _handler(hass, "F454")
    with pytest.raises(ValueError, match="Invalid zone address"):
        handler.watch_zone_actuators("2*22")
    assert handler._actuator_zones == {}


async def test_zones_arriving_during_a_batch_are_coalesced_into_the_next_one(hass, fast_bus_pacing, monkeypatch):
    """A zone registered while a batch is on the bus, and one 50 ms later, share the next batch."""
    monkeypatch.setattr("custom_components.myhome.gateway.ZONE_REQUEST_COALESCE", 0.2)
    handler = _handler(hass, "F454")
    handler._initial_discovery_done.set()
    sent: list[list[str]] = []

    async def _send(frames):
        sent.append(list(frames))
        if len(sent) == 1:
            handler.watch_zone_actuators("5")
            hass.loop.call_later(0.05, handler.watch_zone_actuators, "7")

    handler.send_paced = _send
    handler.watch_zone_actuators("2")
    await _settle(hass)
    assert sent == [["*#4*2*20##"], ["*#4*5*20##", "*#4*7*20##"]]


# ------------------------------------------------------------------ entity: runtime season transition (review 5642758778)

def _dimension_requests(zone) -> list[tuple]:
    return [c.args for c in zone._gateway_handler.request_zone_dimension.call_args_list]


async def test_season_moving_to_the_fan_coil_s_side_asks_the_fan_speed_once(hass):
    """Booted in heating (valve), the zone switches to cooling (fan coil) without a reload.

    The entity is not polled after add: nothing would ask ``*#4*2*11##`` and ``fan_mode``
    would keep the synthetic ``auto`` until a command or a reconnect.
    """
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2#2*20*5##", "*#4*2*19*5*0##")  # heating season, cooling fan coil
    assert _dimension_requests(zone) == [("2", 19)]
    assert zone.fan_mode == "auto"
    _feed(zone, "*4*0*2##")  # the plant switches to the cooling season
    assert _dimension_requests(zone) == [("2", 19), ("2", 11)]
    _feed(zone, "*4*0*2##", "*#4*2*12*0235*3##", "*4*0*2##")  # the next polls repeat the season
    assert _dimension_requests(zone) == [("2", 19), ("2", 11)]
    _feed(zone, "*#4*2*11*2##")
    assert zone.fan_mode == "medium"
    _feed(zone, "*4*1*2##")  # back to the valve's season: nothing to ask
    assert _dimension_requests(zone) == [("2", 19), ("2", 11)]


async def test_season_moving_to_the_heating_fan_coil_asks_the_fan_speed(hass):
    """The other direction: cooling valve, heating fan coil, booted in cooling."""
    zone = _zone(hass)
    _feed(zone, "*#4*2*0*0239##", "*4*0*2##", "*#4*2*19*0*5##")  # cooling season; dimension 19 reveals a heating fan coil
    assert zone.extra_state_attributes["fan_coil_heating"] is True
    assert zone.extra_state_attributes["fan_coil_cooling"] is False
    assert _dimension_requests(zone) == []
    _feed(zone, "*4*1*2##")
    assert _dimension_requests(zone) == [("2", 11)]
    _feed(zone, "*4*1*2##", "*4*0*2##", "*4*1*2##")  # repeat, the valve's season, the fan coil's again
    assert _dimension_requests(zone) == [("2", 11), ("2", 11)]


async def test_season_change_with_unknown_sides_asks_nothing(hass):
    """Dimension 19 has not answered: the side is not known, the pending request decides later."""
    zone = _zone(hass, fan=True)
    _feed(zone, *F461_POLL_REPLY)
    _feed(zone, "*4*0*2##", "*4*1*2##")
    zone._gateway_handler.request_zone_dimension.assert_not_called()
    assert zone.fan_mode == "auto"


async def test_season_change_without_a_fan_coil_asks_nothing(hass):
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2#1*20*0##", "*#4*2#2*20*0##")  # two valves
    _feed(zone, "*4*0*2##", "*4*1*2##")
    zone._gateway_handler.request_zone_dimension.assert_not_called()
    assert zone.fan_mode is None


async def test_the_first_season_after_add_does_not_ask_twice(hass):
    """Restored sides, no season: the poll on add already asked ``*#4*2*11##``."""
    zone = _zone(hass)
    _added_at_once(zone)
    zone.async_get_last_state = AsyncMock(
        return_value=State("climate.zone_2", "cool", {"fan_mode": "auto", "fan_coil_cooling": True, "fan_coil_heating": False})
    )
    await zone.async_added_to_hass()
    assert _status_frames(zone) == ["*#4*2##", "*#4*2*11##"]
    _feed(zone, "*#4*2*0*0239##", "*4*0*2##")  # the poll's reply names the cooling season
    zone._gateway_handler.request_zone_dimension.assert_not_called()


async def test_a_restored_cooling_season_spelled_conditioning_by_ownd_is_not_a_change(hass):
    """The restored attribute says ``cooling``, OWNd decodes ``*4*0*2##`` as ``conditioning``."""
    zone = _zone(hass)
    _added_at_once(zone)
    zone.async_get_last_state = AsyncMock(
        return_value=State("climate.zone_2", "cool", {"fan_mode": "low", "fan_coil_cooling": True, "fan_coil_heating": False, "season": "cooling"})
    )
    await zone.async_added_to_hass()
    assert _status_frames(zone)[-1] == "*#4*2*11##"
    _feed(zone, "*4*0*2##")
    zone._gateway_handler.request_zone_dimension.assert_not_called()
    _feed(zone, "*4*1*2##", "*4*0*2##")  # a real round trip through the valve's season
    assert _dimension_requests(zone) == [("2", 11)]


async def test_a_season_decoded_as_a_mode_by_an_older_ownd_asks_the_fan_speed_too(hass):
    """OWNd releases without ``MESSAGE_TYPE_SEASON`` deliver ``*4*0*2##`` as a plain mode."""
    from OWNd.message import CLIMATE_MODE_COOL, MESSAGE_TYPE_MODE

    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2#2*20*5##", "*#4*2*19*5*0##")
    assert _dimension_requests(zone) == [("2", 19)]
    legacy = MagicMock()
    legacy.message_type = MESSAGE_TYPE_MODE
    legacy.mode = CLIMATE_MODE_COOL
    legacy.__str__ = MagicMock(return_value="*4*0*2##")
    legacy.human_readable_log = "Zone 2 mode cool"
    zone.handle_event(legacy)
    assert zone._season == "cooling"
    assert _dimension_requests(zone) == [("2", 19), ("2", 11)]
    zone.handle_event(legacy)
    assert _dimension_requests(zone) == [("2", 19), ("2", 11)]


# ------------------------------------------------------------------ live trace replays through MyHOMEClimate (review 5642758778, 2)

TRACES = Path(__file__).resolve().parent / "fixtures" / "traces"
F461_TRACE = TRACES / "f461_live_2026-10-23" / "myhome_trace_F461_live_2026-10-23.json"
F461_RUN6 = "run6_event_session_ha_fixed_integration"
MHS1_TRACE = TRACES / "myhomeserver1_live_2026-10-24" / "myhome_trace_MyHomeServer1_live_2026-10-24.json"
MHS1_RUN2 = "run2_reads_thermo_sound_alarm_lights"


@pytest.fixture(scope="module")
def f461_rows() -> list[dict]:
    """The chronological rows of the 2026-10-23 F461 2.0.11 live capture (#799)."""
    assert F461_TRACE.is_file(), F461_TRACE
    return json.loads(F461_TRACE.read_text(encoding="utf-8"))["rows"]


@pytest.fixture(scope="module")
def mhs1_rows() -> list[dict]:
    """The chronological rows of the 2026-10-24 MyHomeServer1 3.87.13 live capture (#799)."""
    assert MHS1_TRACE.is_file(), MHS1_TRACE
    return json.loads(MHS1_TRACE.read_text(encoding="utf-8"))["rows"]


def _evt(rows: list[dict], run: str) -> list[str]:
    return [r["frame"] for r in rows if r["run"] == run and r["dir"] == "rx" and r["session"] == "evt"]


def _evt_between(rows: list[dict], run: str, frame: str) -> list[str]:
    """Event-session frames from the request ``frame`` of ``run`` until the next request of that run."""
    run_rows = [r for r in rows if r["run"] == run]
    start = next(i for i, r in enumerate(run_rows) if r["dir"] == "tx" and r["frame"] == frame)
    out: list[str] = []
    for r in run_rows[start + 1 :]:
        if r["dir"] == "tx":
            break
        if r["dir"] == "rx" and r["session"] == "evt":
            out.append(r["frame"])
    return out


def _trace_gateway(model: str, mac: str) -> MagicMock:
    gw = MagicMock()
    gw.mac = mac
    gw.unique_id = mac
    gw.bus_group = mac
    gw.primary_gateway_mac = None
    gw.delegated_whos = set()
    gw.delegated_away_whos = set()
    gw.log_id = f"[{model} live]"
    gw.model = model
    gw.has_central_unit = False
    gw.supports_zone_status = True
    gw.initial_discovery_pending = False
    gw.sweep_active = False
    gw.is_connected = True
    gw.profile = get_gateway_profile(model)
    gw._gateway = SimpleNamespace(profile=gw.profile)
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    gw.watch_zone_actuators = MagicMock(return_value=MagicMock())
    gw.request_zone_dimension = MagicMock()
    return gw


async def _trace_plant(hass, gw: MagicMock, zones: list[str]) -> tuple[dict[str, MyHOMEClimate], list]:
    """The climate platform of ``gw`` with ``zones`` configured, bus discovery on; the added entities too."""
    from custom_components.myhome.climate import async_setup_entry as climate_setup

    platforms_cfg = {
        "climate": {
            f"climate_zone_{z}": {
                CONF_WHERE: z, "who": "4", "zone": z, "name": f"Climate Zone {z}", "heat": True, "cool": True,
                "standalone": True, "central": False, "manufacturer": "BTicino", "model": gw.model,
            }
            for z in zones
        },
    }
    hass.data = {DOMAIN: {gw.mac: {CONF_ENTITY: gw, CONF_PLATFORMS: platforms_cfg}}}
    entry = MagicMock()
    entry.data = {"mac": gw.mac}
    entry.entry_id = f"trace_{gw.model}"
    entry.options = {}
    attach_runtime(hass, entry, gw.mac, gw)
    added: list = []
    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.discovery.er.async_get", return_value=MagicMock()),
    ):
        await climate_setup(hass, entry, added.extend)
    entities = {}
    for ent in added:
        ent.hass = hass
        ent.entity_id = f"climate.{ent._device_id}"
        ent.async_write_ha_state = MagicMock()
        ent.async_schedule_update_ha_state = MagicMock()
        attach_platform(ent, entry)
        await ent.async_added_to_hass()
        entities[ent._where] = ent
    assert sorted(entities) == sorted(zones)
    return entities, added


def _replay(hass, mac: str, frames: list[str]) -> list[OWNMessage]:
    """Deliver the frames the way the gateway handler does (``myhome_message_<mac>``)."""
    delivered = []
    for raw in frames:
        msg = OWNMessage.parse(raw)
        assert msg is not None, raw
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        delivered.append(msg)
    return delivered


async def test_replay_f461_run6_through_the_climate_entity(hass, f461_rows):
    """1770 s of event session while the fixed integration ran: idle after each poll's actuator pair.

    Every ``*#4*2##`` reply (dimensions 0, 12, season, 14, 13) is followed about 2.2 s later
    by ``*#4*2#1*20*0##`` + ``*#4*2#2*20*5##``, the answer to the integration's
    ``*#4*#0*20##``. Before the first pair ``hvac_action`` is unknown; from it on the zone is
    ``idle`` with ``actuators`` ``{"1": "off", "2": "off"}``, through six more polls.
    """
    gw = _trace_gateway("F461", "00:03:50:00:10:23")
    entities, added = await _trace_plant(hass, gw, ["2"])
    zone2 = entities["2"]
    assert zone2.hvac_action is None and "actuators" not in zone2.extra_state_attributes
    gw.watch_zone_actuators.assert_called_once_with("2")
    frames = _evt(f461_rows, F461_RUN6)
    assert len(frames) == 171
    seen: list[tuple[str, HVACAction | None, dict | None]] = []
    for raw in frames:
        _replay(hass, gw.mac, [raw])
        if raw.startswith(("*#4*2*", "*#4*2#", "*4*1*2##", "*4*0*2##")):  # zone 2 only: run6 has no other zone
            seen.append((raw, zone2.hvac_action, dict(zone2.extra_state_attributes.get("actuators") or {}) or None))
    await hass.async_block_till_done()
    assert len(added) == 1  # nothing was discovered besides the configured zone
    assert seen[:7] == [
        ("*#4*2*0*0241##", None, None),
        ("*#4*2*12*0235*3##", None, None),
        ("*4*1*2##", None, None),
        ("*#4*2*14*0235*3##", None, None),
        ("*#4*2*13*00##", None, None),
        ("*#4*2#1*20*0##", HVACAction.IDLE, {"1": "off"}),
        ("*#4*2#2*20*5##", HVACAction.IDLE, {"1": "off", "2": "off"}),
    ]
    polls = [f for f, _, _ in seen if f == "*#4*2*13*00##"]
    assert len(polls) == 7  # the first start and six reconnect sweeps
    fan_frames = [f for f, _, _ in seen if f == "*#4*2#2*20*5##"]
    assert len(fan_frames) == 8  # one per poll, repeated once (891.963 s and 892.007 s), reversed once (2043 s)
    assert all(action == HVACAction.IDLE and acts == {"1": "off", "2": "off"} for _, action, acts in seen[7:])
    # the hashed write echo parses as a command, not an event: the platform drops it, the state stays
    assert seen.count(("*#4*2#1*#20*0##", HVACAction.IDLE, {"1": "off", "2": "off"})) == 1
    assert zone2.hvac_action == HVACAction.IDLE
    assert zone2.extra_state_attributes["actuators"] == {"1": "off", "2": "off"}
    assert zone2.current_temperature == 24.1
    assert zone2.target_temperature == 23.5
    assert zone2.hvac_mode == HVACMode.HEAT
    assert zone2.extra_state_attributes["season"] == "heating"
    assert zone2.current_humidity == 38
    # the fan coil revealed by *#4*2#2*20*5## asked its side once; heating season: no *#4*2*11##
    assert zone2.fan_mode == "auto"
    assert [c.args for c in gw.request_zone_dimension.call_args_list] == [("2", 19)]
    assert [str(c.args[0]) for c in gw.send_status_request.await_args_list] == ["*#4*2##"]


async def test_replay_myhomeserver1_all_zone_actuator_burst_through_the_climate_entities(hass, mhs1_rows):
    """``*#4*#0*20##`` on a 34-zone plant: 43 actuator frames in 0.39 s, the zone-0 pumps among them.

    Every configured zone ends with its ``actuators`` filled from the ``Z#1`` / ``Z#2`` frames
    and ``hvac_action: idle``; the five fan coils ask their side once each; the ``0#1``..``0#4``
    pump frames discover no zone, are watched by no entity and asked for nothing.
    """
    burst = _evt_between(mhs1_rows, MHS1_RUN2, "*#4*#0*20##")
    assert len(burst) == 43
    zones = sorted({f.split("*")[2].split("#")[0] for f in burst if not f.startswith("*#4*0#")}, key=int)
    assert len(zones) == 34 and zones[0] == "35" and zones[-1] == "70"
    gw = _trace_gateway("MyHomeServer1", "00:03:50:00:20:24")
    entities, added = await _trace_plant(hass, gw, zones)
    assert sorted(c.args[0] for c in gw.watch_zone_actuators.call_args_list) == zones
    assert all(e.hvac_action is None for e in entities.values())
    _replay(hass, gw.mac, burst)
    await hass.async_block_till_done()
    assert len(added) == 34  # the pump frames 0#1..0#4 discovered no phantom zone
    assert "0" not in entities and "#0" not in entities
    fan_coils = {"47", "48", "57", "60", "70"}
    for z, ent in entities.items():
        expected = {"1": "off", "2": "off"} if z in fan_coils else {"1": "off"}
        assert ent.extra_state_attributes["actuators"] == expected, z
        assert ent.hvac_action == HVACAction.IDLE, z
        assert (ent.fan_mode == "auto") is (z in fan_coils), z
    # the fan coils ask dimension 19 once each, in the order the plant answered
    assert [c.args for c in gw.request_zone_dimension.call_args_list] == [
        ("48", 19), ("47", 19), ("60", 19), ("70", 19), ("57", 19),
    ]
    assert gw.watch_zone_actuators.call_count == 34
    assert [str(c.args[0]) for c in gw.send_status_request.await_args_list] == [f"*#4*{z}##" for z in zones]
