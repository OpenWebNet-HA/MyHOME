"""The burglar alarm panel is read-only through Home Assistant's own services.

The central unit rejects arm/disarm sent as WHO 5 frames over SCS (#564), so
the panel advertises no arm/trigger features and refuses disarm. Arming goes
through installer-programmed AUX frames via myhome.send_message.
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import CONF_ENTITY, CONF_FIRMWARE, DOMAIN

PLANT_YAML = Path(__file__).resolve().parent / "fixtures" / "plants" / "issue_311_f454" / "myhome.yaml"


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["alarm_arm_away", "alarm_arm_home", "alarm_trigger", "alarm_disarm"])
async def test_alarm_services_send_nothing(hass: HomeAssistant, service: str) -> None:
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
        options={CONF_FILE_PATH: str(PLANT_YAML)},
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
    async_dispatcher_send(hass, f"myhome_message_{mac}", OWNMessage.parse("*5*9*0##"))
    await hass.async_block_till_done()

    state = hass.states.get("alarm_control_panel.alarm_0") or hass.states.get("alarm_control_panel.alarm_zone_0")
    assert state is not None
    assert state.state == AlarmControlPanelState.DISARMED
    assert state.attributes["supported_features"] == 0

    with patch.object(handler, "send", AsyncMock()) as send:
        # Arm/trigger: core refuses the unsupported feature; disarm: the entity refuses
        expected = ServiceValidationError if service == "alarm_disarm" else HomeAssistantError
        with pytest.raises(expected):
            await hass.services.async_call(
                "alarm_control_panel", service, {"entity_id": state.entity_id}, blocking=True
            )
        send.assert_not_awaited()

    await hass.config_entries.async_unload(entry.entry_id)
