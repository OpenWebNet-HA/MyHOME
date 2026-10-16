"""Replay and regression tests for authentic video intercom ducking and volume restoration.

Validates the media player entity and centralised power state engine against the
authentic on-wire bus trace captured from an MH202 gateway with F441M audio matrix
and video entrance panel during intercom video streaming (Issue #669).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.media_player.const import MediaPlayerState
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNMessage, OWNSoundEvent

from custom_components.myhome.const import CONF_SOURCE_DEFAULTS, CONF_SOURCE_NAME
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.decoder_pool import DecoderPool
from tests.test_component_media_player import _create_test_zone

FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "traces"
    / "mh202_sound_intercom_ducking"
    / "myhome_trace_MH202_sound_intercom_ducking.json"
)


@pytest.fixture
def mock_gateway():
    gateway = MagicMock()
    gateway.mac = "00:03:50:11:22:33"
    gateway.log_id = "[MYHOME gateway - MH202]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    return gateway


def _resolve_where(frame_str: str) -> str | None:
    """Extract the amplifier WHERE target from WHO 16 or WHO 22 frame."""
    if frame_str.startswith("*#16*"):
        parts = frame_str.split("*")
        return parts[2] if len(parts) > 2 else None
    if frame_str.startswith("*#22*3#"):
        # *#22*3#AREA#POINT*...
        parts = frame_str.split("*")
        where_field = parts[2]
        _, area, point = where_field.split("#")
        return f"{area}{point}"
    return None


@pytest.mark.asyncio
async def test_mh202_intercom_ducking_trace_keeps_all_off_amplifiers_off(hass, mock_gateway):
    """Replay authentic MH202 trace (#669): ducking and restoration must keep OFF zones OFF."""
    assert FIXTURE_PATH.is_file(), f"Missing trace fixture: {FIXTURE_PATH}"

    with open(FIXTURE_PATH, encoding="utf-8") as f:
        trace_data = json.load(f)

    frames = trace_data["frames"]
    assert len(frames) == 30

    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool

    base_options = {
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_DEFAULTS: {"3": 2, "4": 2, "5": 2, "6": 2, "7": 2, "8": 2},
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    # All six amplifiers in the house (where: 31, 41, 51, 61, 71, 81)
    zones = {}
    for where in ("31", "41", "51", "61", "71", "81"):
        z = _create_test_zone(hass, mock_gateway, runtime, where, f"media_player.zone_{where}")
        z._options = lambda: dict(base_options)
        z._gateway_handler.config_entry.options = dict(base_options)
        z._attr_state = MediaPlayerState.OFF
        zones[where] = z

    # Replay all 30 frames in authentic sequence
    for frame_entry in frames:
        raw = frame_entry["raw"]
        target = _resolve_where(raw)
        if target and target in zones:
            parsed = OWNSoundEvent.parse(raw)
            if parsed:
                zones[target].handle_event(parsed)

    await hass.async_block_till_done()

    # 1. Verify every amplifier remains strictly MediaPlayerState.OFF
    for where, z in zones.items():
        assert z.state == MediaPlayerState.OFF, f"Zone {where} woke to {z.state}"
        assert pool.get_leader(z.entity_id) is None
        assert z.group_members is None

    # 2. Verify restored volume levels
    assert zones["51"].volume_level == pytest.approx(0.0)
    assert zones["31"].volume_level == pytest.approx(1 / 31.0)
    assert zones["41"].volume_level == pytest.approx(6 / 31.0)
    assert zones["61"].volume_level == pytest.approx(7 / 31.0)
    assert zones["71"].volume_level == pytest.approx(1 / 31.0)  # remained at ducked level
    assert zones["81"].volume_level == pytest.approx(1 / 31.0)


@pytest.mark.asyncio
async def test_mh202_intercom_ducking_with_concurrent_active_stream(hass, mock_gateway):
    """Intercom ducking trace replay while an independent group is actively streaming."""
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        trace_data = json.load(f)

    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", MediaPlayerState.PLAYING)

    base_options = {
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_DEFAULTS: {"2": 2, "3": 2, "4": 2, "5": 2, "6": 2, "7": 2, "8": 2},
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    # Active group: leader (zone 21) and member (zone 22)
    leader = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_room")
    member = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.kitchen")
    for z in (leader, member):
        z._options = lambda: dict(base_options)
        z._gateway_handler.config_entry.options = dict(base_options)
        z._attr_state = MediaPlayerState.ON
        z._attr_source = "Cambridge"

    await pool.claim("media_player.living_room", preferred_source=2)
    leader._active_decoder = "media_player.streamer"
    await pool.add_member("media_player.living_room", "media_player.kitchen")

    # Inactive zones from trace
    inactive_zones = {}
    for where in ("31", "41", "51", "61", "71", "81"):
        z = _create_test_zone(hass, mock_gateway, runtime, where, f"media_player.zone_{where}")
        z._options = lambda: dict(base_options)
        z._gateway_handler.config_entry.options = dict(base_options)
        z._attr_state = MediaPlayerState.OFF
        inactive_zones[where] = z

    # Replay all frames
    for frame_entry in trace_data["frames"]:
        raw = frame_entry["raw"]
        target = _resolve_where(raw)
        if target and target in inactive_zones:
            parsed = OWNSoundEvent.parse(raw)
            if parsed:
                inactive_zones[target].handle_event(parsed)

    await hass.async_block_till_done()

    # Active group is undisturbed
    assert leader.state == MediaPlayerState.PLAYING
    assert member.state == MediaPlayerState.PLAYING
    assert leader.group_members == ["media_player.living_room", "media_player.kitchen"]
    assert member.group_members == ["media_player.living_room", "media_player.kitchen"]

    # Inactive zones stayed OFF and were never added to the group
    for where, z in inactive_zones.items():
        assert z.state == MediaPlayerState.OFF
        assert z.group_members is None
        assert pool.get_leader(z.entity_id) is None


@pytest.mark.asyncio
async def test_mh202_trace_full_dispatcher_pipeline(hass, mock_gateway):
    """Feed the trace directly into the gateway dispatcher to verify message filtering."""
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        trace_data = json.load(f)

    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    mac = mock_gateway.mac

    base_options = {
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_DEFAULTS: {"4": 2},
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    zone41 = _create_test_zone(hass, mock_gateway, runtime, "41", "media_player.zone_41")
    zone41._options = lambda: dict(base_options)
    zone41._gateway_handler.config_entry.options = dict(base_options)
    zone41._attr_state = MediaPlayerState.OFF

    # Simulate messages reaching the entity via dispatcher
    for frame_entry in trace_data["frames"]:
        raw = frame_entry["raw"]
        parsed = OWNMessage.parse(raw)
        if isinstance(parsed, OWNSoundEvent) and parsed.where == "41":
            zone41.handle_event(parsed)
        else:
            # Send through dispatcher channel to ensure no unhandled exceptions
            async_dispatcher_send(hass, f"myhome_message_{mac}", parsed)

    await hass.async_block_till_done()

    # Zone 41 stayed off, restored to volume 6
    assert zone41.state == MediaPlayerState.OFF
    assert zone41.volume_level == pytest.approx(6 / 31.0)


@pytest.mark.asyncio
async def test_concurrent_intercom_ducking_during_wake_pending_window(hass, mock_gateway):
    """Intercom call arrives while an amplifier is in the middle of a wake-up sequence.

    Tests the critical edge case where an unpowered amplifier receives a turn_on command
    (initiating wake sequence and arming the 3.0s wake-echo window), and during this window
    an intercom ducking frame (*#16*41*1*1##) arrives, followed by the wake echo OFF
    (*16*13*41##), the wake ON (*16*3*41##), and finally intercom restoration (*#16*41*1*6##).

    Expected:
    1. Ducking adjusts volume without aborting the wake sequence.
    2. Echo OFF is ignored as self-echo within the wake window.
    3. Wake ON confirms amplifier is active.
    4. Intercom restoration updates volume to 6/31.0 and amplifier remains ON.
    """
    import time

    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool

    base_options = {
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_DEFAULTS: {"4": 2},
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    zone = _create_test_zone(hass, mock_gateway, runtime, "41", "media_player.zone_41")
    zone._options = lambda: dict(base_options)
    zone._gateway_handler.config_entry.options = dict(base_options)
    zone._attr_state = MediaPlayerState.OFF

    # Simulate wake sequence initiation
    zone._wake_off_sent_at = time.monotonic()
    zone._attr_state = MediaPlayerState.ON
    assert zone._is_wake_echo() is True

    # 1. Door entry ducking arrives during wake echo window
    duck_frame = OWNSoundEvent.parse("*#16*41*1*1##")
    zone.handle_event(duck_frame)
    assert zone.state == MediaPlayerState.ON
    assert zone.volume_level == pytest.approx(1 / 31.0)

    # 2. Gateway echoes the wake sequence's initial OFF frame
    echo_off = OWNSoundEvent.parse("*16*13*41##")
    zone.handle_event(echo_off)
    # Must NOT turn off the amplifier
    assert zone.state == MediaPlayerState.ON

    # 3. Gateway confirms wake sequence ON frame
    wake_on = OWNSoundEvent.parse("*16*3*41##")
    zone.handle_event(wake_on)
    assert zone.state == MediaPlayerState.ON

    # 4. Intercom call ends and restoration frame arrives
    restore_frame = OWNSoundEvent.parse("*#16*41*1*6##")
    zone.handle_event(restore_frame)
    assert zone.state == MediaPlayerState.ON
    assert zone.volume_level == pytest.approx(6 / 31.0)

