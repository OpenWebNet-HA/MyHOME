"""WHO=14 Actuator Lock and post-#964 read-back gap tests for WHO=1 lights & switches (#976)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_BRIGHTNESS_PCT,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_FLASH,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
    FLASH_SHORT,
    ColorMode,
)
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.core import HomeAssistant
from OWNd.message import OWNEvent

from custom_components.myhome.const import (
    CONF_TRANSITION_MODE,
    TRANSITION_MODE_NATIVE,
    TRANSITION_MODE_SOFTWARE,
    percent_to_eight_bits,
)
from custom_components.myhome.light import MyHOMELight
from custom_components.myhome.switch import MyHOMESwitch


def _make_gateway(
    *,
    transition_mode: str = TRANSITION_MODE_SOFTWARE,
    with_lock_mgr: bool = False,
) -> SimpleNamespace:
    sent: list[str] = []
    status_requests: list[str] = []

    async def _send(cmd: object, *args: object, **kwargs: object) -> bool:
        sent.append(str(cmd))
        return True

    async def _send_status(cmd: object, *args: object, **kwargs: object) -> bool:
        status_requests.append(str(cmd))
        return True

    lock_mgr = None
    if with_lock_mgr:
        lock_mgr = SimpleNamespace(clear_lock_from_actuation=MagicMock())

    return SimpleNamespace(
        mac="00:03:50:00:09:76",
        unique_id="00:03:50:00:09:76",
        device_registry_id=None,
        log_id="[MH200N]",
        availability_signal="myhome_avail_test",
        config_entry=SimpleNamespace(options={CONF_TRANSITION_MODE: transition_mode}),
        send=AsyncMock(side_effect=_send),
        send_status_request=AsyncMock(side_effect=_send_status),
        sent=sent,
        status_requests=status_requests,
        _actuator_locks=lock_mgr,
        is_who_available=lambda _who: True,
    )


def _make_light(
    hass: HomeAssistant | None,
    gateway: SimpleNamespace,
    *,
    where: str = "55",
    interface: str | None = None,
    dimmable: bool = True,
    color_temp: bool = False,
    rgb: bool = False,
) -> MyHOMELight:
    dev_id = f"{where}#4#{interface}" if interface else where
    light = MyHOMELight(
        hass=hass,
        name=f"Light {where}",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id=dev_id,
        who="1",
        where=where,
        interface=interface,
        dimmable=dimmable,
        manufacturer="BTicino",
        model="Dimmer" if dimmable else "Relay",
        gateway=gateway,  # type: ignore[arg-type]
        color_temp=color_temp,
        rgb=rgb,
    )
    if hass is not None:
        light.hass = hass
    light.entity_id = f"light.light_{where}"
    light.platform = MagicMock()
    light.async_schedule_update_ha_state = MagicMock()
    light.async_write_ha_state = MagicMock()
    return light


def _make_switch(
    hass: HomeAssistant,
    gateway: SimpleNamespace,
    *,
    where: str = "31",
    interface: str | None = None,
) -> MyHOMESwitch:
    dev_id = f"{where}#4#{interface}" if interface else where
    sw = MyHOMESwitch(
        hass=hass,
        name=f"Switch {where}",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id=dev_id,
        who="1",
        where=where,
        interface=interface,
        device_class=SwitchDeviceClass.SWITCH,
        manufacturer="BTicino",
        model="F411/2",
        gateway=gateway,  # type: ignore[arg-type]
    )
    sw.hass = hass
    sw.entity_id = f"switch.switch_{where}"
    sw.platform = MagicMock()
    sw.async_schedule_update_ha_state = MagicMock()
    sw.async_write_ha_state = MagicMock()
    return sw


async def test_wave68runner_mh200n_121707_sequence(hass: HomeAssistant) -> None:
    """Replay @wave68runner's exact MH200N 12:17:07 sequence with zero bounce while locked."""
    gw = _make_gateway()
    light = _make_light(hass, gw, where="55", dimmable=True)
    assert "actuator_locked" not in light.extra_state_attributes
    assert light._actuator_locked is None

    # 1. *1*1*55## -> *1*2*55## -> HA sets 20% (*#1*55*#1*120*0##) -> *#1*55*1*120*5##
    light.handle_event(OWNEvent.parse("*1*1*55##"))
    assert light.is_on is True
    light.handle_event(OWNEvent.parse("*1*2*55##"))
    assert light._attr_brightness_pct == 1

    await light.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 20})
    assert gw.sent[-1] == "*#1*55*#1*120*0##"
    light.handle_event(OWNEvent.parse("*#1*55*1*120*5##"))
    assert light.is_on is True
    assert light._attr_brightness_pct == 20
    assert light._last_brightness_pct == 20
    assert light._pending_dim_confirm == set()

    # 2. *14*0*55## locks actuator 55 at (True, 20)
    light.async_schedule_update_ha_state.reset_mock()
    light.handle_actuator_lock(True)
    assert light._actuator_locked is True
    assert light.extra_state_attributes["actuator_locked"] is True
    assert light._locked_snapshot == (True, 20)
    assert light._dirty_while_locked is False
    light.async_schedule_update_ha_state.assert_called_once()

    # 3. Five slider moves (77, 46, 77, 39, 72) + two *1*0*55## OFF presses while locked
    light.async_schedule_update_ha_state.reset_mock()
    light.async_write_ha_state.reset_mock()
    gw.sent.clear()

    for pct in (77, 46, 77, 39, 72):
        await light.async_turn_on(**{ATTR_BRIGHTNESS_PCT: pct})
    await light.async_turn_off()
    await light.async_turn_off()

    assert gw.sent == [
        "*#1*55*#1*177*0##",
        "*#1*55*#1*146*0##",
        "*#1*55*#1*177*0##",
        "*#1*55*#1*139*0##",
        "*#1*55*#1*172*0##",
        "*1*0*55##",
        "*1*0*55##",
    ]
    # Zero bounce: HA state, brightness, and _last_brightness_pct never mutated!
    assert light.is_on is True
    assert light._attr_brightness_pct == 20
    assert light.brightness == percent_to_eight_bits(20)
    assert light._last_brightness_pct == 20
    assert light._dirty_while_locked is True
    assert light._pending_dim_confirm == set()
    light.async_schedule_update_ha_state.assert_not_called()
    light.async_write_ha_state.assert_not_called()

    # Polling while locked returns unchanged state -> remains locked
    light.handle_event(OWNEvent.parse("*#1*55*1*120*5##"))
    assert light._actuator_locked is True
    assert light.is_on is True
    assert light._attr_brightness_pct == 20

    # 4. *14*1*55## unlocks -> refreshes status & brightness because dirty_while_locked
    light.handle_actuator_lock(False)
    await hass.async_block_till_done()
    assert light._actuator_locked is False
    assert light.extra_state_attributes["actuator_locked"] is False
    assert light._locked_snapshot is None
    assert light._dirty_while_locked is False
    assert gw.status_requests == ["*#1*55##", "*#1*55*1##"]

    # 5. Subsequent 40%, 71%, off behave normally
    for pct, expected_cmd, reply in (
        (40, "*#1*55*#1*140*0##", "*#1*55*1*140*5##"),
        (71, "*#1*55*#1*171*0##", "*#1*55*1*171*5##"),
    ):
        await light.async_turn_on(**{ATTR_BRIGHTNESS_PCT: pct})
        assert gw.sent[-1] == expected_cmd
        assert light._attr_brightness_pct == pct
        light.handle_event(OWNEvent.parse(reply))
        assert light._attr_brightness_pct == pct
        assert light._last_brightness_pct == pct

    await light.async_turn_off()
    assert gw.sent[-1] == "*1*0*55##"
    light.handle_event(OWNEvent.parse("*1*0*55##"))
    assert light.is_on is False
    assert light._last_brightness_pct == 71


async def test_locked_while_off_slider_move_zero_bounce(hass: HomeAssistant) -> None:
    """Locked OFF dimmer keeps is_on=False, brightness=0, and _last_brightness_pct intact."""
    gw = _make_gateway()
    light = _make_light(hass, gw, where="55", dimmable=True)
    light.handle_event(OWNEvent.parse("*#1*55*1*135*5##"))
    light.handle_event(OWNEvent.parse("*1*0*55##"))
    light._attr_brightness_pct = 0
    light._attr_brightness = 0
    assert light.is_on is False
    assert light._last_brightness_pct == 35

    light.handle_actuator_lock(True)
    light.async_schedule_update_ha_state.reset_mock()
    gw.sent.clear()

    # Instant slider move and software-stepped transition slider move
    await light.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 77})
    await light.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 65, ATTR_TRANSITION: 3.0})

    assert gw.sent == ["*#1*55*#1*177*0##", "*#1*55*#1*165*0##"]
    assert light._fade_task is None
    assert light.is_on is False
    assert light._attr_brightness_pct == 0
    assert light.brightness == 0
    assert light._last_brightness_pct == 35
    light.async_schedule_update_ha_state.assert_not_called()

    # Unlock refreshes and clears dirty flag
    light.handle_actuator_lock(False)
    await hass.async_block_till_done()
    assert gw.status_requests == ["*#1*55##", "*#1*55*1##"]


async def test_locked_fade_color_temp_hs_rgb_timed_and_plain(hass: HomeAssistant) -> None:
    """Verify lock cancels active fades and suppresses mutations across all light command paths."""
    gw = _make_gateway(transition_mode=TRANSITION_MODE_SOFTWARE)

    # 1. Mid-fade lock cancels active fade task
    dimmer = _make_light(hass, gw, where="10", dimmable=True)
    dimmer._attr_is_on = True
    dimmer._attr_brightness_pct = 20
    dimmer._last_brightness_pct = 20

    stop_event = asyncio.Event()

    async def _slow_sleep(*_args: object, **_kwargs: object) -> None:
        await stop_event.wait()

    with patch("asyncio.sleep", side_effect=_slow_sleep):
        await dimmer.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80, ATTR_TRANSITION: 4.0})
        assert dimmer._fade_task is not None
        dimmer.handle_actuator_lock(True)
        assert dimmer._fade_task is None
        stop_event.set()
        await hass.async_block_till_done()

    snap_bri = dimmer._attr_brightness_pct
    snap_last = dimmer._last_brightness_pct
    dimmer.async_schedule_update_ha_state.reset_mock()
    dimmer.async_write_ha_state.reset_mock()
    gw.sent.clear()

    # Transition-only, brightness+transition, brightness=0, transition-only turn_off, plain on, timed
    await dimmer.async_turn_on(**{ATTR_TRANSITION: 2.0})
    await dimmer.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 90, ATTR_TRANSITION: 2.0})
    await dimmer.async_turn_on(**{ATTR_BRIGHTNESS: 0})
    await dimmer.async_turn_off(**{ATTR_TRANSITION: 2.0})
    await dimmer.async_turn_on()
    await dimmer.async_turn_on_timed(duration=60, brightness_pct=50)

    assert gw.sent == [
        "*1*1*10##",
        "*#1*10*#1*190*0##",
        "*1*0*10##",
        "*1*0*10##",
        "*1*1*10##",
        "*#1*10*#1*150*0##",
        "*1*11*10##",
    ]
    assert gw.status_requests == []
    assert dimmer._fade_task is None
    assert dimmer._attr_brightness_pct == snap_bri
    assert dimmer._last_brightness_pct == snap_last
    dimmer.async_schedule_update_ha_state.assert_not_called()
    dimmer.async_write_ha_state.assert_not_called()

    # 2. Color temperature light while locked (CT-only and CT+brightness)
    ct_light = _make_light(hass, gw, where="20", dimmable=True, color_temp=True)
    ct_light._attr_is_on = False
    ct_light._attr_color_temp = 333
    ct_light._attr_color_temp_kelvin = 3003
    ct_light.handle_actuator_lock(True)
    ct_light.async_schedule_update_ha_state.reset_mock()
    gw.sent.clear()

    await ct_light.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})
    await ct_light.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 5000, ATTR_BRIGHTNESS_PCT: 60})
    assert gw.sent == [
        "*#1*20*#14*250##",
        "*#1*20*#14*200##",
        "*#1*20*#1*160*0##",
    ]
    assert ct_light.is_on is False
    assert ct_light.color_temp == 333
    assert ct_light.color_temp_kelvin == 3003
    assert ct_light._attr_brightness_pct is None
    ct_light.async_schedule_update_ha_state.assert_not_called()

    # 3. HS / RGB light while locked
    rgb_light = _make_light(hass, gw, where="25", interface="02", dimmable=True, rgb=True)
    rgb_light._attr_is_on = True
    rgb_light._attr_hs_color = (10.0, 20.0)
    rgb_light._attr_rgb_color = (255, 212, 204)
    rgb_light._attr_brightness_pct = 30
    rgb_light._last_brightness_pct = 30
    rgb_light.handle_actuator_lock(True)
    rgb_light.async_schedule_update_ha_state.reset_mock()
    gw.sent.clear()

    await rgb_light.async_turn_on(**{ATTR_HS_COLOR: (180.0, 90.0), ATTR_BRIGHTNESS_PCT: 80})
    await rgb_light.async_turn_on(**{ATTR_RGB_COLOR: (0, 0, 255)})
    assert gw.sent == [
        "*#1*25#4#02*#12*180*90*80##",
        "*#1*25#4#02*#12*240*100*30##",
    ]
    assert rgb_light.hs_color == (10.0, 20.0)
    assert rgb_light.rgb_color == (255, 212, 204)
    assert rgb_light._attr_brightness_pct == 30
    assert rgb_light._last_brightness_pct == 30
    rgb_light.async_schedule_update_ha_state.assert_not_called()

    # 4. ON/OFF light unlock when dirty (status only, no get_brightness) and when clean/no hass
    relay = _make_light(hass, gw, where="30", dimmable=False)
    relay.handle_actuator_lock(True)
    await relay.async_turn_on(**{ATTR_FLASH: FLASH_SHORT})
    assert gw.sent[-1] == "*1*20*30##"
    assert relay._dirty_while_locked is True
    gw.status_requests.clear()
    relay.handle_actuator_lock(False)
    await hass.async_block_till_done()
    assert gw.status_requests == ["*#1*30##"]

    # Clean unlock does not schedule refresh; dirty unlock with hass=None is safe
    gw.status_requests.clear()
    relay.handle_actuator_lock(True)
    relay.handle_actuator_lock(False)
    await hass.async_block_till_done()
    assert gw.status_requests == []

    relay_no_hass = _make_light(None, gw, where="31", dimmable=False)
    relay_no_hass.handle_actuator_lock(True)
    await relay_no_hass.async_turn_on()
    relay_no_hass.handle_actuator_lock(False)
    assert relay_no_hass._dirty_while_locked is False


async def test_self_healing_unlock_and_locked_fine_preset_guard(hass: HomeAssistant) -> None:
    """Self-healing unlock fires only on real contradicting bus state, not polls or translations."""
    gw = _make_gateway(with_lock_mgr=True)
    light = _make_light(hass, gw, where="55", interface="01", dimmable=True)

    # Seed as fine dimmer at 53%
    light.handle_event(OWNEvent.parse("*#1*55#4#01*1*153*4##"))
    assert light.is_on is True
    assert light._attr_brightness_pct == 53

    light.handle_actuator_lock(True)
    assert light._locked_snapshot == (True, 53)

    # Translation frame, dim step, unknown WHAT 99, unchanged Dim 1, and coarse preset echo do NOT unlock
    for frame in (
        "*1*1000#0*55#4#01##",
        "*1*30*55#4#01##",
        "*1*99*55#4#01##",
        "*#1*55#4#01*1*153*4##",
        "*1*7*55#4#01##",
    ):
        light.handle_event(OWNEvent.parse(frame))
        assert light._actuator_locked is True, frame

    # Coarse preset 7 (50%) while locked fine dimmer at 53% keeps 53% and requests Dim 1
    await hass.async_block_till_done()
    assert light._attr_brightness_pct == 53
    assert "*#1*55#4#01*1##" in gw.status_requests
    gw._actuator_locks.clear_lock_from_actuation.assert_not_called()

    # Exact Dim 1 brightness change (53% -> 65%) self-heals unlock
    light.handle_event(OWNEvent.parse("*#1*55#4#01*1*165*4##"))
    assert light._actuator_locked is False
    assert light.extra_state_attributes["actuator_locked"] is False
    assert light._locked_snapshot is None
    assert light._attr_brightness_pct == 65
    gw._actuator_locks.clear_lock_from_actuation.assert_called_once_with("55", "01")

    # Lock again at (True, 65); real OFF event (*1*0*55#4#01##) self-heals unlock
    gw._actuator_locks.clear_lock_from_actuation.reset_mock()
    light.handle_actuator_lock(True)
    light.handle_event(OWNEvent.parse("*1*0*55#4#01##"))
    assert light._actuator_locked is False
    assert light.is_on is False
    gw._actuator_locks.clear_lock_from_actuation.assert_called_once_with("55", "01")

    # Lock again at (False, 65) with _actuator_locks=None; WHAT 19 (no_load) self-heals unlock
    gw._actuator_locks = None
    light.handle_actuator_lock(True)
    light.handle_event(OWNEvent.parse("*1*19*55#4#01##"))
    assert light._actuator_locked is False
    assert light.extra_state_attributes["no_load"] is True


async def test_post_964_unknown_lock_readback_gaps(hass: HomeAssistant) -> None:
    """Close post-#964 read-back gaps when _actuator_locked is None (locked before HA started)."""
    gw = _make_gateway(transition_mode=TRANSITION_MODE_NATIVE)

    # 1. OFF-locked dimmer before HA started: rejected brightness write -> #964 reads *1*0*55##
    dimmer = _make_light(hass, gw, where="55", dimmable=True)
    dimmer.handle_event(OWNEvent.parse("*#1*55*1*125*5##"))
    dimmer.handle_event(OWNEvent.parse("*1*0*55##"))
    dimmer._attr_brightness_pct = 0
    dimmer._attr_brightness = 0
    assert dimmer._last_brightness_pct == 25

    await dimmer.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 77})
    assert dimmer._last_brightness_pct == 77
    assert dimmer._pending_dim_confirm == {1}
    assert dimmer._pre_write_last_brightness_pct == 25

    # Second write before reply preserves original pre_write_last_brightness_pct = 25
    await dimmer.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 88})
    assert dimmer._pre_write_last_brightness_pct == 25

    # #964 read-back returns *1*0*55## -> restores _last_brightness_pct=25 and zeroes brightness
    dimmer.handle_event(OWNEvent.parse("*1*0*55##"))
    assert dimmer.is_on is False
    assert dimmer._last_brightness_pct == 25
    assert dimmer._attr_brightness_pct == 0
    assert dimmer._attr_brightness == 0
    assert dimmer._pending_dim_confirm == set()
    assert dimmer._pre_write_last_brightness_pct is None

    # 2. DALI ballast answering #964 *#1*55## with plain *1*1*55## after Dim 1 write
    await dimmer.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 60})
    assert 1 in dimmer._pending_dim_confirm
    dimmer.handle_event(OWNEvent.parse("*1*1*55##"))
    await hass.async_block_till_done()
    assert 1 not in dimmer._pending_dim_confirm
    assert gw.status_requests[-1] == "*#1*55*1##"

    # Coarse preset reply clears Dim 1 pending without duplicating refresh here
    gw.status_requests.clear()
    await dimmer.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 45})
    dimmer.handle_event(OWNEvent.parse("*1*6*55##"))
    await hass.async_block_till_done()
    assert 1 not in dimmer._pending_dim_confirm
    assert dimmer._pre_write_last_brightness_pct is None

    # 3. Software-stepped transition-only write -> *1*0*55## restores _last_brightness_pct
    sw_gw = _make_gateway(transition_mode=TRANSITION_MODE_SOFTWARE)
    sw_dimmer = _make_light(hass, sw_gw, where="56", dimmable=True)
    sw_dimmer._attr_is_on = False
    sw_dimmer._last_brightness_pct = 40
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await sw_dimmer.async_turn_on(**{ATTR_TRANSITION: 0.5})
        assert sw_dimmer._fade_task is not None
        await sw_dimmer._fade_task
    sw_dimmer.handle_event(OWNEvent.parse("*1*0*56##"))
    assert sw_dimmer._last_brightness_pct == 40
    assert sw_dimmer._attr_brightness_pct == 0

    # Timed turn on with brightness records Dim 1 and recovers on *1*0*56##
    await sw_dimmer.async_turn_on_timed(duration=60, brightness_pct=90)
    assert 1 in sw_dimmer._pending_dim_confirm
    sw_dimmer.handle_event(OWNEvent.parse("*1*0*56##"))
    assert sw_dimmer._last_brightness_pct == 40

    # 4. Dimension 14 (color_temp) and Dimension 12 (hs_color) read-backs on plain *1*1*##
    dali = _make_light(hass, gw, where="25", interface="02", dimmable=True, color_temp=True, rgb=True)
    dali._attr_is_on = True
    dali._last_brightness_pct = 33
    gw.status_requests.clear()

    await dali.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 3000})
    assert 14 in dali._pending_dim_confirm
    # Sentinel 14*1 does not clear 14
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*1##"))
    assert 14 in dali._pending_dim_confirm
    # Plain *1*1*## triggers get_color_temperature
    dali.handle_event(OWNEvent.parse("*1*1*25#4#02##"))
    await hass.async_block_till_done()
    assert 14 not in dali._pending_dim_confirm
    assert gw.status_requests[-1] == "*#1*25#4#02*14##"
    # Real Dim 14 report clears 14 cleanly
    await dali.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*250##"))
    assert 14 not in dali._pending_dim_confirm

    # CT-only write followed by plain *1*0*## clears 14 without corrupting _last_brightness_pct
    await dali.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 3500})
    dali.handle_event(OWNEvent.parse("*1*0*25#4#02##"))
    assert dali._pending_dim_confirm == set()
    assert dali._last_brightness_pct == 33

    # HS write -> plain *1*1*## triggers get_hsv_color
    gw.status_requests.clear()
    await dali.async_turn_on(**{ATTR_HS_COLOR: (120.0, 50.0), ATTR_BRIGHTNESS_PCT: 80})
    assert 12 in dali._pending_dim_confirm
    assert dali._pre_write_last_brightness_pct == 33
    dali.handle_event(OWNEvent.parse("*1*1*25#4#02##"))
    await hass.async_block_till_done()
    assert 12 not in dali._pending_dim_confirm
    assert gw.status_requests[-1] == "*#1*25#4#02*12##"

    # Real Dim 12 report clears 12; HS write followed by *1*0*## restores _last_brightness_pct
    await dali.async_turn_on(**{ATTR_HS_COLOR: (200.0, 60.0), ATTR_BRIGHTNESS_PCT: 90})
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*12*200*60*90##"))
    assert 12 not in dali._pending_dim_confirm
    assert dali._last_brightness_pct == 90

    await dali.async_turn_on(**{ATTR_HS_COLOR: (240.0, 70.0), ATTR_BRIGHTNESS_PCT: 50})
    dali.handle_event(OWNEvent.parse("*1*0*25#4#02##"))
    assert dali._last_brightness_pct == 90
    assert dali._attr_brightness_pct == 0

    # Plain ON with 12/14 pending when hass is None does not raise
    dali_no_hass = _make_light(None, gw, where="26", dimmable=True, color_temp=True, rgb=True)
    await dali_no_hass.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 3000})
    await dali_no_hass.async_turn_on(**{ATTR_HS_COLOR: (100.0, 50.0), ATTR_BRIGHTNESS_PCT: 50})
    dali_no_hass.handle_event(OWNEvent.parse("*1*1*26##"))
    assert dali_no_hass._pending_dim_confirm == set()


async def test_myhome_switch_who14_lock_timed_and_self_healing_unlock(hass: HomeAssistant) -> None:
    """MyHOMESwitch suppresses async_turn_on_timed optimism while locked and self-heals on real change."""
    gw = _make_gateway(with_lock_mgr=True)
    sw = _make_switch(hass, gw, where="31", interface="01")
    assert "actuator_locked" not in sw.extra_state_attributes
    assert sw._actuator_locked is None

    # Start OFF
    sw.handle_event(OWNEvent.parse("*1*0*31#4#01##"))
    assert sw.is_on is False

    # Lock while OFF
    sw.async_schedule_update_ha_state.reset_mock()
    sw.handle_actuator_lock(True)
    assert sw._actuator_locked is True
    assert sw._locked_is_on is False
    assert sw.extra_state_attributes["actuator_locked"] is True
    sw.async_schedule_update_ha_state.assert_called_once()

    # async_turn_on_timed while locked sends cmd without setting is_on=True or calling write_ha_state
    sw.async_write_ha_state.reset_mock()
    await sw.async_turn_on_timed(duration=60)
    assert gw.sent[-1] == "*1*11*31#4#01##"
    assert sw.is_on is False
    sw.async_write_ha_state.assert_not_called()

    # Same-state *1*0*31#4#01## and translation *1*1000#1*31#4#01## keep lock
    sw.handle_event(OWNEvent.parse("*1*0*31#4#01##"))
    sw.handle_event(OWNEvent.parse("*1*1000#1*31#4#01##"))
    assert sw._actuator_locked is True
    gw._actuator_locks.clear_lock_from_actuation.assert_not_called()

    # Real bus turn-on (*1*1*31#4#01##) self-heals unlock and notifies _actuator_locks
    sw.handle_event(OWNEvent.parse("*1*1*31#4#01##"))
    assert sw._actuator_locked is False
    assert sw.extra_state_attributes["actuator_locked"] is False
    assert sw._locked_is_on is None
    assert sw.is_on is True
    gw._actuator_locks.clear_lock_from_actuation.assert_called_once_with("31", "01")

    # Lock while ON, self-healing unlock when _actuator_locks is None
    gw._actuator_locks = None
    sw.handle_actuator_lock(True)
    assert sw._locked_is_on is True
    sw.handle_event(OWNEvent.parse("*1*0*31#4#01##"))
    assert sw._actuator_locked is False
    assert sw.is_on is False

    # Explicit unlock clears _locked_is_on and restores normal async_turn_on_timed optimism
    sw.handle_actuator_lock(True)
    assert sw._locked_is_on is False
    sw.handle_actuator_lock(False)
    assert sw._actuator_locked is False
    assert sw._locked_is_on is None
    assert sw.extra_state_attributes["actuator_locked"] is False

    await sw.async_turn_on_timed(duration=60)
    assert sw.is_on is True
    sw.async_write_ha_state.assert_called_once()


async def test_cached_register_and_pre_poll_none_state_self_healing(hass: HomeAssistant) -> None:
    """Self-healing unlock works after cached register() or pre-poll None-state locking."""
    from custom_components.myhome.actuator_lock import ActuatorLockManager

    # 1. Light locked before initial poll (_attr_is_on is None, _attr_brightness_pct is None)
    gw1 = _make_gateway()
    mgr1 = ActuatorLockManager(gw1)  # type: ignore[arg-type]
    gw1._actuator_locks = mgr1
    light_prepoll = _make_light(hass, gw1, where="55", dimmable=True)
    mgr1.register(light_prepoll)

    mgr1.observe(OWNEvent.parse("*14*0*55##"))
    assert light_prepoll._actuator_locked is True
    assert light_prepoll._locked_snapshot == (None, None)

    # First poll reports ON -> backfills (True, None), keeps lock
    light_prepoll.handle_event(OWNEvent.parse("*1*1*55##"))
    assert light_prepoll._actuator_locked is True
    assert light_prepoll._locked_snapshot == (True, None)

    # First Dim 1 poll reports 20% -> backfills (True, 20), keeps lock
    light_prepoll.handle_event(OWNEvent.parse("*#1*55*1*120*5##"))
    assert light_prepoll._actuator_locked is True
    assert light_prepoll._locked_snapshot == (True, 20)

    # Unchanged Dim 1 poll keeps lock; real brightness change (55%) self-heals unlock
    light_prepoll.handle_event(OWNEvent.parse("*#1*55*1*120*5##"))
    assert light_prepoll._actuator_locked is True
    light_prepoll.handle_event(OWNEvent.parse("*#1*55*1*155*5##"))
    assert light_prepoll._actuator_locked is False
    assert light_prepoll.extra_state_attributes["actuator_locked"] is False
    assert mgr1.is_locked("55") is False

    # 2. Light locked via cached register() while _locked_snapshot is None, then restored ON/20%
    gw2 = _make_gateway()
    mgr2 = ActuatorLockManager(gw2)  # type: ignore[arg-type]
    gw2._actuator_locks = mgr2
    mgr2.observe(OWNEvent.parse("*14*0*56##"))

    light_cached = _make_light(hass, gw2, where="56", dimmable=True)
    mgr2.register(light_cached)
    assert light_cached._actuator_locked is True
    assert light_cached._locked_snapshot is None
    light_cached._attr_is_on = True
    light_cached._attr_brightness_pct = 20

    # Immediate contradicting OFF frame seeds (True, 20) before _attr_is_on mutates -> self-heals!
    light_cached.handle_event(OWNEvent.parse("*1*0*56##"))
    assert light_cached._actuator_locked is False
    assert light_cached.extra_state_attributes["actuator_locked"] is False
    assert mgr2.is_locked("56") is False

    # 3. Switch locked before initial poll (_attr_is_on is None)
    sw_prepoll = _make_switch(hass, gw1, where="31", interface="01")
    mgr1.register(sw_prepoll)
    mgr1.observe(OWNEvent.parse("*14*0*31#4#01##"))
    assert sw_prepoll._actuator_locked is True
    assert sw_prepoll._locked_is_on is None

    # Initial poll ON backfills _locked_is_on=True, unchanged ON stays locked, OFF self-heals
    sw_prepoll.handle_event(OWNEvent.parse("*1*1*31#4#01##"))
    assert sw_prepoll._actuator_locked is True
    assert sw_prepoll._locked_is_on is True
    sw_prepoll.handle_event(OWNEvent.parse("*1*1*31#4#01##"))
    assert sw_prepoll._actuator_locked is True
    sw_prepoll.handle_event(OWNEvent.parse("*1*0*31#4#01##"))
    assert sw_prepoll._actuator_locked is False
    assert mgr1.is_locked("31", "01") is False

    # 4. Switch locked via cached register() while _locked_is_on is None, then restored ON
    mgr2.observe(OWNEvent.parse("*14*0*32##"))
    sw_cached = _make_switch(hass, gw2, where="32")
    mgr2.register(sw_cached)
    assert sw_cached._actuator_locked is True
    assert sw_cached._locked_is_on is None
    sw_cached._attr_is_on = True
    sw_cached.handle_event(OWNEvent.parse("*1*0*32##"))
    assert sw_cached._actuator_locked is False
    assert sw_cached.extra_state_attributes["actuator_locked"] is False
    assert mgr2.is_locked("32") is False


async def test_switch_ignores_what30_31_dim_steps(hass: HomeAssistant) -> None:
    """WHO=1 WHAT 30/31 dim steps must neither turn an OFF switch ON nor clear its lock."""
    from custom_components.myhome.actuator_lock import ActuatorLockManager

    gw = _make_gateway()
    mgr = ActuatorLockManager(gw)
    gw._actuator_locks = mgr
    sw = _make_switch(hass, gw, where="33")
    sw._attr_is_on = False
    mgr.register(sw)

    # Unlocked OFF switch stays OFF when WHAT 30 / 31 arrives
    sw.handle_event(OWNEvent.parse("*1*30*33##"))
    assert sw.is_on is False
    sw.handle_event(OWNEvent.parse("*1*31*33##"))
    assert sw.is_on is False

    # Locked OFF switch neither turns ON nor self-heals on WHAT 30 / 31
    mgr.observe(OWNEvent.parse("*14*0*33##"))
    assert sw._actuator_locked is True
    sw.handle_event(OWNEvent.parse("*1*30*33##"))
    sw.handle_event(OWNEvent.parse("*1*31*33##"))
    assert sw.is_on is False
    assert sw._actuator_locked is True
    assert mgr.is_locked("33") is True


async def test_dali_dim12_dim14_unlock_refresh_self_healing_and_rejected_cleanup(
    hass: HomeAssistant,
) -> None:
    """DALI Dim 12/14 unlock refreshes colour dims, self-heals on changes, and restores rejected writes."""
    from custom_components.myhome.actuator_lock import ActuatorLockManager

    gw = _make_gateway(transition_mode=TRANSITION_MODE_NATIVE)
    mgr = ActuatorLockManager(gw)
    gw._actuator_locks = mgr
    dali = _make_light(hass, gw, where="25", dimmable=True, color_temp=True, rgb=True)
    dali._attr_supported_color_modes = {ColorMode.HS, ColorMode.COLOR_TEMP}
    mgr.register(dali)

    # 1. Clean DALI baseline -> lock -> dirty write -> unlock refreshes all 4 requests
    dali.handle_event(OWNEvent.parse("*#1*25*14*300##"))
    dali.handle_event(OWNEvent.parse("*#1*25*12*100*50*80##"))
    dali._attr_is_on = True
    mgr.observe(OWNEvent.parse("*14*0*25##"))
    assert dali._locked_color_temp_snapshot == 300
    assert dali._locked_hs_snapshot == (100.0, 50.0)

    gw.sent.clear()
    await dali.async_turn_on(color_temp_kelvin=3500)
    assert dali._dirty_while_locked is True
    assert dali._attr_color_temp == 300

    mgr.observe(OWNEvent.parse("*14*1*25##"))
    await hass.async_block_till_done()
    assert gw.status_requests == ["*#1*25##", "*#1*25*1##", "*#1*25*14##", "*#1*25*12##"]

    # 2. Unchanged Dim 14 / 12 polls & OWNd sentinels keep lock; genuine Dim 14 change unlocks
    mgr.observe(OWNEvent.parse("*14*0*25##"))
    dali.handle_event(OWNEvent.parse("*#1*25*14*300##"))
    dali.handle_event(OWNEvent.parse("*#1*25*14*1##"))
    dali.handle_event(OWNEvent.parse("*#1*25*12*100*50*80##"))
    dali.handle_event(OWNEvent.parse("*#1*25*12*511*127*255##"))
    assert dali._actuator_locked is True

    dali.handle_event(OWNEvent.parse("*#1*25*14*250##"))
    assert dali._actuator_locked is False
    assert dali.color_temp == 250

    # 3. Genuine Dim 12 H/S change, Dim 12 V-only change, and RGB-only Dim 12 change self-heal
    mgr.observe(OWNEvent.parse("*14*0*25##"))
    dali.handle_event(OWNEvent.parse("*#1*25*12*200*50*80##"))
    assert dali._actuator_locked is False

    mgr.observe(OWNEvent.parse("*14*0*25##"))
    dali.handle_event(OWNEvent.parse("*#1*25*12*200*50*40##"))
    assert dali._actuator_locked is False

    dali._attr_rgb_color = (255, 0, 0)
    mgr.observe(OWNEvent.parse("*14*0*25##"))
    rgb_only = SimpleNamespace(
        is_translation=False,
        is_on=None,
        brightness=None,
        brightness_preset=None,
        hs=None,
        rgb=(0, 255, 0),
        value=None,
        color_temp=None,
        unknown_state=None,
        dimension=12,
        human_readable_log="rgb",
    )
    dali.handle_event(rgb_only)
    assert dali._actuator_locked is False

    # Backfill snapshots when locked BEFORE initial Dim 14 / Dim 12 poll arrives
    dali_fresh = _make_light(hass, gw, where="26", dimmable=True, color_temp=True, rgb=True)
    dali_fresh._attr_supported_color_modes = {ColorMode.HS, ColorMode.COLOR_TEMP}
    dali_fresh._attr_is_on = True
    dali_fresh._attr_brightness_pct = 50
    mgr.register(dali_fresh)
    mgr.observe(OWNEvent.parse("*14*0*26##"))
    assert dali_fresh._locked_color_temp_snapshot is None
    assert dali_fresh._locked_hs_snapshot is None
    dali_fresh.handle_event(OWNEvent.parse("*#1*26*14*250##"))
    assert dali_fresh._actuator_locked is True
    assert dali_fresh._locked_color_temp_snapshot == 250
    dali_fresh.handle_event(OWNEvent.parse("*#1*26*12*120*60*50##"))
    assert dali_fresh._actuator_locked is True
    assert dali_fresh._locked_hs_snapshot == (120.0, 60.0)
    assert dali_fresh._locked_rgb_snapshot is not None

    # 4. OFF-locked rejected-write read-back cleanup (unknown lock state before WHO=14 seen):
    #    4a. _last_brightness_pct originally 0 -> Dim 1 zero read-back restores 0 and 0%
    off_new = _make_light(hass, gw, where="27", dimmable=True)
    off_new._attr_is_on = False
    off_new._attr_brightness_pct = 0
    off_new._attr_brightness = 0
    off_new._last_brightness_pct = 0
    await off_new.async_turn_on(brightness_pct=45)
    assert off_new._last_brightness_pct == 45
    off_new.handle_event(OWNEvent.parse("*#1*27*1*100*5##"))
    assert off_new._last_brightness_pct == 0
    assert off_new._attr_brightness_pct == 0
    assert off_new._attr_brightness == 0
    assert off_new._pending_dim_confirm == set()

    #    4b. Rejected HS write on OFF DALI light -> *1*0*28## restores prior CT mode & value
    off_dali = _make_light(hass, gw, where="28", dimmable=True, color_temp=True, rgb=True)
    off_dali._attr_supported_color_modes = {ColorMode.HS, ColorMode.COLOR_TEMP}
    off_dali._attr_is_on = False
    off_dali._attr_brightness_pct = 0
    off_dali._attr_brightness = 0
    off_dali._last_brightness_pct = 65
    off_dali._attr_color_mode = ColorMode.COLOR_TEMP
    off_dali._attr_color_temp = 333
    off_dali._attr_color_temp_kelvin = 3003
    off_dali._attr_hs_color = (10.0, 20.0)
    off_dali._attr_rgb_color = (255, 220, 200)
    await off_dali.async_turn_on(hs_color=(180, 90))
    assert off_dali._attr_color_mode == ColorMode.HS
    off_dali.handle_event(OWNEvent.parse("*1*0*28##"))
    assert off_dali.is_on is False
    assert off_dali._attr_color_mode == ColorMode.COLOR_TEMP
    assert off_dali.color_temp == 333
    assert off_dali._attr_hs_color == (10.0, 20.0)
    assert off_dali._last_brightness_pct == 65
    assert off_dali._attr_brightness_pct == 0

    #    4c. Rejected color_temp write on OFF DALI light -> *1*0*28## restores HS mode & 0% brightness
    off_dali._attr_color_mode = ColorMode.HS
    off_dali._attr_brightness = None
    await off_dali.async_turn_on(color_temp_kelvin=4000)
    assert off_dali._attr_color_mode == ColorMode.COLOR_TEMP
    assert off_dali._attr_brightness_pct == 65
    off_dali.handle_event(OWNEvent.parse("*1*0*28##"))
    assert off_dali._attr_color_mode == ColorMode.HS
    assert off_dali.color_temp == 333
    assert off_dali._attr_brightness_pct == 0
    assert off_dali._attr_brightness == 0

    #    4d. Accepted CT + Dim 1 writes clear pre-write snapshot cleanly
    await off_dali.async_turn_on(color_temp_kelvin=4000, brightness_pct=50)
    off_dali.handle_event(OWNEvent.parse("*#1*28*1*150*5##"))
    off_dali.handle_event(OWNEvent.parse("*#1*28*14*250##"))
    assert off_dali._pending_dim_confirm == set()
    assert off_dali._pre_write_color_state is None


async def test_light_echo_watchdog_skips_known_locked_lights(hass: HomeAssistant) -> None:
    """LightEchoWatchdog must not arm or confirm status read-backs for known WHO=14 locked lights."""
    import time
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from OWNd.message import OWNCommand
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.myhome.actuator_lock import ActuatorLockManager
    from custom_components.myhome.gateway_confirm import LightEchoWatchdog

    sent: list[str] = []
    handler = SimpleNamespace(
        hass=hass,
        log_id="[WHO14]",
        send_status_request=AsyncMock(side_effect=lambda cmd: sent.append(str(cmd))),
    )
    mgr = ActuatorLockManager(handler)  # type: ignore[arg-type]
    handler._actuator_locks = mgr
    handler.actuator_lock_owner = lambda: handler
    dog = LightEchoWatchdog(handler)  # type: ignore[arg-type]

    try:
        # Lock point 07 before command -> watchdog skips arming and sends nothing
        mgr.observe(OWNEvent.parse("*14*0*07##"))
        dog.command_acknowledged(OWNCommand.parse("*1*1*07##"), time.monotonic())
        assert dog.pending == {}
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        await hass.async_block_till_done()
        assert sent == []

        # Unlocked at ACK time, then locked during 1.5 s delay -> _confirm skips read-back
        mgr.observe(OWNEvent.parse("*14*1*07##"))
        dog.command_acknowledged(OWNCommand.parse("*1*1*07##"), time.monotonic())
        assert dog.pending == {"07": "*1*1*07##"}
        mgr.observe(OWNEvent.parse("*14*0*07##"))
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        await hass.async_block_till_done()
        assert sent == []
    finally:
        dog.cancel_all()
