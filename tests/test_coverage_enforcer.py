"""Automated test suite verifying the strict 100% test coverage enforcer."""
import os
import xml.etree.ElementTree as ET

import pytest

from scripts.update_readme_coverage import update_readme_and_svg
from scripts.verify_ownd_coverage import (
    collapse_line_ranges,
    get_source_snippet,
    normalize_coverage_filename,
    verify_all_coverage,
)


def test_normalize_coverage_filename():
    """Verify filename normalization handles relative, absolute, and bare paths."""
    assert normalize_coverage_filename("cover.py") == "custom_components/myhome/cover.py"
    assert normalize_coverage_filename("cover_strategies/base.py") == "custom_components/myhome/cover_strategies/base.py"
    assert normalize_coverage_filename("custom_components/myhome/sensor.py") == "custom_components/myhome/sensor.py"
    assert normalize_coverage_filename("myhome/switch.py") == "custom_components/myhome/switch.py"
    assert normalize_coverage_filename(r"cover_strategies\base.py") == "custom_components/myhome/cover_strategies/base.py"


def test_collapse_line_ranges():
    """Verify conversion of line number lists into compact ranges."""
    assert collapse_line_ranges([]) == ""
    assert collapse_line_ranges(["5"]) == "5"
    assert collapse_line_ranges(["1", "2", "3"]) == "1-3"
    assert collapse_line_ranges(["10", "11", "15", "16", "17", "20"]) == "10-11, 15-17, 20"
    assert collapse_line_ranges(["668", "667"]) == "667-668"


def test_get_source_snippet():
    """Verify source code line retrieval."""
    snippet = get_source_snippet("custom_components/myhome/const.py", 1)
    assert snippet != ""
    assert get_source_snippet("nonexistent_file.py", 999) == ""


def test_verify_all_coverage_missing_xml():
    """Verify return code when coverage.xml is missing."""
    assert verify_all_coverage("/path/does/not/exist.xml") == 1


def test_verify_all_coverage_success(tmp_path):
    """Verify that a 100% coverage report passes."""
    xml_file = tmp_path / "coverage.xml"

    from scripts.verify_ownd_coverage import EXCLUDED_MODULES, MYHOME_DIR, REPO_ROOT

    disk_files = []
    for root_dir, _, files in os.walk(MYHOME_DIR):
        for f in files:
            if f.endswith(".py"):
                full_path = os.path.join(root_dir, f)
                rel_path = os.path.relpath(full_path, REPO_ROOT).replace("\\", "/")
                if rel_path not in EXCLUDED_MODULES:
                    disk_files.append(rel_path)

    root = ET.Element("coverage")
    pkg = ET.SubElement(root, "package")
    for df in disk_files:
        cls = ET.SubElement(pkg, "class", attrib={"filename": df, "line-rate": "1.0"})
        lines = ET.SubElement(cls, "lines")
        ET.SubElement(lines, "line", number="1", hits="1")

    ET.ElementTree(root).write(str(xml_file))

    summary_file = tmp_path / "summary.md"
    monkeypatch_env = {"GITHUB_STEP_SUMMARY": str(summary_file)}
    with pytest.MonkeyPatch.context() as mp:
        for k, v in monkeypatch_env.items():
            mp.setenv(k, v)
        assert verify_all_coverage(str(xml_file)) == 0

    assert "Strict 100% Test Coverage Enforced" in summary_file.read_text(encoding="utf-8")


def test_verify_all_coverage_detects_uncovered_lines(tmp_path):
    """Verify that any uncovered line causes failure with line numbers and snippets."""
    xml_file = tmp_path / "coverage.xml"
    summary_file = tmp_path / "summary.md"

    root = ET.Element("coverage")
    pkg = ET.SubElement(root, "package")
    cls = ET.SubElement(pkg, "class", attrib={"filename": "custom_components/myhome/binary_sensor.py", "line-rate": "0.99"})
    lines = ET.SubElement(cls, "lines")
    ET.SubElement(lines, "line", number="667", hits="0")
    ET.SubElement(lines, "line", number="668", hits="0")
    ET.SubElement(lines, "line", number="669", hits="1")

    ET.ElementTree(root).write(str(xml_file))

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
        assert verify_all_coverage(str(xml_file)) == 1

    summary_content = summary_file.read_text(encoding="utf-8")
    assert "Strict 100% Test Coverage Enforcement Failed" in summary_content
    assert "binary_sensor.py" in summary_content
    assert "667-668" in summary_content


def test_update_readme_coverage_fails_under_100(tmp_path):
    """Verify update_readme_coverage aborts and exits 1 if coverage is under 100%."""
    xml_file = tmp_path / "coverage.xml"

    root = ET.Element("coverage")
    pkg = ET.SubElement(root, "package")
    cls = ET.SubElement(pkg, "class", attrib={"filename": "custom_components/myhome/binary_sensor.py", "line-rate": "0.99"})
    lines = ET.SubElement(cls, "lines")
    ET.SubElement(lines, "line", number="1", hits="0")
    ET.ElementTree(root).write(str(xml_file))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("scripts.update_readme_coverage.COVERAGE_XML", str(xml_file))
        mp.setattr("scripts.update_readme_coverage.COVERAGE_SVG", str(tmp_path / "coverage.svg"))
        mp.setattr("scripts.update_readme_coverage.README_MD", str(tmp_path / "README.md"))
        with pytest.raises(SystemExit) as exc_info:
            update_readme_and_svg()
        assert exc_info.value.code == 1


def test_component_notes_cover_every_integration_module():
    """Every Python module in custom_components/myhome/ has an accurate explicit COMPONENT_NOTES entry."""
    from scripts.update_readme_coverage import COMPONENT_NOTES
    from scripts.verify_ownd_coverage import EXCLUDED_MODULES, MYHOME_DIR, REPO_ROOT

    disk_files = set()
    for root_dir, _, files in os.walk(MYHOME_DIR):
        for f in files:
            if f.endswith(".py"):
                full_path = os.path.join(root_dir, f)
                rel_path = os.path.relpath(full_path, REPO_ROOT).replace("\\", "/")
                if rel_path not in EXCLUDED_MODULES:
                    disk_files.add(rel_path)

    assert set(COMPONENT_NOTES.keys()) == disk_files
    for mod, note in COMPONENT_NOTES.items():
        assert note.strip() and note != "Core integration component", mod
    assert "pulse sensors" not in COMPONENT_NOTES["custom_components/myhome/sensor.py"].lower()
    assert "tilt" not in COMPONENT_NOTES["custom_components/myhome/cover_strategies/native.py"].lower()
    assert "WHO=14" in COMPONENT_NOTES["custom_components/myhome/button.py"]
    assert "WHO=2" in COMPONENT_NOTES["custom_components/myhome/button.py"]
    assert "16/22" in COMPONENT_NOTES["custom_components/myhome/button.py"]


def test_get_test_count_ignores_stale_junit_xml_and_uses_fresh_junit(tmp_path):
    """Stale junit.xml older than coverage.xml is ignored; fresh junit.xml is used."""
    from types import SimpleNamespace

    from scripts.update_readme_coverage import _is_fresh_junit, get_test_count

    cov_xml = tmp_path / "coverage.xml"
    junit_xml = tmp_path / "junit.xml"
    cov_xml.write_text("<coverage line-rate='1.0'/>", encoding="utf-8")
    junit_xml.write_text("<testsuites tests='2090'/>", encoding="utf-8")

    # Make junit.xml 1 hour older than coverage.xml -> stale!
    now = os.path.getmtime(cov_xml)
    os.utime(junit_xml, (now - 3600, now - 3600))
    assert _is_fresh_junit(str(junit_xml), str(cov_xml)) is False

    seen_kwargs: list[dict] = []

    def _fake_run(cmd, **kwargs):
        seen_kwargs.append(kwargs)
        return SimpleNamespace(stdout="5850 tests collected in 1.23s\n")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("pytest.main", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("skip in-proc")))
        mp.setattr("scripts.update_readme_coverage.subprocess.run", _fake_run)
        assert get_test_count(str(junit_xml), str(cov_xml)) == 5850

    assert seen_kwargs
    assert "shell" not in seen_kwargs[0] or seen_kwargs[0]["shell"] is False

    # Touch junit.xml so it is fresh relative to coverage.xml -> parsed directly
    junit_xml.write_text("<testsuites tests='5852'/>", encoding="utf-8")
    os.utime(junit_xml, (now + 1, now + 1))
    assert _is_fresh_junit(str(junit_xml), str(cov_xml)) is True
    assert get_test_count(str(junit_xml), str(cov_xml)) == 5852


