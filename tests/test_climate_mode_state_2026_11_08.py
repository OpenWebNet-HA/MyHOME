"""Climate mode state from status frames only: live F461 + F455 replay, 2026-11-08.

One SCS bus with a BTicino 3550 99-zone central unit (#0), zone 2 a standalone
thermostat and zone 4 a zone of the 3550, reached through an F461 2.0.11 and an F455.
Every service call returned HTTP 200, yet Home Assistant showed the wrong mode:

* the F455 emits ``*4*311*#2##`` after each status report of zone 2, which read as AUTO;
* a standalone zone's ``*4*0*2##`` / ``*4*1*2##`` was taken as season only, so HEAT <-> COOL was not
  shown, and the other gateway's entity kept the old mode;
* the 3550 answers HEAT / COOL on #0 with antifreeze / thermal protection (102 / 202),
  which read as OFF, and the optimistic HEAT / COOL stayed on the second press;
* the F455 hands Home Assistant's own ``*4*0*#0##`` back, which cleared the battery flag;
* HEAT on a zone of a central in the conditioning season and AUTO on a standalone zone
  are not answered at all.

Frames: tests/fixtures/traces/climate_mode_2026_11_08/frames.json.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.exceptions import ServiceValidationError
from OWNd.message import OWNEvent, OWNHeatingCommand

FRAMES = json.loads(
    (Path(__file__).parent / "fixtures" / "traces" / "climate_mode_2026_11_08" / "frames.json").read_text()
)


def _rx(trace: str) -> list[str]:
    return [raw for _t, direction, raw in FRAMES[trace] if direction == "rx"]


def _tx(trace: str) -> list[str]:
    return [raw for _t, direction, raw in FRAMES[trace] if direction == "tx"]


@pytest.fixture(autouse=True)
def _entity_base_init() -> Iterator[None]:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield


def _gateway(has_central_unit: bool = True) -> MagicMock:
    gw = MagicMock()
    gw.mac = "00:03:50:00:00:01"
    gw.unique_id = gw.mac
    gw.log_id = "[Test Gateway]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    gw.has_central_unit = has_central_unit
    return gw


def _climate(gateway: MagicMock, where: str, *, standalone: bool | None, central: bool = False) -> Any:
    from custom_components.myhome.climate import MyHOMEClimate

    hass = MagicMock()
    hass.data = {}
    entity = MyHOMEClimate(
        hass=hass,
        name="Climate",
        device_id=f"4-{where}",
        who="4",
        where=where,
        heating=True,
        cooling=True,
        fan=False,
        standalone=standalone,
        central=central,
        manufacturer="BTicino",
        model="Thermostat",
        gateway=gateway,
    )
    entity.platform = MagicMock()
    entity.entity_id = "climate.test"
    setattr(entity, "async_schedule_update_ha_state", MagicMock())
    setattr(entity, "async_write_ha_state", MagicMock())
    return entity


def _replay(entity: Any, frames: list[str]) -> None:
    for raw in frames:
        entity.handle_event(OWNEvent.parse(raw))


def _sent(gateway: MagicMock) -> list[str]:
    return [str(call.args[0]) for call in gateway.send.await_args_list]


# F1: a standalone zone's state comes from its plain-Z frames, not from the #Z form.


def test_f455_trailing_311_does_not_turn_the_standalone_zone_auto() -> None:
    zone2 = _climate(_gateway(), "2", standalone=True)
    zone2._attr_hvac_mode = HVACMode.OFF
    zone2._attr_hvac_action = HVACAction.OFF
    _replay(zone2, _rx("f455_zone2_heat"))
    assert zone2.hvac_mode == HVACMode.HEAT
    assert zone2.target_temperature == 26.5


def test_zone_of_the_central_still_takes_its_state_from_the_central() -> None:
    # *4*what*#Z## from the 3550 is the zone's state (WHO 4 p. 65): it must survive.
    zone4 = _climate(_gateway(), "4", standalone=False)
    zone4._attr_hvac_mode = HVACMode.OFF
    _replay(zone4, _rx("f461_zone4_auto_in_cooling"))
    assert zone4.hvac_mode == HVACMode.COOL
    # The generic *4*311*#4## is not the zone's state (2026-11-09: F455 / MH201 synthetic frame
    # before the 3550's seasonal status); the seasonal *4*210*#4## / *4*203*#4## are.
    _replay(zone4, ["*4*311*#4##"])
    assert zone4.hvac_mode == HVACMode.COOL
    _replay(zone4, ["*4*203*#4##"])
    assert zone4.hvac_mode == HVACMode.OFF


# F2: the standalone zone's answer to Home Assistant's own mode 1 / 2 setpoint write is its mode.


async def test_standalone_heat_to_cool_shows_cool_from_the_thermostat_answer() -> None:
    gateway = _gateway()
    zone2 = _climate(gateway, "2", standalone=True)
    zone2._target_temperature = 26.5
    _replay(zone2, ["*4*1*2##", "*#4*2*12*0265*3##"])
    zone2._attr_hvac_mode = HVACMode.HEAT
    await zone2.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(gateway) == _tx("f461_zone2_heat_to_cool")
    for raw in _rx("f461_zone2_heat_to_cool"):
        frame = OWNEvent.parse(raw)
        zone2.handle_event(frame)
        zone2.handle_event(frame)  # delivered by the climate and the sensor platform
    assert zone2.hvac_mode == HVACMode.COOL
    assert zone2.extra_state_attributes["season"] == "conditioning"


def test_unsolicited_season_frame_is_the_standalone_zone_mode() -> None:
    # Plain-Z 0 / 1 is a standalone thermostat's operation mode (WHO 4 p. 63), whoever changed it.
    zone2 = _climate(_gateway(), "2", standalone=True)
    zone2._attr_hvac_mode = HVACMode.HEAT
    _replay(zone2, ["*4*0*2##"])
    assert zone2.hvac_mode == HVACMode.COOL
    _replay(zone2, ["*4*1*2##"])
    assert zone2.hvac_mode == HVACMode.HEAT
    _replay(zone2, ["*4*303*2##"])  # off wins through its own frame
    assert zone2.hvac_mode == HVACMode.OFF


async def test_both_gateways_show_cool_after_the_f455_switches_the_standalone_zone() -> None:
    f455, f461 = _gateway(), _gateway()
    zone2_f455 = _climate(f455, "2", standalone=True)
    zone2_f461 = _climate(f461, "2", standalone=True)
    for zone in (zone2_f455, zone2_f461):
        zone._target_temperature = 26.5
        zone._attr_hvac_mode = HVACMode.HEAT
    await zone2_f455.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(f455) == _tx("f455_zone2_cool_from_f455_ui")
    _replay(zone2_f455, _rx("f455_zone2_cool_from_f455_ui"))
    _replay(zone2_f461, _rx("f461_zone2_cool_from_f455_ui"))  # incl. a later mode 3 setpoint change
    assert zone2_f455.hvac_mode == zone2_f461.hvac_mode == HVACMode.COOL


def test_zone_of_the_central_keeps_its_mode_on_a_season_frame() -> None:
    zone4 = _climate(_gateway(), "4", standalone=False)
    zone4._attr_hvac_mode = HVACMode.AUTO
    _replay(zone4, ["*4*0*4##"])
    assert zone4.hvac_mode == HVACMode.AUTO


# F3: 102 / 202 on #0 are protection in their season, and HEAT / COOL on #0 waits for the 3550.


async def test_central_heat_shows_the_antifreeze_the_3550_answers() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, _rx("f461_central_cool"))
    assert central.hvac_mode == HVACMode.COOL
    assert central.extra_state_attributes["central_operation"] == "thermal_protection"

    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == _tx("f461_central_heat")
    assert central.hvac_mode == HVACMode.COOL  # nothing reported yet
    _replay(central, _rx("f461_central_heat"))
    assert central.hvac_mode == HVACMode.HEAT
    assert central.hvac_action != HVACAction.OFF
    attributes = central.extra_state_attributes
    assert attributes["central_operation"] == "antifreeze"
    assert attributes["season"] == "heating"
    assert attributes["battery_fault"] is True
    assert attributes["remote_control"] == "enabled"

    await central.async_set_hvac_mode(HVACMode.HEAT)  # second press: the 3550 sends nothing
    assert _sent(gateway)[-1:] == _tx("f461_central_heat_second_press")
    assert central.hvac_mode == HVACMode.HEAT
    assert central.extra_state_attributes["central_operation"] == "antifreeze"


def test_central_protection_does_not_switch_the_zones_off() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    with patch("custom_components.myhome.climate.async_dispatcher_send") as dispatch:
        _replay(central, _rx("f461_central_cool"))
    assert [call.args[2] for call in dispatch.call_args_list] == [HVACMode.COOL]


def test_central_operation_clears_on_a_manual_mode_report() -> None:
    central = _climate(_gateway(), "#0", standalone=None, central=True)
    _replay(central, _rx("f461_central_heat"))
    assert central.extra_state_attributes["central_operation"] == "antifreeze"
    _replay(central, ["*4*110#0215*#0##"])
    assert "central_operation" not in central.extra_state_attributes
    assert central.hvac_mode == HVACMode.HEAT


async def test_central_off_and_auto_stay_immediate() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    central._attr_hvac_mode = HVACMode.HEAT
    await central.async_set_hvac_mode(HVACMode.OFF)
    assert central.hvac_mode == HVACMode.OFF
    await central.async_set_hvac_mode(HVACMode.AUTO)
    assert central.hvac_mode == HVACMode.AUTO
    sent = _sent(gateway)
    assert sent == ["*4*303*#0##", str(OWNHeatingCommand.set_central_mode("#0", "auto"))]
    assert sent[1] == "*4*3100*#0##"  # published last-weekly-program builder (OWNd#169), traced live


# F6: the F455's echo of Home Assistant's own #0 command is not a 3550 report.


async def test_f455_echo_of_our_central_command_changes_nothing() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    first, second = _rx("f455_central_cool"), _rx("f455_central_cool_second_press")

    async def send_and_echo(command: Any) -> None:
        # The F455 returns the frame before send() is done (1 ms, live). The climate and the
        # sensor platforms both route WHO 4 frames, so the entity gets the same object twice.
        echo = OWNEvent.parse(str(command))
        central.handle_event(echo)
        central.handle_event(echo)

    gateway.send.side_effect = send_and_echo
    central._attr_hvac_mode = HVACMode.HEAT
    central._attr_hvac_action = HVACAction.IDLE
    await central.async_set_hvac_mode(HVACMode.COOL)
    assert first[0] == second[0] == _tx("f455_central_cool")[0]
    assert central.hvac_mode == HVACMode.HEAT  # the echo is not the season report
    _replay(central, first[1:])
    assert central.hvac_mode == HVACMode.COOL
    assert central.extra_state_attributes["battery_fault"] is True

    await central.async_set_hvac_mode(HVACMode.COOL)  # second press, only the echo comes back
    attributes = central.extra_state_attributes
    assert attributes["battery_fault"] is True
    assert attributes["central_operation"] == "thermal_protection"
    assert central.hvac_mode == HVACMode.COOL


async def test_only_one_echo_is_dropped_and_only_while_expected() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    await central.async_set_hvac_mode(HVACMode.COOL)
    _replay(central, ["*4*0*#0##"])  # the echo
    assert central.extra_state_attributes.get("season") is None
    _replay(central, ["*4*0*#0##"])  # another copy of it, e.g. from the second routing
    assert central.extra_state_attributes.get("season") is None
    from custom_components.myhome import climate as climate_module

    later = climate_module.time.monotonic() + 0.121
    with patch("custom_components.myhome.climate.time.monotonic", return_value=later):
        _replay(central, ["*4*0*#0##"])  # an identical frame after the window is a report
    assert central.hvac_mode == HVACMode.COOL

    await central.async_set_hvac_mode(HVACMode.HEAT)
    with patch("custom_components.myhome.climate.time.monotonic", return_value=float("inf")):
        _replay(central, ["*4*1*#0##"])  # too late to be the echo
    assert central.hvac_mode == HVACMode.HEAT


# F4: a zone of the central unit runs in the central's season.


async def test_heat_on_a_zone_of_the_central_in_conditioning_is_refused() -> None:
    gateway = _gateway()
    zone4 = _climate(gateway, "4", standalone=False)
    _replay(zone4, _rx("f461_zone4_auto_in_cooling"))
    zone4._target_temperature = 20.5
    with pytest.raises(ServiceValidationError) as err:
        await zone4.async_set_hvac_mode(HVACMode.HEAT)
    assert err.value.translation_key == "zone_heat_in_conditioning_season"
    gateway.send.assert_not_awaited()
    assert zone4.hvac_mode == HVACMode.COOL
    await zone4.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(gateway) == ["*#4*#4*#14*0205*2##"]


async def test_zone_of_the_central_with_unknown_season_is_not_restricted() -> None:
    gateway = _gateway()
    zone4 = _climate(gateway, "4", standalone=False)
    zone4._target_temperature = 20.5
    await zone4.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == ["*#4*#4*#14*0205*1##"]


async def test_standalone_zone_is_not_season_restricted() -> None:
    gateway = _gateway()
    zone2 = _climate(gateway, "2", standalone=True)
    _replay(zone2, ["*4*0*2##"])
    zone2._target_temperature = 26.5
    await zone2.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == ["*#4*2*#14*0265*1##"]


# F5: AUTO is the central unit's program for its zones; a standalone thermostat has none.


async def test_auto_on_a_standalone_zone_of_a_plant_with_a_central_is_refused() -> None:
    gateway = _gateway(has_central_unit=True)
    zone2 = _climate(gateway, "2", standalone=True)
    zone2._attr_hvac_mode = HVACMode.HEAT
    with pytest.raises(ServiceValidationError) as err:
        await zone2.async_set_hvac_mode(HVACMode.AUTO)
    assert err.value.translation_key == "auto_standalone_zone"
    gateway.send.assert_not_awaited()
    assert zone2.hvac_mode == HVACMode.HEAT


async def test_auto_on_a_standalone_zone_without_a_central_keeps_the_setpoint_fallback() -> None:
    gateway = _gateway(has_central_unit=False)
    zone2 = _climate(gateway, "2", standalone=True)
    zone2._target_temperature = 26.5
    await zone2.async_set_hvac_mode(HVACMode.AUTO)
    assert _sent(gateway) == ["*#4*2*#14*0265*3##"]


async def test_restored_season_alone_does_not_restrict_a_zone_of_the_central() -> None:
    # After a restart the season comes from the stored state until the zone reports again.
    gateway = _gateway()
    zone4 = _climate(gateway, "4", standalone=False)
    zone4._season = "conditioning"
    zone4._target_temperature = 20.5
    await zone4.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == ["*#4*#4*#14*0205*1##"]


# A stale AUTO restored after a restart: the standalone zone's own season frame replaces it.


async def test_restored_auto_on_a_standalone_zone_is_replaced_by_its_season_frame() -> None:
    from homeassistant.core import State

    zone2 = _climate(_gateway(), "2", standalone=True)
    await zone2.async_restore_last_state(State("climate.test", "auto", {"temperature": 26.5, "season": "heating"}))
    assert zone2.hvac_mode == HVACMode.AUTO
    _replay(zone2, _rx("f455_zone2_after_restart"))  # ends with the F455's *4*311*#2##
    assert zone2.hvac_mode == HVACMode.HEAT
    _replay(zone2, ["*4*0*2##"])  # the zone's own report keeps being its mode
    assert zone2.hvac_mode == HVACMode.COOL


async def test_restored_mode_of_a_zone_of_the_central_is_kept_on_a_season_frame() -> None:
    from homeassistant.core import State

    zone4 = _climate(_gateway(), "4", standalone=False)
    await zone4.async_restore_last_state(State("climate.test", "auto", {"temperature": 20.5}))
    _replay(zone4, ["*4*0*4##"])
    assert zone4.hvac_mode == HVACMode.AUTO


def test_central_off_then_antifreeze_is_heat_and_no_longer_action_off() -> None:
    # Live 3550: *4*303*#0## answers OFF; a later *4*1*#0## is answered with antifreeze.
    central = _climate(_gateway(), "#0", standalone=None, central=True)
    _replay(central, ["*4*303*#0##"])
    assert central.hvac_mode == HVACMode.OFF
    assert central.hvac_action == HVACAction.OFF
    _replay(central, ["*4*102*#0##"])
    assert central.hvac_mode == HVACMode.HEAT
    assert central.hvac_action == HVACAction.IDLE
    assert central.extra_state_attributes["central_operation"] == "antifreeze"


# PR #880 review: the F455's synthetic *4*311*#Z## is not a central unit report.


def _dispatcher(gateway: MagicMock) -> Any:
    from custom_components.myhome.gateway_events import GatewayEventDispatcher

    gateway.hass = MagicMock()
    gateway.host = "gateway.test"
    gateway.generate_events = False
    gateway._correlate_shared_bus_traffic = MagicMock()
    gateway._bridge_to_primary = MagicMock()
    gateway.health_owner = MagicMock(return_value=None)
    return GatewayEventDispatcher(gateway)


async def test_f455_synthetic_311_does_not_make_a_standalone_only_plant_central() -> None:
    gateway = _gateway(has_central_unit=False)
    dispatcher = _dispatcher(gateway)
    zone2 = _climate(gateway, "2", standalone=None)  # zero-config: follows the plant
    for raw in _rx("f455_zone2_after_restart"):
        await dispatcher.process_message(OWNEvent.parse(raw))
        zone2.handle_event(OWNEvent.parse(raw))
    assert gateway.has_central_unit is False
    assert zone2._standalone is True
    assert zone2.hvac_mode == HVACMode.HEAT
    await zone2.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(gateway) == ["*#4*2*#14*0265*2##"]


async def test_auto_fallback_survives_the_f455_trailing_311() -> None:
    gateway = _gateway(has_central_unit=False)
    dispatcher = _dispatcher(gateway)
    zone2 = _climate(gateway, "2", standalone=True)
    for raw in _rx("f455_zone2_after_restart"):
        await dispatcher.process_message(OWNEvent.parse(raw))
        zone2.handle_event(OWNEvent.parse(raw))
    assert gateway.has_central_unit is False
    await zone2.async_set_hvac_mode(HVACMode.AUTO)
    assert _sent(gateway) == ["*#4*2*#14*0265*3##"]


@pytest.mark.parametrize(
    ("raw", "central"),
    [
        ("*4*311*#2##", False),  # generic automatic (WHO 4 p. 9), F455 synthetic
        ("*4*310*#2##", False),  # generic manual (WHO 4 p. 9)
        ("*4*303*#2##", False),  # generic off, not a zone status (WHO 4 p. 65)
        ("*4*111*#2##", True),  # zone automatic by central unit (WHO 4 p. 65)
        ("*4*210*#4##", True),  # zone manual conditioning by central unit, live 3550
        ("*4*203*#4##", True),  # zone off conditioning by central unit, live 3550
        ("*4*102*#0##", True),  # the central unit itself (WHO 4 p. 64)
        ("*4*303*#0#1##", True),  # 4-zone central unit
        ("*#4*#4*14*0205*2##", True),  # a dimension of a zone of the central
        ("*#4*2*0*0238##", False),  # a standalone thermostat's temperature
        ("*#4*101*0*0205##", False),  # a probe (WHO 4 p. 6)
        ("*#4*2#1*20*0##", False),  # an actuator of zone 2
    ],
)
async def test_central_unit_detection_by_frame(raw: str, central: bool) -> None:
    gateway = _gateway(has_central_unit=False)
    await _dispatcher(gateway).process_message(OWNEvent.parse(raw))
    assert gateway.has_central_unit is central


# PR #880 review: a gateway that does not echo still gets the 3550's identical answer.


async def test_identical_central_answer_after_the_echo_window_is_a_report() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, _rx("f461_central_heat"))
    central._battery_fault = True
    assert central.extra_state_attributes["central_operation"] == "antifreeze"
    await central.async_set_hvac_mode(HVACMode.OFF)
    assert central.hvac_action == HVACAction.OFF
    assert "central_operation" not in central.extra_state_attributes
    from custom_components.myhome import climate as climate_module

    later = climate_module.time.monotonic() + 0.121  # the 3550's first answer came after 121 ms
    with patch("custom_components.myhome.climate.time.monotonic", return_value=later):
        _replay(central, ["*4*303*#0##"])
    # a mode frame alone does not clear the 3550's flags: only a settled 20 / 21 burst does (2026-11-09)
    assert central.extra_state_attributes["battery_fault"] is True
    assert central.hvac_mode == HVACMode.OFF


async def test_failed_send_disarms_the_echo() -> None:
    gateway = _gateway()
    gateway.send.side_effect = OSError("gateway gone")
    central = _climate(gateway, "#0", standalone=None, central=True)
    with pytest.raises(OSError):
        await central.async_set_hvac_mode(HVACMode.COOL)
    assert central._central_echo is None


# PR #880 review: a 4-zone central unit (#0#1..#0#4) is not polled, it keeps HEAT / COOL.


@pytest.mark.parametrize("hvac_mode", [HVACMode.HEAT, HVACMode.COOL])
async def test_four_zone_central_keeps_the_mode_it_is_set_to(hvac_mode: HVACMode) -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0#1", standalone=None, central=True)
    await central.async_set_hvac_mode(hvac_mode)
    assert central.hvac_mode == hvac_mode
    assert _sent(gateway) == [f"*4*{1 if hvac_mode == HVACMode.HEAT else 0}*#0#1##"]


# PR #880 review: an AUTO the zone itself reports is not cancelled by its season frame.


def test_myhomeserver1_schedule_auto_survives_the_trailing_season_frame() -> None:
    zone1 = _climate(_gateway(has_central_unit=False), "1", standalone=None)
    _replay(zone1, ["*#4*1*7*3*1*0210##", "*4*1*1##"])
    assert zone1.hvac_mode == HVACMode.AUTO
    _replay(zone1, ["*#4*1*7*1*1*0210##", "*4*0*1##"])  # manual context, then conditioning
    assert zone1.hvac_mode == HVACMode.COOL


async def test_restored_auto_then_schedule_auto_is_kept() -> None:
    from homeassistant.core import State

    zone1 = _climate(_gateway(has_central_unit=False), "1", standalone=None)
    await zone1.async_restore_last_state(State("climate.test", "auto", {"temperature": 21.0}))
    _replay(zone1, ["*#4*1*7*3*1*0210##", "*4*1*1##"])
    assert zone1.hvac_mode == HVACMode.AUTO


# PR #880 review: central_operation survives a restart; 302 is the protection of the season.


async def test_central_operation_is_restored() -> None:
    from homeassistant.core import State

    central = _climate(_gateway(), "#0", standalone=None, central=True)
    await central.async_restore_last_state(
        State("climate.test", "heat", {"central_operation": "antifreeze", "season": "heating"})
    )
    assert central.extra_state_attributes["central_operation"] == "antifreeze"
    assert central.hvac_mode == HVACMode.HEAT


@pytest.mark.parametrize(
    ("season_frame", "mode", "operation"),
    [("*4*1*#0##", HVACMode.HEAT, "antifreeze"), ("*4*0*#0##", HVACMode.COOL, "thermal_protection")],
)
def test_central_generic_protection_follows_the_season(season_frame: str, mode: HVACMode, operation: str) -> None:
    central = _climate(_gateway(), "#0", standalone=None, central=True)
    _replay(central, [season_frame, "*4*302*#0##"])
    assert central.hvac_mode == mode
    assert central.extra_state_attributes["central_operation"] == operation


# PR #880 review: the season is in the translated text, not an English placeholder.


@pytest.mark.parametrize("language", ["strings", "en", "fr", "it", "nl"])
def test_season_errors_are_translated_without_a_season_placeholder(language: str) -> None:
    base = Path(__file__).parents[1] / "custom_components" / "myhome"
    path = base / "strings.json" if language == "strings" else base / "translations" / f"{language}.json"
    exceptions = json.loads(path.read_text())["exceptions"]
    assert "zone_mode_outside_central_season" not in exceptions
    for key in ("zone_heat_in_conditioning_season", "zone_cool_in_heating_season", "auto_standalone_zone"):
        assert "{season}" not in exceptions[key]["message"]
        assert "{name}" in exceptions[key]["message"]


async def test_cool_on_a_zone_of_the_central_in_heating_is_refused() -> None:
    gateway = _gateway()
    zone4 = _climate(gateway, "4", standalone=False)
    _replay(zone4, ["*4*1*4##"])
    zone4._target_temperature = 20.5
    with pytest.raises(ServiceValidationError) as err:
        await zone4.async_set_hvac_mode(HVACMode.COOL)
    assert err.value.translation_key == "zone_cool_in_heating_season"
    gateway.send.assert_not_awaited()


async def test_echo_delivered_as_two_copies_changes_nothing() -> None:
    # The second routing may hand the entity another object for the same frame.
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, _rx("f455_central_cool"))
    assert central.extra_state_attributes["battery_fault"] is True

    async def send_and_echo(command: Any) -> None:
        central.handle_event(OWNEvent.parse(str(command)))
        central.handle_event(OWNEvent.parse(str(command)))

    gateway.send.side_effect = send_and_echo
    await central.async_set_hvac_mode(HVACMode.COOL)
    assert central.extra_state_attributes["battery_fault"] is True
    assert central.extra_state_attributes["central_operation"] == "thermal_protection"


def test_zone_of_the_central_found_from_the_3550_quarter_hour_push() -> None:
    # Live 3550 push (14:45:00): no #0 frame, the zone 4 status in the #Z form.
    gateway = _gateway(has_central_unit=False)
    zone4 = _climate(gateway, "4", standalone=None)
    _replay(zone4, ["*#4*4*12*0205*3##", "*4*0*4##", "*#4*4*14*0205*3##", "*4*210*#4##"])
    assert gateway.has_central_unit is True
    assert zone4._standalone is False
    assert zone4.hvac_mode == HVACMode.COOL
