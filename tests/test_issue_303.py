"""Test issue #303: Standalone climate zones fan support and dynamic activation.

Regression tests for:
- Auto-discovery of standalone climate zones exposing FAN_MODE by default.
- Central unit (#0) not exposing FAN_MODE.
- Explicit YAML configuration (fan: false) suppressing fan support.
- Dimension 20 actuator frames (values >= 5) correctly reporting fan speed and fan state
  without corrupting valve hvac_action.
- Dynamic fan feature enablement upon receiving fan frames (Dimension 11 or Dimension 20).
- async_set_fan_mode for standalone zones emitting proper OpenWebNet syntax (*#4*<where>*#11*<speed>##).
- Real trace replay from issue #303 diagnostic bundle.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    MESSAGE_TYPE_ACTION,
    MESSAGE_TYPE_FAN_SPEED,
    OWNEvent,
)

from custom_components.myhome.climate import (
    MyHOMEClimate,
    async_setup_entry,
)
from custom_components.myhome.const import (
    CONF_ENTITY,
    CONF_FAN_SUPPORT,
    CONF_PLATFORMS,
    CONF_STANDALONE,
    DOMAIN,
)

MAC = "00:03:50:11:22:33"


@pytest.fixture
def mock_gateway():
    """Mock gateway with communication methods."""
    gw = MagicMock()
    gw.mac = MAC
    gw.log_id = "[issue 303]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


async def test_standalone_zone_auto_discovery_exposes_fan_by_default(hass: HomeAssistant, mock_gateway):
    """Test that an auto-discovered standalone climate zone exposes FAN_MODE."""
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry_303"
    config_entry.data = {CONF_MAC: MAC}

    # No YAML configuration provided for zone 1 (simulating auto-discovery)
    hass.data = {
        DOMAIN: {
            MAC: {
                CONF_PLATFORMS: {"climate": {}},
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    added_entities: list[MyHOMEClimate] = []
    await async_setup_entry(hass, config_entry, added_entities.extend)

    # Deliver a frame from zone 1 to trigger auto-discovery
    event = OWNEvent.parse("*#4*1*0*0234##")
    async_dispatcher_send(hass, f"myhome_message_{MAC}", event)
    await hass.async_block_till_done()

    assert len(added_entities) == 1
    zone1 = added_entities[0]
    assert zone1._where == "1"
    assert zone1._standalone is True
    assert zone1._fan is True
    assert zone1.supported_features & ClimateEntityFeature.FAN_MODE
    assert zone1.fan_modes == ["auto", "low", "medium", "high"]
    assert zone1.fan_mode == "auto"
    assert zone1.extra_state_attributes["fan_mode"] == "auto"


async def test_central_unit_does_not_expose_fan(hass: HomeAssistant, mock_gateway):
    """Test that a central unit (#0) does not expose FAN_MODE by default."""
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry_303_central"
    config_entry.data = {CONF_MAC: MAC}

    hass.data = {
        DOMAIN: {
            MAC: {
                CONF_PLATFORMS: {"climate": {}},
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    added_entities: list[MyHOMEClimate] = []
    await async_setup_entry(hass, config_entry, added_entities.extend)

    event = OWNEvent.parse("*#4*#0*0*0215##")
    async_dispatcher_send(hass, f"myhome_message_{MAC}", event)
    await hass.async_block_till_done()

    assert len(added_entities) == 1
    central = added_entities[0]
    assert central._where == "#0"
    assert central._central is True
    assert central._fan is False
    assert not (central.supported_features & ClimateEntityFeature.FAN_MODE)
    assert central.fan_modes is None
    assert "fan_mode" not in central.extra_state_attributes


async def test_explicit_yaml_fan_false_respected(hass: HomeAssistant, mock_gateway):
    """Test that explicit fan: false in configuration is respected for standalone zones."""
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry_303_yaml"
    config_entry.data = {CONF_MAC: MAC}

    hass.data = {
        DOMAIN: {
            MAC: {
                CONF_PLATFORMS: {
                    "climate": {
                        "1": {
                            "where": "1",
                            "name": "No Fan Zone",
                            CONF_STANDALONE: True,
                            CONF_FAN_SUPPORT: False,
                        }
                    }
                },
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    added_entities: list[MyHOMEClimate] = []
    await async_setup_entry(hass, config_entry, added_entities.extend)

    assert len(added_entities) == 1
    zone = added_entities[0]
    assert zone._where == "1"
    assert zone._fan is False
    assert not (zone.supported_features & ClimateEntityFeature.FAN_MODE)


async def test_dynamic_fan_mode_activation_on_actuator_event(hass: HomeAssistant, mock_gateway):
    """Test that a fan-disabled entity dynamically activates fan mode upon receiving a fan frame."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Zone 1",
        device_id="4-1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )
    climate.entity_id = "climate.zone_1"
    climate.async_schedule_update_ha_state = MagicMock()

    assert not (climate.supported_features & ClimateEntityFeature.FAN_MODE)
    assert climate._fan is False

    # Receive Dimension 20 actuator frame: Zone 1, actuator 2, value 6 (Fan speed 1 / low)
    event_fan_speed1 = OWNEvent.parse("*#4*1#2*20*6##")
    climate.handle_event(event_fan_speed1)

    assert climate._fan is True
    assert climate.supported_features & ClimateEntityFeature.FAN_MODE
    assert climate.fan_modes == ["auto", "low", "medium", "high"]
    assert climate.fan_mode == "low"
    # Actuator fan status must not set hvac_action to HEATING
    assert climate.hvac_action is None


async def test_dimension_20_does_not_corrupt_valve_hvac_action(hass: HomeAssistant, mock_gateway):
    """Test that fan off (val 5) does not clear valve heating state, and fan on does not override cooling."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Zone 1",
        device_id="4-1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=True,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )
    climate.entity_id = "climate.zone_1"
    climate.async_schedule_update_ha_state = MagicMock()
    climate._attr_hvac_mode = HVACMode.COOL

    # 1. Valve actuator opens in cooling: *#4*1#1*20*1## (actuator 1 is valve)
    event_valve = OWNEvent.parse("*#4*1#1*20*1##")
    climate.handle_event(event_valve)
    assert climate.hvac_action == HVACAction.COOLING

    # 2. Fan turns on at high speed: *#4*1#2*20*8## (actuator 2 is fan, val 8 = speed 3)
    event_fan_high = OWNEvent.parse("*#4*1#2*20*8##")
    climate.handle_event(event_fan_high)
    # Action remains COOLING; fan_mode becomes "high"
    assert climate.hvac_action == HVACAction.COOLING
    assert climate.fan_mode == "high"

    # 3. Fan turns off: *#4*1#2*20*5## (val 5 = fan off)
    event_fan_off = OWNEvent.parse("*#4*1#2*20*5##")
    climate.handle_event(event_fan_off)
    # Action remains COOLING because valve is still open; fan off does NOT set IDLE
    assert climate.hvac_action == HVACAction.COOLING


async def test_dimension_11_event_handling(hass: HomeAssistant, mock_gateway):
    """Test Dimension 11 (fancoil fan speed) event handling."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Zone 6",
        device_id="4-6",
        who="4",
        where="6",
        heating=True,
        cooling=True,
        fan=True,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )
    climate.entity_id = "climate.zone_6"
    climate.async_schedule_update_ha_state = MagicMock()

    # Speed 2 -> medium
    climate.handle_event(OWNEvent.parse("*#4*6*11*2##"))
    assert climate.fan_mode == "medium"

    # Speed 3 -> high
    climate.handle_event(OWNEvent.parse("*#4*6*11*3##"))
    assert climate.fan_mode == "high"

    # Speed 0 -> auto
    climate.handle_event(OWNEvent.parse("*#4*6*11*0##"))
    assert climate.fan_mode == "auto"


async def test_async_set_fan_mode_standalone_syntax(hass: HomeAssistant, mock_gateway):
    """Test async_set_fan_mode on standalone zone generates standalone frame without TypeError."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Zone 1",
        device_id="4-1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=True,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )
    climate.hass = hass
    climate.entity_id = "climate.zone_1"

    # Set fan mode to low (speed 1)
    await climate.async_set_fan_mode("low")
    assert climate.fan_mode == "low"
    mock_gateway.send.assert_awaited_once()
    # Standalone zone syntax is *#4*1*#11*1## (not *#4*#1*#11*1##)
    assert str(mock_gateway.send.call_args[0][0]) == "*#4*1*#11*1##"

    # Set fan mode to auto (speed 0)
    mock_gateway.send.reset_mock()
    await climate.async_set_fan_mode("auto")
    assert climate.fan_mode == "auto"
    assert str(mock_gateway.send.call_args[0][0]) == "*#4*1*#11*0##"


async def test_issue_303_bus_trace_replay(hass: HomeAssistant, mock_gateway):
    """Replay the exact bus trace from issue #303 diagnostic bundle."""
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry_303_replay"
    config_entry.data = {CONF_MAC: MAC}

    hass.data = {
        DOMAIN: {
            MAC: {
                CONF_PLATFORMS: {"climate": {}},
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    added_entities: list[MyHOMEClimate] = []

    def _add_entities(entities):
        for e in entities:
            e.hass = hass
            hass.async_create_task(e.async_added_to_hass())
        added_entities.extend(entities)

    await async_setup_entry(hass, config_entry, _add_entities)

    # Trace excerpt from issue 303:
    trace_frames = [
        "*4*303*1##",
        "*#4*1*0*0234##",
        "*4*4002#1*0#3##",
        "*4*4002*1##",
        "*4*4002#6*0#3##",
        "*4*4002*6##",
        "*#4*1#2*20*5##",  # Zone 1 fan off
        "*#4*6#2*20*5##",  # Zone 6 fan off
        "*#4*6#3*20*5##",  # Zone 6 actuator 3 fan off
        "*#4*3*0*0230##",  # Zone 3 temp 23.0
        "*#4*1#2*20*8##",  # Zone 1 fan speed 3 (high)
        "*#4*3#2*20*6##",  # Zone 3 fan speed 1 (low)
        "*#4*2#2*20*8##",  # Zone 2 fan speed 3 (high)
        "*#4*2#2*20*5##",  # Zone 2 fan off
        "*#4*5#2*20*6##",  # Zone 5 fan speed 1 (low)
    ]

    for frame in trace_frames:
        event = OWNEvent.parse(frame)
        if event is not None:
            async_dispatcher_send(hass, f"myhome_message_{MAC}", event)
            await hass.async_block_till_done()

    await hass.async_block_till_done()

    by_where = {e._where: e for e in added_entities}

    # All zones 1, 2, 3, 5, 6 should be discovered
    assert "1" in by_where
    assert "2" in by_where
    assert "3" in by_where
    assert "5" in by_where
    assert "6" in by_where

    # All should have fan support enabled
    for zone in ("1", "2", "3", "5", "6"):
        assert by_where[zone]._fan is True
        assert by_where[zone].supported_features & ClimateEntityFeature.FAN_MODE

    # Check resulting fan modes:
    # Zone 1 ended at speed 3 -> high
    assert by_where["1"].fan_mode == "high"
    # Zone 3 ended at speed 1 -> low
    assert by_where["3"].fan_mode == "low"
    # Zone 5 ended at speed 1 -> low
    assert by_where["5"].fan_mode == "low"
    # Zone 6 ended with value 5 (fan off, mode remains auto default)
    assert by_where["6"].fan_mode == "auto"


async def test_subordinate_zone_central_mode_update(hass: HomeAssistant, mock_gateway):
    """Test subordinate zone updates when central unit dispatches mode changes."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Zone 1",
        device_id="4-1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=True,
        standalone=False,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )
    climate.entity_id = "climate.zone_1"
    climate._attr_hvac_modes = [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT, HVACMode.COOL]
    climate._attr_hvac_mode = HVACMode.HEAT

    # Master mode OFF -> zone mode OFF, action OFF
    climate._handle_central_mode_update(HVACMode.OFF)
    assert climate.hvac_mode == HVACMode.OFF
    assert climate.hvac_action == HVACAction.OFF

    # Master mode COOL when zone is OFF -> stays OFF
    climate._handle_central_mode_update(HVACMode.COOL)
    assert climate.hvac_mode == HVACMode.OFF

    # Master mode HEAT when zone is not OFF -> zone mode HEAT
    climate._attr_hvac_mode = HVACMode.COOL
    climate._handle_central_mode_update(HVACMode.HEAT)
    assert climate.hvac_mode == HVACMode.HEAT

    # Master mode COOL when zone is not OFF -> zone mode COOL
    climate._handle_central_mode_update(HVACMode.COOL)
    assert climate.hvac_mode == HVACMode.COOL

    # Master mode AUTO when zone is not OFF -> zone mode AUTO
    climate._handle_central_mode_update(HVACMode.AUTO)
    assert climate.hvac_mode == HVACMode.AUTO


async def test_central_unit_async_set_hvac_mode(hass: HomeAssistant, mock_gateway):
    """Test central unit async_set_hvac_mode and central mode dispatching."""
    central = MyHOMEClimate(
        hass=hass,
        name="Central Unit",
        device_id="4-#0",
        who="4",
        where="#0",
        heating=True,
        cooling=True,
        fan=False,
        standalone=False,
        central=True,
        manufacturer="BTicino",
        model="Central Unit (3550)",
        gateway=mock_gateway,
    )
    central.entity_id = "climate.central_unit"

    for mode in [HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO, HVACMode.OFF]:
        mock_gateway.send.reset_mock()
        await central.async_set_hvac_mode(mode)
        assert central.hvac_mode == mode
        mock_gateway.send.assert_awaited_once()

    # Invalid mode does nothing
    mock_gateway.send.reset_mock()
    await central.async_set_hvac_mode("invalid_mode")
    mock_gateway.send.assert_not_called()


async def test_central_unit_async_set_temperature(hass: HomeAssistant, mock_gateway):
    """Test central unit set_temperature in heat and cool modes."""
    central = MyHOMEClimate(
        hass=hass,
        name="Central Unit",
        device_id="4-#0",
        who="4",
        where="#0",
        heating=True,
        cooling=True,
        fan=False,
        standalone=False,
        central=True,
        manufacturer="BTicino",
        model="Central Unit (3550)",
        gateway=mock_gateway,
    )
    central.entity_id = "climate.central_unit"

    # In HEAT mode
    central._attr_hvac_mode = HVACMode.HEAT
    mock_gateway.send.reset_mock()
    await central.async_set_temperature(temperature=21.5)
    assert central._target_temperature == 21.5
    mock_gateway.send.assert_awaited_once()

    # In COOL mode
    central._attr_hvac_mode = HVACMode.COOL
    mock_gateway.send.reset_mock()
    await central.async_set_temperature(temperature=24.0)
    assert central._target_temperature == 24.0
    mock_gateway.send.assert_awaited_once()


async def test_central_unit_event_dispatches_central_mode(hass: HomeAssistant, mock_gateway):
    """Test central unit dispatches central mode update on MESSAGE_TYPE_MODE and MESSAGE_TYPE_MODE_TARGET."""
    central = MyHOMEClimate(
        hass=hass,
        name="Central Unit",
        device_id="4-#0",
        who="4",
        where="#0",
        heating=True,
        cooling=True,
        fan=False,
        standalone=False,
        central=True,
        manufacturer="BTicino",
        model="Central Unit (3550)",
        gateway=mock_gateway,
    )
    central.entity_id = "climate.central_unit"
    central._attr_hvac_modes = [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT, HVACMode.COOL]
    central.async_schedule_update_ha_state = MagicMock()

    # MESSAGE_TYPE_MODE: *4*1*#0## (Mode Heat)
    event_mode = OWNEvent.parse("*4*1*#0##")
    central.handle_event(event_mode)
    assert central.hvac_mode == HVACMode.HEAT

    # MESSAGE_TYPE_MODE_TARGET: *4*1#0215*#0## (Mode Heat, Target 21.5)
    event_target = OWNEvent.parse("*4*1#0215*#0##")
    central.handle_event(event_target)
    assert central.hvac_mode == HVACMode.HEAT
    assert central.target_temperature == 21.5


async def test_dimension_20_and_11_all_speed_branches(hass: HomeAssistant, mock_gateway):
    """Test Dimension 20 values 7 (medium), 9 (auto), mock speed 0, and valve heat/default fallback."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Zone 1",
        device_id="4-1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=True,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )
    climate.entity_id = "climate.zone_1"
    climate.async_schedule_update_ha_state = MagicMock()

    # Dimension 20 val 7 -> speed 2 (medium)
    climate.handle_event(OWNEvent.parse("*#4*1#2*20*7##"))
    assert climate.fan_mode == "medium"

    # Dimension 20 val 9 -> fan_speed None, fan_on True (auto)
    climate.handle_event(OWNEvent.parse("*#4*1#2*20*9##"))
    assert climate.fan_mode == "auto"

    # Mock event for speed == 0 in Dimension 20
    mock_event_speed0 = MagicMock()
    mock_event_speed0.message_type = MESSAGE_TYPE_ACTION
    mock_event_speed0.human_readable_log = "mock"
    mock_event_speed0.fan_speed = 0
    mock_event_speed0.fan_on = True
    climate.handle_event(mock_event_speed0)
    assert climate.fan_mode == "auto"

    # Valve active when hvac_mode is HEAT -> HVACAction.HEATING (lines 660-661)
    climate._attr_hvac_mode = HVACMode.HEAT
    climate.handle_event(OWNEvent.parse("*#4*1#1*20*1##"))
    assert climate.hvac_action == HVACAction.HEATING

    # Valve active when hvac_mode is AUTO -> default fallback HVACAction.HEATING (lines 662-663)
    climate._attr_hvac_mode = HVACMode.AUTO
    climate.handle_event(OWNEvent.parse("*#4*1#1*20*1##"))
    assert climate.hvac_action == HVACAction.HEATING

    # Dimension 11 mock event with speed None and fan_on True (line 691)
    mock_d11 = MagicMock()
    mock_d11.message_type = MESSAGE_TYPE_FAN_SPEED
    mock_d11.human_readable_log = "mock"
    mock_d11.fan_speed = None
    mock_d11.fan_on = True
    climate.handle_event(mock_d11)
    assert climate.fan_mode == "auto"


async def test_climate_setup_skips_non_zone_addresses(hass: HomeAssistant, mock_gateway):
    """Test that non-zone addresses (>= 100) are skipped in entity registry and configured devices."""
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_entry_skip_non_zone",
        data={CONF_MAC: MAC},
    )
    config_entry.add_to_hass(hass)

    # Pre-populate entity registry with a non-zone address >= 100 (triggers lines 105-106)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        domain="climate",
        platform=DOMAIN,
        unique_id=f"{MAC}-4-105",
        config_entry=config_entry,
    )

    hass.data = {
        DOMAIN: {
            MAC: {
                CONF_PLATFORMS: {
                    "climate": {
                        # Configured device with where >= 100 (triggers lines 168-169)
                        "106": {
                            "where": "106",
                            "name": "Invalid Zone 106",
                        }
                    }
                },
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    added_entities = []
    await async_setup_entry(hass, config_entry, added_entities.extend)
    assert len(added_entities) == 0


async def test_climate_restore_invalid_hvac_mode(hass: HomeAssistant, mock_gateway):
    """Test climate entity restoration with unsupported or invalid HVAC modes (lines 476-478)."""
    from unittest.mock import patch

    from homeassistant.core import State

    # 1. Mode not in supported hvac modes (cooling=False, so cool is unsupported -> lines 476)
    climate1 = MyHOMEClimate(
        hass=hass,
        name="Zone 1",
        device_id="4-1",
        who="4",
        where="1",
        heating=True,
        cooling=False,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )
    climate1.entity_id = "climate.zone_1"
    state_unsupported = State("climate.zone_1", "cool", {"temperature": "21.5"})
    with patch.object(climate1, "async_get_last_state", return_value=state_unsupported):
        await climate1.async_added_to_hass()
    assert climate1.hvac_mode == HVACMode.OFF
    assert climate1.target_temperature == 21.5

    # 2. Invalid garbage string raising ValueError (lines 477-478)
    climate2 = MyHOMEClimate(
        hass=hass,
        name="Zone 2",
        device_id="4-2",
        who="4",
        where="2",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )
    climate2.entity_id = "climate.zone_2"
    state_garbage = State("climate.zone_2", "totally_invalid_mode", {})
    with patch.object(climate2, "async_get_last_state", return_value=state_garbage):
        await climate2.async_added_to_hass()
    assert climate2.hvac_mode == HVACMode.OFF

