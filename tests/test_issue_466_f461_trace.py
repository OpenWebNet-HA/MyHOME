"""Tests for #466: Real-World BTicino F461 Gateway Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
BTicino F461 DALI/Web Server gateway (contributed by @lyubomirtraykov in
issue #466 comment 5870342995) can be deterministically parsed and replayed
through the integration event dispatcher without exceptions or regressions,
closing the WHO 13 (gateway diagnostics) and WHO 1013 (gateway object model)
blind spots for this model.
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
from OWNd.message import OWNEvent, OWNGatewayEvent, OWNHeatingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_466"
F461_TRACE_FILE = TRACES_DIR / "myhome_trace_F461_all_2026-09-28T12-42-23.json"


@pytest.mark.asyncio
async def test_f461_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 200 on-wire frames from the physical F461 bus capture.

    Ensures every frame across WHO 1 (lights), WHO 2 (automation), WHO 4
    (climate), WHO 5 (alarm), WHO 13 (gateway), WHO 16 (audio), WHO 18
    (energy), and WHO 1013 (gateway object model) replays cleanly through
    the event dispatcher.
    """
    assert F461_TRACE_FILE.is_file(), f"Missing trace fixture: {F461_TRACE_FILE}"

    with open(F461_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    gateway_info = trace_data["gateway"]
    assert gateway_info["model"] == "F461"
    assert gateway_info["identification"]["who13_code"] == "200"
    assert gateway_info["identification"]["who1013_code"] == "134"

    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 200

    mac = "00:03:50:00:04:61"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.61",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "F461",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "F461 DALI Web Server",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.0.11",
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
            pytest.fail(f"Failed to parse authentic F461 frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            if hasattr(msg, "who") and msg.who:
                whos_seen.add(str(msg.who))
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 200

    expected_whos = {"1", "2", "4", "5", "13", "16", "18", "1013"}
    assert expected_whos.issubset(whos_seen), (
        f"Missing expected WHOs. Found: {whos_seen}, expected subset: {expected_whos}"
    )

    await hass.config_entries.async_unload(entry.entry_id)


def test_f461_gateway_diagnostics_frames() -> None:
    """Verify WHO 13 gateway diagnostics genuinely answered by the physical F461.

    Unlike WHO 2 (automation), WHO 5 (alarm), WHO 16 (audio), and WHO 18
    (energy) — for which this capture only shows outbound queries with no
    on-wire reply, because this particular plant has no automation, alarm,
    audio, or energy hardware attached — the gateway diagnostics below are
    genuine request/reply pairs captured from the physical unit.
    """
    time_reply = OWNMessage.parse("*#13**0*15*40*52*999##")
    assert isinstance(time_reply, OWNGatewayEvent)
    assert time_reply.who == 13
    assert time_reply.dimension == 0

    # Dimension 15 (device type code) is shared across F454/MyHomeServer1/
    # MH202/F461/H4890, so WHO 13 alone cannot disambiguate the F461; that
    # is what WHO 1013 (below) is for.
    device_type_reply = OWNMessage.parse("*#13**15*200##")
    assert isinstance(device_type_reply, OWNGatewayEvent)
    assert device_type_reply.who == 13
    assert device_type_reply._device_type == "F454"

    firmware_reply = OWNMessage.parse("*#13**16*2*0*11##")
    assert isinstance(firmware_reply, OWNGatewayEvent)
    assert firmware_reply.who == 13
    assert firmware_reply._firmware_version == "2.0.11"


def test_f461_gateway_object_model_frame() -> None:
    """Verify the WHO 1013 gateway object model identity reply from the F461.

    Object model code 134 uniquely identifies the F461, resolving the
    ambiguity left by the WHO 13 device type code (200) shared with other
    gateways.
    """
    identity = OWNMessage.parse("*#1013**1*134*15*5*0##")
    assert isinstance(identity, OWNEvent)
    assert identity.who == 1013


def test_f461_climate_zone_diagnostic_frames() -> None:
    """Verify WHO 4 climate zone telemetry from the physical F461 sweep."""
    # Zone 3: main temperature 24.1°C, mode 303 (off), local set temperature
    # and target temperature both 23.0°C, no local offset, fan auto.
    main_temp = OWNMessage.parse("*#4*3*0*0241##")
    assert isinstance(main_temp, OWNHeatingEvent)
    assert main_temp.who == 4
    assert main_temp.zone == 3
    assert main_temp.main_temperature == 24.1

    mode = OWNMessage.parse("*4*303*3##")
    assert isinstance(mode, OWNHeatingEvent)
    assert mode.zone == 3
    assert mode.mode == "off"

    local_set_temp = OWNMessage.parse("*#4*3*12*0230*3##")
    assert isinstance(local_set_temp, OWNHeatingEvent)
    assert local_set_temp.local_set_temperature == 23.0

    set_temp = OWNMessage.parse("*#4*3*14*0230*3##")
    assert isinstance(set_temp, OWNHeatingEvent)
    assert set_temp.set_temperature == 23.0

    local_offset = OWNMessage.parse("*#4*3*13*00##")
    assert isinstance(local_offset, OWNHeatingEvent)
    assert local_offset.local_offset == 0

    fan = OWNMessage.parse("*#4*3*11*0##")
    assert isinstance(fan, OWNHeatingEvent)
    assert fan.fan_speed == 0
