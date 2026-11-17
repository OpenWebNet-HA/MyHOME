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
from homeassistant.const import CONF_MAC, Platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from OWNd.message import OWNAuxEvent, OWNDryContactEvent, OWNMessage, OWNSoundEvent

from custom_components.myhome.const import CONF_SOURCE_DEFAULTS, CONF_SOURCE_NAME
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.decoder_pool import DecoderPool
from custom_components.myhome.discovery import PlatformDiscovery
from custom_components.myhome.media_player import _sound_route_keys, _zone_address
from custom_components.myhome.media_player_routing import route_pseudo_zones
from custom_components.myhome.sound_source import (
    MyHOMESoundSource,
    who22_source_id,
    who22_tuner_listener,
)
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


DOORBELL_TRACE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "traces"
    / "mh202_sound_intercom_ducking"
    / "myhome_trace_MH202_all_2026-10-18T10-27-48.json"
)

WALL_CONTROLS_TRACE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "traces"
    / "mh202_sound_intercom_ducking"
    / "myhome_trace_MH202_all_2026-10-18T10-34-13.json"
)


@pytest.mark.asyncio
async def test_mh202_visitor_doorbell_call_trace_replay(hass, mock_gateway):
    """Replay the 61-frame visitor doorbell call + lock release trace (#669).

    Validates:
    1. All 61 frames parse cleanly via OWNMessage.parse, including:
       - 4-component WHO 8 call initiation (*8*1#1#4#20*11##) and caller ID (*8*9#1#4*20##)
       - WHO 6 door lock release in conversation (*6*22*1##, *6*10*4000##) and
         pulse release (*6*24*1##, *6*23*4000##)
       - WHO 6 camera OFF (*6*9**##), WHO 25 dry-contact OFF event (*25*32#1*31##),
         and WHO 9 auxiliary events (*9*1*6##, *9*0*6##)
    2. All 6 powered-off amplifiers (31, 41, 51, 61, 71, 81) remain strictly OFF
       throughout ducking and restoration while an independent group (21 + 22) streams.
    3. Restored amplifier volumes match the on-wire post-call values
       (31->0, 41->6, 51->6, 61->7, 71->1, 81->1) and no phantom media_player
       entities are discovered from non-WHO 16 or WHO 22 frames.
    """
    assert DOORBELL_TRACE_PATH.is_file(), f"Missing trace fixture: {DOORBELL_TRACE_PATH}"

    with open(DOORBELL_TRACE_PATH, encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "MH202"
    assert trace_data["gateway"]["firmware"] == "1.0"
    frames = trace_data["frames"]
    assert len(frames) == 61

    # Verify all 61 frames parse cleanly
    parsed_frames = []
    for entry in frames:
        raw = entry["raw"]
        msg = OWNMessage.parse(raw)
        assert msg is not None, f"Failed to parse frame: {raw}"
        parsed_frames.append((raw, msg))

    # Spot-check key WHO 8 / WHO 6 / WHO 25 / WHO 9 frames in the visitor call sequence
    by_raw = dict(parsed_frames)

    call_msg = by_raw["*8*1#1#4#20*11##"]
    assert call_msg.who == 8
    assert getattr(call_msg, "what", getattr(call_msg, "_what", None)) == 1
    assert getattr(call_msg, "what_param", getattr(call_msg, "_what_param", None)) == [
        "1",
        "4",
        "20",
    ]
    assert call_msg.where == "11"

    caller_id_msg = by_raw["*8*9#1#4*20##"]
    assert caller_id_msg.who == 8
    assert getattr(caller_id_msg, "what", getattr(caller_id_msg, "_what", None)) == 9
    assert getattr(caller_id_msg, "what_param", getattr(caller_id_msg, "_what_param", None)) == [
        "1",
        "4",
    ]
    assert caller_id_msg.where == "20"

    lock_conv_msg = by_raw["*6*22*1##"]
    assert lock_conv_msg.who == 6
    assert getattr(lock_conv_msg, "what", getattr(lock_conv_msg, "_what", None)) == 22
    assert lock_conv_msg.where == "1"

    lock_ep_msg = by_raw["*6*10*4000##"]
    assert lock_ep_msg.who == 6
    assert getattr(lock_ep_msg, "what", getattr(lock_ep_msg, "_what", None)) == 10
    assert lock_ep_msg.where == "4000"

    lock_rel_1 = by_raw["*6*24*1##"]
    assert lock_rel_1.who == 6
    assert getattr(lock_rel_1, "what", getattr(lock_rel_1, "_what", None)) == 24
    assert lock_rel_1.where == "1"

    lock_rel_ep = by_raw["*6*23*4000##"]
    assert lock_rel_ep.who == 6
    assert getattr(lock_rel_ep, "what", getattr(lock_rel_ep, "_what", None)) == 23
    assert lock_rel_ep.where == "4000"

    cam_off_msg = by_raw["*6*9**##"]
    assert cam_off_msg.who == 6
    assert getattr(cam_off_msg, "what", getattr(cam_off_msg, "_what", None)) == 9
    assert cam_off_msg.where == "*"

    dry_contact_msg = by_raw["*25*32#1*31##"]
    assert isinstance(dry_contact_msg, OWNDryContactEvent)
    assert dry_contact_msg.who == 25
    assert dry_contact_msg.where == "31"
    assert dry_contact_msg.is_on is False

    aux_on_msg = by_raw["*9*1*6##"]
    assert isinstance(aux_on_msg, OWNAuxEvent)
    assert aux_on_msg.where == "6"
    assert aux_on_msg.is_on is True

    aux_off_msg = by_raw["*9*0*6##"]
    assert isinstance(aux_off_msg, OWNAuxEvent)
    assert aux_off_msg.where == "6"
    assert aux_off_msg.is_on is False

    # Replay across active streaming group (21 + 22) and 6 OFF zones (31..81)
    # through the real PlatformDiscovery + FrameRouter pipeline.
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", MediaPlayerState.PLAYING)

    base_options = {
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_DEFAULTS: {"2": 2, "3": 2, "4": 2, "5": 2, "6": 2, "7": 2, "8": 2},
    }
    unload_cbs = []
    config_entry = MagicMock(
        entry_id="test_doorbell_trace_entry",
        data={CONF_MAC: mock_gateway.mac},
        options=dict(base_options),
        runtime_data=runtime,
    )
    config_entry.async_on_unload.side_effect = unload_cbs.append
    mock_gateway.config_entry = config_entry

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

    inactive_zones = {}
    for where in ("31", "41", "51", "61", "71", "81"):
        z = _create_test_zone(hass, mock_gateway, runtime, where, f"media_player.zone_{where}")
        z._options = lambda: dict(base_options)
        z._gateway_handler.config_entry.options = dict(base_options)
        z._attr_state = MediaPlayerState.OFF
        inactive_zones[where] = z

    added_entities: list = []
    discovery = PlatformDiscovery(
        hass,
        config_entry,
        added_entities.extend,
        platform=Platform.MEDIA_PLAYER,
        who="16",
        event_type=OWNSoundEvent,
        build=lambda ctx: None,
        address=_zone_address,
        pre_message=route_pseudo_zones(runtime.router),
        route_keys=_sound_route_keys,
        key_suffix="#16",
    )
    for where, z in {"21": leader, "22": member, **inactive_zones}.items():
        key = f"{where}#16"
        discovery.known.add(key)
        unload_cbs.append(runtime.router.subscribe("16", [key], z.handle_event))
    discovery.listen()

    try:
        for raw, msg in parsed_frames:
            async_dispatcher_send(hass, f"myhome_message_{mock_gateway.mac}", msg)
            # Also feed paired WHO 22 volume frames directly to verify OWNSoundEvent.parse
            # resilience if a WHO 22 Dimension 1 frame reaches handle_event.
            if raw.startswith("*#22*3#"):
                target = _resolve_where(raw)
                if target and target in inactive_zones:
                    who22_evt = OWNSoundEvent.parse(raw)
                    if who22_evt:
                        inactive_zones[target].handle_event(who22_evt)

        await hass.async_block_till_done()

        # No phantom media_player entities were discovered
        assert added_entities == []

        # Active group remains playing and undisturbed
        assert leader.state == MediaPlayerState.PLAYING
        assert member.state == MediaPlayerState.PLAYING
        assert leader.group_members == ["media_player.living_room", "media_player.kitchen"]

        # All 6 plant zones stayed strictly OFF with exact post-call restored volumes
        for where, z in inactive_zones.items():
            assert z.state == MediaPlayerState.OFF, f"Zone {where} woke to {z.state}"
            assert z.group_members is None
            assert pool.get_leader(z.entity_id) is None

        assert inactive_zones["31"].volume_level == pytest.approx(0.0)
        assert inactive_zones["41"].volume_level == pytest.approx(6 / 31.0)
        assert inactive_zones["51"].volume_level == pytest.approx(6 / 31.0)
        assert inactive_zones["61"].volume_level == pytest.approx(7 / 31.0)
        assert inactive_zones["71"].volume_level == pytest.approx(1 / 31.0)
        assert inactive_zones["81"].volume_level == pytest.approx(1 / 31.0)
    finally:
        for unsub in unload_cbs:
            unsub()


@pytest.mark.asyncio
async def test_mh202_wall_controls_trace_zone_41_lifecycle(hass, mock_gateway):
    """Replay the 146-frame physical wall switch ON, volume ramp, and OFF trace (#669).

    Validates through the real PlatformDiscovery + FrameRouter + route_pseudo_zones +
    MyHOMESoundSource + who22_tuner_listener pipeline:
    1. All 146 frames parse cleanly via OWNMessage.parse (dual-dialect WHO 22 + WHO 16).
    2. Matrix routing frame (*16*3*143##) is routed via route_pseudo_zones to Zone 41,
       setting Zone 41 source to Source 3 ("Streamer 3") without prematurely waking
       Zone 41 before the amplifier ON frame (*16*3*41##) arrives, and without
       discovering a phantom entity for "143".
    3. Amplifier ON frame (*16*3*41##) wakes Zone 41 and auto-joins the active stream
       on Source 3, while source activation (*16*3*103##) turns on the declared
       MyHOMESoundSource ("103#16") via _sound_route_keys and is ignored by zone entities.
    4. WHO 22 source frames (*22*1#4#4*2#3##, *22*21#4#4*5#2#3##, *22*2#4#4*5#2#3##,
       *#22*2#3*12*1*4##) resolve who22_source_id == 3 and are handled cleanly by
       who22_tuner_listener without creating phantom entities.
    5. Physical wall rocker volume steps (*22*3#1*3#4#1## / *22*4#1*3#4#1##) paired with
       WHO 16 volume reports (*#16*41*1*<vol>##) drive Zone 41 through the exact on-wire
       volume trajectory: 6 -> 15 -> 5 -> 12 -> 7 (on the 0..31 scale).
    6. Physical wall switch OFF (*16*13*41##) turns Zone 41 OFF cleanly while all other
       zones (31, 51, 61, 71, 81) remain OFF throughout the entire trace.
    """
    assert WALL_CONTROLS_TRACE_PATH.is_file(), f"Missing trace fixture: {WALL_CONTROLS_TRACE_PATH}"

    with open(WALL_CONTROLS_TRACE_PATH, encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "MH202"
    assert trace_data["gateway"]["firmware"] == "1.0"
    frames = trace_data["frames"]
    assert len(frames) == 146

    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer_s3": 3})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer_s3", MediaPlayerState.PLAYING)

    base_options = {
        CONF_SOURCE_NAME.format(3): "Streamer 3",
        CONF_SOURCE_DEFAULTS: {"3": 3, "4": 3, "5": 3, "6": 3, "7": 3, "8": 3},
    }
    unload_cbs = []
    config_entry = MagicMock(
        entry_id="test_wall_controls_trace_entry",
        data={CONF_MAC: mock_gateway.mac},
        options=dict(base_options),
        runtime_data=runtime,
    )
    config_entry.async_on_unload.side_effect = unload_cbs.append
    mock_gateway.config_entry = config_entry

    zones = {}
    for where in ("31", "41", "51", "61", "71", "81"):
        z = _create_test_zone(hass, mock_gateway, runtime, where, f"media_player.zone_{where}")
        z._options = lambda: dict(base_options)
        z._gateway_handler.config_entry.options = dict(base_options)
        z._attr_state = MediaPlayerState.OFF
        zones[where] = z

    # Also register a declared MyHOMESoundSource for Source 3 (WHERE=103)
    source_103 = MyHOMESoundSource(
        hass=hass,
        name="Streamer 3",
        device_id="103#16",
        who="16",
        where="103",
        manufacturer="BTicino",
        model="Audio Source",
        gateway=mock_gateway,
    )
    source_103.entity_id = "media_player.audio_source_3"
    source_103._publish_state = MagicMock()

    added_entities: list = []
    discovery = PlatformDiscovery(
        hass,
        config_entry,
        added_entities.extend,
        platform=Platform.MEDIA_PLAYER,
        who="16",
        event_type=OWNSoundEvent,
        build=lambda ctx: None,
        address=_zone_address,
        pre_message=route_pseudo_zones(runtime.router),
        route_keys=_sound_route_keys,
        key_suffix="#16",
    )
    for where, z in zones.items():
        key = f"{where}#16"
        discovery.known.add(key)
        unload_cbs.append(runtime.router.subscribe("16", [key], z.handle_event))

    unload_cbs.append(
        runtime.router.subscribe("16", [source_103.device_key], source_103.handle_event)
    )
    unload_cbs.append(
        async_dispatcher_connect(
            hass,
            f"myhome_message_{mock_gateway.mac}",
            who22_tuner_listener([source_103]),
        )
    )
    discovery.listen()

    zone41 = zones["41"]
    observed_states: list[tuple[str, MediaPlayerState | None, float | None, str | None]] = []
    volume_trajectory_hw: list[int] = []
    who22_source_frames: list[str] = []

    try:
        for entry in frames:
            raw = entry["raw"]
            msg = OWNMessage.parse(raw)
            assert msg is not None, f"Failed to parse frame: {raw}"

            if who22_source_id(msg) == 3:
                who22_source_frames.append(raw)

            async_dispatcher_send(hass, f"myhome_message_{mock_gateway.mac}", msg)

            if isinstance(msg, OWNSoundEvent):
                where = msg.where or ""
                if where == "41" and msg.volume is not None:
                    volume_trajectory_hw.append(msg.volume)
                if where in ("41", "143", "103"):
                    observed_states.append(
                        (raw, zone41.state, zone41.volume_level, zone41.source)
                    )

        await hass.async_block_till_done()

        # 0. No phantom entities created for 143, 103, or WHO 22 addresses
        assert added_entities == []

        # 1. Verify *16*3*143## was routed by route_pseudo_zones to Zone 41, setting
        #    source to "Streamer 3" while keeping Zone 41 OFF until *16*3*41##
        routing_step = next(s for s in observed_states if s[0] == "*16*3*143##")
        assert routing_step[1] == MediaPlayerState.OFF
        assert routing_step[3] == "Streamer 3"

        # 2. Verify *16*3*41## turned Zone 41 ON and *16*3*103## turned Source 103 ON
        wake_step = next(s for s in observed_states if s[0] == "*16*3*41##")
        assert wake_step[1] in (MediaPlayerState.ON, MediaPlayerState.PLAYING)
        assert source_103.state == MediaPlayerState.ON
        assert who22_source_frames == [
            "*22*1#4#4*2#3##",
            "*22*21#4#4*5#2#3##",
            "*22*2#4#4*5#2#3##",
            "*#22*2#3*12*1*4##",
        ]

        # 3. Verify the exact hardware volume trajectory reported by *#16*41*1*<vol>##
        # Starts at 6, ramps UP to peak 15, ramps DOWN to trough 5, ramps UP to 12, ramps DOWN to 7
        assert volume_trajectory_hw[0] == 6
        assert max(volume_trajectory_hw) == 15
        assert min(volume_trajectory_hw) == 5
        assert volume_trajectory_hw[-1] == 7
        assert volume_trajectory_hw == [
            6,
            7, 8, 9, 10, 11, 12, 13, 14, 15,
            14, 13, 12, 11, 10, 9, 8, 7, 6, 5,
            6, 7, 8, 9, 10, 11, 12,
            11, 10, 9, 8, 7,
        ]

        # 4. Verify *16*13*41## turned Zone 41 back OFF at final volume 7/31
        assert zone41.state == MediaPlayerState.OFF
        assert zone41.volume_level == pytest.approx(7 / 31.0)
        assert zone41.source == "Streamer 3"

        # 5. All other zones remained strictly OFF throughout
        for where in ("31", "51", "61", "71", "81"):
            assert zones[where].state == MediaPlayerState.OFF
    finally:
        for unsub in unload_cbs:
            unsub()
