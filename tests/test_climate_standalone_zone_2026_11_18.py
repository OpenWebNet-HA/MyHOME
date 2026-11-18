"""A thermostat the central unit is not running, found from the bus: live F461 + F455 replay, 2026-11-18.

One SCS bus with a BTicino 3550 99-zone central unit (#0) and zones 2 (a fan-coil thermostat), 3
and 4, all discovered from the bus (nothing in ``myhome.yaml``), so every zone followed the plant
(#697) and was written through the central unit. On 2026-11-18 the 3550 was in the heating season
and zone 2 in conditioning:

* ``climate.set_temperature`` 21.0 on zone 2 sent ``*#4*#2*#14*0210*3##`` on both gateways. Both
  acknowledged it, nothing answered, the thermostat kept 20.0 and Home Assistant reported no error;
* the direct ``*#4*2*#14*0210*3##`` was answered by the thermostat (``*#4*2*12*0210*3##``, 45 ms);
* the 3550 answered the same ``#Z`` write for zone 3 (``*#4*3*#14*0250*1##`` 34 ms after the ACK,
  ``*4*110*#3##`` / ``*#4*#3*14*0250*1##`` 0.55 s after it), and reported zones 3 and 4 in the ``#Z``
  form after every ``*#4*Z##`` and in its quarter-hour push, zone 2 never;
* zone 2 showed ``heat`` (restored) on the F461 and ``auto`` (the F455's made-up ``*4*311*#2##``)
  on the F455 while it reported ``*4*0*2##``, conditioning.

The same 3550 did run zone 2 on 2026-11-12, in conditioning on its weekly program: it wrote
``*#4*2*#14*0200*2##`` and reported ``*4*211*#2##``
(tests/fixtures/traces/central_restore_3100_live_2026-11-12). So which zones a central unit runs
is not fixed, and nothing on the bus lists them: the form of a write goes by its result.

Frames: tests/fixtures/traces/standalone_zone_2026_11_18/frames.json.
"""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACMode
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNEvent

from custom_components.myhome import climate as climate_module
from custom_components.myhome.where_grammar import validate_standalone_zones

TRACES = Path(__file__).parent / "fixtures" / "traces"
FRAMES = json.loads((TRACES / "standalone_zone_2026_11_18" / "frames.json").read_text())
# The F461 monitor of 2026-11-12: the 3550 in conditioning, switched off and back to its program.
PROGRAM_RESTORE = [
    row["frame"]
    for row in json.loads(
        (
            TRACES / "central_restore_3100_live_2026-11-12" / "myhome_trace_F461_central_restore_2026-11-12.json"
        ).read_text(encoding="utf-8")
    )["rows"]
    if row["dir"] == "rx"
]


def _rx(trace: str) -> list[str]:
    return [raw for _t, direction, raw in FRAMES[trace] if direction == "rx"]


def _tx(trace: str) -> list[str]:
    return [raw for _t, direction, raw in FRAMES[trace] if direction == "tx"]


@pytest.fixture(autouse=True)
def _entity_base_init() -> Iterator[None]:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield


@pytest.fixture(autouse=True)
def _short_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(climate_module, "SETPOINT_CONFIRM_TIMEOUT", 0.05)
    monkeypatch.setattr(climate_module, "EMPTY_REPLY_SETTLE", 0.2)


class _Plant:
    """A gateway double that acknowledges every frame and plays the plant's captured answers.

    ``answers`` maps a written frame to the event-session frames that followed it in the capture;
    a frame that is not in it is acknowledged and nothing answers, which is what both live
    gateways did with ``*#4*#2*#14*0210*3##``.
    """

    def __init__(self, *, has_central_unit: bool = True) -> None:
        self.gateway = MagicMock()
        self.gateway.mac = "00:03:50:00:00:01"
        self.gateway.unique_id = self.gateway.mac
        self.gateway.log_id = "[Test Gateway]"
        self.gateway.model = "F461"
        self.gateway.has_central_unit = has_central_unit
        self.gateway.send = self.send
        self.gateway.send_status_request = self.send_status_request
        self.sent: list[str] = []
        self.answers: dict[str, list[list[str]]] = {}
        self.nack: set[str] = set()
        self.entity: Any = None

    def answer(self, frame: str, *replies: list[str]) -> None:
        """Queue what follows ``frame``: one list of frames per time it is written."""
        self.answers.setdefault(frame, []).extend(replies)

    async def _write(self, message: Any) -> asyncio.Future[float]:
        frame = str(message)
        self.sent.append(frame)
        loop = asyncio.get_running_loop()
        written: asyncio.Future[float] = loop.create_future()
        if frame in self.nack:
            written.cancel()
            return written
        written.set_result(loop.time())
        queued = self.answers.get(frame) or []
        replies = queued.pop(0) if queued else []
        for raw in replies:
            loop.call_soon(self.entity.handle_event, OWNEvent.parse(raw))
        return written

    async def send(self, message: Any, **_kwargs: Any) -> asyncio.Future[float]:
        return await self._write(message)

    async def send_status_request(self, message: Any) -> asyncio.Future[float]:
        return await self._write(message)


def _zone(plant: Any, where: str, *, standalone: bool | None = None) -> Any:
    from custom_components.myhome.climate import MyHOMEClimate

    hass = MagicMock()
    hass.data = {}
    entity = MyHOMEClimate(
        hass=hass, name=f"Climate Zone {where}", device_id=f"4-{where}", who="4", where=where,
        heating=True, cooling=True, fan=False, standalone=standalone, central=False,
        manufacturer="BTicino", model="Heating Zone",
        gateway=plant.gateway if isinstance(plant, _Plant) else plant,
    )
    entity.platform = MagicMock()
    entity.entity_id = "climate.test"
    setattr(entity, "async_schedule_update_ha_state", MagicMock())
    setattr(entity, "async_write_ha_state", MagicMock())
    if isinstance(plant, _Plant):
        plant.entity = entity
    return entity


def _replay(entity: Any, frames: list[str]) -> None:
    """Hand the zone its own frames of a capture, as the platform's routing does."""
    for raw in frames:
        message = OWNEvent.parse(raw)
        # A *#14* write seen on the bus is a command, not an event; other zones' frames are not routed here.
        if type(message).__name__ == "OWNHeatingEvent" and str(message.zone) == entity._where:
            entity.handle_event(message)


def _zone2_plant() -> _Plant:
    """Zone 2 as captured: silent on the ``#Z`` write, 20.0 on a read, takes the direct write."""
    plant = _Plant()
    plant.answer("*#4*2*14##", ["*#4*2*14*0200*3##"])  # the read while it still holds 20.0
    plant.answer(_tx("f455_view_zone2_setpoint_direct")[0], _rx("f455_view_zone2_setpoint_direct"))
    return plant


# The central unit tells which zones are its own.


def test_the_central_unit_reports_the_zones_it_is_running() -> None:
    plant = _Plant()
    zones = {where: _zone(plant, where) for where in ("2", "3", "4")}
    for trace in ("f461_startup_zone_status", "f455_quarter_hour_push"):
        for zone in zones.values():
            _replay(zone, _rx(trace))
    assert zones["3"]._central_zone_seen is True
    assert zones["4"]._central_zone_seen is True
    assert zones["2"]._central_zone_seen is False  # no *4*1xx*#2## / *#4*#2*14*...## in either capture


def test_the_same_central_unit_ran_zone_2_six_days_earlier() -> None:
    """2026-11-12, conditioning: ``*4*211*#2##`` after the 3550 went back to its weekly program."""
    assert "*#4*2*#14*0200*2##" in PROGRAM_RESTORE  # the 3550's own write to zone 2
    assert PROGRAM_RESTORE.count("*4*211*#2##") == 3
    zone2 = _zone(_Plant(), "2")
    zone2._standalone_detected = True  # what a write had found while the 3550 left the zone alone
    _replay(zone2, PROGRAM_RESTORE)
    assert zone2._central_zone_seen is True
    assert zone2._standalone_detected is False
    assert zone2._standalone is False
    assert HVACMode.AUTO in zone2.hvac_modes  # a zone of the central unit again: its program is offered


def test_the_f455_generic_frame_for_the_standalone_zone_is_not_a_central_report() -> None:
    zone2 = _zone(_Plant(), "2")
    _replay(zone2, _rx("f455_zone2_setpoint_via_central"))  # ends with the F455's *4*311*#2##
    _replay(zone2, _rx("f455_view_f461_zone2_setpoint_via_central"))  # *4*310*#2##
    assert zone2._central_zone_seen is False


@pytest.mark.parametrize("trace", ["f461_zone3_setpoint_via_central", "f455_zone3_setpoint_via_central"])
async def test_the_central_unit_answer_confirms_the_write_for_its_zone(trace: str) -> None:
    """Zone 3 as either gateway showed it: the 3550's answer ends the check, nothing else is sent."""
    plant = _Plant()
    zone3 = _zone(plant, "3")
    zone3._target_temperature = 24.0
    plant.answer("*#4*#3*#14*0250*3##", _rx(trace))
    await zone3.async_set_temperature(temperature=25.0)
    assert plant.sent == ["*#4*#3*#14*0250*3##"]  # no read, no direct write
    assert zone3._central_zone_seen is True
    assert zone3._standalone is False
    assert zone3._standalone_detected is False


async def test_a_late_answer_of_the_central_unit_does_not_make_the_zone_standalone() -> None:
    """The direct write went out, and what confirmed it was the central unit's report of the zone."""
    plant = _Plant()
    zone3 = _zone(plant, "3")
    zone3._target_temperature = 24.0
    plant.answer("*#4*3*14##", ["*#4*3*14*0240*3##"])  # still the old setpoint when it is read
    plant.answer("*#4*3*#14*0250*3##", _rx("f461_zone3_setpoint_via_central"))  # *4*110*#3##, *#4*#3*14*0250*1##
    await zone3.async_set_temperature(temperature=25.0)
    assert plant.sent == ["*#4*#3*#14*0250*3##", "*#4*3*14##", "*#4*3*#14*0250*3##"]
    assert zone3._standalone_detected is False
    assert zone3._standalone is False


# U-2: the write that did nothing.


async def test_the_standalone_zone_gets_its_setpoint_directly_when_the_central_unit_is_silent() -> None:
    plant = _zone2_plant()
    zone2 = _zone(plant, "2")
    _replay(zone2, _rx("f461_startup_zone_status"))
    assert zone2.target_temperature == 20.0
    assert zone2._standalone is False  # as today: the plant has a central unit (#697)

    # What the F455 instance saw after its write: the zone's old state, read by another client.
    plant.answer("*#4*#2*#14*0210*3##", _rx("f455_zone2_setpoint_via_central"))
    await zone2.async_set_temperature(temperature=21.0)

    assert plant.sent == ["*#4*#2*#14*0210*3##", "*#4*2*14##", "*#4*2*#14*0210*3##"]
    assert zone2._standalone_detected is True
    assert zone2._standalone is True
    assert zone2.target_temperature == 21.0
    assert zone2.extra_state_attributes["standalone_detected"] is True

    plant.sent.clear()
    await zone2.async_set_temperature(temperature=20.0)
    assert plant.sent == ["*#4*2*#14*0200*3##"]  # the direct form from now on, no check


async def test_detection_puts_the_mode_right(monkeypatch: pytest.MonkeyPatch) -> None:
    """U-1: ``heat`` restored (F461) or the F455's ``auto``, while the zone reports conditioning."""
    for shown, restored in ((HVACMode.HEAT, True), (HVACMode.AUTO, False)):
        plant = _zone2_plant()
        zone2 = _zone(plant, "2")
        zone2._attr_hvac_mode = shown
        zone2._hvac_mode_restored = restored
        _replay(zone2, _rx("f461_startup_zone_status"))  # *4*0*2##: season only for a zone of the central
        assert zone2.hvac_mode == shown
        await zone2.async_set_temperature(temperature=21.0)
        assert zone2.hvac_mode == HVACMode.COOL
        assert HVACMode.AUTO not in zone2.hvac_modes  # the central unit has no program for it (#895)
        # From here on the F455's made-up frame no longer turns it AUTO (#880 rule for standalone zones).
        _replay(zone2, ["*4*311*#2##"])
        assert zone2.hvac_mode == HVACMode.COOL


async def test_a_detected_standalone_zone_survives_a_restart() -> None:
    plant = _Plant()
    zone2 = _zone(plant, "2")
    await zone2.async_restore_last_state(
        State("climate.test", "cool", {"temperature": 20.0, "season": "conditioning", "standalone_detected": True})
    )
    assert zone2._standalone is True
    _replay(zone2, _rx("f461_startup_zone_status"))
    assert zone2.hvac_mode == HVACMode.COOL
    await zone2.async_set_temperature(temperature=21.0)
    assert plant.sent == ["*#4*2*#14*0210*3##"]


async def test_a_report_of_the_central_unit_overrides_what_a_write_found() -> None:
    zone3 = _zone(_Plant(), "3")
    zone3._standalone_detected = True
    assert zone3._standalone is True
    _replay(zone3, _rx("f455_quarter_hour_push"))  # *4*110*#3## from the 3550
    assert zone3._standalone_detected is False
    assert zone3._standalone is False
    assert "standalone_detected" not in zone3.extra_state_attributes


async def test_a_zone_that_takes_neither_form_raises() -> None:
    plant = _Plant()
    zone2 = _zone(plant, "2")
    _replay(zone2, _rx("f461_startup_zone_status"))
    plant.answer("*#4*2*14##", ["*#4*2*14*0200*3##"], ["*#4*2*14*0200*3##"])
    with pytest.raises(HomeAssistantError) as err:
        await zone2.async_set_temperature(temperature=21.0)
    assert err.value.translation_key == "setpoint_not_taken"
    assert plant.sent == ["*#4*#2*#14*0210*3##", "*#4*2*14##", "*#4*2*#14*0210*3##", "*#4*2*14##"]
    assert zone2._standalone_detected is False


async def test_a_zone_configured_under_the_central_unit_is_not_written_directly() -> None:
    """``standalone: false`` (the ``myhome.yaml`` default) wins (#697): an error names the option."""
    plant = _zone2_plant()
    zone2 = _zone(plant, "2", standalone=False)
    _replay(zone2, _rx("f461_startup_zone_status"))
    with pytest.raises(HomeAssistantError) as err:
        await zone2.async_set_temperature(temperature=21.0)
    assert err.value.translation_key == "setpoint_not_taken_via_central"
    assert plant.sent == ["*#4*#2*#14*0210*3##", "*#4*2*14##"]


async def test_no_answer_to_the_read_is_not_an_error() -> None:
    plant = _Plant()  # nothing answers anything: a zone that is gone
    zone2 = _zone(plant, "2")
    zone2._target_temperature = 20.0
    await zone2.async_set_temperature(temperature=21.0)
    assert plant.sent == ["*#4*#2*#14*0210*3##", "*#4*2*14##"]  # silence proves nothing: no direct write
    assert zone2._standalone_detected is False


async def test_a_setpoint_the_zone_already_holds_teaches_nothing() -> None:
    plant = _zone2_plant()
    zone2 = _zone(plant, "2")
    _replay(zone2, _rx("f461_startup_zone_status"))
    await zone2.async_set_temperature(temperature=20.0)  # what it has
    assert plant.sent == ["*#4*#2*#14*0200*3##", "*#4*2*14##"]  # the read shows 20.0: taken as confirmed
    assert zone2._standalone_detected is False


async def test_the_direct_form_refused_by_the_gateway_is_not_standalone() -> None:
    """An F454 refuses ``*#4*3*#14*...`` for a zone of its central unit (#697)."""
    plant = _Plant()
    zone3 = _zone(plant, "3")
    zone3._target_temperature = 20.0
    plant.nack.add("*#4*3*#14*0210*3##")
    plant.answer("*#4*3*14##", ["*#4*3*14*0200*3##"], ["*#4*3*14*0200*3##"])
    with pytest.raises(HomeAssistantError):
        await zone3.async_set_temperature(temperature=21.0)
    assert zone3._standalone_detected is False


async def test_the_direct_form_unanswered_and_a_silent_second_read_is_not_an_error() -> None:
    """Old setpoint on the first read, then nothing: silence proves nothing, a warning is logged."""
    plant = _Plant()
    zone2 = _zone(plant, "2")
    _replay(zone2, _rx("f461_startup_zone_status"))
    plant.answer("*#4*2*14##", ["*#4*2*14*0200*3##"], [])  # answers once, then not at all
    await zone2.async_set_temperature(temperature=21.0)
    assert plant.sent == ["*#4*#2*#14*0210*3##", "*#4*2*14##", "*#4*2*#14*0210*3##", "*#4*2*14##"]
    assert zone2._standalone_detected is False


async def test_a_read_that_cannot_be_sent_is_not_the_zone_answer() -> None:
    plant = _Plant()
    zone2 = _zone(plant, "2")
    _replay(zone2, _rx("f461_startup_zone_status"))
    plant.gateway.send_status_request = AsyncMock(side_effect=RuntimeError("session closed"))
    await zone2.async_set_temperature(temperature=21.0)
    assert plant.sent == ["*#4*#2*#14*0210*3##"]  # the read was not written, so nothing else is tried
    assert zone2._standalone_detected is False


@pytest.mark.parametrize("overtaken_in", ["direct_wait", "second_read"])
async def test_a_newer_write_takes_over_the_check_of_the_direct_form(overtaken_in: str) -> None:
    """A second ``set_temperature`` while the direct form is checked: the first check stops, its own decides."""
    plant = _Plant()
    zone2 = _zone(plant, "2")
    _replay(zone2, _rx("f461_startup_zone_status"))
    plant.answer("*#4*2*14##", ["*#4*2*14*0200*3##"], ["*#4*2*14*0200*3##"])
    newer = (22.0, 22.0, asyncio.Event())
    wait, read = zone2._wait_for_setpoint, zone2._read_back_setpoint
    waits = reads = 0

    async def _wait(written: object, check: Any) -> bool:
        nonlocal waits
        waits += 1
        taken = bool(await wait(written, check))
        if overtaken_in == "direct_wait" and waits == 2:
            zone2._setpoint_check = newer
        return taken

    async def _read(check: Any) -> float | None:
        nonlocal reads
        reads += 1
        reported: float | None = await read(check)
        if overtaken_in == "second_read" and reads == 2:
            zone2._setpoint_check = newer
        return reported

    with patch.object(zone2, "_wait_for_setpoint", _wait), patch.object(zone2, "_read_back_setpoint", _read):
        await zone2.async_set_temperature(temperature=21.0)  # no error: the newer write's check reports
    assert plant.sent[:3] == ["*#4*#2*#14*0210*3##", "*#4*2*14##", "*#4*2*#14*0210*3##"]
    assert zone2._standalone_detected is False


def test_only_a_report_about_this_zone_marks_it_as_one_of_the_central_unit() -> None:
    plant = _Plant()
    zone3 = _zone(plant, "3")
    for raw in ("*4*110*#4##", "*4*21*#0##", "*4*311*#3##"):  # another zone, the central unit itself, generic
        zone3._note_central_zone_report(OWNEvent.parse(raw))
    assert zone3._central_zone_seen is False
    # Zone 3 of another bus (behind F422 interface 01). Not a captured frame: the captured
    # ``*4*110*#3##`` with the interface suffix ``#4#01`` of the addressing rule.
    zone3._note_central_zone_report(OWNEvent.parse("*4*110*#3#4#01##"))
    assert zone3._central_zone_seen is False
    zone3._note_central_zone_report(OWNEvent.parse("*4*110*#3##"))
    assert zone3._central_zone_seen is True

    from custom_components.myhome.climate import MyHOMEClimate

    central = MyHOMEClimate(
        hass=MagicMock(), name="Central Unit 0", device_id="4-#0", who="4", where="#0", heating=True, cooling=True,
        fan=False, standalone=None, central=True, manufacturer="BTicino", model="3550", gateway=plant.gateway,
    )
    central._note_central_zone_report(OWNEvent.parse("*4*110*#3##"))
    assert central._central_zone_seen is False


@pytest.mark.parametrize(
    ("raw", "zone"),
    [
        ("*4*110*#3##", "3"),
        ("*#4*#3*14*0250*1##", "3"),
        ("*4*311*#2##", None),  # generic: the F455's made-up frame for a standalone zone
        ("*#4*2*14*0200*3##", None),  # the zone's own report
        ("*4*21*#0##", None),  # the central unit itself
    ],
)
def test_central_report_zone(raw: str, zone: str | None) -> None:
    from custom_components.myhome.where_grammar import central_report_zone

    assert central_report_zone(OWNEvent.parse(raw)) == zone


async def test_a_gateway_double_without_delivery_information_is_not_checked() -> None:
    """As the cover and lock platforms: no delivery future, no check (the #697 tests' gateway)."""
    gateway = MagicMock()
    gateway.mac = "00:03:50:00:00:01"
    gateway.unique_id = gateway.mac
    gateway.log_id = "[Test Gateway]"
    gateway.has_central_unit = True
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    zone2 = _zone(gateway, "2")
    await zone2.async_set_temperature(temperature=21.0)
    assert [str(call.args[0]) for call in gateway.send.await_args_list] == ["*#4*#2*#14*0210*3##"]
    gateway.send_status_request.assert_not_awaited()


# The option.


async def test_a_zone_listed_as_standalone_is_written_directly() -> None:
    plant = _Plant()
    zone2 = _zone(plant, "2", standalone=True)  # what build() passes for a zone of CONF_STANDALONE_ZONES
    _replay(zone2, _rx("f461_startup_zone_status"))
    assert zone2.hvac_mode == HVACMode.COOL  # *4*0*2## is its mode (#880)
    await zone2.async_set_temperature(temperature=21.0)
    assert plant.sent == _tx("f455_view_zone2_setpoint_direct")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2, 7", (["2", "7"], True)),
        ("02\n3", (["2", "3"], True)),
        ("", ([], True)),
        (None, ([], True)),
        (["2", 2, " 4 "], (["2", "4"], True)),
        ("#0", ([], False)),
        ("0", ([], False)),
        ("102", ([], False)),
        ("2#1", ([], False)),
        ("zone 2", ([], False)),
    ],
)
def test_standalone_zones_option_is_validated(raw: Any, expected: tuple[list[str], bool]) -> None:
    assert validate_standalone_zones(raw) == expected


async def test_zones_of_the_gateway_option_are_built_standalone(hass: Any) -> None:
    from custom_components.myhome.climate import async_setup_entry
    from custom_components.myhome.const import CONF_STANDALONE_ZONES
    from tests.conftest import attach_runtime

    hass.data = {"myhome": {"mac": {"platforms": {"climate": {}}, "entity": MagicMock()}}}
    config_entry = MagicMock()
    config_entry.data = {"mac": "mac"}
    config_entry.entry_id = "test_entry"
    config_entry.options = {CONF_STANDALONE_ZONES: ["2"]}
    registry = []
    for where in ("2", "3", "#0"):
        entry = MagicMock()
        entry.domain = "climate"
        entry.unique_id = f"mac-4-{where}"
        entry.entity_id = f"climate.zone_{where.strip('#')}"
        entry.name = None
        registry.append(entry)

    with patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=registry), \
         patch("custom_components.myhome.discovery.er.async_get"):
        attach_runtime(hass, config_entry)
        entities: list[Any] = []
        await async_setup_entry(hass, config_entry, entities.extend)

    by_where = {entity._where: entity for entity in entities}
    assert by_where["2"]._standalone_config is True
    assert by_where["3"]._standalone_config is None  # still found from the bus
    assert by_where["#0"]._standalone_config is False  # the central unit is never a standalone zone
