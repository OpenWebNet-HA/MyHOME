"""Tests for #667: MH200 Video Door Entry, Intercom & Impulse Gate/Garage Trace Replay.

Replays authentic on-wire frames recorded from a physical BTicino MH200 installation
during an outdoor bell ring, video intercom call, speech, handset hang-up, and
impulse gate/garage door operations.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from OWNd.message import (
    OWNEvent,
    OWNLightingEvent,
    OWNMessage,
    OWNSoundEvent,
)

try:
    from OWNd.message import OWNDoorEntryEvent
except ImportError:  # pragma: no cover
    from custom_components.myhome.gateway_events import (
        OWNDoorEntryEvent,  # type: ignore[assignment]
    )
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import DOMAIN
from custom_components.myhome.gateway import MyHOMEGatewayHandler

FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_667"
    / "myhome_trace_MH200_intercom_bell_gate_2026-10-09.json"
)


def test_issue_667_fixture_present_and_valid() -> None:
    """Verify that the issue 667 fixture file exists and has expected metadata."""
    assert FIXTURE_PATH.is_file(), f"Missing trace fixture: {FIXTURE_PATH}"

    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert data["gateway"]["model"] == "MH200"
    assert data["gateway"]["firmware"] == "2.1.0"
    assert len(data["frames"]) == 34


def test_issue_667_all_frames_parse() -> None:
    """Verify every frame in the authentic trace parses into an OWNMessage / OWNEvent."""
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    raw_frames = [f["raw"] for f in data["frames"]]

    for raw in raw_frames:
        msg = OWNEvent.parse(raw)
        assert msg is not None, f"Failed to parse frame: {raw}"
        assert isinstance(msg, OWNEvent)

    # Spot-check critical frames
    call_msg = OWNEvent.parse("*8*1#1#4*74##")
    assert isinstance(call_msg, OWNEvent)
    assert call_msg.who == 8
    assert getattr(call_msg, "what", getattr(call_msg, "_what", None)) == 1
    assert getattr(call_msg, "what_param", getattr(call_msg, "_what_param", None)) == ["1", "4"]
    assert call_msg.where == "74"

    hangup_msg = OWNEvent.parse("*8*9#1#4*73##")
    assert isinstance(hangup_msg, OWNEvent)
    assert hangup_msg.who == 8
    assert getattr(hangup_msg, "what", getattr(hangup_msg, "_what", None)) == 9
    assert getattr(hangup_msg, "what_param", getattr(hangup_msg, "_what_param", None)) == ["1", "4"]
    assert hangup_msg.where == "73"

    camera_off_msg = OWNEvent.parse("*6*9##")
    assert isinstance(camera_off_msg, (OWNDoorEntryEvent, OWNEvent))
    if getattr(camera_off_msg, "is_valid", False):
        assert camera_off_msg.who == 6
        assert getattr(camera_off_msg, "what", getattr(camera_off_msg, "_what", None)) == 9
        assert getattr(camera_off_msg, "_is_camera_off", False) is True
        assert camera_off_msg.human_readable_log == "Door entry camera switched OFF."

    garage_impulse = OWNEvent.parse("*1*1*71##")
    assert isinstance(garage_impulse, OWNLightingEvent)
    assert garage_impulse.who == 1
    assert garage_impulse.where == "71"

    gate_impulse = OWNEvent.parse("*1*1*72##")
    assert isinstance(gate_impulse, OWNLightingEvent)
    assert gate_impulse.who == 1
    assert gate_impulse.where == "72"

    sound_frame = OWNEvent.parse("*#16*81*1*1##")
    assert isinstance(sound_frame, OWNSoundEvent)
    assert sound_frame.who == 16


@pytest.mark.asyncio
async def test_issue_667_trace_full_replay(hass: HomeAssistant) -> None:
    """Replay all 34 authentic trace frames through MyHOMEGatewayHandler._process_message.

    Verifies that:
    1. All 34 frames are processed without throwing any exceptions.
    2. The WHO 8 entrance panel call (*8*1#1#4*74##) fires myhome_doorbell_event.
    3. Dispatcher signals are emitted for every valid message.
    4. WHO 13 dimension 15 device type detection resolves gateway model to MH200.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_PASSWORD: "open",
            CONF_MAC: "00:03:50:00:88:88",
        },
        entry_id="test_mh200_issue_667_entry",
    )
    entry.add_to_hass(hass)

    gateway = MyHOMEGatewayHandler(hass, entry)

    captured_events: list[Event] = []
    unsub_event = hass.bus.async_listen(
        "myhome_doorbell_event",
        lambda event: captured_events.append(event),
    )

    dispatcher_messages: list[OWNMessage] = []

    def _capture_dispatcher(msg: OWNMessage) -> None:
        dispatcher_messages.append(msg)

    unsub_dispatcher = async_dispatcher_connect(
        hass,
        f"myhome_message_{gateway.mac}",
        _capture_dispatcher,
    )

    try:
        data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        for frame_entry in data["frames"]:
            raw = frame_entry["raw"]
            msg = OWNEvent.parse(raw)
            assert msg is not None
            await gateway._process_message(msg)

        await hass.async_block_till_done()

        # 34 frames should all dispatch to message listeners
        assert len(dispatcher_messages) == 34

        # Exactly 1 doorbell ring occurred during the capture session
        assert len(captured_events) == 1
        assert captured_events[0].data == {
            "where": "74",
            "event": "call",
            "is_broadcast": False,
            "gateway_mac": gateway.mac,
            "entry_id": entry.entry_id,
        }

        # Verify gateway model was detected as MH200 from WHO 13 dimension 15
        assert gateway.gateway.model == "MH200"
    finally:
        unsub_event()
        unsub_dispatcher()
