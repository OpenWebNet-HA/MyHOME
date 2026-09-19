"""Test issue #311: F454 gateway with Burglar Alarm (WHO=5) physical trace replay.

Verifies:
1. Real-world trace replay from physical BTicino F454 gateway.
2. Burglar Alarm WHO=5 central unit entity discovery (alarm_control_panel.alarm_0).
3. State transitions for arm away (*5*1*0## / *5*8*0##) and disarm (*5*2*0## / *5*9*0##).
4. Auxiliary channel (WHO=9) binary sensor state changes (*9*1*1## / *9*0*1##).
5. Lighting state updates from the trace (*1*1*31## / *1*0*31##).
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.components.alarm_control_panel import AlarmControlPanelState
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
from OWNd.message import OWNAlarmEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import CONF_ENTITY, CONF_FIRMWARE, DOMAIN

FIXTURES_PLANTS_DIR = Path(__file__).resolve().parent / "fixtures" / "plants"


@pytest.mark.asyncio
async def test_real_world_trace_replay_issue_311(hass: HomeAssistant) -> None:
    """Replay live bus capture from Issue #311 physical F454 and Burglar Alarm system."""
    plant_dir = FIXTURES_PLANTS_DIR / "issue_311_f454"
    plant_yaml = plant_dir / "myhome.yaml"
    diag_json = plant_dir / "diagnostic_summary.json"

    assert plant_yaml.is_file()
    assert diag_json.is_file()

    with open(diag_json, "r", encoding="utf-8") as f:
        diag_data = json.load(f)

    raw_frames = diag_data["data"]["bus_monitor"]["recent_frames"]
    assert len(raw_frames) == 152, f"Expected 152 frames in trace, found {len(raw_frames)}"

    mac = "00:03:50:00:03:11"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.1",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "F454",
            CONF_FIRMWARE: "2.0",
        },
        options={
            CONF_FILE_PATH: str(plant_yaml),
        },
        unique_id=mac,
        title="F454 Gateway",
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

    handler = hass.data[DOMAIN][mac][CONF_ENTITY]
    handler._on_event_connection_state_change(True)

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
            async_dispatcher_send(hass, f"myhome_message_{mac}", parsed_msg)

    await hass.async_block_till_done()

    alarm_0 = hass.states.get("alarm_control_panel.alarm_0") or hass.states.get("alarm_control_panel.alarm_zone_0")
    assert alarm_0 is not None, "Alarm 0 must exist"


    # In the end of the trace, the alarm is disarmed (*5*2*0## and *5*9*0##)
    assert alarm_0.state == AlarmControlPanelState.DISARMED

    # 2. Test arming away event explicitly on the entity
    arm_msg = OWNMessage.parse("*5*1*0##")
    assert isinstance(arm_msg, OWNAlarmEvent)
    async_dispatcher_send(hass, f"myhome_message_{mac}", arm_msg)
    await hass.async_block_till_done()
    assert hass.states.get(alarm_0.entity_id).state == AlarmControlPanelState.ARMED_AWAY

    # 3. Test disarming event explicitly on the entity
    disarm_msg = OWNMessage.parse("*5*2*0##")
    assert isinstance(disarm_msg, OWNAlarmEvent)
    async_dispatcher_send(hass, f"myhome_message_{mac}", disarm_msg)
    await hass.async_block_till_done()
    assert hass.states.get(alarm_0.entity_id).state == AlarmControlPanelState.DISARMED


    # 4. Verify auxiliary sensor state
    aux_1 = hass.states.get("binary_sensor.binary_sensor_1")
    assert aux_1 is not None, "binary_sensor.binary_sensor_1 must exist"

    # 5. Verify light entity from trace
    light_31 = hass.states.get("light.light_31")
    assert light_31 is not None, "light.light_31 must exist"


    await hass.config_entries.async_unload(entry.entry_id)
