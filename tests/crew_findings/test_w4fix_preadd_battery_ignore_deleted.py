# ruff: noqa
# Reproduction r1s0_preadd_battery_ignore_deleted of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from homeassistant.helpers import issue_registry as ir
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.device_health import fault_issue_id, FaultKind
from tests.test_device_health import _health, _frame, _issue, ENTRY_ID
from tests.test_crew_central_season_restore import _gateway, _climate, _entity_base_init, FLAGS


async def test_preadd_battery_ignore_deleted(hass):
    health = _health(hass)
    issue = fault_issue_id(ENTRY_ID, FaultKind.CENTRAL_BATTERY_FAULT, 4, '#0')
    for frame in FLAGS:
        health.observe(_frame(frame))
    ir.async_get(hass).async_ignore(DOMAIN, issue, True)
    health.clear_all(keep_ignored=True)
    gw = _gateway()
    gw.device_health = _health(hass)
    central = _climate(gw, '#0', standalone=None, central=True)
    central.platform = None  # first status burst seeds the entity before HA adds it
    for frame in FLAGS:
        event = _frame(frame)
        gw.device_health.observe(event)
        central.handle_event(event)
    assert _issue(hass, issue) is not None and _issue(hass, issue).dismissed_version is not None
    gw.device_health.clear_all()
