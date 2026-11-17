"""Replay of the live MH200 answers to Dimension 1 requests on relays and dimmers (#733).

tests/fixtures/traces/issue_733/README.md describes the capture.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.light import ColorMode
from homeassistant.core import HomeAssistant
from OWNd.message import OWNEvent

from custom_components.myhome.light import MyHOMELight

TRACE = (
    Path(__file__).parent
    / "fixtures"
    / "traces"
    / "issue_733"
    / "myhome_trace_MH200_dim1_probe_2026-10-20T19-22-47.json"
)
RELAYS = ("81", "84", "57", "11")
DIMMERS = ("29", "67")


def _frames() -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = json.loads(TRACE.read_text(encoding="utf-8"))["frames"]
    return frames


def _light(hass: HomeAssistant, where: str) -> MyHOMELight:
    """A light as bus discovery builds it from a plain ON/OFF frame."""
    light = MyHOMELight(
        hass=hass,
        name=f"Light {where}",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id=f"1-{where}",
        who="1",
        where=where,
        interface=None,
        dimmable=False,
        manufacturer="BTicino",
        model="Lighting Device",
        gateway=MagicMock(),
    )
    light.async_schedule_update_ha_state = MagicMock()
    return light


def _dim1_probe_replies() -> dict[str, list[str]]:
    """The lighting frames each *#1*W*1## request brought back, by WHERE."""
    replies: dict[str, list[str]] = {}
    current: str | None = None
    for frame in _frames():
        if frame["direction"] == "tx":
            probe = re.fullmatch(r"\*#1\*(\d+)\*1##", frame["raw"])
            current = probe.group(1) if probe else None
            if current is not None:
                replies[current] = []
        elif current is not None and getattr(OWNEvent.parse(frame["raw"]), "where", None) == current:
            replies[current].append(frame["raw"])
    return replies


def test_relays_answer_dimension1_with_plain_status_and_dimmers_with_a_level() -> None:
    """Relays answer *#1*W*1## with their WHAT status; dimmers with *#1*W*1*LEVEL*SPEED##."""
    assert _dim1_probe_replies() == {
        "81": ["*1*1*81##"],
        "84": ["*1*1*84##"],
        "57": ["*1*1*57##"],
        "11": ["*1*0*11##"],
        "29": ["*1*1*29##", "*#1*29*1*200*2##", "*1*1*29##"],
        "67": ["*1*1*67##", "*#1*67*1*174*2##", "*1*1*67##"],
    }


def test_dimension1_replies_promote_dimmers_and_leave_relays_on_off(hass: HomeAssistant) -> None:
    """Fed to bus-discovered lights, the replies promote only the dimmers to BRIGHTNESS."""
    for where, frames in _dim1_probe_replies().items():
        light = _light(hass, where)
        for raw in frames:
            light.handle_event(OWNEvent.parse(raw))
        if where in DIMMERS:
            assert ColorMode.BRIGHTNESS in light.supported_color_modes, where
        else:
            assert light.supported_color_modes == {ColorMode.ONOFF}, where


def test_on_dimmers_first_answer_the_general_request_without_a_level(hass: HomeAssistant) -> None:
    """In the *#1*0## sweep an ON dimmer sends *1*1*W## before its level frame; the level still promotes it."""
    sweep_end = next(i for i, f in enumerate(_frames()) if re.fullmatch(r"\*#1\*\d+\*1##", f["raw"]))
    sweep = [f["raw"] for f in _frames()[1:sweep_end] if f["direction"] == "rx"]

    for where in DIMMERS:
        mine = [raw for raw in sweep if getattr(OWNEvent.parse(raw), "where", None) == where]
        assert mine[0] == f"*1*1*{where}##"
        light = _light(hass, where)
        light.handle_event(OWNEvent.parse(mine[0]))
        assert light.supported_color_modes == {ColorMode.ONOFF}
        for raw in mine[1:]:
            light.handle_event(OWNEvent.parse(raw))
        assert ColorMode.BRIGHTNESS in light.supported_color_modes, where

    for where in ("81", "84", "57"):
        assert [raw for raw in sweep if getattr(OWNEvent.parse(raw), "where", None) == where] in (
            [f"*1*1*{where}##"],
            [f"*1*1*{where}##", f"*1*1*{where}##"],
        )
