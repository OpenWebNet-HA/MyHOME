#!/usr/bin/env python3
"""Update and calibrate the Gateway Profiles table in README.md.

Maintains live, automated documentation of supported gateway profiles and hardware
specifications by cross-referencing custom_components/myhome/const.py (SUPPORTED_GATEWAY_MODELS),
custom_components/myhome/manifest.json (ssdp), and hardware specifications.
Called locally, by scripts/verify_ha_standards.py, and by GitHub Actions CI workflows.
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

README_MD = REPO_ROOT / "README.md"
CONST_PY = REPO_ROOT / "custom_components" / "myhome" / "const.py"
MANIFEST_JSON = REPO_ROOT / "custom_components" / "myhome" / "manifest.json"
STRINGS_JSON = REPO_ROOT / "custom_components" / "myhome" / "strings.json"
CONFIG_FLOW_OPTIONS_PY = REPO_ROOT / "custom_components" / "myhome" / "config_flow_options.py"
CONFIG_FLOW_AUDIO_PY = REPO_ROOT / "custom_components" / "myhome" / "config_flow_audio.py"
CONFIG_FLOW_TOPOLOGY_PY = REPO_ROOT / "custom_components" / "myhome" / "config_flow_topology.py"

START_MARKER = "<!-- GATEWAY_PROFILES_START -->"
END_MARKER = "<!-- GATEWAY_PROFILES_END -->"

OPTIONS_START_MARKER = "<!-- GATEWAY_OPTIONS_START -->"
OPTIONS_END_MARKER = "<!-- GATEWAY_OPTIONS_END -->"

DISCOVERY_START_MARKER = "<!-- GATEWAY_DISCOVERY_START -->"
DISCOVERY_END_MARKER = "<!-- GATEWAY_DISCOVERY_END -->"

ORACLE_JSON = REPO_ROOT / "tests" / "golden" / "firmware_oracle.json"
PRE_WHO16_OWND_PIN = "OWNd==2.0.0b11"
STARTUP_DISCOVERY_WHOS: tuple[int, ...] = (1, 2, 4, 5, 16, 18)

FOOTER_NOTE = (
    "*This table is automatically updated from gateway profile definitions "
    "and hardware specifications.*"
)

# Authoritative gateway metadata ordered by category and generation
GATEWAY_METADATA: list[dict[str, object]] = [
    {
        "model": "**F454**",
        "const_models": ["F454"],
        "ssdp_models": ["F454"],
        "profile_name": "F454",
        "protocol": "OpenWebNet / HMAC",
        "max_workers": "4 workers",
        "delay": "50 ms",
        "upnp": "✅ Port 49153",
        "notes": "Full high-speed multi-session support",
    },
    {
        "model": "**F455**",
        "const_models": ["F455"],
        "ssdp_models": ["F455"],
        "profile_name": "F455",
        "protocol": "OpenWebNet / HMAC",
        "max_workers": "4 workers",
        "delay": "50 ms",
        "upnp": "✅ Port 49153",
        "notes": "Basic gateway (single SCS bus)",
    },
    {
        "model": "**F461**",
        "const_models": ["F461"],
        "ssdp_models": [],
        "profile_name": "F461",
        "protocol": "OpenWebNet / HMAC",
        "max_workers": "4 workers",
        "delay": "50 ms",
        "upnp": "❌ Manual",
        "notes": "Compact DIN Ethernet Web Server",
    },
    {
        "model": "**MH202**",
        "const_models": ["MH202"],
        "ssdp_models": ["MH202"],
        "profile_name": "MH202",
        "protocol": "OpenWebNet / HMAC",
        "max_workers": "2 workers",
        "delay": "100 ms",
        "upnp": "✅ Port 49153",
        "notes": "Modern scenario programmer gateway",
    },
    {
        "model": "**MH201**",
        "const_models": ["MH201"],
        "ssdp_models": ["MH201"],
        "profile_name": "MH201",
        "protocol": "OpenWebNet",
        "max_workers": "1 worker",
        "delay": "100 ms",
        "upnp": "✅ Port 49153",
        "notes": "Second-generation scenario programmer",
    },
    {
        "model": "**MyHomeServer1**",
        "const_models": ["MyHomeServer1"],
        "ssdp_models": ["MyHomeServer1"],
        "profile_name": "MyHomeServer1",
        "protocol": "OpenWebNet / HMAC",
        "max_workers": "4 workers",
        "delay": "20 ms",
        "upnp": "✅ SSDP",
        "notes": "Cloud/local hybrid gateway",
    },
    {
        "model": "**MH200N**",
        "const_models": ["MH200N"],
        "ssdp_models": ["MH200N"],
        "profile_name": "MH200N",
        "protocol": "OpenWebNet",
        "max_workers": "1 worker",
        "delay": "150 ms",
        "upnp": "✅ SSDP",
        "notes": "Second-generation scenario programmer",
    },
    {
        "model": "**MH200** *(Legacy)*",
        "const_models": ["MH200"],
        "ssdp_models": ["MH200"],
        "profile_name": "MH200",
        "protocol": "OpenWebNet",
        "max_workers": "1 worker",
        "delay": "150 ms",
        "upnp": "✅ SSDP",
        "notes": "Strict single-session pacing; watchdog hardened",
    },
    {
        "model": "**H4890 / AM4890**",
        "const_models": ["AM4890", "H4890", "LN4890", "067292", "573958", "078479"],
        "ssdp_models": ["AM4890", "H4890", "LN4890", "LN4890A", "067292", "573958", "078479"],
        "profile_name": "H4890",
        "protocol": "OpenWebNet",
        "max_workers": "1 worker",
        "delay": "50 ms",
        "upnp": "✅ SSDP",
        "notes": '3.5" Touch screen display IP gateway (Axolute / Livinglight)',
    },
    {
        "model": "**F452 / F453 / F453AV**",
        "const_models": ["F453AV", "F453", "F452", "F452V"],
        "ssdp_models": ["F452", "F453", "F453AV"],
        "profile_name": "F453AV",
        "protocol": "OpenWebNet",
        "max_workers": "1 worker",
        "delay": "150 ms",
        "upnp": "✅ Port 49153",
        "notes": "Audio/video & web server gateway",
    },
    {
        "model": "**H4684 / H/L4684**",
        "const_models": ["H4684"],
        "ssdp_models": ["H/L4684", "HL4684"],
        "profile_name": "H4684",
        "protocol": "OpenWebNet",
        "max_workers": "1 worker",
        "delay": "50 ms",
        "upnp": "✅ SSDP",
        "notes": "Colour touch screen IP gateway (Axolute / LivingLight)",
    },
]

# Startup discovery rows. With an importable OWNd the WHOs a row advertises are derived from
# the installed profile (``profile.supports_who``) over STARTUP_DISCOVERY_WHOS, exactly as the
# runtime gate in gateway.py decides what to send, so the table follows an OWNd profile change
# at the pin bump. ``pinned_whos`` is the row the pinned OWNd (the ``OWNd==`` pin in
# manifest.json) yields: it is used only when OWNd is not importable (the quality-scale,
# test-coverage and docs CI jobs run the docs check without it), so the generated table is the
# same with and without OWNd. At a pin bump that changes a profile, update ``pinned_whos``
# together with ``sync_documentation.py --update``; ``tests/test_gateway_profiles.py`` pins the
# fallback to the committed table. ``sweep_omits`` lists the general requests of those WHOs the
# startup sweep leaves out anyway because the gateway firmware drops them before the bus
# (GENERAL_ZONE_REQUEST_REFUSED / SOUND_STATUS_REQUEST_REFUSED in custom_components/myhome/gateway.py;
# tests/test_startup_requests_per_firmware.py checks these lists against the sweep itself, and
# verify_profiles_against_oracle checks them against ``pinned_whos``); a request of a WHO the row
# does not advertise is not an omission (nothing is sent for that WHO at all) and is left out of
# the generated row. ``sweep_replaces`` lists what the sweep sends in place of an omitted general
# request (``_heating_startup_requests`` in gateway.py; tests/test_review_955_third_pass_2026_11_18.py
# checks the lists against the sweep itself), so the column does not read as "no WHO 4 request".
# ``notes`` describes the row; where a profile may list or drop WHO 16 between
# OWNd releases, ``notes_without_who16`` is used instead when the row does not carry WHO 16.
GENERAL_ZONE_STATUS = "*#4*0##"
SOUND_STATUS = "*#16*0*5##"
CENTRAL_UNIT_STATUS = "*#4*#0##"
ALL_ACTUATORS_STATUS = "*#4*0#0*20##"
GATEWAY_DISCOVERY_METADATA: list[dict[str, object]] = [
    {
        "model": "**MH202**",
        "profile_name": "MH202",
        "pinned_whos": [1, 2, 4, 5, 16, 18],
        "sweep_omits": [GENERAL_ZONE_STATUS, SOUND_STATUS],
        "notes": "Burglar Alarm (`*#5*0##`) and Energy (`*#18*51*...`) queries. WHO 4 and WHO 16 are advertised, but the `010024` firmware drops `*#4*0##` and `*#16*0*5##` (own-firmware-oracle), so the sweep leaves both out; climate zones are polled with `*#4*<WHERE>##` (#866).",
    },
    {
        "model": "**H4890 / AM4890**",
        "profile_name": "H4890",
        "pinned_whos": [1, 2, 4, 5, 16, 18],
        "sweep_omits": [],
        "notes": "Supports Burglar Alarm, Sound (`*#16*0*5##`), and Energy meter 51 queries.",
    },
    {
        "model": "**F454**",
        "profile_name": "F454",
        "pinned_whos": [1, 2, 4, 16, 18],
        "sweep_omits": [GENERAL_ZONE_STATUS],
        "sweep_replaces": [ALL_ACTUATORS_STATUS],
        "notes": "Full lighting, cover, sound (`bt_difson` firmware-verified), and energy meter 51 queries. The `020051` firmware drops `*#4*0##` (own-firmware-oracle), so the sweep asks every actuator in its place (`*#4*0#0*20##`); zones are polled with `*#4*<WHERE>##` (#629, #866). Alarm query omitted.",
    },
    {
        "model": "**F452 / F453 / F453AV**",
        "profile_name": "F453AV",
        "pinned_whos": [1, 2, 4, 16, 18],
        "sweep_omits": [],
        "notes": "Lighting, cover, climate, sound (`bt_difson` firmware-verified on F453/F453AV), and energy meter 51 queries. Alarm query omitted.",
    },
    {
        "model": "**F461**",
        "profile_name": "F461",
        "pinned_whos": [1, 2, 4, 18],
        "sweep_omits": [GENERAL_ZONE_STATUS],
        "sweep_replaces": [CENTRAL_UNIT_STATUS, ALL_ACTUATORS_STATUS],
        "notes": "Lighting, cover, and energy meter 51 queries. Sound is WHO 22 only (no `bt_difson`; every WHO 16 frame refused, own-firmware-oracle `020011`, confirmed live on 2.0.11). The firmware also drops `*#4*0##`, so the sweep asks the central unit's status (`*#4*#0##`) and every actuator (`*#4*0#0*20##`) in its place; zones are polled with `*#4*<WHERE>##`. Alarm query omitted.",
        "notes_without_who16": "Lighting, cover, and energy meter 51 queries. Sound is WHO 22 only (no `bt_difson`; every WHO 16 frame refused, own-firmware-oracle `020011`, confirmed live on 2.0.11). The firmware also drops `*#4*0##`, so the sweep asks the central unit's status (`*#4*#0##`) and every actuator (`*#4*0#0*20##`) in its place; zones are polled with `*#4*<WHERE>##`. Alarm query omitted.",
    },
    {
        "model": "**F455**",
        "profile_name": "F455",
        "pinned_whos": [1, 2, 4, 18],
        "sweep_omits": [GENERAL_ZONE_STATUS],
        "sweep_replaces": [ALL_ACTUATORS_STATUS],
        "notes": "Entry-level gateway: lighting, cover, and energy meter 51 queries. Not in own-firmware-oracle; a physical F455 1.0.76 ACKed `*#4*0##` after 9.1 s with no zone frame although its zones answer single reads (`tests/fixtures/traces/issue_466`), so the sweep asks every actuator in its place (`*#4*0#0*20##`); a climate zone that stays silent is asked read by read after the sweep (#466, #932). Sound (WHO 16/22) and Alarm (WHO 5) omitted per installer manual.",
    },
    {
        "model": "**MyHomeServer1**",
        "profile_name": "MyHomeServer1",
        "pinned_whos": [1, 2, 4, 18],
        "sweep_omits": [GENERAL_ZONE_STATUS],
        "sweep_replaces": [CENTRAL_UNIT_STATUS, ALL_ACTUATORS_STATUS],
        "notes": "Lighting, cover, and energy meter 51 queries. Sound is WHO 22 only (no `bt_difson`; every WHO 16 frame refused, own-firmware-oracle `028206`, confirmed live on 3.87.13). The firmware also drops `*#4*0##`, so the sweep asks the central unit's status (`*#4*#0##`) and every actuator (`*#4*0#0*20##`) in its place; zones are polled with `*#4*<WHERE>##`. Alarm query omitted.",
        "notes_without_who16": "Lighting, cover, and energy meter 51 queries. Sound is WHO 22 only (no `bt_difson`; every WHO 16 frame refused, own-firmware-oracle `028206`, confirmed live on 3.87.13). The firmware also drops `*#4*0##`, so the sweep asks the central unit's status (`*#4*#0##`) and every actuator (`*#4*0#0*20##`) in its place; zones are polled with `*#4*<WHERE>##`. Alarm query omitted.",
    },
    {
        "model": "**MH201**",
        "profile_name": "MH201",
        "pinned_whos": [1, 2, 4, 16, 18],
        "sweep_omits": [GENERAL_ZONE_STATUS],
        "notes": "Lighting, cover, climate, sound (unmeasured class default), and energy meter 51 queries. Not in own-firmware-oracle; a physical MH201 3.3.6 returned no zone frame to `*#4*0##` although its zones report (`tests/fixtures/traces/issue_466`), so the sweep leaves it out and each climate zone asks `*#4*<WHERE>*0##` itself. Alarm query omitted.",
    },
    {
        "model": "**MH200** *(Legacy)*",
        "profile_name": "MH200",
        "pinned_whos": [1, 2, 4, 16],
        "sweep_omits": [GENERAL_ZONE_STATUS],
        "notes": "Lighting, cover, climate, and sound queries (`bt_difson` hardware and firmware-verified). The `020102` firmware drops `*#4*0##` (own-firmware-oracle), so its climate zones ask `*#4*<WHERE>*0##` themselves. Alarm and energy queries omitted.",
    },
    {
        "model": "**MH200N**",
        "profile_name": "MH200N",
        "pinned_whos": [1, 2, 4, 16, 18],
        "sweep_omits": [],
        "notes": "Lighting, cover, climate, sound (`bt_difson` hardware and firmware-verified), and energy meter 51 queries (`bt_supervisione` in the `010108` image; a physical MH200N 1.1.8 answers `*#18*51*51##`, `tests/fixtures/traces/issue_466`). The `010108` firmware forwards `*#4*0##` (own-firmware-oracle), unlike the MH200. Alarm query omitted.",
    },
    {
        "model": "**H4684 / H/L4684**",
        "profile_name": "H4684",
        "pinned_whos": [1, 2, 4, 16, 18],
        "sweep_omits": [GENERAL_ZONE_STATUS],
        "notes": "Lighting, cover, climate, sound, and energy meter 51 queries (`bt_multi` WHO 22 also enabled on firmware >= `6.0.0`). Both `020054` (own-firmware-oracle) and live `6.0.11` (`tests/fixtures/traces/issue_466`) drop `*#4*0##`, so each climate zone asks `*#4*<WHERE>*0##` itself; the SSDP spellings `H/L4684` and `HL4684` resolve to `H4684Profile`. Alarm query omitted.",
    },
]


def _frame_who(frame: str) -> int:
    """Return the WHO of a general status request such as ``*#4*0##``."""
    return int(frame.split("*")[1].lstrip("#"))


def sweep_whos(row: dict[str, object]) -> list[int]:
    """Return the WHOs whose general request the startup sweep actually sends for ``row``."""
    whos = row.get("queried_whos")
    omitted = {_frame_who(str(frame)) for frame in cast_list(row.get("sweep_omits"))}
    return [w for w in whos if w not in omitted] if isinstance(whos, list) else []


def cast_list(value: object) -> list[object]:
    """Return ``value`` as a list (an absent ``sweep_omits`` means nothing is omitted)."""
    return list(value) if isinstance(value, (list, tuple)) else []


def pinned_whos(row: dict[str, object]) -> list[int] | None:
    """Return ``row["pinned_whos"]`` as WHO numbers, or ``None`` when it is not a list of them."""
    pinned = row.get("pinned_whos")
    if not isinstance(pinned, (list, tuple)) or not all(isinstance(w, int) and not isinstance(w, bool) for w in pinned):
        return None
    return list(pinned)


def in_sweep_order(whos: list[int]) -> list[int]:
    """Return ``whos`` in the order of STARTUP_DISCOVERY_WHOS, without repeats (what the sweep sends)."""
    return [w for w in STARTUP_DISCOVERY_WHOS if w in whos]


def extract_gateway_models_from_const(const_path: Path = CONST_PY) -> list[str]:
    """Parse SUPPORTED_GATEWAY_MODELS from custom_components/myhome/const.py using AST."""
    if not const_path.exists():
        return []

    tree = ast.parse(const_path.read_text(encoding="utf-8"))
    models: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "SUPPORTED_GATEWAY_MODELS" for t in node.targets)
        ) or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "SUPPORTED_GATEWAY_MODELS"
        ):
            val_node = node.value
            if isinstance(val_node, (ast.Tuple, ast.List)):
                for elt in val_node.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        models.append(elt.value)
    return models


def extract_ssdp_models_from_manifest(manifest_path: Path = MANIFEST_JSON) -> set[str]:
    """Extract model names configured for SSDP discovery from manifest.json."""
    if not manifest_path.exists():
        return set()

    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        ssdp_entries = manifest_data.get("ssdp", [])
        return {
            entry["modelName"]
            for entry in ssdp_entries
            if isinstance(entry, dict) and "modelName" in entry
        }
    except Exception:
        return set()


def manifest_pins_pre_who16_ownd(manifest_path: Path = MANIFEST_JSON) -> bool:
    """Return True while manifest.json still pins the pre-OWNd#142 release (OWNd==2.0.0b11)."""
    if not manifest_path.exists():
        return False

    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        reqs = manifest_data.get("requirements", [])
        return isinstance(reqs, list) and PRE_WHO16_OWND_PIN in reqs
    except Exception:
        return False


def calibrate_metadata_with_ownd(
    manifest_path: Path = MANIFEST_JSON,
    metadata: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Dynamically calibrate gateway metadata against OWNd.profiles and manifest.json without mutating global state."""
    try:
        from OWNd.profiles import get_gateway_profile
    except ImportError:
        get_gateway_profile = None

    ssdp_models = extract_ssdp_models_from_manifest(manifest_path)
    calibrated = copy.deepcopy(GATEWAY_METADATA if metadata is None else metadata)

    for gw in calibrated:
        prof_name = gw.get("profile_name")
        if prof_name and get_gateway_profile is not None:
            prof = get_gateway_profile(str(prof_name))
            if prof is not None:
                workers = int(prof.max_command_sessions)
                gw["max_workers"] = f"{workers} {'workers' if workers > 1 else 'worker'}"
                gw["delay"] = f"{int(round(prof.command_queue_delay * 1000))} ms"
                gw["protocol"] = (
                    "OpenWebNet / HMAC"
                    if getattr(prof, "supports_hmac", False)
                    else "OpenWebNet"
                )

        # Cross-reference UPnP / SSDP discovery
        explicit_ssdp = gw.get("ssdp_models", [])
        const_models = gw.get("const_models", [])
        has_ssdp = any(m in ssdp_models for m in explicit_ssdp) or any(
            m in ssdp_models for m in const_models
        )

        if has_ssdp:
            if "Port 49153" in str(gw.get("upnp", "")):
                gw["upnp"] = "✅ Port 49153"
            else:
                gw["upnp"] = "✅ SSDP"
        else:
            gw["upnp"] = "❌ Manual"

    return calibrated


def generate_gateway_profiles_table(
    const_path: Path = CONST_PY,
    manifest_path: Path = MANIFEST_JSON,
) -> str:
    """Generate the markdown table for supported gateway profiles."""
    metadata = calibrate_metadata_with_ownd(manifest_path)
    if const_path.exists():
        supported_models = extract_gateway_models_from_const(const_path)
        # Exclude generic fallback placeholder
        concrete_models = [m for m in supported_models if m.lower() != "generic"]

        # Verify all concrete models in const.py are mapped to a gateway row
        mapped_const_models: set[str] = set()
        for gw in metadata:
            const_list = gw.get("const_models", [])
            if isinstance(const_list, list):
                mapped_const_models.update(const_list)

        unmapped = [m for m in concrete_models if m not in mapped_const_models]
        if unmapped:
            raise ValueError(f"Gateway models defined in const.py missing from GATEWAY_METADATA: {unmapped}")

    lines = [
        "| Gateway Model | Protocol Support | Max Command Workers | Inter-Frame Delay | UPnP Discovery | Notes |",
        "|---|---|---|---|---|---|",
    ]

    for gw in metadata:
        model = gw["model"]
        protocol = gw["protocol"]
        max_workers = gw["max_workers"]
        delay = gw["delay"]
        upnp = gw["upnp"]
        notes = gw["notes"]
        lines.append(f"| {model} | {protocol} | {max_workers} | {delay} | {upnp} | {notes} |")

    return "\n".join(lines)


def build_block(
    const_path: Path = CONST_PY,
    manifest_path: Path = MANIFEST_JSON,
) -> str:
    """Return the complete marker-wrapped table block."""
    table = generate_gateway_profiles_table(const_path, manifest_path)
    return f"{START_MARKER}\n{table}\n{END_MARKER}"

# Authoritative Options Flow metadata cross-verified against config_flow_options.py,
# config_flow_audio.py, config_flow_topology.py, and strings.json["options"]["step"]["user"]["data"].
GATEWAY_OPTIONS_SPECS: list[dict[str, object]] = [
    {
        "keys": ("command_worker_count",),
        "option": "**Command Worker Concurrency**",
        "key_cell": "`command_worker_count`",
        "selector": "Integer",
        "default": "`1` (`2` on F455 and MyHomeServer1, via `command_session_default`)",
        "limits": "Range 1–4 capped by model (`1` on single-session models, `2` on MH202, `4` on F454/F455/F461/MyHomeServer1; `1–10` on Generic)",
        "description": "Number of concurrent asynchronous command sessions (`*99*0##`) dispatched to the gateway.",
    },
    {
        "keys": ("transition_mode",),
        "option": "**Dimmer Transition Mode**",
        "key_cell": "`transition_mode`",
        "selector": "Select",
        "default": "`software_stepped`",
        "limits": "`software_stepped`, `native`, `auto`",
        "description": "Home Assistant software-stepped fade interpolation vs native hardware speed parameter (`auto` aliases `software_stepped`).",
    },
    {
        "keys": ("generate_events",),
        "option": "**Event Bus Broadcasting**",
        "key_cell": "`generate_events`",
        "selector": "Boolean",
        "default": "`False`",
        "limits": "All gateways",
        "description": "Emits raw OpenWebNet bus frames onto the Home Assistant event bus as `myhome_message_event`.",
    },
    {
        "keys": ("broadcast_resync",),
        "option": "**Broadcast Re-sync**",
        "key_cell": "`broadcast_resync`",
        "selector": "Boolean",
        "default": "`True`",
        "limits": "All gateways",
        "description": "Sweeps group (`#G`), area, and general (`WHERE = 0`) light addresses for status after a debounced silence window.",
    },
    {
        "keys": ("address",),
        "option": "**Gateway Host Address**",
        "key_cell": "`address` *(writes `entry.data[\"host\"]`)*",
        "selector": "IPv4 String",
        "default": "Current `host`",
        "limits": "Valid IPv4",
        "description": "In-place update of the gateway IPv4 address (`host`) without deleting the config entry.",
    },
    {
        "keys": ("password",),
        "option": "**Gateway Password**",
        "key_cell": "`password` *(writes `entry.data[\"password\"]`)*",
        "selector": "String",
        "default": "Current `password`",
        "limits": "OPEN numeric / SHA-256 HMAC",
        "description": "In-place update of the OpenWebNet password without deleting the config entry.",
    },
    {
        "keys": ("name",),
        "option": "**Gateway Hardware Model**",
        "key_cell": "`name` *(writes `entry.data[\"name\"]` / model)*",
        "selector": "Select",
        "default": "Current model",
        "limits": "`SUPPORTED_GATEWAY_MODELS`",
        "description": "In-place override of the gateway hardware model and active OWNd profile (`model_source = \"manual\"`).",
    },
    {
        "keys": ("ignored_addresses",),
        "option": "**Ignored Bus Addresses**",
        "key_cell": "`ignored_addresses`",
        "selector": "Multiline Text",
        "default": "`[]`",
        "limits": "All gateways",
        "description": "Newline- or comma-separated `WHO/WHERE` addresses (e.g. `1/74`, `1/74#4#01`) silenced from discovery, entity creation, and fault tracking.",
    },
    {
        "keys": ("standalone_zones",),
        "option": "**Standalone Thermostats**",
        "key_cell": "`standalone_zones`",
        "selector": "Text",
        "default": "`[]`",
        "limits": "Zones `1`..`99` (local bus)",
        "description": "Comma-separated thermostat zones (e.g. `2, 7`) that are not managed by the central unit of a plant that has one: written as `*#4*Z*#14*...` instead of `*#4*#Z*#14*...`. Empty: found from the bus (see [Climate](climate.md)). Same as `standalone: true` in `myhome.yaml`. The emulated firmware of F453, F453AV, H4684, MH200 and MH200N refuses that direct form (own-firmware-oracle `trace-corpus.tsv:55`; not yet seen on a real gateway): a refused write fails the service call.",
    },
    {
        "keys": ("bus_topology",),
        "option": "**Bus Topology**",
        "key_cell": "`bus_topology`",
        "selector": "Select",
        "default": "`standalone`",
        "limits": "`standalone`, `shared` *(multi-gateway setups)*",
        "description": "Declares whether this gateway operates on an independent SCS bus (`standalone`) or shares a physical SCS bus with another configured gateway (`shared`).",
    },
    {
        "keys": ("gateway_role",),
        "option": "**Gateway Role**",
        "key_cell": "`gateway_role`",
        "selector": "Select",
        "default": "`primary`",
        "limits": "`primary`, `secondary`, `standby` *(shared bus)*",
        "description": "Shared-bus coordination role: `primary` owns general bus discovery, `secondary` handles delegated `WHO` subsystems, and `standby` stays passive for hot failover.",
    },
    {
        "keys": ("primary_gateway",),
        "option": "**Primary Gateway**",
        "key_cell": "`primary_gateway`",
        "selector": "Select",
        "default": "First peer gateway",
        "limits": "Configured non-follower gateways",
        "description": "Target primary gateway MAC address paired with a `secondary` or `standby` follower on a shared SCS bus.",
    },
    {
        "keys": ("delegated_whos",),
        "option": "**Delegated Subsystems**",
        "key_cell": "`delegated_whos`",
        "selector": "Multi-Select",
        "default": "Auto-inferred (`[]`)",
        "limits": "Delegatable `WHO` codes (`secondary` role)",
        "description": "OpenWebNet `WHO` subsystems delegated to a `secondary` gateway on a shared bus (for example `WHO = 6` / `WHO = 8` door entry or `WHO = 16` audio).",
    },
    {
        "keys": ("auto_join_streaming",),
        "option": "**Auto-Join Streaming Groups**",
        "key_cell": "`auto_join_streaming`",
        "selector": "Boolean",
        "default": "`True`",
        "limits": "WHO 16 audio zones",
        "description": "Automatically joins an active streaming decoder group when turning on a room amplifier or adjusting volume from a physical wall control.",
    },
    {
        "keys": ("tuner_pictures",),
        "option": "**FM Tuner Station Pictures**",
        "key_cell": "`tuner_pictures`",
        "selector": "Multiline Text",
        "default": "`\"\"`",
        "limits": "`<MHz>=<URL>` lines",
        "description": "Custom station artwork mapping (`MHz=URL` per line, e.g. `96.2=/local/radio/one.png`) displayed while an F500/F500N tuner is tuned to that frequency.",
    },
    {
        "keys": ("source_1_name", "source_2_name", "source_3_name", "source_4_name"),
        "option": "**Audio Matrix Source Names**",
        "key_cell": "`source_1_name`..`source_4_name`",
        "selector": "Text",
        "default": "`\"\"`",
        "limits": "4 matrix inputs (`S1`–`S4`)",
        "description": "Friendly labels for physical sound sources wired to F441/F441M matrix inputs `S1`–`S4` (blank hides unconfigured inputs from source selection).",
    },
    {
        "keys": ("source_1_tuner", "source_2_tuner", "source_3_tuner", "source_4_tuner"),
        "option": "**Audio Source FM Tuner Flag**",
        "key_cell": "`source_1_tuner`..`source_4_tuner`",
        "selector": "Boolean",
        "default": "`False`",
        "limits": "4 matrix inputs (`S1`–`S4`)",
        "description": "Declares whether matrix input `S1`–`S4` is an F500/F500N RDS FM tuner, creating its radio `media_player`, frequency `number`, and seek `button` entities.",
    },
    {
        "keys": tuple(f"default_source_env_{i}" for i in range(1, 10)),
        "option": "**Audio Environment Default Source**",
        "key_cell": "`default_source_env_1`..`default_source_env_9` *(stored in `source_defaults`)*",
        "selector": "Select",
        "default": "`none`",
        "limits": "Active audio environments `1`–`9` (`none`, `1`–`4`)",
        "description": "Per-environment default matrix input (`S1`–`S4`) selected when turning on a two-digit room amplifier (`EA`) from Home Assistant (`none` leaves routing unchanged).",
    },
    {
        "keys": ("decoder_1_entity", "decoder_2_entity", "decoder_3_entity", "decoder_4_entity"),
        "option": "**Streaming Decoder Entity**",
        "key_cell": "`decoder_1_entity`..`decoder_4_entity`",
        "selector": "Entity (`media_player`)",
        "default": "`\"\"`",
        "limits": "4 decoder slots (excludes MyHOME & MA clones)",
        "description": "Upstream hardware `media_player` entity (e.g. Squeezelite, WiiM, Cambridge Audio) whose analog line-out feeds one of the matrix inputs.",
    },
    {
        "keys": ("decoder_1_source", "decoder_2_source", "decoder_3_source", "decoder_4_source"),
        "option": "**Streaming Decoder Matrix Input**",
        "key_cell": "`decoder_1_source`..`decoder_4_source`",
        "selector": "Select",
        "default": "Slot index (`1`–`4`)",
        "limits": "Unique matrix input `1`–`4` per active decoder",
        "description": "Physical F441/F441M matrix source input (`S1`–`S4`) wired to each streaming decoder's line-out.",
    },
    {
        "keys": ("decoder_1_pre_gain", "decoder_2_pre_gain", "decoder_3_pre_gain", "decoder_4_pre_gain"),
        "option": "**Streaming Decoder Pre-Gain**",
        "key_cell": "`decoder_1_pre_gain`..`decoder_4_pre_gain`",
        "selector": "Integer (`%`)",
        "default": "`0`",
        "limits": "`0–100%` integer (`step: 1`)",
        "description": "Fixed line-level feed trim (`0–100%` integer: `0%` leaves upstream volume untouched; `1–100%` sets upstream volume to `min(1.0, 0.75 + pre_gain / 100)`).",
    },
    {
        "keys": ("decoder_1_companion", "decoder_2_companion", "decoder_3_companion", "decoder_4_companion"),
        "option": "**Streaming Decoder Companion**",
        "key_cell": "`decoder_1_companion`..`decoder_4_companion`",
        "selector": "Entity (`media_player`)",
        "default": "`\"\"` *(auto-detect)*",
        "limits": "4 decoder slots (optional companion player)",
        "description": "Optional companion `media_player` entity receiving stream URLs (`play_media`) on behalf of the hardware decoder when left blank for auto-detection.",
    },
]


def extract_const_scalars(const_path: Path = CONST_PY) -> dict[str, str | int]:
    """Parse top-level string and integer constants from const.py using stdlib AST."""
    scalars: dict[str, str | int] = {
        # Standard Home Assistant constants imported in config_flow_options.py
        "CONF_HOST": "host",
        "CONF_NAME": "name",
        "CONF_PASSWORD": "password",
    }
    if not const_path.exists():
        raise FileNotFoundError(f"{const_path} not found")

    tree = ast.parse(const_path.read_text(encoding="utf-8"), filename=str(const_path))
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            val = stmt.value
            if isinstance(val, ast.Constant) and isinstance(val.value, (str, int)) and not isinstance(val.value, bool):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        scalars[target.id] = val.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            val = stmt.value
            if isinstance(val, ast.Constant) and isinstance(val.value, (str, int)) and not isinstance(val.value, bool):
                scalars[stmt.target.id] = val.value
    return scalars


def _eval_scalar_expr(expr: ast.AST, env_vars: dict[str, str | int]) -> str | int | None:
    """Evaluate a simple constant/name/format/addition AST expression against ``env_vars``."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, (str, int)) and not isinstance(expr.value, bool):
        return expr.value
    if isinstance(expr, ast.Name) and expr.id in env_vars:
        return env_vars[expr.id]
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        left = _eval_scalar_expr(expr.left, env_vars)
        right = _eval_scalar_expr(expr.right, env_vars)
        if isinstance(left, int) and isinstance(right, int):
            return left + right
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr == "format"
        and len(expr.args) == 1
    ):
        tmpl = _eval_scalar_expr(expr.func.value, env_vars)
        arg = _eval_scalar_expr(expr.args[0], env_vars)
        if isinstance(tmpl, str) and arg is not None:
            return tmpl.format(arg)
    return None


def _unwrap_vol_key_expr(expr: ast.AST, env_vars: dict[str, str | int]) -> str | None:
    """Resolve a ``Required(<key>, ...)`` / ``vol.Optional(<key>, ...)`` or literal schema key."""
    target = expr
    if isinstance(expr, ast.Call):
        fn = expr.func
        fn_name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
        if fn_name in ("Required", "Optional") and expr.args:
            target = expr.args[0]
    val = _eval_scalar_expr(target, env_vars)
    return val if isinstance(val, str) else None


def _collect_schema_keys_from_stmts(
    stmts: list[ast.stmt],
    env_vars: dict[str, str | int],
    found: set[str],
) -> None:
    """Walk function body statements to collect all ``schema_dict`` Voluptuous keys."""
    for stmt in stmts:
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            value = stmt.value
            if value is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "schema_dict" and isinstance(value, ast.Dict):
                    for k in value.keys:
                        if k is not None:
                            resolved = _unwrap_vol_key_expr(k, env_vars)
                            if resolved is not None:
                                found.add(resolved)
                elif (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "schema_dict"
                ):
                    resolved = _unwrap_vol_key_expr(target.slice, env_vars)
                    if resolved is not None:
                        found.add(resolved)
                elif isinstance(target, ast.Name):
                    eval_val = _eval_scalar_expr(value, env_vars)
                    if eval_val is not None:
                        env_vars[target.id] = eval_val
        elif isinstance(stmt, ast.For) and isinstance(stmt.target, ast.Name):
            loop_var = stmt.target.id
            iter_vals: list[str | int] | None = None
            if (
                isinstance(stmt.iter, ast.Call)
                and isinstance(stmt.iter.func, ast.Name)
                and stmt.iter.func.id == "range"
                and len(stmt.iter.args) == 2
            ):
                start = _eval_scalar_expr(stmt.iter.args[0], env_vars)
                stop = _eval_scalar_expr(stmt.iter.args[1], env_vars)
                if isinstance(start, int) and isinstance(stop, int):
                    iter_vals = list(range(start, stop))
            elif (
                isinstance(stmt.iter, ast.Call)
                and isinstance(stmt.iter.func, ast.Attribute)
                and stmt.iter.func.attr == "_audio_environments"
            ):
                # Two-digit amplifier environments 1..9 (environment "0" is excluded)
                iter_vals = [str(i) for i in range(1, 10)]
            if iter_vals is not None:
                for item in iter_vals:
                    sub_env = dict(env_vars)
                    sub_env[loop_var] = item
                    _collect_schema_keys_from_stmts(stmt.body, sub_env, found)
        elif isinstance(stmt, ast.If):
            _collect_schema_keys_from_stmts(stmt.body, dict(env_vars), found)
            _collect_schema_keys_from_stmts(stmt.orelse, dict(env_vars), found)


def extract_options_flow_keys(
    const_path: Path = CONST_PY,
    options_path: Path = CONFIG_FLOW_OPTIONS_PY,
    audio_path: Path = CONFIG_FLOW_AUDIO_PY,
    topology_path: Path = CONFIG_FLOW_TOPOLOGY_PY,
) -> set[str]:
    """Introspect all Options Flow form keys via stdlib AST across options, audio, and topology mixins."""
    base_env = extract_const_scalars(const_path)
    found: set[str] = set()
    targets: tuple[tuple[Path, str], ...] = (
        (options_path, "async_step_user"),
        (audio_path, "_append_audio_schema"),
        (topology_path, "_append_topology_schema"),
    )
    for file_path, fn_name in targets:
        if not file_path.exists():
            raise FileNotFoundError(f"{file_path} not found")
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        matched_fn = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
                matched_fn = True
                _collect_schema_keys_from_stmts(node.body, dict(base_env), found)
        if not matched_fn:
            raise ValueError(f"Expected function '{fn_name}' not found in {file_path}")
    return found


def extract_strings_options_keys(strings_path: Path = STRINGS_JSON) -> set[str]:
    """Extract ``options.step.user.data`` keys from strings.json."""
    if not strings_path.exists():
        raise FileNotFoundError(f"{strings_path} not found")
    payload = json.loads(strings_path.read_text(encoding="utf-8"))
    data = payload.get("options", {}).get("step", {}).get("user", {}).get("data")
    if not isinstance(data, dict):
        raise ValueError(f"Missing 'options.step.user.data' mapping in {strings_path}")
    return set(data.keys())


def extract_strings_options_description_keys(strings_path: Path = STRINGS_JSON) -> set[str]:
    """Extract ``options.step.user.data_description`` keys from strings.json."""
    if not strings_path.exists():
        raise FileNotFoundError(f"{strings_path} not found")
    payload = json.loads(strings_path.read_text(encoding="utf-8"))
    desc = payload.get("options", {}).get("step", {}).get("user", {}).get("data_description", {})
    if not isinstance(desc, dict):
        raise ValueError(f"Invalid 'options.step.user.data_description' mapping in {strings_path}")
    return set(desc.keys())


def generate_gateway_options_table(
    *,
    const_path: Path = CONST_PY,
    options_path: Path = CONFIG_FLOW_OPTIONS_PY,
    audio_path: Path = CONFIG_FLOW_AUDIO_PY,
    topology_path: Path = CONFIG_FLOW_TOPOLOGY_PY,
    strings_path: Path = STRINGS_JSON,
    specs: list[dict[str, object]] | None = None,
) -> str:
    """Generate the markdown table for Gateway Runtime Options and cross-verify 1-to-1 key parity."""
    active_specs = GATEWAY_OPTIONS_SPECS if specs is None else specs
    flow_keys = extract_options_flow_keys(
        const_path=const_path,
        options_path=options_path,
        audio_path=audio_path,
        topology_path=topology_path,
    )
    strings_keys = extract_strings_options_keys(strings_path)
    desc_keys = extract_strings_options_description_keys(strings_path)

    missing_in_strings = sorted(flow_keys - strings_keys)
    extra_in_strings = sorted(strings_keys - flow_keys)
    if missing_in_strings or extra_in_strings:
        raise ValueError(
            "Options Flow schema keys disagree with strings.json['options']['step']['user']['data']: "
            f"missing_in_strings={missing_in_strings}, extra_in_strings={extra_in_strings}"
        )

    orphan_desc_keys = sorted(desc_keys - flow_keys)
    if orphan_desc_keys:
        raise ValueError(
            "Orphaned keys in strings.json['options']['step']['user']['data_description'] "
            f"not present in Options Flow schema: {orphan_desc_keys}"
        )

    documented_keys: set[str] = set()
    duplicate_doc_keys: list[str] = []
    for spec in active_specs:
        raw_keys = spec.get("keys", ())
        if not isinstance(raw_keys, (list, tuple)) or not raw_keys:
            raise ValueError(f"Invalid 'keys' in GATEWAY_OPTIONS_SPECS entry: {spec!r}")
        for k in raw_keys:
            key_str = str(k)
            if key_str in documented_keys and key_str not in duplicate_doc_keys:
                duplicate_doc_keys.append(key_str)
            documented_keys.add(key_str)

    if duplicate_doc_keys:
        raise ValueError(f"Duplicate keys in GATEWAY_OPTIONS_SPECS: {duplicate_doc_keys}")

    missing_in_docs = sorted(flow_keys - documented_keys)
    extra_in_docs = sorted(documented_keys - flow_keys)
    if missing_in_docs or extra_in_docs:
        raise ValueError(
            "GATEWAY_OPTIONS_SPECS keys disagree with Options Flow schema: "
            f"missing_in_docs={missing_in_docs}, extra_in_docs={extra_in_docs}"
        )

    lines = [
        "| Option | Key | Selector / Type | Default | Session / Model Limits | Description |",
        "| :--- | :--- | :---: | :---: | :--- | :--- |",
    ]
    for spec in active_specs:
        lines.append(
            f"| {spec['option']} | {spec['key_cell']} | {spec['selector']} | "
            f"{spec['default']} | {spec['limits']} | {spec['description']} |"
        )
    return "\n".join(lines)


def build_options_block() -> str:
    """Return the complete marker-wrapped options table block."""
    table = generate_gateway_options_table()
    return f"{OPTIONS_START_MARKER}\n{table}\n{OPTIONS_END_MARKER}"


def discovery_whos_for(profile: object) -> list[int]:
    """The startup discovery WHOs the runtime sends to a gateway with ``profile``.

    The same rule as ``MyHOMEGatewayHandler._profile_supports_who`` for a real OWNd
    profile: a WHO is queried only when the profile advertises it. The two differ at the
    edges: an object without ``supports_who`` yields no rows here (the table lists what a
    profile advertises), while the runtime gate assumes everything and queries every WHO.
    """
    supports = getattr(profile, "supports_who", None)
    if not callable(supports):
        return []
    return [w for w in STARTUP_DISCOVERY_WHOS if bool(supports(w))]


def calibrate_discovery_with_ownd(
    discovery_metadata: list[dict[str, object]] | None = None,
    *,
    use_pinned: bool = False,
) -> list[dict[str, object]]:
    """Derive the startup discovery rows from OWNd.profiles.

    ``queried_whos`` (the advertised column), the ``sweep_omits`` that still apply and the
    sound wording follow the installed profile unless ``use_pinned=True``. Without an
    importable OWNd (or when ``use_pinned=True``) the rows carry ``pinned_whos`` (what the
    pinned OWNd yields), so the generated table matches the pinned release either way; nothing
    else is guessed.
    """
    if use_pinned:
        get_gateway_profile = None
    else:
        try:
            from OWNd.profiles import get_gateway_profile
        except ImportError:
            get_gateway_profile = None

    calibrated = copy.deepcopy(
        GATEWAY_DISCOVERY_METADATA if discovery_metadata is None else discovery_metadata
    )
    for row in calibrated:
        pinned = pinned_whos(row) or []
        row.pop("pinned_whos", None)
        without_who16 = row.pop("notes_without_who16", None)
        if get_gateway_profile is None:
            queried = in_sweep_order(pinned)
        else:
            prof_name = row.get("profile_name")
            prof = get_gateway_profile(str(prof_name)) if prof_name else None
            queried = discovery_whos_for(prof) if prof is not None else []
        row["queried_whos"] = queried
        # A general request of a WHO the row does not advertise is not an omission of the
        # sweep (nothing is sent for that WHO at all), so it leaves the generated row.
        row["sweep_omits"] = [
            frame for frame in cast_list(row.get("sweep_omits")) if _frame_who(str(frame)) in queried
        ]
        if queried and without_who16 and 16 not in queried:
            row["notes"] = without_who16
    return calibrated


def generate_gateway_discovery_table(*, use_pinned: bool = False) -> str:
    """Generate the markdown table for Gateway Startup Discovery Capabilities."""
    rows = calibrate_discovery_with_ownd(use_pinned=use_pinned)
    lines = [
        "| Gateway Model | Profile Subsystems (advertised) | Startup Sweep Requests (sent) | Profile Support Notes |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for row in rows:
        model = row["model"]
        whos = row["queried_whos"]
        who_str = "WHO " + ", ".join(str(w) for w in whos) if whos else "none"
        sent = "WHO " + ", ".join(str(w) for w in sweep_whos(row)) if whos else "none"
        omitted = cast_list(row.get("sweep_omits"))
        if omitted:
            sent += " (firmware drops " + ", ".join(f"`{frame}`" for frame in omitted)
            replaced = cast_list(row.get("sweep_replaces"))
            if replaced:
                sent += "; " + ", ".join(f"`{frame}`" for frame in replaced) + f" sent in place of `{GENERAL_ZONE_STATUS}`"
            sent += ")"
        notes = row["notes"]
        lines.append(f"| {model} | {who_str} | {sent} | {notes} |")
    return "\n".join(lines)


def build_discovery_block(*, use_pinned: bool = False) -> str:
    """Return the complete marker-wrapped startup discovery table block."""
    table = generate_gateway_discovery_table(use_pinned=use_pinned)
    return f"{DISCOVERY_START_MARKER}\n{table}\n{DISCOVERY_END_MARKER}"


def _format_oracle_version(version_code: str) -> str:
    """Convert 6-digit oracle version code (VVRRBB) to dotted string 'V.R.B'."""
    if len(version_code) == 6 and version_code.isdigit():
        return f"{int(version_code[0:2])}.{int(version_code[2:4])}.{int(version_code[4:6])}"
    return version_code


def _patched_ownd_profiles() -> list[str]:
    """Name the OWNd profiles that were changed after OWNd built them.

    The oracle check is only meaningful against OWNd's own profile data: a shim that
    copies oracle verdicts into OWNd would make it compare the oracle with itself.
    """
    from OWNd import profiles as ownd_profiles

    patched: list[str] = []
    for profile in ownd_profiles.canonical_profiles():
        cls = type(profile)
        if cls.__init__.__module__ != ownd_profiles.__name__ or profile != cls():
            patched.append(profile.model_name)
    return patched


def verify_profiles_against_oracle(
    oracle_path: Path = ORACLE_JSON,
    manifest_path: Path = MANIFEST_JSON,
) -> list[str]:
    """Cross-verify OWNd gateway profiles and MyHOME discovery metadata against firmware_oracle.json."""
    try:
        from OWNd.profiles import GenericGatewayProfile, get_gateway_profile
    except ImportError:
        return []

    if not oracle_path.is_file():
        return [f"Firmware oracle fixture not found: {oracle_path}"]

    data = json.loads(oracle_path.read_text(encoding="utf-8"))
    verdicts = data.get("verdicts", {})
    gateways = data.get("gateways", [])

    def _verdict(product: str, frame: str) -> tuple[str, str] | None:
        for row in verdicts.get(frame, []):
            if row.get("product") == product:
                return (str(row.get("reply")), str(row.get("verdict")))
        return None

    violations: list[str] = [
        f"OWNd {model} profile was patched after import; the oracle check would verify the patch, not OWNd."
        for model in _patched_ownd_profiles()
    ]
    probe_checks: tuple[tuple[int, str, str], ...] = (
        (1, "*1*1*31##", "WHO 1 Lighting"),
        (2, "*2*1*31##", "WHO 2 Automation"),
        (3, "*3*2*#1##", "WHO 3 Load Control"),
        (4, "*#4*1*0##", "WHO 4 Heating"),
        (25, "*25*21#1*21##", "WHO 25 CEN+"),
    )

    for gw in gateways:
        if gw.get("status") != "emulated":
            continue
        product = str(gw["product"])
        fw_dotted = _format_oracle_version(str(gw.get("version", "")))
        profile = get_gateway_profile(product, fw_dotted)
        if isinstance(profile, GenericGatewayProfile):
            continue

        if not profile.auth_measured:
            violations.append(
                f"{product} ({fw_dotted}) is emulated in firmware oracle but has auth_measured=False."
            )

        expected_audio = _verdict(product, "*#16*0*5##") == ("ack", "out")
        if profile.audio_measured != expected_audio:
            violations.append(
                f"{product} ({fw_dotted}) audio_measured={profile.audio_measured} disagrees with "
                f"oracle *#16*0*5## verdict (expected {expected_audio})."
            )

        for who_code, probe_frame, label in probe_checks:
            verdict_pair = _verdict(product, probe_frame)
            expected_supported = verdict_pair is not None and verdict_pair[1] == "out"
            actual_supported = profile.supports_who(who_code)
            if actual_supported != expected_supported:
                violations.append(
                    f"{product} ({fw_dotted}) supports_who({who_code})={actual_supported} for {label} "
                    f"disagrees with oracle {probe_frame} verdict (expected {expected_supported})."
                )

    allow_who16_transition = manifest_pins_pre_who16_ownd(manifest_path)
    for row in GATEWAY_DISCOVERY_METADATA:
        prof_name = str(row.get("profile_name", ""))
        prof = get_gateway_profile(prof_name)
        if isinstance(prof, GenericGatewayProfile):
            violations.append(
                f"Discovery row {row.get('model')} names an OWNd profile that does not exist: "
                f"{prof_name} (resolves to the generic profile)."
            )
        pinned = pinned_whos(row)
        if pinned is None:
            violations.append(
                f"Discovery row {row.get('model')} pinned_whos={row.get('pinned_whos')!r} is not a list of WHO numbers."
            )
            pinned = []
        if [w for w in pinned if w not in STARTUP_DISCOVERY_WHOS]:
            violations.append(
                f"Discovery row {row.get('model')} pinned_whos={pinned} names a WHO the startup sweep never asks for."
            )
        elif pinned != in_sweep_order(pinned):
            violations.append(
                f"Discovery row {row.get('model')} pinned_whos={pinned} is not in startup sweep order "
                f"{in_sweep_order(pinned)} (a repeated or reordered WHO)."
            )
        # While manifest.json still pins OWNd==2.0.0b11, the pin may differ from the installed
        # profile only by WHO 16, and only on a row that declares the WHO 16 switch
        # (``notes_without_who16``): the one profile change the fallback absorbs between the
        # pinned release and the next (OWNd #142). Once manifest.json bumps past 2.0.0b11,
        # ``allow_who16_transition`` flips to False and any difference is flagged as stale.
        expected_whos = discovery_whos_for(prof)
        tolerated = (
            {16}
            if (allow_who16_transition and row.get("notes_without_who16"))
            else set()
        )
        if (set(pinned) ^ set(expected_whos)) - tolerated:
            violations.append(
                f"Discovery row {row.get('model')} pinned_whos={pinned} disagrees with the installed "
                f"{prof_name} profile {expected_whos}."
            )
        for frame in cast_list(row.get("sweep_omits")):
            if str(frame) not in (GENERAL_ZONE_STATUS, SOUND_STATUS) or _frame_who(str(frame)) not in pinned:
                violations.append(
                    f"Discovery row {row.get('model')} sweep_omits={frame!r} is not a general request "
                    f"of a WHO the row advertises (pinned_whos={pinned})."
                )

    return violations



def check_readme_in_sync(
    readme_path: Path = README_MD,
    const_path: Path = CONST_PY,
    manifest_path: Path = MANIFEST_JSON,
) -> tuple[bool, str]:
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

    expected_block = build_block(const_path, manifest_path)
    actual_block = match.group(0)
    if actual_block.strip() != expected_block.strip():
        return False, f"Table content in {readme_path.name} differs from generated table"

    return True, "Table is in sync"


def update_readme(
    readme_path: Path = README_MD,
    const_path: Path = CONST_PY,
    manifest_path: Path = MANIFEST_JSON,
) -> bool:
    """Update README.md with the generated gateway profiles table.

    Returns True if file was changed, False if unchanged, missing, or invalid.
    """
    if not readme_path.exists():
        return False

    content = readme_path.read_text(encoding="utf-8")
    expected_block = build_block(const_path, manifest_path)

    if START_MARKER in content and END_MARKER in content:
        pattern = re.compile(rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}", re.DOTALL)
        updated = pattern.sub(expected_block, content)
        # Ensure footer note is present after END_MARKER
        if FOOTER_NOTE not in updated:
            updated = updated.replace(END_MARKER, f"{END_MARKER}\n\n{FOOTER_NOTE}")
    else:
        # Replace existing static table under ### Gateway Profiles
        static_pattern = re.compile(
            r"(### Gateway Profiles\s*\n\s*)"
            r"(\| Gateway Model \| Protocol Support \|.*?\n)(?=\s*\n### |\s*\n---|\s*\n## )",
            re.DOTALL,
        )
        if not static_pattern.search(content):
            return False
        replacement = f"\\1{expected_block}\n\n{FOOTER_NOTE}\n"
        updated = static_pattern.sub(replacement, content)

    if updated == content:
        return False

    readme_path.write_text(updated, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    """CLI interface for updating or checking the Gateway Profiles table."""
    parser = argparse.ArgumentParser(
        description="Update or check the Gateway Profiles table in README.md"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated table block to stdout without modifying README.md",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check whether README.md is in sync without modifying it",
    )
    parser.add_argument(
        "--readme",
        type=Path,
        default=README_MD,
        help="Path to README.md (default: repo root README.md)",
    )
    parser.add_argument(
        "--const",
        type=Path,
        default=CONST_PY,
        help="Path to const.py (default: custom_components/myhome/const.py)",
    )

    args = parser.parse_args(argv)

    if args.dry_run:
        print(build_block(args.const))
        return 0

    if args.check:
        in_sync, msg = check_readme_in_sync(args.readme, args.const)
        if in_sync:
            print(f"OK: {msg}")
            return 0
        print(f"ERROR: {msg}")
        return 1

    if not args.readme.exists():
        print(f"ERROR: {args.readme} does not exist", file=sys.stderr)
        return 1

    changed = update_readme(args.readme, args.const)
    if changed:
        print(f"Updated Gateway Profiles table in {args.readme.name}")
    else:
        print(f"Gateway Profiles table in {args.readme.name} is already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
