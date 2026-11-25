"""Regression tests for PR #1016 services.py placeholder model skip, MH202 regex, and CI workflows."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from homeassistant.components import websocket_api
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, CONF_MAC, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity import Entity
from OWNd.connection import get_gateway_profile
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    ATTR_GATEWAY,
    ATTR_MESSAGE,
    DOMAIN,
    SERVICE_TURN_ON_TIMED,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.services import (
    _MH202_ALL_PROBES_CRASH_RE,
    SERVICE_SEND_MESSAGE,
    _handler_model_key,
    _is_mh202_who4_crash_frame,
    async_setup_services,
    gateway_crash_frame_reason,
)
from custom_components.myhome.websocket import ws_bus_monitor_send

REPO_ROOT = Path(__file__).resolve().parent.parent
MAC = "00:03:50:de:ad:01"


def _make_real_handler(
    hass: HomeAssistant,
    *,
    gw_model_name: str | None,
    profile: Any = None,
    entry_name: str | None = None,
    entry_title: str = "Mock Title",
    mac: str = MAC,
) -> MyHOMEGatewayHandler:
    """Construct a real MyHOMEGatewayHandler instance with custom gateway/config_entry metadata."""
    data: dict[str, Any] = {CONF_HOST: "192.0.2.50", CONF_PORT: 20000, CONF_MAC: mac}
    if entry_name is not None:
        data[CONF_NAME] = entry_name
    entry = MockConfigEntry(domain=DOMAIN, title=entry_title, data=data, unique_id=mac)
    entry.add_to_hass(hass)

    handler = object.__new__(MyHOMEGatewayHandler)
    handler.hass = hass
    handler.config_entry = entry
    handler.gateway = SimpleNamespace(
        model_name=gw_model_name,
        model=gw_model_name,
        profile=profile if profile is not None else get_gateway_profile(""),
        log_id=f"[{mac}]",
    )
    handler.send = AsyncMock()  # type: ignore[method-assign]
    handler.send_status_request = AsyncMock()  # type: ignore[method-assign]
    handler._delegated_target = lambda _msg: None  # type: ignore[method-assign]
    handler._failover_target = lambda _msg: None  # type: ignore[method-assign]
    return handler


@pytest.mark.parametrize(
    "placeholder",
    [
        "",
        "   ",
        None,
        "None",
        "null",
        "Generic",
        "Generic Gateway",
        "Gateway",
        "Unknown",
        "Unknown model",
        "Custom (IP Gateway)",
    ],
)
async def test_real_handler_placeholder_model_falls_through_to_entry_and_blocks_crash_frames(
    hass: HomeAssistant,
    attach_gateway: Any,
    placeholder: str | None,
) -> None:
    """Real MyHOMEGatewayHandler with placeholder gateway.model_name falls through to config_entry/title."""
    await async_setup_services(hass)

    # 1. MH202 resolved from config_entry.data[CONF_NAME] despite placeholder _model_key
    mh202_handler = _make_real_handler(
        hass,
        gw_model_name=placeholder,
        entry_name="BTicino MH202",
        entry_title="Custom (IP Gateway)",
        mac="00:03:50:10:16:01",
    )
    assert _handler_model_key(mh202_handler) == "MH202"
    assert gateway_crash_frame_reason(mh202_handler, "*#4*001##") is not None
    assert gateway_crash_frame_reason(mh202_handler, "*#4*2#1##") is not None

    attach_gateway("00:03:50:10:16:01", mh202_handler)
    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SEND_MESSAGE,
            {ATTR_GATEWAY: "00:03:50:10:16:01", ATTR_MESSAGE: "*#4*001##"},
            blocking=True,
        )
    assert exc_info.value.translation_key == "send_message_refused_frame"
    mh202_handler.send_status_request.assert_not_awaited()

    ws_conn = MagicMock(spec=websocket_api.ActiveConnection)
    ws_conn.send_result = MagicMock()
    ws_conn.send_error = MagicMock()
    ws_bus_monitor_send(
        hass,
        ws_conn,
        {"id": 1, "type": "myhome/bus_monitor/send", "mac": "00:03:50:10:16:01", "frame": "*#4*001##"},
    )
    await hass.async_block_till_done()
    ws_conn.send_result.assert_not_called()
    ws_conn.send_error.assert_called_once()

    # 2. H4684 resolved from config_entry.title when data[CONF_NAME] is also a placeholder
    h4684_handler = _make_real_handler(
        hass,
        gw_model_name=placeholder,
        entry_name="Custom (IP Gateway)",
        entry_title="BTicino H/L4684 (192.0.2.51)",
        mac="00:03:50:10:16:02",
    )
    assert _handler_model_key(h4684_handler) == "H4684"
    assert gateway_crash_frame_reason(h4684_handler, "*#13**10##") is not None
    assert gateway_crash_frame_reason(h4684_handler, "*#13**11##") is not None
    assert gateway_crash_frame_reason(h4684_handler, "*#13**12##") is not None


async def test_handler_model_key_profile_delegation_and_custom_fallback(
    hass: HomeAssistant,
) -> None:
    """Verify concrete profile resolution, delegated peer placeholder skip, and fallback behavior."""
    # Concrete profile wins even when gateway.model_name and entry fields are placeholders
    mh200_profile = get_gateway_profile("MH200")
    h_prof = _make_real_handler(
        hass,
        gw_model_name="Unknown",
        profile=mh200_profile,
        entry_name="Unknown model",
        entry_title="Gateway",
        mac="00:03:50:10:16:03",
    )
    assert _handler_model_key(h_prof) == "MH200"
    assert gateway_crash_frame_reason(h_prof, "*#13**10##") is not None

    # Mock handler with placeholder _model_key and concrete profile resolves via profile candidate
    stub_prof = SimpleNamespace(
        _model_key="UNKNOWN",
        gateway=SimpleNamespace(profile=get_gateway_profile("MH202"), model_name="Generic"),
    )
    assert _handler_model_key(stub_prof) == "MH202"

    # Non-model room label in CONF_NAME does not shadow known model in config_entry.title
    room_named = _make_real_handler(
        hass,
        gw_model_name="My Basement Hub",
        entry_name="Basement Hub",
        entry_title="MH202",
        mac="00:03:50:10:16:04",
    )
    assert _handler_model_key(room_named) == "MH202"

    # Unlisted non-placeholder model returns normalized fallback key; pure placeholders return ""
    assert _handler_model_key(SimpleNamespace(_model_key="ExperimentalX99")) == "EXPERIMENTALX99"
    all_placeholders = _make_real_handler(
        hass,
        gw_model_name="Unknown model",
        entry_name="Custom (IP Gateway)",
        entry_title="Gateway",
        mac="00:03:50:10:16:05",
    )
    assert _handler_model_key(all_placeholders) == ""

    # Shared-bus WHO=4 delegation to a real MH202 peer whose gateway.model_name is None
    f461_primary = _make_real_handler(
        hass,
        gw_model_name="F461",
        entry_name="F461",
        mac="00:03:50:10:16:06",
    )
    mh202_secondary = _make_real_handler(
        hass,
        gw_model_name=None,
        entry_name="MH202",
        mac="00:03:50:10:16:07",
    )
    f461_primary._delegated_target = lambda _msg: mh202_secondary  # type: ignore[method-assign]
    assert gateway_crash_frame_reason(f461_primary, "*#4*001##") is not None
    assert gateway_crash_frame_reason(f461_primary, "*#4*001*#14*0215*1##") is not None


def test_mh202_all_probes_crash_regex_and_classifier_boundaries() -> None:
    """Verify _MH202_ALL_PROBES_CRASH_RE catches 001..099 forms and never false-matches 0#1 or #0#1."""
    for crash_frame in (
        "*#4*001##",
        "*#4*001*0##",
        "*#4*001*#14*0215*1##",
        "*#4*001#4#01##",
        "*#4*009##",
        "*#4*099*0##",
    ):
        assert _MH202_ALL_PROBES_CRASH_RE.fullmatch(crash_frame) is not None
        assert _is_mh202_who4_crash_frame(crash_frame) is True

    for safe_frame in (
        "*#4*0#1*20##",
        "*#4*#0#1##",
        "*#4*#0#1*#14*0215*3##",
        "*#4*000##",
        "*#4*0100*0##",
        "*#4*0105*0##",
    ):
        assert _MH202_ALL_PROBES_CRASH_RE.fullmatch(safe_frame) is None
        assert _is_mh202_who4_crash_frame(safe_frame) is False

    # Dimensionless 3-token and 4-token actuator forms on MH202
    assert _is_mh202_who4_crash_frame("*#4*3#1#2##") is True
    assert _is_mh202_who4_crash_frame("*#4*3#0#2##") is False
    assert _is_mh202_who4_crash_frame("*#4*1#2#3#4##") is True
    assert _is_mh202_who4_crash_frame("*#4*1#2#0#4##") is False


def test_release_branch_triggers_present_in_all_three_workflows() -> None:
    """Verify chore/release-* is present in push and pull_request triggers across all 3 workflows."""
    for rel_path in (
        ".github/workflows/quality-scale.yml",
        ".github/workflows/pypi_standards.yml",
        ".github/workflows/ownd-smoke.yml",
    ):
        wf = yaml.safe_load((REPO_ROOT / rel_path).read_text(encoding="utf-8"))
        wf_on = wf.get("on") or wf[True]
        assert "chore/release-*" in wf_on["push"]["branches"], rel_path
        assert "chore/release-*" in wf_on["pull_request"]["branches"], rel_path


async def test_turn_on_timed_service_dispatches_across_light_and_switch_platforms(
    hass: HomeAssistant,
) -> None:
    """Verify myhome.turn_on_timed collects light/switch entities and invokes async_turn_on_timed."""
    await async_setup_services(hass)

    class _TimedDummy(Entity):
        def __init__(self, entity_id: str) -> None:
            self.hass = hass
            self.entity_id = entity_id
            self.async_turn_on_timed = AsyncMock()

    light_ent = _TimedDummy("light.test_timed")
    switch_ent = _TimedDummy("switch.test_timed")
    sensor_ent = _TimedDummy("sensor.ignored_domain")
    hass.states.async_set("light.test_timed", "off")
    hass.states.async_set("switch.test_timed", "off")

    platforms = [
        SimpleNamespace(domain="light", entities={"light.test_timed": light_ent}),
        SimpleNamespace(domain="switch", entities={"switch.test_timed": switch_ent}),
        SimpleNamespace(domain="sensor", entities={"sensor.ignored_domain": sensor_ent}),
    ]
    with patch(
        "custom_components.myhome.services.entity_platform.async_get_platforms",
        return_value=platforms,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_TURN_ON_TIMED,
            {ATTR_ENTITY_ID: ["light.test_timed", "switch.test_timed"], "duration": 30},
            blocking=True,
        )

    light_ent.async_turn_on_timed.assert_awaited_once()
    switch_ent.async_turn_on_timed.assert_awaited_once()
    sensor_ent.async_turn_on_timed.assert_not_awaited()

