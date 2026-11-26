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
        assert gateway_confirm._commanded_on(OWNCommand.parse("*#1*25*#14*1##")) is None
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

    def _fresh_step_fut(_cmd: Any = None) -> asyncio.Future[float]:
        fut: asyncio.Future[float] = hass.loop.create_future()
        fut.set_result(time.monotonic())
        return fut

    gw2.send_fade_step = AsyncMock(side_effect=_fresh_step_fut)
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
    assert dim55._last_brightness_pct == 23
    assert gw2.send_fade_step.await_count == 1

    # 4. Unlocked dimmer after HA restart (not was_fine) with 1 pp DALI 8-bit arc rounding
    #    (speed=0, unsolicited) confirms step 1 and completes fade instead of false abort!
    dim_round = MyHOMELight(
        hass=hass,
        name="Dim 56",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="56",
        who="1",
        where="56",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="DALI",
        gateway=gw2,
    )
    _attach_live_entity(hass, dim_round, "light.dim_56_round")
    dim_round.handle_event(OWNEvent.parse("*1*4*56##"))  # 20% coarse, was_fine is False
    dim_round._fade_engine.first_echo_timeout = 0.05
    await dim_round.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 30, ATTR_TRANSITION: 0.3})
    assert dim_round._fade_task is not None
    await asyncio.sleep(0)
    p_step = dim_round._fade_engine.pending_first_step_pct
    assert isinstance(p_step, int) and p_step > 21
    # Unsolicited speed=0 echo 1 pp off pending_step confirms step 1!
    dim_round.handle_event(OWNEvent.parse(f"*#1*56*1*{100 + p_step - 1}*0##"))
    await dim_round._fade_task
    assert dim_round._attr_brightness_pct == 30

    # Locked dimmer above 60% (*1*9*57## = 75%, true physical level 88%, delta 13 in 11..15)
    # and exact pending_step collision on non-instant speed stay guarded!
    dim_hi = MyHOMELight(
        hass=hass,
        name="Dim 57",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="57",
        who="1",
        where="57",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=gw2,
    )
    _attach_live_entity(hass, dim_hi, "light.dim_57_hi")
    dim_hi.handle_event(OWNEvent.parse("*1*9*57##"))  # 75% coarse
    dim_hi._brightness_restored = True
    dim_hi._fade_engine.first_echo_timeout = 0.05
    await dim_hi.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 95, ATTR_TRANSITION: 0.2})
    assert dim_hi._fade_task is not None
    await asyncio.sleep(0)
    dim_hi.handle_event(OWNEvent.parse("*#1*57*1*188*2##"))
    await dim_hi._fade_task
    assert dim_hi._attr_brightness_pct == 88
    assert dim_hi._brightness_restored is False
    assert dim_hi._last_brightness_pct == 88

    # 5. Cold-boot DALI (prev_color_temp is None): passive poll Dim 14 reply neither flips is_on True
    #    nor wipes a queued _pending_cmd_color_temp before the real write echo arrives!
    dali_cold = MyHOMELight(
        hass=hass,
        name="DALI Cold",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="26#4#02",
        who="1",
        where="26",
        interface="02",
        dimmable=True,
        manufacturer="BTicino",
        model="DALI",
        gateway=handler,
    )
    _attach_live_entity(hass, dali_cold, "light.dali_26_cold")
    dali_cold._promote_color_mode(ColorMode.COLOR_TEMP)
    dali_cold._attr_is_on = False
    dali_cold._attr_color_temp = None
    f_cold = hass.loop.create_future()
    handler.send = AsyncMock(return_value=f_cold)
    await dali_cold.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})
    poll_cold = OWNEvent.parse("*#1*26#4#02*14*250##")
    setattr(poll_cold, "_from_status_request", True)
    dali_cold.handle_event(poll_cold)
    assert dali_cold.is_on is False
    assert dali_cold._pending_cmd_color_temp is not None
    f_cold.set_result(time.monotonic())
    await asyncio.sleep(0)
    dali_cold.handle_event(OWNEvent.parse("*#1*26#4#02*14*250##"))
    assert dali_cold.is_on is True

    # 6. Send-failure rollback on turn_on_timed, HS, combined CT + Dim 1 raise, combined CT +
    #    maybe_instant_brightness raise/cancel, and combined CT + software_stepped step-1 abort!
    dali._promote_color_mode(ColorMode.HS)
    dali._promote_color_mode(ColorMode.COLOR_TEMP)
    dali._pending_cmd_brightness_pct = None
    handler.send = AsyncMock(side_effect=RuntimeError("timed send fail"))
    with pytest.raises(RuntimeError):
        await dali.async_turn_on_timed(duration=10, brightness_pct=45)
    assert dali._pending_cmd_brightness_pct is None
    with pytest.raises(RuntimeError):
        await dali.async_turn_on(**{ATTR_HS_COLOR: (120.0, 80.0), ATTR_BRIGHTNESS_PCT: 50})
    assert dali._pending_cmd_brightness_pct is None
    # v_int == 0 HS branch
    done_hs = hass.loop.create_future()
    done_hs.set_result(time.monotonic())
    handler.send = AsyncMock(return_value=done_hs)
    await dali.async_turn_on(**{ATTR_HS_COLOR: (120.0, 80.0), ATTR_BRIGHTNESS_PCT: 0})

    # Combined CT + native Dim 1 where Dim 14 succeeds & echoes ON while Dim 1 raises synchronously
    dali._attr_is_on = False
    dali._attr_brightness_pct = 0
    dali._last_brightness_pct = 65
    ct_calls = 0

    async def _ct_ok_then_dim1_fail(cmd: Any) -> Any:
        nonlocal ct_calls
        ct_calls += 1
        if ct_calls == 1:
            fut = hass.loop.create_future()
            fut.set_result(time.monotonic())
            dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*220##"))
            return fut
        raise RuntimeError("dim1 send failed")

    handler.send = _ct_ok_then_dim1_fail  # type: ignore[assignment]
    with pytest.raises(RuntimeError):
        await dali.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4500, ATTR_BRIGHTNESS_PCT: 40})
    assert dali._pending_cmd_brightness_pct is None
    assert dali.is_on is True
    assert dali._attr_brightness_pct == 65

    # Combined CT + software_stepped maybe_instant_brightness (start=0, target=1): raise & cancel fallback
    dali._attr_is_on = False
    dali._attr_brightness_pct = 0
    dali._last_brightness_pct = 70
    ct_calls = 0
    with pytest.raises(RuntimeError):
        await dali.async_turn_on(
            **{ATTR_COLOR_TEMP_KELVIN: 4500, ATTR_BRIGHTNESS_PCT: 1, ATTR_TRANSITION: 2.0}
        )
    assert dali._pending_cmd_brightness_pct is None
    assert dali._attr_brightness_pct == 70

    dali._attr_is_on = False
    dali._attr_brightness_pct = 0
    f_ct_inst = hass.loop.create_future()
    f_ct_inst.set_result(time.monotonic())
    f_br_inst = hass.loop.create_future()
    seq_inst = iter([f_ct_inst, f_br_inst])
    handler.send = AsyncMock(side_effect=lambda _c: next(seq_inst))
    await dali.async_turn_on(
        **{ATTR_COLOR_TEMP_KELVIN: 4500, ATTR_BRIGHTNESS_PCT: 1, ATTR_TRANSITION: 2.0}
    )
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*210##"))
    assert dali._attr_brightness_pct == 0
    f_br_inst.cancel()
    await asyncio.sleep(0)
    assert dali._attr_brightness_pct == 70

    # Combined CT + software_stepped start_fade(0 -> 30) aborting at step 1 restores _last_brightness_pct
    dali._attr_is_on = False
    dali._attr_brightness_pct = 0
    dali._last_brightness_pct = 72
    dali._fade_engine.first_echo_timeout = 0.03
    f_ct_fade = hass.loop.create_future()
    f_ct_fade.set_result(time.monotonic())
    f_step_fade = hass.loop.create_future()
    f_step_fade.set_result(time.monotonic())
    handler.send = AsyncMock(return_value=f_ct_fade)
    handler.send_fade_step = AsyncMock(return_value=f_step_fade)
    await dali.async_turn_on(
        **{ATTR_COLOR_TEMP_KELVIN: 4500, ATTR_BRIGHTNESS_PCT: 30, ATTR_TRANSITION: 2.0}
    )
    assert dali._fade_task is not None
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*205##"))
    assert dali.is_on is True
    assert dali._attr_brightness_pct == 0
    await dali._fade_task
    await asyncio.sleep(0)
    assert dali._attr_brightness_pct == 72

    # 7. SoftwareFadeEngine stale fade #1 unwinding after fade #2 armed step 1 never clobbers fade #2!
    slow_cancel_gate = asyncio.Event()
    step_calls = 0

    async def _slow_step1_send(_cmd: Any) -> Any:
        nonlocal step_calls
        step_calls += 1
        fut = hass.loop.create_future()
        fut.set_result(time.monotonic())
        if step_calls == 1:
            try:
                await slow_cancel_gate.wait()
            except asyncio.CancelledError:
                await slow_cancel_gate.wait()
                raise
        return fut

    gw2.send_fade_step = _slow_step1_send  # type: ignore[assignment]
    dim55.handle_event(OWNEvent.parse("*#1*55*1*120*0##"))
    dim55._fade_engine.first_echo_timeout = 0.5
    await dim55.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 60, ATTR_TRANSITION: 0.4})
    fade1_task = dim55._fade_task
    assert fade1_task is not None
    await asyncio.sleep(0)
    # Start fade #2 while fade #1 ignores cancellation past cancel_fade_robustly's 0.15s shield timeout
    await dim55.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80, ATTR_TRANSITION: 0.2})
    fade2_task = dim55._fade_task
    assert fade2_task is not None and fade2_task is not fade1_task
    await asyncio.sleep(0)
    assert dim55._fade_engine._step1_echo_event is not None
    assert dim55._fade_engine.pending_first_step_pct is not None
    # Now let fade #1 unwind its finally blocks -> fade #2's step-1 state stays intact!
    slow_cancel_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await fade1_task
    assert dim55._fade_engine._step1_echo_event is not None
    assert dim55._fade_engine.pending_first_step_pct is not None
    dim55.handle_event(OWNEvent.parse("*#1*55*1*150*0##"))
    await fade2_task
    assert dim55._attr_brightness_pct == 80

    # 8. Warm Standby Dim 14 echo runs _bridge_to_primary BEFORE echo_watchdog.status_seen
    #    so Primary's light entity flips is_on=True first and zero spurious (where, None) poll is armed!
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    from custom_components.myhome.const import (
        CONF_BUS_TOPOLOGY,
        CONF_GATEWAY_ROLE,
        CONF_PRIMARY_GATEWAY,
        ROLE_PRIMARY,
        ROLE_STANDBY,
        TOPOLOGY_SHARED,
    )

    pri_mac = "00:03:50:bb:77:01"
    stb_mac = "00:03:50:bb:77:02"
    pri_e = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.71", CONF_PORT: 20000, CONF_MAC: pri_mac, CONF_NAME: "F461"},
        options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY},
        unique_id=pri_mac,
    )
    pri_e.add_to_hass(hass)
    pri_gw = MyHOMEGatewayHandler(hass, pri_e)
    pri_gw.is_connected = False
    pri_gw._available = False
    pri_e.runtime_data = MyHOMERuntimeData(gateway=pri_gw)

    stb_e = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.72", CONF_PORT: 20000, CONF_MAC: stb_mac, CONF_NAME: "F461"},
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_STANDBY,
            CONF_PRIMARY_GATEWAY: pri_mac,
        },
        unique_id=stb_mac,
    )
    stb_e.add_to_hass(hass)
    stb_gw = MyHOMEGatewayHandler(hass, stb_e)
    stb_gw.is_connected = True
    stb_gw._available = True
    stb_gw._event_session_ready.set()
    stb_e.runtime_data = MyHOMERuntimeData(gateway=stb_gw)

    pri_dali = MyHOMELight(
        hass=hass,
        name="Primary DALI 27",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="27#4#02",
        who="1",
        where="27",
        interface="02",
        dimmable=True,
        manufacturer="BTicino",
        model="DALI",
        gateway=pri_gw,
    )
    _attach_live_entity(hass, pri_dali, "light.pri_dali_27")
    pri_dali._promote_color_mode(ColorMode.COLOR_TEMP)
    pri_dali._attr_is_on = False
    pri_e.runtime_data.entities["light"] = {"27#4#02": pri_dali}
    unsub_pri = async_dispatcher_connect(
        hass,
        f"myhome_message_{pri_mac}",
        callback(lambda msg: pri_dali.handle_event(msg) if isinstance(msg, OWNEvent) else None),
    )
    try:
        done_stb = hass.loop.create_future()
        w_ts = time.monotonic()
        done_stb.set_result(w_ts)
        stb_gw.send = AsyncMock(return_value=done_stb)  # type: ignore[method-assign]
        await pri_dali.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})
        stb_gw._echo_watchdog.command_acknowledged(
            OWNCommand.parse("*#1*27#4#02*#14*250##"), w_ts
        )
        assert ("27#4#02", 14) in stb_gw._echo_watchdog._watches
        await stb_gw._process_message(OWNMessage.parse("*#1*27#4#02*14*250##"))
        assert pri_dali.is_on is True
        assert stb_gw._echo_watchdog._watches == {}
    finally:
        unsub_pri()
        stb_gw._echo_watchdog.cancel_all()

    # 9. Real physical F418/F418U2 dimmer after HA restart (RestoreEntity, not was_fine,
    #    was_coarse_preset is False) ALWAYS echoes stored hardware speed != 0 (e.g. *5 in
    #    issue_976 MH200N trace lines 571-607): step 1 confirms immediately (including 1 pp
    #    DALI rounding) instead of aborting unlocked fades!
    from homeassistant.core import State

    f418_restored = MyHOMELight(
        hass=hass,
        name="F418 Restored 41",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="41",
        who="1",
        where="41",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=handler,
    )
    _attach_live_entity(hass, f418_restored, "light.f418_restored_41")
    await f418_restored.async_restore_last_state(
        State(
            "light.f418_restored_41",
            "on",
            {"brightness": 102, "supported_color_modes": ["brightness"], "color_mode": "brightness"},
        )
    )
    assert f418_restored.is_on is True
    assert f418_restored._exact_level_seen is False
    assert f418_restored._brightness_from_coarse_preset is False
    f418_restored._fade_engine.first_echo_timeout = 0.05

    def _fresh_fut(_cmd: Any = None) -> asyncio.Future[float]:
        fut: asyncio.Future[float] = hass.loop.create_future()
        fut.set_result(time.monotonic())
        return fut

    handler.send = AsyncMock(side_effect=_fresh_fut)
    handler.send_fade_step = AsyncMock(side_effect=_fresh_fut)
    await f418_restored.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80, ATTR_TRANSITION: 0.25})
    assert f418_restored._fade_task is not None
    await asyncio.sleep(0)
    step1_f418 = f418_restored._fade_engine.pending_first_step_pct
    assert isinstance(step1_f418, int) and step1_f418 > 40
    # Unsolicited F418 echo with hardware speed=5 != 0 AND 1 pp rounding confirms step 1!
    f418_restored.handle_event(OWNEvent.parse(f"*#1*41*1*{100 + step1_f418 - 1}*5##"))
    await f418_restored._fade_task
    assert f418_restored._attr_brightness_pct == 80

    # 10. Cold-boot coarse preset (*1*4*42## -> 20%) with exact pending_step == reported_lvl (23%)
    #     when *#1*42*1## poll is in-flight in _command_pool._in_flight_messages (*99*1## overtake)
    #     AND cold-boot DALI *#1*28*14## in-flight poll overtake via _is_from_poll!
    dim42 = MyHOMELight(
        hass=hass,
        name="Dim 42",
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
    _attach_live_entity(hass, dim42, "light.dim_42_poll_race")
    dim42.handle_event(OWNEvent.parse("*1*4*42##"))  # 20% coarse -> _brightness_from_coarse_preset=True
    dim42._fade_engine.first_echo_timeout = 0.05
    await dim42.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 29, ATTR_TRANSITION: 0.25})
    assert dim42._fade_task is not None
    await asyncio.sleep(0)
    exact_step = dim42._fade_engine.pending_first_step_pct
    assert isinstance(exact_step, int)
    handler._command_pool._in_flight_messages.append(OWNCommand.parse("*#1*42*1##"))
    try:
        dim42.handle_event(OWNEvent.parse(f"*#1*42*1*{100 + exact_step}*0##"))
        await dim42._fade_task
    finally:
        handler._command_pool._in_flight_messages.clear()
    assert dim42._attr_brightness_pct == exact_step
    assert dim42._brightness_from_coarse_preset is False

    dali_cold._attr_is_on = False
    dali_cold._attr_color_temp = None
    f_cold2 = hass.loop.create_future()
    handler.send = AsyncMock(return_value=f_cold2)
    await dali_cold.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})
    handler._command_pool._in_flight_messages.append(
        OWNCommand.parse("*#1*26#4#02*14##")
    )
    try:
        # Unmarked *99*1## event arriving ahead of command-session *#*1## while poll is in flight
        dali_cold.handle_event(OWNEvent.parse("*#1*26#4#02*14*250##"))
    finally:
        handler._command_pool._in_flight_messages.clear()
    assert dali_cold.is_on is False
    assert dali_cold._pending_cmd_color_temp is not None
    f_cold2.cancel()
    await asyncio.sleep(0)

    # 11. Combined CT + brightness edge cases:
    #   (a) _cancel_fade_robustly raising CancelledError rolls back _pending_cmd_brightness_pct & restores _last_brightness_pct
    #   (b) handled_inst with written_inst is None (already ON at target brightness) restores prev_pending_br (no phantom token)
    #   (c) handle_actuator_lock(True) during step-1 combined CT + fade restores _last_brightness_pct synchronously
    #   (d) WHAT 19 (is_no_load) cancels an active software_stepped fade
    dali._attr_is_on = False
    dali._attr_brightness_pct = 0
    dali._last_brightness_pct = 68
    handler.send = AsyncMock(side_effect=_fresh_fut)
    with patch.object(
        dali,
        "_cancel_fade_robustly",
        AsyncMock(
            side_effect=lambda: (
                dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*222##")),
                (_ for _ in ()).throw(asyncio.CancelledError()),
            )[1]
        ),
    ):
        with pytest.raises(asyncio.CancelledError):
            await dali.async_turn_on(
                **{ATTR_COLOR_TEMP_KELVIN: 4500, ATTR_BRIGHTNESS_PCT: 50, ATTR_TRANSITION: 2.0}
            )
    assert dali._pending_cmd_brightness_pct is None
    assert dali.is_on is True
    assert dali._attr_brightness_pct == 68

    # (b) Already ON at 50%: combined CT + brightness_pct=50 + transition=2.0 sends zero Dim 1 frame -> no phantom token!
    dali._apply_brightness_state(50, is_on=True)
    dali._pending_cmd_brightness_pct = None
    await dali.async_turn_on(
        **{ATTR_COLOR_TEMP_KELVIN: 3500, ATTR_BRIGHTNESS_PCT: 50, ATTR_TRANSITION: 2.0}
    )
    assert dali._pending_cmd_brightness_pct is None

    # (c) handle_actuator_lock(True) mid-step-1 combined CT + fade restores _last_brightness_pct synchronously
    dali._actuator_locked = False
    dali._attr_is_on = False
    dali._attr_brightness_pct = 0
    dali._last_brightness_pct = 74
    dali._fade_engine.first_echo_timeout = 5.0
    handler.send_fade_step = AsyncMock(side_effect=_fresh_fut)
    await dali.async_turn_on(
        **{ATTR_COLOR_TEMP_KELVIN: 4000, ATTR_BRIGHTNESS_PCT: 40, ATTR_TRANSITION: 2.0}
    )
    await asyncio.sleep(0)
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*250##"))
    assert dali.is_on is True
    assert dali._attr_brightness_pct == 0
    dali.handle_actuator_lock(True)
    assert dali._attr_brightness_pct == 74
    dali.handle_actuator_lock(False)

    # (d) WHAT 19 (is_no_load) cancels an active software_stepped fade
    dali._apply_brightness_state(30, is_on=True)
    await dali.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80, ATTR_TRANSITION: 2.0})
    assert dali._fade_engine.is_fading is True
    await asyncio.sleep(0)
    dali.handle_event(OWNEvent.parse("*1*19*25#4#02##"))
    assert dali._fade_engine.is_fading is False
    assert dali.is_on is False

    # 12. Cold-boot locked fine dimmer coarse preset -> exact Dim 1 refinement (MH200 FW 2.1.0:
    #     *1*10*43## 100% -> +150ms *#1*43*1*189*2## 89%) MUST NOT clear actuator_locked,
    #     and unlocked coarse-start fade down (75% -> 20%) is NOT cancelled by in-flight Dim 1 poll (88%)!
    dim43 = MyHOMELight(
        hass=hass,
        name="Dim 43",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="43",
        who="1",
        where="43",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=handler,
    )
    _attach_live_entity(hass, dim43, "light.dim_43_mh200")
    dim43.handle_actuator_lock(True)
    assert dim43.actuator_locked is True
    dim43.handle_event(OWNEvent.parse("*1*10*43##"))  # 100% coarse bucket
    assert dim43._attr_brightness_pct == 100
    assert dim43.actuator_locked is True
    dim43.handle_event(OWNEvent.parse("*#1*43*1*189*2##"))  # exact 89% (+150ms)
    assert dim43._attr_brightness_pct == 89
    assert dim43.actuator_locked is True  # still locked!
    # Real subsequent brightness change (offline unlock -> 85%) DOES self-heal actuator_locked!
    dim43.handle_event(OWNEvent.parse("*#1*43*1*185*2##"))
    assert dim43._attr_brightness_pct == 85
    assert dim43.actuator_locked is False

    dim44 = MyHOMELight(
        hass=hass,
        name="Dim 44",
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
    _attach_live_entity(hass, dim44, "light.dim_44_fade_down")
    dim44.handle_event(OWNEvent.parse("*1*9*44##"))  # 75% coarse bucket
    dim44._fade_engine.first_echo_timeout = 0.2
    await dim44.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 20, ATTR_TRANSITION: 0.25})
    assert dim44._fade_task is not None
    await asyncio.sleep(0)
    step1_44 = dim44._fade_engine.pending_first_step_pct
    assert isinstance(step1_44, int) and step1_44 < 75
    # In-flight Dim 1 poll refines 75% -> 88% (abs(88 - step1_44) >= 10) without cancelling fade!
    msg_poll_88 = OWNEvent.parse("*#1*44*1*188*2##")
    setattr(msg_poll_88, "_from_status_request", True)
    dim44.handle_event(msg_poll_88)
    assert dim44._fade_engine.is_fading is True
    assert dim44._fade_engine.fade_start_pct == 88
    # Step 1 echo arrives and completes the fade to 20%!
    dim44.handle_event(OWNEvent.parse(f"*#1*44*1*{100 + step1_44}*2##"))
    await dim44._fade_task
    assert dim44._attr_brightness_pct == 20

    # 13. _is_from_poll real secondary/standby/follower topology + plain *#1*W## vs Dim 14
    #     + confirmed_ct_step + combined CT + sub-50ms transition (0.02s)!
    dali_cold._attr_is_on = False
    dali_cold._attr_color_temp = None
    handler.send = AsyncMock(side_effect=_fresh_fut)
    await dali_cold.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})
    # Plain *#1*W## (dimension=None) in _in_flight_messages does NOT block Dim 14 OFF->ON!
    handler._command_pool._in_flight_messages.append(OWNCommand.parse("*#1*26#4#02##"))
    try:
        dali_cold.handle_event(OWNEvent.parse("*#1*26#4#02*14*250##"))
    finally:
        handler._command_pool._in_flight_messages.clear()
    assert dali_cold.is_on is True

    # Delegated secondary / standby / follower sweep & in-flight poll checks + confirmed_ct_step
    fake_sec = SimpleNamespace(
        sweep_active=False,
        _command_pool=SimpleNamespace(_in_flight_messages=[OWNCommand.parse("*#1*26#4#02*14##")]),
    )
    fake_stb = SimpleNamespace(
        sweep_active=True,
        _command_pool=SimpleNamespace(_in_flight_messages=[]),
    )
    with (
        patch.object(handler, "_get_secondary_for_who", return_value=fake_sec),
        patch.object(handler, "_get_standby_gateway", return_value=fake_stb),
    ):
        assert dali_cold._is_from_poll(OWNEvent.parse("*#1*26#4#02*14*250##"), 14) is True
        fake_stb.sweep_active = False
        assert dali_cold._is_from_poll(OWNEvent.parse("*#1*26#4#02*14*250##"), 14) is True
        # Follower gateway resolves primary's secondary/standby too
        follower_gw = SimpleNamespace(
            is_follower=True,
            sweep_active=False,
            _command_pool=SimpleNamespace(_in_flight_messages=[]),
            _get_primary_gateway=lambda: handler,
        )
        orig_gw = dali_cold._gateway_handler
        dali_cold._gateway_handler = follower_gw  # type: ignore[assignment]
        try:
            assert dali_cold._is_from_poll(OWNEvent.parse("*#1*26#4#02*14*250##"), 14) is True
        finally:
            dali_cold._gateway_handler = orig_gw

        # confirmed_ct_step: when prev_color_temp (370) moves to target (250), lingering *#1*W*14## poll is overridden!
        dali_cold._attr_is_on = False
        dali_cold._attr_color_temp = 370
        await dali_cold.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})
        dali_cold.handle_event(OWNEvent.parse("*#1*26#4#02*14*250##"))
        assert dali_cold.is_on is True

    # Combined CT + sub-50ms software_stepped transition (0.02s) anchors pending brightness without premature fallback!
    dali._attr_is_on = False
    dali._attr_brightness_pct = 0
    dali._last_brightness_pct = 82
    f_ct_fast = hass.loop.create_future()
    f_ct_fast.set_result(time.monotonic())
    f_br_fast = hass.loop.create_future()
    handler.send = AsyncMock(side_effect=[f_ct_fast, f_br_fast])
    await dali.async_turn_on(
        **{ATTR_COLOR_TEMP_KELVIN: 4000, ATTR_BRIGHTNESS_PCT: 45, ATTR_TRANSITION: 0.02}
    )
    await asyncio.sleep(0)
    dali.handle_event(OWNEvent.parse("*#1*25#4#02*14*250##"))
    assert dali.is_on is True
    assert dali._attr_brightness_pct == 0  # held until Dim 1 echo or NACK!
    f_br_fast.cancel()
    await asyncio.sleep(0)
    assert dali._attr_brightness_pct == 82  # falls back cleanly on Dim 1 NACK!


async def test_premerge_audit_light_switch_fade_unavailable_and_unsent_token_guards(
    hass: HomeAssistant,
) -> None:
    """Verify established fine dimmer coarse_base, STATE_UNAVAILABLE restore, _PENDING_UNSENT, and cancel_fade_robustly."""
    from homeassistant.const import STATE_UNAVAILABLE
    from homeassistant.helpers.restore_state import RestoredExtraData

    from custom_components.myhome.light_fade import SoftwareFadeEngine

    entry, handler = _make_gateway(
        hass,
        model="MH200",
        options={CONF_TRANSITION_MODE: TRANSITION_MODE_SOFTWARE},
    )
    handler._event_session_ready.set()

    # 1. Established fine dimmer (_exact_level_seen=True / was_fine=True):
    #    - coarse WHAT 9 (*1*9*44## -> 75%) + lock (*14*0*44##) + Dim 1 refinement (*#1*44*1*188*2## -> 88%)
    #      keeps actuator_locked=True (coarse_dim1_refinement=True)!
    #    - unlocked fade 75% -> 20% survives in-flight Dim 1 poll 88% (coarse_quantized_start=True)!
    #    - cold-boot unseen lock at *1*9*44## (75%, true 88%) fading 75% -> 95% (pending_step == 88)
    #      does NOT falsely confirm step 1 when polled *#1*44*1*188*2## arrives!
    dim44 = MyHOMELight(
        hass=hass,
        name="Fine Dimmer 44",
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
    dim44._poll_on_add = False
    dim44._probe_dimmer_on_add = False
    _attach_live_entity(hass, dim44, "light.fine_44")

    # Establish _exact_level_seen = True first!
    dim44.handle_event(OWNEvent.parse("*#1*44*1*150*2##"))
    assert dim44._exact_level_seen is True

    dim44.handle_event(OWNEvent.parse("*1*9*44##"))  # 75%, _brightness_from_coarse_preset = True
    assert dim44._attr_brightness_pct == 75
    assert dim44._brightness_from_coarse_preset is True
    assert handler._actuator_locks.observe(OWNCommand.parse("*14*0*44##")) is True
    assert dim44.actuator_locked is True

    dim44.handle_event(OWNEvent.parse("*#1*44*1*188*2##"))  # +13 pp refinement on established fine dimmer
    assert dim44._attr_brightness_pct == 88
    assert dim44.actuator_locked is True
    # Trailing *1*9*44## inside #851/#984 refresh window holds exact 88% & keeps lock intact
    dim44.handle_event(OWNEvent.parse("*1*9*44##"))
    assert dim44._attr_brightness_pct == 88
    assert dim44.actuator_locked is True

    # Unlocked fade 75% -> 20% on established fine dimmer when Dim 1 poll refines 75% -> 88%
    assert handler._actuator_locks.observe(OWNCommand.parse("*14*1*44##")) is True
    if dim44._brightness_refresh_task is not None:
        dim44._brightness_refresh_task.cancel()
        await asyncio.gather(dim44._brightness_refresh_task, return_exceptions=True)
    dim44._reset_brightness_refresh_guard()
    dim44._brightness_refresh_after = 0.0
    dim44.handle_event(OWNEvent.parse("*1*9*44##"))
    assert dim44._brightness_from_coarse_preset is True

    def _done_write(_cmd: Any = None) -> asyncio.Future[float]:
        fut = hass.loop.create_future()
        fut.set_result(time.monotonic())
        return fut

    handler.send = AsyncMock(side_effect=_done_write)
    handler.send_fade_step = AsyncMock(side_effect=_done_write)
    dim44._fade_engine.first_echo_timeout = 0.25
    await dim44.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 20, ATTR_TRANSITION: 0.20})
    await asyncio.sleep(0)
    s1 = dim44._fade_engine.pending_first_step_pct
    assert isinstance(s1, int) and s1 < 75
    poll_88 = OWNEvent.parse("*#1*44*1*188*2##")
    setattr(poll_88, "_from_status_request", True)
    dim44.handle_event(poll_88)
    assert dim44._fade_engine.is_fading is True
    assert dim44._fade_engine.fade_start_pct == 88
    dim44.handle_event(OWNEvent.parse(f"*#1*44*1*{100 + s1}*2##"))
    assert dim44._fade_task is not None
    await dim44._fade_task
    assert dim44._attr_brightness_pct == 20

    # Cold-boot unseen lock on established fine dimmer at *1*9*44## (75%, true 88%), fade -> 95%
    handler._actuator_locks._locks.pop(("44", None), None)
    dim44._actuator_locked = None
    if dim44._brightness_refresh_task is not None:
        dim44._brightness_refresh_task.cancel()
        await asyncio.gather(dim44._brightness_refresh_task, return_exceptions=True)
    dim44._reset_brightness_refresh_guard()
    dim44._brightness_refresh_after = 0.0
    dim44.handle_event(OWNEvent.parse("*1*9*44##"))
    dim44._fade_engine.first_echo_timeout = 0.08
    await dim44.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 95, ATTR_TRANSITION: 0.12})
    await asyncio.sleep(0)
    poll_88_cold = OWNEvent.parse("*#1*44*1*188*2##")
    setattr(poll_88_cold, "_from_status_request", True)
    dim44.handle_event(poll_88_cold)
    assert dim44._fade_task is not None
    await dim44._fade_task
    assert dim44._attr_brightness_pct == 88  # aborted cleanly at real frozen level, NOT 95%!

    # 2. STATE_UNAVAILABLE persistence via MyHOMEEntity.extra_restore_state_data (light + switch + error fallback)
    assert dim44.extra_restore_state_data is None
    assert handler._actuator_locks.observe(OWNCommand.parse("*14*0*44##")) is True
    saved_light_extra = dim44.extra_restore_state_data
    assert saved_light_extra is not None
    assert saved_light_extra.as_dict() == {"actuator_locked": True}

    handler._actuator_locks.clear()
    dim44_restarted = MyHOMELight(
        hass=hass,
        name="Fine Dimmer 44",
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
    dim44_restarted._poll_on_add = False
    dim44_restarted._probe_dimmer_on_add = False
    dim44_restarted.entity_id = "light.fine_44"
    with (
        patch.object(
            dim44_restarted,
            "async_get_last_state",
            AsyncMock(return_value=State("light.fine_44", STATE_UNAVAILABLE, {})),
        ),
        patch.object(
            dim44_restarted,
            "async_get_last_extra_data",
            AsyncMock(return_value=RestoredExtraData(saved_light_extra.as_dict())),
        ),
    ):
        await dim44_restarted.async_added_to_hass()
    assert dim44_restarted.actuator_locked is True
    assert handler._actuator_locks.is_locked("44") is True

    sw_46 = MyHOMESwitch(
        hass=hass,
        name="Switch 46",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="46",
        who="1",
        where="46",
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F411/4",
        gateway=handler,
    )
    sw_46._poll_on_add = False
    sw_46.entity_id = "switch.sw_46"
    with (
        patch.object(
            sw_46,
            "async_get_last_state",
            AsyncMock(return_value=State("switch.sw_46", STATE_UNAVAILABLE, {})),
        ),
        patch.object(
            sw_46,
            "async_get_last_extra_data",
            AsyncMock(return_value=RestoredExtraData({"actuator_locked": True})),
        ),
    ):
        await sw_46.async_added_to_hass()
    assert sw_46.actuator_locked is True
    sw_46._restore_actuator_lock(State("switch.sw_46", "on", {"actuator_locked": False}))
    assert sw_46.actuator_locked is True
    assert sw_46.extra_restore_state_data is not None
    assert sw_46.extra_restore_state_data.as_dict() == {"actuator_locked": True}
    assert handler._actuator_locks.is_locked("46") is True

    # Missing restore cache on STATE_UNAVAILABLE degrades gracefully without raising
    sw_47 = MyHOMESwitch(
        hass=hass,
        name="Switch 47",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="47",
        who="1",
        where="47",
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F411/4",
        gateway=handler,
    )
    sw_47._poll_on_add = False
    sw_47.entity_id = "switch.sw_47"
    with (
        patch.object(
            sw_47,
            "async_get_last_state",
            AsyncMock(return_value=State("switch.sw_47", STATE_UNAVAILABLE, {})),
        ),
        patch.object(
            sw_47,
            "async_get_last_extra_data",
            AsyncMock(side_effect=RuntimeError("no restore cache")),
        ),
    ):
        await sw_47.async_added_to_hass()
    assert sw_47.actuator_locked is None
    assert sw_47.extra_restore_state_data is None

    # 3. _PENDING_UNSENT sentinel preserves _pending_cmd_color_temp & _pending_cmd_brightness_pct
    #    when *1*0*W## or *1*1*W## arrives DURING await _send_switch_on(...)!
    dali28 = MyHOMELight(
        hass=hass,
        name="DALI 28",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="28",
        who="1",
        where="28",
        interface="02",
        dimmable=True,
        manufacturer="BTicino",
        model="F429",
        gateway=handler,
        color_temp=True,
    )
    dali28._poll_on_add = False
    dali28._probe_dimmer_on_add = False
    _attach_live_entity(hass, dali28, "light.dali_28")
    dali28._attr_is_on = False
    dali28._last_brightness_pct = 25

    send_calls_28 = 0

    async def _send_with_mid_await_off_and_on(cmd: Any) -> asyncio.Future[float]:
        nonlocal send_calls_28
        send_calls_28 += 1
        if send_calls_28 == 1:
            # Arrives while Dim 14 token[0] is still _PENDING_UNSENT!
            dali28.handle_event(OWNEvent.parse("*1*0*28#4#02##"))
        elif send_calls_28 == 2:
            # Arrives while Dim 1 token[0] is still _PENDING_UNSENT!
            dali28.handle_event(OWNEvent.parse("*1*1*28#4#02##"))
            dali28.handle_event(OWNEvent.parse("*#1*28#4#02*14*250##"))
        fut = hass.loop.create_future()
        fut.set_result(time.monotonic())
        return fut

    handler.send = _send_with_mid_await_off_and_on  # type: ignore[assignment]
    await dali28.async_turn_on(**{ATTR_COLOR_TEMP: 250, ATTR_BRIGHTNESS_PCT: 80})
    assert dali28._pending_cmd_brightness_pct is not None
    assert dali28._attr_brightness_pct != 25  # did NOT bounce to stale _last_brightness_pct=25!
    dali28.handle_event(OWNEvent.parse("*#1*28#4#02*1*180*0##"))
    assert dali28.is_on is True
    assert dali28._attr_brightness_pct == 80
    assert dali28._pending_cmd_brightness_pct is None

    # 4. SoftwareFadeEngine.cancel_fade_robustly:
    #    (a) concurrent newer fade_task started while old fade unwinds is NOT clobbered to None!
    #    (b) cancelling the caller task itself propagates CancelledError!
    swallow_cancel = True
    unwinding_entered = asyncio.Event()

    async def _send_cb(_pct: int) -> asyncio.Future[float]:
        fut = hass.loop.create_future()
        fut.set_result(time.monotonic())
        return fut

    eng = SoftwareFadeEngine(
        where="99",
        create_task_cb=lambda coro: asyncio.create_task(coro),
        send_instant_cb=_send_cb,
        apply_state_cb=lambda _p, _o: None,
        update_ha_state_cb=lambda: None,
        get_worker_count_cb=lambda: 1,
        get_transition_mode_cb=lambda: TRANSITION_MODE_SOFTWARE,
        is_on_cb=lambda: True,
    )

    async def _sticky_fade() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            unwinding_entered.set()
            if swallow_cancel:
                await asyncio.sleep(10)
            raise

    t_old = asyncio.create_task(_sticky_fade())
    eng.fade_task = t_old
    await asyncio.sleep(0)

    cancel_coro = asyncio.create_task(eng.cancel_fade_robustly())
    await unwinding_entered.wait()
    # Concurrent Fade #2 starts while Fade #1 is still unwinding inside cancel_fade_robustly!
    t_new = eng.start_fade(20, 80, 0.30)
    await cancel_coro  # 0.15 s shield timeout finishes without wiping t_new!
    assert eng.fade_task is t_new
    assert eng.is_fading is True
    t_old.cancel()
    await asyncio.gather(t_old, return_exceptions=True)
    eng.cancel_fade_if_active()
    await asyncio.gather(t_new, return_exceptions=True)

    # Caller task itself cancelled while awaiting cancel_fade_robustly -> propagates CancelledError!
    t_caller_target = asyncio.create_task(_sticky_fade())
    eng.fade_task = t_caller_target
    await asyncio.sleep(0)
    unwinding_entered.clear()
    caller_task = asyncio.create_task(eng.cancel_fade_robustly())
    await unwinding_entered.wait()
    caller_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller_task
    assert eng.fade_task is None
    t_caller_target.cancel()
    await asyncio.gather(t_caller_target, return_exceptions=True)

    # Also cover task raising RuntimeError inside cancel_fade_robustly
    async def _boom_on_cancel() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError as err:
            raise RuntimeError("boom during unwind") from err

    t_boom = asyncio.create_task(_boom_on_cancel())
    eng.fade_task = t_boom
    await asyncio.sleep(0)
    await eng.cancel_fade_robustly()
    assert eng.fade_task is None

    # 5. LightEchoWatchdog #4#1 -> #4#01 canonicalization & ActuatorLockManager 'bus_interface' config key
    dog = LightEchoWatchdog(SimpleNamespace(hass=hass, log_id="[GW]", send_status_request=AsyncMock()))  # type: ignore[arg-type]
    try:
        dog.command_acknowledged(OWNCommand.parse("*1*1*09#4#1##"), time.monotonic())
        assert "09#4#01" in dog.pending
        dog.status_seen(OWNMessage.parse("*1*1*09#4#01##"))
        assert dog.pending == {}
    finally:
        dog.cancel_all()

    assert entry.runtime_data is not None
    entry.runtime_data.platforms["light"] = {
        "g2": {"where": "#2", "bus_interface": "1", "members": ["12"]},
    }
    assert ("12", "01") in handler._actuator_locks._resolve_group_keys("#2", "01")

    # 6. #1013 + #977: Two coarse presets (*1*7* -> 50%, *1*8* -> 60% within <=15 pp of restored 55%)
    #    arriving while _brightness_refresh_out is True on a locked restored dimmer keep lock True & 55%,
    #    chain next_preset with the locked 55% fallback, and self-heal when Dim 1 reports 62%!
    dim48 = MyHOMELight(
        hass=hass,
        name="Restored 48",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="48",
        who="1",
        where="48",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=handler,
    )
    dim48._poll_on_add = False
    dim48._probe_dimmer_on_add = False
    _attach_live_entity(hass, dim48, "light.restored_48")
    await dim48.async_restore_last_state(
        State(
            "light.restored_48",
            "on",
            {"supported_color_modes": ["brightness"], "brightness": 140, "actuator_locked": True},
        )
    )
    assert dim48.actuator_locked is True
    assert dim48._attr_brightness_pct == 55
    assert dim48._exact_level_pct is None

    dim1_gate: asyncio.Future[float] = hass.loop.create_future()
    dim1_second: asyncio.Future[float] = hass.loop.create_future()
    dim1_second.set_result(time.monotonic())
    handler.send_status_request = AsyncMock(side_effect=[dim1_gate, dim1_second])
    dim48._brightness_refresh_after = 0.0
    dim48.handle_event(OWNEvent.parse("*1*7*48##"))  # 50% -> starts refresh 1 (_brightness_refresh_out=True)
    await asyncio.sleep(0)
    assert dim48._brightness_refresh_out is True
    assert dim48._attr_brightness_pct == 55 and dim48.actuator_locked is True
    dim48.handle_event(OWNEvent.parse("*1*8*48##"))  # 60% while refresh 1 is out -> queues next_preset=8!
    assert dim48._brightness_refresh_again is True
    assert dim48._attr_brightness_pct == 55 and dim48.actuator_locked is True
    dim1_gate.set_result(time.monotonic())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert dim48._brightness_refresh_preset == 8
    assert dim48._brightness_refresh_level == 55
    dim48.handle_event(OWNEvent.parse("*#1*48*1*162*2##"))  # real level moved 55% -> 62%: self-heals!
    assert dim48._attr_brightness_pct == 62
    assert dim48.actuator_locked is False

    # 7. Concurrent turn_on(brightness_pct=80) while combined turn_on(color_temp=250, brightness_pct=30)
    #    fails in _send_switch_on preserves Call 2's (80, ...) pending brightness token!
    dali28._pending_cmd_brightness_pct = None
    entered_ct = asyncio.Event()
    fail_ct = asyncio.Event()
    ct_call_idx = 0

    async def _interleaved_ct_send(cmd: Any) -> asyncio.Future[float]:
        nonlocal ct_call_idx
        ct_call_idx += 1
        if ct_call_idx == 1:
            entered_ct.set()
            await fail_ct.wait()
            raise RuntimeError("ct send failed")
        fut = hass.loop.create_future()
        fut.set_result(time.monotonic())
        return fut

    handler.send = _interleaved_ct_send  # type: ignore[assignment]
    t_ct = asyncio.create_task(dali28.async_turn_on(**{ATTR_COLOR_TEMP: 250, ATTR_BRIGHTNESS_PCT: 30}))
    await entered_ct.wait()
    await dali28.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80})
    assert dali28._pending_cmd_brightness_pct is not None
    assert dali28._pending_cmd_brightness_pct[0] == 80
    fail_ct.set()
    with pytest.raises(RuntimeError, match="ct send failed"):
        await t_ct
    assert dali28._pending_cmd_brightness_pct is not None
    assert dali28._pending_cmd_brightness_pct[0] == 80


async def test_lock_on_relay_and_pending_dim1_and_who1_scope_polls(
    hass: HomeAssistant,
) -> None:
    """Verify locking ON relays/dimmers preserves brightness, task-safe CT fade abort, and WHO=1 scope polls."""
    entry, handler = _make_gateway(
        hass,
        model="F461",
        options={CONF_TRANSITION_MODE: TRANSITION_MODE_SOFTWARE},
    )
    handler._event_session_ready.set()

    def _done_fut(_cmd: Any = None) -> asyncio.Future[float]:
        fut = hass.loop.create_future()
        fut.set_result(time.monotonic())
        return fut

    handler.send = AsyncMock(side_effect=_done_fut)
    handler.send_fade_step = AsyncMock(side_effect=_done_fut)

    # 1a. Locking an ON ColorMode.ONOFF relay (F411) keeps brightness=None (zero 255 corruption)
    relay_05 = MyHOMELight(
        hass=hass,
        name="Relay 05",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="05",
        who="1",
        where="05",
        interface=None,
        dimmable=False,
        manufacturer="BTicino",
        model="F411/4",
        gateway=handler,
    )
    relay_05._poll_on_add = False
    relay_05._probe_dimmer_on_add = False
    ev_relay = _attach_live_entity(hass, relay_05, "light.relay_05")

    relay_05.handle_event(OWNEvent.parse("*1*1*05##"))
    assert relay_05.is_on is True
    assert relay_05.brightness is None
    assert relay_05._attr_brightness_pct is None

    ev_relay.clear()
    assert handler._actuator_locks.observe(OWNCommand.parse("*14*0*05##")) is True
    assert relay_05.actuator_locked is True
    assert relay_05.brightness is None
    assert relay_05._attr_brightness_pct is None
    assert len(ev_relay) == 1
    # Direct defensive call also ignores non-dimmer color modes
    relay_05._restore_combined_ct_brightness_on_abort()
    assert relay_05.brightness is None

    # 1b. Locking an ON dimmer (F418) before initial Dim 1 arrives keeps brightness=None,
    #     and the subsequent initial Dim 1 reply (20%) does NOT clear actuator_locked!
    dim_06 = MyHOMELight(
        hass=hass,
        name="Dimmer 06",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="06",
        who="1",
        where="06",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=handler,
    )
    dim_06._poll_on_add = False
    dim_06._probe_dimmer_on_add = False
    _attach_live_entity(hass, dim_06, "light.dim_06")

    dim_06.handle_event(OWNEvent.parse("*1*1*06##"))
    assert dim_06.is_on is True
    assert dim_06.brightness is None
    assert dim_06._attr_brightness_pct is None

    assert handler._actuator_locks.observe(OWNCommand.parse("*14*0*06##")) is True
    assert dim_06.actuator_locked is True
    assert dim_06.brightness is None
    assert dim_06._attr_brightness_pct is None

    dim_06.handle_event(OWNEvent.parse("*#1*06*1*120*2##"))
    assert dim_06._attr_brightness_pct == 20
    assert dim_06.actuator_locked is True

    # 1c. Task-scoped _combined_ct_fade_task survives stale done_callback when a combined fade is re-triggered
    dali_25 = MyHOMELight(
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
    )
    dali_25._poll_on_add = False
    dali_25._probe_dimmer_on_add = False
    _attach_live_entity(hass, dali_25, "light.dali_25_task_guard")
    dali_25._attr_is_on = False
    dali_25._attr_brightness_pct = 0
    dali_25._last_brightness_pct = 77
    dali_25._fade_engine.first_echo_timeout = 5.0

    await dali_25.async_turn_on(
        **{ATTR_COLOR_TEMP_KELVIN: 4000, ATTR_BRIGHTNESS_PCT: 40, ATTR_TRANSITION: 2.0}
    )
    fade_1 = dali_25._combined_ct_fade_task
    assert fade_1 is not None
    # Cancel fade_1 synchronously and start fade_2 on the same tick before fade_1's done_callback runs
    dali_25._cancel_fade_if_active()
    fade_2 = dali_25._fade_engine.start_fade(0, 60, 2.0)
    dali_25._combined_ct_fade_task = fade_2
    fade_2.add_done_callback(dali_25._restore_combined_ct_brightness_on_abort)
    await asyncio.gather(fade_1, return_exceptions=True)
    await asyncio.sleep(0)
    # fade_1's stale done_callback did NOT clobber fade_2!
    assert dali_25._combined_ct_fade_task is fade_2
    dali_25.handle_event(OWNEvent.parse("*#1*25#4#02*14*250##"))
    assert dali_25.is_on is True
    assert dali_25._attr_brightness_pct == 0
    dali_25.handle_actuator_lock(True)
    assert dali_25._attr_brightness_pct == 77
    assert dali_25._combined_ct_fade_task is None
    await asyncio.gather(fade_2, return_exceptions=True)
    dali_25.handle_actuator_lock(False)

    # 2. WHO=1 scope polls in _poll_targets_self / _is_from_poll:
    #    - Unrouted General '0' (*#1*0*14##, *#1*0##) covers BOTH main-bus AND #4#02 DALI lights
    #      (per WHO_1.pdf §2.3 & F461 capture tests/fixtures/plants/issue_288_f461_dali_dt8:50-56)!
    #    - Routed General '0#4#02' covers only #4#02 (isolating main bus and #4#01).
    #    - Area ('00', '1'..'9', '100', '2#4#02') and '#G' group ('#3#4#02' & resync_manager '#4') polls match!
    lt_10 = MyHOMELight(
        hass=hass,
        name="Point 10",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="10",
        who="1",
        where="10",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=handler,
    )
    lt_1001 = MyHOMELight(
        hass=hass,
        name="Point 1001",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="1001",
        who="1",
        where="1001",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=handler,
    )

    assert entry.runtime_data is not None
    entry.runtime_data.platforms["light"] = {
        "grp3_dali": {"where": "#3#4#02", "members": ["25"]},
    }
    handler._resync_manager.register_group_members("#4#4#02", ["25"])
    handler._resync_manager.register_group_members("#4", ["06"])

    # Empty-WHERE diagnostic query -> False
    empty_diag = SimpleNamespace(who=1, is_dimension_request=True, dimension=14, where="")
    assert dali_25._poll_targets_self(empty_diag) is False

    # Own point -> True; unrelated point on same and different bus -> False
    assert dali_25._poll_targets_self(OWNCommand.parse("*#1*25#4#02*14##")) is True
    assert dali_25._poll_targets_self(OWNCommand.parse("*#1*99#4#02*14##")) is False
    assert dim_06._poll_targets_self(OWNCommand.parse("*#1*99##")) is False
    assert dali_25._poll_targets_self(OWNCommand.parse("*#1*99*14##")) is False

    # Unrouted system-wide General '0' covers BOTH main-bus dim_06 AND #4#02 dali_25!
    assert dim_06._poll_targets_self(OWNCommand.parse("*#1*0##")) is True
    assert dali_25._poll_targets_self(OWNCommand.parse("*#1*0*14##")) is True

    # End-to-end: in-flight *#1*0*14## on cold-boot OFF DALI 25#4#02 keeps is_on=False & preserves pending CT!
    dali_25._attr_is_on = False
    dali_25._attr_color_temp = None
    f_pending_ct = hass.loop.create_future()
    handler.send = AsyncMock(return_value=f_pending_ct)
    await dali_25.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})
    handler._command_pool._in_flight_messages.append(OWNCommand.parse("*#1*0*14##"))
    try:
        dali_25.handle_event(OWNEvent.parse("*#1*25#4#02*14*250##"))
    finally:
        handler._command_pool._in_flight_messages.clear()
    assert dali_25.is_on is False
    assert dali_25._pending_cmd_color_temp is not None
    f_pending_ct.cancel()
    await asyncio.sleep(0)

    # Routed General '0#4#II' isolates local buses cleanly
    assert dali_25._poll_targets_self(OWNCommand.parse("*#1*0#4#02*14##")) is True
    assert dim_06._poll_targets_self(OWNCommand.parse("*#1*0#4#02##")) is False
    assert dali_25._poll_targets_self(OWNCommand.parse("*#1*0#4#01*14##")) is False
    assert dali_25._poll_targets_self(SimpleNamespace(who=1, is_status_request=True, where="0#4#99")) is False

    # Area polls: '00', '1'..'9', '100', and routed '2#4#02' (with PL=0 '10' vs Area 10 '100' isolation)
    assert dim_06._poll_targets_self(OWNCommand.parse("*#1*00##")) is True
    assert dim_06._poll_targets_self(OWNCommand.parse("*#1*1##")) is False
    assert lt_10._poll_targets_self(OWNCommand.parse("*#1*1##")) is True
    assert lt_10._poll_targets_self(OWNCommand.parse("*#1*100##")) is False
    assert lt_1001._poll_targets_self(OWNCommand.parse("*#1*100##")) is True
    assert dali_25._poll_targets_self(OWNCommand.parse("*#1*2*14##")) is False
    assert dali_25._poll_targets_self(OWNCommand.parse("*#1*2#4#02*14##")) is True
    assert dali_25._poll_targets_self(OWNCommand.parse("*#1*3#4#02*14##")) is False

    # #G group polls: resolved via ActuatorLockManager (#3#4#02) and resync_manager (#4#4#02, #4)
    assert dali_25._poll_targets_self(OWNCommand.parse("*#1*#3#4#02*14##")) is True
    assert dali_25._poll_targets_self(OWNCommand.parse("*#1*#4#4#02*14##")) is True
    assert dim_06._poll_targets_self(OWNCommand.parse("*#1*#4##")) is True
    assert dali_25._poll_targets_self(OWNCommand.parse("*#1*#9#4#02*14##")) is False
