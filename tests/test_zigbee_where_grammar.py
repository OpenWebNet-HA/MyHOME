"""Unit tests for Wave 2a OpenZigBee #9 addressing, validators, discovery, ignored, and health."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from OWNd.message import OWNMessage
from OWNd.profiles import F454Profile, Legrand3578Profile
from voluptuous import Invalid

from custom_components.myhome.const import CONF_PLATFORMS
from custom_components.myhome.device_health import (
    DOCUMENTED_EVENTS,
    DeviceHealth,
    FaultKind,
    fault_issue_id,
    message_where,
)
from custom_components.myhome.discovery import Address, is_broadcast_where
from custom_components.myhome.ignored import (
    IgnoredAddresses,
    parse_ignored_address,
    parse_ignored_addresses,
)
from custom_components.myhome.validate import (
    General,
    Group,
    MacAddress,
    PointToPoint,
    config_schema,
    cover_schema,
    format_mac,
    light_schema,
)
from custom_components.myhome.where_grammar import (
    ZIGBEE_BROADCAST_WHERE_RE,
    ZIGBEE_GROUP_WHERE_RE,
    ZIGBEE_UNICAST_WHERE_RE,
    effective_message_where,
    expose_message_where,
    is_zigbee_where,
    normalize_expose_where,
    parse_zigbee_group_where,
    parse_zigbee_where,
)


def test_zigbee_where_regex_constants() -> None:
    """Regex constants match documented ZigBee unicast, group, and broadcast shapes."""
    assert ZIGBEE_UNICAST_WHERE_RE.fullmatch("32818401#9") is not None
    assert ZIGBEE_UNICAST_WHERE_RE.fullmatch("12#9") is None
    assert ZIGBEE_GROUP_WHERE_RE.fullmatch("#25#9") is not None
    assert ZIGBEE_GROUP_WHERE_RE.fullmatch("#9") is None
    assert ZIGBEE_BROADCAST_WHERE_RE.fullmatch("0#9") is not None
    assert ZIGBEE_BROADCAST_WHERE_RE.fullmatch("0#00#9") is not None
    assert ZIGBEE_BROADCAST_WHERE_RE.fullmatch("0#0#9") is None


def test_context_gating_on_string_helpers() -> None:
    """Contextless and SCS strings never classify as ZigBee without address_family='zigbee'."""
    for sample in ("32818401#9", "012#9", "100#9", "#25#9", "0#9", "0#00#9", "0#01#9"):
        assert is_zigbee_where(sample) is False
        assert is_zigbee_where(sample, address_family="scs") is False
        assert is_zigbee_where(sample, address_family="zigbee") is True

    assert parse_zigbee_where("32818401#9") is None
    assert parse_zigbee_where("32818401#9", address_family="scs") is None
    assert parse_zigbee_where("012#9") is None
    assert parse_zigbee_where("012#9", address_family="scs") is None
    assert parse_zigbee_group_where("#25#9") is None
    assert parse_zigbee_group_where("#25#9", address_family="scs") is None


def test_parse_zigbee_where_and_group_and_is_zigbee_where() -> None:
    """Cover all valid and invalid branches of parse_zigbee_where, parse_zigbee_group_where, is_zigbee_where."""
    assert parse_zigbee_where("32818401#9", address_family="zigbee") == ("328184", "01")
    assert parse_zigbee_where(" 702053501#9 ", address_family="zigbee") == ("7020535", "01")
    assert parse_zigbee_where("100#9", address_family="zigbee") == ("1", "00")
    assert parse_zigbee_where("012#9", address_family="zigbee") == ("0", "12")
    for bad_uni in (None, 123, "", "12#9", "12#4#9", "12#4#09", "#25#9", "0#9", "0#00#9", "abc01#9"):
        assert parse_zigbee_where(bad_uni, address_family="zigbee") is None  # type: ignore[arg-type]

    assert parse_zigbee_group_where("#1#9", address_family="zigbee") == 1
    assert parse_zigbee_group_where(" #25#9 ", address_family="zigbee") == 25
    assert parse_zigbee_group_where("#65535#9", address_family="zigbee") == 65535
    for bad_grp in (None, 25, "#0#9", "#65536#9", "#9", "32818401#9", "#abc#9", "0#9"):
        assert parse_zigbee_group_where(bad_grp, address_family="zigbee") is None  # type: ignore[arg-type]

    for bad_any in (None, 123, "", "12", "#9", "12#4#9", "12#4#09", "#0#9", "#65536#9", "0#0#9", "12#9"):
        assert is_zigbee_where(bad_any, address_family="zigbee") is False  # type: ignore[arg-type]


def test_effective_message_where_and_expose_guards() -> None:
    """effective_message_where and expose helpers strictly gate #9 on is_zigbee context."""
    zb = Legrand3578Profile()
    scs = F454Profile()

    msg_zb = OWNMessage.parse("*1*1*32818401#9##", gateway=zb)
    assert effective_message_where(msg_zb) == "32818401#9"

    msg_grp = OWNMessage.parse("*1*1*#25#9##", gateway=zb)
    assert effective_message_where(msg_grp) == "#25#9"

    msg_bcast = OWNMessage.parse("*2*0*0#9##", gateway=zb)
    assert effective_message_where(msg_bcast) == "0#9"

    msg_unit_bcast = OWNMessage.parse("*1*1*0#01#9##", gateway=zb)
    assert effective_message_where(msg_unit_bcast) == "0#01#9"

    # Without zigbee context or under SCS profile, is_zigbee is False -> bare where only.
    for ctx in (None, scs):
        msg_no_zb = OWNMessage.parse("*1*1*32818401#9##", gateway=ctx)
        assert effective_message_where(msg_no_zb) == "32818401"

    assert effective_message_where(SimpleNamespace(is_zigbee=True, zigbee_where="", where="12")) == "12"
    assert effective_message_where(SimpleNamespace(where=12)) == "12"
    assert effective_message_where(SimpleNamespace(where=None)) is None
    assert effective_message_where(SimpleNamespace(where=True)) is None
    assert effective_message_where(SimpleNamespace(where="")) is None

    # normalize_expose_where and expose_message_where
    assert normalize_expose_where(1, "0004#9", address_family="zigbee") == "0004#9"
    assert normalize_expose_where(2, "0009#9", address_family="zigbee") == "0009#9"
    assert normalize_expose_where(1, "0004") == "04"
    assert normalize_expose_where(4, "012#9") == "012#9"
    assert normalize_expose_where(1, "0004#9") == "04#9"

    zb_0004 = OWNMessage.parse("*1*1*0004#9##", gateway=zb)
    assert expose_message_where(zb_0004) == "0004#9"
    assert expose_message_where(OWNMessage.parse("*4*1*012#9##")) == "012#9"
    assert expose_message_where(OWNMessage.parse("*1*1*#9##", gateway=zb)) == "#9"


@pytest.mark.parametrize(
    ("frame", "expected_where"),
    [
        ("*4*1*012#9##", "012"),
        ("*4*1*1#9##", "1"),
        ("*22*1*2#9##", "2"),
        ("*1*1*#9##", "#9"),
    ],
)
def test_wired_scs_hash9_collisions_across_core_modules(
    frame: str,
    expected_where: str,
) -> None:
    """Wired SCS frames containing #9 stay SCS-identical without or with Legrand3578Profile."""
    for ctx in (None, F454Profile(), Legrand3578Profile()):
        msg = OWNMessage.parse(frame, gateway=ctx)
        assert msg is not None, frame
        assert msg.is_zigbee is False, (frame, ctx)
        assert msg.zigbee_where is None, (frame, ctx)
        assert Address.from_message(msg) == Address(expected_where, None)
        assert effective_message_where(msg) == expected_where
        assert message_where(msg) == expected_where

    assert is_broadcast_where("#9") is False


def test_discovery_address_from_message_zigbee() -> None:
    """Address.from_message preserves zigbee_where and ignores empty zigbee_where."""
    zb = Legrand3578Profile()
    assert Address.from_message(OWNMessage.parse("*1*1*32818401#9##", gateway=zb)) == Address("32818401#9", None)
    assert Address.from_message(OWNMessage.parse("*2*1*702053501#9##", gateway=zb)) == Address("702053501#9", None)
    assert Address.from_message(OWNMessage.parse("*1*1*#25#9##", gateway=zb)) == Address("#25#9", None)
    assert Address.from_message(OWNMessage.parse("*1*1*0#00#9##", gateway=zb)) == Address("0#00#9", None)
    assert Address.from_message(OWNMessage.parse("*2*0*0#9##", gateway=zb)) == Address("0#9", None)
    assert Address.from_message(SimpleNamespace(is_zigbee=True, zigbee_where=None, where="12")) is None

    assert is_broadcast_where("0#9") is True
    assert is_broadcast_where("0#00#9") is True
    assert is_broadcast_where("0#01#9") is True
    assert is_broadcast_where("#9") is False
    assert is_broadcast_where("#25#9") is False
    assert is_broadcast_where("32818401#9") is False


def test_validate_eui64_and_zigbee_where_validators() -> None:
    """validate.py accepts 8-octet EUI-64s and ZigBee #9 WHEREs while keeping SCS rules intact."""
    expected_eui = "00:12:4b:00:01:02:03:04"
    for raw in (
        "00:12:4b:00:01:02:03:04",
        "00-12-4B-00-01-02-03-04",
        "00.12.4b.00.01.02.03.04",
        "00 12 4B 00 01 02 03 04",
        "00124b0001020304",
        "0:12:4b:0:1:2:3:4",
    ):
        assert format_mac(raw) == expected_eui
        assert MacAddress()(raw) == expected_eui

    assert format_mac("00:03:50:ab:cd:ef") == "00:03:50:ab:cd:ef"
    assert format_mac("00:03:50:ab:cd:ef:12") is None
    assert format_mac("00:12:4b:00:01:02:03:zz") is None
    assert format_mac(None) is None

    gen = General()
    assert gen("0") == "0"
    assert gen("0#9") == "0#9"
    assert gen("0#00#9") == "0#00#9"
    assert gen("0#01#9") == "0#01#9"
    for bad_gen in ("0#0#9", "00", "1", 0):
        with pytest.raises(Invalid):
            gen(bad_gen)

    grp = Group()
    assert grp("#1") == "#1"
    assert grp("#25#9") == "#25#9"
    assert grp("#65535#9") == "#65535#9"
    for bad_grp in ("#0#9", "#65536#9", "#0", "#256", "1", 42):
        with pytest.raises(Invalid):
            grp(bad_grp)

    p2p = PointToPoint()
    assert p2p("32818401#9") == "32818401#9"
    assert p2p("100#9") == "100#9"
    assert p2p("0004") == "04"
    for bad_p2p in ("12#9", "12#4#01", "123", "9901", 11):
        with pytest.raises(Invalid):
            p2p(bad_p2p)

    # Full config_schema + light/cover schemas with EUI-64 and ZigBee #9 addresses
    cfg = config_schema(
        {
            expected_eui: {
                "light": {
                    "lamp": {"where": "32818401#9", "name": "ZigBee Lamp"},
                    "grp": {"where": "#25#9", "name": "ZigBee Group", "members": ["32818401#9"]},
                    "all": {"where": "0#00#9", "name": "All ZigBee"},
                },
                "cover": {
                    "blind": {"where": "702053501#9", "name": "ZigBee Blind"},
                },
            }
        }
    )
    assert expected_eui in cfg
    assert "1-32818401#9" in cfg[expected_eui][CONF_PLATFORMS]["light"]
    assert "1-#25#9" in cfg[expected_eui][CONF_PLATFORMS]["light"]
    assert "2-702053501#9" in cfg[expected_eui][CONF_PLATFORMS]["cover"]
    assert light_schema({"l": {"where": "32818401#9", "name": "L"}})["1-32818401#9"]["where"] == "32818401#9"
    assert cover_schema({"c": {"where": "702053501#9", "name": "C"}})["2-702053501#9"]["where"] == "702053501#9"


def test_ignored_zigbee_addresses_and_scs_preservation() -> None:
    """ignored.py preserves #9 and #<G>#9 without breaking SCS #0/#9 or invalid abc#xyz."""
    assert parse_ignored_address("1/32818401#9") == (1, "32818401#9")
    assert parse_ignored_address("WHO 1 WHERE 32818401#9") == (1, "32818401#9")
    assert parse_ignored_address({"who": 1, "where": "32818401#9"}) == (1, "32818401#9")
    assert parse_ignored_address((1, "32818401#9")) == (1, "32818401#9")

    assert parse_ignored_address("1/#25#9") == (1, "#25#9")
    assert parse_ignored_address("2:#25#9") == (2, "#25#9")
    assert parse_ignored_address({"who": 1, "where": "#25#9"}) == (1, "#25#9")
    assert parse_ignored_address((2, "#25#9")) == (2, "#25#9")

    assert parse_ignored_address("1/0#00#9") == (1, "0#00#9")
    assert parse_ignored_address("2/0#9") == (2, "0#9")
    assert parse_ignored_address("1/#9") == (1, "9")
    assert parse_ignored_address("4/#0") == (4, "0")
    assert parse_ignored_address("1/abc#xyz") is None
    assert parse_ignored_address({"who": 1, "where": "#"}) is None
    assert parse_ignored_address((1, "#")) is None

    assert parse_ignored_addresses("1/32818401#9, 1/#25#9, 2/0#9") == [
        "1/#25#9",
        "1/32818401#9",
        "2/0#9",
    ]

    assert IgnoredAddresses._norm_where("0032818401#9") == "32818401#9"
    assert IgnoredAddresses._norm_where("#25#9") == "#25#9"
    assert IgnoredAddresses._norm_where("0#00#9") == "0#00#9"
    assert IgnoredAddresses._norm_where("0#9") == "0#9"

    ign = IgnoredAddresses(["1/32818401#9", "1/#25#9", "1/0#00#9"])
    assert ign.is_ignored(1, "32818401#9") is True
    assert ign.is_ignored(1, "0032818401#9") is True
    assert ign.is_ignored(1, "32818401") is False
    assert ign.is_ignored(1, "#25#9") is True
    assert ign.is_ignored(1, "#25") is False
    assert ign.is_ignored(1, "0#00#9") is True
    assert ign.is_ignored(1, "0") is False


async def test_device_health_zigbee_message_where_and_fault_tracking(hass) -> None:
    """device_health.py uses zigbee_where when is_zigbee=True and skips documented events."""
    assert DOCUMENTED_EVENTS == frozenset({32, 34, 39})

    zb = Legrand3578Profile()
    zb_fault = OWNMessage.parse("*1*99*32818401#9##", gateway=zb)
    zb_toggle = OWNMessage.parse("*1*32*32818401#9##", gateway=zb)
    zb_off = OWNMessage.parse("*1*0*32818401#9##", gateway=zb)
    zb_group_fault = OWNMessage.parse("*1*99*#25#9##", gateway=zb)
    zb_bcast_fault = OWNMessage.parse("*1*99*0#9##", gateway=zb)

    assert message_where(zb_fault) == "32818401#9"
    assert message_where(OWNMessage.parse("*1*99*32818401#9##")) == "32818401"
    assert message_where(SimpleNamespace(is_zigbee=True, zigbee_where="", where="12")) is None
    assert message_where(OWNMessage.parse("*1*99*74#4#01##")) == "74#4#01"

    handler = SimpleNamespace(
        hass=hass,
        config_entry=SimpleNamespace(entry_id="zb_entry", options={}, data={}),
        name="Legrand 3578",
        log_id="[zb]",
    )
    health = DeviceHealth(handler)
    health.observe(zb_group_fault)
    health.observe(zb_bcast_fault)
    health.observe(zb_toggle)
    assert health.faults == []

    health.observe(zb_fault)
    assert len(health.faults) == 1
    assert health.faults[0]["where"] == "32818401#9"
    assert fault_issue_id("zb_entry", FaultKind.UNMAPPED_STATUS, 1, "32818401#9").endswith("32818401_9")

    # Documented toggle event does not clear an active fault.
    health.observe(zb_toggle)
    assert len(health.faults) == 1

    # Explicit OFF clears it.
    health.observe(zb_off)
    assert health.faults == []
