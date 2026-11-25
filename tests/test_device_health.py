"""Device faults seen on the bus become self-clearing repair issues (device_health.py).

Frames are from the MH200 capture EVID-MH200-WHAT19-FAULT: an actuator in a fault
state answers its status request with ``*1*19*74##`` (WHAT 19 is outside the
published WHO 1 table), with a WHO 1001 DIMENSION 11 mask 3.45 s before it.
"""
import json
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from OWNd.message import OWNEvent
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.myhome import device_health as dh
from custom_components.myhome.const import CONF_IGNORED_ADDRESSES, DOMAIN
from custom_components.myhome.device_health import DeviceHealth, Fault, FaultKind, fault_issue_id
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_events import GatewayEventDispatcher
from custom_components.myhome.light import MyHOMELight
from custom_components.myhome.sensor import MyHOMETemperatureSensor

ENTRY_ID = "health_entry"
FAULT = "*1*99*74##"
MASK = "*#1001*74*11*111110111111111111110111##"
OFF = "*1*0*74##"
ISSUE_74 = fault_issue_id(ENTRY_ID, FaultKind.UNMAPPED_STATUS, 1, "74")


def _health(hass, ignored_addresses=None):
    options = {}
    if ignored_addresses is not None:
        options[CONF_IGNORED_ADDRESSES] = ignored_addresses
    handler = SimpleNamespace(
        hass=hass,
        config_entry=SimpleNamespace(entry_id=ENTRY_ID, options=options, data={}),
        name="MH200 Gateway",
        log_id="[health]",
    )
    return DeviceHealth(handler)


def _issue(hass, issue_id=ISSUE_74):
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


def _frame(raw):
    return OWNEvent.parse(raw)


async def _settle_burst(hass):
    """Let the central unit's status reply settle (device_health.CENTRAL_FLAGS_SETTLE)."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=dh.CENTRAL_FLAGS_SETTLE + 0.1))
    await hass.async_block_till_done()


async def test_unmapped_lighting_status_raises_an_issue_for_an_unconfigured_address(hass):
    health = _health(hass)
    health.observe(_frame(FAULT))

    issue = _issue(hass)
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.translation_key == "unmapped_device_status"
    assert issue.translation_placeholders == {
        "device": "WHO 1 WHERE 74",  # no entity names the address
        "gateway": "MH200 Gateway",
        "who": "1",
        "where": "74",
        "code": "99",
        "evidence": "",
    }
    assert issue.learn_more_url == (
        "https://openwebnet-ha.github.io/MyHOME/latest/diagnostics/repair-issues/#unmapped-device-status"
    )
    assert health.faults == [{"who": 1, "where": "74", "kind": "unmapped_status", "code": "99", "evidence": ""}]


async def test_a_stuck_actuator_does_not_rewrite_the_issue_on_every_poll(hass):
    health = _health(hass)
    with patch.object(dh, "async_create_issue", wraps=dh.async_create_issue) as create:
        for _ in range(3):
            health.observe(_frame(FAULT))
    assert create.call_count == 1


async def test_a_normal_status_clears_the_issue(hass):
    health = _health(hass)
    health.observe(_frame(FAULT))
    health.observe(_frame(OFF))
    health.observe(_frame(OFF))
    assert _issue(hass) is None
    assert health.faults == []


async def test_a_normal_status_clears_an_issue_the_tracker_does_not_hold(hass):
    """Raised by an earlier tracker (a frame racing the unload) or a standby that stopped listening."""
    _health(hass).observe(_frame(FAULT))
    assert _issue(hass) is not None

    fresh = _health(hass)  # new tracker, same entry: knows nothing of the issue
    fresh.observe(_frame(OFF))
    assert _issue(hass) is None


async def test_the_autodiagnostic_mask_is_attached_raw_to_the_fault_it_follows(hass):
    health = _health(hass)
    health.observe(_frame(FAULT))
    health.observe(_frame(MASK))

    issue = _issue(hass)
    assert issue.translation_key == "unmapped_device_status_autodiag"
    assert issue.translation_placeholders["evidence"] == MASK

    # the next poll answers the same code without a new mask: the evidence stays
    health.observe(_frame(FAULT))
    assert _issue(hass).translation_placeholders["evidence"] == MASK


async def test_a_recent_mask_is_attached_to_a_fault_that_follows_it(hass):
    health = _health(hass)
    health.observe(_frame(MASK))
    health.observe(_frame(FAULT))
    assert _issue(hass).translation_placeholders["evidence"] == MASK


async def test_a_stale_mask_is_not_attached(hass):
    health = _health(hass)
    with patch.object(dh.time, "monotonic", return_value=1000.0):
        health.observe(_frame(MASK))
    with patch.object(dh.time, "monotonic", return_value=1000.0 + dh.EVIDENCE_WINDOW + 1):
        health.observe(_frame(FAULT))
    assert _issue(hass).translation_placeholders["evidence"] == ""


async def test_a_mask_long_after_the_last_odd_status_is_not_attached(hass):
    """The window holds in both directions."""
    health = _health(hass)
    with patch.object(dh.time, "monotonic", return_value=1000.0):
        health.observe(_frame(FAULT))
    with patch.object(dh.time, "monotonic", return_value=1000.0 + dh.EVIDENCE_WINDOW + 1):
        health.observe(_frame(MASK))
    assert _issue(hass).translation_placeholders["evidence"] == ""
    assert health.faults[0]["evidence"] == ""

    # the actuator answers its next poll with the same code: the fresh mask belongs to it
    with patch.object(dh.time, "monotonic", return_value=1000.0 + dh.EVIDENCE_WINDOW + 2):
        health.observe(_frame(FAULT))
    assert _issue(hass).translation_placeholders["evidence"] == MASK


async def test_a_mask_alone_raises_nothing(hass):
    """Its bits are undocumented: it is evidence, never a trigger."""
    health = _health(hass)
    health.observe(_frame(MASK))
    health.observe(_frame("*#1001*74*7*111110111111111111110111##"))
    assert _issue(hass) is None
    assert health.faults == []


async def test_an_address_behind_an_f422_keeps_its_interface(hass):
    health = _health(hass)
    health.observe(_frame("*1*99*74#4#01##"))
    issue = _issue(hass, fault_issue_id(ENTRY_ID, FaultKind.UNMAPPED_STATUS, 1, "74#4#01"))
    assert issue is not None
    assert issue.issue_id.endswith("_unmapped_status_1_74_4_01")
    assert issue.translation_placeholders["where"] == "74#4#01"
    assert _issue(hass) is None  # not the local-bus 74


async def test_scope_frames_are_not_device_faults(hass):
    health = _health(hass)
    health.observe(_frame(FAULT))
    for raw in ("*1*99*0##", "*1*99*7##", "*1*99*#5##", "*1*0*0##"):
        health.observe(_frame(raw))
    assert health.faults == [{"who": 1, "where": "74", "kind": "unmapped_status", "code": "99", "evidence": ""}]


async def test_naming_the_address_renames_the_issue_and_forgetting_it_drops_it(hass):
    health = _health(hass)
    health.observe(_frame(FAULT))
    health.name_address("1", "74", "Hall")
    assert _issue(hass).translation_placeholders["device"] == "Hall"
    with patch.object(dh, "async_create_issue") as create:
        health.name_address(1, "74", "Hall")  # same name: nothing to rewrite
    create.assert_not_called()

    health.forget_address(1, "74")
    assert _issue(hass) is None
    assert health.faults == []


async def test_the_issue_stays_while_another_entity_uses_the_address(hass):
    health = _health(hass)
    health.observe(_frame(FAULT))
    health.name_address(1, "74", "Hall", owner="light-uid")
    health.name_address(1, "74", "Hall", owner="sensor-uid")

    health.forget_address(1, "74", owner="sensor-uid")
    assert _issue(hass) is not None

    health.forget_address(1, "74", owner="light-uid")
    assert _issue(hass) is None


async def test_entity_reports_share_the_tracker_and_clear_all_drops_everything(hass):
    health = _health(hass)
    health.report(Fault(4, "71", FaultKind.UNRESPONSIVE), device="Zone 71")
    health.observe(_frame(FAULT))
    zone = _issue(hass, fault_issue_id(ENTRY_ID, FaultKind.UNRESPONSIVE, 4, "71"))
    assert zone.translation_key == "unresponsive_zone"
    assert zone.translation_placeholders["device"] == "Zone 71"
    assert zone.translation_placeholders["zone"] == "Zone 71"  # a translation may still use {zone}
    assert zone.learn_more_url.endswith("#heating-zone-no-longer-answers")

    health.clear_all()
    assert _issue(hass) is None
    assert _issue(hass, zone.issue_id) is None


async def test_an_unload_keeps_the_issue_the_owner_ignored(hass):
    """The entry's unload withdraws the issues, except one whose deletion would drop an "Ignore"."""
    health = _health(hass)
    health.report(Fault(4, "71", FaultKind.UNRESPONSIVE), device="Zone 71")
    health.observe(_frame(FAULT))
    zone_id = fault_issue_id(ENTRY_ID, FaultKind.UNRESPONSIVE, 4, "71")
    ir.async_get(hass).async_ignore(DOMAIN, ISSUE_74, True)

    health.clear_all(keep_ignored=True)
    assert _issue(hass, zone_id) is None
    assert _issue(hass).dismissed_version is not None
    assert health.faults == []

    # After the reload: the same fault raises the issue in place, still ignored ...
    reloaded = _health(hass)
    reloaded.observe(_frame(FAULT))
    assert _issue(hass).dismissed_version is not None
    assert reloaded.faults != []
    # ... and the recovery clears it, as for any issue the tracker does not hold.
    _health(hass).observe(_frame(OFF))
    assert _issue(hass) is None


async def test_no_issue_without_a_config_entry(hass):
    handler = SimpleNamespace(hass=hass, config_entry=None, name="gw", log_id="")
    health = DeviceHealth(handler)
    health.observe(_frame(FAULT))  # tracked, but nowhere to file it
    assert health.faults and not [
        issue for (domain, _), issue in ir.async_get(hass).issues.items() if domain == DOMAIN
    ]


def _dispatcher(hass, delegated_away=(), entry_id=ENTRY_ID, mac="00:03:50:00:00:74"):
    handler = MagicMock()
    handler.hass = hass
    handler.mac = mac
    handler.log_id = "[health]"
    handler.name = "MH200 Gateway"
    handler.generate_events = False
    handler.is_standby = False
    handler.is_secondary = False
    handler.delegated_away_whos = set(delegated_away)
    handler.config_entry = SimpleNamespace(entry_id=entry_id)
    handler.send_status_request = AsyncMock()
    handler.device_health = DeviceHealth(handler)
    handler.health_owner = lambda: MyHOMEGatewayHandler.health_owner(handler)
    return GatewayEventDispatcher(handler)


async def test_the_gateway_feeds_every_frame_to_the_tracker(hass):
    dispatcher = _dispatcher(hass)
    await dispatcher.process_message(_frame(FAULT))
    await dispatcher.process_message(_frame(MASK))
    assert _issue(hass).translation_placeholders["evidence"] == MASK


async def test_a_primary_that_delegated_lighting_raises_no_lighting_faults(hass):
    """On a shared bus the secondary owning WHO 1 raises it; WHO 1001 follows WHO 1."""
    dispatcher = _dispatcher(hass, delegated_away={1})
    await dispatcher.process_message(_frame(FAULT))
    await dispatcher.process_message(_frame(MASK))
    assert _issue(hass) is None
    assert dispatcher.handler.device_health.faults == []


def _light(hass, health_handler):
    light = MyHOMELight(
        hass=hass, name="Hall", entity_name="Hall", icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on",
        device_id="74", who="1", where="74", interface=None, dimmable=False,
        manufacturer="B", model="M", gateway=health_handler,
    )
    light.hass = hass
    light.entity_id = "light.hall"
    return light


async def test_removing_the_entity_drops_the_issue_of_its_address(hass):
    dispatcher = _dispatcher(hass)
    health = dispatcher.handler.device_health
    light = _light(hass, dispatcher.handler)
    await dispatcher.process_message(_frame(FAULT))
    health.name_address(*light._health_address, light._device_name, owner=light._health_owner)
    assert _issue(hass).translation_placeholders["device"] == "Hall"

    # a reload, or an entity_id rename (the old object goes while the registry still holds
    # the entity): the fault is still there, so is its issue
    await light.async_will_remove_from_hass()
    assert _issue(hass) is not None

    await light.async_removed_from_registry()  # the owner deleted the entity
    assert _issue(hass) is None


async def test_a_failing_tracker_does_not_cost_the_frame_its_handling(hass, caplog):
    dispatcher = _dispatcher(hass)
    with patch.object(DeviceHealth, "observe", side_effect=RuntimeError("boom")):
        await dispatcher.process_message(_frame(FAULT))
    assert "Device health could not process" in caplog.text


async def test_only_lighting_frames_reach_the_tracker(hass):
    dispatcher = _dispatcher(hass)
    with patch.object(DeviceHealth, "observe") as observe:
        await dispatcher.process_message(_frame("*2*1*51##"))
    observe.assert_not_called()


def _gateways(primary_connected):
    """A primary and its warm standby, each with its own tracker."""
    primary = SimpleNamespace(is_standby=False, is_connected=primary_connected, device_health=object())
    standby = SimpleNamespace(is_standby=True, device_health=object(), _get_primary_gateway=lambda: primary)
    primary._get_standby_gateway = lambda: standby
    return primary, standby


async def test_a_standby_carrying_an_offline_primary_files_faults_under_the_primary(hass):
    primary, standby = _gateways(primary_connected=False)
    assert MyHOMEGatewayHandler.health_owner(standby) is primary.device_health


async def test_a_standby_files_nothing_once_the_primary_is_back(hass):
    primary, standby = _gateways(primary_connected=True)
    assert MyHOMEGatewayHandler.health_owner(standby) is None
    # from here on the primary's own tracker sees the recovery
    assert MyHOMEGatewayHandler.health_owner(primary) is primary.device_health


async def test_a_standby_without_its_primary_files_nothing(hass):
    primary, standby = _gateways(primary_connected=False)
    standby._get_primary_gateway = lambda: None
    assert MyHOMEGatewayHandler.health_owner(standby) is None
    standby._get_primary_gateway = lambda: primary
    primary._get_standby_gateway = lambda: object()  # somebody else's standby
    assert MyHOMEGatewayHandler.health_owner(standby) is None


def _failover_pair(hass):
    """A primary and its warm standby, each with its own entry, dispatcher and tracker."""
    primary = _dispatcher(hass, entry_id="primary_entry", mac="00:03:50:00:00:01")
    standby = _dispatcher(hass, entry_id="standby_entry", mac="00:03:50:00:00:02")
    standby.handler.is_standby = True
    standby.handler._get_primary_gateway = lambda: primary.handler
    standby.handler._profile_supports_who = lambda who: True
    primary.handler._get_standby_gateway = lambda: standby.handler
    return primary, standby


def _owned_issue(hass, entry_id):
    return _issue(hass, fault_issue_id(entry_id, FaultKind.UNMAPPED_STATUS, 1, "74"))


async def test_a_recovery_seen_by_the_standby_during_a_failover_clears_the_primarys_issue(hass):
    primary, standby = _failover_pair(hass)
    primary.handler.is_connected = False

    await standby.process_message(_frame(FAULT))
    assert _owned_issue(hass, "primary_entry") is not None
    assert _owned_issue(hass, "standby_entry") is None
    assert standby.handler.device_health.faults == []

    await standby.process_message(_frame(OFF))
    assert _owned_issue(hass, "primary_entry") is None


async def test_a_fault_raised_during_a_failover_clears_on_the_primary_after_the_failback(hass):
    primary, standby = _failover_pair(hass)
    primary.handler.is_connected = False
    await standby.process_message(_frame(FAULT))

    primary.handler.is_connected = True  # failback: the standby stops filing
    await standby.process_message(_frame(OFF))
    assert _owned_issue(hass, "primary_entry") is not None

    await primary.process_message(_frame(OFF))
    assert _owned_issue(hass, "primary_entry") is None
    assert _owned_issue(hass, "standby_entry") is None


async def test_frames_without_a_point_address_or_a_binary_mask_are_ignored(hass):
    health = _health(hass)
    health.observe(SimpleNamespace(who=1, where=None, unknown_state=99, is_on=None))
    health.observe(SimpleNamespace(who=1, where="74", unknown_state=32, is_on=None))
    health.observe(SimpleNamespace(who=1001, where="74", dimension=99, dimension_value=["01"]))
    health.observe(SimpleNamespace(who=1001, where="74", dimension=11, dimension_value=[]))
    health.observe(SimpleNamespace(who=1001, where="74", dimension=11, dimension_value=["1201"]))
    health.observe(_frame(FAULT))
    assert _issue(hass).translation_placeholders["evidence"] == ""


async def test_an_entity_without_a_numeric_who_files_no_fault(hass):
    dispatcher = _dispatcher(hass)
    light = _light(hass, dispatcher.handler)
    light._who = "light"
    assert light._health_address is None
    light._report_fault(FaultKind.UNMAPPED_STATUS, "99")
    assert dispatcher.handler.device_health.faults == []


async def test_sub_entity_added_to_hass_preserves_device_name_on_issue(hass):
    dispatcher = _dispatcher(hass)
    dispatcher.handler.availability_signal = "myhome_avail"
    health = dispatcher.handler.device_health

    # Climate zone reports an unresponsive issue for device "Heating zone Zone 71"
    health.report(Fault(4, "71", FaultKind.UNRESPONSIVE), device="Heating zone Zone 71")
    zone_issue = _issue(hass, fault_issue_id(ENTRY_ID, FaultKind.UNRESPONSIVE, 4, "71"))
    assert zone_issue.translation_placeholders["device"] == "Heating zone Zone 71"

    # A temperature sensor sharing address (4, "71") is added to hass
    sensor = MyHOMETemperatureSensor(
        hass=hass,
        name="Heating zone Zone 71",
        device_id="4-71",
        who="4",
        where="71",
        device_class=SensorDeviceClass.TEMPERATURE,
        manufacturer="BTicino",
        model="Probe",
        gateway=dispatcher.handler,
    )
    sensor.entity_id = "sensor.heating_zone_zone_71_temperature"
    assert sensor._device_name == "Heating zone Zone 71"
    assert sensor._display_name == "Heating zone Zone 71 Temperature"

    await sensor.async_added_to_hass()

    # The issue describes the device, so name_address() passes _device_name
    # and keeps "Heating zone Zone 71", without renaming to the sensor's display name
    updated_issue = _issue(hass, fault_issue_id(ENTRY_ID, FaultKind.UNRESPONSIVE, 4, "71"))
    assert updated_issue.translation_placeholders["device"] == "Heating zone Zone 71"


async def test_ignored_address_suppresses_actuator_fault(hass):
    """An ignored address emits no repair issues on abnormal status."""
    health = _health(hass, ignored_addresses=["1/74"])
    health.observe(_frame(FAULT))
    assert _issue(hass) is None
    assert health.faults == []


async def test_ignored_address_suppresses_autodiag(hass):
    """An ignored address suppresses both autodiagnostic frame tracking and fault reporting."""
    health = _health(hass, ignored_addresses=["1/74"])
    health.observe(_frame(MASK))
    health.observe(_frame(FAULT))
    assert _issue(hass) is None
    assert health.faults == []


async def test_ignored_address_cleans_preexisting_issues_at_startup(hass):
    """Configuring an address as ignored removes pre-existing repair issues on startup."""
    normal_health = _health(hass)
    normal_health.observe(_frame(FAULT))
    assert _issue(hass) is not None

    # Gateway reloads with 1/74 in ignored_addresses
    _ignored_health = _health(hass, ignored_addresses=["1/74"])
    assert _issue(hass) is None

    # Pre-existing routed issue on 74#4#01 is isolated from bare 1/74, and cleaned by 1/74#4#1
    routed_issue_id = fault_issue_id(ENTRY_ID, FaultKind.UNMAPPED_STATUS, 1, "74#4#01")
    normal_health.observe(_frame("*1*99*74#4#01##"))
    assert _issue(hass, routed_issue_id) is not None

    _ignored_main_only = _health(hass, ignored_addresses=["1/74"])
    assert _issue(hass, routed_issue_id) is not None

    _ignored_routed = _health(hass, ignored_addresses=["1/74#4#1"])
    assert _issue(hass, routed_issue_id) is None

    # Pre-existing issue on 74 is cleaned by zero-padded 1/074
    _health(hass).observe(_frame(FAULT))
    assert _issue(hass) is not None

    _ignored_padded = _health(hass, ignored_addresses=["1/074"])
    assert _issue(hass) is None


async def test_ignored_address_suppresses_name_address_and_report(hass):
    """name_address and direct report calls do nothing for ignored addresses."""
    health = _health(hass, ignored_addresses=["1/74"])
    health.name_address(1, "74", "Ignored Lamp")
    assert (1, "74") not in health._names

    fault = Fault(1, "74", FaultKind.UNMAPPED_STATUS, "99")
    health.report(fault)
    assert _issue(hass) is None
    assert health.faults == []
    assert health.is_ignored("invalid_who", "74") is False


async def test_what_19_no_load_does_not_raise_an_issue(hass):
    """WHAT 19 (dimmer no-load / open circuit) is a recognized state: raises no issue and clears prior fault."""
    health = _health(hass)
    # Prior unmapped anomaly on 74
    health.observe(_frame("*1*99*74##"))
    assert _issue(hass) is not None
    assert len(health.faults) == 1

    # WHAT 19 clears the anomaly and raises no new fault
    health.observe(_frame("*1*19*74##"))
    assert _issue(hass) is None
    assert health.faults == []


async def test_central_unit_remote_control_disabled_and_battery_fault_lifecycle(hass):
    """WHO 4 #0 diagnostic frames raise and self-clear CENTRAL_REMOTE_CONTROL_DISABLED and CENTRAL_BATTERY_FAULT (#819)."""
    dispatcher = _dispatcher(hass)
    health = dispatcher.handler.device_health

    rc_issue_id = fault_issue_id(ENTRY_ID, FaultKind.CENTRAL_REMOTE_CONTROL_DISABLED, 4, "#0")
    bat_issue_id = fault_issue_id(ENTRY_ID, FaultKind.CENTRAL_BATTERY_FAULT, 4, "#0")

    # 0. WHO 4 dimension frames and non-#0 zone frames are ignored by DeviceHealth._observe_heating
    await dispatcher.process_message(_frame("*#4*1*0*0210##"))
    await dispatcher.process_message(_frame("*4*111*#1##"))
    assert health.faults == []

    # 1. *4*20*#0## raises central_remote_control_disabled; *4*31*#0## raises central_battery_fault
    await dispatcher.process_message(_frame("*4*1101*#0##"))
    await dispatcher.process_message(_frame("*4*20*#0##"))
    await dispatcher.process_message(_frame("*4*31*#0##"))

    rc_issue = _issue(hass, rc_issue_id)
    bat_issue = _issue(hass, bat_issue_id)
    assert rc_issue is not None
    assert rc_issue.translation_key == "central_remote_control_disabled"
    assert rc_issue.translation_placeholders["device"] == "WHO 4 WHERE #0"
    assert bat_issue is not None
    assert bat_issue.translation_key == "central_battery_fault"

    # 2. Reporting the same fault with a device name updates the existing issue's device placeholder
    health.report(
        Fault(4, "#0", FaultKind.CENTRAL_REMOTE_CONTROL_DISABLED, "20"),
        device="Central Unit 0",
    )
    assert _issue(hass, rc_issue_id).translation_placeholders["device"] == "Central Unit 0"
    assert _issue(hass, bat_issue_id).translation_placeholders["device"] == "Central Unit 0"

    # 3. Next #0 status burst without *4*31*#0## and with *4*21*#0## clears both issues: the
    # remote-control one at once, the battery one when the burst has settled without a 31
    # (the mode frame that opens the burst says nothing about the battery).
    await _settle_burst(hass)
    await dispatcher.process_message(_frame("*4*1101*#0##"))
    assert _issue(hass, bat_issue_id) is not None
    assert _issue(hass, rc_issue_id) is not None

    await dispatcher.process_message(_frame("*4*21*#0##"))
    assert _issue(hass, rc_issue_id) is None
    assert _issue(hass, bat_issue_id) is not None
    await _settle_burst(hass)
    assert _issue(hass, bat_issue_id) is None
    assert health.faults == []


CENTRAL_FLAGS_TRACE = json.loads(
    (
        Path(__file__).parent / "fixtures" / "traces" / "central_flags_f455_poll_2026_11_09" / "frames.json"
    ).read_text()
)
BATTERY_ISSUE = fault_issue_id(ENTRY_ID, FaultKind.CENTRAL_BATTERY_FAULT, 4, "#0")


def _central_reply(window, gateway):
    return [raw for _t, gw, raw in CENTRAL_FLAGS_TRACE[window] if gw == gateway]


@pytest.mark.parametrize(
    ("window", "gateway"),
    [
        ("central_reply_via_f461_0747", "f461"),
        ("central_reply_via_f461_0747", "f455"),
        ("central_reply_via_f455_0748", "f461"),
        ("central_reply_via_f455_0748", "f455"),
    ],
)
async def test_an_ignored_battery_issue_stays_ignored_through_every_central_reply(hass, window, gateway):
    """A 3550 without a battery repeats ``*4*31*#0##`` in every reply (live F461 + F455, 2026-11-09).

    The mode frame that opens the reply used to delete the issue, and the 31 that followed
    4 ms later created it again as a new, un-ignored one: nine to ten times an hour.
    """
    frames = _central_reply(window, gateway)
    assert frames == ["*4*210#0240*#0##", "*4*21*#0##", "*4*31*#0##"]
    dispatcher = _dispatcher(hass)
    for raw in frames:
        await dispatcher.process_message(_frame(raw))
    await _settle_burst(hass)
    registry = ir.async_get(hass)
    registry.async_ignore(DOMAIN, BATTERY_ISSUE, True)
    ignored = _issue(hass, BATTERY_ISSUE)
    assert ignored.dismissed_version is not None

    with (
        patch.object(dh, "async_create_issue", wraps=dh.async_create_issue) as create,
        patch.object(dh, "async_delete_issue", wraps=dh.async_delete_issue) as delete,
    ):
        for _ in range(3):
            # each frame twice: the reply on the command session and its copy on the event session
            for raw in frames:
                await dispatcher.process_message(_frame(raw))
                await dispatcher.process_message(_frame(raw))
            await _settle_burst(hass)
    assert _issue(hass, BATTERY_ISSUE) is ignored  # the same registry row: never deleted, never rewritten
    assert create.call_count == 0
    assert BATTERY_ISSUE not in [call.args[2] for call in delete.call_args_list]


async def test_the_battery_issue_clears_once_a_reply_settles_without_the_flag(hass):
    dispatcher = _dispatcher(hass)
    for raw in _central_reply("central_reply_via_f461_0747", "f461"):
        await dispatcher.process_message(_frame(raw))
    await _settle_burst(hass)
    assert _issue(hass, BATTERY_ISSUE) is not None

    # The battery was replaced: the next reply is the mode and the remote-control frame only
    for raw in _central_reply("central_reply_via_f461_0747", "f461")[:2]:
        await dispatcher.process_message(_frame(raw))
    assert _issue(hass, BATTERY_ISSUE) is not None  # not before the flags had their time
    await _settle_burst(hass)
    assert _issue(hass, BATTERY_ISSUE) is None
    assert dispatcher.handler.device_health.faults == []


async def test_the_echo_of_a_central_command_does_not_clear_the_battery_issue(hass):
    """An F455 hands Home Assistant's own ``*4*0*#0##`` back on its event session (live, 2026-11-08).

    At the second press the 3550 had nothing new to report: the echo was the only frame, and
    as a mode frame it deleted the issue of a fault that was still there.
    """
    trace = json.loads(
        (Path(__file__).parent / "fixtures" / "traces" / "climate_mode_2026_11_08" / "frames.json").read_text()
    )
    first = [raw for _t, direction, raw in trace["f455_central_cool"] if direction == "rx"]
    second = [raw for _t, direction, raw in trace["f455_central_cool_second_press"] if direction == "rx"]
    assert first == ["*4*0*#0##", "*4*202*#0##", "*4*21*#0##", "*4*31*#0##"]
    assert second == ["*4*0*#0##"]
    dispatcher = _dispatcher(hass)
    for raw in first:
        await dispatcher.process_message(_frame(raw))
    await _settle_burst(hass)
    assert _issue(hass, BATTERY_ISSUE) is not None
    for raw in second:
        await dispatcher.process_message(_frame(raw))
    await _settle_burst(hass)
    assert _issue(hass, BATTERY_ISSUE) is not None


def _restart_stub(hass, issue_id):
    """Leave of an ignored, non-persistent issue what ``issue_registry.async_load`` leaves after a restart."""
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    # Use dataclasses.replace so fields added to IssueEntry in newer Home Assistant
    # releases (config_entry_id in 2026.11+, config_subentry_id in 2026.12+) are preserved.
    registry.issues[(DOMAIN, issue_id)] = replace(
        issue,
        active=False,
        breaks_in_ha_version=None,
        data=None,
        is_fixable=None,
        is_persistent=False,
        issue_domain=None,
        learn_more_url=None,
        severity=None,
        translation_key=None,
        translation_placeholders=None,
    )
    # A new Home Assistant run starts with an empty ``hass.data``
    hass.data.pop(dh.DATA_TRACKERS_STARTED, None)


async def test_an_ignored_battery_issue_is_shown_again_after_a_restart(hass):
    """Decision of 2026-11-18: "Ignore" on the battery repair holds until Home Assistant restarts."""
    dispatcher = _dispatcher(hass)
    frames = _central_reply("central_reply_via_f461_0747", "f461")
    for raw in frames:
        await dispatcher.process_message(_frame(raw))
    await _settle_burst(hass)
    ir.async_get(hass).async_ignore(DOMAIN, BATTERY_ISSUE, True)
    assert _issue(hass, BATTERY_ISSUE).dismissed_version is not None
    dispatcher.handler.device_health.clear_all(keep_ignored=True)  # cancels the open cycle, if any
    _restart_stub(hass, BATTERY_ISSUE)

    restarted = _dispatcher(hass)
    # The first tracker of the run forgets the "Ignore" (the stub is gone) ...
    assert _issue(hass, BATTERY_ISSUE) is None
    for raw in frames:
        await restarted.process_message(_frame(raw))
    await _settle_burst(hass)
    # ... and the first reply that still carries *4*31*#0## shows the repair again.
    issue = _issue(hass, BATTERY_ISSUE)
    assert issue.active is True
    assert issue.dismissed_version is None
    assert issue.translation_key == "central_battery_fault"

    # Ignored again, it holds through a reload of the same run (the entry is known to hass.data).
    ir.async_get(hass).async_ignore(DOMAIN, BATTERY_ISSUE, True)
    restarted.handler.device_health.clear_all(keep_ignored=True)
    reloaded = _dispatcher(hass)
    assert _issue(hass, BATTERY_ISSUE).dismissed_version is not None
    for raw in frames:
        await reloaded.process_message(_frame(raw))
    await _settle_burst(hass)
    assert _issue(hass, BATTERY_ISSUE).dismissed_version is not None


async def test_a_restart_keeps_the_ignore_of_other_faults_and_of_other_entries(hass):
    """Only the battery issue of the tracker's own entry loses its "Ignore" at a restart."""
    health = _health(hass)
    health.observe(_frame(FAULT))
    registry = ir.async_get(hass)
    registry.async_ignore(DOMAIN, ISSUE_74, True)
    other_entry_battery = fault_issue_id("other_entry", FaultKind.CENTRAL_BATTERY_FAULT, 4, "#0")
    ir.async_create_issue(
        hass, DOMAIN, other_entry_battery, is_fixable=False, severity=ir.IssueSeverity.WARNING,
        translation_key="central_battery_fault",
    )
    registry.async_ignore(DOMAIN, other_entry_battery, True)
    health.clear_all(keep_ignored=True)
    _restart_stub(hass, ISSUE_74)

    _health(hass)
    assert _issue(hass).dismissed_version is not None
    assert _issue(hass, other_entry_battery).dismissed_version is not None


async def test_restart_stub_preserves_extra_issue_entry_fields_across_ha_versions(hass):
    """``_restart_stub`` tolerates new non-default ``IssueEntry`` fields (HA 2026.11 ``config_entry_id``, 2026.12 ``config_subentry_id``)."""

    @dataclass(slots=True, frozen=True)
    class _FutureIssueEntry:
        active: bool
        breaks_in_ha_version: str | None
        config_entry_id: str | None
        config_subentry_id: str | None
        created: object
        data: dict | None
        dismissed_version: str | None
        domain: str
        is_fixable: bool | None
        is_persistent: bool
        issue_domain: str | None
        issue_id: str
        learn_more_url: str | None
        severity: ir.IssueSeverity | None
        translation_key: str | None
        translation_placeholders: dict[str, str] | None

    registry = ir.async_get(hass)
    now = dt_util.utcnow()
    registry.issues[(DOMAIN, ISSUE_74)] = _FutureIssueEntry(  # type: ignore[assignment]
        active=True,
        breaks_in_ha_version="2027.1.0",
        config_entry_id=ENTRY_ID,
        config_subentry_id=None,
        created=now,
        data={"k": "v"},
        dismissed_version="2026.12.0.dev0",
        domain=DOMAIN,
        is_fixable=False,
        is_persistent=False,
        issue_domain=DOMAIN,
        issue_id=ISSUE_74,
        learn_more_url="https://example.invalid",
        severity=ir.IssueSeverity.WARNING,
        translation_key="unmapped_device_status",
        translation_placeholders={"where": "74"},
    )
    hass.data[dh.DATA_TRACKERS_STARTED] = {ENTRY_ID}

    _restart_stub(hass, ISSUE_74)

    stub = registry.async_get_issue(DOMAIN, ISSUE_74)
    assert stub is not None
    assert stub.active is False
    assert stub.dismissed_version == "2026.12.0.dev0"
    assert stub.translation_key is None
    assert getattr(stub, "config_entry_id", None) == ENTRY_ID
    assert getattr(stub, "config_subentry_id", "missing") is None
    assert dh.DATA_TRACKERS_STARTED not in hass.data


def test_a_tracker_on_a_hass_without_a_data_dict_forgets_nothing():
    """A bare stand-in for hass (no ``data`` dict) is left alone by the restart rule."""
    handler = MagicMock()
    handler.hass = SimpleNamespace(data=None)
    handler.config_entry = SimpleNamespace(entry_id=ENTRY_ID, options={})
    with patch.object(DeviceHealth, "_clean_ignored_issues"):
        health = DeviceHealth(handler)
    assert health.faults == []


async def test_a_reload_cancels_an_open_central_cycle(hass):
    dispatcher = _dispatcher(hass)
    health = dispatcher.handler.device_health
    for raw in _central_reply("central_reply_via_f461_0747", "f461"):
        await dispatcher.process_message(_frame(raw))
    assert health._central_cycles
    health.clear_all(keep_ignored=True)
    assert not health._central_cycles
    assert _issue(hass, BATTERY_ISSUE) is None  # not ignored: withdrawn with the entry, as before


async def test_no_central_cycle_without_a_hass(hass):
    handler = SimpleNamespace(hass=None, config_entry=SimpleNamespace(entry_id=ENTRY_ID, options={}, data={}), name="gw", log_id="")
    health = DeviceHealth(handler)
    for raw in _central_reply("central_reply_via_f461_0747", "f461"):
        health.observe(_frame(raw))
    assert not health._central_cycles
    assert health.faults == [{"who": 4, "where": "#0", "kind": "central_battery_fault", "code": "31", "evidence": ""}]
    health.clear_all(keep_ignored=True)  # no registry to ask: nothing is kept
    assert health.faults == []

async def test_device_health_heating_edge_cases(hass):
    """Verify DeviceHealth heating edge cases: dimensions, translations, ignored addresses, and bus routing (#819)."""
    dispatcher = _dispatcher(hass)
    health = dispatcher.handler.device_health

    # 1. Dimension frame on #0 is ignored by heating health observer
    await dispatcher.process_message(_frame("*#4*#0*12*0210*3##"))
    assert health.faults == []

    # 2. Translation frame on #0 is ignored
    tx_msg = _frame("*4*20*#0##")
    tx_msg._family = "COMMAND_TRANSLATION"
    await dispatcher.process_message(tx_msg)
    assert health.faults == []

    # 3. Ignored address: configure 4/#0 as ignored
    from types import SimpleNamespace

    from custom_components.myhome.const import CONF_IGNORED_ADDRESSES
    dispatcher.handler.config_entry = SimpleNamespace(entry_id=ENTRY_ID, options={CONF_IGNORED_ADDRESSES: ["4/#0"]})
    await dispatcher.process_message(_frame("*4*20*#0##"))
    assert health.faults == []
    dispatcher.handler.config_entry = SimpleNamespace(entry_id=ENTRY_ID, options={})

    # 4. Bus-routed central unit (#0#4#1 behind interface 1):
    rc_routed_id = fault_issue_id(ENTRY_ID, FaultKind.CENTRAL_REMOTE_CONTROL_DISABLED, 4, "#0#4#1")
    bat_routed_id = fault_issue_id(ENTRY_ID, FaultKind.CENTRAL_BATTERY_FAULT, 4, "#0#4#1")

    await dispatcher.process_message(_frame("*4*20*#0#4#1##"))
    await dispatcher.process_message(_frame("*4*31*#0#4#1##"))
    assert _issue(hass, rc_routed_id) is not None
    assert _issue(hass, bat_routed_id) is not None

    # Next status burst: the mode frame leaves both issues alone
    await _settle_burst(hass)
    await dispatcher.process_message(_frame("*4*1101*#0#4#1##"))
    assert _issue(hass, bat_routed_id) is not None
    assert _issue(hass, rc_routed_id) is not None

    # *4*21*#0#4#1## clears remote control disabled on #0#4#1; no 31 after it clears the battery fault
    await dispatcher.process_message(_frame("*4*21*#0#4#1##"))
    assert _issue(hass, rc_routed_id) is None
    await _settle_burst(hass)
    assert _issue(hass, bat_routed_id) is None
    assert health.faults == []




async def test_a_four_zone_central_units_frame_is_not_the_99_zone_central_units(hass):
    """CA5-8: ``#0#N`` is zone N of a 4-zone central unit; OWNd reports its WHERE as ``#0``.

    The flags are published for ``#0`` only (WHO 4 p. 23-24). Read as ``#0``, a ``#0#1`` frame
    opened the reply cycle that clears the 3550's battery issue and its WHAT 31 raised one.
    No ``#0#N`` frame is in the trace corpus: these are built for the test.
    """
    health = _health(hass)
    battery = fault_issue_id(ENTRY_ID, FaultKind.CENTRAL_BATTERY_FAULT, 4, "#0")
    remote = fault_issue_id(ENTRY_ID, FaultKind.CENTRAL_REMOTE_CONTROL_DISABLED, 4, "#0")

    for raw in ("*4*31*#0#1##", "*4*20*#0#4##", "*4*31*#0#4#4#01##", "*4*20*#0#1#4#01##"):
        health.observe(_frame(raw))
    assert health.faults == [] and not health._central_cycles
    assert _issue(hass, battery) is None and _issue(hass, remote) is None

    # The 3550's own fault stays through a 4-zone unit's "remote control enabled".
    for raw in ("*4*102*#0##", "*4*21*#0##", "*4*31*#0##"):  # live F461 / F455 reply, 2026-11-09
        health.observe(_frame(raw))
    await _settle_burst(hass)
    assert _issue(hass, battery) is not None
    health.observe(_frame("*4*21*#0#1##"))
    await _settle_burst(hass)
    assert _issue(hass, battery) is not None
    assert [fault["where"] for fault in health.faults] == ["#0"]


async def test_ignore_on_the_battery_repair_sticks_through_replies_and_a_reload(hass):
    """The whole path on a set-up entry: gateway, tracker, central climate entity, unload, setup.

    Frames: the 3550's reply through the F461, 2026-11-09 07:47 (central_flags_f455_poll_2026_11_09).
    """
    from OWNd.message import OWNMessage

    from tests.test_multi_gateway_regressions import PRI, _set_up, _setup_patches

    frames = _central_reply("central_reply_via_f461_0747", "f461")

    async def _reply(entry):
        gateway = entry.runtime_data.gateway
        gateway.is_connected = gateway._available = True
        for raw in frames:
            await gateway._process_message(OWNMessage.parse(raw))
        await hass.async_block_till_done()
        await _settle_burst(hass)

    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        entry = await _set_up(hass, PRI, "192.168.0.35", {})
        issue_id = fault_issue_id(entry.entry_id, FaultKind.CENTRAL_BATTERY_FAULT, 4, "#0")
        await _reply(entry)
        assert hass.states.get("climate.central_unit_0").attributes["battery_fault"] is True
        registry = ir.async_get(hass)
        registry.async_ignore(DOMAIN, issue_id, True)
        created = _issue(hass, issue_id).created

        for _ in range(3):
            await _reply(entry)
        issue = _issue(hass, issue_id)
        assert issue.dismissed_version is not None and issue.created == created

        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        issue = _issue(hass, issue_id)
        assert issue is not None and issue.dismissed_version is not None

        await _reply(entry)
        issue = _issue(hass, issue_id)
        assert issue.active is True
        assert issue.dismissed_version is not None and issue.created == created
        assert entry.runtime_data.gateway.device_health.faults == [
            {"who": 4, "where": "#0", "kind": "central_battery_fault", "code": "31", "evidence": ""}
        ]

        # The battery is replaced: the reply without *4*31*#0## clears the issue, ignored or not
        frames = frames[:2]
        await _reply(entry)
        assert _issue(hass, issue_id) is None


def test_in_every_capture_the_battery_flag_follows_the_remote_control_frame():
    """What the tracker's cycle rests on, checked on every gateway that has a capture.

    ``*4*31*WHERE##`` is collected in the cycle the central unit's ``*4*20*WHERE##`` /
    ``*4*21*WHERE##`` opens (WHO 4 p. 24-25). A gateway that showed the flag without that
    frame would never open a cycle, and the issue of a repaired battery would stay.
    """
    import re

    from tests.test_device_health_corpus import TRACES, _frames

    central = re.compile(r"^\*4\*(\d+)(?:#[0-9#]*)?\*(#0(?:#4#\d+)?)##$")
    flags_seen, orphans = 0, []
    for path in sorted(p for p in TRACES.rglob("*") if p.suffix in (".json", ".jsonl", ".log", ".txt")):
        last: dict[str, int] = {}
        for _stamp, raw in _frames(path):
            match = central.match(raw)
            if match is None:
                continue
            what, where = int(match[1]), match[2]
            if what == 31:
                flags_seen += 1
                if last.get(where) not in (20, 21):
                    orphans.append((path.name, raw, last.get(where)))
            elif what not in (22, 23, 24, 30):  # the other optional flags of the same reply
                last[where] = what
    assert flags_seen >= 40  # F455, F461 and MH200N captures
    assert orphans == []
