"""Trace verification tests for #777: BTicino F454 (2.0.51) + Legrand 067226 + BTicino F411 at WHERE 52.

Fixture: tests/fixtures/traces/issue_777/f454_067226_f411_shared_where52.json
Contributed by @eC0y0te on:
- https://github.com/OpenWebNet-HA/MyHOME/issues/777#issuecomment-6364859778
- https://github.com/OpenWebNet-HA/MyHOME/issues/777#issuecomment-6375223714
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import CONF_NAME
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    MESSAGE_TYPE_MOTION_TIMEOUT,
    MESSAGE_TYPE_PIR_SENSITIVITY,
    OWNCommand,
    OWNEvent,
    OWNLightingCommand,
    OWNLightingEvent,
    OWNMessage,
)

from custom_components.myhome.const import CONF_PLATFORMS, CONF_WHERE, DOMAIN
from custom_components.myhome.light import async_setup_entry as async_setup_light
from tests.conftest import attach_runtime

TRACE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_777"
    / "f454_067226_f411_shared_where52.json"
)
MAC = "00:03:50:aa:77:52"


def _load_trace() -> dict:
    return json.loads(TRACE_PATH.read_text(encoding="utf-8"))


def test_issue_777_trace_fixture_schema_and_frame_parsing() -> None:
    """Every frame in the #777 F454 + 067226 + F411 fixture parses cleanly through OWNd."""
    assert TRACE_PATH.is_file()
    data = _load_trace()

    assert data["gateway"]["model"] == "F454"
    assert data["gateway"]["firmware"] == "2.0.51"
    assert data["gateway"]["command_worker_count"] == 1
    assert data["plant"]["shared_where"] == "52"
    assert data["plant"]["light_actuator"]["model"] == "F411"
    assert data["plant"]["motion_detector"]["model"] == "067226"

    frames = data["frames"]
    assert len(frames) == 39
    assert sum(1 for f in frames if f["direction"] == "tx") == 6
    assert sum(1 for f in frames if f["direction"] == "rx") == 33

    for entry in frames:
        raw = entry["raw"]
        direction = entry["direction"]
        parsed = OWNCommand.parse(raw) if direction == "tx" else OWNMessage.parse(raw)
        assert parsed is not None, f"Failed to parse {direction} frame: {raw}"
        if direction == "tx":
            assert isinstance(parsed, OWNLightingCommand)
            assert parsed.is_status_request or parsed.is_dimension_request
        else:
            assert isinstance(parsed, OWNLightingEvent)
            assert parsed.who == 1


def test_issue_777_occupancy_cycles_match_45s_dimension_7_timeout() -> None:
    """In all three captured occupancy cycles, WHAT 39 arrives ~45-46.6 s after the last WHAT 34."""
    data = _load_trace()
    frames = data["frames"]

    # Verify reported DIMENSION 7 timeout is 45 seconds.
    dim7_frames = [
        OWNEvent.parse(f["raw"])
        for f in frames
        if f["direction"] == "rx" and f["raw"].startswith("*#1*0*7*")
    ]
    assert len(dim7_frames) == 2
    for ev in dim7_frames:
        assert ev.message_type == MESSAGE_TYPE_MOTION_TIMEOUT
        assert ev.motion_timeout == timedelta(seconds=45)

    # Verify each WHAT 39 is preceded by a last WHAT 34 ~45-47 s earlier and followed
    # within <100 ms by *1*1000#0*52## and *1*0*52##.
    what39_indices = [i for i, f in enumerate(frames) if f["raw"] == "*1*39*52##"]
    assert len(what39_indices) == 3

    for idx in what39_indices:
        prev_what34 = max(
            (f for f in frames[:idx] if f["raw"] == "*1*34*52##"),
            key=lambda f: f["t"],
        )
        hold_s = frames[idx]["t"] - prev_what34["t"]
        assert 45.0 <= hold_s <= 47.0

        assert frames[idx + 1]["raw"] == "*1*1000#0*52##"
        assert frames[idx + 2]["raw"] == "*1*0*52##"
        off_delay_s = frames[idx + 2]["t"] - frames[idx]["t"]
        assert 0.0 < off_delay_s <= 0.15


def test_issue_777_reload_dimension_replies_precede_command_nack() -> None:
    """Verify @eC0y0te's reload + tcpdump trace (#issuecomment-6375223714):
    each WHERE=0 reply arrives on the event session ~138-139 ms after worker unqueue,
    while the command-session *#*0## NACK arrives ~2.0-2.1 s after worker unqueue.
    """
    data = _load_trace()
    timeline = data["reload_dimension_timing"]
    assert len(timeline) == 6

    # Timestamps are strictly monotonic across the single-worker reload sequence.
    times = [step["t"] for step in timeline]
    assert times == sorted(times)

    # First request: *#1*52*5## -> *#1*0*5*2## (+138 ms) -> *#*0## (+2.109 s)
    req5, rep5, nack5 = timeline[0], timeline[1], timeline[2]
    assert req5["event"] == "worker_unqueued" and req5["raw"] == "*#1*52*5##"
    assert rep5["event"] == "event_reply_received" and rep5["raw"] == "*#1*0*5*2##"
    assert nack5["event"] == "command_nack_received" and nack5["raw"] == "*#*0##"
    assert req5["t"] < rep5["t"] < nack5["t"]
    assert pytest.approx(rep5["t"] - req5["t"], abs=1e-3) == 0.138
    assert pytest.approx(nack5["t"] - req5["t"], abs=1e-3) == 2.109

    parsed_rep5 = OWNEvent.parse(rep5["raw"])
    assert parsed_rep5.message_type == MESSAGE_TYPE_PIR_SENSITIVITY
    assert parsed_rep5.pir_sensitivity == 2

    # Second request: *#1*52*7## unqueued 51 ms after first NACK -> *#1*0*7*0*0*45## (+139 ms) -> *#*0## (+2.018 s)
    req7, rep7, nack7 = timeline[3], timeline[4], timeline[5]
    assert req7["event"] == "worker_unqueued" and req7["raw"] == "*#1*52*7##"
    assert rep7["event"] == "event_reply_received" and rep7["raw"] == "*#1*0*7*0*0*45##"
    assert nack7["event"] == "command_nack_received" and nack7["raw"] == "*#*0##"
    assert nack5["t"] < req7["t"] < rep7["t"] < nack7["t"]
    assert pytest.approx(rep7["t"] - req7["t"], abs=1e-3) == 0.139
    assert pytest.approx(nack7["t"] - req7["t"], abs=1e-3) == 2.018

    parsed_rep7 = OWNEvent.parse(rep7["raw"])
    assert parsed_rep7.message_type == MESSAGE_TYPE_MOTION_TIMEOUT
    assert parsed_rep7.motion_timeout == timedelta(seconds=45)


@pytest.mark.asyncio
async def test_issue_777_trace_replay_on_configured_light_52(hass) -> None:
    """Replaying all 33 rx frames from the F454 trace drives light 52 cleanly without creating WHERE=0 entities."""
    data = _load_trace()
    mock_gateway = MagicMock()
    mock_gateway.mac = MAC
    lights = {"spots_wc": {CONF_WHERE: "52", CONF_NAME: "Spots WC"}}
    hass.data.setdefault(DOMAIN, {})[MAC] = {
        "entity": mock_gateway,
        CONF_PLATFORMS: {"light": lights},
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": MAC}
    config_entry.entry_id = "entry_777_trace"
    mock_er = MagicMock()

    added: list = []
    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.discovery.er.async_get", return_value=mock_er),
        patch("custom_components.myhome.light.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.light.er.async_get", return_value=mock_er),
    ):
        attach_runtime(hass, config_entry)
        await async_setup_light(hass, config_entry, added.extend)
        await hass.async_block_till_done()
        assert len(added) == 1
        light = added[0]
        assert light._device_id == "52"

        for entry in data["frames"]:
            if entry["direction"] != "rx":
                continue
            async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(entry["raw"]))
            await hass.async_block_till_done()

        # At the end of the trace (14:17:38.670 *1*0*52##), the light is OFF and no extra entities were created.
        assert light._attr_is_on is False
        assert [e._device_id for e in added] == ["52"]
