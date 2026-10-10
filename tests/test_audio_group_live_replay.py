"""Replay of the live group lifecycle captured on a physical MH200 + F441M (2026-09-29).

Three rooms (badkamer 36 leads Bureau 21 and Eetkamer 23) on the Audio Decoder
decoder: pause, the anti-hiss switch-off that keeps the group, and the resume.
Every assertion here exists because the live run broke without it; the
fixture's ``findings`` say how (see also the README beside it).
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from OWNd.message import OWNSoundEvent
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.decoder_pool import PAUSE_TAKEOVER_AFTER, DecoderPool
from tests.test_component_media_player import _create_test_zone

FIXTURE = json.loads(
    (
        Path(__file__).parent
        / "fixtures"
        / "traces"
        / "mh200_sound_f441m"
        / "live_2026-09-29_group_park_and_resume.json"
    ).read_text(encoding="utf-8")
)
DECODER = "media_player.audio_decoder"
LEADER = "36"
MEMBERS = ("21", "23")


def _entity_id(where: str) -> str:
    return f"media_player.audio_zone_{where}"


@pytest.fixture
def mock_gateway():
    gateway = MagicMock()
    gateway.mac = "00:11:22:33:44:55"
    gateway.log_id = "[MYHOME gateway - 192.168.1.40]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    return gateway


async def _plant(hass, mock_gateway):
    """The three rooms grouped on the decoder, amplifiers on, decoder playing."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {DECODER: 2})
    runtime.decoder_pool = pool
    zones = {
        where: _create_test_zone(hass, mock_gateway, runtime, where, _entity_id(where))
        for where in (LEADER, *MEMBERS)
    }
    for zone in zones.values():
        zone._attr_state = MediaPlayerState.ON
    hass.states.async_set(DECODER, "on")
    assert await pool.claim(_entity_id(LEADER)) is not None
    for where in MEMBERS:
        await pool.add_member(_entity_id(LEADER), _entity_id(where))
    zones[LEADER]._active_decoder = DECODER
    hass.states.async_set(DECODER, "playing")
    return zones, pool


def _sent(mock_gateway) -> list[str]:
    return [str(call.args[0]) for call in mock_gateway.send.call_args_list]


def _replay(zones, frames) -> None:
    """Feed captured bus frames to the room they are addressed to."""
    for entry in frames:
        message = OWNSoundEvent.parse(entry["frame"])
        if message is None or getattr(message, "is_source_event", False):
            continue
        where = message.where or ""
        for zone in zones.values():
            if zone.where == where or where.startswith("1") and len(where) == 3:
                zone.handle_event(message)


async def _park(hass, zones) -> None:
    """The decoder has been paused for 60 s: run the anti-hiss timer."""
    leader = zones[LEADER]
    timers = []

    def call_later(_hass, delay, action):
        timers.append((delay, action))
        return MagicMock()

    hass.states.async_set(DECODER, "paused")
    with patch("custom_components.myhome.media_player_decoder.async_call_later", side_effect=call_later):
        leader._async_decoder_state_changed(
            MagicMock(data={"entity_id": DECODER, "new_state": State(DECODER, "paused")})
        )
        assert [delay for delay, _ in timers] == [60.0]
        timers[0][1](None)
    await hass.async_block_till_done()


@pytest.mark.parametrize("state", FIXTURE["decoder_states_seen"]["free"])
@pytest.mark.asyncio
async def test_a_decoder_in_these_states_is_free(hass, state):
    """Both live "all decoders busy" failures happened with the Audio Decoder in "on"."""
    pool = DecoderPool(hass, {DECODER: 2})
    hass.states.async_set(DECODER, state)
    assert await pool.claim(_entity_id(LEADER)) == (DECODER, 2)


@pytest.mark.asyncio
async def test_a_paused_decoder_is_free_only_after_five_minutes(hass):
    pool = DecoderPool(hass, {DECODER: 2})
    hass.states.async_set(DECODER, "paused")
    assert await pool.claim(_entity_id(LEADER)) is None
    later = dt_util.utcnow() + timedelta(seconds=PAUSE_TAKEOVER_AFTER + 1)
    with patch("custom_components.myhome.decoder_pool.dt_util.utcnow", return_value=later):
        assert await pool.claim(_entity_id(LEADER)) == (DECODER, 2)


@pytest.mark.parametrize("state", FIXTURE["decoder_states_seen"]["busy"])
@pytest.mark.asyncio
async def test_a_decoder_in_these_states_is_busy(hass, state):
    pool = DecoderPool(hass, {DECODER: 2})
    hass.states.async_set(DECODER, state)
    assert await pool.claim(_entity_id(LEADER)) is None


@pytest.mark.asyncio
async def test_the_pause_switches_every_amplifier_off_and_keeps_the_group(hass, mock_gateway):
    zones, pool = await _plant(hass, mock_gateway)
    mock_gateway.send.reset_mock()

    await _park(hass, zones)

    assert _sent(mock_gateway) == FIXTURE["park"]["expected_tx"]
    assert pool.get_members(_entity_id(LEADER)) == [_entity_id(w) for w in MEMBERS]
    assert pool.owned_decoder(_entity_id(LEADER)) == DECODER

    # The amplifiers' own OFF echoes come back over the bus: nobody leaves the group.
    _replay(zones, FIXTURE["park"]["bus_rx"])
    await hass.async_block_till_done()
    assert pool.get_members(_entity_id(LEADER)) == [_entity_id(w) for w in MEMBERS]

    # Music Assistant dissolves a group whose leader (or a member) reports "off".
    for zone in zones.values():
        assert zone.state in (MediaPlayerState.PAUSED, MediaPlayerState.IDLE)
    assert zones[LEADER].group_members == [_entity_id(LEADER), *(_entity_id(w) for w in MEMBERS)]


@pytest.mark.asyncio
async def test_play_shows_every_room_on_before_the_slow_wake_frames_and_wakes_them_all(hass, mock_gateway):
    zones, pool = await _plant(hass, mock_gateway)
    await _park(hass, zones)
    mock_gateway.send.reset_mock()

    states_at_first_frame: list[MediaPlayerState] = []

    async def record(*_args, **_kwargs):
        if not states_at_first_frame:
            states_at_first_frame.extend(zone.state for zone in zones.values())

    mock_gateway.send.side_effect = record
    hass.states.async_set(DECODER, "on")  # what the decoder reports before a new stream
    async_mock_service(hass, "media_player", "play_media")
    async_mock_service(hass, "media_player", "turn_on")
    with patch("asyncio.sleep", return_value=None):
        await zones[LEADER].async_play_media("music", "http://example.invalid/stream.mp3")

    assert states_at_first_frame == [MediaPlayerState.ON] * 3
    sent = _sent(mock_gateway)
    for frame in FIXTURE["resume"]["expected_tx_contains"]:
        assert frame in sent, frame
    assert sent.index("*16*3*36##") < sent.index("*16*3*21##") < sent.index("*16*3*23##")
    assert pool.get_members(_entity_id(LEADER)) == [_entity_id(w) for w in MEMBERS]

    # What the bus said back (the MH200's wake burst) leaves everything on, at the captured volumes.
    _replay(zones, FIXTURE["resume"]["bus_rx"])
    for zone in zones.values():
        assert zone.state != MediaPlayerState.OFF
    for where, level in FIXTURE["resume"]["volumes_after"].items():
        assert zones[where]._attr_volume_level == pytest.approx(level / 31.0)


def _queue_only_send(mock_gateway, events: list[str]) -> list[asyncio.Future]:
    """``send`` as the real gateway does it: queue the frame, hand back its write future."""
    futures: list[asyncio.Future] = []

    async def send(command, *_args, **_kwargs):
        events.append(str(command).strip("*#").replace("*", "-"))
        future = asyncio.get_running_loop().create_future()
        futures.append(future)
        return future

    mock_gateway.send.side_effect = send
    return futures


async def _resume_by_play(hass, zones, events, play_media=None):
    hass.states.async_set(DECODER, "on")

    async def default_play_media(call):
        events.append("play_media")

    hass.services.async_register("media_player", "play_media", play_media or default_play_media)
    async_mock_service(hass, "media_player", "turn_on")
    with patch("asyncio.sleep", return_value=None):
        await zones[LEADER].async_play_media("music", "http://example.invalid/stream.mp3")


@pytest.mark.asyncio
async def test_play_queues_each_frame_once_and_the_leader_before_the_stream(hass, mock_gateway):
    """One OFF/ON per room, one route per environment and source, and the stream after the leader."""
    zones, _pool = await _plant(hass, mock_gateway)
    await _park(hass, zones)
    mock_gateway.send.reset_mock()
    events: list[str] = []
    _queue_only_send(mock_gateway, events)

    await _resume_by_play(hass, zones, events)

    frames = [e for e in events if e != "play_media"]
    assert frames == [
        "16-13-36",
        "16-3-36",
        "16-3-102",
        "16-3-132",
        "16-3-122",
        "16-13-21",
        "16-3-21",
        "16-13-23",
        "16-3-23",
    ], frames
    assert len(frames) == len(set(frames)), frames
    leader_frames = {"16-13-36", "16-3-36"}
    assert leader_frames <= set(events[: events.index("play_media")])
    assert events.index("16-3-36") < events.index("play_media")


@pytest.mark.asyncio
async def test_the_wake_echo_window_starts_when_the_off_is_written_not_when_it_is_queued(hass, mock_gateway):
    """Behind other frames the OFF reaches the bus late; its echo is still ours."""
    zones, _pool = await _plant(hass, mock_gateway)
    await _park(hass, zones)
    events: list[str] = []
    futures = _queue_only_send(mock_gateway, events)
    member = zones["23"]

    with patch("custom_components.myhome.media_player.time.monotonic", return_value=100.0):
        await member._async_wake_zone()
    off_written = futures[0]
    off_written.set_result(106.0)  # six seconds behind the other rooms' frames
    await hass.async_block_till_done()

    with patch("custom_components.myhome.media_player.time.monotonic", return_value=107.0):
        assert member._is_wake_echo()
    with patch("custom_components.myhome.media_player.time.monotonic", return_value=110.0):
        assert not member._is_wake_echo()


@pytest.mark.asyncio
async def test_a_failed_play_leaves_no_room_showing_on_while_its_amplifier_is_off(hass, mock_gateway):
    zones, _pool = await _plant(hass, mock_gateway)
    await _park(hass, zones)
    events: list[str] = []
    _queue_only_send(mock_gateway, events)

    async def failing_play_media(call):
        raise RuntimeError("refused")

    with pytest.raises(HomeAssistantError):
        await _resume_by_play(hass, zones, events, failing_play_media)
    assert _pool.owned_decoder(_entity_id(LEADER)) is None  # released, not stuck busy
    await hass.async_block_till_done()

    for zone in zones.values():
        assert not zone._wake_pending
        assert (zone.state == MediaPlayerState.ON) != zone._parked  # on only if its frames went out
    assert not [t for t in asyncio.all_tasks() if "wake group members" in (t.get_name() or "")]


@pytest.mark.asyncio
async def test_a_play_that_finds_every_decoder_busy_does_not_leave_the_group_showing_on(hass, mock_gateway):
    zones, pool = await _plant(hass, mock_gateway)
    await _park(hass, zones)
    events: list[str] = []
    _queue_only_send(mock_gateway, events)
    hass.states.async_set(DECODER, "playing")  # someone else's stream: nothing to claim

    with patch.object(pool, "claim", AsyncMock(return_value=None)), pytest.raises(HomeAssistantError):
        await zones["21"].async_play_media("music", "http://example.invalid/stream.mp3")

    for zone in zones.values():
        assert not zone._wake_pending
        assert zone.state in (MediaPlayerState.PAUSED, MediaPlayerState.IDLE)


@pytest.mark.asyncio
async def test_a_wake_that_is_interrupted_leaves_the_room_parked(hass, mock_gateway):
    zones, _pool = await _plant(hass, mock_gateway)
    await _park(hass, zones)
    mock_gateway.send.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await zones["23"]._async_wake_zone()

    assert zones["23"]._parked


def test_the_fixture_records_the_matrix_source_and_the_hardware_note():
    assert FIXTURE["meta"]["zones"][LEADER]["role"] == "leader"
    assert any("OFF then ON" in note for note in FIXTURE["meta"]["findings"])


WAKE_FIXTURE = json.loads(
    (
        Path(__file__).parent
        / "fixtures"
        / "traces"
        / "mh200_sound_f441m"
        / "live_2026-09-29_group_wake_by_play.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.asyncio
async def test_play_writes_the_same_frames_as_the_live_wake(hass, mock_gateway):
    """The frames the integration wrote on the plant are the ones a replayed play queues."""
    live = [
        f["frame"].strip("*#").replace("*", "-")
        for f in WAKE_FIXTURE["second_play_after_auto_off"]
        if f["dir"] == "tx"
    ]
    zones, _pool = await _plant(hass, mock_gateway)
    await _park(hass, zones)
    mock_gateway.send.reset_mock()
    events: list[str] = []
    _queue_only_send(mock_gateway, events)

    await _resume_by_play(hass, zones, events)

    assert [e for e in events if e != "play_media"] == live


@pytest.mark.asyncio
async def test_multi_cycle_park_and_resume_preserves_topology(hass, mock_gateway):
    """Multiple park and resume cycles keep the group books intact across pause/wake cycles."""
    zones, pool = await _plant(hass, mock_gateway)

    # --- Cycle 1: Park via 60s pause timeout, resume via async_turn_on ---
    await _park(hass, zones)
    for zone in zones.values():
        assert zone.state in (MediaPlayerState.PAUSED, MediaPlayerState.IDLE)
    assert pool.get_members(_entity_id(LEADER)) == [_entity_id(w) for w in MEMBERS]

    # Resume via leader turn_on
    mock_gateway.send.reset_mock()
    with patch("asyncio.sleep", return_value=None):
        await zones[LEADER].async_turn_on()
    await hass.async_block_till_done()

    for zone in zones.values():
        assert zone._attr_state == MediaPlayerState.ON
    assert pool.get_members(_entity_id(LEADER)) == [_entity_id(w) for w in MEMBERS]

    # --- Cycle 2: Park again, receive bus volume report while parked, then resume via play ---
    await _park(hass, zones)
    for zone in zones.values():
        assert zone.state in (MediaPlayerState.PAUSED, MediaPlayerState.IDLE)

    # Dimension 1 volume report arrives during parked state: must NOT wake or dissolve
    vol_msg = OWNSoundEvent.parse("*#16*36*1*15##")
    zones[LEADER].handle_event(vol_msg)
    await hass.async_block_till_done()

    assert zones[LEADER].state in (MediaPlayerState.PAUSED, MediaPlayerState.IDLE)
    assert pool.get_members(_entity_id(LEADER)) == [_entity_id(w) for w in MEMBERS]

    # Resume via play_media
    mock_gateway.send.reset_mock()
    events: list[str] = []
    _queue_only_send(mock_gateway, events)
    await _resume_by_play(hass, zones, events)
    await hass.async_block_till_done()

    for zone in zones.values():
        assert zone._attr_state == MediaPlayerState.ON
    assert pool.get_members(_entity_id(LEADER)) == [_entity_id(w) for w in MEMBERS]

    # --- Cycle 3: Park and explicit turn_off disbands group ---
    await _park(hass, zones)
    await zones[LEADER].async_turn_off()
    await hass.async_block_till_done()

    assert pool.get_members(_entity_id(LEADER)) == []
    assert pool.owned_decoder(_entity_id(LEADER)) is None
    assert zones[LEADER].state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_anti_hiss_auto_off_for_owned_and_unowned_rooms(hass, mock_gateway):
    """Anti-hiss auto-off (3s) shuts down both owned and unowned rooms hearing a stopped decoder."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {DECODER: 2})
    runtime.decoder_pool = pool

    # Owned room (holds decoder claim)
    owned_zone = _create_test_zone(hass, mock_gateway, runtime, "36", _entity_id("36"))
    owned_zone._attr_state = MediaPlayerState.ON
    owned_zone._active_decoder = DECODER
    await pool.claim(_entity_id("36"))

    # Unowned room (hearing Source 2 on the matrix, but not in any group)
    unowned_zone = _create_test_zone(hass, mock_gateway, runtime, "21", _entity_id("21"))
    unowned_zone._attr_state = MediaPlayerState.ON
    unowned_zone._attr_source = "Cambridge"

    # Tuner room (hearing Source 1 on the matrix, where no decoder is wired)
    tuner_zone = _create_test_zone(hass, mock_gateway, runtime, "14", _entity_id("14"))
    tuner_zone._attr_state = MediaPlayerState.ON
    tuner_zone._attr_source = "Radio"

    # Mock timers to capture anti-hiss callbacks
    owned_timers = []
    unowned_timers = []
    tuner_timers = []

    def mock_call_later_owned(_hass, delay, action):
        owned_timers.append((delay, action))
        return MagicMock()

    def mock_call_later_unowned(_hass, delay, action):
        unowned_timers.append((delay, action))
        return MagicMock()

    def mock_call_later_tuner(_hass, delay, action):
        tuner_timers.append((delay, action))
        return MagicMock()

    # Decoder transitions to idle
    hass.states.async_set(DECODER, "idle")

    with patch("custom_components.myhome.media_player_decoder.async_call_later", side_effect=mock_call_later_owned):
        owned_zone._async_decoder_state_changed(
            MagicMock(data={"entity_id": DECODER, "new_state": State(DECODER, "idle"), "old_state": State(DECODER, "playing")})
        )

    with patch("custom_components.myhome.media_player_decoder.async_call_later", side_effect=mock_call_later_unowned):
        unowned_zone._async_decoder_state_changed(
            MagicMock(data={"entity_id": DECODER, "new_state": State(DECODER, "idle"), "old_state": State(DECODER, "playing")})
        )

    with patch("custom_components.myhome.media_player_decoder.async_call_later", side_effect=mock_call_later_tuner):
        tuner_zone._async_decoder_state_changed(
            MagicMock(data={"entity_id": DECODER, "new_state": State(DECODER, "idle"), "old_state": State(DECODER, "playing")})
        )

    # 1. Both owned and unowned rooms armed the 3.0s anti-hiss timer
    assert [d for d, _ in owned_timers] == [3.0]
    assert [d for d, _ in unowned_timers] == [3.0]

    # Tuner room does not hear this decoder, so no timer was armed
    assert tuner_timers == []

    # 2. Fire the auto-off timers
    with patch.object(owned_zone, "async_turn_off", new_callable=AsyncMock) as mock_off_owned, \
         patch.object(unowned_zone, "async_turn_off", new_callable=AsyncMock) as mock_off_unowned:
        owned_timers[0][1](None)
        unowned_timers[0][1](None)
        await hass.async_block_till_done()

        mock_off_owned.assert_called_once()
        mock_off_unowned.assert_called_once()

