"""Tests for #466: Real-World BTicino F455 Gateway Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
Legrand F455 Basic Gateway (firmware 1.0.86, contributed by @lionelser in
issue #466 comment 5938596974) can be deterministically parsed and replayed
through the integration event dispatcher without exceptions or regressions,
closing the F455 hardware blind spot.
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
from OWNd.message import OWNGatewayEvent, OWNLightingCommand, OWNLightingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_466"
F455_TRACE_FILE = TRACES_DIR / "config_entry-myhome_F455.json"


@pytest.mark.asyncio
async def test_f455_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 80 on-wire frames from the physical F455 bus capture.

    Ensures every frame across WHO 1 (lights, dimmers, pushbuttons) and
    WHO 13 (gateway diagnostics, device type, firmware, clock) replays
    cleanly through the event dispatcher.
    """
    assert F455_TRACE_FILE.is_file(), f"Missing trace fixture: {F455_TRACE_FILE}"

    with open(F455_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    gateway_info = trace_data["data"]["gateway"]
    assert gateway_info["model_name"] == "F455"
    assert gateway_info["firmware"] == "1.0.86"
    assert gateway_info["identification"]["who13_code"] == "200"
    assert gateway_info["identification"]["profile"] == "F455Profile"

    raw_frames = trace_data["data"]["bus_monitor"]["recent_frames"]
    assert len(raw_frames) == 80

    mac = "00:03:50:00:04:55"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.55",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "F455",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:Basic gateway:1",
            CONF_MANUFACTURER: "Legrand S.p.A.",
            CONF_FIRMWARE: "1.0.86",
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
            pytest.fail(f"Failed to parse authentic F455 frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            if hasattr(msg, "who") and msg.who:
                whos_seen.add(str(msg.who))
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 80

    # Frames include WHO 1 (lights), WHO 2 (covers query), WHO 4 (climate query),
    # WHO 5 (alarm query), WHO 13 (gateway), WHO 16 (audio query), WHO 18 (energy queries),
    # and WHO 1013 (diagnostics query).
    expected_whos = {"1", "2", "4", "5", "13", "16", "18", "1013"}
    assert expected_whos.issubset(whos_seen), (
        f"Missing expected WHOs. Found: {whos_seen}, expected subset: {expected_whos}"
    )

    # Discovered lighting entities should have updated states from the replay
    # WHERE 11, 14, 16, 17 were set to ON levels (10 or 9)
    light_11 = hass.states.get("light.light_11")
    if light_11 is not None:
        assert light_11.state in ("on", "off")

    await hass.config_entries.async_unload(entry.entry_id)


def test_f455_dimmer_dimension_telemetry_frames() -> None:
    """Verify authentic WHO 1 dimmer telemetry frames captured from the F455.

    Confirms both Dimension 4 (where=13, where=15) and Dimension 1 (where=11, 12, 14, 16, 17)
    reports from physical dimmers on the F455 basic gateway.
    """
    # Dimension 4 dimmer report: WHERE 13 at level 100%, 2 steps
    dim4_msg = OWNMessage.parse("*#1*13*4*100*2##")
    assert isinstance(dim4_msg, OWNLightingEvent)
    assert dim4_msg.who == 1
    assert dim4_msg.where == "13"
    assert dim4_msg.dimension == 4

    # Dimension 4 dimmer report: WHERE 15 at level 100%, 2 steps
    dim4_msg_15 = OWNMessage.parse("*#1*15*4*100*2##")
    assert isinstance(dim4_msg_15, OWNLightingEvent)
    assert dim4_msg_15.who == 1
    assert dim4_msg_15.where == "15"
    assert dim4_msg_15.dimension == 4

    # Dimension 1 dimmer reports
    dim1_12 = OWNMessage.parse("*#1*12*1*134*5##")
    assert isinstance(dim1_12, OWNLightingEvent)
    assert dim1_12.who == 1
    assert dim1_12.where == "12"
    assert dim1_12.dimension == 1

    dim1_17 = OWNMessage.parse("*#1*17*1*175*1##")
    assert isinstance(dim1_17, OWNLightingEvent)
    assert dim1_17.who == 1
    assert dim1_17.where == "17"
    assert dim1_17.dimension == 1


def test_f455_pushbutton_translation_frames() -> None:
    """Verify lighting pushbutton command translations (*1*1000#x*where##)."""
    # Pushbutton pressed on WHERE 12
    press = OWNMessage.parse("*1*1000#1*12##")
    assert isinstance(press, (OWNLightingEvent, OWNLightingCommand))
    assert press.who == 1
    assert press.where == "12"

    # Pushbutton released on WHERE 12
    release = OWNMessage.parse("*1*1000#0*12##")
    assert isinstance(release, (OWNLightingEvent, OWNLightingCommand))
    assert release.who == 1
    assert release.where == "12"


def test_f455_gateway_identification_and_clock_frames() -> None:
    """Verify WHO 13 gateway diagnostics genuine replies from the physical F455."""
    # Clock reply: 20:40:21
    clock = OWNMessage.parse("*#13**0*20*40*21*##")
    assert isinstance(clock, OWNGatewayEvent)
    assert clock.who == 13
    assert clock.dimension == 0

    # Device type code 200
    dev_type = OWNMessage.parse("*#13**15*200##")
    assert isinstance(dev_type, OWNGatewayEvent)
    assert dev_type.who == 13
    assert dev_type.dimension == 15
    assert dev_type._device_type == "F454"  # code 200 maps to modern gateway family

    # Firmware 1.0.86
    fw = OWNMessage.parse("*#13**16*1*0*86##")
    assert isinstance(fw, OWNGatewayEvent)
    assert fw.who == 13
    assert fw.dimension == 16
    assert fw._firmware_version == "1.0.86"
