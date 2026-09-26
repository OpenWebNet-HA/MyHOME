"""Test the MyHOME media player platform and dynamic proxy."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

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
    OWNEvent,
    OWNSoundEvent,
)

from custom_components.myhome.const import (
    CONF_DECODER_ENTITY,
    CONF_DECODER_PRE_GAIN,
    CONF_DECODER_SOURCE,
    CONF_ENTITY,
    CONF_SOURCE_DEFAULTS,
    CONF_SOURCE_NAME,
    DOMAIN,
)
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.decoder_pool import DecoderPool
from custom_components.myhome.media_player import (
    MyHOMEMediaPlayer,
    _build_pool,
    _zone_address,
    async_setup_entry,
    async_unload_entry,
)
from tests.conftest import attach_platform, attach_runtime


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
    # Entities reach the decoder pool through self.platform.config_entry.runtime_data
    entry = MagicMock()
    entry.data = {CONF_MAC: mock_gateway.mac}
    entry.runtime_data = MyHOMERuntimeData(gateway=mock_gateway)
    attach_platform(p, entry)
    return p


def _set_pool(player, pool):
    """Install ``pool`` as the entry's decoder pool (None = not configured)."""
    player.platform.config_entry.runtime_data.decoder_pool = pool


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
        attach_runtime(hass, mock_config_entry)
        await async_setup_entry(hass, mock_config_entry, async_add_entities)

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 1
    assert entities[0]._display_name == "Audio Zone 1"

    # Unload
    attach_runtime(hass, mock_config_entry)
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
    # Amplifier 11: environment 1, so the 111 routing frame below reaches it
    registry_entry.unique_id = f"{mock_gateway.mac}-11#16"

    async_add_entities = MagicMock()

    with patch("homeassistant.helpers.entity_registry.async_get"), \
         patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[registry_entry]):
        attach_runtime(hass, mock_config_entry)
        await async_setup_entry(hass, mock_config_entry, async_add_entities)

    mac = mock_config_entry.data[CONF_MAC]

    # Test filtering out message without zone
    no_zone_msg = MagicMock(spec=OWNSoundEvent, is_source_event=False, where=None)
    async_dispatcher_send(hass, f"myhome_message_{mac}", "RAW_STRING")
    async_dispatcher_send(hass, f"myhome_message_{mac}", no_zone_msg)

    # Test filtering out source event early (line 154)
    src_msg = MagicMock(spec=OWNSoundEvent, where="101", is_source_event=True)
    async_dispatcher_send(hass, f"myhome_message_{mac}", src_msg)

    # Test pseudo-zone routing event matching known player 11#16 (environment 1)
    routing_msg = MagicMock(spec=OWNSoundEvent, where="111", is_source_event=False)
    async_dispatcher_send(hass, f"myhome_message_{mac}", routing_msg)

    # Test source event filter before unique_id (line 173)
    src_msg_late = MagicMock(spec=OWNSoundEvent, where="2", is_source_event=True)
    async_dispatcher_send(hass, f"myhome_message_{mac}", src_msg_late)

    # Test standard zone event discovery
    zone_msg = MagicMock(
        spec=OWNSoundEvent,
        where="2",
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
    assert new_players[0]._display_name == "Audio Zone 2"


@pytest.mark.asyncio
async def test_media_player_features_with_and_without_pool(hass, player, mock_gateway):
    """Test feature advertisement depending on decoder pool configuration."""
    _set_pool(player, None)
    assert MediaPlayerEntityFeature.PLAY_MEDIA not in player.supported_features
    assert MediaPlayerEntityFeature.TURN_ON in player.supported_features
    assert MediaPlayerEntityFeature.SELECT_SOURCE in player.supported_features

    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.is_configured = True
    mock_pool.decoder_entity_ids = ["media_player.squeezelite_1"]
    _set_pool(player, mock_pool)

    assert MediaPlayerEntityFeature.PLAY_MEDIA in player.supported_features
    assert MediaPlayerEntityFeature.PAUSE in player.supported_features
    assert MediaPlayerEntityFeature.NEXT_TRACK in player.supported_features


@pytest.mark.asyncio
async def test_async_added_to_hass_and_pool_rebuild(hass, player, mock_gateway):
    """Test lifecycle registration and pool rebuild listener."""
    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.is_configured = True
    mock_pool.decoder_entity_ids = ["media_player.squeezelite_1"]
    _set_pool(player, mock_pool)

    player.async_write_ha_state = MagicMock()

    await player.async_added_to_hass()

    # Trigger pool rebuild dispatcher
    async_dispatcher_send(hass, f"myhome_pool_updated_{mock_gateway.mac}")
    player.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_play_media_without_configured_pool(hass, player, mock_gateway):
    """Test play_media does nothing if pool is not configured."""
    _set_pool(player, None)

    await player.async_play_media("music", "http://stream.url")
    assert player._active_decoder is None


@pytest.mark.asyncio
async def test_play_media_all_decoders_busy(hass, player, mock_gateway):
    """Test play_media raises HomeAssistantError when all decoders are busy."""
    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.is_configured = True
    mock_pool.claim = AsyncMock(return_value=None)
    _set_pool(player, mock_pool)

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
    _set_pool(player, mock_pool)

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
    _set_pool(player, mock_pool)

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
    _set_pool(player, mock_pool)

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
    _set_pool(player, mock_pool)

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


def _name_sources(player, **names):
    """Give the entry configured matrix source names, e.g. ``_name_sources(p, s2="Cambridge")``."""
    options = dict(player.platform.config_entry.options or {})
    for key, value in names.items():
        options[CONF_SOURCE_NAME.format(int(key[1:]))] = value
    player.platform.config_entry.options = options


@pytest.mark.asyncio
async def test_select_source_routes_environment(hass, player, mock_gateway):
    """Selecting a source activates it and routes the zone's environment to it.

    A wall panel sends ``*16*3*102##`` + ``*16*3*122##`` for zone 23; the
    routing address carries the environment digit, not the amplifier digit.
    """
    player._where = "23"
    _name_sources(player, s2="Cambridge")

    await player.async_select_source("Cambridge")

    sent = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert sent == ["*16*3*102##", "*16*3*122##"]
    assert player.source == "Cambridge"


@pytest.mark.asyncio
async def test_select_source_legacy_labels_without_configuration(hass, player, mock_gateway):
    """Without configured names the legacy ``Source N`` labels still work."""
    player._where = "11"
    assert player.source_list == ["Source 1", "Source 2", "Source 3", "Source 4"]

    await player.async_select_source("Source 2")

    sent = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert sent == ["*16*3*102##", "*16*3*112##"]


@pytest.mark.asyncio
async def test_select_source_rejects_unknown_source(hass, player, mock_gateway):
    """An unknown label is refused rather than silently sending a bogus frame."""
    _name_sources(player, s2="Cambridge")

    assert player.source_list == ["Cambridge"]
    with pytest.raises(HomeAssistantError):
        await player.async_select_source("Radio")
    mock_gateway.send.assert_not_called()


def test_unconfigured_source_is_visible_and_logged(hass, player, mock_gateway, caplog):
    """A zone routed to an empty matrix input says so, and warns once.

    The integration never corrects the routing: the user chose it at the wall
    panel, and silently overriding that would be its own surprise.
    """
    player.async_schedule_update_ha_state = MagicMock()
    player._where = "23"
    _name_sources(player, s2="Cambridge")

    # Wall panel routes environment 2 to source 1, which has nothing wired to it
    player.handle_event(MagicMock(spec=OWNSoundEvent, is_source_event=False, where="121", is_on=False, is_off=False, volume=None))

    assert player.source == "Source 1 (not configured)"
    assert "not configured" in caplog.text
    mock_gateway.send.assert_not_called()

    # The warning is logged once per source, not on every re-broadcast
    caplog.clear()
    player.handle_event(MagicMock(spec=OWNSoundEvent, is_source_event=False, where="121", is_on=False, is_off=False, volume=None))
    assert "not configured" not in caplog.text


def test_routing_event_targets_the_environment(hass, player, mock_gateway):
    """Routing is announced per environment: zone 23 follows 12S, not 13S."""
    player.async_schedule_update_ha_state = MagicMock()
    player._where = "23"
    _name_sources(player, s2="Cambridge")

    player.handle_event(MagicMock(spec=OWNSoundEvent, is_source_event=False, where="132", is_on=False, is_off=False, volume=None))
    assert player.source is None

    player.handle_event(MagicMock(spec=OWNSoundEvent, is_source_event=False, where="122", is_on=False, is_off=False, volume=None))
    assert player.source == "Cambridge"


def test_parsed_routing_frame_routes_whatever_owned_reports_as_zone(hass, player, mock_gateway):
    """Routing is read from ``where``, not from OWNd's ``zone``.

    Real parsed frames, so this holds whatever the installed OWNd reports as
    ``zone`` for a ``1ES`` frame (OWNd#51 briefly made it ``None``).
    """
    player.async_schedule_update_ha_state = MagicMock()
    player._where = "23"
    player._attr_state = MediaPlayerState.OFF
    _name_sources(player, s2="Cambridge")

    routing = OWNEvent.parse("*16*3*122##")
    assert isinstance(routing, OWNSoundEvent)
    assert _zone_address(routing).where == "122"

    player.handle_event(routing)

    assert player.source == "Cambridge"
    # A routing frame says nothing about this zone's power: an ON routing
    # frame must not resurrect a zone that was switched off.
    assert player.state == MediaPlayerState.OFF

    # Source 0 is no matrix input: still routing, never an amplifier `120`.
    player.handle_event(OWNEvent.parse("*16*3*120##"))
    assert player.source == "Cambridge"
    assert player.state == MediaPlayerState.OFF


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
    _set_pool(player, mock_pool)

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
    player._where = "11"

    # Async update
    await player.async_update()
    mock_gateway.send_status_request.assert_called_once()

    # Matrix routing event (112 -> route the amplifiers of environment 1 to source 2)
    msg_routing = MagicMock(spec=OWNSoundEvent, is_source_event=False, where="112", is_on=False, is_off=False, volume=None)
    player.handle_event(msg_routing)
    assert player.source == "Source 2"

    # Turn on event
    msg_on = MagicMock(spec=OWNSoundEvent, is_source_event=False, where="1", is_on=True, is_off=False, volume=None)
    player.handle_event(msg_on)
    assert player.state == MediaPlayerState.ON

    # Turn off event with active decoder
    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.release = AsyncMock()
    _set_pool(player, mock_pool)
    player._active_decoder = "media_player.squeezelite_1"

    msg_off = MagicMock(spec=OWNSoundEvent, is_source_event=False, where="1", is_on=False, is_off=True, volume=None)
    player.handle_event(msg_off)
    assert player.state == MediaPlayerState.OFF
    assert player._active_decoder is None

    # Volume update with mute / unmute detection
    msg_vol_0 = MagicMock(spec=OWNSoundEvent, is_source_event=False, where="1", is_on=False, is_off=False, volume=0)
    player.handle_event(msg_vol_0)
    assert player._attr_volume_level == 0.0
    assert player.is_volume_muted is True

    msg_vol_15 = MagicMock(spec=OWNSoundEvent, is_source_event=False, where="1", is_on=False, is_off=False, volume=15)
    player.handle_event(msg_vol_15)
    assert pytest.approx(player._attr_volume_level, 0.01) == 15 / 31.0
    assert player.is_volume_muted is False

    # Catch RuntimeError in async_schedule_update_ha_state
    player.async_schedule_update_ha_state.side_effect = RuntimeError("HA state error")
    player.handle_event(msg_vol_15)


@pytest.mark.asyncio
async def test_play_media_decoder_fails_to_wake_warning(hass, player, mock_gateway):
    """Test play_media when decoder does not wake up within timeout raises HomeAssistantError."""
    player.async_write_ha_state = MagicMock()
    player.async_schedule_update_ha_state = MagicMock()

    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.is_configured = True
    mock_pool.claim = AsyncMock(return_value=("media_player.squeezelite_1", 1))
    mock_pool.release = AsyncMock()
    _set_pool(player, mock_pool)

    # Decoder stays off
    hass.states.async_set("media_player.squeezelite_1", MediaPlayerState.OFF)

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock), \
         patch("asyncio.sleep", return_value=None), \
         pytest.raises(HomeAssistantError) as exc_info:
        await player.async_play_media("music", "http://stream.url")

    assert exc_info.value.translation_key == "decoder_wake_timeout"
    assert player._active_decoder is None
    mock_pool.release.assert_awaited_once_with(player.entity_id)


@pytest.mark.asyncio
async def test_turn_off_decoder_stop_error_handled(hass, player, mock_gateway):
    """Test turn_off handles exception when stopping decoder playback."""
    player.async_schedule_update_ha_state = MagicMock()

    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.release = AsyncMock()
    _set_pool(player, mock_pool)

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
    _set_pool(player, mock_pool)

    player._active_decoder = "media_player.squeezelite_1"
    hass.states.async_set("media_player.squeezelite_1", MediaPlayerState.PLAYING, {"is_volume_muted": False})

    # async_call succeeds for volume_set, but raises for volume_mute
    async def mock_call(domain, service, data):
        if service == "volume_mute":
            raise RuntimeError("Mute not supported")

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock, side_effect=mock_call):
        await player.async_mute_volume(True)

    assert player._attr_is_volume_muted is True



def _set_default_source(player, environment, source):
    """Configure the per-environment default matrix source."""
    options = dict(player.platform.config_entry.options or {})
    options[CONF_SOURCE_DEFAULTS] = {environment: source}
    player.platform.config_entry.options = options


@pytest.mark.asyncio
async def test_turn_on_applies_the_environment_default_source(hass, player, mock_gateway):
    """Turning a zone on from HA routes it to the configured default source."""
    player._where = "23"
    _name_sources(player, s2="Cambridge")
    _set_default_source(player, "2", 2)

    await player.async_turn_on()

    sent = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert sent[-2:] == ["*16*3*102##", "*16*3*122##"]
    assert player.source == "Cambridge"


@pytest.mark.asyncio
async def test_turn_on_leaves_routing_alone_without_a_default(hass, player, mock_gateway):
    """Without a configured default the existing routing is untouched."""
    player._where = "23"
    _name_sources(player, s2="Cambridge")

    await player.async_turn_on()

    sent = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert all("*16*3*1" not in frame or frame.endswith("*23##") for frame in sent)
    assert player.source is None


def test_wall_panel_routing_is_not_corrected(hass, player, mock_gateway):
    """A default source never overrides a choice made at a wall panel.

    The user pressed a button in the room; silently routing the zone back
    would be the surprise this design set out to avoid.
    """
    player.async_schedule_update_ha_state = MagicMock()
    player._where = "23"
    _name_sources(player, s2="Cambridge")
    _set_default_source(player, "2", 2)

    player.handle_event(
        MagicMock(spec=OWNSoundEvent, is_source_event=False, where="121",
                  is_on=False, is_off=False, volume=None)
    )

    assert player.source == "Source 1 (not configured)"
    mock_gateway.send.assert_not_called()


@pytest.mark.asyncio
async def test_play_media_routes_to_the_claimed_decoder(hass, player, mock_gateway):
    """Streaming routes the zone to the input its decoder is wired to."""
    player._where = "23"
    _name_sources(player, s1="Streamer")
    pool = MagicMock()
    pool.is_configured = True
    pool.claim = AsyncMock(return_value=("media_player.squeezelite_1", 1))
    pool.get_pre_gain = MagicMock(return_value=0)
    _set_pool(player, pool)
    hass.states.async_set("media_player.squeezelite_1", MediaPlayerState.IDLE)

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
        await player.async_play_media("music", "http://stream")

    sent = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert sent[-2:] == ["*16*3*101##", "*16*3*121##"]


@pytest.mark.asyncio
async def test_pool_claim_prefers_the_default_source(hass, player, mock_gateway):
    """The zone asks the pool for a decoder on its default input."""
    player._where = "23"
    _set_default_source(player, "2", 2)
    pool = MagicMock()
    pool.is_configured = True
    pool.claim = AsyncMock(return_value=("media_player.cambridge_2", 2))
    _set_pool(player, pool)
    hass.states.async_set("media_player.cambridge_2", MediaPlayerState.IDLE)

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
        await player.async_play_media("music", "http://stream")

    assert pool.claim.call_args.kwargs["preferred_source"] == 2


def _streaming_pool(decoder="media_player.squeezelite_1", source=1):
    """A mock pool that hands out one decoder."""
    pool = MagicMock()
    pool.is_configured = True
    pool.claim = AsyncMock(return_value=(decoder, source))
    pool.get_pre_gain = MagicMock(return_value=0)
    pool.environment_owner = MagicMock(return_value=None)
    return pool


def _sent(mock_gateway):
    return [str(call.args[0]) for call in mock_gateway.send.call_args_list]


@pytest.mark.asyncio
async def test_play_media_without_configuration_trusts_the_wall_panels(hass, player, mock_gateway):
    """An installation that never described its matrix is not routed on upgrade.

    The decoder slot numbers of such an entry were never used before, so
    nobody checked them; routing on them would switch rooms to wrong inputs.
    """
    player._where = "23"
    pool = _streaming_pool()
    _set_pool(player, pool)
    hass.states.async_set("media_player.squeezelite_1", MediaPlayerState.IDLE)

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock), \
         patch("asyncio.sleep", return_value=None):
        await player.async_play_media("music", "http://stream")

    assert not any(frame.startswith("*16*3*1") for frame in _sent(mock_gateway))
    # Without configuration the environment is not claimed either
    assert pool.claim.call_args.kwargs["environment"] is None


@pytest.mark.asyncio
async def test_play_media_never_routes_to_an_invalid_decoder_source(hass, player, mock_gateway, caplog):
    """A decoder slot saved as 0 by the old options form sends no frame."""
    player._where = "23"
    _name_sources(player, s1="Streamer")
    _set_pool(player, _streaming_pool(source=0))
    hass.states.async_set("media_player.squeezelite_1", MediaPlayerState.IDLE)

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock), \
         patch("asyncio.sleep", return_value=None):
        await player.async_play_media("music", "http://stream")

    assert "*16*3*100##" not in _sent(mock_gateway)
    assert "*16*3*120##" not in _sent(mock_gateway)
    assert "cannot route" in caplog.text


@pytest.mark.asyncio
async def test_play_media_is_refused_while_the_environment_streams(hass, player, mock_gateway):
    """Zones of one environment share a matrix output, so one stream at a time.

    Handing zone 23 a second decoder would re-route zone 22 onto the new stream
    while Home Assistant still showed zone 22 playing its own.
    """
    player._where = "23"
    _name_sources(player, s1="Streamer", s2="Cambridge")
    pool = DecoderPool(hass, {"media_player.dec_a": 1, "media_player.dec_b": 2})
    hass.states.async_set("media_player.dec_a", MediaPlayerState.IDLE)
    hass.states.async_set("media_player.dec_b", MediaPlayerState.IDLE)
    await pool.claim("media_player.audio_zone_22", environment="2")
    _set_pool(player, pool)

    with pytest.raises(HomeAssistantError) as err:
        await player.async_play_media("music", "http://stream")

    assert err.value.translation_key == "environment_busy"
    assert err.value.translation_placeholders["owner"] == "media_player.audio_zone_22"
    mock_gateway.send.assert_not_called()
    assert pool.get_assignment(player.entity_id) is None


@pytest.mark.asyncio
async def test_turn_on_does_not_reroute_a_zone_that_is_already_on(hass, player, mock_gateway):
    """Turning an ON zone on again sends nothing: the route may carry a stream."""
    player._where = "23"
    _name_sources(player, s2="Cambridge")
    _set_default_source(player, "2", 2)
    player._attr_state = MediaPlayerState.ON

    await player.async_turn_on()

    mock_gateway.send.assert_not_called()


@pytest.mark.asyncio
async def test_turn_on_keeps_the_route_of_a_streaming_environment(hass, player, mock_gateway):
    """A default source is not applied over another zone's stream."""
    player._where = "23"
    _name_sources(player, s2="Cambridge")
    _set_default_source(player, "2", 2)
    pool = _streaming_pool()
    pool.environment_owner = MagicMock(return_value="media_player.audio_zone_22")
    _set_pool(player, pool)

    with patch("asyncio.sleep", return_value=None):
        await player.async_turn_on()

    assert not any(frame.startswith("*16*3*1") for frame in _sent(mock_gateway))
    pool.environment_owner.assert_called_once_with("2", exclude=player.entity_id)


@pytest.mark.asyncio
async def test_select_source_refuses_environment_zero(hass, player, mock_gateway):
    """Amplifiers 01-09 would be routed with 10S, the source device address."""
    player._where = "05"

    with pytest.raises(HomeAssistantError) as err:
        await player.async_select_source("Source 2")

    assert err.value.translation_key == "routing_unsupported"
    mock_gateway.send.assert_not_called()


@pytest.mark.asyncio
async def test_select_source_refuses_unnamed_inputs_once_sources_are_named(hass, player, mock_gateway):
    """Neither the legacy label nor the "not configured" label selects a blank input."""
    player._where = "23"
    _name_sources(player, s2="Cambridge")

    for label in ("Source 3", "Source 3 (not configured)"):
        with pytest.raises(HomeAssistantError):
            await player.async_select_source(label)
    mock_gateway.send.assert_not_called()


@pytest.mark.asyncio
async def test_select_source_legacy_label_outside_the_matrix_is_refused(hass, player, mock_gateway):
    """``Source 0`` or ``Source 9`` is not a matrix input, configured or not."""
    for label in ("Source 0", "Source 9"):
        with pytest.raises(HomeAssistantError):
            await player.async_select_source(label)
    mock_gateway.send.assert_not_called()


@pytest.mark.asyncio
async def test_select_source_is_refused_while_the_environment_streams(hass, player, mock_gateway):
    """A source change on zone 23 would take zone 22 off its stream."""
    player._where = "23"
    pool = _streaming_pool()
    pool.environment_owner = MagicMock(return_value="media_player.audio_zone_22")
    _set_pool(player, pool)

    with pytest.raises(HomeAssistantError) as err:
        await player.async_select_source("Source 2")

    assert err.value.translation_key == "environment_busy"
    assert err.value.translation_placeholders == {
        "entity_id": player.entity_id,
        "owner": "media_player.audio_zone_22",
        "environment": "2",
    }
    mock_gateway.send.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_play_media_in_one_environment(hass, player, mock_gateway):
    """Two zones of one environment starting together: exactly one wins.

    The environment check runs under the pool lock, so the second claim sees
    the first one even when both requests are in flight at the same time.
    """
    pool = DecoderPool(hass, {"media_player.dec_a": 1, "media_player.dec_b": 2})
    hass.states.async_set("media_player.dec_a", MediaPlayerState.IDLE)
    hass.states.async_set("media_player.dec_b", MediaPlayerState.IDLE)

    zone_22 = player
    zone_22._where = "22"
    _name_sources(zone_22, s1="Streamer", s2="Cambridge")
    _set_pool(zone_22, pool)

    zone_23 = MyHOMEMediaPlayer(
        hass=hass, name="Audio Zone 23", entity_name=None, device_id="23#16",
        who="16", where="23", manufacturer="BTicino", model="Audio System",
        gateway=mock_gateway,
    )
    zone_23.hass = hass
    zone_23.entity_id = "media_player.audio_zone_23"
    attach_platform(zone_23, zone_22.platform.config_entry)

    for zone in (zone_22, zone_23):
        zone.async_write_ha_state = MagicMock()
        zone.async_schedule_update_ha_state = MagicMock()

    import asyncio

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock), \
         patch("asyncio.sleep", return_value=None):
        results = await asyncio.gather(
            zone_22.async_play_media("music", "http://a"),
            zone_23.async_play_media("music", "http://b"),
            return_exceptions=True,
        )

    errors = [r for r in results if isinstance(r, HomeAssistantError)]
    assert len(errors) == 1
    assert errors[0].translation_key == "environment_busy"
    assert sum(r is None for r in results) == 1
    owners = {pool.get_assignment(z.entity_id) for z in (zone_22, zone_23)}
    assert len(owners - {None}) == 1


def test_routing_to_a_source_outside_the_matrix_is_not_labelled(hass, player, mock_gateway):
    """``159`` is a routing frame, but S9 is not an F441M input."""
    player.async_schedule_update_ha_state = MagicMock()
    player._where = "53"

    player.handle_event(MagicMock(spec=OWNSoundEvent, is_source_event=False, where="159",
                                  is_on=False, is_off=False, volume=None))
    assert player.source is None

    player.handle_event(MagicMock(spec=OWNSoundEvent, is_source_event=False, where="152",
                                  is_on=False, is_off=False, volume=None))
    assert player.source == "Source 2"


def test_environment_zero_has_no_routing_address():
    """``10S`` is a source device; environment 0 has no ``1ES`` form."""
    from custom_components.myhome.media_player import _routing_address

    assert _routing_address("05", 2) is None
    assert _routing_address("15", 2) == "112"


# ── Golden corpus: our addressing against frames captured on real hardware ────

def _golden_sound_fixtures():
    """Load the WHO=16 fixtures captured on real F441M installations."""
    import json
    from pathlib import Path

    corpus = Path(__file__).resolve().parent / "golden" / "corpus.json"
    return [f for f in json.loads(corpus.read_text(encoding="utf-8"))
            if f.get("who") == 16]


@pytest.mark.parametrize(
    ("environment", "source", "frame"),
    [
        ("1", 1, "*16*3*111##"),
        ("1", 2, "*16*3*112##"),
        ("2", 1, "*16*3*121##"),
        ("2", 2, "*16*3*122##"),
        ("3", 1, "*16*3*131##"),
        ("8", 1, "*16*3*181##"),
    ],
)
def test_routing_address_matches_captured_frames(environment, source, frame):
    """Our routing address reproduces frames captured on two installations.

    Plant B pins the digit order on its own: amplifier 11 is routed to source 2
    with 112 and to source 1 with 111, and a general power-on sweeps 111..181.
    """
    from custom_components.myhome.media_player import _parse_routing_address, _routing_address

    # A two-digit amplifier address in that environment, e.g. environment 2 -> "23"
    zone = f"{environment}3"
    assert _routing_address(zone, source) == frame.removeprefix("*16*3*").removesuffix("##")
    assert _parse_routing_address(frame.removeprefix("*16*3*").removesuffix("##")) == (source, environment)


def test_source_addresses_are_not_routing_addresses():
    """101-109 are source devices; decoding them as routing invents a source 0."""
    from custom_components.myhome.media_player import _parse_routing_address

    for fixture in _golden_sound_fixtures():
        where = str(fixture.get("where"))
        if where.startswith("10") and len(where) == 3:
            assert _parse_routing_address(where) is None, where


def test_captured_amplifier_addresses_resolve_to_their_environment():
    """Amplifier addresses are EA: the environment is the first digit."""
    from custom_components.myhome.media_player import _zone_environment

    assert _zone_environment("23") == "2"   # plant A, eetkamer
    assert _zone_environment("11") == "1"   # plant B
    assert _zone_environment("36") == "3"   # plant A, badkamer



@pytest.mark.parametrize(
    ("where", "environment", "route_s2"),
    [
        ("11", "1", "112"),     # amplifier 1 of environment 1
        ("23", "2", "122"),
        ("01", "0", None),      # environment 0: 10S is the source device
        ("09", "0", None),
        ("1", None, None),      # not in the WHERE table: 01 or 11?
        ("7", None, None),
        ("0", None, None),      # general amplifier address
        ("#1", None, None),     # environment command, not an amplifier
        ("123", None, None),
    ],
)
def test_only_two_digit_amplifiers_are_routed(where, environment, route_s2):
    """The WHO=16 WHERE table lists amplifiers as 01-99, and OWNd keeps the
    padding.  A single digit would have to be guessed into an environment, and
    a wrong guess switches somebody else's room, so it is never routed.
    """
    from custom_components.myhome.media_player import _routing_address, _zone_environment

    assert _zone_environment(where) == environment
    assert _routing_address(where, 2) == route_s2


@pytest.mark.asyncio
async def test_select_source_refuses_a_single_digit_address(hass, player, mock_gateway):
    """A hand-written ``1`` is refused with the address in the message."""
    player._where = "1"

    with pytest.raises(HomeAssistantError) as err:
        await player.async_select_source("Source 2")

    assert err.value.translation_key == "routing_unsupported"
    assert err.value.translation_placeholders["where"] == "1"
    mock_gateway.send.assert_not_called()


# ── WHO=22 mirrors: the other dialect spells the addressing out ──────────────

@pytest.mark.parametrize(
    ("who16", "environment", "source", "who22"),
    [
        ("*16*3*111##", "1", 1, "*22*2#4#1*5#2#1##"),
        ("*16*3*112##", "1", 2, "*22*2#4#1*5#2#2##"),
        ("*16*3*121##", "2", 1, "*22*2#4#2*5#2#1##"),
        ("*16*3*181##", "8", 1, "*22*2#4#8*5#2#1##"),
    ],
)
def test_routing_agrees_with_the_who22_mirror(who16, environment, source, who22):
    """Our decoding of a routing frame matches its WHO=22 twin.

    An MH200N announces every sound event in both dialects. WHO=22 writes the
    environment and the source into separate, separator-delimited fields, so
    the pair is independent evidence for how the WHO=16 pseudo address packs
    them - this is not our inference, it is the protocol restating itself.
    WHAT is ``2#MULTIMEDIA_TYPE#AREA`` and WHERE ``5#2#SOURCE_ID``.
    """
    from custom_components.myhome.media_player import _parse_routing_address

    pseudo = who16.removeprefix("*16*3*").removesuffix("##")
    assert _parse_routing_address(pseudo) == (source, environment)

    what_param = who22.split("*")[2].split("#")      # ["2", "4", AREA]
    where_param = who22.split("*")[3].split("#")     # ["5", "2", SOURCE]
    assert what_param[2] == environment
    assert int(where_param[2]) == source


@pytest.mark.parametrize(
    ("amplifier", "area", "point"),
    [("11", "1", "1"), ("12", "1", "2"), ("31", "3", "1")],
)
def test_amplifier_address_agrees_with_the_who22_speaker_form(amplifier, area, point):
    """Amplifier ``EA`` is area then point, as WHO=22 writes it as ``3#AREA#POINT``."""
    from custom_components.myhome.media_player import _zone_environment

    assert _zone_environment(amplifier) == area
    assert amplifier == f"{area}{point}"


def test_default_source_ignores_malformed_options(hass, player):
    """A malformed default-source option is ignored rather than acted on."""
    player._where = "23"

    def _set(value):
        options = dict(player.platform.config_entry.options or {})
        options[CONF_SOURCE_DEFAULTS] = value
        player.platform.config_entry.options = options

    _set("not-a-mapping")
    assert player._default_source() is None

    _set({"2": "radio"})
    assert player._default_source() is None

    _set({"2": 0})          # 0 is not a source; 101-109 start at 1
    assert player._default_source() is None

    _set({"2": 99})
    assert player._default_source() is None

    _set({"3": 2})          # another environment's default does not apply here
    assert player._default_source() is None

    _set({"2": 2})
    assert player._default_source() == 2


def _create_test_zone(hass, mock_gateway, runtime, where, entity_id):
    if runtime.decoder_pool is None:
        runtime.decoder_pool = DecoderPool(hass, {})
    p = MyHOMEMediaPlayer(
        hass=hass,
        name=f"Audio Zone {where}",
        entity_name=None,
        device_id=f"{where}#16",
        who="16",
        where=where,
        manufacturer="BTicino",
        model="Audio System",
        gateway=mock_gateway,
    )
    p.hass = hass
    p.entity_id = entity_id
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {CONF_MAC: mock_gateway.mac}
    entry.options = {
        CONF_SOURCE_NAME.format(1): "Radio",
        CONF_SOURCE_NAME.format(2): "Cambridge",
    }
    entry.runtime_data = runtime
    attach_platform(p, entry)
    runtime.media_players[entity_id] = p
    return p


@pytest.mark.asyncio
async def test_grouping_feature_advertised(hass, mock_gateway):
    """GROUPING is advertised for zones with routing address, omitted for env 0."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    z22 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.audio_zone_22")
    z01 = _create_test_zone(hass, mock_gateway, runtime, "01", "media_player.audio_zone_01")
    assert z22.supported_features & MediaPlayerEntityFeature.GROUPING
    assert not (z01.supported_features & MediaPlayerEntityFeature.GROUPING)
    _set_pool(z22, DecoderPool(hass, {"media_player.dec": 1}))
    assert z22.supported_features & MediaPlayerEntityFeature.GROUPING


@pytest.mark.asyncio
async def test_group_members_property(hass, mock_gateway):
    """group_members returns None when standalone, and [leader, *members] when grouped."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    z22 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.audio_zone_22")
    z23 = _create_test_zone(hass, mock_gateway, runtime, "23", "media_player.audio_zone_23")

    assert z22.group_members is None
    assert z23.group_members is None

    await z22.async_join_players(["media_player.audio_zone_23"])
    assert z22.group_members == ["media_player.audio_zone_22", "media_player.audio_zone_23"]
    assert z23.group_members == ["media_player.audio_zone_22", "media_player.audio_zone_23"]


@pytest.mark.asyncio
async def test_join_players_single_environment(hass, mock_gateway):
    """Joining zones in the same environment routes matrix and turns on member amplifier."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    z22 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.audio_zone_22")
    z23 = _create_test_zone(hass, mock_gateway, runtime, "23", "media_player.audio_zone_23")
    z22._attr_source = "Cambridge"  # Source 2

    mock_gateway.send.reset_mock()
    await z22.async_join_players(["media_player.audio_zone_23"])

    # Frames sent: *16*3*102## (activate source 2), *16*3*122## (route env 2 to src 2), *16*3*23## (turn on amp 23)
    sent_frames = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert "*16*3*102##" in sent_frames
    assert "*16*3*122##" in sent_frames
    assert "*16*3*23##" in sent_frames
    assert z23.state == MediaPlayerState.ON
    assert z23.source == "Cambridge"


@pytest.mark.asyncio
async def test_join_players_cross_environment(hass, mock_gateway):
    """Joining zones across environments routes member environment and powers on."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec", "idle")

    z22 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.audio_zone_22")
    z35 = _create_test_zone(hass, mock_gateway, runtime, "35", "media_player.audio_zone_35")

    # z22 plays media (claiming decoder on source 2)
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
        await z22.async_play_media("music", "http://stream")

    assert z22._active_decoder == "media_player.dec"

    mock_gateway.send.reset_mock()
    await z22.async_join_players(["media_player.audio_zone_35"])

    # Environment 3 should be routed to source 2: *16*3*132##, and amp 35 turned on: *16*3*35##
    sent_frames = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert "*16*3*102##" in sent_frames
    assert "*16*3*132##" in sent_frames
    assert "*16*3*35##" in sent_frames
    assert z35.state == MediaPlayerState.ON
    assert z35.source == "Cambridge"
    assert pool.get_assignment("media_player.audio_zone_35") == "media_player.dec"


@pytest.mark.asyncio
async def test_join_players_environment_conflict(hass, mock_gateway):
    """Joining a zone whose environment is already streaming another decoder raises an error."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1, "media_player.dec2": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec1", "idle")
    hass.states.async_set("media_player.dec2", "idle")

    z14 = _create_test_zone(hass, mock_gateway, runtime, "14", "media_player.audio_zone_14")
    z22 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.audio_zone_22")
    _create_test_zone(hass, mock_gateway, runtime, "17", "media_player.audio_zone_17")

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
        await z14.async_play_media("music", "http://stream1")
        await z22.async_play_media("music", "http://stream2")

    # z14 holds dec1 in Env 1, z22 holds dec2 in Env 2.
    # Joining z17 (in Env 1) to z22 conflicts with z14's stream.
    with pytest.raises(HomeAssistantError) as exc_info:
        await z22.async_join_players(["media_player.audio_zone_17"])
    assert exc_info.value.translation_key == "environment_busy"


@pytest.mark.asyncio
async def test_unjoin_player_member(hass, mock_gateway):
    """Member unjoining turns off its own amplifier and removes from group."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec", "idle")

    z22 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.audio_zone_22")
    z23 = _create_test_zone(hass, mock_gateway, runtime, "23", "media_player.audio_zone_23")

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
        await z22.async_play_media("music", "http://stream")

    await z22.async_join_players(["media_player.audio_zone_23"])
    assert pool.get_assignment("media_player.audio_zone_23") == "media_player.dec"

    mock_gateway.send.reset_mock()
    await z23.async_unjoin_player()

    # Member turns off its amplifier
    sent_frames = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert "*16*0*23##" in sent_frames or "*16*13*23##" in sent_frames
    assert z23.state == MediaPlayerState.OFF
    assert z23.group_members is None
    assert z22.group_members is None
    assert pool.get_assignment("media_player.audio_zone_23") is None
    # Leader is still streaming
    assert pool.get_assignment("media_player.audio_zone_22") == "media_player.dec"


@pytest.mark.asyncio
async def test_unjoin_player_leader_disbands(hass, mock_gateway):
    """Leader unjoining disbands the group and turns off all members."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec", "idle")

    z22 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.audio_zone_22")
    z23 = _create_test_zone(hass, mock_gateway, runtime, "23", "media_player.audio_zone_23")
    z35 = _create_test_zone(hass, mock_gateway, runtime, "35", "media_player.audio_zone_35")

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
        await z22.async_play_media("music", "http://stream")

    await z22.async_join_players(["media_player.audio_zone_23", "media_player.audio_zone_35"])

    mock_gateway.send.reset_mock()
    await z22.async_unjoin_player()

    sent_frames = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert any("23##" in f for f in sent_frames)
    assert any("35##" in f for f in sent_frames)
    assert z23.state == MediaPlayerState.OFF
    assert z35.state == MediaPlayerState.OFF
    assert z22.group_members is None
    assert z23.group_members is None
    assert z35.group_members is None


@pytest.mark.asyncio
async def test_turn_off_leader_disbands_group(hass, mock_gateway):
    """Calling async_turn_off on the leader disbands group members."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    z22 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.audio_zone_22")
    z23 = _create_test_zone(hass, mock_gateway, runtime, "23", "media_player.audio_zone_23")

    await z22.async_join_players(["media_player.audio_zone_23"])
    assert z22.group_members == ["media_player.audio_zone_22", "media_player.audio_zone_23"]

    await z22.async_turn_off()
    assert z22.group_members is None
    assert z23.group_members is None
    assert z23.state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_bus_off_cleans_up_group(hass, mock_gateway):
    """Bus OFF frame received for a zone cleans up group tracking."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    z22 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.audio_zone_22")
    z23 = _create_test_zone(hass, mock_gateway, runtime, "23", "media_player.audio_zone_23")

    await z22.async_join_players(["media_player.audio_zone_23"])
    assert z23.group_members is not None

    # Off frame for 23 from wall switch
    event = MagicMock(spec=OWNSoundEvent)
    event.where = "23"
    event.is_source_event = False
    event.is_on = False
    event.is_off = True
    event.volume = None
    z23.handle_event(event)
    await asyncio.sleep(0)

    assert z23.group_members is None


@pytest.mark.asyncio
async def test_cambridge_audio_incompatible_warning_and_error(hass, mock_gateway):
    """Configuring a cambridge_audio entity creates a repair issue, and play_media raises error."""
    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "media_player", "cambridge_audio", "unique_cxn", suggested_object_id="cambridge_cxn"
    )

    entry = MagicMock()
    entry.entry_id = "test_gw"
    entry.options = {
        CONF_DECODER_ENTITY.format(1): "media_player.cambridge_cxn",
        CONF_DECODER_SOURCE.format(1): 2,
    }

    with patch("custom_components.myhome.media_player.async_create_incompatible_decoder_issue") as mock_issue:
        pool = _build_pool(hass, entry)
        mock_issue.assert_called_once_with(
            hass, "test_gw", "media_player.cambridge_cxn", "cambridge_audio"
        )

    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.cambridge_cxn", "idle")
    z22 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.audio_zone_22")

    with pytest.raises(HomeAssistantError) as exc_info:
        await z22.async_play_media("music", "http://stream")
    assert exc_info.value.translation_key == "decoder_incompatible_platform"
    # Verify decoder was released and is not stuck as busy
    assert pool.get_assignment("media_player.audio_zone_22") is None


@pytest.mark.asyncio
async def test_passive_metadata_mirroring_and_transport(hass, mock_gateway):
    """A zone turned on and routed to a decoder source passively mirrors track info and transport."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set(
        "media_player.streamer",
        "playing",
        {
            "media_title": "Comfortably Numb",
            "media_artist": "Pink Floyd",
            "media_album_name": "The Wall",
            "entity_picture": "http://art.jpg",
        },
    )

    z22 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.audio_zone_22")
    z22._attr_state = MediaPlayerState.ON
    z22._attr_source = "Cambridge"  # Source 2

    assert z22._effective_decoder == "media_player.streamer"
    assert z22.state == MediaPlayerState.PLAYING
    assert z22.media_title == "Comfortably Numb"
    assert z22.media_artist == "Pink Floyd"
    assert z22.media_album_name == "The Wall"
    assert z22.entity_picture == "http://art.jpg"

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as mock_service:
        await z22.async_media_pause()
        mock_service.assert_called_once_with(
            "media_player", "media_pause", {"entity_id": "media_player.streamer"}
        )

    # Group member also mirrors leader's decoder
    z23 = _create_test_zone(hass, mock_gateway, runtime, "23", "media_player.audio_zone_23")
    await z22.async_join_players(["media_player.audio_zone_23"])

    assert z23._effective_decoder == "media_player.streamer"
    assert z23.media_title == "Comfortably Numb"
    assert z23.state == MediaPlayerState.PLAYING


def test_get_group_members_none_runtime():
    """_get_group_members returns None when runtime is None."""
    from custom_components.myhome.media_player import _get_group_members
    assert _get_group_members(None, "media_player.any") is None


@pytest.mark.asyncio
async def test_async_will_remove_from_hass_cleans_groups(hass, mock_gateway):
    """Removing entity from hass cleans up groups in DecoderPool for both leaders and members."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {})
    runtime.decoder_pool = pool
    z1 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone1")
    z2 = _create_test_zone(hass, mock_gateway, runtime, "23", "media_player.zone2")
    z3 = _create_test_zone(hass, mock_gateway, runtime, "31", "media_player.zone3")

    await pool.add_group_member("media_player.zone1", "media_player.zone2")
    await pool.add_group_member("media_player.zone1", "media_player.zone3")

    # Member z3 removed (leaving z2)
    await z3.async_will_remove_from_hass()
    assert pool.get_members("media_player.zone1") == ["media_player.zone2"]

    # Member z2 removed (last member, so group deleted)
    await z2.async_will_remove_from_hass()
    assert pool.get_group_members("media_player.zone1") is None

    # Leader z1 removed when it was in group
    await pool.add_group_member("media_player.zone1", "media_player.zone2")
    await z1.async_will_remove_from_hass()
    assert pool.get_group_members("media_player.zone1") is None


@pytest.mark.asyncio
async def test_async_play_media_leader_with_existing_group_members(hass, mock_gateway):
    """async_play_media routes and powers on existing group members when leader starts new playback."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", "idle")

    z1 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone1")
    z2 = _create_test_zone(hass, mock_gateway, runtime, "23", "media_player.zone2")
    z2._attr_state = MediaPlayerState.OFF

    await pool.add_group_member("media_player.zone1", "media_player.zone2", environment="2")

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
        await z1.async_play_media("music", "http://stream")

    assert z2._attr_state == MediaPlayerState.ON
    assert z2.state == MediaPlayerState.ON

    # Test member environment collision during play_media: member is dropped from group and its HA state updated
    _create_test_zone(hass, mock_gateway, runtime, "33", "media_player.zone3")
    await pool.add_group_member("media_player.zone1", "media_player.zone3", environment="3")
    with patch.object(pool, "environment_owner", return_value="media_player.other"), \
         patch.object(pool, "get_assignment", return_value="media_player.other_decoder"):
        with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
            await z1.async_play_media("music", "http://stream")
    assert "media_player.zone3" not in pool.get_members("media_player.zone1")


@pytest.mark.asyncio
async def test_async_join_and_unjoin_edge_cases(hass, mock_gateway):
    """Test defensive guards in async_join_players and async_unjoin_player."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {})
    runtime.decoder_pool = pool
    z = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone1")

    # runtime is None
    with patch.object(MyHOMEMediaPlayer, "_runtime_data", new_callable=PropertyMock, return_value=None):
        await z.async_join_players(["media_player.zone2"])
        await z.async_unjoin_player()

    # pool is None
    with patch.object(z, "_get_pool", return_value=None):
        with pytest.raises(HomeAssistantError) as err:
            await z.async_join_players(["media_player.zone2"])
        assert err.value.translation_key == "grouping_unavailable"
        await z.async_unjoin_player()

    # new_members empty (only contains self)
    await z.async_join_players(["media_player.zone1"])
    assert pool.get_members("media_player.zone1") == []


def test_source_event_ignored_on_zone(hass, mock_gateway):
    """A source switching event (*16*3*10S##) is ignored and returns immediately (line 1359)."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    z = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone1")
    z._attr_state = MediaPlayerState.OFF

    event = MagicMock(spec=OWNSoundEvent)
    event.where = "101"
    event.is_source_event = True
    z.handle_event(event)

    assert z._attr_state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_async_join_passive_environment_busy_conflict(hass, mock_gateway):
    """Passive join raises HomeAssistantError if member environment is locked by another zone."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 1})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", "idle")

    # Another zone owns environment 2 on pool
    await pool.claim("media_player.other_zone", environment="2")

    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone1")
    z1._attr_source = "Source 1"
    _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.zone2")

    with pytest.raises(HomeAssistantError) as exc_info:
        await z1.async_join_players(["media_player.zone2"])
    assert exc_info.value.translation_key == "environment_busy"


@pytest.mark.asyncio
async def test_async_join_member_stealing_from_other_group(hass, mock_gateway):
    """Joining a member already in a group cleans it from the old group."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {})
    runtime.decoder_pool = pool
    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone1")
    z1._attr_source = "Source 1"
    _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone2")
    z3 = _create_test_zone(hass, mock_gateway, runtime, "31", "media_player.zone3")
    z3._attr_source = "Source 2"

    await z1.async_join_players(["media_player.zone2"])
    assert pool.get_members("media_player.zone1") == ["media_player.zone2"]

    # z3 joins z2
    await z3.async_join_players(["media_player.zone2"])
    assert pool.get_members("media_player.zone1") == []
    assert pool.get_members("media_player.zone3") == ["media_player.zone2"]


@pytest.mark.asyncio
async def test_async_turn_off_member_and_leader_with_pool(hass, mock_gateway):
    """Turning off member removes it from group; turning off leader disbands members via pool."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 1})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", "idle")

    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone1")
    z2 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone2")

    # Set up group with pool
    await pool.claim("media_player.zone1")
    await z1.async_join_players(["media_player.zone2"])
    z1._attr_state = MediaPlayerState.ON
    z2._attr_state = MediaPlayerState.ON

    # Member turns off: removes self from group & disbands empty group
    await z2.async_turn_off()
    assert pool.get_members("media_player.zone1") == []
    assert z1.group_members is None

    # Re-group
    await z1.async_join_players(["media_player.zone2"])
    z1._active_decoder = "media_player.streamer"

    # Leader turns off: disbands and removes member via pool, stopping decoder
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as mock_service:
        await z1.async_turn_off()
        mock_service.assert_called_with(
            "media_player", "media_stop", {"entity_id": "media_player.streamer"}
        )
    assert pool.get_members("media_player.zone1") == []
    assert z2._attr_state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_bus_off_event_on_leader_and_member_with_pool(hass, mock_gateway):
    """Bus OFF frame cleanly disbands leader group, stops decoder via media_stop, and removes member from group."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 1})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", "idle")

    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone1")
    z2 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone2")
    z3 = _create_test_zone(hass, mock_gateway, runtime, "33", "media_player.zone3")

    await pool.claim("media_player.zone1")
    z1._active_decoder = "media_player.streamer"
    await z1.async_join_players(["media_player.zone2", "media_player.zone3"])

    # The wall switch is pressed well after the join woke the room, not
    # inside the window where an OFF is taken for the wake sequence's echo.
    z2._wake_off_sent_at = None

    # Member z2 receives bus OFF event
    ev2 = MagicMock(spec=OWNSoundEvent)
    ev2.where = "22"
    ev2.is_source_event = False
    ev2.is_on = False
    ev2.is_off = True
    ev2.volume = None
    z2.handle_event(ev2)
    await asyncio.sleep(0)
    assert pool.get_members("media_player.zone1") == ["media_player.zone3"]

    # Leader z1 receives bus OFF event
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as mock_service:
        ev1 = MagicMock(spec=OWNSoundEvent)
        ev1.where = "11"
        ev1.is_source_event = False
        ev1.is_on = False
        ev1.is_off = True
        ev1.volume = None
        z1.handle_event(ev1)
        await asyncio.sleep(0)
        # Verify media_stop was called on the decoder (High Issue 1 fix!)
        mock_service.assert_called_with(
            "media_player", "media_stop", {"entity_id": "media_player.streamer"}
        )
    assert pool.get_members("media_player.zone1") == []
    assert z3._attr_state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_env_0_omits_grouping_feature_and_rejects_join(hass, mock_gateway):
    """Environment 0 (unroutable) omits GROUPING and rejects async_join_players."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {})
    runtime.decoder_pool = pool
    z0 = _create_test_zone(hass, mock_gateway, runtime, "01", "media_player.zone01")
    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")

    # Environment 0 omits GROUPING feature flag
    assert not (z0.supported_features & MediaPlayerEntityFeature.GROUPING)
    assert bool(z1.supported_features & MediaPlayerEntityFeature.GROUPING)

    # Leader in env 0 cannot join others
    with pytest.raises(HomeAssistantError) as exc:
        await z0.async_join_players(["media_player.zone11"])
    assert exc.value.translation_key == "routing_unsupported"

    # Member in env 0 cannot be joined
    with pytest.raises(HomeAssistantError) as exc:
        await z1.async_join_players(["media_player.zone01"])
    assert exc.value.translation_key == "routing_unsupported"


@pytest.mark.asyncio
async def test_async_join_rejects_foreign_entity(hass, mock_gateway):
    """async_join_players rejects foreign entities not in runtime.media_players."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {})
    runtime.decoder_pool = pool
    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")

    with pytest.raises(HomeAssistantError) as exc:
        await z1.async_join_players(["media_player.sonos_living_room"])
    assert exc.value.translation_key == "foreign_entity_not_supported"


@pytest.mark.asyncio
async def test_callee_as_leader_leaves_previous_group(hass, mock_gateway):
    """When a member calls async_join_players, it leaves its previous group first."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {})
    runtime.decoder_pool = pool
    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    z2 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    _create_test_zone(hass, mock_gateway, runtime, "33", "media_player.zone33")

    # z1 groups with z2
    await z1.async_join_players(["media_player.zone22"])
    assert pool.get_members("media_player.zone11") == ["media_player.zone22"]

    # z2 now calls async_join_players with z3 (becoming leader of its own group)
    await z2.async_join_players(["media_player.zone33"])
    assert pool.get_members("media_player.zone11") == []
    assert pool.get_members("media_player.zone22") == ["media_player.zone33"]


@pytest.mark.asyncio
async def test_member_holding_decoder_releases_when_joining_group(hass, mock_gateway):
    """A member holding an active decoder stops and releases it when joining a group."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer1": 1, "media_player.streamer2": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer1", "idle")
    hass.states.async_set("media_player.streamer2", "idle")

    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    z2 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")

    await pool.claim("media_player.zone11")
    z1._active_decoder = "media_player.streamer1"

    await pool.claim("media_player.zone22")
    z2._active_decoder = "media_player.streamer2"

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as mock_service:
        await z1.async_join_players(["media_player.zone22"])
        mock_service.assert_called_with(
            "media_player", "media_stop", {"entity_id": "media_player.streamer2"}
        )

    assert z2._active_decoder is None
    assert pool.get_assignment("media_player.zone22") == "media_player.streamer1"


@pytest.mark.asyncio
async def test_bus_routing_different_source_drops_member_from_group(hass, mock_gateway):
    """When a member receives a matrix routing frame pointing to a different source, it drops from the group."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer1": 1})
    runtime.decoder_pool = pool

    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    z1._attr_source = "Radio"
    z2 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")

    # Case 1: leader has no active decoder, expected_source resolved via _attr_source (line 1413)
    await z1.async_join_players(["media_player.zone22"])
    assert pool.get_members("media_player.zone11") == ["media_player.zone22"]

    event = MagicMock(spec=OWNSoundEvent)
    event.where = "122"  # 1 + env 2 + src 2
    event.is_source_event = False
    event.is_on = False
    event.is_off = False
    event.volume = None
    z2.handle_event(event)
    await asyncio.sleep(0)

    assert pool.get_members("media_player.zone11") == []
    assert z2.group_members is None

    # Case 2: leader has active decoder, expected_source resolved via decoder_source (line 1411)
    z1._active_decoder = "media_player.streamer1"
    await z1.async_join_players(["media_player.zone22"])
    assert pool.get_members("media_player.zone11") == ["media_player.zone22"]

    z2.handle_event(event)
    await asyncio.sleep(0)

    assert pool.get_members("media_player.zone11") == []
    assert z2.group_members is None



@pytest.mark.asyncio
async def test_options_reload_cleans_orphaned_repair_issues(hass, mock_gateway):
    """_build_pool removes orphaned incompatible decoder repair issues when decoder is removed."""
    from homeassistant.helpers import issue_registry as ir

    from custom_components.myhome.repairs import (
        ISSUE_INCOMPATIBLE_DECODER,
        async_create_incompatible_decoder_issue,
    )

    entry = MagicMock()
    entry.entry_id = "gw_clean"
    entry.options = {}  # Empty options — decoder was removed

    # Pre-create an issue for an old decoder
    async_create_incompatible_decoder_issue(hass, "gw_clean", "media_player.old_cxn", "cambridge_audio")
    issue_reg = ir.async_get(hass)
    assert (DOMAIN, f"{ISSUE_INCOMPATIBLE_DECODER}_gw_clean_media_player_old_cxn") in issue_reg.issues

    # Building pool clears the orphaned issue
    _build_pool(hass, entry)
    assert (DOMAIN, f"{ISSUE_INCOMPATIBLE_DECODER}_gw_clean_media_player_old_cxn") not in issue_reg.issues


@pytest.mark.asyncio
async def test_error_handling_in_join_and_turn_off(hass, mock_gateway):
    """Test exception handling when stopping decoder or sending off frame."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1, "media_player.dec2": 2})
    runtime.decoder_pool = pool

    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    z2 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")

    # Member z2 has active decoder, and stopping it raises an exception (lines 923-924)
    z2._active_decoder = "media_player.dec2"
    with patch("homeassistant.core.ServiceRegistry.async_call", side_effect=RuntimeError("stop failed")):
        await z1.async_join_players(["media_player.zone22"])
    assert pool.get_members("media_player.zone11") == ["media_player.zone22"]
    assert z2._active_decoder is None

    # Leader z1 turns off, but member z2 gateway send raises an exception (lines 1064-1065)
    orig_send = mock_gateway.send

    async def selective_send(cmd):
        if "22" in str(cmd):
            raise RuntimeError("bus failed for member")
        return await orig_send(cmd)

    mock_gateway.send = AsyncMock(side_effect=selective_send)
    await z1.async_turn_off()
    assert z2.state == MediaPlayerState.OFF
    assert pool.get_members("media_player.zone11") == []


@pytest.mark.asyncio
async def test_join_snapshot_replace_semantics(hass, mock_gateway):
    """Joining with an updated list replaces the group; omitted members are turned off and dropped."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool

    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    z2 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    z3 = _create_test_zone(hass, mock_gateway, runtime, "33", "media_player.zone33")
    z1._attr_source = "Radio"

    with patch("asyncio.sleep", return_value=None):
        # Initial join with both members
        await z1.async_join_players(["media_player.zone22", "media_player.zone33"])
        assert pool.get_members("media_player.zone11") == ["media_player.zone22", "media_player.zone33"]
        assert z2.state == MediaPlayerState.ON
        assert z3.state == MediaPlayerState.ON

        # Replace group with only z2 (omitting z3)
        await z1.async_join_players(["media_player.zone22"])
        assert pool.get_members("media_player.zone11") == ["media_player.zone22"]
        assert z2.state == MediaPlayerState.ON
        assert z3.state == MediaPlayerState.OFF

        # Disband group by joining only self
        await z1.async_join_players(["media_player.zone11"])
        assert pool.get_members("media_player.zone11") == []
        assert z2.state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_member_transport_controls_split(hass, mock_gateway):
    """Member transport controls (pause/play/next/prev) are no-ops; stop leaves group and turns off amp."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool

    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    z2 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")

    # Claim decoder for leader z1
    z1._active_decoder = "media_player.dec1"
    z1._attr_state = MediaPlayerState.ON

    with patch("asyncio.sleep", return_value=None):
        await z1.async_join_players(["media_player.zone22"])
    assert pool.get_members("media_player.zone11") == ["media_player.zone22"]
    assert pool.get_leader("media_player.zone22") == "media_player.zone11"

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as mock_call:
        # Non-stop transport controls on member should be ignored (no-op)
        await z2.async_media_pause()
        await z2.async_media_play()
        await z2.async_media_next_track()
        await z2.async_media_previous_track()
        mock_call.assert_not_called()

        # Stop on member leaves the group and turns off member room
        await z2.async_media_stop()
        assert z2.state == MediaPlayerState.OFF
        assert pool.get_members("media_player.zone11") == []
        assert pool.get_leader("media_player.zone22") is None
        # Leader remains playing and claims decoder
        assert z1._active_decoder == "media_player.dec1"


@pytest.mark.asyncio
async def test_leader_entity_removed_keeps_rooms_playing(hass, mock_gateway):
    """Removing an entity clears the group books but sends nothing to the bus.

    Removal happens on every reload, options change and entity_id rename;
    none of those is a request to silence the member rooms.
    """
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool

    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    z2 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    z3 = _create_test_zone(hass, mock_gateway, runtime, "33", "media_player.zone33")
    z1._attr_source = "Radio"

    with patch("asyncio.sleep", return_value=None):
        await z1.async_join_players(["media_player.zone22", "media_player.zone33"])
    assert z2.state == MediaPlayerState.ON

    # A member going away republishes the leader's shrunken group.
    z1.async_write_ha_state = MagicMock()
    await z3.async_will_remove_from_hass()
    assert pool.get_members("media_player.zone11") == ["media_player.zone22"]
    z1.async_write_ha_state.assert_called()

    mock_gateway.send.reset_mock()
    z2.async_write_ha_state = MagicMock()
    await z1.async_will_remove_from_hass()
    mock_gateway.send.assert_not_called()
    assert z2.state == MediaPlayerState.ON
    assert pool.get_members("media_player.zone11") == []
    z2.async_write_ha_state.assert_called()


@pytest.mark.asyncio
async def test_dampen_leader_off_and_bus_off_recursion(hass, mock_gateway):
    """_turning_off flag dampens recursive task creation from bus OFF frames."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool

    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    z1._turning_off = True

    # If _turning_off is already True, handle_event on is_off frame skips scheduling task
    with patch.object(hass, "async_create_task") as mock_task:
        off_event = MagicMock(spec=OWNSoundEvent)
        off_event.where = "11"
        off_event.is_off = True
        off_event.is_on = False
        off_event.is_source_event = False
        off_event.volume = None
        z1.handle_event(off_event)
        mock_task.assert_not_called()

    # Direct call to _async_handle_turn_off returns early if already turning off
    mock_gateway.send.reset_mock()
    await z1._async_handle_turn_off()
    mock_gateway.send.assert_not_called()


@pytest.mark.asyncio
async def test_join_wake_sequence_sends_off_then_on(hass, mock_gateway):
    """Joining a member sends the hardware-required OFF then ON wake sequence."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool

    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    z2 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    z1._attr_source = "Radio"
    z2._attr_state = MediaPlayerState.OFF

    with patch("asyncio.sleep", return_value=None):
        await z1.async_join_players(["media_player.zone22"])

    sent_cmds = [str(call.args[0]) for call in mock_gateway.send.call_args_list if "22" in str(call.args[0])]
    assert "*16*13*22##" in sent_cmds
    assert "*16*3*22##" in sent_cmds
    off_idx = sent_cmds.index("*16*13*22##")
    on_idx = sent_cmds.index("*16*3*22##")
    assert off_idx < on_idx


@pytest.mark.asyncio
async def test_play_media_routes_and_wakes_passive_group_members(hass, mock_gateway):
    """Calling play_media on a leader that formed a passive group routes and wakes members."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool

    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    z2 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    z1._options = lambda: {"source_1_name": "Streamer"}
    z2._options = lambda: {"source_1_name": "Streamer"}

    hass.states.async_set("media_player.dec1", MediaPlayerState.IDLE)

    with patch("asyncio.sleep", return_value=None):
        # Join passively (z1 has no active decoder or source yet)
        await z1.async_join_players(["media_player.zone22"])
        assert pool.get_members("media_player.zone11") == ["media_player.zone22"]

        # Now play_media is called on leader
        with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
            await z1.async_play_media("music", "http://stream.url")

    assert z1._active_decoder == "media_player.dec1"
    assert z2.state == MediaPlayerState.ON
    assert z2._attr_source == "Streamer"


def test_wall_panel_source_change_on_leader(hass, mock_gateway):
    """Source change on leader updates leader source label without crashing."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool

    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")

    # Routing event 112 -> environment 1 to source 2
    event = MagicMock(spec=OWNSoundEvent)
    event.where = "112"
    event.is_source_event = False
    event.is_on = False
    event.is_off = False
    event.volume = None
    z1.handle_event(event)
    assert z1._attr_source == "Cambridge"


def test_public_accessors_and_properties(hass, player):
    """Test active_decoder and where public properties."""
    player._where = "14"
    player._active_decoder = "media_player.custom_dec"
    assert player.where == "14"
    assert player.active_decoder == "media_player.custom_dec"




# ── Audit follow-up: bus echo, atomic joins, routing opt-in ───────────────────


def _echo_to_zones(mock_gateway, runtime):
    """Report amplifier ON/OFF commands back to their zone, as the event session does.

    The gateway puts every command it executes on the bus, so the OFF of the
    wake sequence reaches the zone that sent it (see the iMyHome captures in
    tests/golden/frames/who16_sound.yaml).
    """
    async def send(command):
        who, what, where = str(command).strip("*#").split("*")[:3]
        if who != "16" or what not in ("3", "13") or len(where) != 2:
            return
        for zone in list(runtime.media_players.values()):
            if zone._where == where:
                zone.handle_event(MagicMock(
                    spec=OWNSoundEvent, is_source_event=False, where=where,
                    is_on=what == "3", is_off=what == "13", volume=None,
                ))

    mock_gateway.send = AsyncMock(side_effect=send)


@pytest.mark.asyncio
async def test_join_survives_the_wake_sequence_echo(hass, mock_gateway):
    """A room that was off stays in the group after its own wake OFF comes back."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool
    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    z2 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    z1._attr_source = "Radio"
    _echo_to_zones(mock_gateway, runtime)

    with patch("asyncio.sleep", return_value=None):
        await z1.async_join_players(["media_player.zone22"])
    await hass.async_block_till_done()

    assert pool.get_members("media_player.zone11") == ["media_player.zone22"]
    assert z2.state == MediaPlayerState.ON


@pytest.mark.asyncio
async def test_play_media_from_an_off_leader_survives_the_echo(hass, mock_gateway):
    """The leader keeps its decoder and its members through its own wake OFF."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec1", MediaPlayerState.IDLE)
    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    _echo_to_zones(mock_gateway, runtime)

    with patch("asyncio.sleep", return_value=None):
        await z1.async_join_players(["media_player.zone22"])
        with patch(
            "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
        ) as service:
            await z1.async_play_media("music", "http://stream")
            await hass.async_block_till_done()

    assert [call.args[1] for call in service.call_args_list] == ["play_media"]
    assert z1._active_decoder == "media_player.dec1"
    assert pool.get_assignment("media_player.zone11") == "media_player.dec1"
    assert pool.get_members("media_player.zone11") == ["media_player.zone22"]


@pytest.mark.asyncio
async def test_off_after_the_wake_window_still_turns_the_zone_off(hass, mock_gateway):
    """Only an OFF right after a wake is taken for its echo; a later one is real."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    runtime.decoder_pool = DecoderPool(hass, {})
    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    off = MagicMock(spec=OWNSoundEvent, is_source_event=False, where="11",
                    is_on=False, is_off=True, volume=None)

    with patch("asyncio.sleep", return_value=None):
        await z1.async_turn_on()
    z1._attr_state = MediaPlayerState.ON  # the ON the bus reports next
    assert z1._is_wake_echo()
    z1.handle_event(off)
    assert z1._attr_state == MediaPlayerState.ON

    with patch(
        "custom_components.myhome.media_player.time.monotonic",
        return_value=z1._wake_off_sent_at + 10,
    ):
        z1.handle_event(off)
    await hass.async_block_till_done()
    assert z1._attr_state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_refused_join_leaves_rooms_and_decoders_alone(hass, mock_gateway):
    """An environment conflict is found before any frame or decoder call goes out."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1, "media_player.dec2": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec1", "idle")
    hass.states.async_set("media_player.dec2", "idle")

    leader = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    streaming = _create_test_zone(hass, mock_gateway, runtime, "33", "media_player.zone33")
    _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.zone21")
    _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    leader._attr_source = "Radio"
    await pool.claim("media_player.zone21", environment="2")
    await pool.claim("media_player.zone33", environment="3")
    streaming._active_decoder = pool.get_assignment("media_player.zone33")

    mock_gateway.send.reset_mock()
    with patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ) as service, pytest.raises(HomeAssistantError) as err:
        # zone33 would give up its stream; zone22 collides with zone21.
        await leader.async_join_players(["media_player.zone33", "media_player.zone22"])

    assert err.value.translation_key == "environment_busy"
    mock_gateway.send.assert_not_called()
    service.assert_not_called()
    assert streaming._active_decoder == "media_player.dec2"
    assert pool.get_assignment("media_player.zone33") == "media_player.dec2"
    assert pool.get_members("media_player.zone11") == []


@pytest.mark.asyncio
async def test_join_turns_off_rooms_of_a_disbanded_group(hass, mock_gateway):
    """A joining zone's old group loses its stream, so its rooms are switched off."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec1", "idle")
    leader = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    joiner = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    orphan = _create_test_zone(hass, mock_gateway, runtime, "33", "media_player.zone33")
    leader._attr_source = "Radio"
    joiner._attr_source = "Radio"

    with patch("asyncio.sleep", return_value=None):
        await joiner.async_join_players(["media_player.zone33"])
        assert orphan.state == MediaPlayerState.ON
        with patch(
            "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
        ):
            await leader.async_join_players(["media_player.zone22"])

    assert orphan.state == MediaPlayerState.OFF
    assert "*16*13*33##" in _sent(mock_gateway)
    assert pool.get_group_members("media_player.zone33") is None


@pytest.mark.asyncio
async def test_join_stops_the_decoder_a_joining_zone_held(hass, mock_gateway):
    """A zone that streamed on its own gives its decoder back when it joins."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1, "media_player.dec2": 2})
    runtime.decoder_pool = pool
    leader = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    joiner = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    await pool.claim("media_player.zone22")
    joiner._active_decoder = "media_player.dec1"

    with patch("asyncio.sleep", return_value=None), patch(
        "homeassistant.core.ServiceRegistry.async_call",
        new_callable=AsyncMock,
        side_effect=HomeAssistantError("unreachable"),
    ) as service:
        await leader.async_join_players(["media_player.zone22"])

    service.assert_called_once_with(
        "media_player", "media_stop", {"entity_id": "media_player.dec1"}
    )
    assert joiner._active_decoder is None
    assert pool.get_assignment("media_player.zone22") is None
    assert pool.get_members("media_player.zone11") == ["media_player.zone22"]


@pytest.mark.asyncio
async def test_join_without_routing_configured_only_wakes_members(hass, mock_gateway):
    """Until the matrix is described in the options, joins send no routing frames."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    runtime.decoder_pool = DecoderPool(hass, {})
    leader = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    member = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    leader._options = lambda: {}
    leader._attr_source = "Source 1"

    mock_gateway.send.reset_mock()
    with patch("asyncio.sleep", return_value=None):
        await leader.async_join_players(["media_player.zone22"])

    assert _sent(mock_gateway) == ["*16*13*22##", "*16*3*22##"]
    assert member.state == MediaPlayerState.ON


@pytest.mark.asyncio
async def test_play_media_without_routing_configured_wakes_members(hass, mock_gateway):
    """Members are switched on for the stream, and left on the input they are on."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec1", "idle")
    leader = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    member = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    leader._options = lambda: {}
    await pool.add_member("media_player.zone11", "media_player.zone22", environment="2")

    mock_gateway.send.reset_mock()
    with patch("asyncio.sleep", return_value=None), patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ):
        await leader.async_play_media("music", "http://stream")

    sent = _sent(mock_gateway)
    assert "*16*3*22##" in sent
    assert not any(frame.startswith("*16*3*12") for frame in sent)
    assert member.state == MediaPlayerState.ON


@pytest.mark.asyncio
async def test_member_on_another_input_does_not_mirror_the_group(hass, mock_gateway):
    """A member mirrors the leader's decoder only while it listens to that input."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec1", MediaPlayerState.PLAYING, {"media_title": "Song"})
    _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    member = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    await pool.claim("media_player.zone11")
    await pool.add_member("media_player.zone11", "media_player.zone22")
    member._attr_state = MediaPlayerState.ON

    member._attr_source = "Radio"  # source 1, where dec1 is wired
    assert member.state == MediaPlayerState.PLAYING
    assert member.media_title == "Song"

    member._attr_source = "Cambridge"  # source 2: the room hears something else
    assert member.state == MediaPlayerState.ON
    assert member.media_title is None

    # Joined without routing, before any routing frame: the input is unknown,
    # which is no evidence the room hears the group, so nothing is mirrored.
    member._attr_source = None
    assert member.state == MediaPlayerState.ON
    assert member.media_title is None


@pytest.mark.asyncio
async def test_play_media_passes_over_a_decoder_that_refuses_streams(hass, mock_gateway):
    """cambridge_audio is skipped for a stream URL but used for internet radio."""
    from homeassistant.helpers import entity_registry as er

    er.async_get(hass).async_get_or_create(
        "media_player", "cambridge_audio", "unique_cxn", suggested_object_id="cxn"
    )
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(
        hass,
        {"media_player.cxn": 1, "media_player.dlna": 2},
        stream_incompatible={"media_player.cxn"},
    )
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.cxn", "idle")
    hass.states.async_set("media_player.dlna", "idle")
    z11 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    z22 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")

    with patch("asyncio.sleep", return_value=None), patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ):
        await z11.async_play_media("music", "http://stream")
        await z22.async_play_media("internet_radio", "http://radio")

    assert z11._active_decoder == "media_player.dlna"
    assert z22._active_decoder == "media_player.cxn"


@pytest.mark.asyncio
async def test_play_media_on_a_member_republishes_its_old_leader(hass, mock_gateway):
    """A member that starts its own stream leaves the group, and the leader shows it."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1, "media_player.dec2": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec1", "idle")
    hass.states.async_set("media_player.dec2", "idle")
    leader = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    member = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    await pool.claim("media_player.zone11")
    await pool.add_member("media_player.zone11", "media_player.zone22")
    leader.async_write_ha_state = MagicMock()

    with patch("asyncio.sleep", return_value=None), patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ):
        await member.async_play_media("music", "http://stream")

    assert pool.get_members("media_player.zone11") == []
    assert member._active_decoder == "media_player.dec2"
    leader.async_write_ha_state.assert_called()


# ── Audit round 4: failed claims, listeners follow the pool ──────────────────


@pytest.mark.asyncio
async def test_play_media_on_a_member_that_cannot_claim_stays_grouped(hass, mock_gateway):
    """All decoders busy: the member gets decoders_busy and is still in its group."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec1", "idle")
    _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    member = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    await pool.claim("media_player.zone11")
    await pool.add_member("media_player.zone11", "media_player.zone22")

    with pytest.raises(HomeAssistantError) as err:
        await member.async_play_media("music", "http://stream")

    assert err.value.translation_key == "decoders_busy"
    assert member.group_members == ["media_player.zone11", "media_player.zone22"]
    assert member._active_decoder is None


@pytest.mark.asyncio
async def test_failed_start_republishes_the_disbanded_group(hass, mock_gateway):
    """A leader whose decoder never wakes gives it back, and its members show that."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec1", MediaPlayerState.OFF)
    leader = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    member = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    await pool.add_member("media_player.zone11", "media_player.zone22")
    member.async_write_ha_state = MagicMock()

    with patch("asyncio.sleep", return_value=None), patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ), pytest.raises(HomeAssistantError) as err:
        await leader.async_play_media("music", "http://stream")

    assert err.value.translation_key == "decoder_wake_timeout"
    assert pool.get_assignment("media_player.zone11") is None
    assert member.group_members is None
    member.async_write_ha_state.assert_called()


@pytest.mark.asyncio
async def test_decoder_watch_follows_the_rebuilt_pool(hass, player, mock_gateway):
    """Saving options rebuilds the pool without a reload; the zone watches the new decoders.

    A zone added before any decoder was configured watched nothing. After the
    rebuild it must follow the new decoder, stop following a removed one, and
    forget a claim the new pool does not hold.
    """
    _set_pool(player, DecoderPool(hass, {}))
    player.async_write_ha_state = MagicMock()
    player.async_schedule_update_ha_state = MagicMock()
    await player.async_added_to_hass()
    player._active_decoder = "media_player.old"

    _set_pool(player, DecoderPool(hass, {"media_player.new": 1}))
    async_dispatcher_send(hass, f"myhome_pool_updated_{mock_gateway.mac}")
    assert player._active_decoder is None

    # The zone streams from the new decoder; its state changes reach the zone.
    player._active_decoder = "media_player.new"
    hass.states.async_set("media_player.new", MediaPlayerState.PLAYING, {"media_title": "Song"})
    await hass.async_block_till_done()
    player.async_schedule_update_ha_state.assert_called()

    # Decoder removed again: nothing is watched any more.
    _set_pool(player, DecoderPool(hass, {}))
    async_dispatcher_send(hass, f"myhome_pool_updated_{mock_gateway.mac}")
    assert player._unsub_decoders is None

    # The remove callback registered at add time drops whichever watch is current.
    _set_pool(player, DecoderPool(hass, {"media_player.new": 1}))
    async_dispatcher_send(hass, f"myhome_pool_updated_{mock_gateway.mac}")
    assert player._unsub_decoders is not None
    for remove in player._on_remove or []:
        remove()
    assert player._unsub_decoders is None
