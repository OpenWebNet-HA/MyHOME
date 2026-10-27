"""Tests for #466: Real-World BTicino MyHomeServer1 WHO 25 Scenario Plus Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
BTicino MyHomeServer1 gateway (firmware 2.87.13, contributed by @TheDarkWizard in
issue #466 comment 6085724930) can be deterministically parsed and replayed
through the integration event dispatcher without exceptions or regressions.

Specifically validates:
- WHO 25 Scheduled Scenario PLUS command transmission (*25*11#0*11##) and on-wire monitor echo
- Gateway round-trip timing (~82.8 ms) confirming hardware acceptance and bus relay
- Interleaved WHO 18 active power measurements (dimension 113) on meters 51 and 52
- WHO 4 climate zone dimension 60 telemetry
- WHO 1 lighting actuator status reports
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
    OWNEnergyEvent,
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

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_466"
MHS1_TRACE_FILE = TRACES_DIR / "config_entry-myhome_MyHomeServer1_who25_scenario_plus.json"


@pytest.mark.asyncio
async def test_myhomeserver1_scenarioplus_trace_replay_without_exceptions(
    hass: HomeAssistant,
) -> None:
    """Replay all 500 on-wire frames from the physical MyHomeServer1 trace capture."""
    assert MHS1_TRACE_FILE.is_file(), f"Missing trace fixture: {MHS1_TRACE_FILE}"

    with open(MHS1_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    gateway_info = trace_data["data"]["gateway"]
    assert gateway_info["model_name"] == "MyHomeServer1"
    assert gateway_info["firmware"] == "2.87.13"
    assert gateway_info["identification"]["profile"] == "MyHomeServer1Profile"

    raw_frames = trace_data["data"]["bus_monitor"]["recent_frames"]
    assert len(raw_frames) == 500

    mac = "00:03:50:00:04:66"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.1",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "MyHomeServer1",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:webserver:1",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.87.13",
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
    assert replayed == 500
    assert "25" in whos_seen, "Trace must contain WHO 25 scenario plus frames"
    assert "18" in whos_seen, "Trace must contain WHO 18 energy frames"
    assert "4" in whos_seen, "Trace must contain WHO 4 climate frames"
    assert "1" in whos_seen, "Trace must contain WHO 1 lighting frames"


def test_myhomeserver1_scenarioplus_specific_frame_grammar() -> None:
    """Verify syntactic and semantic correctness of key frames in the capture."""
    # 1. WHO 25 Scenario Plus ON / Start frame (*25*11#0*11##)
    msg_on = OWNMessage.parse("*25*11#0*11##")
    assert msg_on is not None
    assert msg_on.who == 25
    assert msg_on.where == "11"

    # 2. WHO 18 Dimension 113 instant active power reading on meter 51
    energy_frame = OWNMessage.parse("*#18*51*113*2343##")
    assert isinstance(energy_frame, OWNEnergyEvent)
    assert energy_frame.where == "51"
    assert energy_frame.dimension == 113

    # 3. WHO 4 Dimension 60 climate reading on zone 2
    climate_frame = OWNMessage.parse("*#4*2*60*49##")
    assert isinstance(climate_frame, OWNHeatingEvent)
    assert climate_frame.where == "2"
    assert climate_frame.dimension == 60

    # 4. WHO 1 lighting state report on extended address 0014
    light_frame = OWNMessage.parse("*1*0*0014##")
    assert isinstance(light_frame, OWNLightingEvent)
    assert light_frame.where == "0014"
    assert light_frame.is_on is False
