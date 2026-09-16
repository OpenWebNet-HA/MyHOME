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
    CONF_COLOR_TEMP,
    CONF_DIMMABLE,
    CONF_LOCK_FEATURES,
    CONF_RGB,
    DOMAIN,
)
from custom_components.myhome.light import MyHOMELight, async_setup_entry
from custom_components.myhome.validate import config_schema
from tests.conftest import attach_runtime

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "plants" / "issue_288_f461_dali_dt8"
GATEWAY_MAC = "00:03:50:00:02:88"


def _light_platform_from_yaml(yaml_text: str) -> dict:
    """Run a myhome.yaml snippet through the real validator, as __init__ does."""
    from homeassistant.util.yaml.loader import parse_yaml

    validated = config_schema(parse_yaml(yaml_text))
    return validated[GATEWAY_MAC]["platforms"]["light"]


def _hass_with_lights(hass: HomeAssistant, lights: dict) -> MagicMock:
    mock_gateway = MagicMock()
    mock_gateway.mac = GATEWAY_MAC
    mock_gateway.send = AsyncMock()
    mock_gateway.log_id = "GATEWAY"
    hass.data = {DOMAIN: {GATEWAY_MAC: {"entity": mock_gateway, "platforms": {"light": lights}}}}
    return mock_gateway


async def _setup_lights(hass: HomeAssistant) -> list[MyHOMELight]:
    config_entry = MagicMock()
    config_entry.data = {"mac": GATEWAY_MAC}
    config_entry.entry_id = "test_entry"
    with patch(
        "custom_components.myhome.discovery.er.async_entries_for_config_entry",
        return_value=[],
    ), patch(
        "custom_components.myhome.discovery.er.async_get",
        return_value=MagicMock(),
    ):
        async_add_entities = MagicMock()
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, async_add_entities)
    async_add_entities.assert_called_once()
    entities = list(async_add_entities.call_args[0][0])
    for entity in entities:
        entity.hass = hass
        entity.async_schedule_update_ha_state = MagicMock()
    return entities


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


def test_locked_tunable_white_rejects_hsv_but_tracks_ct(hass: HomeAssistant) -> None:
    """A light locked to tunable white ignores Dim 12 but still follows Dim 14."""
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
        model="DALI",
        gateway=mock_gateway,
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    # Dim 12 is rejected
    light.handle_event(OWNEvent.parse("*#1*25#4#02*12*350*80*80##"))
    assert ColorMode.HS not in light.supported_color_modes
    assert light.hs_color is None

    # Dim 14 is the declared capability and is tracked
    light.handle_event(OWNEvent.parse("*#1*25#4#02*14*153##"))
    assert light.supported_color_modes == {ColorMode.COLOR_TEMP}
    assert light.color_mode == ColorMode.COLOR_TEMP
    assert light.color_temp == 153


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
    """Replay the physical F461 trace against the plant's own myhome.yaml.

    Light 25 is the RGBW ballast the reporter drives from the app (HSV writes
    and tunable-white writes); 26-29 only ever answer the HSV sentinel.  All
    five are locked, so the entities must end up exactly as declared.
    """
    from homeassistant.util.yaml.loader import load_yaml

    with open(FIXTURE_DIR / "diagnostic_summary.json", "r", encoding="utf-8") as f:
        diag = json.load(f)
    frames = diag["data"]["bus_monitor"]["recent_frames"]
    assert len(frames) == 46

    validated = config_schema(load_yaml(str(FIXTURE_DIR / "myhome.yaml")))
    _hass_with_lights(hass, validated[GATEWAY_MAC]["platforms"]["light"])
    lights = {light._where: light for light in await _setup_lights(hass)}

    for item in frames:
        raw = item["raw"]
        parsed = OWNEvent.parse(raw)
        if parsed is None:
            parsed = OWNCommand.parse(raw)
        if parsed is None:
            continue
        # OWNd splits "25#4#02" into where="25" and interface="02"
        if getattr(parsed, "interface", None) != "02":
            continue
        addr = getattr(parsed, "where", None)
        if addr in lights:
            lights[addr].handle_event(parsed)

    light_25 = lights["25"]
    assert light_25.supported_color_modes == {ColorMode.HS, ColorMode.COLOR_TEMP}
    # The last Dim 12 status on the wire is *#1*25#4#02*12*53*10*74##
    assert light_25.hs_color == (53.0, 10.0)
    assert light_25.color_mode == ColorMode.HS
    # The last Dim 14 status is *#1*25#4#02*14*153## -> 153 mireds
    assert light_25.color_temp == 153
    # The last dimmer level is *#1*25#4#02*1*174*5## -> 74%
    assert light_25._attr_brightness_pct == 74

    # 26-29 received only the sentinel and stayed relays
    for addr in ("26", "27", "28", "29"):
        assert lights[addr].supported_color_modes == {ColorMode.ONOFF}
        assert lights[addr].color_mode == ColorMode.ONOFF
        assert lights[addr].hs_color is None


def _locked_light(hass: HomeAssistant, **capabilities) -> MyHOMELight:
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
        lock_features=True,
        manufacturer="BTicino",
        model="DALI",
        gateway=mock_gateway,
        **capabilities,
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()
    return light


def test_locked_out_dimension_is_discarded_whole_and_logged(hass: HomeAssistant, caplog) -> None:
    """A remembered Dim 12 value on a locked dimmer is not this light's brightness either."""
    light = _locked_light(hass, dimmable=True)
    light.handle_event(OWNEvent.parse("*#1*26#4#02*1*174*5##"))
    assert light._attr_brightness_pct == 74

    with caplog.at_level("DEBUG", logger="custom_components.myhome"):
        light.handle_event(OWNEvent.parse("*#1*26#4#02*12*353*74*80##"))

    # neither the colour nor the HSV "value" (80) reached the entity
    assert light.supported_color_modes == {ColorMode.BRIGHTNESS}
    assert light.hs_color is None
    assert light._attr_brightness_pct == 74
    assert "locked to ['brightness']" in caplog.text
    assert "Dimension 12" in caplog.text


def test_locked_relay_ignores_level_but_keeps_on_off(hass: HomeAssistant, caplog) -> None:
    """A locked relay takes is_on from a Dimension 1 frame but never its level."""
    light = _locked_light(hass, dimmable=False)
    with caplog.at_level("DEBUG", logger="custom_components.myhome"):
        light.handle_event(OWNEvent.parse("*#1*26#4#02*1*174*5##"))
    assert light.is_on is True
    assert light.supported_color_modes == {ColorMode.ONOFF}
    assert light.brightness is None
    assert "Dimension 1 frame" in caplog.text


def test_locked_tunable_white_without_dimmable_still_tracks_level(hass: HomeAssistant) -> None:
    """color_temp implies brightness; the level on Dimension 1 is never locked out."""
    light = _locked_light(hass, dimmable=False, color_temp=True)
    assert ColorMode.BRIGHTNESS in light._allowed_color_modes
    light.handle_event(OWNEvent.parse("*#1*26#4#02*14*153##"))
    light.handle_event(OWNEvent.parse("*#1*26#4#02*1*174*5##"))
    assert light.supported_color_modes == {ColorMode.COLOR_TEMP}
    assert light.color_temp == 153
    assert light._attr_brightness_pct == 74


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


def test_light_schema_accepts_dali_capability_keys() -> None:
    """myhome.yaml may declare color_temp / rgb / hs / lock_features on a light."""
    lights = _light_platform_from_yaml(
        f"""
        "{GATEWAY_MAC}":
          light:
            l25:
              where: "25"
              interface: "02"
              name: "Light 25"
              dimmable: true
              color_temp: true
              rgb: true
              lock_features: true
            l26:
              where: "26"
              name: "Light 26"
        """
    )
    l25 = lights["1-25#4#02"]
    assert l25[CONF_DIMMABLE] is True
    assert l25[CONF_COLOR_TEMP] is True
    assert l25[CONF_RGB] is True
    assert l25[CONF_LOCK_FEATURES] is True
    # A light that does not mention the DALI keys keeps the same shape as before
    l26 = lights["1-26"]
    assert l26[CONF_DIMMABLE] is False
    assert CONF_COLOR_TEMP not in l26
    assert CONF_RGB not in l26
    assert CONF_LOCK_FEATURES not in l26


def test_issue_288_fixture_yaml_passes_the_validator() -> None:
    """The golden plant's myhome.yaml is a configuration a user can actually load."""
    from homeassistant.util.yaml.loader import load_yaml

    validated = config_schema(load_yaml(str(FIXTURE_DIR / "myhome.yaml")))
    lights = validated[GATEWAY_MAC]["platforms"]["light"]
    assert lights["1-25#4#02"][CONF_RGB] is True
    assert lights["1-25#4#02"][CONF_COLOR_TEMP] is True
    assert all(lights[f"1-{w}#4#02"][CONF_LOCK_FEATURES] is True for w in ("25", "26", "27", "28", "29"))


async def test_plain_yaml_light_still_learns_dimming_from_the_bus(hass: HomeAssistant) -> None:
    """Regression: the schema stamps dimmable=False on every light; that is not an opt-out."""
    lights = _light_platform_from_yaml(
        f"""
        "{GATEWAY_MAC}":
          light:
            l30:
              where: "30"
              name: "Light 30"
        """
    )
    assert lights["1-30"][CONF_DIMMABLE] is False
    _hass_with_lights(hass, lights)
    (light,) = await _setup_lights(hass)
    assert light._lock_features is False
    assert light.supported_color_modes == {ColorMode.ONOFF}

    light.handle_event(OWNEvent.parse("*#1*30*1*150*5##"))
    assert light.supported_color_modes == {ColorMode.BRIGHTNESS}
    light.handle_event(OWNEvent.parse("*#1*30*14*153##"))
    assert ColorMode.COLOR_TEMP in light.supported_color_modes


async def test_setup_yaml_locked_relay_never_promotes(hass: HomeAssistant) -> None:
    """A locked light with no capability flags is a relay, whatever the bus says."""
    lights = _light_platform_from_yaml(
        f"""
        "{GATEWAY_MAC}":
          light:
            l30:
              where: "30"
              name: "Light 30"
              lock_features: true
        """
    )
    _hass_with_lights(hass, lights)
    (light,) = await _setup_lights(hass)
    assert light._lock_features is True
    assert light._allowed_color_modes == {ColorMode.ONOFF}

    light.handle_event(OWNEvent.parse("*#1*30*1*150*5##"))
    light.handle_event(OWNEvent.parse("*#1*30*12*350*80*80##"))
    light.handle_event(OWNEvent.parse("*#1*30*14*153##"))
    assert light.supported_color_modes == {ColorMode.ONOFF}
    assert light.color_mode == ColorMode.ONOFF


async def test_setup_yaml_locked_rgbw_keeps_both_colour_modes(hass: HomeAssistant) -> None:
    """The fixture's light 25 (RGBW, locked) exposes HS and CT and nothing else."""
    from homeassistant.util.yaml.loader import load_yaml

    validated = config_schema(load_yaml(str(FIXTURE_DIR / "myhome.yaml")))
    _hass_with_lights(hass, validated[GATEWAY_MAC]["platforms"]["light"])
    lights = {light._where: light for light in await _setup_lights(hass)}
    assert set(lights) == {"25", "26", "27", "28", "29"}
    assert lights["25"].supported_color_modes == {ColorMode.HS, ColorMode.COLOR_TEMP}
    assert lights["25"]._allowed_color_modes == {ColorMode.HS, ColorMode.COLOR_TEMP, ColorMode.BRIGHTNESS}
    for where in ("26", "27", "28", "29"):
        assert lights[where]._lock_features is True
        assert lights[where].supported_color_modes == {ColorMode.ONOFF}


async def test_setup_restored_light_feature_lock(hass: HomeAssistant) -> None:
    """A light restored from the entity registry picks up lock_features from myhome.yaml."""
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
                            CONF_LOCK_FEATURES: True,
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
        "custom_components.myhome.discovery.er.async_entries_for_config_entry",
        return_value=[mock_entry],
    ), patch(
        "custom_components.myhome.discovery.er.async_get",
        return_value=MagicMock(),
    ):
        async_add_entities = MagicMock()
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, async_add_entities)
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        light_entity = entities[0]
        assert light_entity._lock_features is True
        assert light_entity._allowed_color_modes == {ColorMode.ONOFF}
        assert light_entity._is_mode_forbidden(ColorMode.HS)
        assert light_entity._is_mode_forbidden(ColorMode.COLOR_TEMP)
        assert light_entity._is_mode_forbidden(ColorMode.BRIGHTNESS)


async def test_async_add_light_feature_lock_and_translation(hass: HomeAssistant) -> None:
    """A locked yaml relay stays a relay when a dimmer level arrives; translations are ignored."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "00:03:50:00:02:88"
    mock_gateway.send = AsyncMock()
    mock_gateway.log_id = "GATEWAY"

    # A lock can only come from myhome.yaml (the customize.yaml recovery is gone):
    # the light exists from setup, and its frames must neither unlock it nor
    # create a second entity.
    hass.data = {
        DOMAIN: {
            "00:03:50:00:02:88": {
                "entity": mock_gateway,
                "platforms": {
                    "light": _light_platform_from_yaml(
                        f"""
{GATEWAY_MAC}:
  light:
    relay_33:
      where: '33'
      name: Light 33
      lock_features: true
"""
                    )
                },
            },
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": "00:03:50:00:02:88"}
    config_entry.entry_id = "test_entry"

    with patch(
        "custom_components.myhome.discovery.er.async_entries_for_config_entry",
        return_value=[],
    ), patch(
        "custom_components.myhome.discovery.er.async_get",
        return_value=MagicMock(),
    ):
        async_add_entities = MagicMock()
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, async_add_entities)

    from homeassistant.helpers.dispatcher import async_dispatcher_send

    # 1. Translation message ignored (covers line 352)
    msg_trans = MagicMock(spec=OWNLightingEvent)
    msg_trans.is_translation = True
    async_dispatcher_send(hass, "myhome_message_00:03:50:00:02:88", msg_trans)

    # 2. A dimmer level for the locked relay: an unlocked light would take it as
    #    proof of dimming; a locked light without `dimmable: true` stays a relay
    #    (it would be rebuilt as a relay after a restart anyway).
    msg_light = OWNEvent.parse("*#1*33*1*150*5##")
    async_dispatcher_send(hass, "myhome_message_00:03:50:00:02:88", msg_light)

    assert async_add_entities.call_count == 1  # created at setup, not discovered twice
    added_entities = async_add_entities.call_args[0][0]
    discovered = added_entities[0]
    assert discovered._lock_features is True
    assert discovered._allowed_color_modes == {ColorMode.ONOFF}
    assert discovered.supported_color_modes == {ColorMode.ONOFF}
