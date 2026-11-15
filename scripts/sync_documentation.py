#!/usr/bin/env python3
"""Automated Documentation Synchronization & Anti-Drift Sentinel.

Validates and synchronizes documentation (README.md and the docs/ MkDocs github.io site)
against the codebase:
1. Gateway Profiles table (README.md, docs/getting-started/hardware-compatibility.md,
   docs/configuration/gateways.md, docs/index.md) cross-referenced with const.py.
2. Supported Entity Domains & Automations table (README.md, docs/configuration/supported_functions.md)
   cross-referenced with const.py (PLATFORMS) and device_trigger.py.
3. Hardware Trace Availability Matrix (README.md, docs/trace-availability.md)
   cross-referenced with actual test fixtures.
4. Services & Actions Reference (docs/configuration/services.md)
   cross-referenced with custom_components/myhome/services.yaml.
5. Home Assistant Repairs Issues (docs/diagnostics/repair-issues.md)
   cross-referenced with custom_components/myhome/repairs.py and strings.json.
6. MkDocs navigation & link integrity.

CLI Modes:
- python scripts/sync_documentation.py --check: Verifies docs are in sync, exits 1 on drift.
- python scripts/sync_documentation.py --update: Rewrites out-of-sync doc blocks in place.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CUSTOM_COMPONENTS_DIR = REPO_ROOT / "custom_components" / "myhome"
CONST_PY = CUSTOM_COMPONENTS_DIR / "const.py"
MANIFEST_JSON = CUSTOM_COMPONENTS_DIR / "manifest.json"
SERVICES_YAML = CUSTOM_COMPONENTS_DIR / "services.yaml"
STRINGS_JSON = CUSTOM_COMPONENTS_DIR / "strings.json"
REPAIRS_PY = CUSTOM_COMPONENTS_DIR / "repairs.py"
MKDOCS_YML = REPO_ROOT / "mkdocs.yml"
MKDOCS_V09_YML = REPO_ROOT / "mkdocs-v0.9.yml"
README_MD = REPO_ROOT / "README.md"
ROADMAP_MD = REPO_ROOT / "ROADMAP.md"
PYPROJECT_TOML = REPO_ROOT / "pyproject.toml"
ISSUE_TEMPLATE_CONFIG = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml"
DOCS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docs.yml"
DOCS_DIR = REPO_ROOT / "docs"
DOCS_V09_DIR = REPO_ROOT / "docs-v0.9"

ROADMAP_DOC_TARGETS = [
    ROADMAP_MD,
    DOCS_DIR / "roadmap.md",
]

CANONICAL_DOCS_URL = "https://openwebnet-ha.github.io/MyHOME/"
_LEGACY_WIKI_RE = re.compile(
    r"github\.com/(?:OpenWebNet-HA|anotherjulien)/MyHOME/wiki",
    re.IGNORECASE,
)

# Documentation target files for Gateway Profiles table
GATEWAY_DOC_TARGETS = [
    README_MD,
    DOCS_DIR / "configuration" / "gateways.md",
    DOCS_DIR / "getting-started" / "hardware-compatibility.md",
    DOCS_DIR / "index.md",
]

# Markers
GATEWAY_START_MARKER = "<!-- GATEWAY_PROFILES_START -->"
GATEWAY_END_MARKER = "<!-- GATEWAY_PROFILES_END -->"

OPTIONS_START_MARKER = "<!-- GATEWAY_OPTIONS_START -->"
OPTIONS_END_MARKER = "<!-- GATEWAY_OPTIONS_END -->"

DISCOVERY_START_MARKER = "<!-- GATEWAY_DISCOVERY_START -->"
DISCOVERY_END_MARKER = "<!-- GATEWAY_DISCOVERY_END -->"

DOMAINS_START_MARKER = "<!-- SUPPORTED_DOMAINS_START -->"
DOMAINS_END_MARKER = "<!-- SUPPORTED_DOMAINS_END -->"

TRACE_START_MARKER = "<!-- TRACE_MATRIX_START -->"
TRACE_END_MARKER = "<!-- TRACE_MATRIX_END -->"

SERVICES_START_MARKER = "<!-- SERVICES_TABLE_START -->"
SERVICES_END_MARKER = "<!-- SERVICES_TABLE_END -->"

ROADMAP_MERMAID_START_MARKER = "<!-- ROADMAP_COVERAGE_MERMAID_START -->"
ROADMAP_MERMAID_END_MARKER = "<!-- ROADMAP_COVERAGE_MERMAID_END -->"
ROADMAP_GATEWAY_START_MARKER = "<!-- ROADMAP_GATEWAY_TABLE_START -->"
ROADMAP_GATEWAY_END_MARKER = "<!-- ROADMAP_GATEWAY_TABLE_END -->"
ROADMAP_SUBSYSTEM_START_MARKER = "<!-- ROADMAP_SUBSYSTEM_TABLE_START -->"
ROADMAP_SUBSYSTEM_END_MARKER = "<!-- ROADMAP_SUBSYSTEM_TABLE_END -->"


def rel_path(p: Path) -> str:
    """Safely return path relative to REPO_ROOT, or path string if outside."""
    try:
        return str(p.relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Gateway Profiles Synchronization
# ─────────────────────────────────────────────────────────────────────────────

def get_gateway_block() -> str:
    """Build the canonical Gateway Profiles table block."""
    try:
        from scripts.update_gateway_profiles import build_block
    except ImportError:
        from update_gateway_profiles import build_block
    return build_block(CONST_PY, CUSTOM_COMPONENTS_DIR / "manifest.json")


def sync_gateway_profiles(update: bool = False) -> tuple[bool, list[str]]:
    """Check or update the Gateway Profiles table across all target files."""
    messages: list[str] = []
    all_ok = True

    try:
        from scripts.update_gateway_profiles import verify_profiles_against_oracle
    except ImportError:
        from update_gateway_profiles import verify_profiles_against_oracle

    oracle_violations = verify_profiles_against_oracle(
        manifest_path=CUSTOM_COMPONENTS_DIR / "manifest.json",
    )
    if oracle_violations:
        for violation in oracle_violations:
            messages.append(f"Gateway profile oracle mismatch: {violation}")
        all_ok = False
    else:
        messages.append("Gateway profiles are consistent with firmware oracle verdicts")

    expected_block = get_gateway_block().strip()
    pattern = re.compile(
        rf"{re.escape(GATEWAY_START_MARKER)}.*?{re.escape(GATEWAY_END_MARKER)}",
        re.DOTALL,
    )

    for target in GATEWAY_DOC_TARGETS:
        if not target.exists():
            messages.append(f"Gateway target not found: {rel_path(target)}")
            all_ok = False
            continue

        content = target.read_text(encoding="utf-8")
        if GATEWAY_START_MARKER not in content or GATEWAY_END_MARKER not in content:
            messages.append(
                f"Missing markers {GATEWAY_START_MARKER} and/or {GATEWAY_END_MARKER} "
                f"in {rel_path(target)}"
            )
            all_ok = False
            continue

        match = pattern.search(content)
        if not match:
            messages.append(f"Could not parse marker block in {rel_path(target)}")
            all_ok = False
            continue

        actual_block = match.group(0).strip()
        if actual_block != expected_block:
            if update:
                new_content = pattern.sub(expected_block, content)
                target.write_text(new_content, encoding="utf-8")
                messages.append(f"Updated Gateway Profiles table in {rel_path(target)}")
            else:
                messages.append(
                    f"Gateway Profiles table out of sync in {rel_path(target)}"
                )
                all_ok = False
        else:
            messages.append(f"Gateway Profiles table in sync: {rel_path(target)}")

    return all_ok, messages


def get_gateway_discovery_block() -> str:
    """Build the canonical Gateway Startup Discovery Capabilities table block.

    While manifest.json still pins OWNd==2.0.0b11 (before OWNd #142 is in a pinned
    release), the committed docs track the pinned OWNd rows (`use_pinned=True`) so
    CI jobs with unreleased OWNd@master and CI jobs without OWNd agree on the same
    table. Once manifest.json bumps past 2.0.0b11, `use_pinned` flips to False and
    both `verify_profiles_against_oracle()` and `--check` enforce regenerating the rows.
    """
    try:
        from scripts.update_gateway_profiles import (
            build_discovery_block,
            manifest_pins_pre_who16_ownd,
        )
    except ImportError:
        from update_gateway_profiles import (
            build_discovery_block,
            manifest_pins_pre_who16_ownd,
        )
    manifest_path = CUSTOM_COMPONENTS_DIR / "manifest.json"
    return build_discovery_block(use_pinned=manifest_pins_pre_who16_ownd(manifest_path))


def sync_gateway_discovery(update: bool = False) -> tuple[bool, list[str]]:
    """Check or update the Gateway Startup Discovery Capabilities table in hardware-compatibility.md."""
    target = DOCS_DIR / "getting-started" / "hardware-compatibility.md"
    messages: list[str] = []
    if not target.exists():
        return False, [f"Hardware compatibility doc not found: {rel_path(target)}"]

    content = target.read_text(encoding="utf-8")
    if DISCOVERY_START_MARKER not in content or DISCOVERY_END_MARKER not in content:
        messages.append(
            f"Missing markers {DISCOVERY_START_MARKER} and/or {DISCOVERY_END_MARKER} "
            f"in {rel_path(target)}"
        )
        return False, messages

    pattern = re.compile(
        rf"{re.escape(DISCOVERY_START_MARKER)}.*?{re.escape(DISCOVERY_END_MARKER)}",
        re.DOTALL,
    )
    match = pattern.search(content)
    if not match:
        messages.append(f"Could not parse discovery marker block in {rel_path(target)}")
        return False, messages

    expected_block = get_gateway_discovery_block().strip()
    actual_block = match.group(0).strip()
    if actual_block != expected_block:
        if update:
            new_content = pattern.sub(expected_block, content)
            target.write_text(new_content, encoding="utf-8")
            messages.append(f"Updated Gateway Discovery table in {rel_path(target)}")
            return True, messages
        else:
            messages.append(f"Gateway Discovery table out of sync in {rel_path(target)}")
            return False, messages
    else:
        messages.append(f"Gateway Discovery table in sync: {rel_path(target)}")
        return True, messages


def get_gateway_options_block() -> str:
    """Build the canonical Gateway Runtime Options table block."""
    try:
        from scripts.update_gateway_profiles import build_options_block
    except ImportError:
        from update_gateway_profiles import build_options_block
    return build_options_block()


def sync_gateway_options(update: bool = False) -> tuple[bool, list[str]]:
    """Check or update the Gateway Runtime Options table in docs/configuration/gateways.md."""
    target = DOCS_DIR / "configuration" / "gateways.md"
    messages: list[str] = []
    if not target.exists():
        return False, [f"Gateways doc not found: {rel_path(target)}"]

    content = target.read_text(encoding="utf-8")
    if OPTIONS_START_MARKER not in content or OPTIONS_END_MARKER not in content:
        messages.append(
            f"Missing markers {OPTIONS_START_MARKER} and/or {OPTIONS_END_MARKER} "
            f"in {rel_path(target)}"
        )
        return False, messages

    pattern = re.compile(
        rf"{re.escape(OPTIONS_START_MARKER)}.*?{re.escape(OPTIONS_END_MARKER)}",
        re.DOTALL,
    )
    match = pattern.search(content)
    if not match:
        messages.append(f"Could not parse options marker block in {rel_path(target)}")
        return False, messages

    expected_block = get_gateway_options_block().strip()
    actual_block = match.group(0).strip()
    if actual_block != expected_block:
        if update:
            new_content = pattern.sub(expected_block, content)
            target.write_text(new_content, encoding="utf-8")
            messages.append(f"Updated Gateway Options table in {rel_path(target)}")
            return True, messages
        else:
            messages.append(f"Gateway Options table out of sync in {rel_path(target)}")
            return False, messages
    else:
        messages.append(f"Gateway Options table in sync: {rel_path(target)}")
        return True, messages


# ─────────────────────────────────────────────────────────────────────────────
# 2. Supported Entity Domains & Automations Synchronization
# ─────────────────────────────────────────────────────────────────────────────

def sync_supported_domains(update: bool = False) -> tuple[bool, list[str]]:
    """Check or update the Supported Entity Domains table in README.md."""
    try:
        from scripts.update_supported_domains import (
            check_readme_in_sync,
            update_readme,
        )
    except ImportError:
        from update_supported_domains import (
            check_readme_in_sync,
            update_readme,
        )

    messages: list[str] = []
    if update:
        changed = update_readme(README_MD)
        if changed:
            messages.append("Updated Supported Entity Domains table in README.md")
        else:
            messages.append("Supported Entity Domains table in README.md is already up to date")
    else:
        in_sync, msg = check_readme_in_sync(README_MD)
        if not in_sync:
            messages.append(f"Supported Entity Domains table out of sync: {msg}")
            return False, messages
        messages.append("Supported Entity Domains table in sync: README.md")

    # Also verify that every platform in PLATFORMS (const.py) is documented in supported_functions.md
    supported_functions_doc = DOCS_DIR / "configuration" / "supported_functions.md"
    if supported_functions_doc.exists():
        content = supported_functions_doc.read_text(encoding="utf-8")
        try:
            from scripts.update_supported_domains import extract_platforms_from_const
        except ImportError:
            from update_supported_domains import extract_platforms_from_const
        platforms = extract_platforms_from_const()
        missing_platforms = []
        for p in platforms:
            pattern = re.compile(rf"###\s+`?{re.escape(p)}`?", re.IGNORECASE)
            if not pattern.search(content):
                missing_platforms.append(p)
        if missing_platforms:
            messages.append(
                f"Platforms from const.py missing sections in supported_functions.md: {missing_platforms}"
            )
            return False, messages
        else:
            messages.append(f"All {len(platforms)} platforms are documented in supported_functions.md")

    return True, messages


# ─────────────────────────────────────────────────────────────────────────────
# 3. Hardware Trace Availability Matrix Synchronization
# ─────────────────────────────────────────────────────────────────────────────

def sync_trace_matrix(update: bool = False) -> tuple[bool, list[str]]:
    """Check or update the Hardware Trace Matrix across README.md and docs/trace-availability.md."""
    try:
        from scripts.update_trace_matrix import build_matrix, update_file
    except ImportError:
        from update_trace_matrix import build_matrix, update_file

    matrix_md = build_matrix()
    expected_block = f"{TRACE_START_MARKER}\n{matrix_md}\n{TRACE_END_MARKER}".strip()
    pattern = re.compile(
        rf"{re.escape(TRACE_START_MARKER)}.*?{re.escape(TRACE_END_MARKER)}",
        re.DOTALL,
    )

    targets = [README_MD, DOCS_DIR / "trace-availability.md"]
    messages: list[str] = []
    all_ok = True

    for target in targets:
        if not target.exists():
            messages.append(f"Trace matrix target not found: {rel_path(target)}")
            all_ok = False
            continue

        content = target.read_text(encoding="utf-8")
        if TRACE_START_MARKER not in content or TRACE_END_MARKER not in content:
            messages.append(
                f"Missing markers {TRACE_START_MARKER} / {TRACE_END_MARKER} in {rel_path(target)}"
            )
            all_ok = False
            continue

        match = pattern.search(content)
        if not match:
            messages.append(f"Could not parse trace matrix block in {rel_path(target)}")
            all_ok = False
            continue

        if match.group(0).strip() != expected_block:
            if update:
                update_file(target, matrix_md)
                messages.append(f"Updated Trace Matrix in {rel_path(target)}")
            else:
                messages.append(f"Trace Matrix out of sync in {rel_path(target)}")
                all_ok = False
        else:
            messages.append(f"Trace Matrix in sync: {rel_path(target)}")

    if update:
        try:
            try:
                from scripts.update_trace_matrix import sync_github_issue
            except (ImportError, AttributeError):
                from update_trace_matrix import sync_github_issue
            sync_github_issue(issue_number=466, new_table=matrix_md)
            messages.append("Triggered GitHub issue #466 trace matrix sync")
        except Exception as e:
            messages.append(f"GitHub issue sync skipped/failed: {e}")

    return all_ok, messages


def get_roadmap_blocks() -> tuple[str, str, str]:
    """Build the canonical (mermaid, gateway_table, subsystem_table) blocks for ROADMAP.md."""
    try:
        from scripts.update_roadmap_tables import build_all_roadmap_blocks
    except ImportError:
        from update_roadmap_tables import build_all_roadmap_blocks
    return build_all_roadmap_blocks()


def sync_roadmap_tables(update: bool = False) -> tuple[bool, list[str]]:
    """Check or update dynamic Gateway (Table 1), Subsystem (Table 2), and Mermaid blocks in roadmap docs."""
    try:
        from scripts.update_roadmap_tables import sync_roadmap_file
    except ImportError:
        from update_roadmap_tables import sync_roadmap_file

    blocks = get_roadmap_blocks()
    messages: list[str] = []
    all_ok = True

    for target in ROADMAP_DOC_TARGETS:
        ok_t, msgs_t = sync_roadmap_file(target, blocks, update=update)
        all_ok = all_ok and ok_t
        messages.extend(msgs_t)

    return all_ok, messages


# ─────────────────────────────────────────────────────────────────────────────
# 4. Services Reference Synchronization
# ─────────────────────────────────────────────────────────────────────────────

def parse_services_yaml() -> dict[str, Any]:
    """Parse custom_components/myhome/services.yaml."""
    if not SERVICES_YAML.exists():
        raise FileNotFoundError(f"{SERVICES_YAML} not found")

    content = SERVICES_YAML.read_text(encoding="utf-8")
    try:
        import yaml
        return yaml.safe_load(content) or {}
    except ImportError as exc:
        raise ImportError(
            "PyYAML is required to parse services.yaml. "
            "Please install PyYAML (e.g. `pip install pyyaml`)."
        ) from exc


def generate_services_summary_table(services_data: dict[str, Any]) -> str:
    """Generate the markdown table for services summary."""
    lines = [
        "| Service | Target | Description |",
        "| :--- | :--- | :--- |",
    ]

    for service_name, s_data in sorted(services_data.items()):
        # Determine target
        target = "Gateway"
        if isinstance(s_data, dict):
            target_dict = s_data.get("target", {})
            if isinstance(target_dict, dict) and "entity" in target_dict:
                ent = target_dict["entity"]
                if isinstance(ent, dict) and "domain" in ent:
                    dom = ent["domain"]
                    if isinstance(dom, list):
                        target = ", ".join(f"`{d}`" for d in dom)
                    elif isinstance(dom, str):
                        target = f"`{dom}`"
            elif service_name in ("start_sending_instant_power", "stop_sending_instant_power"):
                target = "`sensor`"

            raw_desc = s_data.get("description", "").strip()
            description = " ".join(raw_desc.splitlines()).strip()
            if description and not description.endswith("."):
                description = f"{description}."
        else:
            description = ""

        anchor = f"#myhome{service_name}"
        lines.append(f"| [`myhome.{service_name}`]({anchor}) | {target} | {description} |")

    return "\n".join(lines)


def build_services_table_block() -> str:
    """Return the marker-wrapped services summary table block."""
    services_data = parse_services_yaml()
    table = generate_services_summary_table(services_data)
    return f"{SERVICES_START_MARKER}\n{table}\n{SERVICES_END_MARKER}"


def sync_services(update: bool = False) -> tuple[bool, list[str]]:
    """Check or update services documentation against services.yaml."""
    services_doc = DOCS_DIR / "configuration" / "services.md"
    if not services_doc.exists():
        return False, [f"{services_doc} does not exist"]

    messages: list[str] = []
    all_ok = True
    services_data = parse_services_yaml()
    doc_content = services_doc.read_text(encoding="utf-8")

    # 1. Check all services are documented with a heading
    missing_sections = []
    for service_name in services_data:
        # Expect heading like '## myhome.service_name' or '## 1. `myhome.service_name`'
        pattern = re.compile(rf"##\s+(?:\d+\.\s+)?`?myhome\.{re.escape(service_name)}`?", re.IGNORECASE)
        if not pattern.search(doc_content):
            missing_sections.append(f"myhome.{service_name}")

    if missing_sections:
        messages.append(f"Services missing documentation sections in services.md: {missing_sections}")
        all_ok = False
    else:
        messages.append(f"All {len(services_data)} services have documented sections in services.md")

    # Check for orphaned service headings in services.md that are not in services.yaml
    documented_services = set(re.findall(r"##\s+(?:\d+\.\s+)?`?myhome\.([a-zA-Z0-9_]+)`?", doc_content))
    orphaned_services = sorted(documented_services - set(services_data.keys()))
    if orphaned_services:
        messages.append(f"Documented services not found in services.yaml: {orphaned_services}")
        all_ok = False

    # 2. Check all parameter fields are documented
    missing_fields: list[str] = []
    for service_name, s_data in services_data.items():
        if isinstance(s_data, dict) and "fields" in s_data:
            fields = s_data["fields"]
            if isinstance(fields, dict):
                for field_name in fields:
                    if f"`{field_name}`" not in doc_content and f"| {field_name} |" not in doc_content:
                        missing_fields.append(f"myhome.{service_name} field '{field_name}'")

    if missing_fields:
        messages.append(f"Service fields missing in services.md: {missing_fields}")
        all_ok = False
    else:
        messages.append("All service parameter fields are documented in services.md")

    # 3. Synchronize Summary Table if markers exist
    if SERVICES_START_MARKER in doc_content and SERVICES_END_MARKER in doc_content:
        pattern = re.compile(
            rf"{re.escape(SERVICES_START_MARKER)}.*?{re.escape(SERVICES_END_MARKER)}",
            re.DOTALL,
        )
        expected_block = build_services_table_block().strip()
        match = pattern.search(doc_content)
        if match and match.group(0).strip() != expected_block:
            if update:
                new_content = pattern.sub(expected_block, doc_content)
                services_doc.write_text(new_content, encoding="utf-8")
                messages.append("Updated Services Summary table in docs/configuration/services.md")
            else:
                messages.append("Services Summary table out of sync in docs/configuration/services.md")
                all_ok = False
        else:
            messages.append("Services Summary table in sync: docs/configuration/services.md")

    return all_ok, messages


# ─────────────────────────────────────────────────────────────────────────────
# 5. Repair Issues Synchronization
# ─────────────────────────────────────────────────────────────────────────────

def extract_repair_issues_from_code() -> set[str]:
    """Extract all ISSUE_* constants from custom_components/myhome/repairs.py."""
    if not REPAIRS_PY.exists():
        return set()

    issues: set[str] = set()
    tree = ast.parse(REPAIRS_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith("ISSUE_"):
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        issues.add(node.value.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id.startswith("ISSUE_"):
                if getattr(node, "value", None) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    issues.add(node.value.value)
    return issues


def extract_repair_issues_from_strings() -> set[str]:
    """Extract issues keys from custom_components/myhome/strings.json."""
    if not STRINGS_JSON.exists():
        return set()
    try:
        data = json.loads(STRINGS_JSON.read_text(encoding="utf-8"))
        return set(data.get("issues", {}).keys())
    except Exception:
        return set()


def sync_repair_issues(update: bool = False) -> tuple[bool, list[str]]:
    """Verify docs/diagnostics/repair-issues.md documents all registered repair issues."""
    repair_doc = DOCS_DIR / "diagnostics" / "repair-issues.md"
    if not repair_doc.exists():
        return False, [f"{repair_doc} does not exist"]

    messages: list[str] = []
    all_ok = True

    code_issues = extract_repair_issues_from_code()
    strings_issues = extract_repair_issues_from_strings()
    all_known_issues = code_issues | strings_issues

    doc_content = repair_doc.read_text(encoding="utf-8")
    missing_in_docs: list[str] = []

    for issue_key in sorted(all_known_issues):
        # Look for **Repair Key**: `issue_key` or `Repair Key: `issue_key``
        pattern = re.compile(rf"Repair Key[:\*`\s]+`?{re.escape(issue_key)}`?", re.IGNORECASE)
        if not pattern.search(doc_content):
            missing_in_docs.append(issue_key)

    if missing_in_docs:
        messages.append(
            f"Repair issues defined in code/strings but missing from repair-issues.md: {missing_in_docs}"
        )
        all_ok = False
    else:
        messages.append(
            f"All {len(all_known_issues)} repair issues are documented in docs/diagnostics/repair-issues.md"
        )

    return all_ok, messages


# ─────────────────────────────────────────────────────────────────────────────
# 6. MkDocs Navigation & Markdown Link Health
# ─────────────────────────────────────────────────────────────────────────────

def _load_mkdocs_yaml(mkdocs_path: Path) -> dict[str, Any]:
    """Safely parse mkdocs.yml without importing Material !!python/name: tags."""
    import yaml

    class _MkDocsSafeLoader(yaml.SafeLoader):
        pass

    _MkDocsSafeLoader.add_multi_constructor(
        "tag:yaml.org,2002:python/",
        lambda loader, suffix, node: None,
    )
    loaded = yaml.load(mkdocs_path.read_text(encoding="utf-8"), Loader=_MkDocsSafeLoader)
    return loaded if isinstance(loaded, dict) else {}


def _collect_nav_paths(node: Any) -> list[str]:
    """Recursively collect markdown file paths listed under mkdocs.yml 'nav'."""
    paths: list[str] = []
    if isinstance(node, str):
        if node.endswith(".md"):
            paths.append(node)
    elif isinstance(node, list):
        for item in node:
            paths.extend(_collect_nav_paths(item))
    elif isinstance(node, dict):
        for val in node.values():
            paths.extend(_collect_nav_paths(val))
    return paths


def sync_mkdocs_nav(update: bool = False) -> tuple[bool, list[str]]:
    """Verify mkdocs.yml navigation includes all markdown documentation pages without duplicates or missing targets."""
    if not MKDOCS_YML.exists():
        return False, [f"{MKDOCS_YML} not found"]

    messages: list[str] = []
    all_ok = True
    mkdocs_text = MKDOCS_YML.read_text(encoding="utf-8")

    nav_paths: list[str] = []
    try:
        mkdocs_data = _load_mkdocs_yaml(MKDOCS_YML)
        nav_paths = _collect_nav_paths(mkdocs_data.get("nav", []))
    except Exception as exc:
        messages.append(f"Failed to parse {rel_path(MKDOCS_YML)}: {exc}")
        all_ok = False

    if nav_paths:
        seen_nav: set[str] = set()
        duplicate_nav: list[str] = []
        for p in nav_paths:
            if p in seen_nav and p not in duplicate_nav:
                duplicate_nav.append(p)
            seen_nav.add(p)
        if duplicate_nav:
            messages.append(
                f"Duplicate markdown pages in mkdocs.yml navigation: {duplicate_nav}"
            )
            all_ok = False

        missing_nav_targets = [
            p for p in dict.fromkeys(nav_paths) if not (DOCS_DIR / p).is_file()
        ]
        if missing_nav_targets:
            messages.append(
                f"mkdocs.yml navigation references missing markdown files: {missing_nav_targets}"
            )
            all_ok = False

    # Excluded files not meant for non-root navigation checks
    EXCLUDED_DOCS = {
        "index.md",
    }

    nav_path_set = set(nav_paths)
    unlisted_pages: list[str] = []
    for md_file in sorted(DOCS_DIR.rglob("*.md")):
        md_rel = md_file.relative_to(DOCS_DIR).as_posix()
        if md_rel in EXCLUDED_DOCS:
            continue
        if nav_path_set:
            if md_rel not in nav_path_set:
                unlisted_pages.append(md_rel)
        elif md_rel not in mkdocs_text:
            unlisted_pages.append(md_rel)

    if unlisted_pages:
        messages.append(f"Markdown pages missing from mkdocs.yml navigation: {unlisted_pages}")
        all_ok = False
    elif all_ok:
        messages.append("All documentation pages are referenced once in mkdocs.yml navigation")

    return all_ok, messages


# ─────────────────────────────────────────────────────────────────────────────
# 7. Integration & Dependency Version Synchronization
# ─────────────────────────────────────────────────────────────────────────────

def extract_integration_version_from_const() -> str:
    """Extract INTEGRATION_VERSION from custom_components/myhome/const.py."""
    if not CONST_PY.exists():
        return ""
    tree = ast.parse(CONST_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "INTEGRATION_VERSION":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    return ""


def extract_manifest_version_info() -> tuple[str, str]:
    """Extract (version, ownd_req) from custom_components/myhome/manifest.json."""
    manifest_path = CUSTOM_COMPONENTS_DIR / "manifest.json"
    if not manifest_path.exists():
        return "", ""
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = data.get("version", "")
        reqs = data.get("requirements", [])
        ownd_req = next((r for r in reqs if r.startswith("OWNd==")), "")
        return version, ownd_req
    except Exception:
        return "", ""


def sync_version_references(update: bool = False) -> tuple[bool, list[str]]:
    """Verify and synchronize integration version and OWNd requirements across documentation."""
    messages: list[str] = []
    all_ok = True

    const_version = extract_integration_version_from_const()
    manifest_version, ownd_req = extract_manifest_version_info()

    if not const_version:
        return False, ["Could not read INTEGRATION_VERSION from const.py"]
    if not manifest_version:
        return False, ["Could not read version from manifest.json"]

    if const_version != manifest_version:
        messages.append(
            f"Version mismatch: const.py has '{const_version}' but manifest.json has '{manifest_version}'"
        )
        all_ok = False
    else:
        messages.append(f"Integration version '{const_version}' verified across const.py and manifest.json")

    # 1. Check docs/architecture/anti-drift-safeguards.md
    safeguards_doc = DOCS_DIR / "architecture" / "anti-drift-safeguards.md"
    if safeguards_doc.exists() and ownd_req:
        content = safeguards_doc.read_text(encoding="utf-8")
        if ownd_req not in content:
            if update:
                new_content = re.sub(r"OWNd==[0-9a-zA-Z\.\-_+]+", ownd_req, content)
                safeguards_doc.write_text(new_content, encoding="utf-8")
                messages.append(f"Updated OWNd pin to '{ownd_req}' in {rel_path(safeguards_doc)}")
            else:
                messages.append(
                    f"Outdated OWNd reference in {rel_path(safeguards_doc)} (expected '{ownd_req}')"
                )
                all_ok = False
        else:
            messages.append(f"OWNd requirement '{ownd_req}' in sync: {rel_path(safeguards_doc)}")

    # 2. Check docs/getting-started/installation.md
    install_doc = DOCS_DIR / "getting-started" / "installation.md"
    if install_doc.exists():
        content = install_doc.read_text(encoding="utf-8")
        expected_tag = f'TAG="{const_version}"'
        if expected_tag not in content:
            if update:
                new_content = re.sub(r'TAG=["\'][0-9a-zA-Z\.\-_+]+["\']', expected_tag, content)
                install_doc.write_text(new_content, encoding="utf-8")
                messages.append(f"Updated release tag to '{const_version}' in {rel_path(install_doc)}")
            else:
                messages.append(f"Outdated release tag in {rel_path(install_doc)} (expected '{expected_tag}')")
                all_ok = False
        else:
            messages.append(f"Release tag in sync: {rel_path(install_doc)}")

    # 3. Check docs/migration/upgrade-from-094.md
    upgrade_doc = DOCS_DIR / "migration" / "upgrade-from-094.md"
    if upgrade_doc.exists():
        content = upgrade_doc.read_text(encoding="utf-8")
        expected_tag = f'TAG="{const_version}"'
        if expected_tag not in content:
            if update:
                new_content = re.sub(r'TAG=["\'][0-9a-zA-Z\.\-_+]+["\']', expected_tag, content)
                upgrade_doc.write_text(new_content, encoding="utf-8")
                messages.append(f"Updated release tag to '{const_version}' in {rel_path(upgrade_doc)}")
            else:
                messages.append(f"Outdated release tag in {rel_path(upgrade_doc)} (expected '{expected_tag}')")
                all_ok = False
        else:
            messages.append(f"Release tag in sync: {rel_path(upgrade_doc)}")

    # 4. Check OWNd== pins in ROADMAP.md and docs/roadmap.md
    if ownd_req:
        for roadmap_doc in (ROADMAP_MD, DOCS_DIR / "roadmap.md"):
            if not roadmap_doc.exists():
                continue
            rm_content = roadmap_doc.read_text(encoding="utf-8")
            found_pins = set(re.findall(r"OWNd==[0-9a-zA-Z\.\-_+]+", rm_content))
            if not found_pins:
                continue
            if found_pins != {ownd_req}:
                if update:
                    new_rm = re.sub(r"OWNd==[0-9a-zA-Z\.\-_+]+", ownd_req, rm_content)
                    roadmap_doc.write_text(new_rm, encoding="utf-8")
                    messages.append(f"Updated OWNd pin to '{ownd_req}' in {rel_path(roadmap_doc)}")
                else:
                    messages.append(
                        f"Outdated OWNd reference in {rel_path(roadmap_doc)} "
                        f"(got {sorted(found_pins)}, expected '{ownd_req}')"
                    )
                    all_ok = False
            else:
                messages.append(f"OWNd requirement '{ownd_req}' in sync: {rel_path(roadmap_doc)}")

    return all_ok, messages


# ─────────────────────────────────────────────────────────────────────────────
# 8. Link & Anchor Health
# ─────────────────────────────────────────────────────────────────────────────

def check_markdown_link_health() -> tuple[bool, list[str]]:
    """Check for malformed anchor links, empty targets, and broken relative links across docs/ and docs-v0.9/."""
    messages: list[str] = []
    all_ok = True

    # Pattern for anchor links with emoji-stripped leading hyphen like '#-'
    bad_anchor_pattern = re.compile(r"\[([^\]]+)\]\((\S*?#-[\w-]+)\)")
    # Pattern for markdown links
    link_pattern = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")

    doc_roots = [d for d in (DOCS_DIR, DOCS_V09_DIR) if d.is_dir()]
    for root_dir in doc_roots:
        for md_file in sorted(root_dir.rglob("*.md")):
            raw_content = md_file.read_text(encoding="utf-8")
            content = re.sub(r"```.*?```", "", raw_content, flags=re.DOTALL)

            # 1. Emoji-stripped leading hyphen
            matches = bad_anchor_pattern.findall(content)
            if matches:
                for _text, link in matches:
                    messages.append(
                        f"Malformed anchor '{link}' (stripped emoji leading hyphen) in {rel_path(md_file)}"
                    )
                    all_ok = False

            # 2. Empty links and broken local file links
            for text, url in link_pattern.findall(content):
                url_clean = url.strip()
                if not url_clean or url_clean == "#":
                    messages.append(f"Empty link target in {rel_path(md_file)}: [{text}]({url})")
                    all_ok = False
                    continue

                if (
                    url_clean.startswith("http://")
                    or url_clean.startswith("https://")
                    or url_clean.startswith("mailto:")
                ):
                    continue

                # Check local file existence if link has a path component
                target_path_str = url_clean.split("#")[0].strip()
                if target_path_str:
                    resolved = (md_file.parent / target_path_str).resolve()
                    if not resolved.exists():
                        messages.append(
                            f"Broken relative file link in {rel_path(md_file)}: [{text}]({url_clean})"
                        )
                        all_ok = False

    if all_ok:
        messages.append("All markdown links and anchors validated cleanly (0 broken links).")

    return all_ok, messages


# ─────────────────────────────────────────────────────────────────────────────
# 9. Starting README, License & Versioned Site Standards
# ─────────────────────────────────────────────────────────────────────────────

def _github_heading_slugs(markdown_text: str) -> set[str]:
    """Compute GitHub-flavored Markdown anchor slugs for all headings outside code fences."""
    stripped = re.sub(r"```.*?```", "", markdown_text, flags=re.DOTALL)
    slug_counts: dict[str, int] = {}
    slugs: set[str] = set()
    for line in stripped.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if not match:
            continue
        raw_heading = match.group(1)
        # Unwrap inline markdown links [text](url) -> text and strip formatting characters
        raw_heading = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", raw_heading)
        raw_heading = raw_heading.replace("`", "")
        slug_base = re.sub(r"[^\w\s-]", "", raw_heading.lower()).strip()
        slug_base = slug_base.replace(" ", "-")
        if not slug_base:
            continue
        count = slug_counts.get(slug_base, 0)
        slug_counts[slug_base] = count + 1
        slug = slug_base if count == 0 else f"{slug_base}-{count}"
        slugs.add(slug)
    return slugs


def _validate_canonical_site_urls(text: str, source_label: str) -> list[str]:
    """Validate all canonical MyHOME GitHub Pages URLs against docs/ and docs-v0.9/ targets and anchors."""
    errs: list[str] = []
    no_fences = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    for match in re.finditer(r"https://openwebnet-ha\.github\.io/MyHOME/([^\s)\"'\]>]*)", no_fences):
        full_url = match.group(0).rstrip(".,;:")
        sub = full_url[len(CANONICAL_DOCS_URL) :]
        if not sub or sub.startswith("beta"):
            continue
        if sub.startswith("latest/"):
            target_root = DOCS_DIR
            rel_url = sub[len("latest/") :]
        elif sub.startswith("0.9.4/") or sub.startswith("legacy/"):
            target_root = DOCS_V09_DIR
            rel_url = sub.split("/", 1)[1]
        else:
            errs.append(f"{source_label} uses unversioned or unsupported manual URL '{full_url}'")
            continue

        if not target_root.is_dir():
            continue

        path_part, _, frag = rel_url.partition("#")
        path_part = path_part.strip("/")
        candidates = (
            [target_root / "index.md"]
            if not path_part
            else [
                target_root / f"{path_part}.md",
                target_root / path_part / "index.md",
                target_root / path_part / "README.md",
            ]
        )
        md_target = next((c for c in candidates if c.is_file()), None)
        if md_target is None:
            errs.append(f"{source_label} links to missing manual page '{full_url}'")
            continue

        if frag:
            target_text = md_target.read_text(encoding="utf-8")
            gh_slugs = _github_heading_slugs(target_text)
            mkdocs_slugs = {re.sub(r"-+", "-", s) for s in gh_slugs}
            explicit_ids = set(re.findall(r"\{\s*#([\w-]+)\s*\}", target_text))
            if frag not in (gh_slugs | mkdocs_slugs | explicit_ids):
                errs.append(
                    f"{source_label} links to missing anchor '#{frag}' in {rel_path(md_target)} ('{full_url}')"
                )
    return errs


def check_readme_and_site_standards() -> tuple[bool, list[str]]:
    """Verify starting README, Apache-2.0 license consistency, manual URLs, and non-beta Pages config."""
    messages: list[str] = []
    all_ok = True

    # 1. Starting README.md standards
    if not README_MD.exists():
        messages.append(f"Starting README not found: {rel_path(README_MD)}")
        all_ok = False
    else:
        readme_text = README_MD.read_text(encoding="utf-8")

        if CANONICAL_DOCS_URL not in readme_text:
            messages.append(
                f"README.md must link to the canonical manual URL '{CANONICAL_DOCS_URL}'"
            )
            all_ok = False

        if "openwebnet-ha.github.io/MyHOME/beta" in readme_text:
            messages.append(
                "README.md still links to '/MyHOME/beta' instead of the canonical manual URL"
            )
            all_ok = False

        if _LEGACY_WIKI_RE.search(readme_text):
            messages.append(
                "README.md still links to deprecated GitHub '/wiki' instead of the canonical manual URL"
            )
            all_ok = False

        if "AGPL" in readme_text:
            messages.append("README.md contains outdated AGPL license reference (expected Apache-2.0)")
            all_ok = False

        if not (
            ("Apache-2.0" in readme_text or "Apache License 2.0" in readme_text)
            and "(LICENSE)" in readme_text
        ):
            messages.append(
                "README.md must reference Apache-2.0 / Apache License 2.0 and link to (LICENSE)"
            )
            all_ok = False

        legacy_patterns = [
            (r"\b0\.9\.4\b", "legacy v0.9.4 version comparison/rollback"),
            (r"\bv0\.9\b", "legacy v0.9 version comparison"),
            (r"\b0\.8\.\d+\b", "legacy v0.8.x version comparison"),
            (r"Safe Rollback", "legacy rollback section"),
            (r"Older cores stay on", "legacy beta core pinning comparison"),
        ]
        for pat, desc in legacy_patterns:
            if re.search(pat, readme_text, flags=re.IGNORECASE):
                messages.append(f"README.md contains prohibited {desc} ('{pat}')")
                all_ok = False

        # Validate README.md internal anchors, markdown links, and HTML image paths
        slugs = _github_heading_slugs(readme_text)
        readme_no_fences = re.sub(r"```.*?```", "", readme_text, flags=re.DOTALL)

        for text, url in re.findall(r"\[([^\]]*)\]\(([^)]*)\)", readme_no_fences):
            url_clean = url.strip()
            if not url_clean or url_clean == "#":
                messages.append(f"Empty link target in README.md: [{text}]({url})")
                all_ok = False
                continue
            if "\ufe0f" in url_clean or re.search(r"#-[\w-]+$", url_clean):
                messages.append(f"Malformed heading anchor in README.md: [{text}]({url_clean})")
                all_ok = False
                continue
            if url_clean.startswith("#"):
                anchor = url_clean[1:]
                if anchor not in slugs:
                    messages.append(
                        f"Unresolved internal heading anchor in README.md: [{text}]({url_clean})"
                    )
                    all_ok = False
                continue
            if url_clean.startswith(("http://", "https://", "mailto:")):
                continue
            path_part = url_clean.split("#")[0].strip()
            if path_part and not (README_MD.parent / path_part).resolve().exists():
                messages.append(
                    f"Broken relative file link in README.md: [{text}]({url_clean})"
                )
                all_ok = False

        for img_src in re.findall(r'<img\s+[^>]*src=["\']([^"\']+)["\']', readme_no_fences):
            src_clean = img_src.strip()
            if src_clean.startswith(("http://", "https://", "data:")):
                continue
            if not (README_MD.parent / src_clean).resolve().exists():
                messages.append(f"Broken HTML image src in README.md: {src_clean}")
                all_ok = False

        readme_site_errs = _validate_canonical_site_urls(readme_text, "README.md")
        if readme_site_errs:
            messages.extend(readme_site_errs)
            all_ok = False

    # 2. MkDocs configuration license & beta-page checks
    for mkdocs_file, check_beta_diff in ((MKDOCS_YML, True), (MKDOCS_V09_YML, False)):
        if not mkdocs_file.exists():
            continue
        mkdocs_label = mkdocs_file.name
        mkdocs_raw = mkdocs_file.read_text(encoding="utf-8")
        if "AGPL" in mkdocs_raw:
            messages.append(
                f"{mkdocs_label} contains outdated AGPL license reference (expected Apache-2.0)"
            )
            all_ok = False
        if check_beta_diff:
            for removed_page in ("beta-differences.md", "f454-regression-checks.md"):
                if removed_page in mkdocs_raw:
                    messages.append(f"{mkdocs_label} still references removed {removed_page} page")
                    all_ok = False
        try:
            mkdocs_cfg = _load_mkdocs_yaml(mkdocs_file)
            copyright_str = str(mkdocs_cfg.get("copyright", ""))
            if "Apache-2.0" not in copyright_str and "Apache License 2.0" not in copyright_str:
                messages.append(f"{mkdocs_label} copyright notice must specify Apache-2.0")
                all_ok = False
        except Exception as exc:
            messages.append(f"Failed to parse {mkdocs_label} in license check: {exc}")
            all_ok = False

    # 3. Manifest & repository metadata documentation URLs
    if MANIFEST_JSON.exists():
        try:
            manifest_data = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
            doc_url = manifest_data.get("documentation", "")
            if doc_url != CANONICAL_DOCS_URL:
                messages.append(
                    f"manifest.json 'documentation' must be '{CANONICAL_DOCS_URL}' (got '{doc_url}')"
                )
                all_ok = False
        except Exception as exc:
            messages.append(f"Failed to parse manifest.json: {exc}")
            all_ok = False

    if PYPROJECT_TOML.exists():
        pyproject_text = PYPROJECT_TOML.read_text(encoding="utf-8")
        if "openwebnet-ha.github.io/MyHOME/beta" in pyproject_text:
            messages.append("pyproject.toml still points Documentation URL to '/MyHOME/beta'")
            all_ok = False
        if _LEGACY_WIKI_RE.search(pyproject_text):
            messages.append("pyproject.toml still points URL to deprecated GitHub '/wiki'")
            all_ok = False
        if "AGPL" in pyproject_text:
            messages.append("pyproject.toml contains outdated AGPL license reference")
            all_ok = False

    if ISSUE_TEMPLATE_CONFIG.exists():
        issue_cfg_text = ISSUE_TEMPLATE_CONFIG.read_text(encoding="utf-8")
        if "openwebnet-ha.github.io/MyHOME/beta" in issue_cfg_text:
            messages.append(
                ".github/ISSUE_TEMPLATE/config.yml still points User Manual URL to '/MyHOME/beta'"
            )
            all_ok = False
        if _LEGACY_WIKI_RE.search(issue_cfg_text):
            messages.append(
                ".github/ISSUE_TEMPLATE/config.yml still points URL to deprecated GitHub '/wiki'"
            )
            all_ok = False

    # 3b. Integration modules & docs must not link to '/MyHOME/beta' or legacy GitHub Wiki,
    #     and all canonical '/MyHOME/latest/...' & '/MyHOME/0.9.4/...' URLs must resolve.
    if CUSTOM_COMPONENTS_DIR.is_dir():
        for code_file in sorted(CUSTOM_COMPONENTS_DIR.rglob("*")):
            if not code_file.is_file() or code_file.suffix not in {".py", ".json", ".yaml", ".yml"}:
                continue
            code_text = code_file.read_text(encoding="utf-8")
            code_label = rel_path(code_file)
            if "openwebnet-ha.github.io/MyHOME/beta" in code_text:
                messages.append(f"{code_label} still points documentation URL to '/MyHOME/beta'")
                all_ok = False
            if _LEGACY_WIKI_RE.search(code_text):
                messages.append(
                    f"{code_label} still points documentation URL to legacy GitHub '/wiki'"
                )
                all_ok = False
            site_errs = _validate_canonical_site_urls(code_text, code_label)
            if site_errs:
                messages.extend(site_errs)
                all_ok = False

    for doc_tree in (DOCS_DIR, DOCS_V09_DIR):
        if not doc_tree.is_dir():
            continue
        for md_file in sorted(doc_tree.rglob("*.md")):
            md_text = md_file.read_text(encoding="utf-8")
            md_label = rel_path(md_file)
            if "openwebnet-ha.github.io/MyHOME/beta" in md_text:
                messages.append(f"{md_label} still links to '/MyHOME/beta'")
                all_ok = False
            if _LEGACY_WIKI_RE.search(md_text):
                messages.append(
                    f"{md_label} still links to legacy GitHub '/wiki' instead of manual pages"
                )
                all_ok = False
            site_errs = _validate_canonical_site_urls(md_text, md_label)
            if site_errs:
                messages.extend(site_errs)
                all_ok = False

    # 3c. Roadmap standards (no deprecated WHO 22 / shutterRun=AUTO claims, no broken links/beta/wiki URLs)
    stale_roadmap_phrases = (
        ("Formally Deprecated", "false WHO 22 deprecation status"),
        ("obsolete Legrand analog FM", "false WHO 22 obsolete analog FM claim"),
        ("shutterRun=AUTO", "non-OpenWebNet openHAB shutterRun=AUTO claim"),
        ("station preset slot", "false WHO 22 preset-slot number entity claim"),
        ("preset save)", "false WHO 22 preset-save button entity claim"),
    )
    for rm_path in (ROADMAP_MD, DOCS_DIR / "roadmap.md"):
        if not rm_path.exists():
            continue
        rm_text = rm_path.read_text(encoding="utf-8")
        rm_label = rel_path(rm_path)
        for phrase, desc in stale_roadmap_phrases:
            if phrase in rm_text:
                messages.append(f"{rm_label} contains prohibited {desc} ('{phrase}')")
                all_ok = False
        if rm_path == ROADMAP_MD:
            if "openwebnet-ha.github.io/MyHOME/beta" in rm_text:
                messages.append("ROADMAP.md still links to '/MyHOME/beta'")
                all_ok = False
            if _LEGACY_WIKI_RE.search(rm_text):
                messages.append("ROADMAP.md still links to deprecated GitHub '/wiki'")
                all_ok = False
            rm_slugs = _github_heading_slugs(rm_text)
            rm_no_fences = re.sub(r"```.*?```", "", rm_text, flags=re.DOTALL)
            for text, url in re.findall(r"\[([^\]]*)\]\(([^)]*)\)", rm_no_fences):
                url_clean = url.strip()
                if not url_clean or url_clean == "#":
                    messages.append(f"Empty link target in ROADMAP.md: [{text}]({url})")
                    all_ok = False
                    continue
                if url_clean.startswith("#"):
                    if url_clean[1:] not in rm_slugs:
                        messages.append(
                            f"Unresolved internal heading anchor in ROADMAP.md: [{text}]({url_clean})"
                        )
                        all_ok = False
                    continue
                if url_clean.startswith(("http://", "https://", "mailto:")):
                    continue
                path_part = url_clean.split("#")[0].strip()
                if path_part and not (ROADMAP_MD.parent / path_part).resolve().exists():
                    messages.append(
                        f"Broken relative file link in ROADMAP.md: [{text}]({url_clean})"
                    )
                    all_ok = False
            rm_site_errs = _validate_canonical_site_urls(rm_text, "ROADMAP.md")
            if rm_site_errs:
                messages.extend(rm_site_errs)
                all_ok = False

    # 4. Docs workflow invariants (no v0.9.4 -> latest hijack, pre-release pruning, master /latest/ freshness)
    if DOCS_WORKFLOW.exists():
        wf_text = DOCS_WORKFLOW.read_text(encoding="utf-8")
        for line in wf_text.splitlines():
            if "mkdocs-v0.9.yml" in line and "latest" in line and not line.lstrip().startswith("#"):
                messages.append(
                    ".github/workflows/docs.yml aliases legacy mkdocs-v0.9.yml (0.9.4) to 'latest'"
                )
                all_ok = False
        if "mike delete" not in wf_text or "(a|b|rc" not in wf_text:
            messages.append(
                ".github/workflows/docs.yml must prune pre-release (a/b/rc) snapshots from GitHub Pages"
            )
            all_ok = False
        if "STALE_LEGACY_ALIASES" not in wf_text:
            messages.append(
                ".github/workflows/docs.yml must strip stale non-legacy aliases (such as 'latest') from 0.9.4 before redeploying"
            )
            all_ok = False
        if '"${REF_NAME}" = "master"' not in wf_text or "MANIFEST_STABLE_MINOR" not in wf_text:
            messages.append(
                ".github/workflows/docs.yml must keep '/latest/' updated on pushes to master when manifest is on a stable 2.x line"
            )
            all_ok = False
        if "IS_LATEST_MINOR" not in wf_text:
            messages.append(
                ".github/workflows/docs.yml must guard older-minor patch releases from overwriting 'latest'"
            )
            all_ok = False
        if "refs/heads/gh-pages:refs/remotes/origin/gh-pages" not in wf_text:
            messages.append(
                ".github/workflows/docs.yml must fetch origin/gh-pages explicitly before running mike"
            )
            all_ok = False

    if all_ok:
        messages.append(
            "Starting README, Apache-2.0 license metadata, and versioned manual URLs verified cleanly."
        )

    return all_ok, messages


# ─────────────────────────────────────────────────────────────────────────────
# Master Routine & CLI Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def check_all_documentation(update: bool = False) -> tuple[bool, list[str]]:
    """Run all documentation synchronization checks or updates.

    Returns (is_all_in_sync, list_of_report_messages).
    """
    overall_ok = True
    all_messages: list[str] = []

    # 1. Gateway profiles
    ok_gw, msgs_gw = sync_gateway_profiles(update=update)
    overall_ok = overall_ok and ok_gw
    all_messages.extend(msgs_gw)

    # 1b. Gateway options
    ok_opt, msgs_opt = sync_gateway_options(update=update)
    overall_ok = overall_ok and ok_opt
    all_messages.extend(msgs_opt)

    # 1c. Gateway startup discovery capabilities
    ok_disc, msgs_disc = sync_gateway_discovery(update=update)
    overall_ok = overall_ok and ok_disc
    all_messages.extend(msgs_disc)

    # 2. Supported domains
    ok_dom, msgs_dom = sync_supported_domains(update=update)
    overall_ok = overall_ok and ok_dom
    all_messages.extend(msgs_dom)

    # 3. Trace matrix
    ok_trace, msgs_trace = sync_trace_matrix(update=update)
    overall_ok = overall_ok and ok_trace
    all_messages.extend(msgs_trace)

    # 3b. Dynamic roadmap gateway & subsystem tables
    ok_rm, msgs_rm = sync_roadmap_tables(update=update)
    overall_ok = overall_ok and ok_rm
    all_messages.extend(msgs_rm)

    # 4. Services
    ok_srv, msgs_srv = sync_services(update=update)
    overall_ok = overall_ok and ok_srv
    all_messages.extend(msgs_srv)

    # 5. Repairs
    ok_rep, msgs_rep = sync_repair_issues(update=update)
    overall_ok = overall_ok and ok_rep
    all_messages.extend(msgs_rep)

    # 6. Version references across docs
    ok_ver, msgs_ver = sync_version_references(update=update)
    overall_ok = overall_ok and ok_ver
    all_messages.extend(msgs_ver)

    # 7. MkDocs nav
    ok_nav, msgs_nav = sync_mkdocs_nav(update=update)
    overall_ok = overall_ok and ok_nav
    all_messages.extend(msgs_nav)

    # 8. Link & anchor health
    ok_anchors, msgs_anchors = check_markdown_link_health()
    overall_ok = overall_ok and ok_anchors
    all_messages.extend(msgs_anchors)

    # 9. Starting README, license & site standards
    ok_std, msgs_std = check_readme_and_site_standards()
    overall_ok = overall_ok and ok_std
    all_messages.extend(msgs_std)

    return overall_ok, all_messages


def main(argv: list[str] | None = None) -> int:
    """CLI interface for documentation synchronization and anti-drift validation."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Validate or synchronize documentation against the MyHOME codebase."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check whether documentation is in sync without modifying files (exit 1 on drift)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update all out-of-sync documentation tables and marker blocks in place",
    )

    args = parser.parse_args(argv)

    # Default mode is --check if neither --check nor --update is given
    do_update = args.update and not args.check

    print("=" * 70)
    print("Running Documentation Anti-Drift Sentinel & Synchronizer")
    print("Mode:", "UPDATE" if do_update else "CHECK")
    print("=" * 70)

    in_sync, messages = check_all_documentation(update=do_update)

    for msg in messages:
        print(f"  • {msg}")

    print("=" * 70)
    if in_sync:
        print("SUCCESS: All documentation is calibrated and in sync with the codebase!\n")
        return 0
    else:
        if do_update:
            print("WARNING: Some documentation files required manual review or were updated. (Returning exit 1 as required)\n")
            return 1
        else:
            print("FAILED: Documentation drift detected! Run 'python scripts/sync_documentation.py --update' to sync.\n")
            return 1


if __name__ == "__main__":
    sys.exit(main())
