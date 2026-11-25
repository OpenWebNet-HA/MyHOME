"""Five points of the review of the WHO 4 changes: thermo switch, ``myhome.yaml`` keys, probe latch, in-flight check.

- A dimension 20 frame whose WHERE is the bare zone names no actuator. OWNd reports actuator 1
  for it, and the whole-zone switch took that as a proven actuator: its next writes went to
  ``Z#1`` only. No capture holds such a frame from a gateway; the write is the one the switch
  itself sends while no actuator is known (``test_issue_785_thermo_actuator_switch.py``).
- ``central: true`` with ``zone: "0"`` gave ``#0#0`` and with ``zone: "#0#1"`` gave ``#0##0#1``:
  addresses no central unit has.
- A zone written ``#1`` or ``01`` beside a 4-zone central unit (``zone: "1"``, ``central: true``)
  lost its number to the central unit's entry, also in the copy of the platforms an earlier
  version saved in the entry options.
- The latch of a refused probe read belongs to the gateway that served the read (a delegated
  secondary, the warm standby), not to the primary.
- A command handed to another gateway is in flight in that gateway's command pool.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from OWNd.exceptions import OWNNackError
from OWNd.message import OWNHeatingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import async_setup_entry as climate_setup_entry
from custom_components.myhome.climate_discovery import _zone_config
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.discovery import Address
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.legacy_yaml import (
    CONF_MIGRATED_YAML_PLATFORMS,
    _apply_imported_copy,
    _apply_yaml_platforms,
    _snapshot,
    canonicalise_imported_platforms,
)
from custom_components.myhome.switch import MyHOMEThermoSwitch
from custom_components.myhome.typing_compat import device_info_to_dict
from custom_components.myhome.validate import climate_schema
from custom_components.myhome.where_grammar import (
    thermo_dim20_whole_zone_target,
    thermo_event_interface,
)
from tests import test_crew_central_season_restore
from tests.conftest import attach_runtime
from tests.test_crew_central_season_restore import HEAT as CENTRAL_HEAT
from tests.test_crew_central_season_restore import _climate as _central_climate
from tests.test_f455_zone_polling import _handler as _f455_handler
from tests.test_issue_785_thermo_actuator_switch import _mock_gateway
from tests.test_probe_fallback_read import (
    READ,
    _answers,
    _command_session,
    _refused,
    _sensor,
    _sent,
    _settle,
)

DISCOVERY_MAC = "00:03:50:00:09:83"
_entity_base_init = test_crew_central_season_restore._entity_base_init

# ── thermo switch: a bare-zone dimension 20 frame ──


def _zone_switch(hass: HomeAssistant, gateway: MagicMock) -> MyHOMEThermoSwitch:
    switch = MyHOMEThermoSwitch(
        hass=hass, name="Radiators", entity_name=None, icon=None, icon_on=None, device_id="4-3",
        where="3", zone="3", actuator=None, interface=None, device_class=None, manufacturer="BTicino",
        model="F430", turn_on_command=None, turn_off_command=None, gateway=gateway,
    )
    switch.hass = hass
    switch.entity_id = "switch.radiators"
    switch.async_write_ha_state = MagicMock()
    return switch


def _written(gateway: MagicMock) -> list[str]:
    return [str(call.args[0]) for call in gateway.send.await_args_list]


@pytest.mark.parametrize(
    "frames",
    [
        ["*#4*3*#20*1##"],  # the switch's own whole-zone write, seen again on the event session
        ["*#4*3*20*1##"],  # the same address as a status frame
        ["*#4*3#0*20*1##", "*#4*3*#20*1##"],  # after the all-actuators report of the zone
    ],
)
async def test_a_bare_zone_actuator_frame_proves_no_actuator_number(hass: HomeAssistant, frames: list[str]) -> None:
    gw = _mock_gateway()
    zone = _zone_switch(hass, gw)
    for frame in frames:
        zone.handle_event(OWNMessage.parse(frame))
    assert zone.is_on is True
    await zone.async_turn_off()
    assert _written(gw) == ["*#4*3*#20*0##"]  # still the whole zone: the relay may be 3#2

    # the actuator that does report is the one written from then on
    gw.send.reset_mock()
    zone.handle_event(OWNHeatingEvent("*#4*3#2*20*1##"))
    await zone.async_turn_off()
    assert _written(gw) == ["*#4*3#2*#20*0##"]


# ── myhome.yaml: the central unit's address ──


@pytest.mark.parametrize(
    ("zone", "where"),
    [("0", "#0"), ("#0", "#0"), ("#0#0", "#0"), ("#0#1", "#0#1"), ("1", "#0#1")],
)
def test_central_true_gives_an_address_a_central_unit_has(zone: str, where: str) -> None:
    validated = climate_schema({"c": {"zone": zone, "central": True}})
    assert validated["c"]["zone"] == where
    assert validated[where] is validated["c"]
    assert validated["c"]["name"] == "Central unit"


# ── myhome.yaml: a zone beside the 4-zone central unit that has its number ──

ZONE = {"name": "Zona 1"}
CENTRAL = {"zone": "1", "central": True, "name": "Centrale 4695"}


def _assert_zone_one_is_the_zones(climate: dict) -> None:
    for key in ("1", "zone_1", "4-1"):
        assert climate[key]["name"] == "Zona 1", key
    assert climate["#0#1"]["name"] == "Centrale 4695"
    assert _zone_config(climate, Address("1"), "1")["name"] == "Zona 1"
    assert _zone_config(climate, Address("#0#1"), "#0#1")["name"] == "Centrale 4695"


@pytest.mark.parametrize("zone", ["#1", "01"])
@pytest.mark.parametrize("order", [("zona1", "centrale"), ("centrale", "zona1")])
def test_a_zone_written_with_a_hash_or_a_leading_zero_keeps_its_number(order: tuple[str, str], zone: str) -> None:
    raw = {"zona1": {"zone": zone, **ZONE}, "centrale": dict(CENTRAL)}
    validated = climate_schema({key: raw[key] for key in order})
    _assert_zone_one_is_the_zones(validated)
    configured: dict[str, dict] = {"climate": {}}
    _apply_yaml_platforms({"climate": validated}, configured)
    _assert_zone_one_is_the_zones(configured["climate"])


# The keys of the two entries as version 2.0.0 filed them (Z: the zone's entry, C: the central
# unit's), for the zone spelling and the order of the entries in the file. The copy saved in the
# entry options keeps this order.
SAVED_BY_2_0_0 = [
    ("#1", [("4-#1", "Z"), ("zona1", "Z"), ("1", "C"), ("#1", "Z"), ("zone_1", "C"), ("4-1", "C"), ("centrale", "C"), ("#0#1", "C")]),
    ("01", [("4-01", "Z"), ("zona1", "Z"), ("01", "Z"), ("zone_01", "Z"), ("4-1", "C"), ("centrale", "C"), ("1", "C"), ("#0#1", "C"), ("zone_1", "C")]),
    ("01", [("4-1", "C"), ("centrale", "C"), ("1", "C"), ("#0#1", "C"), ("zone_1", "C"), ("4-01", "Z"), ("zona1", "Z"), ("01", "Z"), ("zone_01", "Z")]),
    ("1", [("4-1", "C"), ("zona1", "Z"), ("1", "C"), ("zone_1", "C"), ("centrale", "C"), ("#0#1", "C")]),
]


@pytest.mark.parametrize(("zone", "keys"), SAVED_BY_2_0_0)
def test_a_saved_copy_where_the_central_unit_holds_the_zones_number_gives_it_back(zone: str, keys: list[tuple[str, str]]) -> None:
    entry = {
        "Z": climate_schema({"zona1": {"zone": zone, **ZONE}})["zona1"],
        "C": climate_schema({"centrale": dict(CENTRAL)})["centrale"],
    }
    saved = _snapshot({"climate": {key: entry[which] for key, which in keys}})
    configured: dict[str, dict] = {"climate": {}}
    _apply_yaml_platforms(saved, configured)
    _assert_zone_one_is_the_zones(configured["climate"])


# ── temperature probe: the latch of a refused read ──


@pytest.fixture
def gateway() -> MagicMock:
    gw = MagicMock()
    gw.mac = "00:03:50:00:11:06"
    gw.log_id = "[primary]"
    gw.device_registry_id = None
    gw.is_connected = True
    gw.event_connections = 3
    gw._who4_target = MagicMock(return_value=gw)
    gw.interface_zone_request_refused = False
    return gw


def _standby() -> MagicMock:
    standby = MagicMock()
    standby.log_id = "[standby]"
    standby.is_connected = True
    standby.event_connections = 1
    return standby


async def test_a_probe_read_refused_by_the_standby_is_not_sent_every_five_minutes(hass: HomeAssistant, gateway: MagicMock) -> None:
    gateway.is_connected = False  # the primary is down, the warm standby serves WHO 4
    gateway._who4_target = MagicMock(return_value=_standby())
    gateway.send_status_request = AsyncMock(return_value=_refused(hass))
    probe = _sensor(hass, gateway)
    await probe.async_update()
    await _settle()
    assert _sent(gateway) == [READ] and probe._fallback_read_refused is True
    await probe.async_update()
    await _settle()
    assert _sent(gateway) == [READ]


async def test_a_probe_the_primary_refused_is_asked_again_through_the_gateway_that_takes_over(hass: HomeAssistant, gateway: MagicMock) -> None:
    gateway.send_status_request = AsyncMock(return_value=_refused(hass))
    probe = _sensor(hass, gateway)
    await probe.async_update()
    await _settle()
    await probe.async_update()
    await _settle()
    assert _sent(gateway) == [READ] and probe._fallback_read_refused is True

    gateway.is_connected = False  # no new event session of the primary: its count stays
    gateway._who4_target = MagicMock(return_value=_standby())
    await probe.async_update()
    await _settle()
    assert _sent(gateway) == [READ, READ]
    assert probe._fallback_read_refused is True  # the standby refused it too
    await probe.async_update()
    await _settle()
    assert _sent(gateway) == [READ, READ]

    gateway.is_connected = True  # the primary is back and serves the read again
    gateway._who4_target = MagicMock(return_value=gateway)
    await probe.async_update()
    await _settle()
    assert _sent(gateway) == [READ, READ, READ]


async def test_a_zone_sensor_behind_an_interface_is_not_read_where_the_gateway_does_not_route_who_4(hass: HomeAssistant, gateway: MagicMock) -> None:
    gateway.send_status_request = AsyncMock()
    gateway.interface_zone_request_refused = True
    zone = _sensor(hass, gateway, where="11#4#01")
    await zone.async_update()
    assert _sent(gateway) == []
    gateway.interface_zone_request_refused = False
    await zone.async_update()
    assert len(_sent(gateway)) == 1


# ── gateway: a command handed to another gateway ──


def _handler_with_peers() -> tuple[MagicMock, MagicMock, MagicMock]:
    handler = MagicMock(spec=MyHOMEGatewayHandler)
    handler._command_pool = MagicMock()
    handler._command_pool._in_flight_messages = []
    handler.delegated_away_whos = set()
    handler.is_connected = True
    handler._get_secondary_for_who.return_value = None
    handler._get_standby_gateway.return_value = None
    peers = []
    for _ in range(2):
        peer = MagicMock()
        peer.is_connected = True
        peer._profile_supports_who.return_value = True
        peer._command_pool._in_flight_messages = []
        peers.append(peer)
    return handler, peers[0], peers[1]


def test_a_command_being_written_by_the_gateway_it_was_handed_to_is_in_flight() -> None:
    frame = "*4*303*#0##"  # tests/test_central_season_pair_delivery.py
    command = OWNMessage.parse(frame)
    handler, secondary, standby = _handler_with_peers()
    in_flight = MyHOMEGatewayHandler.command_in_flight

    assert in_flight(handler, command) is False
    handler._command_pool._in_flight_messages = [command]
    assert in_flight(handler, command) is True
    handler._command_pool._in_flight_messages = []

    # WHO 4 is delegated to a secondary gateway: ``send`` queued the command there
    handler.delegated_away_whos = {4}
    handler._get_secondary_for_who.return_value = secondary
    assert in_flight(handler, command) is False
    secondary._command_pool._in_flight_messages = [command]
    assert in_flight(handler, command) is True
    assert in_flight(handler, OWNMessage.parse(frame)) is False  # another command with the same text

    # the primary was down when the command was sent: the warm standby is writing it
    handler.delegated_away_whos = set()
    handler._get_secondary_for_who.return_value = None
    handler._get_standby_gateway.return_value = standby
    handler.is_connected = False
    standby._command_pool._in_flight_messages = [command]
    assert in_flight(handler, command) is True
    handler.is_connected = True  # ... and stays in flight there when the primary reconnects meanwhile
    assert in_flight(handler, command) is True
    standby._command_pool._in_flight_messages = []
    assert in_flight(handler, command) is False


# ── what the review of these changes found ──


def test_a_central_unit_where_is_not_the_whole_zone_for_a_thermo_switch(hass: HomeAssistant) -> None:
    relay = MyHOMEThermoSwitch(
        hass=hass, name="Relay", entity_name=None, icon=None, icon_on=None, device_id="4-1#2",
        where="1#2", zone="1", actuator="2", interface=None, device_class=None, manufacturer="BTicino",
        model="F430", turn_on_command=None, turn_off_command=None, gateway=_mock_gateway(),
    )
    # A parser input (tests/test_issue_944_actuator_answer_not_central.py), in no capture: OWNd
    # gives zone 1 for it, and it names neither the relay nor the whole zone.
    for frame in ("*#4*#0#1*20*1##", "*#4*#1*20*1##"):
        relay.handle_event(OWNMessage.parse(frame))
        assert relay.is_on is None
    relay.handle_event(OWNMessage.parse("*#4*1#0*20*1##"))  # all the actuators of zone 1 (WHO 4 PDF, 1.5.8)
    assert relay.is_on is True


def test_the_probe_group_of_a_zone_does_not_take_the_keys_of_the_zone() -> None:
    # ``1`` is the master probe of zone 1, ``001`` all the probes of zone 1 (ownkb:chunk:r000569).
    raw = {"probes": {"name": "Probe group", "zone": "001"}, "master": {"name": "Master", "zone": "01"}, "central": dict(CENTRAL)}
    validated = climate_schema(raw)
    configured: dict[str, dict] = {"climate": {}}
    _apply_yaml_platforms({"climate": validated}, configured)
    for climate in (validated, configured["climate"]):
        assert climate["001"]["name"] == "Probe group"
        assert climate["1"]["name"] == "Master" and climate["zone_1"]["name"] == "Master"
        assert _zone_config(climate, Address("1"), "1")["name"] == "Master"


async def test_the_primarys_own_availability_does_not_end_a_refusal_of_the_secondary(hass: HomeAssistant, gateway: MagicMock) -> None:
    secondary = _standby()
    gateway._who4_target.return_value = secondary
    gateway.send_status_request = AsyncMock(return_value=_refused(hass))
    probe = _sensor(hass, gateway)
    probe.async_write_ha_state = MagicMock()
    await probe.async_update()
    await _settle()
    assert probe._fallback_refused_by is secondary
    for connected in (False, True):  # the primary goes and comes back; the secondary's session is the same
        gateway.is_connected = connected
        probe._handle_availability_update()
        await probe.async_update()
        await _settle()
    assert _sent(gateway) == [READ]
    secondary.event_connections += 1  # the secondary's own reconnect is a new gateway session
    probe._handle_availability_update()
    assert probe._fallback_read_refused is False


async def test_a_late_refusal_of_the_gateway_that_no_longer_serves_the_read_changes_nothing(hass: HomeAssistant, gateway: MagicMock) -> None:
    standby = _standby()
    late = hass.loop.create_future()
    gateway.send_status_request = AsyncMock(side_effect=[late, _refused(hass), _refused(hass)])
    gateway._who4_target.return_value = standby
    gateway.is_connected = False
    probe = _sensor(hass, gateway)
    await probe.async_update()  # served by the standby, not answered yet
    gateway.is_connected = True
    gateway._who4_target.return_value = gateway
    await probe.async_update()  # the primary is back and refuses it
    await _settle()
    assert probe._fallback_refused_by is gateway
    late._gateway_nack = True
    late.cancel()
    await _settle()
    assert probe._fallback_refused_by is gateway
    await probe.async_update()
    assert _sent(gateway) == [READ, READ]


async def test_no_gateway_left_to_serve_the_read_is_not_a_takeover(hass: HomeAssistant, gateway: MagicMock) -> None:
    standby = _standby()
    gateway.is_connected = False
    gateway._get_standby_gateway.return_value = standby
    gateway._delegated_target.return_value = None
    gateway._failover_target.side_effect = lambda message: MyHOMEGatewayHandler._failover_target(gateway, message)
    gateway._who4_target.side_effect = lambda: MyHOMEGatewayHandler._who4_target(gateway)
    gateway.send_status_request = AsyncMock(return_value=_refused(hass))
    probe = _sensor(hass, gateway)
    await probe.async_update()
    await _settle()
    assert probe._fallback_refused_by is standby
    standby.is_connected = False  # the standby is gone too: the target falls back to the offline primary
    for _ in range(2):
        await probe.async_update()
        await _settle()
    assert _sent(gateway) == [READ]
    standby.is_connected = True  # back on a new session: asked once
    standby.event_connections += 1
    await probe.async_update()
    await _settle()
    assert _sent(gateway) == [READ, READ]


async def test_a_timeout_of_the_connection_that_was_lost_does_not_latch_the_new_one(hass: HomeAssistant) -> None:
    primary = _f455_handler(hass, "F455")
    standby = _f455_handler(hass, "F461")
    primary._get_standby_gateway = lambda: standby
    standby._on_event_connection_state_change(True)
    probe = _sensor(hass, primary)
    pending = hass.loop.create_future()
    standby.send_status_request = AsyncMock(return_value=pending)
    await probe.async_update()
    assert _sent(standby) == [READ]
    standby._on_event_connection_state_change(False)
    pending._command_timeout = True
    pending.cancel()  # the sender gives up while the standby is disconnected
    standby._on_event_connection_state_change(True)  # connected again before the callback runs
    await _settle()
    assert probe._fallback_read_refused is False
    await probe.async_update()
    assert _sent(standby) == [READ, READ]


@pytest.mark.usefixtures("_entity_base_init")
async def test_a_command_the_secondary_is_writing_is_not_withdrawn_when_the_delegation_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler, secondary, _standby_gateway = _handler_with_peers()
    handler.delegated_away_whos = {4}
    handler._get_secondary_for_who.return_value = secondary
    handler._delegated_target.return_value = secondary
    handler.command_in_flight.side_effect = lambda command: MyHOMEGatewayHandler.command_in_flight(handler, command)
    pending: asyncio.Future[float] = asyncio.get_running_loop().create_future()

    async def send(command: OWNMessage) -> asyncio.Future[float]:
        return await MyHOMEGatewayHandler.send(handler, command)

    handler.send = AsyncMock(side_effect=send)

    async def handed_over(command: object) -> asyncio.Future[float]:
        secondary._command_pool._in_flight_messages.append(command)
        return pending

    secondary.send = AsyncMock(side_effect=handed_over)
    central = _central_climate(handler, "#0", standalone=None, central=True)
    monkeypatch.setattr("custom_components.myhome.climate.SETPOINT_DELIVERY_TIMEOUT", 0.01)
    call = asyncio.create_task(central._send_central_command(OWNMessage.parse(CENTRAL_HEAT), "_central_echo", written_required=True))
    await asyncio.sleep(0)
    assert secondary._command_pool._in_flight_messages
    # The options change (WHO 4 no longer delegated) before the entry is reloaded.
    handler.delegated_away_whos = set()
    handler._get_secondary_for_who.return_value = None
    try:
        with pytest.raises(HomeAssistantError) as error:
            await call
        assert error.value.translation_key == "central_command_unconfirmed"
        assert not pending.cancelled()
    finally:
        pending.cancel()


def test_a_command_handed_to_the_standby_is_in_flight_in_its_pool_whatever_is_connected_later() -> None:
    handler, _secondary, standby = _handler_with_peers()
    handler._delegated_target.return_value = None
    handler._failover_target.return_value = standby
    command = OWNMessage.parse("*4*303*#0##")

    async def handed_over(message: object, **kwargs: object) -> None:
        standby._command_pool._in_flight_messages.append(message)

    standby.send = AsyncMock(side_effect=handed_over)
    asyncio.run(MyHOMEGatewayHandler.send(handler, command))
    asyncio.run(MyHOMEGatewayHandler.send(handler, command, is_cancel=True))
    assert standby.send.await_args_list[1].kwargs == {"is_cancel": True}
    handler._get_standby_gateway.return_value = None  # the standby entry is gone from the topology
    assert MyHOMEGatewayHandler.command_in_flight(handler, command) is True


# ── a central unit that 2.0.0 saved as ``#0#0`` ──


async def _climate_entities(hass: HomeAssistant, saved_ids: list[str], yaml: dict | None, options: dict | None = None) -> list:
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": DISCOVERY_MAC}, unique_id=DISCOVERY_MAC, options=options or {})
    entry.add_to_hass(hass)
    gateway = MagicMock(mac=DISCOVERY_MAC, is_connected=True)
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    runtime = attach_runtime(hass, entry, DISCOVERY_MAC, gateway)
    runtime.platforms["climate"] = {}
    registry = er.async_get(hass)
    for number, unique_id in enumerate(saved_ids):
        registry.async_get_or_create(
            "climate", DOMAIN, f"{DISCOVERY_MAC}-4-{unique_id}", config_entry=entry, suggested_object_id=f"saved_{number}"
        )
    if yaml is not None:
        _apply_yaml_platforms({"climate": climate_schema(yaml)}, runtime.platforms)
    entities: list = []
    await climate_setup_entry(hass, entry, entities.extend)
    return entities


PLANT = {"plant": {"central": True, "zone": "0", "name": "Plant"}}


async def test_the_entity_saved_as_central_unit_00_becomes_the_central_unit(hass: HomeAssistant) -> None:
    entities = await _climate_entities(hass, ["#0#0"], PLANT)
    assert [entity._where for entity in entities] == ["#0"]
    registry = er.async_get(hass)
    assert registry.async_get("climate.saved_0").unique_id == f"{DISCOVERY_MAC}-4-#0"  # its entity id is kept


async def test_behind_an_interface_too(hass: HomeAssistant) -> None:
    entities = await _climate_entities(hass, ["#0#0#4#01"], {"plant": {**PLANT["plant"], "interface": "01"}})
    assert [(entity._where, entity._interface) for entity in entities] == [("#0", "01")]
    assert er.async_get(hass).async_get("climate.saved_0").unique_id == f"{DISCOVERY_MAC}-4-#0#4#01"


async def test_beside_the_central_unit_found_on_the_bus_the_00_entity_is_removed(hass: HomeAssistant) -> None:
    entities = await _climate_entities(hass, ["#0#0", "#0"], PLANT)
    assert [entity._where for entity in entities] == ["#0"]
    registry = er.async_get(hass)
    assert registry.async_get("climate.saved_0") is None
    assert registry.async_get("climate.saved_1").unique_id == f"{DISCOVERY_MAC}-4-#0"


async def test_a_four_zone_central_unit_and_a_zone_keep_their_saved_entities(hass: HomeAssistant) -> None:
    await _climate_entities(hass, ["#0#1", "10", "#0"], None)
    registry = er.async_get(hass)
    assert [registry.async_get(f"climate.saved_{n}").unique_id.rpartition("-4-")[2] for n in range(3)] == ["#0#1", "10", "#0"]


def test_the_saved_copy_of_a_central_unit_stored_as_00_is_corrected() -> None:
    saved = {
        "climate": {
            key: {"who": "4", "zone": "#0#0", "central": True, "name": "Plant"}
            for key in ("4-0", "plant", "0", "#0#0", "zone_0")  # the keys 2.0.0 filed the entry under
        }
        | {"4-3": {"who": "4", "zone": "3", "central": False, "name": "Zone 3"}},
        "light": {"1-12": {"who": "1", "where": "12"}},
    }
    corrected, lines = canonicalise_imported_platforms(saved)
    assert lines == ["climate: #0#0 -> #0"]
    assert set(corrected["climate"]) == {"4-0", "plant", "0", "#0", "zone_0", "4-3"}
    assert {corrected["climate"][key]["zone"] for key in ("4-0", "plant", "0", "#0", "zone_0")} == {"#0"}
    assert corrected["climate"]["4-3"]["zone"] == "3" and corrected["light"] == saved["light"]
    assert saved["climate"]["#0#0"]["zone"] == "#0#0"  # the stored copy itself is not touched
    assert canonicalise_imported_platforms(corrected) == (corrected, [])

    routed = {"climate": {"#0#0#4#01": {"who": "4", "zone": "#0#0", "central": True, "interface": "01"}}}
    assert canonicalise_imported_platforms(routed)[1] == ["climate: #0#0#4#01 -> #0#4#01"]

    # A copy that already has a central unit under ``#0`` keeps that one.
    both = {"climate": {"#0": {"who": "4", "zone": "#0", "central": True, "name": "A"}, "#0#0": {"who": "4", "zone": "#0#0", "central": True, "name": "B"}}}
    assert canonicalise_imported_platforms(both) == ({"climate": {"#0": both["climate"]["#0"]}}, [])

    # Only the entry's other keys are left: still corrected, and reported once.
    aliases = {"climate": {key: {"who": "4", "zone": "#0#0", "central": True} for key in ("plant", "0")}}
    corrected, lines = canonicalise_imported_platforms(aliases)
    assert lines == ["climate: central unit zone #0#0 -> #0"]
    assert {entry["zone"] for entry in corrected["climate"].values()} == {"#0"}

    # ``zone: "#0#0"`` written by hand without ``central`` is left as the validator leaves it.
    typed = {"climate": {"#0#0": {"who": "4", "zone": "#0#0", "central": False}}}
    assert canonicalise_imported_platforms(typed) == (typed, [])


async def test_a_start_without_myhome_yaml_uses_the_corrected_copy(hass: HomeAssistant) -> None:
    stored = _snapshot({"climate": climate_schema(PLANT)})
    for entry in stored["climate"].values():
        entry["zone"] = "#0#0"
    stored["climate"]["#0#0"] = stored["climate"].pop("#0")  # as 2.0.0 stored it
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": DISCOVERY_MAC}, unique_id=DISCOVERY_MAC, options={CONF_MIGRATED_YAML_PLATFORMS: stored})
    entry.add_to_hass(hass)
    configured: dict[str, dict] = {"climate": {}}
    _apply_imported_copy(hass, entry, configured)
    assert configured["climate"]["#0"]["zone"] == "#0" and "#0#0" not in configured["climate"]
    assert "#0#0" not in entry.options[CONF_MIGRATED_YAML_PLATFORMS]["climate"]


# ── what the review of the final commit found ──


def test_a_central_unit_the_copy_holds_twice_is_the_one_stored_as_hash_0_under_every_key() -> None:
    kept = {"who": "4", "zone": "#0", "central": True, "name": "A"}
    other = {**kept, "zone": "#0#0", "name": "B"}
    saved = {"climate": {"#0": kept, "original": kept, "plant": other, "4-0": other, "#0#0": other, "zone_0": other}}
    corrected, lines = canonicalise_imported_platforms(saved)
    assert corrected == {"climate": {"#0": kept, "original": kept}} and lines == []
    configured: dict[str, dict] = {"climate": {}}
    _apply_yaml_platforms(corrected, configured)
    assert configured["climate"]["#0"]["name"] == "A"

    # The central unit of another bus is another central unit.
    routed = {"who": "4", "zone": "#0", "central": True, "interface": "01"}
    saved = {"climate": {"#0#4#01": routed, "#0#0": {"who": "4", "zone": "#0#0", "central": True}}}
    corrected, lines = canonicalise_imported_platforms(saved)
    assert set(corrected["climate"]) == {"#0#4#01", "#0"} and lines == ["climate: #0#0 -> #0"]


@pytest.mark.parametrize("interface", [None, "01"])
@pytest.mark.parametrize("order", [("room", "controlled"), ("controlled", "room")])
def test_one_zone_written_twice_is_the_same_entry_under_every_number_key(order: tuple[str, str], interface: str | None) -> None:
    routed = {"interface": interface} if interface else {}
    raw = {"room": {"zone": "01", "name": "Probe", **routed}, "controlled": {"zone": "#1", "name": "Controlled", **routed}}
    validated = climate_schema({key: raw[key] for key in order})
    routing = f"#4#{interface}" if interface else ""
    numbers = [f"1{routing}", f"4-1{routing}"] + ([] if interface else ["zone_1"])
    assert len({id(validated[key]) for key in numbers}) == 1  # ``4-1`` and ``zone_1`` are whose ``1`` is
    configured: dict[str, dict] = {"climate": {}}
    _apply_yaml_platforms({"climate": validated}, configured)
    address, key = Address("1", interface), f"1{routing}"
    assert _zone_config(configured["climate"], address, key)["name"] == _zone_config(validated, address, key)["name"]
    assert {configured["climate"][number]["name"] for number in numbers} == {validated[numbers[0]]["name"]}


async def _saved_central_00_with_its_device(hass: HomeAssistant, also: str | None = None) -> tuple:
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": DISCOVERY_MAC}, unique_id=DISCOVERY_MAC)
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{DISCOVERY_MAC}-4-#0#0")}, name="Plant"
    )
    devices.async_update_device(device.id, name_by_user="Boiler room")
    saved = er.async_get(hass).async_get_or_create(
        "climate", DOMAIN, f"{DISCOVERY_MAC}-4-#0#0", config_entry=entry, device_id=device.id
    )
    if also is not None:
        devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, also)}, name="Other")
    gateway = MagicMock(mac=DISCOVERY_MAC, is_connected=True, send=AsyncMock(), send_status_request=AsyncMock(), device_registry_id=None)
    runtime = attach_runtime(hass, entry, DISCOVERY_MAC, gateway)
    runtime.platforms["climate"] = {}
    entities: list = []
    await climate_setup_entry(hass, entry, entities.extend)
    return entry, device, saved, entities


async def test_the_device_of_the_entity_saved_as_central_unit_00_follows_it(hass: HomeAssistant) -> None:
    entry, device, saved, entities = await _saved_central_00_with_its_device(hass)
    assert entities[0].unique_id == er.async_get(hass).async_get(saved.entity_id).unique_id == f"{DISCOVERY_MAC}-4-#0"
    # The device the entity names is the one the user named, not a new one.
    named = device_info_to_dict(entities[0].device_info)["identifiers"]
    assert named == {(DOMAIN, f"{DISCOVERY_MAC}-4-#0")}
    held = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert [(found.id, found.name_by_user, found.identifiers) for found in held] == [(device.id, "Boiler room", named)]


async def test_a_device_that_already_holds_the_central_units_identifier_keeps_it(hass: HomeAssistant) -> None:
    entry, device, saved, _entities = await _saved_central_00_with_its_device(hass, also=f"{DISCOVERY_MAC}-4-#0")
    assert er.async_get(hass).async_get(saved.entity_id).unique_id == f"{DISCOVERY_MAC}-4-#0"
    assert dr.async_get(hass).async_get(device.id).identifiers == {(DOMAIN, f"{DISCOVERY_MAC}-4-#0#0")}


async def test_an_address_00_the_configuration_still_names_keeps_its_saved_entity(hass: HomeAssistant) -> None:
    # ``zone: "#0#0"`` typed by hand without ``central`` is left as the validator leaves it.
    entities = await _climate_entities(hass, ["#0#0"], {"plant": {"zone": "#0#0", "central": False}})
    assert [entity._where for entity in entities] == ["#0#0"]
    assert er.async_get(hass).async_get("climate.saved_0").unique_id == f"{DISCOVERY_MAC}-4-#0#0"


# ── what the audit of the review fixes left open ──


async def test_a_central_unit_found_behind_an_interface_is_the_one_myhome_yaml_writes_as_zone_0(hass: HomeAssistant) -> None:
    entities = await _climate_entities(hass, ["#0#4#01"], {"plant": {"zone": "0", "name": "Plant", "interface": "01"}})
    assert [(entity._where, entity._interface) for entity in entities] == [("#0", "01")]
    assert device_info_to_dict(entities[0].device_info)["name"] == "Plant"


@pytest.mark.parametrize("spelling", ["#1", "01"])
async def test_a_saved_zone_is_the_zone_myhome_yaml_writes_in_another_spelling(hass: HomeAssistant, spelling: str) -> None:
    entities = await _climate_entities(hass, ["1", "#0#1"], {"centrale": dict(CENTRAL), "zona1": {"zone": spelling, **ZONE}})
    assert sorted(entity._display_name for entity in entities) == ["Centrale 4695", "Zona 1"]


@pytest.mark.parametrize(
    ("zone", "where"),
    [("0#4#01", "#0"), ("#0#4#01", "#0"), ("#0#0#4#01", "#0"), ("1#4#01", "#0#1"), ("#0#1#4#01", "#0#1")],
)
def test_central_true_with_the_interface_written_in_the_zone_gives_a_central_units_address(zone: str, where: str) -> None:
    validated = climate_schema({"c": {"zone": zone, "central": True}})
    address = Address.from_config("c", validated["c"])
    assert (address.clean_where, address.interface) == (where, "01")


async def test_the_entity_saved_as_central_unit_00_with_the_interface_in_its_zone_becomes_the_central_unit(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    # 2.0.0 made ``#0#0#4#01`` of ``zone: "0#4#01"``, ``central: true``.
    caplog.set_level(logging.INFO, logger="custom_components.myhome")
    entities = await _climate_entities(hass, ["#0#0#4#01"], {"plant": {"zone": "0#4#01", "central": True, "name": "Plant"}})
    assert [(entity._where, entity._interface) for entity in entities] == [("#0", "01")]
    assert er.async_get(hass).async_get("climate.saved_0").unique_id == f"{DISCOVERY_MAC}-4-#0#4#01"
    assert "climate.saved_0 was saved as central unit #0#0#4#01; it is the central unit #0#4#01" in caplog.text


@pytest.mark.parametrize(("saved", "interface"), [("#0##0#1", None), ("#0##0#1#4#01", "01")])
async def test_the_entity_saved_under_the_doubled_address_of_a_four_zone_central_unit_becomes_it(
    hass: HomeAssistant, saved: str, interface: str | None
) -> None:
    # 2.0.0 made ``#0##0#1`` of ``zone: "#0#1"``, ``central: true``.
    config = {"zone": "#0#1", "central": True} | ({"interface": interface} if interface else {})
    entities = await _climate_entities(hass, [saved], {"plant": config})
    assert [(entity._where, entity._interface) for entity in entities] == [("#0#1", interface)]
    routing = f"#4#{interface}" if interface else ""
    assert er.async_get(hass).async_get("climate.saved_0").unique_id == f"{DISCOVERY_MAC}-4-#0#1{routing}"


def test_the_saved_copy_of_a_central_unit_stored_under_a_doubled_address_is_corrected() -> None:
    saved = {"climate": {key: {"who": "4", "zone": "#0##0#1", "central": True} for key in ("plant", "#0##0#1")}}
    corrected, lines = canonicalise_imported_platforms(saved)
    assert lines == ["climate: #0##0#1 -> #0#1"]
    assert set(corrected["climate"]) == {"plant", "#0#1"}
    assert {entry["zone"] for entry in corrected["climate"].values()} == {"#0#1"}


@pytest.mark.parametrize(
    ("named_kept", "area_kept", "expected"),
    [
        (None, None, ("Boiler room", "cellar")),  # what the user gave the removed one
        ("Plant room", "attic", ("Plant room", "attic")),  # what the user gave the one that stays
        ("Plant room", None, ("Plant room", "cellar")),
    ],
)
async def test_the_name_and_area_of_the_removed_central_unit_go_to_the_one_that_stays(
    hass: HomeAssistant, named_kept: str | None, area_kept: str | None, expected: tuple[str, str]
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": DISCOVERY_MAC}, unique_id=DISCOVERY_MAC)
    entry.add_to_hass(hass)
    areas = ar.async_get(hass)
    for area_name in ("cellar", "attic"):
        areas.async_create(area_name)
    devices, registry = dr.async_get(hass), er.async_get(hass)
    held = {}
    for where, name, area in (("#0#0", "Boiler room", "cellar"), ("#0", named_kept, area_kept)):
        device = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{DISCOVERY_MAC}-4-{where}")}, name="Plant")
        devices.async_update_device(device.id, name_by_user=name, area_id=area)
        registry.async_get_or_create("climate", DOMAIN, f"{DISCOVERY_MAC}-4-{where}", config_entry=entry, device_id=device.id)
        held[where] = device.id
    gateway = MagicMock(mac=DISCOVERY_MAC, is_connected=True, send=AsyncMock(), send_status_request=AsyncMock(), device_registry_id=None)
    attach_runtime(hass, entry, DISCOVERY_MAC, gateway).platforms["climate"] = {}
    entities: list = []
    await climate_setup_entry(hass, entry, entities.extend)
    assert [entity._where for entity in entities] == ["#0"]
    kept = devices.async_get(held["#0"])
    assert (kept.name_by_user, kept.area_id) == expected


async def test_a_removed_central_unit_without_a_device_hands_nothing_over(hass: HomeAssistant) -> None:
    entities = await _climate_entities(hass, ["#0#0", "#0"], PLANT)  # neither saved entity has a device
    assert [entity._where for entity in entities] == ["#0"]
    assert dr.async_entries_for_config_entry(dr.async_get(hass), entities[0]._gateway_handler.config_entry.entry_id) == []


async def test_the_availability_of_a_secondary_of_another_who_does_not_end_a_refusal_of_the_primary(hass: HomeAssistant, gateway: MagicMock) -> None:
    gateway.availability_signal = "primary_availability"
    probe = _sensor(hass, gateway)
    probe.async_write_ha_state = MagicMock()
    gateway.send_status_request = AsyncMock(return_value=_refused(hass))
    await probe.async_update()
    await _settle()
    assert probe._fallback_refused_by is gateway
    disconnect = async_dispatcher_connect(hass, gateway.availability_signal, probe._handle_availability_update)
    try:
        secondary = MagicMock(hass=hass, is_standby=False, is_secondary=True, delegated_whos={1}, availability_signal="secondary_availability")
        secondary._get_primary_gateway.return_value = gateway
        MyHOMEGatewayHandler._notify_availability(secondary)  # lights went to a secondary that reconnected
        await hass.async_block_till_done()
        assert probe._fallback_read_refused is True
        await probe.async_update()
        assert _sent(gateway) == [READ]
        gateway.event_connections += 1  # the primary's own new event session does end it
        MyHOMEGatewayHandler._notify_availability(secondary)
        await hass.async_block_till_done()
        assert probe._fallback_read_refused is False
    finally:
        disconnect()


async def test_a_read_queued_before_the_connection_and_refused_on_it_is_latched(hass: HomeAssistant) -> None:
    handler = _f455_handler(hass, "F455")
    probe = _sensor(hass, handler)
    await probe.async_update()  # queued: no event session yet, no worker has written it
    handler._on_event_connection_state_change(True)
    session = _command_session()
    _answers(session, None, OWNNackError(f"Gateway rejected message `{READ}` with NACK", frame=READ))
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        await handler.send_buffer.put(None)
        await handler.sending_loop(0)
    await _settle()
    assert str(session.send.await_args.kwargs["message"]) == READ  # written on the new connection
    assert probe._fallback_read_refused is True and probe._fallback_refused_by is handler
    assert probe._fallback_refused_on_connection == handler.event_connections


async def test_a_read_written_on_a_connection_that_is_gone_when_it_is_refused_is_not_latched(hass: HomeAssistant) -> None:
    handler = _f455_handler(hass, "F455")
    handler._on_event_connection_state_change(True)
    probe = _sensor(hass, handler)
    session = _command_session()

    async def _send(message: object, is_status_request: bool = False) -> None:
        handler._on_event_connection_state_change(False)  # the event session drops while the read is out ...
        handler._on_event_connection_state_change(True)  # ... and is back before the gateway's NACK is handled
        session.last_error = OWNNackError(f"Gateway rejected message `{READ}` with NACK", frame=READ)

    session.send = AsyncMock(side_effect=_send)
    await probe.async_update()
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        await handler.send_buffer.put(None)
        await handler.sending_loop(0)
    await _settle()
    assert probe._fallback_read_refused is False


@pytest.mark.parametrize(
    "frame",
    [
        "*#4*3#1#2*20*150##",  # tests/test_issue_949_fancoil_dim20_output_level.py:278: a 3-token WHERE
        "*#4*3#1#4#99*20*150##",  # ibid. :279 with zone 3: an interface out of range
    ],
)
async def test_a_where_that_is_neither_the_zone_nor_one_actuator_changes_nothing(hass: HomeAssistant, frame: str) -> None:
    zone = _zone_switch(hass, _mock_gateway())
    zone._interface = thermo_event_interface(OWNMessage.parse(frame))  # past the interface filter, to the WHERE itself
    zone.handle_event(OWNMessage.parse(frame))
    assert zone.is_on is None and zone.extra_state_attributes.get("actuators") is None
    assert thermo_dim20_whole_zone_target(OWNMessage.parse(frame)) is False


@pytest.mark.parametrize(
    ("frame", "whole_zone"),
    [
        ("*#4*3*20*1##", True),
        ("*#4*3*#20*1##", True),
        ("*#4*3#0*20*1##", True),
        ("*#4*3#4#01*20*1##", True),
        ("*#4*3#0#4#01*20*1##", True),
        ("*#4*3#3*20*160##", False),
        ("*#4*3#1#4#01*20*1##", False),
        ("*#4*#0#1*20*1##", False),
        ("*#4*#1*20*1##", False),
        ("*#4*0#1*20*1##", False),
    ],
)
def test_the_wheres_that_are_a_whole_zone(frame: str, whole_zone: bool) -> None:
    assert thermo_dim20_whole_zone_target(OWNMessage.parse(frame)) is whole_zone


@pytest.mark.parametrize("off", ["*#4*3*#20*0##", "*#4*3#0*20*0##"])
async def test_a_whole_zone_off_puts_the_known_outputs_of_the_whole_zone_switch_at_0(hass: HomeAssistant, off: str) -> None:
    zone = _zone_switch(hass, _mock_gateway())
    zone.handle_event(OWNMessage.parse("*#4*3#3*20*160##"))  # tests/test_issue_949_fancoil_dim20_output_level.py:1303
    assert zone.is_on is True and zone.extra_state_attributes["actuator_levels"] == {"3": 60}
    zone.handle_event(OWNMessage.parse("*#4*3*#20*1##"))  # on says nothing about the level
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 60}
    zone.handle_event(OWNMessage.parse(off))
    assert zone.is_on is False and zone.extra_state_attributes["actuator_levels"] == {"3": 0}
