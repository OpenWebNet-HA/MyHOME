"""#333: a zone's actuator status must not be delivered to the zone with the actuator's number.

``*#4*<zone>#<actuator>*20*<state>##`` reports one actuator of one zone. The
``#<actuator>`` part was read as a second calling zone, so every "actuator 1
is on" frame - one per zone, as most zones have a single actuator - also put
zone 1 into HEATING. Reported on a MyHOMEServer1 with a 3550 central unit and
LN4691 probes; the same routing exists since 0.9.3.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import CONF_MAC
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent

from custom_components.myhome.climate import MyHOMEClimate, async_setup_entry
from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, DOMAIN

MAC = "00:03:50:00:03:33"


@pytest.fixture
async def zones(hass):
    """Central unit and zones 1-3 as the reporter's myhome.yaml declares them."""
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.log_id = "[issue 333]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    devices = {
        where: {"where": where, "name": f"Zone {where}", "heat": True, "cool": False, "standalone": False}
        for where in ("#0", "1", "2", "3")
    }
    hass.data[DOMAIN] = {MAC: {CONF_PLATFORMS: {"climate": devices}, CONF_ENTITY: gateway}}
    config_entry = MagicMock()
    config_entry.entry_id = "issue_333"
    config_entry.data = {CONF_MAC: MAC}
    added: list[MyHOMEClimate] = []
    await async_setup_entry(hass, config_entry, added.extend)
    by_where = {e._where: e for e in added}
    for entity in added:
        entity.hass = hass
        entity._attr_hvac_mode = HVACMode.HEAT
        entity._attr_hvac_action = HVACAction.IDLE
        await entity.async_added_to_hass()
    return by_where


async def test_actuator_status_reaches_its_own_zone_only(hass, zones):
    """Zone 2's actuator 1 turning on heats zone 2 - and nothing else."""
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*2#1*20*1##"))
    await hass.async_block_till_done()

    assert zones["2"].hvac_action == HVACAction.HEATING
    assert zones["1"].hvac_action == HVACAction.IDLE
    assert zones["3"].hvac_action == HVACAction.IDLE
    assert zones["#0"].hvac_action == HVACAction.IDLE


async def test_every_zone_reports_actuator_one(hass, zones):
    """The reporter's plant: one actuator per zone, all numbered 1 - zone 1 follows only its own."""
    for frame in ("*#4*3#1*20*1##", "*#4*2#1*20*1##"):
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(frame))
    await hass.async_block_till_done()
    assert zones["1"].hvac_action == HVACAction.IDLE
    assert zones["2"].hvac_action == HVACAction.HEATING
    assert zones["3"].hvac_action == HVACAction.HEATING

    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*1#1*20*1##"))
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*2#1*20*0##"))
    await hass.async_block_till_done()
    assert zones["1"].hvac_action == HVACAction.HEATING
    assert zones["2"].hvac_action == HVACAction.IDLE


async def test_actuator_frame_discovers_no_phantom_zone(hass, zones):
    """Zone 5's actuator 2 (``5#2``) does not discover a "zone 2" - nor is one created for actuator 7."""
    before = set(zones)
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*3#7*20*1##"))
    await hass.async_block_till_done()
    assert zones["3"].hvac_action == HVACAction.HEATING
    assert set(zones) == before


async def test_where_zero_frames_still_name_their_zone_in_the_parameter(hass, zones):
    """``*#4*0#2*0*0215##`` is zone 2's temperature: the WHERE=0 parameter form keeps working."""
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*0#2*0*0215##"))
    await hass.async_block_till_done()
    assert zones["2"].current_temperature == 21.5
    assert zones["1"].current_temperature is None
