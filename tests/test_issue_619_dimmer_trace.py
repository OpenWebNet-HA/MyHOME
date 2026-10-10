"""Tests for #619: Authentic BTicino F453AV & F414 Dimmer Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
BTicino F453AV gateway testing an F414 modular phase-cut dimmer on WHERE 13
(with a 35 W halogen load) replay deterministically without errors, and that
unloaded dimmer channels reporting WHAT 19 handle open circuit cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)
from custom_components.myhome.light import ColorMode

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_619"
F453AV_TRACE_FILE = TRACES_DIR / "myhome_trace_F453AV_all_2026-10-04T08-28-13.json"
F453AV_SWEEP_FILE = TRACES_DIR / "myhome_sweep_F453AV_all_2026-10-04T19-45-11.json"


@pytest.mark.asyncio
async def test_f453av_dimmer_trace_replay(hass: HomeAssistant) -> None:
    """Replay all frames from the physical F453AV bus capture.

    Ensures every frame across WHO 1 (lights 13, 32, 42, 33, 52, 72) and
    WHO 13 (gateway heartbeat) replayed cleanly through the event dispatcher.
    """
    assert F453AV_TRACE_FILE.is_file(), f"Missing trace fixture: {F453AV_TRACE_FILE}"

    with open(F453AV_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    gateway_info = trace_data["gateway"]
    assert gateway_info["model"] == "F453AV"
    assert gateway_info["identification"]["who13_code"] == "12"

    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 23

    mac = "00:03:50:00:04:53"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.0.155",
            CONF_PORT: 20000,
            CONF_PASSWORD: "open",
            CONF_MAC: mac,
            CONF_NAME: "F453AV Gateway",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "F453AV Web Server",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "1.0",
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
    for item in raw_frames:
        raw = item.get("raw")
        if not raw or raw in ("*#*1##", "*#*0##"):
            continue

        try:
            msg = OWNMessage.parse(raw)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"Failed to parse authentic F453AV frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            replayed += 1

    await hass.async_block_till_done()
    assert replayed == 23

    # Check light 13 (F414 with 35 W halogen load)
    state_13 = hass.states.get("light.light_13")
    assert state_13 is not None
    # Final frame in trace was *1*0*13## -> turned OFF
    assert state_13.state == "off"
    assert state_13.attributes.get("supported_color_modes") == [ColorMode.BRIGHTNESS]
    assert "no_load" not in state_13.attributes
    assert "unknown_state" not in state_13.attributes

    # Check unloaded channel 14 emitting WHAT 19
    async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse("*1*19*14##"))
    await hass.async_block_till_done()

    state_14 = hass.states.get("light.light_14")
    assert state_14 is not None
    assert state_14.state == "off"
    assert state_14.attributes.get("no_load") is True
    assert "unknown_state" not in state_14.attributes
    assert ColorMode.BRIGHTNESS in state_14.attributes.get("supported_color_modes", [])

    # Sending *1*0*14## keeps no_load: True (turning off an unloaded channel does not reconnect load)
    async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse("*1*0*14##"))
    await hass.async_block_till_done()
    state_14 = hass.states.get("light.light_14")
    assert state_14.state == "off"
    assert state_14.attributes.get("no_load") is True

    # Reconnecting a load and sending level 4 clears no_load
    async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse("*1*4*14##"))
    await hass.async_block_till_done()
    state_14 = hass.states.get("light.light_14")
    assert state_14.state == "on"
    assert "no_load" not in state_14.attributes

    # Device health has 0 faults raised throughout the entire trace and test
    assert handler.device_health.faults == []


@pytest.mark.asyncio
async def test_f453av_sweep_trace_replay(hass: HomeAssistant) -> None:
    """Replay all 157 frames from the authentic physical F453AV bus sweep capture.

    Ensures every frame across WHO 1 (lights), WHO 2 (covers), WHO 4 (climate),
    WHO 5 (alarm), WHO 13 (gateway), WHO 16 (audio), WHO 18 (energy), and
    WHO 1001 (actuator diagnostics) replays cleanly without errors, verifying that
    multiple unloaded F414 dimmers on WHERE 13, 14, 24, 31, 34 are correctly
    recognized with no_load=True and promoted to dimmable.
    """
    assert F453AV_SWEEP_FILE.is_file(), f"Missing trace fixture: {F453AV_SWEEP_FILE}"

    with open(F453AV_SWEEP_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    gateway_info = trace_data["gateway"]
    assert gateway_info["model"] == "F453AV"
    assert gateway_info["firmware"] == "3.0.14"
    assert gateway_info["identification"]["who13_code"] == "12"
    assert gateway_info["identification"]["who13_firmware"] == "3.0.14"

    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 157

    mac = "00:03:50:00:04:53"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.0.155",
            CONF_PORT: 20000,
            CONF_PASSWORD: "open",
            CONF_MAC: mac,
            CONF_NAME: "F453AV Gateway",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "F453AV Web Server",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "3.0.14",
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
        if not raw or raw in ("*#*1##", "*#*0##"):
            continue

        try:
            msg = OWNMessage.parse(raw)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"Failed to parse authentic F453AV frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            replayed += 1
            if hasattr(msg, "who") and msg.who is not None:
                whos_seen.add(str(msg.who))
            elif item.get("who"):
                whos_seen.add(str(item["who"]))

    await hass.async_block_till_done()
    assert replayed == 157
    assert {"1", "2", "4", "5", "13", "16", "18", "1001"}.issubset(whos_seen)

    # Verify unloaded F414 dimmers reporting WHAT 19
    for where in ("13", "14", "24", "31", "34"):
        state = hass.states.get(f"light.light_{where}")
        assert state is not None, f"Expected light entity for WHERE {where}"
        assert state.state == "off"
        assert state.attributes.get("no_load") is True
        assert "unknown_state" not in state.attributes
        assert ColorMode.BRIGHTNESS in state.attributes.get("supported_color_modes", [])

    # Verify standard lights reporting normal ON/OFF
    state_71 = hass.states.get("light.light_71")
    assert state_71 is not None
    assert state_71.state == "on"
    assert "no_load" not in state_71.attributes

    state_11 = hass.states.get("light.light_11")
    assert state_11 is not None
    assert state_11.state == "off"
    assert "no_load" not in state_11.attributes

    # Device health has 0 faults raised throughout the entire trace replay
    assert handler.device_health.faults == []


