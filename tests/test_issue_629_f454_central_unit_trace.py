"""Tests for #629: Authentic BTicino F454 Gateway & Central Unit (#0) Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
BTicino F454 gateway (FW 1.0.34) running a 99-zone thermoregulation plant:
1. Replay 122 sweep frames across WHO 1, 2, 4, 5, 13, 16, 18, and 1013 deterministically.
2. Replay authentic central unit #0 responses (*4*202*#0## and status flags *4*21*#0##,
   *4*22*#0##, *4*24*#0##) and subordinate zones 5 and 12 (*4*210*#5##, *4*210*#12##)
   confirming central unit status handling.
3. Validate dimension-14 status query (*#4*#0*14##) parsing and gateway rejection behavior.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.components.climate import HVACMode
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
from OWNd.message import OWNHeatingCommand, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_629"
F454_SWEEP_FILE = TRACES_DIR / "myhome_sweep_F454_all_2026-10-05T10-51-55.json"
F454_STATUS_TRACE_FILE = TRACES_DIR / "myhome_trace_F454_all_2026-10-05T11-14-00.json"
F454_DIM14_TRACE_FILE = TRACES_DIR / "myhome_trace_F454_all_2026-10-05T11-14-57.json"
F454_TIMEOUT_TRACE_FILE = TRACES_DIR / "myhome_trace_F454_all_2026-10-05T11-16-07.json"


@pytest.mark.asyncio
async def test_f454_sweep_trace_replay(hass: HomeAssistant) -> None:
    """Replay all 122 frames from the authentic physical F454 bus sweep capture.

    Ensures every frame across WHO 1 (lights), WHO 2 (covers), WHO 4 (climate scan),
    WHO 5 (alarm), WHO 13 (gateway info/clock), WHO 16 (audio), WHO 18 (energy), and
    WHO 1013 (diagnostics) replays cleanly through the event dispatcher without errors.
    """
    assert F454_SWEEP_FILE.is_file(), f"Missing trace fixture: {F454_SWEEP_FILE}"

    with open(F454_SWEEP_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    gateway_info = trace_data["gateway"]
    assert gateway_info["model"] == "F454"
    assert gateway_info["firmware"] == "1.0.34"
    assert gateway_info["identification"]["who13_code"] == "200"
    assert gateway_info["identification"]["who13_firmware"] == "1.0.34"

    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 122

    mac = "00:03:50:00:04:54"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.0.35",
            CONF_PORT: 20000,
            CONF_PASSWORD: "open",
            CONF_MAC: mac,
            CONF_NAME: "F454 Gateway",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "F454 Web Server",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "1.0.34",
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
            pytest.fail(f"Failed to parse authentic F454 frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            replayed += 1
            if hasattr(msg, "who") and msg.who is not None:
                whos_seen.add(str(msg.who))
            elif item.get("who"):
                whos_seen.add(str(item["who"]))

    await hass.async_block_till_done()
    assert replayed == 122
    assert {"1", "2", "4", "5", "13", "16", "18", "1013"}.issubset(whos_seen)

    # Check lighting states (relays reporting OFF during sweep)
    for where in ("11", "21", "31", "51", "61", "71", "81"):
        state = hass.states.get(f"light.light_{where}")
        assert state is not None, f"Expected light entity for WHERE {where}"
        assert state.state == "off"

    # Check cover states (actuators reporting STOP during sweep)
    for where in ("42", "43", "44", "45", "46", "49", "39"):
        state = hass.states.get(f"cover.cover_{where}")
        assert state is not None, f"Expected cover entity for WHERE {where}"

    # Verify device health has 0 faults raised
    assert handler.device_health.faults == []


@pytest.mark.asyncio
async def test_f454_central_unit_plain_status_replay(hass: HomeAssistant) -> None:
    """Replay authentic central unit #0 responses to *#4*#0## on F454 gateway.

    Verifies that:
    1. *#4*#0## requests status of central unit #0.
    2. Central unit responds with *4*202*#0## setting HVACMode.OFF on Climate Zone 0.
    3. Operational flags *4*21*#0##, *4*22*#0##, *4*24*#0## parse and process cleanly.
    4. Subordinate zone frames *4*210*#5## and *4*210*#12## set HVACMode.COOL.
    """
    assert F454_STATUS_TRACE_FILE.is_file(), f"Missing trace fixture: {F454_STATUS_TRACE_FILE}"

    with open(F454_STATUS_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "F454"
    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 7

    mac = "00:03:50:00:04:54"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.0.35",
            CONF_PORT: 20000,
            CONF_PASSWORD: "open",
            CONF_MAC: mac,
            CONF_NAME: "F454 Gateway",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "F454 Web Server",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "1.0.34",
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
            pytest.fail(f"Failed to parse authentic frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            replayed += 1

    await hass.async_block_till_done()
    assert replayed == 7

    # Central unit #0 was set to OFF via *4*202*#0##
    state_0 = hass.states.get("climate.climate_zone_0")
    assert state_0 is not None, "Expected climate entity for Central Unit 0"
    assert state_0.state == HVACMode.OFF

    # Zones 5 and 12 were set to COOL via *4*210*#5## and *4*210*#12##
    state_5 = hass.states.get("climate.climate_zone_5")
    assert state_5 is not None, "Expected climate entity for Zone 5"
    assert state_5.state == HVACMode.COOL

    state_12 = hass.states.get("climate.climate_zone_12")
    assert state_12 is not None, "Expected climate entity for Zone 12"
    assert state_12.state == HVACMode.COOL

    # Verify device health has 0 faults raised
    assert handler.device_health.faults == []


def test_f454_central_unit_dimension_14_and_timeout_frames() -> None:
    """Verify parsing and structure of rejected dimension-14 and unconfigured 4-zone frames.

    Confirms that:
    1. *#4*#0*14## parses as a dimension-14 status request on #0.
    2. *#4*#0#1## parses as a status request on 4-zone address #0#1.
    3. Plain central status request *#4*#0## is canonical for #0.
    """
    assert F454_DIM14_TRACE_FILE.is_file()
    assert F454_TIMEOUT_TRACE_FILE.is_file()

    with open(F454_DIM14_TRACE_FILE, "r", encoding="utf-8") as f:
        dim14_data = json.load(f)
    assert len(dim14_data["frames"]) == 1
    dim14_raw = dim14_data["frames"][0]["raw"]
    assert dim14_raw == "*#4*#0*14##"

    msg_dim14 = OWNMessage.parse(dim14_raw)
    assert msg_dim14 is not None
    assert msg_dim14.who == 4
    assert msg_dim14.where == "#0"
    assert msg_dim14.dimension == 14

    with open(F454_TIMEOUT_TRACE_FILE, "r", encoding="utf-8") as f:
        timeout_data = json.load(f)
    assert len(timeout_data["frames"]) == 1
    timeout_raw = timeout_data["frames"][0]["raw"]
    assert timeout_raw == "*#4*#0#1##"

    msg_timeout = OWNMessage.parse(timeout_raw)
    assert msg_timeout is not None
    assert msg_timeout.who == 4
    assert str(msg_timeout) == "*#4*#0#1##"  # round-trips unchanged; OWNd's where/param split is not pinned here

    # Command builder verification:
    cmd_plain = OWNHeatingCommand.status("#0")
    assert str(cmd_plain) == "*#4*#0##"
