"""Comprehensive WHO=14 actuator lock & echo-confirmed light/switch tests (#976)."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_BRIGHTNESS_PCT,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
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

    # Coarse preset *1*4*42## (20% bucket == _nearest_coarse_preset(22)) must NOT falsely unlock!
    light.handle_event(OWNEvent.parse("*1*4*42##"))
    assert light.actuator_locked is True
    # Reset back to 22% via Dim 1 reply (still locked)
    light.handle_event(OWNEvent.parse("*#1*42*1*120*5##"))
    assert light.actuator_locked is True

    # Software-stepped commands while known locked send 1 single frame (0 multi-step spam, 0 state mutation)
    handler.send.reset_mock()
    await light.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80, ATTR_TRANSITION: 3.0})
    await light.async_turn_on(**{ATTR_TRANSITION: 3.0})
    await light.async_turn_off(**{ATTR_TRANSITION: 3.0})
    assert light._fade_task is None
    assert [str(c.args[0]) for c in handler.send.await_args_list] == [
        "*#1*42*#1*180*0##",
        "*#1*42*#1*120*0##",
        "*1*0*42##",
    ]
    assert light.is_on is True
    assert light._attr_brightness_pct == 20

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
