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
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    OWNAlarmCommand,
    OWNAlarmEvent,
    OWNEvent,
)

from custom_components.myhome.alarm_control_panel import (
    PLATFORM,
    STATE_ARMED_AWAY,
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

        # Zone status frames (*5*11*#1##..*5*11*#8##) from empty gateways (MH200 / MH200N) do NOT discover phantom alarms
        for zone in range(1, 9):
            phantom_zone_msg = OWNEvent.parse(f"*5*11*#{zone}##")
            assert isinstance(phantom_zone_msg, OWNAlarmEvent)
            async_dispatcher_send(hass, f"myhome_message_{mac}", phantom_zone_msg)
        assert len(added_entities) == 3

        # Empty-WHERE frames (*5*WHAT*##, e.g. from F453AV) and star frames route under "0"
        # and update every panel that follows system broadcasts (both central and zone panels)
        central_alarm = added_entities[0]
        zone1_alarm = added_entities[1]

        # Activation (*5*1*## - system operational, not armed away)
        msg_empty_activation = OWNEvent.parse("*5*1*##")
        assert isinstance(msg_empty_activation, OWNAlarmEvent)
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg_empty_activation)
        assert len(added_entities) == 3
        assert central_alarm.alarm_state == (STATE_ARMED_AWAY if msg_empty_activation.is_armed_away else STATE_DISARMED)
        assert central_alarm.extra_state_attributes["raw_state"] == "activation"
        assert central_alarm.extra_state_attributes["state_code"] == 1
        assert zone1_alarm.extra_state_attributes["raw_state"] == "activation"
        assert zone1_alarm.extra_state_attributes["state_code"] == 1

        # Armed away (*5*8*## - engage)
        msg_empty_away = OWNEvent.parse("*5*8*##")
        assert isinstance(msg_empty_away, OWNAlarmEvent)
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg_empty_away)
        assert len(added_entities) == 3
        assert central_alarm.alarm_state == STATE_ARMED_AWAY
        assert central_alarm.extra_state_attributes["raw_state"] == "engage"
        assert central_alarm.extra_state_attributes["state_code"] == 8
        assert zone1_alarm.alarm_state == STATE_ARMED_AWAY
        assert zone1_alarm.extra_state_attributes["raw_state"] == "engage"
        assert zone1_alarm.extra_state_attributes["state_code"] == 8

        # Disarmed (*5*9*## - disengage)
        msg_empty_disarmed = OWNEvent.parse("*5*9*##")
        assert isinstance(msg_empty_disarmed, OWNAlarmEvent)
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg_empty_disarmed)
        assert len(added_entities) == 3
        assert central_alarm.alarm_state == STATE_DISARMED
        assert central_alarm.extra_state_attributes["raw_state"] == "disengage"
        assert central_alarm.extra_state_attributes["state_code"] == 9
        assert zone1_alarm.alarm_state == STATE_DISARMED

        # Star address frame (*5*8**##) parses with WHERE=0 and routes under "0"
        msg_star_away = OWNEvent.parse("*5*8**##")
        assert isinstance(msg_star_away, OWNAlarmEvent)
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg_star_away)
        assert len(added_entities) == 3
        assert central_alarm.alarm_state == STATE_ARMED_AWAY
        assert zone1_alarm.alarm_state == STATE_ARMED_AWAY

        # Power telemetry: battery ok (*5*5*##)
        msg_empty_battery = OWNEvent.parse("*5*5*##")
        assert isinstance(msg_empty_battery, OWNAlarmEvent)
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg_empty_battery)
        assert len(added_entities) == 3
        assert central_alarm.extra_state_attributes["raw_state"] == "battery ok"
        assert central_alarm.extra_state_attributes["state_code"] == 5
        assert zone1_alarm.extra_state_attributes["raw_state"] == "battery ok"
        assert zone1_alarm.extra_state_attributes["state_code"] == 5

        # Power telemetry: mains present (*5*7*##)
        msg_empty_mains = OWNEvent.parse("*5*7*##")
        assert isinstance(msg_empty_mains, OWNAlarmEvent)
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg_empty_mains)
        assert len(added_entities) == 3
        assert central_alarm.extra_state_attributes["raw_state"] == "network present"
        assert central_alarm.extra_state_attributes["state_code"] == 7
        assert zone1_alarm.extra_state_attributes["raw_state"] == "network present"
        assert zone1_alarm.extra_state_attributes["state_code"] == 7

        # Unload
        attach_runtime(hass, config_entry)
        assert await async_unload_entry(hass, config_entry) is True


async def test_alarm_empty_where_does_not_discover_phantom_alarm(hass: HomeAssistant, mock_gateway):
    """Test that empty-WHERE system frames (*5*WHAT*##) never discover phantom alarm panels."""
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {},
            }
        }
    }

    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "alarm_empty_where_entry"

    mock_er = MagicMock()
    with patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_er), \
         patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]):

        added_entities = []

        def fake_add_entities(entities):
            added_entities.extend(entities)

        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, fake_add_entities)
        assert len(added_entities) == 0

        # Gateway responses (*5*9*##, *5*1*##, *5*8*##, *5*5*##, *5*7*##) must never create entities
        for frame in ("*5*9*##", "*5*1*##", "*5*8*##", "*5*5*##", "*5*7*##"):
            msg = OWNEvent.parse(frame)
            assert isinstance(msg, OWNAlarmEvent)
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)

        assert len(added_entities) == 0


async def test_alarm_registry_cleanup_purges_phantom_zones(hass: HomeAssistant, mock_gateway):
    """Test that phantom zone partition entries in the registry are purged on startup."""
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {},
            }
        }
    }

    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "alarm_cleanup_entry"

    mock_er = MagicMock()
    # Central unit (should NOT be removed)
    reg_central = MagicMock()
    reg_central.domain = PLATFORM
    reg_central.unique_id = f"{mac}-5-0"
    reg_central.entity_id = "alarm_control_panel.alarm_0"

    # Phantom zones #1 and #2 (SHOULD be removed)
    reg_zone1 = MagicMock()
    reg_zone1.domain = PLATFORM
    reg_zone1.unique_id = f"{mac}-5-#1"
    reg_zone1.entity_id = "alarm_control_panel.alarm_1"

    reg_zone2 = MagicMock()
    reg_zone2.domain = PLATFORM
    reg_zone2.unique_id = f"{mac}-5-#2"
    reg_zone2.entity_id = "alarm_control_panel.alarm_2"

    entries = [reg_central, reg_zone1, reg_zone2]

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_er), \
         patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=entries):

        added_entities = []

        def fake_add_entities(entities):
            added_entities.extend(entities)

        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, fake_add_entities)

        # Only the central unit (0) should be restored
        assert len(added_entities) == 1
        assert added_entities[0]._where == "0"

        # Phantom zones should have been removed from registry
        mock_er.async_remove.assert_any_call("alarm_control_panel.alarm_1")
        mock_er.async_remove.assert_any_call("alarm_control_panel.alarm_2")
        assert mock_er.async_remove.call_count == 2


async def test_alarm_registry_cleanup_preserves_yaml_zones(hass: HomeAssistant, mock_gateway):
    """Test that zone partition entries configured in YAML are NOT purged from registry."""
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {
                    PLATFORM: {
                        "#1": {"name": "Protected Zone 1"},
                    }
                },
            }
        }
    }

    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "alarm_cleanup_yaml_entry"

    mock_er = MagicMock()
    reg_central = MagicMock()
    reg_central.domain = PLATFORM
    reg_central.unique_id = f"{mac}-5-0"
    reg_central.entity_id = "alarm_control_panel.alarm_0"

    reg_zone1 = MagicMock()
    reg_zone1.domain = PLATFORM
    reg_zone1.unique_id = f"{mac}-5-#1"
    reg_zone1.entity_id = "alarm_control_panel.alarm_1"

    reg_zone2 = MagicMock()
    reg_zone2.domain = PLATFORM
    reg_zone2.unique_id = f"{mac}-5-#2"
    reg_zone2.entity_id = "alarm_control_panel.alarm_2"

    entries = [reg_central, reg_zone1, reg_zone2]

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_er), \
         patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=entries):

        added_entities = []

        def fake_add_entities(entities):
            added_entities.extend(entities)

        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, fake_add_entities)

        # Central unit (0) and YAML-protected Zone 1 (#1) should be restored
        assert len(added_entities) == 2
        wheres = [e._where for e in added_entities]
        assert "0" in wheres
        assert "#1" in wheres

        # Only phantom zone 2 should have been removed from registry
        mock_er.async_remove.assert_called_once_with("alarm_control_panel.alarm_2")


async def test_alarm_bus_discovers_central_unit(hass: HomeAssistant, mock_gateway):
    """Test that a central unit frame (WHERE=0) on the bus discovers an alarm panel."""
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {},
            }
        }
    }

    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "alarm_bus_central_entry"

    mock_er = MagicMock()
    with patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_er), \
         patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]):

        added_entities = []

        def fake_add_entities(entities):
            added_entities.extend(entities)

        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, fake_add_entities)
        assert len(added_entities) == 0

        # Central unit frame (*5*1*0##) from bus discovers alarm panel
        central_msg = OWNEvent.parse("*5*1*0##")
        assert isinstance(central_msg, OWNAlarmEvent)
        async_dispatcher_send(hass, f"myhome_message_{mac}", central_msg)
        assert len(added_entities) == 1
        assert added_entities[0]._where == "0"

        # Unload
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
        # Read-only: the central unit rejects SCS arm/disarm (#564)
        assert alarm_central.supported_features == AlarmControlPanelEntityFeature(0)
        assert alarm_central.alarm_state == STATE_DISARMED
        assert alarm_central.state == STATE_DISARMED
        assert alarm_central.extra_state_attributes["where"] == "0"
        assert alarm_central.extra_state_attributes["raw_state"] == "disarmed"
        assert alarm_zone1._display_name == "Zone 1 Burglar Alarm"

    async def test_async_lifecycle_and_update(self, alarm_central, alarm_zone1):
        alarm_central.async_on_remove = MagicMock()
        await alarm_central.async_added_to_hass()
        assert alarm_central.async_on_remove.call_count == 1  # availability; frames come via the router
        alarm_central._gateway_handler.send_status_request.assert_awaited()

        alarm_zone1.async_on_remove = MagicMock()
        await alarm_zone1.async_added_to_hass()
        assert alarm_zone1.async_on_remove.call_count == 1

    async def test_alarm_is_read_only(self, alarm_central):
        # Core gates arm/trigger on supported_features; disarm has no flag, so
        # the entity refuses it rather than send a frame the panel rejects.
        with pytest.raises(ServiceValidationError) as err:
            await alarm_central.async_alarm_disarm()
        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "alarm_read_only"
        assert err.value.translation_placeholders == {"name": alarm_central._display_name}
        alarm_central._gateway_handler.send.assert_not_awaited()

    def test_status_requests(self):
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

        # Activation (*5*1*0##) is "system operational", not armed: the F454
        # trace (#311) sends it on every disarm (*5*2*0## -> *5*1*0## -> *5*9*0##).
        # OWNd <= 2.0.0b9 still reads it as armed_away; OWNd#66 does not.
        msg_activation = OWNEvent.parse("*5*1*0##")
        alarm_central.handle_event(msg_activation)
        assert alarm_central.alarm_state == (STATE_ARMED_AWAY if msg_activation.is_armed_away else STATE_DISARMED)
        assert alarm_central.extra_state_attributes["raw_state"] == "activation"
        assert alarm_central.extra_state_attributes["state_code"] == 1

        # Armed away event (*5*8*0## - engage)
        msg_away = OWNEvent.parse("*5*8*0##")
        alarm_central.handle_event(msg_away)
        assert alarm_central.alarm_state == STATE_ARMED_AWAY
        assert alarm_central.extra_state_attributes["raw_state"] == "engage"
        assert alarm_central.extra_state_attributes["state_code"] == 8

        # Active zone (*5*11*0##): home and away arming look the same on the
        # bus, so the panel never reports armed_home and keeps its state
        msg_zone = OWNEvent.parse("*5*11*0##")
        alarm_central.handle_event(msg_zone)
        assert alarm_central.alarm_state == STATE_ARMED_AWAY
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
    assert mod.STATE_ARMED_AWAY is AlarmControlPanelState.ARMED_AWAY
    assert mod.STATE_TRIGGERED is AlarmControlPanelState.TRIGGERED


