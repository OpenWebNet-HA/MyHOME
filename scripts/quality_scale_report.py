#!/usr/bin/env python3
"""Audit quality_scale.yaml against the official Home Assistant Integration Quality Scale.

The Integration Quality Scale has four tiers - Bronze, Silver, Gold, Platinum -
and a tier is reached only when every rule of that tier *and of every lower
tier* is ``done`` or ``exempt``. This script:

1. compares ``custom_components/myhome/quality_scale.yaml`` with the official
   rule catalogue (rules that are not listed count as *missing*, i.e. not done);
2. computes the highest tier actually reached and the rules blocking the next;
3. prints a Markdown report (also appended to ``$GITHUB_STEP_SUMMARY`` in CI);
4. optionally writes a shields-style SVG badge (``--badge``) and a JSON
   summary (``--json``);
5. exits non-zero when the manifest is malformed or, with ``--require TIER``,
   when that tier is not reached.

It certifies conformance to the published checklist as recorded in the
manifest - it is not an award; only Home Assistant core review grants tiers.

Reference: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - dependency error surfaced to the caller
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "custom_components" / "myhome" / "quality_scale.yaml"

TIERS = ["bronze", "silver", "gold", "platinum"]
TIER_LABEL = {"bronze": "🥉 Bronze", "silver": "🥈 Silver", "gold": "🥇 Gold", "platinum": "🏆 Platinum"}
TIER_COLOR = {"none": "#9f9f9f", "bronze": "#cd7f32", "silver": "#a8a9ad", "gold": "#d4af37", "platinum": "#4fc3f7"}

# Official catalogue (developers.home-assistant.io, Integration Quality Scale rules).
RULES: dict[str, list[str]] = {
    "bronze": [
        "action-setup", "appropriate-polling", "brands", "common-modules",
        "config-flow-test-coverage", "config-flow", "dependency-transparency",
        "docs-actions", "docs-triggers", "docs-conditions", "docs-high-level-description",
        "docs-installation-instructions", "docs-removal-instructions", "entity-event-setup",
        "entity-unique-id", "has-entity-name", "runtime-data", "test-before-configure",
        "test-before-setup", "unique-config-entry",
    ],
    "silver": [
        "action-exceptions", "config-entry-unloading", "docs-configuration-parameters",
        "docs-installation-parameters", "entity-unavailable", "integration-owner",
        "log-when-unavailable", "parallel-updates", "reauthentication-flow", "test-coverage",
    ],
    "gold": [
        "devices", "diagnostics", "discovery-update-info", "discovery", "docs-data-update",
        "docs-examples", "docs-known-limitations", "docs-supported-devices",
        "docs-supported-functions", "docs-troubleshooting", "docs-use-cases", "dynamic-devices",
        "entity-category", "entity-device-class", "entity-disabled-by-default",
        "entity-translations", "exception-translations", "icon-translations",
        "reconfiguration-flow", "repair-issues", "stale-devices",
    ],
    "platinum": ["async-dependency", "inject-websession", "strict-typing"],
}
VALID_STATUSES = {"done", "todo", "in_progress", "exempt"}
SATISFIED = {"done", "exempt"}
STATUS_ICON = {"done": "✅", "exempt": "➖", "in_progress": "🔄", "todo": "⬜", "missing": "❓"}


def load_manifest(path: Path) -> dict[str, dict]:
    """Return {rule: {"status": ..., "comment": ...}}; raise ValueError on malformed input."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules = data.get("rules")
    if not isinstance(rules, dict):
        raise ValueError("quality_scale.yaml must contain a top-level 'rules' mapping")
    out: dict[str, dict] = {}
    for name, value in rules.items():
        if isinstance(value, str):
            value = {"status": value}
        if not isinstance(value, dict) or value.get("status") not in VALID_STATUSES:
            raise ValueError(f"rule '{name}' has an invalid status: {value!r} (expected one of {sorted(VALID_STATUSES)})")
        out[str(name)] = {"status": value["status"], "comment": str(value.get("comment", "")).strip()}
    return out


def audit(manifest: dict[str, dict]) -> dict:
    """Compute per-tier status and the highest tier reached."""
    known = {r for rules in RULES.values() for r in rules}
    tiers: dict[str, dict] = {}
    reached = "none"
    lower_ok = True
    for tier in TIERS:
        rows = []
        for rule in RULES[tier]:
            entry = manifest.get(rule)
            status = entry["status"] if entry else "missing"
            rows.append({"rule": rule, "status": status, "comment": entry["comment"] if entry else ""})
        blocking = [r["rule"] for r in rows if r["status"] not in SATISFIED]
        complete = lower_ok and not blocking
        tiers[tier] = {"rules": rows, "blocking": blocking, "complete": complete,
                       "satisfied": sum(1 for r in rows if r["status"] in SATISFIED), "total": len(rows)}
        if complete:
            reached = tier
        lower_ok = lower_ok and not blocking
    unknown = sorted(set(manifest) - known)
    return {"reached": reached, "tiers": tiers, "unknown_rules": unknown}


def next_tier(reached: str) -> str | None:
    idx = -1 if reached == "none" else TIERS.index(reached)
    return TIERS[idx + 1] if idx + 1 < len(TIERS) else None


def markdown(result: dict) -> str:
    reached = result["reached"]
    lines = ["# Home Assistant Integration Quality Scale audit", ""]
    lines.append(f"**Tier reached: {TIER_LABEL.get(reached, '— none yet')}**  ")
    nxt = next_tier(reached)
    if nxt:
        blocking = result["tiers"][nxt]["blocking"]
        lines.append(f"Next: {TIER_LABEL[nxt]} — blocked by {len(blocking)} rule(s): " + ", ".join(f"`{b}`" for b in blocking))
    lines.append("")
    lines.append("> Computed from `custom_components/myhome/quality_scale.yaml` against the official rule list. "
                 "A tier requires every rule of that tier and all lower tiers to be `done` or `exempt`. "
                 "Rules not listed in the manifest count as ❓ missing. Tiers are formally awarded only by Home Assistant core review.")
    lines.append("")
    for tier in TIERS:
        t = result["tiers"][tier]
        state = "✅ complete" if t["complete"] else f"{t['satisfied']}/{t['total']} satisfied"
        lines += [f"## {TIER_LABEL[tier]} — {state}", "", "| Rule | Status | Comment |", "| :--- | :---: | :--- |"]
        for r in t["rules"]:
            lines.append(f"| `{r['rule']}` | {STATUS_ICON[r['status']]} {r['status']} | {r['comment']} |")
        lines.append("")
    if result["unknown_rules"]:
        lines += ["## ⚠️ Rules in the manifest that are not part of the official scale", "",
                  ", ".join(f"`{r}`" for r in result["unknown_rules"]), ""]
    return "\n".join(lines)


def badge_svg(reached: str) -> str:
    label = "quality scale"
    value = {"none": "not yet bronze"}.get(reached, reached)
    color = TIER_COLOR[reached]
    lw, vw = 6 * len(label) + 10, 6 * len(value) + 12
    w = lw + vw
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" aria-label="{label}: {value}">'
        f'<linearGradient id="b" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<mask id="a"><rect width="{w}" height="20" rx="3" fill="#fff"/></mask><g mask="url(#a)">'
        f'<path fill="#555" d="M0 0h{lw}v20H0z"/><path fill="{color}" d="M{lw} 0h{vw}v20H{lw}z"/><path fill="url(#b)" d="M0 0h{w}v20H0z"/></g>'
        f'<g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">'
        f'<text x="{lw / 2}" y="15" fill="#010101" fill-opacity=".3">{label}</text><text x="{lw / 2}" y="14">{label}</text>'
        f'<text x="{lw + vw / 2}" y="15" fill="#010101" fill-opacity=".3">{value}</text><text x="{lw + vw / 2}" y="14">{value}</text></g></svg>'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--badge", type=Path, help="write a shields-style SVG badge here")
    parser.add_argument("--json", type=Path, help="write the audit result as JSON here")
    parser.add_argument("--require", choices=TIERS, help="exit 1 unless this tier is reached")
    parser.add_argument("--quiet", action="store_true", help="do not print the Markdown report")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
    except (OSError, ValueError, yaml.YAMLError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2

    result = audit(manifest)
    report = markdown(result)
    if not args.quiet:
        print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    if args.badge:
        args.badge.write_text(badge_svg(result["reached"]), encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")

    if args.require and (result["reached"] == "none" or TIERS.index(result["reached"]) < TIERS.index(args.require)):
        print(f"FAILED: required tier {args.require} not reached (reached: {result['reached']})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
