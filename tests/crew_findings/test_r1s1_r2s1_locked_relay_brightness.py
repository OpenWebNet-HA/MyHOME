# ruff: noqa
from unittest.mock import AsyncMock
from homeassistant.components.light import ColorMode
from tests.test_issue_288_feature_lock import _light_platform_from_yaml, _hass_with_lights, _setup_lights,GATEWAY_MAC
from tests.test_dimmer_on_at_level_0_2026_11_20 import _feed, PLAIN_ON, LEVEL_0
from custom_components.myhome import light as mod

async def test_locked_relay_does_not_gain_brightness_from_check(hass, monkeypatch):
    # Replay of dimmer telemetry to a fixture the user explicitly configured as on/off only.
    lights = _light_platform_from_yaml(f'''"{GATEWAY_MAC}":
      light:
        relay:
          where: "0010"
          name: "Relay"
          lock_features: true
    ''')
    gw = _hass_with_lights(hass, lights)
    gw.send_status_request=AsyncMock(return_value=None)
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S',0)
    (relay,) = await _setup_lights(hass)
    _feed(relay, PLAIN_ON, LEVEL_0)
    await hass.async_block_till_done()
    _feed(relay, PLAIN_ON)
    assert relay.supported_color_modes == {ColorMode.ONOFF}
    assert relay.brightness is None, 'unanswered level check fabricated a brightness on the locked relay'
