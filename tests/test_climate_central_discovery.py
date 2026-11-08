"""Regression and discovery tests for central heating units (BTicino 3550 / 4695).

Guarantees:
1. Physical bus frames from 3550 central units (#0) and 4-zone central units (#0#1)
   never trigger bus discovery of phantom 'Climate Zone 99' (#582).
2. Deleting an unused/ghost heating zone entity permanently drops its unresponsive
   repair alert, clears the entity registry, and prevents resurrection in the absence
   of on-wire frames for that zone.
3. Central units operate via event-driven broadcast synchronization without point-to-point status polling.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from OWNd.message import OWNEvent, OWNHeatingEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import (
    MyHOMEClimate,
    _calling_zones,
    _zone_address,
    _zone_route_keys,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.device_health import DeviceHealth, FaultKind, fault_issue_id
from tests.conftest import attach_runtime

MAC = "00:03:50:44:55:66"

BTICINO_3550_FRAMES = [
    # Outbound mode commands (sent by HA / integration to central unit #0)
    "*4*303*#0##",  # Conditioning OFF command
    "*4*1*#0##",  # Manual Heating command
    "*4*0*#0##",  # Manual Cooling command
    "*4*311*#0##",  # Auto Heating/Cooling command
    # Inbound operating mode status broadcasts (emitted by physical central unit #0)
    "*4*110*#0##",  # Manual Heating operating mode status
    "*4*210*#0##",  # Manual Cooling operating mode status
    "*4*303*#0##",  # Generic OFF operating mode status
    "*4*311*#0##",  # Programmed / automatic operation status (generic; mapped to HVACMode.AUTO)
    "*4*110#0200*#0##",  # Manual setpoint 20.0 °C on central unit
    "*4*210#0240*#0##",  # Cooling setpoint 24.0 °C on central unit
    "*4*21*#0##",  # Remote control enabled
    "*4*20*#0##",  # Remote control disabled
    "*4*31*#0##",  # Central unit battery fault
    "*4*40*#0##",  # Release local probe adjustment
    "*4*1101*#0##",  # Weekly program 1 (heating)
    "*4*1102*#0##",  # Weekly program 2 (heating)
    "*4*1103*#0##",  # Weekly program 3 (heating)
    "*4*1201*#0##",  # Scenario 1 (heating)
    "*#4*#0*30*01*10*2026##",  # Holiday end date
    "*#4*#0*31*12*00##",  # Holiday end time
]

BTICINO_4695_FRAMES = [
    "*4*303*#0#1##",  # Conditioning OFF command for 4695 zone 1
    "*4*1*#0#1##",  # Manual Heating command for 4695 zone 1
    "*4*0*#0#1##",  # Manual Cooling command for 4695 zone 1
    "*4*110*#0#1##",  # Manual Heating status for 4695 zone 1
    "*4*210*#0#1##",  # Manual Cooling status for 4695 zone 1
    "*4*303*#0#1##",  # Generic OFF status for 4695 zone 1
    "*4*311*#0#1##",  # Programmed / automatic operation status (generic) for 4695 zone 1
    "*4*110#0210*#0#1##",  # Manual setpoint 21.0 °C for 4695 zone 1
    "*4*1101*#0#1##",  # Weekly program 1 for 4695 zone 1
    "*4*1201*#0#1##",  # Scenario 1 for 4695 zone 1
]


@pytest.mark.parametrize("raw_frame", BTICINO_3550_FRAMES)
def test_3550_frames_never_resolve_to_zone_99(raw_frame: str) -> None:
    """Assert that 3550 central unit frames route strictly to #0 and never resolve to zone 99 (#582)."""
    message = OWNEvent.parse(raw_frame)
    assert message is not None

    zones, _ = _calling_zones(message)
    assert "99" not in zones
    assert zones == ["#0"]

    address = _zone_address(message)
    assert address is not None
    assert address.where == "#0"
    assert address.clean_where == "#0"

    route_keys = _zone_route_keys(message, address)
    assert "99" not in route_keys
    assert "4-99" not in route_keys
    assert "#0" in route_keys


@pytest.mark.parametrize("raw_frame", BTICINO_4695_FRAMES)
def test_4695_frames_never_resolve_to_zone_99(raw_frame: str) -> None:
    """Assert that 4695 4-zone central unit frames route to subordinate zone 1 and never resolve to zone 99 (#582)."""
    message = OWNEvent.parse(raw_frame)
    assert message is not None

    zones, _ = _calling_zones(message)
    assert "99" not in zones
    assert zones == ["1"]

    address = _zone_address(message)
    assert address is not None
    assert address.where == "1"
    assert address.clean_where == "1"

    route_keys = _zone_route_keys(message, address)
    assert "99" not in route_keys
    assert "1" in route_keys
    assert "#0" in route_keys


async def test_3550_bus_traffic_never_discovers_phantom_climate_zone_99(hass: HomeAssistant) -> None:
    """Feed complete 3550 central unit bus activity into PlatformDiscovery and assert zone 99 is never created (#582)."""
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
    entry.add_to_hass(hass)

    gateway = MagicMock()
    gateway.mac = MAC
    gateway.is_connected = True
    gateway.log_id = "[test 3550 discovery]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()

    runtime = attach_runtime(hass, entry, MAC, gateway)
    runtime.platforms["climate"] = {}

    discovered_entities: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, discovered_entities.extend)

    # 1. Initially, no entities exist
    assert len(discovered_entities) == 0

    # 2. Dispatch all 3550 central unit traffic through the real message dispatcher
    for raw_frame in BTICINO_3550_FRAMES:
        event = OWNHeatingEvent(raw_frame)
        async_dispatcher_send(hass, f"myhome_message_{MAC}", event)

    # 3. Central unit #0 is discovered with default bus name, but phantom zone 99 is NEVER discovered
    assert len(discovered_entities) == 1
    assert discovered_entities[0]._where == "#0"
    assert discovered_entities[0]._central is True
    # Without yaml, bus discovery names a central unit 'Climate Zone {suffix}' (suffix='0')
    assert discovered_entities[0]._device_name == "Climate Zone 0"
    assert not any(getattr(e, "_where", None) == "99" for e in discovered_entities)
    assert not any("99" in (getattr(e, "_device_name", "") or "") for e in discovered_entities)

    # 4. Positive control: genuine subordinate zone traffic (e.g. Zone 5) MUST trigger dynamic discovery
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*4*110*5##"))
    assert len(discovered_entities) == 2
    assert any(getattr(e, "_where", None) == "5" for e in discovered_entities)


async def test_ghost_zone_99_deletion_permanently_clears_repair_and_prevents_resurrection(
    hass: HomeAssistant,
) -> None:
    """Assert deleting a ghost Climate Zone 99 entity removes its repair, clears registry, and does not resurrect on central bus traffic (#582)."""
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
    entry.add_to_hass(hass)

    gateway = MagicMock()
    gateway.mac = MAC
    gateway.is_connected = True
    gateway.log_id = "[test ghost 99]"
    gateway.name = "MyHomeServer1 Gateway"
    gateway.hass = hass
    gateway.config_entry = entry
    gateway.device_health = DeviceHealth(gateway)
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()

    runtime = attach_runtime(hass, entry, MAC, gateway)
    runtime.platforms["climate"] = {}

    # Simulate pre-existing ghost entity in entity registry (e.g. from prior user configuration)
    entity_reg = er.async_get(hass)
    ghost_reg_entry = entity_reg.async_get_or_create(
        domain="climate",
        platform=DOMAIN,
        unique_id=f"{MAC}-4-99",
        config_entry=entry,
        original_name="Climate Zone 99",
    )
    assert "99" in ghost_reg_entry.entity_id

    entities: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, entities.extend)

    # Registered ghost zone 99 is built from registry
    assert len(entities) == 1
    ghost_z99 = entities[0]
    assert ghost_z99._where == "99"
    ghost_z99.entity_id = ghost_reg_entry.entity_id

    # Simulate 2 failed polls on ghost zone 99 (is_connected=True satisfies PollHealth precondition)
    future_nack = asyncio.get_running_loop().create_future()
    gateway.send_status_request.return_value = future_nack
    await ghost_z99.async_update()
    future_nack.cancel()
    await asyncio.sleep(0)

    future_nack2 = asyncio.get_running_loop().create_future()
    gateway.send_status_request.return_value = future_nack2
    await ghost_z99.async_update()
    future_nack2.cancel()
    await asyncio.sleep(0)

    # Repair issue is raised for ghost zone 99
    issue_id = fault_issue_id(entry.entry_id, FaultKind.UNRESPONSIVE, 4, "99")
    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, issue_id) is not None

    # Owner removes the entity from Home Assistant settings
    entity_reg.async_remove(ghost_z99.entity_id)
    await ghost_z99.async_removed_from_registry()

    # The repair issue is automatically dropped
    assert issue_reg.async_get_issue(DOMAIN, issue_id) is None

    # Entity registry no longer contains zone 99
    assert entity_reg.async_get(ghost_z99.entity_id) is None
    remaining_entries = er.async_entries_for_config_entry(entity_reg, entry.entry_id)
    assert not any(e.unique_id == f"{MAC}-4-99" for e in remaining_entries)

    # Simulate entry unload / unsubscription before reload
    await async_unload_entry(hass, entry)
    while entry._on_unload:
        entry._on_unload.pop()()

    # Simulate entry reload / system restart after deletion
    reloaded_entities: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, reloaded_entities.extend)

    # Zone 99 is NOT restored because it was removed from the entity registry
    assert len(reloaded_entities) == 0

    # Central unit bus activity does NOT resurrect or recreate ghost zone 99
    gateway.send_status_request.reset_mock()
    for raw_frame in BTICINO_3550_FRAMES:
        event = OWNHeatingEvent(raw_frame)
        async_dispatcher_send(hass, f"myhome_message_{MAC}", event)

    # Central unit #0 is discovered, but zone 99 is not resurrected
    assert len(reloaded_entities) == 1
    assert reloaded_entities[0]._where == "#0"
    assert issue_reg.async_get_issue(DOMAIN, issue_id) is None
    assert entity_reg.async_get_entity_id("climate", DOMAIN, f"{MAC}-4-99") is None

    # Positive control: if genuine on-wire traffic for Zone 99 arrives (*4*110*99##),
    # discovery recognizes it as a valid subordinate zone.
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*4*110*99##"))
    assert len(reloaded_entities) == 2
    discovered_z99 = reloaded_entities[-1]
    assert discovered_z99._where == "99"
    assert discovered_z99._central is False


async def test_central_unit_event_driven_synchronization(hass: HomeAssistant) -> None:
    """Verify central units operate purely via event-driven bus synchronization without point-to-point status polling (#582)."""
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.log_id = "[test event-driven sync]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()

    cu = MyHOMEClimate(
        hass=hass,
        name="Centrale termoregolazione",
        device_id="cu_3550",
        who="4",
        where="#0",
        heating=True,
        cooling=True,
        fan=False,
        standalone=False,
        central=True,
        manufacturer="BTicino",
        model="Central Unit (3550)",
        gateway=gateway,
    )
    cu.entity_id = "climate.centrale_termoregolazione"
    cu.async_write_ha_state = MagicMock()

    # Track central mode updates dispatched to subordinate zones
    central_mode_events: list[HVACMode] = []

    @callback
    def _record_central_mode(mode: HVACMode) -> None:
        central_mode_events.append(mode)

    async_dispatcher_connect(
        hass,
        f"myhome_central_mode_{MAC}",
        _record_central_mode,
    )

    # 1. Startup update sends canonical plain status request *#4*#0## (#629)
    await cu.async_update()
    gateway.send_status_request.assert_called_once()
    assert str(gateway.send_status_request.call_args[0][0]) == "*#4*#0##"

    # 2. Command path: setting mode via HA emits central commands and dispatches signal.
    # HEAT / COOL only change the season: the mode shown comes from the 3550's answer
    # (antifreeze / thermal protection, live 3550 2026-11-08).
    await cu.async_set_hvac_mode(HVACMode.HEAT)
    sent_cmd = gateway.send.call_args[0][0]
    assert str(sent_cmd) == "*4*1*#0##"
    assert cu.hvac_mode is None
    cu.handle_event(OWNHeatingEvent("*4*102*#0##"))
    assert cu.hvac_mode == HVACMode.HEAT
    assert central_mode_events[-1] == HVACMode.HEAT

    await cu.async_set_hvac_mode(HVACMode.COOL)
    sent_cmd = gateway.send.call_args[0][0]
    assert str(sent_cmd) == "*4*0*#0##"
    assert cu.hvac_mode == HVACMode.HEAT
    cu.handle_event(OWNHeatingEvent("*4*202*#0##"))
    assert cu.hvac_mode == HVACMode.COOL
    assert central_mode_events[-1] == HVACMode.COOL

    await cu.async_set_hvac_mode(HVACMode.AUTO)
    sent_cmd = gateway.send.call_args[0][0]
    assert str(sent_cmd) == "*4*311*#0##"
    assert cu.hvac_mode == HVACMode.AUTO
    assert central_mode_events[-1] == HVACMode.AUTO

    await cu.async_set_hvac_mode(HVACMode.OFF)
    sent_cmd = gateway.send.call_args[0][0]
    assert str(sent_cmd) == "*4*303*#0##"
    assert cu.hvac_mode == HVACMode.OFF
    assert central_mode_events[-1] == HVACMode.OFF

    # 3. Autonomous operating mode broadcast events update HVACMode and dispatch signal
    # (later than the 2 s in which a gateway's echo of the last command is expected)
    cu._central_echo = None
    cu.handle_event(OWNHeatingEvent("*4*110*#0##"))  # Manual heating status event
    assert cu.hvac_mode == HVACMode.HEAT
    assert central_mode_events[-1] == HVACMode.HEAT

    cu.handle_event(OWNHeatingEvent("*4*210*#0##"))  # Manual cooling status event
    assert cu.hvac_mode == HVACMode.COOL
    assert central_mode_events[-1] == HVACMode.COOL

    cu.handle_event(OWNHeatingEvent("*4*311*#0##"))  # Programmed / Auto status event
    assert cu.hvac_mode == HVACMode.AUTO
    assert central_mode_events[-1] == HVACMode.AUTO

    cu.handle_event(OWNHeatingEvent("*4*303*#0##"))  # Generic OFF status event
    assert cu.hvac_mode == HVACMode.OFF
    assert central_mode_events[-1] == HVACMode.OFF

    # 4. Target setpoint broadcast frame updates target temperature and restores mode
    cu.handle_event(OWNHeatingEvent("*4*110#0215*#0##"))
    assert cu.target_temperature == 21.5
    assert cu.hvac_mode == HVACMode.HEAT
    assert central_mode_events[-1] == HVACMode.HEAT

    # 5. Weekly programs and scenarios: heating programs actively transition mode to HEAT
    cu._attr_hvac_mode = HVACMode.OFF
    cu.handle_event(OWNHeatingEvent("*4*1101*#0##"))  # Weekly program 1 (heating)
    assert cu.hvac_mode == HVACMode.HEAT

    cu._attr_hvac_mode = HVACMode.OFF
    cu.handle_event(OWNHeatingEvent("*4*1201*#0##"))  # Scenario 1 (heating)
    assert cu.hvac_mode == HVACMode.HEAT


async def test_yaml_central_unit_and_bus_traffic_coalesce_without_duplicate_entities(
    hass: HomeAssistant,
) -> None:
    """Assert a YAML-configured central unit (#0) coalesces cleanly with bus traffic, producing exactly 1 entity (#582).

    Covers the setup reported in #582:
      climate:
        central_unit:
          zone: "#0"
          name: Centrale termoregolazione
    Proves that physical bus traffic addressed to #0 updates the YAML entity directly
    and NEVER spawns a duplicate bus-discovered entity ('Climate Zone 0' or 'Climate Zone 99').
    """
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
    entry.add_to_hass(hass)

    gateway = MagicMock()
    gateway.mac = MAC
    gateway.is_connected = True
    gateway.log_id = "[test yaml+bus coalescing]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()

    runtime = attach_runtime(hass, entry, MAC, gateway)
    runtime.platforms["climate"] = {
        "central_unit": {
            "zone": "#0",
            "name": "Centrale termoregolazione",
            "central": True,
        }
    }

    # 1. Platform setup creates exactly 1 entity with the configured YAML name
    entities: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, entities.extend)

    assert len(entities) == 1
    cu = entities[0]
    assert cu._device_name == "Centrale termoregolazione"
    assert cu._where == "#0"
    assert cu._central is True

    # 2. Dispatch complete BTicino 3550 bus traffic
    for raw_frame in BTICINO_3550_FRAMES:
        event = OWNHeatingEvent(raw_frame)
        async_dispatcher_send(hass, f"myhome_message_{MAC}", event)

    # 3. Assert NO duplicate entity is created: entities list remains length 1
    assert len(entities) == 1
    assert not any(getattr(e, "_where", None) == "99" for e in entities)
    assert not any(getattr(e, "_device_name", None) == "Climate Zone 0" for e in entities)

    # 4. Bus frame for subordinate zone (Zone 5) creates Zone 5 alongside the central unit
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*4*110*5##"))
    assert len(entities) == 2
    assert any(getattr(e, "_where", None) == "5" for e in entities)
    assert any(getattr(e, "_where", None) == "#0" for e in entities)

    # 5. Simulate clean unload and restart with entity in registry and YAML config active
    entity_reg = er.async_get(hass)
    entity_reg.async_get_or_create(
        domain="climate",
        platform=DOMAIN,
        unique_id=f"{MAC}-4-#0",
        config_entry=entry,
        original_name="Centrale termoregolazione",
    )
    await async_unload_entry(hass, entry)
    while entry._on_unload:
        entry._on_unload.pop()()

    runtime.platforms["climate"] = {
        "central_unit": {
            "zone": "#0",
            "name": "Centrale termoregolazione",
            "central": True,
        }
    }
    reloaded_entities: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, reloaded_entities.extend)

    # Restored entity and YAML configuration coalesce: exactly 1 central unit
    assert len(reloaded_entities) == 1
    assert reloaded_entities[0]._where == "#0"
    assert reloaded_entities[0]._central is True

    # Replaying bus frames continues to route to the single entity without duplicating
    for raw_frame in BTICINO_3550_FRAMES:
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent(raw_frame))

    assert len(reloaded_entities) == 1


async def test_3550_central_unit_diagnostic_attributes_and_state_restore(hass: HomeAssistant) -> None:
    """Verify #0 diagnostic burst frames (WHAT 20..24, 30, 31), flag reset on next burst, and state restoration (#819)."""
    from homeassistant.core import State

    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
    entry.add_to_hass(hass)

    gateway = MagicMock()
    gateway.mac = MAC
    gateway.is_connected = True
    gateway.log_id = "[test 3550 diagnostics]"
    gateway.name = "MH200N Gateway"
    gateway.hass = hass
    gateway.config_entry = entry
    gateway.device_health = DeviceHealth(gateway)
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()

    cu = MyHOMEClimate(
        hass=hass,
        name="Central Unit 0",
        device_id="4-#0",
        who="4",
        where="#0",
        heating=True,
        cooling=True,
        fan=False,
        standalone=False,
        central=True,
        manufacturer="BTicino",
        model="Central Unit (3550)",
        gateway=gateway,
    )
    cu.entity_id = "climate.central_unit_0"
    cu.async_write_ha_state = MagicMock()

    # 1. Replay the exact MH200N + 3550 *#4*#0## status burst from Discussion #819:
    # *4*1101*#0##, *4*20*#0##, *4*30*#0##, *4*31*#0##, *4*22*#0##
    for raw in ("*4*1101*#0##", "*4*20*#0##", "*4*30*#0##", "*4*31*#0##", "*4*22*#0##"):
        cu.handle_event(OWNHeatingEvent(raw))

    attrs = cu.extra_state_attributes
    assert cu.hvac_mode == HVACMode.HEAT
    assert attrs["season"] == "heating"
    assert attrs["program"] == 1
    assert attrs["remote_control"] == "disabled"
    assert attrs["failure_detected"] is True
    assert attrs["battery_fault"] is True
    assert attrs["at_least_one_probe_off"] is True
    assert attrs["at_least_one_probe_antifreeze"] is False
    assert attrs["at_least_one_probe_manual"] is False

    issue_reg = ir.async_get(hass)
    rc_id = fault_issue_id(entry.entry_id, FaultKind.CENTRAL_REMOTE_CONTROL_DISABLED, 4, "#0")
    bat_id = fault_issue_id(entry.entry_id, FaultKind.CENTRAL_BATTERY_FAULT, 4, "#0")
    assert issue_reg.async_get_issue(DOMAIN, rc_id) is not None
    assert issue_reg.async_get_issue(DOMAIN, bat_id) is not None

    # 2. Next burst: user enabled Telegestione (*4*21*#0##), replaced battery (no *4*31*#0##),
    # resolved the zone failure (no *4*30*#0##) and turned probe 4 back on (no *4*22*#0##),
    # while one probe is in antifreeze (*4*23*#0##) and one is in manual (*4*24*#0##)
    for raw in ("*4*1101*#0##", "*4*21*#0##", "*4*23*#0##", "*4*24*#0##"):
        cu.handle_event(OWNHeatingEvent(raw))

    attrs2 = cu.extra_state_attributes
    assert attrs2["remote_control"] == "enabled"
    assert attrs2["failure_detected"] is False
    assert attrs2["battery_fault"] is False
    assert attrs2["at_least_one_probe_off"] is False
    assert attrs2["at_least_one_probe_antifreeze"] is True
    assert attrs2["at_least_one_probe_manual"] is True
    assert issue_reg.async_get_issue(DOMAIN, rc_id) is None
    assert issue_reg.async_get_issue(DOMAIN, bat_id) is None

    # 3. Restore state round-trip
    restored = MyHOMEClimate(
        hass=hass,
        name="Central Unit 0",
        device_id="4-#0",
        who="4",
        where="#0",
        heating=True,
        cooling=True,
        fan=False,
        standalone=False,
        central=True,
        manufacturer="BTicino",
        model="Central Unit (3550)",
        gateway=gateway,
    )
    await restored.async_restore_last_state(State("climate.central_unit_0", "heat", attrs2))
    restored_attrs = restored.extra_state_attributes
    assert restored_attrs["remote_control"] == "enabled"
    assert restored_attrs["at_least_one_probe_off"] is False
    assert restored_attrs["at_least_one_probe_antifreeze"] is True
    assert restored_attrs["at_least_one_probe_manual"] is True
    assert restored_attrs["failure_detected"] is False
    assert restored_attrs["battery_fault"] is False


async def test_3550_yaml_default_central_false_and_scs_only_bus_discovery_poll(
    hass: HomeAssistant,
) -> None:
    """Ensure zone='#0' with schema default central=False stays central=True, and bus-discovered #0 on MH200N polls *#4*#0## (#819)."""
    from custom_components.myhome.discovery import message_has_state

    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
    entry.add_to_hass(hass)

    gateway = MagicMock()
    gateway.mac = MAC
    gateway.is_connected = True
    gateway.supports_zone_status = False  # SCS-only gateway (MH200N / F454)
    gateway.zone_temperature_poll_needed = False
    gateway.log_id = "[test MH200N #0]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()

    runtime = attach_runtime(hass, entry, MAC, gateway)
    # validate.py sets Optional(CONF_CENTRAL, default=False): Boolean() when user omits `central:` in YAML
    runtime.platforms["climate"] = {
        "central_unit": {
            "zone": "#0",
            "central": False,
        }
    }

    entities: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, entities.extend)
    assert len(entities) == 1
    cu = entities[0]
    assert cu._central is True
    assert cu._device_name == "Central Unit 0"
    assert cu._poll_on_add is True
    assert message_has_state(cu, OWNHeatingEvent("*4*1101*#0##")) is False


async def test_subordinate_zone_dim12_hydration_and_mode_setpoint_control(
    hass: HomeAssistant,
) -> None:
    """Verify subordinate zone hydrates nominal setpoint from Dimension 12/14 and writes #Z setpoints on mode changes (#819)."""
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.is_connected = True
    gateway.has_central_unit = False
    gateway.log_id = "[test zone 1 dim12]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()

    z1 = MyHOMEClimate(
        hass=hass,
        name="Climate Zone 1",
        device_id="4-1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=False,
        standalone=None,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    z1.entity_id = "climate.climate_zone_1"
    z1.async_write_ha_state = MagicMock()

    # Probe WHERE with leading '#' (#105) must not mark the plant as having a central unit
    # via either GatewayEventDispatcher.process_message or MyHOMEClimate.handle_event
    from OWNd.message import OWNHeatingCommand

    from custom_components.myhome.gateway_events import GatewayEventDispatcher

    gateway.hass = hass
    gateway.host = "192.168.1.35"
    gateway.generate_events = False
    gateway._correlate_shared_bus_traffic = MagicMock()
    gateway._bridge_to_primary = MagicMock()
    gateway.health_owner = MagicMock(return_value=None)
    dispatcher = GatewayEventDispatcher(gateway)

    await dispatcher.process_message(OWNHeatingEvent("*#4*#105*0*0210##"))
    assert gateway.has_central_unit is False
    await dispatcher.process_message(OWNHeatingCommand("*#4*#105*#14*0210*1##"))
    gateway.send_status_request.assert_not_called()
    z1.handle_event(OWNHeatingEvent("*#4*#105*0*0210##"))
    assert gateway.has_central_unit is False

    # 1. Replay Discussion #819 Zone 1 (*#4*1##) sequence:
    # Dimension 0 temperature (*#4*1*0*0199##), Dimension 12 setpoint in force (*#4*1*12*0170*3##)
    # while hvac_mode is still None, followed by *4*111*#1## (Automatic heating mode on 3550 plant)
    z1.handle_event(OWNHeatingEvent("*#4*1*0*0199##"))
    z1.handle_event(OWNHeatingEvent("*#4*1*12*0170*3##"))
    assert z1.target_temperature == 17.0
    assert z1._target_temperature is None  # hvac_mode is still None
    assert z1._effective_nominal_temperature() == 17.0

    await dispatcher.process_message(OWNHeatingEvent("*4*111*#1##"))
    assert gateway.has_central_unit is True
    gateway.has_central_unit = False

    # Arrival of *4*111*#1## marks the plant as having a central unit, sets mode to HEAT,
    # and hydrates _target_temperature from _local_target_temperature - _local_offset
    z1.handle_event(OWNHeatingEvent("*4*111*#1##"))
    assert gateway.has_central_unit is True
    assert z1.hvac_mode == HVACMode.HEAT
    assert z1._target_temperature == 17.0

    # 2. Switching Zone 1 to manual HEAT writes *#4*#1*#14*0170*1## using the hydrated setpoint
    await z1.async_set_hvac_mode(HVACMode.HEAT)
    assert str(gateway.send.call_args[0][0]) == "*#4*#1*#14*0170*1##"

    # 3. Adjusting Zone 1 temperature to 21.0 °C writes *#4*#1*#14*0210*3##
    gateway.send.reset_mock()
    await z1.async_set_temperature(temperature=21.0)
    assert str(gateway.send.call_args[0][0]) == "*#4*#1*#14*0210*3##"

    # 4. Returning Zone 1 to 3550 schedule (AUTO) writes *4*311*#1##
    gateway.send.reset_mock()
    await z1.async_set_hvac_mode(HVACMode.AUTO)
    assert str(gateway.send.call_args[0][0]) == "*4*311*#1##"

    # 5. Zone 2 (Eloisa) sequence from diagnostics.json: Dimension 14 (*#4*2*14*0150*3##)
    # arrives while hvac_mode is None, followed by *4*111*#2## without Dimension 12
    z2 = MyHOMEClimate(
        hass=hass,
        name="Eloisa",
        device_id="4-2",
        who="4",
        where="2",
        heating=True,
        cooling=False,
        fan=False,
        standalone=None,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    z2.entity_id = "climate.eloisa"
    z2.async_write_ha_state = MagicMock()
    z2.handle_event(OWNHeatingEvent("*#4*2*0*0194##"))
    z2.handle_event(OWNHeatingEvent("*#4*2*14*0150*3##"))
    assert z2._target_temperature == 15.0
    assert z2._local_target_temperature is None
    z2.handle_event(OWNHeatingEvent("*4*111*#2##"))
    assert z2.hvac_mode == HVACMode.HEAT
    assert z2._local_target_temperature == 15.0
    assert z2.target_temperature == 15.0

async def test_subordinate_zone_hydration_edge_cases(hass: HomeAssistant) -> None:
    """Verify subordinate zone hydration with non-zero offsets, central broadcast, and active guard (#819)."""
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.is_connected = True
    gateway.has_central_unit = True
    gateway.is_standby = False
    gateway.is_secondary = False
    gateway.delegated_away_whos = set()
    gateway.supports_zone_status = True
    gateway.log_id = "[test hydration edge cases]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()

    # 1. Hydration with positive knob offset (+2.0 °C):
    # Dim 13 arrives (+2.0 °C), Dim 12 arrives (19.0 °C local setpoint), then mode becomes HEAT.
    # Hydrated nominal should be 19.0 - 2.0 = 17.0 °C.
    z_pos = MyHOMEClimate(
        hass=hass,
        name="Zone Pos",
        device_id="4-1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=False,
        standalone=None,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    z_pos.entity_id = "climate.zone_pos"
    z_pos.async_write_ha_state = MagicMock()

    z_pos.handle_event(OWNHeatingEvent("*#4*1*13*02##"))  # +2 °C offset
    assert z_pos._local_offset == 2.0
    z_pos.handle_event(OWNHeatingEvent("*#4*1*12*0190*3##"))  # 19.0 °C local
    assert z_pos.target_temperature == 19.0
    assert z_pos._target_temperature is None

    z_pos.handle_event(OWNHeatingEvent("*4*111*#1##"))  # Auto/Heat mode
    assert z_pos.hvac_mode == HVACMode.HEAT
    assert z_pos._target_temperature == 17.0
    assert z_pos.target_temperature == 19.0

    await z_pos.async_set_hvac_mode(HVACMode.HEAT)
    assert str(gateway.send.call_args[0][0]) == "*#4*#1*#14*0170*1##"

    # 2. Hydration with negative knob offset (-1.0 °C):
    # Dim 13 arrives (-1.0 °C), Dim 12 arrives (18.0 °C local setpoint), then mode becomes HEAT.
    # Hydrated nominal should be 18.0 - (-1.0) = 19.0 °C.
    z_neg = MyHOMEClimate(
        hass=hass,
        name="Zone Neg",
        device_id="4-2",
        who="4",
        where="2",
        heating=True,
        cooling=True,
        fan=False,
        standalone=None,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    z_neg.entity_id = "climate.zone_neg"
    z_neg.async_write_ha_state = MagicMock()

    z_neg.handle_event(OWNHeatingEvent("*#4*2*13*11##"))  # -1 °C offset
    assert z_neg._local_offset == -1.0
    z_neg.handle_event(OWNHeatingEvent("*#4*2*12*0180*3##"))  # 18.0 °C local
    assert z_neg._target_temperature is None

    z_neg.handle_event(OWNHeatingEvent("*4*111*#2##"))
    assert z_neg.hvac_mode == HVACMode.HEAT
    assert z_neg._target_temperature == 19.0
    assert z_neg.target_temperature == 18.0

    # 3. Hydration triggered by central unit mode broadcast (_handle_central_mode_update):
    # Zone starts with mode None, Dim 12 (20.0 °C). Central mode update to HEAT triggers hydration.
    z_cu = MyHOMEClimate(
        hass=hass,
        name="Zone CU Follower",
        device_id="4-3",
        who="4",
        where="3",
        heating=True,
        cooling=True,
        fan=False,
        standalone=None,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    z_cu.entity_id = "climate.zone_cu_follower"
    z_cu.async_write_ha_state = MagicMock()

    z_cu.handle_event(OWNHeatingEvent("*#4*3*12*0200*3##"))
    assert z_cu.hvac_mode is None
    assert z_cu._target_temperature is None

    z_cu._handle_central_mode_update(HVACMode.HEAT)
    assert z_cu.hvac_mode == HVACMode.HEAT
    assert z_cu._target_temperature == 20.0

    # 4. Mode change when already active does NOT re-hydrate or overwrite nominal:
    # z_pos is in HEAT with nominal 17.0. Knob moved so local target becomes 22.0.
    # Mode changes to COOL (*4*211*#1##) with prev_mode == HEAT -> nominal stays 17.0.
    z_pos._local_target_temperature = 22.0
    z_pos.handle_event(OWNHeatingEvent("*4*211*#1##"))
    assert z_pos.hvac_mode == HVACMode.COOL
    assert z_pos._target_temperature == 17.0

    # 5. Transition from None to active via MESSAGE_TYPE_SEASON (*4*1*4##):
    z_sea = MyHOMEClimate(
        hass=hass,
        name="Zone Season",
        device_id="4-4",
        who="4",
        where="4",
        heating=True,
        cooling=True,
        fan=False,
        standalone=None,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    z_sea.entity_id = "climate.zone_season"
    z_sea.async_write_ha_state = MagicMock()

    z_sea.handle_event(OWNHeatingEvent("*#4*4*12*0185*3##"))
    assert z_sea._target_temperature is None
    z_sea.handle_event(OWNHeatingEvent("*4*1*4##"))  # Season heating frame
    assert z_sea.hvac_mode == HVACMode.HEAT
    assert z_sea._target_temperature == 18.5

    # 6. Direct validation of _effective_nominal_temperature():
    # Setpoint present:
    assert z_sea._effective_nominal_temperature() == 18.5
    # Target None, local target present, active mode:
    z_sea._target_temperature = None
    assert z_sea._effective_nominal_temperature() == 18.5
    # Target None, local target present, but mode is OFF: protection guard holds
    z_sea._attr_hvac_mode = HVACMode.OFF
    assert z_sea._effective_nominal_temperature() is None
    # Both None:
    z_sea._local_target_temperature = None
    assert z_sea._effective_nominal_temperature() is None


async def test_3550_central_unit_burst_and_diagnostic_edge_cases(hass: HomeAssistant) -> None:
    """Verify all 7 diagnostic WHAT flags simultaneously, season burst reset, holiday mode burst, and non-central isolation (#819)."""
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
    entry.add_to_hass(hass)

    gateway = MagicMock()
    gateway.mac = MAC
    gateway.is_connected = True
    gateway.log_id = "[test 3550 burst edge cases]"
    gateway.name = "MH200N Gateway"
    gateway.hass = hass
    gateway.config_entry = entry
    gateway.device_health = DeviceHealth(gateway)
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()

    cu = MyHOMEClimate(
        hass=hass,
        name="Central Unit 0",
        device_id="4-#0",
        who="4",
        where="#0",
        heating=True,
        cooling=True,
        fan=False,
        standalone=False,
        central=True,
        manufacturer="BTicino",
        model="Central Unit (3550)",
        gateway=gateway,
    )
    cu.entity_id = "climate.central_unit_0"
    cu.async_write_ha_state = MagicMock()

    # 1. Burst with all 7 diagnostic flags simultaneously active:
    burst_all = (
        "*4*1101*#0##",
        "*4*20*#0##",
        "*4*22*#0##",
        "*4*23*#0##",
        "*4*24*#0##",
        "*4*30*#0##",
        "*4*31*#0##",
    )
    for raw in burst_all:
        cu.handle_event(OWNHeatingEvent(raw))

    attrs = cu.extra_state_attributes
    assert attrs["remote_control"] == "disabled"
    assert attrs["at_least_one_probe_off"] is True
    assert attrs["at_least_one_probe_antifreeze"] is True
    assert attrs["at_least_one_probe_manual"] is True
    assert attrs["failure_detected"] is True
    assert attrs["battery_fault"] is True

    # 2. Next burst starts with MESSAGE_TYPE_SEASON (*4*1*#0##):
    cu.handle_event(OWNHeatingEvent("*4*1*#0##"))
    attrs_season = cu.extra_state_attributes
    assert attrs_season["remote_control"] == "disabled"
    assert attrs_season["at_least_one_probe_off"] is False
    assert attrs_season["at_least_one_probe_antifreeze"] is False
    assert attrs_season["at_least_one_probe_manual"] is False
    assert attrs_season["failure_detected"] is False
    assert attrs_season["battery_fault"] is False

    # *4*21*#0## explicitly enables remote control
    cu.handle_event(OWNHeatingEvent("*4*21*#0##"))
    assert cu.extra_state_attributes["remote_control"] == "enabled"

    # 3. Burst starting with MESSAGE_TYPE_MODE_TARGET (*4*1101#10*#0## - holiday mode):
    cu.handle_event(OWNHeatingEvent("*4*31*#0##"))
    assert cu.extra_state_attributes["battery_fault"] is True
    cu.handle_event(OWNHeatingEvent("*4*1101#10*#0##"))
    assert cu.extra_state_attributes["battery_fault"] is False

    # 4. Unknown WHAT code (e.g. WHAT 99): ignored without error
    cu.handle_event(OWNHeatingEvent("*4*99*#0##"))
    assert cu.extra_state_attributes["battery_fault"] is False

    # 5. Non-central zone receiving WHAT 20 / WHAT 31 ignores them completely
    sub_zone = MyHOMEClimate(
        hass=hass,
        name="Zone 1",
        device_id="4-1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=False,
        standalone=None,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    sub_zone.entity_id = "climate.zone_1"
    sub_zone.async_write_ha_state = MagicMock()

    sub_zone.handle_event(OWNHeatingEvent("*4*20*1##"))
    sub_zone.handle_event(OWNHeatingEvent("*4*31*1##"))
    assert "remote_control" not in sub_zone.extra_state_attributes
    assert "battery_fault" not in sub_zone.extra_state_attributes


async def test_is_central_thermo_where_and_routing_edge_cases(hass: HomeAssistant) -> None:
    """Verify is_central_thermo_where grammar rules and GatewayEventDispatcher follow-up probe guards (#819)."""
    from OWNd.message import OWNHeatingCommand

    from custom_components.myhome.gateway_events import GatewayEventDispatcher
    from custom_components.myhome.where_grammar import is_central_thermo_where

    class _Msg:
        def __init__(self, where: str | None) -> None:
            self.where = where

    # 1. Grammar checks: central addresses vs probes vs non-central
    positive = ["#0", "#1", "#99", "#0#1", "#0#2", "#0#3", "#0#4", "#0#4#1", "#1#4#2", "4-#0", "4-#1"]
    for w in positive:
        assert is_central_thermo_where(_Msg(w)) is True, f"Expected True for {w}"

    negative = [
        "#100", "#105", "#201", "4-#105",
        "1", "0", "0#1", "4-1",
        None, "", "#abc", "#",
    ]
    for w in negative:
        assert is_central_thermo_where(_Msg(w)) is False, f"Expected False for {w}"

    assert is_central_thermo_where(object()) is False

    # 2. GatewayEventDispatcher Dimension 14 command follow-up routing:
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.host = "192.168.1.35"
    gateway.is_connected = True
    gateway.has_central_unit = True
    gateway.is_standby = False
    gateway.is_secondary = False
    gateway.delegated_away_whos = set()
    gateway.supports_zone_status = True
    gateway.log_id = "[test dispatcher dim14 follow-up]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    gateway.hass = hass
    gateway.generate_events = False
    gateway._correlate_shared_bus_traffic = MagicMock()
    gateway._bridge_to_primary = MagicMock()
    gateway.health_owner = MagicMock(return_value=None)

    dispatcher = GatewayEventDispatcher(gateway)

    # Probe command: no status request
    await dispatcher.process_message(OWNHeatingCommand("*#4*#105*#14*0210*1##"))
    gateway.send_status_request.assert_not_called()

    # Pump command: no status request
    await dispatcher.process_message(OWNHeatingCommand("*#4*0#1*#14*0210*1##"))
    gateway.send_status_request.assert_not_called()

    # Zone command: status request sent for *#4*1##
    await dispatcher.process_message(OWNHeatingCommand("*#4*#1*#14*0210*1##"))
    assert str(gateway.send_status_request.call_args[0][0]) == "*#4*1##"


