"""#466: BTicino F459 (FW 2.1.5) — full Home Assistant debug log on MyHOME v2.0.1 / OWNd v2.0.2 (2026-11-26).

Sanitized full ``custom_components.myhome`` debug log of a real plant behind an F459
(163 lights including F422 ``#4#01``, 40 covers, 20 climate zones, 1 dry-contact binary
sensor). Adds the first ``WHO=25`` (dry contact) and first ``WHO=1001`` (lighting
autodiagnostic bitmask) captures on F459, plus explicit ``WHO=13`` ``Dim 16`` firmware
``2.1.5`` and full 5-dimension ``WHO=4`` zone + central-pump ``0#1..3`` coverage.
"""

from __future__ import annotations

import collections
import hashlib
import re
from pathlib import Path

from OWNd.message import (
    OWNAutomationEvent,
    OWNDryContactEvent,
    OWNGatewayDiagnosticEvent,
    OWNGatewayEvent,
    OWNHeatingEvent,
    OWNLightingDiagnosticEvent,
    OWNLightingEvent,
    OWNMessage,
)

from scripts.anonymize_plant_fixture import findings

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures/traces/f459_full_debug_log_2026-11-26"
FIXTURE = FIXTURE_DIR / "F459_full_debug_log_2026-11-26T15-46-40.log"
README = FIXTURE_DIR / "README.md"
SHA256 = "cd817823e9d58c6564d73e91bc7696f86274013edacbe1314e5ffa51b2674fce"

ANY_LINE = re.compile(
    r"^(\S+ \S+) (DEBUG|INFO|WARNING|ERROR) \(MainThread\) \[custom_components\.myhome(?:\.\w+)?\] "
    r"(?:\[([^\]]*)\] )?(.*)$"
)
RECEIVED = re.compile(r"^Message received: `([^`]+)`$")
QUEUED = re.compile(r"^Message `([^`]+)` was successfully queued\.$")
COLLECTED = re.compile(r"^Collected command response `([^`]+)`\.$")

SYNTHETIC_ADDRESS = "192.0.2.59"
SYNTHETIC_MAC = "00:03:50:00:11:26"
LABEL = f"F459 gateway - {SYNTHETIC_ADDRESS}"


def _lines() -> list[str]:
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def _entries() -> list[tuple[str, str, str | None, str]]:
    out: list[tuple[str, str, str | None, str]] = []
    for line in _lines():
        m = ANY_LINE.match(line)
        assert m is not None, f"Unexpected non-myhome log line: {line!r}"
        out.append((m.group(1), m.group(2), m.group(3), m.group(4)))
    return out


def _received() -> list[tuple[str, str | None, str]]:
    out: list[tuple[str, str | None, str]] = []
    for ts, _level, label, text in _entries():
        m = RECEIVED.match(text)
        if m:
            out.append((ts, label, m.group(1)))
    return out


def _queued() -> list[tuple[str, str]]:
    return [(ts, m.group(1)) for ts, _, _, t in _entries() for m in [QUEUED.match(t)] if m]


def _who(raw: str) -> str:
    m = re.match(r"^\*#?(\d+)\*", raw)
    assert m is not None, raw
    return m.group(1)


def test_fixture_provenance_and_privacy() -> None:
    """Fixture preserves exact SHA-256 and carries zero LAN IPs, real MACs, ULIDs, or room names."""
    raw_bytes = FIXTURE.read_bytes()
    assert hashlib.sha256(raw_bytes).hexdigest() == SHA256
    text = raw_bytes.decode("utf-8")
    assert findings(text) == []

    readme_text = README.read_text(encoding="utf-8")
    assert findings(readme_text) == []
    assert SHA256 in readme_text

    ips = set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text))
    macs = set(re.findall(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", text))
    assert ips == {SYNTHETIC_ADDRESS}
    assert macs == {SYNTHETIC_MAC}
    assert re.findall(r"\b01[A-Z0-9]{24}\b", text) == []

    # Every entity_id in post-discovery skip lines uses generic synthetic naming.
    entity_ids = re.findall(r"\blight\.[a-z0-9_]+\b", text)
    assert len(entity_ids) == 119
    assert all(re.fullmatch(r"light\.light_\d{3}", eid) for eid in entity_ids)

    # OPEN numeric-nonce authentication only; no password echoed.
    assert sum("Received nonce: `*#" in line for line in _lines()) == 3
    assert not any("SHA challenge" in line for line in _lines())


def test_capture_environment_and_identity() -> None:
    """F459 FW 2.1.5 runs on MyHOME v2.0.1 / OWNd v2.0.2 and reports WHO=13=200 + WHO=1013=65."""
    texts = [t for _, _, _, t in _entries()]
    assert "Setting up MyHOME gateway 'F459 Gateway' (v2.0.1, OWNd v2.0.2)" in texts
    assert (
        "WHO=13 reports device type 200 (seen on multiple modern gateways); keeping model `F459` "
        "until WHO=1013 answers." in texts
    )
    assert "Requesting WHO=1013 dimension 1 (OBJECT_MODEL) to settle the model." in texts
    assert "Gateway's firmware version is: 2.1.5." in texts
    assert "Skipping `*#4*0##`: F459 firmware refuses it." in texts
    assert "Skipping `*#16*0*5##`: F459 firmware refuses it." in texts
    assert "Skipping WHO=5 discovery: not supported by F459 profile." in texts

    assert f"{SYNTHETIC_MAC}: light restored/configured 163 entities (163 addresses known)" in texts
    assert f"{SYNTHETIC_MAC}: cover restored/configured 40 entities (41 addresses known)" in texts
    assert f"{SYNTHETIC_MAC}: climate restored/configured 20 entities (40 addresses known)" in texts
    assert (
        f"{SYNTHETIC_MAC}: binary_sensor restored/configured 1 entities (2 addresses known)"
        in texts
    )

    who13_rx = [raw for _, _, raw in _received() if _who(raw) == "13"]
    assert who13_rx.count("*#13**15*200##") == 11
    assert who13_rx.count("*#13**16*2*1*5##") == 1
    fw_msg = OWNMessage.parse("*#13**16*2*1*5##")
    assert isinstance(fw_msg, OWNGatewayEvent)
    assert fw_msg.dimension == 16 and fw_msg.dimension_value == ["2", "1", "5"]

    who1013_rx = [raw for _, _, raw in _received() if _who(raw) == "1013"]
    assert who1013_rx == [
        "*#1013**1*65*15*5*0##",
        "*#1013*260*11*111111111111111101101111##",
    ]
    model_msg = OWNMessage.parse(who1013_rx[0])
    assert isinstance(model_msg, OWNGatewayDiagnosticEvent)
    assert model_msg.device_type == "F459"


def test_inbound_frames_and_ownd_parse_coverage() -> None:
    """All 818 inbound frames across 7 WHO subsystems parse cleanly in OWNd."""
    received = _received()
    assert len(received) == 818
    assert received[0][0] == "2026-11-26 17:42:28.923+02:00"
    assert received[-1][0] == "2026-11-26 17:46:21.974+02:00"
    assert {label for _, label, _ in received} == {LABEL}

    whos = collections.Counter(_who(raw) for _, _, raw in received)
    assert whos == {
        "1": 528,
        "2": 127,
        "4": 138,
        "13": 12,
        "25": 1,
        "1001": 10,
        "1013": 2,
    }

    messages = [OWNMessage.parse(raw) for _, _, raw in received]
    assert all(m is not None for m in messages)
    kinds = collections.Counter(type(m).__name__ for m in messages)
    assert kinds == {
        "OWNLightingEvent": 528,
        "OWNHeatingEvent": 138,
        "OWNAutomationEvent": 127,
        "OWNGatewayEvent": 12,
        "OWNLightingDiagnosticEvent": 10,
        "OWNGatewayDiagnosticEvent": 2,
        "OWNDryContactEvent": 1,
    }
    assert not any("Data received is not a message" in line for line in _lines())


def test_who25_dry_contact_and_who1001_lighting_diagnostics() -> None:
    """First F459 WHO=25 dry-contact response and WHO=1001 main-bus / #4#01 diagnostic masks."""
    texts = [t for _, _, _, t in _entries()]
    assert "Message `*#25*31##` was successfully queued." in texts
    assert "Message `*#25*31##` was successfully sent." in texts
    assert "Collected command response `*25*31#0*31##`." in texts
    assert "Sensor 1 reported ON." in texts

    who25_rx = [raw for _, _, raw in _received() if _who(raw) == "25"]
    assert who25_rx == ["*25*31#0*31##"]
    dry = OWNMessage.parse(who25_rx[0])
    assert isinstance(dry, OWNDryContactEvent)

    who1001_rx = [raw for _, _, raw in _received() if _who(raw) == "1001"]
    assert len(who1001_rx) == 10
    assert set(who1001_rx) == {
        "*#1001*52*11*011110111111110101100111##",
        "*#1001*0614#4#01*11*111110111111111101100111##",
        "*#1001*97#4#01*11*011110111111110101100111##",
    }
    for raw in set(who1001_rx):
        diag = OWNMessage.parse(raw)
        assert isinstance(diag, OWNLightingDiagnosticEvent)
        assert diag.dimension == 11


def test_who1_f422_dim1_dim4_and_translation_frame() -> None:
    """WHO=1 covers #4#01 routing, Dim 1 ON / Dim 4 OFF+ON dimmers, groups, and 1000# translation."""
    raws = [raw for _, _, raw in _received() if _who(raw) == "1"]
    assert len(raws) == 528
    events = [raw for raw in raws if raw.startswith("*1*")]
    dim1 = [raw for raw in raws if re.match(r"^\*#1\*[0-9#]+\*1\*", raw)]
    dim4 = [raw for raw in raws if re.match(r"^\*#1\*[0-9#]+\*4\*", raw)]
    assert len(events) == 283
    assert len(dim1) == 219
    assert len(dim4) == 26
    assert sum(1 for raw in dim4 if raw.split("*")[4] == "100") == 25
    assert "*#1*1009*4*200*2##" in dim4
    assert "*1*1000#1#255*1009##" in events

    routed = [raw for raw in raws if "#4#01" in raw]
    assert len(routed) == 208
    assert isinstance(OWNMessage.parse("*#1*0615#4#01*4*100*2##"), OWNLightingEvent)

    texts = [t for _, _, _, t in _entries()]
    assert "Ignoring translation message `*1*1000#1#255*1009##`" in texts
    assert "group #6 had 13 leading member echoes, skipping sweep" in texts
    assert "group #5#4#01 had 2 leading member echoes, skipping sweep" in texts


def test_who2_general_timeout_and_per_cover_fallback() -> None:
    """`*#2*0##` times out on F459 and falls back to polling all 40 covers individually."""
    texts = [t for _, _, _, t in _entries()]
    assert (
        texts.count("Timed out awaiting the complete response for `*#2*0##`; command session closed.")
        == 2
    )
    per_cover = [raw for _, raw in _queued() if re.match(r"^\*#2\*\d+##$", raw) and raw != "*#2*0##"]
    assert len(per_cover) == 40

    w2_rx = [raw for _, _, raw in _received() if raw.startswith("*2*")]
    assert len(w2_rx) == 127
    assert len({raw.split("*")[3].rstrip("#") for raw in w2_rx}) == 40
    assert collections.Counter(raw.split("*")[2] for raw in w2_rx) == {"0": 124, "1": 3}
    assert "*2*1*0015##" in w2_rx
    assert isinstance(OWNMessage.parse("*2*1*0015##"), OWNAutomationEvent)


def test_who4_five_dimensions_central_pumps_and_silent_zone_61() -> None:
    """WHO=4 captures 19 active zones across Dims 0/12/13/20/60 + season, 0#1..3 pumps, and silent zone 61."""
    w4_rx = [raw for _, _, raw in _received() if _who(raw) == "4"]
    assert len(w4_rx) == 138

    parsed = [OWNMessage.parse(raw) for raw in w4_rx]
    assert all(isinstance(m, OWNHeatingEvent) for m in parsed)
    dim_what = collections.Counter((m.dimension, m.what) for m in parsed if m is not None)
    assert dim_what == {
        (20, None): 43,
        (0, None): 19,
        (60, None): 19,
        (13, None): 19,
        (12, None): 19,
        (None, 1): 19,
    }
    for pump in ("*#4*0#1*20*0##", "*#4*0#2*20*0##", "*#4*0#3*20*0##"):
        assert pump in w4_rx

    texts = [t for _, _, _, t in _entries()]
    for nacked in ("*#4*61*0##", "*#4*61*20##", "*#4*61*13##"):
        assert any(
            t.startswith(f"Gateway rejected status request {nacked} (NACK, 0 response(s))")
            for t in texts
        )


def test_who18_energy_probes_acknowledged_without_replies() -> None:
    """Energy probes `*#18*51*51##` and `*#18*51*113##` are sent with zero WHO=18 replies."""
    texts = [t for _, _, _, t in _entries()]
    for frame in ("*#18*51*51##", "*#18*51*113##"):
        assert f"Message `{frame}` was successfully sent." in texts
    assert not any(_who(raw) == "18" for _, _, raw in _received())
