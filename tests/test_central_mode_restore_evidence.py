"""Central unit: which reports prove the state the season / program pair is built on (a saved state, an
echo of our own command, a frame of another address and an unconfirmed `off` do not).
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.climate.const import HVACMode
from homeassistant.core import State
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from OWNd.message import OWNEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import async_setup_entry
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.device_health import FaultKind, fault_issue_id
from tests.conftest import attach_runtime
from tests.test_crew_central_season_restore import (
    ANTIFREEZE,
    COOL,
    FLAGS,
    HEAT,
    HEATING_PROGRAM,
    RESTORE,
    THERMAL_PROTECTION,
    _climate,
    _gateway,
    _replay,
    _sent,
)
from tests.test_crew_central_season_restore import (
    _entity_base_init as _entity_base_init,
)
from tests.test_device_health import ENTRY_ID, _frame, _health, _issue
from tests.test_issue_944_actuator_answer_not_central import _plant, _receive
from tests.test_issue_944_actuator_answer_not_central import _sent as _plant_sent


async def test_off_without_a_report_is_not_proof_for_restore():
    gw = _gateway()
    central = _climate(gw, "#0", standalone=None, central=True)
    await central.async_set_hvac_mode(HVACMode.OFF)
    gw.send.reset_mock()
    # No response to off, and no other report since startup (acceptance 4).
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == [HEAT]


async def test_optimistic_off_restore():
    gw = _gateway()
    cu = _climate(gw, "#0", standalone=None, central=True)
    # Last physical state: manual heating (tests/fixtures/traces/issue_466/README.md:30).
    cu.handle_event(OWNMessage.parse("*4*110#0180*#0##"))
    failed = asyncio.get_running_loop().create_future()
    failed.cancel()  # off never written (gateway_sessions.py:1540-1547)
    gw.send.return_value = failed
    await cu.async_set_hvac_mode(HVACMode.OFF)
    await asyncio.sleep(0)
    gw.send.reset_mock()
    gw.send.return_value = None
    await cu.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == [HEAT], (
        "an optimistic off replaced a reported manual heating setpoint by the program"
    )


async def test_shared_bus_seasonless_frame_does_not_validate_saved_season():
    gw = _gateway()
    c = _climate(gw, "#0", central=True, standalone=None)
    await c.async_restore_last_state(State(c.entity_id, "cool", {"season": "conditioning"}))
    # F455 sees another gateway's command. 3550 did not answer it in the capture.
    # tests/fixtures/traces/central_restore_3100_live_2026-11-12/README.md:84-87
    _replay(c, ["*4*311*#0##"])
    assert not c._season_reported
    await c.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == [HEAT]


async def test_saved_protection_season_only():
    gw = _gateway()
    cu = _climate(gw, "#0", standalone=None, central=True)
    await cu.async_restore_last_state(
        State(cu.entity_id, "heat", {"central_operation": "antifreeze"})
    )
    # A season selector, e.g. from another client; not an operating-mode report.
    cu.handle_event(OWNMessage.parse(HEAT))
    await cu.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == [HEAT], (
        "season confirmed, but the only protection information came from storage"
    )


async def test_other_controller_season_command_cannot_release_reported_off():
    gw = _gateway()
    c = _climate(gw, "#0", standalone=None, central=True)
    # Monitor sequence in tests/fixtures/traces/central_restore_3100_live_2026-11-12/README.md:82-89:
    # another client's season selection appears on the monitoring gateway, but read-back is still OFF.
    _replay(c, ["*4*203*#0##", COOL])
    await c.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(gw) == [COOL, RESTORE]


async def test_each_echo_object_stays_recognized_on_second_delivery():
    central = _climate(_gateway(), "#0", standalone=None, central=True)
    _replay(central, [ANTIFREEZE])
    await central.async_set_hvac_mode(HVACMode.COOL)
    season_echo = OWNEvent.parse(COOL)
    central.handle_event(season_echo)
    central.handle_event(OWNEvent.parse(RESTORE))
    _replay(central, [HEATING_PROGRAM])  # intervening report
    with patch(
        "custom_components.myhome.climate.time.monotonic", return_value=time.monotonic() + 1
    ):
        central.handle_event(season_echo)
    assert central.hvac_mode == HVACMode.HEAT


async def test_same_season_echo_object_stays_an_echo_after_second_slot_consumed():
    c = _climate(_gateway(), "#0", central=True, standalone=None)
    _replay(c, [THERMAL_PROTECTION])
    await c.async_set_hvac_mode(HVACMode.HEAT)
    season_echo = OWNEvent.parse(HEAT)
    c.handle_event(season_echo)
    c.handle_event(OWNEvent.parse(RESTORE))
    assert c.hvac_mode == HVACMode.COOL
    with patch(
        "custom_components.myhome.climate.time.monotonic", return_value=time.monotonic() + 1
    ):
        c.handle_event(
            season_echo
        )  # duplicate dispatch of same object, not a later identical report
    assert c.hvac_mode == HVACMode.COOL


async def test_discovered_before_storage_restored():
    gw = _gateway()
    central = _climate(gw, "#0", standalone=None, central=True)
    central.entity_id = None
    central.platform = None
    _replay(central, [THERMAL_PROTECTION])
    central.entity_id = "climate.test"
    await central.async_restore_last_state(State("climate.test", "heat", {"season": "heating"}))
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == [HEAT, RESTORE]


async def test_discovery_before_restore(hass):
    mac = "<mac>"
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": mac}, unique_id=mac)
    entry.add_to_hass(hass)
    gw = MagicMock()
    gw.mac = mac
    gw.is_connected = True
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    runtime = attach_runtime(hass, entry, mac, gw)
    runtime.platforms["climate"] = {}
    entities = []
    await async_setup_entry(hass, entry, entities.extend)
    # The message router delivers to newly created entities before HA adds them.
    async_dispatcher_send(
        hass, f"myhome_message_{mac}", OWNMessage.parse("*4*102*#0##")
    )  # climate_mode_2026_11_08/frames.json:103
    (cu,) = entities
    assert cu.hvac_mode == HVACMode.HEAT
    cu.entity_id = "climate.test"
    cu.hass = hass
    cu.platform = MagicMock()
    cu.async_get_last_state = AsyncMock(
        return_value=State(cu.entity_id, "heat", {"season": "heating"})
    )
    await cu.async_added_to_hass()
    await cu.async_set_hvac_mode(HVACMode.HEAT)
    assert [str(call.args[0]) for call in gw.send.await_args_list] == [
        "*4*1*#0##",
        "*4*3100*#0##",
    ]  # tests/test_crew_central_season_restore.py:15-17


async def test_plant_has_newer_live_season_from_central_about_its_zone(hass):
    handler, zones = await _plant(hass)
    await _receive(hass, handler, [HEATING_PROGRAM])
    central = next(c for c in zones if c._central)
    # Central now reports zone 4 in manual conditioning. No new #0 burst:
    # tests/fixtures/traces/climate_mode_2026_11_08/frames.json (f461 zone 4).
    await _receive(hass, handler, ["*4*210*#4##"])
    assert handler.central_season == "conditioning"
    handler.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _plant_sent(handler) == [HEAT, RESTORE]


async def test_last_confirmed_manual_season_survives_a_local_echo(hass):
    handler, entities = await _plant(hass)
    # Composed scenario, no central answer after COOL; real monitor echo format in
    # tests/fixtures/traces/central_restore_3100_live_2026-11-12/README.md:88.
    await _receive(
        hass, handler, ["*4*110#0180*#0##"]
    )  # tests/fixtures/traces/issue_466/README.md:30
    central = next(c for c in entities if c._central)
    await central.async_set_hvac_mode(HVACMode.COOL)
    await _receive(hass, handler, [COOL])
    assert central.hvac_mode == HVACMode.HEAT
    handler.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _plant_sent(handler) == [HEAT]


async def test_first_new_season_after_reconnect_supersedes_old_heating_report(hass):
    handler, zones = await _plant(hass)
    handler._on_event_connection_state_change(True)
    await _receive(hass, handler, [HEATING_PROGRAM])
    central = next(z for z in zones if z._central)
    handler._on_event_connection_state_change(False)
    handler._on_event_connection_state_change(True)
    # First report in the new session: a central-controlled zone, manual cooling.
    # tests/test_issue_944_actuator_answer_not_central.py:107
    await _receive(hass, handler, ["*4*210*#4##"])
    assert handler.central_season == "conditioning"
    handler.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.COOL)
    assert _plant_sent(handler) == [COOL]


async def test_general_probe_off_does_not_replace_manual_setpoint(hass):
    """A general-probes operation is not an OFF report from the 3550."""
    handler, zones = await _plant(hass)
    await _receive(
        hass, handler, ["*4*110#0180*#0##"]
    )  # tests/fixtures/traces/issue_466/README.md:30
    central = next(c for c in zones if c._central)
    # Composed routing case, not a capture. General OFF verbatim from tests/test_issue_256_257.py:235-236.
    await _receive(hass, handler, ["*4*303*0##"])
    handler.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _plant_sent(handler) == [HEAT]


async def test_active_holiday_is_not_an_old_protection_state():
    """The published vacation status, unlike a generic command echo, proves a mode change."""
    g = _gateway()
    c = _climate(g, "#0", central=True, standalone=None)
    # The example of the WHO 4 PDF (1.6.18, N days holiday mode), not a plant capture: the command
    # carries the program to return to (`*4*33002#3103*#0##`), the frame on the monitor does not.
    _replay(c, [ANTIFREEZE, "*4*33003*#0##"])
    assert c.hvac_mode == HVACMode.AUTO and c.extra_state_attributes.get("holiday_days") == 3
    await c.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(g) == [HEAT]


async def test_central_alias_general_off():
    central = _climate(_gateway(), "0", central=True, standalone=None)
    _replay(central, [HEATING_PROGRAM])
    assert central.hvac_mode == HVACMode.HEAT
    _replay(central, ["*4*303*0##"])  # tests/test_climate_central_discovery.py:1202
    assert central.hvac_mode == HVACMode.HEAT


async def test_four_zone_signal_bleed(hass):
    gw = _gateway()
    central = _climate(gw, "#0#1", standalone=None, central=True)
    central.hass = hass
    unrelated = _climate(gw, "3", standalone=False)
    _replay(unrelated, ["*4*110*#3##"])  # tests/test_climate_zone_operation.py:82
    assert unrelated.hvac_mode == HVACMode.HEAT
    # Registration from climate.py:1588-1595
    disconnect = async_dispatcher_connect(
        hass, f"myhome_central_mode_{gw.mac}", unrelated._handle_central_mode_update
    )
    try:
        _replay(central, ["*4*103*#0#1##"])  # tests/test_climate_central_discovery.py:1203
        assert central.hvac_mode == HVACMode.OFF
        assert unrelated.hvac_mode == HVACMode.HEAT
    finally:
        disconnect()


async def test_preadd_battery_ignore_deleted(hass):
    health = _health(hass)
    issue = fault_issue_id(ENTRY_ID, FaultKind.CENTRAL_BATTERY_FAULT, 4, "#0")
    for frame in FLAGS:
        health.observe(_frame(frame))
    ir.async_get(hass).async_ignore(DOMAIN, issue, True)
    health.clear_all(keep_ignored=True)
    gw = _gateway()
    gw.device_health = _health(hass)
    central = _climate(gw, "#0", standalone=None, central=True)
    central.platform = None  # first status burst seeds the entity before HA adds it
    for frame in FLAGS:
        event = _frame(frame)
        gw.device_health.observe(event)
        central.handle_event(event)
    assert _issue(hass, issue) is not None and _issue(hass, issue).dismissed_version is not None
    gw.device_health.clear_all()
