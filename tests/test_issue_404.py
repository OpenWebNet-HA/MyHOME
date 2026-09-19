"""Regression tests for Issue #404 and Issue #303.

Verifies:
1. Standalone climate zones with fancoil fan actuators update hvac_action to COOLING
   or HEATING when the fan runs (Dimension 20), resolving Issue #404.
2. When the fan stops (Dimension 20 value 5) and all actuators are off, hvac_action
   correctly transitions back to IDLE (or OFF).
3. The configured fan_mode='auto' is preserved and NOT overwritten by Dimension 20
   instantaneous running speed telemetry, resolving Issue #303 comment 5728387183.
4. Full 42-frame bus trace from Issue #404 replays cleanly through Home Assistant.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent

from custom_components.myhome.climate import MyHOMEClimate, async_setup_entry
from custom_components.myhome.const import (
    CONF_ENTITY,
    CONF_PLATFORMS,
    DOMAIN,
)
from tests.conftest import attach_runtime

MAC = "00:03:50:40:04:04"


@pytest.fixture
def mock_gateway():
    gw = MagicMock()
    gw.log_id = "[issue 404]"
    gw.mac = MAC
    return gw

ISSUE_404_BUS_TRACE = [
    "*#4*2*60*46##",
    "*4*303*1##",
    "*4*303*1##",
    "*#4*1*12*0280*3##",
    "*#4*1*0*0259##",
    "*#4*1*#14*0230*2##",
    "*4*0*1##",
    "*#4*1*12*0230*3##",
    "*#4*1*0*0259##",
    "*#4*1#2*#20*8##",
    "*#4*1#2*20*8##",
    "*4*4001#1*0#3##",
    "*#4*0#3*20*1##",
    "*#4*1*#14*0295*2##",
    "*#4*1*0*0259##",
    "*#4*4*60*46##",
    "*#4*1*#14*0270*2##",
    "*#4*1*12*0270*3##",
    "*4*0*1##",
    "*#4*1*0*0259##",
    "*4*4002#1*0#3##",
    "*#4*0#3*20*0##",
    "*4*4002*1##",
    "*#4*1#2*#20*5##",
    "*#4*1#2*20*5##",
    "*#4*1*#14*0200*2##",
    "*#4*1*12*0200*3##",
    "*4*0*1##",
    "*#4*1*0*0259##",
    "*#4*1#2*#20*8##",
    "*#4*1#2*20*8##",
    "*4*4001#1*0#3##",
    "*#4*0#3*20*1##",
    "*4*303*1##",
    "*4*303*1##",
    "*#4*1*12*0200*3##",
    "*#4*1*0*0259##",
    "*4*4002#1*0#3##",
    "*#4*0#3*20*0##",
    "*4*4002*1##",
    "*#4*1#2*#20*5##",
    "*#4*1#2*20*5##",
]


async def test_fancoil_fan_sets_hvac_action_cooling_and_idle(hass: HomeAssistant, mock_gateway):
    """Test that fan activation updates hvac_action to COOLING, and fan off updates to IDLE (#404)."""
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
        model="Fancoil Zone",
        gateway=mock_gateway,
    )
    climate.entity_id = "climate.zone_1"
    climate.async_schedule_update_ha_state = MagicMock()
    climate._attr_hvac_action = HVACAction.OFF

    # 1. Zone mode set to COOL via *4*0*1##
    climate.handle_event(OWNEvent.parse("*4*0*1##"))
    assert climate.hvac_mode == HVACMode.COOL
    assert climate.hvac_action == HVACAction.IDLE

    # 2. Fan actuator starts at high speed (*20*8, speed 3)
    climate.handle_event(OWNEvent.parse("*#4*1#2*20*8##"))
    assert climate.hvac_action == HVACAction.COOLING
    assert climate.fan_mode == "auto"
    assert climate.extra_state_attributes["running_fan_speed"] == "high"

    # 3. Fan actuator stops (*20*5, fan off)
    climate.handle_event(OWNEvent.parse("*#4*1#2*20*5##"))
    assert climate.hvac_action == HVACAction.IDLE
    assert climate.fan_mode == "auto"
    assert climate.extra_state_attributes["running_fan_speed"] == "off"


async def test_fancoil_fan_sets_hvac_action_heating_and_idle(hass: HomeAssistant, mock_gateway):
    """Test that fan activation updates hvac_action to HEATING in heat mode (#404)."""
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
        model="Fancoil Zone",
        gateway=mock_gateway,
    )
    climate.entity_id = "climate.zone_1"
    climate.async_schedule_update_ha_state = MagicMock()

    # 1. Zone mode set to HEAT
    climate._attr_hvac_mode = HVACMode.HEAT
    climate._attr_hvac_action = HVACAction.IDLE

    # 2. Fan actuator starts at speed 2 (*20*7, medium)
    climate.handle_event(OWNEvent.parse("*#4*1#2*20*7##"))
    assert climate.hvac_action == HVACAction.HEATING
    assert climate.extra_state_attributes["running_fan_speed"] == "medium"

    # 3. Fan actuator stops (*20*5)
    climate.handle_event(OWNEvent.parse("*#4*1#2*20*5##"))
    assert climate.hvac_action == HVACAction.IDLE
    assert climate.extra_state_attributes["running_fan_speed"] == "off"


async def test_fancoil_fan_auto_mode_preservation_under_dimension_20(hass: HomeAssistant, mock_gateway):
    """Test that setting fan_mode='auto' is not corrupted by actuator Dimension 20 running telemetry (#303)."""
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
        model="Fancoil Zone",
        gateway=mock_gateway,
    )
    climate.entity_id = "climate.zone_1"
    climate.async_schedule_update_ha_state = MagicMock()

    # Explicitly set fan mode to auto via Dimension 11 (or async_set_fan_mode)
    climate.handle_event(OWNEvent.parse("*#4*1*11*0##"))
    assert climate.fan_mode == "auto"

    # Actuator runs at high speed (*20*8)
    climate.handle_event(OWNEvent.parse("*#4*1#2*20*8##"))
    assert climate.fan_mode == "auto"
    assert climate.extra_state_attributes["running_fan_speed"] == "high"

    # Actuator drops to low speed (*20*6)
    climate.handle_event(OWNEvent.parse("*#4*1#2*20*6##"))
    assert climate.fan_mode == "auto"
    assert climate.extra_state_attributes["running_fan_speed"] == "low"

    # Actuator stops (*20*5)
    climate.handle_event(OWNEvent.parse("*#4*1#2*20*5##"))
    assert climate.fan_mode == "auto"
    assert climate.extra_state_attributes["running_fan_speed"] == "off"


async def test_fancoil_fan_in_auto_hvac_mode_determines_action_from_temperature(hass: HomeAssistant, mock_gateway):
    """Test that when hvac_mode is AUTO, active fan derives action from temperature delta."""
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
        model="Fancoil Zone",
        gateway=mock_gateway,
    )
    climate.entity_id = "climate.zone_1"
    climate.async_schedule_update_ha_state = MagicMock()

    climate._attr_hvac_mode = HVACMode.AUTO
    climate._target_temperature = 22.0

    # 1. Current temp 25.0°C > target 22.0°C -> Cooling
    climate._attr_current_temperature = 25.0
    climate.handle_event(OWNEvent.parse("*#4*1#2*20*8##"))
    assert climate.hvac_action == HVACAction.COOLING

    # 2. Current temp 19.0°C < target 22.0°C -> Heating
    climate._attr_current_temperature = 19.0
    climate.handle_event(OWNEvent.parse("*#4*1#2*20*8##"))
    assert climate.hvac_action == HVACAction.HEATING


async def test_issue_404_trace_replay(hass: HomeAssistant, mock_gateway):
    """Replay the full 42-frame bus trace from Issue #404 and verify exact state transitions."""
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry_404"
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
    attach_runtime(hass, config_entry)
    await async_setup_entry(hass, config_entry, added_entities.extend)

    # Deliver all frames from the Issue #404 diagnostic bundle
    for raw in ISSUE_404_BUS_TRACE:
        event = OWNEvent.parse(raw)
        if event is not None:
            async_dispatcher_send(hass, f"myhome_message_{MAC}", event)

    await hass.async_block_till_done()

    by_where = {e._where: e for e in added_entities}
    assert "1" in by_where, f"Zone 1 was not discovered; discovered: {list(by_where.keys())}"
    z1 = by_where["1"]

    # Final state after trace:
    # Zone 1 received *4*303*1## (off) and ended with *#4*1#2*20*5## (fan off)
    assert z1.hvac_mode == HVACMode.OFF
    assert z1.hvac_action == HVACAction.OFF
    assert z1.fan_mode == "auto"
    assert z1.extra_state_attributes["running_fan_speed"] == "off"
    assert z1.current_temperature == 25.9
    assert z1.target_temperature == 20.0


async def test_golden_sample_lyubomirtraykov_zone6_trace(hass: HomeAssistant, mock_gateway):
    """Test golden sample real-world trace from @lyubomirtraykov (Zone 6 fancoil).

    Verifies:
    1. Thermostat Dimension 11 speed changes update fan_mode ('low' -> 'medium' -> 'high' -> 'auto').
    2. Actuator Dimension 20 frames update running_fan_speed without clobbering fan_mode.
    3. Multi-actuator (valves/motors #2 and #3) state tracking maintains accurate hvac_action.
    """
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
        model="F461 Fancoil",
        gateway=mock_gateway,
    )
    climate.entity_id = "climate.zone_6"
    climate.async_schedule_update_ha_state = MagicMock()

    # Initial mode: Cooling, Idle
    climate._attr_hvac_mode = HVACMode.COOL
    climate._attr_hvac_action = HVACAction.IDLE

    # Frame 1: Thermostat sets fan speed to Low (*#4*6*11*1##)
    climate.handle_event(OWNEvent.parse("*#4*6*11*1##"))
    assert climate.fan_mode == "low"
    assert climate.hvac_action == HVACAction.IDLE

    # Frames 2-5: Actuators 2 and 3 spin up at low speed (speed 1, *20*6)
    climate.handle_event(OWNEvent.parse("*#4*6#2*#20*6##"))
    climate.handle_event(OWNEvent.parse("*#4*6#2*20*6##"))
    assert climate.hvac_action == HVACAction.COOLING
    assert climate.fan_mode == "low"
    assert climate.extra_state_attributes["running_fan_speed"] == "low"

    climate.handle_event(OWNEvent.parse("*#4*6#3*#20*6##"))
    climate.handle_event(OWNEvent.parse("*#4*6#3*20*6##"))
    assert climate.hvac_action == HVACAction.COOLING
    assert climate.fan_mode == "low"
    assert climate.extra_state_attributes["running_fan_speed"] == "low"

    # Frame 6: Thermostat increases speed to Medium (*#4*6*11*2##)
    climate.handle_event(OWNEvent.parse("*#4*6*11*2##"))
    assert climate.fan_mode == "medium"
    assert climate.hvac_action == HVACAction.COOLING

    # Frames 7-10: Actuators 2 and 3 report speed 2 (*20*7)
    climate.handle_event(OWNEvent.parse("*#4*6#2*#20*7##"))
    climate.handle_event(OWNEvent.parse("*#4*6#2*20*7##"))
    assert climate.extra_state_attributes["running_fan_speed"] == "medium"
    assert climate.fan_mode == "medium"

    climate.handle_event(OWNEvent.parse("*#4*6#3*#20*7##"))
    climate.handle_event(OWNEvent.parse("*#4*6#3*20*7##"))
    assert climate.extra_state_attributes["running_fan_speed"] == "medium"
    assert climate.fan_mode == "medium"

    # Frame 11: Thermostat sets speed to High (*#4*6*11*3##)
    climate.handle_event(OWNEvent.parse("*#4*6*11*3##"))
    assert climate.fan_mode == "high"

    # Frame 12: Thermostat switches back to Auto (*#4*6*11*0##)
    climate.handle_event(OWNEvent.parse("*#4*6*11*0##"))
    assert climate.fan_mode == "auto"
    # Actuator speed is still medium from previous frames and not overwritten by auto
    assert climate.extra_state_attributes["running_fan_speed"] == "medium"


async def test_fan_mode_restoration_on_ha_restart(hass: HomeAssistant, mock_gateway):
    """Test that fan_mode and FAN_MODE feature are restored from HA storage on restart."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Zone 6",
        device_id="4-6",
        who="4",
        where="6",
        heating=True,
        cooling=True,
        fan=False,  # Initially unconfigured
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Fancoil Zone",
        gateway=mock_gateway,
    )
    climate.entity_id = "climate.zone_6"
    assert not climate._fan
    assert not (climate.supported_features & ClimateEntityFeature.FAN_MODE)

    # Simulate HA restoring previous state where fan was active in 'medium' mode
    last_state = State(
        entity_id="climate.zone_6",
        state="cool",
        attributes={
            "temperature": 23.5,
            "fan_mode": "medium",
            "supported_features": int(
                ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.TARGET_TEMPERATURE
            ),
        },
    )
    await climate.async_restore_last_state(last_state)

    assert climate.hvac_mode == HVACMode.COOL
    assert climate.target_temperature == 23.5
    assert climate._fan is True
    assert climate.fan_mode == "medium"
    assert climate.supported_features & ClimateEntityFeature.FAN_MODE


async def test_fan_mode_restoration_edge_cases(hass: HomeAssistant, mock_gateway):
    """Test edge cases in async_restore_last_state for fan mode."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Zone 6",
        device_id="4-6",
        who="4",
        where="6",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Fancoil Zone",
        gateway=mock_gateway,
    )
    # 1. None state
    await climate.async_restore_last_state(None)
    assert not climate._fan

    # 2. State without fan_mode but with supported_features bit
    last_state_feat_only = State(
        entity_id="climate.zone_6",
        state="heat",
        attributes={
            "temperature": "invalid_temp",
            "supported_features": int(ClimateEntityFeature.FAN_MODE),
        },
    )
    await climate.async_restore_last_state(last_state_feat_only)
    assert climate._fan is True
    assert climate.fan_mode == "auto"  # Default from _enable_fan_mode()

    # 3. State with invalid fan_mode string
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
        model="Fancoil Zone",
        gateway=mock_gateway,
    )
    last_state_invalid_fan = State(
        entity_id="climate.zone_2",
        state="heat",
        attributes={
            "fan_mode": "turbo_ultra",
        },
    )
    await climate2.async_restore_last_state(last_state_invalid_fan)
    assert climate2._fan is True
    assert climate2.fan_mode == "auto"


async def test_async_update_queries_dimension_11_when_fan_enabled(hass: HomeAssistant, mock_gateway):
    """Test that async_update queries Dimension 11 when fan is supported, and omits when not."""
    mock_gateway.send_status_request = AsyncMock()

    # Fan enabled
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
        model="Fancoil Zone",
        gateway=mock_gateway,
    )
    await climate.async_update()
    assert mock_gateway.send_status_request.call_count == 2
    calls = [str(call[0][0]) for call in mock_gateway.send_status_request.call_args_list]
    assert "*#4*6##" in calls
    assert "*#4*6*11##" in calls

    # Fan disabled
    mock_gateway.send_status_request.reset_mock()
    climate_no_fan = MyHOMEClimate(
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
        model="Radiator Zone",
        gateway=mock_gateway,
    )
    await climate_no_fan.async_update()
    assert mock_gateway.send_status_request.call_count == 1
    assert str(mock_gateway.send_status_request.call_args[0][0]) == "*#4*1##"

