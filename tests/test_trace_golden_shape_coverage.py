"""Tests for the Trace-to-Golden shape coverage ratchet and Staged-Superset sync."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from OWNd.message import OWNMessage

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_JSON = REPO_ROOT / "tests" / "golden" / "corpus.json"
BASELINE_JSON = REPO_ROOT / "tests" / "golden" / "uncovered_trace_shapes_baseline.json"
HL4684_NOV13_TRACE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "traces"
    / "issue_466"
    / "myhome_trace_HL4684_all_2026-11-13T06-59-18.json"
)


def _load_module(name: str, rel_path: str) -> Any:
    target = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, target)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


shape_gate = _load_module(
    "check_trace_shape_coverage", "tools/golden/check_trace_shape_coverage.py"
)
sync_corpus = _load_module("sync_golden_corpus", "scripts/sync_golden_corpus.py")

MAX_RATCHETED_LEGACY_SHAPES = 259


def test_live_trace_shape_coverage_ratchet_passes() -> None:
    """All JSON trace fixtures must satisfy the Golden Corpus gate."""
    assert shape_gate.DEFAULT_ENCYCLOPEDIA_EVIDENCE_DIR is None
    audit = shape_gate.evaluate_trace_shape_coverage()
    assert audit.ok, shape_gate.format_audit_failures(audit)
    assert not audit.new_uncovered_shapes
    assert not audit.stale_baseline_shapes
    assert not audit.unexpected_invalid_frames
    assert not audit.stale_invalid_allowlist
    assert not audit.stale_unimplemented_allowlist
    assert not audit.graduated_unimplemented_allowlist

    baseline_payload = json.loads(BASELINE_JSON.read_text(encoding="utf-8"))
    shapes = baseline_payload["shapes"]
    assert baseline_payload["total_uncovered_shapes"] == len(shapes)
    assert len(shapes) <= MAX_RATCHETED_LEGACY_SHAPES


def test_single_repo_ci_without_encyclopedia_sibling_passes(tmp_path: Path) -> None:
    """Encyclopedia evidence is opt-in; CI without a sibling passes and --encyclopedia-evidence enforces it."""
    audit = shape_gate.evaluate_trace_shape_coverage(encyclopedia_evidence_dir=None)
    assert audit.ok, shape_gate.format_audit_failures(audit)

    ev_dir = tmp_path / "evidence" / "EVID-TEST"
    ev_dir.mkdir(parents=True)
    (ev_dir / "manifest.json").write_text(
        json.dumps({"evidence_id": "EVID-TEST"}), encoding="utf-8"
    )
    (ev_dir / "frames.jsonl").write_text(
        json.dumps({"dir": "rx", "raw": "*1*1#2#3#4*11#4#01##"}) + "\n",
        encoding="utf-8",
    )
    opt_in_audit = shape_gate.evaluate_trace_shape_coverage(
        encyclopedia_evidence_dir=tmp_path / "evidence"
    )
    assert not opt_in_audit.ok
    assert "OpenWebNet-Encyclopedia/evidence/EVID-TEST" in shape_gate.format_audit_failures(
        opt_in_audit
    )
    assert shape_gate.main(["--encyclopedia-evidence", str(tmp_path / "evidence")]) == 1


def test_negative_gate_catches_missing_hl4684_who3_shape() -> None:
    """Dropping WHO 3 `#P` load-control status shape must fail naming #466 Nov 13 & *3*1*#8##."""
    corpus_records: list[dict[str, Any]] = json.loads(
        CORPUS_JSON.read_text(encoding="utf-8")
    )
    filtered = [
        rec for rec in corpus_records if rec["frame"] not in ("*3*1*#8##", "*3*1*#1##")
    ]
    assert len(filtered) == len(corpus_records) - 2

    audit = shape_gate.evaluate_trace_shape_coverage(corpus_records=filtered)
    assert not audit.ok
    report = shape_gate.format_audit_failures(audit)
    assert "myhome_trace_HL4684_all_2026-11-13T06-59-18.json" in report
    assert "*3*1*#8##" in report


def test_stale_ratchet_entry_is_rejected(tmp_path: Path) -> None:
    """If a baseline shape becomes covered by corpus.json, CI must demand ratchet shrinkage."""
    baseline_payload = json.loads(BASELINE_JSON.read_text(encoding="utf-8"))
    covered_msg = OWNMessage.parse("*3*1*#8##")
    assert covered_msg is not None
    covered_key_str = shape_gate.serialize_shape_key(
        shape_gate.message_shape_key(covered_msg)
    )
    baseline_payload["shapes"][covered_key_str] = {
        "example_frame": "*3*1*#8##",
        "first_seen_in": "tests/fixtures/traces/issue_466/myhome_trace_HL4684_all_2026-11-13T06-59-18.json",
    }
    bloated_baseline = tmp_path / "bloated_baseline.json"
    bloated_baseline.write_text(json.dumps(baseline_payload), encoding="utf-8")

    audit = shape_gate.evaluate_trace_shape_coverage(
        baseline_path=bloated_baseline,
        encyclopedia_evidence_dir=None,
    )
    assert not audit.ok
    assert covered_key_str in shape_gate.format_audit_failures(audit)


def test_unexpected_invalid_frame_in_trace_is_caught(tmp_path: Path) -> None:
    """Any unparseable or exception-raising frame outside the allowlist must fail CI."""
    bad_trace = tmp_path / "bad_trace.json"
    bad_trace.write_text(
        json.dumps(
            {
                "frames": [
                    {"direction": "rx", "raw": "*#13**#0*22*46*45*9999##"},
                    {"direction": "rx", "raw": "*not_openwebnet*"},
                ]
            }
        ),
        encoding="utf-8",
    )
    empty_baseline = tmp_path / "baseline.json"
    empty_baseline.write_text(json.dumps({"shapes": {}}), encoding="utf-8")

    audit = shape_gate.evaluate_trace_shape_coverage(
        baseline_path=empty_baseline,
        fixtures_dir=tmp_path,
        encyclopedia_evidence_dir=None,
    )
    assert not audit.ok
    assert len(audit.unexpected_invalid_frames) == 2


def test_suggest_cli_for_covered_and_uncovered_traces(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--suggest` survives malformed/signaling frames and preserves `what_param` & `is_translation`."""
    rc = shape_gate.suggest_for_trace(HL4684_NOV13_TRACE)
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 uncovered shapes" in out

    novel_trace = tmp_path / "novel.json"
    novel_trace.write_text(
        json.dumps(
            {
                "frames": [
                    {"direction": "tx", "raw": "*1*1#2#3#4*11#4#01##"},
                    {"direction": "rx", "raw": "*#*1##"},
                    {"direction": "rx", "raw": "*#*0##"},
                    {"direction": "rx", "raw": "*#13**#0*22*46*45*9999##"},
                    {"direction": "rx", "raw": "*not_openwebnet*"},
                    {"direction": "tx", "raw": "*#1*11#4#01*77##"},
                    {"direction": "rx", "raw": "*1*1000#1#2*11#4#02##"},
                ]
            }
        ),
        encoding="utf-8",
    )
    rc2 = shape_gate.suggest_for_trace(novel_trace)
    assert rc2 == 0
    out2 = capsys.readouterr().out
    assert "3 uncovered shape(s)" in out2
    parsed_yaml = yaml.safe_load(
        "\n".join(line for line in out2.splitlines() if not line.startswith("#"))
    )
    assert parsed_yaml[0]["frame"] == "*1*1#2#3#4*11#4#01##"
    assert parsed_yaml[0]["direction"] == "command"
    assert parsed_yaml[0]["what"] == "1#2#3#4"
    assert parsed_yaml[0]["interface"] == "01"
    assert "is_translation" not in parsed_yaml[0]
    assert (
        parsed_yaml[0]["mcp_notes"]
        == "Probe only: sent in novel.json, not received there (add the reply: ACK or NACK)"
    )

    assert parsed_yaml[1]["frame"] == "*#1*11#4#01*77##"
    assert parsed_yaml[1]["direction"] == "dimension_request"
    assert parsed_yaml[1]["dimension"] == 77
    assert (
        parsed_yaml[1]["mcp_notes"]
        == "Probe only: sent in novel.json, not received there (add the reply: ACK or NACK)"
    )

    assert parsed_yaml[2]["frame"] == "*1*1000#1#2*11#4#02##"
    assert parsed_yaml[2]["direction"] == "event"
    assert parsed_yaml[2]["what"] == "1000#1#2"
    assert parsed_yaml[2]["interface"] == "02"
    assert parsed_yaml[2]["is_translation"] is True
    assert parsed_yaml[2]["mcp_notes"] == "Captured in novel.json"

    mixed_trace = tmp_path / "mixed.json"
    mixed_trace.write_text(
        json.dumps(
            {
                "frames": [
                    {"direction": "tx", "raw": "*1*1#2#3#4*11#4#01##"},
                    {"direction": "rx", "raw": "*1*1#2#3#4*12#4#01##"},
                ]
            }
        ),
        encoding="utf-8",
    )
    rc3 = shape_gate.suggest_for_trace(mixed_trace)
    assert rc3 == 0
    out3 = capsys.readouterr().out
    parsed_mixed = yaml.safe_load(
        "\n".join(line for line in out3.splitlines() if not line.startswith("#"))
    )
    assert len(parsed_mixed) == 1
    assert parsed_mixed[0]["frame"] == "*1*1#2#3#4*12#4#01##"
    assert parsed_mixed[0]["direction"] == "event"
    assert parsed_mixed[0]["mcp_notes"] == "Captured in mixed.json"


def test_who4_dim_7_19_20_discrete_bucketing_and_continuous_collapse() -> None:
    """ShapeKey must distinguish discrete WHO 4 Dim 7/19/20 codes while collapsing 101..200."""

    def _sk(raw: str) -> Any:
        msg = OWNMessage.parse(raw)
        assert msg is not None
        return shape_gate.message_shape_key(msg)

    # WHO 4 Dim 20: 100 (0% OFF) != 101..200 (active), and 14 != 15 != 16
    assert _sk("*#4*1#3*20*100##") != _sk("*#4*1#3*20*155##")
    assert _sk("*#4*1#3*20*101##") == _sk("*#4*1#3*20*200##")
    assert len({_sk("*#4*1#2*20*14##"), _sk("*#4*1#2*20*15##"), _sk("*#4*1#2*20*16##")}) == 3

    # WHO 4 Dim 19: discrete codes 0/14/100 stay distinct; 101..200 collapse
    assert len({_sk("*#4*1*19*0*14##"), _sk("*#4*1*19*0*100##"), _sk("*#4*1*19*0*150##")}) == 3
    assert _sk("*#4*1*19*0*101##") == _sk("*#4*1*19*0*199##")

    # WHO 4 Dim 7: distinct (ctx, status) stay distinct; continuous temp token collapses
    assert _sk("*#4*1*7*1*2*0200##") != _sk("*#4*1*7*1*3*0200##")
    assert _sk("*#4*1*7*1*2*0200##") == _sk("*#4*1*7*1*2*0215##")

    # Round-trip serialization
    for sample in ("*#*1##", "*1*1*11##", "*#4*1#3*20*100##"):
        key = _sk(sample)
        assert shape_gate.deserialize_shape_key(shape_gate.serialize_shape_key(key)) == key

    with pytest.raises(ValueError, match="Invalid serialized ShapeKey"):
        shape_gate.deserialize_shape_key("bad|key")


def _seed_mini_repo(root: Path, records: list[dict[str, Any]]) -> None:
    for rel in sync_corpus.LOCKSTEP_INFRA_RELPATHS:
        src = REPO_ROOT / rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    source_yaml = root / "tests" / "golden" / "SOURCE.yaml"
    shutil.copyfile(REPO_ROOT / "tests" / "golden" / "SOURCE.yaml", source_yaml)
    readme_md = root / "tests" / "golden" / "README.md"
    shutil.copyfile(REPO_ROOT / "tests" / "golden" / "README.md", readme_md)

    frames_dir = root / "tests" / "golden" / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    (frames_dir / "who01_lighting.yaml").write_text(
        yaml.safe_dump(records, sort_keys=False), encoding="utf-8"
    )
    assert sync_corpus._run_validate_corpus(root, check_only=False) == 0


def test_sync_golden_corpus_live_and_staged_superset_contracts(tmp_path: Path) -> None:
    """Verify hermetic check_staged_superset and synthetic superset/drift/collision/graduation modes."""
    ok_standalone, _ = sync_corpus.check_staged_superset(REPO_ROOT, None)
    assert ok_standalone

    ok_req_missing, msgs_req_missing = sync_corpus.check_staged_superset(
        REPO_ROOT, tmp_path / "nonexistent_ownd", require_ownd=True
    )
    assert not ok_req_missing
    assert any("Required OWNd checkout not found" in m for m in msgs_req_missing)

    rec_a = {
        "id": "lighting.event.on.point11",
        "frame": "*1*1*11##",
        "direction": "event",
        "who": 1,
        "what": 1,
        "where": "11",
        "source": "legrand-spec",
        "mcp_valid": True,
        "roundtrip": True,
    }
    rec_b = {
        "id": "lighting.event.off.point11",
        "frame": "*1*0*11##",
        "direction": "event",
        "who": 1,
        "what": 0,
        "where": "11",
        "source": "community-plant-capture",
        "mcp_valid": True,
        "roundtrip": True,
    }

    mh_dir = tmp_path / "mh"
    ow_dir = tmp_path / "ow"

    # 1. Valid staged superset: MyHOME has [rec_a, rec_b], OWNd has [rec_a]
    _seed_mini_repo(mh_dir, [rec_a, rec_b])
    _seed_mini_repo(ow_dir, [rec_a])
    ok, msgs = sync_corpus.check_staged_superset(mh_dir, ow_dir, require_ownd=True)
    assert ok, "\n".join(msgs)
    assert any("1 staged MyHOME-only fixture" in m for m in msgs)

    # 2. Missing canonical OWNd ID in MyHOME -> fails
    _seed_mini_repo(mh_dir, [rec_b])
    ok_miss, msgs_miss = sync_corpus.check_staged_superset(mh_dir, ow_dir)
    assert not ok_miss
    assert any("missing 1 canonical OWNd fixture ID" in m for m in msgs_miss)

    # 3. Divergent shared ID -> fails
    rec_a_div = {**rec_a, "mcp_notes": "diverged"}
    _seed_mini_repo(mh_dir, [rec_a_div])
    ok_div, msgs_div = sync_corpus.check_staged_superset(mh_dir, ow_dir)
    assert not ok_div
    assert any("shared fixture ID(s) diverge" in m for m in msgs_div)

    # 4. Raw-frame ID collision -> fails
    _seed_mini_repo(ow_dir, [{**rec_a, "frame": "*1*0*11##", "what": 0}])
    _seed_mini_repo(mh_dir, [{**rec_a, "frame": "*1*1*11##"}, rec_b])
    ok_col, msgs_col = sync_corpus.check_staged_superset(mh_dir, ow_dir)
    assert not ok_col
    assert any("raw-frame ID collision" in m for m in msgs_col)

    # 5. sync_from_ownd pulls OWNd updates while preserving staged MyHOME-only rec_b,
    #    OWNd's comments/quoting, and MyHOME's SOURCE.yaml, and sync_to_ownd promotes rec_b
    #    upstream with LF line endings and deletes stale YAML files.
    _seed_mini_repo(ow_dir, [rec_a])
    ow_who01 = ow_dir / "tests" / "golden" / "frames" / "who01_lighting.yaml"
    ow_who01_text = (
        "# Canonical WHO=1 header comment\n\n"
        + ow_who01.read_text(encoding="utf-8").replace("where: '11'", 'where: "11"')
    )
    ow_who01.write_text(ow_who01_text, encoding="utf-8", newline="\n")
    assert sync_corpus._run_validate_corpus(ow_dir, check_only=False) == 0

    _seed_mini_repo(mh_dir, [rec_b])
    mh_source = mh_dir / "tests" / "golden" / "SOURCE.yaml"
    mh_source.write_text(
        mh_source.read_text(encoding="utf-8") + "\n# staged-myhome-provenance\n",
        encoding="utf-8",
        newline="\n",
    )

    assert sync_corpus.sync_from_ownd(mh_dir, ow_dir) == 0
    mh_who01_text = (mh_dir / "tests" / "golden" / "frames" / "who01_lighting.yaml").read_text(
        encoding="utf-8"
    )
    assert mh_who01_text.startswith(ow_who01_text.rstrip("\n") + "\n\n")
    assert "# staged-myhome-provenance" in mh_source.read_text(encoding="utf-8")

    # A second sync_from_ownd run is idempotent.
    mh_source_before = mh_source.read_text(encoding="utf-8")
    assert sync_corpus.sync_from_ownd(mh_dir, ow_dir) == 0
    assert (mh_dir / "tests" / "golden" / "frames" / "who01_lighting.yaml").read_text(
        encoding="utf-8"
    ) == mh_who01_text
    assert mh_source.read_text(encoding="utf-8") == mh_source_before

    ok_after_pull, _ = sync_corpus.check_staged_superset(mh_dir, ow_dir, require_ownd=True)
    assert ok_after_pull

    stale_ow_yaml = ow_dir / "tests" / "golden" / "frames" / "who99_obsolete.yaml"
    stale_ow_yaml.write_text("[]\n", encoding="utf-8")
    assert sync_corpus.sync_to_ownd(mh_dir, ow_dir) == 0
    assert not stale_ow_yaml.exists()
    ok_after_push, msgs_push = sync_corpus.check_staged_superset(mh_dir, ow_dir, require_ownd=True)
    assert ok_after_push
    assert any("0 staged MyHOME-only fixture" in m for m in msgs_push)
    assert b"\r\n" not in (ow_dir / "tests" / "golden" / "corpus.json").read_bytes()

    # 6. Multi-file staged graduation & collision in MyHOME-only YAML files during sync_from_ownd
    _seed_mini_repo(mh_dir, [rec_a])
    mh_staged_yaml = mh_dir / "tests" / "golden" / "frames" / "who99_staged.yaml"
    mh_staged_yaml.write_text(yaml.safe_dump([rec_b], sort_keys=False), encoding="utf-8")
    assert sync_corpus._run_validate_corpus(mh_dir, check_only=False) == 0

    # 6a. Collision in MyHOME-only YAML file when OWNd adopts same frame under a different ID
    _seed_mini_repo(ow_dir, [rec_a, {**rec_b, "id": "lighting.event.off.different_id"}])
    assert sync_corpus.sync_from_ownd(mh_dir, ow_dir) == 1

    # 6b. Clean graduation when OWNd adopts rec_b into who01_lighting.yaml -> who99_staged.yaml unlinked
    _seed_mini_repo(ow_dir, [rec_a, rec_b])
    assert sync_corpus.sync_from_ownd(mh_dir, ow_dir) == 0
    assert not mh_staged_yaml.exists()
    ok_grad, _ = sync_corpus.check_staged_superset(mh_dir, ow_dir, require_ownd=True)
    assert ok_grad

    # 7. Fallback resilience when `jsonschema` is absent (`pyyaml`-only environment)
    no_js_dir = tmp_path / "no_jsonschema_repo"
    for rel in sync_corpus.LOCKSTEP_INFRA_RELPATHS:
        dst = no_js_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, dst)
    shutil.copyfile(
        REPO_ROOT / "tests" / "golden" / "SOURCE.yaml",
        no_js_dir / "tests" / "golden" / "SOURCE.yaml",
    )
    (no_js_dir / "tests" / "golden" / "frames").mkdir(parents=True, exist_ok=True)
    (no_js_dir / "tests" / "golden" / "frames" / "who01_lighting.yaml").write_text(
        yaml.safe_dump([rec_a], sort_keys=False), encoding="utf-8"
    )
    val_path = no_js_dir / "tools" / "golden" / "validate_corpus.py"
    val_path.write_text(
        val_path.read_text(encoding="utf-8").replace(
            "import jsonschema", "raise ImportError('no jsonschema')"
        ),
        encoding="utf-8",
    )
    assert sync_corpus._run_validate_corpus(no_js_dir, check_only=False) == 0
    assert (no_js_dir / "tests" / "golden" / "corpus.json").is_file()
    assert sync_corpus._run_validate_corpus(no_js_dir, check_only=True) == 0
