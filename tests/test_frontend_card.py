"""Run the bus-monitor Lovelace card's node:test suite as part of pytest.

The card (custom_components/myhome/frontend/myhome-bus-card.js) has no build
step and no JS toolchain; its behaviour tests live in tests/frontend/ and run
on node's built-in test runner (node >= 20). GitHub's ubuntu runners ship node,
so CI always executes them; locally the test is skipped when node is missing
unless CI is set, in which case it fails loudly.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_TESTS = REPO_ROOT / "tests" / "frontend"


def _node() -> str | None:
    return shutil.which("node")


@pytest.mark.skipif(
    _node() is None and not os.environ.get("CI"),
    reason="node is not installed; the card tests run in CI",
)
def test_bus_monitor_card_node_suite() -> None:
    """The card's workflow, export and transmit-guard tests must pass under node --test."""
    node = _node()
    assert node is not None, "node is required in CI to run tests/frontend"

    # The TAP reporter is the same on every node version and whether or not stdout
    # is a terminal (the default reporter is not: spec on a TTY, tap in CI).
    result = subprocess.run(
        [node, "--test", "--test-reporter=tap", *sorted(str(p) for p in FRONTEND_TESTS.glob("*.test.mjs"))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    summary = "\n".join(line for line in result.stdout.splitlines() if line.startswith(("not ok", "# ")))
    assert result.returncode == 0, (
        f"node --test failed (exit {result.returncode})\n{summary}\n{result.stderr[-2000:]}"
    )
    counts = dict(re.findall(r"^# (tests|pass|fail) (\d+)$", result.stdout, re.M))
    assert counts.get("fail") == "0" and int(counts.get("pass", "0")) == int(counts.get("tests", "-1")) > 0, summary
