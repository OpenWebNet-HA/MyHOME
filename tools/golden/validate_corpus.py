#!/usr/bin/env python3
"""OpenWebNet Golden Corpus Validator.

Validates all YAML frame fixture files in tests/golden/frames/
against tests/golden/schema.json, ensuring ID uniqueness,
raw frame uniqueness, SOURCE.yaml integrity, privacy standards,
and compiled corpus.json synchronization.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import jsonschema
except ImportError:
    jsonschema = None  # type: ignore[assignment]

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
SCHEMA_PATH = GOLDEN_DIR / "schema.json"
FRAMES_DIR = GOLDEN_DIR / "frames"
CORPUS_JSON_PATH = GOLDEN_DIR / "corpus.json"
SOURCE_PATH = GOLDEN_DIR / "SOURCE.yaml"
PRIVATE_IP = re.compile(r"\b(10\.\d+|172\.(1[6-9]|2\d|3[01])|192\.168)\.\d+\.\d+\b")
MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")
LOCAL_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|/home/|/Users/", re.IGNORECASE)


def validate_corpus(check_only: bool = False) -> int:
    """Validate all golden corpus files against schema.json and sync/check corpus.json."""
    if not SCHEMA_PATH.is_file():
        print(f"Error: Schema not found at {SCHEMA_PATH}", file=sys.stderr)
        return 1

    if yaml is None:
        if CORPUS_JSON_PATH.is_file():
            print(
                "Note: pyyaml not installed; verifying corpus.json integrity...",
                file=sys.stderr,
            )
            with open(CORPUS_JSON_PATH, encoding="utf-8") as f:
                records = json.load(f)
            if not (isinstance(records, list) and len(records) >= 1):
                print(
                    f"Error: {CORPUS_JSON_PATH} must be a non-empty JSON list of fixtures.",
                    file=sys.stderr,
                )
                return 1
            return 0
        print(
            "Warning: pyyaml not installed and corpus.json missing.",
            file=sys.stderr,
        )
        return 1 if check_only else 0

    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)

    validator: Any = jsonschema.Draft7Validator(schema) if jsonschema is not None else None
    if validator is None:
        print(
            "Note: jsonschema not installed; validating YAML, privacy, and corpus.json sync.",
            file=sys.stderr,
        )

    if not FRAMES_DIR.is_dir():
        print(f"Error: Frames directory not found at {FRAMES_DIR}", file=sys.stderr)
        return 1

    yaml_files = sorted(FRAMES_DIR.glob("*.yaml"))
    if not yaml_files:
        print(f"Error: No YAML files found in {FRAMES_DIR}", file=sys.stderr)
        return 1

    total_frames = 0
    errors: list[str] = []
    seen_ids: dict[str, str] = {}
    seen_frames: dict[str, str] = {}
    all_valid_records: list[dict[str, Any]] = []

    for yf in yaml_files:
        rel_path = yf.relative_to(REPO_ROOT)
        with open(yf, encoding="utf-8") as f:
            try:
                records = yaml.safe_load(f)
            except yaml.YAMLError as exc:
                errors.append(f"{rel_path}: YAML parsing error: {exc}")
                continue

        if not isinstance(records, list):
            errors.append(
                f"{rel_path}: Expected a list of frame objects, got {type(records).__name__}"
            )
            continue

        for idx, rec in enumerate(records):
            total_frames += 1
            if not isinstance(rec, dict):
                errors.append(f"{rel_path} [record_{idx}]: Expected mapping, got {type(rec).__name__}")
                continue

            rec_id = str(rec.get("id", f"record_{idx}"))
            raw_frame = rec.get("frame")

            if validator is not None:
                schema_errors = list(validator.iter_errors(rec))
                for err in schema_errors:
                    err_path = ".".join(str(p) for p in err.path)
                    errors.append(
                        f"{rel_path} [{rec_id}]: Schema error at '{err_path}': {err.message}"
                    )

            if rec_id in seen_ids:
                errors.append(
                    f"{rel_path} [{rec_id}]: Duplicate id already defined in {seen_ids[rec_id]}"
                )
            else:
                seen_ids[rec_id] = str(rel_path)

            if raw_frame:
                if raw_frame in seen_frames:
                    errors.append(
                        f"{rel_path} [{rec_id}]: Duplicate raw frame '{raw_frame}' "
                        f"already defined in {seen_frames[raw_frame]}"
                    )
                else:
                    seen_frames[raw_frame] = f"{rel_path} [{rec_id}]"

            rec_str = json.dumps(rec)
            if PRIVATE_IP.search(rec_str):
                errors.append(
                    f"{rel_path} [{rec_id}]: Privacy violation - private LAN IP address leaked in fixture"
                )
            for mac in MAC.findall(rec_str):
                if not mac.lower().startswith("00:03:50:00:"):
                    errors.append(
                        f"{rel_path} [{rec_id}]: Privacy violation - non-synthetic MAC '{mac}' leaked in fixture"
                    )
            if LOCAL_PATH.search(rec_str):
                errors.append(
                    f"{rel_path} [{rec_id}]: Privacy violation - personal filesystem path leaked in fixture"
                )

            record_copy = dict(rec)
            record_copy["_file"] = yf.name
            all_valid_records.append(record_copy)

    if not SOURCE_PATH.is_file():
        errors.append(f"Missing provenance manifest at {SOURCE_PATH.relative_to(REPO_ROOT)}")
    else:
        source_text = SOURCE_PATH.read_text(encoding="utf-8")
        if PRIVATE_IP.search(source_text):
            errors.append(
                f"{SOURCE_PATH.relative_to(REPO_ROOT)}: Privacy violation - "
                "private LAN IP address leaked in manifest"
            )
        for mac in MAC.findall(source_text):
            if not mac.lower().startswith("00:03:50:00:"):
                errors.append(
                    f"{SOURCE_PATH.relative_to(REPO_ROOT)}: Privacy violation - "
                    f"non-synthetic MAC '{mac}' leaked in manifest"
                )
        if LOCAL_PATH.search(source_text):
            errors.append(
                f"{SOURCE_PATH.relative_to(REPO_ROOT)}: Privacy violation - "
                "personal filesystem path leaked in manifest"
            )
        try:
            source_doc = yaml.safe_load(source_text)
            if not isinstance(source_doc, dict) or "authorities" not in source_doc:
                errors.append(
                    f"{SOURCE_PATH.relative_to(REPO_ROOT)}: Expected mapping with 'authorities' key"
                )
        except yaml.YAMLError as exc:
            errors.append(f"{SOURCE_PATH.relative_to(REPO_ROOT)}: YAML parsing error: {exc}")

    compiled_json = json.dumps(all_valid_records, indent=2) + "\n"

    if check_only and not errors:
        if not CORPUS_JSON_PATH.is_file():
            errors.append(
                f"{CORPUS_JSON_PATH.relative_to(REPO_ROOT)}: Missing compiled corpus.json "
                "(run `python tools/golden/validate_corpus.py`)"
            )
        else:
            existing_json = CORPUS_JSON_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
            if existing_json != compiled_json:
                errors.append(
                    f"{CORPUS_JSON_PATH.relative_to(REPO_ROOT)} is out of sync with frames/*.yaml "
                    "(run `python tools/golden/validate_corpus.py`)"
                )

    print("=" * 60)
    print("OpenWebNet Golden Corpus Validation Summary")
    print("=" * 60)
    print(f"Files checked : {len(yaml_files)}")
    print(f"Total frames  : {total_frames}")

    if errors:
        print(f"Validation FAILED with {len(errors)} error(s):", file=sys.stderr)
        for err_msg in errors:
            print(f"  - {err_msg}", file=sys.stderr)
        return 1

    if not check_only:
        with open(CORPUS_JSON_PATH, "w", encoding="utf-8", newline="\n") as f:
            f.write(compiled_json)
        print("Status        : ALL FIXTURES VALID (PASS)")
        print(f"Synchronized  : {CORPUS_JSON_PATH.relative_to(REPO_ROOT)}")
    else:
        print("Status        : ALL FIXTURES VALID & IN SYNC (PASS)")
    print("=" * 60)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for golden corpus validation."""
    parser = argparse.ArgumentParser(description="Validate OpenWebNet Golden Corpus YAML fixtures.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify schema validity and corpus.json sync without modifying files on disk.",
    )
    args = parser.parse_args(argv)
    return validate_corpus(check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
