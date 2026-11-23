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
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CUSTOM_COMPONENTS_DIR = REPO_ROOT / "custom_components" / "myhome"
CONST_PY = CUSTOM_COMPONENTS_DIR / "const.py"
MANIFEST_JSON = CUSTOM_COMPONENTS_DIR / "manifest.json"
SERVICES_YAML = CUSTOM_COMPONENTS_DIR / "services.yaml"
STRINGS_JSON = CUSTOM_COMPONENTS_DIR / "strings.json"
REPAIRS_PY = CUSTOM_COMPONENTS_DIR / "repairs.py"
WEBSOCKET_PY = CUSTOM_COMPONENTS_DIR / "websocket.py"
DEVICE_TRIGGER_PY = CUSTOM_COMPONENTS_DIR / "device_trigger.py"
GATEWAY_PY = CUSTOM_COMPONENTS_DIR / "gateway.py"
BUS_CARD_JS = CUSTOM_COMPONENTS_DIR / "frontend" / "myhome-bus-card.js"
BUS_VIEW_JS = CUSTOM_COMPONENTS_DIR / "frontend" / "panel" / "panel-bus-monitor-view.js"
BUS_TRANSLATIONS_JS = CUSTOM_COMPONENTS_DIR / "frontend" / "panel" / "panel-bus-translations.js"
UPDATE_TRACE_MATRIX_PY = REPO_ROOT / "scripts" / "update_trace_matrix.py"
BLUEPRINTS_DIR = REPO_ROOT / "blueprints" / "automation" / "myhome"
MKDOCS_YML = REPO_ROOT / "mkdocs.yml"
MKDOCS_V09_YML = REPO_ROOT / "mkdocs-v0.9.yml"
README_MD = REPO_ROOT / "README.md"
ROADMAP_MD = REPO_ROOT / "ROADMAP.md"
CONTRIBUTING_MD = REPO_ROOT / "CONTRIBUTING.md"
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
    DOCS_DIR / "openwebnet-who-specifications.md",
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


def generate_services_summary_table(
    services_data: dict[str, Any], doc_content: str | None = None
) -> str:
    """Generate the markdown table for services summary.

    When ``doc_content`` contains numbered headings such as
    ``## 1. `myhome.send_message```, preserves heading order and generates
    matching ``#1-myhomesend_message`` anchors.
    """
    lines = [
        "| Service | Target | Description |",
        "| :--- | :--- | :--- |",
    ]

    heading_numbers: dict[str, int] = {}
    if doc_content:
        for match in re.finditer(
            r"^##\s+(?:(\d+)\.\s+)?`?myhome\.([a-zA-Z0-9_]+)`?",
            doc_content,
            re.MULTILINE,
        ):
            num_str, svc = match.group(1), match.group(2)
            if num_str and svc in services_data and svc not in heading_numbers:
                heading_numbers[svc] = int(num_str)

    if heading_numbers and set(heading_numbers) == set(services_data):
        ordered_items = sorted(
            services_data.items(), key=lambda kv: (heading_numbers[kv[0]], kv[0])
        )
    else:
        ordered_items = sorted(services_data.items())

    for service_name, s_data in ordered_items:
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

        if service_name in heading_numbers:
            anchor = f"#{heading_numbers[service_name]}-myhome{service_name}"
        else:
            anchor = f"#myhome{service_name}"
        lines.append(f"| [`myhome.{service_name}`]({anchor}) | {target} | {description} |")

    return "\n".join(lines)


def build_services_table_block(doc_content: str | None = None) -> str:
    """Return the marker-wrapped services summary table block."""
    services_data = parse_services_yaml()
    table = generate_services_summary_table(services_data, doc_content=doc_content)
    return f"{SERVICES_START_MARKER}\n{table}\n{SERVICES_END_MARKER}"


def _extract_service_sections(doc_content: str) -> dict[str, str]:
    """Slice ``services.md`` into ``{service_name: section_body}`` by ``## ... myhome.<svc>`` headings."""
    matches = list(
        re.finditer(
            r"^##\s+(?:\d+\.\s+)?`?myhome\.([a-zA-Z0-9_]+)`?[^\n]*$",
            doc_content,
            re.MULTILINE,
        )
    )
    sections: dict[str, str] = {}
    for idx, m in enumerate(matches):
        svc = m.group(1)
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(doc_content)
        sections[svc] = doc_content[start:end]
    return sections


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

    # 2. Check all parameter fields are documented in their respective service section
    service_sections = _extract_service_sections(doc_content)
    missing_fields: list[str] = []
    for service_name, s_data in services_data.items():
        if isinstance(s_data, dict) and "fields" in s_data:
            fields = s_data["fields"]
            if isinstance(fields, dict):
                section_body = service_sections.get(service_name, "")
                for field_name in fields:
                    if f"`{field_name}`" not in section_body and f"| {field_name} |" not in section_body:
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
        expected_block = build_services_table_block(doc_content=doc_content).strip()
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
    manifest_path = MANIFEST_JSON
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
        found_pins = set(re.findall(r"OWNd==[0-9a-zA-Z\.\-_+]+", content))
        if not found_pins or found_pins != {ownd_req}:
            if update and found_pins:
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

                # Check local file existence and #fragment anchor health
                target_path_str, _, frag_str = url_clean.partition("#")
                target_path_str = target_path_str.strip()
                frag_str = frag_str.strip()
                resolved_md: Path | None = md_file
                if target_path_str:
                    resolved = (md_file.parent / target_path_str).resolve()
                    if not resolved.exists():
                        messages.append(
                            f"Broken relative file link in {rel_path(md_file)}: [{text}]({url_clean})"
                        )
                        all_ok = False
                        resolved_md = None
                    elif resolved.is_file() and resolved.suffix.lower() == ".md":
                        resolved_md = resolved
                    else:
                        resolved_md = None

                if frag_str and resolved_md is not None and root_dir == DOCS_DIR:
                    target_text = resolved_md.read_text(encoding="utf-8")
                    gh_slugs = _github_heading_slugs(target_text)
                    mkdocs_slugs = {re.sub(r"-+", "-", s).strip("-") for s in gh_slugs}
                    explicit_ids = set(re.findall(r"\{\s*#([\w-]+)\s*\}", target_text))
                    if frag_str not in (gh_slugs | mkdocs_slugs | explicit_ids):
                        messages.append(
                            f"Broken heading anchor '#{frag_str}' in {rel_path(md_file)}: [{text}]({url_clean})"
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
        ("Venetian Dim 10 Tilt", "false WHO 2 Dimension 10 wire tilt-angle claim"),
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
# 10. Canonical WHO Labels Parity (Gate 1)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_who_labels_ast(trace_script: Path) -> dict[int, str]:
    """Extract WHO_LABELS dict from scripts/update_trace_matrix.py via AST."""
    tree = ast.parse(trace_script.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        dict_node: ast.expr | None = None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "WHO_LABELS":
                    dict_node = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "WHO_LABELS":
                dict_node = node.value
        if isinstance(dict_node, ast.Dict):
            out: dict[int, str] = {}
            for k, v in zip(dict_node.keys, dict_node.values):
                if (
                    isinstance(k, ast.Constant)
                    and isinstance(k.value, int)
                    and not isinstance(k.value, bool)
                    and isinstance(v, ast.Constant)
                    and isinstance(v.value, str)
                ):
                    out[k.value] = v.value
            return out
    return {}


def _extract_bus_card_who_catalog(bus_card_js: Path) -> dict[int, tuple[str, str]]:
    """Extract {who_int: (name, short)} from the monitor implementation."""
    text = bus_card_js.read_text(encoding="utf-8")
    match = re.search(r"const\s+WHO_CATALOG\s*=\s*\{(.*?)\n\};", text, re.DOTALL)
    if not match:
        return {}
    catalog: dict[int, tuple[str, str]] = {}
    for entry in re.finditer(
        r'"(\d+)"\s*:\s*\{\s*name:\s*"([^"]+)"\s*,\s*short:\s*"([^"]+)"',
        match.group(1),
    ):
        catalog[int(entry.group(1))] = (entry.group(2), entry.group(3))
    return catalog


def _who_labels_semantically_aligned(short_label: str, catalog_name: str, catalog_short: str) -> bool:
    """Return True if trace-matrix WHO_LABELS entry semantically aligns with WHO_CATALOG."""
    lbl = short_label.lower().strip()
    cat = f"{catalog_name} {catalog_short}".lower()
    synonym_groups = (
        ("climate", "heating", "thermoregulation"),
        ("audio", "sound"),
        ("scenario", "mh200n"),
        ("lights", "light", "lighting"),
        ("autom.", "automation"),
        ("aux", "auxiliary"),
    )
    for group in synonym_groups:
        if any(g in lbl for g in group) and any(g in cat for g in group):
            return True
    tokens = [t for t in re.split(r"[^a-z0-9+]+", lbl) if len(t) >= 3]
    for tok in tokens:
        prefix = tok[:4] if len(tok) >= 4 else tok
        if prefix in cat:
            return True
    return False


def check_who_labels_parity() -> tuple[bool, list[str]]:
    """Verify WHO_LABELS in update_trace_matrix.py aligns with panel-bus-monitor-view.js WHO_CATALOG."""
    messages: list[str] = []
    if not UPDATE_TRACE_MATRIX_PY.exists():
        return False, [f"Trace matrix script missing: {rel_path(UPDATE_TRACE_MATRIX_PY)}"]
    if not BUS_VIEW_JS.exists():
        return False, [f"Bus monitor view JS missing: {rel_path(BUS_VIEW_JS)}"]

    who_labels = _extract_who_labels_ast(UPDATE_TRACE_MATRIX_PY)
    who_catalog = _extract_bus_card_who_catalog(BUS_VIEW_JS)
    if not who_labels or not who_catalog:
        return False, ["Could not parse WHO_LABELS or WHO_CATALOG for parity check"]

    all_ok = True
    if who_labels.get(9, "").strip().lower() == "power" or not who_labels.get(9, "").lower().startswith("aux"):
        messages.append(
            f"WHO 9 label mismatch in update_trace_matrix.py: got {who_labels.get(9)!r}, expected 'Aux' (Auxiliary)"
        )
        all_ok = False
    if who_labels.get(25, "").strip().lower() == "diag" or "cen+" not in who_labels.get(25, "").lower():
        messages.append(
            f"WHO 25 label mismatch in update_trace_matrix.py: got {who_labels.get(25)!r}, expected 'CEN+'"
        )
        all_ok = False

    for who_code, label in sorted(who_labels.items()):
        if who_code < 1000 and "diag" in label.lower():
            messages.append(
                f"Non-diagnostic WHO {who_code} in update_trace_matrix.py must not be labeled {label!r}"
            )
            all_ok = False
        if who_code >= 1000 and "diag" not in label.lower():
            messages.append(
                f"Diagnostic WHO {who_code} in update_trace_matrix.py must carry a 'Diag' label (got {label!r})"
            )
            all_ok = False
        if who_code in who_catalog:
            cat_name, cat_short = who_catalog[who_code]
            if not _who_labels_semantically_aligned(label, cat_name, cat_short):
                messages.append(
                    f"WHO {who_code} label drift: update_trace_matrix.py has {label!r} vs "
                    f"panel-bus-monitor-view.js WHO_CATALOG {cat_name!r} ({cat_short!r})"
                )
                all_ok = False

    if all_ok:
        messages.append(
            f"Canonical WHO labels verified across update_trace_matrix.py ({len(who_labels)}) "
            f"and panel-bus-monitor-view.js WHO_CATALOG ({len(who_catalog)})."
        )
    return all_ok, messages


# ─────────────────────────────────────────────────────────────────────────────
# 11. Auto-Discovery, Startup Sweep & Subsystem Matrix Sentinel (Gate 4)
# ─────────────────────────────────────────────────────────────────────────────

_HA_ENTITY_PLATFORM_NAMES = frozenset(
    {
        "light",
        "switch",
        "cover",
        "climate",
        "binary_sensor",
        "sensor",
        "media_player",
        "button",
        "number",
        "alarm_control_panel",
        "lock",
        "camera",
    }
)


def _is_ctx_source_cmp(expr: ast.expr, op_type: type[ast.cmpop], val: str) -> bool:
    """Return True if expr is ``ctx.source == val`` (or ``!=`` depending on op_type)."""
    return (
        isinstance(expr, ast.Compare)
        and len(expr.ops) == 1
        and isinstance(expr.ops[0], op_type)
        and len(expr.comparators) == 1
        and isinstance(expr.comparators[0], ast.Constant)
        and expr.comparators[0].value == val
        and isinstance(expr.left, ast.Attribute)
        and expr.left.attr == "source"
    )


def _func_unconditionally_rejects_bus(fn_node: ast.FunctionDef) -> bool:
    """Return True if an accept(ctx) function unconditionally returns False when ctx.source == 'bus'."""
    for stmt in fn_node.body:
        if (
            isinstance(stmt, ast.If)
            and len(stmt.body) == 1
            and isinstance(stmt.body[0], ast.Return)
            and isinstance(stmt.body[0].value, ast.Constant)
            and stmt.body[0].value.value is False
        ):
            if _is_ctx_source_cmp(stmt.test, ast.Eq, "bus"):
                return True
            if isinstance(stmt.test, ast.BoolOp) and isinstance(stmt.test.op, ast.Or):
                if any(_is_ctx_source_cmp(v, ast.Eq, "bus") for v in stmt.test.values):
                    return True
        elif isinstance(stmt, ast.Return) and stmt.value is not None:
            if _is_ctx_source_cmp(stmt.value, ast.NotEq, "bus"):
                return True
            if isinstance(stmt.value, ast.BoolOp) and isinstance(stmt.value.op, ast.And):
                if any(_is_ctx_source_cmp(v, ast.NotEq, "bus") for v in stmt.value.values):
                    return True
    return False


def _extract_platform_discovery_contracts(
    comp_dir: Path,
) -> dict[tuple[str, str], bool]:
    """AST-introspect PlatformDiscovery across all 11 entity platforms.

    Returns mapping ``(platform_name, who_str) -> bus_auto_discovers``.
    """
    contracts: dict[tuple[str, str], bool] = {}
    entity_platforms = (
        "light",
        "switch",
        "cover",
        "climate",
        "binary_sensor",
        "sensor",
        "media_player",
        "button",
        "number",
        "alarm_control_panel",
        "lock",
    )

    for plat in entity_platforms:
        py_path = comp_dir / f"{plat}.py"
        if not py_path.exists():
            continue
        tree = ast.parse(py_path.read_text(encoding="utf-8"))

        func_nodes: dict[str, ast.FunctionDef] = {}
        func_sources: dict[str, str] = {}
        chained_pd_ids: dict[int, bool] = {}
        module_calls_handle_message = False
        unchained_start_listens = False

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_nodes[node.name] = node
                func_sources[node.name] = ast.unparse(node)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "handle_message":
                    module_calls_handle_message = True
                elif node.func.attr == "start":
                    listen_val = True
                    for kw in node.keywords:
                        if (
                            kw.arg == "listen"
                            and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, bool)
                        ):
                            listen_val = kw.value.value
                    inner = node.func.value
                    if (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == "PlatformDiscovery"
                    ):
                        chained_pd_ids[id(inner)] = listen_val
                    elif listen_val:
                        unchained_start_listens = True

        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "PlatformDiscovery"
            ):
                continue
            who_str: str | None = None
            accept_node: ast.expr | None = None
            for kw in node.keywords:
                if (
                    kw.arg == "who"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    who_str = kw.value.value
                elif kw.arg == "accept":
                    accept_node = kw.value
            if who_str is None:
                continue

            if id(node) in chained_pd_ids:
                receives_bus = chained_pd_ids[id(node)]
            else:
                receives_bus = unchained_start_listens or module_calls_handle_message

            accept_rejects_bus = False
            if isinstance(accept_node, ast.Lambda):
                lam_src = ast.unparse(accept_node)
                if "ctx.source != 'bus'" in lam_src or 'ctx.source != "bus"' in lam_src:
                    accept_rejects_bus = True
            elif isinstance(accept_node, ast.Name) and accept_node.id in func_nodes:
                accept_rejects_bus = _func_unconditionally_rejects_bus(
                    func_nodes[accept_node.id]
                )
            elif (
                isinstance(accept_node, ast.Call)
                and isinstance(accept_node.func, ast.Name)
                and accept_node.func.id in func_sources
                and accept_node.args
                and isinstance(accept_node.args[0], ast.Constant)
            ):
                arg_val = accept_node.args[0].value
                fn_src = func_sources[accept_node.func.id]
                if f"who != {arg_val}" in fn_src:
                    accept_rejects_bus = True

            contracts[(plat, who_str)] = receives_bus and (not accept_rejects_bus)

    return contracts


def _extract_runtime_discovery_and_events(
    comp_dir: Path,
) -> tuple[
    set[int],
    dict[tuple[str, str], bool],
    set[str],
    dict[str, set[str]],
    list[str],
    set[str],
]:
    """AST-extract startup WHOs, PlatformDiscovery map, trigger types, unsupported triggers, subtypes, and fired events."""
    startup_whos: set[int] = set()
    platform_discovery = _extract_platform_discovery_contracts(comp_dir)
    gw_py = comp_dir / "gateway.py"
    if gw_py.exists():
        gw_tree = ast.parse(gw_py.read_text(encoding="utf-8"))
        for node in ast.walk(gw_tree):
            val_node: ast.expr | None = None
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "DISCOVERY_REQUESTS":
                        val_node = node.value
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == "DISCOVERY_REQUESTS":
                    val_node = node.value
            if isinstance(val_node, (ast.Tuple, ast.List)):
                for elt in val_node.elts:
                    if (
                        isinstance(elt, ast.Tuple)
                        and elt.elts
                        and isinstance(elt.elts[0], ast.Constant)
                        and isinstance(elt.elts[0].value, int)
                    ):
                        startup_whos.add(elt.elts[0].value)

    const_strings: dict[str, str] = {}
    const_py = comp_dir / "const.py"
    if const_py.exists():
        const_tree = ast.parse(const_py.read_text(encoding="utf-8"))
        for node in ast.walk(const_tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        const_strings[t.id] = node.value.value

    trigger_types: set[str] = set()
    unsupported_by_family: dict[str, set[str]] = {"15": set(), "25": set()}
    subtypes: list[str] = []
    dt_py = comp_dir / "device_trigger.py"
    if dt_py.exists():
        dt_tree = ast.parse(dt_py.read_text(encoding="utf-8"))
        local_sets: dict[str, set[str]] = {}
        for node in dt_tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if isinstance(node.value, ast.Set):
                    resolved = {
                        const_strings[e.id]
                        for e in node.value.elts
                        if isinstance(e, ast.Name) and e.id in const_strings
                    } | {
                        e.value
                        for e in node.value.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    }
                    local_sets[name] = resolved
                    if name == "TRIGGER_TYPES":
                        trigger_types = set(resolved)
                elif name == "TRIGGER_SUBTYPES" and isinstance(node.value, ast.ListComp):
                    # [f"button_{i}" for i in range(0, 32)]
                    for gen in node.value.generators:
                        if (
                            isinstance(gen.iter, ast.Call)
                            and isinstance(gen.iter.func, ast.Name)
                            and gen.iter.func.id == "range"
                            and len(gen.iter.args) == 2
                            and all(isinstance(a, ast.Constant) and isinstance(a.value, int) for a in gen.iter.args)
                        ):
                            lo = int(gen.iter.args[0].value)  # type: ignore[arg-type]
                            hi = int(gen.iter.args[1].value)  # type: ignore[arg-type]
                            subtypes = [f"button_{i}" for i in range(lo, hi)]
                elif name == "_UNSUPPORTED_TRIGGER_TYPES" and isinstance(node.value, ast.Dict):
                    for k, v in zip(node.value.keys, node.value.values):
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            fam_set: set[str] = set()
                            if isinstance(v, ast.Set):
                                for e in v.elts:
                                    if isinstance(e, ast.Name) and e.id in const_strings:
                                        fam_set.add(const_strings[e.id])
                            elif isinstance(v, ast.BinOp) and isinstance(v.op, ast.BitOr):
                                for side in (v.left, v.right):
                                    if isinstance(side, ast.Name) and side.id in local_sets:
                                        fam_set |= local_sets[side.id]
                                    elif isinstance(side, ast.Set):
                                        for e in side.elts:
                                            if isinstance(e, ast.Name) and e.id in const_strings:
                                                fam_set.add(const_strings[e.id])
                            unsupported_by_family[k.value] = fam_set

    fired_events: set[str] = set()
    for py_file in sorted(comp_dir.glob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        local_consts = dict(const_strings)
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        local_consts[t.id] = node.value.value
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "async_fire":
                if node.args:
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str) and arg0.value.startswith("myhome_"):
                        fired_events.add(arg0.value)
                    elif isinstance(arg0, ast.Name) and arg0.id in local_consts and local_consts[arg0.id].startswith("myhome_"):
                        fired_events.add(local_consts[arg0.id])

    return (
        startup_whos,
        platform_discovery,
        trigger_types,
        unsupported_by_family,
        subtypes,
        fired_events,
    )


def _check_unimplemented_who_table_platforms(
    doc_path: Path, unimplemented_whos: set[str]
) -> list[str]:
    """Return errors if any table row for an unimplemented WHO claims an HA entity platform."""
    if not doc_path.exists():
        return [f"Missing subsystem doc: {rel_path(doc_path)}"]
    errors: list[str] = []
    lines = doc_path.read_text(encoding="utf-8").splitlines()
    platform_col_idx: int | None = None
    who_col_idx: int | None = None

    for line_no, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            platform_col_idx = None
            who_col_idx = None
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells if c):
            continue
        if who_col_idx is None:
            lower_cells = [c.lower() for c in cells]
            if lower_cells and lower_cells[0] == "who":
                who_col_idx = 0
                for idx, lc in enumerate(lower_cells):
                    if "platform" in lc or "home assistant entity" in lc:
                        platform_col_idx = idx
                        break
            continue

        if platform_col_idx is None or platform_col_idx >= len(cells):
            continue
        who_cell = re.sub(r"[*`_]", "", cells[who_col_idx]).strip().split()[0] if cells[who_col_idx].strip() else ""
        if who_cell in unimplemented_whos:
            plat_cell = cells[platform_col_idx]
            tokens = {t.lower() for t in re.findall(r"[a-z_]+", plat_cell)}
            claimed = sorted(tokens & _HA_ENTITY_PLATFORM_NAMES)
            if claimed:
                errors.append(
                    f"{rel_path(doc_path)}:{line_no} assigns HA platform(s) {claimed} "
                    f"to unimplemented WHO {who_cell}: {plat_cell!r}"
                )
    return errors


def check_discovery_and_subsystem_docs() -> tuple[bool, list[str]]:
    """Enforce auto-discovery, startup sweep, device_trigger, and WHO subsystem contracts."""
    messages: list[str] = []
    all_ok = True

    (
        startup_whos,
        platform_discovery,
        trigger_types,
        unsupported_by_family,
        subtypes,
        fired_events,
    ) = _extract_runtime_discovery_and_events(CUSTOM_COMPONENTS_DIR)

    # 0. Verify PlatformDiscovery AST contracts & startup sweep WHOs in docs/configuration/README.md
    expected_bus_contracts: dict[tuple[str, str], bool] = {
        ("light", "1"): True,
        ("switch", "1"): False,
        ("switch", "4"): False,
        ("cover", "2"): True,
        ("cover", "1"): False,
        ("climate", "4"): True,
        ("binary_sensor", "25"): True,
        ("binary_sensor", "9"): False,
        ("binary_sensor", "1"): True,
        ("sensor", "18"): True,
        ("sensor", "1"): True,
        ("sensor", "4"): True,
        ("media_player", "16"): True,
        ("alarm_control_panel", "5"): True,
        ("lock", "6"): False,
    }
    for plat_key, exp_disc in expected_bus_contracts.items():
        if platform_discovery.get(plat_key) is not exp_disc:
            messages.append(
                f"Expected PlatformDiscovery{plat_key} bus auto-discovery={exp_disc}, "
                f"got {platform_discovery.get(plat_key)!r}"
            )
            all_ok = False

    cfg_readme = DOCS_DIR / "configuration" / "README.md"
    if startup_whos and cfg_readme.exists():
        cfg_readme_text = cfg_readme.read_text(encoding="utf-8")
        sweep_match = re.search(
            r"Startup discovery queries[^`\n]*`WHO\s*=\s*([0-9,\s]+)`",
            cfg_readme_text,
        )
        if not sweep_match:
            messages.append(
                f"{rel_path(cfg_readme)} must document 'Startup discovery queries ... (`WHO = ...`)'"
            )
            all_ok = False
        else:
            doc_startup_whos = {
                int(tok.strip())
                for tok in sweep_match.group(1).split(",")
                if tok.strip().isdigit()
            }
            if doc_startup_whos != startup_whos:
                messages.append(
                    f"{rel_path(cfg_readme)} startup sweep WHOs {sorted(doc_startup_whos)} "
                    f"do not match gateway.py DISCOVERY_REQUESTS {sorted(startup_whos)}"
                )
                all_ok = False

    # 1. Switches & binary sensors must not claim false dynamic bus auto-discovery
    switches_doc = DOCS_DIR / "configuration" / "switches.md"
    if switches_doc.exists():
        sw_text = switches_doc.read_text(encoding="utf-8")
        if re.search(
            r"(?i)\bswitches\s+are\s+automatically\s+discovered\b|"
            r"\bwho\s*=\s*1\s+switches\s+(?:are\s+)?(?:auto-?discover\w*|automatically\s+discovered)",
            sw_text,
        ):
            messages.append(
                f"{rel_path(switches_doc)} falsely claims WHO = 1 switches auto-discover from bus traffic"
            )
            all_ok = False

    bs_doc = DOCS_DIR / "configuration" / "binary-sensors.md"
    if bs_doc.exists():
        bs_text = bs_doc.read_text(encoding="utf-8")
        if re.search(
            r"(?i)\bbinary\s+sensors\s+for\s+all\s+supported\s+who\b[^.\n]*automatically\s+discovered|"
            r"\bwho\s*=\s*9\b[^.\n]{0,60}(?<!not\s)(?<!never\s)\b(?:automatically\s+discovered|auto-?discovers?\s+from)",
            bs_text,
        ):
            messages.append(
                f"{rel_path(bs_doc)} falsely claims WHO = 9 auxiliary binary sensors auto-discover from bus traffic"
            )
            all_ok = False

    # 2. Unimplemented WHOs (3, 7, 17, 24) must not list HA entity platforms
    unimplemented = {"3", "7", "17", "24"}
    for who_doc in (
        DOCS_DIR / "openwebnet-who-specifications.md",
        DOCS_DIR / "configuration" / "supported_functions.md",
    ):
        errs = _check_unimplemented_who_table_platforms(who_doc, unimplemented)
        if errs:
            messages.extend(errs)
            all_ok = False

    # 3. Verify CEN / CEN+ device_trigger subtypes and support matrix
    sf_doc = DOCS_DIR / "configuration" / "supported_functions.md"
    cen_doc = DOCS_DIR / "configuration" / "cen_cenplus.md"
    if subtypes and sf_doc.exists() and cen_doc.exists():
        sf_text = sf_doc.read_text(encoding="utf-8")
        cen_text = cen_doc.read_text(encoding="utf-8")
        first_sub, last_sub = subtypes[0], subtypes[-1]
        if first_sub not in sf_text or last_sub not in sf_text:
            messages.append(
                f"{rel_path(sf_doc)} must document exact device_trigger subtype range {first_sub}..{last_sub}"
            )
            all_ok = False
        if re.search(r"\b1\.\.31\b|\b1–31\b|\bbutton_1`?\s*(?:\.\.|–|-)\s*`?button_31\b", cen_text):
            messages.append(
                f"{rel_path(cen_doc)} documents stale 1..31 button range instead of 0..31 ({first_sub}..{last_sub})"
            )
            all_ok = False
        for trig in sorted(trigger_types):
            if trig not in sf_text:
                messages.append(f"Trigger type {trig!r} missing from {rel_path(sf_doc)}")
                all_ok = False
            if trig not in cen_text:
                messages.append(f"Trigger type {trig!r} missing from {rel_path(cen_doc)}")
                all_ok = False

        # Validate ✅ / ❌ cells in supported_functions.md trigger table
        in_trig_table = False
        for line_no, raw in enumerate(sf_text.splitlines(), start=1):
            s = raw.strip()
            if s.startswith("| Trigger |") and "CEN" in s and "CEN+" in s:
                in_trig_table = True
                continue
            if in_trig_table:
                if not s.startswith("|"):
                    in_trig_table = False
                    continue
                cells = [c.strip() for c in s.strip("|").split("|")]
                if len(cells) < 3 or "---" in cells[0]:
                    continue
                row_triggers = [t for t in re.findall(r"`([a-z0-9_]+)`", cells[0]) if t in trigger_types]
                for trig in row_triggers:
                    exp_cen = trig not in unsupported_by_family.get("15", set())
                    exp_cenplus = trig not in unsupported_by_family.get("25", set())
                    cen_ok = ("✅" in cells[1]) if exp_cen else ("❌" in cells[1] and "✅" not in cells[1])
                    cenplus_ok = ("✅" in cells[2]) if exp_cenplus else ("❌" in cells[2] and "✅" not in cells[2])
                    if not cen_ok or not cenplus_ok:
                        messages.append(
                            f"{rel_path(sf_doc)}:{line_no} CEN/CEN+ trigger matrix mismatch for {trig!r}: "
                            f"got ({cells[1]}, {cells[2]}), expected CEN={'✅' if exp_cen else '❌'}, "
                            f"CEN+={'✅' if exp_cenplus else '❌'}"
                        )
                        all_ok = False

    # 4. Every myhome_* event fired in runtime code must appear in docs/
    if DOCS_DIR.exists() and fired_events:
        all_docs_text = "\n".join(
            md.read_text(encoding="utf-8") for md in sorted(DOCS_DIR.rglob("*.md"))
        )
        undocumented_events = sorted(ev for ev in fired_events if ev not in all_docs_text)
        if undocumented_events:
            messages.append(
                f"Runtime Home Assistant bus events missing from docs/: {undocumented_events}"
            )
            all_ok = False

    if all_ok:
        messages.append(
            f"Auto-discovery, WHO subsystem matrix, CEN/CEN+ triggers ({len(trigger_types)}), "
            f"and {len(fired_events)} runtime bus events verified cleanly."
        )
    return all_ok, messages


# ─────────────────────────────────────────────────────────────────────────────
# 12. Bus Monitor Card & WebSocket API Sentinel (Gate 5)
# ─────────────────────────────────────────────────────────────────────────────

_REMOVED_BUS_CARD_LABELS = (
    "Report Issue / Copy Diagnostic Bundle",
    "Report Issue / Copy Trace",
)


def _extract_websocket_contract(ws_py: Path) -> tuple[set[str], int | None]:
    """AST-extract WS_TYPE_* endpoints and SCHEMA_WS_HISTORY limit max from websocket.py."""
    tree = ast.parse(ws_py.read_text(encoding="utf-8"))
    ws_types: set[str] = set()
    history_max: int | None = None

    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.startswith("WS_TYPE_"):
                    ws_types.add(node.value.value)
        schema_dict: ast.expr | None = None
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "SCHEMA_WS_HISTORY":
                    schema_dict = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "SCHEMA_WS_HISTORY":
                schema_dict = node.value
        if isinstance(schema_dict, ast.Dict):
            for k, v in zip(schema_dict.keys, schema_dict.values):
                if (
                    isinstance(k, ast.Call)
                    and k.args
                    and isinstance(k.args[0], ast.Constant)
                    and k.args[0].value == "limit"
                ):
                    for sub in ast.walk(v):
                        if (
                            isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Attribute)
                            and sub.func.attr == "Range"
                        ):
                            for kw in sub.keywords:
                                if kw.arg == "max" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, int):
                                    history_max = kw.value.value
    return ws_types, history_max


def _extract_bus_card_toolbar_buttons(js_text: str, translations_text: str = "") -> list[str]:
    """Extract toolbar labels from the shared view and its English translations."""
    english = re.search(r'"en"\s*:\s*(\{.*?\n  \})', translations_text, re.DOTALL)
    translations = json.loads(english.group(1)) if english else {}
    labels: list[str] = []
    toolbar_match = re.search(
        r'<div class="toolbar\b[^>]*>(.*?)</div>\s*</div>\s*<div id="help-panel"',
        js_text,
        re.DOTALL,
    )
    toolbar_html = toolbar_match.group(1) if toolbar_match else js_text
    for attrs, btn_body in re.findall(
        r'<button\b([^>]*\bid="btn-(?!help\b)[a-z0-9_-]+"[^>]*)>(.*?)</button>',
        toolbar_html,
        re.DOTALL,
    ):
        key = re.search(r'data-bus-text="([^"]+)"', attrs)
        cleaned = translations.get(key.group(1), " ".join(btn_body.split())) if key else " ".join(btn_body.split())
        if cleaned and cleaned not in labels:
            labels.append(cleaned)

    trace_toggle = re.search(
        r'this\._tracing\s*\?\s*"([^"]+)"\s*:\s*"([^"]+)"',
        js_text,
    )
    if trace_toggle:
        for lbl in (trace_toggle.group(1).strip(), trace_toggle.group(2).strip()):
            lbl = translations.get(lbl, lbl)
            if lbl and lbl not in labels:
                labels.append(lbl)
    return labels


def check_bus_monitor_and_ws_docs() -> tuple[bool, list[str]]:
    """Verify Bus Monitor Lovelace card & WebSocket API contracts across docs."""
    messages: list[str] = []
    if not all(path.exists() for path in (WEBSOCKET_PY, BUS_CARD_JS, BUS_VIEW_JS, BUS_TRANSLATIONS_JS)):
        return False, ["Missing WebSocket, card, shared monitor view or translations for Bus Monitor check"]

    all_ok = True
    ws_types, ws_history_max = _extract_websocket_contract(WEBSOCKET_PY)
    js_text = BUS_CARD_JS.read_text(encoding="utf-8")
    who_catalog = _extract_bus_card_who_catalog(BUS_VIEW_JS)
    toolbar_buttons = _extract_bus_card_toolbar_buttons(
        BUS_VIEW_JS.read_text(encoding="utf-8"), BUS_TRANSLATIONS_JS.read_text(encoding="utf-8")
    )
    card_tags = set(re.findall(r'\.define\(\s*"([^"]+)"', js_text))
    max_frames_match = re.search(
        r'name:\s*"max_frames".*?max:\s*(\d+)', js_text, re.DOTALL
    )
    card_max_frames = int(max_frames_match.group(1)) if max_frames_match else None

    if not toolbar_buttons:
        messages.append("Could not extract toolbar button labels from the shared monitor view")
        all_ok = False

    if ws_history_max is None or card_max_frames is None:
        messages.append("Could not extract SCHEMA_WS_HISTORY max or myhome-bus-card.js max_frames max")
        all_ok = False
    elif ws_history_max < card_max_frames:
        messages.append(
            f"WebSocket history limit max ({ws_history_max}) is smaller than myhome-bus-card.js max_frames ({card_max_frames})"
        )
        all_ok = False

    bus_doc = DOCS_DIR / "configuration" / "bus_monitor.md"
    if not bus_doc.exists():
        messages.append(f"Missing Bus Monitor doc: {rel_path(bus_doc)}")
        all_ok = False
    else:
        bus_text = bus_doc.read_text(encoding="utf-8")
        missing_ws = sorted(ep for ep in ws_types if ep not in bus_text)
        if missing_ws:
            messages.append(
                f"{rel_path(bus_doc)} is missing WebSocket endpoint(s): {missing_ws}"
            )
            all_ok = False
        if "myhome-openwebnet-bus-monitor" in card_tags and "custom:myhome-openwebnet-bus-monitor" not in bus_text:
            messages.append(
                f"{rel_path(bus_doc)} must document primary card tag 'custom:myhome-openwebnet-bus-monitor'"
            )
            all_ok = False
        if str(len(who_catalog)) not in bus_text:
            messages.append(
                f"{rel_path(bus_doc)} must document accurate WHO_CATALOG count ({len(who_catalog)})"
            )
            all_ok = False
        missing_buttons = [btn for btn in toolbar_buttons if btn not in bus_text]
        if missing_buttons:
            messages.append(
                f"{rel_path(bus_doc)} is missing toolbar button label(s) from the shared monitor view: {missing_buttons}"
            )
            all_ok = False

    scan_targets: list[Path] = []
    if DOCS_DIR.exists():
        scan_targets.extend(sorted(DOCS_DIR.rglob("*.md")))
    for root_md in (README_MD, ROADMAP_MD, CONTRIBUTING_MD):
        if root_md.exists():
            scan_targets.append(root_md)

    ws_ref_re = re.compile(r"\bmyhome/(?:bus_monitor|cover)/[a-z0-9_]+\b")
    for md_file in scan_targets:
        text = md_file.read_text(encoding="utf-8")
        for ref in sorted(set(ws_ref_re.findall(text))):
            if ref not in ws_types:
                messages.append(
                    f"{rel_path(md_file)} references nonexistent WebSocket endpoint '{ref}' "
                    f"(valid: {sorted(ws_types)})"
                )
                all_ok = False
        for old_label in _REMOVED_BUS_CARD_LABELS:
            if old_label in text:
                messages.append(
                    f"{rel_path(md_file)} references removed Bus Monitor button label {old_label!r}"
                )
                all_ok = False

    if all_ok:
        messages.append(
            f"Bus Monitor card ({sorted(card_tags)}, max_frames={card_max_frames}, "
            f"{len(who_catalog)} WHO entries) and {len(ws_types)} WebSocket endpoints verified."
        )
    return all_ok, messages


# ─────────────────────────────────────────────────────────────────────────────
# 13. Blueprint Schema & Version-Aware Branch-URL Sentinel (Gate 6)
# ─────────────────────────────────────────────────────────────────────────────

def _expected_repo_branch(manifest_version: str) -> tuple[str, str]:
    """Return (expected_branch, stale_branch) from manifest.json version."""
    if re.search(r"(?:a|b|rc)\d+", manifest_version):
        return "v2-phase1-architecture", "master"
    return "master", "v2-phase1-architecture"


def _collect_strings_recursive(obj: Any) -> list[str]:
    """Collect all string values inside nested dicts/lists."""
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_collect_strings_recursive(v))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_collect_strings_recursive(item))
    return out


def sync_blueprints_and_branch_urls(update: bool = False) -> tuple[bool, list[str]]:
    """Validate blueprints (including !input tags and lock-only safety) and branch URLs."""
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required to validate blueprints.") from exc

    class _BlueprintLoader(yaml.SafeLoader):
        pass

    _BlueprintLoader.add_constructor(
        "!input",
        lambda loader, node: {"__input__": loader.construct_scalar(node)},
    )

    messages: list[str] = []
    all_ok = True
    manifest_version, _ = extract_manifest_version_info()
    if not manifest_version:
        return False, ["Could not read version from manifest.json for branch-URL check"]

    expected_branch, stale_branch = _expected_repo_branch(manifest_version)

    # 1. Blueprint YAML schema, source_url, and lock-only "Open Gate" safety check
    if BLUEPRINTS_DIR.exists():
        for bp_file in sorted(BLUEPRINTS_DIR.glob("*.yaml")):
            raw_text = bp_file.read_text(encoding="utf-8")
            expected_url = (
                f"https://github.com/OpenWebNet-HA/MyHOME/blob/{expected_branch}/"
                f"blueprints/automation/myhome/{bp_file.name}"
            )
            stale_url = (
                f"https://github.com/OpenWebNet-HA/MyHOME/blob/{stale_branch}/"
                f"blueprints/automation/myhome/{bp_file.name}"
            )
            if stale_url in raw_text and update:
                raw_text = raw_text.replace(stale_url, expected_url)
                bp_file.write_text(raw_text, encoding="utf-8")
                messages.append(
                    f"Updated blueprint source_url branch to '{expected_branch}' in {rel_path(bp_file)}"
                )

            try:
                bp_data = yaml.load(raw_text, Loader=_BlueprintLoader) or {}
            except Exception as err:
                messages.append(f"Failed parsing blueprint {rel_path(bp_file)}: {err}")
                all_ok = False
                continue

            bp_meta = bp_data.get("blueprint", {}) if isinstance(bp_data, dict) else {}
            if bp_meta.get("domain") != "automation":
                messages.append(
                    f"{rel_path(bp_file)} blueprint.domain must be 'automation' (got {bp_meta.get('domain')!r})"
                )
                all_ok = False
            actual_url = bp_meta.get("source_url", "")
            if actual_url != expected_url:
                messages.append(
                    f"{rel_path(bp_file)} blueprint.source_url mismatch: got {actual_url!r}, expected {expected_url!r}"
                )
                all_ok = False

            inputs = bp_meta.get("input", {}) if isinstance(bp_meta.get("input"), dict) else {}
            has_lock_only_input = False
            has_cover_input = False
            for inp_name, inp_spec in inputs.items():
                if not isinstance(inp_spec, dict):
                    continue
                sel = inp_spec.get("selector", {})
                ent_sel = sel.get("entity") if isinstance(sel, dict) else None
                if isinstance(ent_sel, dict):
                    dom = ent_sel.get("domain")
                    dom_set = {dom} if isinstance(dom, str) else set(dom) if isinstance(dom, list) else set()
                    if "cover" in dom_set:
                        has_cover_input = True
                    if dom_set == {"lock"}:
                        has_lock_only_input = True
                        inp_text = f"{inp_spec.get('name', '')} {inp_spec.get('description', '')}"
                        if re.search(r"(?i)\bopen\s+gate\b", inp_text):
                            messages.append(
                                f"{rel_path(bp_file)} lock-only input {inp_name!r} must not advertise 'Open Gate'"
                            )
                            all_ok = False

            if has_lock_only_input and not has_cover_input:
                all_bp_strings = " ".join(
                    _collect_strings_recursive(bp_data.get("variables"))
                    + _collect_strings_recursive(bp_data.get("action"))
                )
                if re.search(r"(?i)\bopen\s+gate\b", all_bp_strings):
                    messages.append(
                        f"{rel_path(bp_file)} restricts entity selector to domain 'lock' only "
                        "but advertises 'Open Gate' in notification/action text"
                    )
                    all_ok = False

    # 2. mkdocs.yml and mkdocs-v0.9.yml edit_uri branch check
    for mk_file, expected_dir_slug in ((MKDOCS_YML, "docs"), (MKDOCS_V09_YML, "docs-v0.9")):
        if not mk_file.exists():
            continue
        mk_text = mk_file.read_text(encoding="utf-8")
        expected_uri = f"edit/{expected_branch}/{expected_dir_slug}/"
        stale_uri = f"edit/{stale_branch}/{expected_dir_slug}/"
        if stale_uri in mk_text:
            if update:
                mk_text = mk_text.replace(stale_uri, expected_uri)
                mk_file.write_text(mk_text, encoding="utf-8")
                messages.append(f"Updated edit_uri branch to '{expected_branch}' in {rel_path(mk_file)}")
            else:
                messages.append(
                    f"Outdated edit_uri in {rel_path(mk_file)}: found {stale_uri!r}, expected {expected_uri!r}"
                )
                all_ok = False
        elif f"edit_uri: {expected_uri}" not in mk_text:
            messages.append(
                f"{rel_path(mk_file)} missing expected 'edit_uri: {expected_uri}'"
            )
            all_ok = False

    # 3. hardware-coupled-sensors.md blueprint import URL branch check
    hw_sensors_doc = DOCS_DIR / "guides" / "hardware-coupled-sensors.md"
    if hw_sensors_doc.exists():
        hw_text = hw_sensors_doc.read_text(encoding="utf-8")
        stale_enc = f"blob%2F{stale_branch}%2Fblueprints%2F"
        exp_enc = f"blob%2F{expected_branch}%2Fblueprints%2F"
        stale_plain = f"blob/{stale_branch}/blueprints/"
        exp_plain = f"blob/{expected_branch}/blueprints/"
        if stale_enc in hw_text or stale_plain in hw_text:
            if update:
                hw_text = hw_text.replace(stale_enc, exp_enc).replace(stale_plain, exp_plain)
                hw_sensors_doc.write_text(hw_text, encoding="utf-8")
                messages.append(
                    f"Updated blueprint import URL branch to '{expected_branch}' in {rel_path(hw_sensors_doc)}"
                )
            else:
                messages.append(
                    f"Outdated blueprint import branch '{stale_branch}' in {rel_path(hw_sensors_doc)} "
                    f"(expected '{expected_branch}' for version {manifest_version})"
                )
                all_ok = False
        elif exp_enc not in hw_text and exp_plain not in hw_text:
            messages.append(
                f"{rel_path(hw_sensors_doc)} missing blueprint URL targeting branch '{expected_branch}'"
            )
            all_ok = False

    if all_ok:
        messages.append(
            f"Blueprints and version-aware branch URLs ('{expected_branch}' for v{manifest_version}) verified."
        )
    return all_ok, messages


# ─────────────────────────────────────────────────────────────────────────────
# 14. Cross-Manifest & Packaging Metadata Sync (Gate 7)
# ─────────────────────────────────────────────────────────────────────────────

def sync_packaging_metadata(update: bool = False) -> tuple[bool, list[str]]:
    """Enforce and auto-sync pyproject.toml and CONTRIBUTING.md against manifest.json."""
    messages: list[str] = []
    all_ok = True

    manifest_version, ownd_req = extract_manifest_version_info()
    if not manifest_version or not ownd_req:
        return False, ["Could not extract version and OWNd requirement from manifest.json"]

    if not PYPROJECT_TOML.exists():
        return False, [f"Missing {rel_path(PYPROJECT_TOML)}"]

    toml_text = PYPROJECT_TOML.read_text(encoding="utf-8")
    data = tomllib.loads(toml_text)
    project = data.get("project", {})
    py_version = project.get("version", "")
    req_python = project.get("requires-python", "")
    classifiers: list[str] = list(project.get("classifiers", []))
    test_deps: list[str] = list(project.get("optional-dependencies", {}).get("test", []))

    needs_toml_update = False
    new_toml = toml_text

    if py_version != manifest_version:
        if update:
            new_toml = re.sub(
                r'(?m)^version\s*=\s*"[^"]*"',
                f'version = "{manifest_version}"',
                new_toml,
                count=1,
            )
            needs_toml_update = True
            messages.append(f"Updated pyproject.toml version to '{manifest_version}'")
        else:
            messages.append(
                f"pyproject.toml version mismatch: got {py_version!r}, expected {manifest_version!r}"
            )
            all_ok = False

    if req_python != ">=3.14":
        if update:
            new_toml = re.sub(
                r'(?m)^requires-python\s*=\s*"[^"]*"',
                'requires-python = ">=3.14"',
                new_toml,
                count=1,
            )
            needs_toml_update = True
            messages.append('Updated pyproject.toml requires-python to ">=3.14"')
        else:
            messages.append(
                f"pyproject.toml requires-python must be '>=3.14' (got {req_python!r})"
            )
            all_ok = False

    if "Programming Language :: Python :: 3.13" in classifiers:
        if update:
            new_toml = re.sub(
                r'(?m)^\s*"Programming Language :: Python :: 3\.13"\s*,?\r?\n',
                "",
                new_toml,
            )
            needs_toml_update = True
            messages.append("Removed stale 'Programming Language :: Python :: 3.13' classifier from pyproject.toml")
        else:
            messages.append(
                "pyproject.toml classifiers must not include 'Programming Language :: Python :: 3.13'"
            )
            all_ok = False

    if ownd_req not in test_deps:
        if update and re.search(r'"OWNd==[^"]+"', new_toml):
            new_toml = re.sub(r'"OWNd==[^"]+"', f'"{ownd_req}"', new_toml)
            needs_toml_update = True
            messages.append(f"Updated pyproject.toml test dependency to '{ownd_req}'")
        else:
            messages.append(
                f"pyproject.toml optional-dependencies.test missing '{ownd_req}' (got {[d for d in test_deps if d.startswith('OWNd')]})"
            )
            all_ok = False

    if needs_toml_update:
        PYPROJECT_TOML.write_text(new_toml, encoding="utf-8")

    if CONTRIBUTING_MD.exists():
        contrib_text = CONTRIBUTING_MD.read_text(encoding="utf-8")
        if "Python 3.14" not in contrib_text:
            messages.append("CONTRIBUTING.md must reference 'Python 3.14'")
            all_ok = False
        if re.search(r"Python\s+3\.1[0-3]\b", contrib_text):
            messages.append("CONTRIBUTING.md references stale pre-3.14 Python version")
            all_ok = False

    if all_ok and not needs_toml_update:
        messages.append(
            f"Packaging metadata verified cleanly (pyproject.toml v{manifest_version}, >=3.14, {ownd_req})."
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

    # 10. Canonical WHO labels parity (Gate 1)
    ok_who_lbl, msgs_who_lbl = check_who_labels_parity()
    overall_ok = overall_ok and ok_who_lbl
    all_messages.extend(msgs_who_lbl)

    # 11. Auto-discovery, startup sweep & subsystem matrix contracts (Gate 4)
    ok_subsys, msgs_subsys = check_discovery_and_subsystem_docs()
    overall_ok = overall_ok and ok_subsys
    all_messages.extend(msgs_subsys)

    # 12. Bus Monitor Lovelace card & WebSocket API contracts (Gate 5)
    ok_bus, msgs_bus = check_bus_monitor_and_ws_docs()
    overall_ok = overall_ok and ok_bus
    all_messages.extend(msgs_bus)

    # 13. Blueprint schema & version-aware branch-URL contracts (Gate 6)
    ok_bp, msgs_bp = sync_blueprints_and_branch_urls(update=update)
    overall_ok = overall_ok and ok_bp
    all_messages.extend(msgs_bp)

    # 14. Cross-manifest & packaging metadata sync (Gate 7)
    ok_pkg, msgs_pkg = sync_packaging_metadata(update=update)
    overall_ok = overall_ok and ok_pkg
    all_messages.extend(msgs_pkg)

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
