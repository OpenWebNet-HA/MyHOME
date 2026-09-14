#!/usr/bin/env python3
"""Strip personal data from a plant fixture (tests/fixtures/plants/<plant>/).

A plant fixture is a user's ``myhome.yaml`` plus the integration's diagnostics
download. The test harness only needs the *shape* of the plant - which WHO /
WHERE addresses exist, their platforms and options, and the frames the bus
produced. It does not need room names, family names, LAN addresses, gateway
MACs, config-entry ids or passwords, so this script replaces them:

- device keys and names become ``<platform>_<where>`` / ``<Label> <where>``
  (``light_10`` / ``Light 10``), deterministically, so replayed entity ids are
  predictable (``light.light_10``); the class stays in the key
  (``sensor_51_power``) so a meter's entries keep one device name, and a bus
  interface stays part of the identity (``cover_11i02`` / ``Cover 11I02``);
- IPv4 addresses become ``192.0.2.<n>`` (RFC 5737 documentation range), MACs
  ``00:03:50:00:<issue number>`` (the BTicino OUI is public, the rest is
  synthetic), the config-entry id a synthetic ULID-shaped string, passwords
  ``null``, the Home Assistant time zone ``UTC``.

``myhome.yaml`` is edited line by line: everything that is not one of those
values - quoting, indentation, the file header - stays byte for byte, so a
reviewer sees only what changed. Section comments inside a platform
(``# -- Kitchen --``) become ``# -- group n --``. The diagnostics are parsed,
sanitized as a structure (a numeric password, an escaped quote or single-line
JSON cannot slip through a text pass) and written back with the bus frames one
per line.

Run it on every contributed fixture before committing::

    python scripts/anonymize_plant_fixture.py tests/fixtures/plants/issue_999_f454

It prints the mapping old entity id -> new entity id so tests can be updated,
and the same input always yields the same output. ``--check`` reports personal
data in files or directories instead of rewriting (the pre-commit hook and
``tests/test_fixture_privacy.py`` run it over the whole test tree).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")
YAML_KEY = re.compile(r"^(\s*)([^\s#][^:]*):(\s*)(.*)$")

LABELS = {
    "light": "Light",
    "switch": "Switch",
    "cover": "Cover",
    "climate": "Climate Zone",
    "binary_sensor": "Binary Sensor",
    "sensor": "Sensor",
    "media_player": "Audio Zone",
    "button": "Button",
    "alarm_control_panel": "Alarm Zone",
}
CLASS_KEYS = ("class", "device_class")
NAME_KEYS = ("name", "entity_name")


def slugify(text: str) -> str:
    """Home Assistant's entity-id slug of a name (ASCII letters, digits, ``_``)."""
    import unicodedata

    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_")


def _line_end(line: str) -> str:
    return line[len(line.rstrip("\r\n")):]


class Anonymizer:
    def __init__(self, plant: str) -> None:
        self.plant = plant
        self.ips: dict[str, str] = {}
        self.macs: dict[str, str] = {}
        self.entity_ids: dict[str, str] = {}
        #: (platform, old device key) -> (new device key, new name)
        self.devices: dict[tuple[str, str], tuple[str, str]] = {}

    # ── scalars ──────────────────────────────────────────────────────

    def ip(self, match: re.Match[str]) -> str:
        return self.ips.setdefault(match.group(0), f"192.0.2.{len(self.ips) + 1}")

    def mac(self, match: re.Match[str]) -> str:
        # Plant-specific so fixtures never share a gateway identity: the first number in
        # the plant name (the issue number) fills the last two octets, later MACs count up.
        number = re.search(r"\d+", self.plant)
        base = int((number.group(0) if number else "0")[-4:], 16) + len(self.macs)
        return self.macs.setdefault(match.group(0).lower(), f"00:03:50:00:{base >> 8:02x}:{base & 0xFF:02x}")

    def scrub_text(self, text: str) -> str:
        return MAC.sub(self.mac, IPV4.sub(self.ip, text))

    # ── myhome.yaml ──────────────────────────────────────────────────

    def plan_devices(self, config: dict[str, Any]) -> None:
        """Decide the new key and name of every device from the parsed configuration."""
        for gateway in config.values():
            if not isinstance(gateway, dict):
                continue
            for platform, devices in gateway.items():
                if platform in LABELS and isinstance(devices, dict):
                    self._plan_platform(platform, devices)

    def _plan_platform(self, platform: str, devices: dict[str, Any]) -> None:
        label = LABELS[platform]
        seen: dict[str, int] = {}
        for old_key, cfg in devices.items():
            if not isinstance(cfg, dict):
                continue
            where = str(cfg.get("where", cfg.get("zone", old_key)))
            interface = cfg.get("interface") or cfg.get("bus_interface")
            address = where.replace("#", "_").strip("_") + (f"I{interface}" if interface else "")
            klass = next((str(cfg[k]) for k in CLASS_KEYS if cfg.get(k)), "")
            # The key carries the class (a meter is one power and one energy entry, one
            # device name); the name is the address alone, as Home Assistant adds the
            # class to the entity id itself (sensor.sensor_51_power).
            tag = slugify(address + (f"_{klass}" if klass and platform in ("sensor", "binary_sensor") else ""))
            seen[tag] = seen.get(tag, 0) + 1
            if seen[tag] > 1:
                tag = f"{tag}_{seen[tag]}"
            new_key, new_name = f"{platform}_{tag}", f"{label} {address.replace('_', ' ')}"
            self.devices[(platform, str(old_key))] = (new_key, new_name)
            self.entity_ids[f"{platform}.{slugify(old_key)}"] = f"{platform}.{slugify(new_key)}"
            suffix = f"_{slugify(klass)}" if klass and platform in ("sensor", "binary_sensor") else ""
            for old_name in {cfg.get(k) for k in NAME_KEYS if cfg.get(k)}:
                base = slugify(str(old_name))
                self.entity_ids[f"{platform}.{base}"] = f"{platform}.{slugify(new_name)}"
                if suffix:
                    self.entity_ids[f"{platform}.{base}{suffix}"] = f"{platform}.{slugify(new_name)}{suffix}"

    def yaml_text(self, text: str) -> str:
        """Rewrite device keys, names and addresses in place; every other byte is kept."""
        self.plan_devices(yaml.safe_load(text))
        out: list[str] = []
        platform: str | None = None
        platform_indent = device_indent = -1
        device: tuple[str, str] | None = None
        groups = 0
        for line in text.splitlines(keepends=True):
            match = YAML_KEY.match(line.rstrip("\r\n"))
            if match is None:
                stripped = line.lstrip()
                if platform is not None and stripped.startswith("#") and len(line) - len(stripped) > platform_indent:
                    # Section comments inside a platform name rooms; keep the grouping, not the words
                    groups += 1
                    line = f"{line[: len(line) - len(stripped)]}# ── group {groups} ──{_line_end(line)}"
                out.append(self.scrub_text(line))
                continue
            lead, key, gap, value = match.group(1), match.group(2), match.group(3), match.group(4)
            indent = len(lead)
            if key in LABELS and value == "" and (platform is None or indent <= platform_indent):
                platform, platform_indent, device = key, indent, None
            elif platform is not None and indent > platform_indent and (device is None or indent <= device_indent) and value == "":
                device, device_indent = (platform, key), indent
                if device in self.devices:
                    line = f"{lead}{self.devices[device][0]}:{gap}{_line_end(line)}"
            elif device is not None and indent > device_indent and key in NAME_KEYS and device in self.devices:
                quote = value[0] if value[:1] in "\"'" else ""
                line = f"{lead}{key}:{gap}{quote}{self.devices[device][1]}{quote}{_line_end(line)}"
            elif platform is not None and indent <= platform_indent:
                platform, device = None, None
            out.append(self.scrub_text(line))
        return "".join(out)

    # ── diagnostics ──────────────────────────────────────────────────

    def json_text(self, text: str) -> str:
        """Parse the diagnostics, sanitize the structure, serialize it back.

        The file is a user's download: numeric passwords, escaped quotes,
        single-line JSON are all possible, so nothing here works on the text.
        """
        return dump_json(self.json_data(json.loads(text))) + "\n"

    def json_data(self, diag: Any) -> Any:
        """The sanitized diagnostics structure (a new object; the input is not modified)."""
        entry_id = f"01PLANT{slugify(self.plant).upper().replace('_', '')}".ljust(26, "0")[:26]
        original_id = None
        entry = diag.get("data", {}).get("config_entry") if isinstance(diag, dict) else None
        if isinstance(entry, dict) and isinstance(entry.get("entry_id"), str):
            original_id = entry["entry_id"]  # also keys setup_times

        def walk(value: Any, key: str | None = None) -> Any:
            if isinstance(value, dict):
                if key == "custom_components":
                    # what else the home runs is nobody's business
                    return {k: walk(v, k) for k, v in value.items() if k == "myhome"}
                return {
                    (entry_id if original_id and k == original_id else k): walk(v, k)
                    for k, v in value.items()
                }
            if isinstance(value, list):
                return [walk(v, key) for v in value]
            if key in SECRET_KEYS:
                return None  # whatever the type: "12345", 12345, null
            if key == "entry_id" and isinstance(value, str):
                return entry_id
            if key == "timezone" and isinstance(value, str):
                return "UTC"
            if key == "file_path" and isinstance(value, str):
                return "/config/myhome.yaml"
            if isinstance(value, str):
                if original_id and original_id in value:
                    value = value.replace(original_id, entry_id)
                return self.scrub_text(value)
            return value

        return walk(diag)


SECRET_KEYS = frozenset({"password", "pin", "token", "secret", "UDN", "friendly_name"})


def dump_json(value: Any, indent: int = 0) -> str:
    """``json.dumps(indent=2)``, except that a list of flat objects - the bus frames -
    keeps one object per line, so a fixture stays diffable and greppable."""
    pad, inner = " " * indent, " " * (indent + 2)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = (f"{inner}{json.dumps(str(k))}: {dump_json(v, indent + 2)}" for k, v in value.items())
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(isinstance(v, dict) and not any(isinstance(x, (dict, list)) for x in v.values()) for v in value):
            items = (inner + json.dumps(v, separators=(", ", ": ")) for v in value)
        else:
            items = (inner + dump_json(v, indent + 2) for v in value)
        return "[\n" + ",\n".join(items) + f"\n{pad}]"
    return json.dumps(value)


def anonymize(plant_dir: Path) -> Anonymizer:
    a = Anonymizer(plant_dir.name)
    yaml_path, diag_path = plant_dir / "myhome.yaml", plant_dir / "diagnostic_summary.json"
    yaml_path.write_bytes(a.yaml_text(yaml_path.read_bytes().decode("utf-8")).encode("utf-8"))
    if diag_path.exists():
        diag_path.write_bytes(a.json_text(diag_path.read_bytes().decode("utf-8")).encode("utf-8"))
    return a


PRIVATE_IP = re.compile(r"\b(10\.\d+|172\.(1[6-9]|2\d|3[01])|192\.168)\.\d+\.\d+\b")
LOCAL_PATH = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z]|/home/[a-z][a-z0-9_-]*/|/Users/[A-Za-z][a-z0-9_-]*/")
SECRET = re.compile(
    r'"(password|pin|token|secret)":\s*"(?!\*\*REDACTED)[^"]*\d[^"]*"|^\s*(password|pin|token|secret):\s*["\']?[^"\'\s]*\d',
    re.I | re.M,
)
#: A file may carry deliberate samples of what the check looks for (its own tests)
ALLOW_SAMPLES = "privacy-check: allow-samples"


DATA_SUFFIXES = (".json", ".yaml", ".yml")


def findings(text: str, data_file: bool = True) -> list[str]:
    """What in ``text`` looks like personal data (empty when clean).

    Data files (JSON / YAML) get the full rule set: a diagnostics download or a
    plant configuration pasted into the tree lands there. Source files keep
    placeholder addresses and passwords on purpose, so only the strong signals
    apply to them: a local path with a user name, a real config-entry id.
    """
    found: list[str] = []
    if ALLOW_SAMPLES in text:
        return found
    if data_file and PRIVATE_IP.search(text):
        found.append("LAN address")
    if data_file and any(not m.lower().startswith("00:03:50:00:") for m in MAC.findall(text)):
        found.append("non-synthetic MAC")
    if data_file and SECRET.search(text):
        found.append("secret value")
    if LOCAL_PATH.search(text):
        found.append("local path")
    if any(not u.startswith("01PLANT") for u in re.findall(r"\b01[A-Z0-9]{24}\b", text)):
        found.append("config-entry id")
    return found


def check(paths: list[Path]) -> int:
    """Report personal data in the given files or directories; exit 1 when any is found."""
    files = [
        f for path in paths for f in (path.rglob("*") if path.is_dir() else [path])
        if f.suffix in DATA_SUFFIXES + (".py", ".md", ".js", ".ambr") and "translations" not in f.parts
    ]
    dirty = 0
    for f in sorted(files):
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = findings(text, data_file=f.suffix in DATA_SUFFIXES)
        if hits:
            dirty += 1
            print(f"{f}: {', '.join(hits)}")
    print(f"{len(files)} files checked, {dirty} with personal data")
    return 1 if dirty else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("plant", type=Path, nargs="+", help="fixture directory (tests/fixtures/plants/<plant>), or files/directories with --check")
    parser.add_argument("--mapping", type=Path, help="write the old -> new entity id / address mapping as JSON")
    parser.add_argument("--check", action="store_true", help="report personal data instead of rewriting; exit 1 when any is found")
    args = parser.parse_args(argv)
    if args.check:
        return check(args.plant)
    mapping: dict[str, Any] = {}
    for plant in args.plant:
        a = anonymize(plant)
        mapping[plant.name] = {"entity_ids": a.entity_ids, "ips": a.ips, "macs": a.macs}
        print(f"{plant}: {len(a.devices)} devices, {len(a.ips)} IPs, {len(a.macs)} MACs rewritten")
    if args.mapping:
        args.mapping.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
