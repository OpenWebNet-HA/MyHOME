"""Central unit 'auto' on a 3550: the 311 does nothing, the 3100 restores the program. Live F461 + F455, 2026-11-12.

Test plant: a BTicino F461 2.0.11 and a Legrand F455 1.0.76 on one SCS bus with a BTicino 3550
99-zone central unit (#0) in the conditioning season, one Home Assistant gateway entry per
gateway, an event-session monitor on each gateway. Two identical cycles, each from the central
unit switched OFF:

* ``climate.set_hvac_mode auto`` on the F461 entry's central entity: the F455 monitor shows
  ``*4*311*#0##`` (what OWNd up to 2.0.0b13 builds) on the bus right after the call, no monitor
  shows an answer of the 3550, and ``*#4*#0##`` still answers ``*4*203*#0##`` 2 s and 8 s later.
* ``climate.set_hvac_mode cool`` on the F455 entry's central entity (``*4*0*#0##`` on the F455
  monitor): ``*#4*#0##`` still answers ``*4*203*#0##``.
* ``*4*3100*#0##`` (WHO 4 "last set up weekly program") on the F461 command session: ACK, and
  both monitors show ``*4*2101*#0##``, ``*4*21*#0##``, ``*4*31*#0##``, ``*4*22*#0##`` within
  0.16 s of the send; ``*#4*#0##`` answers ``*4*2101*#0##`` 3 s later.

This is the capture behind OpenWebNet-HA/OWNd#169 (``set_central_mode('#0', 'auto')`` builds the
3100). Archives and README: tests/fixtures/traces/central_restore_3100_live_2026-11-12/.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACMode
from OWNd.message import OWNEvent, OWNHeatingEvent

from custom_components.myhome.router import FrameRouter

TRACES = Path(__file__).parent / "fixtures" / "traces" / "central_restore_3100_live_2026-11-12"
ARCHIVES: dict[str, dict[str, Any]] = {
    model: json.loads((TRACES / f"myhome_trace_{model}_central_restore_2026-11-12.json").read_text(encoding="utf-8"))
    for model in ("F461", "F455")
}
GATEWAYS = sorted(ARCHIVES)

READ_BACK = "*#4*#0##"
RESTORE = "*4*3100*#0##"
ANSWER = ["*4*2101*#0##", "*4*21*#0##", "*4*31*#0##", "*4*22*#0##"]
SENDS = ("tx", "tx_other_gateway")  # a command session send, by this archive's gateway or by the other one


def _rows(model: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = ARCHIVES[model]["rows"]
    return rows


def _monitor(model: str) -> list[str]:
    return [row["frame"] for row in _rows(model) if row["dir"] == "rx"]


def _central(frames: list[str]) -> list[str]:
    return [raw for raw in frames if raw.endswith("*#0##")]


def _step(model: str, text: str) -> list[str]:
    """Monitor frames of every step whose name contains ``text``."""
    return [row["frame"] for row in _rows(model) if row["dir"] == "rx" and text in row["step"]]


@pytest.fixture(autouse=True)
def _entity_base_init() -> Iterator[None]:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield


@pytest.fixture(autouse=True)
def _timers() -> Iterator[None]:
    """The climate timers (flag cycle, held generic frame) are not scheduled: no state here waits on one."""
    with patch("custom_components.myhome.climate.async_call_later", return_value=lambda: None):
        yield


def _central_entity() -> Any:
    from custom_components.myhome.climate import MyHOMEClimate

    gateway = MagicMock()
    gateway.mac = "00:03:50:00:00:01"
    gateway.unique_id = gateway.mac
    gateway.log_id = "[Test Gateway]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    gateway.has_central_unit = True
    hass = MagicMock()
    hass.data = {}
    entity = MyHOMEClimate(
        hass=hass,
        name="Central unit",
        device_id="4-#0",
        who="4",
        where="#0",
        heating=True,
        cooling=True,
        fan=False,
        standalone=None,
        central=True,
        manufacturer="BTicino",
        model="3550",
        gateway=gateway,
    )
    entity.platform = MagicMock()
    entity.entity_id = "climate.test"
    setattr(entity, "async_schedule_update_ha_state", MagicMock())
    setattr(entity, "async_write_ha_state", MagicMock())
    return entity


# The archives.


@pytest.mark.parametrize(("model", "frames", "rows"), [("F461", 176, 218), ("F455", 186, 228)])
def test_archive_holds_the_monitor_frames_and_the_harness_rows(model: str, frames: int, rows: int) -> None:
    archive = ARCHIVES[model]
    assert archive["capture"]["kind"] == "derived_trace_archive"
    assert archive["gateway"]["model"] == model
    assert archive["capture"]["window"]["frames"] == len(archive["frames"]) == frames
    assert archive["capture"]["window"]["rows"] == len(archive["rows"]) == rows
    assert [f["raw"] for f in archive["frames"]] == _monitor(model)
    assert all(f["direction"] == "rx" for f in archive["frames"])
    stamps = [row["t"] for row in archive["rows"]]
    assert stamps == sorted(stamps)
    assert {row["dir"] for row in archive["rows"]} == {"step", "call", "rx", "tx", "tx_other_gateway"}


def test_both_archives_log_the_same_harness_run() -> None:
    def harness(model: str) -> list[tuple[str, str, str]]:
        return [
            (row["utc"], row["dir"] if row["dir"] not in SENDS else "send", row.get("frame") or row.get("service") or row["step"])
            for row in _rows(model)
            if row["dir"] != "rx"
        ]

    assert harness("F461") == harness("F455")
    # A send is ``tx`` in the archive of the gateway whose command session wrote it.
    assert {(r["frame"], r["dir"]) for r in _rows("F461") if r["dir"] in SENDS} == {
        (READ_BACK, "tx"),
        (READ_BACK, "tx_other_gateway"),
        (RESTORE, "tx"),
        ("*4*2101*#0##", "tx"),
    }
    assert {(r["frame"], r["dir"]) for r in _rows("F455") if r["dir"] in SENDS} == {
        (READ_BACK, "tx"),
        (READ_BACK, "tx_other_gateway"),
        (RESTORE, "tx_other_gateway"),
        ("*4*2101*#0##", "tx_other_gateway"),
    }


@pytest.mark.parametrize("model", GATEWAYS)
def test_every_monitor_frame_is_a_who4_event(model: str) -> None:
    for raw in _monitor(model):
        message = OWNEvent.parse(raw)
        assert isinstance(message, OWNHeatingEvent), raw
        assert "unknown" not in str(message.human_readable_log).lower(), raw


# What the 3550 did.


def test_read_backs_of_the_central_unit_follow_only_the_off_and_the_3100() -> None:
    read_backs = sorted(
        (row for model in GATEWAYS for row in _rows(model) if row["dir"] == "tx" and row["frame"] == READ_BACK),
        key=lambda row: row["t"],
    )
    assert [(row["step"], row["replies"][0]) for row in read_backs] == [
        ("c baseline read-back *#4*#0## via F461", "*4*2101*#0##"),
        ("c1 read-back after off via F455", "*4*203*#0##"),
        ("c1 read-back +2 s via F455", "*4*203*#0##"),  # 2 s after the 311
        ("c1 read-back +8 s via F461", "*4*203*#0##"),  # 8 s after the 311
        ("c1 read-back +2 s via F461", "*4*203*#0##"),  # 2 s after the season command *4*0*#0##
        ("c1 read-back +3 s via F455", "*4*2101*#0##"),  # 3 s after the 3100
        ("c2 read-back after off via F455", "*4*203*#0##"),
        ("c2 read-back +2 s via F455", "*4*203*#0##"),
        ("c2 read-back +8 s via F461", "*4*203*#0##"),
        ("c2 read-back +2 s via F461", "*4*203*#0##"),
        ("c2 read-back +3 s via F455", "*4*2101*#0##"),
    ]


@pytest.mark.parametrize("cycle", ["c1", "c2"])
def test_the_311_is_on_the_bus_and_the_3550_does_not_answer(cycle: str) -> None:
    step = f"{cycle} F461 set_hvac_mode auto"
    calls = [row for row in _rows("F461") if row["dir"] == "call" and step in row["step"]]
    assert [call["data"] for call in calls] == [{"entity_id": "climate.climate_zone_0", "hvac_mode": "auto"}]
    # The F455 monitor shows the frame right after the call; the F461 monitor shows nothing at all.
    assert _step("F455", step) == ["*4*311*#0##"]
    assert _step("F461", step) == []


@pytest.mark.parametrize("cycle", ["c1", "c2"])
def test_the_season_command_does_not_wake_the_3550_either(cycle: str) -> None:
    step = f"{cycle} F455 set_hvac_mode cool"
    assert _step("F455", step) == ["*4*0*#0##"]
    assert _step("F461", step) == []


@pytest.mark.parametrize("model", GATEWAYS)
def test_the_3100_is_acknowledged_and_answered_with_the_program(model: str) -> None:
    sends = [row for row in _rows("F461") if row["dir"] == "tx" and row["frame"] == RESTORE]
    assert [(row["ack"], row["reply_time_s"]) for row in sends] == [(True, 0.097), (True, 0.111)]
    assert RESTORE not in _monitor(model)  # neither gateway shows the command on its event session
    rows = _rows(model)
    for send in sends:
        after = [row for row in rows if row["dir"] == "rx" and row["t"] >= send["t"]][:4]
        assert [row["frame"] for row in after] == ANSWER
        assert all(0.13 <= row["t"] - send["t"] <= 0.16 for row in after)


def test_the_central_unit_frames_of_the_run() -> None:
    # Six central unit frames on the F461 monitor; the F455 monitor adds the two command frames.
    assert set(_central(_monitor("F461"))) == {"*4*2101*#0##", "*4*203*#0##", "*4*21*#0##", "*4*22*#0##", "*4*24*#0##", "*4*31*#0##"}
    assert set(_central(_monitor("F455"))) - set(_central(_monitor("F461"))) == {"*4*311*#0##", "*4*0*#0##"}


# What the central entity shows when the monitor frames are routed to it.


@pytest.mark.parametrize("model", GATEWAYS)
def test_central_entity_agrees_with_every_read_back(model: str) -> None:
    from custom_components.myhome.climate import _zone_route_keys

    shown = {"*4*203*#0##": HVACMode.OFF, "*4*2101*#0##": HVACMode.COOL}
    central = _central_entity()
    router = FrameRouter()
    router.subscribe("4", ["#0"], central.handle_event)

    checked = 0
    expected: str | None = None
    for row in [*_rows(model), {"dir": "end"}]:
        if row["dir"] == "rx":
            message = OWNEvent.parse(row["frame"])
            router.publish("4", _zone_route_keys(message, None), message)
            continue
        if expected is not None:  # the read-back's answer has passed on this monitor
            assert central.hvac_mode == shown[expected], row
            if expected == "*4*2101*#0##":
                assert central.extra_state_attributes["program"] == 1
                assert central.extra_state_attributes["season"] == "conditioning"
            else:
                assert "program" not in central.extra_state_attributes
            checked += 1
            expected = None
        if row["dir"] in SENDS and row["frame"] == READ_BACK:
            expected = row["replies"][0]
    assert checked == 11


@pytest.mark.parametrize("model", GATEWAYS)
def test_central_entity_shows_the_restored_program_before_any_read_back(model: str) -> None:
    from custom_components.myhome.climate import _zone_route_keys

    central = _central_entity()
    router = FrameRouter()
    router.subscribe("4", ["#0"], central.handle_event)
    restored = 0
    for row in _rows(model):
        if row["dir"] == "rx":
            message = OWNEvent.parse(row["frame"])
            router.publish("4", _zone_route_keys(message, None), message)
            if row["frame"] == "*4*22*#0##":  # the last frame of the 3550's answer to the 3100
                assert central.hvac_mode == HVACMode.COOL
                assert central.extra_state_attributes["program"] == 1
                restored += 1
        elif row["dir"] in SENDS and row["frame"] == RESTORE:
            assert central.hvac_mode == HVACMode.OFF  # what the 311 and the season command left
    assert restored == 2
