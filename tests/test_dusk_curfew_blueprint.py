"""Tests for the MyHOME dusk curfew & hardware-coupled sensor automation blueprint."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers import template
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util import yaml as yaml_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

Calls = list[tuple[str, list[str]]]


def _get_blueprint_path() -> Path:
    """Return the absolute path to the dusk curfew blueprint."""
    return Path(__file__).parent.parent / "blueprints" / "automation" / "myhome" / "dusk_curfew.yaml"


def _load_blueprint() -> Blueprint:
    raw_data: dict[str, Any] = yaml_util.load_yaml(str(_get_blueprint_path()))
    return Blueprint(raw_data, expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA)


def _hms(moment: datetime) -> str:
    return moment.strftime("%H:%M:%S")


def _curfew_window(*, active: bool) -> dict[str, str]:
    """Return curfew inputs that do (or do not) contain the current local time.

    The time condition uses the real clock, so a fixed 23:00-06:00 window would make
    these tests depend on when they run.
    """
    now = dt_util.now()
    if active:
        return {"curfew_time": _hms(now - timedelta(hours=1)), "curfew_end_time": _hms(now + timedelta(hours=1))}
    return {"curfew_time": _hms(now + timedelta(hours=2)), "curfew_end_time": _hms(now + timedelta(hours=3))}


async def _settle() -> None:
    """Let triggered runs reach their waits.

    hass.async_block_till_done() would block on a run's pending wait_for_trigger timeout.
    """
    for _ in range(50):
        await asyncio.sleep(0)


async def _advance(hass: HomeAssistant, delta: timedelta) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + delta)
    await _settle()


def _register_services(hass: HomeAssistant) -> Calls:
    calls: Calls = []

    def recorder(action: str) -> Callable[[Any], None]:
        def record(call: Any) -> None:
            entity_id = call.data.get("entity_id", [])
            calls.append((action, [entity_id] if isinstance(entity_id, str) else list(entity_id)))

        return record

    hass.services.async_register("light", "turn_on", recorder("on"))
    hass.services.async_register("light", "turn_off", recorder("off"))
    hass.services.async_register("homeassistant", "turn_off", recorder("ha_off"))
    return calls


@asynccontextmanager
async def _automation(hass: HomeAssistant, inputs: dict[str, Any], alias: str) -> AsyncIterator[None]:
    bp_inputs = BlueprintInputs(_load_blueprint(), {"use_blueprint": {"path": "test", "input": inputs}})
    substituted = bp_inputs.async_substitute()
    substituted["alias"] = alias
    assert await async_setup_component(hass, "automation", {"automation": [substituted]})
    await hass.async_block_till_done()
    try:
        yield
    finally:
        # Cancels any run still waiting, so no timers leak past the test.
        await hass.services.async_call("automation", "turn_off", {"entity_id": f"automation.{alias}"}, blocking=True)


def _called(calls: Calls, action: str, entity_id: str) -> bool:
    return any(a == action and entity_id in ids for a, ids in calls)


async def _set(hass: HomeAssistant, entity_id: str, state: str) -> None:
    hass.states.async_set(entity_id, state)
    await _settle()


def test_dusk_curfew_blueprint_schema_and_inputs() -> None:
    """Verify dusk_curfew.yaml complies with HA's native AUTOMATION_BLUEPRINT_SCHEMA."""
    blueprint_path = _get_blueprint_path()
    assert blueprint_path.is_file(), f"Blueprint file not found at {blueprint_path}"

    # Verify no dead bundled copy remains in custom_components
    bundled_dir = Path(__file__).parent.parent / "custom_components" / "myhome" / "blueprints"
    assert not bundled_dir.exists(), "custom_components/myhome/blueprints must not exist"

    bp = _load_blueprint()
    assert bp.domain == "automation"
    assert bp.name == "MyHOME - Dusk Curfew & Hardware-Coupled Sensor Control"

    assert set(bp.inputs.keys()) == {
        "target_light",
        "curfew_time",
        "curfew_end_time",
        "max_duration",
        "sync_lights",
        "presence_entity",
        "away_timeout",
        "after_sunset_only",
        "override_entity",
        "override_mode",
        "service_timeout_hours",
    }

    # Validate target_light entity selector (HA normalizes domain to list)
    assert bp.inputs["target_light"]["selector"]["entity"]["domain"] == ["light"]

    # Validate curfew_time & curfew_end_time selectors and defaults
    assert "time" in bp.inputs["curfew_time"]["selector"]
    assert bp.inputs["curfew_time"]["default"] == "23:00:00"
    assert "time" in bp.inputs["curfew_end_time"]["selector"]
    assert bp.inputs["curfew_end_time"]["default"] == "06:00:00"

    # Validate default values
    assert bp.inputs["sync_lights"]["default"] == {}
    assert bp.inputs["presence_entity"]["default"] == ""
    assert bp.inputs["away_timeout"]["default"] == 0
    assert bp.inputs["max_duration"]["default"] == 0
    assert bp.inputs["after_sunset_only"]["default"] is False
    assert bp.inputs["override_entity"]["default"] == ""
    assert bp.inputs["override_mode"]["default"] == "service_power"
    assert bp.inputs["service_timeout_hours"]["default"] == 4

    modes = [o["value"] for o in bp.inputs["override_mode"]["selector"]["select"]["options"]]
    assert modes == ["service_power", "maintenance_hold", "pause_automation"]


def test_dusk_curfew_blueprint_substitution() -> None:
    """Verify substituting inputs produces the expected triggers and trigger_variables."""
    user_inputs = {
        "target_light": "light.light_98",
        "curfew_time": "23:00:00",
        "curfew_end_time": "06:00:00",
        "max_duration": 180,
        "sync_lights": {"entity_id": ["light.garden_pathway", "light.driveway_spots"]},
        "presence_entity": "zone.home",
        "away_timeout": 15,
        "after_sunset_only": False,
        "override_entity": "input_boolean.gardener_power",
        "override_mode": "service_power",
        "service_timeout_hours": 3,
    }

    bp_inputs = BlueprintInputs(_load_blueprint(), {"use_blueprint": {"path": "test", "input": user_inputs}})
    bp_inputs.validate()
    substituted = bp_inputs.async_substitute()

    assert substituted["mode"] == "parallel"
    assert substituted["max"] == 10

    # Verify trigger_variables scoping so template triggers can access presence_entity & override_entity
    trig_vars = substituted["trigger_variables"]
    assert trig_vars["presence_entity"] == "zone.home"
    assert trig_vars["away_timeout"] == 15
    assert trig_vars["override_entity"] == "input_boolean.gardener_power"

    triggers = substituted["triggers"]
    assert {t.get("id") for t in triggers} == {
        "light_turned_on",
        "light_turned_off",
        "curfew_reached",
        "ha_started",
        "presence_away",
        "override_activated",
        "override_deactivated",
    }

    # Verify light_turned_on trigger has from: off, to: on (prevent flap on gateway reconnect)
    turn_on_trig = next(t for t in triggers if t.get("id") == "light_turned_on")
    assert turn_on_trig["from"] == "off"
    assert turn_on_trig["to"] == "on"
    assert turn_on_trig["entity_id"] == "light.light_98"

    # Verify light_turned_off trigger has from: on, to: off
    turn_off_trig = next(t for t in triggers if t.get("id") == "light_turned_off")
    assert turn_off_trig["from"] == "on"
    assert turn_off_trig["to"] == "off"

    curfew_trig = next(t for t in triggers if t.get("id") == "curfew_reached")
    assert curfew_trig["trigger"] == "time"
    assert curfew_trig["at"] == "23:00:00"


async def test_dusk_curfew_presence_template_rendering(hass: HomeAssistant) -> None:
    """Verify presence template handles zone counts, person, binary_sensor and alarm panel states."""
    bp_inputs = BlueprintInputs(_load_blueprint(), {"use_blueprint": {"path": "test", "input": {"target_light": "light.light_98"}}})
    t = template.Template(bp_inputs.async_substitute()["variables"]["is_away"], hass)

    def away(entity_id: str, state: str) -> bool:
        hass.states.async_set(entity_id, state)
        return bool(t.async_render({"presence_entity": entity_id}, parse_result=True))

    assert t.async_render({"presence_entity": ""}, parse_result=True) is False

    assert away("zone.home", "0") is True
    assert away("zone.home", "1") is False
    assert away("zone.home", "2") is False

    assert away("person.john", "not_home") is True
    assert away("person.john", "away") is True
    assert away("person.john", "home") is False

    assert away("binary_sensor.presence", "off") is True
    assert away("binary_sensor.presence", "on") is False

    # A disarmed alarm usually means someone is home
    assert away("alarm_control_panel.house", "armed_away") is True
    assert away("alarm_control_panel.house", "armed_vacation") is True
    assert away("alarm_control_panel.house", "disarmed") is False
    assert away("alarm_control_panel.house", "armed_home") is False


async def test_dusk_curfew_behavioral_turn_on_and_from_off_guard(hass: HomeAssistant) -> None:
    """from: off guard prevents an unavailable->on flap; off->on syncs companion lights."""
    calls = _register_services(hass)
    hass.states.async_set("light.light_98", "off")
    inputs = {
        "target_light": "light.light_98",
        "sync_lights": {"entity_id": ["light.garden_pathway"]},
        **_curfew_window(active=False),
    }
    async with _automation(hass, inputs, "test_turn_on_guard"):
        await _set(hass, "light.light_98", "unavailable")
        await _set(hass, "light.light_98", "on")
        assert not any(action == "on" for action, _ in calls)

        await _set(hass, "light.light_98", "off")
        calls.clear()
        await _set(hass, "light.light_98", "on")
        assert _called(calls, "on", "light.garden_pathway")


async def test_dusk_curfew_behavioral_daylight_guard(hass: HomeAssistant) -> None:
    """after_sunset_only stops companion activation when the sun is above the horizon."""
    calls = _register_services(hass)
    hass.states.async_set("light.light_98", "off")
    hass.states.async_set("sun.sun", "above_horizon")
    inputs = {
        "target_light": "light.light_98",
        "sync_lights": {"entity_id": ["light.garden_pathway"]},
        "after_sunset_only": True,
        **_curfew_window(active=False),
    }
    async with _automation(hass, inputs, "test_daylight_guard"):
        await _set(hass, "light.light_98", "on")
        assert not any(action == "on" for action, _ in calls)

        await _set(hass, "light.light_98", "off")
        hass.states.async_set("sun.sun", "below_horizon")
        calls.clear()
        await _set(hass, "light.light_98", "on")
        assert _called(calls, "on", "light.garden_pathway")


async def test_dusk_curfew_behavioral_presence_away_trigger(hass: HomeAssistant) -> None:
    """presence_away trigger fires via trigger_variables and turns off lights."""
    calls = _register_services(hass)
    hass.states.async_set("light.light_98", "on")
    hass.states.async_set("zone.home", "1")
    inputs = {
        "target_light": "light.light_98",
        "presence_entity": "zone.home",
        "away_timeout": 1,
        **_curfew_window(active=False),
    }
    async with _automation(hass, inputs, "test_presence_away_guard"):
        await _set(hass, "zone.home", "0")
        await _advance(hass, timedelta(seconds=65))
        assert _called(calls, "off", "light.light_98")


async def test_dusk_curfew_curfew_fires_unless_service_power(hass: HomeAssistant) -> None:
    """Curfew turns the light off at curfew_time, but not while service power is on."""
    calls = _register_services(hass)
    curfew_at = (dt_util.now() + timedelta(minutes=5)).replace(microsecond=0)
    hass.states.async_set("light.light_98", "on")
    hass.states.async_set("input_boolean.gardener_power", "off")
    inputs = {
        "target_light": "light.light_98",
        "curfew_time": _hms(curfew_at),
        "curfew_end_time": _hms(curfew_at + timedelta(hours=1)),
        "override_entity": "input_boolean.gardener_power",
        "override_mode": "service_power",
        "service_timeout_hours": 0,
    }
    async with _automation(hass, inputs, "test_curfew"):
        # Positive control: without an override the curfew trigger fires
        async_fire_time_changed(hass, dt_util.as_utc(curfew_at))
        await _settle()
        assert _called(calls, "off", "light.light_98")

        # With service power on, the next day's curfew leaves the light alone
        await _set(hass, "input_boolean.gardener_power", "on")
        calls.clear()
        async_fire_time_changed(hass, dt_util.as_utc(curfew_at + timedelta(days=1)))
        await _settle()
        assert not _called(calls, "off", "light.light_98")


async def test_dusk_curfew_turn_on_during_curfew_grace(hass: HomeAssistant) -> None:
    """A turn-on inside the curfew window is turned off after the 2-minute grace period."""
    calls = _register_services(hass)
    hass.states.async_set("light.light_98", "off")
    inputs = {"target_light": "light.light_98", **_curfew_window(active=True)}
    async with _automation(hass, inputs, "test_curfew_grace"):
        await _set(hass, "light.light_98", "on")
        await _advance(hass, timedelta(minutes=1))
        assert not _called(calls, "off", "light.light_98")
        await _advance(hass, timedelta(minutes=2, seconds=5))
        assert _called(calls, "off", "light.light_98")


async def test_dusk_curfew_max_duration(hass: HomeAssistant) -> None:
    """max_duration turns the light and its companions off when it runs out."""
    calls = _register_services(hass)
    hass.states.async_set("light.light_98", "off")
    inputs = {
        "target_light": "light.light_98",
        "max_duration": 60,
        "sync_lights": {"entity_id": ["light.garden_pathway"]},
        **_curfew_window(active=False),
    }
    async with _automation(hass, inputs, "test_max_duration"):
        await _set(hass, "light.light_98", "on")
        await _advance(hass, timedelta(minutes=59))
        assert not _called(calls, "off", "light.light_98")
        await _advance(hass, timedelta(minutes=61))
        assert _called(calls, "off", "light.light_98")
        assert _called(calls, "off", "light.garden_pathway")


async def test_dusk_curfew_stale_max_duration_does_not_cut_service_power(hass: HomeAssistant) -> None:
    """A finished session's max_duration timer must not cut a later service power session."""
    calls = _register_services(hass)
    hass.states.async_set("light.light_98", "off")
    hass.states.async_set("input_boolean.gardener_power", "off")
    inputs = {
        "target_light": "light.light_98",
        "max_duration": 60,
        "override_entity": "input_boolean.gardener_power",
        "override_mode": "service_power",
        "service_timeout_hours": 0,
        **_curfew_window(active=False),
    }
    async with _automation(hass, inputs, "test_stale_max_duration"):
        await _set(hass, "light.light_98", "on")  # dusk session starts its 60 min timer
        await _set(hass, "light.light_98", "off")  # switched off by hand
        await _set(hass, "input_boolean.gardener_power", "on")  # unconstrained service power
        await _set(hass, "light.light_98", "on")
        calls.clear()
        await _advance(hass, timedelta(minutes=61))
        assert not _called(calls, "off", "light.light_98")


async def test_dusk_curfew_behavioral_service_power_override(hass: HomeAssistant) -> None:
    """service_power energizes the circuit on demand; switching it off turns the light off."""
    calls = _register_services(hass)
    hass.states.async_set("light.light_98", "off")
    hass.states.async_set("input_boolean.gardener_power", "off")
    inputs = {
        "target_light": "light.light_98",
        "sync_lights": {"entity_id": ["light.garden_pathway"]},
        "override_entity": "input_boolean.gardener_power",
        "override_mode": "service_power",
        "service_timeout_hours": 0,
        **_curfew_window(active=False),
    }
    async with _automation(hass, inputs, "test_service_power_override"):
        await _set(hass, "input_boolean.gardener_power", "on")
        assert _called(calls, "on", "light.light_98")

        await _set(hass, "light.light_98", "on")
        calls.clear()
        await _set(hass, "input_boolean.gardener_power", "off")
        assert _called(calls, "off", "light.light_98")


async def test_dusk_curfew_service_power_timeout(hass: HomeAssistant) -> None:
    """service_timeout_hours turns the circuit off and resets the helper."""
    calls = _register_services(hass)
    hass.states.async_set("light.light_98", "off")
    hass.states.async_set("input_boolean.gardener_power", "off")
    inputs = {
        "target_light": "light.light_98",
        "override_entity": "input_boolean.gardener_power",
        "override_mode": "service_power",
        "service_timeout_hours": 1,
        **_curfew_window(active=False),
    }
    async with _automation(hass, inputs, "test_service_timeout"):
        await _set(hass, "input_boolean.gardener_power", "on")
        await _set(hass, "light.light_98", "on")
        calls.clear()
        await _advance(hass, timedelta(minutes=61))
        assert _called(calls, "off", "light.light_98")
        assert _called(calls, "ha_off", "input_boolean.gardener_power")


async def test_dusk_curfew_stale_service_timeout_does_not_cut_later_session(hass: HomeAssistant) -> None:
    """Ending service power early cancels its timeout instead of cutting a later light session."""
    calls = _register_services(hass)
    hass.states.async_set("light.light_98", "off")
    hass.states.async_set("input_boolean.gardener_power", "off")
    inputs = {
        "target_light": "light.light_98",
        "override_entity": "input_boolean.gardener_power",
        "override_mode": "service_power",
        "service_timeout_hours": 1,
        **_curfew_window(active=False),
    }
    async with _automation(hass, inputs, "test_stale_service_timeout"):
        await _set(hass, "input_boolean.gardener_power", "on")
        await _set(hass, "light.light_98", "on")
        await _set(hass, "input_boolean.gardener_power", "off")  # gardener done early
        await _set(hass, "light.light_98", "off")
        await _set(hass, "light.light_98", "on")  # later, ordinary photocell turn-on
        calls.clear()
        await _advance(hass, timedelta(minutes=61))
        assert not _called(calls, "off", "light.light_98")
        assert not _called(calls, "ha_off", "input_boolean.gardener_power")


async def test_dusk_curfew_restart_rearms_service_timeout(hass: HomeAssistant) -> None:
    """A restart during service power re-arms the timeout instead of leaving power on forever."""
    calls = _register_services(hass)
    hass.states.async_set("light.light_98", "on")
    hass.states.async_set("input_boolean.gardener_power", "on")
    inputs = {
        "target_light": "light.light_98",
        "override_entity": "input_boolean.gardener_power",
        "override_mode": "service_power",
        "service_timeout_hours": 1,
        **_curfew_window(active=False),
    }
    async with _automation(hass, inputs, "test_restart_rearm"):
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await _settle()
        calls.clear()
        await _advance(hass, timedelta(minutes=61))
        assert _called(calls, "off", "light.light_98")
        assert _called(calls, "ha_off", "input_boolean.gardener_power")


async def test_dusk_curfew_behavioral_maintenance_hold_override(hass: HomeAssistant) -> None:
    """maintenance_hold turns the light off and switches photocell turn-ons back off."""
    calls = _register_services(hass)
    hass.states.async_set("light.light_98", "on")
    hass.states.async_set("input_boolean.maintenance_lock", "off")
    inputs = {
        "target_light": "light.light_98",
        "sync_lights": {"entity_id": ["light.garden_pathway"]},
        "override_entity": "input_boolean.maintenance_lock",
        "override_mode": "maintenance_hold",
        **_curfew_window(active=False),
    }
    async with _automation(hass, inputs, "test_maintenance_hold"):
        await _set(hass, "input_boolean.maintenance_lock", "on")
        assert _called(calls, "off", "light.light_98")

        await _set(hass, "light.light_98", "off")
        calls.clear()
        await _set(hass, "light.light_98", "on")
        assert _called(calls, "off", "light.light_98")
