"""#466: BTicino F459 gateway — full Home Assistant debug log of a real plant (2026-11-05).

The F459 was not in the trace matrix before this capture. The plant has 158 lights (67 addresses
answering, 20 behind an F422 bus interface at ``#4#01``), 40 covers and 19 thermostat zones; the log
is the complete ``home-assistant_myhome`` export of the session (exported 16:23:24Z), verbatim from
the first ``custom_components.myhome`` line, with only the gateway address and MAC made synthetic
(``192.0.2.59``, ``00:03:50:00:11:05``). Home Assistant 2026.10.x, MyHOME 2.0.0b17, OWNd 2.0.0b13.

What the F459 firmware shows on the wire:

- WHO 13 dimension 15 answers device type ``200``; WHO 1013 dimension 1 answers
  ``*#1013**1*65*15*5*0##`` (object model F459).
- The general lighting status ``*#1*0##`` completes (79 frames, acknowledgement after 6.0 s). The
  general automation status ``*#2*0##`` delivers 44 cover frames in 3.2 s and never terminates:
  MyHOME's 10 s window expires, the command session is closed and the covers are read one by one.
- WHO 1 requests and status are routed across the F422 (``#4#01``) in both directions.
- Energy reads ``*#18*51*51##`` / ``*#18*51*113##`` are acknowledged with no reply frame.

Every one of the 613 inbound frames parses on OWNd 2.0.0b13 and on dev alike.
"""

from __future__ import annotations

import collections
import hashlib
import re
from pathlib import Path

from OWNd.message import (
    OWNAutomationEvent,
    OWNGatewayDiagnosticEvent,
    OWNGatewayEvent,
    OWNHeatingEvent,
    OWNLightingEvent,
    OWNMessage,
)

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures/traces/issue_466/F459_full_debug_log_2026-11-05T16-23-24.txt"
)
SHA256 = "4297921f6cfae59e5318507b49def4dd5ce68864ef5120a12bb1b3e636d3e516"

# Same shape as the other debug-log fixture tests: integration lines, keeping the level and the
# "[<label> gateway - <addr>]" prefix. This export carries a UTC offset on the timestamp.
ANY_LINE = re.compile(
    r"^(\S+ \S+) (DEBUG|INFO|WARNING|ERROR) \(MainThread\) \[custom_components\.myhome(?:\.\w+)?\] "
    r"(?:\[([^\]]*)\] )?(.*)$"
)
RECEIVED = re.compile(r"^Message received: `([^`]+)`$")
QUEUED = re.compile(r"^Message `([^`]+)` was successfully queued\.$")
COLLECTED = re.compile(r"^Collected command response `([^`]+)`\.$")

SYNTHETIC_ADDRESS = "192.0.2.59"
SYNTHETIC_MAC = "00:03:50:00:11:05"
LABEL = f"F459 gateway - {SYNTHETIC_ADDRESS}"


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


def _queued() -> list[tuple[str, str]]:
    return [(ts, m.group(1)) for ts, _, _, t in _entries() for m in [QUEUED.match(t)] if m]


def _who(raw: str) -> str:
    m = re.match(r"\*#?(\d+)\*", raw)
    assert m, raw
    return m.group(1)


def test_fixture_provenance_is_pinned() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == SHA256


def test_capture_environment_is_the_f459_on_b17() -> None:
    texts = [t for _, _, _, t in _entries()]
    assert texts.count("Setting up MyHOME gateway 'F459 Gateway' (v2.0.0b17, OWNd v2.0.0b13)") == 2
    assert (
        "WHO=13 reports device type 200 (seen on multiple modern gateways); keeping model `F459` "
        "until WHO=1013 answers." in texts
    )
    assert "Requesting WHO=1013 dimension 1 (OBJECT_MODEL) to settle the model." in texts
    assert "Skipping `*#4*0##`: F459 firmware refuses it." in texts
    assert "Skipping `*#16*0*5##`: F459 firmware refuses it." in texts
    assert "Skipping WHO=5 discovery: not supported by F459 profile." in texts
    # Entity inventory of the plant, from the restore lines.
    assert f"{SYNTHETIC_MAC}: light restored/configured 158 entities (158 addresses known)" in texts
    assert f"{SYNTHETIC_MAC}: cover restored/configured 40 entities (41 addresses known)" in texts
    assert f"{SYNTHETIC_MAC}: climate restored/configured 19 entities (19 addresses known)" in texts


def test_no_gateway_network_identity_is_committed() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    ips = set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text))
    macs = set(re.findall(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", text))
    assert ips == {SYNTHETIC_ADDRESS}
    assert macs == {SYNTHETIC_MAC}
    # The BTicino OUI is kept so the MAC-prefix identification path stays meaningful.
    assert SYNTHETIC_MAC.startswith("00:03:50:")
    # OPEN password authentication: numeric nonces only, never a password line with a value.
    assert sum("Received nonce: `*#" in line for line in _lines()) == 4
    assert not any("SHA challenge" in line for line in _lines())


def test_setup_flow_connection_refusals_and_retries() -> None:
    texts = [(ts, lv, t) for ts, lv, _, t in _entries()]
    # Three refused test-session connects right after the yaml reload, then ConfigEntryNotReady.
    refused = [ts for ts, lv, t in texts if t.startswith("Test session connection failed")]
    assert len(refused) == 3
    assert refused[0].startswith("2026-11-05 18:20:41") and refused[-1].startswith(
        "2026-11-05 18:21:04"
    )
    assert any(
        lv == "ERROR" and t == "Test session connection still refused after 3 attempts."
        for _, lv, t in texts
    )
    # 5 s later the test session is accepted at once.
    assert any(
        ts.startswith("2026-11-05 18:21:13") and t == "Test session established successfully."
        for ts, _, t in texts
    )
    # First command-session connect after the event session opened times out; the retry succeeds.
    assert any(
        t == "Command session: connect timed out (10s). Retrying in 1s (attempt 1/5)."
        for _, _, t in texts
    )
    established = [ts for ts, _, t in texts if t == "Command session established successfully."]
    assert len(established) == 2
    assert established[0].startswith("2026-11-05 18:21:24")


def test_inbound_frames_span_and_who_distribution() -> None:
    received = _received()
    assert len(received) == 613
    assert received[0][0] == "2026-11-05 18:21:24.924+02:00"
    assert received[-1][0] == "2026-11-05 18:23:08.283+02:00"
    assert {label for _, label, _ in received} == {LABEL}
    whos = collections.Counter(_who(raw) for _, _, raw in received)
    assert whos == {"1": 427, "2": 126, "4": 53, "13": 5, "1013": 2}


def test_parse_coverage_and_class_counts_are_ownd_version_stable() -> None:
    raws = [raw for _, _, raw in _received()]
    messages = [OWNMessage.parse(raw) for raw in raws]
    unparsed = [raw for raw, m in zip(raws, messages) if m is None]
    assert unparsed == []
    kinds = collections.Counter(type(m).__name__ for m in messages)
    assert kinds == {
        "OWNLightingEvent": 427,
        "OWNAutomationEvent": 126,
        "OWNHeatingEvent": 53,
        "OWNGatewayEvent": 5,
        "OWNGatewayDiagnosticEvent": 2,
    }
    lines = _lines()
    assert not any("Data received is not a message" in line for line in lines)
    assert not any("Unsupported message type" in line for line in lines)


def test_who13_device_type_200_and_who1013_object_model_f459() -> None:
    received = _received()
    device_type = [(ts, raw) for ts, _, raw in received if _who(raw) == "13"]
    assert [raw for _, raw in device_type] == ["*#13**15*200##"] * 5
    # Received at 25 s intervals, with no queued `*#13**15##` in the log.
    assert [ts[11:19] for ts, _ in device_type] == [
        "18:21:28",
        "18:21:53",
        "18:22:18",
        "18:22:43",
        "18:23:08",
    ]
    assert not any(raw.startswith("*#13*") for _, raw in _queued())
    msg = OWNMessage.parse("*#13**15*200##")
    assert isinstance(msg, OWNGatewayEvent)
    assert msg.dimension == 15 and msg.dimension_value == ["200"]

    diagnostics = [raw for _, _, raw in received if _who(raw) == "1013"]
    assert diagnostics == [
        "*#1013**1*65*15*5*0##",
        "*#1013*260*11*111111111111111101101111##",
    ]
    model = OWNMessage.parse(diagnostics[0])
    assert isinstance(model, OWNGatewayDiagnosticEvent)
    assert model.dimension == 1 and model.dimension_value == ["65", "15", "5", "0"]
    assert model.device_type == "F459"
    assert "Message `*#1013**1##` was successfully sent." in {t for _, _, _, t in _entries()}
    other = OWNMessage.parse(diagnostics[1])
    assert isinstance(other, OWNGatewayDiagnosticEvent)
    assert other.where == "260" and other.dimension == 11
    assert (
        sum("Unhandled WHO=1013 diagnostic message: `*#1013*260*11*" in line for line in _lines())
        == 1
    )


def test_general_lighting_status_completes_but_general_automation_status_never_terminates() -> None:
    entries = _entries()
    texts = [(ts, t) for ts, _, _, t in entries]

    def _ts(text: str) -> str:
        return next(ts for ts, t in texts if t == text)

    # `*#1*0##`: unqueued 18:21:24.806, acknowledged ("successfully sent") 6.0 s later.
    assert _ts("Message `*#1*0##` was successfully unqueued by worker 0.").startswith(
        "2026-11-05 18:21:24.806"
    )
    assert _ts("Message `*#1*0##` was successfully sent.").startswith("2026-11-05 18:21:30.848")
    # `*#2*0##`: unqueued 18:21:41.085, 44 cover frames by 18:21:44.5, then nothing until MyHOME's
    # 10 s window closes the command session.
    assert _ts("Message `*#2*0##` was successfully unqueued by worker 0.").startswith(
        "2026-11-05 18:21:41.085"
    )
    timeouts = [
        ts
        for ts, t in texts
        if t == "Timed out awaiting the complete response for `*#2*0##`; command session closed."
    ]
    assert [ts[11:19] for ts in timeouts] == ["18:15:32", "18:21:51"]
    window = [
        raw
        for ts, _, raw in _received()
        if "2026-11-05 18:21:41.085" <= ts <= "2026-11-05 18:21:51.087" and raw.startswith("*2*")
    ]
    assert len(window) == 44
    assert not any(t == "Message `*#2*0##` was successfully sent." for _, t in texts)
    # Fallback: the covers are then read one by one.
    per_cover = [raw for _, raw in _queued() if re.match(r"\*#2\*\d+##$", raw) and raw != "*#2*0##"]
    assert len(per_cover) == 40
    assert _ts("Message `*#2*0711##` was successfully sent.").startswith("2026-11-05 18:22:31")


def test_who2_covers_all_report_stopped() -> None:
    raws = [raw for _, _, raw in _received() if raw.startswith("*2*")]
    assert len(raws) == 126
    assert {raw.split("*")[2] for raw in raws} == {"0"}
    assert len({raw.split("*")[3].rstrip("#") for raw in raws}) == 40
    for raw in ("*2*0*0711##", "*2*0*37##", "*2*0*0014##"):
        assert raw in raws
        assert isinstance(OWNMessage.parse(raw), OWNAutomationEvent)


def test_who1_lighting_is_routed_across_the_f422_interface_in_both_directions() -> None:
    raws = [raw for _, _, raw in _received() if _who(raw) == "1"]
    assert len(raws) == 427
    events = [raw for raw in raws if raw.startswith("*1*")]
    levels = [raw for raw in raws if re.match(r"\*#1\*[0-9#]+\*1\*\d+\*\d+##$", raw)]
    assert len(events) == 238
    assert len(levels) == 156
    assert "*1*1*#5##" in events  # group 5 ON event (MyHOME also reads `*#1*#5##`)
    # The remaining 33 frames are the dimension-4 form the F459 returns for an OFF dimmer
    # (see test_off_dimmers_answer_the_dimension_1_read_with_the_dimension_4_form).
    assert len(raws) - len(events) - len(levels) == 33
    routed = [raw for raw in raws if "#4#01" in raw]
    assert len(routed) == 161
    assert sum(raw.startswith("*1*") for raw in routed) == 82
    assert "*#1*0613#4#01*1##" in {raw for _, raw in _queued()}
    assert "*#1*0613#4#01*1*140*2##" in routed and "*1*0*72#4#01##" in routed
    msg = OWNMessage.parse("*#1*98#4#01*1*200*4##")
    assert isinstance(msg, OWNLightingEvent)
    assert msg.where == "98" and msg.interface == "01" and msg.brightness == 100
    msg = OWNMessage.parse("*1*0*72#4#01##")
    assert isinstance(msg, OWNLightingEvent)
    assert msg.where == "72" and msg.interface == "01" and msg.is_on is False
    lines = _lines()
    idx = next(
        i for i, line in enumerate(lines) if "Message received: `*#1*0615#4#01*1*154*4##`" in line
    )
    assert "Light 0615 on interface 01 is switched on at 54%." in lines[idx + 1]
    # Dimension-1 replies carry level 100..200 plus the speed step.
    assert {int(raw.split("*")[4]) for raw in levels} == {
        132,
        140,
        149,
        150,
        151,
        152,
        154,
        198,
        200,
    }
    assert {raw.split("*")[5].rstrip("#") for raw in levels} == {"2", "4", "5"}


def test_off_dimmers_answer_the_dimension_1_read_with_the_dimension_4_form() -> None:
    """`*#1*W*1##` to an OFF dimmer is answered `*#1*W*4*100*S##`, not the dimension-1 form."""
    entries = _entries()
    texts = [t for _, _, _, t in entries]
    dim4 = [raw for _, _, raw in _received() if re.match(r"\*#1\*[0-9#]+\*4\*", raw)]
    assert len(dim4) == 33
    assert {raw.split("*")[4] for raw in dim4} == {"100"}
    assert collections.Counter(raw.split("*")[5].rstrip("#") for raw in dim4) == {"2": 15, "4": 18}
    # Each one is collected as the command response to that light's dimension-1 read, and no
    # dimension-4 request was ever queued.
    collected = {m.group(1) for t in texts for m in [COLLECTED.match(t)] if m}
    assert set(dim4) <= collected
    assert not any(re.match(r"\*#1\*[0-9#]+\*4##$", raw) for _, raw in _queued())
    for raw in dim4:
        where = raw.split("*")[2]
        assert f"Message `*#1*{where}*1##` was successfully sent." in texts
    # No dimmer answers both forms: the dimension-1 replies are all ON levels (>= 132).
    ones = {raw.split("*")[2] for _, _, raw in _received() if re.match(r"\*#1\*[0-9#]+\*1\*", raw)}
    assert ones.isdisjoint({raw.split("*")[2] for raw in dim4})
    msg = OWNMessage.parse("*#1*86*4*100*4##")
    assert isinstance(msg, OWNLightingEvent)
    assert msg.dimension == 4 and msg.brightness == 0 and msg.is_on is False
    lines = _lines()
    idx = next(i for i, line in enumerate(lines) if "Message received: `*#1*86*4*100*4##`" in line)
    assert "Light 86 is switched off." in lines[idx + 1]


def test_who4_zones_are_polled_per_zone_and_decode() -> None:
    raws = [raw for _, _, raw in _received() if raw.startswith("*#4*")]
    assert len(raws) == 53
    dims = collections.Counter(re.match(r"\*#4\*\d+(?:#1)?\*(\d+)\*", raw).group(1) for raw in raws)
    assert dims == {"0": 19, "20": 22, "60": 12}
    zones = {raw.split("*")[2].split("#")[0] for raw in raws}
    assert len(zones) == 19 and {"29", "59"} <= zones
    queued = {raw for _, raw in _queued() if raw.startswith("*#4*")}
    assert "*#4*0##" not in queued
    assert {"*#4*59*0##", "*#4*59*20##", "*#4*59*60##"} <= queued
    temp = OWNMessage.parse("*#4*59*0*0236##")
    assert isinstance(temp, OWNHeatingEvent) and temp.zone == 59 and temp.main_temperature == 23.6
    actuator = OWNMessage.parse("*#4*59#1*20*0##")
    assert isinstance(actuator, OWNHeatingEvent) and actuator.zone == 59
    humidity = OWNMessage.parse("*#4*59*60*41##")
    assert isinstance(humidity, OWNHeatingEvent) and humidity.dimension == 60
    assert {int(raw.split("*")[4].rstrip("#")) for raw in raws if "*60*" in raw} == {
        37,
        38,
        40,
        41,
        42,
        45,
    }
    lines = _lines()
    idx = next(i for i, line in enumerate(lines) if "Message received: `*#4*59#1*20*0##`" in line)
    assert "Zone 59's actuator 1 is off." in lines[idx + 1]


def test_energy_reads_are_acknowledged_with_no_reply_and_five_lighting_reads_are_nacked() -> None:
    texts = [t for _, _, _, t in _entries()]
    for frame in ("*#18*51*51##", "*#18*51*113##"):
        assert f"Message `{frame}` was successfully sent." in texts
    assert not any(_who(raw) == "18" for _, _, raw in _received())
    rejected = [
        re.match(r"Gateway rejected status request (\S+) \(NACK, 0 response\(s\)\)", t).group(1)
        for t in texts
        if t.startswith("Gateway rejected status request")
    ]
    assert sorted(rejected) == sorted(
        ["*#1*25#4#01*1##", "*#1*0114##", "*#1*0113##", "*#1*0713#4#01##", "*#1*92*1##"]
    )
