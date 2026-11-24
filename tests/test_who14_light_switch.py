"""Comprehensive WHO=14 actuator lock & echo-confirmed light/switch tests (#976)."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_BRIGHTNESS_PCT,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
    ColorMode,
)
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util
from OWNd.message import OWNCommand, OWNEvent, OWNMessage
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.myhome import gateway_confirm
from custom_components.myhome import light as light_mod
from custom_components.myhome.actuator_lock import (
    ActuatorLockManager,
    apply_actuator_lock_state,
)
from custom_components.myhome.const import (
    CONF_TRANSITION_MODE,
    DOMAIN,
    TRANSITION_MODE_NATIVE,
    TRANSITION_MODE_SOFTWARE,
)
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.expose import ExposeManager
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_confirm import LightEchoWatchdog
from custom_components.myhome.light import ATTR_COLOR_TEMP, MyHOMELight
from custom_components.myhome.switch import MyHOMESwitch

GW_MAC = "00:03:50:aa:55:01"


def _make_gateway(
    hass: HomeAssistant,
    *,
    model: str = "MH200N",
    options: dict[str, Any] | None = None,
) -> tuple[MockConfigEntry, MyHOMEGatewayHandler]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.55",
            CONF_PORT: 20000,
            CONF_PASSWORD: "secret",
            CONF_MAC: GW_MAC,
            CONF_NAME: model,
        },
        options=options or {},
        unique_id=GW_MAC,
    )
    entry.add_to_hass(hass)
    handler = MyHOMEGatewayHandler(hass, entry)
    handler.send = AsyncMock()
    handler.send_fade_step = AsyncMock()
    handler.send_status_request = AsyncMock()
    entry.runtime_data = MyHOMERuntimeData(gateway=handler)
    return entry, handler


def _attach_live_entity(hass: HomeAssistant, entity: Any, entity_id: str) -> list[Event]:
    """Attach an entity to ``hass`` so ``_publish_state`` writes real HA state machine states."""
    entity.hass = hass
    entity.entity_id = entity_id
    entity.platform = MagicMock(config_entry=entity._gateway_handler.config_entry)
    entity._register_availability_listener()

    @callback
    def _write_state() -> None:
        if entity._attr_is_on is None:
            st = "unknown"
        else:
            st = "on" if entity._attr_is_on else "off"
        attrs = dict(entity.state_attributes or {})
        attrs.update(entity.extra_state_attributes or {})
        hass.states.async_set(entity_id, st, attrs)

    entity.async_write_ha_state = _write_state
    entity.async_schedule_update_ha_state = _write_state
    events: list[Event] = []
    async_track_state_change_event(hass, [entity_id], events.append)
    return events


async def _expire_watchdog(hass: HomeAssistant) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# 1. Scenario S3: @wave68runner MH200N dimmer 55 + WLED 1015 ExposeManager
# ---------------------------------------------------------------------------


async def test_s3_mh200n_dimmer_55_wled_1015_zero_bounce_locked_on_off_and_cold_boot(
    hass: HomeAssistant,
) -> None:
    """Verify 0.0s state bounce and 0 WLED 1015 follower triggers across locked ON, OFF, and None."""
    entry, handler = _make_gateway(
        hass,
        model="MH200N",
        options={CONF_TRANSITION_MODE: TRANSITION_MODE_NATIVE},
    )
    dimmer_55 = MyHOMELight(
        hass=hass,
        name="Dimmer 55",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="55",
        who="1",
        where="55",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=handler,
    )
    dimmer_events = _attach_live_entity(hass, dimmer_55, "light.dimmer_55")

    # Configure WLED 1015 follower exposed onto SCS bus via ExposeManager:
    # An HA follower mirrors light.dimmer_55 -> light.wled_follower -> ExposeBinding(1015)
    hass.states.async_set("light.wled_follower", "off", {})
    wled_follower_triggers: list[str] = []

    @callback
    def _mirror_dimmer_to_wled(ev: Event) -> None:
        new_st = ev.data.get("new_state")
        old_st = ev.data.get("old_state")
        if new_st is None or (
            old_st is not None
            and old_st.state == new_st.state
            and old_st.attributes.get("brightness") == new_st.attributes.get("brightness")
        ):
            return
        wled_follower_triggers.append(f"{new_st.state}:{new_st.attributes.get('brightness')}")
        hass.states.async_set(
            "light.wled_follower",
            new_st.state,
            {"brightness": new_st.attributes.get("brightness")},
        )

    unsub_mirror = async_track_state_change_event(hass, ["light.dimmer_55"], _mirror_dimmer_to_wled)
    expose_mgr = ExposeManager(
        hass,
        entry,
        handler,
        [{"entity_id": "light.wled_follower", "who": 1, "where": "1015", "cooldown": 0.0}],
    )
    await expose_mgr.async_start()
    exposed_tx: list[str] = []
    handler.send_exposed = AsyncMock(side_effect=lambda cmd, **_kw: exposed_tx.append(str(cmd)))

    try:
        # --- Phase A: Locked while OFF (*1*0*55## then *14*0*55##) ---
        dimmer_55.handle_event(OWNEvent.parse("*1*0*55##"))
        await hass.async_block_till_done()
        assert hass.states.get("light.dimmer_55").state == "off"

        handler._actuator_locks.observe(OWNMessage.parse("*14*0*55##"))
        await hass.async_block_till_done()
        assert dimmer_55.actuator_locked is True
        assert hass.states.get("light.dimmer_55").attributes["actuator_locked"] is True

        wled_follower_triggers.clear()
        exposed_tx.clear()
        dimmer_events.clear()
        handler.send.reset_mock()
        handler.send_status_request.reset_mock()

        # User drags slider to 80% and calls timed turn-on while locked OFF
        await dimmer_55.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80})
        await dimmer_55.async_turn_on_timed(duration=60, brightness_pct=70)
        await hass.async_block_till_done()

        # Commands still transmit on SCS (in case actuator power-cycled offline),
        # but HA state NEVER bounces to ON and WLED 1015 NEVER triggers!
        assert [str(c.args[0]) for c in handler.send.await_args_list] == [
            "*#1*55*#1*180*0##",
            "*#1*55*#1*170*0##",
            "*1*11*55##",
        ]
        assert dimmer_55.is_on is False
        assert dimmer_55._last_brightness_pct == 100  # NOT corrupted by rejected 80%/70% writes!
        assert hass.states.get("light.dimmer_55").state == "off"
        assert dimmer_events == []
        assert wled_follower_triggers == []
        assert exposed_tx == []

        # Watchdog skips arming & polling when known locked
        handler._echo_watchdog.command_acknowledged(
            OWNCommand.parse("*#1*55*#1*180*0##"), time.monotonic()
        )
        assert handler._echo_watchdog.pending == {}
        await _expire_watchdog(hass)
        handler.send_status_request.assert_not_called()

        # --- Phase B: Unlock (*14*1*55##) skips redundant #612 poll because _bus_state_complete=True ---
        handler._actuator_locks.observe(OWNMessage.parse("*14*1*55##"))
        await hass.async_block_till_done()
        assert dimmer_55.actuator_locked is False
        assert hass.states.get("light.dimmer_55").attributes["actuator_locked"] is False
        handler.send_status_request.assert_not_called()

        # --- Phase C: Locked while ON at 20% (*#1*55*1*120*5## then *14*0*55##) ---
        dimmer_55.handle_event(OWNEvent.parse("*#1*55*1*120*5##"))
        await hass.async_block_till_done()
        assert dimmer_55.is_on is True
        assert dimmer_55._attr_brightness_pct == 20

        handler._actuator_locks.observe(OWNMessage.parse("*14*0*55##"))
        await hass.async_block_till_done()
        assert dimmer_55.actuator_locked is True

        wled_follower_triggers.clear()
        exposed_tx.clear()
        handler.send.reset_mock()

        # Slider drag 20% -> 80% + click OFF while locked ON at 20%
        await dimmer_55.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80})
        await dimmer_55.async_turn_off()
        await hass.async_block_till_done()

        assert [str(c.args[0]) for c in handler.send.await_args_list] == [
            "*#1*55*#1*180*0##",
            "*1*0*55##",
        ]
        assert dimmer_55.is_on is True
        assert dimmer_55._attr_brightness_pct == 20
        assert wled_follower_triggers == []
        assert exposed_tx == []

        # --- Phase D: Cold boot (actuator_locked is None) while physically locked ON at 20% ---
        handler._actuator_locks.clear()
        dimmer_55._actuator_locked = None
        dimmer_55._attr_extra_state_attributes.pop("actuator_locked", None)
        dimmer_55.async_write_ha_state()
        await hass.async_block_till_done()

        wled_follower_triggers.clear()
        exposed_tx.clear()
        handler.send_status_request.reset_mock()

        async def _mh200n_status_reply(cmd: Any) -> None:
            raw = str(cmd)
            if raw == "*#1*55##":
                dimmer_55.handle_event(OWNEvent.parse("*1*1*55##"))
            elif raw == "*#1*55*1##":
                dimmer_55.handle_event(OWNEvent.parse("*#1*55*1*120*5##"))

        handler.send_status_request.side_effect = _mh200n_status_reply

        # Drag slider 20% -> 80% when actuator_locked is None:
        # 1) Immediately: state stays 20% (0 drift, 0 follower triggers)
        # 2) At 1.5s: LightEchoWatchdog polls *#1*55## AND *#1*55*1## -> stays 20% (0 permanent drift!)
        await dimmer_55.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80})
        handler._echo_watchdog.command_acknowledged(
            OWNCommand.parse("*#1*55*#1*180*0##"), time.monotonic()
        )
        assert dimmer_55._attr_brightness_pct == 20
        assert wled_follower_triggers == []

        await _expire_watchdog(hass)
        assert [str(c.args[0]) for c in handler.send_status_request.await_args_list] == [
            "*#1*55##",
            "*#1*55*1##",
        ]
        assert dimmer_55.is_on is True
        assert dimmer_55._attr_brightness_pct == 20
        assert wled_follower_triggers == []
        assert exposed_tx == []
    finally:
        unsub_mirror()
        await expose_mgr.async_stop()


# ---------------------------------------------------------------------------
# 2. Dimension-Aware LightEchoWatchdog (Dim 1, Dim 12, Dim 14, #9, Lock Skip)
# ---------------------------------------------------------------------------


async def test_watchdog_dimension_aware_read_back_zigbee_guard_and_lock_skip(
    hass: HomeAssistant,
) -> None:
    """Verify Dim 1/12/14 status_seen & _confirm read-backs, #9 ZigBee guard, and lock skip."""
    locks = ActuatorLockManager(SimpleNamespace(hass=hass, config_entry=None))  # type: ignore[arg-type]
    handler = SimpleNamespace(
        hass=hass,
        log_id="[GW]",
        send_status_request=AsyncMock(),
        _actuator_locks=locks,
    )
    dog = LightEchoWatchdog(handler)  # type: ignore[arg-type]
    try:
        # 1. Dim 14 (color_temp): sentinel *#1*25*14*1## does NOT satisfy watch; valid *#1*25*14*300## DOES
        dog.command_acknowledged(OWNCommand.parse("*#1*25*#14*300##"), time.monotonic())
        assert dog.pending == {"25": "*#1*25*#14*300##"}
        dog.status_seen(OWNMessage.parse("*#1*25*14*1##"))
        assert "25" in dog.pending
        dog.status_seen(OWNMessage.parse("*#1*25*14*300##"))
        assert dog.pending == {}

        # Unanswered Dim 14 polls both status and Dim 14
        dog.command_acknowledged(OWNCommand.parse("*#1*25*#14*300##"), time.monotonic())
        await _expire_watchdog(hass)
        assert [str(c.args[0]) for c in handler.send_status_request.await_args_list] == [
            "*#1*25##",
            "*#1*25*14##",
        ]
        handler.send_status_request.reset_mock()

        # 2. Dim 12 (HSV): sentinel 511*127*255 and wrong polarity V=0 do NOT satisfy V=80 watch;
        # matching V=80 Dim 12 event DOES satisfy watch
        dog.command_acknowledged(OWNCommand.parse("*#1*26*#12*120*50*80##"), time.monotonic())
        dog.status_seen(OWNMessage.parse("*#1*26*12*511*127*255##"))
        dog.status_seen(OWNMessage.parse("*#1*26*12*120*50*0##"))
        assert "26" in dog.pending
        dog.status_seen(OWNMessage.parse("*#1*26*12*120*50*80##"))
        assert dog.pending == {}

        # Unanswered Dim 12 polls both status and Dim 12
        dog.command_acknowledged(OWNCommand.parse("*#1*26*#12*120*50*80##"), time.monotonic())
        await _expire_watchdog(hass)
        assert [str(c.args[0]) for c in handler.send_status_request.await_args_list] == [
            "*#1*26##",
            "*#1*26*12##",
        ]
        handler.send_status_request.reset_mock()

        # 3. ZigBee #9 Dim 1 write skips get_brightness (3578 NACKs Dim 1)
        dog.command_acknowledged(
            OWNCommand.parse("*#1*701234501#9*#1*150*0##"), time.monotonic()
        )
        await _expire_watchdog(hass)
        assert [str(c.args[0]) for c in handler.send_status_request.await_args_list] == [
            "*#1*701234501#9##",
        ]
        handler.send_status_request.reset_mock()

        # 4. Lock observed AFTER watch armed cancels/skips _confirm at expiry (0 polls!)
        dog.command_acknowledged(OWNCommand.parse("*1*1*55##"), time.monotonic())
        assert "55" in dog.pending
        locks.set_point_lock("55", None, True, observed=True)
        await _expire_watchdog(hass)
        handler.send_status_request.assert_not_called()

        # 5. Lock already active when new command_acknowledged replaces previous watch
        locks.set_point_lock("55", None, False, observed=True)
        dog.command_acknowledged(OWNCommand.parse("*1*1*55##"), time.monotonic())
        assert "55" in dog.pending
        locks.set_point_lock("55", None, True, observed=True)
        dog.command_acknowledged(OWNCommand.parse("*1*0*55##"), time.monotonic())
        assert dog.pending == {}

        # 6. Dim 14 sentinel write (<= 1) returns None in _commanded_on
        assert gateway_confirm._commanded_on(OWNCommand.parse("*#1*25*#14*1##")) is False
        assert (
            gateway_confirm._commanded_on(SimpleNamespace(what=None, dimension=14, dimension_value=[]))
            is None
        )
    finally:
        dog.cancel_all()


# ---------------------------------------------------------------------------
# 3. RestoreEntity, Pre-Seeded Lock, DALI Carryover & Self-Healing
# ---------------------------------------------------------------------------


async def test_restore_entity_initial_poll_and_dali_self_healing(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify RestoreEntity + initial poll preserves actuator_locked=True and DALI self-heals only on real diff."""
    _entry, handler = _make_gateway(hass, model="F461")

    # Pre-lock 25#4#02 in manager before entity construction / registration
    handler._actuator_locks.observe(OWNMessage.parse("*14*0*25#4#02##"))

    # Also verify __init__ preserves _actuator_locked if already set on subclass/instance
    with patch.object(MyHOMELight, "_actuator_locked", True):
        pre_locked = MyHOMELight(
            hass=hass,
            name="Pre",
            entity_name=None,
            icon=None,
            icon_on=None,
            device_id="91",
            who="1",
            where="91",
            interface=None,
            dimmable=False,
            manufacturer="BTicino",
            model="Relay",
            gateway=handler,
        )
        assert pre_locked.extra_state_attributes["actuator_locked"] is True

    # Fresh restart simulation: manager is empty at boot, entity restores actuator_locked=True
    handler._actuator_locks.clear()
    dali = MyHOMELight(
        hass=hass,
        name="DALI 25",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="25#4#02",
        who="1",
        where="25",
        interface="02",
        dimmable=True,
        manufacturer="BTicino",
        model="F429",
        gateway=handler,
        color_temp=True,
        rgb=True,
    )
    _attach_live_entity(hass, dali, "light.dali_25")
    await dali.async_restore_last_state(
        State(
            "light.dali_25",
            "on",
            {
                "supported_color_modes": ["hs", "color_temp"],
                "actuator_locked": True,
            },
        )
    )
    assert dali.actuator_locked is True
    assert handler._actuator_locks.is_locked("25", "02") is True
    assert dali._attr_brightness_pct is None
    assert dali.color_temp is None
    assert dali.hs_color is None

    # Initial discovery polls seed None -> X WITHOUT clearing restored actuator_locked=True!
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*1*150*0##"))
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*300##"))
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*12*180*100*50##"))
    assert dali.actuator_locked is True
    assert dali._attr_brightness_pct == 50
    assert dali.color_temp == 300
    assert dali.hs_color == (180.0, 100.0)

    # Repeating unchanged status/dimension polls or sentinels keeps actuator_locked=True!
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*1*150*0##"))
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*300##"))
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*1##"))
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*12*180*100*50##"))
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*12*511*127*255##"))
    dali.handle_event(OWNEvent.parse("*1*30*25#4#02##"))
    dali.handle_event(OWNEvent.parse("*1*19*25#4#02##"))
    dali.handle_event(OWNEvent.parse("*1*99*25#4#02##"))
    # Restore ON at 50% after WHAT 19 test while re-locking
    apply_actuator_lock_state(dali, False)
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*1*150*0##"))
    handler._actuator_locks.observe(OWNMessage.parse("*14*0*25#4#02##"))
    assert dali.actuator_locked is True

    # Interface isolation: local 25 or other interface 25#4#01 never clears lock on 25#4#02
    dali.handle_event(OWNEvent.parse("*1*0*25##"))
    dali._attr_is_on = True
    dali.handle_event(OWNEvent.parse("*1*0*25#4#01##"))
    dali._attr_is_on = True
    dali.handle_event(OWNEvent.parse("*1*0*0##"))
    dali.handle_event(OWNEvent.parse("*1*0*2##"))
    dali.handle_event(OWNEvent.parse("*1*0*#1##"))
    assert dali.actuator_locked is True

    # Locked DALI writes do not mutate state before echo, and _pending_cmd_brightness_pct expires cleanly
    clock = [1000.0]
    monkeypatch.setattr(light_mod.time, "monotonic", lambda: clock[0])
    handler.send.reset_mock()
    await dali.async_turn_on(**{ATTR_COLOR_TEMP: 250})
    await dali.async_turn_on(**{ATTR_HS_COLOR: (240.0, 80.0), ATTR_BRIGHTNESS: 204})
    assert dali.color_temp == 300
    assert dali.hs_color == (180.0, 100.0)
    assert dali._attr_brightness_pct == 50

    # Within 1.5s, RGB write uses pending 80%; after 1.5s expiry, falls back to confirmed 50%!
    await dali.async_turn_on(**{ATTR_RGB_COLOR: (255, 0, 0)})
    assert str(handler.send.await_args_list[-1].args[0]) == "*#1*25#4#02*#12*0*100*80##"
    clock[0] += 2.0
    await dali.async_turn_on(**{ATTR_RGB_COLOR: (255, 0, 0)})
    assert str(handler.send.await_args_list[-1].args[0]) == "*#1*25#4#02*#12*0*100*50##"

    # Failed async_turn_off restores _pending_cmd_brightness_pct when enqueue raises
    await dali.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 77})
    assert dali._pending_cmd_brightness_pct is not None
    handler.send = AsyncMock(side_effect=RuntimeError("send failed"))
    with pytest.raises(RuntimeError):
        await dali.async_turn_off()
    assert dali._pending_cmd_brightness_pct is not None
    handler.send = AsyncMock()

    # Genuine P2P color_temp change (300 -> 250) self-heals actuator_locked=False!
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*250##"))
    assert dali.color_temp == 250
    assert dali.actuator_locked is False
    assert handler._actuator_locks.is_locked("25", "02") is False

    # Re-lock and verify genuine P2P hs_color change ((180, 100) -> (240, 80)) self-heals!
    handler._actuator_locks.observe(OWNMessage.parse("*14*0*25#4#02##"))
    assert dali.actuator_locked is True
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*12*240*80*50##"))
    assert dali.hs_color == (240.0, 80.0)
    assert dali.actuator_locked is False


# ---------------------------------------------------------------------------
# 4. SoftwareFadeEngine Locked Single-Frame Bypass, Mid-Fade Lock & Fine Preset Guard
# ---------------------------------------------------------------------------


async def test_software_fade_locked_bypass_mid_fade_lock_and_fine_preset_bucket(
    hass: HomeAssistant,
) -> None:
    """Verify single-frame bypass when locked, mid-fade lock cancel, unlock poll, and fine preset guard."""
    entry, handler = _make_gateway(
        hass,
        model="MH200N",
        options={CONF_TRANSITION_MODE: TRANSITION_MODE_SOFTWARE},
    )
    light = MyHOMELight(
        hass=hass,
        name="Fine Dimmer 42",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="42",
        who="1",
        where="42",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=handler,
    )
    _attach_live_entity(hass, light, "light.fine_42")

    # 1. Unlock when _bus_state_complete is False triggers async_update()
    handler._actuator_locks.observe(OWNMessage.parse("*14*0*42##"))
    assert light.actuator_locked is True
    assert light._bus_state_complete is False
    handler.send_status_request.reset_mock()
    handler._actuator_locks.observe(OWNMessage.parse("*14*1*42##"))
    await hass.async_block_till_done()
    assert light.actuator_locked is False
    handler.send_status_request.assert_called_once()

    # Also cover handle_actuator_lock(False) when hass is None (no crash)
    detached = MyHOMELight(
        hass=None,
        name="Detached",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="43",
        who="1",
        where="43",
        interface=None,
        dimmable=False,
        manufacturer="BTicino",
        model="Relay",
        gateway=handler,
    )
    detached.handle_actuator_lock(True)
    detached.handle_actuator_lock(False)
    assert detached.actuator_locked is False

    # 2. Seed fine dimmer at exact 22% (*#1*42*1*122*5##) and lock it
    light.handle_event(OWNEvent.parse("*#1*42*1*122*5##"))
    assert light._exact_level_seen is True
    assert light._attr_brightness_pct == 22
    handler._actuator_locks.observe(OWNMessage.parse("*14*0*42##"))
    assert light.actuator_locked is True

    # Coarse preset *1*4*42## (20% bucket, |20 - 22| <= 15) holds 22% (0 bounce!) and
    # subsequent exact *#1*42*1*122*5## reply keeps 22% and preserves actuator_locked=True!
    light.handle_event(OWNEvent.parse("*1*4*42##"))
    assert light._attr_brightness_pct == 22
    assert light.actuator_locked is True
    light.handle_event(OWNEvent.parse("*#1*42*1*122*5##"))
    assert light._attr_brightness_pct == 22
    assert light.actuator_locked is True

    # Software-stepped commands while known locked send 1 single frame (0 multi-step spam, 0 state mutation)
    handler.send.reset_mock()
    await light.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80, ATTR_TRANSITION: 3.0})
    await light.async_turn_on(**{ATTR_TRANSITION: 3.0})
    await light.async_turn_off(**{ATTR_TRANSITION: 3.0})
    assert light._fade_task is None
    assert [str(c.args[0]) for c in handler.send.await_args_list] == [
        "*#1*42*#1*180*0##",
        "*#1*42*#1*122*0##",
        "*1*0*42##",
    ]
    assert light.is_on is True
    assert light._attr_brightness_pct == 22

    # Different coarse preset bucket (*1*8*42## -> 60% != preset 4) self-heals actuator_locked=False!
    light.handle_event(OWNEvent.parse("*1*8*42##"))
    assert light.actuator_locked is False
    assert light._attr_brightness_pct == 60

    # Re-lock at 60% and verify exact Dimension 1 brightness change (60% -> 45%) also self-heals!
    handler._actuator_locks.observe(OWNMessage.parse("*14*0*42##"))
    assert light.actuator_locked is True
    # Routed frame *#1*42#4#01*1*145*5## on local light 42 does NOT self-heal
    light.handle_event(OWNEvent.parse("*#1*42#4#01*1*145*5##"))
    light._attr_brightness_pct = 60
    assert light.actuator_locked is True
    assert light._is_own_point_event(OWNEvent.parse("*1*1*0##")) is False
    # Own P2P Dimension 1 change *#1*42*1*145*5## self-heals!
    light.handle_event(OWNEvent.parse("*#1*42*1*145*5##"))
    assert light.actuator_locked is False
    assert light._attr_brightness_pct == 45

    # 3. Mid-fade WHO=14 lock cancels active software fade immediately
    light._fade_engine.first_echo_timeout = 5.0
    await light.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 90, ATTR_TRANSITION: 2.0})
    assert light._fade_engine.is_fading is True
    handler._actuator_locks.observe(OWNMessage.parse("*14*0*42##"))
    assert light._fade_engine.is_fading is False
    assert light.actuator_locked is True
    await hass.async_block_till_done()

    # 4. Cold-boot (actuator_locked=None) concurrent frozen Dim 1 poll during step-1 echo wait
    # cancels fade via cancel_delta without drifting!
    handler._actuator_locks.clear()
    light._actuator_locked = None
    light._attr_brightness_pct = 20
    light._attr_is_on = True
    await light.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80, ATTR_TRANSITION: 1.0})
    await asyncio.sleep(0)
    assert light._fade_engine.is_fading is True
    assert light._fade_engine.pending_first_step_pct is not None
    # Frozen dimmer still at 20% while step 1 asked for >= 30%
    light.handle_event(OWNEvent.parse("*#1*42*1*120*5##"))
    await hass.async_block_till_done()
    assert light._fade_engine.is_fading is False
    assert light._attr_brightness_pct == 20

    # 5. Native transition-only switch_on(speed) is also non-optimistic until echo
    hass.config_entries.async_update_entry(
        entry, options={CONF_TRANSITION_MODE: TRANSITION_MODE_NATIVE}
    )
    light._attr_is_on = False
    handler.send.reset_mock()
    await light.async_turn_on(**{ATTR_TRANSITION: 4.0})
    assert [str(c.args[0]) for c in handler.send.await_args_list] == ["*1*1#4*42##"]
    assert light.is_on is False
    light.handle_event(OWNEvent.parse("*1*1*42##"))
    assert light.is_on is True


# ---------------------------------------------------------------------------
# 5. MyHOMESwitch WHO=14 Lock, WHAT 30/31 Ignore, Interface Isolation & Self-Heal
# ---------------------------------------------------------------------------


async def test_who14_switch_lock_what30_ignore_and_self_healing(
    hass: HomeAssistant,
) -> None:
    """Verify MyHOMESwitch non-optimistic timed writes, WHAT 30/31 ignore, and state-diff self-heal."""
    _entry, handler = _make_gateway(hass, model="F461")

    # Pre-locked __init__ attribute preservation check on MyHOMESwitch
    with patch.object(MyHOMESwitch, "_actuator_locked", True):
        pre_sw = MyHOMESwitch(
            hass=hass,
            name="Pre Switch",
            entity_name=None,
            icon=None,
            icon_on=None,
            device_id="92",
            who="1",
            where="92",
            interface=None,
            device_class="switch",
            manufacturer="BTicino",
            model="F411/4",
            gateway=handler,
        )
        assert pre_sw.extra_state_attributes["actuator_locked"] is True

    sw = MyHOMESwitch(
        hass=hass,
        name="Relay 07",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="07",
        who="1",
        where="07",
        interface=None,
        device_class="switch",
        manufacturer="BTicino",
        model="F411/4",
        gateway=handler,
    )
    sw_events = _attach_live_entity(hass, sw, "switch.relay_07")

    # WHAT 30 / 31 dim steps are ignored even when unlocked (never flip switch ON!)
    sw.handle_event(OWNEvent.parse("*1*0*07##"))
    assert sw.is_on is False
    sw.handle_event(OWNEvent.parse("*1*30*07##"))
    sw.handle_event(OWNEvent.parse("*1*31*07##"))
    assert sw.is_on is False

    # Lock switch 07 while OFF (*14*0*07##)
    handler._actuator_locks.observe(OWNMessage.parse("*14*0*07##"))
    await hass.async_block_till_done()
    assert sw.actuator_locked is True
    assert hass.states.get("switch.relay_07").attributes["actuator_locked"] is True

    sw_events.clear()
    handler.send.reset_mock()

    # Plain & timed turn-on send bus commands without mutating state
    await sw.async_turn_on()
    await sw.async_turn_on_timed(duration=60)
    await hass.async_block_till_done()
    assert [str(c.args[0]) for c in handler.send.await_args_list] == [
        "*1*1*07##",
        "*1*11*07##",
    ]
    assert sw.is_on is False
    assert sw_events == []

    # Unchanged status poll (*1*0*07##), broadcasts, interface mismatch, translations keep lock True
    sw.handle_event(OWNEvent.parse("*1*0*07##"))
    sw.handle_event(OWNEvent.parse("*1*1000#1*07##"))
    sw.handle_event(OWNEvent.parse("*1*1*0##"))
    sw.handle_event(OWNEvent.parse("*1*1*00##"))
    sw.handle_event(OWNEvent.parse("*1*1*#1##"))
    sw.handle_event(OWNEvent.parse("*1*1*07#4#01##"))
    sw._attr_is_on = False
    assert sw.actuator_locked is True

    # Offline-unlocked physical actuation (*1*1*07##, False -> True) self-heals actuator_locked=False!
    sw.handle_event(OWNEvent.parse("*1*1*07##"))
    await hass.async_block_till_done()
    assert sw.is_on is True
    assert sw.actuator_locked is False
    assert hass.states.get("switch.relay_07").attributes["actuator_locked"] is False

    # Routed switch & light _is_own_point_event edge cases (single-digit interface, non-digit interface, empty where)
    routed_sw = MyHOMESwitch(
        hass=hass,
        name="Routed 07",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="07#4#1",
        who="1",
        where="07",
        interface="1",
        device_class="switch",
        manufacturer="BTicino",
        model="F411/4",
        gateway=handler,
    )
    assert routed_sw._is_own_point_event(OWNEvent.parse("*1*1*07#4#01##")) is True
    assert routed_sw._is_own_point_event(OWNEvent.parse("*1*1*07#4#02##")) is False
    assert routed_sw._is_own_point_event(OWNEvent.parse("*1*1*07##")) is False
    assert routed_sw._is_own_point_event(OWNEvent.parse("*1*1*0##")) is False
    assert routed_sw._is_own_point_event(SimpleNamespace(who=13, where=None)) is False

    alpha_if_sw = MyHOMESwitch(
        hass=hass,
        name="Alpha Sw",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="07#4#a",
        who="1",
        where="07",
        interface="a",
        device_class="switch",
        manufacturer="BTicino",
        model="F411/4",
        gateway=handler,
    )
    assert (
        alpha_if_sw._is_own_point_event(
            SimpleNamespace(who=1, where="07", where_param=["4", "a"])
        )
        is True
    )
    assert (
        alpha_if_sw._is_own_point_event(
            SimpleNamespace(who=1, where="07", where_param=["4", "b"])
        )
        is False
    )

    alpha_if_light = MyHOMELight(
        hass=hass,
        name="Alpha Light",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="07#4#a",
        who="1",
        where="07",
        interface="a",
        dimmable=False,
        manufacturer="BTicino",
        model="Relay",
        gateway=handler,
    )
    assert alpha_if_light._is_own_point_event(OWNEvent.parse("*1*1*07#4#01##")) is False
    assert (
        alpha_if_light._is_own_point_event(
            SimpleNamespace(who=1, where="07", where_param=["4", "a"])
        )
        is True
    )
    assert alpha_if_light._is_own_point_event(SimpleNamespace(who=13, where=None)) is False

    probe_light = MyHOMELight(
        hass=hass,
        name="Probe Light",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="93",
        who="1",
        where="93",
        interface=None,
        dimmable=False,
        manufacturer="BTicino",
        model="Relay",
        gateway=handler,
        probe_dimmer_on_add=True,
    )
    probe_light.hass = hass
    probe_light.entity_id = "light.probe_93"
    await probe_light.async_added_to_hass()
    for remove_cb in list(probe_light._on_remove or ()):
        remove_cb()


async def test_locked_fine_and_restored_dimmer_coarse_preset_quantization_no_bounce(
    hass: HomeAssistant,
) -> None:
    """Verify all 4 coarse-preset quantization & RestoreEntity lock-preservation cases (#976)."""
    _entry, handler = _make_gateway(hass, model="F461")

    # --- Case (a): 22% -> *1*4*42## -> *#1*42*1*122*5## (stays 22%, 0 bounce, actuator_locked=True) ---
    light_42 = MyHOMELight(
        hass=hass,
        name="Dimmer 42",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="42",
        who="1",
        where="42",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=handler,
    )
    ev_42 = _attach_live_entity(hass, light_42, "light.dimmer_42")
    light_42.handle_event(OWNEvent.parse("*#1*42*1*122*5##"))
    handler._actuator_locks.observe(OWNMessage.parse("*14*0*42##"))
    await hass.async_block_till_done()
    assert light_42.actuator_locked is True
    assert light_42._attr_brightness_pct == 22

    ev_42.clear()
    handler.send_status_request.reset_mock()
    light_42._brightness_refresh_after = 0.0
    light_42.handle_event(OWNEvent.parse("*1*4*42##"))
    await hass.async_block_till_done()
    assert light_42._attr_brightness_pct == 22
    assert light_42.actuator_locked is True
    assert ev_42 == []
    assert [str(c.args[0]) for c in handler.send_status_request.await_args_list] == [
        "*#1*42*1##"
    ]

    light_42.handle_event(OWNEvent.parse("*#1*42*1*122*5##"))
    await hass.async_block_till_done()
    assert light_42._attr_brightness_pct == 22
    assert light_42.actuator_locked is True
    assert ev_42 == []

    # --- Case (b): 22% -> *1*4*42## -> *#1*42*1*120*5## (offline unlock to 20% self-heals!) ---
    light_42._brightness_refresh_after = 0.0
    light_42.handle_event(OWNEvent.parse("*1*4*42##"))
    assert light_42._attr_brightness_pct == 22
    assert light_42.actuator_locked is True
    light_42.handle_event(OWNEvent.parse("*#1*42*1*120*5##"))
    await hass.async_block_till_done()
    assert light_42._attr_brightness_pct == 20
    assert light_42.actuator_locked is False

    # --- Case (c): F461/F418 boundary 44% -> *1*7*44## (50% preset) -> *#1*44*1*144*5## ---
    light_44 = MyHOMELight(
        hass=hass,
        name="Dimmer 44",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="44",
        who="1",
        where="44",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=handler,
    )
    ev_44 = _attach_live_entity(hass, light_44, "light.dimmer_44")
    light_44.handle_event(OWNEvent.parse("*#1*44*1*144*5##"))
    handler._actuator_locks.observe(OWNMessage.parse("*14*0*44##"))
    await hass.async_block_till_done()
    ev_44.clear()

    light_44._brightness_refresh_after = 0.0
    light_44.handle_event(OWNEvent.parse("*1*7*44##"))
    await hass.async_block_till_done()
    assert light_44._attr_brightness_pct == 44
    assert light_44.actuator_locked is True
    assert ev_44 == []

    light_44.handle_event(OWNEvent.parse("*#1*44*1*144*5##"))
    await hass.async_block_till_done()
    assert light_44._attr_brightness_pct == 44
    assert light_44.actuator_locked is True
    assert ev_44 == []

    # --- Case (d): RestoreEntity ("brightness": 204 = 80%, "actuator_locked": True) + startup *1*9*55## ---
    handler._actuator_locks.clear()
    restored_55 = MyHOMELight(
        hass=hass,
        name="Restored Dimmer 55",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="55",
        who="1",
        where="55",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=handler,
    )
    ev_55 = _attach_live_entity(hass, restored_55, "light.restored_55")
    await restored_55.async_restore_last_state(
        State(
            "light.restored_55",
            "on",
            {
                "supported_color_modes": ["brightness"],
                "brightness": 204,
                "actuator_locked": True,
            },
        )
    )
    restored_55.async_write_ha_state()
    await hass.async_block_till_done()
    assert restored_55.actuator_locked is True
    assert restored_55._is_on_restored is True
    assert restored_55._exact_level_seen is False
    assert restored_55._attr_brightness_pct == 80

    ev_55.clear()
    handler.send_status_request.reset_mock()
    restored_55._brightness_refresh_after = 0.0
    restored_55.handle_event(OWNEvent.parse("*1*9*55##"))
    await hass.async_block_till_done()
    assert restored_55._attr_brightness_pct == 80
    assert restored_55.actuator_locked is True
    assert ev_55 == []
    assert [str(c.args[0]) for c in handler.send_status_request.await_args_list] == [
        "*#1*55*1##"
    ]

    # Item 7: Repeated coarse preset *1*9*55## BEFORE Dim 1 answers still holds restored 80%!
    restored_55._brightness_refresh_after = 0.0
    restored_55.handle_event(OWNEvent.parse("*1*9*55##"))
    await hass.async_block_till_done()
    assert restored_55._attr_brightness_pct == 80
    assert restored_55.actuator_locked is True
    assert ev_55 == []

    restored_55.handle_event(OWNEvent.parse("*#1*55*1*180*5##"))
    await hass.async_block_till_done()
    assert restored_55._attr_brightness_pct == 80
    assert restored_55.actuator_locked is True
    assert restored_55._exact_level_seen is True
    assert restored_55._brightness_restored is False
    assert ev_55 == []


async def test_dali_dim14_turn_on_passive_poll_plain_on_lock_guard_and_step1_cold_boot(
    hass: HomeAssistant,
) -> None:
    """Audit #977 items 1, 4a, 4b, 11: DALI Dim 14 OFF->ON, passive poll, plain ON lock, fade step 1."""
    _entry, handler = _make_gateway(
        hass,
        model="F461",
        options={CONF_TRANSITION_MODE: TRANSITION_MODE_SOFTWARE},
    )
    dali = MyHOMELight(
        hass=hass,
        name="DALI DT8",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="25#4#02",
        who="1",
        where="25",
        interface="02",
        dimmable=True,
        manufacturer="BTicino",
        model="F429",
        gateway=handler,
        color_temp=True,
        rgb=True,
    )
    _attach_live_entity(hass, dali, "light.dali_dt8")

    # 1. Item 1: Passive Dim 14 status report while OFF (and while locked OFF) keeps is_on=False!
    dali.handle_event(OWNEvent.parse("*1*0*25#4#02##"))
    dali._last_brightness_pct = 65
    handler._actuator_locks.observe(OWNMessage.parse("*14*0*25#4#02##"))
    assert dali.is_on is False
    assert dali.actuator_locked is True
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*419##"))
    assert dali.is_on is False
    assert dali.color_temp == 419
    assert dali.actuator_locked is True

    # Item 11: Plain async_turn_on() while known locked sends *1*1*W## and skips async_update()!
    handler.send.reset_mock()
    handler.send_status_request.reset_mock()
    await dali.async_turn_on()
    assert [str(c.args[0]) for c in handler.send.await_args_list] == ["*1*1*25#4#02##"]
    handler.send_status_request.assert_not_called()

    # Unlock DALI and call turn_on(color_temp=169) from OFF: issue_288_f461_dali_dt8 echoes ONLY Dim 14!
    handler._actuator_locks.observe(OWNMessage.parse("*14*1*25#4#02##"))
    dali._attr_brightness_pct = 0
    await dali.async_turn_on(**{ATTR_COLOR_TEMP: 169})
    assert dali.is_on is False  # non-optimistic before echo
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*169##"))
    assert dali.is_on is True
    assert dali.color_temp == 169
    assert dali._attr_brightness_pct == 65

    # Cold-boot unknown (is_on=None) turn_on(color_temp=220) + enqueue rollback coverage
    dali._attr_is_on = None
    handler.send = AsyncMock(side_effect=RuntimeError("tx err"))
    with pytest.raises(RuntimeError):
        await dali.async_turn_on(**{ATTR_COLOR_TEMP: 220})
    assert dali._pending_cmd_color_temp is None
    handler.send = AsyncMock()

    await dali.async_turn_on(**{ATTR_COLOR_TEMP: 220})
    assert dali._pending_cmd_color_temp is not None
    handler.send = AsyncMock(side_effect=RuntimeError("off err"))
    with pytest.raises(RuntimeError):
        await dali.async_turn_off()
    assert dali._pending_cmd_color_temp is not None
    handler.send = AsyncMock()
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*220##"))
    assert dali.is_on is True

    # 2. Item 4b: Cold-boot locked dimmer at 20% with gentle fade (step 1 = 22%, |22 - 20| <= 5):
    # unchanged *1*1*55##, same-bucket *1*4*55##, and unchanged *#1*55*1*120*5## MUST NOT confirm step 1!
    dim_55 = MyHOMELight(
        hass=hass,
        name="Dimmer 55",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="55",
        who="1",
        where="55",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=handler,
    )
    _attach_live_entity(hass, dim_55, "light.dim_55_step1")
    assert dim_55._fade_engine.first_echo_timeout >= 2.5  # Item 4a: > 1.5s LightEchoWatchdog!
    dim_55._fade_engine.first_echo_timeout = 0.05
    dim_55.handle_event(OWNEvent.parse("*#1*55*1*120*5##"))
    assert dim_55.actuator_locked is None
    assert dim_55._attr_brightness_pct == 20

    handler.send_fade_step.reset_mock()
    await dim_55.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 24, ATTR_TRANSITION: 0.3})
    fade_task = dim_55._fade_task
    assert fade_task is not None
    await asyncio.sleep(0)
    dim_55.handle_event(OWNEvent.parse("*1*1*55##"))  # already ON -> ignored for step-1 confirm
    dim_55.handle_event(OWNEvent.parse("*1*4*55##"))  # 20% bucket -> ignored for step-1 confirm
    dim_55.handle_event(OWNEvent.parse("*#1*55*1*120*5##"))  # unchanged 20% == fade_start -> cancels!
    await fade_task
    assert len(handler.send_fade_step.await_args_list) == 1
    assert dim_55._attr_brightness_pct == 20

    # Genuine coarse preset jump (*1*8*55## = 60%) or OFF->ON *1*1*55## DOES confirm step 1!
    dim_55._fade_engine.first_echo_timeout = 0.5
    await dim_55.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 60, ATTR_TRANSITION: 0.2})
    ft2 = dim_55._fade_task
    assert ft2 is not None
    await asyncio.sleep(0)
    dim_55.handle_event(OWNEvent.parse("*1*8*55##"))
    await ft2
    assert dim_55._attr_brightness_pct == 60

    dim_55.handle_event(OWNEvent.parse("*1*0*55##"))
    assert dim_55.is_on is False
    await dim_55.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 50, ATTR_TRANSITION: 0.2})
    ft3 = dim_55._fade_task
    assert ft3 is not None
    await asyncio.sleep(0)
    dim_55.handle_event(OWNEvent.parse("*1*1*55##"))
    await ft3
    assert dim_55.is_on is True

    # Coarse-only dimmer (_coarse_speed_seen=True): same bucket *1*4*55## (20%) is ignored,
    # while next bucket *1*5*55## (30% != 20%) confirms step 1!
    dim_55.handle_event(OWNEvent.parse("*#1*55*1*120*240##"))
    assert dim_55._coarse_speed_seen is True
    assert dim_55._attr_brightness_pct == 20
    await dim_55.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 40, ATTR_TRANSITION: 0.2})
    ft4 = dim_55._fade_task
    assert ft4 is not None
    await asyncio.sleep(0)
    dim_55.handle_event(OWNEvent.parse("*1*4*55##"))  # frozen 20% bucket -> not confirmed yet
    dim_55.handle_event(OWNEvent.parse("*1*5*55##"))  # moved to 30% bucket -> confirms!
    await ft4
    assert dim_55._attr_brightness_pct == 40

    # Pre-09W29 F418 coarse ladder 24% (speed=240) cold-boot locked: backward quantized *1*4*55## (20% < 24%)
    # MUST NOT confirm an upward fade 24% -> 80%!
    dim_55._fade_engine.first_echo_timeout = 0.05
    dim_55.handle_event(OWNEvent.parse("*#1*55*1*124*240##"))
    assert dim_55._attr_brightness_pct == 24
    await dim_55.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80, ATTR_TRANSITION: 0.3})
    ft5 = dim_55._fade_task
    assert ft5 is not None
    await asyncio.sleep(0)
    dim_55.handle_event(OWNEvent.parse("*1*4*55##"))  # 20% < 24% -> rejected!
    await ft5
    assert dim_55._attr_brightness_pct == 24

    # Fine dimmer at 44% fading UP 44% -> 80%: quantized *1*7*55## (50%, +6 pp <= 15) refreshes Dim 1 without confirming!
    dim_55._coarse_speed_seen = False
    dim_55._brightness_refresh_after = 0.0
    dim_55.handle_event(OWNEvent.parse("*#1*55*1*144*5##"))
    assert dim_55._exact_level_seen is True and dim_55._attr_brightness_pct == 44
    handler.send_status_request.reset_mock()
    await dim_55.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80, ATTR_TRANSITION: 0.3})
    ft6 = dim_55._fade_task
    assert ft6 is not None
    await asyncio.sleep(0)
    dim_55.handle_event(OWNEvent.parse("*1*7*55##"))  # +6 pp <= 15 -> polls Dim 1, does not confirm
    await ft6
    assert dim_55._attr_brightness_pct == 44
    assert any(str(c.args[0]) == "*#1*55*1##" for c in handler.send_status_request.await_args_list)

    # Downward fade 80% -> 74% (step 1 = 79%): frozen/upward Dim 1 (80%, 82%) is rejected while downward Dim 1 (75%) confirms!
    dim_55._fade_engine.first_echo_timeout = 0.5
    dim_55.handle_event(OWNEvent.parse("*#1*55*1*180*5##"))
    assert dim_55._attr_brightness_pct == 80
    await dim_55.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 74, ATTR_TRANSITION: 0.3})
    ft7 = dim_55._fade_task
    assert ft7 is not None
    await asyncio.sleep(0)
    dim_55.handle_event(OWNEvent.parse("*#1*55*1*180*5##"))  # unchanged 80% -> ignored
    dim_55.handle_event(OWNEvent.parse("*#1*55*1*182*5##"))  # wrong direction 82% > 80% -> ignored
    dim_55.handle_event(OWNEvent.parse("*#1*55*1*175*5##"))  # progressed down 75% < 80% -> confirms!
    await ft7
    assert dim_55._attr_brightness_pct == 74

    # DALI Dim 14 written-future anchoring: socket NACK rollback, stale callback ignore, write_ts deadline,
    # and queued Dim 14 surviving an earlier pre-write *1*0*W## echo!
    loop = asyncio.get_running_loop()
    dali.handle_event(OWNEvent.parse("*1*0*25#4#02##"))
    assert dali.is_on is False

    fut_stale: asyncio.Future[float] = loop.create_future()
    fut_nack: asyncio.Future[float] = loop.create_future()
    handler.send = AsyncMock(side_effect=[fut_stale, fut_nack])
    await dali.async_turn_on(**{ATTR_COLOR_TEMP: 240})
    await dali.async_turn_on(**{ATTR_COLOR_TEMP: 250})
    fut_stale.set_exception(RuntimeError("stale nack"))  # superseded token -> ignored
    await asyncio.sleep(0)
    assert dali._pending_cmd_color_temp is not None
    fut_nack.set_exception(RuntimeError("active nack"))  # active token -> clears _pending_cmd_color_temp!
    await asyncio.sleep(0)
    assert dali._pending_cmd_color_temp is None
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*250##"))
    assert dali.is_on is False

    fut_off: asyncio.Future[float] = loop.create_future()
    fut_ct: asyncio.Future[float] = loop.create_future()
    dali.handle_event(OWNEvent.parse("*1*1*25#4#02##"))
    assert dali.is_on is True
    handler.send = AsyncMock(side_effect=[fut_off, fut_ct])
    await dali.async_turn_off()
    await dali.async_turn_on(**{ATTR_COLOR_TEMP: 260})
    fut_off.set_result(loop.time())
    await asyncio.sleep(0)
    dali.handle_event(OWNEvent.parse("*1*0*25#4#02##"))  # pre-write *1*0* does NOT wipe queued Dim 14!
    assert dali.is_on is False and dali._pending_cmd_color_temp is not None
    fut_ct.set_result(loop.time() + 10.0)  # anchors deadline on socket write_ts
    await asyncio.sleep(0)
    assert dali._pending_cmd_color_temp[1] > loop.time() + 10.0
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*260##"))
    assert dali.is_on is True and dali.color_temp == 260
    handler.send = AsyncMock()


async def test_issue_976_archived_mh200n_trace_replay_on_ownd_202(
    hass: HomeAssistant,
) -> None:
    """Replay tests/fixtures/traces/issue_976/myhome_trace_MH200N_all_2026-11-20T12-17-07.json on OWNd 2.0.2."""
    trace_path = (
        Path(__file__).parent
        / "fixtures"
        / "traces"
        / "issue_976"
        / "myhome_trace_MH200N_all_2026-11-20T12-17-07.json"
    )
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert len(payload["frames"]) == 49

    _entry, handler = _make_gateway(hass, model="MH200N")
    dimmer_55 = MyHOMELight(
        hass=hass,
        name="Dimmer 55",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="55",
        who="1",
        where="55",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=handler,
    )
    ev_55 = _attach_live_entity(hass, dimmer_55, "light.mh200n_dimmer_55")
    _entry.runtime_data.entities.setdefault("light", {})["55"] = dimmer_55

    locked_phase_changes = 0
    for idx, entry_frame in enumerate(payload["frames"]):
        raw = entry_frame["raw"]
        direction = entry_frame["direction"]
        if direction == "tx":
            parsed_cmd = OWNCommand.parse(raw)
            if getattr(parsed_cmd, "who", None) == 14:
                handler._actuator_locks.observe(parsed_cmd)
            elif getattr(parsed_cmd, "who", None) == 1 and getattr(parsed_cmd, "where", None) == "55":
                if 26 <= idx <= 34:
                    ev_55.clear()
                    dim_val = getattr(parsed_cmd, "brightness", None)
                    if isinstance(dim_val, int):
                        await dimmer_55.async_turn_on(**{ATTR_BRIGHTNESS_PCT: dim_val})
                    elif getattr(parsed_cmd, "what", None) == 0:
                        await dimmer_55.async_turn_off()
                    await hass.async_block_till_done()
                    locked_phase_changes += len(ev_55)
        else:
            parsed_rx = OWNMessage.parse(raw)
            await handler._process_message(parsed_rx)
            if getattr(parsed_rx, "who", None) == 1 and getattr(parsed_rx, "where", None) == "55":
                dimmer_55.handle_event(parsed_rx)
            await hass.async_block_till_done()

    assert locked_phase_changes == 0
    assert dimmer_55.actuator_locked is False
    assert dimmer_55.is_on is False
    assert dimmer_55._attr_brightness_pct == 0


async def test_dali_combined_ct_brightness_f422_nack_poll_guard_and_coarse_quantized_fade(
    hass: HomeAssistant,
) -> None:
    """Verify combined Dim 14+Dim 1 zero spike & F422 NACK rollback, cold-boot CT poll guard, and coarse-start fade."""
    _entry, handler = _make_gateway(hass, model="F461")
    dali = MyHOMELight(
        hass=hass,
        name="DALI 25",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="25#4#02",
        who="1",
        where="25",
        interface="02",
        dimmable=True,
        manufacturer="BTicino",
        model="DALI",
        gateway=handler,
    )
    _attach_live_entity(hass, dali, "light.dali_25_boost")
    dali._promote_color_mode(ColorMode.COLOR_TEMP)
    dali._attr_is_on = False
    dali._attr_brightness_pct = 0
    dali._last_brightness_pct = 100

    # 1. Combined turn_on(color_temp_kelvin=5000, brightness_pct=30):
    #    - Pre-write *1*0*W## preserves queued Dim 14 and Dim 1 tokens
    #    - No intermediate 100% brightness spike when Dim 14 echoes ahead of Dim 1
    #    - F422 FIFO NACK on Dim 1 clears _pending_cmd_brightness_pct and restores _last_brightness_pct
    f_ct = hass.loop.create_future()
    f_br = hass.loop.create_future()
    seq = iter([f_ct, f_br])
    handler.send = AsyncMock(side_effect=lambda _cmd: next(seq))
    await dali.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 5000, ATTR_BRIGHTNESS_PCT: 30})
    dali.handle_event(OWNEvent.parse("*1*0*25#4#02##"))
    assert dali._pending_cmd_color_temp is not None
    assert dali._pending_cmd_brightness_pct is not None
    f_ct.set_result(time.monotonic())
    await asyncio.sleep(0)
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*200##"))
    assert dali.is_on is True
    assert dali._attr_brightness_pct == 0  # zero 100% spike while Dim 1 is still in flight
    f_br.cancel()
    await asyncio.sleep(0)
    assert dali._pending_cmd_brightness_pct is None
    assert dali._attr_brightness_pct == 100

    # Combined send-error rollback restores previous _pending_cmd_brightness_pct
    handler.send = AsyncMock(side_effect=RuntimeError("socket down"))
    with pytest.raises(RuntimeError):
        await dali.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 3500, ATTR_BRIGHTNESS: 128})
    assert dali._pending_cmd_color_temp is None
    assert dali._pending_cmd_brightness_pct is None

    # 2. Cold-boot locked-OFF DALI (ct=250): passive status-request Dim 14 reply does NOT flip light ON
    dali._attr_is_on = False
    dali._attr_color_temp = 250
    done_fut = hass.loop.create_future()
    done_fut.set_result(time.monotonic())
    handler.send = AsyncMock(return_value=done_fut)
    await dali.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4050})
    poll_ct = OWNEvent.parse("*#1*25#4#02*14*250##")
    setattr(poll_ct, "_from_status_request", True)
    dali.handle_event(poll_ct)
    assert dali.is_on is False

    # 3. Cold-boot coarse-quantized fine dimmer (*1*4*55## = 20%, physically locked at 23%):
    #    frozen *#1*55*1*123*5## updates start level to 23% without running optimistic fade to 80%
    _e2, gw2 = _make_gateway(hass, model="F461")
    step_fut = hass.loop.create_future()
    step_fut.set_result(time.monotonic())
    gw2.send_fade_step = AsyncMock(return_value=step_fut)
    dim55 = MyHOMELight(
        hass=hass,
        name="Dim 55",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="55",
        who="1",
        where="55",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=gw2,
    )
    _attach_live_entity(hass, dim55, "light.dim_55_coarse_quant")
    dim55.handle_event(OWNEvent.parse("*1*4*55##"))
    dim55._fade_engine.first_echo_timeout = 0.05
    await dim55.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80, ATTR_TRANSITION: 2.5})
    assert dim55._fade_task is not None
    await asyncio.sleep(0)
    dim55.handle_event(OWNEvent.parse("*#1*55*1*123*5##"))
    await dim55._fade_task
    assert dim55._attr_brightness_pct == 23
    assert gw2.send_fade_step.await_count == 1
