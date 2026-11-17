"""Tests for #466: Real-World BTicino MH200 Gateway Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
BTicino MH200 gateway via the live `myhome-gateway` session can be
deterministically parsed and replayed through the integration event dispatcher
without exceptions or regressions, covering WHO 1, 2, 5, 9, 13, 16, 17, 1001, and 1013.
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
from OWNd.message import (
    OWNAlarmEvent,
    OWNAuxEvent,
    OWNEvent,
    OWNGatewayEvent,
    OWNLightingEvent,
    OWNMessage,
    OWNSceneEvent,
    OWNSoundEvent,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_466"
MH200_TRACE_FILE = TRACES_DIR / "myhome_trace_MH200_all_2026-09-26T21-00-00.json"


@pytest.mark.asyncio
async def test_mh200_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 144 on-wire frames from the physical MH200 bus capture.

    Ensures every frame across WHO 1 (lights), WHO 2 (covers), WHO 5 (alarm),
    WHO 9 (auxiliary), WHO 13 (gateway), WHO 16 (audio), WHO 17 (scenarios),
    WHO 1001 (lighting diagnostic), and WHO 1013 (gateway object model)
    replays cleanly through the event dispatcher.
    """
    assert MH200_TRACE_FILE.is_file(), f"Missing trace fixture: {MH200_TRACE_FILE}"

    with open(MH200_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    gateway_info = trace_data["gateway"]
    assert gateway_info["model"] == "MH200"
    assert gateway_info["identification"]["who13_code"] == 4

    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 144

    mac = "00:03:50:00:02:00"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.200",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MH200",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "MH200 Scenario Programmer",
            CONF_MANUFACTURER: "BTicino",
            CONF_FIRMWARE: "2.0.0",
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
            pytest.fail(f"Failed to parse authentic MH200 frame {raw!r}: {exc}")

        # Empty-WHERE alarm frames (*5*0*##, etc.) parse to None by design
        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            if hasattr(msg, "who") and msg.who:
                whos_seen.add(str(msg.who))
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 144

    expected_whos = {"1", "2", "5", "9", "13", "16", "17", "1001", "1013"}
    assert expected_whos.issubset(whos_seen), (
        f"Missing expected WHOs. Found: {whos_seen}, expected subset: {expected_whos}"
    )

    await hass.config_entries.async_unload(entry.entry_id)


def test_mh200_auxiliary_subsystem_frames() -> None:
    """Verify auxiliary (WHO 9) channel status responses from the physical MH200."""
    aux_frames = [
        ("*9*0*0##", 0, "0"),
        ("*9*0*1##", 0, "1"),
        ("*9*0*2##", 0, "2"),
    ]

    for raw, expected_state, expected_where in aux_frames:
        msg = OWNMessage.parse(raw)
        assert isinstance(msg, OWNAuxEvent)
        assert msg.who == 9
        assert msg.state_code == expected_state
        assert str(msg.where) == expected_where


def test_mh200_advanced_scenario_subsystem_frames() -> None:
    """Verify advanced scenario (WHO 17) execution states from the physical MH200."""
    scenario_frames = [
        ("*17*2*30##", "30", 2),   # scenario 30 stopped
        ("*17*3*30##", "30", 3),   # scenario 30 enabled
        ("*17*2*137##", "137", 2), # scenario 137 stopped
        ("*17*3*137##", "137", 3), # scenario 137 enabled
    ]

    for raw, expected_scenario, expected_state in scenario_frames:
        msg = OWNMessage.parse(raw)
        assert isinstance(msg, OWNSceneEvent)
        assert msg.who == 17
        assert msg.scenario == expected_scenario
        assert msg.state == expected_state


def test_mh200_diagnostics_and_gateway_frames() -> None:
    """Verify gateway diagnostics (WHO 1013 and WHO 1001) from the physical MH200."""
    # WHO 1013 Gateway Object Model diagnostic
    diag_1013 = OWNMessage.parse("*#1013**1*4##")
    assert isinstance(diag_1013, OWNEvent)
    assert diag_1013.who == 1013

    # WHO 1001 Lighting bus physical diagnostic
    diag_1001 = OWNMessage.parse("*#1001*74*11*111110111111111111110111##")
    assert isinstance(diag_1001, OWNEvent)
    assert diag_1001.who == 1001

    # WHO 13 Gateway Device Type (Code 4 = MH200)
    gw_type = OWNMessage.parse("*#13**15*4##")
    assert isinstance(gw_type, OWNGatewayEvent)
    assert gw_type.who == 13
    assert gw_type._device_type == "MH200"


def test_mh200_burglar_alarm_subsystem_frames() -> None:
    """Verify burglar alarm (WHO 5) empty-WHERE frames and zone status."""
    # Empty-WHERE frames emit None from older OWNd parser or parse as OWNAlarmEvent
    for raw in ("*5*0*##", "*5*9*##", "*5*5*##", "*5*7*##"):
        msg = OWNMessage.parse(raw)
        if msg is not None:
            assert isinstance(msg, OWNAlarmEvent)
            assert msg.who == 5

    # Active zone status frames parse cleanly
    for zone in range(1, 9):
        raw = f"*5*11*#{zone}##"
        msg = OWNMessage.parse(raw)
        assert isinstance(msg, OWNAlarmEvent)
        assert msg.who == 5
        assert msg.zone == str(zone)
        assert msg.state_code == 11
        assert msg.state_name == "active zone"


def test_mh200_audio_subsystem_frames() -> None:
    """Verify sound system / audio (WHO 16) zone controls and volume feedback."""
    audio_frames = [
        ("*16*13*21##", "21", True, False),   # Zone 21 OFF
        ("*16*3*101##", "101", False, True),  # Source 1 ON
        ("*16*3*102##", "102", False, True),  # Source 2 ON
        ("*16*3*122##", "122", False, True),  # Zone 122 ON
    ]

    for raw, expected_where, expected_off, expected_on in audio_frames:
        msg = OWNMessage.parse(raw)
        assert isinstance(msg, OWNSoundEvent)
        assert msg.who == 16
        assert msg.where == expected_where
        assert msg.is_off is expected_off
        assert msg.is_on is expected_on

    # Volume status reports
    vol_28 = OWNMessage.parse("*#16*23*1*28##")
    assert isinstance(vol_28, OWNSoundEvent)
    assert vol_28.who == 16
    assert vol_28.where == "23"
    assert vol_28.volume == 28

    vol_10 = OWNMessage.parse("*#16*23*1*10##")
    assert isinstance(vol_10, OWNSoundEvent)
    assert vol_10.who == 16
    assert vol_10.where == "23"
    assert vol_10.volume == 10


def test_mh200_lighting_and_automation_subsystem_frames() -> None:
    """Verify lighting (WHO 1) and automation (WHO 2) frames from the physical MH200."""
    light = OWNMessage.parse("*1*0*11##")
    assert isinstance(light, OWNLightingEvent)
    assert light.who == 1
    assert str(light.where) == "11"
    assert light.is_on is False

    cover = OWNMessage.parse("*2*0*85##")
    assert cover is not None
    assert cover.who == 2
    assert str(cover.where) == "85"

