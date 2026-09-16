"""Tests for MyHOME alarm_control_panel platform (WHO=5)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntityFeature,
)
from homeassistant.const import (
    CONF_NAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    OWNAlarmCommand,
    OWNAlarmEvent,
    OWNEvent,
)

from custom_components.myhome.alarm_control_panel import (
    PLATFORM,
    STATE_ARMED_AWAY,
    STATE_ARMED_HOME,
    STATE_DISARMED,
    STATE_TRIGGERED,
    MyHOMEAlarmControlPanel,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.myhome.const import (
    CONF_PLATFORMS,
    CONF_WHERE,
    DOMAIN,
)
from tests.conftest import attach_runtime


@pytest.fixture
def mock_gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:55:55"
    gw.unique_id = "00:03:50:00:55:55"
    gw.log_id = "[Test Alarm Gateway]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


async def test_alarm_setup_restores_and_discovers(hass: HomeAssistant, mock_gateway):
    """Test alarm platform setup restoring from registry and discovering new devices."""
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {
                    PLATFORM: {
                        "0": {
                            CONF_WHERE: "0",
                            CONF_NAME: "Central Alarm",
                        },
                        "1": {
                            CONF_WHERE: "1",
                            CONF_NAME: "Zone 1 Alarm",
                        },
                    }
                },
            }
        }
    }

    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "alarm_test_entry"

    # Mock entity registry restore check
    mock_er = MagicMock()
    reg_1 = MagicMock()
    reg_1.domain = PLATFORM
    reg_1.unique_id = f"{mac}-5-0"

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_er), \
         patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[reg_1]):

        added_entities = []

        def fake_add_entities(entities):
            added_entities.extend(entities)

        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, fake_add_entities)

        # Restored (0) + Configured from YAML (1) = 2 alarms
        assert len(added_entities) == 2

        # Test discovering a new alarm zone 2 via message dispatcher
        new_alarm_msg = OWNEvent.parse("*5*1*2##")
        assert isinstance(new_alarm_msg, OWNAlarmEvent)
        async_dispatcher_send(hass, f"myhome_message_{mac}", new_alarm_msg)
        assert len(added_entities) == 3

        # Sending message for existing alarm zone triggers update signal rather than creating duplicate
        async_dispatcher_send(hass, f"myhome_message_{mac}", new_alarm_msg)
        assert len(added_entities) == 3

        # Unload
        attach_runtime(hass, config_entry)
        assert await async_unload_entry(hass, config_entry) is True


class TestMyHOMEAlarmEntity:
    """Test MyHOMEAlarmControlPanel entity methods and features."""

    @pytest.fixture
    def alarm_central(self, hass, mock_gateway):
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            alarm = MyHOMEAlarmControlPanel(
                hass=hass,
                name="Central Burglar Alarm",
                entity_name="Central Burglar Alarm",
                device_id="0",
                who="5",
                where="0",
                manufacturer="BTicino",
                model="Burglar Alarm 3486",
                gateway=mock_gateway,
            )
            alarm.hass = hass
            alarm.async_schedule_update_ha_state = MagicMock()
            return alarm

    @pytest.fixture
    def alarm_zone1(self, hass, mock_gateway):
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            alarm = MyHOMEAlarmControlPanel(
                hass=hass,
                name="Zone 1 Burglar Alarm",
                entity_name=None,
                device_id="1",
                who="5",
                where="1",
                manufacturer="BTicino",
                model="Burglar Alarm Zone",
                gateway=mock_gateway,
            )
            alarm.hass = hass
            alarm.async_schedule_update_ha_state = MagicMock()
            return alarm

    def test_alarm_attributes(self, alarm_central, alarm_zone1):
        assert alarm_central.supported_features == (
            AlarmControlPanelEntityFeature.ARM_AWAY
            | AlarmControlPanelEntityFeature.ARM_HOME
            | AlarmControlPanelEntityFeature.TRIGGER
        )
        assert alarm_central.alarm_state == STATE_DISARMED
        assert alarm_central.state == STATE_DISARMED
        assert alarm_central.extra_state_attributes["where"] == "0"
        assert alarm_central.extra_state_attributes["raw_state"] == "disarmed"
        assert alarm_zone1._display_name == "Zone 1 Burglar Alarm"

    async def test_async_lifecycle_and_update(self, alarm_central, alarm_zone1):
        alarm_central.async_on_remove = MagicMock()
        await alarm_central.async_added_to_hass()
        assert alarm_central.async_on_remove.call_count == 2
        alarm_central._gateway_handler.send_status_request.assert_awaited()

        # Zone 1 also subscribes to global zone 0 broadcast
        alarm_zone1.async_on_remove = MagicMock()
        await alarm_zone1.async_added_to_hass()
        assert alarm_zone1.async_on_remove.call_count == 3

    async def test_alarm_commands(self, alarm_central, alarm_zone1):
        # Disarm
        await alarm_central.async_alarm_disarm()
        alarm_central._gateway_handler.send.assert_awaited()
        assert str(alarm_central._gateway_handler.send.call_args[0][0]) == "*5*2*0##"

        # Arm Away
        alarm_central._gateway_handler.send.reset_mock()
        await alarm_central.async_alarm_arm_away()
        alarm_central._gateway_handler.send.assert_awaited()
        assert str(alarm_central._gateway_handler.send.call_args[0][0]) == "*5*1*0##"

        # Arm Home
        alarm_central._gateway_handler.send.reset_mock()
        await alarm_central.async_alarm_arm_home()
        alarm_central._gateway_handler.send.assert_awaited()
        assert str(alarm_central._gateway_handler.send.call_args[0][0]) == "*5*1*0##"

        # Trigger / Panic
        alarm_central._gateway_handler.send.reset_mock()
        await alarm_central.async_alarm_trigger()
        alarm_central._gateway_handler.send.assert_awaited()
        assert str(alarm_central._gateway_handler.send.call_args[0][0]) == "*5*17*0##"

        # Status requests
        cmd_central = OWNAlarmCommand.status("0")
        assert str(cmd_central) == "*#5*0##"
        cmd_where_less = OWNAlarmCommand.status(None)
        assert str(cmd_where_less) == "*#5##"
        cmd_zone1 = OWNAlarmCommand.status("1")
        assert str(cmd_zone1) == "*#5*#1##"

    def test_handle_event(self, alarm_central):
        # Disarmed event (*5*2*0## - deactivation)
        msg_disarmed = OWNEvent.parse("*5*2*0##")
        alarm_central.handle_event(msg_disarmed)
        assert alarm_central.alarm_state == STATE_DISARMED
        assert alarm_central.extra_state_attributes["raw_state"] == "deactivation"
        assert alarm_central.extra_state_attributes["state_code"] == 2

        # Armed away event (*5*1*0## - activation)
        msg_away = OWNEvent.parse("*5*1*0##")
        alarm_central.handle_event(msg_away)
        assert alarm_central.alarm_state == STATE_ARMED_AWAY
        assert alarm_central.extra_state_attributes["raw_state"] == "activation"
        assert alarm_central.extra_state_attributes["state_code"] == 1

        # Armed home event (*5*11*0## - active zone)
        msg_home = OWNEvent.parse("*5*11*0##")
        alarm_central.handle_event(msg_home)
        assert alarm_central.alarm_state == STATE_ARMED_HOME
        assert alarm_central.extra_state_attributes["raw_state"] == "active zone"
        assert alarm_central.extra_state_attributes["state_code"] == 11

        # Triggered event (*5*15*0## - intrusion alarm)
        msg_alarm = OWNEvent.parse("*5*15*0##")
        alarm_central.handle_event(msg_alarm)
        assert alarm_central.alarm_state == STATE_TRIGGERED
        assert alarm_central.extra_state_attributes["raw_state"] == "intrusion alarm"
        assert alarm_central.extra_state_attributes["state_code"] == 15


def test_alarm_states_are_the_core_enum():
    """Panel states come from AlarmControlPanelState (core 2024.11+), not string constants."""
    from homeassistant.components.alarm_control_panel import AlarmControlPanelState

    from custom_components.myhome import alarm_control_panel as mod

    assert mod.STATE_DISARMED is AlarmControlPanelState.DISARMED
    assert mod.STATE_ARMED_HOME is AlarmControlPanelState.ARMED_HOME
    assert mod.STATE_ARMED_AWAY is AlarmControlPanelState.ARMED_AWAY
    assert mod.STATE_TRIGGERED is AlarmControlPanelState.TRIGGERED


