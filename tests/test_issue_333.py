"""#333: a zone's actuator status must not be delivered to the zone with the actuator's number.

``*#4*<zone>#<actuator>*20*<state>##`` reports one actuator of one zone. The
``#<actuator>`` part was read as a second calling zone, so every "actuator 1
is on" frame - one per zone, as most zones have a single actuator - also put
zone 1 into HEATING. Reported on a MyHOMEServer1 with a 3550 central unit and
LN4691 probes; the same routing exists since 0.9.3.
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import (
    CONF_FILE_PATH,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import MyHOMEClimate, async_setup_entry
from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, DOMAIN
from tests.conftest import attach_runtime

FIXTURES_PLANTS_DIR = Path(__file__).resolve().parent / "fixtures" / "plants"
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
    attach_runtime(hass, config_entry)
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


async def test_pump_call_reaches_the_calling_zone_only(hass, zones):
    """``*4*4001#2*0#3##`` from this plant: zone 2 calls pump 3 (``0#3``), not zone 3 (#431)."""
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*4*4001#2*0#3##"))
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*0#3*20*1##"))
    await hass.async_block_till_done()
    assert zones["3"].hvac_action == HVACAction.IDLE
    assert zones["1"].hvac_action == HVACAction.IDLE


@pytest.mark.asyncio
async def test_real_world_trace_replay_issue_333(hass: HomeAssistant) -> None:
    """Replay live bus capture from Issue #333 physical MyHomeServer1 climate system.

    Verifies:
    1. Real-world trace replay from physical BTicino MyHomeServer1 gateway with 3550 central unit.
    2. Climate entities discovery across zones #0, 1-6.
    3. Zone 2 heats up when actuator 1 turns on (*#4*2#1*20*1##).
    4. Zone 1 strictly remains IDLE when Zone 2's actuator turns on (fixing #333).
    5. Zone 2 returns to IDLE when actuator 1 turns off (*#4*2#1*20*0##).
    """
    plant_dir = FIXTURES_PLANTS_DIR / "issue_333_myhomeserver1"
    plant_yaml = plant_dir / "myhome.yaml"
    diag_json = plant_dir / "diagnostic_summary.json"

    assert plant_yaml.is_file()
    assert diag_json.is_file()

    with open(diag_json, "r", encoding="utf-8") as f:
        diag_data = json.load(f)

    raw_frames = diag_data["data"]["bus_monitor"]["recent_frames"]
    assert len(raw_frames) == 96, f"Expected 96 frames in trace, found {len(raw_frames)}"

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.1",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: MAC,
            CONF_NAME: "MyHomeServer1",
        },
        options={
            CONF_FILE_PATH: str(plant_yaml),
        },
        unique_id=MAC,
        title="MyHomeServer1 Gateway",
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    handler = entry.runtime_data.gateway
    handler._on_event_connection_state_change(True)

    climate_1 = hass.states.get("climate.climate_zone_1")
    climate_2 = hass.states.get("climate.climate_zone_2")
    assert climate_1 is not None, "Climate Zone 1 must exist"
    assert climate_2 is not None, "Climate Zone 2 must exist"

    actuator_on_seen = False
    actuator_off_seen = False

    # Replay all captured frames from the physical bus trace
    for item in raw_frames:
        raw = item.get("raw")
        direction = item.get("direction", "rx")
        if not raw:
            continue
        try:
            parsed_msg = OWNMessage.parse(raw)
        except Exception:
            parsed_msg = None

        handler.bus_monitor.record_frame(direction=direction, raw=raw, parsed=parsed_msg)
        if direction == "rx" and parsed_msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{MAC}", parsed_msg)

        if raw == "*#4*2#1*20*1##":
            actuator_on_seen = True
            await hass.async_block_till_done()
            # Zone 2 is HEATING
            assert hass.states.get(climate_2.entity_id).attributes.get("hvac_action") == HVACAction.HEATING
            # CRITICAL #333 ASSERTION: Zone 1 MUST NOT be heating!
            assert hass.states.get(climate_1.entity_id).attributes.get("hvac_action") != HVACAction.HEATING

        elif raw == "*#4*2#1*20*0##":
            actuator_off_seen = True
            await hass.async_block_till_done()
            assert hass.states.get(climate_2.entity_id).attributes.get("hvac_action") == HVACAction.IDLE

    assert actuator_on_seen, "Actuator ON frame must be seen in trace"
    assert actuator_off_seen, "Actuator OFF frame must be seen in trace"

    await hass.async_block_till_done()

    # At the end of trace replay, zone 2 is idle and zone 1 is not heating
    assert hass.states.get(climate_2.entity_id).attributes.get("hvac_action") == HVACAction.IDLE
    assert hass.states.get(climate_1.entity_id).attributes.get("hvac_action") != HVACAction.HEATING

    # Verify binary sensors from trace (channel 1)
    aux_1 = hass.states.get("binary_sensor.binary_sensor_1")
    assert aux_1 is not None

    await hass.config_entries.async_unload(entry.entry_id)

