"""Tests for Issue #288 / #307 Part 2: Authoritative Feature Lock and DALI DT8."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.light import ColorMode
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from OWNd.message import OWNCommand, OWNEvent, OWNLightingEvent

from custom_components.myhome.const import (
    CONF_AUTO_PROMOTE,
    CONF_COLOR_TEMP,
    CONF_DIMMABLE,
    CONF_RGB,
    DOMAIN,
)
from custom_components.myhome.light import MyHOMELight, async_setup_entry


def test_feature_lock_tunable_white_ignores_dim12_hsv(hass: HomeAssistant) -> None:
    """Test that a Tunable White light with lock_features ignores Dim 12 HSV frames."""
    mock_gateway = MagicMock()
    mock_gateway.send = AsyncMock()
    mock_gateway.log_id = "GATEWAY"

    light = MyHOMELight(
        hass=hass,
        name="Light 25",
        entity_name="Light 25",
        icon="mdi:lightbulb",
        icon_on="mdi:lightbulb-on",
        device_id="25#4#02",
        who="1",
        where="25",
        interface="02",
        dimmable=True,
        color_temp=True,
        rgb=False,
        lock_features=True,
        manufacturer="BTicino",
        model="F461 DALI Ballast",
        gateway=mock_gateway,
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    assert light.supported_color_modes == {ColorMode.COLOR_TEMP}
    assert light.color_mode == ColorMode.COLOR_TEMP

    # Physical gateway emits cached Dim 12 HSV frame
    event_hsv = OWNEvent.parse("*#1*25#4#02*12*350*80*80##")
    light.handle_event(event_hsv)

    # Must NOT promote to HS mode
    assert ColorMode.HS not in light.supported_color_modes
    assert light.color_mode == ColorMode.COLOR_TEMP
    assert light.hs_color is None
    # Stale Dim 12 Value (80) must NOT override brightness
    assert light.brightness is None

    # Physical gateway emits Dim 14 Tunable White frame (153 mireds)
    event_ct = OWNEvent.parse("*#1*25#4#02*14*153##")
    light.handle_event(event_ct)

    assert light.color_mode == ColorMode.COLOR_TEMP
    assert light.color_temp == 153
    assert light.color_temp_kelvin == 6535


def test_feature_lock_dimmer_ignores_dim12_and_dim14(hass: HomeAssistant) -> None:
    """Test that a standard dimmer with lock_features ignores Dim 12 and Dim 14."""
    mock_gateway = MagicMock()
    mock_gateway.send = AsyncMock()
    mock_gateway.log_id = "GATEWAY"

    light = MyHOMELight(
        hass=hass,
        name="Light 27",
        entity_name="Light 27",
        icon="mdi:lightbulb",
        icon_on="mdi:lightbulb-on",
        device_id="27#4#02",
        who="1",
        where="27",
        interface="02",
        dimmable=True,
        color_temp=False,
        rgb=False,
        lock_features=True,
        manufacturer="BTicino",
        model="Standard Dimmer",
        gateway=mock_gateway,
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    assert light.supported_color_modes == {ColorMode.BRIGHTNESS}
    assert light.color_mode == ColorMode.BRIGHTNESS

    # Emit Dim 12 and Dim 14
    light.handle_event(OWNEvent.parse("*#1*27#4#02*12*350*80*80##"))
    light.handle_event(OWNEvent.parse("*#1*27#4#02*14*153##"))

    assert light.supported_color_modes == {ColorMode.BRIGHTNESS}
    assert light.color_mode == ColorMode.BRIGHTNESS

    # Valid dimmer event
    light.handle_event(OWNEvent.parse("*#1*27#4#02*1*196*5##"))
    assert light._attr_brightness_pct == 96


def test_feature_lock_relay_ignores_all_promotions(hass: HomeAssistant) -> None:
    """Test that a relay light with lock_features stays strictly ONOFF."""
    mock_gateway = MagicMock()
    mock_gateway.send = AsyncMock()
    mock_gateway.log_id = "GATEWAY"

    light = MyHOMELight(
        hass=hass,
        name="Light 26",
        entity_name="Light 26",
        icon="mdi:lightbulb",
        icon_on="mdi:lightbulb-on",
        device_id="26#4#02",
        who="1",
        where="26",
        interface="02",
        dimmable=False,
        color_temp=False,
        rgb=False,
        lock_features=True,
        manufacturer="BTicino",
        model="Relay Actuator",
        gateway=mock_gateway,
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    assert light.supported_color_modes == {ColorMode.ONOFF}
    assert light.color_mode == ColorMode.ONOFF

    # Attempt promotions with Dimmer, Dim 12, Dim 14
    light.handle_event(OWNEvent.parse("*#1*26#4#02*1*196*5##"))
    light.handle_event(OWNEvent.parse("*#1*26#4#02*12*350*80*80##"))
    light.handle_event(OWNEvent.parse("*#1*26#4#02*14*153##"))

    assert light.supported_color_modes == {ColorMode.ONOFF}
    assert light.color_mode == ColorMode.ONOFF
    assert light.brightness is None


def test_disallowed_color_modes_explicit_rgb_false(hass: HomeAssistant) -> None:
    """Test that explicit rgb: false disables HS auto-promotion while allowing CT."""
    mock_gateway = MagicMock()
    mock_gateway.send = AsyncMock()
    mock_gateway.log_id = "GATEWAY"

    light = MyHOMELight(
        hass=hass,
        name="Light 25",
        entity_name="Light 25",
        icon="mdi:lightbulb",
        icon_on="mdi:lightbulb-on",
        device_id="25#4#02",
        who="1",
        where="25",
        interface="02",
        dimmable=False,
        color_temp=False,
        rgb=False,
        disallowed_color_modes={ColorMode.HS},
        manufacturer="BTicino",
        model="DALI",
        gateway=mock_gateway,
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    # Dim 12 is rejected
    light.handle_event(OWNEvent.parse("*#1*25#4#02*12*350*80*80##"))
    assert ColorMode.HS not in light.supported_color_modes

    # Dim 14 is accepted and auto-promotes
    light.handle_event(OWNEvent.parse("*#1*25#4#02*14*153##"))
    assert ColorMode.COLOR_TEMP in light.supported_color_modes
    assert light.color_mode == ColorMode.COLOR_TEMP


async def test_async_update_queries_both_hs_and_ct_for_rgbw(hass: HomeAssistant) -> None:
    """Test that async_update queries both HSV and Color Temp for dual RGBW lights."""
    mock_gateway = MagicMock()
    mock_gateway.send_status_request = AsyncMock()
    mock_gateway.log_id = "GATEWAY"

    light = MyHOMELight(
        hass=hass,
        name="RGBW Light",
        entity_name="RGBW Light",
        icon="mdi:lightbulb",
        icon_on="mdi:lightbulb-on",
        device_id="25#4#02",
        who="1",
        where="25",
        interface="02",
        dimmable=True,
        color_temp=True,
        rgb=True,
        manufacturer="BTicino",
        model="DALI RGBW",
        gateway=mock_gateway,
    )
    light.hass = hass

    await light.async_update()

    sent_frames = [str(call[0][0]) for call in mock_gateway.send_status_request.call_args_list]
    assert any("*#1*25#4#02*1##" in f or "*#1*25#4#02*1" in f for f in sent_frames)
    assert any("*#1*25#4#02*12##" in f for f in sent_frames)
    assert any("*#1*25#4#02*14##" in f for f in sent_frames)


async def test_async_update_skips_locked_out_dimensions(hass: HomeAssistant) -> None:
    """Test that async_update skips get_hsv_color when HS mode is locked out."""
    mock_gateway = MagicMock()
    mock_gateway.send_status_request = AsyncMock()
    mock_gateway.log_id = "GATEWAY"

    light = MyHOMELight(
        hass=hass,
        name="CT Only Light",
        entity_name="CT Only Light",
        icon="mdi:lightbulb",
        icon_on="mdi:lightbulb-on",
        device_id="25#4#02",
        who="1",
        where="25",
        interface="02",
        dimmable=True,
        color_temp=True,
        rgb=False,
        lock_features=True,
        manufacturer="BTicino",
        model="DALI CT",
        gateway=mock_gateway,
    )
    light.hass = hass

    await light.async_update()

    sent_frames = [str(call[0][0]) for call in mock_gateway.send_status_request.call_args_list]
    assert any("*#1*25#4#02*1##" in f or "*#1*25#4#02*1" in f for f in sent_frames)
    assert any("*#1*25#4#02*14##" in f for f in sent_frames)
    # Dimension 12 must NEVER be requested
    assert not any("*#1*25#4#02*12##" in f for f in sent_frames)


async def test_state_restoration_with_feature_lock(hass: HomeAssistant) -> None:
    """Test that state restoration respects lock_features and restores last_mode."""
    mock_gateway = MagicMock()
    mock_gateway.log_id = "GATEWAY"

    light = MyHOMELight(
        hass=hass,
        name="Light 25",
        entity_name="Light 25",
        icon="mdi:lightbulb",
        icon_on="mdi:lightbulb-on",
        device_id="25#4#02",
        who="1",
        where="25",
        interface="02",
        dimmable=True,
        color_temp=True,
        rgb=False,
        lock_features=True,
        manufacturer="BTicino",
        model="DALI",
        gateway=mock_gateway,
    )
    light.hass = hass

    # Mock previous state containing polluted HS mode
    last_state = MagicMock()
    last_state.attributes = {
        "supported_color_modes": ["hs", "color_temp", "brightness"],
        "color_mode": "color_temp",
        "brightness": 200,
        "color_temp": 250,
    }
    last_state.state = "on"

    await light.async_restore_last_state(last_state)

    assert light.supported_color_modes == {ColorMode.COLOR_TEMP}
    assert light.color_mode == ColorMode.COLOR_TEMP
    assert light.brightness == 200
    assert light.color_temp == 250


def test_translation_frame_ignored(hass: HomeAssistant) -> None:
    """Test that translation frames are ignored in handle_event and async_add_light."""
    mock_gateway = MagicMock()
    mock_gateway.log_id = "GATEWAY"

    light = MyHOMELight(
        hass=hass,
        name="Light 25",
        entity_name="Light 25",
        icon="mdi:lightbulb",
        icon_on="mdi:lightbulb-on",
        device_id="25#4#02",
        who="1",
        where="25",
        interface="02",
        dimmable=True,
        manufacturer="BTicino",
        model="DALI",
        gateway=mock_gateway,
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    # Translation message
    msg = MagicMock()
    msg.is_translation = True
    msg.is_on = None

    light.handle_event(msg)
    # Should return early without error
    assert light.is_on is None


async def test_issue_288_f461_dali_trace_replay(hass: HomeAssistant) -> None:
    """Replay authentic F461 DALI DT8 trace against locked and unlocked entities."""
    fixture_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "plants"
        / "issue_288_f461_dali_dt8"
        / "diagnostic_summary.json"
    )
    assert fixture_path.is_file()

    with open(fixture_path, "r", encoding="utf-8") as f:
        diag = json.load(f)

    frames = diag["data"]["bus_monitor"]["recent_frames"]
    assert len(frames) == 46

    mock_gateway = MagicMock()
    mock_gateway.log_id = "GATEWAY"
    mock_gateway.send = AsyncMock()

    # Light 25 is Tunable White with feature lock
    light_25 = MyHOMELight(
        hass=hass,
        name="Light 25",
        entity_name="Light 25",
        icon="mdi:lightbulb",
        icon_on="mdi:lightbulb-on",
        device_id="25#4#02",
        who="1",
        where="25",
        interface="02",
        dimmable=True,
        color_temp=True,
        rgb=False,
        lock_features=True,
        manufacturer="BTicino",
        model="F461 DALI Ballast",
        gateway=mock_gateway,
    )
    light_25.hass = hass
    light_25.async_schedule_update_ha_state = MagicMock()

    # Lights 26, 27, 28, 29 are standard dimmers / relays
    other_lights = {}
    for addr in ("26", "27", "28", "29"):
        other_light = MyHOMELight(
            hass=hass,
            name=f"Light {addr}",
            entity_name=f"Light {addr}",
            icon="mdi:lightbulb",
            icon_on="mdi:lightbulb-on",
            device_id=f"{addr}#4#02",
            who="1",
            where=addr,
            interface="02",
            dimmable=False,
            lock_features=True,
            manufacturer="BTicino",
            model="F461 DALI Ballast",
            gateway=mock_gateway,
        )
        other_light.hass = hass
        other_light.async_schedule_update_ha_state = MagicMock()
        other_lights[addr] = other_light

    # Replay all frames
    for item in frames:
        raw = item["raw"]
        parsed = OWNEvent.parse(raw)
        if parsed is None:
            parsed = OWNCommand.parse(raw)
        if parsed is None:
            continue

        target_where = getattr(parsed, "where", None)
        if target_where == "25#4#02" or target_where == "25":
            light_25.handle_event(parsed)
        elif target_where and "#4#02" in target_where:
            addr = target_where.split("#4#02")[0]
            if addr in other_lights:
                other_lights[addr].handle_event(parsed)

    # Verify final states
    # Light 25 should remain ColorMode.COLOR_TEMP and NEVER have promoted to HS
    assert light_25.supported_color_modes == {ColorMode.COLOR_TEMP}
    assert light_25.color_mode == ColorMode.COLOR_TEMP
    assert light_25.hs_color is None
    # CT was set by *#1*25#4#02*14*153## -> 153 mireds
    assert light_25.color_temp == 153
    # Brightness was set by *#1*25#4#02*1*174*5## -> 74%
    assert light_25._attr_brightness_pct == 74

    # Other lights (26-29) received sentinels and stayed ONOFF
    for addr, other_light in other_lights.items():
        assert other_light.supported_color_modes == {ColorMode.ONOFF}
        assert other_light.color_mode == ColorMode.ONOFF


def test_rgb_locked_light_allowed_modes(hass: HomeAssistant) -> None:
    """Test that a light with lock_features=True and rgb=True has ColorMode.HS in allowed modes."""
    mock_gateway = MagicMock()
    mock_gateway.log_id = "GATEWAY"

    light = MyHOMELight(
        hass=hass,
        name="Light 10",
        entity_name="Light 10",
        icon="mdi:lightbulb",
        icon_on="mdi:lightbulb-on",
        device_id="10",
        who="1",
        where="10",
        interface=None,
        dimmable=True,
        color_temp=False,
        rgb=True,
        lock_features=True,
        manufacturer="BTicino",
        model="RGB Light",
        gateway=mock_gateway,
    )
    assert light.supported_color_modes == {ColorMode.HS}
    assert ColorMode.HS in light._allowed_color_modes


def test_promote_color_mode_direct_call_forbidden(hass: HomeAssistant) -> None:
    """Test calling _promote_color_mode directly with a forbidden mode returns early."""
    mock_gateway = MagicMock()
    mock_gateway.log_id = "GATEWAY"

    light = MyHOMELight(
        hass=hass,
        name="Light 10",
        entity_name="Light 10",
        icon="mdi:lightbulb",
        icon_on="mdi:lightbulb-on",
        device_id="10",
        who="1",
        where="10",
        interface=None,
        dimmable=False,
        color_temp=True,
        rgb=False,
        lock_features=True,
        manufacturer="BTicino",
        model="CT Light",
        gateway=mock_gateway,
    )
    assert light.supported_color_modes == {ColorMode.COLOR_TEMP}
    light._promote_color_mode(ColorMode.HS)
    assert ColorMode.HS not in light.supported_color_modes


async def test_setup_yaml_light_feature_lock_and_disallowed(hass: HomeAssistant) -> None:
    """Test async_setup_entry with YAML light having auto_promote: false and explicit false booleans."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "00:03:50:00:02:88"
    hass.data = {
        DOMAIN: {
            "00:03:50:00:02:88": {
                "entity": mock_gateway,
                "platforms": {
                    "light": {
                        "30": {
                            "where": "30",
                            CONF_NAME: "Light 30",
                            CONF_AUTO_PROMOTE: False,
                            CONF_RGB: False,
                            CONF_COLOR_TEMP: False,
                            CONF_DIMMABLE: False,
                        }
                    }
                },
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": "00:03:50:00:02:88"}
    config_entry.entry_id = "test_entry"

    with patch(
        "custom_components.myhome.light.er.async_entries_for_config_entry",
        return_value=[],
    ), patch(
        "custom_components.myhome.light.er.async_get",
        return_value=MagicMock(),
    ):
        async_add_entities = MagicMock()
        await async_setup_entry(hass, config_entry, async_add_entities)
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        light_entity = entities[0]
        assert light_entity._lock_features is True
        assert ColorMode.HS in light_entity._disallowed_color_modes
        assert ColorMode.COLOR_TEMP in light_entity._disallowed_color_modes
        assert ColorMode.BRIGHTNESS in light_entity._disallowed_color_modes


async def test_setup_restored_light_feature_lock_and_disallowed(hass: HomeAssistant) -> None:
    """Test async_setup_entry with restored light having auto_promote: false and explicit false booleans."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "00:03:50:00:02:88"
    hass.data = {
        DOMAIN: {
            "00:03:50:00:02:88": {
                "entity": mock_gateway,
                "platforms": {
                    "light": {
                        "31": {
                            "where": "31",
                            CONF_NAME: "Light 31",
                            CONF_AUTO_PROMOTE: False,
                            CONF_RGB: False,
                            CONF_COLOR_TEMP: False,
                            CONF_DIMMABLE: False,
                        }
                    }
                },
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": "00:03:50:00:02:88"}
    config_entry.entry_id = "test_entry"

    mock_entry = MagicMock()
    mock_entry.domain = "light"
    mock_entry.unique_id = "00:03:50:00:02:88-1-31"

    with patch(
        "custom_components.myhome.light.er.async_entries_for_config_entry",
        return_value=[mock_entry],
    ), patch(
        "custom_components.myhome.light.er.async_get",
        return_value=MagicMock(),
    ):
        async_add_entities = MagicMock()
        await async_setup_entry(hass, config_entry, async_add_entities)
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        light_entity = entities[0]
        assert light_entity._lock_features is True
        assert ColorMode.HS in light_entity._disallowed_color_modes
        assert ColorMode.COLOR_TEMP in light_entity._disallowed_color_modes
        assert ColorMode.BRIGHTNESS in light_entity._disallowed_color_modes


async def test_async_add_light_feature_lock_and_translation(hass: HomeAssistant) -> None:
    """Test async_add_light dynamic discovery with translation and feature lock."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "00:03:50:00:02:88"
    mock_gateway.send = AsyncMock()
    mock_gateway.log_id = "GATEWAY"

    hass.data = {
        DOMAIN: {
            "00:03:50:00:02:88": {
                "entity": mock_gateway,
                "platforms": {},
            },
            "customizations": {
                "light.light_33": {
                    "auto_promote": False,
                    "rgb": False,
                    "color_temp": False,
                    "dimmable": False,
                }
            },
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": "00:03:50:00:02:88"}
    config_entry.entry_id = "test_entry"

    with patch(
        "custom_components.myhome.light.er.async_entries_for_config_entry",
        return_value=[],
    ), patch(
        "custom_components.myhome.light.er.async_get",
        return_value=MagicMock(),
    ):
        async_add_entities = MagicMock()
        await async_setup_entry(hass, config_entry, async_add_entities)

    from homeassistant.helpers.dispatcher import async_dispatcher_send

    # 1. Translation message ignored (covers line 352)
    msg_trans = MagicMock(spec=OWNLightingEvent)
    msg_trans.is_translation = True
    async_dispatcher_send(hass, "myhome_message_00:03:50:00:02:88", msg_trans)

    # 2. Dynamic light discovery with feature lock
    msg_light = OWNEvent.parse("*1*1*33##")
    async_dispatcher_send(hass, "myhome_message_00:03:50:00:02:88", msg_light)

    assert async_add_entities.call_count == 1
    added_entities = async_add_entities.call_args[0][0]
    discovered = added_entities[0]
    assert discovered._lock_features is True
    assert ColorMode.HS in discovered._disallowed_color_modes
    assert ColorMode.COLOR_TEMP in discovered._disallowed_color_modes
    assert ColorMode.BRIGHTNESS in discovered._disallowed_color_modes
