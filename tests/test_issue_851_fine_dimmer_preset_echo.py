"""A coarse WHAT 2..10 echo must not overwrite the exact level of a fine dimmer (#851).

Live F461 2.0.11 + F418 at 0010, 2026-11-03: the plain status request answers
``*1*7*0010##`` while Dimension 1 says 53 % (``*#1*0010*1*153*4##``); the F418
reports WHAT 7 for anything between about 44 and 57 %. A fine dimmer keeps its
exact level on such an echo, shows the mapped value only as a provisional level
when it was off, and asks Dimension 1 once. Coarse-only dimmers (speed 240,
#767) keep the preset table (#828).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.light import ColorMode
from OWNd.message import OWNMessage

from custom_components.myhome.light import MyHOMELight


def _light(hass, where="0010"):
    gateway = MagicMock()
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    gateway.log_id = "[F461]"
    light = MyHOMELight(
        hass=hass, name="Kitchen", entity_name="Kitchen", icon=None, icon_on=None,
        device_id=where, who="1", where=where, interface=None, dimmable=True,
        manufacturer="BTicino", model="F461", gateway=gateway,
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()
    return light, gateway


async def test_preset_echo_asks_dimension_1_for_the_exact_level(hass):
    light, gateway = _light(hass)
    light.handle_event(OWNMessage.parse("*#1*0010*1*153*4##"))
    assert light._attr_brightness_pct == 53

    light.handle_event(OWNMessage.parse("*1*7*0010##"))
    await hass.async_block_till_done()

    assert light.is_on is True
    assert light._attr_brightness_pct == 50  # the mapped preset, shown at once (#828)
    assert light._bus_state_complete is False
    gateway.send_status_request.assert_awaited_once()
    assert str(gateway.send_status_request.await_args.args[0]) == "*#1*0010*1##"

    # the Dimension 1 reply the refresh asked for restores the exact level
    light.handle_event(OWNMessage.parse("*#1*0010*1*153*4##"))
    assert light._attr_brightness_pct == 53


async def test_preset_echo_on_an_off_fine_dimmer_is_provisional(hass):
    light, gateway = _light(hass)
    light.handle_event(OWNMessage.parse("*#1*0010*1*153*4##"))
    light.handle_event(OWNMessage.parse("*1*0*0010##"))
    assert light.is_on is False

    light.handle_event(OWNMessage.parse("*1*7*0010##"))
    await hass.async_block_till_done()

    assert light.is_on is True
    assert light._attr_brightness_pct == 50  # provisional, until Dimension 1 answers
    gateway.send_status_request.assert_awaited_once()

    light.handle_event(OWNMessage.parse("*#1*0010*1*147*4##"))
    assert light._attr_brightness_pct == 47
    assert light.color_mode == ColorMode.BRIGHTNESS


async def test_one_refresh_per_echo_burst(hass):
    light, gateway = _light(hass)
    light.handle_event(OWNMessage.parse("*#1*0010*1*153*4##"))
    light.handle_event(OWNMessage.parse("*1*7*0010##"))
    light.handle_event(OWNMessage.parse("*1*7*0010##"))
    await hass.async_block_till_done()
    assert gateway.send_status_request.await_count == 1

    light._brightness_refresh_after = 0.0  # the one-second window has passed
    light.handle_event(OWNMessage.parse("*1*7*0010##"))
    await hass.async_block_till_done()
    assert gateway.send_status_request.await_count == 2


@pytest.mark.parametrize(
    ("exact_frame", "echo", "expected"),
    [
        ("*#1*61*1*130*240##", "*1*6*61##", 40),  # coarse-only dimmer (#767): table wins
    ],
)
async def test_coarse_only_dimmer_keeps_the_preset_table(hass, exact_frame, echo, expected):
    light, gateway = _light(hass, where="61")
    light.handle_event(OWNMessage.parse(exact_frame))
    light.handle_event(OWNMessage.parse(echo))
    await hass.async_block_till_done()
    assert light._attr_brightness_pct == expected
    gateway.send_status_request.assert_not_awaited()


async def test_preset_without_a_prior_exact_level_is_unchanged(hass):
    """No exact level yet: the mapped preset applies and the startup sweep polls (#466, #612)."""
    light, gateway = _light(hass)
    light.handle_event(OWNMessage.parse("*1*7*0010##"))
    await hass.async_block_till_done()
    assert light._attr_brightness_pct == 50
    assert light._bus_state_complete is False
    gateway.send_status_request.assert_not_awaited()


async def test_dimension_4_and_off_reports_do_not_mark_a_fine_dimmer(hass):
    """Dimension 4's trailing value is not the Dimension 1 speed, and an off report says nothing."""
    light, gateway = _light(hass)
    light.handle_event(OWNMessage.parse("*#1*0010*4*153*2##"))
    light.handle_event(OWNMessage.parse("*#1*0010*1*100*0##"))  # off, speed 0
    light.handle_event(OWNMessage.parse("*1*7*0010##"))
    await hass.async_block_till_done()
    assert light._attr_brightness_pct == 50
    gateway.send_status_request.assert_not_awaited()


async def test_an_off_report_with_a_normal_speed_does_not_unmark_a_coarse_dimmer(hass):
    light, gateway = _light(hass, where="61")
    light.handle_event(OWNMessage.parse("*#1*61*1*130*240##"))
    light.handle_event(OWNMessage.parse("*#1*61*1*100*0##"))
    light.handle_event(OWNMessage.parse("*1*6*61##"))
    await hass.async_block_till_done()
    assert light._attr_brightness_pct == 40
    gateway.send_status_request.assert_not_awaited()


async def test_no_refresh_without_hass(hass):
    """A light not yet added to Home Assistant applies the preset and asks nothing."""
    light, gateway = _light(hass)
    light.handle_event(OWNMessage.parse("*#1*0010*1*153*4##"))
    light.hass = None
    light.handle_event(OWNMessage.parse("*1*7*0010##"))
    assert light._attr_brightness_pct == 50
    gateway.send_status_request.assert_not_awaited()
