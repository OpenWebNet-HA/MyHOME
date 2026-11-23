"""A zone candidate whose confirmation read nothing answers is kept (review of PR 971, issue 966).

Before: the candidate was removed on a NACK or on an ACK without a frame, so the zone's next
frame was a first frame again. A zone that exists and refuses this read (an F455 zone without
a probe, ``custom_components/myhome/gateway.py`` "Evidence") was never created and cost one
read per report. Now it stays a candidate, is not read again, and is created by its next
independent frame within ``CANDIDATE_MAX_AGE``. A single corrupted frame still creates nothing.

Frames: ``*#4*1*0*0199##`` and ``*#4*1*12*0170*3##`` from
``tests/fixtures/traces/issue_466/myhome_sweep_MH200N_all_2026-11-02T12-53-15.json``;
``*#4*65*0*0190##`` and ``*#4*2*0*0191##`` from
``tests/fixtures/traces/issue_966/myhome_trace_MyHomeServer1_bitflip_zone65_2026-11-19.json``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from custom_components.myhome import climate_discovery
from tests.test_issue_966_phantom_climate_zone import _plant, _receive, _settle

pytestmark = pytest.mark.usefixtures("fast_bus_pacing")
ANSWERS = pytest.mark.parametrize("answer", [False, True], ids=["nack", "ack_without_frame"])

ZONE_1_TEMPERATURE = "*#4*1*0*0199##"
ZONE_1_LOCAL_TARGET = "*#4*1*12*0170*3##"
ZONE_65_CORRUPTED = "*#4*65*0*0190##"
ZONE_2_TEMPERATURE = "*#4*2*0*0191##"


def _clock_and_read(monkeypatch: pytest.MonkeyPatch, answer: bool) -> tuple[dict[str, float], list[str], Any]:
    """A clock the test moves, the list of zones read, and a read that nothing answers."""
    clock = {"now": 2000.0}
    monkeypatch.setattr(climate_discovery, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    calls: list[str] = []

    async def read(where: str) -> bool | None:
        calls.append(where)
        return answer

    return clock, calls, read


@ANSWERS
async def test_a_zone_whose_read_nothing_answers_is_created_by_its_next_report(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, answer: bool
) -> None:
    clock, calls, read = _clock_and_read(monkeypatch, answer)
    handler, zones, _entry = await _plant(hass, read=read)

    await _receive(hass, handler, [ZONE_1_TEMPERATURE])
    await _settle(hass, handler)
    assert zones == []
    assert calls == ["1"]

    clock["now"] += 600.0
    await _receive(hass, handler, [ZONE_1_TEMPERATURE])
    await _settle(hass, handler)
    assert [z._where for z in zones] == ["1"]
    assert calls == ["1"]


@ANSWERS
async def test_twelve_reports_cost_one_read(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, answer: bool
) -> None:
    clock, calls, read = _clock_and_read(monkeypatch, answer)
    handler, zones, _entry = await _plant(hass, read=read)

    for _report in range(12):
        await _receive(hass, handler, [ZONE_1_TEMPERATURE])
        await _settle(hass, handler)
        clock["now"] += 600.0

    assert [z._where for z in zones] == ["1"]
    assert calls == ["1"]


async def test_a_single_corrupted_frame_creates_nothing_and_is_read_once_per_candidate(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock, calls, read = _clock_and_read(monkeypatch, False)
    handler, zones, _entry = await _plant(hass, read=read)

    await _receive(hass, handler, [ZONE_65_CORRUPTED])
    await _settle(hass, handler)
    await _settle(hass, handler)
    assert zones == []
    assert calls == ["65"]

    # Past CANDIDATE_MAX_AGE the address is a first frame again: read once more, still no zone.
    clock["now"] += climate_discovery.CANDIDATE_MAX_AGE + 1.0
    await _receive(hass, handler, [ZONE_65_CORRUPTED])
    await _settle(hass, handler)
    assert zones == []
    assert calls == ["65", "65"]


async def test_a_pending_candidate_is_not_read_again_when_another_zone_is_read(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock, calls, read = _clock_and_read(monkeypatch, False)
    handler, zones, _entry = await _plant(hass, read=read)

    await _receive(hass, handler, [ZONE_65_CORRUPTED])
    await _settle(hass, handler)
    clock["now"] += 60.0
    await _receive(hass, handler, [ZONE_2_TEMPERATURE])
    await _settle(hass, handler)

    assert zones == []
    assert calls == ["65", "2"]


async def test_the_held_first_frame_is_replayed_when_a_second_frame_confirms(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock, calls, read = _clock_and_read(monkeypatch, False)
    handler, zones, _entry = await _plant(hass, read=read)

    await _receive(hass, handler, [ZONE_1_TEMPERATURE])
    await _settle(hass, handler)
    clock["now"] += 300.0
    await _receive(hass, handler, [ZONE_1_LOCAL_TARGET])

    assert [z._where for z in zones] == ["1"]
    assert zones[0].current_temperature == 19.9
    assert calls == ["1"]


async def test_an_identical_frame_inside_the_echo_window_confirms_nothing(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock, calls, read = _clock_and_read(monkeypatch, False)
    handler, zones, _entry = await _plant(hass, read=read)

    await _receive(hass, handler, [ZONE_65_CORRUPTED])
    await _settle(hass, handler)
    clock["now"] += 1.0
    await _receive(hass, handler, [ZONE_65_CORRUPTED])

    assert zones == []
    assert calls == ["65"]
