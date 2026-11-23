"""Tests for pinned OWNd installation in tests/conftest.py and pypi_standards.yml."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests import conftest as tests_conftest

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_JSON = REPO_ROOT / "custom_components" / "myhome" / "manifest.json"
PYPI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pypi_standards.yml"


@pytest.mark.parametrize(
    "branch",
    ["", "   ", "master", "main", "feat/*", "a?b", "a[0]", "a..b", "-flag", "bad branch"],
)
def test_has_paired_ownd_branch_rejects_default_or_unsafe_names(
    monkeypatch: pytest.MonkeyPatch, branch: str
) -> None:
    """Default, empty, glob, and unsafe branch names never call git ls-remote."""
    called = False

    def fail_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal called
        called = True
        raise AssertionError(f"subprocess.run should not be called for {branch!r}")

    monkeypatch.setattr(tests_conftest.subprocess, "run", fail_run)
    assert tests_conftest._has_paired_ownd_branch(branch) is False
    assert called is False


def test_has_paired_ownd_branch_checks_exact_tab_field_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only an exact refs/heads/<branch> match with rc=0 returns True."""
    branch = "feat/233-openzigbee-serial"
    expected_ref = f"refs/heads/{branch}"
    seen_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        seen_cmds.append(cmd)
        return responses.pop(0)

    responses = [
        SimpleNamespace(returncode=0, stdout=f"deadbeef\t{expected_ref}\n"),
        SimpleNamespace(returncode=0, stdout=""),
        SimpleNamespace(returncode=0, stdout=f"deadbeef\t{expected_ref}-extra\n"),
        SimpleNamespace(returncode=128, stdout=f"deadbeef\t{expected_ref}\n"),
    ]
    monkeypatch.setattr(tests_conftest.subprocess, "run", fake_run)

    assert tests_conftest._has_paired_ownd_branch(f" {branch} ") is True
    assert seen_cmds[0] == [
        "git",
        "ls-remote",
        "--heads",
        tests_conftest._OWND_REPO_URL,
        "--",
        expected_ref,
    ]
    assert tests_conftest._has_paired_ownd_branch(branch) is False
    assert tests_conftest._has_paired_ownd_branch(branch) is False
    assert tests_conftest._has_paired_ownd_branch(branch) is False

    def raise_oserror(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("git missing")

    monkeypatch.setattr(tests_conftest.subprocess, "run", raise_oserror)
    assert tests_conftest._has_paired_ownd_branch(branch) is False


def test_install_ownd_for_tests_uses_manifest_pin_on_normal_pr_in_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In CI on a normal PR branch without a paired OWNd branch, install the manifest.json pin."""
    pinned_reqs = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))["requirements"]
    assert any(req.startswith("OWNd==") for req in pinned_reqs)

    pip_calls: list[list[str]] = []
    ls_remote_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        ls_remote_calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="")

    def fake_check_call(cmd, **kwargs):  # type: ignore[no-untyped-def]
        pip_calls.append(cmd)

    monkeypatch.setattr(tests_conftest.subprocess, "run", fake_run)
    monkeypatch.setattr(tests_conftest.subprocess, "check_call", fake_check_call)

    result = tests_conftest._install_ownd_for_tests(
        env={"GITHUB_ACTIONS": "true", "GITHUB_HEAD_REF": "fix/issue-999"}
    )

    assert result == pinned_reqs[0]
    assert len(ls_remote_calls) == 1
    assert pip_calls == [[sys.executable, "-m", "pip", "install", *pinned_reqs]]
    assert all("OWNd.git@master" not in arg for cmd in pip_calls for arg in cmd)


def test_install_ownd_for_tests_skips_ls_remote_on_master_in_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In CI on master, git ls-remote is skipped and the manifest.json pin is installed."""
    pinned_reqs = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))["requirements"]
    pip_calls: list[list[str]] = []

    def fail_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("git ls-remote should not run on master")

    def fake_check_call(cmd, **kwargs):  # type: ignore[no-untyped-def]
        pip_calls.append(cmd)

    monkeypatch.setattr(tests_conftest.subprocess, "run", fail_run)
    monkeypatch.setattr(tests_conftest.subprocess, "check_call", fake_check_call)

    result = tests_conftest._install_ownd_for_tests(
        env={"GITHUB_ACTIONS": "true", "GITHUB_REF_NAME": "master"}
    )

    assert result == pinned_reqs[0]
    assert pip_calls == [[sys.executable, "-m", "pip", "install", *pinned_reqs]]


def test_install_ownd_for_tests_uses_paired_branch_when_present_in_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In CI on a paired branch, install git+...OWNd.git@<branch> with --upgrade --force-reinstall."""
    branch = "feat/233-openzigbee-serial"
    pip_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(returncode=0, stdout=f"deadbeef\trefs/heads/{branch}\n")

    def fake_check_call(cmd, **kwargs):  # type: ignore[no-untyped-def]
        pip_calls.append(cmd)

    monkeypatch.setattr(tests_conftest.subprocess, "run", fake_run)
    monkeypatch.setattr(tests_conftest.subprocess, "check_call", fake_check_call)

    result = tests_conftest._install_ownd_for_tests(
        env={"GITHUB_ACTIONS": "true", "GITHUB_HEAD_REF": branch}
    )

    expected_target = f"git+{tests_conftest._OWND_REPO_URL}@{branch}"
    assert result == expected_target
    assert pip_calls == [
        [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", expected_target]
    ]


def test_install_ownd_for_tests_falls_back_to_master_when_pin_unreleased_in_ci(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the pinned OWNd version is not yet on PyPI in CI, fall back to OWNd@master keeping other deps."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"requirements": ["OWNd==9.9.9", "some-other-dep>=1.0"]}),
        encoding="utf-8",
    )
    pip_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(returncode=0, stdout="")

    def fake_check_call(cmd, **kwargs):  # type: ignore[no-untyped-def]
        pip_calls.append(cmd)
        if len(pip_calls) == 1:
            raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(tests_conftest.subprocess, "run", fake_run)
    monkeypatch.setattr(tests_conftest.subprocess, "check_call", fake_check_call)

    result = tests_conftest._install_ownd_for_tests(
        manifest_path=str(manifest),
        env={"GITHUB_ACTIONS": "true", "GITHUB_HEAD_REF": "fix/issue-999"},
    )

    expected_fallback = f"git+{tests_conftest._OWND_REPO_URL}@master"
    assert result == expected_fallback
    assert pip_calls == [
        [sys.executable, "-m", "pip", "install", "OWNd==9.9.9", "some-other-dep>=1.0"],
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            expected_fallback,
            "some-other-dep>=1.0",
        ],
    ]


def test_install_ownd_for_tests_outside_ci_uses_manifest_and_propagates_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Outside CI, git ls-remote is never called, pip errors re-raise, and missing reqs raise RuntimeError."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"requirements": ["OWNd==2.0.1"]}), encoding="utf-8")
    pip_calls: list[list[str]] = []

    def fail_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("git ls-remote should not run outside CI")

    def ok_check_call(cmd, **kwargs):  # type: ignore[no-untyped-def]
        pip_calls.append(cmd)

    monkeypatch.setattr(tests_conftest.subprocess, "run", fail_run)
    monkeypatch.setattr(tests_conftest.subprocess, "check_call", ok_check_call)

    assert (
        tests_conftest._install_ownd_for_tests(manifest_path=str(manifest), env={})
        == "OWNd==2.0.1"
    )
    assert pip_calls == [[sys.executable, "-m", "pip", "install", "OWNd==2.0.1"]]

    def fail_check_call(cmd, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(tests_conftest.subprocess, "check_call", fail_check_call)
    with pytest.raises(subprocess.CalledProcessError):
        tests_conftest._install_ownd_for_tests(manifest_path=str(manifest), env={})

    empty_manifest = tmp_path / "empty_manifest.json"
    empty_manifest.write_text(json.dumps({"requirements": []}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="No requirements found"):
        tests_conftest._install_ownd_for_tests(manifest_path=str(empty_manifest), env={})
    with pytest.raises(RuntimeError, match="No requirements found"):
        tests_conftest._install_ownd_for_tests(
            manifest_path=str(tmp_path / "missing.json"), env={}
        )


def test_pypi_standards_workflow_installs_pinned_ownd_unless_paired_or_unreleased() -> None:
    """unit-tests-and-coverage installs .[test] (pinned OWNd) without unconditionally overwriting it."""
    workflow = yaml.safe_load(PYPI_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["unit-tests-and-coverage"]["steps"]
    install_step = next(step for step in steps if step.get("name") == "Install test dependencies")

    assert install_step["shell"] == "bash"
    assert install_step["env"]["OWND_REF"] == "${{ github.head_ref || github.ref_name }}"

    script = install_step["run"]
    assert "git ls-remote --heads" in script
    assert "grep -Fx" in script
    assert (
        'pip install ".[test]" homeassistant python-dateutil pytz pytest-socket\n'
        'pip install --upgrade --force-reinstall "git+https://github.com/OpenWebNet-HA/OWNd.git@${OWND_REF}" \\\n'
        '  || pip install --upgrade --force-reinstall "git+https://github.com/OpenWebNet-HA/OWNd.git@master"'
    ) not in script
    assert '|| pip install --upgrade --force-reinstall "git+https://github.com/OpenWebNet-HA/OWNd.git@master"' not in script
