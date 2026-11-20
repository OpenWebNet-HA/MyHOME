"""Tests for WHO=14 Actuator Lock core tracking (ActuatorLockManager & MyHOMEEntity)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.cover import CoverDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.actuator_lock import (
    ActuatorLockManager,
    actuator_locks_of,
)
from custom_components.myhome.binary_sensor import MyHOMEMotionSensor
from custom_components.myhome.button import (
    CalibrateCoverButtonEntity,
    DisableCommandButtonEntity,
    EnableCommandButtonEntity,
)
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.cover import MyHOMECover, MyHOMEImpulseCover
from custom_components.myhome.cover_scope import CoverScope, MyHOMEScopeCover
from custom_components.myhome.expose import ExposeBinding, ExposeManager
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.light import MyHOMELight
from custom_components.myhome.light_group import MyHOMELightGroup
from custom_components.myhome.myhome_device import MyHOMEEntity
from custom_components.myhome.sensor import MyHOMEIlluminanceSensor
from custom_components.myhome.switch import MyHOMESwitch
from tests.conftest import attach_runtime

MAC = "00:03:50:14:97:60"


def _make_handler(hass: HomeAssistant) -> tuple[MyHOMEGatewayHandler, MockConfigEntry]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 20000,
            CONF_PASSWORD: "open",
            CONF_MAC: MAC,
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.myhome.gateway.OWNGateway"):
        handler = MyHOMEGatewayHandler(hass, entry, generate_events=False)
        handler.gateway.serial = MAC
        handler.gateway.host = "192.0.2.10"
        handler.send = AsyncMock()
        handler.send_status_request = AsyncMock()
    attach_runtime(hass, entry, MAC, handler)
    return handler, entry


def _make_bare_entity(
    hass: HomeAssistant | None,
    handler: Any,
    *,
    who: str = "1",
    where: str = "11",
    interface: str | None = None,
    platform: str = "light",
    with_attrs: bool = True,
) -> MyHOMEEntity:
    entity = MyHOMEEntity(
        hass=hass,
        name=f"Entity {where}",
        platform=platform,
        device_id=f"{where}#4#{interface}" if interface else where,
        who=who,
        where=where,
        manufacturer="BTicino",
        model="Test",
        gateway=handler,
    )
    entity._interface = interface
    if with_attrs:
        entity._attr_extra_state_attributes = {"A": where[:1], "PL": where[1:]}
    entity._publish_state = MagicMock()
    return entity


def test_actuator_locks_of_returns_manager_or_none(hass: HomeAssistant) -> None:
    """actuator_locks_of returns manager when present, else None."""
    handler, _ = _make_handler(hass)
    mgr = actuator_locks_of(handler)
    assert isinstance(mgr, ActuatorLockManager)
    assert actuator_locks_of(None) is None
    assert actuator_locks_of(SimpleNamespace()) is None
    assert actuator_locks_of(MagicMock()) is None


def test_point_lock_unlock_and_canonical_spelling(hass: HomeAssistant) -> None:
    """Point-to-point WHO=14 frames update state, canonical 0004->04, and entity attrs."""
    handler, _ = _make_handler(hass)
    mgr = handler._actuator_locks
    ent = _make_bare_entity(hass, handler, who="1", where="04")
    assert ent._actuator_locked is None
    assert "actuator_locked" not in ent._attr_extra_state_attributes

    unsub = mgr.register(ent)
    assert mgr.is_locked("04") is None
    assert mgr.is_locked("0004") is None

    mgr.observe(OWNMessage.parse("*14*0*0004##"))
    assert mgr.is_locked("04") is True
    assert mgr.is_locked("0004") is True
    assert ent._actuator_locked is True
    assert ent._attr_extra_state_attributes["actuator_locked"] is True
    ent._publish_state.assert_called_once()

    mgr.observe(OWNMessage.parse("*14*1*04##"))
    assert mgr.is_locked("04") is False
    assert ent._actuator_locked is False
    assert ent._attr_extra_state_attributes["actuator_locked"] is False
    assert ent._publish_state.call_count == 2

    # Unsub is idempotent
    unsub()
    unsub()


def test_interface_isolation_and_normalization(hass: HomeAssistant) -> None:
    """#4#II frames isolate per interface and normalize single-digit interfaces."""
    handler, _ = _make_handler(hass)
    mgr = handler._actuator_locks
    ent_main = _make_bare_entity(hass, handler, who="1", where="07", interface=None)
    ent_if1 = _make_bare_entity(hass, handler, who="1", where="07", interface="1")
    ent_if2 = _make_bare_entity(hass, handler, who="2", where="07", interface="02")
    mgr.register(ent_main)
    mgr.register(ent_if1)
    mgr.register(ent_if2)

    mgr.observe(OWNMessage.parse("*14*0*07#4#01##"))
    assert mgr.is_locked("07", "01") is True
    assert mgr.is_locked("07", "1") is True
    assert mgr.is_locked("07#4#01") is True
    assert mgr.is_locked("07") is None
    assert mgr.is_locked("07", "02") is None
    assert mgr.is_locked("07", "abc") is None
    assert ent_if1._actuator_locked is True
    assert ent_main._actuator_locked is None
    assert ent_if2._actuator_locked is None

    mgr.observe(OWNMessage.parse("*14*1*07#4#1##"))
    assert mgr.is_locked("07", "01") is False
    assert ent_if1._actuator_locked is False


def test_general_and_area_scopes_and_point_10(hass: HomeAssistant) -> None:
    """General (0/00) and Area (1..9/100) respect interfaces and distinguish point 10."""
    handler, _ = _make_handler(hass)
    mgr = handler._actuator_locks

    ent_a0 = _make_bare_entity(hass, handler, who="1", where="05")
    ent_a1 = _make_bare_entity(hass, handler, who="1", where="11")
    ent_a1_4d = _make_bare_entity(hass, handler, who="1", where="0115")
    ent_p10 = _make_bare_entity(hass, handler, who="1", where="10")
    ent_a10 = _make_bare_entity(hass, handler, who="2", where="1005")
    ent_if1 = _make_bare_entity(hass, handler, who="1", where="11", interface="01")

    for e in (ent_a0, ent_a1, ent_a1_4d, ent_p10, ent_a10, ent_if1):
        mgr.register(e)

    # Cached unseen point on main bus + another on area 1 & area 10 & interface 01
    mgr.observe(OWNMessage.parse("*14*0*98##"))
    mgr.observe(OWNMessage.parse("*14*1*12##"))
    mgr.observe(OWNMessage.parse("*14*1*1002##"))
    mgr.observe(OWNMessage.parse("*14*0*98#4#01##"))

    # Area 1 lock (*14*0*1##): locks 11, 0115, and cached 12; leaves 05, 10, 1005, and iface 01 untouched
    mgr.observe(OWNMessage.parse("*14*0*1##"))
    assert ent_a1._actuator_locked is True
    assert ent_a1_4d._actuator_locked is True
    assert mgr.is_locked("12") is True
    assert ent_a0._actuator_locked is None
    assert ent_p10._actuator_locked is None
    assert ent_a10._actuator_locked is None
    assert ent_if1._actuator_locked is None
    assert mgr.is_locked("98#4#01") is True

    # Area 10 lock (*14*0*100##): locks 1005 and cached 1002, NOT point 10
    mgr.observe(OWNMessage.parse("*14*0*100##"))
    assert ent_a10._actuator_locked is True
    assert mgr.is_locked("1002") is True
    assert ent_p10._actuator_locked is None

    # Point 10 lock (*14*0*10##): locks ONLY point 10
    mgr.observe(OWNMessage.parse("*14*0*10##"))
    assert ent_p10._actuator_locked is True

    # General unlock (*14*1*00##) and general lock (*14*0*0##) stay on same interface
    mgr.observe(OWNMessage.parse("*14*1*00##"))
    assert all(e._actuator_locked is False for e in (ent_a0, ent_a1, ent_a1_4d, ent_p10, ent_a10))
    assert mgr.is_locked("98") is False
    assert ent_if1._actuator_locked is None
    assert mgr.is_locked("98#4#01") is True

    mgr.observe(OWNMessage.parse("*14*0*0##"))
    assert all(e._actuator_locked is True for e in (ent_a0, ent_a1, ent_a1_4d, ent_p10, ent_a10))
    assert ent_if1._actuator_locked is None

    # Interface-scoped general lock/unlock
    mgr.observe(OWNMessage.parse("*14*0*0#4#01##"))
    assert ent_if1._actuator_locked is True
    assert mgr.is_locked("98", "01") is True
    mgr.observe(OWNMessage.parse("*14*1*0#4#01##"))
    assert ent_if1._actuator_locked is False
    assert mgr.is_locked("98", "01") is False


def test_ignored_frames_and_exposed_addresses(hass: HomeAssistant) -> None:
    """Translations, non-0/1 WHATs, groups, ZigBee, invalid WHEREs, and exposed addresses are ignored."""
    handler, entry = _make_handler(hass)
    mgr = handler._actuator_locks

    expose_mgr = ExposeManager(hass, entry)
    expose_mgr.bindings = [
        ExposeBinding(who=1, where="55"),
        ExposeBinding(who=2, where="08#4#01"),
    ]
    entry.runtime_data.expose_manager = expose_mgr

    # Non-lock, translation, invalid WHAT, dimension request, groups, ZigBee, malformed
    mgr.observe(OWNMessage.parse("*1*1*55##"))
    mgr.observe(OWNMessage.parse("*14*1000#0*55##"))
    mgr.observe(OWNMessage.parse("*14*2*55##"))
    mgr.observe(OWNMessage.parse("*#14*55##"))
    mgr.observe(OWNMessage.parse("*14*0*#1##"))
    mgr.observe(OWNMessage.parse("*14*0*701234501#9##"))
    mgr.observe(SimpleNamespace())
    # Synthetic OWNActuatorLockEvent edge cases (bool what, empty where, invalid iface, non-digit base)
    ev_bool_what = OWNMessage.parse("*14*0*55##")
    assert ev_bool_what is not None
    with patch.object(type(ev_bool_what), "what", property(lambda _self: True)):
        mgr.observe(ev_bool_what)
    with patch("custom_components.myhome.actuator_lock.expose_message_where", return_value=""):
        mgr.observe(ev_bool_what)
    with patch("custom_components.myhome.actuator_lock.expose_message_where", return_value="55#4#bad"):
        mgr.observe(ev_bool_what)
    with patch("custom_components.myhome.actuator_lock.expose_message_where", return_value="abc"):
        mgr.observe(ev_bool_what)

    # Exposed virtual addresses must be ignored
    mgr.observe(OWNMessage.parse("*14*0*55##"))
    mgr.observe(OWNMessage.parse("*14*0*08#4#01##"))
    assert mgr.is_locked("55") is None
    assert mgr.is_locked("08", "01") is None
    assert mgr._states == {}


def test_registration_filters_late_add_and_clear(hass: HomeAssistant) -> None:
    """register() filters invalid/non-actuator entities, applies cached state without publish, and clear() resets."""
    handler, _ = _make_handler(hass)
    mgr = handler._actuator_locks

    # Pre-seed lock before entity registers
    mgr.observe(OWNMessage.parse("*14*0*61##"))
    late_ent = _make_bare_entity(hass, handler, who="1", where="61", with_attrs=False)
    unsub = mgr.register(late_ent)
    assert late_ent._actuator_locked is True
    assert late_ent._attr_extra_state_attributes == {"actuator_locked": True}
    late_ent._publish_state.assert_not_called()

    # Duplicate registration does not duplicate entity in list
    mgr.register(late_ent)
    assert mgr._entities.count(late_ent) == 1

    # Ignored registrations
    ignored = [
        _make_bare_entity(hass, handler, who="4", where="11"),
        _make_bare_entity(hass, handler, who="1", where="11", platform="button"),
        _make_bare_entity(hass, handler, who="1", where="11", platform="binary_sensor"),
        _make_bare_entity(hass, handler, who="1", where="11", platform="sensor"),
        _make_bare_entity(hass, handler, who="1", where=""),
        _make_bare_entity(hass, handler, who="1", where="#1"),
        _make_bare_entity(hass, handler, who="1", where="0"),
        _make_bare_entity(hass, handler, who="1", where="00"),
        _make_bare_entity(hass, handler, who="1", where="0#4#01"),
        _make_bare_entity(hass, handler, who="1", where="701234501#9"),
        SimpleNamespace(_who="2", _where="11", scope=object()),
    ]
    for ent in ignored:
        noop_unsub = mgr.register(ent)
        noop_unsub()
    assert mgr._entities == [late_ent]

    # Bare MyHOMEEntity without _attr_extra_state_attributes still updates cleanly via handle_actuator_lock
    bare = _make_bare_entity(hass, handler, who="1", where="62", with_attrs=False)
    bare.handle_actuator_lock(False)
    assert bare._actuator_locked is False
    assert bare._attr_extra_state_attributes == {"actuator_locked": False}
    bare._publish_state.assert_called_once()

    # Clear lock from actuation on bare entity
    bare._clear_actuator_lock_on_actuation()  # _actuator_locked is False -> no-op
    assert mgr.is_locked("62") is None
    late_ent._clear_actuator_lock_on_actuation()
    assert late_ent._actuator_locked is False
    assert late_ent._attr_extra_state_attributes["actuator_locked"] is False
    assert mgr.is_locked("61") is False

    # clear_lock_from_actuation ignores empty where, and works when handler has no manager
    mgr.clear_lock_from_actuation("")
    orphan = _make_bare_entity(hass, MagicMock(), who="1", where="63")
    orphan._actuator_locked = True
    orphan._clear_actuator_lock_on_actuation()
    assert orphan._actuator_locked is False

    unsub()
    assert mgr._entities == []
    mgr.clear()
    assert mgr._states == {}


@pytest.mark.asyncio
async def test_live_entity_subclasses_and_dispatcher_integration(hass: HomeAssistant) -> None:
    """All WHO=1/2 actuator subclasses register (including advanced cover) while buttons/sensors/scopes do not."""
    handler, entry = _make_handler(hass)

    light = MyHOMELight(
        hass=hass, name="Light 06", entity_name=None, icon=None, icon_on=None,
        device_id="06", who="1", where="06", interface=None, dimmable=False,
        manufacturer="BTicino", model="F411/4", gateway=handler,
    )
    switch = MyHOMESwitch(
        hass=hass, name="Switch 07", entity_name=None, icon=None, icon_on=None,
        device_id="07", who="1", where="07", interface="01", device_class="switch",
        manufacturer="BTicino", model="F411/4", gateway=handler,
    )
    cover_timed = MyHOMECover(
        hass=hass, name="Cover 05", entity_name=None, device_id="05", who="2",
        where="05", interface=None, advanced=False, travel_time=5,
        manufacturer="BTicino", model="F411/2", gateway=handler,
    )
    cover_adv = MyHOMECover(
        hass=hass, name="Cover 08", entity_name=None, device_id="08", who="2",
        where="08", interface=None, advanced=True, travel_time=10,
        manufacturer="Legrand", model="67557", gateway=handler,
    )
    cover_impulse = MyHOMEImpulseCover(
        hass=hass, name="Gate 09", entity_name=None, device_id="09", who="1",
        where="09", interface=None, device_class=CoverDeviceClass.GATE,
        travel_time=10.0, pulse_duration=0.5, min_cycle_time=5.0,
        pin_code=None, state_sensor=None, manufacturer="BTicino",
        model="Relay", gateway=handler,
    )
    scope_cover = MyHOMEScopeCover(
        hass=hass, name="Area 1 Covers", entity_name=None, device_id="1",
        who="2", where="1", interface=None, advanced=False, travel_time=10,
        manufacturer="BTicino", model="Scope", gateway=handler,
        scope=CoverScope.of("1"),
    )
    light_group = MyHOMELightGroup(
        hass=hass, name="Group 1", device_id="#1", group=1, gateway=handler,
        members=[], dimmable=False, color_temp=False, rgb=False, hs=False,
    )
    btn_disable = DisableCommandButtonEntity(
        hass=hass, platform="button", name="Cover 05", device_id="05",
        who="2", where="05", interface=None, manufacturer="BTicino",
        model="F411/2", gateway=handler,
    )
    btn_enable = EnableCommandButtonEntity(
        hass=hass, platform="button", name="Cover 05", device_id="05",
        who="2", where="05", interface=None, manufacturer="BTicino",
        model="F411/2", gateway=handler,
    )
    btn_cal = CalibrateCoverButtonEntity(
        hass=hass, platform="button", device_id="05", where="05",
        interface=None, name="Cover 05", gateway=handler, config_entry=entry,
    )
    pir = MyHOMEMotionSensor(
        hass=hass, name="PIR 06", entity_name=None, device_id="06", who="1",
        where="06", inverted=False, device_class=BinarySensorDeviceClass.MOTION,
        manufacturer="BTicino", model="PIR", gateway=handler,
    )
    lux = MyHOMEIlluminanceSensor(
        hass=hass, name="Lux 06", device_id="06", who="1", where="06",
        device_class=SensorDeviceClass.ILLUMINANCE, manufacturer="BTicino",
        model="Lux", gateway=handler,
    )

    all_entities = [
        light, switch, cover_timed, cover_adv, cover_impulse,
        scope_cover, light_group, btn_disable, btn_enable, btn_cal, pir, lux,
    ]
    for idx, ent in enumerate(all_entities):
        ent.hass = hass
        ent.entity_id = f"test.ent_{idx}"
        ent.async_write_ha_state = MagicMock()
        ent.async_schedule_update_ha_state = MagicMock()
        with (
            patch.object(ent, "async_get_last_state", AsyncMock(return_value=None)),
            patch.object(ent, "async_update", AsyncMock()),
        ):
            await ent.async_added_to_hass()
        # Idempotency: calling _register_availability_listener again does not duplicate
        ent._register_availability_listener()

    actuators = [light, switch, cover_timed, cover_adv, cover_impulse]
    non_actuators = [scope_cover, light_group, btn_disable, btn_enable, btn_cal, pir, lux]
    assert handler._actuator_locks._entities == actuators

    dispatcher = handler._event_dispatcher
    await dispatcher.process_message(OWNMessage.parse("*14*0*0##"))
    await dispatcher.process_message(OWNMessage.parse("*14*0*07#4#01##"))

    for ent in actuators:
        assert ent._actuator_locked is True
        assert ent.extra_state_attributes["actuator_locked"] is True
    for ent in non_actuators:
        assert ent._actuator_locked is None
        assert "actuator_locked" not in (ent.extra_state_attributes or {})

    # Verify inactive gateway (e.g., passive standby) ignores WHO=14
    with patch.object(dispatcher, "_is_active_for_who", return_value=False):
        await dispatcher.process_message(OWNMessage.parse("*14*1*06##"))
    assert light._actuator_locked is True

    # Verify active unlock via process_message
    await dispatcher.process_message(OWNMessage.parse("*14*1*06##"))
    assert light._actuator_locked is False

    # Verify removal runs cleanup callback and allows clean re-registration
    for ent in all_entities:
        ent._call_on_remove_callbacks()
    assert handler._actuator_locks._entities == []
    assert light._actuator_lock_registered is False

    await handler.close_listener()
    assert handler._actuator_locks._states == {}

    # process_message handles missing/None actuator lock manager cleanly
    handler._actuator_locks = None
    await dispatcher.process_message(OWNMessage.parse("*14*0*06##"))

    # Adding an entity whose gateway has no ActuatorLockManager returns cleanly
    orphan = _make_bare_entity(hass, MagicMock(), who="1", where="64")
    orphan.entity_id = "light.orphan_64"
    with (
        patch.object(orphan, "async_get_last_state", AsyncMock(return_value=None)),
        patch.object(orphan, "async_update", AsyncMock()),
    ):
        await orphan.async_added_to_hass()
    assert orphan._actuator_lock_registered is False
