"""Tests for bt_termo P2 integration handling in MyHOME."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd import message as own_message
from OWNd.message import (
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
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.gateway import _zone_request_frame
from tests.conftest import attach_runtime

MAC = "00:03:50:00:02:99"
_HAS_OWND_P2 = hasattr(own_message, "MESSAGE_TYPE_PUMP_CALL") and hasattr(
    own_message, "MESSAGE_TYPE_LOCAL_OFFSET_RELEASE"
)


def _parse_typed(frame: str, expected_type: str) -> OWNHeatingEvent:
    """Parse a WHO=4 frame and verify its P2 message_type when OWNd P2 is installed.

    On PyPI OWNd 2.0.2 (before OWNd#190 is released), the parser leaves ``_type`` as
    ``None`` for P2 frames; populate ``_type`` so MyHOME's handlers are still exercised.
    """
    ev = OWNEvent.parse(frame)
    assert isinstance(ev, OWNHeatingEvent)
    if _HAS_OWND_P2:
        assert ev.message_type == expected_type
    else:
        ev._type = expected_type
    return ev


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
    assert _MESSAGE_TYPE_PUMP_CALL == "pump_call"
    assert DISCOVERY_MESSAGE_TYPE_PUMP_CALL == "pump_call"
    assert _MESSAGE_TYPE_LOCAL_OFFSET_RELEASE == "local_offset_release"
    assert DISCOVERY_MESSAGE_TYPE_LOCAL_OFFSET_RELEASE == "local_offset_release"
    ownd_pump_call = getattr(own_message, "MESSAGE_TYPE_PUMP_CALL", None)
    if ownd_pump_call is not None:
        assert _MESSAGE_TYPE_PUMP_CALL == ownd_pump_call
    ownd_release = getattr(own_message, "MESSAGE_TYPE_LOCAL_OFFSET_RELEASE", None)
    if ownd_release is not None:
        assert _MESSAGE_TYPE_LOCAL_OFFSET_RELEASE == ownd_release


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
        zone.handle_event(_parse_typed(frame, _MESSAGE_TYPE_PUMP_CALL))

    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.hvac_action == HVACAction.HEATING
    assert zone.target_temperature == 22.0


def _attach_primary_runtime(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    switch_entities: dict[str, dict[str, str]] | None = None,
) -> MagicMock:
    """Attach a primary (discovering) gateway MagicMock with climate platform enabled."""
    gw = MagicMock()
    gw.mac = MAC
    gw.model = "F461"
    gw.log_id = "[F461]"
    gw.is_follower = False
    gw.delegated_whos = set()
    gw.delegated_away_whos = set()
    gw.is_device_ignored = MagicMock(return_value=False)
    gw.read_zone_temperature = AsyncMock(return_value=False)
    gw.request_zone_dimension = MagicMock()
    gw.send_status_request = AsyncMock()
    gw.wait_for_initial_discovery = AsyncMock(return_value=False)
    gw.watch_zone_actuators = MagicMock(return_value=lambda: None)
    runtime = attach_runtime(hass, entry, gateway=gw)
    runtime.platforms["climate"] = {}
    if switch_entities:
        runtime.platforms["switch"] = dict(switch_entities)
    return gw


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
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    _attach_primary_runtime(
        hass,
        entry,
        switch_entities={"pump_zone_valve": {"where": "7#1", "who": "4", "name": "Zone 7 Valve"}},
    )

    added: list[MyHOMEClimate] = []
    await climate_setup_entry(hass, entry, added.extend)

    # Hold an actuator frame first (as in live #785 plants) to verify non-telemetry frames never confirm it
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*7#1*20*0##"))
    for frame, expected_type in (
        ("*4*4001#7*0#1##", _MESSAGE_TYPE_PUMP_CALL),
        ("*4*4001*7##", _MESSAGE_TYPE_PUMP_CALL),
        ("*4*4002#7*0#1##", _MESSAGE_TYPE_PUMP_CALL),
        ("*4*4002*7##", _MESSAGE_TYPE_PUMP_CALL),
        ("*4*40*7##", _MESSAGE_TYPE_LOCAL_OFFSET_RELEASE),
    ):
        ev = _parse_typed(frame, expected_type)
        async_dispatcher_send(hass, f"myhome_message_{MAC}", ev)
    await hass.async_block_till_done()
    assert added == []


def test_p2_13_zero_calling_zone_pump_call_has_no_discovery_address() -> None:
    for frame in ("*4*4001#0*0#3##", "*4*4001#105*0#3##", "*4*4002*#0#3##"):
        ev = _parse_typed(frame, _MESSAGE_TYPE_PUMP_CALL)
        assert _zone_address(ev) is None


async def test_p2_14_local_offset_release_never_promotes_unconfigured_held_zone(
    hass: HomeAssistant,
) -> None:
    """*4*40*Z## is a bus command echo and must not promote even a non-switch held actuator zone."""
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
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    _attach_primary_runtime(hass, entry)

    added: list[MyHOMEClimate] = []
    await climate_setup_entry(hass, entry, added.extend)

    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*9#1*20*0##"))
    async_dispatcher_send(
        hass,
        f"myhome_message_{MAC}",
        _parse_typed("*4*40*9##", _MESSAGE_TYPE_LOCAL_OFFSET_RELEASE),
    )
    await hass.async_block_till_done()
    assert added == []

    # Positive control: genuine telemetry for zone 9 DOES confirm the held actuator zone
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*9*0*0215##"))
    await hass.async_block_till_done()
    assert len(added) == 1
    assert added[0]._where == "9"


def test_p2_14_local_offset_release_refreshes_dim13_and_dim12_only_when_needed() -> None:
    zone = _make_zone("2")
    zone.handle_event(OWNHeatingEvent("*#4*2*13*02##"))
    assert zone._local_offset == 2
    assert zone._knob_pos == "+2"
    zone._gateway_handler.request_zone_dimension.reset_mock()

    # Inbound *4*40*2## refreshes both DIM 13 and DIM 12 when offset is non-zero
    release = _parse_typed("*4*40*2##", _MESSAGE_TYPE_LOCAL_OFFSET_RELEASE)
    frames_before = zone._poll_health.frames
    zone.handle_event(release)
    assert [c.args for c in zone._gateway_handler.request_zone_dimension.call_args_list] == [
        ("2", 13),
        ("2", 12),
    ]
    # Bus command echo must not count as thermostat telemetry in poll health
    assert zone._poll_health.frames == frames_before

    # Synthetic *4*40*#2## via central unit is ignored on a standalone zone
    zone._gateway_handler.request_zone_dimension.reset_mock()
    zone.handle_event(_parse_typed("*4*40*#2##", _MESSAGE_TYPE_LOCAL_OFFSET_RELEASE))
    zone._gateway_handler.request_zone_dimension.assert_not_called()

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
    release_central = _parse_typed("*4*40*#0##", _MESSAGE_TYPE_LOCAL_OFFSET_RELEASE)
    central.handle_event(release_central)
    central._gateway_handler.request_zone_dimension.assert_not_called()

    unified = _make_zone("#0#1", central=True)
    unified._unified_master = True
    unified.handle_event(OWNHeatingEvent("*4*1101*#0#1##"))
    unified.handle_event(OWNHeatingEvent("*#4*1*13*02##"))
    unified.handle_event(OWNHeatingEvent("*#4*1*12*0220*3##"))
    assert unified._local_offset == 2
    assert unified.target_temperature == 22.0
    unified._gateway_handler.request_zone_dimension.reset_mock()
    release_master = _parse_typed("*4*40*1##", _MESSAGE_TYPE_LOCAL_OFFSET_RELEASE)
    unified.handle_event(release_master)
    assert [c.args for c in unified._gateway_handler.request_zone_dimension.call_args_list] == [
        ("1", 13),
        ("1", 12),
    ]
    # Replaying both requested reads resets local offset AND target_temperature cleanly
    unified.handle_event(OWNHeatingEvent("*#4*1*13*00##"))
    unified.handle_event(OWNHeatingEvent("*#4*1*12*0200*3##"))
    assert unified._local_offset == 0
    assert unified.target_temperature == 20.0


@pytest.mark.asyncio
async def test_p2_14_restored_plain_zone_local_offset_release_preserves_nominal() -> None:
    """Restored plain zone with offset +2 + DIM 12 push recovers nominal 20.0 on WHAT 40."""
    from homeassistant.core import State

    zone = _make_zone("2", model="F455")
    zone._gateway_handler.supports_zone_status = False
    await zone.async_restore_last_state(
        State(
            "climate.zone_2",
            "heat",
            {
                "temperature": 22.0,
                "nominal_temperature": 20.0,
                "local_offset": 2,
                "knob_pos": "+2",
            },
        )
    )
    zone.handle_event(OWNHeatingEvent("*#4*2*12*0220*3##"))
    zone._gateway_handler.request_zone_dimension.reset_mock()
    zone.handle_event(_parse_typed("*4*40*2##", _MESSAGE_TYPE_LOCAL_OFFSET_RELEASE))
    assert [c.args for c in zone._gateway_handler.request_zone_dimension.call_args_list] == [
        ("2", 13),
        ("2", 12),
    ]
    zone.handle_event(OWNHeatingEvent("*#4*2*13*00##"))
    zone.handle_event(OWNHeatingEvent("*#4*2*12*0200*3##"))
    assert zone._local_offset == 0
    assert zone.target_temperature == 20.0
    assert zone._target_temperature == 20.0


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
