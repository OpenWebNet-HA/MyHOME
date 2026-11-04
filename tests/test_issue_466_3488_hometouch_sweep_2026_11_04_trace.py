"""#466: 3488 HOMETOUCH with MyHomeServer1 (FW 3.2.5) connected — thermoregulation sweep.

Contributed by @gimprota76 in:
- https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-6626706525

The gateway is a MyHomeServer1 on firmware 3.2.5 (``identification.source == "manual"``,
``configured_model == "MyHomeServer1"``, WHO 13 code ``200``, ``MyHomeServer1Profile``). The plant is a
3488 HOMETOUCH touch screen connected to that MyHomeServer1: the 3488 is not a web server and cannot be
the OpenWebNet endpoint, so OWNd connects over IP, port and password to the MyHomeServer1.
Home Assistant 2026.10.4, MyHOME 2.0.0b17, OWNd 2.0.0b13.

A 200-frame window of the gateway's bus buffer (``truncated: true``) that is entirely WHO 4
thermoregulation: ~40 zones report their actuator state (dimension 20), setpoint and measured
temperature (dimensions 0 / 12 / 14 / 13), manual-mode (WHAT 4002) and heating season (``*4*1*Z##``),
with the central unit polled at ``#0`` and slave-probe dimension 60 reads.
"""

from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path

from OWNd.message import OWNMessage

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures/traces/issue_466/myhome_sweep_MyHomeServer1_all_2026-11-04T09-35-58.json"
)
SHA256 = "33eed24b5abdb953fcb628f3ebcdf473fc1af5f00347e87ffd889fba9de52e14"


def _doc() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _frames() -> list[dict]:
    return _doc()["frames"]


def _raws(who: int) -> list[str]:
    return [f["raw"] for f in _frames() if str(f.get("who")) == str(who)]


def test_fixture_provenance_is_pinned() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == SHA256


def test_capture_environment_is_the_myhomeserver1_3_2_5_on_b17() -> None:
    doc = _doc()
    # The export's gateway block: MyHomeServer1, firmware 3.2.5 (WHO 13 dimension 16), manual entry.
    assert doc["gateway"]["firmware"] == "3.2.5"
    assert doc["gateway"]["model"] == "MyHomeServer1"
    ident = doc["gateway"].get("identification", {})
    assert ident.get("source") == "manual"
    assert ident.get("configured_model") == "MyHomeServer1"
    assert str(ident.get("who13_firmware")) == "3.2.5"


def test_no_gateway_network_identity_is_committed() -> None:
    for raw in _raws(13):
        assert not raw.startswith(("*#13**10*", "*#13**11*", "*#13**12*"))


def test_every_frame_parses_and_is_all_who4() -> None:
    frames = _frames()
    assert len(frames) == 200
    messages = [OWNMessage.parse(f["raw"]) for f in frames]
    assert all(m is not None for m in messages)
    kinds = collections.Counter(type(m).__name__ for m in messages)
    # The window is entirely WHO 4: 192 events + 8 reads parsed as commands.
    assert kinds["OWNHeatingEvent"] == 192
    assert kinds["OWNHeatingCommand"] == 8
    assert set(kinds) == {"OWNHeatingEvent", "OWNHeatingCommand"}


def test_who4_thermoregulation_sweep_decodes() -> None:
    w4 = _raws(4)
    # Heating season, manual-mode report (WHAT 4002), dimension 14 setpoint reads, the central-unit
    # #0 actuator poll, and slave-probe dimension 60 reads are all present and parse as WHO 4.
    assert "*4*1*65##" in w4
    assert "*4*4002*65##" in w4
    assert any(r.startswith("*#4*65*14*") for r in w4)
    assert "*#4*#0*20##" in w4
    assert any(r.startswith("*#4*") and "*60*" in r for r in w4)
    for r in w4:
        assert OWNMessage.parse(r) is not None
