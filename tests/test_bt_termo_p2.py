"""Tests for bt_termo P2 integration handling in MyHOME."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    MESSAGE_TYPE_LOCAL_OFFSET_RELEASE,
    MESSAGE_TYPE_PUMP_CALL,
    OWNEvent,
    OWNHeatingCommand,
    OWNHeatingEvent,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import (
    _MESSAGE_TYPE_LOCAL_OFFSET_RELEASE,
    _MESSAGE_TYPE_PUMP_CALL,
    MyHOMEClimate,
)
from custom_components.myhome.climate import async_setup_entry as climate_setup_entry
from custom_components.myhome.climate_discovery import (
    _MESSAGE_TYPE_LOCAL_OFFSET_RELEASE as DISCOVERY_MESSAGE_TYPE_LOCAL_OFFSET_RELEASE,
)
from custom_components.myhome.climate_discovery import (
    _MESSAGE_TYPE_PUMP_CALL as DISCOVERY_MESSAGE_TYPE_PUMP_CALL,
)
from custom_components.myhome.climate_discovery import (
    _zone_address,
)
from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, DOMAIN
from custom_components.myhome.gateway import _zone_request_frame
from tests.conftest import attach_runtime

MAC = "00:03:50:00:02:99"


def _make_zone(where: str = "2", *, model: str = "F461", central: bool = False) -> MyHOMEClimate:
    gw = MagicMock()
    gw.mac = MAC
    gw.model = model
    gw.log_id = f"[{model}]"
    gw.is_connected = True
    gw.send_command = AsyncMock()
    gw.send_status_request = AsyncMock()
    gw.request_zone_dimension = MagicMock()
    gw.zone_temperature_poll_needed = True
    gw.zone_actuator_request_refused = False
    gw.silent_zone_actuator_read_refused = False
    gw.interface_zone_request_refused = False
    gw.central_season = None
    gw.watch_zone_actuators = MagicMock(return_value=lambda: None)
    entity = MyHOMEClimate(
        hass=None,
        name=f"Zone {where}",
        device_id=f"4-{where}",
        who="4",
        where=where,
        manufacturer="BTicino",
        model="Thermostat",
        gateway=gw,
        heating=True,
        cooling=True,
        fan=False,
        standalone=not central,
        central=central,
    )
    entity.async_schedule_update_ha_state = MagicMock()
    return entity


def test_p2_constants_match_ownd() -> None:
    assert _MESSAGE_TYPE_PUMP_CALL == MESSAGE_TYPE_PUMP_CALL
    assert DISCOVERY_MESSAGE_TYPE_PUMP_CALL == MESSAGE_TYPE_PUMP_CALL
    assert _MESSAGE_TYPE_LOCAL_OFFSET_RELEASE == MESSAGE_TYPE_LOCAL_OFFSET_RELEASE
    assert DISCOVERY_MESSAGE_TYPE_LOCAL_OFFSET_RELEASE == MESSAGE_TYPE_LOCAL_OFFSET_RELEASE


def test_p2_13_pump_call_does_not_alter_climate_state() -> None:
    zone = _make_zone("2")
    zone.handle_event(OWNHeatingEvent("*#4*2*0*0215##"))
    zone.handle_event(OWNHeatingEvent("*4*1*2##"))
    zone.handle_event(OWNHeatingEvent("*#4*2*14*0220*3##"))
    zone.handle_event(OWNHeatingEvent("*#4*2#1*20*1##"))
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.hvac_action == HVACAction.HEATING
    assert zone.target_temperature == 22.0

    for frame in ("*4*4001#2*0#3##", "*4*4002#2*0#3##", "*4*4002*2##"):
        ev = OWNEvent.parse(frame)
        assert isinstance(ev, OWNHeatingEvent)
        zone.handle_event(ev)

    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.hvac_action == HVACAction.HEATING
    assert zone.target_temperature == 22.0


async def test_p2_13_typed_pump_calls_still_defer_thermo_switch_zones(hass: HomeAssistant) -> None:
    """Typed MESSAGE_TYPE_PUMP_CALL must still defer phantom climate creation on #785 switch zones."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="F461",
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: MAC,
            CONF_NAME: "F461",
        },
        options={
            CONF_PLATFORMS: {
                "switch": {
                    CONF_ENTITY: {
                        "pump_zone_valve": {"where": "7#1", "who": "4", "name": "Zone 7 Valve"},
                    }
                }
            }
        },
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    attach_runtime(hass, entry)

    added: list[MyHOMEClimate] = []
    await climate_setup_entry(hass, entry, added.extend)

    # Hold an actuator frame first (as in live #785 plants) to verify non-telemetry frames never confirm it
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*7#1*20*0##"))
    for frame in ("*4*4001#7*0#1##", "*4*4001*7##", "*4*4002#7*0#1##", "*4*4002*7##", "*4*40*7##"):
        ev = OWNEvent.parse(frame)
        assert isinstance(ev, OWNHeatingEvent)
        assert ev.message_type in (MESSAGE_TYPE_PUMP_CALL, MESSAGE_TYPE_LOCAL_OFFSET_RELEASE)
        async_dispatcher_send(hass, f"myhome_message_{MAC}", ev)
    await hass.async_block_till_done()
    assert added == []


def test_p2_13_zero_calling_zone_pump_call_has_no_discovery_address() -> None:
    ev = OWNEvent.parse("*4*4001#0*0#3##")
    assert isinstance(ev, OWNHeatingEvent)
    assert _zone_address(ev) is None


def test_p2_14_local_offset_release_refreshes_dim13_only_when_needed() -> None:
    zone = _make_zone("2")
    zone.handle_event(OWNHeatingEvent("*#4*2*13*02##"))
    assert zone._local_offset == 2
    assert zone._knob_pos == "+2"
    zone._gateway_handler.request_zone_dimension.reset_mock()

    # Inbound *4*40*2## refreshes DIM 13 when offset is non-zero
    release = OWNEvent.parse("*4*40*2##")
    assert isinstance(release, OWNHeatingEvent)
    zone.handle_event(release)
    zone._gateway_handler.request_zone_dimension.assert_called_once_with("2", 13)

    # Once DIM 13 reports normal 00, another *4*40*2## does not re-poll unnecessarily
    zone._gateway_handler.request_zone_dimension.reset_mock()
    zone.handle_event(OWNHeatingEvent("*#4*2*13*00##"))
    assert zone._local_offset == 0
    assert zone._knob_pos == "0"
    zone.handle_event(release)
    zone._gateway_handler.request_zone_dimension.assert_not_called()


def test_p2_14_central_unit_and_unified_four_zone_local_offset_release() -> None:
    central = _make_zone("#0", central=True)
    central._gateway_handler.request_zone_dimension.reset_mock()
    release_central = OWNEvent.parse("*4*40*#0##")
    assert isinstance(release_central, OWNHeatingEvent)
    central.handle_event(release_central)
    central._gateway_handler.request_zone_dimension.assert_not_called()

    unified = _make_zone("#0#1", central=True)
    unified._unified_master = True
    unified.handle_event(OWNHeatingEvent("*#4*1*13*02##"))
    assert unified._local_offset == 2
    unified._gateway_handler.request_zone_dimension.reset_mock()
    release_master = OWNEvent.parse("*4*40*1##")
    assert isinstance(release_master, OWNHeatingEvent)
    unified.handle_event(release_master)
    unified._gateway_handler.request_zone_dimension.assert_called_once_with("1", 13)


@pytest.mark.parametrize(
    ("where", "expected"),
    [
        ("01", "*#4*1*0##"),
        ("02", "*#4*2*0##"),
        ("09#4#01", "*#4*9#4#01*0##"),
        ("09#4#1", "*#4*9#4#01*0##"),
        ("2", "*#4*2*0##"),
        ("10", "*#4*10*0##"),
        ("0", "*#4*0*0##"),
    ],
)
def test_p2_15_zone_request_frame_dim0_strips_leading_zero(where: str, expected: str) -> None:
    assert _zone_request_frame(where, 0) == expected


def test_p2_15_silent_zone_reads_and_startup_poll_avoid_quirk05_on_zero_padded_yaml() -> None:
    """myhome.yaml ``zone: "02"`` must emit canonical zone ``2`` reads, never ``*#4*02*0##`` (QUIRK-05)."""
    zone = _make_zone("02")
    reads = zone._silent_zone_reads()
    assert reads[0] == "*#4*2*0##"
    assert all("*#4*02" not in frame for frame in reads)
    assert zone._request_where == "2"
    assert str(OWNHeatingCommand.get_temperature(zone._request_where)) == "*#4*2*0##"
