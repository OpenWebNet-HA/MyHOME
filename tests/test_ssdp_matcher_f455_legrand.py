"""An F455 that reports ``Legrand S.p.A.`` as manufacturer is offered by SSDP discovery.

Read from an F455 1.0.76 on 2026-11-18: its UPnP description holds ``manufacturer``
``Legrand S.p.A.``, ``modelName`` ``F455``, ``modelNumber`` ``1.0``. It answers Home Assistant's
search for ``ssdp:all`` with ``ST: ssdp:all`` (not with ``upnp:rootdevice``), and it announces
itself with ``NOTIFY ssdp:alive``, ``NT upnp:rootdevice``, ``max-age=500`` (seen 378 s and 417 s
apart). An announcement has no ``ST`` header, so with that search answer nothing on this gateway
carries ``st: upnp:rootdevice``: a matcher with that key did not start the flow for it (live), and
every matcher asked for ``BTicino S.p.A.`` anyway.
"""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / "custom_components" / "myhome" / "manifest.json"

# What Home Assistant matches: the headers of the packet plus the UPnP description.
_DESCRIPTION = {"manufacturer": "Legrand S.p.A.", "modelName": "F455"}
SEARCH_ANSWER = {"st": "ssdp:all", **_DESCRIPTION}
ANNOUNCEMENT = {"nt": "upnp:rootdevice", "nts": "ssdp:alive", **_DESCRIPTION}


def _matchers() -> list[dict[str, str]]:
    matchers: list[dict[str, str]] = json.loads(MANIFEST.read_text(encoding="utf-8"))["ssdp"]
    return matchers


def _is_matched(packet: dict[str, str]) -> bool:
    """Home Assistant starts the flow when every key of one matcher equals the packet's value."""
    return any(all(packet.get(key) == value for key, value in matcher.items()) for matcher in _matchers())


def test_the_search_answer_of_a_legrand_f455_is_matched() -> None:
    assert _is_matched(SEARCH_ANSWER)


def test_the_announcement_of_a_legrand_f455_is_matched() -> None:
    assert _is_matched(ANNOUNCEMENT)


def test_the_other_announcements_of_its_burst_are_not_matched() -> None:
    """One flow per burst: the device, UUID and service announcements start none."""
    for nt in ("urn:schemas-upnp-org:device:RoomC:1.0", "urn:schemas-bticino-it:service:Reachability:1"):
        assert not _is_matched({**ANNOUNCEMENT, "nt": nt})


def test_a_bticino_f455_is_matched_as_before() -> None:
    assert _is_matched({"st": "upnp:rootdevice", "manufacturer": "BTicino S.p.A.", "modelName": "F455"})


def test_another_model_under_the_legrand_name_is_not_matched() -> None:
    assert not _is_matched({**SEARCH_ANSWER, "modelName": "MH200N"})
    assert not _is_matched({**ANNOUNCEMENT, "modelName": "MH200N"})
