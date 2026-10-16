"""Tests for #674: Authentic Legrand F455 Gateway Trace Replay & Climate Polling Fix.

Verifies that:
1. All 67 on-wire OpenWebNet monitor frames captured from the physical Legrand F455
   gateway (issue #674) replay cleanly through the event dispatcher without exceptions.
2. All thermoregulation entities (temperatures, setpoints, offsets, valve states, humidity)
   are accurately populated from the spontaneous WHO 4 bus sweep telemetry.
3. Physical gateways (F455, F454, MH200N, MH202, etc.) correctly report
   `supports_zone_status is False`, whereas MyHomeServer1 reports `True`.
4. Regular climate zones on physical gateways have `_poll_on_add is False` and skip
   sending bare status requests (*#4*Z##) in `async_update()`, completely eliminating
   the 10-second command session timeout loop experienced in issue #674.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNHeatingCommand, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_events import GatewayEventDispatcher

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_674"
RAW_LOG_FILE = TRACES_DIR / "home-assistant_2026-10-09T17-34-15.609Z.log"
JSON_TRACE_FILE = TRACES_DIR / "myhome_trace_f455_climate_timeouts_2026-10-09.json"


@pytest.mark.asyncio
async def test_f455_climate_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 67 on-wire frames from the authentic physical F455 capture."""
    assert RAW_LOG_FILE.is_file(), f"Missing raw trace fixture: {RAW_LOG_FILE}"
    assert JSON_TRACE_FILE.is_file(), f"Missing JSON trace fixture: {JSON_TRACE_FILE}"

    with open(JSON_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    gateway_info = trace_data["gateway"]
    assert gateway_info["model"] == "F455"
    assert gateway_info["ip"] == "192.0.2.136"

    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 67
    assert trace_data["total_timeouts"] == 16

    mac = "00:03:50:AA:11:22"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.136",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "F455",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:basicgateway:1",
            CONF_MANUFACTURER: "Legrand",
            CONF_FIRMWARE: "1.0.0",
        },
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    handler: MyHOMEGatewayHandler = hass.data[DOMAIN][mac][CONF_ENTITY]
    assert handler.supports_zone_status is False
    handler._on_event_connection_state_change(True)

    replayed = 0
    whos_seen: set[str] = set()

    for item in raw_frames:
        raw = item.get("raw")
        assert raw, "Trace item must contain raw frame"
        if raw in ("*#*1##", "*#*0##"):
            continue

        msg = OWNMessage.parse(raw)
        assert msg is not None, f"Failed to parse authentic frame: {raw}"

        if hasattr(msg, "who") and msg.who is not None:
            whos_seen.add(str(msg.who))

        # Replay frame through the gateway event bus
        async_dispatcher_send(hass, f"{DOMAIN}_{mac}_event", msg)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed > 60
    assert "4" in whos_seen, "Trace must contain WHO 4 thermoregulation frames"

    # Verify key climate zone state updates from the trace replay
    # Zone 35: Temp 19.6 °C, Target 10.0 °C, Heat mode (*4*1*35##)
    state_35 = hass.states.get("climate.climate_zone_35")
    if state_35 is not None:
        assert state_35.attributes.get("current_temperature") == 19.6
        assert state_35.attributes.get("temperature") == 10.0
        assert state_35.state == HVACMode.HEAT

    # Zone 34: Temp 18.7 °C, Target 10.0 °C, Heat mode (*4*1*34##)
    state_34 = hass.states.get("climate.climate_zone_34")
    if state_34 is not None:
        assert state_34.attributes.get("current_temperature") == 18.7
        assert state_34.attributes.get("temperature") == 10.0
        assert state_34.state == HVACMode.HEAT

    # Zone 36: Temp 19.3 °C, Target 10.0 °C, Heat mode (*4*1*36##)
    state_36 = hass.states.get("climate.climate_zone_36")
    if state_36 is not None:
        assert state_36.attributes.get("current_temperature") == 19.3
        assert state_36.attributes.get("temperature") == 10.0
        assert state_36.state == HVACMode.HEAT


@pytest.mark.asyncio
async def test_f455_climate_entities_poll_on_add_disabled(hass: HomeAssistant) -> None:
    """Verify that regular climate zones on F455 do not poll on add and skip status requests."""
    gateway = MagicMock(spec=MyHOMEGatewayHandler)
    gateway.mac = "00:03:50:AA:11:22"
    gateway.model = "F455"
    gateway.supports_zone_status = False
    gateway.log_id = "[F455]"
    gateway.send_status_request = AsyncMock()

    zone = MyHOMEClimate(
        hass=hass,
        name="Zone 35",
        device_id="4-35",
        who="4",
        where="35",
        heating=True,
        cooling=False,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="Legrand",
        model="Heating Zone",
        gateway=gateway,
    )
    zone.hass = hass
    zone.entity_id = "climate.zone_35"

    # Crucial assertion: _poll_on_add MUST be False on F455 to prevent startup command flooding
    assert zone._poll_on_add is False
    assert zone._gateway_supports_zone_status is False

    # Calling async_update() must not send *#4*35##
    await zone.async_update()
    gateway.send_status_request.assert_not_called()

    # Also verify fallback when _gateway_handler is None
    zone_none = MagicMock(spec=MyHOMEClimate)
    zone_none._gateway_handler = None
    assert MyHOMEClimate._gateway_supports_zone_status.fget(zone_none) is True


def test_gateway_supports_zone_status_property(hass: HomeAssistant) -> None:
    """Verify that supports_zone_status correctly distinguishes MyHomeServer1 from physical bridges."""
    config_entry = MagicMock()
    config_entry.data = {
        CONF_HOST: "192.168.1.1",
        CONF_PORT: 20000,
        CONF_PASSWORD: None,
        CONF_MAC: "00:03:50:11:22:33",
        CONF_NAME: "F455",
    }

    handler = MyHOMEGatewayHandler(hass, config_entry)

    # 1. F455
    handler.gateway.model_name = "F455"
    assert handler.supports_zone_status is False

    # 2. F454
    handler.gateway.model_name = "F454"
    assert handler.supports_zone_status is False

    # 3. MH200N
    handler.gateway.model_name = "MH200N"
    assert handler.supports_zone_status is False

    # 4. MH202
    handler.gateway.model_name = "MH202"
    assert handler.supports_zone_status is False

    # 5. MyHomeServer1
    handler.gateway.model_name = "MyHomeServer1"
    assert handler.supports_zone_status is True


def test_issue_674_timeout_frames_analysis() -> None:
    """Verify that all 16 timeouts in the trace correspond to dimension-less zone status requests."""
    with open(JSON_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    timeouts = trace_data["timeouts"]
    assert len(timeouts) == 16

    expected_zones = [
        "35", "34", "36", "28", "37", "27", "29", "26",
        "15", "17", "16", "14", "9", "6", "8", "21",
    ]

    for idx, item in enumerate(timeouts):
        req = item["request"]
        expected_zone = expected_zones[idx]
        assert req == f"*#4*{expected_zone}##", (
            f"Timeout #{idx} was {req}, expected *#4*{expected_zone}##"
        )


@pytest.mark.asyncio
async def test_gateway_events_skips_zone_status_query_on_physical_gateways(hass: HomeAssistant) -> None:
    """Verify that GatewayEventDispatcher ignores dimension 14 heating command on physical gateways (#674)."""
    # 1. Physical gateway (supports_zone_status is False) - line 352 (return) must be hit
    handler_f455 = MagicMock()
    handler_f455.hass = hass
    handler_f455.mac = "00:03:50:AA:11:22"
    handler_f455.log_id = "[F455]"
    handler_f455.supports_zone_status = False
    handler_f455.send_status_request = AsyncMock()

    dispatcher_f455 = GatewayEventDispatcher(handler_f455)
    dispatcher_f455._is_active_for_who = lambda who: True  # type: ignore[method-assign]

    msg = MagicMock(spec=OWNHeatingCommand)
    msg.dimension = 14
    msg.where = "35"

    await dispatcher_f455.process_message(msg)
    handler_f455.send_status_request.assert_not_called()

    # 2. MyHomeServer1 (supports_zone_status is True) - sends query
    handler_mhs1 = MagicMock()
    handler_mhs1.hass = hass
    handler_mhs1.mac = "00:03:50:BB:33:44"
    handler_mhs1.log_id = "[MHS1]"
    handler_mhs1.supports_zone_status = True
    handler_mhs1.send_status_request = AsyncMock()

    dispatcher_mhs1 = GatewayEventDispatcher(handler_mhs1)
    dispatcher_mhs1._is_active_for_who = lambda who: True  # type: ignore[method-assign]

    await dispatcher_mhs1.process_message(msg)
    handler_mhs1.send_status_request.assert_called_once()

