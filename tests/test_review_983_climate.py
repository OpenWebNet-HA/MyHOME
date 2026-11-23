"""Four points of the review of the WHO 4 changes, in the climate entity.

- The commanded setpoint saved before a restart (``nominal_temperature``) is what a zone goes
  back to; a protection setpoint that arrives after the restart must not replace it by the
  setpoint that was in force, nor count as the commanded setpoint having been reported.
- A central unit configured with WHERE ``0`` is the 99-zone central unit ``#0`` on the bus.
- A mode frame that carries a setpoint (manual) is the zone's operation too.
- The fan speed ``15`` (off) of dimension 11 is over when the zone reports a speed again.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from OWNd.message import OWNHeatingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import MyHOMEClimate, async_setup_entry
from custom_components.myhome.const import DOMAIN, central_mode_signal
from custom_components.myhome.legacy_yaml import _apply_yaml_platforms
from custom_components.myhome.validate import climate_schema
from tests.conftest import attach_runtime
from tests.test_climate_setpoint_in_force import (
    F461_POLL_REPLY,
    ZONE_3_ANTIFREEZE,
    ZONE_3_MANUAL_25,
    _central_plant_gateway,
    _feed,
    _saved,
    _zone,
)
from tests.test_device_health import _dispatcher

MAC = "00:03:50:00:04:97"


def _sent(zone: MyHOMEClimate) -> list[str]:
    return [str(call.args[0]) for call in zone._gateway_handler.send.await_args_list]


# ── the commanded setpoint saved before a restart ──

# Zone 2 of the F461 plant (``F461_POLL_REPLY``): 23.5 in force, 21.0 commanded. Then the zone
# goes into antifreeze: its mode frame and its protection setpoint, in the two orders a zone
# sends them (``ZONE_3_ANTIFREEZE`` has the mode first; a status burst has dimension 12 first).
INTO_ANTIFREEZE = [("*4*102*2##", "*#4*2*12*0070*3##"), ("*#4*2*12*0070*3##", "*4*102*2##")]


@pytest.mark.parametrize("frames", INTO_ANTIFREEZE)
async def test_a_zone_restored_while_running_keeps_its_commanded_setpoint_through_protection(hass: HomeAssistant, frames: tuple[str, str]) -> None:
    live = _zone(hass)
    _feed(live, *F461_POLL_REPLY)
    saved = _saved(live)
    assert (saved.state, saved.attributes["temperature"], saved.attributes["nominal_temperature"]) == ("heat", 23.5, 21.0)
    _feed(live, *frames)
    await live.async_set_hvac_mode(HVACMode.HEAT)
    assert len(_sent(live)) == 1  # what the entity that did not restart writes: the commanded 21.0

    restored = _zone(hass)
    await restored.async_restore_last_state(saved)
    assert restored.extra_state_attributes["nominal_temperature"] == 21.0
    _feed(restored, *frames)
    assert restored.hvac_mode == HVACMode.OFF and restored.target_temperature == 7.0
    assert restored.extra_state_attributes["nominal_temperature"] == 21.0
    assert "*#4*2*14##" in restored._silent_zone_reads()  # saved, not reported since the restart
    await restored.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(restored) == _sent(live)


async def test_a_zone_restored_while_running_shows_its_commanded_setpoint_when_it_leaves_off(hass: HomeAssistant) -> None:
    live = _zone(hass)
    _feed(live, *F461_POLL_REPLY)
    restored = _zone(hass)
    await restored.async_restore_last_state(_saved(live))
    _feed(restored, "*4*102*2##", "*4*1*2##")  # into antifreeze and out again, no setpoint frame
    assert restored.hvac_mode == HVACMode.HEAT
    assert restored.target_temperature == 21.0  # until dimension 12 says what is in force
    _feed(restored, "*#4*2*12*0235*3##")
    assert restored.target_temperature == 23.5


async def test_a_saved_commanded_setpoint_is_asked_until_the_zone_reports_it(hass: HomeAssistant) -> None:
    live = _zone(hass, where="3", gateway=_central_plant_gateway(), standalone=None)
    _feed(live, *ZONE_3_MANUAL_25, *ZONE_3_ANTIFREEZE)
    restored = _zone(hass, where="3", gateway=_central_plant_gateway(), standalone=None)
    await restored.async_restore_last_state(_saved(live))
    _feed(restored, "*4*102*3##", "*#4*3*0*0255##", "*#4*3*12*0070*3##")  # reports again, in antifreeze
    assert restored.extra_state_attributes["nominal_temperature"] == 25.0
    assert "*#4*3*14##" in restored._silent_zone_reads()
    _feed(restored, "*#4*#3*14*0250*1##")  # the commanded setpoint, from the plant
    assert "*#4*3*14##" not in restored._silent_zone_reads()
    assert restored.extra_state_attributes["nominal_temperature"] == 25.0


@pytest.mark.parametrize(
    ("attributes", "shown"),
    [
        ({"temperature": 7.0}, None),  # saved by 2.0.0: no commanded setpoint beside it
        ({"temperature": 7.0, "nominal_temperature": 25.0}, 25.0),
    ],
)
async def test_a_zone_that_reported_running_before_it_was_restored_does_not_show_the_saved_protection_setpoint(
    hass: HomeAssistant, attributes: dict, shown: float | None
) -> None:
    zone = _zone(hass, where="3", gateway=_central_plant_gateway(), standalone=None)
    _feed(zone, "*4*1*3##")  # the frame that discovered it, replayed before the saved state
    await zone.async_restore_last_state(State(zone.entity_id, "off", attributes))
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.target_temperature == shown
    assert zone._effective_nominal_temperature() == shown  # never the 7.0 (#383)
    assert "*#4*3*12##" in zone._silent_zone_reads()


async def test_a_knob_position_reported_before_the_restore_is_kept(hass: HomeAssistant) -> None:
    zone = _zone(hass, where="4")
    _feed(zone, "*#4*4*13*02##")  # MH200N, issue_466
    await zone.async_restore_last_state(State(zone.entity_id, "heat", {"temperature": 21.0, "local_offset": 0}))
    assert zone._local_offset == 2
    assert zone.extra_state_attributes["local_offset"] == 2


# ── WHERE 0 as the central unit ──


async def test_a_central_unit_configured_as_zone_0_is_the_central_unit_on_the_bus(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
    entry.add_to_hass(hass)
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.is_connected = True
    gateway.log_id = "[test where 0]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    runtime = attach_runtime(hass, entry, MAC, gateway)
    configured: dict[str, dict] = {"climate": {}}
    _apply_yaml_platforms({"climate": climate_schema({"centrale": {"zone": "0", "name": "Centrale"}})}, configured)
    runtime.platforms["climate"] = configured["climate"]

    entities: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, entities.extend)
    assert [entity._where for entity in entities] == ["0"]
    central = entities[0]
    central.hass = hass
    central.entity_id = "climate.centrale"
    central.async_write_ha_state = MagicMock()
    assert central._bus_where == "#0" and central._request_where == "#0"

    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*4*102*#0##"))  # tests/fixtures/traces/issue_697/f454_99zone_central_subordinate_zones.json:58
    assert central.hvac_mode == HVACMode.HEAT  # antifreeze: the heating season
    assert len(entities) == 1  # and no second central unit from its own frame

    await central.async_update()
    assert [str(call.args[0]) for call in gateway.send_status_request.await_args_list] == ["*#4*#0##"]


# ── a mode frame with a setpoint is the zone's operation ──


async def test_a_manual_setpoint_frame_ends_the_antifreeze_operation_of_the_zone(hass: HomeAssistant) -> None:
    zone = _zone(hass, where="1", gateway=_central_plant_gateway(), standalone=None)
    _feed(zone, "*4*102*1##")  # F453 (discussion_770), MyHomeServer1 (issue_429)
    assert zone.extra_state_attributes["zone_operation"] == "antifreeze"
    _feed(zone, "*4*110#0160*#0#1##")  # a 4-zone central unit setting zone 1 to manual 16.0 (issue 994)
    assert zone.hvac_mode == HVACMode.HEAT and zone.target_temperature == 16.0
    assert zone.extra_state_attributes["zone_operation"] == "manual"


# ── the fan speed of dimension 11 ──


async def test_a_fan_speed_after_off_ends_the_off(hass: HomeAssistant) -> None:
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY, "*#4*2*11*2##")  # tests/fixtures/traces/f461_live_2026-10-15/myhome_trace_F461_live_2026-10-15.json:2991
    assert zone.fan_mode == "medium" and "running_fan_speed" not in zone.extra_state_attributes
    # 15 is "OFF" in the dimension 11 table of the WHO 4 document; no capture holds the frame.
    _feed(zone, "*#4*2*11*15##")
    assert zone.extra_state_attributes["running_fan_speed"] == "off"
    for frame, mode in (("*#4*2*11*1##", "low"), ("*#4*2*11*0##", "auto")):
        _feed(zone, "*#4*2*11*15##", frame)
        assert zone.fan_mode == mode
        assert "running_fan_speed" not in zone.extra_state_attributes


# ── what the review of these changes found ──


async def _zone_3_restored_in_antifreeze(hass: HomeAssistant, gateway: MagicMock | None = None) -> MyHOMEClimate:
    """Zone 3 saved in antifreeze with 25.0 commanded, restored, and in antifreeze again."""
    live = _zone(hass, where="3", gateway=_central_plant_gateway(), standalone=None)
    _feed(live, *ZONE_3_MANUAL_25, *ZONE_3_ANTIFREEZE)
    restored = _zone(hass, where="3", gateway=gateway or _central_plant_gateway(), standalone=None)
    await restored.async_restore_last_state(_saved(live))
    _feed(restored, *ZONE_3_ANTIFREEZE)
    return restored


async def test_the_read_of_a_saved_commanded_setpoint_is_sent_and_not_only_planned(hass: HomeAssistant) -> None:
    gateway = _central_plant_gateway()
    zone = await _zone_3_restored_in_antifreeze(hass, gateway)
    assert "*#4*3*14##" in zone._silent_zone_reads()
    gateway.silent_zone_poll_needed = True
    gateway.wait_for_zone_requests = AsyncMock()
    gateway.pace_startup_status_write = AsyncMock()

    async def reply(request: object) -> asyncio.Future[bool]:
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        if str(request) == "*#4*3*14##":
            future.cancel()  # refused: the poll ends there, and the value is still the saved one
        else:
            future.set_result(True)
        return future

    gateway.send_status_request = AsyncMock(side_effect=reply)
    await zone._poll_if_silent(grace=0)
    assert "*#4*3*14##" in [str(call.args[0]) for call in gateway.send_status_request.await_args_list]
    assert "*#4*3*14##" in zone._silent_zone_reads()


async def test_a_season_frame_does_not_count_as_the_commanded_setpoint(hass: HomeAssistant) -> None:
    zone = await _zone_3_restored_in_antifreeze(hass)
    _feed(zone, ZONE_3_MANUAL_25[1])  # the season frame alone: the zone runs again, no setpoint reported
    assert zone.hvac_mode == HVACMode.HEAT and zone.target_temperature == 25.0
    assert "*#4*3*14##" in zone._silent_zone_reads()
    _feed(zone, "*#4*#3*14*0250*1##")
    assert "*#4*3*14##" not in zone._silent_zone_reads()


async def test_a_setpoint_in_force_of_the_running_zone_is_its_commanded_setpoint_also_at_the_saved_value(
    hass: HomeAssistant,
) -> None:
    """Dimension 12 with the season and no dimension 14 is what an F461 sends after a setpoint
    write (``tests/test_climate_setpoint_in_force.py``): the running zone has then said what it
    is set to, and the value being the saved one does not make it "saved" again."""
    live = _zone(hass)
    _feed(live, "*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*13*00##", "*#4*2*0*0239##")
    restored = _zone(hass)
    await restored.async_restore_last_state(_saved(live))
    assert "*#4*2*14##" in restored._silent_zone_reads()
    _feed(restored, "*#4*2*0*0239##", "*#4*2*12*0210*3##", "*4*1*2##")
    assert restored.target_temperature == 21.0
    assert "*#4*2*14##" not in restored._silent_zone_reads()
    # Into protection within the same burst: the commanded setpoint is the saved one again.
    _feed(restored, "*#4*2*12*0070*3##", "*4*102*2##")
    assert restored.extra_state_attributes["nominal_temperature"] == 21.0
    assert "*#4*2*14##" in restored._silent_zone_reads()


async def test_the_same_knob_report_after_leaving_protection_keeps_the_commanded_setpoint_shown(
    hass: HomeAssistant,
) -> None:
    live = _zone(hass)
    _feed(live, *F461_POLL_REPLY)
    restored = _zone(hass)
    await restored.async_restore_last_state(_saved(live))
    _feed(restored, "*4*102*2##", "*4*1*2##")
    assert restored.target_temperature == 21.0
    _feed(restored, "*#4*2*13*00##")  # the knob where it was: nothing new
    assert restored.target_temperature == 21.0
    assert restored.extra_state_attributes["nominal_temperature"] == 21.0
    _feed(restored, "*#4*2*13*02##")
    assert restored.target_temperature == 23.0
    assert restored.extra_state_attributes["nominal_temperature"] == 21.0
    await restored.async_set_hvac_mode(HVACMode.HEAT)
    assert "0210" in _sent(restored)[0]  # the commanded setpoint, without the knob


@pytest.mark.parametrize("with_nominal", [True, False])
async def test_a_knob_report_gives_the_same_state_before_and_after_the_restore(hass: HomeAssistant, with_nominal: bool) -> None:
    live = _zone(hass)
    _feed(live, "*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*13*00##")
    saved = _saved(live)
    if not with_nominal:  # a state saved by 2.0.0
        saved = State(saved.entity_id, saved.state, {k: v for k, v in saved.attributes.items() if k != "nominal_temperature"})

    def view(zone: MyHOMEClimate) -> tuple[object, ...]:
        attributes = zone.extra_state_attributes
        return attributes["knob_pos"], attributes["local_offset"], attributes["nominal_temperature"], zone.target_temperature

    after = _zone(hass)
    await after.async_restore_last_state(saved)
    _feed(after, "*#4*2*13*02##")
    before = _zone(hass)
    _feed(before, "*#4*2*13*02##")
    await before.async_restore_last_state(saved)
    assert view(before) == view(after) == ("+2", 2, 21.0, 23.0)


async def test_a_saved_offset_that_is_not_a_number_counts_as_none(hass: HomeAssistant) -> None:
    zone = _zone(hass)
    await zone.async_restore_last_state(State(zone.entity_id, "heat", {"temperature": 21.0, "local_offset": "?"}))
    assert zone.target_temperature == 21.0 and zone._local_offset == 0


async def test_a_valve_report_that_both_fans_are_off_outlives_a_later_set_speed(hass: HomeAssistant) -> None:
    zone = _zone(hass)
    _feed(zone, "*#4*2*11*15##")
    # F455: tests/fixtures/traces/issue_466/F455_direct_requests_zone3_2026-11-06.jsonl:17
    _feed(zone, "*#4*2*19*5*5##")
    assert zone.extra_state_attributes["running_fan_speed"] == "off"
    _feed(zone, "*#4*2*11*1##")
    assert zone.fan_mode == "low"  # the speed the zone is set to
    assert zone.extra_state_attributes["running_fan_speed"] == "off"  # what its fans do, from the valve report


async def _central_configured_as_zone_0(hass: HomeAssistant, gateway: MagicMock, **config: str) -> list[MyHOMEClimate]:
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
    entry.add_to_hass(hass)
    gateway.config_entry = entry
    runtime = attach_runtime(hass, entry, MAC, gateway)
    configured: dict[str, dict] = {"climate": {}}
    _apply_yaml_platforms({"climate": climate_schema({"centrale": {"zone": "0", "name": "Centrale", **config}})}, configured)
    runtime.platforms["climate"] = configured["climate"]
    entities: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, entities.extend)
    for entity in entities:
        entity.hass = hass
        entity.entity_id = "climate.centrale"
        entity.async_write_ha_state = MagicMock()
    return entities


async def test_a_central_unit_configured_as_zone_0_behind_an_interface_is_not_found_a_second_time(
    hass: HomeAssistant,
) -> None:
    gateway = MagicMock(mac=MAC, is_connected=True, send=AsyncMock(), send_status_request=AsyncMock())
    entities = await _central_configured_as_zone_0(hass, gateway, interface="01")
    (central,) = entities
    assert central._request_where == "#0#4#01"
    # A built frame (the 99-zone central unit behind interface 01); no capture holds one.
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*4*31*#0#4#01##"))
    assert len(entities) == 1
    assert central.extra_state_attributes["battery_fault"] is True
    assert central._health_address == (4, "#0#4#01")


async def test_the_battery_of_a_central_unit_configured_as_zone_0_raises_one_repair(hass: HomeAssistant) -> None:
    dispatcher = _dispatcher(hass, mac=MAC)
    (central,) = await _central_configured_as_zone_0(hass, dispatcher.handler)
    # tests/fixtures/traces/climate_mode_2026_11_08/frames.json:113
    await dispatcher.process_message(OWNMessage.parse("*4*31*#0##"))
    assert central.extra_state_attributes["battery_fault"] is True
    assert central._health_address == (4, "#0")
    repairs = [issue for domain, issue in ir.async_get(hass).issues if domain == DOMAIN and "central_battery_fault" in issue]
    assert len(repairs) == 1, repairs


# ── what the review of the final commit found ──


async def test_a_fan_off_of_dimension_11_after_the_valve_report_does_not_replace_it(hass: HomeAssistant) -> None:
    zone = _zone(hass)
    _feed(zone, "*#4*2*19*5*5##", "*#4*2*11*15##", "*#4*2*11*1##")
    assert zone.fan_mode == "low"
    assert zone.extra_state_attributes["running_fan_speed"] == "off"  # still what the valve report said


@pytest.mark.parametrize(
    ("central_plant", "frames"),
    [
        # MyHomeServer1: tests/fixtures/traces/issue_429/myhome_trace_MyHomeServer1_who4_2026-09-23T19-32-29.json:61, :361
        (False, ("*#4*1*7*1*2##", "*#4*1*7*1*1*0225##")),
        (True, ("*4*110#0160*#0#1##",)),  # a manual-with-setpoint report (issue 994)
    ],
)
async def test_a_setpoint_the_zone_reports_in_another_frame_than_dimension_14_ends_the_saved_one(
    hass: HomeAssistant, central_plant: bool, frames: tuple[str, ...]
) -> None:
    def zone_1() -> MyHOMEClimate:
        return _zone(hass, where="1", gateway=_central_plant_gateway(), standalone=None) if central_plant else _zone(hass, where="1")

    live = zone_1()
    _feed(live, *frames)
    restored = zone_1()
    await restored.async_restore_last_state(_saved(live))
    assert restored._nominal_is_saved()
    _feed(restored, *frames)
    assert restored.target_temperature == live.target_temperature
    assert not restored._nominal_is_saved()
    assert restored._silent_zone_reads() == live._silent_zone_reads()  # nothing asked that a zone not restarted would not ask


@pytest.mark.parametrize("knob_first", [True, False])
async def test_the_knob_of_a_zone_that_said_it_is_off_adds_no_offset_at_the_restore(hass: HomeAssistant, knob_first: bool) -> None:
    live = _zone(hass)
    _feed(live, *F461_POLL_REPLY)
    saved = _saved(live)  # heat, 23.5 in force, 21.0 commanded, knob 0
    zone = _zone(hass)
    _feed(zone, "*4*102*2##")
    if knob_first:
        _feed(zone, "*#4*2*13*02##")
    await zone.async_restore_last_state(saved)
    if not knob_first:
        _feed(zone, "*#4*2*13*02##")
    attributes = zone.extra_state_attributes
    assert zone.hvac_mode == HVACMode.OFF
    assert zone.target_temperature == 23.5  # what was saved: no frame said 25.5
    assert (attributes["nominal_temperature"], attributes["local_offset"], attributes["knob_pos"]) == (21.0, 2, "+2")


# ── what the audit of the review fixes left open ──


async def _zone_2_restored(hass: HomeAssistant, *, without: str | None = None) -> MyHOMEClimate:
    """Zone 2 saved while heating (23.5 in force, 21.0 commanded, knob 0), restored."""
    live = _zone(hass)
    _feed(live, *F461_POLL_REPLY)
    saved = _saved(live)
    if without is not None:
        saved = State(saved.entity_id, saved.state, {key: value for key, value in saved.attributes.items() if key != without})
    zone = _zone(hass)
    await zone.async_restore_last_state(saved)
    return zone


async def test_an_unchanged_knob_between_the_two_protection_frames_keeps_the_commanded_setpoint(hass: HomeAssistant) -> None:
    zone = await _zone_2_restored(hass)
    _feed(zone, "*#4*2*12*0070*3##", "*#4*2*13*00##", "*4*102*2##")  # the knob report of the same status burst
    assert zone.hvac_mode == HVACMode.OFF and zone.target_temperature == 7.0
    assert zone.extra_state_attributes["nominal_temperature"] == 21.0


async def test_a_knob_that_did_change_between_the_two_frames_is_a_new_setpoint(hass: HomeAssistant) -> None:
    zone = await _zone_2_restored(hass)
    _feed(zone, "*#4*2*12*0070*3##", "*#4*2*13*02##", "*4*102*2##")
    assert zone.hvac_mode == HVACMode.OFF
    assert zone.extra_state_attributes["local_offset"] == 2


@pytest.mark.parametrize("restore_first", [True, False])
async def test_a_commanded_setpoint_report_beside_the_restore_keeps_the_saved_knob(hass: HomeAssistant, restore_first: bool) -> None:
    live = _zone(hass)
    _feed(live, "*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*13*01##")
    saved = _saved(live)  # heat, 21.0 commanded, knob +1
    zone = _zone(hass)
    if restore_first:
        await zone.async_restore_last_state(saved)
    _feed(zone, "*4*1*2##", "*#4*2*14*0210*3##")  # the mode and the commanded setpoint, not the knob
    if not restore_first:
        await zone.async_restore_last_state(saved)
    attributes = zone.extra_state_attributes
    assert (zone.target_temperature, attributes["knob_pos"], attributes["local_offset"]) == (22.0, "+1", 1)
    assert attributes["nominal_temperature"] == 21.0


async def test_a_state_saved_without_its_commanded_setpoint_is_still_asked_for_it(hass: HomeAssistant) -> None:
    zone = await _zone_2_restored(hass, without="nominal_temperature")  # as an earlier version saved it
    _feed(zone, "*4*102*2##", "*#4*2*12*0070*3##")
    assert zone.target_temperature == 7.0
    assert "*#4*2*14##" in zone._silent_zone_reads()


async def test_a_setpoint_written_to_a_zone_restored_off_is_its_commanded_setpoint(hass: HomeAssistant) -> None:
    live = _zone(hass)
    _feed(live, *F461_POLL_REPLY, "*4*102*2##", "*#4*2*12*0070*3##")
    zone = _zone(hass)
    await zone.async_restore_last_state(_saved(live))  # off, 7.0 shown, 21.0 commanded
    await zone.async_set_temperature(temperature=22.0)
    assert _sent(zone) == ["*#4*2*#14*0220*3##"]
    _feed(zone, "*#4*2*12*0220*3##", "*4*1*2##")  # the zone takes it: in force, then running
    assert zone.hvac_mode == HVACMode.HEAT
    assert (zone.target_temperature, zone.extra_state_attributes["nominal_temperature"]) == (22.0, 22.0)


async def test_a_protection_setpoint_after_a_write_to_a_zone_restored_off_is_not_its_echo(hass: HomeAssistant) -> None:
    live = _zone(hass)
    _feed(live, *F461_POLL_REPLY, "*4*102*2##", "*#4*2*12*0070*3##")
    zone = _zone(hass)
    await zone.async_restore_last_state(_saved(live))
    for entity in (live, zone):  # the zone that did not restart shows the same
        await entity.async_set_temperature(temperature=22.0)
        _feed(entity, "*#4*2*12*0070*3##")  # 15.0 below what was written: no knob gives that
        assert entity.hvac_mode == HVACMode.OFF and entity.target_temperature == 7.0
        assert entity.extra_state_attributes["nominal_temperature"] == 21.0  # the write was not confirmed


async def test_a_mode_frame_during_the_silent_poll_does_not_drop_the_read_of_the_commanded_setpoint(hass: HomeAssistant) -> None:
    zone = await _zone_2_restored(hass)
    _feed(zone, "*#4*2*0*0239##", "*#4*2*12*0070*3##")  # the first frames of the zone's reply
    gateway = zone._gateway_handler
    gateway.silent_zone_poll_needed = True
    gateway.wait_for_zone_requests = AsyncMock()
    gateway.pace_startup_status_write = AsyncMock()

    async def reply(_request: object) -> asyncio.Future[bool]:
        if gateway.send_status_request.await_count == 1:
            _feed(zone, "*4*102*2##")  # the mode frame arrives while the first read is out
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        future.set_result(True)
        return future

    gateway.send_status_request = AsyncMock(side_effect=reply)
    await zone._poll_if_silent(grace=0)
    assert zone.extra_state_attributes["nominal_temperature"] == 21.0
    assert "*#4*2*14##" in [str(call.args[0]) for call in gateway.send_status_request.await_args_list]


@pytest.mark.parametrize("state", ["unknown", "unavailable"])
async def test_a_setpoint_saved_before_the_mode_was_known_is_not_a_commanded_setpoint(hass: HomeAssistant, state: str) -> None:
    live = _zone(hass)
    _feed(live, "*#4*2*12*0070*3##")  # what is in force, before the zone said its mode
    assert live.hvac_mode is None and "nominal_temperature" not in live.extra_state_attributes
    saved = State(live.entity_id, state, {"temperature": live.target_temperature, **live.extra_state_attributes})
    zone = _zone(hass)
    await zone.async_restore_last_state(saved)
    _feed(zone, "*4*102*2##")
    with pytest.raises(ServiceValidationError):
        await zone.async_set_hvac_mode(HVACMode.HEAT)  # no 7.0 written as the setpoint to heat to
    assert _sent(zone) == []


async def test_the_central_unit_behind_an_interface_does_not_set_the_mode_of_the_local_zones(hass: HomeAssistant) -> None:
    gateway = MagicMock(mac=MAC, is_connected=True, send=AsyncMock(), send_status_request=AsyncMock())
    (central,) = await _central_configured_as_zone_0(hass, gateway, interface="01", cool=True)
    local = _zone(hass, gateway=gateway, standalone=False)
    routed = _zone(hass, gateway=gateway, standalone=False, interface="01")
    _feed(local, "*4*1*2##")
    routed.handle_event(OWNHeatingEvent("*4*1*2#4#01##"))  # built from *4*1*2## and the interface suffix
    assert central_mode_signal(MAC, None) == f"myhome_central_mode_{MAC}"
    unsubscribe = [
        async_dispatcher_connect(hass, central_mode_signal(MAC, zone._interface), zone._handle_central_mode_update)
        for zone in (local, routed)
    ]
    try:
        # Built from *4*202*#0## and the interface suffix (tests/test_standalone_zone_season_2026_11_18.py:319): no capture.
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*4*202*#0#4#01##"))
        assert central.hvac_mode == HVACMode.COOL
        assert (local.hvac_mode, routed.hvac_mode) == (HVACMode.HEAT, HVACMode.COOL)
    finally:
        for stop in unsubscribe:
            stop()


def _knob_view(zone: MyHOMEClimate) -> tuple:
    attributes = zone.extra_state_attributes
    return (zone.hvac_mode, zone.target_temperature, attributes.get("nominal_temperature"), attributes.get("knob_pos"), attributes.get("local_offset"))


@pytest.mark.parametrize(
    ("frames", "view"),
    [
        # in force 22.0, read as the commanded setpoint until the saved knob says it is 21.0 + 1
        (("*4*1*2##", "*#4*2*12*0220*3##"), (HVACMode.HEAT, 22.0, 21.0, "+1", 1)),
        # off: the saved knob is kept, and no offset is shown
        (("*4*102*2##", "*#4*2*14*0210*3##"), (HVACMode.OFF, 21.0, 21.0, "+1", 1)),
        # the knob the zone reported itself stands
        (("*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*13*02##"), (HVACMode.HEAT, 23.0, 21.0, "+2", 2)),
    ],
)
async def test_the_saved_knob_beside_a_setpoint_reported_before_the_restore(hass: HomeAssistant, frames: tuple[str, ...], view: tuple) -> None:
    live = _zone(hass)
    _feed(live, "*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*13*01##")
    saved = _saved(live)  # heat, 21.0 commanded, knob +1
    before, after = _zone(hass), _zone(hass)
    _feed(before, *frames)
    await before.async_restore_last_state(saved)
    await after.async_restore_last_state(saved)
    _feed(after, *frames)
    assert _knob_view(before) == view
    if view[0] == HVACMode.OFF:
        # A zone that is off shows the saved setpoint in force (22.0) until its dimension 12, when
        # the saved state came first: the knob, the commanded setpoint and the mode are the same.
        assert (_knob_view(after)[0], *_knob_view(after)[2:]) == (view[0], *view[2:])
    else:
        assert _knob_view(after) == view  # the same state in both orders


async def test_a_saved_knob_that_is_not_a_number_is_no_knob_beside_a_reported_setpoint(hass: HomeAssistant) -> None:
    zone = _zone(hass)
    _feed(zone, "*4*1*2##", "*#4*2*14*0210*3##")
    await zone.async_restore_last_state(State(zone.entity_id, "heat", {"temperature": 22.0, "nominal_temperature": 21.0, "local_offset": "x"}))
    assert (zone.target_temperature, zone.extra_state_attributes["local_offset"]) == (21.0, 0)


async def test_a_setpoint_in_force_long_after_the_write_is_not_its_echo(hass: HomeAssistant) -> None:
    live = _zone(hass)
    _feed(live, *F461_POLL_REPLY, "*4*102*2##", "*#4*2*12*0070*3##")
    zone = _zone(hass)
    await zone.async_restore_last_state(_saved(live))
    await zone.async_set_temperature(temperature=22.0)
    written, at = zone._written_target_temperature
    zone._written_target_temperature = (written, at - 60)  # a minute ago
    _feed(zone, "*#4*2*12*0220*3##")
    assert zone.hvac_mode == HVACMode.OFF
    assert zone.extra_state_attributes["nominal_temperature"] == 21.0  # the saved one, until the zone runs or reports it
