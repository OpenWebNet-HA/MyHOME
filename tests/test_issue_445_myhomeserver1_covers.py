"""Tests for #445: Real-World MyHomeServer1 & LN-4660M2 Centralized Cover Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
BTicino MyHomeServer1 gateway and LN-4660M2 centralized shutter button
(contributed by @f18m in issue #445 and #466 comment 5853591733)
can be deterministically parsed and replayed against the integration state machine
without exceptions or regressions.
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
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNAutomationEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_445"
UP_TRACE_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_LN4660M2_centralized_up_then_stop_2026-09-26.json"
DOWN_TRACE_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_LN4660M2_centralized_down_then_stop_2026-09-26.json"
STATIONARY_STOP_TRACE_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_LN4660M2_stationary_stop_2026-09-28.json"
LOCAL_KEYPAD_TRACE_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_LN4660M2_local_room_down_then_stop_2026-09-28.json"


@pytest.mark.asyncio
async def test_myhomeserver1_up_then_stop_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 16 frames from the LN-4660M2 centralized UP-then-STOP capture.

    Ensures that multi-parameter Advanced Automation commands on General address 0
    (*2*11#...*0##, *2*10#...*0##) and actuator Dimension 10 feedback across
    points 02, 03, 04, 08, 09, 0010, 0011 replay cleanly through the event dispatcher.
    """
    assert UP_TRACE_FILE.is_file(), f"Missing trace fixture: {UP_TRACE_FILE}"

    with open(UP_TRACE_FILE, encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "MyHomeServer1"
    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 16

    mac = "00:03:50:00:01:01"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MyHomeServer1",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "MyHomeServer1 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "Unknown",
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
            pytest.fail(f"Failed to parse authentic MyHomeServer1 frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 16

    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_myhomeserver1_down_then_stop_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 26 frames from the LN-4660M2 centralized DOWN-then-STOP capture.

    Ensures that Advanced DOWN (*2*12#100#001#1*0##), limit position reports (0%),
    intermediate position reports (61%, 50%), and 4-digit actuator addresses (0010, 0011)
    replay cleanly without unhandled exceptions.
    """
    assert DOWN_TRACE_FILE.is_file(), f"Missing trace fixture: {DOWN_TRACE_FILE}"

    with open(DOWN_TRACE_FILE, encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "MyHomeServer1"
    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 26

    mac = "00:03:50:00:01:02"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.51",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MyHomeServer1",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "MyHomeServer1 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "Unknown",
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
            pytest.fail(f"Failed to parse authentic MyHomeServer1 frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 26

    await hass.config_entries.async_unload(entry.entry_id)


def test_ln4660m2_advanced_general_automation_events_and_dim10() -> None:
    """Verify Advanced General Automation commands and Dimension 10 parsing (#445, #466)."""
    # 1. Advanced General UP
    up_cmd = OWNMessage.parse("*2*11#100#001#1*0##")
    assert isinstance(up_cmd, OWNAutomationEvent)
    assert up_cmd.who == 2
    assert up_cmd.where == "0"
    assert up_cmd._what == 11
    assert up_cmd._what_param == ["100", "001", "1"]
    assert up_cmd.is_general is True
    assert up_cmd.is_opening is True
    assert up_cmd.is_closing is False

    # 2. Advanced General DOWN
    down_cmd = OWNMessage.parse("*2*12#100#001#1*0##")
    assert isinstance(down_cmd, OWNAutomationEvent)
    assert down_cmd.who == 2
    assert down_cmd.where == "0"
    assert down_cmd._what == 12
    assert down_cmd._what_param == ["100", "001", "1"]
    assert down_cmd.is_general is True
    assert down_cmd.is_opening is False
    assert down_cmd.is_closing is True

    # 3. Advanced General STOP
    stop_cmd = OWNMessage.parse("*2*10#001#1*0##")
    assert isinstance(stop_cmd, OWNAutomationEvent)
    assert stop_cmd.who == 2
    assert stop_cmd.where == "0"
    assert stop_cmd._what == 10
    assert stop_cmd._what_param == ["001", "1"]
    assert stop_cmd.is_general is True
    assert stop_cmd.is_opening is False
    assert stop_cmd.is_closing is False

    # 4. Multi-parameter Dimension 10 feedback on 4-digit address
    pos_26 = OWNMessage.parse("*#2*0010*10*10*26*001*0##")
    assert isinstance(pos_26, OWNAutomationEvent)
    assert pos_26.who == 2
    assert pos_26.where == "0010"
    assert pos_26.dimension == 10
    assert pos_26.current_position == 26
    assert pos_26._dimension_value == ["10", "26", "001", "0"]
    assert pos_26._priority == 1

    # 5. Position 86% feedback on standard 2-digit address
    pos_86 = OWNMessage.parse("*#2*02*10*10*86*001*0##")
    assert isinstance(pos_86, OWNAutomationEvent)
    assert pos_86.who == 2
    assert pos_86.where == "02"
    assert pos_86.dimension == 10
    assert pos_86.current_position == 86


@pytest.mark.asyncio
async def test_centralized_button_fires_general_automation_bus_events(hass: HomeAssistant) -> None:
    """Verify LN-4660M2 centralized button presses trigger myhome_general_automation_event (#445)."""
    mac = "00:03:50:00:01:03"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.52",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MyHomeServer1",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "MyHomeServer1 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "Unknown",
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

    captured_events: list[dict] = []

    def on_event(event: Event) -> None:
        captured_events.append(event.data)

    hass.bus.async_listen("myhome_general_automation_event", on_event)

    # 1. Dispatch Centralized UP
    up_msg = OWNMessage.parse("*2*11#100#001#1*0##")
    await handler._process_message(up_msg)
    await hass.async_block_till_done()

    # 2. Dispatch Centralized DOWN
    down_msg = OWNMessage.parse("*2*12#100#001#1*0##")
    await handler._process_message(down_msg)
    await hass.async_block_till_done()

    # 3. Dispatch Centralized STOP
    stop_msg = OWNMessage.parse("*2*10#001#1*0##")
    await handler._process_message(stop_msg)
    await hass.async_block_till_done()

    assert len(captured_events) == 3
    assert captured_events[0] == {
        "message": "*2*11#100#001#1*0##",
        "event": "open",
        "where": "0",
        "gateway_mac": mac,
        "entry_id": entry.entry_id,
    }
    assert captured_events[1] == {
        "message": "*2*12#100#001#1*0##",
        "event": "close",
        "where": "0",
        "gateway_mac": mac,
        "entry_id": entry.entry_id,
    }
    assert captured_events[2] == {
        "message": "*2*10#001#1*0##",
        "event": "stop",
        "where": "0",
        "gateway_mac": mac,
        "entry_id": entry.entry_id,
    }

    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_myhomeserver1_stationary_stop_trace_fires_single_general_stop_event(hass: HomeAssistant) -> None:
    """Replay the 2026-09-28 stationary STOP/PRESET capture (#445 comment 5868017035).

    Confirms that pressing the LN-4660M2 STOP/PRESET button while every shutter is
    already stationary dispatches exactly one `myhome_general_automation_event` with
    event `stop`, followed only by per-actuator Dimension 10 / point-stop telemetry
    that must not itself trigger a general automation event (WHERE != "0").
    """
    assert STATIONARY_STOP_TRACE_FILE.is_file(), f"Missing trace fixture: {STATIONARY_STOP_TRACE_FILE}"

    with open(STATIONARY_STOP_TRACE_FILE, encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "MyHomeServer1"
    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 15

    mac = "00:03:50:00:01:04"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.53",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MyHomeServer1",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "MyHomeServer1 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "Unknown",
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

    captured_events: list[dict] = []
    hass.bus.async_listen("myhome_general_automation_event", lambda event: captured_events.append(event.data))

    replayed = 0
    for item in raw_frames:
        raw = item.get("raw")
        if not raw or raw in ("*#*1##", "*#*0##"):
            continue

        try:
            msg = OWNMessage.parse(raw)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"Failed to parse authentic MyHomeServer1 frame {raw!r}: {exc}")

        if msg is not None:
            await handler._process_message(msg)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 15

    # Exactly one general (centralized) STOP event, from the *2*10#001#1*0## frame;
    # the seven per-actuator Dimension 10 reports and *2*0*WHERE## confirmations
    # that follow must not themselves fire a general automation event.
    assert len(captured_events) == 1
    assert captured_events[0]["event"] == "stop"
    assert captured_events[0]["where"] == "0"
    assert captured_events[0]["gateway_mac"] == mac

    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_myhomeserver1_local_room_keypad_does_not_fire_general_automation_event(hass: HomeAssistant) -> None:
    """Replay the 2026-09-28 local room keypad capture (#445 comment 5868017035).

    A local room button (as opposed to the LN-4660M2 centralized button) moves a
    single actuator at its own point address using the basic `*2*WHAT*WHERE##`
    syntax, not the multi-parameter Advanced form seen on General address 0. It
    must not dispatch `myhome_general_automation_event`, which is reserved for
    centralized/area/group commands.
    """
    assert LOCAL_KEYPAD_TRACE_FILE.is_file(), f"Missing trace fixture: {LOCAL_KEYPAD_TRACE_FILE}"

    with open(LOCAL_KEYPAD_TRACE_FILE, encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "MyHomeServer1"
    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 4

    mac = "00:03:50:00:01:05"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.54",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MyHomeServer1",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "MyHomeServer1 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "Unknown",
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

    captured_events: list[dict] = []
    hass.bus.async_listen("myhome_general_automation_event", lambda event: captured_events.append(event.data))

    replayed = 0
    for item in raw_frames:
        raw = item.get("raw")
        if not raw or raw in ("*#*1##", "*#*0##"):
            continue

        try:
            msg = OWNMessage.parse(raw)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"Failed to parse authentic MyHomeServer1 frame {raw!r}: {exc}")

        assert isinstance(msg, OWNAutomationEvent)
        assert msg.is_general is False

        if msg is not None:
            await handler._process_message(msg)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 4
    assert captured_events == []

    await hass.config_entries.async_unload(entry.entry_id)
