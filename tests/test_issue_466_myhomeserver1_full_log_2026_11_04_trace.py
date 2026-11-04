"""#466: 3488 HOMETOUCH with MyHomeServer1 (FW 3.2.5) connected — full Home Assistant debug log.

Contributed by the reporter of the 2026-11-04 MyHomeServer1 sweep (#466), as the complete,
untruncated ``home-assistant_myhome`` log of the whole session (exported 10:30:17Z); the Bus Monitor
export of that morning (``myhome_sweep_MyHomeServer1_all_2026-11-04T09-35-58.json``) is a 200-frame
buffer window that does not overlap this log's DEBUG traffic, so both are kept.

The gateway is a MyHomeServer1: the integration auto-detects firmware ``3.2.5`` via WHO 13 dimension
16, WHO 13 reports device type ``200`` and the ``1013-1-128`` model code, and the MAC carries the
BTicino OUI. The plant is a 3488 HOMETOUCH touch screen connected to that MyHomeServer1: the 3488 is
not a web server and cannot be the OpenWebNet endpoint, so OWNd connects over IP, port and password
to the MyHomeServer1 and every frame below is received from it. Mid-session (11:44:36 local) the
reporter re-configured the entry from the ``MyHomeServer1`` profile to ``Generic``, so every one of
the 1374 inbound frames in the log is received under the Generic profile on MyHomeServer1 hardware.

Late in the session (12:27-12:30 local) the reporter used the 3488's video door-entry functions,
which puts WHO 7 (video) and WHO 8 (door entry) traffic on the event session. The WHO 7 event-form
frames carry an empty WHERE (``*7*73#1#100*##``, ``*7*300#127#0#0#1#5000#2*##``, ``*7*0*##``, ...)
and OWNd returns ``None`` for them on 2.0.0b13 and on dev alike, which MyHOME logs as
"Data received is not a message"; the WHO 7 dimension forms and all WHO 8 frames parse.

Home Assistant 2026.10.4, MyHOME 2.0.0b17, OWNd 2.0.0b13. The log is verbatim from the first
``custom_components.myhome`` line; only the gateway address, an unrelated LAN host in two
``aioshelly`` error lines, and the MAC were made synthetic (``192.0.2.9``, ``192.0.2.26``,
``00:03:50:00:11:04``), as for the 2026-09-29 debug-log fixture.
"""

from __future__ import annotations

import collections
import hashlib
import re
from pathlib import Path

from OWNd.message import (
    OWNEvent,
    OWNHeatingEvent,
    OWNIntercomEvent,
    OWNLightingEvent,
    OWNMessage,
)

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures/traces/issue_466/MyHomeServer1_full_debug_log_2026-11-04T10-30-17.txt"
)
SHA256 = "998a6ba7186573f35a3fdd2e7ce0665bf53e003f0ca830998848cc3f6fe64d5b"

# Same shape as the 2026-09-29 debug-log fixture test: DEBUG lines of the integration logger.
LOG_LINE = re.compile(
    r"^(\S+ \S+) DEBUG \(MainThread\) \[custom_components\.myhome\] (?:\[[^\]]*\] )?(.*)$"
)
# Any integration line, keeping the level and the "[<label> gateway - <addr>]" prefix.
ANY_LINE = re.compile(
    r"^(\S+ \S+) (DEBUG|INFO|WARNING) \(MainThread\) \[custom_components\.myhome(?:\.\w+)?\] "
    r"(?:\[([^\]]*)\] )?(.*)$"
)
RECEIVED = re.compile(r"^Message received: `([^`]+)`$")

SYNTHETIC_ADDRESS = "192.0.2.9"
SYNTHETIC_OTHER_HOST = "192.0.2.26"
SYNTHETIC_MAC = "00:03:50:00:11:04"


def _lines() -> list[str]:
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def _entries() -> list[tuple[str, str, str | None, str]]:
    out = []
    for line in _lines():
        m = ANY_LINE.match(line)
        if m:
            out.append((m.group(1), m.group(2), m.group(3), m.group(4)))
    return out


def _received() -> list[tuple[str, str | None, str]]:
    """(timestamp, gateway label, raw frame) for every inbound frame."""
    out = []
    for ts, _level, label, text in _entries():
        m = RECEIVED.match(text)
        if m:
            out.append((ts, label, m.group(1)))
    return out


def _who(raw: str) -> str:
    m = re.match(r"\*#?(\d+)\*", raw)
    assert m, raw
    return m.group(1)


def test_fixture_provenance_is_pinned() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == SHA256


def test_capture_environment_is_the_myhomeserver1_3_2_5_on_b17() -> None:
    texts = [t for _, _, _, t in _entries()]
    assert "Initializing MyHOME integration v2.0.0b17 (OWNd v2.0.0b13)" in texts
    assert "Auto-detected gateway firmware `3.2.5` via WHO=13 Dimension 16." in texts
    assert any(
        t.startswith("WHO=13 reports device type 200 (seen on multiple modern gateways)")
        for t in texts
    )
    assert any(t.startswith("The gateway reports model code 1013-1-128") for t in texts)
    assert any(t.startswith("Setting up MyHOME gateway 'MyHomeServer1 Gateway'") for t in texts)


def test_no_gateway_network_identity_is_committed() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    ips = set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text))
    macs = set(re.findall(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", text))
    assert ips == {SYNTHETIC_ADDRESS, SYNTHETIC_OTHER_HOST}
    assert macs == {SYNTHETIC_MAC}
    # The BTicino OUI is kept so the MAC-prefix identification path stays meaningful.
    assert SYNTHETIC_MAC.startswith("00:03:50:")
    # The second host only ever appears in an unrelated integration's error lines.
    for line in _lines():
        if SYNTHETIC_OTHER_HOST in line:
            assert "[aioshelly." in line and "custom_components.myhome" not in line


def test_entry_was_switched_from_myhomeserver1_to_generic_profile_mid_session() -> None:
    labels = [(ts, label) for ts, _, label, _ in _entries() if label]
    assert labels, "no labelled gateway lines"
    first_generic = next(ts for ts, label in labels if label.startswith("Generic gateway"))
    last_mhs1 = max(ts for ts, label in labels if label.startswith("MyHomeServer1 gateway"))
    assert last_mhs1 < first_generic
    # The Bus Monitor / DEBUG traffic is entirely after the switch.
    assert {label for _, label, _ in _received()} == {f"Generic gateway - {SYNTHETIC_ADDRESS}"}


def test_inbound_frames_span_and_who_distribution() -> None:
    received = _received()
    assert len(received) == 1374
    assert received[0][0] == "2026-11-04 11:47:38.921"
    assert received[-1][0] == "2026-11-04 12:30:11.946"
    whos = collections.Counter(_who(raw) for _, _, raw in received)
    assert whos == {"4": 950, "1": 349, "7": 44, "8": 29, "130": 2}


def test_parse_coverage_and_class_counts_are_ownd_version_stable() -> None:
    raws = [raw for _, _, raw in _received()]
    messages = [OWNMessage.parse(raw) for raw in raws]
    parsed = [m for m in messages if m is not None]
    unparsed = [raw for raw, m in zip(raws, messages) if m is None]
    assert len(parsed) == 1338
    assert len(unparsed) == 36
    kinds = collections.Counter(type(m).__name__ for m in parsed)
    # 950 WHO 4 (752 events + 198 reads parsed as commands), 349 WHO 1 lighting events, 29 WHO 8
    # door-entry events, and 10 base OWNEvent: the 6 WHO 7 dimension forms, the 2 WHO 7 event forms
    # that carry a WHERE, and the 2 undocumented WHO 130 broadcasts. Identical on b13 and dev.
    assert kinds["OWNHeatingEvent"] == 752
    assert kinds["OWNHeatingCommand"] == 198
    assert kinds["OWNLightingEvent"] == 349
    assert kinds["OWNIntercomEvent"] == 29
    assert kinds["OWNEvent"] == 10
    assert set(kinds) == {
        "OWNHeatingEvent",
        "OWNHeatingCommand",
        "OWNLightingEvent",
        "OWNIntercomEvent",
        "OWNEvent",
    }
    # Every unparsed frame is a WHO 7 event form with an empty WHERE (`*7*<what>*##`).
    assert all(_who(raw) == "7" for raw in unparsed)
    assert all(raw.endswith("*##") and not raw.startswith("*#") for raw in unparsed)


def test_who7_video_event_form_with_empty_where_is_unparsed_and_logged_by_myhome() -> None:
    lines = _lines()
    raws = [raw for _, _, raw in _received() if _who(raw) == "7"]
    assert len(raws) == 44
    assert "*7*73#1#100*##" in raws and "*7*0*##" in raws
    assert "*7*300#127#0#0#1#5000#2*##" in raws
    assert OWNMessage.parse("*7*73#1#100*##") is None
    assert OWNMessage.parse("*7*300#127#0#0#1#5000#2*##") is None
    # The two event forms that do carry a WHERE parse (to the base event), as do the dimension forms.
    assert isinstance(OWNMessage.parse("*7*72*20##"), OWNEvent)
    assert isinstance(OWNMessage.parse("*#7**20*50*50*50*75*50##"), OWNEvent)
    # MyHOME reports every unparsed frame on the next line; 45 such warnings in the log.
    assert sum("Data received is not a message: `*7*" in line for line in lines) == 45
    idx = next(i for i, line in enumerate(lines) if "Message received: `*7*73#0#0*##`" in line)
    assert "Data received is not a message: `*7*73#0#0*##`" in lines[idx + 1]


def test_who8_door_entry_events_decode() -> None:
    raws = [raw for _, _, raw in _received() if _who(raw) == "8"]
    assert len(raws) == 29
    assert "*8*3#5#4*410##" in raws and "*#8**35*1*0*0##" in raws
    for raw in raws:
        assert isinstance(OWNMessage.parse(raw), OWNIntercomEvent)


def test_undocumented_who_130_broadcast_is_tolerated() -> None:
    raws = [raw for _, _, raw in _received() if raw.startswith("*#130*")]
    assert raws == ["*#130**1*6*3*2*5##"] * 2
    msg = OWNMessage.parse(raws[0])
    assert msg is not None
    assert type(msg) is OWNEvent


def test_who1_four_digit_where_lights_decode() -> None:
    raws = [raw for _, _, raw in _received() if raw.startswith("*1*")]
    assert "*1*0*0115##" in raws
    for raw in raws:
        if re.match(r"\*1\*\d+\*\d{4}##$", raw):
            assert isinstance(OWNMessage.parse(raw), OWNLightingEvent)


def test_who4_manual_mode_and_actuator_state_decode_to_entity_updates() -> None:
    lines = _lines()
    raws = [raw for _, _, raw in _received() if raw.startswith(("*4*", "*#4*"))]
    assert "*4*4002*59##" in raws
    assert isinstance(OWNMessage.parse("*4*4002*59##"), OWNHeatingEvent)
    # The dimension-20 actuator read is consumed and logged as a zone update on the very next line.
    idx = next(i for i, line in enumerate(lines) if "Message received: `*#4*59#1*20*0##`" in line)
    assert "Zone 59's actuator 1 is off." in lines[idx + 1]


def test_gateway_echoes_the_dimension_write_form_which_myhome_does_not_consume() -> None:
    lines = _lines()
    # MyHomeServer1 echoes `*#4*Z#N*#20*V##` (the write form) right before the `*20*` read form.
    # MyHOME logs the write form as unsupported and takes the state from the read form.
    idx = next(i for i, line in enumerate(lines) if "Message received: `*#4*59#1*#20*0##`" in line)
    assert "Unsupported message type: `*#4*59#1*#20*0##`" in lines[idx + 1]
    assert "Message received: `*#4*59#1*20*0##`" in lines[idx + 2]
    assert OWNMessage.parse("*#4*59#1*#20*0##") is not None
