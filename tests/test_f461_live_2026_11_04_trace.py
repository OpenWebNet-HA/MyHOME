"""Pins for the 2026-11-04 live BTicino F461 capture (full debug log + bus sweep export).

Fixtures:
  tests/fixtures/traces/f461_live_2026-11-04/F461_full_debug_log_2026-11-04T15-45-56.txt
  tests/fixtures/traces/f461_live_2026-11-04/myhome_sweep_F461_all_2026-11-04T15-45-56.json

A local Home Assistant 2026.10.2 ran MyHOME 2.0.0b17 with the released OWNd 2.0.0b13 against the
physical F461 2.0.11 test plant; every device was exercised over the REST API and the gateway's
WHO 13 dimensions were read. Every test pins a fact of the capture itself (a frame present, a NACK,
an echo form, the WHO distribution) so the module passes on the base branch with the pinned OWNd.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
from pathlib import Path

from OWNd.message import OWNMessage

DIR = Path(__file__).resolve().parent / "fixtures/traces/f461_live_2026-11-04"
LOG = DIR / "F461_full_debug_log_2026-11-04T15-45-56.txt"
SWEEP = DIR / "myhome_sweep_F461_all_2026-11-04T15-45-56.json"
LOG_SHA256 = "d34644c169bee30d2f6bc6957ae6d95c8f9055d2b6494ffdef5d60974d1a7694"
SWEEP_SHA256 = "508222ff7a9f20ffb304b2305159ced5d3952c0666fe81ba4411d78b2a035398"
SYNTHETIC_ADDRESS = "192.0.2.92"
SYNTHETIC_MAC = "00:03:50:00:11:04"

_RX = re.compile(r"^(\S+ \S+) DEBUG .*Message received: `([^`]+)`")
_TX = re.compile(r"Message `([^`]+)` was successfully sent")


def _lines() -> list[str]:
    return LOG.read_text(encoding="utf-8").splitlines()


def _received() -> list[tuple[str, str]]:
    out = []
    for line in _lines():
        m = _RX.match(line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def _sent() -> list[str]:
    return [m.group(1) for line in _lines() if (m := _TX.search(line))]


def _sweep() -> dict:
    return json.loads(SWEEP.read_text(encoding="utf-8"))


def _after(frame: str, window: int = 14) -> list[str]:
    """The frames received within ``window`` log lines after ``frame`` was sent (first send)."""
    lines = _lines()
    i = next(k for k, line in enumerate(lines) if f"Message `{frame}` was successfully sent" in line)
    return [m.group(2) for line in lines[i + 1 : i + 1 + window] if (m := _RX.match(line))]


def test_fixture_provenance_is_pinned() -> None:
    assert hashlib.sha256(LOG.read_bytes()).hexdigest() == LOG_SHA256
    assert hashlib.sha256(SWEEP.read_bytes()).hexdigest() == SWEEP_SHA256


def test_capture_environment_is_the_f461_2_0_11_on_released_ownd() -> None:
    text = LOG.read_text(encoding="utf-8")
    assert "Initializing MyHOME integration v2.0.0b17 (OWNd v2.0.0b13)" in text
    assert "Setting up MyHOME gateway 'F461 Gateway' (v2.0.0b17, OWNd v2.0.0b13)" in text
    doc = _sweep()
    assert doc["environment"]["ownd_version"] == "2.0.0b13"
    assert doc["environment"]["integration_version"] == "2.0.0b17"
    assert doc["gateway"]["model"] == "F461" and doc["gateway"]["firmware"] == "2.0.11"
    ident = doc["gateway"]["identification"]
    assert ident["who13_code"] == "200" and ident["who13_firmware"] == "2.0.11"
    assert ident["who1013_model"] == "F461" and ident["profile"] == "F461Profile"


def test_no_gateway_network_identity_is_committed() -> None:
    text = LOG.read_text(encoding="utf-8")
    ips = set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)) - {"255.255.255.0"}
    assert ips == {SYNTHETIC_ADDRESS, "127.0.0.1"}
    macs = {m.lower() for m in re.findall(r"\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b", text, re.I)}
    assert macs == {SYNTHETIC_MAC}
    assert "*#13**10*192*0*2*92##" in text and "*#13**12*0*3*80*0*17*4##" in text
    assert "password" not in text.lower().replace("session password", "")
    assert SWEEP_SHA256 and SYNTHETIC_ADDRESS not in SWEEP.read_text(encoding="utf-8")


def test_log_span_counts_and_who_distribution() -> None:
    received = _received()
    assert len(received) == 169
    assert received[0][0] == "2026-11-04 17:37:13.168"
    assert received[-1][0] == "2026-11-04 17:45:28.881"
    whos = collections.Counter(re.match(r"\*#?(\d+)", f).group(1) for _, f in received)
    assert whos == {"1": 50, "4": 48, "2": 31, "13": 20, "1001": 16, "1013": 2, "14": 2}
    assert len(_sent()) == 107
    assert sum(1 for line in _lines() if "NACK" in line) == 26


def test_every_received_frame_parses_in_ownd() -> None:
    for _, frame in _received():
        assert OWNMessage.parse(frame) is not None, frame


def test_who13_reads_carry_four_digit_years_and_timezone_999() -> None:
    frames = [f for _, f in _received()]
    assert "*#13**1*03*04*11*2026##" in frames
    assert "*#13**22*17*42*27*999*03*04*11*2026##" in frames
    assert "*#13**0*17*45*24*999##" in frames
    assert "*#13**16*2*0*11##" in frames and "*#13**15*200##" in frames
    assert "*#13**17*4*2*0##" in frames and "*#13**19*0*14*53*9##" in frames
    assert "*#13**23*5*10*35##" in frames and "*#13**24*2*26*4##" in frames
    assert "*#13**27*70#52#54#49##" in frames and "*#1013**1*134*15*5*0##" in frames


def test_f418_dimmer_answers_level_writes_steps_and_parameterised_steps() -> None:
    frames = [f for _, f in _received()]
    assert "*#1*0010*1*130*4##" in _after("*#1*0010*#1*130*0##")
    assert "*1*10*0010##" in _after("*1*30*0010##")
    after_down = _after("*1*31*0010##")
    assert "*1*9*0010##" in after_down and "*#1*0010*1*179*4##" in after_down
    after_step = _after("*1*30#2#255*0010##")
    assert "*#1*0010*1*181*4##" in after_step and "*#1*0010*4*181*2##" in after_step
    assert "*#1*0010*4*181*2##" in _after("*1*1#255*0010##")
    assert frames.count("*#1001*0010*11*111110111111111101100111##") == 16
    text = LOG.read_text(encoding="utf-8")
    assert "Unsupported message type: `*#1001*0010*11*111110111111111101100111##`" in text


def _rejected() -> list[str]:
    """Status requests MyHOME reports as NACKed (`Gateway rejected status request X (NACK ...)`)."""
    return [m.group(1) for line in _lines() if (m := re.search(r"rejected status request (\S+) \(NACK", line))]


def test_shutter_dimension_11_is_nacked_and_advanced_commands_echo_as_1000_events() -> None:
    rejected = _rejected()
    assert "*#2*05*11##" in rejected and "*#2*05*10##" not in rejected
    frames = [f for _, f in _received()]
    assert "*2*0*05##" in frames and "*2*1000#0*05##" in frames
    # The advanced command echoes arrive on the event session while the command is still queued,
    # so they are pinned as present rather than as "after the send".
    assert frames.count("*2*1000#11#1#001#1*05##") == 1
    assert frames.count("*2*1000#10#001#1*05##") == 2
    assert frames.count("*2*1000#12#1#001#1*05##") == 1
    assert frames.count("*2*1000#13#001#1*05##") == 1
    assert frames.count("*2*1000#14#001#1*05##") == 1
    raws = [f["raw"] for f in _sweep()["frames"]]
    for raw in ("*2*1000#11#1#001#1*05##", "*2*1000#10#001#1*05##", "*2*1000#13#001#1*05##", "*2*1000#14#001#1*05##"):
        assert raw in raws


def test_zone_2_reads_central_forms_nacked_and_mode_events() -> None:
    rejected = _rejected()
    for frame in ("*#4*#2##", "*#4*#0##", "*#4*#0*19##", "*#17##", "*#4*2*11##"):
        assert frame in rejected, frame
    for frame in ("*#4*2*0##", "*#4*2*12##", "*#4*2*13##", "*#4*2*14##", "*#4*2*19##", "*#4*2*20##", "*#4*2*60##", "*#4*2#1*20##"):
        assert frame not in rejected, frame
    frames = [f for _, f in _received()]
    for frame in ("*#4*2*19*5*0##", "*#4*2#1*20*0##", "*#4*2#2*20*5##", "*#4*2*60*41##", "*#4*2*13*00##", "*#4*2*11*0##"):
        assert frame in frames, frame
    assert "*4*0*2##" in _after("*#4*2*#14*0245*2##")
    assert "*4*1*2##" in _after("*#4*2*#14*0245*1##")
    assert "*4*303*2##" in _after("*4*303*2##")
    assert "*#1*0010*4*200*2##" in frames


def test_sweep_export_window_and_distribution() -> None:
    doc = _sweep()
    frames = doc["frames"]
    assert len(frames) == 200 and doc["capture"]["window"]["truncated"] is True
    assert frames[0]["raw"] == "*#1*0010##" and frames[-1]["raw"] == "*#18*79#0*1200##"
    assert collections.Counter(f["direction"] for f in frames) == {"rx": 103, "tx": 97}
    assert collections.Counter(str(f["who"]) for f in frames) == {
        "4": 56, "2": 44, "1": 40, "18": 36, "13": 10, "1001": 9, "14": 2, "1013": 2, "17": 1,
    }
    assert sum(1 for f in frames if f["raw"] == "*14*1*0##") == 2
    for f in frames:
        assert OWNMessage.parse(f["raw"]) is not None, f["raw"]
