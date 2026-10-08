"""Tests for scripts/update_gateway_profiles.py."""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "update_gateway_profiles.py"

spec = importlib.util.spec_from_file_location("update_gateway_profiles", SCRIPT)
ugp = importlib.util.module_from_spec(spec)
sys.modules["update_gateway_profiles"] = ugp
spec.loader.exec_module(ugp)

END_MARKER = ugp.END_MARKER
GATEWAY_METADATA = ugp.GATEWAY_METADATA
START_MARKER = ugp.START_MARKER
check_readme_in_sync = ugp.check_readme_in_sync
extract_gateway_models_from_const = ugp.extract_gateway_models_from_const
extract_ssdp_models_from_manifest = ugp.extract_ssdp_models_from_manifest
generate_gateway_profiles_table = ugp.generate_gateway_profiles_table
main = ugp.main
update_readme = ugp.update_readme


def test_extract_gateway_models_from_const():
    """Verify gateway models extracted from const.py match expected list."""
    models = extract_gateway_models_from_const()
    assert "F454" in models
    assert "F455" in models
    assert "F461" in models
    assert "MH202" in models
    assert "MH201" in models
    assert "MyHomeServer1" in models
    assert "MH200N" in models
    assert "MH200" in models
    assert "AM4890" in models
    assert "F452" in models
    assert "F452V" in models
    assert "F453" in models
    assert "F453AV" in models
    assert "Generic" in models


def test_extract_ssdp_models_from_manifest():
    """Verify SSDP discovery models extracted from manifest.json."""
    ssdp_models = extract_ssdp_models_from_manifest()
    assert "F454" in ssdp_models
    assert "MyHomeServer1" in ssdp_models
    assert "AM4890" in ssdp_models
    assert "HL4684" in ssdp_models


def test_generate_gateway_profiles_table():
    """Verify markdown table format and content."""
    table = generate_gateway_profiles_table()
    assert "| Gateway Model | Protocol Support | Max Command Workers | Inter-Frame Delay | UPnP Discovery | Notes |" in table
    assert "| **F454** | OpenWebNet / HMAC | 4 workers | 50 ms | ✅ Port 49153 |" in table
    assert "| **MH202** | OpenWebNet / HMAC | 2 workers | 100 ms | ✅ Port 49153 |" in table
    assert "| **MH201** | OpenWebNet | 1 worker | 100 ms | ✅ Port 49153 |" in table
    assert "| **MH200N** | OpenWebNet | 1 worker | 150 ms | ✅ SSDP |" in table
    assert "| **MH200** *(Legacy)* | OpenWebNet | 1 worker | 150 ms | ✅ SSDP |" in table
    assert "| **H4890 / AM4890** | OpenWebNet | 1 worker | 50 ms | ✅ SSDP |" in table
    assert "| **Legrand 3578** | OpenWebNet (Serial) | 1 worker | 50 ms | ❌ Manual (Serial) |" in table

    for gw in GATEWAY_METADATA:
        assert gw["model"] in table


def test_real_readme_in_sync():
    """Verify repository README.md has valid markers and matches generated output."""
    in_sync, msg = check_readme_in_sync()
    assert in_sync, f"README.md is out of sync: {msg}"


def test_temp_readme_detection_and_update(tmp_path: Path):
    """Verify check and update work correctly on a temporary README copy."""
    temp_readme = tmp_path / "README.md"

    temp_readme.write_text(
        "### Gateway Profiles\n\n"
        "| Gateway Model | Protocol Support | Max Command Workers | Inter-Frame Delay | UPnP Discovery | Notes |\n"
        "|---|---|---|---|---|---|\n"
        "| **F454** | OpenWebNet / HMAC | 4 workers | 20 ms | ✅ Port 49153 | Old notes |\n\n"
        "---\n",
        encoding="utf-8",
    )

    in_sync, msg = check_readme_in_sync(temp_readme)
    assert not in_sync
    assert "Missing markers" in msg

    changed = update_readme(temp_readme)
    assert changed is True

    in_sync, msg = check_readme_in_sync(temp_readme)
    assert in_sync is True

    content = temp_readme.read_text(encoding="utf-8")
    assert START_MARKER in content
    assert END_MARKER in content
    assert "| **H4890 / AM4890** |" in content
    assert "*This table is automatically updated from gateway profile definitions" in content

    changed_again = update_readme(temp_readme)
    assert changed_again is False


def test_cli_flags(capsys):
    """Verify CLI --dry-run and --check behavior."""
    ret_dry = main(["--dry-run"])
    assert ret_dry == 0
    captured = capsys.readouterr()
    assert START_MARKER in captured.out
    assert END_MARKER in captured.out
    assert "| **F454** |" in captured.out

    ret_check = main(["--check"])
    assert ret_check == 0


def test_unmapped_model_raises_error(tmp_path: Path):
    """Verify that an unmapped model in const.py raises ValueError during table generation."""
    dummy_const = tmp_path / "const.py"
    dummy_const.write_text('SUPPORTED_GATEWAY_MODELS = ["F454", "UnmappedGateway9999"]\n', encoding="utf-8")

    with pytest.raises(ValueError, match="Gateway models defined in const.py missing from GATEWAY_METADATA"):
        generate_gateway_profiles_table(dummy_const)


def test_calibrate_metadata_with_ownd():
    """Verify dynamic calibration against OWNd profiles and manifest.json without mutating global state."""
    original_metadata = copy.deepcopy(ugp.GATEWAY_METADATA)

    calibrated = ugp.calibrate_metadata_with_ownd()
    assert ugp.GATEWAY_METADATA == original_metadata
    assert calibrated is not ugp.GATEWAY_METADATA

    f454 = next(gw for gw in calibrated if "F454" in str(gw["model"]))
    assert f454["max_workers"] == "4 workers"
    assert f454["delay"] == "50 ms"
    assert f454["upnp"] == "✅ Port 49153"

    mh202 = next(gw for gw in calibrated if "MH202" in str(gw["model"]))
    assert mh202["max_workers"] == "2 workers"
    assert mh202["delay"] == "100 ms"

    mh200n = next(gw for gw in calibrated if "MH200N" in str(gw["model"]))
    assert mh200n["max_workers"] == "1 worker"
    assert mh200n["delay"] == "150 ms"
    assert mh200n["upnp"] == "✅ SSDP"


def test_missing_files_and_markers_graceful_degradation(tmp_path: Path):
    """Verify graceful degradation when files or markers are missing."""
    nonexistent = tmp_path / "nonexistent.md"
    assert update_readme(nonexistent) is False

    no_markers = tmp_path / "no_markers.md"
    no_markers.write_text("# Hello World\nNo markers here.", encoding="utf-8")
    assert update_readme(no_markers) is False

    nonexistent_const = tmp_path / "const.py"
    assert extract_gateway_models_from_const(nonexistent_const) == []

