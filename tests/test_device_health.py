"""Device faults seen on the bus become self-clearing repair issues (device_health.py).

Frames are from the MH200 capture EVID-MH200-WHAT19-FAULT: an actuator in a fault
state answers its status request with ``*1*19*74##`` (WHAT 19 is outside the
published WHO 1 table), with a WHO 1001 DIMENSION 11 mask 3.45 s before it.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers import issue_registry as ir
from OWNd.message import OWNEvent

from custom_components.myhome import device_health as dh
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.device_health import DeviceHealth, Fault, FaultKind, fault_issue_id
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_events import GatewayEventDispatcher
from custom_components.myhome.light import MyHOMELight

ENTRY_ID = "health_entry"
FAULT = "*1*19*74##"
MASK = "*#1001*74*11*111110111111111111110111##"
OFF = "*1*0*74##"
ISSUE_74 = fault_issue_id(ENTRY_ID, FaultKind.UNMAPPED_STATUS, 1, "74")


def _health(hass):
    handler = SimpleNamespace(
        hass=hass, config_entry=SimpleNamespace(entry_id=ENTRY_ID), name="MH200 Gateway", log_id="[health]"
    )
    return DeviceHealth(handler)


def _issue(hass, issue_id=ISSUE_74):
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


def _frame(raw):
    return OWNEvent.parse(raw)


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
        "code": "19",
        "evidence": "",
    }
    assert issue.learn_more_url.endswith("/diagnostics/repair-issues/#unmapped-device-status")
    assert health.faults == [{"who": 1, "where": "74", "kind": "unmapped_status", "code": "19", "evidence": ""}]


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
    health.observe(_frame("*1*19*74#4#01##"))
    issue = _issue(hass, fault_issue_id(ENTRY_ID, FaultKind.UNMAPPED_STATUS, 1, "74#4#01"))
    assert issue is not None
    assert issue.issue_id.endswith("_unmapped_status_1_74_4_01")
    assert issue.translation_placeholders["where"] == "74#4#01"
    assert _issue(hass) is None  # not the local-bus 74


async def test_scope_frames_are_not_device_faults(hass):
    health = _health(hass)
    health.observe(_frame(FAULT))
    for raw in ("*1*19*0##", "*1*19*7##", "*1*19*#5##", "*1*0*0##"):
        health.observe(_frame(raw))
    assert health.faults == [{"who": 1, "where": "74", "kind": "unmapped_status", "code": "19", "evidence": ""}]


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
    health.name_address(*light._health_address, light._display_name, owner=light._health_owner)
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
    health.observe(SimpleNamespace(who=1, where=None, unknown_state=19, is_on=None))
    health.observe(SimpleNamespace(who=1001, where="74", dimension=11, _dimension_value=[]))
    health.observe(SimpleNamespace(who=1001, where="74", dimension=11, _dimension_value=["1201"]))
    health.observe(_frame(FAULT))
    assert _issue(hass).translation_placeholders["evidence"] == ""


async def test_an_entity_without_a_numeric_who_files_no_fault(hass):
    dispatcher = _dispatcher(hass)
    light = _light(hass, dispatcher.handler)
    light._who = "light"
    assert light._health_address is None
    light._report_fault(FaultKind.UNMAPPED_STATUS, "19")
    assert dispatcher.handler.device_health.faults == []
