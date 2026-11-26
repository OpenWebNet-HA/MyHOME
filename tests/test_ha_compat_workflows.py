"""Contract tests for Upstream Home Assistant beta & dev CI compatibility workflows."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_COMPAT_WF = REPO_ROOT / ".github" / "workflows" / "ha-upstream-compat.yml"
CONTAINER_SMOKE_WF = REPO_ROOT / ".github" / "workflows" / "ha-container-smoke.yml"


def _load_workflow(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    # PyYAML 1.1 parses bare `on:` as boolean True.
    triggers = workflow.get("on") if "on" in workflow else workflow.get(True)
    assert isinstance(triggers, dict)
    return triggers


def test_ha_compat_workflows_run_on_all_pull_requests() -> None:
    """Both HA compatibility workflows must run on PRs targeting any branch (including release branches)."""
    for wf_path in (UPSTREAM_COMPAT_WF, CONTAINER_SMOKE_WF):
        wf = _load_workflow(wf_path)
        triggers = _triggers(wf)

        assert "pull_request" in triggers, f"{wf_path.name} missing pull_request trigger"
        assert triggers["pull_request"] is None, (
            f"{wf_path.name} must not restrict pull_request.branches so PRs targeting "
            "chore/release-* and staging branches still test HA beta & dev"
        )

        push_branches = triggers["push"]["branches"]
        for expected in ("main", "master", "v2-*"):
            assert expected in push_branches, f"{wf_path.name} missing {expected!r} in push.branches"


def test_ha_compat_workflows_run_daily_and_enforce_stable_beta_dev_matrix() -> None:
    """Daily schedule catches mid-week HA b0..b6 betas; all three channels disallow silent failure."""
    upstream = _load_workflow(UPSTREAM_COMPAT_WF)
    container = _load_workflow(CONTAINER_SMOKE_WF)

    assert _triggers(upstream)["schedule"] == [{"cron": "0 3 * * *"}]
    assert _triggers(container)["schedule"] == [{"cron": "0 4 * * *"}]

    for wf_name, job in (
        ("ha-upstream-compat.yml", upstream["jobs"]["ha-compatibility"]),
        ("ha-container-smoke.yml", container["jobs"]["ha-container-smoke"]),
    ):
        include = job["strategy"]["matrix"]["include"]
        by_channel = {entry["ha-channel"]: entry for entry in include}
        assert set(by_channel) == {"stable", "beta", "dev"}, wf_name
        for ch, entry in by_channel.items():
            assert entry["allow-failure"] is False, f"{wf_name} channel {ch} must not allow failure"


def test_ha_upstream_compat_fails_visibly_and_installs_constraints_and_freezer() -> None:
    """ha-upstream-compat must not swallow dev install failures with exit 0 and must pin constraints."""
    wf = _load_workflow(UPSTREAM_COMPAT_WF)
    steps = wf["jobs"]["ha-compatibility"]["steps"]

    install_step = next(
        s for s in steps if str(s.get("name", "")).startswith("Install Home Assistant")
    )
    script = install_step["run"]

    assert "installed=false" not in script
    assert "Skipping dev tests" not in script
    assert "exit 0" not in script
    assert "package_constraints.txt" in script
    assert 'PIP_CONSTRAINT=$HA_CONSTRAINTS' in script
    assert "pytest-freezer" in script

    for test_step_name in ("Fast Platform Import Smoke Test", "Run the full test suite"):
        step = next(s for s in steps if s.get("name") == test_step_name)
        assert "if" not in step, f"{test_step_name!r} must not be conditionally skipped"


def test_container_smoke_sanitizes_untrusted_ownd_ref(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """run_ha_container_smoke falls back to master if OWND_REF has shell metacharacters or traversal."""
    import scripts.run_ha_container_smoke as smoke

    captured_dockerfiles: list[str] = []

    def fake_run_cmd(cmd: list[str], check: bool = True, capture_output: bool = False) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="2.1.0b2\n", stderr="")

    def fake_subprocess_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        if cmd[:3] == ["docker", "build", "-t"]:
            captured_dockerfiles.append(kwargs["input"])
            # Abort right after inspecting the generated Dockerfile so no real docker runs.
            return SimpleNamespace(returncode=1, stdout="", stderr="stop-after-dockerfile")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(smoke, "get_pinned_version", lambda: "9.9.9")
    monkeypatch.setattr(smoke, "is_pypi_released", lambda pkg, ver: False)
    monkeypatch.setattr(smoke, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(smoke.subprocess, "run", fake_subprocess_run)

    monkeypatch.setenv("OWND_REF", "feat/x; touch /tmp/pwn #")
    assert smoke.test_ha_container(channel="dev", ownd_target="auto") is False
    assert len(captured_dockerfiles) == 1
    assert "refs/heads/master.tar.gz" in captured_dockerfiles[0]
    assert "touch /tmp/pwn" not in captured_dockerfiles[0]

    captured_dockerfiles.clear()
    monkeypatch.setenv("OWND_REF", "feat/../evil")
    assert smoke.test_ha_container(channel="dev", ownd_target="auto") is False
    assert "refs/heads/master.tar.gz" in captured_dockerfiles[0]
    assert ".." not in captured_dockerfiles[0]

    captured_dockerfiles.clear()
    monkeypatch.setenv("OWND_REF", "feat/233-openzigbee-serial")
    assert smoke.test_ha_container(channel="dev", ownd_target="auto") is False
    assert "refs/heads/feat/233-openzigbee-serial.tar.gz" in captured_dockerfiles[0]
