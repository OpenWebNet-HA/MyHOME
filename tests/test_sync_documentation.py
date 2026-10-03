"""Tests for scripts/sync_documentation.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "sync_documentation.py"
sys.path.insert(0, str(SCRIPT.parent))

spec = importlib.util.spec_from_file_location("sync_documentation", SCRIPT)
syncdoc = importlib.util.module_from_spec(spec)
sys.modules["sync_documentation"] = syncdoc
spec.loader.exec_module(syncdoc)

GATEWAY_START_MARKER = syncdoc.GATEWAY_START_MARKER
GATEWAY_END_MARKER = syncdoc.GATEWAY_END_MARKER
SERVICES_START_MARKER = syncdoc.SERVICES_START_MARKER
SERVICES_END_MARKER = syncdoc.SERVICES_END_MARKER


def test_sync_gateway_profiles_detects_drift(tmp_path, monkeypatch):
    """Verify sync_gateway_profiles detects out of sync content and updates it."""
    dummy_file = tmp_path / "dummy_gateways.md"
    dummy_file.write_text(
        f"{GATEWAY_START_MARKER}\nOld Outdated Table\n{GATEWAY_END_MARKER}",
        encoding="utf-8",
    )

    monkeypatch.setattr(syncdoc, "GATEWAY_DOC_TARGETS", [dummy_file])

    monkeypatch.setattr(syncdoc, "get_gateway_block", lambda: f"{GATEWAY_START_MARKER}\nNew Synced Table\n{GATEWAY_END_MARKER}")

    ok, messages = syncdoc.sync_gateway_profiles(update=False)
    assert ok is False
    assert any("out of sync" in m for m in messages)

    # Update mode should succeed
    ok, messages = syncdoc.sync_gateway_profiles(update=True)
    assert ok is True
    assert any("Updated Gateway Profiles table" in m for m in messages)
    assert dummy_file.read_text(encoding="utf-8") == f"{GATEWAY_START_MARKER}\nNew Synced Table\n{GATEWAY_END_MARKER}"

    # Subsequent check mode should now pass
    ok, messages = syncdoc.sync_gateway_profiles(update=False)
    assert ok is True


def test_sync_gateway_profiles_missing_target(tmp_path, monkeypatch):
    """Verify sync_gateway_profiles handles missing files and missing markers."""
    missing_file = tmp_path / "nonexistent.md"
    no_marker_file = tmp_path / "no_markers.md"
    no_marker_file.write_text("Hello world", encoding="utf-8")

    monkeypatch.setattr(syncdoc, "GATEWAY_DOC_TARGETS", [missing_file, no_marker_file])
    monkeypatch.setattr(syncdoc, "get_gateway_block", lambda: "New Table")

    ok, messages = syncdoc.sync_gateway_profiles(update=False)
    assert ok is False
    assert any("not found" in m for m in messages)
    assert any("Missing markers" in m for m in messages)


def test_sync_trace_matrix_missing_target(tmp_path, monkeypatch):
    """Verify sync_trace_matrix handles missing file and missing markers."""
    dummy_missing = tmp_path / "missing.md"
    dummy_nomarkers = tmp_path / "nomarkers.md"
    dummy_nomarkers.write_text("text without markers", encoding="utf-8")

    # Call with fake targets
    orig_dir = syncdoc.DOCS_DIR
    try:
        monkeypatch.setattr(syncdoc, "README_MD", dummy_missing)
        monkeypatch.setattr(syncdoc, "DOCS_DIR", tmp_path)
        # Mock build_matrix so we don't try to import it from scripts
        class MockUpdateTraceMatrix:
            @staticmethod
            def build_matrix(): return "Mock Matrix"
            @staticmethod
            def update_file(t, m): pass
        monkeypatch.setitem(sys.modules, "scripts.update_trace_matrix", MockUpdateTraceMatrix)
        monkeypatch.setitem(sys.modules, "update_trace_matrix", MockUpdateTraceMatrix)
        ok, msgs = syncdoc.sync_trace_matrix(update=False)
        assert ok is False
        assert any("not found" in m for m in msgs)
    finally:
        monkeypatch.setattr(syncdoc, "DOCS_DIR", orig_dir)


def test_parse_services_yaml():
    """Verify parse_services_yaml extracts services correctly."""
    services = syncdoc.parse_services_yaml()
    assert len(services) >= 11
    for expected_svc in (
        "send_message", "turn_on_timed", "sync_time", "sweep_bus",
        "calibrate_cover", "stop_cover_calibration", "set_cover_travel_time",
        "reset_cover_travel_time", "start_sending_instant_power",
        "tuner_seek_up", "tuner_seek_down",
    ):
        assert expected_svc in services


def test_parse_services_yaml_missing_pyyaml(monkeypatch):
    """Verify parse_services_yaml raises ImportError when pyyaml is missing."""
    import builtins

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("No module named 'yaml'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    with pytest.raises(ImportError, match="PyYAML is required to parse services.yaml"):
        syncdoc.parse_services_yaml()


def test_sync_services_detects_missing_section(tmp_path, monkeypatch):
    """Verify sync_services flags when a service section is absent in doc."""
    dummy_services_doc = tmp_path / "services.md"
    dummy_services_doc.write_text("## Only one service\n", encoding="utf-8")

    monkeypatch.setattr(syncdoc, "DOCS_DIR", tmp_path)
    (tmp_path / "configuration").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "configuration" / "services.md"
    target.write_text("## Only partial doc\n", encoding="utf-8")

    ok, messages = syncdoc.sync_services(update=False)
    assert ok is False
    assert any("Services missing documentation sections" in m for m in messages)


def test_sync_services_missing_file(tmp_path, monkeypatch):
    """Verify sync_services handles missing services.md file."""
    monkeypatch.setattr(syncdoc, "DOCS_DIR", tmp_path)
    ok, messages = syncdoc.sync_services(update=False)
    assert ok is False
    assert any("does not exist" in m for m in messages)


def test_extract_repair_issues_from_code_and_strings(tmp_path, monkeypatch):
    """Verify repair issues are extracted from repairs.py and strings.json."""
    dummy_repairs = tmp_path / "repairs.py"
    dummy_repairs.write_text(
        "ISSUE_ONE = 'one_issue'\nISSUE_TWO: str = 'two_issue'\n", encoding="utf-8"
    )
    dummy_strings = tmp_path / "strings.json"
    dummy_strings.write_text(
        '{"issues": {"one_issue": {}, "two_issue": {}}}', encoding="utf-8"
    )

    monkeypatch.setattr(syncdoc, "REPAIRS_PY", dummy_repairs)
    monkeypatch.setattr(syncdoc, "STRINGS_JSON", dummy_strings)

    code_issues = syncdoc.extract_repair_issues_from_code()
    strings_issues = syncdoc.extract_repair_issues_from_strings()

    assert "one_issue" in code_issues
    assert "two_issue" in code_issues
    assert len(code_issues) == 2

    assert code_issues == strings_issues





def test_sync_repair_issues_missing_issue(tmp_path, monkeypatch):
    """Verify sync_repair_issues detects an undocumented repair issue."""
    (tmp_path / "diagnostics").mkdir(parents=True, exist_ok=True)
    dummy_repairs_doc = tmp_path / "diagnostics" / "repair-issues.md"
    dummy_repairs_doc.write_text("## No keys here\n", encoding="utf-8")

    monkeypatch.setattr(syncdoc, "DOCS_DIR", tmp_path)
    ok, messages = syncdoc.sync_repair_issues(update=False)
    assert ok is False
    assert any("missing from repair-issues.md" in m for m in messages)








def test_check_markdown_link_health_detects_malformed(tmp_path, monkeypatch):
    """Verify check_markdown_link_health flags links like [test](#-bad-anchor)."""
    bad_doc = tmp_path / "bad.md"
    bad_doc.write_text("Here is a [bad link](#-bad-anchor)", encoding="utf-8")

    monkeypatch.setattr(syncdoc, "DOCS_DIR", tmp_path)
    ok, messages = syncdoc.check_markdown_link_health()
    assert ok is False
    assert any("Malformed anchor" in m for m in messages)


def test_check_markdown_link_health_detects_empty_and_broken(tmp_path, monkeypatch):
    """Verify check_markdown_link_health detects empty link targets and broken local file links."""
    doc = tmp_path / "links.md"
    doc.write_text(
        "Line with [empty link]() and []() and [hash link](#) and [broken](./missing_page.md).\n"
        "And a valid relative link [self](links.md).\n"
        "And an external link [github](https://github.com).\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(syncdoc, "DOCS_DIR", tmp_path)
    ok, messages = syncdoc.check_markdown_link_health()
    assert ok is False
    assert any("Empty link target" in m and "[empty link]()" in m for m in messages)
    assert any("Empty link target" in m and "[]()" in m for m in messages)
    assert any("Empty link target" in m and "[hash link](#)" in m for m in messages)
    assert any("Broken relative file link" in m and "missing_page.md" in m for m in messages)





def test_sync_version_references_detects_drift_and_updates(tmp_path, monkeypatch):
    """Verify sync_version_references detects outdated versions and updates them in place."""
    (tmp_path / "architecture").mkdir(parents=True, exist_ok=True)
    (tmp_path / "getting-started").mkdir(parents=True, exist_ok=True)
    (tmp_path / "migration").mkdir(parents=True, exist_ok=True)

    safeguards = tmp_path / "architecture" / "anti-drift-safeguards.md"
    safeguards.write_text("Pinned to OWNd==1.0.0 in manifest.\n", encoding="utf-8")

    install = tmp_path / "getting-started" / "installation.md"
    install.write_text('Download TAG="1.0.0" release.\n', encoding="utf-8")

    upgrade = tmp_path / "migration" / "upgrade-from-094.md"
    upgrade.write_text('Upgrade with TAG="1.0.0" release.\n', encoding="utf-8")

    monkeypatch.setattr(syncdoc, "DOCS_DIR", tmp_path)

    # Check mode detects drift
    ok, messages = syncdoc.sync_version_references(update=False)
    assert ok is False
    assert any("Outdated OWNd reference" in m for m in messages)
    assert any("Outdated release tag" in m for m in messages)

    # Update mode fixes drift
    ok_up, messages_up = syncdoc.sync_version_references(update=True)
    assert ok_up is True
    assert any("Updated OWNd pin" in m for m in messages_up)
    assert any("Updated release tag" in m for m in messages_up)

    # Subsequent check passes
    ok_after, messages_after = syncdoc.sync_version_references(update=False)
    assert ok_after is True


def test_sync_version_references_mismatch_const_manifest(monkeypatch):
    """Verify version mismatch between const.py and manifest.json is detected."""
    monkeypatch.setattr(
        syncdoc,
        "extract_manifest_version_info",
        lambda: ("9.9.9", "OWNd==2.0.0b8"),
    )
    ok, messages = syncdoc.sync_version_references(update=False)
    assert ok is False
    assert any("Version mismatch" in m for m in messages)


def test_sync_supported_domains_missing_platform(tmp_path, monkeypatch):
    """Verify sync_supported_domains flags missing platforms in supported_functions.md."""
    (tmp_path / "configuration").mkdir(parents=True, exist_ok=True)
    func_doc = tmp_path / "configuration" / "supported_functions.md"
    func_doc.write_text(
        "### `light`\n### `switch`\n### `cover`\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(syncdoc, "DOCS_DIR", tmp_path)

    class MockUpdateSupportedDomains:
        @staticmethod
        def check_readme_in_sync(readme): return True, ""
        @staticmethod
        def update_readme(readme): return False
        @staticmethod
        def extract_platforms_from_const(): return ["light", "switch", "cover", "missing_platform"]

    monkeypatch.setitem(sys.modules, "scripts.update_supported_domains", MockUpdateSupportedDomains)
    monkeypatch.setitem(sys.modules, "update_supported_domains", MockUpdateSupportedDomains)

    ok, messages = syncdoc.sync_supported_domains(update=False)
    assert ok is False
    assert any("Platforms from const.py missing sections in supported_functions.md" in m for m in messages)


def test_sync_services_detects_orphaned_service(tmp_path, monkeypatch):
    """Verify sync_services detects services documented in markdown that do not exist in services.yaml."""
    real_doc = syncdoc.DOCS_DIR / "configuration" / "services.md"
    content = real_doc.read_text(encoding="utf-8")
    content += "\n## 10. `myhome.phantom_nonexistent_service`\n"

    (tmp_path / "configuration").mkdir(parents=True, exist_ok=True)
    test_doc = tmp_path / "configuration" / "services.md"
    test_doc.write_text(content, encoding="utf-8")

    monkeypatch.setattr(syncdoc, "DOCS_DIR", tmp_path)
    ok, messages = syncdoc.sync_services(update=False)
    assert ok is False
    assert any("Documented services not found in services.yaml" in m for m in messages)
    assert any("phantom_nonexistent_service" in m for m in messages)


def test_sync_services_missing_field(tmp_path, monkeypatch):
    """Verify sync_services detects when service fields are not documented."""
    (tmp_path / "configuration").mkdir(parents=True, exist_ok=True)
    test_doc = tmp_path / "configuration" / "services.md"
    # Document all service names but omit their field parameters
    services_data = syncdoc.parse_services_yaml()
    headings = "\n".join(f"## `myhome.{s}`\nSome description without field names.\n" for s in services_data)
    test_doc.write_text(headings, encoding="utf-8")

    monkeypatch.setattr(syncdoc, "DOCS_DIR", tmp_path)
    ok, messages = syncdoc.sync_services(update=False)
    assert ok is False
    assert any("Service fields missing in services.md" in m for m in messages)


def test_check_all_documentation_real_tree():
    """Verify check_all_documentation runs cleanly on the actual repository tree with zero drift."""
    ok, messages = syncdoc.check_all_documentation(update=False)
    assert ok is True, f"Real tree documentation drift detected: {messages}"











