"""A thermostat the central unit is not running, found at start from the season it reports.

Live plant, 2026-11-18, a BTicino 3550 (#0) with zones 3 and 4, and zone 2, which it does not
run; every zone found on the bus, nothing in ``myhome.yaml``:

* the 3550 answered ``*#4*#0##`` with ``*4*102*#0##`` (heating), zones 3 and 4 reported
  ``*4*1*3##`` / ``*4*1*4##``, zone 2 ``*4*0*2##`` (conditioning);
* zone 2, taken as a zone of the 3550, showed ``heat`` while it was cooling and offered no
  ``cool`` until its first setpoint write found it standalone (#951);
* a read of the setpoint in force is answered with the zone's season frame: ``*#4*2*12##`` ->
  ``*#4*2*12*0200*3##``, ``*4*0*2##`` on the F461 2.0.11 (3.7 s) and the F455 1.0.76 (0.1 s),
  ``*#4*3*12##`` -> ``*#4*3*12*0265*3##``, ``*4*1*3##`` on both, ``*#4*70*12##`` ->
  ``*#4*70*12*0230*3##``, ``*4*1*70##`` on a MyHomeServer1 3.87.13.

So a zone that reports the season the central unit is not in is read again ten seconds later,
and a second report of that season, with no report of the central unit about the zone in
between, makes it a standalone thermostat without any write.

One frame is not enough: on the MyHomeServer1 plant of #453 the central unit went from heating
to conditioning (``*4*202*#0##``, 19:39:28.629) and zone 4, which it reports (``*4*203*#4##``),
still sent ``*4*1*4##`` 0.66 s later, then ``*4*202*4##`` 2.0 s after the change
(tests/fixtures/traces/issue_453).
"""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACMode
from homeassistant.core import HomeAssistant
from OWNd.message import OWNEvent

from custom_components.myhome import climate as climate_module
from custom_components.myhome.gateway import KNOWN_GATEWAY_MODELS
from tests.test_climate_standalone_zone_2026_11_18 import _Plant, _replay, _rx, _zone
from tests.test_f455_zone_polling import _handler
from tests.test_issue_944_actuator_answer_not_central import _plant, _receive
from tests.test_thermo_discovery_at_start import _drain

# The answers of 2026-11-18 15:00 to `*#4*2*12##` and `*#4*3*12##` (F461 and F455 alike).
ZONE_2_READ = ["*#4*2*12*0200*3##", "*4*0*2##"]
ZONE_3_READ = ["*#4*3*12*0265*3##", "*4*1*3##"]
# The MyHomeServer1 monitor of #453: rows 0 (the central unit in manual heating), 40 (zone 4 in
# heating), 58 (the central unit in conditioning protection), 65 (zone 4 still in heating),
# 75-76 (zone 4 in conditioning protection) and 115 (the central unit about zone 4).
ISSUE_453 = [
    row["raw"]
    for row in json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "traces"
            / "issue_453"
            / "myhome_trace_MyHomeServer1_all_2026-09-25T19-40-15.json"
        ).read_text(encoding="utf-8")
    )["frames"]
]


@pytest.fixture(autouse=True)
def _entity_base_init() -> Iterator[None]:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield


@pytest.fixture(autouse=True)
def _short_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(climate_module, "STANDALONE_SEASON_CONFIRM_DELAY", 0.01)
    monkeypatch.setattr(climate_module, "PACED_READ_TIMEOUT", 0.3)


def _live_zone(where: str, *, central_season: str | None, answer: list[str] | None = None, **kwargs: Any) -> Any:
    """A zone found on the bus, on a plant whose central unit reported ``central_season``.

    ``answer``: what follows the read of dimension 12, None when nothing answers.
    """
    plant = _Plant()
    plant.gateway.central_season = central_season
    plant.gateway.wait_for_initial_discovery = AsyncMock()
    zone = _zone(plant, where, **kwargs)
    zone.hass.async_create_background_task = lambda coro, name=None: asyncio.get_running_loop().create_task(coro)

    def read(_where: str, _dimension: int) -> None:
        for raw in answer or []:
            asyncio.get_running_loop().call_soon(zone.handle_event, OWNEvent.parse(raw))

    plant.gateway.request_zone_dimension = MagicMock(side_effect=read)
    zone._plant = plant
    return zone


async def _checked(zone: Any) -> None:
    """Let the running season check finish."""
    task = zone._season_check_task
    assert task is not None
    await task
    assert zone._season_check_task is None


def test_the_frames_this_file_rests_on() -> None:
    assert "*4*0*2##" in _rx("f461_startup_zone_status")
    assert [ISSUE_453[i] for i in (0, 40, 58, 65, 75, 76, 115)] == [
        "*4*110#0200*#0##",
        "*4*1*4##",
        "*4*202*#0##",
        "*4*1*4##",
        "*4*202*4##",
        "*#4*4*12*0350*3##",
        "*4*203*#4##",
    ]


async def test_zone_2_is_found_standalone_at_start_without_a_write() -> None:
    zone2 = _live_zone("2", central_season="heating", answer=ZONE_2_READ)
    zone2._handle_central_mode_update(HVACMode.HEAT)  # what the 3550's *4*102*#0## gives its zones
    _replay(zone2, _rx("f461_startup_zone_status"))  # the answer to *#4*2##, with *4*0*2##
    assert zone2.hvac_mode == HVACMode.HEAT  # one frame: still a zone of the central unit
    assert zone2._standalone is False

    await _checked(zone2)

    zone2._gateway_handler.request_zone_dimension.assert_called_once_with("2", 12)
    assert zone2._standalone is True
    assert zone2.extra_state_attributes["standalone_detected"] is True
    assert zone2.hvac_mode == HVACMode.COOL
    assert HVACMode.AUTO not in zone2.hvac_modes  # the central unit has no program for it (#895)
    assert HVACMode.HEAT in zone2.hvac_modes and HVACMode.COOL in zone2.hvac_modes

    # The central unit's next status no longer puts its own mode on the zone.
    zone2._handle_central_mode_update(HVACMode.HEAT)
    assert zone2.hvac_mode == HVACMode.COOL
    zone2._handle_central_mode_update(HVACMode.OFF)
    assert zone2.hvac_mode == HVACMode.COOL
    # And its setpoint is written directly.
    await zone2.async_set_temperature(temperature=21.0)
    assert zone2._plant.sent == ["*#4*2*#14*0210*3##"]


async def test_a_zone_in_the_season_of_the_central_unit_is_not_checked() -> None:
    zone3 = _live_zone("3", central_season="heating", answer=ZONE_3_READ)
    _replay(zone3, _rx("f461_startup_zone_status"))  # *4*1*3##, then the 3550's *4*110*#3##
    assert zone3._season_check_task is None
    assert zone3._central_zone_seen is True
    assert zone3._standalone is False


async def test_a_zone_that_lags_a_season_change_of_the_central_unit_stays_its_zone() -> None:
    """#453: one status in the old season 0.66 s after the change, the new one 2.0 s after it."""
    zone4 = _live_zone("4", central_season="heating", answer=[ISSUE_453[75], ISSUE_453[76]])
    _replay(zone4, [ISSUE_453[40]])  # *4*1*4##, the central unit in heating: nothing to check
    assert zone4._season_check_task is None
    zone4._gateway_handler.central_season = "conditioning"  # *4*202*#0##
    _replay(zone4, [ISSUE_453[65]])  # *4*1*4## again
    assert zone4._season_check_task is not None

    await _checked(zone4)

    zone4._gateway_handler.request_zone_dimension.assert_called_once_with("4", 12)
    assert zone4._standalone is False  # the read brought *4*202*4##: it followed the central unit
    assert "standalone_detected" not in zone4.extra_state_attributes


async def test_a_zone_that_followed_before_the_read_is_not_read() -> None:
    zone4 = _live_zone("4", central_season="conditioning")
    _replay(zone4, [ISSUE_453[65]])  # *4*1*4##
    _replay(zone4, [ISSUE_453[75]])  # *4*202*4##, as captured 1.4 s later
    await _checked(zone4)
    zone4._gateway_handler.request_zone_dimension.assert_not_called()
    assert zone4._standalone is False


async def test_a_report_of_the_central_unit_about_the_zone_ends_the_check() -> None:
    """The frames of #453 in another order than captured: the central unit's report first."""
    zone4 = _live_zone("4", central_season="conditioning", answer=[ISSUE_453[65]])
    _replay(zone4, [ISSUE_453[65]])  # *4*1*4##
    _replay(zone4, [ISSUE_453[115]])  # *4*203*#4##: the central unit runs it
    await _checked(zone4)
    zone4._gateway_handler.request_zone_dimension.assert_not_called()
    assert zone4._standalone is False


async def test_a_report_of_the_central_unit_during_the_read_ends_the_check() -> None:
    zone4 = _live_zone("4", central_season="conditioning", answer=[ISSUE_453[115], ISSUE_453[65]])
    _replay(zone4, [ISSUE_453[65]])
    await _checked(zone4)
    zone4._gateway_handler.request_zone_dimension.assert_called_once_with("4", 12)
    assert zone4._central_zone_seen is True
    assert zone4._standalone is False


async def test_nothing_is_concluded_from_a_read_nobody_answers(caplog: pytest.LogCaptureFixture) -> None:
    zone2 = _live_zone("2", central_season="heating", answer=None)
    _replay(zone2, ZONE_2_READ)
    with caplog.at_level("DEBUG"):
        await _checked(zone2)
    assert zone2._standalone is False
    assert "did not answer `*#4*2*12##` with its season" in caplog.text
    # The zone's next season frame starts a new check.
    _replay(zone2, ZONE_2_READ)
    assert zone2._season_check_task is not None
    zone2._cancel_season_check()


async def test_a_gateway_that_cannot_send_the_read_concludes_nothing() -> None:
    zone2 = _live_zone("2", central_season="heating")
    zone2._gateway_handler.request_zone_dimension = None  # an older handler
    zone2._gateway_handler.wait_for_initial_discovery = None
    _replay(zone2, ZONE_2_READ)
    await _checked(zone2)
    assert zone2._standalone is False


async def test_the_season_of_the_central_unit_learned_later_starts_the_check() -> None:
    """An F455 is not asked for the central unit's status at start: its season comes with its next report."""
    zone2 = _live_zone("2", central_season=None, answer=ZONE_2_READ)
    _replay(zone2, ZONE_2_READ)
    assert zone2._season_check_task is None  # the central unit's season is not known
    zone2._gateway_handler.central_season = "heating"
    zone2._handle_central_mode_update(HVACMode.HEAT)
    await _checked(zone2)
    assert zone2._standalone is True
    assert zone2.hvac_mode == HVACMode.COOL


@pytest.mark.parametrize(
    "kwargs, has_central_unit",
    [
        ({"standalone": False}, True),  # configured as a zone of the central unit
        ({"standalone": True}, True),  # configured standalone
        ({}, False),  # no central unit on the plant
    ],
)
async def test_only_a_zone_found_on_the_bus_next_to_a_central_unit_is_checked(
    kwargs: dict[str, Any], has_central_unit: bool
) -> None:
    zone2 = _live_zone("2", central_season="heating", answer=ZONE_2_READ, **kwargs)
    zone2._gateway_handler.has_central_unit = has_central_unit
    _replay(zone2, ZONE_2_READ)
    assert zone2._season_check_task is None


async def test_an_entity_that_is_not_live_is_not_checked() -> None:
    zone2 = _live_zone("2", central_season="heating", answer=ZONE_2_READ)
    zone2.platform = None
    _replay(zone2, ZONE_2_READ)
    assert zone2._season_check_task is None


async def test_a_removed_entity_stops_its_check() -> None:
    zone2 = _live_zone("2", central_season="heating", answer=ZONE_2_READ)
    zone2._cancel_season_check()  # nothing running
    _replay(zone2, ZONE_2_READ)
    task = zone2._season_check_task
    zone2._cancel_season_check()
    assert zone2._season_check_task is None
    with pytest.raises(asyncio.CancelledError):
        await task
    assert zone2._standalone is False


async def test_the_gateway_keeps_the_season_the_central_unit_reports_about_itself(hass: HomeAssistant) -> None:
    handler, _zones = await _plant(hass)
    assert handler.central_season is None
    await _receive(hass, handler, ["*4*21*#0##"])  # remote control enabled: no season
    assert handler.has_central_unit is True and handler.central_season is None
    await _receive(hass, handler, ["*4*102*#0##"])  # the 3550, 2026-11-18
    assert handler.central_season == "heating"
    await _receive(hass, handler, ["*4*202*#0##"])  # #453
    assert handler.central_season == "conditioning"
    # The 3550 about one of its zones carries its season too: all an F455 sees of it in the
    # quarter-hour report (*4*110*#3##, standalone_zone_2026_11_18).
    await _receive(hass, handler, ["*4*110*#3##"])
    assert handler.central_season == "heating"
    await _receive(hass, handler, ["*4*210*#4##"])  # central_flags_f455_poll_2026_11_09
    assert handler.central_season == "conditioning"
    await _receive(hass, handler, ["*4*0*2##"])  # a zone's own season
    assert handler.central_season == "conditioning"


@pytest.mark.parametrize("model", sorted(KNOWN_GATEWAY_MODELS))
async def test_the_confirmation_read_goes_out_on_every_gateway_model(
    hass: HomeAssistant, fast_bus_pacing: None, model: str
) -> None:
    """``*#4*Z*12##`` is no model's refused request: the tables drop only dimensions 20 and 60.

    WHO 4 document 1.5.3: the request is answered with dimension 12 and the zone's operation
    mode frame. Every emulated firmware puts it on the bus (own-firmware-oracle
    trace-corpus.tsv:57 of each of the 13 images); answered live by an F461, an F455 and a
    MyHomeServer1 (2026-11-18); the silent-zone poll already sends it on every model (#466, #932).
    """
    handler = _handler(hass, model)
    handler._initial_discovery_done.set()
    handler.request_zone_dimension("2", 12)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == ["*#4*2*12##"]


async def test_a_zone_behind_an_interface_is_not_checked_against_the_local_central_unit() -> None:
    """Another bus may have its own central unit, in its own season."""
    zone2 = _live_zone("2", central_season="heating", answer=ZONE_2_READ)
    _replay(zone2, ZONE_2_READ)
    zone2._cancel_season_check()
    zone2._interface = "01"
    assert zone2._own_season == "conditioning"
    zone2._check_season_against_central()
    assert zone2._season_check_task is None
    # And the local zone 2 does not take the season of zone 2 behind an interface.
    local = _live_zone("2", central_season="conditioning")
    local.handle_event(OWNEvent.parse("*4*1*2#4#01##"))  # built from *4*1*2## and the interface suffix
    assert local._own_season is None and local._own_off is None


async def test_a_central_unit_behind_an_interface_does_not_set_the_local_season(hass: HomeAssistant) -> None:
    handler, _zones = await _plant(hass)
    await _receive(hass, handler, ["*4*202*#0#4#01##"])  # built from *4*202*#0## and the interface suffix: no capture
    assert handler.central_season is None
    await _receive(hass, handler, ["*4*102*#0##"])
    assert handler.central_season == "heating"


async def test_the_season_is_kept_before_the_entities_see_the_frame(hass: HomeAssistant) -> None:
    """Review of #955, N1: the central unit's entity hands its mode to the zones while it handles the frame."""
    from homeassistant.core import callback
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    handler, _zones = await _plant(hass)
    seen: list[Any] = []

    @callback
    def _seen(_message: Any) -> None:  # in the event loop, as the entities' handlers are
        seen.append(handler.central_season)

    async_dispatcher_connect(hass, f"myhome_message_{handler.mac}", _seen)
    await _receive(hass, handler, ["*4*102*#0##", "*4*202*#0##"])
    assert seen == ["heating", "conditioning"]


async def test_a_standalone_zone_in_protection_is_found_and_stays_off() -> None:
    """Review of #955, N2: ``*4*102*2##`` is antifreeze, a zone that is off in the heating season."""
    # Built from zone 2's captured read (*#4*2*12*0200*3##, *4*0*2##) with the protection frame.
    protection = ["*#4*2*12*0070*3##", "*4*102*2##"]
    zone2 = _live_zone("2", central_season="conditioning", answer=protection)
    _replay(zone2, protection)
    assert zone2.hvac_mode == HVACMode.OFF
    await _checked(zone2)
    assert zone2._standalone is True
    assert zone2.hvac_mode == HVACMode.OFF  # not turned to heat by the detection
