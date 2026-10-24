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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from OWNd.message import OWNHeatingEvent
from OWNd.profiles import GenericGatewayProfile, get_gateway_profile

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.const import CONF_FIRMWARE
from custom_components.myhome.gateway import MyHOMEGatewayHandler

MAC = "00:03:50:00:00:44"

F461_POLL_REPLY = (
    "*#4*2*0*0239##",
    "*#4*2*12*0235*3##",
    "*4*1*2##",
    "*#4*2*14*0210*3##",
    "*#4*2*13*00##",
)


def _zone(hass, where="2", gateway=None, **kwargs):
    if gateway is None:
        gateway = MagicMock()
        gateway.mac = MAC
        gateway.send = AsyncMock()
        gateway.send_status_request = AsyncMock()
        gateway.has_central_unit = False
    zone = MyHOMEClimate(
        hass=hass, name=f"Zone {where}", device_id=f"4-{where}", who="4", where=where,
        heating=True, cooling=True, fan=False, standalone=True, central=False,
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


async def test_fan_coil_side_is_asked_again_only_if_the_first_answer_never_came(hass):
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2#2*20*5##", "*#4*2*19*5*0##")
    assert zone._fan_coil_sides_asked is False
    _feed(zone, "*#4*2#2*20*5##")  # the side is known: nothing more to ask
    zone._gateway_handler.request_zone_dimension.assert_called_once_with("2", 19)


async def test_restored_fan_mode_without_the_side_asks_for_it_on_add(hass):
    zone = _zone(hass)
    zone._gateway_handler.initial_discovery_pending = False
    zone._gateway_handler.sweep_active = False
    zone._gateway_handler.supports_zone_status = True
    zone._enable_fan_mode()  # what a restored ``fan_mode`` attribute does
    await zone.async_added_to_hass()
    zone._gateway_handler.request_zone_dimension.assert_called_once_with("2", 19)


async def test_a_gateway_without_zone_requests_keeps_polling_the_fan_speed(hass):
    """An older gateway handler (no ``request_zone_dimension``) behaves as before."""
    zone = _zone(hass)
    del zone._gateway_handler.request_zone_dimension
    _feed(zone, *F461_POLL_REPLY, "*#4*2#2*20*5##")
    assert zone._fan_coil_sides_asked is False
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

@pytest.mark.parametrize("where", ["", "2*22", "2 ", "a", "*#0", "2#1*20"])
def test_an_invalid_zone_address_is_refused_at_once(hass, where):
    """``2*22`` would make ``*#4*2*22*20##``, a dimension 22 write that reboots the F461."""
    handler = _handler(hass, "F454")
    with pytest.raises(ValueError, match="Invalid zone address"):
        handler.request_zone_dimension(where, 20)
    assert handler._zone_requests == set()
    assert handler._zone_request_task is None


@pytest.mark.parametrize("where", ["2", "#2", "01", "3#4#01", "#3#4#01", "2#1#4#01", "2#1"])
def test_a_plain_or_routed_zone_address_is_accepted(hass, where):
    handler = _handler(hass, "F454")
    handler.request_zone_dimension(where, 20)
    assert handler._zone_requests == {(where, 20)}


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


# ------------------------------------------------------------------ gateway: identity settles after the entities were added (review 5639409551, 4)

async def test_a_gateway_identified_late_as_refusing_is_not_asked(hass, fast_bus_pacing):
    """Entities are added as ``Generic``; WHO 13 / WHO 1013 then says MH202 before the sweep ends."""
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


async def test_a_gateway_identified_late_as_supported_is_asked(hass, fast_bus_pacing):
    """Entities are added as a misconfigured MH200N; WHO 13 then corrects it to F461."""
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
    monkeypatch.setattr("custom_components.myhome.gateway.ZONE_REQUEST_COALESCE", 5.0)
    handler = _handler(hass, "F461")
    handler.watch_zone_actuators("2")
    await hass.async_block_till_done()
    await handler.initial_discovery()
    await _settle(hass)
    assert _drain(handler)[-1] == "*#4*#0*20##"
