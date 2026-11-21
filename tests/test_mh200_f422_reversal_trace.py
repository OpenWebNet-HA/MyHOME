"""Tests for #973: MH200 (FW 2.1.0) + F422 timed cover mid-run reversal bus capture.

Verifies the sanitized 13-frame live trace recorded on a BTicino MH200 gateway
(FW 2.1.0, WHO=13 code 4) controlling a timed WHO=2 cover behind an F422 bus
interface (WHERE = 12#4#02, local bus 02, Dimension 10 NACK, M=0 ~61 s cutoff).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

from OWNd.message import OWNAutomationCommand, OWNAutomationEvent, OWNMessage

REPO_ROOT = Path(__file__).resolve().parent.parent
TRACE_DIR = REPO_ROOT / "tests" / "fixtures" / "traces" / "mh200_f422_reversal_2026-11-20"
TRACE_FILE = TRACE_DIR / "myhome_trace_MH200_where12_bus02_reversal_2026-11-20.json"
README_FILE = TRACE_DIR / "README.md"
EXPECTED_SHA256 = "ca76b36c6bcbc5ed70c2d59e30f3417503e9b1b357003aaa0acee018dcceaba7"


def _load_trace() -> dict:
    return json.loads(TRACE_FILE.read_text(encoding="utf-8"))


def test_mh200_f422_reversal_trace_metadata_and_sha256() -> None:
    """Verify SHA-256 checksum, anonymized gateway metadata, and telemetry counts."""
    assert TRACE_FILE.is_file()
    assert README_FILE.is_file()

    digest = hashlib.sha256(TRACE_FILE.read_bytes()).hexdigest()
    assert digest == EXPECTED_SHA256
    assert EXPECTED_SHA256 in README_FILE.read_text(encoding="utf-8")

    data = _load_trace()
    gw = data["gateway"]
    assert gw["model"] == "MH200"
    assert gw["firmware"] == "2.1.0"
    assert gw["host"] is None
    assert gw["port"] is None
    assert gw["mac_prefix"] == "00:03:50"
    assert gw["identification"]["who13_code"] == 4
    assert gw["identification"]["profile"] == "MH200Profile"

    assert data["telemetry"] == {
        "total_rx": 9,
        "total_tx": 4,
        "captured_in_buffer": 13,
        "buffer_depth": 200,
        "queue_depth": 0,
    }

    frames = data["frames"]
    assert len(frames) == 13
    timestamps = [frame["timestamp"] for frame in frames]
    assert timestamps == sorted(timestamps)


def test_mh200_f422_reversal_trace_privacy() -> None:
    """Verify the trace archive and README pass repository privacy scanning."""
    spec = importlib.util.spec_from_file_location(
        "anonymize_plant_fixture",
        REPO_ROOT / "scripts" / "anonymize_plant_fixture.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["anonymize_plant_fixture"] = module
    spec.loader.exec_module(module)

    assert module.findings(TRACE_FILE.read_text(encoding="utf-8"), data_file=True) == []
    assert module.findings(README_FILE.read_text(encoding="utf-8"), data_file=False) == []


def test_mh200_f422_reversal_trace_ownd_parsing_and_timings() -> None:
    """Verify OWNd parses every frame and confirms #973 timing invariants."""
    frames = _load_trace()["frames"]

    expected_sequence = [
        ("tx", "*#2*12#4#02##"),
        ("rx", "*2*0*12#4#02##"),
        ("tx", "*2*1*12#4#02##"),
        ("rx", "*2*0*12#4#02##"),
        ("rx", "*2*1*12#4#02##"),
        ("rx", "*2*0*12#4#02##"),
        ("tx", "*2*2*12#4#02##"),
        ("rx", "*2*0*12#4#02##"),
        ("rx", "*2*2*12#4#02##"),
        ("tx", "*2*1*12#4#02##"),
        ("rx", "*2*0*12#4#02##"),
        ("rx", "*2*1*12#4#02##"),
        ("rx", "*2*0*12#4#02##"),
    ]
    assert [(f["direction"], f["raw"]) for f in frames] == expected_sequence

    assert str(OWNAutomationCommand.status("12#4#02")) == frames[0]["raw"]
    assert str(OWNAutomationCommand.raise_shutter("12#4#02")) == frames[2]["raw"]
    assert str(OWNAutomationCommand.lower_shutter("12#4#02")) == frames[6]["raw"]
    assert str(OWNAutomationCommand.raise_shutter("12#4#02")) == frames[9]["raw"]

    rx_whats: list[int] = []
    for idx, frame in enumerate(frames):
        parsed = OWNMessage.parse(frame["raw"])
        assert parsed is not None
        assert parsed.who == 2
        assert parsed.where == "12"
        assert parsed.interface == "02"
        if idx == 0:
            assert isinstance(parsed, OWNAutomationCommand)
            assert parsed.is_status_request
        else:
            assert isinstance(parsed, OWNAutomationEvent)
            if frame["direction"] == "rx":
                rx_whats.append(parsed.what)

    assert rx_whats == [0, 0, 1, 0, 0, 2, 0, 1, 0]

    # Pre-move STOP frames appear 109–195 ms after TX, followed ~500 ms later by motion.
    assert round(frames[3]["timestamp"] - frames[2]["timestamp"], 3) == 0.109
    assert round(frames[4]["timestamp"] - frames[3]["timestamp"], 3) == 0.513
    assert round(frames[7]["timestamp"] - frames[6]["timestamp"], 3) == 0.195
    assert round(frames[8]["timestamp"] - frames[7]["timestamp"], 3) == 0.500
    assert round(frames[10]["timestamp"] - frames[9]["timestamp"], 3) == 0.109
    assert round(frames[11]["timestamp"] - frames[10]["timestamp"], 3) == 0.500

    # Homing M=0 cutoff (~61.0 s), ~10.07 s DOWN excursion, and ~61.0 s post-reversal cutoff.
    assert round(frames[5]["timestamp"] - frames[4]["timestamp"], 3) == 61.027
    assert round(frames[9]["timestamp"] - frames[8]["timestamp"], 3) == 10.067
    assert round(frames[12]["timestamp"] - frames[11]["timestamp"], 3) == 60.963
