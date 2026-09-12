"""Test the MyHOME media player platform and dynamic proxy."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.media_player import (
    DOMAIN as PLATFORM,
)
from homeassistant.components.media_player import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import CONF_MAC
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    OWNSoundEvent,
)

from custom_components.myhome.const import (
    CONF_DECODER_ENTITY,
    CONF_DECODER_PRE_GAIN,
    CONF_DECODER_SOURCE,
    CONF_ENTITY,
    DOMAIN,
)
from custom_components.myhome.decoder_pool import DecoderPool
from custom_components.myhome.media_player import (
    MyHOMEMediaPlayer,
    _build_pool,
    async_setup_entry,
    async_unload_entry,
)


@pytest.fixture
def mock_gateway():
    gateway = MagicMock()
    gateway.mac = "00:11:22:33:44:55"
    gateway.log_id = "[MYHOME gateway - 192.168.1.5]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    return gateway


@pytest.fixture
def mock_config_entry():
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {CONF_MAC: "00:11:22:33:44:55"}
    entry.options = {
        CONF_DECODER_ENTITY.format(1): "media_player.squeezelite_1",
        CONF_DECODER_SOURCE.format(1): 1,
        CONF_DECODER_PRE_GAIN.format(1): 15,
        CONF_DECODER_ENTITY.format(2): "media_player.cambridge_2",
        CONF_DECODER_SOURCE.format(2): 2,
        CONF_DECODER_PRE_GAIN.format(2): 10,
    }
    return entry


@pytest.fixture
def player(hass, mock_gateway):
    p = MyHOMEMediaPlayer(
        hass=hass,
        name="Audio Zone 1",
        entity_name=None,
        device_id="1#16",
        who="16",
        where="1",
        manufacturer="BTicino",
        model="Audio System",
        gateway=mock_gateway,
    )
    p.hass = hass
    p.entity_id = "media_player.audio_zone_1"
    return p


def test_build_pool(hass, mock_config_entry):
    """Test building decoder pool from config entry options."""
    pool = _build_pool(hass, mock_config_entry)
    assert pool.is_configured is True
    assert len(pool.decoder_entity_ids) == 2
    assert pool._decoder_map["media_player.squeezelite_1"] == 1
    assert pool.get_pre_gain("media_player.squeezelite_1") == 15
    assert pool._decoder_map["media_player.cambridge_2"] == 2
    assert pool.get_pre_gain("media_player.cambridge_2") == 10


@pytest.mark.asyncio
async def test_setup_and_unload_entry_with_restored_entities(hass, mock_config_entry, mock_gateway):
    """Test setup entry restoring existing entities from registry and unloading."""
    hass.data = {
        DOMAIN: {
            mock_config_entry.data[CONF_MAC]: {
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    registry_entry = MagicMock()
    registry_entry.domain = PLATFORM
    registry_entry.unique_id = f"{mock_gateway.mac}-1#16"

    async_add_entities = MagicMock()

    with patch("homeassistant.helpers.entity_registry.async_get") as mock_er_get, \
         patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[registry_entry]):
        mock_er_get.return_value = MagicMock()
        await async_setup_entry(hass, mock_config_entry, async_add_entities)

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 1
    assert entities[0].name == "Audio Zone 1"

    # Unload
    assert await async_unload_entry(hass, mock_config_entry) is True


@pytest.mark.asyncio
async def test_dynamic_discovery_listener(hass, mock_config_entry, mock_gateway):
    """Test dynamic discovery and filtering of media players from bus messages."""
    hass.data = {
        DOMAIN: {
            mock_config_entry.data[CONF_MAC]: {
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    registry_entry = MagicMock()
    registry_entry.domain = PLATFORM
    registry_entry.unique_id = f"{mock_gateway.mac}-1#16"

    async_add_entities = MagicMock()

    with patch("homeassistant.helpers.entity_registry.async_get"), \
         patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[registry_entry]):
        await async_setup_entry(hass, mock_config_entry, async_add_entities)

    mac = mock_config_entry.data[CONF_MAC]

    # Test filtering out message without zone
    no_zone_msg = MagicMock(spec=OWNSoundEvent, zone=None)
    async_dispatcher_send(hass, f"myhome_message_{mac}", "RAW_STRING")
    async_dispatcher_send(hass, f"myhome_message_{mac}", no_zone_msg)

    # Test filtering out source event early (line 154)
    src_msg = MagicMock(spec=OWNSoundEvent, zone="101", is_source_event=True)
    async_dispatcher_send(hass, f"myhome_message_{mac}", src_msg)

    # Test pseudo-zone routing event matching known player 1#16 (lines 160-169)
    routing_msg = MagicMock(spec=OWNSoundEvent, zone="111", is_source_event=False)
    async_dispatcher_send(hass, f"myhome_message_{mac}", routing_msg)

    # Test source event filter before unique_id (line 173)
    src_msg_late = MagicMock(spec=OWNSoundEvent, zone="2", is_source_event=True)
    async_dispatcher_send(hass, f"myhome_message_{mac}", src_msg_late)

    # Test standard zone event discovery
    zone_msg = MagicMock(
        spec=OWNSoundEvent,
        zone="2",
        who="16",
        is_source_event=False,
        is_on=True,
        is_off=False,
        volume=None,
    )
    async_dispatcher_send(hass, f"myhome_message_{mac}", zone_msg)

    # Restored (1) + Discovered (1)
    assert async_add_entities.call_count == 2
    new_players = async_add_entities.call_args[0][0]
    assert len(new_players) == 1
    assert new_players[0].name == "Audio Zone 2"


@pytest.mark.asyncio
async def test_media_player_features_with_and_without_pool(hass, player, mock_gateway):
    """Test feature advertisement depending on decoder pool configuration."""
    hass.data = {DOMAIN: {mock_gateway.mac: {}}}
    assert MediaPlayerEntityFeature.PLAY_MEDIA not in player.supported_features
    assert MediaPlayerEntityFeature.TURN_ON in player.supported_features
    assert MediaPlayerEntityFeature.SELECT_SOURCE in player.supported_features

    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.is_configured = True
    mock_pool.decoder_entity_ids = ["media_player.squeezelite_1"]
    hass.data[DOMAIN][mock_gateway.mac]["decoder_pool"] = mock_pool

    assert MediaPlayerEntityFeature.PLAY_MEDIA in player.supported_features
    assert MediaPlayerEntityFeature.PAUSE in player.supported_features
    assert MediaPlayerEntityFeature.NEXT_TRACK in player.supported_features


@pytest.mark.asyncio
async def test_async_added_to_hass_and_pool_rebuild(hass, player, mock_gateway):
    """Test lifecycle registration and pool rebuild listener."""
    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.is_configured = True
    mock_pool.decoder_entity_ids = ["media_player.squeezelite_1"]
    hass.data = {DOMAIN: {mock_gateway.mac: {"decoder_pool": mock_pool}}}

    player.async_write_ha_state = MagicMock()

    await player.async_added_to_hass()

    # Trigger pool rebuild dispatcher
    async_dispatcher_send(hass, f"myhome_pool_updated_{mock_gateway.mac}")
    player.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_play_media_without_configured_pool(hass, player, mock_gateway):
    """Test play_media does nothing if pool is not configured."""
    hass.data = {DOMAIN: {mock_gateway.mac: {}}}

    await player.async_play_media("music", "http://stream.url")
    assert player._active_decoder is None


@pytest.mark.asyncio
async def test_play_media_all_decoders_busy(hass, player, mock_gateway):
    """Test play_media raises HomeAssistantError when all decoders are busy."""
    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.is_configured = True
    mock_pool.claim = AsyncMock(return_value=None)
    hass.data = {DOMAIN: {mock_gateway.mac: {"decoder_pool": mock_pool}}}

    with pytest.raises(HomeAssistantError, match="All audio matrix inputs are currently in use"):
        await player.async_play_media("music", "http://stream.url")


@pytest.mark.asyncio
async def test_play_media_success_and_wake_off_decoder(hass, player, mock_gateway):
    """Test play_media claiming decoder, waking it from off, waking amp, and playing."""
    player.async_write_ha_state = MagicMock()
    player.async_schedule_update_ha_state = MagicMock()

    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.is_configured = True
    mock_pool.claim = AsyncMock(return_value=("media_player.squeezelite_1", 1))
    hass.data = {DOMAIN: {mock_gateway.mac: {"decoder_pool": mock_pool}}}

    hass.states.async_set("media_player.squeezelite_1", MediaPlayerState.OFF)

    async def mock_sleep_wake(seconds):
        hass.states.async_set("media_player.squeezelite_1", MediaPlayerState.IDLE)

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as mock_call, \
         patch("asyncio.sleep", side_effect=mock_sleep_wake):
        await player.async_play_media(
            "music",
            "http://stream.url",
            announce=True,
            enqueue="replace",
            extra={"test": 123},
        )

    assert player._active_decoder == "media_player.squeezelite_1"
    assert player.state == MediaPlayerState.IDLE

    calls = mock_call.call_args_list
    assert len(calls) == 2
    assert calls[0].args == ("media_player", "turn_on", {"entity_id": "media_player.squeezelite_1"})
    assert calls[1].args[0] == "media_player"
    assert calls[1].args[1] == "play_media"
    assert calls[1].args[2]["entity_id"] == "media_player.squeezelite_1"
    assert calls[1].args[2]["media_content_id"] == "http://stream.url"
    assert calls[1].args[2]["announce"] is True
    assert calls[1].args[2]["enqueue"] == "replace"


@pytest.mark.asyncio
async def test_play_media_failure_releases_decoder(hass, player, mock_gateway):
    """Test play_media error recovery when decoder fails to start playback."""
    player._attr_state = MediaPlayerState.ON

    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.is_configured = True
    mock_pool.claim = AsyncMock(return_value=("media_player.squeezelite_1", 1))
    mock_pool.release = AsyncMock()
    hass.data = {DOMAIN: {mock_gateway.mac: {"decoder_pool": mock_pool}}}

    hass.states.async_set("media_player.squeezelite_1", MediaPlayerState.IDLE)

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock, side_effect=RuntimeError("Connection refused")):
        with pytest.raises(HomeAssistantError, match="failed to start playback"):
            await player.async_play_media("music", "http://stream.url")

    mock_pool.release.assert_called_once_with(player.entity_id)
    assert player._active_decoder is None


@pytest.mark.asyncio
async def test_transport_controls_forwarding(hass, player):
    """Test forwarding play/pause/stop/next/prev controls to active decoder."""
    player._active_decoder = "media_player.squeezelite_1"

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as mock_call:
        await player.async_media_pause()
        await player.async_media_play()
        await player.async_media_stop()
        await player.async_media_next_track()
        await player.async_media_previous_track()

        assert mock_call.call_count == 5
        services_called = [c.args[1] for c in mock_call.call_args_list]
        assert services_called == [
            "media_pause",
            "media_play",
            "media_stop",
            "media_next_track",
            "media_previous_track",
        ]


@pytest.mark.asyncio
async def test_turn_on_and_turn_off_with_active_decoder(hass, player, mock_gateway):
    """Test turning zone on and turning off with clean decoder release."""
    player.async_schedule_update_ha_state = MagicMock()

    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.release = AsyncMock()
    hass.data = {DOMAIN: {mock_gateway.mac: {"decoder_pool": mock_pool}}}

    # Turn on
    with patch("asyncio.sleep", return_value=None):
        await player.async_turn_on()
    assert mock_gateway.send.call_count >= 2

    # Set active decoder and turn off
    player._active_decoder = "media_player.squeezelite_1"
    player._attr_state = MediaPlayerState.ON

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as mock_call:
        await player.async_turn_off()
        assert player.state == MediaPlayerState.OFF
        mock_call.assert_called_once_with(
            "media_player", "media_stop", {"entity_id": "media_player.squeezelite_1"}
        )
        mock_pool.release.assert_called_once_with(player.entity_id)
        assert player._active_decoder is None


@pytest.mark.asyncio
async def test_volume_controls_and_gain_staging(hass, player, mock_gateway):
    """Test volume up/down, volume set with gain staging, and mute propagation."""
    player.async_schedule_update_ha_state = MagicMock()

    # Step volume
    await player.async_volume_up()
    await player.async_volume_down()
    assert mock_gateway.send.call_count == 2

    # Set volume with active decoder and pre-gain staging
    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.get_pre_gain.return_value = 20  # +20% pre-gain
    hass.data = {DOMAIN: {mock_gateway.mac: {"decoder_pool": mock_pool}}}

    player._active_decoder = "media_player.squeezelite_1"
    player._attr_is_volume_muted = True

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as mock_call:
        await player.async_set_volume_level(0.5)
        assert player._attr_is_volume_muted is False

        # Check gain staging service call: 0.5 + 20/100 = 0.70
        mock_call.assert_called_once_with(
            "media_player",
            "volume_set",
            {"entity_id": "media_player.squeezelite_1", "volume_level": 0.70},
        )

        # Mute volume
        mock_call.reset_mock()
        hass.states.async_set("media_player.squeezelite_1", MediaPlayerState.PLAYING, {"is_volume_muted": False})

        await player.async_mute_volume(True)
        assert player._attr_is_volume_muted is True
        mock_call.assert_any_call(
            "media_player", "volume_mute", {"entity_id": "media_player.squeezelite_1", "is_volume_muted": True}
        )

        # Unmute volume
        mock_call.reset_mock()
        await player.async_mute_volume(False)
        assert player._attr_is_volume_muted is False


@pytest.mark.asyncio
async def test_source_selection_ignored(hass, player, mock_gateway):
    """Test source selection via HA is ignored to prevent audible relay hiss."""
    await player.async_select_source("Source 2")
    mock_gateway.send.assert_not_called()


def test_metadata_and_state_mirroring(hass, player, mock_gateway):
    """Test state and track metadata mirrored from backend decoder."""
    # Zone off -> state is OFF regardless of decoder
    player._attr_state = MediaPlayerState.OFF
    player._active_decoder = "media_player.squeezelite_1"
    hass.states.async_set("media_player.squeezelite_1", MediaPlayerState.PLAYING)
    assert player.state == MediaPlayerState.OFF

    # Zone on -> mirrors PLAYING from decoder
    player._attr_state = MediaPlayerState.ON
    assert player.state == MediaPlayerState.PLAYING

    # Check track metadata
    hass.states.async_set(
        "media_player.squeezelite_1",
        MediaPlayerState.PLAYING,
        {
            "media_title": "Test Title",
            "media_artist": "Test Artist",
            "media_album_name": "Test Album",
            "entity_picture": "http://album.art/pic.jpg",
        },
    )
    assert player.media_title == "Test Title"
    assert player.media_artist == "Test Artist"
    assert player.media_album_name == "Test Album"
    assert player.entity_picture == "http://album.art/pic.jpg"

    # When decoder state is missing, metadata returns None (line 713)
    player._active_decoder = "media_player.missing"
    assert player.media_title is None


def test_decoder_state_changed_reverse_sync(hass, player, mock_gateway):
    """Test volume reverse-sync when user changes decoder volume externally."""
    # Return early if no active decoder (line 736)
    player._active_decoder = None
    player._async_decoder_state_changed(MagicMock())

    player._active_decoder = "media_player.squeezelite_1"
    player._attr_volume_level = 0.3
    player.async_schedule_update_ha_state = MagicMock()

    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.get_pre_gain.return_value = 10  # 10%
    hass.data = {DOMAIN: {mock_gateway.mac: {"decoder_pool": mock_pool}}}

    # Ignore if not active decoder (line 738)
    event_other = MagicMock()
    event_other.data = {"entity_id": "media_player.other", "new_state": None}
    player._async_decoder_state_changed(event_other)

    # Ignore if syncing volume
    player._syncing_volume = True
    new_state = State("media_player.squeezelite_1", MediaPlayerState.PLAYING, {"volume_level": 0.60})
    event = MagicMock()
    event.data = {"entity_id": "media_player.squeezelite_1", "new_state": new_state}
    player._async_decoder_state_changed(event)
    assert player._attr_volume_level == 0.3

    # Reverse sync when not syncing volume
    player._syncing_volume = False
    player._async_decoder_state_changed(event)
    assert pytest.approx(player._attr_volume_level, 0.01) == 0.50


@pytest.mark.asyncio
async def test_handle_event_bus_messages(hass, player, mock_gateway):
    """Test handling bus messages for routing, state, and volume."""
    player.async_schedule_update_ha_state = MagicMock()

    # Async update
    await player.async_update()
    mock_gateway.send_status_request.assert_called_once()

    # Matrix routing event (e.g. zone 121 -> Route source 2 to zone x1)
    msg_routing = MagicMock(spec=OWNSoundEvent, zone="121", is_on=False, is_off=False, volume=None)
    player.handle_event(msg_routing)
    assert player.source == "Source 2"

    # Turn on event
    msg_on = MagicMock(spec=OWNSoundEvent, zone="1", is_on=True, is_off=False, volume=None)
    player.handle_event(msg_on)
    assert player.state == MediaPlayerState.ON

    # Turn off event with active decoder
    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.release = AsyncMock()
    hass.data = {DOMAIN: {mock_gateway.mac: {"decoder_pool": mock_pool}}}
    player._active_decoder = "media_player.squeezelite_1"

    msg_off = MagicMock(spec=OWNSoundEvent, zone="1", is_on=False, is_off=True, volume=None)
    player.handle_event(msg_off)
    assert player.state == MediaPlayerState.OFF
    assert player._active_decoder is None

    # Volume update with mute / unmute detection
    msg_vol_0 = MagicMock(spec=OWNSoundEvent, zone="1", is_on=False, is_off=False, volume=0)
    player.handle_event(msg_vol_0)
    assert player._attr_volume_level == 0.0
    assert player.is_volume_muted is True

    msg_vol_15 = MagicMock(spec=OWNSoundEvent, zone="1", is_on=False, is_off=False, volume=15)
    player.handle_event(msg_vol_15)
    assert pytest.approx(player._attr_volume_level, 0.01) == 15 / 31.0
    assert player.is_volume_muted is False

    # Catch RuntimeError in async_schedule_update_ha_state
    player.async_schedule_update_ha_state.side_effect = RuntimeError("HA state error")
    player.handle_event(msg_vol_15)


@pytest.mark.asyncio
async def test_play_media_decoder_fails_to_wake_warning(hass, player, mock_gateway):
    """Test play_media when decoder does not wake up within timeout logs warning."""
    player.async_write_ha_state = MagicMock()
    player.async_schedule_update_ha_state = MagicMock()

    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.is_configured = True
    mock_pool.claim = AsyncMock(return_value=("media_player.squeezelite_1", 1))
    hass.data = {DOMAIN: {mock_gateway.mac: {"decoder_pool": mock_pool}}}

    # Decoder stays off
    hass.states.async_set("media_player.squeezelite_1", MediaPlayerState.OFF)

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock), \
         patch("asyncio.sleep", return_value=None):
        await player.async_play_media("music", "http://stream.url")

    assert player._active_decoder == "media_player.squeezelite_1"


@pytest.mark.asyncio
async def test_turn_off_decoder_stop_error_handled(hass, player, mock_gateway):
    """Test turn_off handles exception when stopping decoder playback."""
    player.async_schedule_update_ha_state = MagicMock()

    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.release = AsyncMock()
    hass.data = {DOMAIN: {mock_gateway.mac: {"decoder_pool": mock_pool}}}

    player._active_decoder = "media_player.squeezelite_1"
    player._attr_state = MediaPlayerState.ON

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock, side_effect=RuntimeError("Decoder unreachable")):
        await player.async_turn_off()

    assert player.state == MediaPlayerState.OFF
    mock_pool.release.assert_called_once_with(player.entity_id)
    assert player._active_decoder is None


@pytest.mark.asyncio
async def test_mute_volume_decoder_error_handled(hass, player, mock_gateway):
    """Test mute_volume handles exception when calling decoder volume_mute."""
    player.async_schedule_update_ha_state = MagicMock()

    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.get_pre_gain.return_value = 0
    hass.data = {DOMAIN: {mock_gateway.mac: {"decoder_pool": mock_pool}}}

    player._active_decoder = "media_player.squeezelite_1"
    hass.states.async_set("media_player.squeezelite_1", MediaPlayerState.PLAYING, {"is_volume_muted": False})

    # async_call succeeds for volume_set, but raises for volume_mute
    async def mock_call(domain, service, data):
        if service == "volume_mute":
            raise RuntimeError("Mute not supported")

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock, side_effect=mock_call):
        await player.async_mute_volume(True)

    assert player._attr_is_volume_muted is True

