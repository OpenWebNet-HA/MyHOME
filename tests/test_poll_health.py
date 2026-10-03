"""A zone that never answers its status request stops costing the startup queue (#466)."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from homeassistant.helpers import issue_registry as ir
from OWNd.message import OWNHeatingEvent

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.device_health import DeviceHealth, Fault, FaultKind, fault_issue_id
from custom_components.myhome.poll_health import (
    REPROBE_AFTER,
    SKIP_AFTER_FAILED_POLLS,
    PollHealth,
)

NOW = 1_800_000_000.0
ENTRY_ID = "poll_health_entry"


def test_two_failed_polls_make_an_address_unresponsive():
    health = PollHealth()
    assert not health.failed(NOW)
    assert not health.should_skip(NOW)  # one failure is not enough: might be a glitch
    assert health.failed(NOW + 60)  # the second one is
    assert health.should_skip(NOW + 120)


def test_reprobe_after_a_week():
    health = PollHealth()
    for _ in range(SKIP_AFTER_FAILED_POLLS):
        health.failed(NOW)
    assert health.should_skip(NOW + REPROBE_AFTER - 1)
    assert not health.should_skip(NOW + REPROBE_AFTER + 1)


def test_any_frame_clears_the_mark():
    health = PollHealth()
    for _ in range(SKIP_AFTER_FAILED_POLLS):
        health.failed(NOW)
    assert health.frame_seen() is True
    assert not health.should_skip(NOW) and health.attributes() == {}
    assert health.frame_seen() is False


def test_state_attributes_round_trip():
    health = PollHealth()
    for _ in range(SKIP_AFTER_FAILED_POLLS):
        health.failed(NOW)
    restored = PollHealth()
    restored.restore(health.attributes())
    assert restored.should_skip(NOW + 1)
    junk = PollHealth()
    junk.restore({"failed_polls": "x", "unresponsive_since": "y"})
    assert not junk.unresponsive


def _zone(hass, connected=True, where="71", central=False, name=None):
    gateway = MagicMock()
    gateway.mac = "00:03:50:00:04:66"
    gateway.name = "MyHomeServer1 Gateway"
    gateway.log_id = "[poll health]"
    gateway.is_connected = connected
    gateway.send_status_request = AsyncMock()
    gateway.hass = hass
    gateway.config_entry.entry_id = ENTRY_ID
    gateway.device_health = DeviceHealth(gateway)
    model = "Central Unit (3550)" if where == "#0" else ("Central Unit (4695)" if where == "#0#1" else "Heating Zone")
    display_name = name or (f"Central Unit {where}" if central else f"Zone {where}")
    zone = MyHOMEClimate(
        hass=hass, name=display_name, device_id=f"4-{where}", who="4", where=where, heating=True, cooling=False,
        fan=False, standalone=not central, central=central, manufacturer="BTicino", model=model, gateway=gateway,
    )
    zone.hass = hass
    zone.entity_id = f"climate.zone_{where.replace('#', '')}"
    zone.async_write_ha_state = MagicMock()
    zone._publish_state = MagicMock()
    return zone, gateway


def _issue(hass, zone):
    issue_id = fault_issue_id(ENTRY_ID, FaultKind.UNRESPONSIVE, 4, zone._full_where)
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


async def _poll(hass, zone, gateway, outcome):
    """Run one startup poll whose request ends as ``outcome`` ('nack' or 'ack')."""
    written = asyncio.get_running_loop().create_future()
    gateway.send_status_request.return_value = written
    await zone.async_update()
    written.cancel() if outcome == "nack" else written.set_result(1.0)
    await asyncio.sleep(0)


async def test_two_unanswered_startups_skip_the_third_and_raise_a_repair(hass):
    zone, gateway = _zone(hass)
    await _poll(hass, zone, gateway, "nack")
    assert _issue(hass, zone) is None  # one miss: keep asking
    await _poll(hass, zone, gateway, "nack")
    issue = _issue(hass, zone)
    assert issue is not None
    assert issue.translation_key == "unresponsive_zone"
    assert issue.translation_placeholders["device"] == "Zone 71"
    assert issue.translation_placeholders["gateway"] == "MyHomeServer1 Gateway"
    assert zone.extra_state_attributes["failed_polls"] == 2

    gateway.send_status_request.reset_mock()
    await zone.async_update()
    gateway.send_status_request.assert_not_called()  # skipped: no queue time


async def test_a_frame_from_the_zone_recovers_it(hass):
    zone, gateway = _zone(hass)
    await _poll(hass, zone, gateway, "nack")
    await _poll(hass, zone, gateway, "nack")
    zone.handle_event(OWNHeatingEvent("*#4*71*0*0210##"))
    assert _issue(hass, zone) is None
    assert "failed_polls" not in zone.extra_state_attributes
    await zone.async_update()
    gateway.send_status_request.assert_called()


async def test_an_answered_poll_resets_the_count(hass):
    zone, gateway = _zone(hass)
    await _poll(hass, zone, gateway, "nack")
    await _poll(hass, zone, gateway, "ack")
    await _poll(hass, zone, gateway, "nack")
    assert _issue(hass, zone) is None


async def test_a_disconnected_gateway_is_not_the_zones_fault(hass):
    zone, gateway = _zone(hass, connected=False)
    for _ in range(3):
        await _poll(hass, zone, gateway, "nack")
    assert _issue(hass, zone) is None and "failed_polls" not in zone.extra_state_attributes


async def test_frames_during_the_request_mean_the_zone_answered(hass):
    zone, gateway = _zone(hass)
    written = asyncio.get_running_loop().create_future()
    gateway.send_status_request.return_value = written
    await zone.async_update()
    zone.handle_event(OWNHeatingEvent("*#4*71*0*0210##"))
    written.cancel()  # a later timeout after data arrived
    await asyncio.sleep(0)
    assert "failed_polls" not in zone.extra_state_attributes


async def test_restored_unresponsive_zone_is_skipped_at_startup(hass):
    zone, gateway = _zone(hass)
    state = MagicMock()
    state.state = "off"
    state.attributes = {"failed_polls": 2, "unresponsive_since": time.time() - 3600}
    await zone.async_restore_last_state(state)
    await zone.async_update()
    gateway.send_status_request.assert_not_called()
    assert _issue(hass, zone) is not None


async def test_a_reprobe_that_is_answered_clears_the_repair(hass):
    zone, gateway = _zone(hass)
    await _poll(hass, zone, gateway, "nack")
    await _poll(hass, zone, gateway, "nack")
    assert _issue(hass, zone) is not None
    zone._poll_health.since = time.time() - 8 * 24 * 3600  # re-probe is due
    await _poll(hass, zone, gateway, "ack")
    assert _issue(hass, zone) is None
    assert "failed_polls" not in zone.extra_state_attributes
    zone._publish_state.assert_called()


async def test_removing_the_entity_drops_its_repair(hass):
    zone, gateway = _zone(hass)
    await _poll(hass, zone, gateway, "nack")
    await _poll(hass, zone, gateway, "nack")
    assert _issue(hass, zone) is not None
    await zone.async_removed_from_registry()  # the owner deleted the entity
    assert _issue(hass, zone) is None


async def test_central_unit_exempt_from_status_poll_and_poll_health(hass):
    """Central units (#0, #0#1) do not answer Dimension 14 status queries (#582)."""
    central, gateway = _zone(hass, where="#0", central=True, name="Centrale termoregolazione")
    await central.async_update()
    gateway.send_status_request.assert_not_called()
    assert "failed_polls" not in central.extra_state_attributes
    assert "unresponsive_since" not in central.extra_state_attributes
    assert _issue(hass, central) is None


async def test_central_unit_restores_cleanly_and_clears_stale_repair(hass):
    """Upgrading from v2.0.0b14 auto-clears any false-positive unresponsive repairs for central units (#582)."""
    central, gateway = _zone(hass, where="#0", central=True, name="Centrale termoregolazione")

    # 1. Stale repair issue exists from an earlier run
    gateway.device_health.report(Fault(4, "#0", FaultKind.UNRESPONSIVE))
    assert _issue(hass, central) is not None

    # 2. State restored with failed_polls: 2
    state = MagicMock()
    state.state = "heat"
    state.attributes = {"failed_polls": 2, "unresponsive_since": time.time() - 3600}
    await central.async_restore_last_state(state)

    # Issue must be automatically dropped and attributes kept clean
    assert _issue(hass, central) is None
    assert "failed_polls" not in central.extra_state_attributes

    # 3. async_added_to_hass also guarantees issue cleanup
    gateway.device_health.report(Fault(4, "#0", FaultKind.UNRESPONSIVE))
    assert _issue(hass, central) is not None
    await central.async_added_to_hass()
    assert _issue(hass, central) is None


async def test_four_zone_central_unit_exempt_from_poll_health(hass):
    """Four-zone central units (#0#1) are also exempt from status polling and PollHealth (#582)."""
    cu4, gateway = _zone(hass, where="#0#1", central=True, name="Central Unit 4-Zone")
    await cu4.async_update()
    gateway.send_status_request.assert_not_called()
    assert "failed_polls" not in cu4.extra_state_attributes
    assert _issue(hass, cu4) is None

