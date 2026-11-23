"""Comprehensive tests for bidirectional WHO=14 & Dim 10 actuator lock tracking (#976)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant, State
from OWNd.connection import OWNNackError
from OWNd.message import (
    OWNActuatorLockCommand,
    OWNActuatorLockEvent,
    OWNCommand,
    OWNMessage,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.actuator_lock import (
    ActuatorLockManager,
    _is_valid_point_where,
    _normalize_iface,
    _parse_group_ref,
    _parse_hash_group,
    _point_area,
    _split_point_key,
    actuator_locks_of,
    apply_actuator_lock_state,
    is_lockable_entity,
)
from custom_components.myhome.const import (
    CONF_BUS_INTERFACE,
    CONF_BUS_TOPOLOGY,
    CONF_DELEGATED_WHOS,
    CONF_GATEWAY_ROLE,
    CONF_MEMBERS,
    CONF_PRIMARY_GATEWAY,
    CONF_WHERE,
    DOMAIN,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    ROLE_STANDBY,
    TOPOLOGY_SHARED,
)
from custom_components.myhome.cover_scope import CoverFamily, CoverScope
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.expose import ExposeBinding, ExposeManager
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.myhome_device import MyHOMEEntity
from custom_components.myhome.where_grammar import (
    automation_dim10_info,
    canonical_point_address,
    canonical_point_spelling,
    expose_message_where,
    normalize_expose_where,
)

PRI_MAC = "00:03:50:aa:bb:01"
SEC_MAC = "00:03:50:aa:bb:02"
STB_MAC = "00:03:50:aa:bb:03"


class _DummyPointEntity(MyHOMEEntity):
    """Minimal concrete WHO=1/2 point entity for unit testing MyHOMEEntity lock hooks."""

    def __init__(
        self,
        hass: HomeAssistant | None,
        gateway: Any,
        *,
        who: str = "2",
        platform: str = "cover",
        where: str = "56",
        interface: str | None = None,
        device_id: str | None = None,
    ) -> None:
        self._interface = interface
        self._full_where = f"{where}#4#{interface}" if interface else where
        super().__init__(
            hass=hass,
            name=f"Dev {where}",
            platform=platform,
            device_id=device_id or self._full_where,
            who=who,
            where=where,
            manufacturer="BTicino",
            model="Actuator",
            gateway=gateway,
        )
        self._attr_is_on: bool | None = None
        self.publish_count = 0

    def _publish_state(self) -> None:
        self.publish_count += 1
        super()._publish_state()


def _make_gateway(
    hass: HomeAssistant,
    *,
    mac: str = PRI_MAC,
    model: str = "MH201",
    options: dict[str, object] | None = None,
) -> tuple[MockConfigEntry, MyHOMEGatewayHandler]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 20000,
            CONF_PASSWORD: "secret",
            CONF_MAC: mac,
            CONF_NAME: model,
        },
        options=options or {},
        unique_id=mac,
    )
    entry.add_to_hass(hass)
    handler = MyHOMEGatewayHandler(hass, entry)
    entry.runtime_data = MyHOMERuntimeData(gateway=handler)
    return entry, handler


# ---------------------------------------------------------------------------
# 1. where_grammar: automation_dim10_info & WHO=14 canonicalization
# ---------------------------------------------------------------------------


def test_automation_dim10_info_parses_all_shutter_info_codes_and_rejects_invalid() -> None:
    """Verify WHO=2 Dimension 10 SHUTTER_INFO (4th value) extraction across valid/invalid shapes."""
    for code in (0, 12, 13, 14, 15):
        msg = OWNMessage.parse(f"*#2*56*10*10*50*001*{code}##")
        assert automation_dim10_info(msg) == code

    # Lightweight double with integer info value
    assert (
        automation_dim10_info(
            SimpleNamespace(who=2, dimension=10, dimension_value=["11", "40", "001", 12])
        )
        == 12
    )

    # Rejects wrong WHO, bool WHO, None, translations, commands, wrong dimension
    assert automation_dim10_info(None) is None
    assert automation_dim10_info(SimpleNamespace(who=True, dimension=10, dimension_value=["10", "50", "001", "12"])) is None
    assert automation_dim10_info(OWNMessage.parse("*#1*56*1*100*0##")) is None
    assert automation_dim10_info(OWNMessage.parse("*2*0*56##")) is None
    assert automation_dim10_info(OWNMessage.parse("*2*1000#0*56##")) is None
    assert automation_dim10_info(
        SimpleNamespace(who=2, dimension=10, is_translation=True, dimension_value=["10", "50", "001", "12"])
    ) is None
    assert automation_dim10_info(OWNCommand.parse("*#2*56*10##")) is None

    # Rejects missing / malformed 4th parameter
    assert automation_dim10_info(SimpleNamespace(who=2, dimension=10, dimension_value=None)) is None
    assert automation_dim10_info(OWNMessage.parse("*#2*56*10*10*50*001##")) is None
    assert automation_dim10_info(
        SimpleNamespace(who=2, dimension=10, dimension_value=["10", "50", "001", None])
    ) is None
    assert automation_dim10_info(
        SimpleNamespace(who=2, dimension=10, dimension_value=["10", "50", "001", True])
    ) is None
    assert automation_dim10_info(
        SimpleNamespace(who=2, dimension=10, dimension_value=["10", "50", "001", "abc"])
    ) is None
    assert automation_dim10_info(
        SimpleNamespace(who=2, dimension=10, dimension_value=["10", "50", "001", "   "])
    ) is None


def test_who14_where_grammar_canonicalization() -> None:
    """Verify WHO=14 events and commands normalize 0004 -> 04 and preserve scopes/#4#II."""
    assert canonical_point_spelling("0004") == "04"
    assert canonical_point_address("0004#4#01") == "04#4#01"
    assert normalize_expose_where(14, "0004") == "04"
    assert normalize_expose_where(14, "0004#4#02") == "04#4#02"

    for raw, expected in (
        ("*14*0*56##", "56"),
        ("*14*1*0004##", "04"),
        ("*14*0*0004#4#01##", "04#4#01"),
        ("*14*0*07#4#2##", "07#4#2"),
        ("*14*0*0##", "0"),
        ("*14*0*00##", "00"),
        ("*14*0*1##", "1"),
        ("*14*0*10##", "10"),
        ("*14*0*100##", "100"),
        ("*14*0*#1##", "#1"),
        ("*14*0*#1#4#02##", "#1#4#02"),
        ("*14*0*12345#9##", "12345#9"),
    ):
        ev = OWNMessage.parse(raw)
        cmd = OWNCommand.parse(raw)
        assert expose_message_where(ev) == expected
        assert expose_message_where(cmd) == expected


# ---------------------------------------------------------------------------
# 2. Pure address & helper unit tests in actuator_lock.py
# ---------------------------------------------------------------------------


def test_actuator_lock_address_and_group_helpers() -> None:
    """Verify interface, point, area, split-key, and group parsing helpers."""
    assert _normalize_iface(None) is None
    assert _normalize_iface(True) is None
    assert _normalize_iface("") is None
    assert _normalize_iface("abc") is None
    assert _normalize_iface("123") is None
    assert _normalize_iface("16") is None
    assert _normalize_iface("0") == "00"
    assert _normalize_iface(2) == "02"
    assert _normalize_iface("09") == "09"
    assert _normalize_iface("15") == "15"

    assert _is_valid_point_where("") is False
    assert _is_valid_point_where("0") is False
    assert _is_valid_point_where("00") is False
    assert _is_valid_point_where("1") is False
    assert _is_valid_point_where("9") is False
    assert _is_valid_point_where("100") is False
    assert _is_valid_point_where("105") is False
    assert _is_valid_point_where("0000") is False
    assert _is_valid_point_where("1101") is False
    assert _is_valid_point_where("0116") is False
    assert _is_valid_point_where("12345") is False
    assert _is_valid_point_where("01") is True
    assert _is_valid_point_where("10") is True
    assert _is_valid_point_where("56") is True
    assert _is_valid_point_where("0010") is True
    assert _is_valid_point_where("0809") is True
    assert _is_valid_point_where("1000") is True
    assert _is_valid_point_where("1015") is True

    # Area mapping across Area 00, 1..9 (including PL=0 point '10' and '0809'), and Area 10 ('100')
    assert _point_area("01") == "00"
    assert _point_area("09") == "00"
    assert _point_area("0010") == "00"
    assert _point_area("10") == "1"
    assert _point_area("11") == "1"
    assert _point_area("0100") == "1"
    assert _point_area("0809") == "8"
    assert _point_area("99") == "9"
    assert _point_area("1000") == "100"
    assert _point_area("1001") == "100"

    # _split_point_key edge cases
    assert _split_point_key(None) == ("", None)
    assert _split_point_key(True) == ("", None)
    assert _split_point_key("56", False) == ("", None)
    assert _split_point_key("") == ("", None)
    assert _split_point_key("#1") == ("", None)
    assert _split_point_key("12345#9") == ("", None)
    assert _split_point_key("07#4#16") == ("", None)
    assert _split_point_key("07#4#01", "02") == ("", None)
    assert _split_point_key("07#4#01", "1") == ("07", "01")
    assert _split_point_key("07", "99") == ("", None)
    assert _split_point_key("07", "2") == ("07", "02")
    assert _split_point_key("2#1") == ("", None)
    assert _split_point_key("100") == ("", None)
    assert _split_point_key("0004#4#2") == ("04", "02")

    # Group tag helpers
    assert _parse_hash_group(None) is None
    assert _parse_hash_group(1) is None
    assert _parse_hash_group("1") is None
    assert _parse_hash_group("#0") is None
    assert _parse_hash_group("#256") is None
    assert _parse_hash_group("#abc") is None
    assert _parse_hash_group("#01") == "#1"
    assert _parse_hash_group("#255") == "#255"

    assert _parse_group_ref(None) is None
    assert _parse_group_ref(True) is None
    assert _parse_group_ref(0) is None
    assert _parse_group_ref(256) is None
    assert _parse_group_ref("abc") is None
    assert _parse_group_ref(1) == "#1"
    assert _parse_group_ref("#05") == "#5"


def test_apply_lock_state_and_actuator_locks_of_helpers(hass: HomeAssistant) -> None:
    """Verify attribute dict preservation/creation and actuator_locks_of resolution."""
    ent_no_attrs = SimpleNamespace()
    apply_actuator_lock_state(ent_no_attrs, True)
    assert ent_no_attrs._actuator_locked is True
    assert ent_no_attrs._attr_extra_state_attributes == {"actuator_locked": True}

    ent_with_attrs = SimpleNamespace(_attr_extra_state_attributes={"custom": 42})
    apply_actuator_lock_state(ent_with_attrs, False)
    assert ent_with_attrs._actuator_locked is False
    assert ent_with_attrs._attr_extra_state_attributes == {"custom": 42, "actuator_locked": False}

    _, gw = _make_gateway(hass)
    assert isinstance(gw._actuator_locks, ActuatorLockManager)
    assert actuator_locks_of(gw) is gw._actuator_locks
    assert actuator_locks_of(None) is None
    assert actuator_locks_of(SimpleNamespace(_actuator_locks="not_a_manager")) is None
    stub = SimpleNamespace(_actuator_locks=gw._actuator_locks)
    assert actuator_locks_of(stub) is gw._actuator_locks


# ---------------------------------------------------------------------------
# 3. ActuatorLockManager registration, scopes, #4#II, groups & expose skip
# ---------------------------------------------------------------------------


def test_register_filters_entities_and_manages_subscriptions(hass: HomeAssistant) -> None:
    """Verify register() accepts only point WHO=1/2 light/switch/cover entities and unsubscribes cleanly."""
    _, gw = _make_gateway(hass)
    mgr = gw._actuator_locks

    # Pre-seed lock on ("10", None) - note "10" IS valid point A=1, PL=0!
    assert mgr.observe(OWNActuatorLockEvent.parse("*14*0*10##")) is True
    assert mgr.is_locked("10") is True
    assert mgr.is_locked("0") is None

    # Rejected shapes
    rejected_cases = [
        SimpleNamespace(_who="4", _platform="climate", _where="1"),
        SimpleNamespace(_who="1", _platform="button", _where="10"),
        SimpleNamespace(_who="2", _platform="cover", _where="#1", scope=object()),
        SimpleNamespace(_who="1", _platform="light", _where="#1", _member_entity_ids=[]),
        SimpleNamespace(_who="1", _platform="light", _where="0"),
        SimpleNamespace(_who="1", _platform="light", _where="00"),
        SimpleNamespace(_who="1", _platform="light", _where="1"),
        SimpleNamespace(_who="1", _platform="light", _where="100"),
        SimpleNamespace(_who="1", _platform="light", _where="12345#9"),
    ]
    for bad in rejected_cases:
        assert is_lockable_entity(bad) is False
        noop = mgr.register(bad)  # type: ignore[arg-type]
        noop()
        assert not hasattr(bad, "_actuator_locked")

    # Accepted point "10": receives pre-seeded lock synchronously without _publish_state()
    ent_a = _DummyPointEntity(hass, gw, who="1", platform="light", where="10")
    ent_b = _DummyPointEntity(hass, gw, who="1", platform="switch", where="10")
    unsub_a = mgr.register(ent_a)
    # Duplicate registration is idempotent
    unsub_a_dup = mgr.register(ent_a)
    unsub_b = mgr.register(ent_b)
    assert ent_a.actuator_locked is True
    assert ent_a.publish_count == 0
    assert len(mgr._entities[("10", None)]) == 2

    # Removing ent_a keeps ent_b in bucket; removing ent_b cleans up key; repeat unsub is safe
    unsub_a()
    unsub_a_dup()
    assert mgr._entities[("10", None)] == [ent_b]
    unsub_b()
    unsub_b()
    assert ("10", None) not in mgr._entities

    mgr.clear()
    assert mgr.is_locked("10") is None


def test_observe_rejections_and_interface_isolation(hass: HomeAssistant) -> None:
    """Verify observe() rejects non-WHO14, translations, invalid WHAT/WHERE, and isolates #4#II."""
    _, gw = _make_gateway(hass)
    mgr = gw._actuator_locks

    ent_main = _DummyPointEntity(hass, gw, who="2", platform="cover", where="07")
    ent_if01 = _DummyPointEntity(hass, gw, who="2", platform="cover", where="07", interface="01")
    ent_if02 = _DummyPointEntity(hass, gw, who="1", platform="light", where="07", interface="02")
    mgr.register(ent_main)
    mgr.register(ent_if01)
    mgr.register(ent_if02)

    assert mgr.observe(None) is False
    assert mgr.observe(OWNMessage.parse("*2*0*07##")) is False
    assert mgr.observe(OWNMessage.parse("*14*1000#0*07##")) is False
    assert mgr.observe(OWNCommand.parse("*#14*07##")) is False
    assert mgr.observe(OWNActuatorLockEvent("*14*9*07##")) is False
    assert mgr.observe(SimpleNamespace()) is False
    bad_bool_what = OWNActuatorLockEvent.parse("*14*0*07##")
    with patch.object(OWNActuatorLockEvent, "what", True):
        assert mgr.observe(bad_bool_what) is False
    assert mgr.observe(OWNActuatorLockEvent.parse("*14*0*12345#9##")) is False
    assert mgr.observe(OWNActuatorLockEvent("*14*0*07#4#16##")) is False
    assert mgr.observe(OWNActuatorLockEvent("*14*0*#4#01##")) is False
    assert mgr.observe(OWNActuatorLockEvent("*14*0*##")) is False
    assert mgr.observe(OWNActuatorLockEvent("*14*0*105##")) is False

    # Lock only #4#2 (normalized to "02") via outbound command
    assert mgr.observe(OWNActuatorLockCommand.parse("*14*0*07#4#2##")) is True
    assert ent_main.actuator_locked is None
    assert ent_if01.actuator_locked is None
    assert ent_if02.actuator_locked is True
    assert mgr.is_locked("07", "2") is True
    assert mgr.is_locked("07#4#02") is True
    assert mgr.is_locked("07", "01") is None
    assert mgr.is_locked("07") is None

    # General on #4#01 only affects #4#01, not main bus or #4#02
    assert mgr.observe(OWNActuatorLockEvent.parse("*14*0*0#4#01##")) is True
    assert ent_if01.actuator_locked is True
    assert ent_main.actuator_locked is None

    # General on main bus only affects main bus (plus pre-seeded main-bus keys)
    mgr.set_point_lock("55", None, False, observed=True)
    assert mgr.observe(OWNActuatorLockEvent.parse("*14*0*0##")) is True
    assert ent_main.actuator_locked is True
    assert mgr.is_locked("55") is True
    assert mgr.observe(OWNActuatorLockCommand.parse("*14*1*0#4#02##")) is True
    assert ent_if02.actuator_locked is False


def test_observe_area_00_1_to_9_100_vs_point_10(hass: HomeAssistant) -> None:
    """Verify Area 00, 1..9, 100 scope resolution and ensure *14*0*10## is Point 10, NOT Area 10."""
    _, gw = _make_gateway(hass)
    mgr = gw._actuator_locks

    pts = {
        w: _DummyPointEntity(hass, gw, who="1", platform="light", where=w)
        for w in ("01", "09", "0010", "10", "11", "99", "1001")
    }
    pt_if01 = _DummyPointEntity(hass, gw, who="1", platform="light", where="11", interface="01")
    for ent in pts.values():
        mgr.register(ent)
    mgr.register(pt_if01)

    # 1. *14*0*10## locks ONLY Point 10, never 11 or 1001!
    assert mgr.observe(OWNActuatorLockEvent.parse("*14*0*10##")) is True
    assert pts["10"].actuator_locked is True
    assert pts["11"].actuator_locked is None
    assert pts["1001"].actuator_locked is None

    # 2. *14*0*00## locks Area 00 ("01", "09", "0010") only
    assert mgr.observe(OWNActuatorLockEvent.parse("*14*0*00##")) is True
    assert pts["01"].actuator_locked is True
    assert pts["09"].actuator_locked is True
    assert pts["0010"].actuator_locked is True
    assert pts["11"].actuator_locked is None

    # 3. *14*0*1## locks Area 1 ("10", "11") on main riser only (not 11#4#01!)
    assert mgr.observe(OWNActuatorLockCommand.parse("*14*1*10##")) is True
    assert pts["10"].actuator_locked is False
    assert mgr.observe(OWNActuatorLockEvent.parse("*14*0*1##")) is True
    assert pts["10"].actuator_locked is True
    assert pts["11"].actuator_locked is True
    assert pt_if01.actuator_locked is None

    # 4. *14*0*100## locks Area 10 ("1001"), leaving Area 9 ("99") untouched
    assert mgr.observe(OWNActuatorLockEvent.parse("*14*0*100##")) is True
    assert pts["1001"].actuator_locked is True
    assert pts["99"].actuator_locked is None

    # Unpopulated Area 5 returns False
    assert mgr.observe(OWNActuatorLockEvent.parse("*14*0*5##")) is False


def test_observe_group_resolution_and_exposed_skip(hass: HomeAssistant) -> None:
    """Verify #G group resolution across CoverScope, LightGroup, platforms configs, and expose skips."""
    entry, gw = _make_gateway(hass)
    runtime = entry.runtime_data
    mgr = gw._actuator_locks

    # Configure virtual exposed WHO=1 on "19" and WHO=2 on "59"
    exp_mgr = ExposeManager(hass, entry, gw)
    exp_mgr.bindings = [
        ExposeBinding(who=1, where="19", entity_id="light.virtual_19"),
        ExposeBinding(who=2, where="59", entity_id="cover.virtual_59"),
    ]
    runtime.expose_manager = exp_mgr

    # Direct point lock on exposed WHO=1 / WHO=2 addresses is rejected
    assert mgr.observe(OWNActuatorLockEvent.parse("*14*0*19##")) is False
    assert mgr.observe(OWNActuatorLockCommand.parse("*14*0*59##")) is False
    assert mgr.is_locked("19") is None
    assert mgr.is_locked("59") is None

    # 1. CoverFamily + CoverScope (#1 -> 55, 56, plus exposed 59)
    family = CoverFamily()
    cov_55 = _DummyPointEntity(hass, gw, who="2", platform="cover", where="55")
    cov_56 = _DummyPointEntity(hass, gw, who="2", platform="cover", where="56")
    cov_55.scope = None  # type: ignore[attr-defined]
    cov_56.scope = None  # type: ignore[attr-defined]
    family.add(cov_55)  # type: ignore[arg-type]
    family.add(cov_56)  # type: ignore[arg-type]
    scope_1 = CoverScope.of("#1", None, ("55", "56", "59"))
    assert scope_1 is not None
    fake_scope_cover = SimpleNamespace(
        scope=scope_1,
        _members=lambda: [cov_55, cov_56],
    )
    family._covers.append(fake_scope_cover)  # type: ignore[arg-type]
    mgr.register(cov_55)
    mgr.register(cov_56)

    # 2. LightGroup via router handler (#2 -> 12, 19-exposed) + runtime.entities
    lt_12 = _DummyPointEntity(hass, gw, who="1", platform="light", where="12")
    mgr.register(lt_12)
    fake_light_group = SimpleNamespace(
        _where="#2",
        _interface=None,
        _member_entity_ids=["light.lt_12"],
        _declared_members=["12", "19"],
    )
    def _group_cb(msg: Any) -> None:
        return

    bound_method = fake_light_group.__init__  # has __self__ == None on SimpleNamespace; attach bound method
    class _FakeLG:
        _where = "#2"
        _interface = None
        _member_entity_ids = ["light.lt_12"]
        _declared_members = ["12", "19"]

        def handle_event(self, _msg: Any) -> None:
            return

    lg_inst = _FakeLG()
    runtime.router.subscribe("1", ("#2",), lg_inst.handle_event)
    runtime.entities["light"] = {"lg2": lg_inst}

    # 3. runtime.platforms (#3 via group config members + point config groups list, plus #4#01 isolation)
    sw_21 = _DummyPointEntity(hass, gw, who="1", platform="switch", where="21")
    sw_22 = _DummyPointEntity(hass, gw, who="1", platform="switch", where="22")
    sw_21_if01 = _DummyPointEntity(hass, gw, who="1", platform="switch", where="21", interface="01")
    mgr.register(sw_21)
    mgr.register(sw_22)
    mgr.register(sw_21_if01)
    runtime.platforms["switch"] = {
        "grp_3": {CONF_WHERE: "#3", CONF_MEMBERS: ["21"]},
        "pt_22": {CONF_WHERE: "22", "groups": [3]},
        "pt_21_if01": {CONF_WHERE: "21", "interface": "1", "groups": ["#3"]},
        "grp_other_if": {CONF_WHERE: "#3", CONF_BUS_INTERFACE: "02", CONF_MEMBERS: ["22"]},
    }

    # Invalid group tags rejected
    assert mgr.observe(OWNActuatorLockEvent("*14*0*#0##")) is False
    assert mgr.observe(OWNActuatorLockEvent("*14*0*#256##")) is False
    assert mgr.observe(OWNActuatorLockEvent("*14*0*#99##")) is False
    _ = bound_method, _group_cb

    # Group #1 locks cov_55 & cov_56, skipping exposed 59
    assert mgr.observe(OWNActuatorLockCommand.parse("*14*0*#1##")) is True
    assert cov_55.actuator_locked is True
    assert cov_56.actuator_locked is True
    assert mgr.is_locked("59") is None

    # Group #2 locks lt_12, skipping exposed 19
    assert mgr.observe(OWNActuatorLockEvent.parse("*14*0*#2##")) is True
    assert lt_12.actuator_locked is True
    assert mgr.is_locked("19") is None

    # Group #3 on main bus locks sw_21 & sw_22, leaving sw_21#4#01 untouched; #3#4#01 locks sw_21_if01
    assert mgr.observe(OWNActuatorLockEvent.parse("*14*0*#3##")) is True
    assert sw_21.actuator_locked is True
    assert sw_22.actuator_locked is True
    assert sw_21_if01.actuator_locked is None

    assert mgr.observe(OWNActuatorLockCommand.parse("*14*0*#3#4#1##")) is True
    assert sw_21_if01.actuator_locked is True


# ---------------------------------------------------------------------------
# 4. Dim 10 set_point_lock, RestoreEntity soft restore, idempotency & clear
# ---------------------------------------------------------------------------


async def test_entity_lock_lifecycle_soft_restore_and_colocated_clear(hass: HomeAssistant) -> None:
    """Verify idempotent handle_actuator_lock, soft restore precedence, and co-located clear."""
    _, gw = _make_gateway(hass)
    mgr = gw._actuator_locks

    # Invalid keys on set_point_lock and clear_lock_from_actuation are safe no-ops
    mgr.set_point_lock("0", None, True)
    mgr.clear_lock_from_actuation("0", None)

    ent1 = _DummyPointEntity(hass, gw, who="2", platform="cover", where="56")
    ent2 = _DummyPointEntity(hass, gw, who="1", platform="switch", where="56")
    ent1._register_availability_listener()
    ent1._register_availability_listener()  # idempotent second call
    ent2._register_availability_listener()

    # Non-lockable entity and hass=None/mgr=None entity skip registration
    non_lockable = _DummyPointEntity(hass, gw, who="4", platform="climate", where="1")
    non_lockable._register_availability_listener()
    assert non_lockable._actuator_lock_registered is False

    detached_gw = SimpleNamespace(availability_signal="sig", unique_id=PRI_MAC, mac=PRI_MAC, device_registry_id=None)
    detached_ent = _DummyPointEntity(None, detached_gw, who="1", platform="light", where="12")
    detached_ent._register_actuator_lock()
    assert detached_ent._actuator_lock_registered is False

    # Soft restore via async_restore_last_state sets both co-located entities without publishing
    await ent1.async_restore_last_state(State("cover.dev_56", "open", {"actuator_locked": True}))
    assert ent1.actuator_locked is True
    assert ent2.actuator_locked is True
    assert ent1.publish_count == 0
    assert ent2.publish_count == 0
    assert mgr.is_locked("56") is True
    assert ("56", None) not in mgr._observed_keys

    # Live Dim 10 observation (SHUTTER_INFO=0 -> unlocked) overrides soft restore and marks observed
    mgr.set_point_lock("56", None, False, observed=True)
    assert ent1.actuator_locked is False
    assert ent2.actuator_locked is False
    assert ent1.publish_count == 1
    assert ent2.publish_count == 1
    assert ("56", None) in mgr._observed_keys

    # Repeat observed=True with same state is idempotent (0 extra publishes)
    mgr.set_point_lock("56", None, False, observed=True)
    ent1.handle_actuator_lock(False)
    assert ent1.publish_count == 1

    # Once observed live, a late soft restore (observed=False) is a strict no-op!
    mgr.set_point_lock("56", None, True, observed=False)
    assert mgr.is_locked("56") is False
    assert ent1.actuator_locked is False

    # Lock via Dim 10 SHUTTER_INFO=12, then self-heal via ent1._clear_actuator_lock()
    mgr.set_point_lock("56", None, True, observed=True)
    assert ent1.actuator_locked is True
    assert ent2.actuator_locked is True
    assert ent1.publish_count == 2
    assert ent2.publish_count == 2

    ent1._clear_actuator_lock()
    assert ent1.actuator_locked is False
    assert ent2.actuator_locked is False
    # ent1 updates in-place (caller's handle_event publishes ent1), while co-located ent2 IS published!
    assert ent1.publish_count == 2
    assert ent2.publish_count == 3
    assert mgr.is_locked("56") is False

    # Calling _clear_actuator_lock again when already False is a no-op
    ent1._clear_actuator_lock()
    assert ent2.publish_count == 3


async def test_added_to_hass_restore_without_manager_and_non_bool_attr(hass: HomeAssistant) -> None:
    """Verify MyHOMEEntity.async_added_to_hass soft-restores when gateway has no manager and ignores non-bool."""
    stub_gw = SimpleNamespace(
        mac=PRI_MAC,
        unique_id=PRI_MAC,
        device_registry_id=None,
        availability_signal=f"{DOMAIN}_{PRI_MAC}_availability",
        is_who_available=lambda _who: True,
    )
    ent = _DummyPointEntity(hass, stub_gw, who="1", platform="light", where="12")
    ent._poll_on_add = False
    ent.entity_id = "light.dev_12"

    with patch.object(
        ent,
        "async_get_last_state",
        AsyncMock(return_value=State("light.dev_12", "on", {"actuator_locked": "not_a_bool"})),
    ):
        await ent.async_added_to_hass()
    assert ent.actuator_locked is None

    ent2 = _DummyPointEntity(hass, stub_gw, who="1", platform="light", where="13")
    ent2._poll_on_add = False
    ent2.entity_id = "light.dev_13"
    with patch.object(
        ent2,
        "async_get_last_state",
        AsyncMock(return_value=State("light.dev_13", "off", {"actuator_locked": True})),
    ):
        await ent2.async_added_to_hass()
    assert ent2.actuator_locked is True
    assert ent2._attr_extra_state_attributes["actuator_locked"] is True

    # _clear_actuator_lock also works cleanly when gateway has no manager
    ent2._clear_actuator_lock()
    assert ent2.actuator_locked is False


# ---------------------------------------------------------------------------
# 5. Gateway Inbound Event + Outbound CommandWorkerPool ACK/NACK + Topology
# ---------------------------------------------------------------------------


async def test_gateway_events_and_command_worker_ack_vs_nack(hass: HomeAssistant) -> None:
    """Verify MH201 outbound *#*1## ACK locks, F461 *#*0## NACK rejects, and inbound event dispatch."""
    _, gw = _make_gateway(hass, model="MH201")
    gw.is_connected = True
    gw._event_session_ready.set()

    cov_56 = _DummyPointEntity(hass, gw, who="2", platform="cover", where="56")
    cov_56._register_availability_listener()

    # 1. Inbound translation event (*14*1000#0*56##) is ignored by GatewayEventDispatcher
    await gw._process_message(OWNMessage.parse("*14*1000#0*56##"))
    assert cov_56.actuator_locked is None

    # 2. Outbound *14*0*56## ACKed on CommandWorkerPool (MH201 never echoes WHO=14 on *99*1##!)
    fake_session = MagicMock()
    fake_session.is_open = True
    fake_session.last_error = None
    fake_session.connect = AsyncMock(return_value=True)
    fake_session.send = AsyncMock(return_value=True)
    fake_session.close = AsyncMock()
    fake_session.disconnect = AsyncMock()

    with patch(
        "custom_components.myhome.gateway.OWNCommandSession",
        return_value=fake_session,
    ):
        worker = asyncio.create_task(gw.sending_loop(0))
        try:
            fut_lock = await gw.send(OWNActuatorLockCommand.lock("56"))
            await asyncio.wait_for(fut_lock, timeout=2.0)
            assert cov_56.actuator_locked is True
            assert cov_56.publish_count == 1
            assert gw._actuator_locks.is_locked("56") is True

            # Subsequent inbound *14*0*56## echo (e.g. on F461/MH200N) is idempotent!
            await gw._process_message(OWNMessage.parse("*14*0*56##"))
            assert cov_56.actuator_locked is True
            assert cov_56.publish_count == 1

            # Outbound *14*0*99## NACKed (e.g. unpopulated WHERE 99 on F461 or unsupported F450)
            fake_session.send = AsyncMock(return_value=None)
            fake_session.last_error = OWNNackError("*14*0*99##")
            fut_nack = await gw.send(OWNActuatorLockCommand.lock("99"))
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(fut_nack, timeout=2.0)
            assert getattr(fut_nack, "_gateway_nack", False) is True
            assert gw._actuator_locks.is_locked("99") is None

            # Outbound *14*1*56## ACK unlocks cov_56
            fake_session.send = AsyncMock(return_value=True)
            fake_session.last_error = None
            fut_unlock = await gw.send(OWNActuatorLockCommand.unlock("56"))
            await asyncio.wait_for(fut_unlock, timeout=2.0)
            assert cov_56.actuator_locked is False
            assert cov_56.publish_count == 2

            # Handler with _actuator_locks = None inside sending_loop is safe
            saved_mgr = gw._actuator_locks
            gw._actuator_locks = None  # type: ignore[assignment]
            fut_nomgr = await gw.send(OWNActuatorLockCommand.lock("56"))
            await asyncio.wait_for(fut_nomgr, timeout=2.0)
            gw._actuator_locks = saved_mgr
        finally:
            gw._command_pool.close()
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker

    # Inbound event when _actuator_locks is None is also a safe no-op
    saved = gw._actuator_locks
    gw._actuator_locks = None  # type: ignore[assignment]
    await gw._process_message(OWNMessage.parse("*14*0*56##"))
    gw._actuator_locks = saved

    await gw.close_listener()
    assert gw._actuator_locks.is_locked("56") is None


async def test_warm_standby_and_secondary_delegation_lock_routing(hass: HomeAssistant) -> None:
    """Verify Warm Standby failover and Secondary WHO delegation route to primary _actuator_locks."""
    _, pri_gw = _make_gateway(
        hass,
        mac=PRI_MAC,
        model="F461",
        options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY},
    )
    _, sec_gw = _make_gateway(
        hass,
        mac=SEC_MAC,
        model="F454",
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: PRI_MAC,
            CONF_DELEGATED_WHOS: [2],
        },
    )
    _, stb_gw = _make_gateway(
        hass,
        mac=STB_MAC,
        model="MH200N",
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_STANDBY,
            CONF_PRIMARY_GATEWAY: PRI_MAC,
        },
    )

    pri_gw.is_connected = True
    sec_gw.is_connected = True
    stb_gw.is_connected = True

    cov_pri = _DummyPointEntity(hass, pri_gw, who="2", platform="cover", where="31")
    cov_pri._register_availability_listener()

    # 1. While primary is connected, standby has no lock owner and ignores inbound WHO=14
    assert pri_gw.actuator_lock_owner() is pri_gw._actuator_locks
    assert stb_gw.actuator_lock_owner() is None
    await stb_gw._process_message(OWNMessage.parse("*14*0*31##"))
    assert cov_pri.actuator_locked is None

    # 2. Secondary with delegated WHO=2 routes directly to primary's _actuator_locks
    assert sec_gw.actuator_lock_owner() is pri_gw._actuator_locks
    await sec_gw._process_message(OWNMessage.parse("*14*0*31##"))
    assert cov_pri.actuator_locked is True

    # 3. Primary disconnects -> Warm Standby failover becomes active lock owner!
    pri_gw.is_connected = False
    assert stb_gw.actuator_lock_owner() is pri_gw._actuator_locks
    await stb_gw._process_message(OWNMessage.parse("*14*1*31##"))
    assert cov_pri.actuator_locked is False

    # 4. Orphaned secondary/standby pointing to unknown primary MAC
    _, orphan_sec = _make_gateway(
        hass,
        mac="00:03:50:aa:bb:98",
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:ff:ff:ff",
            CONF_DELEGATED_WHOS: [1],
        },
    )
    assert orphan_sec.actuator_lock_owner() is orphan_sec._actuator_locks

    _, orphan_stb = _make_gateway(
        hass,
        mac="00:03:50:aa:bb:99",
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_STANDBY,
            CONF_PRIMARY_GATEWAY: "00:03:50:ff:ff:ff",
        },
    )
    assert orphan_stb.actuator_lock_owner() is None
