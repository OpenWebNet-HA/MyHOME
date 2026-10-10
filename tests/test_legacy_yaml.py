"""Standalone unit tests for legacy_yaml module."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant

from custom_components.myhome.const import (
    CONF_FILE_PATH,
)
from custom_components.myhome.legacy_yaml import _read_legacy_yaml, load_legacy_myhome_yaml


@pytest.fixture
def mock_hass(tmp_path):
    """Create a minimal mock HomeAssistant instance without spinning up core."""
    hass = MagicMock(spec=HomeAssistant)
    hass.config = MagicMock()
    hass.config.path = MagicMock(side_effect=lambda p: str(tmp_path / p))
    hass.async_add_executor_job = AsyncMock(side_effect=lambda f, *args: f(*args))
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])
    return hass


@pytest.fixture
def base_entry() -> ConfigEntry:
    """Create a minimal mock ConfigEntry."""
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_id"
    entry.data = {CONF_MAC: "00:03:50:81:22:33"}
    entry.options = {}
    return entry


def _make_configured_platforms() -> dict[str, dict[str, dict]]:
    return {
        "light": {},
        "switch": {},
        "cover": {},
        "climate": {},
        "binary_sensor": {},
        "sensor": {},
        "media_player": {},
        "button": {},
        "alarm_control_panel": {},
    }


# ── 1. File Path Resolution & Fallbacks ──────────────────────────────────────


async def test_legacy_yaml_no_file(mock_hass, base_entry, tmp_path):
    """When neither configured path nor fallback exists, loader returns without error."""
    configured_platforms = _make_configured_platforms()
    with patch("os.path.isfile", return_value=False):
        await load_legacy_myhome_yaml(mock_hass, base_entry, configured_platforms)

    for plat_dict in configured_platforms.values():
        assert len(plat_dict) == 0


async def test_legacy_yaml_missing_custom_file(mock_hass, base_entry, tmp_path):
    """When a custom file_path option is given but does not exist, loader returns cleanly."""
    base_entry.options = {CONF_FILE_PATH: str(tmp_path / "nonexistent.yaml")}
    configured_platforms = _make_configured_platforms()

    with patch("os.path.isfile", return_value=False):
        await load_legacy_myhome_yaml(mock_hass, base_entry, configured_platforms)

    for plat_dict in configured_platforms.values():
        assert len(plat_dict) == 0


async def test_legacy_yaml_fallback_to_config_myhome(mock_hass, base_entry, tmp_path):
    """When default path does not exist, loader checks /config/myhome.yaml fallback."""
    configured_platforms = _make_configured_platforms()
    yaml_content = {
        "00:03:50:81:22:33": {
            "light": {"living_room": {"where": "12", "name": "Living Light"}}
        }
    }

    def mock_isfile(path):
        return path == "/config/myhome.yaml"

    with patch("os.path.isfile", side_effect=mock_isfile), patch(
        "homeassistant.util.yaml.loader.load_yaml", return_value=yaml_content
    ):
        await load_legacy_myhome_yaml(mock_hass, base_entry, configured_platforms)

    assert "12" in configured_platforms["light"]
    assert configured_platforms["light"]["12"]["name"] == "Living Light"


async def test_legacy_yaml_custom_path_loaded(mock_hass, base_entry, tmp_path):
    """When custom file_path option points to an existing file, it is loaded."""
    custom_file = tmp_path / "custom_myhome.yaml"
    custom_file.write_text(
        "00:03:50:81:22:33:\n  switch:\n    sw1:\n      where: '31'\n      name: 'Switch 1'\n",
        encoding="utf-8",
    )
    base_entry.options = {CONF_FILE_PATH: str(custom_file)}
    configured_platforms = _make_configured_platforms()

    await load_legacy_myhome_yaml(mock_hass, base_entry, configured_platforms)

    assert "31" in configured_platforms["switch"]
    assert configured_platforms["switch"]["31"]["name"] == "Switch 1"


# ── 2. Top-Level Platforms with 0, 1, or N Gateways ─────────────────────────


async def test_legacy_yaml_top_level_platforms_zero_gateways(mock_hass, base_entry, tmp_path):
    """Top-level platforms without gateway MAC are wrapped under the entry MAC when 0 entries exist in registry."""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(
        "light:\n  kitchen:\n    where: '15'\n    name: 'Kitchen'\n",
        encoding="utf-8",
    )
    mock_hass.config_entries.async_entries.return_value = []
    configured_platforms = _make_configured_platforms()

    await load_legacy_myhome_yaml(mock_hass, base_entry, configured_platforms)

    assert "15" in configured_platforms["light"]
    assert configured_platforms["light"]["15"]["name"] == "Kitchen"


async def test_legacy_yaml_top_level_platforms_single_gateway(mock_hass, base_entry, tmp_path):
    """Top-level platforms without gateway MAC are wrapped under the entry's MAC for single gateway."""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(
        "light:\n  kitchen:\n    where: '15'\n    name: 'Kitchen'\n",
        encoding="utf-8",
    )
    # 0 or 1 gateways in HA
    mock_hass.config_entries.async_entries.return_value = [base_entry]
    configured_platforms = _make_configured_platforms()

    await load_legacy_myhome_yaml(mock_hass, base_entry, configured_platforms)

    assert "15" in configured_platforms["light"]
    assert configured_platforms["light"]["15"]["name"] == "Kitchen"


async def test_legacy_yaml_top_level_platforms_multi_gateway_rejected(
    mock_hass, base_entry, tmp_path
):
    """Top-level platforms without MAC are rejected when multiple gateways are configured."""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(
        "light:\n  kitchen:\n    where: '15'\n    name: 'Kitchen'\n",
        encoding="utf-8",
    )
    entry_2 = MagicMock(spec=ConfigEntry)
    entry_2.entry_id = "second_entry"
    mock_hass.config_entries.async_entries.return_value = [base_entry, entry_2]
    configured_platforms = _make_configured_platforms()

    await load_legacy_myhome_yaml(mock_hass, base_entry, configured_platforms)

    # Must be empty because config was rejected
    assert len(configured_platforms["light"]) == 0


# ── 3. MAC Key Matching (Table-Driven) ──────────────────────────────────────


@pytest.mark.parametrize(
    ("yaml_mac", "entry_mac", "expected_match"),
    [
        ("00:03:50:81:22:33", "00:03:50:81:22:33", True),   # Formatted == Formatted
        ("000350812233", "00:03:50:81:22:33", True),       # Raw == Formatted
        ("00:03:50:81:22:33", "000350812233", True),       # Formatted == Raw
        ("000350812233", "000350812233", True),           # Raw == Raw
        ("00-03-50-81-22-33", "00:03:50:81:22:33", True),   # Hyphens == Formatted
        ("0003.5081.2233", "00:03:50:81:22:33", True),     # Dots == Formatted
        ("00:03:50:81:22:AA", "00:03:50:81:22:aa", True),   # Upper vs lower
        ("00:03:50:81:22:33", "00:03:50:99:99:99", False),  # Different gateway
    ],
)
async def test_legacy_yaml_mac_key_matching(
    mock_hass, base_entry, tmp_path, yaml_mac, entry_mac, expected_match
):
    """Test matching gateway entries across diverse MAC formatting styles."""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(
        f"'{yaml_mac}':\n  light:\n    hallway:\n      where: '21'\n      name: 'Hallway Light'\n",
        encoding="utf-8",
    )
    base_entry.data = {CONF_MAC: entry_mac}
    configured_platforms = _make_configured_platforms()

    await load_legacy_myhome_yaml(mock_hass, base_entry, configured_platforms)

    if expected_match:
        assert "21" in configured_platforms["light"]
    else:
        assert "21" not in configured_platforms["light"]


# ── 4. Device Normalization & Routing Synthesis ─────────────────────────────


async def test_legacy_yaml_device_normalization_and_routing(mock_hass, base_entry, tmp_path):
    """Verify where default, routing synthesis, clean WHO, and zone normalization."""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(
        """
00:03:50:81:22:33:
  light:
    "41":
      name: "Light 41 (where omitted)"
    "1-52":
      where: "52"
      interface: 3
      name: "Routed Light"
  climate:
    zone_thermostat:
      zone: "4"
      interface: 2
      name: "Zone 4 Routed"
    simple_zone:
      zone: "1"
      name: "Zone 1 Local"
""",
        encoding="utf-8",
    )
    configured_platforms = _make_configured_platforms()

    await load_legacy_myhome_yaml(mock_hass, base_entry, configured_platforms)

    # 1. Device key used as where when omitted
    assert "41" in configured_platforms["light"]
    assert configured_platforms["light"]["41"]["where"] == "41"

    # 2. Interface routing key #4#<iface> (zero-padded 03) and clean_id
    assert "1-52#4#03" in configured_platforms["light"]
    assert "52#4#03" in configured_platforms["light"]

    # 3. Climate zone routing (zero-padded 02)
    assert "4#4#02" in configured_platforms["climate"]

    # 4. Local climate zone generates zone_1 alias
    assert "1" in configured_platforms["climate"]
    assert "zone_1" in configured_platforms["climate"]


# ── 5. Error Handling & Edge Cases ──────────────────────────────────────────


async def test_legacy_yaml_syntax_error(mock_hass, base_entry, tmp_path):
    """Syntax error in myhome.yaml logs error and does not raise."""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text("00:03:50:81:22:33: [invalid yaml structure: {", encoding="utf-8")
    configured_platforms = _make_configured_platforms()

    # Must complete cleanly without raising
    await load_legacy_myhome_yaml(mock_hass, base_entry, configured_platforms)
    for plat_dict in configured_platforms.values():
        assert len(plat_dict) == 0


async def test_legacy_yaml_non_dict_content(mock_hass, base_entry, tmp_path):
    """File containing valid YAML that is not a dictionary is ignored cleanly."""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text("- item1\n- item2\n", encoding="utf-8")
    configured_platforms = _make_configured_platforms()

    await load_legacy_myhome_yaml(mock_hass, base_entry, configured_platforms)
    for plat_dict in configured_platforms.values():
        assert len(plat_dict) == 0


def test_read_legacy_yaml_helper(tmp_path):
    """Unit test the _read_legacy_yaml helper directly."""
    f1 = tmp_path / "test.yaml"
    f1.write_text("a: 1\n", encoding="utf-8")

    # Primary exists
    path, data, err = _read_legacy_yaml(str(f1), None)
    assert path == str(f1)
    assert data == {"a": 1}
    assert err is None

    # Primary missing, fallback exists
    f2 = tmp_path / "fallback.yaml"
    f2.write_text("b: 2\n", encoding="utf-8")
    path, data, err = _read_legacy_yaml(str(tmp_path / "missing.yaml"), str(f2))
    assert path == str(f2)
    assert data == {"b": 2}
    assert err is None

    # Neither exists
    path, data, err = _read_legacy_yaml(str(tmp_path / "missing1.yaml"), str(tmp_path / "missing2.yaml"))
    assert path is None
    assert data is None
    assert err is None

    # Filesystem permission error is cleanly caught and returned
    with patch("os.path.isfile", side_effect=PermissionError("Permission denied")):
        path, data, err = _read_legacy_yaml(str(f1), None)
        assert path == str(f1)
        assert data is None
        assert isinstance(err, PermissionError)


async def test_legacy_yaml_executor_exception(mock_hass, base_entry):
    """When executor job raises an unexpected error, loader logs and returns cleanly."""
    mock_hass.async_add_executor_job.side_effect = RuntimeError("Executor disk failure")
    configured_platforms = _make_configured_platforms()

    await load_legacy_myhome_yaml(mock_hass, base_entry, configured_platforms)
    for plat_dict in configured_platforms.values():
        assert len(plat_dict) == 0


async def test_legacy_yaml_mac_key_fallback_branches(mock_hass, base_entry, tmp_path):
    """Test raw entry MAC branch and loop fallback with invalid MAC entries."""
    from custom_components.myhome.const import CONF_PLATFORMS

    raw_mac = "000350812233"
    base_entry.data = {CONF_MAC: raw_mac}
    configured_platforms = _make_configured_platforms()

    # Case 1: entry.data[CONF_MAC] is raw hex and present in _validated
    with patch("os.path.isfile", return_value=True), patch(
        "homeassistant.util.yaml.loader.load_yaml",
        return_value={raw_mac: {"light": {"12": {"where": "12", "name": "L12"}}}},
    ), patch("custom_components.myhome.validate.config_schema", return_value={
        raw_mac: {CONF_PLATFORMS: {"light": {"12": {"where": "12", "name": "L12"}}}}
    }):
        await load_legacy_myhome_yaml(mock_hass, base_entry, configured_platforms)
        assert "12" in configured_platforms["light"]

    # Case 2: _validated contains an invalid key that raises in format_mac, then matches
    configured_platforms = _make_configured_platforms()
    with patch("os.path.isfile", return_value=True), patch(
        "homeassistant.util.yaml.loader.load_yaml",
        return_value={12345: {}, "00:03:50:81:22:33": {"light": {"15": {"where": "15"}}}},
    ), patch("custom_components.myhome.validate.config_schema", return_value={
        12345: {},
        "00-03-50-81-22-33": {CONF_PLATFORMS: {"light": {"15": {"where": "15"}}}}
    }):
        await load_legacy_myhome_yaml(mock_hass, base_entry, configured_platforms)
        assert "15" in configured_platforms["light"]

