"""Provenance, privacy, and parser verification for community captures (2026-11-20..22).

Covers:
- Issue #976 / PR #964: MH200N (FW 1.1.8) WHO=14 actuator lock & unlocked control traces
- Issue #968: MH200N (FW 1.1.8) full bus sweep
- Issue #994: F454 (FW 2.0.51) + 4-zone LN4695 full bus sweep
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from OWNd.message import OWNActuatorLockEvent, OWNMessage

from scripts.anonymize_plant_fixture import findings

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces"

FIXTURES: tuple[tuple[str, str, str, int, int, int], ...] = (
    (
        "issue_976/myhome_trace_MH200N_all_2026-11-20T12-17-07.json",
        "67aafc4440807dd88891702b1df832dc40201f78904c23622fd57d881680dee2",
        "MH200N",
        49,
        33,
        16,
    ),
    (
        "issue_976/myhome_trace_MH200N_all_2026-11-20T16-41-08.json",
        "6e317fd1d15179802dfbb28870135da4e8bcabedec243bddf8411efe022e6a63",
        "MH200N",
        190,
        97,
        93,
    ),
    (
        "issue_968/myhome_sweep_MH200N_all_2026-11-20T12-47-02.json",
        "19be38a919c19639849a6b02a37c055507e274440ed6537ed11d06d7100e33bc",
        "MH200N",
        117,
        105,
        12,
    ),
    (
        "issue_994_f454_sweep_2026-11-22/myhome_sweep_F454_all_2026-11-22T21-35-56.json",
        "2997b5daa2cdd93c572a60a270384148b3eb3436bdd342111a033d67f442184d",
        "F454",
        197,
        152,
        45,
    ),
)

READMES: tuple[str, ...] = (
    "issue_976/README.md",
    "issue_968/README.md",
    "issue_994_f454_sweep_2026-11-22/README.md",
)


def _load(rel: str) -> dict[str, Any]:
    return json.loads((TRACES_DIR / rel).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("rel", "expected_sha", "expected_model", "total", "rx_count", "tx_count"),
    FIXTURES,
)
def test_fixture_provenance_privacy_and_ownd_parsing(
    rel: str,
    expected_sha: str,
    expected_model: str,
    total: int,
    rx_count: int,
    tx_count: int,
) -> None:
    """Every captured trace preserves exact SHA-256, zero PII, and parses cleanly in OWNd."""
    path = TRACES_DIR / rel
    raw_bytes = path.read_bytes()
    assert hashlib.sha256(raw_bytes).hexdigest() == expected_sha
    assert findings(raw_bytes.decode("utf-8")) == []

    payload = json.loads(raw_bytes.decode("utf-8"))
    assert payload["gateway"]["model"] == expected_model

    frames = payload["frames"]
    assert len(frames) == total
    assert sum(1 for fr in frames if fr["direction"] == "rx") == rx_count
    assert sum(1 for fr in frames if fr["direction"] == "tx") == tx_count

    for fr in frames:
        parsed = OWNMessage.parse(fr["raw"])
        assert parsed is not None, f"Failed to parse {fr['raw']!r} in {rel}"


@pytest.mark.parametrize("rel", READMES)
def test_trace_readmes_are_privacy_clean(rel: str) -> None:
    """Trace READMEs carry zero personal data."""
    text = (TRACES_DIR / rel).read_text(encoding="utf-8")
    assert findings(text) == []


def test_issue_976_mh200n_who14_lock_suppresses_who1_echoes_until_unlock() -> None:
    """MH200N relays WHO=14 lock/unlock frames and the locked dimmer ignores writes until unlocked."""
    frames = _load("issue_976/myhome_trace_MH200N_all_2026-11-20T12-17-07.json")["frames"]

    lock_rx = OWNMessage.parse(frames[18]["raw"])
    unlock_rx = OWNMessage.parse(frames[38]["raw"])
    assert isinstance(lock_rx, OWNActuatorLockEvent)
    assert isinstance(unlock_rx, OWNActuatorLockEvent)
    assert frames[18]["direction"] == "rx" and frames[18]["raw"] == "*14*0*55##"
    assert frames[38]["direction"] == "rx" and frames[38]["raw"] == "*14*1*55##"

    # Between the locked writes (indices 27..33) and the unlock echo (index 38),
    # the locked dimmer emits zero WHO=1 bus echoes.
    who1_rx_while_locked = [
        fr for fr in frames[27:38] if fr["direction"] == "rx" and str(fr.get("who")) == "1"
    ]
    assert who1_rx_while_locked == []

    # After unlock, WHO=1 writes immediately echo again.
    who1_rx_after_unlock = [
        fr["raw"] for fr in frames[42:] if fr["direction"] == "rx" and str(fr.get("who")) == "1"
    ]
    assert who1_rx_after_unlock == [
        "*#1*55*1*140*5##",
        "*#1*55*1*171*5##",
        "*#1*55*1*100*2##",
    ]


def test_issue_968_mh200n_sweep_omits_ghost_light_51() -> None:
    """MH200N bus sweep confirms FW 1.1.8 (code 44) and 39 lighting addresses without 51."""
    payload = _load("issue_968/myhome_sweep_MH200N_all_2026-11-20T12-47-02.json")
    ident = payload["gateway"]["identification"]
    assert ident["who13_code"] == "44"
    assert ident["who13_firmware"] == "1.1.8"
    assert ident["who1013_code"] == "44"

    who1_wheres = {
        fr["where"]
        for fr in payload["frames"]
        if fr["direction"] == "rx" and str(fr.get("who")) == "1" and fr.get("where")
    }
    assert len(who1_wheres) == 39
    assert "51" not in who1_wheres


def test_issue_994_f454_sweep_identity_and_thermo_zones() -> None:
    """F454 bus sweep confirms FW 2.0.51 (WHO=1013 code 51) and WHO=4 zones 1, 3, 4."""
    payload = _load(
        "issue_994_f454_sweep_2026-11-22/myhome_sweep_F454_all_2026-11-22T21-35-56.json"
    )
    ident = payload["gateway"]["identification"]
    assert ident["who13_code"] == "200"
    assert ident["who13_firmware"] == "2.0.51"
    assert ident["who1013_code"] == "51"

    who4_rx = [
        fr["raw"]
        for fr in payload["frames"]
        if fr["direction"] == "rx" and str(fr.get("who")) == "4"
    ]
    assert who4_rx == [
        "*#4*1*0*0194##",
        "*#4*3*0*0195##",
        "*#4*4*0*0194##",
        "*#4*4#1*20*0##",
        "*#4*1#1*20*0##",
    ]
