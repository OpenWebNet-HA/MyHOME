"""Tests for #564: Real-World BTicino MH202 Secondary Gateway Burglar Alarm Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
MH202 gateway acting as a secondary gateway alongside MyHomeServer1
(contributed by @nicolacavallo84 in issue #564 comment 5917195080) can be
deterministically parsed and replayed against the integration state machine
without exceptions or regressions.
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

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_564"
TRACE_ALARM_FILE = TRACES_DIR / "myhome_trace_MH202_who5_alarm_2026-09-30T18-13-13.json"


@pytest.mark.asyncio
async def test_mh202_who5_alarm_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay authentic WHO 5 burglar alarm frames from the MH202 capture (#564).

    Ensures every frame across arming (*5*1*0##), disarming (*5*9*0##), active
    zones (*5*11*#n##) and inactive zones (*5*18*#n##) replays cleanly through
    the event dispatcher.
    """
    assert TRACE_ALARM_FILE.is_file(), f"Missing trace fixture: {TRACE_ALARM_FILE}"

    with open(TRACE_ALARM_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "MH202"
    assert trace_data["gateway"]["firmware"] == "1.0"
    assert trace_data["gateway"]["identification"]["who13_code"] == "200"

    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 18

    mac = "00:03:50:00:02:02"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.20",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MH202",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "MH202 Gateway",
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
    alarm_frames: list[str] = []

    for item in raw_frames:
        raw = item.get("raw")
        if not raw or raw in ("*#*1##", "*#*0##"):
            continue

        try:
            msg = OWNMessage.parse(raw)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"Failed to parse authentic MH202 alarm frame {raw!r}: {exc}")

        assert isinstance(msg, OWNAlarmEvent)
        assert msg.who == 5
        alarm_frames.append(raw)

        async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 18
    assert "*5*1*0##" in alarm_frames
    assert "*5*9*0##" in alarm_frames
    assert "*5*11*#1##" in alarm_frames
    assert "*5*18*#5##" in alarm_frames

    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_mh202_alarm_entity_state_transitions(hass: HomeAssistant) -> None:
    """Test MyHOMEAlarmControlPanel state transitions with authentic MH202 frames."""
    assert TRACE_ALARM_FILE.is_file(), f"Missing trace fixture: {TRACE_ALARM_FILE}"

    gateway = MagicMock()
    gateway.mac = "00:03:50:00:02:02"
    gateway.log_id = "[MH202]"
    gateway.send = AsyncMock()

    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        alarm = MyHOMEAlarmControlPanel(
            hass=hass,
            name="MH202 Alarm",
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

        # Feed arm away frame *5*8*0## (engage). The trace's *5*1*0## sits 3 ms
        # before *5*9*0## in one status dump: activation, not armed.
        arm_event = OWNAlarmEvent.parse("*5*8*0##")
        assert arm_event is not None
        assert arm_event.is_armed_away is True
        alarm.handle_event(arm_event)
        assert alarm.alarm_state == AlarmControlPanelState.ARMED_AWAY

        # Feed disarm frame *5*9*0##
        disarm_event = OWNAlarmEvent.parse("*5*9*0##")
        assert disarm_event is not None
        assert disarm_event.is_disarmed is True
        alarm.handle_event(disarm_event)
        assert alarm.alarm_state == AlarmControlPanelState.DISARMED

        # Feed zone frame *5*11*#1## (does not pollute system alarm state on OWNd with WHO 5)
        zone_event = OWNAlarmEvent.parse("*5*11*#1##")
        assert zone_event is not None
        if hasattr(zone_event, "is_zone_active"):
            assert zone_event.is_zone_active is True
            alarm.handle_event(zone_event)
            assert alarm.alarm_state == AlarmControlPanelState.DISARMED
