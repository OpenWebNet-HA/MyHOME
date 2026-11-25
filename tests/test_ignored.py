"""Tests for ignored address parsing and matching (ignored.py)."""
from types import SimpleNamespace

import pytest

from custom_components.myhome.const import CONF_IGNORED_ADDRESSES
from custom_components.myhome.ignored import (
    IgnoredAddresses,
    parse_ignored_address,
    parse_ignored_addresses,
    validate_ignored_addresses,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1/74", (1, "74")),
        ("1:74", (1, "74")),
        ("1-74", (1, "74")),
        ("1 74", (1, "74")),
        ("WHO 1 WHERE 74", (1, "74")),
        ("who 1 where 74", (1, "74")),
        ("WHO: 1, WHERE: 74", (1, "74")),
        ("who: 1 where: 74", (1, "74")),
        ("1/74#4#01", (1, "74#4#01")),
        ("1:74#4#01", (1, "74#4#01")),
        ("1-74#4#01", (1, "74#4#01")),
        ("WHO 1 WHERE 74#4#01", (1, "74#4#01")),
        ("  1 / 74#4#01  ", (1, "74#4#01")),
        ("1/074", (1, "074")),
        ({"who": 1, "where": "74"}, (1, "74")),
        ({"who": "1", "where": 74}, (1, "74")),
        ({"WHO": 1, "WHERE": "74#4#01"}, (1, "74#4#01")),
        ((1, "74"), (1, "74")),
        ([1, "74#4#01"], (1, "74#4#01")),
        ([1, 74], (1, "74")),
        ("4/#0", (4, "0")),
        ((4, "#0"), (4, "0")),
        ([4, "#0"], (4, "0")),
        ("1/#14", (1, "#14")),
        ("1/#01", (1, "#1")),
        ("2/#05#4#1", (2, "#5#4#01")),
        ("14/#14", (14, "#14")),
        ({"who": 1, "where": "#14"}, (1, "#14")),
        ((2, "#01"), (2, "#1")),
        ("4/#1", (4, "1")),
        ("4/#01", (4, "1")),
        ("4/01", (4, "1")),
        ("4/#0#4#01", (4, "0#4#01")),
        ("4/#0#1", (4, "#0#1")),
        ("4/#0#99", (4, "#0#99")),
        ("4/#0#01", (4, "#0#1")),
        ("4/#0#01#4#1", (4, "#0#1#4#01")),
        ("4/#0#4", (4, "#0#4")),
        ("4/#0#4#4#01", (4, "#0#4#4#01")),
        ("WHO 4 WHERE #0#1", (4, "#0#1")),
        ({"who": 4, "where": "#0#01#4#1"}, (4, "#0#1#4#01")),
        ((4, "#0#4#4#1"), (4, "#0#4#4#01")),
        ("4/0#1", (4, "0#1")),
        ("4/00#1", (4, "0#1")),
        ("4/01#1", (4, "1#1")),
        ("4/1#01", (4, "1#1")),
        ("4/99#9#4#15", (4, "99#9#4#15")),
        ({"who": 4, "where": "01#9#4#0"}, (4, "1#9#4#00")),
    ],
)
def test_parse_ignored_address_valid(raw, expected):
    """Verify various valid formats parse correctly into (who, where)."""
    assert parse_ignored_address(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "invalid",
        "1/",
        "/74",
        "abc/74",
        "1/abc#xyz",
        "-1/74",
        "1/#",
        "1/#abc",
        "1/#1#2",
        "1/74#4#16",
        "1/74#4#20",
        "1/74#4#001",
        "1/74#4#abc",
        "4/#4#01",
        "4/#0#0",
        "4/#0#00",
        "4/#0#100",
        "4/#1#2",
        "4/#1#2#3",
        "4/0#0",
        "4/1#0",
        "4/1#10",
        "4/100#1",
        "4/1#2#3",
        "4/abc",
        "4/#0#1#4#20",
        {"who": 1},
        {"where": "74"},
        {"who": "not_int", "where": "74"},
        {"who": True, "where": "74"},
        {"who": 1, "where": False},
        {"who": 1, "where": object()},
        {"who": -1, "where": "74"},
        {"who": 1, "where": "７４"},
        {"who": 1, "where": "74/2"},
        ["not_int", "74"],
        ("not_int", "74"),
        (-1, "74"),
        (1, "７４"),
        (1,),
        [1],
        [1, 2, 3],
        123,
        object(),
    ],
)
def test_parse_ignored_address_invalid(raw):
    """Verify invalid inputs return None."""
    assert parse_ignored_address(raw) is None


def test_parse_ignored_addresses_multiline_and_delimiters():
    """Verify parsing multiple addresses with newlines, commas, and deduping."""
    raw = "1/74\n2/12#4#01\n1/74\n\n4/15, 1/74"
    result = parse_ignored_addresses(raw)
    assert result == ["1/74", "2/12#4#01", "4/15"]


def test_parse_ignored_addresses_list_input():
    """Verify parsing list of strings, tuples, and dicts."""
    raw = [
        "1/74",
        (2, "12#4#01"),
        {"who": 4, "where": "15"},
        "1/74",  # duplicate
    ]
    result = parse_ignored_addresses(raw)
    assert result == ["1/74", "2/12#4#01", "4/15"]


def test_parse_ignored_addresses_empty():
    """Verify empty collections and non-string bytes/scalars return empty list."""
    assert parse_ignored_addresses("") == []
    assert parse_ignored_addresses("   ") == []
    assert parse_ignored_addresses([]) == []
    assert parse_ignored_addresses(None) == []
    assert parse_ignored_addresses(b"1/74") == []
    assert parse_ignored_addresses(["invalid_only"]) == []


def test_validate_ignored_addresses_valid():
    """Verify validate_ignored_addresses returns canonical string representations and valid=True."""
    valid_text = "1/74\n2/12#4#01"
    parsed, is_valid = validate_ignored_addresses(valid_text)
    assert is_valid is True
    assert parsed == ["1/74", "2/12#4#01"]

    assert validate_ignored_addresses([]) == ([], True)
    assert validate_ignored_addresses("") == ([], True)
    assert validate_ignored_addresses(None) == ([], True)
    parsed_list, is_valid_list = validate_ignored_addresses(["1/74", "   ", "2/12"])
    assert is_valid_list is True
    assert parsed_list == ["1/74", "2/12"]


def test_validate_ignored_addresses_invalid():
    """Verify validate_ignored_addresses returns valid=False on invalid address inputs."""
    parsed, is_valid = validate_ignored_addresses("not_an_address")
    assert is_valid is False
    assert parsed == []

    parsed, is_valid = validate_ignored_addresses("1/74\nbad_entry")
    assert is_valid is False
    assert parsed == []

    parsed, is_valid = validate_ignored_addresses(["1/74", "bad_entry"])
    assert is_valid is False
    assert parsed == []

    assert validate_ignored_addresses(12345) == ([], False)


def test_ignored_addresses_initialization_and_bool():
    """Verify IgnoredAddresses empty and non-empty state."""
    empty = IgnoredAddresses()
    assert bool(empty) is False
    assert len(empty) == 0
    assert list(empty) == []
    assert empty.is_ignored(1, "74") is False

    populated = IgnoredAddresses([(1, "74"), (2, "12")])
    assert bool(populated) is True
    assert len(populated) == 2
    assert (1, "74") in populated
    assert (2, "12") in populated
    assert (1, "75") not in populated
    assert (123 in populated) is False
    assert ("1/74" in populated) is False
    assert ((1,) in populated) is False
    assert ((1, "74", "extra") in populated) is False


def test_ignored_addresses_from_config_entry():
    """Verify IgnoredAddresses can be constructed from a Home Assistant config entry."""
    entry_none = None
    assert bool(IgnoredAddresses.from_config_entry(entry_none)) is False

    entry_empty = SimpleNamespace(options={}, data={})
    assert bool(IgnoredAddresses.from_config_entry(entry_empty)) is False

    entry_options = SimpleNamespace(
        options={CONF_IGNORED_ADDRESSES: ["1/74", "2/12#4#01"]}, data={}
    )
    ign = IgnoredAddresses.from_config_entry(entry_options)
    assert bool(ign) is True
    assert (1, "74") in ign
    assert (2, "12#4#01") in ign

    # Data fallback when options are empty
    entry_data = SimpleNamespace(
        options={}, data={CONF_IGNORED_ADDRESSES: ["1/74"]}
    )
    ign_data = IgnoredAddresses.from_config_entry(entry_data)
    assert bool(ign_data) is True
    assert (1, "74") in ign_data


def test_ignored_addresses_matching_exact():
    """Verify exact WHO/WHERE matching."""
    ign = IgnoredAddresses([(1, "74")])
    assert ign.is_ignored(1, "74") is True
    assert ign.is_ignored("1", "74") is True
    assert ign.is_ignored(1, "75") is False
    assert ign.is_ignored(2, "74") is False
    assert ign.is_ignored("invalid", "74") is False


def test_ignored_addresses_matching_zero_normalization():
    """Verify leading zero normalization across wire frames."""
    # Configured as bare unpadded '74'
    ign_bare = IgnoredAddresses([(1, "74")])
    assert ign_bare.is_ignored(1, "74") is True
    assert ign_bare.is_ignored(1, "074") is True
    assert ign_bare.is_ignored(1, "0074") is True

    # Configured as padded '074'
    ign_padded = IgnoredAddresses([(1, "074")])
    assert ign_padded.is_ignored(1, "74") is True
    assert ign_padded.is_ignored(1, "074") is True


def test_ignored_addresses_matching_routed_interfaces():
    """Verify bare main-bus addresses stay isolated from F422 sub-buses (#408, #1010, #1015)."""
    # Bare WHO/WHERE 1/74: matches main-bus 74 only, never F422 sub-bus 74#4#01..15
    ign_bare = IgnoredAddresses([(1, "74")])
    assert ign_bare.is_ignored(1, "74") is True
    assert ign_bare.is_ignored(1, "74#4#01") is False
    assert ign_bare.is_ignored(1, "74#4#02") is False
    assert ign_bare.is_ignored(1, "074#4#01") is False
    assert ign_bare.is_ignored(1, "74", interface="1") is False
    assert ign_bare.is_ignored(1, "74", interface="01") is False

    # Qualified WHO/WHERE 1/74#4#01: matches only interface 01
    ign_qual = IgnoredAddresses([(1, "74#4#01")])
    assert ign_qual.is_ignored(1, "74#4#01") is True
    assert ign_qual.is_ignored(1, "074#4#01") is True
    assert ign_qual.is_ignored(1, "74#4#1") is True
    assert ign_qual.is_ignored(1, "74#4#1", interface="01") is True
    assert ign_qual.is_ignored(1, "74#4#02") is False
    assert ign_qual.is_ignored(1, "74") is False

    # Interface parameter matching
    assert ign_qual.is_ignored(1, "74", interface="1") is True
    assert ign_qual.is_ignored(1, "74", interface="01") is True
    assert ign_qual.is_ignored(1, "1-74", interface="01") is True
    assert ign_qual.is_ignored(1, "74", interface="2") is False
    assert ign_qual.is_ignored(1, "74", interface="bus") is False

    # Hyphenated WHERE stripping on main bus
    assert ign_bare.is_ignored(1, "1-74") is True
    assert ign_bare.is_ignored(1, "prefix-74") is True
    assert ign_bare.is_ignored(1, "1-74", interface="01") is False


def test_ignored_addresses_defensive_types():
    """Verify non-string or None where arguments return False safely."""
    ign = IgnoredAddresses([(1, "74")])
    assert ign.is_ignored(1, None) is False  # type: ignore[arg-type]
    assert ign.is_ignored(1, "") is False
    assert (1, None) not in ign
    assert (1, "") not in ign
    assert ("not_int", "74") not in ign

def test_ignored_addresses_matching_central_units_and_hashes():
    """Verify WHO=4 single-token #0/#Z aliasing, #0#N 4-zone centrals, and 0#N/Z#A actuators."""
    ign = IgnoredAddresses(["4/#0", "4/1"])
    assert ign.is_ignored(4, "#0") is True
    assert ign.is_ignored(4, "0") is True
    assert ign.is_ignored(4, "4-#0") is True
    assert ign.is_ignored(4, "#1") is True
    assert ign.is_ignored(4, "#01") is True
    assert ign.is_ignored(4, "1") is True
    assert ign.is_ignored(4, "#2") is False
    assert ign.is_ignored(4, "#0#1") is False
    assert ign.is_ignored(4, "0#1") is False

    # Reverse WHO=4 single-token config: 4/#1 aliases 1
    ign_hash_zone = IgnoredAddresses(["4/#1"])
    assert ign_hash_zone.is_ignored(4, "1") is True
    assert ign_hash_zone.is_ignored(4, "01") is True
    assert ign_hash_zone.is_ignored(4, "#1") is True

    # Routed 99-zone central unit #0#4#01 vs 4-zone central #0#4 vs routed 4-zone #0#4#4#01
    ign_routed_99 = IgnoredAddresses(["4/#0#4#01"])
    assert ign_routed_99.is_ignored(4, "#0#4#01") is True
    assert ign_routed_99.is_ignored(4, "0#4#01") is True
    assert ign_routed_99.is_ignored(4, "#0", interface="01") is True
    assert ign_routed_99.is_ignored(4, "#0#4") is False
    assert ign_routed_99.is_ignored(4, "#0#4#4#01") is False
    assert ign_routed_99.is_ignored(4, "0#4", interface="01") is False

    # 4-zone central #0#1..#0#99 stays strictly distinct from 0#1..0#9 pump/actuator
    ign_cu4 = IgnoredAddresses(["4/#0#01", "4/#0#04#4#1"])
    assert list(sorted(ign_cu4)) == [(4, "#0#1"), (4, "#0#4#4#01")]
    assert ign_cu4.is_ignored(4, "#0#1") is True
    assert ign_cu4.is_ignored(4, "#0#01") is True
    assert ign_cu4.is_ignored(4, "4-#0#1") is True
    assert ign_cu4.is_ignored(4, "0#1") is False
    assert ign_cu4.is_ignored(4, "1") is False
    assert ign_cu4.is_ignored(4, "#1") is False
    assert ign_cu4.is_ignored(4, "#0") is False
    assert ign_cu4.is_ignored(4, "#0#1", interface="01") is False
    # Both device_health (#0#4#4#01, interface=None) and discovery (#0#4, interface="01") match
    assert ign_cu4.is_ignored(4, "#0#4#4#01") is True
    assert ign_cu4.is_ignored(4, "#0#04#4#1") is True
    assert ign_cu4.is_ignored(4, "#0#4", interface="01") is True
    assert ign_cu4.is_ignored(4, "#0#04", interface="1") is True
    assert ign_cu4.is_ignored(4, "0#4", interface="01") is False
    assert ign_cu4.is_ignored(4, "0#4#4#01") is False

    # Ignoring pump 0#1 or zone actuator Z#A never ignores 4-zone central #0#1
    ign_pump = IgnoredAddresses(["4/00#1", "4/01#02#4#1"])
    assert list(sorted(ign_pump)) == [(4, "0#1"), (4, "1#2#4#01")]
    assert ign_pump.is_ignored(4, "0#1") is True
    assert ign_pump.is_ignored(4, "00#1") is True
    assert ign_pump.is_ignored(4, "#0#1") is False
    assert ign_pump.is_ignored(4, "#0#01") is False
    assert ign_pump.is_ignored(4, "1#2", interface="01") is True
    assert ign_pump.is_ignored(4, "01#02#4#1") is True
    assert ign_pump.is_ignored(4, "#0#1", interface="01") is False

    # Validate canonicalization on WHO=4 multi-# addresses and rejection of malformed forms
    canon, ok = validate_ignored_addresses("4/#0#01, 4/#0#01#4#1, 4/00#1, 4/01#1, 4/#0#4#01")
    assert ok is True
    assert canon == ["4/#0#1", "4/#0#1#4#01", "4/0#1", "4/0#4#01", "4/1#1"]
    for malformed in ("4/#0#0", "4/#0#100", "4/#1#2#3", "4/1#4#20"):
        assert validate_ignored_addresses(malformed) == ([], False)


def test_ignored_addresses_group_vs_point_and_area_on_non_who4():
    """Verify #G groups stay strictly distinct from G points/areas across WHO != 4."""
    for who in (0, 1, 2, 14, 15, 25):
        # Ignoring physical point/area never ignores #G group
        ign_point = IgnoredAddresses([f"{who}/14", f"{who}/01", f"{who}/1"])
        assert ign_point.is_ignored(who, "14") is True
        assert ign_point.is_ignored(who, "#14") is False
        assert ign_point.is_ignored(who, f"{who}-#14") is False
        assert ign_point.is_ignored(who, "#1") is False
        assert ign_point.is_ignored(who, "#01") is False

        # Ignoring #G group matches #1 and #01 group spellings, never point 01/14 or area 1
        ign_group = IgnoredAddresses([f"{who}/#14", f"{who}/#01", f"{who}/#05#4#1"])
        assert ign_group.is_ignored(who, "#14") is True
        assert ign_group.is_ignored(who, "#014") is True
        assert ign_group.is_ignored(who, f"{who}-#14") is True
        assert ign_group.is_ignored(who, "14") is False
        assert ign_group.is_ignored(who, f"{who}-14") is False
        assert ign_group.is_ignored(who, "#1") is True
        assert ign_group.is_ignored(who, "#01") is True
        assert ign_group.is_ignored(who, "1") is False
        assert ign_group.is_ignored(who, "01") is False
        assert ign_group.is_ignored(who, "#5", interface="01") is True
        assert ign_group.is_ignored(who, "#05#4#01") is True
        assert ign_group.is_ignored(who, "#5") is False
        assert ign_group.is_ignored(who, "5", interface="01") is False

    # Defensive _norm_where fallback branches on unrecognised query tokens
    assert IgnoredAddresses._norm_where("#4#01", 4) == "#4#01"
    assert IgnoredAddresses._norm_where("#0#100", 4) == "#0#100"
    assert IgnoredAddresses._norm_where("1#99", 4) == "1#99"
    assert IgnoredAddresses._norm_where("custom_token", 1) == "custom_token"
    assert IgnoredAddresses._norm_where("74#4#20", 1) == "74#4#20"

