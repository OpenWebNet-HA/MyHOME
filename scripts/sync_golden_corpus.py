#!/usr/bin/env python3
"""Cross-Repository Golden Corpus Lockstep & Staged-Superset Synchronizer.

Implements Method 2 (Sub-Option 2B):
- `OpenWebNet-HA/OWNd` (`tests/golden/`) is the Canonical Golden Corpus.
- `OpenWebNet-HA/MyHOME` (`tests/golden/`) is a validated strict-superset
  staging buffer (`MyHOME >= OWNd`).

Modes:
- `--check`:
  1. Runs `validate_corpus(check_only=True)` in `MyHOME`.
  2. When `--ownd-dir` (default `../OWNd`) is present, also runs `OWNd`'s
     corpus validation, verifies byte-for-byte LF-normalized equality of
     `schema.json`, `validate_corpus.py`, `test_golden_conformance.py`, and
     `test_golden_privacy.py`, and enforces the Staged-Superset Contract:
       - 0 `OWNd` fixture IDs missing from `MyHOME`
       - 0 shared fixture IDs with divergent fields
       - 0 raw-frame ID collisions between `OWNd` and `MyHOME`
- `--from-ownd`:
  Syncs canonical `OWNd` infrastructure and frame records into `MyHOME` while
  preserving any staged `MyHOME`-only capture records, then rebuilds `corpus.json`.
- `--to-ownd`:
  Promotes all `MyHOME` Golden Corpus files and infrastructure upstream to
  `OWNd` (strictly normalized to `\\n` LF line endings) and rebuilds `OWNd`'s
  `corpus.json`.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import yaml

MYHOME_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OWND_ROOT = MYHOME_ROOT.parent / "OWNd"

LOCKSTEP_INFRA_RELPATHS: tuple[str, ...] = (
    "tests/golden/schema.json",
    "tools/golden/validate_corpus.py",
    "tests/test_golden_conformance.py",
    "tests/test_golden_privacy.py",
)


def _normalize_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _write_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_normalize_lf(text), encoding="utf-8", newline="\n")


def _run_validate_corpus(repo_root: Path, *, check_only: bool) -> int:
    validator_path = repo_root / "tools" / "golden" / "validate_corpus.py"
    if not validator_path.is_file():
        return 1
    spec = importlib.util.spec_from_file_location(
        f"validate_corpus_{repo_root.name}", validator_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {validator_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sig = inspect.signature(module.validate_corpus)
    if check_only and "check_only" not in sig.parameters:
        return 1
    if "check_only" in sig.parameters:
        return int(module.validate_corpus(check_only=check_only))
    return int(module.validate_corpus())


def _record_payload_without_meta(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if k != "_file"}


def check_staged_superset(
    myhome_root: Path = MYHOME_ROOT,
    ownd_root: Path | None = DEFAULT_OWND_ROOT,
    *,
    require_ownd: bool = False,
) -> tuple[bool, list[str]]:
    """Verify local corpus validity and (when `ownd_root` exists) cross-repo lockstep."""
    messages: list[str] = []

    rc_local = _run_validate_corpus(myhome_root, check_only=True)
    if rc_local != 0:
        messages.append("MyHOME validate_corpus(check_only=True) failed.")
        return False, messages
    messages.append("MyHOME local Golden Corpus schema & corpus.json sync: OK")

    if ownd_root is None or not (ownd_root / "tests" / "golden" / "corpus.json").is_file():
        if require_ownd:
            messages.append(
                f"Required OWNd checkout not found at {ownd_root}; cannot verify cross-repo lockstep."
            )
            return False, messages
        messages.append(
            f"Sibling OWNd checkout not present at {ownd_root}; skipped cross-repo diff."
        )
        return True, messages

    ok = True
    for rel in LOCKSTEP_INFRA_RELPATHS:
        mh_file = myhome_root / rel
        ow_file = ownd_root / rel
        if not mh_file.is_file() or not ow_file.is_file():
            messages.append(f"Missing lockstep file: {rel}")
            ok = False
            continue
        if _normalize_lf(mh_file.read_text(encoding="utf-8")) != _normalize_lf(
            ow_file.read_text(encoding="utf-8")
        ):
            messages.append(f"Infrastructure drift between MyHOME and OWNd: {rel}")
            ok = False

    rc_ownd = _run_validate_corpus(ownd_root, check_only=True)
    if rc_ownd != 0:
        messages.append(f"OWNd validate_corpus(check_only=True) failed at {ownd_root}.")
        return False, messages

    myhome_corpus: list[dict[str, Any]] = json.loads(
        (myhome_root / "tests" / "golden" / "corpus.json").read_text(encoding="utf-8")
    )
    ownd_corpus: list[dict[str, Any]] = json.loads(
        (ownd_root / "tests" / "golden" / "corpus.json").read_text(encoding="utf-8")
    )

    myhome_by_id = {r["id"]: r for r in myhome_corpus}
    ownd_by_id = {r["id"]: r for r in ownd_corpus}
    myhome_by_frame = {r["frame"]: r["id"] for r in myhome_corpus}
    ownd_by_frame = {r["frame"]: r["id"] for r in ownd_corpus}

    missing_in_myhome = sorted(set(ownd_by_id) - set(myhome_by_id))
    if missing_in_myhome:
        messages.append(
            f"MyHOME is missing {len(missing_in_myhome)} canonical OWNd fixture ID(s): "
            f"{missing_in_myhome[:10]}"
        )
        ok = False

    divergent_shared: list[str] = []
    for fid in sorted(set(ownd_by_id) & set(myhome_by_id)):
        if _record_payload_without_meta(ownd_by_id[fid]) != _record_payload_without_meta(
            myhome_by_id[fid]
        ):
            divergent_shared.append(fid)
    if divergent_shared:
        messages.append(
            f"{len(divergent_shared)} shared fixture ID(s) diverge between OWNd and MyHOME: "
            f"{divergent_shared[:10]}"
        )
        ok = False

    frame_collisions: list[str] = []
    for raw_frame in sorted(set(ownd_by_frame) & set(myhome_by_frame)):
        if ownd_by_frame[raw_frame] != myhome_by_frame[raw_frame]:
            frame_collisions.append(
                f"{raw_frame}: OWNd={ownd_by_frame[raw_frame]} vs MyHOME={myhome_by_frame[raw_frame]}"
            )
    if frame_collisions:
        messages.append(
            f"{len(frame_collisions)} raw-frame ID collision(s): {frame_collisions[:10]}"
        )
        ok = False

    staged_only = sorted(set(myhome_by_id) - set(ownd_by_id))
    if ok:
        messages.append(
            f"Staged-Superset Contract OK: MyHOME={len(myhome_by_id)} >= OWNd={len(ownd_by_id)} "
            f"({len(staged_only)} staged MyHOME-only fixture(s))."
        )
    return ok, messages


def sync_from_ownd(myhome_root: Path = MYHOME_ROOT, ownd_root: Path = DEFAULT_OWND_ROOT) -> int:
    """Pull canonical `OWNd` infrastructure and frame records into `MyHOME`, preserving staged extras."""
    if not (ownd_root / "tests" / "golden" / "frames").is_dir():
        print(f"[ERROR] OWNd golden directory not found at {ownd_root}")
        return 1

    ownd_frames_dir = ownd_root / "tests" / "golden" / "frames"
    myhome_frames_dir = myhome_root / "tests" / "golden" / "frames"

    ownd_frame_to_id: dict[str, str] = {}
    ownd_all_ids: set[str] = set()
    ownd_yaml_records: dict[str, list[dict[str, Any]]] = {}
    for yf in sorted(ownd_frames_dir.glob("*.yaml")):
        recs = yaml.safe_load(yf.read_text(encoding="utf-8")) or []
        ownd_yaml_records[yf.name] = recs
        for r in recs:
            ownd_all_ids.add(r["id"])
            ownd_frame_to_id[r["frame"]] = r["id"]

    planned_writes: dict[Path, str] = {}
    planned_unlinks: list[Path] = []
    total_staged_extras = 0

    for fname, ownd_recs in ownd_yaml_records.items():
        mh_file = myhome_frames_dir / fname
        staged_extras: list[dict[str, Any]] = []
        if mh_file.is_file():
            mh_recs = yaml.safe_load(mh_file.read_text(encoding="utf-8")) or []
            for r in mh_recs:
                if r["id"] not in ownd_all_ids:
                    if r["frame"] in ownd_frame_to_id:
                        print(
                            f"[ERROR] Raw-frame ID collision during --from-ownd on {r['frame']}: "
                            f"OWNd={ownd_frame_to_id[r['frame']]} vs MyHOME={r['id']}"
                        )
                        return 1
                    staged_extras.append(r)
        total_staged_extras += len(staged_extras)
        if not staged_extras:
            planned_writes[mh_file] = (ownd_frames_dir / fname).read_text(encoding="utf-8")
        else:
            # Keep OWNd's file text (comments, quoting) and append only the staged records.
            staged_yaml = yaml.safe_dump(staged_extras, sort_keys=False, allow_unicode=True)
            if ownd_recs:
                ownd_text = _normalize_lf(
                    (ownd_frames_dir / fname).read_text(encoding="utf-8")
                )
                planned_writes[mh_file] = ownd_text.rstrip("\n") + "\n\n" + staged_yaml
            else:
                planned_writes[mh_file] = staged_yaml

    if myhome_frames_dir.is_dir():
        for mh_only_file in sorted(myhome_frames_dir.glob("*.yaml")):
            if mh_only_file.name in ownd_yaml_records:
                continue
            mh_recs = yaml.safe_load(mh_only_file.read_text(encoding="utf-8")) or []
            remaining_extras: list[dict[str, Any]] = []
            for r in mh_recs:
                if r["id"] in ownd_all_ids:
                    continue
                if r["frame"] in ownd_frame_to_id:
                    print(
                        f"[ERROR] Raw-frame ID collision during --from-ownd on {r['frame']}: "
                        f"OWNd={ownd_frame_to_id[r['frame']]} vs MyHOME={r['id']}"
                    )
                    return 1
                remaining_extras.append(r)
            total_staged_extras += len(remaining_extras)
            if not remaining_extras:
                planned_unlinks.append(mh_only_file)
            elif len(remaining_extras) != len(mh_recs):
                planned_writes[mh_only_file] = yaml.safe_dump(
                    remaining_extras, sort_keys=False, allow_unicode=True
                )

    myhome_frames_dir.mkdir(parents=True, exist_ok=True)
    for rel in LOCKSTEP_INFRA_RELPATHS:
        src = ownd_root / rel
        if src.is_file():
            _write_lf(myhome_root / rel, src.read_text(encoding="utf-8"))

    for dst_path, content in planned_writes.items():
        _write_lf(dst_path, content)
    for stale_path in planned_unlinks:
        stale_path.unlink()

    if total_staged_extras == 0:
        for doc_rel in (
            "tests/golden/SOURCE.yaml",
            "tests/golden/README.md",
            "docs/protocol_conformance_matrix.md",
        ):
            src_doc = ownd_root / doc_rel
            if src_doc.is_file():
                _write_lf(myhome_root / doc_rel, src_doc.read_text(encoding="utf-8"))
    elif not (myhome_root / "tests/golden/SOURCE.yaml").is_file():
        src_source = ownd_root / "tests/golden/SOURCE.yaml"
        if src_source.is_file():
            _write_lf(
                myhome_root / "tests/golden/SOURCE.yaml",
                src_source.read_text(encoding="utf-8"),
            )

    return _run_validate_corpus(myhome_root, check_only=False)


def sync_to_ownd(myhome_root: Path = MYHOME_ROOT, ownd_root: Path = DEFAULT_OWND_ROOT) -> int:
    """Promote all `MyHOME` Golden Corpus fixtures & infrastructure upstream to `OWNd` (LF-normalized)."""
    if not (ownd_root / "tests" / "golden").is_dir():
        print(f"[ERROR] OWNd golden directory not found at {ownd_root}")
        return 1

    for rel in (
        *LOCKSTEP_INFRA_RELPATHS,
        "tests/golden/SOURCE.yaml",
        "tests/golden/README.md",
        "docs/protocol_conformance_matrix.md",
    ):
        src = myhome_root / rel
        if src.is_file():
            _write_lf(ownd_root / rel, src.read_text(encoding="utf-8"))

    mh_frames_dir = myhome_root / "tests" / "golden" / "frames"
    ow_frames_dir = ownd_root / "tests" / "golden" / "frames"
    mh_yaml_names = {yf.name for yf in mh_frames_dir.glob("*.yaml")}
    for stale_ow_file in sorted(ow_frames_dir.glob("*.yaml")):
        if stale_ow_file.name not in mh_yaml_names:
            stale_ow_file.unlink()
    for yf in sorted(mh_frames_dir.glob("*.yaml")):
        _write_lf(ow_frames_dir / yf.name, yf.read_text(encoding="utf-8"))

    return _run_validate_corpus(ownd_root, check_only=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize and verify the OpenWebNet Golden Corpus between OWNd and MyHOME."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check",
        action="store_true",
        help="Verify local validity and Staged-Superset lockstep against OWNd.",
    )
    group.add_argument(
        "--from-ownd",
        action="store_true",
        help="Pull canonical OWNd Golden Corpus updates into MyHOME while preserving staged extras.",
    )
    group.add_argument(
        "--to-ownd",
        action="store_true",
        help="Promote staged MyHOME Golden Corpus fixtures upstream into OWNd (LF-normalized).",
    )
    parser.add_argument(
        "--ownd-dir",
        type=Path,
        default=DEFAULT_OWND_ROOT,
        help="Path to sibling OWNd checkout (default: ../OWNd).",
    )
    parser.add_argument(
        "--require-ownd",
        action="store_true",
        help="Fail `--check` if `--ownd-dir` does not point to a valid OWNd checkout.",
    )
    args = parser.parse_args(argv)

    if args.check:
        ok, messages = check_staged_superset(
            MYHOME_ROOT, args.ownd_dir, require_ownd=args.require_ownd
        )
        for m in messages:
            print(m)
        return 0 if ok else 1
    if args.from_ownd:
        return sync_from_ownd(MYHOME_ROOT, args.ownd_dir)
    return sync_to_ownd(MYHOME_ROOT, args.ownd_dir)


if __name__ == "__main__":
    sys.exit(main())
