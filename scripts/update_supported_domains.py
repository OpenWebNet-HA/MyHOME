#!/usr/bin/env python3
"""Update and calibrate the Supported Entity Domains & Automations table in README.md.

Maintains live, automated documentation of supported entity domains and automations
by cross-referencing custom_components/myhome/const.py (PLATFORMS) and
docs/configuration/supported_functions.md.
Called locally, by scripts/verify_ha_standards.py, and by GitHub Actions CI workflows.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_MD = REPO_ROOT / "README.md"
CONST_PY = REPO_ROOT / "custom_components" / "myhome" / "const.py"
SUPPORTED_FUNCTIONS_MD = REPO_ROOT / "docs" / "configuration" / "supported_functions.md"
DEVICE_TRIGGER_PY = REPO_ROOT / "custom_components" / "myhome" / "device_trigger.py"

START_MARKER = "<!-- SUPPORTED_DOMAINS_START -->"
END_MARKER = "<!-- SUPPORTED_DOMAINS_END -->"

FOOTER_NOTE = (
    "*This table is automatically updated from platform definitions and "
    "[`supported_functions.md`](docs/configuration/supported_functions.md).*"
)

# Canonical domain ordering and capabilities mapping, cross-referenced with supported_functions.md
PREFERRED_ORDER = [
    "light",
    "switch",
    "cover",
    "climate",
    "alarm_control_panel",
    "binary_sensor",
    "sensor",
    "button",
    "media_player",
    "device_trigger",
]

DOMAIN_METADATA: dict[str, dict[str, str]] = {
    "light": {
        "label": "**`light`**",
        "capabilities": (
            "On/Off, Dimmers with brightness control & transitions (stepped & native), "
            "DALI DT8 Tunable White (Dimension 14, 2000K–6535K), HS/RGB colour, "
            "Hardware-offloaded bus timers (`myhome.turn_on_timed` / `timer` parameter)"
        ),
    },
    "switch": {
        "label": "**`switch`**",
        "capabilities": (
            "Relays, auxiliary switches, socket actuators (switch/outlet device classes), "
            "Hardware-offloaded bus timers (`myhome.turn_on_timed` / `timer` parameter)"
        ),
    },
    "cover": {
        "label": "**`cover`**",
        "capabilities": (
            "Motorized shutters, blinds, roll-ups with state tracking, "
            "position-reporting actuators & virtual travel-time positioning"
        ),
    },
    "climate": {
        "label": "**`climate`**",
        "capabilities": (
            "Heating, cooling, 4-pipe systems, thermostats, setpoints, fancoil 3-speed modes, "
            "offset tracking, Central Unit 3550 (`#0`) & 4695 (`#0#1`) master coordination & seasonal propagation"
        ),
    },
    "alarm_control_panel": {
        "label": "**`alarm_control_panel`**",
        "capabilities": (
            "Central units (3485/3486), partitions, arm away/home, disarm, panic trigger, "
            "zone 0 broadcast sync"
        ),
    },
    "binary_sensor": {
        "label": "**`binary_sensor`**",
        "capabilities": (
            "Magnetic contacts, door/window sensors, PIR motion, AUX channels (1–9), "
            "dry contacts (F482/3477), inverted contacts"
        ),
    },
    "sensor": {
        "label": "**`sensor`**",
        "capabilities": (
            "Power meters, energy counters (total/daily/monthly), temperature probes (3475), "
            "illuminance / lux sensors"
        ),
    },
    "button": {
        "label": "**`button`**",
        "capabilities": (
            "Hardware actuator lock/unlock for lights, switches & covers (WHO=14), "
            "cover travel time calibration buttons (per cover & gateway-wide, WHO=2)"
        ),
    },
    "media_player": {
        "label": "**`media_player`**",
        "capabilities": (
            "F441/F441M audio zones, source tracking, volume normalization, software mute, "
            "streaming dynamic proxy (Music Assistant / Spotify Connect)"
        ),
    },
    "device_trigger": {
        "label": "**`device_trigger`** *(Automations)*",
        "capabilities": (
            'Stateless CEN & CEN+ scenario pushbuttons with string-preserved addressing (`"0001"`), '
            "gateway MAC isolation, and 8 native UI trigger types (short press, long press start, held, release, rotary dials)"
        ),
    },
}


def extract_platforms_from_const() -> list[str]:
    """Parse PLATFORMS tuple from custom_components/myhome/const.py using AST."""
    if not CONST_PY.exists():
        raise FileNotFoundError(f"{CONST_PY} not found")

    tree = ast.parse(CONST_PY.read_text(encoding="utf-8"))
    platforms = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "PLATFORMS":
            if isinstance(node.value, (ast.Tuple, ast.List)):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Attribute):
                        platforms.append(elt.attr.lower())
    return platforms


def extract_who_mapping() -> dict[str, str]:
    """Extract WHO mappings from docs/configuration/supported_functions.md."""
    if not SUPPORTED_FUNCTIONS_MD.exists():
        raise FileNotFoundError(f"{SUPPORTED_FUNCTIONS_MD} not found")

    content = SUPPORTED_FUNCTIONS_MD.read_text(encoding="utf-8")
    sections = content.split("\n### ")
    who_mapping: dict[str, str] = {}

    for sec in sections[1:]:
        lines = [line.strip() for line in sec.strip().splitlines() if line.strip()]
        if not lines:
            continue
        header = lines[0]
        m = re.match(r"(?:`([^`]+)`|([^()]+))\s*\((WHO\s+[^)]+)\)", header)
        if not m:
            continue
        name = (m.group(1) or m.group(2)).strip()
        who_raw = m.group(3).strip()
        who_normalized = who_raw.replace("WHO ", "WHO=")

        if name.lower().startswith("device trigger"):
            domain = "device_trigger"
        else:
            domain = name

        who_mapping[domain] = who_normalized

    return who_mapping


def generate_domains_table() -> str:
    """Generate the markdown table for supported entity domains and automations."""
    platforms = extract_platforms_from_const()
    who_mapping = extract_who_mapping()

    missing_in_docs = [p for p in platforms if p not in who_mapping]
    if missing_in_docs:
        raise ValueError(f"Platforms defined in const.py but missing in supported_functions.md: {missing_in_docs}")

    all_domains = list(platforms)
    if DEVICE_TRIGGER_PY.exists() and "device_trigger" not in all_domains:
        all_domains.append("device_trigger")

    missing_metadata = [d for d in all_domains if d not in DOMAIN_METADATA]
    if missing_metadata:
        raise ValueError(f"Domains missing metadata definition: {missing_metadata}")

    def sort_key(d: str) -> int:
        return PREFERRED_ORDER.index(d) if d in PREFERRED_ORDER else 999

    sorted_domains = sorted(all_domains, key=sort_key)

    lines = [
        "| Domain | WHO | Capabilities |",
        "|---|---|---|",
    ]

    for domain in sorted_domains:
        meta = DOMAIN_METADATA[domain]
        label = meta["label"]
        who = who_mapping.get(domain, "—")
        capabilities = meta["capabilities"]
        lines.append(f"| {label} | {who} | {capabilities} |")

    return "\n".join(lines)


def build_block() -> str:
    """Return the complete marker-wrapped table block."""
    table = generate_domains_table()
    return f"{START_MARKER}\n{table}\n{END_MARKER}"


def check_readme_in_sync(readme_path: Path = README_MD) -> tuple[bool, str]:
    """Check if the README.md table is in sync with the generated table."""
    if not readme_path.exists():
        return False, f"{readme_path} does not exist"

    content = readme_path.read_text(encoding="utf-8")
    if START_MARKER not in content or END_MARKER not in content:
        return False, f"Missing markers {START_MARKER} and/or {END_MARKER} in {readme_path.name}"

    pattern = re.compile(rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}", re.DOTALL)
    match = pattern.search(content)
    if not match:
        return False, f"Could not find marker block in {readme_path.name}"

    expected_block = build_block()
    actual_block = match.group(0)
    if actual_block.strip() != expected_block.strip():
        return False, f"Table content in {readme_path.name} differs from generated table"

    return True, "Table is in sync"


def update_readme(readme_path: Path = README_MD) -> bool:
    """Update README.md with the generated supported domains table.

    Returns True if file was changed, False if unchanged.
    """
    if not readme_path.exists():
        raise FileNotFoundError(f"{readme_path} not found")

    content = readme_path.read_text(encoding="utf-8")
    expected_block = build_block()

    if START_MARKER in content and END_MARKER in content:
        pattern = re.compile(rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}", re.DOTALL)
        updated = pattern.sub(expected_block, content)
    else:
        # Replace existing static table under ### Supported Entity Domains & Automations
        static_pattern = re.compile(
            r"(### Supported Entity Domains & Automations\s*\n\s*)"
            r"(\| Domain \| WHO \| Capabilities \|.*?\n)(?=\s*\n---|\s*\n## )",
            re.DOTALL,
        )
        if not static_pattern.search(content):
            raise ValueError(f"Could not find static table to replace in {readme_path.name}")
        replacement = rf"\g<1>{expected_block}\n\n{FOOTER_NOTE}\n"
        updated = static_pattern.sub(replacement, content)

    if updated == content:
        return False

    readme_path.write_text(updated, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if README.md is out of sync")
    parser.add_argument("--dry-run", action="store_true", help="print generated table without modifying files")
    parser.add_argument("--readme", type=Path, default=README_MD, help="path to README.md")
    args = parser.parse_args(argv)

    if args.dry_run:
        print(build_block())
        return 0

    if args.check:
        in_sync, msg = check_readme_in_sync(args.readme)
        if not in_sync:
            print(f"FAILED: {msg}", file=sys.stderr)
            return 1
        print("SUCCESS: Supported Entity Domains table is in sync.")
        return 0

    try:
        changed = update_readme(args.readme)
        if changed:
            print(f"Updated {args.readme.name} Supported Entity Domains table successfully.")
        else:
            print(f"{args.readme.name} Supported Entity Domains table is already up to date.")
        return 0
    except Exception as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
