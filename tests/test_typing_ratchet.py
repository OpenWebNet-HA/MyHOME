"""The strict-typing ratchet only ever lowers the per-module error ceiling."""
import json
from collections import Counter
from unittest.mock import patch

import pytest

from scripts import typing_ratchet


@pytest.fixture
def baseline(tmp_path, monkeypatch):
    path = tmp_path / "mypy_baseline.json"
    monkeypatch.setattr(typing_ratchet, "BASELINE", path)
    monkeypatch.setattr(typing_ratchet, "REPO_ROOT", tmp_path)
    return path


def _counts(**modules):
    return Counter({f"custom_components/myhome/{k}.py": v for k, v in modules.items()})


def test_first_run_establishes_the_ceiling(baseline, capsys):
    with patch.object(typing_ratchet, "run_mypy", return_value=(_counts(light=3, cover=1), "")):
        assert typing_ratchet.main(["--update"]) == 0
    data = json.loads(baseline.read_text())
    assert data["total"] == 4
    assert data["modules"]["custom_components/myhome/light.py"] == 3
    assert "Baseline written" in capsys.readouterr().out


def test_regression_fails_and_is_reported(baseline, capsys):
    baseline.write_text(json.dumps({"modules": {"custom_components/myhome/light.py": 3}}))
    with patch.object(typing_ratchet, "run_mypy", return_value=(_counts(light=4), "")):
        assert typing_ratchet.main([]) == 1
    err = capsys.readouterr().err
    assert "REGRESSION" in err and "light.py: 4 > 3" in err

    # a module that is not in the baseline must be clean
    with patch.object(typing_ratchet, "run_mypy", return_value=(_counts(light=3, new_module=1), "")):
        assert typing_ratchet.main([]) == 1
    assert "new_module.py: 1 > 0" in capsys.readouterr().err


def test_update_lowers_but_never_raises(baseline, capsys):
    baseline.write_text(json.dumps({"modules": {"custom_components/myhome/light.py": 3, "custom_components/myhome/cover.py": 2}}))
    # improvement without --update is reported, not written
    with patch.object(typing_ratchet, "run_mypy", return_value=(_counts(light=1, cover=2), "")):
        assert typing_ratchet.main([]) == 0
    assert "light.py: 1 < 3" in capsys.readouterr().out
    assert json.loads(baseline.read_text())["modules"]["custom_components/myhome/light.py"] == 3

    # --update locks the improvement in and drops modules that are clean now
    with patch.object(typing_ratchet, "run_mypy", return_value=(_counts(light=1), "")):
        assert typing_ratchet.main(["--update", "--verbose"]) == 0
    data = json.loads(baseline.read_text())
    assert data["modules"] == {"custom_components/myhome/light.py": 1}

    # --update with a regression still fails and leaves the file alone
    with patch.object(typing_ratchet, "run_mypy", return_value=(_counts(light=2), "")):
        assert typing_ratchet.main(["--update"]) == 1
    assert json.loads(baseline.read_text())["modules"]["custom_components/myhome/light.py"] == 1


def test_run_mypy_parses_error_lines(baseline):
    fake = (
        "custom_components/myhome/light.py:10: error: boom  [no-untyped-def]\n"
        "custom_components/myhome/light.py:12: note: hint\n"
        "custom_components\\myhome\\cover.py:3: error: boom  [arg-type]\n"
    )

    class Proc:
        stdout = fake

    with patch.object(typing_ratchet.subprocess, "run", return_value=Proc()):
        counts, output = typing_ratchet.run_mypy()
    assert counts == _counts(light=1, cover=1)
    assert output == fake
