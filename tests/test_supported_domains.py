"""Tests for scripts/update_supported_domains.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "update_supported_domains.py"

spec = importlib.util.spec_from_file_location("update_supported_domains", SCRIPT)
usd = importlib.util.module_from_spec(spec)
sys.modules["update_supported_domains"] = usd
spec.loader.exec_module(usd)

DOMAIN_METADATA = usd.DOMAIN_METADATA
END_MARKER = usd.END_MARKER
START_MARKER = usd.START_MARKER
check_readme_in_sync = usd.check_readme_in_sync
extract_platforms_from_const = usd.extract_platforms_from_const
extract_who_mapping = usd.extract_who_mapping
generate_domains_table = usd.generate_domains_table
main = usd.main
update_readme = usd.update_readme


def test_extract_platforms_from_const():
    """Verify platforms extracted from const.py match expected tuple."""
    platforms = extract_platforms_from_const()
    assert "light" in platforms
    assert "switch" in platforms
    assert "cover" in platforms
    assert "climate" in platforms
    assert "binary_sensor" in platforms
    assert "sensor" in platforms
    assert "button" in platforms
    assert "media_player" in platforms
    assert "alarm_control_panel" in platforms
    assert len(platforms) == 9


def test_extract_who_mapping():
    """Verify WHO mapping extracted from supported_functions.md."""
    mapping = extract_who_mapping()
    assert mapping["light"] == "WHO=1"
    assert mapping["switch"] == "WHO=1"
    assert mapping["cover"] == "WHO=2"
    assert mapping["climate"] == "WHO=4"
    assert mapping["alarm_control_panel"] == "WHO=5"
    assert mapping["binary_sensor"] == "WHO=1 / 9 / 25"
    assert mapping["sensor"] == "WHO=1 / 4 / 18"
    assert mapping["button"] == "WHO=14 / 2"
    assert mapping["media_player"] == "WHO=16"
    assert mapping["device_trigger"] == "WHO=15 / 25"


def test_generate_domains_table():
    """Verify markdown table format and content."""
    table = generate_domains_table()
    assert "| Domain | WHO | Capabilities |" in table
    assert "| **`light`** | WHO=1 |" in table
    assert "| **`button`** | WHO=14 / 2 |" in table
    assert "| **`device_trigger`** *(Automations)* | WHO=15 / 25 |" in table

    for domain in DOMAIN_METADATA:
        meta = DOMAIN_METADATA[domain]
        assert meta["label"] in table


def test_real_readme_in_sync():
    """Verify repository README.md has valid markers and matches generated output."""
    in_sync, msg = check_readme_in_sync()
    assert in_sync, f"README.md is out of sync: {msg}"


def test_temp_readme_detection_and_update(tmp_path: Path):
    """Verify check and update work correctly on a temporary README copy."""
    temp_readme = tmp_path / "README.md"

    temp_readme.write_text(
        "### Supported Entity Domains & Automations\n\n"
        "| Domain | WHO | Capabilities |\n"
        "|---|---|---|\n"
        "| **`light`** | WHO=1 | Old description |\n\n"
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
    assert "| **`button`** | WHO=14 / 2 |" in content

    changed_again = update_readme(temp_readme)
    assert changed_again is False


def test_cli_flags(capsys):
    """Verify CLI --dry-run and --check behavior."""
    ret_dry = main(["--dry-run"])
    assert ret_dry == 0
    captured = capsys.readouterr()
    assert START_MARKER in captured.out
    assert END_MARKER in captured.out

    ret_check = main(["--check"])
    assert ret_check == 0
