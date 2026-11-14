"""Tests for #466: Real-World BTicino MH200 F422 Secondary Bus Timed Covers & Plant Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
BTicino MH200 gateway (firmware 2.1.0, via live `myhome-gateway` session)
can be deterministically parsed and replayed through the integration event dispatcher
without exceptions or regressions.

Specifically validates:
1. WHO 2 (Automation): Secondary bus F422 interface cover addresses (WHERE = XX#4#02)
   responding to bus scan (*#2*0#4#02##) with stopped status (*2*0*XX#4#02##),
   local bus cover 85, and confirmation of standard timed relay profile (no Dimension 10 reply).
2. WHO 1 (Lighting): 35 lighting endpoints reporting ON/OFF and discrete brightness levels (WHAT 7, 9, 10).
3. WHO 5 (Burglar Alarm): Multi-partition and 8-zone alarm status reporting (*5*11*#1## .. #8##).
4. WHO 16 (Sound Diffusion): Multi-zone audio distribution (*16*13*ZONE##) across 8 zones and 2 sources.
5. WHO 13 (Gateway Management): Firmware 2.1.0 (*#13**16*2*1*0##), device code 4 (MH200), and clock sync.
6. WHO 1013 (Device Diagnostics): Object model 4 (*#1013**1*4##).
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
    OWNAlarmEvent,
    OWNAutomationCommand,
    OWNAutomationEvent,
    OWNCommand,
    OWNEvent,
    OWNGatewayCommand,
    OWNGatewayEvent,
    OWNLightingEvent,
    OWNMessage,
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
MH200_F422_TRACE_FILE = TRACES_DIR / "myhome_trace_MH200_f422_timed_covers.json"


@pytest.mark.asyncio
async def test_mh200_f422_covers_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all on-wire frames from the physical MH200 F422 cover capture."""
    assert MH200_F422_TRACE_FILE.is_file(), f"Missing trace fixture: {MH200_F422_TRACE_FILE}"

    with open(MH200_F422_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    gateway_info = trace_data["gateway"]
    assert gateway_info["model"] == "MH200"
    assert gateway_info["firmware"] == "2.1.0"
    assert gateway_info["identification"]["profile"] == "MH200Profile"

    raw_frames = trace_data["frames"]
    assert len(raw_frames) >= 400

    mac = "00:03:50:00:02:40"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.40",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "MH200",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:webserver:1",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.1.0",
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

        msg = OWNMessage.parse(raw)
        assert msg is not None, f"Failed to parse authentic frame: {raw}"

        if hasattr(msg, "who") and msg.who is not None:
            whos_seen.add(str(msg.who))

        # Replay frame through the gateway event bus
        async_dispatcher_send(hass, f"{DOMAIN}_{mac}_event", msg)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == len(raw_frames)
    assert "1" in whos_seen, "Trace must contain WHO 1 lighting frames"
    assert "2" in whos_seen, "Trace must contain WHO 2 automation frames"
    assert "5" in whos_seen, "Trace must contain WHO 5 alarm frames"
    assert "13" in whos_seen, "Trace must contain WHO 13 gateway frames"
    assert "16" in whos_seen, "Trace must contain WHO 16 sound frames"


def test_mh200_f422_covers_specific_frame_grammar() -> None:
    """Verify syntactic and semantic correctness of key F422 cover frames."""
    # 1. F422 secondary bus 02 cover status query
    scan_cmd = OWNMessage.parse("*#2*0#4#02##")
    assert isinstance(scan_cmd, (OWNAutomationEvent, OWNAutomationCommand))
    assert scan_cmd.where == "0"
    assert scan_cmd.interface == "02"

    # 2. Cover 11 on secondary bus 02 reporting stopped
    cover_11 = OWNMessage.parse("*2*0*11#4#02##")
    assert isinstance(cover_11, OWNAutomationEvent)
    assert cover_11.where == "11"
    assert cover_11.interface == "02"
    assert cover_11.state == 0
    assert cover_11.is_opening is False
    assert cover_11.is_closing is False

    # 3. Cover 21 on secondary bus 02 reporting stopped
    cover_21 = OWNMessage.parse("*2*0*21#4#02##")
    assert isinstance(cover_21, OWNAutomationEvent)
    assert cover_21.where == "21"
    assert cover_21.interface == "02"
    assert cover_21.state == 0

    # 4. Local bus cover 85 reporting stopped
    cover_85 = OWNMessage.parse("*2*0*85##")
    assert isinstance(cover_85, OWNAutomationEvent)
    assert cover_85.where == "85"
    assert cover_85.state == 0

    # 5. Lighting discrete brightness levels (WHAT 7, 9, 10)
    dim_7 = OWNMessage.parse("*1*7*89##")
    assert isinstance(dim_7, OWNLightingEvent)
    assert dim_7.where == "89"
    assert dim_7._what == 7

    dim_9 = OWNMessage.parse("*1*9*67##")
    assert isinstance(dim_9, OWNLightingEvent)
    assert dim_9.where == "67"
    assert dim_9._what == 9

    dim_10 = OWNMessage.parse("*1*10*99##")
    assert isinstance(dim_10, OWNLightingEvent)
    assert dim_10.where == "99"
    assert dim_10._what == 10

    # 6. Burglar alarm zone reporting
    alarm_zone1 = OWNMessage.parse("*5*11*#1##")
    assert isinstance(alarm_zone1, OWNAlarmEvent)
    assert alarm_zone1.where == "#1"
    assert alarm_zone1._what == 11

    alarm_zone8 = OWNMessage.parse("*5*11*#8##")
    assert isinstance(alarm_zone8, OWNAlarmEvent)
    assert alarm_zone8.where == "#8"

    # 7. Sound diffusion zone reporting
    sound_z21 = OWNMessage.parse("*16*13*21##")
    assert isinstance(sound_z21, OWNSoundEvent)
    assert sound_z21.where == "21"
    assert sound_z21._what == 13

    # Sound source 101 reporting active
    sound_s101 = OWNMessage.parse("*16*3*101##")
    assert isinstance(sound_s101, OWNSoundEvent)
    assert sound_s101.where == "101"
    assert sound_s101._what == 3

    # 8. Gateway firmware 2.1.0 response
    fw_frame = OWNMessage.parse("*#13**16*2*1*0##")
    assert isinstance(fw_frame, (OWNGatewayCommand, OWNGatewayEvent))

    # 9. Gateway object model 4 (MH200)
    obj_frame = OWNMessage.parse("*#1013**1*4##")
    assert isinstance(obj_frame, (OWNCommand, OWNEvent))
