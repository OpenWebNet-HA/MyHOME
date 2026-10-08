"""Tests for #649: Authentic MyHomeServer1 Physical Plant Trace Replay & Conformance.

Verifies that authentic on-wire OpenWebNet monitor frames captured from a physical
BTicino MyHomeServer1 gateway (firmware 3.87.13, 2026-10-07 live session)
can be deterministically parsed and replayed through the integration event dispatcher
without exceptions or regressions.

Hardware & Plant Topology Under Test:
- Gateway: BTicino MyHomeServer1 (Server MyHOME_Up, FW 3.87.13)
- Thermostat: Zone 60 (heating plant with no central unit `#0`)
- Meter: F520 single-phase energy meter (address 51)
- Light: Relay actuator 43 (non-dimmer)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    OWNEvent,
    OWNHeatingEvent,
    OWNLightingEvent,
    OWNMessage,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_649"
RAW_LOG_FILE = TRACES_DIR / "live_test_monitor_2026-10-07.log"
JSON_TRACE_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_live_2026-10-07.json"


@pytest.mark.asyncio
async def test_myhomeserver1_live_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 107 on-wire frames from the physical MyHomeServer1 plant capture."""
    assert RAW_LOG_FILE.is_file(), f"Missing raw trace fixture: {RAW_LOG_FILE}"
    assert JSON_TRACE_FILE.is_file(), f"Missing JSON trace fixture: {JSON_TRACE_FILE}"

    with open(JSON_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    gateway_info = trace_data["gateway"]
    assert gateway_info["model"] == "MyHomeServer1"
    assert gateway_info["firmware"] == "3.87.13"
    assert gateway_info["identification"]["profile"] == "MyHomeServer1Profile"

    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 107

    # Verify raw log frame count matches JSON trace exactly
    with open(RAW_LOG_FILE, "r", encoding="utf-8") as f:
        raw_lines = [line.strip() for line in f if line.strip()]
    assert len(raw_lines) == 107

    mac = "00:03:50:AA:BB:CC"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.0.218",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "MyHomeServer1",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:webserver:1",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "3.87.13",
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

    handler = hass.data[DOMAIN][mac][CONF_ENTITY]
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
    assert replayed == 107
    assert "1" in whos_seen, "Trace must contain WHO 1 lighting frames"
    assert "4" in whos_seen, "Trace must contain WHO 4 thermoregulation frames"


def test_myhomeserver1_live_trace_log_to_json_raw_equality() -> None:
    """Verify that every frame in the raw monitor log matches the JSON trace archive exactly.

    Ensures 1:1 parity in sequence order, timestamp, and raw OpenWebNet frame string
    between the authentic ground-truth log and the derived JSON archive.
    """
    assert RAW_LOG_FILE.is_file(), f"Missing raw trace fixture: {RAW_LOG_FILE}"
    assert JSON_TRACE_FILE.is_file(), f"Missing JSON trace fixture: {JSON_TRACE_FILE}"

    with open(RAW_LOG_FILE, "r", encoding="utf-8") as f:
        raw_lines = [line.strip() for line in f if line.strip()]

    with open(JSON_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    json_frames = trace_data["frames"]
    assert len(raw_lines) == 107
    assert len(json_frames) == 107

    for idx, (log_line, json_frame) in enumerate(zip(raw_lines, json_frames)):
        parts = log_line.split(maxsplit=2)
        assert len(parts) == 3, f"Line {idx + 1} malformed: {log_line}"
        ts_str, marker, raw_frame = parts
        assert marker == "OWN", f"Line {idx + 1} unexpected marker: {marker}"
        assert raw_frame == json_frame["raw"], (
            f"Frame mismatch at line {idx + 1}: log '{raw_frame}' != json '{json_frame['raw']}'"
        )
        assert abs(float(ts_str) - json_frame["timestamp"]) < 1e-4, (
            f"Timestamp mismatch at line {idx + 1}: log {ts_str} != json {json_frame['timestamp']}"
        )


def test_myhomeserver1_live_trace_grammar_and_semantics() -> None:
    """Verify syntactic and semantic properties of key frames from the physical trace."""
    # 1. Zone 60 temperature report (Dimension 0: 23.5 °C)
    temp_msg = OWNMessage.parse("*#4*60*0*0235##")
    assert isinstance(temp_msg, OWNHeatingEvent)
    assert temp_msg.zone == 60
    assert temp_msg.main_temperature == 23.5
    assert temp_msg.message_type == "main_temperature"

    # 2. Zone 60 dimension 12 active setpoint (23.0 °C, generic mode 3)
    setpoint_msg = OWNMessage.parse("*#4*60*12*0230*3##")
    assert isinstance(setpoint_msg, OWNHeatingEvent)
    assert setpoint_msg.zone == 60
    assert setpoint_msg.local_set_temperature == 23.0
    assert setpoint_msg.message_type == "local_target_temperature"

    # 3. Zone 60 dimension 14 programmed setpoint (23.0 °C, generic mode 3)
    prog_setpoint = OWNMessage.parse("*#4*60*14*0230*3##")
    assert isinstance(prog_setpoint, OWNHeatingEvent)
    assert prog_setpoint.zone == 60
    assert prog_setpoint.set_temperature == 23.0
    assert prog_setpoint.message_type == "target_temperature"

    # 4. Zone 60 local offset (Dimension 13: 0.0 °C)
    offset_msg = OWNMessage.parse("*#4*60*13*00##")
    assert isinstance(offset_msg, OWNHeatingEvent)
    assert offset_msg.zone == 60
    assert offset_msg.local_offset == 0

    # 5. Zone 60 season report: heating (*4*1*60##)
    season_heat = OWNMessage.parse("*4*1*60##")
    assert isinstance(season_heat, OWNHeatingEvent)
    assert season_heat.zone == 60

    # 6. Zone 60 season report: conditioning (*4*0*60##)
    season_cool = OWNMessage.parse("*4*0*60##")
    assert isinstance(season_cool, OWNHeatingEvent)
    assert season_cool.zone == 60

    # 7. Zone 60 antifreeze mode (*4*102*60##) and associated setpoint 7.0 °C (*#4*60*12*0070*3##)
    antifreeze_mode = OWNMessage.parse("*4*102*60##")
    assert isinstance(antifreeze_mode, OWNHeatingEvent)
    assert antifreeze_mode.zone == 60
    assert antifreeze_mode.mode == "off"

    antifreeze_setpoint = OWNMessage.parse("*#4*60*12*0070*3##")
    assert isinstance(antifreeze_setpoint, OWNHeatingEvent)
    assert antifreeze_setpoint.zone == 60
    assert antifreeze_setpoint.local_set_temperature == 7.0

    # 8. Zone 60 actuator state frames
    actuator_1 = OWNMessage.parse("*#4*60#1*20*0##")
    assert isinstance(actuator_1, OWNHeatingEvent)
    assert actuator_1.zone == 60
    assert actuator_1.is_active() is False

    actuator_2 = OWNMessage.parse("*#4*60#2*20*5##")
    assert isinstance(actuator_2, OWNHeatingEvent)
    assert actuator_2.zone == 60

    # 9. Lighting Relay 43 ON and OFF states
    light_on = OWNMessage.parse("*1*1*43##")
    assert isinstance(light_on, OWNLightingEvent)
    assert light_on.where == "43"
    assert light_on.is_on is True

    light_off = OWNMessage.parse("*1*0*43##")
    assert isinstance(light_off, OWNLightingEvent)
    assert light_off.where == "43"
    assert light_off.is_on is False

    # 10. Lighting Relay 43 Dimension 2 timer readback (0*0*0 = timer expired)
    timer_msg = OWNMessage.parse("*#1*43*2*0*0*0##")
    assert isinstance(timer_msg, (OWNLightingEvent, OWNEvent))
    assert timer_msg.where == "43"
