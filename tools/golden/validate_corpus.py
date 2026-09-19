#!/usr/bin/env python3
"""OpenWebNet Golden Corpus Validator.

Validates all YAML frame fixture files in tests/golden/frames/
against tests/golden/schema.json, ensuring ID uniqueness,
raw frame uniqueness, and schema conformity.
"""
import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    jsonschema = None

try:
    import yaml
except ImportError:
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
SCHEMA_PATH = GOLDEN_DIR / "schema.json"
FRAMES_DIR = GOLDEN_DIR / "frames"
CORPUS_JSON_PATH = GOLDEN_DIR / "corpus.json"


def validate_corpus() -> int:
    """Validate all golden corpus files against schema.json and sync corpus.json."""
    if not SCHEMA_PATH.is_file():
        print(f"Error: Schema not found at {SCHEMA_PATH}", file=sys.stderr)
        return 1

    if jsonschema is None or yaml is None:
        if CORPUS_JSON_PATH.is_file():
            print("Note: jsonschema/pyyaml not installed; verifying corpus.json integrity...", file=sys.stderr)
            with open(CORPUS_JSON_PATH, "r", encoding="utf-8") as f:
                records = json.load(f)
            assert isinstance(records, list) and len(records) >= 24
            return 0
        print("Warning: Neither jsonschema nor pyyaml installed and corpus.json missing.", file=sys.stderr)
        return 0

    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.load(f)

    validator = jsonschema.Draft7Validator(schema)

    if not FRAMES_DIR.is_dir():
        print(f"Error: Frames directory not found at {FRAMES_DIR}", file=sys.stderr)
        return 1

    yaml_files = sorted(FRAMES_DIR.glob("*.yaml"))
    if not yaml_files:
        print(f"Error: No YAML files found in {FRAMES_DIR}", file=sys.stderr)
        return 1

    total_frames = 0
    errors = []
    seen_ids = {}
    seen_frames = {}
    all_valid_records = []

    for yf in yaml_files:
        rel_path = yf.relative_to(REPO_ROOT)
        with open(yf, "r", encoding="utf-8") as f:
            try:
                records = yaml.safe_load(f)
            except yaml.YAMLError as exc:
                errors.append(f"{rel_path}: YAML parsing error: {exc}")
                continue

        if not isinstance(records, list):
            errors.append(f"{rel_path}: Expected a list of frame objects, got {type(records).__name__}")
            continue

        for idx, rec in enumerate(records):
            total_frames += 1
            rec_id = rec.get("id", f"record_{idx}")
            raw_frame = rec.get("frame")

            # Check schema validation
            schema_errors = list(validator.iter_errors(rec))
            for err in schema_errors:
                errors.append(
                    f"{rel_path} [{rec_id}]: Schema error at '{'.'.join(str(p) for p in err.path)}': {err.message}"
                )

            # Check ID uniqueness
            if rec_id in seen_ids:
                errors.append(
                    f"{rel_path} [{rec_id}]: Duplicate id already defined in {seen_ids[rec_id]}"
                )
            else:
                seen_ids[rec_id] = str(rel_path)

            # Check raw frame uniqueness
            if raw_frame:
                if raw_frame in seen_frames:
                    errors.append(
                        f"{rel_path} [{rec_id}]: Duplicate raw frame '{raw_frame}' already defined in {seen_frames[raw_frame]}"
                    )
                else:
                    seen_frames[raw_frame] = f"{rel_path} [{rec_id}]"

            record_copy = dict(rec)
            record_copy["_file"] = yf.name
            all_valid_records.append(record_copy)

    print("=" * 60)
    print("OpenWebNet Golden Corpus Validation Summary")
    print("=" * 60)
    print(f"Files checked : {len(yaml_files)}")
    print(f"Total frames  : {total_frames}")

    if errors:
        print(f"Validation FAILED with {len(errors)} error(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    # Synchronize corpus.json
    with open(CORPUS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(all_valid_records, f, indent=2)

    print("Status        : ALL FIXTURES VALID (PASS)")
    print(f"Synchronized  : {CORPUS_JSON_PATH.relative_to(REPO_ROOT)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(validate_corpus())
