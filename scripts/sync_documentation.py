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
SERVICES_YAML = CUSTOM_COMPONENTS_DIR / "services.yaml"
STRINGS_JSON = CUSTOM_COMPONENTS_DIR / "strings.json"
REPAIRS_PY = CUSTOM_COMPONENTS_DIR / "repairs.py"
MKDOCS_YML = REPO_ROOT / "mkdocs.yml"
README_MD = REPO_ROOT / "README.md"
DOCS_DIR = REPO_ROOT / "docs"

# Documentation target files for Gateway Profiles table
GATEWAY_DOC_TARGETS = [
    README_MD,
    DOCS_DIR / "configuration" / "gateways.md",
    DOCS_DIR / "getting-started" / "hardware-compatibility.md",
]

# Markers
GATEWAY_START_MARKER = "<!-- GATEWAY_PROFILES_START -->"
GATEWAY_END_MARKER = "<!-- GATEWAY_PROFILES_END -->"

OPTIONS_START_MARKER = "<!-- GATEWAY_OPTIONS_START -->"
OPTIONS_END_MARKER = "<!-- GATEWAY_OPTIONS_END -->"

DOMAINS_START_MARKER = "<!-- SUPPORTED_DOMAINS_START -->"
DOMAINS_END_MARKER = "<!-- SUPPORTED_DOMAINS_END -->"

TRACE_START_MARKER = "<!-- TRACE_MATRIX_START -->"
TRACE_END_MARKER = "<!-- TRACE_MATRIX_END -->"

SERVICES_START_MARKER = "<!-- SERVICES_TABLE_START -->"
SERVICES_END_MARKER = "<!-- SERVICES_TABLE_END -->"


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

def sync_mkdocs_nav(update: bool = False) -> tuple[bool, list[str]]:
    """Verify mkdocs.yml navigation includes all markdown documentation pages."""
    if not MKDOCS_YML.exists():
        return False, [f"{MKDOCS_YML} not found"]

    messages: list[str] = []
    all_ok = True
    mkdocs_text = MKDOCS_YML.read_text(encoding="utf-8")

    # Excluded files not meant for top-level navigation
    EXCLUDED_DOCS = {
        "index.md",
    }

    unlisted_pages: list[str] = []
    for md_file in DOCS_DIR.rglob("*.md"):
        rel_path = md_file.relative_to(DOCS_DIR).as_posix()
        if rel_path in EXCLUDED_DOCS:
            continue
        if rel_path not in mkdocs_text:
            unlisted_pages.append(rel_path)

    if unlisted_pages:
        messages.append(f"Markdown pages missing from mkdocs.yml navigation: {unlisted_pages}")
        all_ok = False
    else:
        messages.append("All documentation pages are referenced in mkdocs.yml navigation")

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



    return all_ok, messages


# ─────────────────────────────────────────────────────────────────────────────
# 8. Link & Anchor Health
# ─────────────────────────────────────────────────────────────────────────────

def check_markdown_link_health() -> tuple[bool, list[str]]:
    """Check for malformed anchor links, empty targets, and broken relative links across docs/."""
    messages: list[str] = []
    all_ok = True

    # Pattern for anchor links with emoji-stripped leading hyphen like '#-'
    bad_anchor_pattern = re.compile(r"\[([^\]]+)\]\((\S*?#-[\w-]+)\)")
    # Pattern for markdown links
    link_pattern = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")

    for md_file in sorted(DOCS_DIR.rglob("*.md")):
        content = md_file.read_text(encoding="utf-8")

        # 1. Emoji-stripped leading hyphen
        matches = bad_anchor_pattern.findall(content)
        if matches:
            for text, link in matches:
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

            # Ignore external URLs and Mike multi-version paths
            if (
                url_clean.startswith("http://")
                or url_clean.startswith("https://")
                or url_clean.startswith("mailto:")
                or "0.9.4" in url_clean
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

    # 2. Supported domains
    ok_dom, msgs_dom = sync_supported_domains(update=update)
    overall_ok = overall_ok and ok_dom
    all_messages.extend(msgs_dom)

    # 3. Trace matrix
    ok_trace, msgs_trace = sync_trace_matrix(update=update)
    overall_ok = overall_ok and ok_trace
    all_messages.extend(msgs_trace)

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
