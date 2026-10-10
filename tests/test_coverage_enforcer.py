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
    assert normalize_coverage_filename("core/transport/tcp.py") == "custom_components/myhome/core/transport/tcp.py"
    assert normalize_coverage_filename("custom_components/myhome/sensor.py") == "custom_components/myhome/sensor.py"
    assert normalize_coverage_filename("myhome/switch.py") == "custom_components/myhome/switch.py"
    assert normalize_coverage_filename(r"core\transport\tcp.py") == "custom_components/myhome/core/transport/tcp.py"


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
