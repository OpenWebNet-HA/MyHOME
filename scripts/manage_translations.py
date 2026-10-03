"""Translation lifecycle and anti-drift management utility for MyHOME.

Provides automated tooling to:
1. Synchronize `translations/en.json` from `strings.json` (developer source of truth).
2. Prune obsolete/orphaned keys from non-English translation catalogs (nl, fr, it, ...).
3. Audit translation coverage and drift across all supported locales.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CUSTOM_COMPONENTS_DIR = REPO_ROOT / "custom_components" / "myhome"
STRINGS_FILE = CUSTOM_COMPONENTS_DIR / "strings.json"
TRANSLATIONS_DIR = CUSTOM_COMPONENTS_DIR / "translations"
EN_FILE = TRANSLATIONS_DIR / "en.json"


def _rel_path(path: Path) -> str:
    """Format path relative to repository root if possible."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def extract_key_paths(node: Any, current_path: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    """Extract all leaf key paths from a nested dictionary."""
    paths: set[tuple[str, ...]] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            paths.update(extract_key_paths(value, current_path + (key,)))
    else:
        paths.add(current_path)
    return paths


def prune_orphaned_keys(
    node: Any, allowed_paths: set[tuple[str, ...]], current_path: tuple[str, ...] = ()
) -> tuple[Any, list[str]]:
    """Recursively prune keys that are not present in allowed_paths.

    Returns the cleaned dictionary (or value) and a list of pruned dot-separated keys.
    """
    if not isinstance(node, dict):
        return node, []

    pruned: list[str] = []
    cleaned: dict[str, Any] = {}

    for key, value in node.items():
        child_path = current_path + (key,)
        if isinstance(value, dict):
            child_node, child_pruned = prune_orphaned_keys(value, allowed_paths, child_path)
            pruned.extend(child_pruned)
            # Only keep dict if it contains at least one remaining child
            if child_node:
                cleaned[key] = child_node
        else:
            if child_path in allowed_paths:
                cleaned[key] = value
            else:
                pruned.append(".".join(child_path))

    return cleaned, pruned


def format_json(data: Any) -> str:
    """Format dictionary as standard Home Assistant translation JSON."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def sync_en(check_only: bool = False) -> bool:
    """Synchronize translations/en.json from strings.json.

    If check_only is True, does not modify en.json and returns False if out of sync.
    """
    if not STRINGS_FILE.exists():
        print(f"ERROR: {STRINGS_FILE} not found.", file=sys.stderr)
        return False

    strings_text = STRINGS_FILE.read_text(encoding="utf-8")
    strings_data = json.loads(strings_text)

    if not EN_FILE.exists():
        if check_only:
            print(f"ERROR: {EN_FILE} does not exist.", file=sys.stderr)
            return False
        EN_FILE.write_text(format_json(strings_data), encoding="utf-8")
        print(f"Created {_rel_path(EN_FILE)} from strings.json.")
        return True

    en_text = EN_FILE.read_text(encoding="utf-8")
    try:
        en_data = json.loads(en_text)
    except json.JSONDecodeError as err:
        print(f"ERROR: Failed to parse {EN_FILE}: {err}", file=sys.stderr)
        return False

    if strings_data == en_data:
        if not check_only:
            print(f"{_rel_path(EN_FILE)} is already synchronized with strings.json.")
        return True

    if check_only:
        print(
            f"ERROR: {_rel_path(EN_FILE)} differs from strings.json. "
            "Run 'python scripts/manage_translations.py sync-en' to update.",
            file=sys.stderr,
        )
        return False

    EN_FILE.write_text(format_json(strings_data), encoding="utf-8")
    print(f"Updated {_rel_path(EN_FILE)} from strings.json.")
    return True


def prune_catalogs(dry_run: bool = False) -> int:
    """Prune orphaned keys from all non-English translation files.

    Returns the total count of pruned keys across all files.
    """
    if not STRINGS_FILE.exists():
        print(f"ERROR: {STRINGS_FILE} not found.", file=sys.stderr)
        return 0

    strings_data = json.loads(STRINGS_FILE.read_text(encoding="utf-8"))
    allowed_paths = extract_key_paths(strings_data)

    total_pruned = 0
    for path in sorted(TRANSLATIONS_DIR.glob("*.json")):
        if path.name == "en.json":
            continue

        try:
            locale_data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            print(f"WARN: Skipping malformed JSON {path.name}: {err}", file=sys.stderr)
            continue

        cleaned_data, pruned_keys = prune_orphaned_keys(locale_data, allowed_paths)
        if pruned_keys:
            total_pruned += len(pruned_keys)
            action = "Would prune" if dry_run else "Pruned"
            print(f"{action} {len(pruned_keys)} orphaned key(s) from {path.name}:")
            for k in pruned_keys:
                print(f"  - {k}")
            if not dry_run:
                path.write_text(format_json(cleaned_data), encoding="utf-8")

    if total_pruned == 0:
        print("No orphaned keys found in any translation catalog.")
    else:
        suffix = " (dry run)" if dry_run else ""
        print(f"Total orphaned keys pruned: {total_pruned}{suffix}")

    return total_pruned


def audit_status(verbose: bool = False, strict: bool = False) -> int:
    """Audit translation coverage and drift across all translation files.

    Returns 0 if healthy, or 1 if strict checks fail.
    """
    if not STRINGS_FILE.exists():
        print(f"ERROR: {STRINGS_FILE} not found.", file=sys.stderr)
        return 1

    strings_data = json.loads(STRINGS_FILE.read_text(encoding="utf-8"))
    strings_paths = extract_key_paths(strings_data)
    total_source_keys = len(strings_paths)

    print("=" * 68)
    print("MyHOME Translation Audit & Coverage Report")
    print(f"Source: {STRINGS_FILE.name} ({total_source_keys} total translation keys)")
    print("=" * 68)
    print(f"{'Locale':<8} {'Keys':<8} {'Missing':<10} {'Orphaned':<10} {'Coverage':<10}")
    print("-" * 68)

    has_drift = False
    has_orphans = False

    for path in sorted(TRANSLATIONS_DIR.glob("*.json")):
        try:
            locale_data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            print(f"{path.stem:<8} ERROR: Malformed JSON: {err}")
            has_drift = True
            continue

        locale_paths = extract_key_paths(locale_data)
        missing = strings_paths - locale_paths
        orphaned = locale_paths - strings_paths

        if path.name == "en.json" and (missing or orphaned or locale_data != strings_data):
            has_drift = True
        if orphaned:
            has_orphans = True

        translated_count = len(locale_paths - orphaned)
        coverage_pct = (translated_count / total_source_keys * 100) if total_source_keys else 0.0

        print(
            f"{path.stem:<8} {len(locale_paths):<8} {len(missing):<10} {len(orphaned):<10} {coverage_pct:>7.1f}%"
        )

        if verbose and (missing or orphaned):
            if orphaned:
                print("  Orphaned keys:")
                for k in sorted(".".join(p) for p in orphaned):
                    print(f"    + {k}")
            if missing and path.name != "en.json":
                # Summarize missing by top-level section
                sections: dict[str, int] = {}
                for p in missing:
                    sec = p[0] if p else "root"
                    sections[sec] = sections.get(sec, 0) + 1
                sec_summary = ", ".join(f"{s}: {c}" for s, c in sorted(sections.items()))
                print(f"  Missing by section ({len(missing)} total): {sec_summary}")

    print("=" * 68)

    if has_drift:
        print("ALERT: en.json is out of sync with strings.json! Run 'sync-en'.", file=sys.stderr)
    if has_orphans:
        print("NOTICE: Orphaned keys detected. Run 'prune' to clean up.", file=sys.stderr)

    if strict and (has_drift or has_orphans):
        return 1
    return 0


def main() -> int:
    """CLI entry point for manage_translations."""
    parser = argparse.ArgumentParser(
        description="MyHOME Translation Lifecycle & Anti-Drift Management Utility"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # sync-en
    sync_parser = subparsers.add_parser(
        "sync-en", help="Synchronize translations/en.json from strings.json"
    )
    sync_parser.add_argument(
        "--check", action="store_true", help="Check only; exit 1 if en.json is out of sync"
    )

    # prune
    prune_parser = subparsers.add_parser(
        "prune", help="Prune obsolete/orphaned keys from non-English translation catalogs"
    )
    prune_parser.add_argument(
        "--dry-run", action="store_true", help="Print orphaned keys without modifying files"
    )

    # status
    status_parser = subparsers.add_parser(
        "status", help="Display translation coverage and drift report across locales"
    )
    status_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show breakdown of missing/orphaned keys"
    )
    status_parser.add_argument(
        "--strict", action="store_true", help="Exit with code 1 if drift or orphaned keys exist"
    )

    # check (CI shortcut)
    check_parser = subparsers.add_parser(
        "check", help="Run strict checks (sync-en check + status check) for CI"
    )
    check_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show breakdown of missing/orphaned keys"
    )

    args = parser.parse_args()

    if args.command == "sync-en":
        success = sync_en(check_only=args.check)
        return 0 if success else 1
    elif args.command == "prune":
        prune_catalogs(dry_run=args.dry_run)
        return 0
    elif args.command == "status":
        return audit_status(verbose=args.verbose, strict=args.strict)
    elif args.command == "check":
        synced = sync_en(check_only=True)
        status_ok = audit_status(verbose=args.verbose, strict=True) == 0
        return 0 if (synced and status_ok) else 1
    else:
        # Default behavior: show status
        return audit_status(verbose=False, strict=False)


if __name__ == "__main__":
    sys.exit(main())
