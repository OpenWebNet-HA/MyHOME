"""A WHO 4 frame reaches a heating zone only when its WHERE names that zone.

Twice a piece of a WHERE was taken for a zone: the actuator of ``Z#N`` and the pump
``0#N`` (#333, #431), then the probe of ``PZZ`` (#549). Each time the fix was one
more special case. This test states the grammar once, independently of the routing
code, and replays every WHO 4 frame in the repository's fixtures through it, so a
new spelling that is routed by its digits fails here instead of on a user's plant.

WHO 4 WHERE (OpenWebNet thermoregulation):

* ``1``-``99``          zone;  ``#0`` central unit
* ``Z#N``               actuator N of zone Z: the frame concerns zone Z
* ``0#N``               pump N (actuator N of zone 0): no zone
* ``PZZ`` (>= 100)      probe P of zone ZZ: a sensor frame, never a zone's
* ``*4*4001#Z*0#N##``   zone Z calls pump N: the frame concerns zone Z
"""

import json
import re
from pathlib import Path

import pytest
from OWNd.message import OWNMessage

from custom_components.myhome.climate import (
    _calling_zones,
    _zone_address,
    _zone_route_keys,
)
from custom_components.myhome.where_grammar import is_probe

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FRAME = re.compile(r"^\*#?4\*")

ZONE = re.compile(r"^[1-9]\d?$")
ACTUATOR = re.compile(r"^([1-9]\d?)#\d+$")
PUMP = re.compile(r"^0#\d+$")
PROBE = re.compile(r"^0?[1-9]\d\d$")
CENTRAL = re.compile(r"^#0$")


def expected_zones(where: str) -> set[str] | None:
    """Zones a WHERE names by the grammar above; ``None`` where the grammar does not decide."""
    if ZONE.match(where):
        return {where}
    if m := ACTUATOR.match(where):
        return {m.group(1)}
    if CENTRAL.match(where):
        return {"#0"}
    if PUMP.match(where) or PROBE.match(where) or where == "0":
        return set()
    return None


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _fixture_frames() -> list[str]:
    frames: set[str] = set()
    for path in sorted(FIXTURES.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        frames.update(s for s in _strings(data) if s.endswith("##") and FRAME.match(s))
    return sorted(frames)


def _who4_message(raw: str):
    try:
        message = OWNMessage.parse(raw)
    except Exception:
        return None
    return message if getattr(message, "who", None) == 4 else None


GRAMMAR = [
    ("*#4*5*0*0210##", {"5"}),
    ("*#4*99*0*0210##", {"99"}),
    ("*#4*12#1*20*1##", {"12"}),
    ("*#4*0#3*20*1##", set()),
    ("*#4*105*0*0296##", set()),
    ("*#4*169*0*0250##", set()),
    ("*4*1*105##", set()),
    ("*4*4001#12*0#3##", {"12"}),
]


@pytest.mark.parametrize(("raw", "zones"), GRAMMAR)
def test_grammar_examples(raw, zones):
    message = OWNMessage.parse(raw)
    assert set(_calling_zones(message)[0]) == zones


@pytest.mark.parametrize("where", ["12#1", "99#3", "5#2", "0#3", "105", "169", "0105"])
def test_no_raw_where_route_key_unless_it_is_a_zone(where):
    """A sub-part of a WHERE (``12`` of ``12#1``) is never enough to deliver a frame to a zone it does not name."""
    frame = {"105": "*#4*105*0*0296##", "169": "*#4*169*0*0250##", "0105": "*#4*0105*0*0296##"}.get(
        where, f"*#4*{where}*20*1##"
    )
    message = OWNMessage.parse(frame)
    expected = expected_zones(where)
    assert expected is not None
    keys = set(_zone_route_keys(message, _zone_address(message)))
    assert {k for k in keys if k.isdigit()} <= expected | {where if ZONE.match(where) else ""}


def test_fixtures_hold_who4_frames():
    """The replay below is worthless if the fixtures stop being found."""
    assert sum(1 for raw in _fixture_frames() if _who4_message(raw)) > 200


def test_every_recorded_who4_frame_reaches_only_the_zones_its_where_names():
    """Replay every WHO 4 frame in tests/fixtures; the routing must agree with the grammar."""
    checked = 0
    for raw in _fixture_frames():
        message = _who4_message(raw)
        if message is None:
            continue
        where = str(getattr(message, "where", ""))
        expected = expected_zones(where)
        if expected is None:
            continue
        zones = set(_calling_zones(message)[0])
        what = getattr(message, "what", None) or getattr(message, "_what", None)
        if what in ("4001", "4002", 4001, 4002):
            continue  # the calling zone is in WHAT; checked by test_grammar_examples
        assert zones <= expected, (
            f"{raw}: routed to zone(s) {sorted(zones - expected)}, WHERE {where!r} names {sorted(expected)}"
        )
        if PROBE.match(where):
            assert is_probe(where) and _zone_address(message) is None, raw
        checked += 1
    assert checked > 200
