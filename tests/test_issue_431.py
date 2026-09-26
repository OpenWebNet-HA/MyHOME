"""#431: a pump status (``*#4*0#<n>*20*...##``) must not switch zone ``n``.

WHERE ``Z#N`` is actuator ``N`` of zone ``Z`` (``Z = 0..99``), so ``0#2`` is
actuator 2 of zone 0: the circulation pump the zones call with
``*4*4001#<zone>*0#<n>##``. OWNd <= 2.0.0b8 reports the frame as zone 2, and
MyHOME read the WHERE=0 parameter as the zone too, so every time zone 1 started
pump 2, zone 2 showed HEATING. Reported on a MyHOMEServer1 with an F459 Driver
Manager, MyHOME 2.0.0b13 + OWNd 2.0.0b8.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import CONF_MAC
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent

from custom_components.myhome.climate import (
    MyHOMEClimate,
    _calling_zones,
    _zone_address,
    _zone_route_keys,
    async_setup_entry,
)
from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, DOMAIN
from tests.conftest import attach_runtime

MAC = "00:03:50:00:04:31"

# The reporter's received frames, in order: zone 1 set to 26 °C, then to 17 °C.
ISSUE_431_TRACE_ON = [
    "*#4*1*0*0196##",
    "*4*1*1##",
    "*#4*1*12*0260*3##",
    "*4*1*1##",
    "*4*110#0170*#0##",
    "*4*21*#0##",
    "*4*24*#0##",
    "*#4*1#1*20*1##",  # zone 1, actuator 1 on
    "*#4*0#2*20*1##",  # pump 2 on
]
ISSUE_431_TRACE_OFF = [
    "*#4*1*0*0195##",
    "*#4*1*12*0170*3##",
    "*4*1*1##",
    "*#4*0#2*20*0##",  # pump 2 off
    "*#4*1#1*20*0##",  # zone 1, actuator 1 off
]


@pytest.fixture
async def plant(hass):
    """Central unit and zones 1-2; returns the entities by WHERE and the list discovery adds to."""
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.log_id = "[issue 431]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    devices = {
        where: {"where": where, "name": f"Zone {where}", "heat": True, "cool": False, "standalone": False}
        for where in ("#0", "1", "2")
    }
    hass.data[DOMAIN] = {MAC: {CONF_PLATFORMS: {"climate": devices}, CONF_ENTITY: gateway}}
    config_entry = MagicMock()
    config_entry.entry_id = "issue_431"
    config_entry.data = {CONF_MAC: MAC}
    attach_runtime(hass, config_entry)
    added: list[MyHOMEClimate] = []
    await async_setup_entry(hass, config_entry, added.extend)
    for entity in added:
        entity.hass = hass
        entity._attr_hvac_mode = HVACMode.HEAT
        entity._attr_hvac_action = HVACAction.IDLE
        await entity.async_added_to_hass()
    return {e._where: e for e in added}, added


def _send(hass, frames):
    for frame in frames:
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(frame))


@pytest.mark.parametrize("frame", ["*#4*0#2*20*1##", "*#4*0#2*20*0##", "*#4*0#3*20*1##"])
def test_pump_status_names_no_zone(frame):
    message = OWNEvent.parse(frame)
    assert _calling_zones(message) == ([], None)
    assert _zone_address(message) is None
    pump = frame.split("*")[2].split("#")[1]
    assert pump not in _zone_route_keys(message, None)


def test_pump_call_names_only_the_calling_zone():
    """``*4*4001#1*0#3##``: zone 1 calls pump 3 - zone 1, not zone 3."""
    message = OWNEvent.parse("*4*4001#1*0#3##")
    assert _calling_zones(message) == (["1"], None)
    assert "3" not in _zone_route_keys(message, _zone_address(message))


def test_zone_actuator_still_names_its_zone():
    """#333 is unchanged: ``2#1`` is zone 2's actuator 1."""
    message = OWNEvent.parse("*#4*2#1*20*1##")
    assert _calling_zones(message) == (["2"], None)


def test_where_zero_behind_an_interface_is_not_zone_4():
    """``0#4#01`` is WHERE=0 behind F422 interface 01; OWNd b8 reads the ``4`` as zone 4."""
    message = OWNEvent.parse("*#4*0#4#01*20*1##")
    assert _calling_zones(message) == ([], "01")
    assert _zone_address(message) is None
    assert not {"4", "4#4#01"} & set(_zone_route_keys(message, None))


def test_zone_behind_an_interface_keeps_its_zone():
    message = OWNEvent.parse("*#4*1#4#01*20*1##")
    assert _calling_zones(message) == (["1"], "01")


def test_four_zone_central_form_keeps_its_zone():
    """``#0#5`` (hashed) is zone 5 of a 4-zone central unit, not a pump."""
    message = OWNEvent.parse("*4*101*#0#5##")
    assert _calling_zones(message) == (["5"], None)


async def test_reporter_trace_leaves_zone_2_idle(hass, plant):
    zones, _ = plant
    _send(hass, ISSUE_431_TRACE_ON)
    await hass.async_block_till_done()
    assert zones["1"].hvac_action == HVACAction.HEATING
    assert zones["2"].hvac_action == HVACAction.IDLE

    _send(hass, ISSUE_431_TRACE_OFF)
    await hass.async_block_till_done()
    assert zones["1"].hvac_action == HVACAction.IDLE
    assert zones["2"].hvac_action == HVACAction.IDLE


async def test_pump_frame_discovers_no_phantom_zone(hass, plant):
    """Pump 7 turning on does not create a "Climate Zone 7"."""
    _, added = plant
    before = len(added)
    _send(hass, ["*#4*0#7*20*1##", "*4*4001#1*0#7##"])
    await hass.async_block_till_done()
    assert len(added) == before
