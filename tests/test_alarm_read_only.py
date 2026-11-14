"""The burglar alarm panel is read-only through Home Assistant's own services.

The central unit rejects arm/disarm sent as WHO 5 frames over SCS (#564), while
HomeKit Bridge, voice assistants, and Home Assistant's armed_away triggers and
conditions gate STATE_ARMED_AWAY capability on AlarmControlPanelEntityFeature.ARM_AWAY
(#466). The panel therefore advertises ARM_AWAY and refuses both alarm_arm_away
and alarm_disarm with ServiceValidationError. Arming goes through
installer-programmed AUX frames via myhome.send_message.
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    device_condition,
    device_trigger,
)
from homeassistant.const import (
    CONF_FILE_PATH,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.setup import async_setup_component
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
    assert state.attributes["supported_features"] == AlarmControlPanelEntityFeature.ARM_AWAY

    with patch.object(handler, "send", AsyncMock()) as send:
        # arm_away & disarm: entity refuses with localized ServiceValidationError;
        # arm_home & trigger: core refuses the unsupported feature.
        expected = (
            ServiceValidationError
            if service in ("alarm_arm_away", "alarm_disarm")
            else HomeAssistantError
        )
        with pytest.raises(expected) as err_info:
            await hass.services.async_call(
                "alarm_control_panel", service, {"entity_id": state.entity_id}, blocking=True
            )
        if service in ("alarm_arm_away", "alarm_disarm"):
            assert isinstance(err_info.value, ServiceValidationError)
            assert err_info.value.translation_domain == DOMAIN
            assert err_info.value.translation_key == "alarm_read_only"
        send.assert_not_awaited()

    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_alarm_arm_away_feature_unlocks_core_triggers_conditions_and_homekit_valid_states(
    hass: HomeAssistant,
) -> None:
    """ARM_AWAY unlocks HA Core armed_away triggers/conditions and HomeKit ValidValues (#466)."""
    mac = "00:03:50:00:04:66"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.1",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "F454",
            CONF_FIRMWARE: "2.0.51",
        },
        options={},
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

    entity_id = "alarm_control_panel.alarm_0"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == AlarmControlPanelState.DISARMED

    # 1. Exact bitmask check used by HomeKit SecuritySystem.__init__
    # (homeassistant/components/homekit/type_security_systems.py) to include
    # HK_ALARM_AWAY_ARMED (1) in SecuritySystemCurrentState ValidValues without
    # advertising unsupported Home/Night/Trigger modes.
    features = int(state.attributes["supported_features"])
    assert bool(features & AlarmControlPanelEntityFeature.ARM_AWAY) is True
    assert bool(features & AlarmControlPanelEntityFeature.ARM_HOME) is False
    assert bool(features & AlarmControlPanelEntityFeature.ARM_NIGHT) is False
    assert bool(features & AlarmControlPanelEntityFeature.TRIGGER) is False

    # 2. Device triggers and device conditions expose armed_away / is_armed_away.
    ent_reg = er.async_get(hass)
    reg_entry = ent_reg.async_get(entity_id)
    assert reg_entry is not None and reg_entry.device_id is not None

    triggers = await device_trigger.async_get_triggers(hass, reg_entry.device_id)
    trigger_types = {t["type"] for t in triggers if t.get("entity_id") == reg_entry.id}
    assert "armed_away" in trigger_types
    assert "disarmed" in trigger_types
    assert "triggered" in trigger_types
    assert "armed_home" not in trigger_types

    conditions = await device_condition.async_get_conditions(hass, reg_entry.device_id)
    condition_types = {c["type"] for c in conditions if c.get("entity_id") == reg_entry.id}
    assert "is_armed_away" in condition_types
    assert "is_disarmed" in condition_types
    assert "is_triggered" in condition_types
    assert "is_armed_home" not in condition_types

    # 3. Live Core domain trigger `alarm_control_panel.armed_away` attaches and
    # fires when `*5*8*0##` arrives from the SCS bus.
    fired_events: list[Event] = []

    @callback
    def _record_event(event: Event) -> None:
        fired_events.append(event)

    hass.bus.async_listen("myhome_test_alarm_armed_away", _record_event)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "alias": "Alarm armed away test",
                    "trigger": {
                        "trigger": "alarm_control_panel.armed_away",
                        "target": {"entity_id": entity_id},
                    },
                    "action": {"event": "myhome_test_alarm_armed_away"},
                }
            ]
        },
    )
    await hass.async_block_till_done()
    assert len(fired_events) == 0

    async_dispatcher_send(hass, f"myhome_message_{mac}", OWNMessage.parse("*5*8*0##"))
    await hass.async_block_till_done()

    updated = hass.states.get(entity_id)
    assert updated is not None
    assert updated.state == AlarmControlPanelState.ARMED_AWAY
    assert len(fired_events) == 1

    await hass.config_entries.async_unload(entry.entry_id)
