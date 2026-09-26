#!/usr/bin/env python3
"""Ratchet `mypy --strict` towards zero (Integration Quality Scale rule strict-typing).

`mypy_baseline.json` records, per module, how many strict-mode errors are
tolerated. This script runs mypy over the integration and:

- fails when any module has *more* errors than its baseline (a regression), or
  when a module that is not in the baseline has errors (new modules start clean);
- with ``--update``, lowers the baseline to the current counts (it never raises
  one) and drops modules that are now clean, so the ceiling only ever comes down;
- prints the remaining per-module counts, which is the to-do list for Platinum.

Run from the repository root with an environment that has Home Assistant
installed (the same one the test suite uses).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "custom_components/myhome"
BASELINE = REPO_ROOT / "mypy_baseline.json"
ERROR_LINE = re.compile(r"^(?P<module>custom_components/myhome/[\w/]+\.py):\d+: error: ")


def run_mypy() -> tuple[Counter[str], str]:
    """Return {module: error count} from a strict mypy run over the package."""
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", PACKAGE, "--strict", "--no-error-summary", "--hide-error-context"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    counts: Counter[str] = Counter()
    for line in proc.stdout.splitlines():
        match = ERROR_LINE.match(line.replace("\\", "/"))
        if match:
            counts[match.group("module")] += 1
    return counts, proc.stdout


def load_baseline() -> dict[str, int]:
    if not BASELINE.exists():
        return {}
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in data.get("modules", {}).items()}


def write_baseline(counts: Counter[str]) -> None:
    payload = {
        "_comment": "Strict-mypy error ceiling per module; lowered by scripts/typing_ratchet.py --update, never raised.",
        "total": sum(counts.values()),
        "modules": dict(sorted(counts.items())),
    }
    BASELINE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--update", action="store_true", help="lower the baseline to the current counts")
    parser.add_argument("--verbose", action="store_true", help="print mypy's output")
    args = parser.parse_args(argv)

    counts, output = run_mypy()
    baseline = load_baseline()
    if args.verbose:
        print(output)

    regressions = {
        module: (baseline.get(module, 0), count)
        for module, count in counts.items()
        if count > baseline.get(module, 0)
    }
    if args.update and not BASELINE.exists():
        regressions = {}  # first run: establish the ceiling
    improvements = {
        module: (ceiling, counts.get(module, 0))
        for module, ceiling in baseline.items()
        if counts.get(module, 0) < ceiling
    }

    total = sum(counts.values())
    print(f"mypy --strict: {total} error(s) in {len(counts)} module(s); baseline {sum(baseline.values())}")
    for module, count in sorted(counts.items(), key=lambda item: -item[1]):
        print(f"  {count:4d}  {module}")

    if regressions:
        print("\nREGRESSION - more strict-typing errors than the baseline allows:", file=sys.stderr)
        for module, (ceiling, count) in sorted(regressions.items()):
            print(f"  {module}: {count} > {ceiling}", file=sys.stderr)
        print("Fix the new errors; the ceiling is never raised.", file=sys.stderr)
        return 1

    if args.update:
        write_baseline(Counter({m: min(c, baseline.get(m, c)) for m, c in counts.items()}))
        print(f"\nBaseline written to {BASELINE.relative_to(REPO_ROOT)}")
    elif improvements:
        print("\nModules below their ceiling (run with --update to lock the progress in):")
        for module, (ceiling, count) in sorted(improvements.items()):
            print(f"  {module}: {count} < {ceiling}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
