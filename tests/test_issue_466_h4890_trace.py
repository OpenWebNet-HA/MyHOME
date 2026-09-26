"""Tests for #466: Real-World BTicino H4890 / 4890-Family Gateway Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
H4890 3.5" Touch Screen gateway (contributed by @nicolacavallo84 in issue #466
comment 5846053726) can be deterministically parsed and replayed against the
integration state machine without exceptions or regressions.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.alarm_control_panel.const import AlarmControlPanelState
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
from OWNd.message import OWNAlarmEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.alarm_control_panel import MyHOMEAlarmControlPanel
from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_466"
SWEEP_TRACE_FILE = TRACES_DIR / "myhome_sweep_H4890_all_2026-09-26T11-48-10.json"
TRACE_ALARM_FILE = TRACES_DIR / "myhome_trace_H4890_all_2026-09-26T11-51-26.json"
TRACE_AUDIO_FILE = TRACES_DIR / "myhome_trace_H4890_all_2026-09-26T11-52-48.json"


@pytest.mark.asyncio
async def test_h4890_sweep_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 200 on-wire frames from the physical H4890 bus sweep capture.

    Ensures every frame across WHO 1 (lights), WHO 2 (covers), WHO 9 (auxiliary),
    WHO 16 (audio), WHO 18 (energy), WHO 22 (sound diffusion), and WHO 25 (CEN+)
    replays cleanly through the event dispatcher.
    """
    assert SWEEP_TRACE_FILE.is_file(), f"Missing trace fixture: {SWEEP_TRACE_FILE}"

    with open(SWEEP_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "H4890"
    assert trace_data["gateway"]["firmware"] == "4.0.15"
    assert trace_data["gateway"]["identification"]["who13_code"] == "200"

    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 200

    mac = "00:03:50:00:48:90"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.90",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "H4890",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "H4890 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "4.0.15",
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
            pytest.fail(f"Failed to parse authentic H4890 frame {raw!r}: {exc}")

        async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        replayed += 1
        if hasattr(msg, "who") and msg.who:
            whos_seen.add(str(msg.who))

    await hass.async_block_till_done()
    assert replayed == 200
    assert {"1", "2", "9", "16", "18", "22", "25"}.issubset(whos_seen)

    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_h4890_alarm_trace_replay_and_state_transitions(hass: HomeAssistant) -> None:
    """Replay authentic WHO 5 burglar alarm frames from the H4890 capture."""
    assert TRACE_ALARM_FILE.is_file(), f"Missing trace fixture: {TRACE_ALARM_FILE}"

    with open(TRACE_ALARM_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 51

    alarm_frames: list[str] = []
    for item in raw_frames:
        raw = item.get("raw")
        assert raw is not None
        msg = OWNMessage.parse(raw)
        assert msg is not None
        if item.get("who") == "5":
            alarm_frames.append(raw)

    # Authentic alarm frames present in trace
    assert "*5*9*0##" in alarm_frames  # Disarm
    assert "*5*1*0##" in alarm_frames  # Arm away
    assert "*5*11*#1##" in alarm_frames
    assert "*5*11*#5##" in alarm_frames
    assert "*5*18*#7##" in alarm_frames

    # Test entity state transitions with authentic alarm frames
    gateway = MagicMock()
    gateway.mac = "00:03:50:00:48:90"
    gateway.log_id = "[H4890]"
    gateway.send = AsyncMock()

    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        alarm = MyHOMEAlarmControlPanel(
            hass=hass,
            name="Main Alarm",
            entity_name=None,
            device_id="0",
            who="5",
            where="0",
            manufacturer="BTicino",
            model="Burglar Alarm",
            gateway=gateway,
        )
        alarm.hass = hass
        alarm.async_schedule_update_ha_state = MagicMock()

        # Feed disarm frame *5*9*0##
        alarm.handle_event(OWNAlarmEvent.parse("*5*9*0##"))
        assert alarm.alarm_state == AlarmControlPanelState.DISARMED

        # Feed arm away frame *5*1*0##
        alarm.handle_event(OWNAlarmEvent.parse("*5*1*0##"))
        assert alarm.alarm_state == AlarmControlPanelState.ARMED_AWAY

        # Disarm again
        alarm.handle_event(OWNAlarmEvent.parse("*5*9*0##"))
        assert alarm.alarm_state == AlarmControlPanelState.DISARMED


def test_h4890_audio_trace_replay() -> None:
    """Replay all 32 frames from the H4890 audio and sound diffusion trace."""
    assert TRACE_AUDIO_FILE.is_file(), f"Missing trace fixture: {TRACE_AUDIO_FILE}"

    with open(TRACE_AUDIO_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    frames = trace_data["frames"]
    assert len(frames) == 32

    whos_seen: set[str] = set()
    for frame in frames:
        raw = frame.get("raw")
        assert raw is not None
        msg = OWNMessage.parse(raw)
        assert msg is not None
        if hasattr(msg, "who") and msg.who:
            whos_seen.add(str(msg.who))

    assert {"9", "16", "22"}.issubset(whos_seen)
