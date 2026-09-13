"""Tests for scripts/quality_scale_report.py (the Integration Quality Scale audit)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "quality_scale_report.py"
MANIFEST = REPO_ROOT / "custom_components" / "myhome" / "quality_scale.yaml"

spec = importlib.util.spec_from_file_location("quality_scale_report", SCRIPT)
qs = importlib.util.module_from_spec(spec)
sys.modules["quality_scale_report"] = qs
spec.loader.exec_module(qs)

ALL_RULES = [r for tier in qs.TIERS for r in qs.RULES[tier]]


def _manifest(tmp_path: Path, statuses: dict[str, str]) -> Path:
    body = "rules:\n" + "".join(f"  {rule}:\n    status: {status}\n    comment: t\n" for rule, status in statuses.items())
    path = tmp_path / "quality_scale.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_official_catalogue_has_four_tiers_and_no_diamond():
    assert qs.TIERS == ["bronze", "silver", "gold", "platinum"]
    assert "diamond" not in " ".join(qs.TIER_LABEL.values()).lower()
    assert len(ALL_RULES) == 54
    assert len(set(ALL_RULES)) == 54


def test_repo_manifest_lists_every_official_rule_and_nothing_else():
    manifest = qs.load_manifest(MANIFEST)
    assert set(manifest) == set(ALL_RULES)
    result = qs.audit(manifest)
    assert result["unknown_rules"] == []
    for tier in qs.TIERS:
        assert not any(r["status"] == "missing" for r in result["tiers"][tier]["rules"])


def test_tier_requires_all_lower_tiers(tmp_path: Path):
    # every rule done except one Bronze rule -> no tier reached, even though Silver..Platinum are complete
    statuses = {r: "done" for r in ALL_RULES}
    statuses["has-entity-name"] = "todo"
    result = qs.audit(qs.load_manifest(_manifest(tmp_path, statuses)))
    assert result["reached"] == "none"
    assert result["tiers"]["bronze"]["blocking"] == ["has-entity-name"]
    assert result["tiers"]["silver"]["blocking"] == []
    assert result["tiers"]["silver"]["complete"] is False  # lower tier incomplete


@pytest.mark.parametrize(
    ("break_rule", "expected"),
    [(None, "platinum"), ("strict-typing", "gold"), ("repair-issues", "silver"), ("test-coverage", "bronze")],
)
def test_reached_tier(tmp_path: Path, break_rule: str | None, expected: str):
    statuses = {r: "done" for r in ALL_RULES}
    if break_rule:
        statuses[break_rule] = "in_progress"
    assert qs.audit(qs.load_manifest(_manifest(tmp_path, statuses)))["reached"] == expected


def test_exempt_counts_as_satisfied_and_missing_rules_block(tmp_path: Path):
    statuses = {r: "done" for r in ALL_RULES}
    statuses["inject-websession"] = "exempt"
    del statuses["brands"]
    result = qs.audit(qs.load_manifest(_manifest(tmp_path, statuses)))
    assert result["reached"] == "none"
    assert result["tiers"]["bronze"]["blocking"] == ["brands"]
    assert next(r for r in result["tiers"]["bronze"]["rules"] if r["rule"] == "brands")["status"] == "missing"
    assert result["tiers"]["platinum"]["satisfied"] == 3


def test_unknown_rules_are_reported(tmp_path: Path):
    statuses = {r: "done" for r in ALL_RULES}
    statuses["diamond-seal"] = "done"
    result = qs.audit(qs.load_manifest(_manifest(tmp_path, statuses)))
    assert result["unknown_rules"] == ["diamond-seal"]
    assert "not part of the official scale" in qs.markdown(result)


def test_invalid_status_is_an_error(tmp_path: Path):
    path = _manifest(tmp_path, {"config-flow": "finished"})
    with pytest.raises(ValueError, match="config-flow"):
        qs.load_manifest(path)
    assert qs.main(["--manifest", str(path), "--quiet"]) == 2


def test_cli_writes_badge_json_and_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    statuses = {r: "done" for r in ALL_RULES}
    statuses["strict-typing"] = "todo"
    path = _manifest(tmp_path, statuses)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    badge = tmp_path / "badge.svg"
    out = tmp_path / "result.json"

    assert qs.main(["--manifest", str(path), "--badge", str(badge), "--json", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "**Tier reached: 🥇 Gold**" in printed
    assert "blocked by 1 rule(s): `strict-typing`" in printed
    assert summary.read_text(encoding="utf-8").startswith("# Home Assistant Integration Quality Scale audit")
    svg = badge.read_text(encoding="utf-8")
    assert svg.startswith("<svg") and ">gold<" in svg
    assert json.loads(out.read_text(encoding="utf-8"))["reached"] == "gold"


def test_require_gate(tmp_path: Path):
    statuses = {r: "done" for r in ALL_RULES}
    statuses["brands"] = "todo"
    path = _manifest(tmp_path, statuses)
    assert qs.main(["--manifest", str(path), "--quiet", "--require", "bronze"]) == 1
    statuses["brands"] = "done"
    path = _manifest(tmp_path, statuses)
    assert qs.main(["--manifest", str(path), "--quiet", "--require", "platinum"]) == 0


def test_badge_for_no_tier():
    assert "not yet bronze" in qs.badge_svg("none")
