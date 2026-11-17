"""Tests for #684: A=0 PL=2 point reports (single-digit WHERE 2) and the area-2 burst that follows.

The MH200 reports a Home + Project point at A=0 PL=2 as ``*1*1*2##``, the same frame as an area 2 broadcast.
The fixture pins what the bus really looks like, and the tests pin how MyHOME treats the two-digit ``02`` form
a user can configure for such a point.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from OWNd.message import OWNLightingCommand, OWNLightingEvent, OWNMessage

from custom_components.myhome.const import area_of_where, is_apl_address, normalize_where
from custom_components.myhome.light import MyHOMELight

TRACE = Path(__file__).parent / "fixtures" / "traces" / "issue_684" / "physical_press_a0pl2_mh200.json"
TRIGGERS = ("*1*1*2##", "*1*0*2##")
BURST = [f"*1*0*{n}##" for n in range(21, 30)]


def _frames() -> list[dict]:
    return json.loads(TRACE.read_text(encoding="utf-8"))["frames"]


def test_every_frame_parses_as_a_lighting_event() -> None:
    for frame in _frames():
        event = OWNMessage.parse(frame["raw"])
        assert isinstance(event, OWNLightingEvent), frame["raw"]
        assert event.who == 1


def test_each_press_is_followed_by_the_area_2_burst() -> None:
    frames = _frames()
    triggers = [i for i, f in enumerate(frames) if f["raw"] in TRIGGERS]
    assert len(triggers) == 4
    for n, i in enumerate(triggers):
        end = triggers[n + 1] if n + 1 < len(triggers) else len(frames)
        burst = [f for f in frames[i + 1 : end] if f["raw"] in BURST]
        assert burst[0]["t"] - frames[i]["t"] < 1.2
        assert [f["raw"] for f in burst] == BURST
        gaps = [b["t"] - a["t"] for a, b in zip(burst, burst[1:])]
        assert max(gaps) < 0.15


def test_press_frame_is_indistinguishable_from_an_area_broadcast() -> None:
    """Single-digit WHERE 2 is Area 2 for every parser, so it can never be matched to A=0 PL=2 from the frame alone."""
    event = OWNMessage.parse("*1*1*2##")
    assert event.entity == "1-2"
    assert area_of_where("2") is None  # not a point-to-point address either


def test_two_digit_form_is_canonical_apl_accepted_by_gateways() -> None:
    """is_apl_address() recognizes 02 as the feasible 2-digit APL form for Area 0, PL 1..9 (oracle lights-area0)."""
    assert is_apl_address("02")
    assert is_apl_address("0002")


def test_two_digit_form_is_what_gateways_accept() -> None:
    assert str(OWNLightingCommand.switch_on("02")) == "*1*1*02##"
    assert str(OWNLightingCommand.switch_on("02#4#01")) == "*1*1*02#4#01##"


def test_two_digit_where_does_not_collide_with_area_after_normalisation() -> None:
    """A configured 02 preserves its leading zero and does not alias the bare WHERE 2."""
    assert normalize_where("02") == "02"
    assert area_of_where("02") == "00"
    assert normalize_where("02") != normalize_where("2")


@pytest.mark.asyncio
async def test_light_configured_at_02_sends_the_two_digit_frame(hass) -> None:
    gateway = MagicMock()
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    light = MyHOMELight(
        hass=hass, name="A0PL2", entity_name="A0PL2", icon=None, icon_on=None,
        device_id="02", who="1", where="02", interface=None, dimmable=False,
        manufacturer="B", model="M", gateway=gateway,
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()
    await light.async_turn_on()
    assert str(gateway.send.call_args.args[0]) == "*1*1*02##"
