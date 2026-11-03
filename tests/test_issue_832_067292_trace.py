"""Tests for #832: Real-World Legrand 067292 (Céliane 3.5" Touch Screen) Diagnostics Trace.

Contributed by @Yvan13120 in:
- https://github.com/OpenWebNet-HA/MyHOME/issues/832#issuecomment-6607331908
(Legrand 067292 Céliane MyHOME_Screen 3.5", firmware 4.0.15, UPnP device type
urn:schemas-bticino-it:device:touchscreen:1, Home Assistant 2026.10.4,
MyHOME 2.0.0b16, OWNd 2.0.0b11).

Pins four hardware, topology, and bus-traffic facts from the diagnostics capture:
1. Verbatim SHA-256 provenance, privacy scanner cleanliness (`findings == []`),
   UPnP device description metadata (`modelName = "067292"`, `manufacturer = "BTicino S.p.A."`,
   `deviceType = "urn:schemas-bticino-it:device:touchscreen:1"`, `firmware = "4.0.15"`).
2. Shared-bus multi-gateway pairing: Legrand 067292 inferred as Tier 3 (`hardware_tier = 3`),
   1 command session (`send_workers = 1`, `command_queue_delay = 0.05`), paired in Warm
   Standby (`gateway_role = "standby"`, `is_follower = True`, `is_standby = True`) with
   primary BTicino F454 (Tier 1).
3. Complete 500-frame bus monitor capture parsing cleanly through OWNd without unparsed frames
   or syntax errors (88 WHO 1 lighting frames, 274 WHO 4 climate frames across zones 1, 2, 4,
   and 138 WHO 13 clock/date sync frames).
4. Gateway profile resolution compatibility across OWNd and MyHOME.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from OWNd.message import OWNHeatingEvent, OWNLightingCommand, OWNLightingEvent, OWNMessage
from OWNd.profiles import H4890Profile

from custom_components.myhome.gateway import _profile_for_model
from scripts.anonymize_plant_fixture import findings

FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_832"
    / "config_entry-myhome-067292.json"
)
EXPECTED_SHA256 = "50c790346f53915f9e31df4dba2494bcd7f4e6c60b54d922f08ec46711f72c9a"


def _load_fixture() -> tuple[str, dict]:
    assert FIXTURE_PATH.is_file(), f"Missing trace fixture: {FIXTURE_PATH}"
    text = FIXTURE_PATH.read_text(encoding="utf-8")
    return text, json.loads(text)


def test_067292_trace_fixture_provenance_and_privacy() -> None:
    """Verify verbatim SHA-256 hash provenance and privacy scanner cleanliness."""
    text, data = _load_fixture()

    calculated_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert calculated_hash == EXPECTED_SHA256, (
        f"Fixture SHA-256 mismatch: {calculated_hash} != {EXPECTED_SHA256}"
    )

    privacy_findings = findings(text)
    assert privacy_findings == [], f"Privacy check failed with findings: {privacy_findings}"

    home_assistant = data.get("home_assistant", {})
    assert home_assistant.get("version") == "2026.10.4"
    assert data["data"]["integration_version"] == "2.0.0b16"
    assert data["data"]["ownd_version"] == "2.0.0b11"


def test_067292_gateway_identity_and_topology_inference() -> None:
    """Verify UPnP identity, manufacturer, deviceType, and shared-bus standby role."""
    _, data = _load_fixture()
    diag_data = data["data"]

    # 1. UPnP Device Description in Config Entry Data
    config_entry_data = diag_data["config_entry"]["data"]
    assert config_entry_data["name"] == "067292"
    assert config_entry_data["manufacturer"] == "BTicino S.p.A."
    assert config_entry_data["deviceType"] == "urn:schemas-bticino-it:device:touchscreen:1"
    assert config_entry_data["firmware"] == "4.0.15"
    assert config_entry_data["port"] == 20000

    # 2. Gateway Runtime Status
    gateway = diag_data["gateway"]
    assert gateway["model_name"] == "067292"
    assert gateway["firmware"] == "4.0.15"
    assert gateway["is_connected"] is True
    assert gateway["send_workers"] == 1
    assert gateway["bus_topology"] == "shared"
    assert gateway["gateway_role"] == "standby"
    assert gateway["is_follower"] is True
    assert gateway["is_standby"] is True

    # 3. Identification Evidence
    ident = gateway["identification"]
    assert ident["model"] == "067292"
    assert ident["source"] == "ssdp"
    assert ident["configured_model"] == "067292"
    assert ident["ssdp_model"] == "067292"

    # 4. Topology Inference Alignment (Tier 3 Follower to Tier 1 Primary F454)
    topology = diag_data["topology_inference"]
    target = topology["target_gateway"]
    assert target["model"] == "067292"
    assert target["hardware_tier"] == 3
    assert target["command_queue_delay"] == 0.05
    assert target["configured_topology"] == "shared"
    assert target["configured_role"] == "standby"

    assert len(topology["evaluations"]) == 1
    eval_f454 = topology["evaluations"][0]
    assert eval_f454["peer_model"] == "F454"
    assert eval_f454["peer_tier"] == 1
    assert eval_f454["recommended_primary_model"] == "F454"
    assert eval_f454["recommended_secondary_model"] == "067292"
    assert eval_f454["recommended_role"] == "standby"
    assert eval_f454["alignment"]["configured_shared_bus"] is True
    assert eval_f454["alignment"]["role_aligned"] is True


def test_067292_bus_monitor_frame_parsing_and_parser_coverage() -> None:
    """Verify all 500 bus frames parse cleanly through OWNd parser without errors."""
    _, data = _load_fixture()
    recent_frames = data["data"]["bus_monitor"]["recent_frames"]
    assert len(recent_frames) == 500

    who_counts: dict[str, int] = {}
    parsed_count = 0

    for frame in recent_frames:
        raw = frame["raw"]
        assert raw.startswith("*") and raw.endswith("##"), f"Invalid OpenWebNet syntax: {raw}"
        who = frame["who"]
        who_counts[who] = who_counts.get(who, 0) + 1

        parsed = OWNMessage.parse(raw)
        assert parsed is not None, f"Failed to parse frame: {raw}"
        parsed_count += 1

        if who == "1":
            assert isinstance(parsed, (OWNLightingCommand, OWNLightingEvent))
        elif who == "4":
            assert isinstance(parsed, OWNHeatingEvent)

    assert parsed_count == 500
    assert who_counts["1"] == 88  # Lighting
    assert who_counts["4"] == 274  # Thermoregulation
    assert who_counts["13"] == 138  # Clock & Gateway Management


def test_067292_gateway_profile_resolution() -> None:
    """Verify profile resolution for model string '067292' resolves to H4890Profile."""
    profile = _profile_for_model("067292")
    assert isinstance(profile, H4890Profile)
    assert profile.model_name == "H4890"
    assert profile.max_command_sessions == 1
    assert profile.command_queue_delay == 0.05
    assert profile.supports_audio is True
    assert 5 in profile.supported_who  # Burglar alarm supported

    # Verify fallback resolution through _MODEL_SPELLINGS when OWNd does not alias natively
    from OWNd.profiles import GenericGatewayProfile

    def _unaliased_lookup(m: str | None, fw: str | None = None):
        if m in ("H4890", "h4890"):
            return profile
        return GenericGatewayProfile(model_name=m or "Generic")

    simulated = _profile_for_model("067292", lookup=_unaliased_lookup)
    assert isinstance(simulated, H4890Profile)


def test_067292_gateway_tier() -> None:
    """Verify gateway performance tier for 067292."""
    from custom_components.myhome.topology import gateway_tier

    assert gateway_tier("067292") == 2
    assert gateway_tier("573958") == 2
    assert gateway_tier("078479") == 2
