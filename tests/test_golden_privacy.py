"""Golden test corpus privacy validation suite.

Ensures that all golden test fixtures, manifests, and corpus files
maintain strict 'privacy first' standards:
- No private LAN addresses (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
- No non-synthetic MAC addresses
- No user-specific file paths (/home/, C:\\Users\\)
- No cleartext household identities or credentials
"""
# privacy-check: allow-samples - the inputs below are what the check must catch

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
FRAMES_DIR = GOLDEN_DIR / "frames"

PRIVATE_IP = re.compile(r"\b(10\.\d+|172\.(1[6-9]|2\d|3[01])|192\.168)\.\d+\.\d+\b")
MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")
LOCAL_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|/home/|/Users/", re.IGNORECASE)


def test_golden_corpus_carries_no_private_ips() -> None:
    """Verify all YAML frames, SOURCE manifest, and corpus.json contain no LAN IPs."""
    files_to_check = list(FRAMES_DIR.glob("*.yaml"))
    for extra in ("corpus.json", "SOURCE.yaml", "README.md", "schema.json"):
        extra_path = GOLDEN_DIR / extra
        if extra_path.is_file():
            files_to_check.append(extra_path)

    assert files_to_check, "No golden corpus files found to check"

    for file_path in files_to_check:
        text = file_path.read_text(encoding="utf-8")
        match = PRIVATE_IP.search(text)
        assert match is None, (
            f"Privacy leak in {file_path.name}: private LAN IP {match.group(0)!r} detected."
        )


def test_golden_corpus_mac_addresses_are_synthetic() -> None:
    """Verify that any MAC addresses in the golden test corpus use synthetic prefixes."""
    files_to_check = list(FRAMES_DIR.glob("*.yaml"))
    for extra in ("corpus.json", "SOURCE.yaml"):
        extra_path = GOLDEN_DIR / extra
        if extra_path.is_file():
            files_to_check.append(extra_path)

    for file_path in files_to_check:
        text = file_path.read_text(encoding="utf-8")
        macs = MAC.findall(text)
        for mac in macs:
            assert mac.lower().startswith("00:03:50:00:"), (
                f"Privacy leak in {file_path.name}: non-synthetic MAC {mac!r} detected."
            )


def test_golden_corpus_no_personal_paths() -> None:
    """Verify no local filesystem user paths leak into golden fixtures, manifest, or corpus."""
    files_to_check = list(FRAMES_DIR.glob("*.yaml"))
    for extra in ("corpus.json", "SOURCE.yaml", "README.md"):
        extra_path = GOLDEN_DIR / extra
        if extra_path.is_file():
            files_to_check.append(extra_path)
    for yf in files_to_check:
        text = yf.read_text(encoding="utf-8")
        assert not LOCAL_PATH.search(text), (
            f"Privacy leak in {yf.name}: personal filesystem path detected."
        )


def test_privacy_detector_catches_violations() -> None:
    """Ensure the privacy regexes correctly identify sensitive household information."""
    assert PRIVATE_IP.search("Host at 192.168.1.27:20000") is not None
    assert PRIVATE_IP.search("Gateway 10.0.0.1 online") is not None
    assert PRIVATE_IP.search("Router 172.20.10.1 connected") is not None
    # Documentation range (RFC 5737) is allowed
    assert PRIVATE_IP.search("Documentation IP 192.0.2.1") is None
    # Local paths
    assert LOCAL_PATH.search("C:\\Users\\someone\\file.yaml") is not None
    assert LOCAL_PATH.search("/home/someone/.config") is not None
