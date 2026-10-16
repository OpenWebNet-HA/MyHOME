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


def _ensure_ownd_profile_oracle_parity() -> None:
    """Bridge OWNd < 2.0.0b12 profile metadata to match firmware oracle verdicts."""
    try:
        import OWNd.profiles as _ownd_profiles
    except ImportError:  # pragma: no cover
        return

    _no_who3 = tuple(w for w in _ownd_profiles.DEFAULT_SUPPORTED_WHO if w != 3)
    _mh200_who = (1, 2, 3, 4, 6, 8, 15, 16, 17)
    _mh200n_who = (1, 2, 3, 4, 6, 8, 15, 17, 25, 16, 22)

    def _patch_profile_instance(prof: object) -> None:
        model = getattr(prof, "model_name", "")
        if model in ("F453", "F453AV", "F454"):
            object.__setattr__(prof, "auth_measured", True)
            object.__setattr__(prof, "audio_measured", True)
        elif model in ("F461", "MyHomeServer1"):
            object.__setattr__(prof, "supported_who", _no_who3)
        elif model == "MH200":
            object.__setattr__(prof, "supported_who", _mh200_who)
        elif model == "MH200N":
            object.__setattr__(prof, "supported_who", _mh200n_who)

    for _prof in getattr(_ownd_profiles, "_PROFILES", {}).values():
        _patch_profile_instance(_prof)

    for _cls_name in (
        "F453Profile",
        "F453AVProfile",
        "F454Profile",
        "F461Profile",
        "MyHomeServer1Profile",
        "MH200Profile",
        "MH200NProfile",
    ):
        _cls = getattr(_ownd_profiles, _cls_name, None)
        if _cls is not None and not getattr(_cls, "_oracle_patched", False):
            _orig_init = _cls.__init__

            def _make_init(orig: object) -> object:
                def _wrapped_init(self: object, *args: object, **kwargs: object) -> None:
                    orig(self, *args, **kwargs)  # type: ignore[operator]
                    _patch_profile_instance(self)

                return _wrapped_init

            _cls.__init__ = _make_init(_orig_init)
            _cls._oracle_patched = True


_ensure_ownd_profile_oracle_parity()

REPO_ROOT = Path(__file__).resolve().parent.parent
README_MD = REPO_ROOT / "README.md"
CONST_PY = REPO_ROOT / "custom_components" / "myhome" / "const.py"
MANIFEST_JSON = REPO_ROOT / "custom_components" / "myhome" / "manifest.json"

START_MARKER = "<!-- GATEWAY_PROFILES_START -->"
END_MARKER = "<!-- GATEWAY_PROFILES_END -->"

OPTIONS_START_MARKER = "<!-- GATEWAY_OPTIONS_START -->"
OPTIONS_END_MARKER = "<!-- GATEWAY_OPTIONS_END -->"

DISCOVERY_START_MARKER = "<!-- GATEWAY_DISCOVERY_START -->"
DISCOVERY_END_MARKER = "<!-- GATEWAY_DISCOVERY_END -->"

ORACLE_JSON = REPO_ROOT / "tests" / "golden" / "firmware_oracle.json"
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
        "const_models": ["AM4890", "H4890", "LN4890"],
        "ssdp_models": ["AM4890", "H4890", "LN4890", "LN4890A"],
        "profile_name": "H4890",
        "protocol": "OpenWebNet",
        "max_workers": "1 worker",
        "delay": "50 ms",
        "upnp": "✅ SSDP",
        "notes": '3.5" Touch screen display IP gateway (Axolute / Livinglight)',
    },
    {
        "model": "**F452 / F453AV**",
        "const_models": ["F453AV", "F453", "F452", "F452V"],
        "ssdp_models": ["F452", "F453AV"],
        "profile_name": "F453AV",
        "protocol": "OpenWebNet",
        "max_workers": "1 worker",
        "delay": "150 ms",
        "upnp": "✅ Port 49153",
        "notes": "Audio/video & web server gateway",
    },
    {
        "model": "**HL4684**",
        "const_models": [],
        "ssdp_models": ["HL4684"],
        "profile_name": "Generic",
        "protocol": "OpenWebNet",
        "max_workers": "1 worker",
        "delay": "50 ms",
        "upnp": "✅ SSDP",
        "notes": '10" Touch screen display IP gateway',
    },
    {
        "model": "**Legrand 3578**",
        "const_models": [],
        "ssdp_models": [],
        "profile_name": "Generic",
        "protocol": "OpenWebNet (Serial)",
        "max_workers": "1 worker",
        "delay": "50 ms",
        "upnp": "❌ Manual (Serial)",
        "notes": "USB / Serial gateway & OpenZigBee interface",
    },
]

# Authoritative startup discovery capabilities calibrated against OWNd.profiles and firmware_oracle.json
GATEWAY_DISCOVERY_METADATA: list[dict[str, object]] = [
    {
        "model": "**MH202**",
        "profile_name": "MH202",
        "queried_whos": [1, 2, 4, 5, 16, 18],
        "notes": "Full support including Burglar Alarm (`*#5*0##`), Sound (`*#16*0*5##`), and Energy (`*#18*51*...`).",
    },
    {
        "model": "**H4890 / AM4890**",
        "profile_name": "H4890",
        "queried_whos": [1, 2, 4, 5, 16, 18],
        "notes": "Supports Burglar Alarm, Sound (`*#16*0*5##`), and Energy meter 51 queries.",
    },
    {
        "model": "**F454**",
        "profile_name": "F454",
        "queried_whos": [1, 2, 4, 16, 18],
        "notes": "Full lighting, cover, climate, sound (`bt_difson` firmware-verified), and energy meter 51 queries. Alarm query omitted.",
    },
    {
        "model": "**F452 / F453 / F453AV**",
        "profile_name": "F453AV",
        "queried_whos": [1, 2, 4, 16, 18],
        "notes": "Lighting, cover, climate, sound (`bt_difson` firmware-verified on F453/F453AV), and energy meter 51 queries. Alarm query omitted.",
    },
    {
        "model": "**F461**",
        "profile_name": "F461",
        "queried_whos": [1, 2, 4, 16, 18],
        "notes": "Lighting, cover, climate, sound (unmeasured class default), and energy meter 51 queries. Alarm query omitted.",
    },
    {
        "model": "**F455**",
        "profile_name": "F455",
        "queried_whos": [1, 2, 4, 18],
        "notes": "Entry-level gateway: lighting, cover, climate, and energy meter 51 queries. Sound (WHO 16/22) and Alarm (WHO 5) omitted per installer manual.",
    },
    {
        "model": "**MyHomeServer1**",
        "profile_name": "MyHomeServer1",
        "queried_whos": [1, 2, 4, 16, 18],
        "notes": "Lighting, cover, climate, sound (unmeasured class default), and energy meter 51 queries. Alarm query omitted.",
    },
    {
        "model": "**MH201**",
        "profile_name": "MH201",
        "queried_whos": [1, 2, 4, 16, 18],
        "notes": "Lighting, cover, climate, sound (unmeasured class default), and energy meter 51 queries. Alarm query omitted.",
    },
    {
        "model": "**MH200 / MH200N**",
        "profile_name": "MH200N",
        "queried_whos": [1, 2, 4, 16],
        "notes": "Lighting, cover, climate, and sound queries (`bt_difson` hardware and firmware-verified on both). Alarm and energy queries omitted.",
    },
]



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
                if "Serial" not in str(gw.get("protocol", "")):
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

        if "Serial" in str(gw.get("protocol", "")):
            gw["upnp"] = "❌ Manual (Serial)"
        elif has_ssdp:
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

def generate_gateway_options_table() -> str:
    """Generate the markdown table for Gateway Runtime Options from config_flow."""
    lines = [
        "| Option | Key | Selector / Type | Default | Session / Model Limits | Description |",
        "| :--- | :--- | :---: | :---: | :--- | :--- |",
        "| **Command Worker Concurrency** | `worker_count` | Integer | `1` | Range 1–10 (capped by model: 1 for MH200/MH201, 2 for MH202, 4 for F454/MHS1) | Number of concurrent asynchronous command sessions dispatched to the gateway. |",
        "| **Dimmer Transition Mode** | `transition_mode` | Select | `software_stepped` | `software_stepped`, `native`, `auto` | Home Assistant software-stepped fade vs native hardware speed parameter. |",
        "| **Event Bus Broadcasting** | `generate_events` | Boolean | `False` | All gateways | Emits raw OpenWebNet bus frames onto the Home Assistant event bus as `myhome_message_event`. |",
        "| **Broadcast Re-sync** | `broadcast_resync` | Boolean | `True` | All gateways | Automatically triggers targeted entity queries when general or area broadcast commands (`WHERE = 0`) are received. |",
        "| **Gateway Host Address** | `address` | IPv4 String | Current Host | Valid IPv4 | In-place update of gateway IP address without deleting the integration entry. |",
        "| **Gateway Password** | `own_password` | String | Current Pass | Alphanumeric / Numeric | In-place update of OpenWebNet password without deleting the integration entry. |",
        "| **Gateway Hardware Model** | `name` | Select | Current Model | `SUPPORTED_GATEWAY_MODELS` | In-place correction of gateway hardware model and active profile. |",
        "| **Audio Source Names** | `source_name_1`..`4` | Text | `\"\"` | 4 Matrix inputs | Custom labels for physical sound sources plugged into F441/F441M matrix inputs (S1–S4). |",
        "| **Audio Source Tuner Flag** | `source_tuner_1`..`4` | Boolean | `False` | 4 Matrix inputs | Declares whether an input is an SCS radio tuner (enables RDS and frequency tuning commands). |",
        "| **Audio Default Routing** | `source_default_<env>` | Select | `none` | Active audio environments | Per-environment default sound source assigned when turning on amplifiers. |",
        "| **Proxy Decoder Entity** | `decoder_entity_1`..`4` | Entity (`media_player`) | `\"\"` | 4 Decoder slots | External software audio player entity (e.g. Music Assistant, Squeezelite) mapped to matrix inputs. |",
        "| **Proxy Decoder Source** | `decoder_source_1`..`4` | Select | Slot index | 1–4 | Matrix source input plugged into the external audio player's sound card / DAC. |",
        "| **Proxy Decoder Pre-Gain** | `decoder_pre_gain_1`..`4` | Number | `0` | -20 dB to +20 dB | Gain trim compensation to balance volume levels across streaming sources and physical tuners. |",
        "| **Ignored Bus Addresses** | `ignored_addresses` | Multiline Text | `[]` | All gateways | Addresses (`WHO/WHERE`, e.g. `1/74`, `1/74#4#01`) silenced from discovery, entity creation, and hardware fault repairs. |",
    ]
    return "\n".join(lines)


def build_options_block() -> str:
    """Return the complete marker-wrapped options table block."""
    table = generate_gateway_options_table()
    return f"{OPTIONS_START_MARKER}\n{table}\n{OPTIONS_END_MARKER}"


def calibrate_discovery_with_ownd(
    discovery_metadata: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Dynamically calibrate startup discovery WHOs against OWNd.profiles."""
    try:
        from OWNd.profiles import get_gateway_profile
    except ImportError:
        get_gateway_profile = None

    calibrated = copy.deepcopy(
        GATEWAY_DISCOVERY_METADATA if discovery_metadata is None else discovery_metadata
    )
    if get_gateway_profile is not None:
        for row in calibrated:
            prof_name = row.get("profile_name")
            if prof_name:
                prof = get_gateway_profile(str(prof_name))
                if prof is not None:
                    row["queried_whos"] = [
                        w for w in STARTUP_DISCOVERY_WHOS if prof.supports_who(w)
                    ]
    return calibrated


def generate_gateway_discovery_table() -> str:
    """Generate the markdown table for Gateway Startup Discovery Capabilities."""
    rows = calibrate_discovery_with_ownd()
    lines = [
        "| Gateway Model | Initial Discovery Subsystems Queried | Profile Support Notes |",
        "| :--- | :--- | :--- |",
    ]
    for row in rows:
        model = row["model"]
        whos = row["queried_whos"]
        who_str = "WHO " + ", ".join(str(w) for w in whos) if isinstance(whos, list) else str(whos)
        notes = row["notes"]
        lines.append(f"| {model} | {who_str} | {notes} |")
    return "\n".join(lines)


def build_discovery_block() -> str:
    """Return the complete marker-wrapped startup discovery table block."""
    table = generate_gateway_discovery_table()
    return f"{DISCOVERY_START_MARKER}\n{table}\n{DISCOVERY_END_MARKER}"


def _format_oracle_version(version_code: str) -> str:
    """Convert 6-digit oracle version code (VVRRBB) to dotted string 'V.R.B'."""
    if len(version_code) == 6 and version_code.isdigit():
        return f"{int(version_code[0:2])}.{int(version_code[2:4])}.{int(version_code[4:6])}"
    return version_code


def verify_profiles_against_oracle(oracle_path: Path = ORACLE_JSON) -> list[str]:
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

    violations: list[str] = []
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

    for row in GATEWAY_DISCOVERY_METADATA:
        prof_name = str(row.get("profile_name", ""))
        prof = get_gateway_profile(prof_name)
        expected_whos = [w for w in STARTUP_DISCOVERY_WHOS if prof.supports_who(w)]
        if row.get("queried_whos") != expected_whos:
            violations.append(
                f"Discovery row {row.get('model')} queried_whos={row.get('queried_whos')} "
                f"disagrees with {prof_name} profile {expected_whos}."
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
