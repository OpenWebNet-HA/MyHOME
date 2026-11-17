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
    assert len(services) >= 12
    for expected_svc in (
        "send_message", "turn_on_timed", "sync_time", "sweep_bus",
        "calibrate_cover", "stop_cover_calibration", "set_cover_travel_time",
        "reset_cover_travel_time", "start_sending_instant_power",
        "stop_sending_instant_power",
        "tuner_seek_up", "tuner_seek_down",
        "tuner_set_frequency", "tuner_store_station",
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


def test_sync_gateway_discovery_detects_drift_and_updates(tmp_path, monkeypatch):
    """Verify sync_gateway_discovery detects drift and updates hardware-compatibility.md."""
    (tmp_path / "getting-started").mkdir(parents=True, exist_ok=True)
    hw_doc = tmp_path / "getting-started" / "hardware-compatibility.md"
    hw_doc.write_text(
        f"{syncdoc.DISCOVERY_START_MARKER}\nOutdated Discovery Table\n{syncdoc.DISCOVERY_END_MARKER}",
        encoding="utf-8",
    )
    monkeypatch.setattr(syncdoc, "DOCS_DIR", tmp_path)

    ok, messages = syncdoc.sync_gateway_discovery(update=False)
    assert ok is False
    assert any("Gateway Discovery table out of sync" in m for m in messages)

    ok_up, messages_up = syncdoc.sync_gateway_discovery(update=True)
    assert ok_up is True
    assert any("Updated Gateway Discovery table" in m for m in messages_up)

    ok_after, _ = syncdoc.sync_gateway_discovery(update=False)
    assert ok_after is True


def test_check_all_documentation_follows_ownd_142_rows_at_the_pin_bump(tmp_path, monkeypatch):
    """manifest.json pins OWNd==2.0.0b13 (past OWNd #142): the committed docs carry the WHO 22-only rows.

    check_all_documentation is green against the pinned profiles (no WHO 16 on the F461 /
    MyHomeServer1), and a stale pre-#142 OWNd install is flagged by both the oracle and the
    discovery-table checks instead of being tolerated as a transition.
    """
    import OWNd.profiles as ownd_profiles

    try:
        from scripts import update_gateway_profiles as ugp_mod
    except ImportError:
        import update_gateway_profiles as ugp_mod

    real_get = ownd_profiles.get_gateway_profile

    class _ProfileWho16:
        def __init__(self, base, with_who16: bool):
            self._base = base
            self._with_who16 = with_who16

        def __getattr__(self, attr):
            return getattr(self._base, attr)

        def supports_who(self, who: int) -> bool:
            if who == 16:
                return self._with_who16
            return bool(self._base.supports_who(who))

    def _patch_profiles(with_who16: bool) -> None:
        def _patched_get(name: str, *args, **kwargs):
            base = real_get(name, *args, **kwargs)
            if getattr(base, "model_name", None) in ("F461", "MyHomeServer1"):
                return _ProfileWho16(base, with_who16)
            return base

        monkeypatch.setattr(ownd_profiles, "get_gateway_profile", _patched_get)

    _patch_profiles(with_who16=False)
    monkeypatch.setattr(ugp_mod, "_patched_ownd_profiles", lambda: [])
    if "update_gateway_profiles" in sys.modules:
        monkeypatch.setattr(sys.modules["update_gateway_profiles"], "_patched_ownd_profiles", lambda: [])

    # 1. With manifest.json at OWNd==2.0.0b13 and the pinned (OWNd #142) profiles, the full
    #    real-tree doc check passes cleanly.
    ok, messages = syncdoc.check_all_documentation(update=False)
    assert ok is True, f"Expected zero drift against the pinned OWNd profiles: {messages}"

    # 2. A stale pre-#142 OWNd (WHO 16 still advertised on the F461 / MyHomeServer1) is flagged
    #    by both the oracle and the discovery table checks: the transition window is closed.
    _patch_profiles(with_who16=True)

    ok_disc, msgs_disc = syncdoc.sync_gateway_discovery(update=False)
    assert ok_disc is False
    assert any("Gateway Discovery table out of sync" in m for m in msgs_disc)

    ok_prof, msgs_prof = syncdoc.sync_gateway_profiles(update=False)
    assert ok_prof is False
    assert any("Gateway profile oracle mismatch" in m for m in msgs_prof)


def test_sync_mkdocs_nav_detects_duplicates_and_missing_targets(tmp_path, monkeypatch):
    """Verify sync_mkdocs_nav catches duplicate nav entries, missing nav files, and Material python tags."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "index.md").write_text("# Home\n", encoding="utf-8")
    (docs_dir / "guide.md").write_text("# Guide\n", encoding="utf-8")

    mkdocs_yml = tmp_path / "mkdocs.yml"
    mkdocs_yml.write_text(
        "site_name: Test\n"
        "markdown_extensions:\n"
        "  - pymdownx.emoji:\n"
        "      emoji_index: !!python/name:material.extensions.emoji.twemoji\n"
        "nav:\n"
        "  - Home: index.md\n"
        "  - Guide: guide.md\n"
        "  - Duplicate Guide: guide.md\n"
        "  - Ghost: missing_page.md\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(syncdoc, "DOCS_DIR", docs_dir)
    monkeypatch.setattr(syncdoc, "MKDOCS_YML", mkdocs_yml)

    ok, messages = syncdoc.sync_mkdocs_nav(update=False)
    assert ok is False
    assert any("Duplicate markdown pages" in m and "guide.md" in m for m in messages)
    assert any("missing markdown files" in m and "missing_page.md" in m for m in messages)


def test_check_readme_and_site_standards_real_tree():
    """Verify starting README, license metadata, and versioned docs workflow pass on real tree."""
    ok, messages = syncdoc.check_readme_and_site_standards()
    assert ok is True, f"Standards check failed on real tree: {messages}"


def test_check_readme_and_site_standards_rejects_agpl_beta_and_legacy_comparisons(tmp_path, monkeypatch):
    """Verify check_readme_and_site_standards flags AGPL, /MyHOME/beta, and legacy 0.9.4 comparisons."""
    (tmp_path / "LICENSE").write_text("Apache License 2.0\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(
        "# MyHOME\n\n"
        "Manual: https://openwebnet-ha.github.io/MyHOME/beta/\n"
        "Wiki: https://github.com/OpenWebNet-HA/MyHOME/wiki\n"
        "Safe Rollback to 0.9.4 if needed. Older cores stay on 2.0.0b12.\n"
        "Licensed under AGPL-3.0 — see [LICENSE](LICENSE).\n",
        encoding="utf-8",
    )
    mkdocs_yml = tmp_path / "mkdocs.yml"
    mkdocs_yml.write_text(
        'copyright: "Licensed under AGPL-3.0"\n'
        "nav:\n"
        "  - Beta: roadmap/beta-differences.md\n"
        "  - F454: f454-regression-checks.md\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"documentation": "https://openwebnet-ha.github.io/MyHOME/beta/"}',
        encoding="utf-8",
    )
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        'license = "AGPL-3.0"\n'
        'Documentation = "https://openwebnet-ha.github.io/MyHOME/beta/"\n'
        'Wiki = "https://github.com/anotherjulien/MyHOME/wiki"\n',
        encoding="utf-8",
    )
    issue_cfg = tmp_path / "config.yml"
    issue_cfg.write_text(
        "url: https://openwebnet-ha.github.io/MyHOME/beta/\n"
        "wiki: https://github.com/OpenWebNet-HA/MyHOME/wiki\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(syncdoc, "README_MD", readme)
    monkeypatch.setattr(syncdoc, "MKDOCS_YML", mkdocs_yml)
    monkeypatch.setattr(syncdoc, "MANIFEST_JSON", manifest)
    monkeypatch.setattr(syncdoc, "PYPROJECT_TOML", pyproject)
    monkeypatch.setattr(syncdoc, "ISSUE_TEMPLATE_CONFIG", issue_cfg)

    ok, messages = syncdoc.check_readme_and_site_standards()
    assert ok is False
    assert any("README.md still links to '/MyHOME/beta'" in m for m in messages)
    assert any("README.md still links to deprecated GitHub '/wiki'" in m for m in messages)
    assert any("README.md contains outdated AGPL" in m for m in messages)
    assert any("legacy v0.9.4" in m for m in messages)
    assert any("Safe Rollback" in m for m in messages)
    assert any("Older cores stay on" in m for m in messages)
    assert any("mkdocs.yml contains outdated AGPL" in m for m in messages)
    assert any("beta-differences.md" in m for m in messages)
    assert any("f454-regression-checks.md" in m for m in messages)
    assert any("manifest.json 'documentation'" in m for m in messages)
    assert any("pyproject.toml still points Documentation URL to '/MyHOME/beta'" in m for m in messages)
    assert any("pyproject.toml still points URL to deprecated GitHub '/wiki'" in m for m in messages)
    assert any("ISSUE_TEMPLATE/config.yml still points User Manual URL to '/MyHOME/beta'" in m for m in messages)
    assert any("ISSUE_TEMPLATE/config.yml still points URL to deprecated GitHub '/wiki'" in m for m in messages)


def test_check_readme_and_site_standards_detects_broken_readme_links_and_anchors(tmp_path, monkeypatch):
    """Verify README.md link/anchor/image validation catches broken targets while ignoring code fences."""
    (tmp_path / "LICENSE").write_text("Apache License 2.0\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(
        "# MyHOME\n\n"
        "[Manual](https://openwebnet-ha.github.io/MyHOME/) • "
        "[Valid Section](#quick-start) • "
        "[Bad Emoji](#\ufe0f-supported-hardware) • "
        "[Missing Anchor](#does-not-exist) • "
        "[Missing File](SECURITY.md) • "
        "[Empty]()\n\n"
        '<img src="docs/images/missing.jpg" alt="missing">\n\n'
        "```markdown\n"
        "# Heading Inside Code Fence Should Be Ignored\n"
        "[Ignored Broken Link](NONEXISTENT_IN_FENCE.md)\n"
        "```\n\n"
        "## Quick Start\n\n"
        "Licensed under [Apache License 2.0](LICENSE).\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(syncdoc, "README_MD", readme)
    ok, messages = syncdoc.check_readme_and_site_standards()
    assert ok is False
    assert any("Malformed heading anchor" in m for m in messages)
    assert any("Unresolved internal heading anchor" in m and "#does-not-exist" in m for m in messages)
    assert any("Broken relative file link" in m and "SECURITY.md" in m for m in messages)
    assert any("Empty link target" in m for m in messages)
    assert any("Broken HTML image src" in m and "docs/images/missing.jpg" in m for m in messages)
    assert not any("NONEXISTENT_IN_FENCE.md" in m for m in messages)


def test_check_readme_and_site_standards_detects_docs_workflow_regressions(tmp_path, monkeypatch):
    """Verify check_readme_and_site_standards catches v0.9.4 -> latest alias hijack, missing beta pruning, or missing master refresh."""
    bad_wf = tmp_path / "docs.yml"
    bad_wf.write_text(
        "mike deploy --push --update-aliases -F mkdocs-v0.9.yml 0.9.4 latest -t 'v0.9.4 (latest)'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(syncdoc, "DOCS_WORKFLOW", bad_wf)

    ok, messages = syncdoc.check_readme_and_site_standards()
    assert ok is False
    assert any("aliases legacy mkdocs-v0.9.yml (0.9.4) to 'latest'" in m for m in messages)
    assert any("must prune pre-release" in m for m in messages)
    assert any("strip stale non-legacy aliases" in m for m in messages)
    assert any("keep '/latest/' updated on pushes to master" in m for m in messages)
    assert any("guard older-minor patch releases" in m for m in messages)
    assert any("fetch origin/gh-pages explicitly" in m for m in messages)


def test_check_readme_and_site_standards_rejects_beta_and_wiki_urls_in_component_and_docs(
    tmp_path, monkeypatch
):
    """Verify /MyHOME/beta and legacy /wiki URLs in custom_components/myhome/ or docs/ fail checks."""
    comp_dir = tmp_path / "custom_components" / "myhome"
    comp_dir.mkdir(parents=True)
    (comp_dir / "repairs.py").write_text(
        'URL1 = "https://openwebnet-ha.github.io/MyHOME/beta/diagnostics/repair-issues/"\n'
        'URL2 = "https://github.com/OpenWebNet-HA/MyHOME/wiki/Configuration#timezone"\n',
        encoding="utf-8",
    )
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "guide.md").write_text(
        "See [beta](https://openwebnet-ha.github.io/MyHOME/beta/) and "
        "[wiki](https://github.com/OpenWebNet-HA/MyHOME/wiki/Gateway-Timezone-Configuration).\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(syncdoc, "CUSTOM_COMPONENTS_DIR", comp_dir)
    monkeypatch.setattr(syncdoc, "DOCS_DIR", docs_dir)

    ok, messages = syncdoc.check_readme_and_site_standards()
    assert ok is False
    assert any("repairs.py still points documentation URL to '/MyHOME/beta'" in m for m in messages)
    assert any("repairs.py still points documentation URL to legacy GitHub '/wiki'" in m for m in messages)
    assert any("guide.md still links to '/MyHOME/beta'" in m for m in messages)
    assert any("guide.md still links to legacy GitHub '/wiki'" in m for m in messages)


def test_canonical_site_url_and_cross_version_relative_link_validation(tmp_path, monkeypatch):
    """Verify broken /MyHOME/latest/, /MyHOME/0.9.4/, #anchors, and ../../0.9.4 relative links fail."""
    docs_dir = tmp_path / "docs"
    (docs_dir / "configuration").mkdir(parents=True)
    (docs_dir / "index.md").write_text("# Home\n", encoding="utf-8")
    (docs_dir / "configuration" / "use_cases.md").write_text(
        "# Use Cases\n\n## 5. Audio { #music-assistant }\n",
        encoding="utf-8",
    )
    (docs_dir / "configuration" / "alarm.md").write_text(
        "# Alarm\n\nBroken relative escape: [Legacy](../../0.9.4/configuration/alarm/)\n",
        encoding="utf-8",
    )

    docs_v09_dir = tmp_path / "docs-v0.9"
    (docs_v09_dir / "getting-started").mkdir(parents=True)
    (docs_v09_dir / "index.md").write_text("# Legacy Home\n", encoding="utf-8")
    (docs_v09_dir / "getting-started" / "installation.md").write_text(
        "# Legacy Install\n\n"
        "Broken relative escape: [v2](../../latest/getting-started/installation/)\n"
        "Valid canonical: [Use Cases](https://openwebnet-ha.github.io/MyHOME/latest/configuration/use_cases/#music-assistant)\n"
        "Missing page: [Ghost](https://openwebnet-ha.github.io/MyHOME/latest/configuration/does_not_exist/)\n"
        "Missing anchor: [Bad Anchor](https://openwebnet-ha.github.io/MyHOME/latest/configuration/use_cases/#wrong-anchor)\n"
        "Unversioned URL: [No Version](https://openwebnet-ha.github.io/MyHOME/configuration/alarm/)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(syncdoc, "DOCS_DIR", docs_dir)
    monkeypatch.setattr(syncdoc, "DOCS_V09_DIR", docs_v09_dir)

    ok_rel, rel_msgs = syncdoc.check_markdown_link_health()
    assert ok_rel is False
    assert any("alarm.md" in m and "../../0.9.4/configuration/alarm/" in m for m in rel_msgs)
    assert any("installation.md" in m and "../../latest/getting-started/installation/" in m for m in rel_msgs)

    site_errs = syncdoc._validate_canonical_site_urls(
        (docs_v09_dir / "getting-started" / "installation.md").read_text(encoding="utf-8"),
        "docs-v0.9/getting-started/installation.md",
    )
    assert any("missing manual page" in m and "does_not_exist" in m for m in site_errs)
    assert any("missing anchor '#wrong-anchor'" in m for m in site_errs)
    assert any("unversioned or unsupported manual URL" in m for m in site_errs)
    assert not any("#music-assistant" in m for m in site_errs)


def _load_update_roadmap_tables():
    urt_script = REPO_ROOT / "scripts" / "update_roadmap_tables.py"
    urt_spec = importlib.util.spec_from_file_location("update_roadmap_tables_test", urt_script)
    urt_mod = importlib.util.module_from_spec(urt_spec)
    sys.modules["update_roadmap_tables_test"] = urt_mod
    urt_spec.loader.exec_module(urt_mod)
    return urt_mod


def test_roadmap_tables_empty_tx_oracle_partial_and_covered_states(tmp_path):
    """Verify dynamic status transitions across empty, tx-only, oracle, diagnostic-only, and covered fixtures."""
    import json

    urt = _load_update_roadmap_tables()
    fix_dir = tmp_path / "fixtures"
    fix_dir.mkdir()

    # 1. Empty corpus: normal gateways are Needed, 3578 is Deferred, Dim 10=255 is Synthetic, CU 4695 is Needed
    empty_summary = urt.scan_fixture_corpus(fixtures_dir=fix_dir)
    gw_empty = {r.mermaid_id: r for r in urt.evaluate_gateway_rows(empty_summary)}
    sub_empty = {r.mermaid_id: r for r in urt.evaluate_subsystem_rows(empty_summary)}
    assert gw_empty["GW_F454"].status_cell == "🔴 **Needed**"
    assert gw_empty["GW_F453"].status_cell == "🔴 **Needed**"
    assert gw_empty["GW_3578"].status_cell == "🔴 **Deferred (v2.1+)**"
    assert sub_empty["SUB_COV_255"].status_cell == "🟡 **Synthetic**"
    assert sub_empty["SUB_CU4695"].status_cell == "🔴 **Needed**"
    assert sub_empty["SUB_WHO22"].status_cell == "🔴 **Needed**"

    # 2. Outbound TX-only frames must NEVER falsely count as physical RX coverage
    tx_dir = fix_dir / "traces" / "tx_only"
    tx_dir.mkdir(parents=True)
    (tx_dir / "tx_probe.json").write_text(
        json.dumps(
            {
                "gateway": {"model": "F454"},
                "frames": [
                    {"direction": "tx", "raw": "*#4*#0#1##"},
                    {"direction": "tx", "raw": "*1*1*11##"},
                ],
            }
        ),
        encoding="utf-8",
    )
    tx_summary = urt.scan_fixture_corpus(fixtures_dir=fix_dir)
    gw_tx = {r.mermaid_id: r for r in urt.evaluate_gateway_rows(tx_summary)}
    sub_tx = {r.mermaid_id: r for r in urt.evaluate_subsystem_rows(tx_summary)}
    assert gw_tx["GW_F454"].status_cell == "🔴 **Needed**"
    assert sub_tx["SUB_CU4695"].status_cell == "🔴 **Needed**"

    # 3. Oracle / synthetic fixtures yield Synthetic, not Covered
    oracle_dir = fix_dir / "traces" / "oracle_f459"
    oracle_dir.mkdir(parents=True)
    (oracle_dir / "oracle.json").write_text(
        json.dumps(
            {
                "synthetic": True,
                "gateway": {"model": "F459"},
                "frames": [{"direction": "rx", "raw": "*22*1#4#1*3#7#5##"}],
            }
        ),
        encoding="utf-8",
    )
    oracle_summary = urt.scan_fixture_corpus(fixtures_dir=fix_dir)
    gw_oracle = {r.mermaid_id: r for r in urt.evaluate_gateway_rows(oracle_summary)}
    sub_oracle = {r.mermaid_id: r for r in urt.evaluate_subsystem_rows(oracle_summary)}
    assert gw_oracle["GW_F459"].status_cell == "🟡 **Synthetic**"
    assert sub_oracle["SUB_WHO22"].status_cell == "🟡 **Synthetic**"

    # 4. Diagnostic-only physical RX frames (WHO 13 / 1013) yield Partial on gateway
    diag_dir = fix_dir / "plants" / "mh202_diag"
    diag_dir.mkdir(parents=True)
    (diag_dir / "diag.json").write_text(
        json.dumps(
            {
                "gateway": {"model": "MH202"},
                "frames": [
                    {"direction": "rx", "raw": "*#13**15*200##"},
                    {"direction": "rx", "raw": "*#1013**1*5*15*5*0##"},
                ],
            }
        ),
        encoding="utf-8",
    )
    diag_summary = urt.scan_fixture_corpus(fixtures_dir=fix_dir)
    gw_diag = {r.mermaid_id: r for r in urt.evaluate_gateway_rows(diag_summary)}
    sub_diag = {r.mermaid_id: r for r in urt.evaluate_subsystem_rows(diag_summary)}
    assert gw_diag["GW_MH202"].status_cell == "🟡 **Partial**"
    assert sub_diag["SUB_DIAG"].status_cell == "🟢 **Covered**"

    # 5. Functional physical RX frames + cover_travel_times + log/jsonl parsing yield Covered
    f453_dir = fix_dir / "traces" / "disc_856"
    f453_dir.mkdir(parents=True)
    (f453_dir / "f453_diag.json").write_text(
        json.dumps(
            {
                "data": {
                    "gateway": {"model_name": "F453"},
                    "config_entry": {
                        "options": {
                            "cover_travel_times": {
                                "11": {"up": 16.0, "down": 14.0},
                                "12": {"up": 25.0, "down": 24.0},
                            }
                        }
                    },
                    "bus_monitor": {
                        "recent_frames": [
                            {"direction": "rx", "raw": "*2*1*11##"},
                            {"direction": "rx", "raw": "*2*0*11##"},
                        ]
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (f453_dir / "F455_direct.jsonl").write_text(
        '{"kind": "event", "raw": "*#4*1*0*0215##"}\n'
        '{"kind": "sent", "frame": "*#4*1*12##", "replies": ["*#4*1*12*0210*3##"]}\n',
        encoding="utf-8",
    )
    (f453_dir / "bundle.txt").write_text(
        "- **Model:** MyHomeServer1\n"
        "[00:01:00.000] [TX] *#4*5*#14*0200*1##\n"
        "[00:01:00.100] [RX] *#4*5*#5*0##\n"
        "[00:01:00.200] [RX] *22*1#4#1*3#7#5##\n",
        encoding="utf-8",
    )

    cov_summary = urt.scan_fixture_corpus(fixtures_dir=fix_dir)
    gw_cov = {r.mermaid_id: r for r in urt.evaluate_gateway_rows(cov_summary)}
    sub_cov = {r.mermaid_id: r for r in urt.evaluate_subsystem_rows(cov_summary)}

    assert gw_cov["GW_F453"].status_cell == "🟢 **Covered**"
    assert "optional legacy `F452 / F452V` captures welcomed" in gw_cov["GW_F453"].target_cell
    assert gw_cov["GW_F455"].status_cell == "🟢 **Covered**"
    assert gw_cov["GW_MHS1"].status_cell == "🟢 **Covered**"
    # Ensure '*#4*5*#5*0##' was parsed as WHO 4, NOT mis-tokenized at '*5*0##' as WHO 5!
    assert sub_cov["SUB_ALARM"].status_cell == "🔴 **Needed**"
    assert sub_cov["SUB_COV_V"].status_cell == "🟢 **Covered**"
    assert "2 calibrated `cover_travel_times` profiles" in sub_cov["SUB_COV_V"].evidence_cell
    assert sub_cov["SUB_WHO22"].status_cell == "🟢 **Covered**"


def test_roadmap_tables_unmapped_model_raises(tmp_path):
    """Verify unmapped gateway models in const.py or fixtures raise ValueError."""
    import json

    urt = _load_update_roadmap_tables()
    fix_dir = tmp_path / "fixtures"
    fix_dir.mkdir()
    bad_const = tmp_path / "const.py"
    bad_const.write_text('SUPPORTED_GATEWAY_MODELS = ["F454", "BrandNewF999"]\n', encoding="utf-8")

    with pytest.raises(ValueError, match="BrandNewF999"):
        urt.scan_fixture_corpus(fixtures_dir=fix_dir, const_py=bad_const)

    (fix_dir / "mystery.json").write_text(
        json.dumps(
            {
                "gateway": {"model": "MysteryGW9000"},
                "frames": [{"direction": "rx", "raw": "*1*1*11##"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="MysteryGW9000"):
        urt.scan_fixture_corpus(fixtures_dir=fix_dir)


def test_sync_roadmap_tables_detects_drift_missing_markers_and_updates(tmp_path, monkeypatch):
    """Verify sync_roadmap_tables catches missing files, missing markers, and out-of-sync blocks."""
    missing_rm = tmp_path / "missing_roadmap.md"
    no_markers_rm = tmp_path / "no_markers.md"
    no_markers_rm.write_text("# Roadmap without markers\n", encoding="utf-8")

    monkeypatch.setattr(syncdoc, "ROADMAP_DOC_TARGETS", [missing_rm, no_markers_rm])
    monkeypatch.setattr(
        syncdoc,
        "get_roadmap_blocks",
        lambda: (
            f"{syncdoc.ROADMAP_MERMAID_START_MARKER}\nM\n{syncdoc.ROADMAP_MERMAID_END_MARKER}",
            f"{syncdoc.ROADMAP_GATEWAY_START_MARKER}\nG\n{syncdoc.ROADMAP_GATEWAY_END_MARKER}",
            f"{syncdoc.ROADMAP_SUBSYSTEM_START_MARKER}\nS\n{syncdoc.ROADMAP_SUBSYSTEM_END_MARKER}",
        ),
    )

    ok_bad, msgs_bad = syncdoc.sync_roadmap_tables(update=False)
    assert ok_bad is False
    assert any("not found" in m for m in msgs_bad)
    assert any("Missing markers" in m for m in msgs_bad)

    drifted_rm = tmp_path / "ROADMAP.md"
    drifted_rm.write_text(
        f"# Roadmap\n"
        f"{syncdoc.ROADMAP_MERMAID_START_MARKER}\nOLD_M\n{syncdoc.ROADMAP_MERMAID_END_MARKER}\n"
        f"{syncdoc.ROADMAP_GATEWAY_START_MARKER}\nOLD_G\n{syncdoc.ROADMAP_GATEWAY_END_MARKER}\n"
        f"{syncdoc.ROADMAP_SUBSYSTEM_START_MARKER}\nOLD_S\n{syncdoc.ROADMAP_SUBSYSTEM_END_MARKER}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(syncdoc, "ROADMAP_DOC_TARGETS", [drifted_rm])

    ok_drift, msgs_drift = syncdoc.sync_roadmap_tables(update=False)
    assert ok_drift is False
    assert sum("out of sync" in m for m in msgs_drift) == 3

    ok_upd, msgs_upd = syncdoc.sync_roadmap_tables(update=True)
    assert ok_upd is True
    assert sum("Updated Roadmap" in m for m in msgs_upd) == 3

    ok_clean, _ = syncdoc.sync_roadmap_tables(update=False)
    assert ok_clean is True


def test_check_readme_and_site_standards_rejects_roadmap_regressions(tmp_path, monkeypatch):
    """Verify false WHO 22 deprecation/entity claims, shutterRun=AUTO, beta/wiki URLs, and outdated OWNd pins fail."""
    bad_roadmap = tmp_path / "ROADMAP.md"
    bad_roadmap.write_text(
        "# Roadmap\n\n"
        "### 5. WHO 22 — Formally Deprecated\n"
        "WHO 22 defines protocol frames for obsolete Legrand analog FM radio tuner modules.\n"
        "Exposes station preset slot `1-8` number and (50 kHz step and preset save).\n"
        "Calibration uses shutterRun=AUTO.\n"
        "Pinned to `OWNd==1.0.0`.\n"
        "Manual: [Beta](https://openwebnet-ha.github.io/MyHOME/beta/)\n"
        "Wiki: [Wiki](https://github.com/OpenWebNet-HA/MyHOME/wiki)\n"
        "Broken: [Missing](#nonexistent-heading) and [NoFile](DOES_NOT_EXIST.md)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(syncdoc, "ROADMAP_MD", bad_roadmap)

    ok_std, msgs_std = syncdoc.check_readme_and_site_standards()
    assert ok_std is False
    assert any("Formally Deprecated" in m for m in msgs_std)
    assert any("obsolete Legrand analog FM" in m for m in msgs_std)
    assert any("station preset slot" in m for m in msgs_std)
    assert any("preset save)" in m for m in msgs_std)
    assert any("shutterRun=AUTO" in m for m in msgs_std)
    assert any("ROADMAP.md still links to '/MyHOME/beta'" in m for m in msgs_std)
    assert any("ROADMAP.md still links to deprecated GitHub '/wiki'" in m for m in msgs_std)
    assert any("Unresolved internal heading anchor in ROADMAP.md" in m for m in msgs_std)
    assert any("Broken relative file link in ROADMAP.md" in m for m in msgs_std)

    ok_ver, msgs_ver = syncdoc.sync_version_references(update=False)
    assert ok_ver is False
    assert any("Outdated OWNd reference" in m and "ROADMAP.md" in m for m in msgs_ver)

    ok_ver_up, _ = syncdoc.sync_version_references(update=True)
    assert ok_ver_up is True
    _, expected_ownd = syncdoc.extract_manifest_version_info()
    assert expected_ownd in bad_roadmap.read_text(encoding="utf-8")


def test_roadmap_tables_keyed_json_windows_concatenated_tcp_and_readme_model_order(tmp_path):
    """Verify keyed JSON window captures, concatenated TCP bursts, compound tilt/dimmer frames, and README model order."""
    import json

    urt = _load_update_roadmap_tables()
    fix_dir = tmp_path / "fixtures"

    # 1. Keyed JSON scenario windows ([ts, gw, raw], <gw>_*: [ts, rx|tx, raw], meta.gateway + dir/bus_rx)
    win_dir = fix_dir / "traces" / "live_windows"
    win_dir.mkdir(parents=True)
    (win_dir / "central_flags.json").write_text(
        json.dumps(
            {
                "quarter_0745": [
                    ["07:45:00.940", "f455", "*#4*4*0*0256##"],
                    ["07:45:01.743", "f461", "*#4*4*12*0240*3##"],
                ]
            }
        ),
        encoding="utf-8",
    )
    (win_dir / "climate_mode.json").write_text(
        json.dumps(
            {
                "_source": "Live capture",
                "f455_zone2_heat": [
                    ["14:26:29.114", "tx", "*#4*#0#1##"],
                    ["14:26:29.209", "rx", "*4*3100*#0##"],
                ],
            }
        ),
        encoding="utf-8",
    )
    (win_dir / "audio_wake.json").write_text(
        json.dumps(
            {
                "meta": {"gateway": "BTicino MH200 (firmware 2.1.0) + F441M matrix"},
                "tx": [{"t": 0.0, "frame": "*#4*#0#1##"}],
                "park": {
                    "expected_tx": ["*#4*#0#1##"],
                    "bus_rx": [{"t": "10:56:20", "frame": "*#16*36*1*26##"}],
                },
                "second_play": [
                    {"t": 0.1, "dir": "tx", "frame": "*#4*#0#1##"},
                    {"t": 0.8, "dir": "rx", "frame": "*16*3*36##"},
                ],
            }
        ),
        encoding="utf-8",
    )
    # Directionless frames dict with telemetry.total_rx == 0 must be ignored
    (win_dir / "zero_rx.json").write_text(
        json.dumps(
            {
                "gateway": {"model": "MH201"},
                "telemetry": {"total_rx": 0, "total_tx": 1},
                "frames": [{"raw": "*#4*#0#1##"}],
            }
        ),
        encoding="utf-8",
    )

    # 2. README earliest-mention gateway attribution + concatenated TCP bursts + Dim 4 + compound Venetian #11#001#1
    f454_dir = fix_dir / "traces" / "readme_order_f454"
    f454_dir.mkdir(parents=True)
    (f454_dir / "README.md").write_text(
        "# Capture\nRecorded on an F454 webserver (behaves differently from MyHomeServer1).\n",
        encoding="utf-8",
    )
    (f454_dir / "capture.txt").write_text(
        "[12:00:00.100] [RX] *#1*0010*4*181*2##*#2*21*#11#001#1*10##*0*1*43##\n",
        encoding="utf-8",
    )

    mh200n_dir = fix_dir / "traces" / "readme_tie_mh200n"
    mh200n_dir.mkdir(parents=True)
    (mh200n_dir / "README.md").write_text(
        "# Capture\nRecorded on MH200N gateway.\n",
        encoding="utf-8",
    )
    (mh200n_dir / "capture.txt").write_text(
        "[12:00:01.000] [RX] *25*21#1*11##\n",
        encoding="utf-8",
    )

    summary = urt.scan_fixture_corpus(fixtures_dir=fix_dir)
    gw_map = {r.mermaid_id: r for r in urt.evaluate_gateway_rows(summary)}
    sub_map = {r.mermaid_id: r for r in urt.evaluate_subsystem_rows(summary)}

    assert gw_map["GW_F455"].status_cell == "🟢 **Covered**"
    assert gw_map["GW_F461"].status_cell == "🟢 **Covered**"
    assert gw_map["GW_MH200"].status_cell == "🟢 **Covered**"
    assert gw_map["GW_F454"].status_cell == "🟢 **Covered**"
    assert gw_map["GW_MH200N"].status_cell == "🟢 **Covered**"
    # F454 README mentioned MyHomeServer1 second; MH200N tied at offset with MH200 -> neither misattributed!
    assert gw_map["GW_MHS1"].status_cell == "🔴 **Needed**"
    assert gw_map["GW_MH201"].status_cell == "🔴 **Needed**"

    # Outbound tx '#0#1' frames and zero_rx frames were ignored
    assert sub_map["SUB_CU4695"].status_cell == "🔴 **Needed**"
    # Concatenated TCP line & new subsystem regexes matched all 3 frames + CEN+ 21# + thermo zones + CU 3550 + WHO 16
    assert sub_map["SUB_LIGHT"].status_cell == "🟢 **Covered**"
    assert sub_map["SUB_COV_H"].status_cell == "🟢 **Covered**"
    assert sub_map["SUB_SCENARIOS"].status_cell == "🟢 **Covered**"
    assert sub_map["SUB_CEN"].status_cell == "🟢 **Covered**"
    assert sub_map["SUB_THERMO_ZONES"].status_cell == "🟢 **Covered**"
    assert sub_map["SUB_CU3550"].status_cell == "🟢 **Covered**"
    assert sub_map["SUB_WHO16"].status_cell == "🟢 **Covered**"


def test_roadmap_tables_deduplicates_identical_capture_sequences(tmp_path):
    """Verify duplicate (gateway, is_oracle, rx_frames) files count once while distinct gateways/sequences count separately."""
    import json

    urt = _load_update_roadmap_tables()
    fix_dir = tmp_path / "fixtures"

    # Pair 1: Identical (F455, False, rx_frames) across plants/ and traces/ (.json + .log) -> counted once,
    # while calibrated cover_travel_times on the duplicate bundle are still preserved.
    plant_dir = fix_dir / "plants" / "dup_f455"
    plant_dir.mkdir(parents=True)
    (plant_dir / "diagnostic_summary.json").write_text(
        json.dumps(
            {
                "data": {
                    "gateway": {"model_name": "F455"},
                    "config_entry": {
                        "options": {
                            "cover_travel_times": {
                                "11": {"up": 18.0, "down": 17.0},
                            }
                        }
                    },
                    "bus_monitor": {
                        "recent_frames": [
                            {"direction": "rx", "raw": "*1*1*11##"},
                            {"direction": "rx", "raw": "*2*0*11##"},
                        ]
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    trace_dir = fix_dir / "traces" / "dup_f455"
    trace_dir.mkdir(parents=True)
    (trace_dir / "trace.json").write_text(
        json.dumps(
            {
                "gateway": {"model": "F455"},
                "frames": [
                    {"direction": "rx", "raw": "*1*1*11##"},
                    {"direction": "rx", "raw": "*2*0*11##"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (trace_dir / "timeline.log").write_text(
        "# Gateway: F455\n"
        "[10:00:00.000] [RX] *1*1*11##\n"
        "[10:00:01.000] [RX] *2*0*11##\n",
        encoding="utf-8",
    )

    # Pair 2: Same rx_frames on a DIFFERENT gateway (F454) -> must still be counted!
    diff_gw_dir = fix_dir / "traces" / "diff_gw_f454"
    diff_gw_dir.mkdir(parents=True)
    (diff_gw_dir / "trace.json").write_text(
        json.dumps(
            {
                "gateway": {"model": "F454"},
                "frames": [
                    {"direction": "rx", "raw": "*1*1*11##"},
                    {"direction": "rx", "raw": "*2*0*11##"},
                ],
            }
        ),
        encoding="utf-8",
    )

    # Pair 3: Same gateway (F455) with DIFFERENT rx_frames -> must also be counted!
    diff_seq_dir = fix_dir / "traces" / "diff_seq_f455"
    diff_seq_dir.mkdir(parents=True)
    (diff_seq_dir / "trace2.json").write_text(
        json.dumps(
            {
                "gateway": {"model": "F455"},
                "frames": [
                    {"direction": "rx", "raw": "*1*0*11##"},
                ],
            }
        ),
        encoding="utf-8",
    )

    # Pair 4: Synthetic/oracle capture with identical gateway & rx_frames does not collide with physical capture
    oracle_dir = fix_dir / "traces" / "oracle_f455"
    oracle_dir.mkdir(parents=True)
    (oracle_dir / "oracle.json").write_text(
        json.dumps(
            {
                "synthetic": True,
                "gateway": {"model": "F455"},
                "frames": [
                    {"direction": "rx", "raw": "*1*0*11##"},
                ],
            }
        ),
        encoding="utf-8",
    )

    # Pair 5: Multi-gateway interleaved .log deduplicates F454 group (matching diff_gw_f454)
    # without suppressing MH202's interleaved frames in the same log file.
    multi_dir = fix_dir / "traces" / "multi_interleaved"
    multi_dir.mkdir(parents=True)
    (multi_dir / "combined.log").write_text(
        "2026-10-01 10:00:00 DEBUG [F454 gateway - 192.168.1.35] Message received: `*1*1*11##`\n"
        "2026-10-01 10:00:01 DEBUG [MH202 gateway - 192.168.1.36] Message received: `*4*1*1##`\n"
        "2026-10-01 10:00:02 DEBUG [F454 gateway - 192.168.1.35] Message received: `*2*0*11##`\n",
        encoding="utf-8",
    )

    # Pair 6: Unattributed / Generic captures with identical frames are NOT falsely deduplicated
    # as same-gateway captures, and Generic/Unknown do not inflate Table 2 gateway model counts.
    unattr_a = fix_dir / "traces" / "unattr_a"
    unattr_a.mkdir(parents=True)
    (unattr_a / "a.json").write_text(
        json.dumps(
            {
                "gateway": {"model": "Generic"},
                "frames": [{"direction": "rx", "raw": "*14*0*11##"}],
            }
        ),
        encoding="utf-8",
    )
    unattr_b = fix_dir / "traces" / "unattr_b"
    unattr_b.mkdir(parents=True)
    (unattr_b / "b.json").write_text(
        json.dumps(
            {
                "gateway": {"model": "Generic"},
                "frames": [{"direction": "rx", "raw": "*14*0*11##"}],
            }
        ),
        encoding="utf-8",
    )

    summary = urt.scan_fixture_corpus(fixtures_dir=fix_dir)
    f455_stats = summary.by_model["F455"]
    f454_stats = summary.by_model["F454"]
    mh202_stats = summary.by_model["MH202"]

    # F455 had 3 physical files (2 duplicates of len=2 + 1 distinct of len=1) -> 2 captures, 3 rx frames!
    assert len(f455_stats.phys_files) == 2
    assert f455_stats.phys_rx_frames == 3
    # Canonical traces/ JSON won over plants/ and .log
    assert "traces/dup_f455/trace.json" in f455_stats.phys_files
    assert "traces/diff_seq_f455/trace2.json" in f455_stats.phys_files
    # Oracle tracked separately without shadowing physical
    assert len(f455_stats.oracle_files) == 1
    assert f455_stats.oracle_rx_frames == 1
    # F454 with identical rx sequence to F455 was counted once (combined.log F454 duplicate skipped)
    assert len(f454_stats.phys_files) == 1
    assert f454_stats.phys_rx_frames == 2
    # MH202 interleaved inside combined.log was preserved independently
    assert len(mh202_stats.phys_files) == 1
    assert mh202_stats.phys_rx_frames == 1
    assert "Generic" not in summary.by_model
    # Both unattributed Generic captures kept, and Generic excluded from Table 2 gateway model count
    sub_map = {r.mermaid_id: r for r in urt.evaluate_subsystem_rows(summary)}
    assert "2 physical `rx` frames across 2 captures —" in sub_map["SUB_LOCK14"].evidence_cell
    assert "gateway model" not in sub_map["SUB_LOCK14"].evidence_cell
    # Calibrated cover_travel_times on the duplicate plants/ JSON was still preserved
    assert summary.calibrated_cover_bundles == [("tests/fixtures/plants/dup_f455/", 1)]



