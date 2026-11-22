# ruff: noqa
from unittest.mock import MagicMock
from homeassistant.components.light import ColorMode
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _feed, _light, PLAIN_ON, LEVEL_0

async def test_locked_relay_never_acquires_brightness_through_verification(hass, monkeypatch):
    # Replay the zero on an address declared to be a relay (constructed configuration).
    # frames.json:20,40. tests/test_issue_288_feature_lock.py:475-482: never its level.
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S', 0)
    _, gw = _light(hass)
    relay = mod.MyHOMELight(hass=hass, name='Relay', entity_name='Relay', icon=None, icon_on=None,
                            device_id='0010', who='1', where='0010', interface=None,
                            dimmable=False, lock_features=True, manufacturer=None, model=None, gateway=gw)
    relay.hass = hass
    relay.async_schedule_update_ha_state = MagicMock()
    _feed(relay, PLAIN_ON, LEVEL_0)
    await hass.async_block_till_done()
    assert relay.is_on is False and relay.color_mode == ColorMode.ONOFF
    _feed(relay, PLAIN_ON)
    assert relay.is_on and relay.brightness is None, 'on/off-only relay has invented a level'
