"""Tests for the translation management and anti-drift script."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from scripts.manage_translations import (
    audit_status,
    extract_key_paths,
    format_json,
    main,
    prune_catalogs,
    prune_orphaned_keys,
    sync_en,
)


def test_extract_key_paths() -> None:
    """Test leaf key path extraction from nested dictionary structures."""
    data = {
        "config": {
            "step": {
                "user": {
                    "title": "Title",
                    "description": "Desc",
                },
            },
        },
        "services": {
            "reload": "Reload",
        },
    }
    paths = extract_key_paths(data)
    assert paths == {
        ("config", "step", "user", "title"),
        ("config", "step", "user", "description"),
        ("services", "reload"),
    }


def test_prune_orphaned_keys() -> None:
    """Test pruning keys not in allowed paths and cleaning empty dicts."""
    allowed = {
        ("config", "step", "user", "title"),
        ("services", "reload"),
    }
    catalog = {
        "config": {
            "step": {
                "user": {
                    "title": "Titre",
                    "old_field": "Vieux",
                },
                "dead_step": {
                    "field": "Mort",
                },
            },
        },
        "services": {
            "reload": "Recharger",
            "obsolete_service": "Ancien",
        },
    }
    cleaned, pruned = prune_orphaned_keys(catalog, allowed)
    assert sorted(pruned) == [
        "config.step.dead_step.field",
        "config.step.user.old_field",
        "services.obsolete_service",
    ]
    assert cleaned == {
        "config": {
            "step": {
                "user": {
                    "title": "Titre",
                },
            },
        },
        "services": {
            "reload": "Recharger",
        },
    }


def test_prune_orphaned_keys_non_dict() -> None:
    """Test non-dict node returns unchanged with empty pruned list."""
    cleaned, pruned = prune_orphaned_keys("scalar", {("test",)})
    assert cleaned == "scalar"
    assert pruned == []


def test_format_json() -> None:
    """Test standard JSON formatting for translations."""
    data = {"key": "valeur"}
    formatted = format_json(data)
    assert formatted.endswith("\n")
    assert json.loads(formatted) == data


def test_sync_en_already_synced(tmp_path: Path) -> None:
    """Test sync_en when strings.json and en.json are identical."""
    strings_file = tmp_path / "strings.json"
    en_file = tmp_path / "en.json"
    content = {"title": "MyHOME"}
    strings_file.write_text(json.dumps(content), encoding="utf-8")
    en_file.write_text(json.dumps(content), encoding="utf-8")

    with patch("scripts.manage_translations.STRINGS_FILE", strings_file), patch(
        "scripts.manage_translations.EN_FILE", en_file
    ):
        assert sync_en(check_only=False) is True
        assert sync_en(check_only=True) is True


def test_sync_en_differing(tmp_path: Path) -> None:
    """Test sync_en updates en.json and respects check_only."""
    strings_file = tmp_path / "strings.json"
    en_file = tmp_path / "en.json"
    strings_file.write_text(json.dumps({"title": "New Title"}), encoding="utf-8")
    en_file.write_text(json.dumps({"title": "Old Title"}), encoding="utf-8")

    with patch("scripts.manage_translations.STRINGS_FILE", strings_file), patch(
        "scripts.manage_translations.EN_FILE", en_file
    ):
        assert sync_en(check_only=True) is False
        assert sync_en(check_only=False) is True
        assert json.loads(en_file.read_text(encoding="utf-8")) == {"title": "New Title"}


def test_sync_en_missing_files(tmp_path: Path) -> None:
    """Test sync_en handling of non-existent files."""
    strings_file = tmp_path / "strings.json"
    en_file = tmp_path / "en.json"

    with patch("scripts.manage_translations.STRINGS_FILE", strings_file), patch(
        "scripts.manage_translations.EN_FILE", en_file
    ):
        assert sync_en(check_only=False) is False

    strings_file.write_text(json.dumps({"test": 1}), encoding="utf-8")
    with patch("scripts.manage_translations.STRINGS_FILE", strings_file), patch(
        "scripts.manage_translations.EN_FILE", en_file
    ):
        assert sync_en(check_only=True) is False
        assert sync_en(check_only=False) is True
        assert en_file.exists()


def test_prune_catalogs(tmp_path: Path) -> None:
    """Test prune_catalogs across mock translation catalogs."""
    strings_file = tmp_path / "strings.json"
    strings_file.write_text(json.dumps({"key": "val"}), encoding="utf-8")

    tr_dir = tmp_path / "translations"
    tr_dir.mkdir()
    fr_file = tr_dir / "fr.json"
    fr_file.write_text(json.dumps({"key": "val_fr", "extra": "extra_fr"}), encoding="utf-8")
    en_file = tr_dir / "en.json"
    en_file.write_text(json.dumps({"key": "val"}), encoding="utf-8")

    with patch("scripts.manage_translations.STRINGS_FILE", strings_file), patch(
        "scripts.manage_translations.TRANSLATIONS_DIR", tr_dir
    ):
        # Dry run
        dry_pruned = prune_catalogs(dry_run=True)
        assert dry_pruned == 1
        assert "extra" in json.loads(fr_file.read_text(encoding="utf-8"))

        # Actual run
        pruned = prune_catalogs(dry_run=False)
        assert pruned == 1
        assert "extra" not in json.loads(fr_file.read_text(encoding="utf-8"))
        assert "key" in json.loads(fr_file.read_text(encoding="utf-8"))


def test_audit_status(tmp_path: Path) -> None:
    """Test audit_status coverage calculations with healthy files."""
    strings_file = tmp_path / "strings.json"
    strings_file.write_text(json.dumps({"key1": "a", "key2": "b"}), encoding="utf-8")

    tr_dir = tmp_path / "translations"
    tr_dir.mkdir()
    en_file = tr_dir / "en.json"
    en_file.write_text(json.dumps({"key1": "a", "key2": "b"}), encoding="utf-8")
    fr_file = tr_dir / "fr.json"
    fr_file.write_text(json.dumps({"key1": "a"}), encoding="utf-8")

    with patch("scripts.manage_translations.STRINGS_FILE", strings_file), patch(
        "scripts.manage_translations.TRANSLATIONS_DIR", tr_dir
    ):
        code = audit_status(verbose=True, strict=True)
        assert code == 0


def test_audit_status_orphaned_fails_strict(tmp_path: Path) -> None:
    """Test audit_status returns 1 in strict mode when orphaned keys exist."""
    strings_file = tmp_path / "strings.json"
    strings_file.write_text(json.dumps({"key1": "a"}), encoding="utf-8")

    tr_dir = tmp_path / "translations"
    tr_dir.mkdir()
    en_file = tr_dir / "en.json"
    en_file.write_text(json.dumps({"key1": "a"}), encoding="utf-8")
    fr_file = tr_dir / "fr.json"
    fr_file.write_text(json.dumps({"key1": "a", "ghost_key": "b"}), encoding="utf-8")

    with patch("scripts.manage_translations.STRINGS_FILE", strings_file), patch(
        "scripts.manage_translations.TRANSLATIONS_DIR", tr_dir
    ):
        assert audit_status(verbose=True, strict=False) == 0
        assert audit_status(verbose=True, strict=True) == 1


def test_audit_status_en_drift_fails_strict(tmp_path: Path) -> None:
    """Test audit_status returns 1 in strict mode when en.json differs from strings.json."""
    strings_file = tmp_path / "strings.json"
    strings_file.write_text(json.dumps({"key1": "a", "key2": "b"}), encoding="utf-8")

    tr_dir = tmp_path / "translations"
    tr_dir.mkdir()
    en_file = tr_dir / "en.json"
    en_file.write_text(json.dumps({"key1": "a", "key2": "outdated"}), encoding="utf-8")

    with patch("scripts.manage_translations.STRINGS_FILE", strings_file), patch(
        "scripts.manage_translations.TRANSLATIONS_DIR", tr_dir
    ):
        assert audit_status(verbose=True, strict=True) == 1


def test_check_fails_on_either_drift_or_orphans() -> None:
    """Test check command exits 1 if either sync_en or audit_status fails."""
    # When sync_en fails
    with patch("sys.argv", ["manage_translations.py", "check"]), patch(
        "scripts.manage_translations.sync_en", return_value=False
    ), patch("scripts.manage_translations.audit_status", return_value=0):
        assert main() == 1

    # When audit_status fails (e.g. orphans present)
    with patch("sys.argv", ["manage_translations.py", "check"]), patch(
        "scripts.manage_translations.sync_en", return_value=True
    ), patch("scripts.manage_translations.audit_status", return_value=1):
        assert main() == 1

    # When both fail
    with patch("sys.argv", ["manage_translations.py", "check"]), patch(
        "scripts.manage_translations.sync_en", return_value=False
    ), patch("scripts.manage_translations.audit_status", return_value=1):
        assert main() == 1

    # When both pass
    with patch("sys.argv", ["manage_translations.py", "check"]), patch(
        "scripts.manage_translations.sync_en", return_value=True
    ), patch("scripts.manage_translations.audit_status", return_value=0):
        assert main() == 0


def test_main_cli_dispatch() -> None:
    """Test main entry point argument parsing and command dispatch."""
    with patch("sys.argv", ["manage_translations.py", "status"]), patch(
        "scripts.manage_translations.audit_status", return_value=0
    ) as mock_status:
        assert main() == 0
        mock_status.assert_called_once()

    with patch("sys.argv", ["manage_translations.py", "sync-en", "--check"]), patch(
        "scripts.manage_translations.sync_en", return_value=True
    ) as mock_sync:
        assert main() == 0
        mock_sync.assert_called_once_with(check_only=True)

    with patch("sys.argv", ["manage_translations.py", "prune", "--dry-run"]), patch(
        "scripts.manage_translations.prune_catalogs", return_value=0
    ) as mock_prune:
        assert main() == 0
        mock_prune.assert_called_once_with(dry_run=True)

    with patch("sys.argv", ["manage_translations.py", "check"]), patch(
        "scripts.manage_translations.sync_en", return_value=True
    ), patch("scripts.manage_translations.audit_status", return_value=0):
        assert main() == 0
