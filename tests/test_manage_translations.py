"""Tests for the translation management and anti-drift script."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.manage_translations import (
    audit_status,
    extract_key_paths,
    extract_leaf_values,
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
    """Test audit_status returns 0 in strict mode when all bundled locales are 100% complete."""
    strings_file = tmp_path / "strings.json"
    strings_file.write_text(json.dumps({"key1": "a", "key2": "b"}), encoding="utf-8")

    tr_dir = tmp_path / "translations"
    tr_dir.mkdir()
    en_file = tr_dir / "en.json"
    en_file.write_text(json.dumps({"key1": "a", "key2": "b"}), encoding="utf-8")
    fr_file = tr_dir / "fr.json"
    fr_file.write_text(json.dumps({"key1": "a_fr", "key2": "b_fr"}), encoding="utf-8")

    with patch("scripts.manage_translations.STRINGS_FILE", strings_file), patch(
        "scripts.manage_translations.TRANSLATIONS_DIR", tr_dir
    ):
        code = audit_status(verbose=True, strict=True)
        assert code == 0


def test_audit_status_missing_keys_fails_strict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test audit_status fails strict mode when a non-English locale has missing keys."""
    strings_file = tmp_path / "strings.json"
    strings_file.write_text(
        json.dumps(
            {
                "config": {"title": "Title", "desc": "Desc"},
                "services": {"reload": "Reload"},
            }
        ),
        encoding="utf-8",
    )

    tr_dir = tmp_path / "translations"
    tr_dir.mkdir()
    (tr_dir / "en.json").write_text(strings_file.read_text(encoding="utf-8"), encoding="utf-8")
    (tr_dir / "fr.json").write_text(
        json.dumps({"config": {"title": "Titre"}}),
        encoding="utf-8",
    )

    with patch("scripts.manage_translations.STRINGS_FILE", strings_file), patch(
        "scripts.manage_translations.TRANSLATIONS_DIR", tr_dir
    ):
        assert audit_status(verbose=False, strict=False) == 0
        non_strict = capsys.readouterr()
        assert "ALERT: Missing translation keys detected in fr!" in non_strict.err

        assert audit_status(verbose=False, strict=True) == 1
        strict_out = capsys.readouterr()
        assert "config.desc" in strict_out.out
        assert "services.reload" in strict_out.out
        assert "config: 1, services: 1" in strict_out.out
        assert "ALERT: Missing translation keys detected in fr!" in strict_out.err

        assert audit_status(verbose=True, strict=True) == 1
        verbose_out = capsys.readouterr()
        assert "config.desc" in verbose_out.out
        assert "services.reload" in verbose_out.out


def test_audit_status_missing_keys_truncates_unless_verbose(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Strict non-verbose mode caps long missing-key lists while verbose mode prints every key."""
    strings_data = {f"k{i:02d}": f"Value {i}" for i in range(25)}
    strings_file = tmp_path / "strings.json"
    strings_file.write_text(json.dumps(strings_data), encoding="utf-8")

    tr_dir = tmp_path / "translations"
    tr_dir.mkdir()
    (tr_dir / "en.json").write_text(json.dumps(strings_data), encoding="utf-8")
    (tr_dir / "fr.json").write_text(json.dumps({}), encoding="utf-8")

    with patch("scripts.manage_translations.STRINGS_FILE", strings_file), patch(
        "scripts.manage_translations.TRANSLATIONS_DIR", tr_dir
    ):
        assert audit_status(verbose=False, strict=True) == 1
        capped = capsys.readouterr().out
        assert "... and 5 more (re-run with -v to list all)" in capped
        assert "- k00" in capped
        assert "- k24" not in capped

        assert audit_status(verbose=True, strict=True) == 1
        full = capsys.readouterr().out
        assert "... and" not in full
        assert "- k24" in full


def test_audit_status_empty_values_fails_strict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test audit_status flags empty/whitespace-only translations in strict mode."""
    strings_file = tmp_path / "strings.json"
    strings_file.write_text(
        json.dumps({"key1": "Hello", "key2": "World", "key3": "Test"}),
        encoding="utf-8",
    )

    tr_dir = tmp_path / "translations"
    tr_dir.mkdir()
    (tr_dir / "en.json").write_text(strings_file.read_text(encoding="utf-8"), encoding="utf-8")
    (tr_dir / "fr.json").write_text(
        json.dumps({"key1": "Bonjour", "key2": "", "key3": "   "}),
        encoding="utf-8",
    )

    with patch("scripts.manage_translations.STRINGS_FILE", strings_file), patch(
        "scripts.manage_translations.TRANSLATIONS_DIR", tr_dir
    ):
        assert audit_status(verbose=True, strict=False) == 0
        capsys.readouterr()

        assert audit_status(verbose=True, strict=True) == 1
        captured = capsys.readouterr()
        assert "! key2" in captured.out
        assert "! key3" in captured.out
        assert "ALERT: Empty/blank translation values detected in fr!" in captured.err


def test_audit_status_malformed_locale_json_fails_strict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test audit_status reports malformed JSON cleanly without misattributing en.json drift."""
    strings_file = tmp_path / "strings.json"
    strings_file.write_text(json.dumps({"key1": "Hello"}), encoding="utf-8")

    tr_dir = tmp_path / "translations"
    tr_dir.mkdir()
    (tr_dir / "en.json").write_text(json.dumps({"key1": "Hello"}), encoding="utf-8")
    (tr_dir / "fr.json").write_text("{not valid json", encoding="utf-8")
    (tr_dir / "it.json").write_text("[]", encoding="utf-8")

    with patch("scripts.manage_translations.STRINGS_FILE", strings_file), patch(
        "scripts.manage_translations.TRANSLATIONS_DIR", tr_dir
    ):
        assert audit_status(verbose=True, strict=True) == 1
        captured = capsys.readouterr()
        assert "ALERT: Malformed JSON detected in fr, it!" in captured.err
        assert "en.json is out of sync" not in captured.err


def test_extract_leaf_values() -> None:
    """Test leaf value extraction preserves exact leaf values for nested dicts."""
    data = {"a": {"b": "val"}, "c": "other"}
    assert extract_leaf_values(data) == {("a", "b"): "val", ("c",): "other"}


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
