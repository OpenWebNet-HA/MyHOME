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
        {"who": 1},
        {"where": "74"},
        {"who": "not_int", "where": "74"},
        ["not_int", "74"],
        ("not_int", "74"),
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
    """Verify empty collections return empty list."""
    assert parse_ignored_addresses("") == []
    assert parse_ignored_addresses("   ") == []
    assert parse_ignored_addresses([]) == []
    assert parse_ignored_addresses(None) == []


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
    """Verify bare addresses match all routed interfaces, while qualified addresses match specifically."""
    # Bare WHO/WHERE 1/74: matches everywhere on that gateway
    ign_bare = IgnoredAddresses([(1, "74")])
    assert ign_bare.is_ignored(1, "74") is True
    assert ign_bare.is_ignored(1, "74#4#01") is True
    assert ign_bare.is_ignored(1, "74#4#02") is True
    assert ign_bare.is_ignored(1, "074#4#01") is True

    # Qualified WHO/WHERE 1/74#4#01: matches only interface 01
    ign_qual = IgnoredAddresses([(1, "74#4#01")])
    assert ign_qual.is_ignored(1, "74#4#01") is True
    assert ign_qual.is_ignored(1, "074#4#01") is True
    assert ign_qual.is_ignored(1, "74#4#02") is False
    assert ign_qual.is_ignored(1, "74") is False

    # Interface parameter matching
    assert ign_qual.is_ignored(1, "74", interface="1") is True
    assert ign_qual.is_ignored(1, "74", interface="01") is True
    assert ign_qual.is_ignored(1, "74", interface="2") is False
    assert ign_qual.is_ignored(1, "74", interface="bus") is False

    # Hyphenated WHERE stripping
    assert ign_bare.is_ignored(1, "1-74") is True
    assert ign_bare.is_ignored(1, "prefix-74") is True


def test_ignored_addresses_defensive_types():
    """Verify non-string or None where arguments return False safely."""
    ign = IgnoredAddresses([(1, "74")])
    assert ign.is_ignored(1, None) is False  # type: ignore[arg-type]
    assert ign.is_ignored(1, "") is False
    assert (1, None) not in ign
    assert (1, "") not in ign
    assert ("not_int", "74") not in ign
