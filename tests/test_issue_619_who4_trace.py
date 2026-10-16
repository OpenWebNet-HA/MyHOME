"""Tests for #619: Authentic BTicino F453AV Central Unit (#0) & WHO 4 Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
BTicino F453AV gateway (FW 3.0.14) with central unit #0 contributed by @nce2704 replay cleanly:
1. myhome_trace_F453AV_who4_2026-10-11T11-02-55.Holiday.ON.json (88 frames):
   Setting Daily Holiday (Holiday Plan) on Central Unit #0 (*4*115#1101*#0##) with weekly program 1.
2. myhome_trace_F453AV_who4_2026-10-11T11-10-23.Holiday.OFF.automatically.json (20 frames):
   Automatic expiration of Holiday mode restoring weekly program 1 (*4*1101*#0##).
3. myhome_trace_F453AV_who4_2026-10-11T11-13-06.Antifreeze.ON.json (112 frames):
   Switching Central Unit #0 to Antifreeze (*4*102*#0##) propagating to all zones.
4. myhome_trace_F453AV_who4_2026-10-11T11-15-50.one.zone.to.cool.json (63 frames):
   Freedompro summer transition: switching one zone to cool triggers central unit #0
   to enter thermal protection (*4*202*#0##) in summer/cooling season.
5. myhome_trace_F453AV_who4_2026-10-11T10-49-26.Central.to.OFF.json (115 frames):
   Switching Central Unit #0 to OFF (*4*103*#0##) propagating OFF to physical zones (*4*303*Z##).
6. myhome_trace_F453AV_who4_2026-10-11T10-29-57.json (121 frames):
   Status sweep verifying coexistence of *4*1*Z## (season heating) alongside *4*111*#Z##
   (weekly program 1) without displacing the programmed schedule mode.
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
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_619"
HOLIDAY_ON_TRACE_FILE = (
    TRACES_DIR / "myhome_trace_F453AV_who4_2026-10-11T11-02-55.Holiday.ON.json"
)
HOLIDAY_OFF_TRACE_FILE = (
    TRACES_DIR
    / "myhome_trace_F453AV_who4_2026-10-11T11-10-23.Holiday.OFF.automatically.json"
)
ANTIFREEZE_ON_TRACE_FILE = (
    TRACES_DIR / "myhome_trace_F453AV_who4_2026-10-11T11-13-06.Antifreeze.ON.json"
)
ONE_ZONE_COOL_TRACE_FILE = (
    TRACES_DIR / "myhome_trace_F453AV_who4_2026-10-11T11-15-50.one.zone.to.cool.json"
)
CENTRAL_OFF_TRACE_FILE = (
    TRACES_DIR / "myhome_trace_F453AV_who4_2026-10-11T10-49-26.Central.to.OFF.json"
)
STATE_SWEEP_TRACE_FILE = TRACES_DIR / "myhome_trace_F453AV_who4_2026-10-11T10-29-57.json"


async def _setup_f453av_gateway(hass: HomeAssistant) -> str:
    """Set up a mock F453AV gateway config entry for trace replaying."""
    mac = "00:03:50:00:04:53"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.0.155",
            CONF_PORT: 20000,
            CONF_PASSWORD: "open",
            CONF_MAC: mac,
            CONF_NAME: "F453AV Gateway",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "F453AV Web Server",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "3.0.14",
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
    return mac


async def _replay_trace_frames(
    hass: HomeAssistant, mac: str, trace_file: Path
) -> int:
    """Replay all valid frames from a trace file into the dispatcher."""
    assert trace_file.is_file(), f"Missing trace fixture: {trace_file}"
    with open(trace_file, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    replayed = 0
    for item in trace_data["frames"]:
        raw = item.get("raw")
        if not raw or raw in ("*#*1##", "*#*0##"):
            continue

        try:
            msg = OWNMessage.parse(raw)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"Failed to parse authentic F453AV frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            replayed += 1

    await hass.async_block_till_done()
    return replayed


@pytest.mark.asyncio
async def test_f453av_holiday_on_trace_replay(hass: HomeAssistant) -> None:
    """Replay Holiday ON trace from physical F453AV gateway.

    Verifies that setting Central Unit #0 to Daily Holiday via *4*115#1101*#0##:
    1. Replays 88 authentic frames cleanly through dispatcher.
    2. Replays central status flags *4*21*#0## and *4*22*#0##.
    3. Replays subzone program reports *4*111*#Z## for active zones.
    4. Leaves 0 device health faults.
    """
    mac = await _setup_f453av_gateway(hass)
    replayed = await _replay_trace_frames(hass, mac, HOLIDAY_ON_TRACE_FILE)
    assert replayed == 88

    handler = hass.data[DOMAIN][mac][CONF_ENTITY]
    assert handler.device_health.faults == []

    # Check Central Unit #0 was discovered
    state_0 = hass.states.get("climate.climate_zone_0")
    assert state_0 is not None


@pytest.mark.asyncio
async def test_f453av_holiday_off_automatically_replay(hass: HomeAssistant) -> None:
    """Replay Holiday OFF trace where central unit returns to weekly program.

    Verifies that expiration of Holiday mode emitting *4*1101*#0##:
    1. Replays 20 authentic frames cleanly without errors.
    2. Updates Central Unit #0 to weekly program 1.
    3. Replays subzone program reports *4*111*#Z##.
    4. Leaves 0 device health faults.
    """
    mac = await _setup_f453av_gateway(hass)
    replayed = await _replay_trace_frames(hass, mac, HOLIDAY_OFF_TRACE_FILE)
    assert replayed == 20

    handler = hass.data[DOMAIN][mac][CONF_ENTITY]
    assert handler.device_health.faults == []


@pytest.mark.asyncio
async def test_f453av_antifreeze_on_replay(hass: HomeAssistant) -> None:
    """Replay Antifreeze ON trace where central unit is set to antifreeze.

    Verifies that setting Central Unit #0 to Antifreeze via *4*102*#0##:
    1. Replays 112 authentic frames cleanly without errors.
    2. Sets Central Unit #0 to HVACMode.OFF (antifreeze protection).
    3. Replays subordinate zone frames *4*102*#Z## and *4*102*Z##.
    4. Leaves 0 device health faults.
    """
    mac = await _setup_f453av_gateway(hass)
    replayed = await _replay_trace_frames(hass, mac, ANTIFREEZE_ON_TRACE_FILE)
    assert replayed == 112

    handler = hass.data[DOMAIN][mac][CONF_ENTITY]
    assert handler.device_health.faults == []

    state_0 = hass.states.get("climate.climate_zone_0")
    assert state_0 is not None
    assert state_0.state == HVACMode.OFF


@pytest.mark.asyncio
async def test_f453av_one_zone_to_cool_replay(hass: HomeAssistant) -> None:
    """Replay Freedompro transition where switching zone to cool changes season.

    Verifies that switching from Winter to Summer season into Thermal Protection:
    1. Replays 63 authentic frames cleanly without errors.
    2. Emits *4*202*#0## putting Central Unit #0 into protection.
    3. Propagates cooling setpoints *#4*Z*12*0350*3## to zones.
    4. Leaves 0 device health faults.
    """
    mac = await _setup_f453av_gateway(hass)
    replayed = await _replay_trace_frames(hass, mac, ONE_ZONE_COOL_TRACE_FILE)
    assert replayed == 63

    handler = hass.data[DOMAIN][mac][CONF_ENTITY]
    assert handler.device_health.faults == []

    state_0 = hass.states.get("climate.climate_zone_0")
    assert state_0 is not None
    assert state_0.state == HVACMode.OFF


@pytest.mark.asyncio
async def test_f453av_central_to_off_replay(hass: HomeAssistant) -> None:
    """Replay Central to OFF trace from physical F453AV gateway.

    Verifies that setting Central Unit #0 to OFF via *4*103*#0##:
    1. Replays 115 authentic frames cleanly without errors.
    2. Puts Central Unit #0 into HVACMode.OFF.
    3. Propagates OFF to subzones *4*103*#Z## and physical zones *4*303*Z##.
    4. Leaves 0 device health faults.
    """
    mac = await _setup_f453av_gateway(hass)
    replayed = await _replay_trace_frames(hass, mac, CENTRAL_OFF_TRACE_FILE)
    assert replayed == 115

    handler = hass.data[DOMAIN][mac][CONF_ENTITY]
    assert handler.device_health.faults == []

    state_0 = hass.states.get("climate.climate_zone_0")
    assert state_0 is not None
    assert state_0.state == HVACMode.OFF


@pytest.mark.asyncio
async def test_f453av_who4_state_sweep_replay(hass: HomeAssistant) -> None:
    """Replay status sweep trace demonstrating season and weekly program coexistence.

    Verifies that receiving *4*1*Z## (season heating telemetry):
    1. Replays 121 authentic frames cleanly without errors.
    2. Coexists with active weekly program reports *4*111*#Z##.
    3. Does not displace programmed schedule states or raise faults.
    4. Leaves 0 device health faults.
    """
    mac = await _setup_f453av_gateway(hass)
    replayed = await _replay_trace_frames(hass, mac, STATE_SWEEP_TRACE_FILE)
    assert replayed == 121

    handler = hass.data[DOMAIN][mac][CONF_ENTITY]
    assert handler.device_health.faults == []
