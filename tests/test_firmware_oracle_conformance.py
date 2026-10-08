"""Firmware Oracle Conformance Test Suite.

Verifies cross-firmware compatibility of OpenWebNet frames, OWNd parser
resilience on authentic firmware-emitted frames, and firmware rejection
guarantees against hash-pinned verdicts from own-firmware-oracle.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from OWNd.message import OWNMessage

REPO_ROOT = Path(__file__).resolve().parent.parent
ORACLE_JSON_PATH = REPO_ROOT / "tests" / "golden" / "firmware_oracle.json"
CORPUS_JSON_PATH = REPO_ROOT / "tests" / "golden" / "corpus.json"


def load_firmware_oracle() -> dict[str, Any]:
    """Load the firmware oracle verdict index."""
    if not ORACLE_JSON_PATH.is_file():
        pytest.skip("tests/golden/firmware_oracle.json fixture not found")
    with open(ORACLE_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


ORACLE_DATA = load_firmware_oracle()
ALL_VERDICTS = ORACLE_DATA.get("verdicts", {})


def test_firmware_oracle_integrity():
    """Verify cryptographic integrity and structure of firmware_oracle.json."""
    data = load_firmware_oracle()
    assert data["format_version"] == "1.0.0"
    assert data["generator"] == "own-firmware-oracle"
    assert data["schema_version"] == "1.0.0"

    verdicts = data["verdicts"]
    canonical_verdicts = json.dumps(verdicts, sort_keys=True, separators=(",", ":"))
    calculated_hash = hashlib.sha256(canonical_verdicts.encode("utf-8")).hexdigest()
    assert calculated_hash == data["verdicts_sha256"], (
        f"Verdicts SHA-256 digest mismatch: {calculated_hash} != {data['verdicts_sha256']}"
    )

    assert data["total_unique_inputs"] == len(verdicts)
    assert len(data["gateways"]) >= 2

    gateway_names = {f"{g['product']} {g['version']}" for g in data["gateways"]}
    assert "MH200N 010108" in gateway_names
    assert "MyHomeServer1 028206" in gateway_names


def test_emitted_own_frames_parseable_by_ownd():
    """Verify that every OpenWebNet frame emitted by real firmware parses cleanly in OWNd."""
    all_emitted: set[str] = set()
    for _inp, entries in ALL_VERDICTS.items():
        for entry in entries:
            for own_frame in entry.get("emitted_own", []):
                all_emitted.add(own_frame)

    assert len(all_emitted) > 0, "Expected at least one emitted OpenWebNet frame in oracle"

    failures: list[str] = []
    for frame in sorted(all_emitted):
        try:
            parsed = OWNMessage.parse(frame)
            if parsed is None:
                failures.append(f"Unparsed frame: {frame}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"Exception parsing {frame}: {type(exc).__name__}: {exc}")

    assert not failures, f"OWNd failed to parse {len(failures)} firmware-emitted frames:\n" + "\n".join(failures)


# Known protocol discrepancies between spec-derived/openwebnet4j corpus fixtures
# and actual gateway firmware behavior (audited in own-firmware-oracle):
KNOWN_GATEWAY_DISCREPANCIES = {
    # Private bus routing #4# is refused on MH200N without explicit routing config:
    ("cover.cmd.up.bus.21", "MH200N"): "nack",
    # Central unit mode commands: MH200N refuses #0 central unit modes in default config:
    ("thermo.cmd.central.mode.heat.cu99", "MH200N"): "nack",
    ("thermo.cmd.central.mode.heat.cu99", "MyHomeServer1"): "ack",
    ("thermo.cmd.central.mode.cool.cu99", "MH200N"): "nack",
    ("thermo.cmd.central.mode.cool.cu99", "MyHomeServer1"): "ack",
    ("thermo.cmd.central.mode.off.cu99", "MH200N"): "nack",
    ("thermo.cmd.central.mode.off.cu99", "MyHomeServer1"): "ack",
}


def test_golden_corpus_against_firmware_oracle():
    """Verify that valid downstream commands in the golden corpus are not rejected by firmware."""
    if not CORPUS_JSON_PATH.is_file():
        pytest.skip("tests/golden/corpus.json fixture not found")

    with open(CORPUS_JSON_PATH, "r", encoding="utf-8") as f:
        corpus = json.load(f)

    # Collect commands that exist in the oracle
    checked_count = 0
    failures: list[str] = []

    for fixture in corpus:
        fixture_id = fixture.get("id", "")
        frame = fixture.get("frame")
        direction = fixture.get("direction")
        # Only evaluate downstream commands where direction is command/down
        if direction not in ("command", "down"):
            continue

        if frame in ALL_VERDICTS:
            entries = ALL_VERDICTS[frame]
            for entry in entries:
                checked_count += 1
                reply = entry.get("reply")
                product = str(entry.get("product"))
                expected_discrepancy = KNOWN_GATEWAY_DISCREPANCIES.get((fixture_id, product))
                if expected_discrepancy is not None:
                    # Assert expected known divergence behavior on this gateway
                    assert reply == expected_discrepancy, (
                        f"Expected known discrepancy {fixture_id} on {product} to be "
                        f"{expected_discrepancy}, got {reply}"
                    )
                    continue

                if fixture.get("valid", True) and not fixture.get("expected_rejection", False):
                    if reply == "nack":
                        failures.append(
                            f"Corpus command {fixture_id} ({frame}) received unexpected NACK on "
                            f"{product} {entry['version']} (suite: {entry['suite']})"
                        )

    assert checked_count > 0, "No corpus commands intersected with firmware oracle verdicts"
    assert not failures, "Encountered unexpected firmware NACKs for golden corpus commands:\n" + "\n".join(failures)


@pytest.mark.parametrize(
    ("frame", "expected_reply", "expected_verdict"),
    [
        ("*#1*0*#1*100*0##", "nack", "silent"),
        ("*#1*31*#1*100*0##", "nack", "silent"),
        ("*#1*31*#1*100*255##", "nack", "silent"),
        ("*#1*31*#1*100*5##", "nack", "silent"),
    ],
)
def test_known_firmware_rejections(frame: str, expected_reply: str, expected_verdict: str):
    """Verify that known protocol boundary frames produce expected rejections on target gateways."""
    assert frame in ALL_VERDICTS, f"Target frame {frame} missing from oracle verdicts"
    entries = ALL_VERDICTS[frame]
    assert len(entries) > 0

    for entry in entries:
        assert entry["reply"] == expected_reply, (
            f"Frame {frame} on {entry['product']} expected reply {expected_reply}, got {entry['reply']}"
        )
        assert entry["verdict"] == expected_verdict, (
            f"Frame {frame} on {entry['product']} expected verdict {expected_verdict}, got {entry['verdict']}"
        )


def test_what19_fault_emitted_event():
    """Verify that *#1*74## produces the expected WHAT 19 lighting fault event on MH200N."""
    frame = "*#1*74##"
    assert frame in ALL_VERDICTS, f"Frame {frame} missing from oracle index"
    entries = ALL_VERDICTS[frame]
    mh200n_entries = [e for e in entries if e["product"] == "MH200N"]
    assert len(mh200n_entries) > 0

    # MH200N emits bus frame and OWN event *1*19*74##
    found_fault_event = False
    for entry in mh200n_entries:
        assert entry["verdict"] == "out"
        if "*1*19*74##" in entry["emitted_own"]:
            found_fault_event = True

    assert found_fault_event, f"Expected *1*19*74## in emitted_own for {frame} on MH200N"


def test_all_gateway_responses_conform_to_openwebnet_protocol():
    """Verify that every verdict entry adheres strictly to OpenWebNet framing rules."""
    valid_replies = {"ack", "nack", "-"}
    valid_verdicts = {"out", "silent", "timeout"}

    for inp, entries in ALL_VERDICTS.items():
        assert inp.startswith("*") and inp.endswith("##"), f"Invalid input frame format: {inp}"
        for entry in entries:
            assert entry["reply"] in valid_replies, (
                f"Invalid reply '{entry['reply']}' for {inp} on {entry['product']}"
            )
            assert entry["verdict"] in valid_verdicts, (
                f"Invalid verdict '{entry['verdict']}' for {inp} on {entry['product']}"
            )
            for bus_hex in entry.get("bus_frames", []):
                # Verify bus frame consists of space-separated hex bytes
                tokens = bus_hex.split()
                assert len(tokens) > 0, f"Empty bus frame for {inp}"
                for token in tokens:
                    assert len(token) == 2, f"Invalid hex token '{token}' in bus frame '{bus_hex}'"
                    int(token, 16)  # Asserts valid hex representation
            for own_frame in entry.get("emitted_own", []):
                assert own_frame.startswith("*") and own_frame.endswith("##"), (
                    f"Invalid emitted OpenWebNet frame: {own_frame}"
                )


def test_dimmer_level_cross_gateway_behavior():
    """Verify cross-gateway divergence on dimmer level write *1*0#1*31##.

    Both MH200N and MyHomeServer1 forward the identical SCS bus frame,
    but MH200N replies with ACK while MyHomeServer1 replies with NACK.
    """
    frame = "*1*0#1*31##"
    assert frame in ALL_VERDICTS
    entries = {e["product"]: e for e in ALL_VERDICTS[frame]}

    assert "MH200N" in entries
    assert "MyHomeServer1" in entries

    mh200n = entries["MH200N"]
    mhs1 = entries["MyHomeServer1"]

    # Both gateways transmit the identical bus frame to the lighting actuator:
    expected_bus = ["24 30 36 44 31 33 31 30 31 34 32 30 44 30 31 30 30 30 31 0d"]
    assert mh200n["bus_frames"] == expected_bus
    assert mhs1["bus_frames"] == expected_bus

    # But their command session replies diverge:
    assert mh200n["reply"] == "ack"
    assert mhs1["reply"] == "nack"


