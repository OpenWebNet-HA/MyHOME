"""Test the MyHOME media player platform and dynamic proxy."""
import asyncio
import contextlib
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
from homeassistant.core import Event, EventStateChangedData, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    OWNEvent,
    OWNMessage,
    OWNSoundEvent,
)

from custom_components.myhome.const import (
    CONF_AUTO_JOIN_STREAMING,
    CONF_DECODER_ENTITY,
    CONF_DECODER_PRE_GAIN,
    CONF_DECODER_SOURCE,
    CONF_ENTITY,
    CONF_SOURCE_DEFAULTS,
    CONF_SOURCE_NAME,
    CONF_SOURCE_TUNER,
    DOMAIN,
)
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.decoder_pool import DecoderPool
from custom_components.myhome.media_player import (
    MyHOMEMediaPlayer,
    _zone_address,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.myhome.media_player_pool import build_pool
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
    pool = build_pool(hass, mock_config_entry)
    assert pool._store.key == "myhome.decoder_pool.test_entry_id"  # its books outlive a restart
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
async def test_async_added_to_hass_watches_the_pool_decoders(hass, player, mock_gateway):
    """Adding the zone starts watching the pool's decoders."""
    mock_pool = MagicMock(spec=DecoderPool)
    mock_pool.is_configured = True
    mock_pool.decoder_entity_ids = ["media_player.squeezelite_1"]
    _set_pool(player, mock_pool)

    await player.async_added_to_hass()

    assert player._unsub_decoders is not None


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
    mock_pool.get_members.return_value = []
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


def test_diagnostics_state_reports_the_zone_without_an_entity_id(player):
    """The diagnostics view of a zone: state, source number, volume, mute, parked - no names."""
    _name_sources(player, s1="Camebridge")
    player._attr_state = MediaPlayerState.ON
    player._attr_source = "Camebridge"
    player._attr_volume_level = 0.0
    player._parked = True

    assert player.diagnostics_state() == {
        "state": "on",
        "source": 1,
        "volume_level": 0.0,
        "is_volume_muted": False,
        "has_decoder": False,
        "parked": True,
        "wake_pending": False,
        "status_seen": False,
    }


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

    # Volume 0 on the bus is a volume, not a mute: Music Assistant locks the
    # slider of a muted player, so a room turned down to 0 could not come back.
    msg_vol_0 = MagicMock(spec=OWNSoundEvent, is_source_event=False, where="1", is_on=False, is_off=False, volume=0)
    player.handle_event(msg_vol_0)
    assert player._attr_volume_level == 0.0
    assert player.is_volume_muted is False

    # An explicit mute survives its own volume-0 echo and ends when the bus
    # reports the volume raised again (a wall panel, say).
    player._attr_is_volume_muted = True
    player.handle_event(msg_vol_0)
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
    mock_pool.get_members.return_value = []
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
        "owner_name": "media_player.audio_zone_22",
        "environment": "2",
        "rooms": "no other room",
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
    from custom_components.myhome.media_player_routing import routing_address

    assert routing_address("05", 2) is None
    assert routing_address("15", 2) == "112"


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
    from custom_components.myhome.media_player_routing import parse_routing_address, routing_address

    # A two-digit amplifier address in that environment, e.g. environment 2 -> "23"
    zone = f"{environment}3"
    assert routing_address(zone, source) == frame.removeprefix("*16*3*").removesuffix("##")
    assert parse_routing_address(frame.removeprefix("*16*3*").removesuffix("##")) == (source, environment)


def test_source_addresses_are_not_routing_addresses():
    """101-109 are source devices; decoding them as routing invents a source 0."""
    from custom_components.myhome.media_player_routing import parse_routing_address

    for fixture in _golden_sound_fixtures():
        where = str(fixture.get("where"))
        if where.startswith("10") and len(where) == 3:
            assert parse_routing_address(where) is None, where


def test_captured_amplifier_addresses_resolve_to_their_environment():
    """Amplifier addresses are EA: the environment is the first digit."""
    from custom_components.myhome.media_player_routing import zone_environment

    assert zone_environment("23") == "2"   # plant A, eetkamer
    assert zone_environment("11") == "1"   # plant B
    assert zone_environment("36") == "3"   # plant A, badkamer



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
    from custom_components.myhome.media_player_routing import routing_address, zone_environment

    assert zone_environment(where) == environment
    assert routing_address(where, 2) == route_s2


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
    from custom_components.myhome.media_player_routing import parse_routing_address

    pseudo = who16.removeprefix("*16*3*").removesuffix("##")
    assert parse_routing_address(pseudo) == (source, environment)

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
    from custom_components.myhome.media_player_routing import zone_environment

    assert zone_environment(amplifier) == area
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
async def test_rooms_turned_on_and_joined_one_by_one_route_once(hass, mock_gateway):
    """Music Assistant turns on and joins each room in turn; the shared frames go out once (live capture)."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    zones = {w: _create_test_zone(hass, mock_gateway, runtime, w, f"media_player.audio_zone_{w}") for w in ("21", "22", "23")}
    zones["21"]._attr_source = "Cambridge"
    zones["22"]._default_source_number = zones["23"]._default_source_number = 2

    mock_gateway.send.reset_mock()
    with patch("custom_components.myhome.media_player.time.monotonic", return_value=100.0):
        await zones["22"]._route_to(2, coalesce=True)  # the turn_on's default source
        await zones["21"].async_join_players(["media_player.audio_zone_22"])
        await zones["23"]._route_to(2, coalesce=True)
        await zones["21"].async_join_players(["media_player.audio_zone_23"])
    sent = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert sent.count("*16*3*102##") == 1, sent
    assert sent.count("*16*3*122##") == 1, sent
    assert "*16*3*22##" in sent and "*16*3*23##" in sent

    # Well after the burst the routing may have been changed elsewhere: send it again.
    mock_gateway.send.reset_mock()
    with patch("custom_components.myhome.media_player.time.monotonic", return_value=200.0):
        await zones["22"]._route_to(2, coalesce=True)
    assert [str(call.args[0]) for call in mock_gateway.send.call_args_list] == ["*16*3*102##", "*16*3*122##"]

    # A group that is switched off starts from scratch.
    zones["21"]._forget_recent_routing()
    assert runtime.routing_recent == {}


@pytest.mark.asyncio
async def test_a_member_leaving_keeps_the_routing_memory_but_a_stopping_leader_clears_it(hass, mock_gateway):
    """Live 2026-09-29: a room unchecked in Music Assistant must not make the next play repeat the routes."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec": 1})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec", "idle")
    z21 = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.audio_zone_21")
    z22 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.audio_zone_22")
    z23 = _create_test_zone(hass, mock_gateway, runtime, "23", "media_player.audio_zone_23")
    for zone in (z21, z22, z23):
        zone._attr_state = MediaPlayerState.ON
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
        await z21.async_play_media("music", "http://stream")
    await z21.async_join_players(["media_player.audio_zone_22", "media_player.audio_zone_23"])
    assert runtime.routing_recent

    await z22.async_turn_off()  # unchecked in Music Assistant
    assert runtime.routing_recent

    await z21.async_turn_off()  # the leader hands the group on: the routes stay valid
    assert runtime.routing_recent
    assert pool.get_assignment("media_player.audio_zone_23") == "media_player.dec"

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
        await z23.async_turn_off()  # the last room stops the stream
    assert runtime.routing_recent == {}


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
    # The source-on frame went out with the play a moment ago and is not repeated.
    assert "*16*3*102##" not in sent_frames
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
    """Member unjoining removes it from the group and grants a grace period.

    The amplifier is not switched off immediately — see _GROUP_LEAVE_GRACE —
    since the same room may be reassigned elsewhere (e.g. as a new leader)
    right after leaving. Deselecting the leader itself goes through the
    separate transfer_leadership handover (test_unjoin_player_leader_disbands)
    and is unaffected by this.
    """
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

    # No frame yet: the amplifier stays on through the grace period.
    sent_frames = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert "*16*0*23##" not in sent_frames and "*16*13*23##" not in sent_frames
    assert z23.state == MediaPlayerState.ON
    assert z23._pending_off_task is not None
    assert z23.group_members is None
    assert z22.group_members is None
    assert pool.get_assignment("media_player.audio_zone_23") is None
    # Leader is still streaming
    assert pool.get_assignment("media_player.audio_zone_22") == "media_player.dec"

    # Nothing reclaims zone23 in this test; cancel the real grace timer.
    z23._cancel_pending_off()


@pytest.mark.asyncio
async def test_unjoin_player_leader_disbands(hass, mock_gateway):
    """Leader unjoining transfers leadership to the next member and leaves remaining members playing."""
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
    # Leader turns off its own amplifier
    assert any("22##" in f for f in sent_frames)
    # Remaining members are NOT turned off
    assert not any("23##" in f for f in sent_frames)
    assert not any("35##" in f for f in sent_frames)
    assert z22.state == MediaPlayerState.OFF
    assert z22.group_members is None
    # Leadership transferred to z23
    assert pool.is_leader("media_player.audio_zone_23")
    assert pool.get_members("media_player.audio_zone_23") == ["media_player.audio_zone_35"]
    assert z23.group_members == ["media_player.audio_zone_23", "media_player.audio_zone_35"]
    assert z35.group_members == ["media_player.audio_zone_23", "media_player.audio_zone_35"]
    assert z23._active_decoder == "media_player.dec"
    assert pool.get_assignment("media_player.audio_zone_23") == "media_player.dec"
    assert pool.get_assignment("media_player.audio_zone_22") is None


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

    with patch("custom_components.myhome.media_player_pool.async_create_incompatible_decoder_issue") as mock_issue:
        pool = build_pool(hass, entry)
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
async def test_cambridge_audio_with_companion_dlna_bridges_stream(hass, mock_gateway):
    """When a cambridge_audio entity has a companion DLNA entity, play_media bridges seamlessly."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    cam_entry = MockConfigEntry(domain="cambridge_audio")
    cam_entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id=cam_entry.entry_id,
        identifiers={("cambridge_audio", "cxn_hw")},
    )
    ent_reg.async_get_or_create(
        "media_player", "cambridge_audio", "cxn_hw", device_id=device.id, suggested_object_id="cambridge_cxn"
    )
    ent_reg.async_get_or_create(
        "media_player", "dlna_dmr", "cxn_hw_dlna", device_id=device.id, suggested_object_id="cambridge_cxn_dlna"
    )

    entry = MagicMock()
    entry.entry_id = "test_gw"
    entry.options = {
        CONF_DECODER_ENTITY.format(1): "media_player.cambridge_cxn",
        CONF_DECODER_SOURCE.format(1): 2,
    }

    with patch("custom_components.myhome.media_player_pool.async_create_incompatible_decoder_issue") as mock_issue:
        pool = build_pool(hass, entry)
        # Repair issue is NOT created because companion is detected!
        mock_issue.assert_not_called()

    assert pool.companion_map == {"media_player.cambridge_cxn": "media_player.cambridge_cxn_dlna"}
    assert "media_player.cambridge_cxn" not in pool.stream_incompatible

    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.cambridge_cxn", "idle")
    hass.states.async_set("media_player.cambridge_cxn_dlna", "idle")
    z22 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.audio_zone_22")

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as mock_call:
        await z22.async_play_media("music", "http://stream")

        # Verify play_media was forwarded to the companion DLNA entity
        calls = [c for c in mock_call.call_args_list if c.args[0] == "media_player" and c.args[1] == "play_media"]
        assert len(calls) == 1
        assert calls[0].args[2]["entity_id"] == "media_player.cambridge_cxn_dlna"
        assert calls[0].args[2]["media_content_id"] == "http://stream"

        # Verify streaming target null and cache branches
        assert z22._streaming_target(None) is None
        z22._companion_cache = {"media_player.cambridge_cxn": "media_player.cambridge_cxn_dlna"}
        assert z22._streaming_target("media_player.cambridge_cxn") == "media_player.cambridge_cxn_dlna"

        # Verify media_stop signals companion, then signals hardware decoder
        mock_call.reset_mock()
        await z22.async_media_stop()
        stop_calls = [c for c in mock_call.call_args_list if c.args[0] == "media_player" and c.args[1] == "media_stop"]
        assert len(stop_calls) == 2
        assert stop_calls[0].args[2]["entity_id"] == "media_player.cambridge_cxn_dlna"
        assert stop_calls[1].args[2]["entity_id"] == "media_player.cambridge_cxn"

        # Verify exception during secondary hardware stop is safely caught and logged
        mock_call.reset_mock()
        mock_call.side_effect = [None, RuntimeError("Secondary stop failed")]
        await z22.async_media_stop()

        # Verify turn_off stops companion and hardware decoder
        mock_call.reset_mock()
        mock_call.side_effect = None
        z22._attr_state = MediaPlayerState.ON
        await z22.async_turn_off()
        turn_off_stops = [c for c in mock_call.call_args_list if c.args[0] == "media_player" and c.args[1] == "media_stop"]
        assert len(turn_off_stops) == 2
        assert turn_off_stops[0].args[2]["entity_id"] == "media_player.cambridge_cxn_dlna"
        assert turn_off_stops[1].args[2]["entity_id"] == "media_player.cambridge_cxn"

        # Verify exception during secondary hardware stop in turn_off is safely caught
        mock_call.reset_mock()
        mock_call.side_effect = [None, RuntimeError("Secondary turn_off stop failed")]
        z22._attr_state = MediaPlayerState.ON
        z22._active_decoder = "media_player.cambridge_cxn"
        await z22.async_turn_off()

        # Test _resolve_playback_state with companion in playing state
        hass.states.async_set("media_player.cambridge_cxn_dlna", "playing")
        assert z22._resolve_playback_state("media_player.cambridge_cxn") == MediaPlayerState.PLAYING

        # Test _resolve_playback_state with no hass
        with patch.object(z22, "hass", None):
            assert z22._resolve_playback_state("media_player.cambridge_cxn") is None

        # Test _get_decoder_attr with companion metadata
        z22._active_decoder = "media_player.cambridge_cxn"
        hass.states.async_set(
            "media_player.cambridge_cxn_dlna",
            "playing",
            {"media_title": "Direct Companion Title"},
        )
        assert z22._get_decoder_attr("media_title") == "Direct Companion Title"


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
    from custom_components.myhome.media_player_group import _get_group_members
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
    z2._attr_state = MediaPlayerState.ON

    # Leader turns off: the group and decoder pass to the member, nothing stops
    mock_gateway.send.reset_mock()
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as mock_service:
        await z1.async_turn_off()
        mock_service.assert_not_called()
    assert [str(c.args[0]) for c in mock_gateway.send.call_args_list] == ["*16*13*11##"]
    assert pool.get_members("media_player.zone1") == []
    assert pool.get_assignment("media_player.zone2") == "media_player.streamer"
    assert z1._attr_state == MediaPlayerState.OFF
    assert z2._attr_state == MediaPlayerState.ON
    assert z2._active_decoder == "media_player.streamer"


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
        # The leader going off at the wall hands the stream to z3: no stop
        mock_service.assert_not_called()
    assert pool.get_members("media_player.zone1") == []
    assert pool.get_assignment("media_player.zone3") == "media_player.streamer"
    assert z3._active_decoder == "media_player.streamer"
    assert z3._attr_state != MediaPlayerState.OFF


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
    """build_pool removes orphaned incompatible decoder repair issues when decoder is removed."""
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
    build_pool(hass, entry)
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
async def test_join_players_is_additive_not_a_snapshot(hass, mock_gateway):
    """Joining a new member keeps every existing one — group_members means "add", not "replace".

    Reported live: adding a third room to an already-playing two-room group
    silently dropped the second room instead of ending up with three. Music
    Assistant's HA player only ever calls join with the newly added entities
    (verified against its source), never the full desired membership, and
    removes a room with a separate unjoin call — never a smaller
    group_members list. A snapshot interpretation of group_members breaks on
    exactly that call shape.
    """
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool

    z1 = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    z2 = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    z3 = _create_test_zone(hass, mock_gateway, runtime, "33", "media_player.zone33")
    z1._attr_source = "Radio"

    with patch("asyncio.sleep", return_value=None):
        # Start a group with one member.
        await z1.async_join_players(["media_player.zone22"])
        assert pool.get_members("media_player.zone11") == ["media_player.zone22"]
        assert z2.state == MediaPlayerState.ON

        # Add a second: zone22 must stay, zone33 must be added — this is the
        # exact call Music Assistant makes and the exact case that broke.
        mock_gateway.send.reset_mock()
        await z1.async_join_players(["media_player.zone33"])
        assert pool.get_members("media_player.zone11") == ["media_player.zone22", "media_player.zone33"]
        assert z2.state == MediaPlayerState.ON
        assert z3.state == MediaPlayerState.ON
        assert "*16*13*22##" not in _sent(mock_gateway)  # zone22 never touched

        # Joining an already-present member again is a harmless no-op.
        await z1.async_join_players(["media_player.zone22"])
        assert pool.get_members("media_player.zone11") == ["media_player.zone22", "media_player.zone33"]

        # Naming only the leader adds nothing and drops nobody (unjoin is the
        # only way to shrink a group).
        await z1.async_join_players(["media_player.zone11"])
        assert pool.get_members("media_player.zone11") == ["media_player.zone22", "media_player.zone33"]
        assert z2.state == MediaPlayerState.ON
        assert z3.state == MediaPlayerState.ON


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
async def test_decoder_watch_reaches_the_zone_and_stops_on_remove(hass, player, mock_gateway):
    """The zone follows its decoder's state, and removing the entity ends the watch.

    Saving options reloads the entry, so a zone never has to swap its watch for
    a rebuilt pool: it is created against the pool it will use.
    """
    _set_pool(player, DecoderPool(hass, {}))
    await player.async_added_to_hass()
    assert player._unsub_decoders is None  # no decoder configured: nothing to watch
    player._untrack_decoders()

    _set_pool(player, DecoderPool(hass, {"media_player.new": 1}))
    player.async_write_ha_state = MagicMock()
    player.async_schedule_update_ha_state = MagicMock()
    await player.async_added_to_hass()
    assert player._unsub_decoders is not None

    player._active_decoder = "media_player.new"
    hass.states.async_set("media_player.new", MediaPlayerState.PLAYING, {"media_title": "Song"})
    await hass.async_block_till_done()
    player.async_schedule_update_ha_state.assert_called()

    for remove in player._on_remove or []:
        remove()
    assert player._unsub_decoders is None


@pytest.mark.asyncio
async def test_decoders_refusing_companion_coverage(hass, mock_gateway):
    """Test coverage for _decoders_refusing when a companion exists."""
    from custom_components.myhome.decoder_pool import DecoderPool
    from custom_components.myhome.media_player import MyHOMEMediaPlayer

    pool = DecoderPool(hass, {})
    pool._stream_incompatible = frozenset(["media_player.cambridge_cxn"])
    pool.companion_map["media_player.cambridge_cxn"] = "media_player.cambridge_dlna"

    p = MyHOMEMediaPlayer(
        hass=hass,
        name="Test",
        entity_name=None,
        device_id="22#16",
        who="16",
        where="22",
        manufacturer="BTicino",
        model="Audio System",
        gateway=mock_gateway,
    )
    # mock the runtime data so pool is found
    from custom_components.myhome.data import MyHOMERuntimeData
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    runtime.decoder_pool = pool
    p._companion_cache = {"media_player.cambridge_cxn": "media_player.cambridge_dlna"}
    p.hass = hass

    refusing = p._decoders_refusing(pool, "music")
    assert "media_player.cambridge_cxn" not in refusing


@pytest.mark.asyncio
async def test_auto_power_off_anti_hiss_on_decoder_states(hass, player, mock_gateway):
    """Test anti-hiss auto power-off when decoder stops, goes idle, or pauses."""
    from homeassistant.core import State

    player._active_decoder = "media_player.squeezelite_1"
    player._attr_state = MediaPlayerState.ON
    player.async_turn_off = AsyncMock()

    # 1. State change from unassigned decoder is ignored
    unassigned_event = MagicMock(
        data={
            "entity_id": "media_player.other_dec",
            "new_state": State("media_player.other_dec", "idle"),
        }
    )
    player._async_decoder_state_changed(unassigned_event)
    assert player._auto_off_unsub is None
    player.async_turn_off.assert_not_called()

    callbacks = []

    def mock_call_later(_hass, delay, action):
        callbacks.append((delay, action))
        return MagicMock()

    # 2. Decoder transitions to OFF: the same short timer as idle, so a
    #    decoder that reports "off" while reconnecting can come back first.
    with patch("custom_components.myhome.media_player_decoder.async_call_later", side_effect=mock_call_later):
        off_event = MagicMock(
            data={
                "entity_id": "media_player.squeezelite_1",
                "new_state": State("media_player.squeezelite_1", "off"),
            }
        )
        player._async_decoder_state_changed(off_event)
        assert [delay for delay, _ in callbacks] == [3.0]
        callbacks.pop()[1](None)
        await hass.async_block_till_done()
        player.async_turn_off.assert_called_once()
        player.async_turn_off.reset_mock()

    # 3. Decoder transitions to IDLE: schedules 3s timer
    with patch("custom_components.myhome.media_player_decoder.async_call_later", side_effect=mock_call_later):
        idle_event = MagicMock(
            data={
                "entity_id": "media_player.squeezelite_1",
                "new_state": State("media_player.squeezelite_1", "idle"),
            }
        )
        player._async_decoder_state_changed(idle_event)
        assert len(callbacks) == 1
        assert callbacks[0][0] == 3.0
        # Trigger timer callback
        callbacks[0][1](None)
        await hass.async_block_till_done()
        player.async_turn_off.assert_called_once()
        player.async_turn_off.reset_mock()

    # 4. Decoder transitions to PLAYING: cancels scheduled auto-off
    mock_unsub = MagicMock()
    player._auto_off_unsub = mock_unsub
    play_event = MagicMock(
        data={
            "entity_id": "media_player.squeezelite_1",
            "new_state": State("media_player.squeezelite_1", "playing"),
        }
    )
    player._async_decoder_state_changed(play_event)
    mock_unsub.assert_called_once()
    assert player._auto_off_unsub is None

    # 5. Decoder transitions to PAUSED: schedules 60s timer
    callbacks.clear()
    with patch("custom_components.myhome.media_player_decoder.async_call_later", side_effect=mock_call_later):
        paused_event = MagicMock(
            data={
                "entity_id": "media_player.squeezelite_1",
                "new_state": State("media_player.squeezelite_1", "paused"),
            }
        )
        player._async_decoder_state_changed(paused_event)
        assert len(callbacks) == 1
        assert callbacks[0][0] == 60.0
        # Trigger timer callback
        callbacks[0][1](None)
        await hass.async_block_till_done()
        player.async_turn_off.assert_called_once()


@pytest.mark.asyncio
async def test_async_media_play_wakes_zone_if_off(hass, player):
    """Test async_media_play wakes the zone when it is in OFF state."""
    player._active_decoder = "media_player.squeezelite_1"
    player._attr_state = MediaPlayerState.OFF
    player._async_wake_zone = AsyncMock()
    player._forward_to_decoder = AsyncMock()
    mock_unsub = MagicMock()
    player._auto_off_unsub = mock_unsub

    await player.async_media_play()

    mock_unsub.assert_called_once()
    assert player._auto_off_unsub is None
    player._async_wake_zone.assert_called_once()
    player._forward_to_decoder.assert_called_once_with("media_play")


@pytest.mark.asyncio
async def test_auto_off_unsub_cancelled_on_turn_on(hass, player, mock_gateway):
    """A pending auto-off timer is cancelled when the zone is explicitly turned on."""
    mock_unsub = MagicMock()
    player._auto_off_unsub = mock_unsub
    player._attr_state = MediaPlayerState.ON  # skip the wake sequence

    await player.async_turn_on()

    mock_unsub.assert_called_once()
    assert player._auto_off_unsub is None


@pytest.mark.asyncio
async def test_auto_off_unsub_cancelled_on_handle_turn_off(hass, player, mock_gateway):
    """A pending auto-off timer is cancelled by the coordinated turn-off sequence."""
    mock_unsub = MagicMock()
    player._auto_off_unsub = mock_unsub

    await player._async_handle_turn_off()

    mock_unsub.assert_called_once()
    assert player._auto_off_unsub is None


@pytest.mark.asyncio
async def test_auto_off_unsub_cancelled_on_will_remove_from_hass(hass, player, mock_gateway):
    """A pending auto-off timer is cancelled when the entity is removed from hass."""
    mock_unsub = MagicMock()
    player._auto_off_unsub = mock_unsub

    await player.async_will_remove_from_hass()

    mock_unsub.assert_called_once()
    assert player._auto_off_unsub is None


@pytest.mark.asyncio
async def test_auto_off_unsub_cancelled_on_play_media(hass, player, mock_gateway):
    """A pending auto-off timer is cancelled when new playback is requested."""
    mock_unsub = MagicMock()
    player._auto_off_unsub = mock_unsub
    _set_pool(player, None)  # short-circuit right after the cancellation

    await player.async_play_media("music", "http://stream.url")

    mock_unsub.assert_called_once()
    assert player._auto_off_unsub is None


@pytest.mark.asyncio
async def test_auto_power_off_cancels_pending_timer_on_off_transition(hass, player, mock_gateway):
    """A decoder going to OFF replaces a pending pause timer with the short one."""
    player._active_decoder = "media_player.squeezelite_1"
    player._attr_state = MediaPlayerState.ON
    player.async_turn_off = AsyncMock()
    mock_unsub = MagicMock()
    player._auto_off_unsub = mock_unsub
    player._auto_off_key = (60.0, "media_player.squeezelite_1")

    off_event = MagicMock(
        data={
            "entity_id": "media_player.squeezelite_1",
            "new_state": State("media_player.squeezelite_1", "off"),
        }
    )
    timers, patcher = _capture_timers()
    with patcher:
        player._async_decoder_state_changed(off_event)
        mock_unsub.assert_called_once()
        assert [delay for delay, _ in timers] == [3.0]
        timers[0][1](None)
    await hass.async_block_till_done()
    player.async_turn_off.assert_called_once()



# ── Group-leave grace period: a departing member keeps playing ──────────────
#
# Motivated by a captured trace (music-assistant.log, single-decoder install,
# decoder_1 on source 2, source_defaults routing every environment to it)
# where deselecting the current leader in the Music Assistant UI produced:
#
#   Transferring leadership of badkamer to audio_zone_23 (1 remaining member(s))
#   Calling set_members on native player badkamer with add=[], remove=['audio_zone_23']
#   Clearing active output protocol on badkamer
#   Setting active output protocol on Eetkamer to Native      (+2.0s)
#   Start Queue Flow stream for Queue Eetkamer                (+1.3s)
#
# Deselecting the leader itself is handled elsewhere: DecoderPool.transfer_leadership
# hands the decoder to the first remaining member atomically the moment the
# leader calls unjoin, with no timing window needed (see
# test_unjoin_player_leader_disbands). What still needs protecting is a plain
# member being dropped from its group without becoming the new leader in the
# same call — its own unjoin, or being left out of a join snapshot — since
# Music Assistant (and other players) may still remove a room from a group
# and only decide what to do with it a couple of seconds later. Turning the
# room off immediately in that window silences it and then wakes it again
# for no reason.


def _echo_to_zones(mock_gateway, runtime):
    """Report amplifier ON/OFF commands back to their zone, as the event session does.

    The gateway puts every command it executes on the bus, so the OFF of the
    wake sequence reaches the zone that sent it.
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
async def test_member_departure_survives_the_wake_sequence_echo(hass, mock_gateway):
    """A member dropped from its group and reclaimed stays on through its own wake echo."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec1", MediaPlayerState.IDLE)
    leader = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    member = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    _echo_to_zones(mock_gateway, runtime)

    with patch("asyncio.sleep", return_value=None), patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ):
        await leader.async_play_media("music", "http://stream")
        await leader.async_join_players(["media_player.zone22"])
        assert member.state == MediaPlayerState.ON

    mock_gateway.send.reset_mock()
    await member.async_unjoin_player()

    # The room must not go silent: no OFF frame yet, state still ON.
    # (No hass.async_block_till_done() here: it waits for every background
    # task, including the real 5s grace timer just scheduled, which would
    # defeat this very assertion.)
    assert "*16*13*22##" not in _sent(mock_gateway)
    assert member.state == MediaPlayerState.ON
    assert member._pending_off_task is not None
    assert pool.get_leader("media_player.zone22") is None

    # The old leader stops (releasing decoder_1), and play_media targets the
    # departed member shortly after — well within the grace window. Its
    # amplifier was never turned off, so the wake sequence is skipped.
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
        await leader.async_turn_off()
    assert pool.get_assignment("media_player.zone11") is None

    mock_gateway.send.reset_mock()
    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
        await member.async_play_media("music", "http://stream.new")

    assert member._active_decoder == "media_player.dec1"
    assert member._pending_off_task is None
    assert "*16*13*22##" not in _sent(mock_gateway)
    assert "*16*3*22##" not in _sent(mock_gateway)


@pytest.mark.asyncio
async def test_group_departure_turns_off_after_the_grace_period(hass, mock_gateway):
    """A member that leaves and is never reused is still switched off — just later."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool
    _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    member = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    await pool.add_member("media_player.zone11", "media_player.zone22")
    member._attr_state = MediaPlayerState.ON

    with patch(
        "custom_components.myhome.media_player_group._GROUP_LEAVE_GRACE", 0.01
    ):
        mock_gateway.send.reset_mock()
        await member.async_unjoin_player()
        assert "*16*13*22##" not in _sent(mock_gateway)

        await asyncio.sleep(0.05)
        await hass.async_block_till_done()

    assert "*16*13*22##" in _sent(mock_gateway)
    assert member._attr_state == MediaPlayerState.OFF
    assert member._pending_off_task is None


@pytest.mark.asyncio
async def test_join_does_not_drop_members_reaching_three(hass, mock_gateway):
    """A group already at two members accepts a third without dropping either.

    Same scenario reported live: badkamer (leader) + eetkamer already
    playing, adding keuken must end with all three grouped.
    """
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec1", "idle")
    leader = _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    third = _create_test_zone(hass, mock_gateway, runtime, "33", "media_player.zone33")
    leader._attr_source = "Radio"

    with patch("asyncio.sleep", return_value=None):
        await leader.async_join_players(["media_player.zone22"])
        await leader.async_join_players(["media_player.zone33"])

    assert pool.get_members("media_player.zone11") == ["media_player.zone22", "media_player.zone33"]
    assert third.state == MediaPlayerState.ON


@pytest.mark.asyncio
async def test_rejoining_during_the_grace_period_cancels_the_off(hass, mock_gateway):
    """A departing member that is immediately joined into a new group is not switched off."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1, "media_player.dec2": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec2", "idle")
    _create_test_zone(hass, mock_gateway, runtime, "11", "media_player.zone11")
    zone = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.zone22")
    new_leader = _create_test_zone(hass, mock_gateway, runtime, "44", "media_player.zone44")
    await pool.add_member("media_player.zone11", "media_player.zone22")
    zone._attr_state = MediaPlayerState.ON
    new_leader._attr_source = "Cambridge"

    await zone.async_unjoin_player()
    assert zone._pending_off_task is not None

    with patch("asyncio.sleep", return_value=None):
        await new_leader.async_join_players(["media_player.zone22"])

    assert zone._pending_off_task is None
    assert pool.get_leader("media_player.zone22") == "media_player.zone44"


# ── Stray rooms: on, listening to a decoder, but holding no claim on it ─────
#
# Live trace (single decoder on source 2, every environment defaulting to it):
# Home Assistant restarted while an environment-2 amplifier was on. The group
# books came back empty, so Music Assistant showed badkamer alone in its group,
# yet the environment-2 room played badkamer's stream because an amplifier that
# is on simply plays what its environment is routed to. When the music stopped
# only badkamer went off (it owned the decoder), and the other room kept
# running: nothing owned it.


def _stray_setup(hass, mock_gateway, *, options=None):
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 2})
    runtime.decoder_pool = pool
    zone = _create_test_zone(hass, mock_gateway, runtime, "23", "media_player.zone23")
    if options:
        zone.platform.config_entry.options.update(options)
    zone._attr_state = MediaPlayerState.ON
    zone.async_turn_off = AsyncMock()
    return zone


def _dec_event(state):
    return MagicMock(
        data={
            "entity_id": "media_player.dec1",
            "new_state": State("media_player.dec1", state),
        }
    )


def _capture_timers():
    timers = []

    def call_later(_hass, delay, action):
        timers.append((delay, action))
        return MagicMock()

    # Setup arms the restore-confirm timer, the decoder layer the anti-hiss ones.
    class _Patcher:
        """Re-usable: a test may enter it more than once."""

        def __enter__(self):
            self._stack = contextlib.ExitStack()
            for module in ("media_player", "media_player_decoder"):
                self._stack.enter_context(
                    patch(f"custom_components.myhome.{module}.async_call_later", side_effect=call_later)
                )
            return self

        def __exit__(self, *exc):
            return self._stack.__exit__(*exc)

    return timers, _Patcher()


@pytest.mark.asyncio
async def test_stray_room_is_switched_off_when_its_decoder_goes_idle(hass, mock_gateway):
    """A room that is on, unclaimed, and routed to the decoder follows it off."""
    zone = _stray_setup(hass, mock_gateway)
    zone._attr_source = "Cambridge"  # source 2, reported on the bus
    assert zone._active_decoder is None

    timers, patcher = _capture_timers()
    with patcher:
        zone._async_decoder_state_changed(_dec_event("idle"))
        assert [delay for delay, _ in timers] == [3.0]
        timers[0][1](None)
    await hass.async_block_till_done()
    zone.async_turn_off.assert_called_once()

    zone.async_turn_off.reset_mock()
    with patcher:
        zone._async_decoder_state_changed(_dec_event("off"))
        assert [delay for delay, _ in timers] == [3.0, 3.0]
        timers[1][1](None)
    await hass.async_block_till_done()
    zone.async_turn_off.assert_called_once()


@pytest.mark.asyncio
async def test_stray_room_falls_back_to_the_environment_default_source(hass, mock_gateway):
    """After a restart no routing was reported yet: the configured default stands in."""
    zone = _stray_setup(hass, mock_gateway, options={CONF_SOURCE_DEFAULTS: {"2": 2}})
    zone._attr_source = None
    assert zone._stray_decoder() == "media_player.dec1"

    timers, patcher = _capture_timers()
    with patcher:
        zone._async_decoder_state_changed(_dec_event("off"))
        timers[0][1](None)
    await hass.async_block_till_done()
    zone.async_turn_off.assert_called_once()


@pytest.mark.asyncio
async def test_stray_room_is_left_alone_when_it_hears_no_decoder(hass, mock_gateway):
    """A room on the tuner input, an unknown input, or already off is never touched."""
    zone = _stray_setup(hass, mock_gateway)

    zone._attr_source = "Radio"  # source 1: nothing streams there
    assert zone._stray_decoder() is None
    zone._attr_source = None  # unknown, and no default configured
    assert zone._stray_decoder() is None

    zone._attr_source = "Cambridge"
    zone._attr_state = MediaPlayerState.OFF
    assert zone._stray_decoder() is None

    zone._async_decoder_state_changed(_dec_event("off"))
    await hass.async_block_till_done()
    zone.async_turn_off.assert_not_called()


@pytest.mark.asyncio
async def test_stray_room_keeps_playing_while_the_decoder_plays(hass, mock_gateway):
    """A playing decoder cancels a pending auto-off, stray or not."""
    zone = _stray_setup(hass, mock_gateway)
    zone._attr_source = "Cambridge"
    unsub = MagicMock()
    zone._auto_off_unsub = unsub

    zone._async_decoder_state_changed(_dec_event("playing"))

    unsub.assert_called_once()
    assert zone._auto_off_unsub is None
    zone.async_turn_off.assert_not_called()


@pytest.mark.asyncio
async def test_room_waiting_out_a_group_leave_grace_is_not_a_stray(hass, mock_gateway):
    """A dropped member has its own pending OFF, and may be reclaimed within it."""
    zone = _stray_setup(hass, mock_gateway)
    zone._attr_source = "Cambridge"
    zone._pending_off_task = MagicMock()

    assert zone._stray_decoder() is None
    zone._async_decoder_state_changed(_dec_event("off"))
    await hass.async_block_till_done()
    zone.async_turn_off.assert_not_called()
    zone._pending_off_task = None


def _on_report(zone):
    zone.handle_event(
        MagicMock(
            spec=OWNSoundEvent,
            is_source_event=False,
            where=zone._where,
            is_on=True,
            is_off=False,
            volume=None,
        )
    )


@pytest.mark.asyncio
async def test_room_found_on_at_startup_is_switched_off_when_the_decoder_is_idle(hass, mock_gateway):
    """Status hydration finds an amplifier on with nothing playing: no stop event will come."""
    zone = _stray_setup(hass, mock_gateway, options={CONF_SOURCE_DEFAULTS: {"2": 2}})
    zone._attr_state = MediaPlayerState.OFF
    hass.states.async_set("media_player.dec1", "idle")

    timers, patcher = _capture_timers()
    with patcher:
        _on_report(zone)
        assert [delay for delay, _ in timers] == [3.0]
        timers[0][1](None)
        # Only the first report is a startup finding.
        zone._auto_off_unsub = None
        _on_report(zone)
        assert len(timers) == 1
    await hass.async_block_till_done()
    zone.async_turn_off.assert_called_once()


@pytest.mark.asyncio
async def test_room_found_on_at_startup_stays_on_while_the_decoder_plays(hass, mock_gateway):
    """A decoder that is playing (or not reporting yet) is no reason to switch a room off."""
    for decoder_state in ("playing", "buffering", "unavailable", "unknown"):
        zone = _stray_setup(hass, mock_gateway, options={CONF_SOURCE_DEFAULTS: {"2": 2}})
        zone._attr_state = MediaPlayerState.OFF
        hass.states.async_set("media_player.dec1", decoder_state)

        timers, patcher = _capture_timers()
        with patcher:
            _on_report(zone)
        await hass.async_block_till_done()
        assert timers == [], decoder_state
        zone.async_turn_off.assert_not_called()

    # No state at all for the decoder yet.
    hass.states.async_remove("media_player.dec1")
    zone = _stray_setup(hass, mock_gateway, options={CONF_SOURCE_DEFAULTS: {"2": 2}})
    zone._attr_state = MediaPlayerState.OFF
    _on_report(zone)
    zone.async_turn_off.assert_not_called()


@pytest.mark.asyncio
async def test_room_found_on_before_its_decoder_reports_goes_off_once_the_decoder_is_idle(hass, mock_gateway):
    """Decoder not reporting at startup: its first real state (idle) still takes the room off."""
    zone = _stray_setup(hass, mock_gateway, options={CONF_SOURCE_DEFAULTS: {"2": 2}})
    zone._attr_state = MediaPlayerState.OFF
    hass.states.async_set("media_player.dec1", "unknown")

    timers, patcher = _capture_timers()
    with patcher:
        _on_report(zone)
        assert timers == []
        zone._async_decoder_state_changed(
            MagicMock(
                data={
                    "entity_id": "media_player.dec1",
                    "old_state": State("media_player.dec1", "unknown"),
                    "new_state": State("media_player.dec1", "idle"),
                }
            )
        )
        assert [delay for delay, _ in timers] == [3.0]
        timers[0][1](None)
    await hass.async_block_till_done()
    zone.async_turn_off.assert_called_once()


@pytest.mark.asyncio
async def test_cancelling_the_auto_off_forgets_its_key_so_the_same_timer_can_be_rearmed(hass, mock_gateway):
    """A cancelled timer must not make an identical re-arm look like a running one."""
    zone = _stray_setup(hass, mock_gateway)
    zone._attr_source = "Cambridge"
    timers, patcher = _capture_timers()
    with patcher:
        zone._async_decoder_state_changed(_dec_event("idle"))
        zone._cancel_auto_off()
        assert zone._auto_off_key is None
        zone._async_decoder_state_changed(_dec_event("idle"))
    assert len(timers) == 2
    assert zone._auto_off_unsub is not None


@pytest.mark.asyncio
async def test_joining_players_cancels_a_stray_room_timer_on_the_new_leader(hass, mock_gateway):
    """A room armed as stray that then leads a group must not be switched off by the old timer."""
    zone = _stray_setup(hass, mock_gateway)
    zone._attr_source = "Cambridge"
    timers, patcher = _capture_timers()
    with patcher:
        zone._async_decoder_state_changed(_dec_event("idle"))
        assert zone._auto_off_unsub is not None
        # Refused early (no matrix routing address) — after the leader was put to work.
        zone._where = "0"
        with pytest.raises(HomeAssistantError):
            await zone.async_join_players(["media_player.other"])
    assert zone._auto_off_unsub is None


@pytest.mark.asyncio
async def test_turning_a_room_on_from_home_assistant_is_not_a_startup_finding(hass, mock_gateway):
    """The ON echo of our own wake sequence must not arm the auto-off."""
    zone = _stray_setup(hass, mock_gateway, options={CONF_SOURCE_DEFAULTS: {"2": 2}})
    zone._attr_state = MediaPlayerState.OFF
    hass.states.async_set("media_player.dec1", "idle")
    _echo_to_zones(mock_gateway, zone._runtime_data)

    timers, patcher = _capture_timers()
    with patcher, patch("asyncio.sleep", return_value=None):
        await zone.async_turn_on()

    assert zone._attr_state == MediaPlayerState.ON
    assert timers == []


@pytest.mark.asyncio
async def test_stray_room_ignores_attribute_only_updates_of_an_idle_decoder(hass, mock_gateway):
    """Spotify Connect straight to the decoder: an idle decoder's volume change is not a stop."""
    zone = _stray_setup(hass, mock_gateway)
    zone._attr_source = "Cambridge"
    same_state = MagicMock(
        data={
            "entity_id": "media_player.dec1",
            "old_state": State("media_player.dec1", "idle", {"volume_level": 0.2}),
            "new_state": State("media_player.dec1", "idle", {"volume_level": 0.3}),
        }
    )

    timers, patcher = _capture_timers()
    with patcher:
        zone._async_decoder_state_changed(same_state)
        assert timers == []

        # The same decoder really stopping is still followed, by a room ...
        stop = MagicMock(
            data={
                "entity_id": "media_player.dec1",
                "old_state": State("media_player.dec1", "playing"),
                "new_state": State("media_player.dec1", "idle"),
            }
        )
        zone._async_decoder_state_changed(stop)
        assert [delay for delay, _ in timers] == [3.0]
        zone._auto_off_unsub = None

        # ... and a pause is followed after a minute, not at once.
        pause = MagicMock(
            data={
                "entity_id": "media_player.dec1",
                "old_state": State("media_player.dec1", "playing"),
                "new_state": State("media_player.dec1", "paused"),
            }
        )
        zone._async_decoder_state_changed(pause)
        assert [delay for delay, _ in timers] == [3.0, 60.0]
    zone.async_turn_off.assert_not_called()


# ── Stray rooms: audit follow-ups ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_zone_asks_the_bus_for_its_own_status_when_added(hass, mock_gateway):
    """Profiles without the collective WHO=16 query (MH200) would leave every room 'off'."""
    zone = _stray_setup(hass, mock_gateway)
    mock_gateway.profile_supports_who = MagicMock(return_value=False)
    mock_gateway.send_status_request.reset_mock()

    await zone.async_added_to_hass()

    mock_gateway.profile_supports_who.assert_called_once_with(16)
    mock_gateway.send_status_request.assert_called_once()
    assert str(mock_gateway.send_status_request.call_args.args[0]) == "*#16*23*5##"


@pytest.mark.asyncio
async def test_zone_leaves_its_status_to_the_collective_sweep_when_there_is_one(hass, mock_gateway):
    """Gateways that send *#16*0*5## at startup get one frame, not one per zone."""
    zone = _stray_setup(hass, mock_gateway)
    mock_gateway.profile_supports_who = MagicMock(return_value=True)
    mock_gateway.send_status_request.reset_mock()

    await zone.async_added_to_hass()

    mock_gateway.send_status_request.assert_not_called()


@pytest.mark.asyncio
async def test_decoder_reporting_late_is_followed_by_its_state_change(hass, mock_gateway):
    """The status reply can beat the decoder's first state: unavailable -> idle still arms it."""
    zone = _stray_setup(hass, mock_gateway, options={CONF_SOURCE_DEFAULTS: {"2": 2}})
    zone._attr_state = MediaPlayerState.OFF
    _on_report(zone)  # no decoder state yet: nothing to decide

    timers, patcher = _capture_timers()
    with patcher:
        zone._async_decoder_state_changed(
            MagicMock(
                data={
                    "entity_id": "media_player.dec1",
                    "old_state": State("media_player.dec1", "unavailable"),
                    "new_state": State("media_player.dec1", "idle"),
                }
            )
        )
    assert [delay for delay, _ in timers] == [3.0]


@pytest.mark.asyncio
async def test_pending_auto_off_is_dropped_when_the_room_moves_to_another_input(hass, mock_gateway):
    """A pause starts a 60 s timer; switching the room to the tuner meanwhile cancels its effect."""
    zone = _stray_setup(hass, mock_gateway)
    zone._attr_source = "Cambridge"

    timers, patcher = _capture_timers()
    with patcher:
        zone._async_decoder_state_changed(_dec_event("paused"))
        assert [delay for delay, _ in timers] == [60.0]
        zone._attr_source = "Radio"  # a wall panel routed the room to source 1
        timers[0][1](None)
    await hass.async_block_till_done()
    zone.async_turn_off.assert_not_called()


@pytest.mark.asyncio
async def test_pending_auto_off_is_cancelled_when_the_room_joins_a_group(hass, mock_gateway):
    """Waking a room (join, play_media, turn_on) drops an auto-off timer from before."""
    zone = _stray_setup(hass, mock_gateway)
    unsub = MagicMock()
    zone._auto_off_unsub = unsub

    with patch("asyncio.sleep", return_value=None):
        await zone._async_wake_zone()

    unsub.assert_called_once()
    assert zone._auto_off_unsub is None


@pytest.mark.asyncio
async def test_startup_check_treats_a_playing_companion_as_playing(hass, mock_gateway):
    """Cambridge playing Spotify Connect natively: hardware entity playing, DLNA companion idle."""
    zone = _stray_setup(hass, mock_gateway, options={CONF_SOURCE_DEFAULTS: {"2": 2}})
    zone._attr_state = MediaPlayerState.OFF
    zone._companion_cache = {"media_player.dec1": "media_player.dec1_dlna"}
    hass.states.async_set("media_player.dec1", "playing")
    hass.states.async_set("media_player.dec1_dlna", "idle")

    timers, patcher = _capture_timers()
    with patcher:
        _on_report(zone)
    assert timers == []

    # Both quiet: paused outranks idle, so the longer timer wins.
    zone._status_seen = False
    hass.states.async_set("media_player.dec1", "idle")
    hass.states.async_set("media_player.dec1_dlna", "paused")
    with patcher:
        _on_report(zone)
    assert [delay for delay, _ in timers] == [60.0]


@pytest.mark.asyncio
async def test_group_member_is_left_to_its_leader(hass, mock_gateway):
    """A member goes off with its leader; it does not run a timer of its own."""
    zone = _stray_setup(hass, mock_gateway, options={CONF_SOURCE_DEFAULTS: {"2": 2}})
    pool = zone._get_pool()
    leader = _create_test_zone(hass, mock_gateway, zone._runtime_data, "36", "media_player.zone36")
    await pool.claim(leader.entity_id)
    await pool.add_member(leader.entity_id, zone.entity_id)
    assert zone._stray_decoder() is None

    zone._async_decoder_state_changed(_dec_event("off"))
    await hass.async_block_till_done()
    zone.async_turn_off.assert_not_called()


@pytest.mark.asyncio
async def test_auto_off_is_not_armed_twice_or_for_a_room_already_off(hass, mock_gateway):
    """One timer per room; nothing is armed for a room that is off or turning off."""
    zone = _stray_setup(hass, mock_gateway)
    zone._attr_source = "Cambridge"

    timers, patcher = _capture_timers()
    with patcher:
        zone._async_decoder_state_changed(_dec_event("idle"))
        zone._async_decoder_state_changed(_dec_event("idle"))  # already pending
        assert len(timers) == 1

        zone._auto_off_unsub = None
        zone._turning_off = True
        zone._arm_auto_off(3.0, "media_player.dec1")
        zone._turning_off = False
        zone._attr_state = MediaPlayerState.OFF
        zone._arm_auto_off(3.0, "media_player.dec1")
        assert len(timers) == 1


@pytest.mark.asyncio
async def test_auto_off_timer_does_nothing_if_the_room_went_off_meanwhile(hass, mock_gateway):
    """A room switched off (or switching off) while the timer runs is not switched off again."""
    zone = _stray_setup(hass, mock_gateway)
    zone._attr_source = "Cambridge"

    timers, patcher = _capture_timers()
    with patcher:
        zone._async_decoder_state_changed(_dec_event("idle"))
        zone._attr_state = MediaPlayerState.OFF
        timers[0][1](None)

        zone._attr_state = MediaPlayerState.ON
        zone._async_decoder_state_changed(_dec_event("idle"))
        zone._turning_off = True
        timers[1][1](None)
        zone._turning_off = False
    await hass.async_block_till_done()
    zone.async_turn_off.assert_not_called()



@pytest.mark.asyncio
async def test_stray_timer_does_not_switch_off_a_room_that_got_its_own_decoder(hass, mock_gateway):
    """Armed as a stray of dec1; the room claims dec2 meanwhile: the old timer is void."""
    zone = _stray_setup(hass, mock_gateway)
    zone._get_pool()._decoder_map["media_player.dec2"] = 1
    zone._attr_source = "Cambridge"

    timers, patcher = _capture_timers()
    with patcher:
        zone._async_decoder_state_changed(_dec_event("idle"))
        zone._active_decoder = "media_player.dec2"  # claimed without a wake
        timers[0][1](None)
    await hass.async_block_till_done()
    zone.async_turn_off.assert_not_called()


@pytest.mark.asyncio
async def test_auto_off_timer_is_replaced_when_the_decoder_or_delay_changes(hass, mock_gateway):
    """Idle A, then moved to idle B, re-arms for B; paused/idle swaps re-arm; the same pair is kept."""
    zone = _stray_setup(hass, mock_gateway)
    zone._get_pool()._decoder_map["media_player.dec2"] = 1
    zone._attr_source = "Cambridge"

    timers, patcher = _capture_timers()
    with patcher:
        zone._arm_auto_off(3.0, "media_player.dec1")
        first = zone._auto_off_unsub
        zone._arm_auto_off(3.0, "media_player.dec1")  # same pair: kept
        assert zone._auto_off_unsub is first
        assert len(timers) == 1

        zone._attr_source = "Radio"  # a wall panel moved the room to dec2's input
        zone._arm_auto_off(3.0, "media_player.dec2")
        first.assert_called_once()  # the dec1 timer is cancelled
        assert len(timers) == 2

        zone._arm_auto_off(60.0, "media_player.dec2")  # idle -> paused
        zone._arm_auto_off(3.0, "media_player.dec2")  # paused -> idle
        assert [delay for delay, _ in timers] == [3.0, 3.0, 60.0, 3.0]

        timers[-1][1](None)
    await hass.async_block_till_done()
    zone.async_turn_off.assert_called_once()


@pytest.mark.asyncio
async def test_buffering_counts_as_playing(hass, mock_gateway):
    """A track change or Spotify Connect handshake passes through buffering: keep the room on."""
    zone = _stray_setup(hass, mock_gateway)
    zone._attr_source = "Cambridge"

    timers, patcher = _capture_timers()
    with patcher:
        zone._async_decoder_state_changed(_dec_event("idle"))
        pending = zone._auto_off_unsub
        zone._async_decoder_state_changed(_dec_event("buffering"))
    pending.assert_called_once()
    assert zone._auto_off_unsub is None

    # The owner path follows the same rule.
    zone._active_decoder = "media_player.dec1"
    with patcher:
        zone._async_decoder_state_changed(_dec_event("idle"))
        pending = zone._auto_off_unsub
        zone._async_decoder_state_changed(_dec_event("buffering"))
    pending.assert_called_once()
    zone.async_turn_off.assert_not_called()


@pytest.mark.asyncio
async def test_unavailable_or_unknown_decoder_arms_nothing(hass, mock_gateway):
    """A decoder integration reloading says nothing about playback."""
    zone = _stray_setup(hass, mock_gateway)
    zone._attr_source = "Cambridge"

    timers, patcher = _capture_timers()
    with patcher:
        zone._async_decoder_state_changed(_dec_event("unavailable"))
        zone._async_decoder_state_changed(_dec_event("unknown"))
    assert timers == []


@pytest.mark.asyncio
async def test_setup_restores_the_books_and_drops_zones_the_bus_never_reports(
    hass, hass_storage, mock_config_entry, mock_gateway
):
    """Groups come back after a restart; a zone that never reports is dropped after the window."""
    key = "myhome.decoder_pool.test_entry_id"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {
            "assignments": {"media_player.squeezelite_1": "media_player.ghost"},
            "groups": {"media_player.ghost": ["media_player.ghost_member"]},
            "environments": {"media_player.ghost": "2"},
        },
    }
    hass.data = {DOMAIN: {mock_config_entry.data[CONF_MAC]: {CONF_ENTITY: mock_gateway}}}

    timers, patcher = _capture_timers()
    with patch("homeassistant.helpers.entity_registry.async_get"), patch(
        "homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]
    ), patcher:
        attach_runtime(hass, mock_config_entry)
        await async_setup_entry(hass, mock_config_entry, MagicMock())

    pool = mock_config_entry.runtime_data.decoder_pool
    assert pool.get_assignment("media_player.ghost") == "media_player.squeezelite_1"
    assert pool.get_members("media_player.ghost") == ["media_player.ghost_member"]
    assert [delay for delay, _ in timers] == [120.0]

    timers[0][1](None)
    await hass.async_block_till_done()
    assert pool.get_assignment("media_player.ghost") is None
    assert pool.get_members("media_player.ghost") == []


@pytest.mark.asyncio
async def test_setup_drops_restored_zones_that_are_no_longer_registered(
    hass, hass_storage, mock_config_entry, mock_gateway
):
    """A zone renamed while Home Assistant was down does not hold its decoder for the confirm window."""
    key = "myhome.decoder_pool.test_entry_id"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {
            "assignments": {"media_player.squeezelite_1": "media_player.old_name"},
            "environments": {"media_player.old_name": "2"},
        },
    }
    hass.data = {DOMAIN: {mock_config_entry.data[CONF_MAC]: {CONF_ENTITY: mock_gateway}}}

    timers, patcher = _capture_timers()
    with patch("homeassistant.helpers.entity_registry.async_get") as get_registry, patch(
        "homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]
    ), patcher:
        get_registry.return_value.async_get.return_value = None
        attach_runtime(hass, mock_config_entry)
        await async_setup_entry(hass, mock_config_entry, MagicMock())

    pool = mock_config_entry.runtime_data.decoder_pool
    assert pool.get_assignment("media_player.old_name") is None
    assert not pool.has_unconfirmed
    assert timers == []  # nothing left to wait for


@pytest.mark.asyncio
async def test_restored_owner_takes_its_claim_back_when_its_amplifier_reports(hass, mock_gateway):
    """The room was playing before the restart; the decoder still plays: nothing to switch off."""
    zone = _stray_setup(hass, mock_gateway)
    zone._attr_state = MediaPlayerState.OFF
    pool = zone._get_pool()
    pool.restore({"assignments": {"media_player.dec1": "media_player.zone23"}})
    hass.states.async_set("media_player.dec1", "playing")

    timers, patcher = _capture_timers()
    with patcher:
        _on_report(zone)

    assert zone._active_decoder == "media_player.dec1"
    assert not pool.has_unconfirmed  # the bus vouched for it
    assert timers == []


@pytest.mark.asyncio
async def test_restored_group_is_visible_to_music_assistant_on_leader_and_member(hass, mock_gateway):
    """group_members is pool-backed: the state written after each ON report carries the restored group."""
    leader = _stray_setup(hass, mock_gateway)
    leader._attr_state = MediaPlayerState.OFF
    pool = leader._get_pool()
    runtime = leader._runtime_data
    member = _create_test_zone(hass, mock_gateway, runtime, "24", "media_player.zone24")
    member._attr_state = MediaPlayerState.OFF
    pool.restore(
        {
            "assignments": {"media_player.dec1": "media_player.zone23"},
            "groups": {"media_player.zone23": ["media_player.zone24"]},
        }
    )
    hass.states.async_set("media_player.dec1", "playing")
    expected = ["media_player.zone23", "media_player.zone24"]

    for zone in (leader, member):
        zone._publish_state = MagicMock()
        _on_report(zone)
        zone._publish_state.assert_called_once()
        assert zone.group_members == expected

    assert leader._active_decoder == "media_player.dec1"
    assert not pool.has_unconfirmed


@pytest.mark.asyncio
async def test_restored_owner_follows_an_idle_decoder_off(hass, mock_gateway):
    """The music stopped while Home Assistant was down: the room owning it goes off, not just strays."""
    zone = _stray_setup(hass, mock_gateway)
    zone._attr_state = MediaPlayerState.OFF
    zone._get_pool().restore({"assignments": {"media_player.dec1": "media_player.zone23"}})
    hass.states.async_set("media_player.dec1", "idle")

    timers, patcher = _capture_timers()
    with patcher:
        _on_report(zone)
        assert [delay for delay, _ in timers] == [3.0]
        timers[0][1](None)
    await hass.async_block_till_done()
    zone.async_turn_off.assert_called_once()


@pytest.mark.asyncio
async def test_restored_zone_found_off_gives_up_its_books(hass, mock_gateway):
    """A stale claim for an amplifier that is off is released by the zone's own turn-off."""
    zone = _stray_setup(hass, mock_gateway)
    zone.async_schedule_update_ha_state = MagicMock()
    pool = zone._get_pool()
    pool.restore({"assignments": {"media_player.dec1": "media_player.zone23"}})
    hass.states.async_set("media_player.dec1", "playing")
    hass.services.async_register("media_player", "media_stop", AsyncMock())

    zone.handle_event(
        MagicMock(spec=OWNSoundEvent, is_source_event=False, where="23", is_on=False, is_off=True, volume=None)
    )
    await hass.async_block_till_done()

    assert not pool.has_unconfirmed
    assert pool.get_assignment("media_player.zone23") is None


@pytest.mark.asyncio
async def test_group_leave_off_is_the_full_turn_off(hass, mock_gateway):
    """After the grace period the room is switched off and cleaned up, not just sent an OFF frame."""
    zone = _stray_setup(hass, mock_gateway)
    zone._attr_state = MediaPlayerState.ON
    zone._async_handle_turn_off = AsyncMock()
    zone.async_write_ha_state = MagicMock()

    with patch("custom_components.myhome.media_player.asyncio.sleep", new=AsyncMock()):
        await zone._async_delayed_off()

    zone._async_handle_turn_off.assert_awaited_once_with(from_bus=False)
    assert zone._pending_off_task is None


@pytest.mark.asyncio
async def test_group_leave_off_skips_the_frame_for_a_room_that_is_already_off(hass, mock_gateway):
    """A parked room leaving a group is cleaned up without a second OFF frame; a room mid-wake still gets one."""
    zone = _stray_setup(hass, mock_gateway)
    zone._async_handle_turn_off = AsyncMock()
    zone.async_write_ha_state = MagicMock()

    with patch("custom_components.myhome.media_player.asyncio.sleep", new=AsyncMock()):
        zone._attr_state = MediaPlayerState.OFF
        await zone._async_delayed_off()
        zone._async_handle_turn_off.assert_awaited_once_with(from_bus=True)

        zone._async_handle_turn_off.reset_mock()
        zone._wake_pending = True  # its amplifier is about to come on
        await zone._async_delayed_off()
        zone._async_handle_turn_off.assert_awaited_once_with(from_bus=False)


# -- Tests: anti-hiss switch-off that keeps the group ----------------------------


async def _leader_with_member(hass, mock_gateway):
    """Zone 23 leads media_player.zone36 on dec1 (both amplifiers on)."""
    leader = _stray_setup(hass, mock_gateway, options={CONF_SOURCE_DEFAULTS: {"2": 2}})
    member = _create_test_zone(hass, mock_gateway, leader._runtime_data, "36", "media_player.zone36")
    member._attr_state = MediaPlayerState.ON
    pool = leader._get_pool()
    hass.states.async_set("media_player.dec1", "idle")
    await pool.claim(leader.entity_id)
    await pool.add_member(leader.entity_id, member.entity_id)
    leader._active_decoder = "media_player.dec1"
    return leader, member, pool


def _off_frame():
    return MagicMock(where="36", is_source_event=False, is_on=False, is_off=True, volume=None)


@pytest.mark.asyncio
async def test_auto_off_of_a_leader_switches_the_amplifiers_off_but_keeps_the_group(hass, mock_gateway):
    """Hiss protection silences the rooms; the group Music Assistant built stays."""
    leader, member, pool = await _leader_with_member(hass, mock_gateway)

    timers, patcher = _capture_timers()
    with patcher:
        leader._arm_auto_off(60.0, "media_player.dec1")
        timers[0][1](None)
    await hass.async_block_till_done()

    leader.async_turn_off.assert_not_called()
    sent = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert "*16*13*23##" in sent and "*16*13*36##" in sent
    assert leader._attr_state == member._attr_state == MediaPlayerState.OFF
    assert pool.get_members(leader.entity_id) == [member.entity_id]
    assert pool.owned_decoder(leader.entity_id) == "media_player.dec1"
    assert leader.group_members == [leader.entity_id, member.entity_id]


@pytest.mark.asyncio
async def test_the_off_echo_of_a_parked_member_does_not_take_it_out_of_the_group(hass, mock_gateway):
    leader, member, pool = await _leader_with_member(hass, mock_gateway)
    timers, patcher = _capture_timers()
    with patcher:
        leader._arm_auto_off(60.0, "media_player.dec1")
        timers[0][1](None)
    await hass.async_block_till_done()

    member.handle_event(_off_frame())
    await hass.async_block_till_done()

    assert pool.get_leader(member.entity_id) == leader.entity_id


@pytest.mark.asyncio
async def test_resuming_a_parked_leader_wakes_the_whole_group(hass, mock_gateway):
    leader, member, pool = await _leader_with_member(hass, mock_gateway)
    timers, patcher = _capture_timers()
    with patcher:
        leader._arm_auto_off(60.0, "media_player.dec1")
        timers[0][1](None)
    await hass.async_block_till_done()
    mock_gateway.send.reset_mock()

    leader._forward_to_decoder = AsyncMock()
    with patch("asyncio.sleep", return_value=None):
        await leader.async_media_play()

    sent = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert "*16*3*23##" in sent and "*16*3*36##" in sent
    assert not leader._parked and not member._parked
    leader._forward_to_decoder.assert_awaited_once_with("media_play")


@pytest.mark.asyncio
async def test_resuming_a_parked_group_starts_the_stream_before_the_members_wake(hass, mock_gateway):
    """Only the leader's frames are in front of the stream; the members follow next to it."""
    leader, member, pool = await _leader_with_member(hass, mock_gateway)
    await leader._async_park_group()
    mock_gateway.send.reset_mock()

    async def _slow_send(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(0)  # the real command worker waits for the gateway's ACK

    mock_gateway.send.side_effect = _slow_send
    seen_at_play: list[str] = []

    async def _play(service: str) -> None:
        seen_at_play.extend(str(call.args[0]) for call in mock_gateway.send.call_args_list)

    leader._forward_to_decoder = _play
    await leader.async_media_play()

    assert "*16*3*23##" in seen_at_play
    assert "*16*3*36##" not in seen_at_play
    sent = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert "*16*3*36##" in sent
    assert not leader._parked and not member._parked
    assert not member._wake_pending


@pytest.mark.asyncio
async def test_a_failed_resume_of_a_parked_group_stops_the_members_wake(hass, mock_gateway):
    leader, member, pool = await _leader_with_member(hass, mock_gateway)
    await leader._async_park_group()
    mock_gateway.send.reset_mock()

    async def _slow_send(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(0)

    mock_gateway.send.side_effect = _slow_send
    leader._forward_to_decoder = AsyncMock(side_effect=HomeAssistantError("no stream"))

    with pytest.raises(HomeAssistantError):
        await leader.async_media_play()
    await hass.async_block_till_done()

    assert not member._wake_pending
    assert not leader._wake_pending


@pytest.mark.asyncio
async def test_resuming_a_parked_leader_without_members_has_nothing_left_to_wake(hass, mock_gateway):
    leader, member, pool = await _leader_with_member(hass, mock_gateway)
    await pool.remove_group_member(member.entity_id)
    leader._parked = True
    leader._forward_to_decoder = AsyncMock()

    await leader.async_media_play()

    assert not leader._parked
    leader._forward_to_decoder.assert_awaited_once_with("media_play")


@pytest.mark.asyncio
async def test_switching_a_parked_leader_off_yourself_disbands_the_group(hass, mock_gateway):
    leader, member, pool = await _leader_with_member(hass, mock_gateway)
    leader.async_turn_off = MyHOMEMediaPlayer.async_turn_off.__get__(leader)
    timers, patcher = _capture_timers()
    with patcher:
        leader._arm_auto_off(60.0, "media_player.dec1")
        timers[0][1](None)
    await hass.async_block_till_done()

    await leader.async_turn_off()

    assert pool.get_members(leader.entity_id) == []
    assert not leader._parked


@pytest.mark.asyncio
async def test_parked_rooms_do_not_report_off_so_music_assistant_keeps_the_group(hass, mock_gateway):
    """Found live: an "off" leader made Music Assistant dissolve the group 6 ms after the park."""
    leader, member, pool = await _leader_with_member(hass, mock_gateway)
    hass.states.async_set("media_player.dec1", "paused")
    timers, patcher = _capture_timers()
    with patcher:
        leader._arm_auto_off(60.0, "media_player.dec1")
        timers[0][1](None)
    await hass.async_block_till_done()

    assert leader.state == MediaPlayerState.PAUSED
    assert member.state == MediaPlayerState.PAUSED
    hass.states.async_set("media_player.dec1", "idle")
    assert leader.state == MediaPlayerState.IDLE

    # a real turn-off (or a wall-panel OFF) is off again
    leader.async_turn_off = MyHOMEMediaPlayer.async_turn_off.__get__(leader)
    await leader.async_turn_off()
    assert leader.state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_members_of_a_parked_group_say_on_before_their_slow_wake_frames_go_out(hass, mock_gateway):
    """Music Assistant dropped a member that still reported paused when the leader came on."""
    leader, member, pool = await _leader_with_member(hass, mock_gateway)
    timers, patcher = _capture_timers()
    with patcher:
        leader._arm_auto_off(60.0, "media_player.dec1")
        timers[0][1](None)
    await hass.async_block_till_done()
    assert member.state == MediaPlayerState.IDLE

    leader._begin_wake_of_parked_group(pool)
    assert leader.state == MediaPlayerState.ON
    assert member.state == MediaPlayerState.ON
    assert member._parked  # the amplifier is still off: the wake frames follow

    mock_gateway.send.reset_mock()
    with patch("asyncio.sleep", return_value=None):
        await member._async_wake_zone()
    sent = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert sent == ["*16*13*36##", "*16*3*36##"]
    assert not member._parked and not member._wake_pending
    assert member.state == MediaPlayerState.ON


@pytest.mark.asyncio
async def test_turn_on_of_a_parked_leader_wakes_the_whole_group(hass, mock_gateway):
    leader, member, pool = await _leader_with_member(hass, mock_gateway)
    await leader._async_park_group()
    mock_gateway.send.reset_mock()

    with patch("asyncio.sleep", return_value=None):
        await leader.async_turn_on()

    sent = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert "*16*3*23##" in sent and "*16*3*36##" in sent
    assert not leader._parked and not member._parked


@pytest.mark.asyncio
async def test_parking_without_a_pool_or_while_turning_off_does_nothing(hass, mock_gateway):
    leader, member, pool = await _leader_with_member(hass, mock_gateway)
    mock_gateway.send.reset_mock()

    leader._turning_off = True
    await leader._async_park_group()
    leader._turning_off = False
    leader._runtime_data.decoder_pool = None
    await leader._async_park_group()

    mock_gateway.send.assert_not_called()
    assert not leader._parked


@pytest.mark.asyncio
async def test_a_gateway_error_while_parking_does_not_keep_the_other_rooms_on(hass, mock_gateway):
    leader, member, pool = await _leader_with_member(hass, mock_gateway)
    mock_gateway.send.side_effect = [RuntimeError("gateway busy"), None]

    await leader._async_park_group()

    assert leader._parked and member._parked
    assert leader._attr_state == member._attr_state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_unparking_without_a_pool_just_wakes_the_zone(hass, mock_gateway):
    leader, _member, _pool = await _leader_with_member(hass, mock_gateway)
    leader._parked = True
    leader._runtime_data.decoder_pool = None
    mock_gateway.send.reset_mock()

    with patch("asyncio.sleep", return_value=None):
        await leader._async_unpark_group()

    sent = [str(call.args[0]) for call in mock_gateway.send.call_args_list]
    assert sent == ["*16*13*23##", "*16*3*23##"]


async def _leader_with_members(hass, mock_gateway, count):
    """Return (runtime, pool, zones) with zone1 leading ``count`` playing members."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 1})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", "idle")
    zones = [
        _create_test_zone(hass, mock_gateway, runtime, str(11 * (i + 1)), f"media_player.zone{i + 1}")
        for i in range(count + 1)
    ]
    await pool.claim("media_player.zone1")
    zones[0]._active_decoder = "media_player.streamer"
    await zones[0].async_join_players([f"media_player.zone{i + 2}" for i in range(count)])
    for zone in zones:
        zone._attr_state = MediaPlayerState.ON
        zone._wake_off_sent_at = None
    return runtime, pool, zones


@pytest.mark.asyncio
async def test_leader_off_with_two_members_puts_only_its_own_frame_on_the_wire(hass, mock_gateway):
    """Leader OFF hands on to the first member; the middle room and the decoder are left alone."""
    _runtime, pool, (z1, z2, z3) = await _leader_with_members(hass, mock_gateway, 2)
    mock_gateway.send.reset_mock()

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as mock_service:
        await z1.async_turn_off()
        mock_service.assert_not_called()

    assert [str(c.args[0]) for c in mock_gateway.send.call_args_list] == ["*16*13*11##"]
    assert pool.get_members("media_player.zone2") == ["media_player.zone3"]
    assert z2._active_decoder == "media_player.streamer"
    assert z3._attr_state == MediaPlayerState.ON
    assert z1._attr_state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_last_room_off_still_stops_and_releases_the_decoder(hass, mock_gateway):
    """After the hand-over the new leader has no members: its OFF ends the stream."""
    _runtime, pool, (z1, z2) = await _leader_with_members(hass, mock_gateway, 1)
    await z1.async_turn_off()

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as mock_service:
        await z2.async_turn_off()
        mock_service.assert_called_with("media_player", "media_stop", {"entity_id": "media_player.streamer"})
    assert pool.get_assignment("media_player.zone2") is None
    assert z2._active_decoder is None


@pytest.mark.asyncio
async def test_bus_off_of_the_leader_sends_no_second_frame(hass, mock_gateway):
    """A wall-panel OFF already is the frame on the wire: the hand-over must not repeat it."""
    _runtime, pool, (z1, z2) = await _leader_with_members(hass, mock_gateway, 1)
    mock_gateway.send.reset_mock()

    await z1._async_handle_turn_off(from_bus=True)

    assert mock_gateway.send.call_count == 0
    assert pool.get_assignment("media_player.zone2") == "media_player.streamer"
    assert z1._attr_state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_parked_leader_off_disbands_the_group(hass, mock_gateway):
    """A parked group is silent already; turning its leader off dissolves it instead of handing it on."""
    _runtime, pool, (z1, z2) = await _leader_with_members(hass, mock_gateway, 1)
    z1._parked = True
    z2._parked = True

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
        await z1.async_turn_off()

    assert pool.get_members("media_player.zone1") == []
    assert pool.get_assignment("media_player.zone2") is None
    assert z2._attr_state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_hand_over_that_finds_the_group_already_moved_changes_nothing(hass, mock_gateway):
    """A second OFF that loses the race must not blank the decoder of the room that took over."""
    _runtime, pool, (z1, z2) = await _leader_with_members(hass, mock_gateway, 1)
    z2._active_decoder = "media_player.streamer"
    await z1.async_turn_off()
    z1._attr_state = MediaPlayerState.ON
    z1._active_decoder = "media_player.streamer"
    mock_gateway.send.reset_mock()

    with patch.object(pool, "transfer_leadership", AsyncMock(return_value=None)):
        await z1._async_hand_over_leadership(pool, ["media_player.zone2"])

    assert mock_gateway.send.call_count == 0
    assert z1._active_decoder == "media_player.streamer"
    assert z1._turning_off is False


@pytest.mark.asyncio
async def test_hand_over_ignores_a_second_off_while_the_first_is_handing_over(hass, mock_gateway):
    """HA's turn_off and the bus echo can overlap: the second one must not start another hand-over."""
    _runtime, pool, (z1, z2) = await _leader_with_members(hass, mock_gateway, 1)
    z1._turning_off = True
    mock_gateway.send.reset_mock()

    with patch.object(pool, "transfer_leadership", AsyncMock()) as transfer:
        await z1._async_hand_over_leadership(pool, ["media_player.zone2"])

    transfer.assert_not_called()
    assert mock_gateway.send.call_count == 0


@pytest.mark.asyncio
async def test_parked_leader_off_still_disbands_when_a_member_frame_fails(hass, mock_gateway):
    """A member that does not take its OFF frame must not keep the group from being released."""
    _runtime, pool, (z1, z2) = await _leader_with_members(hass, mock_gateway, 1)
    z1._parked = True
    z2._parked = True
    mock_gateway.send.side_effect = [None, OSError("bus down")]

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock):
        await z1.async_turn_off()

    assert pool.get_members("media_player.zone1") == []
    assert z2._attr_state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_layer_hooks_are_owned_by_the_layer_above_and_fail_clearly_without_it(player):
    """The hooks a lower layer calls are declared on ZoneBase and overridden further up.

    Calling one on a layer that does not own it raises NotImplementedError rather
    than an AttributeError, and the real entity never falls back to the base stub.
    """
    from custom_components.myhome.media_player_zone import ZoneBase

    hooks = (
        "_async_park_group",
        "_async_wake_zone",
        "_async_handle_turn_off",
        "_async_auto_join_active_stream",
    )
    for name in hooks:
        assert getattr(MyHOMEMediaPlayer, name) is not getattr(ZoneBase, name), name

    with pytest.raises(NotImplementedError):
        await ZoneBase._async_park_group(player)
    with pytest.raises(NotImplementedError):
        await ZoneBase._async_wake_zone(player)
    with pytest.raises(NotImplementedError):
        await ZoneBase._async_handle_turn_off(player)
    with pytest.raises(NotImplementedError):
        await ZoneBase._async_auto_join_active_stream(player)


# ── Automatic streaming group synchronization tests ──────────────────────────


async def _setup_streaming_environment(hass, mock_gateway, options=None):
    """Set up a streaming group environment with a leader on Source 2 and a member in Env 3."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", MediaPlayerState.IDLE)

    base_options = {
        CONF_SOURCE_NAME.format(1): "Radio",
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_DEFAULTS: {"3": 2},
    }
    if options:
        base_options.update(options)

    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    leader = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_room")
    leader._options = lambda: dict(base_options)
    leader._gateway_handler.config_entry.options = dict(base_options)
    leader._attr_state = MediaPlayerState.ON
    leader._attr_source = "Cambridge"
    await pool.claim("media_player.living_room", preferred_source=2)
    leader._active_decoder = "media_player.streamer"
    hass.states.async_set("media_player.streamer", MediaPlayerState.PLAYING)

    zone36 = _create_test_zone(hass, mock_gateway, runtime, "36", "media_player.audio_zone_36")
    zone36._options = lambda: dict(base_options)
    zone36._gateway_handler.config_entry.options = dict(base_options)
    zone36._track_decoders()

    return runtime, pool, leader, zone36


@pytest.mark.asyncio
async def test_wall_button_power_on_auto_joins_active_streaming_group(hass, mock_gateway):
    """Scenario 1: Living room streaming on Source 2. Zone 36 sends power ON and auto-joins."""
    _runtime, pool, leader, zone36 = await _setup_streaming_environment(hass, mock_gateway)
    assert zone36.state == MediaPlayerState.OFF

    msg = OWNSoundEvent.parse("*16*3*36##")
    zone36.handle_event(msg)
    await hass.async_block_till_done()

    assert leader.group_members == ["media_player.living_room", "media_player.audio_zone_36"]
    assert zone36.group_members == ["media_player.living_room", "media_player.audio_zone_36"]
    assert zone36.state == MediaPlayerState.PLAYING
    assert zone36._attr_source == "Cambridge"


@pytest.mark.asyncio
async def test_volume_status_report_when_off_updates_level_without_waking(hass, mock_gateway):
    """Scenario 2: Zone 36 initially OFF receives Dimension 1 volume report, updates volume without waking or joining."""
    _runtime, pool, leader, zone36 = await _setup_streaming_environment(hass, mock_gateway)
    assert zone36.state == MediaPlayerState.OFF

    msg = OWNSoundEvent.parse("*#16*36*1*20##")
    zone36.handle_event(msg)
    await hass.async_block_till_done()

    assert zone36._attr_state == MediaPlayerState.OFF
    assert zone36.volume_level == pytest.approx(20 / 31.0)
    assert pool.get_leader("media_player.audio_zone_36") is None
    assert leader.group_members is None
    assert zone36.state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_wall_button_relative_volume_step_powers_on_and_auto_joins(hass, mock_gateway):
    """Scenario 3: Zone 36 initially OFF receives relative volume step (1001), wakes to ON and auto-joins."""
    _runtime, pool, leader, zone36 = await _setup_streaming_environment(hass, mock_gateway)
    assert zone36.state == MediaPlayerState.OFF

    msg = OWNSoundEvent.parse("*16*1001*36##")
    zone36.handle_event(msg)
    await hass.async_block_till_done()

    assert zone36._attr_state == MediaPlayerState.ON
    assert leader.group_members == ["media_player.living_room", "media_player.audio_zone_36"]
    assert zone36.state == MediaPlayerState.PLAYING


@pytest.mark.asyncio
async def test_wall_button_on_tuner_source_does_not_auto_join(hass, mock_gateway):
    """Scenario 4: Zone 36 routed to Tuner (Source 1) powers ON but does not join streaming group."""
    _runtime, pool, leader, zone36 = await _setup_streaming_environment(
        hass,
        mock_gateway,
        options={
            CONF_SOURCE_DEFAULTS: {"3": 1},
            CONF_SOURCE_TUNER.format(1): True,
        },
    )
    zone36._attr_source = "Radio"
    assert zone36.state == MediaPlayerState.OFF

    msg = OWNSoundEvent.parse("*16*3*36##")
    zone36.handle_event(msg)
    await hass.async_block_till_done()

    assert zone36._attr_state == MediaPlayerState.ON
    assert leader.group_members is None
    assert pool.get_leader("media_player.audio_zone_36") is None


@pytest.mark.asyncio
async def test_wall_panel_source_switch_leaves_and_rejoins_streaming_group(hass, mock_gateway):
    """Scenario 5: Zone 36 switches to Tuner (*16*3*131##) -> drops; switches back (*16*3*132##) -> rejoins."""
    _runtime, pool, leader, zone36 = await _setup_streaming_environment(hass, mock_gateway)

    # First, turn on zone36 so it auto-joins source 2 group
    zone36.handle_event(OWNSoundEvent.parse("*16*3*36##"))
    await hass.async_block_till_done()
    assert leader.group_members == ["media_player.living_room", "media_player.audio_zone_36"]

    # Wall panel switches environment 3 to Tuner (Source 1): *16*3*131##
    zone36.handle_event(OWNSoundEvent.parse("*16*3*131##"))
    await hass.async_block_till_done()

    assert pool.get_leader("media_player.audio_zone_36") is None
    assert leader.group_members is None
    assert zone36._attr_source == "Radio"

    # Wall panel switches environment 3 back to Streamer (Source 2): *16*3*132##
    zone36.handle_event(OWNSoundEvent.parse("*16*3*132##"))
    await hass.async_block_till_done()

    assert leader.group_members == ["media_player.living_room", "media_player.audio_zone_36"]
    assert zone36._attr_source == "Cambridge"
    assert zone36.state == MediaPlayerState.PLAYING


@pytest.mark.asyncio
async def test_wall_button_power_off_leaves_group_cleanly(hass, mock_gateway):
    """Scenario 6: Zone 36 sends *16*13*36## -> leaves group without interrupting leader."""
    _runtime, pool, leader, zone36 = await _setup_streaming_environment(hass, mock_gateway)

    zone36.handle_event(OWNSoundEvent.parse("*16*3*36##"))
    await hass.async_block_till_done()
    assert leader.group_members == ["media_player.living_room", "media_player.audio_zone_36"]

    off_msg = OWNSoundEvent.parse("*16*13*36##")
    zone36.handle_event(off_msg)
    await hass.async_block_till_done()

    assert zone36.state == MediaPlayerState.OFF
    assert pool.get_leader("media_player.audio_zone_36") is None
    assert leader.group_members is None
    assert leader.state == MediaPlayerState.PLAYING
    assert leader._active_decoder == "media_player.streamer"


@pytest.mark.asyncio
async def test_delayed_stream_start_auto_joins_pre_activated_zone(hass, mock_gateway):
    """Scenario 7: Zone 36 turned ON while decoder is IDLE; when decoder transitions to PLAYING, Zone 36 auto-joins."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", MediaPlayerState.IDLE)

    base_options = {
        CONF_SOURCE_NAME.format(1): "Radio",
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_DEFAULTS: {"3": 2},
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))
    leader = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_room")
    leader._options = lambda: dict(base_options)
    leader._gateway_handler.config_entry.options = dict(base_options)
    leader._attr_state = MediaPlayerState.ON
    leader._attr_source = "Cambridge"
    await pool.claim("media_player.living_room", preferred_source=2)
    leader._active_decoder = "media_player.streamer"

    zone36 = _create_test_zone(hass, mock_gateway, runtime, "36", "media_player.audio_zone_36")
    zone36._options = lambda: dict(base_options)
    zone36._gateway_handler.config_entry.options = dict(base_options)
    zone36._track_decoders()

    zone36.handle_event(OWNSoundEvent.parse("*16*3*36##"))
    await hass.async_block_till_done()

    assert pool.get_leader("media_player.audio_zone_36") is None
    assert leader.group_members is None

    old_state = hass.states.get("media_player.streamer")
    hass.states.async_set("media_player.streamer", MediaPlayerState.PLAYING)
    new_state = hass.states.get("media_player.streamer")

    event = Event(
        "state_changed",
        EventStateChangedData(
            entity_id="media_player.streamer",
            old_state=old_state,
            new_state=new_state,
        ),
    )
    zone36._async_decoder_state_changed(event)
    await hass.async_block_till_done()

    assert leader.group_members == ["media_player.living_room", "media_player.audio_zone_36"]
    assert zone36.state == MediaPlayerState.PLAYING


@pytest.mark.asyncio
async def test_auto_join_disabled_via_options(hass, mock_gateway):
    """Scenario 8: When CONF_AUTO_JOIN_STREAMING is False, Zone 36 powers ON without auto-joining."""
    _runtime, pool, leader, zone36 = await _setup_streaming_environment(
        hass, mock_gateway, options={CONF_AUTO_JOIN_STREAMING: False}
    )
    assert zone36.state == MediaPlayerState.OFF

    msg = OWNSoundEvent.parse("*16*3*36##")
    zone36.handle_event(msg)
    await hass.async_block_till_done()

    assert zone36._attr_state == MediaPlayerState.ON
    assert pool.get_leader("media_player.audio_zone_36") is None
    assert leader.group_members is None


@pytest.mark.asyncio
async def test_volume_change_while_already_playing_does_not_retrigger_auto_join(hass, mock_gateway):
    """Volume update while already in group updates volume without redundant auto-join."""
    _runtime, pool, leader, zone36 = await _setup_streaming_environment(hass, mock_gateway)

    # First join the group
    zone36.handle_event(OWNSoundEvent.parse("*16*3*36##"))
    await hass.async_block_till_done()
    assert leader.group_members == ["media_player.living_room", "media_player.audio_zone_36"]

    with patch.object(zone36, "_async_auto_join_active_stream", wraps=zone36._async_auto_join_active_stream) as mock_auto_join:
        # Volume change event arrives on the bus while playing
        zone36.handle_event(OWNSoundEvent.parse("*#16*36*1*25##"))
        await hass.async_block_till_done()

        # Volume updated
        assert zone36.volume_level == pytest.approx(25 / 31.0)
        # Auto-join was NOT called because zone is already ON
        mock_auto_join.assert_not_called()
        assert leader.group_members == ["media_player.living_room", "media_player.audio_zone_36"]


@pytest.mark.asyncio
async def test_volume_down_when_off_does_not_wake_or_join(hass, mock_gateway):
    """Relative volume down (1101) when amplifier is OFF must not wake zone or join group."""
    _runtime, pool, leader, zone36 = await _setup_streaming_environment(hass, mock_gateway)
    assert zone36.state == MediaPlayerState.OFF

    msg = OWNSoundEvent.parse("*16*1101*36##")
    zone36.handle_event(msg)
    await hass.async_block_till_done()

    assert zone36.state == MediaPlayerState.OFF
    assert pool.get_leader("media_player.audio_zone_36") is None
    assert leader.group_members is None


@pytest.mark.asyncio
async def test_volume_zero_when_off_does_not_wake_or_join(hass, mock_gateway):
    """Volume 0 report when amplifier is OFF must not wake zone or join group."""
    _runtime, pool, leader, zone36 = await _setup_streaming_environment(hass, mock_gateway)
    assert zone36.state == MediaPlayerState.OFF

    msg = OWNSoundEvent.parse("*#16*36*1*0##")
    zone36.handle_event(msg)
    await hass.async_block_till_done()

    assert zone36.state == MediaPlayerState.OFF
    assert pool.get_leader("media_player.audio_zone_36") is None
    assert leader.group_members is None


@pytest.mark.asyncio
async def test_rapid_volume_up_burst_is_safe(hass, mock_gateway):
    """Rapid volume up frames sent in quick succession cleanly wake and join without error."""
    _runtime, pool, leader, zone36 = await _setup_streaming_environment(hass, mock_gateway)
    assert zone36.state == MediaPlayerState.OFF

    # Simulate burst of 3 volume-up frames arriving before loop iteration completes
    zone36.handle_event(OWNSoundEvent.parse("*16*1001*36##"))
    zone36.handle_event(OWNSoundEvent.parse("*16*1001*36##"))
    zone36.handle_event(OWNSoundEvent.parse("*16*1001*36##"))
    await hass.async_block_till_done()

    assert zone36._attr_state == MediaPlayerState.ON
    assert leader.group_members == ["media_player.living_room", "media_player.audio_zone_36"]
    assert zone36.state == MediaPlayerState.PLAYING


@pytest.mark.asyncio
async def test_effective_decoder_with_default_source_fallback(hass, mock_gateway):
    """_effective_decoder resolves assigned decoder via _default_source when _attr_source is None."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", MediaPlayerState.PLAYING)

    base_options = {
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_DEFAULTS: {"3": 2},
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    leader = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_room")
    leader._options = lambda: dict(base_options)
    leader._gateway_handler.config_entry.options = dict(base_options)
    await pool.claim("media_player.living_room", preferred_source=2)
    leader._active_decoder = "media_player.streamer"

    zone36 = _create_test_zone(hass, mock_gateway, runtime, "36", "media_player.audio_zone_36")
    zone36._options = lambda: dict(base_options)
    zone36._gateway_handler.config_entry.options = dict(base_options)

    # Add zone36 as member in pool
    await pool.add_member("media_player.living_room", "media_player.audio_zone_36", "3")
    # Simulate post-restart: _attr_source is None
    zone36._attr_source = None
    zone36._attr_state = MediaPlayerState.ON

    # _effective_decoder must find the decoder via default source fallback
    assert zone36._effective_decoder == "media_player.streamer"
    assert zone36.state == MediaPlayerState.PLAYING


@pytest.mark.asyncio
async def test_member_source_switch_to_tuner_only_switches_that_room_and_keeps_group(hass, mock_gateway):
    """When a member switches to Tuner, only that room drops from the group; leader and other members stay playing."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", MediaPlayerState.PLAYING)

    base_options = {
        CONF_SOURCE_NAME.format(1): "Radio",
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_TUNER.format(1): True,
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    leader = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_room")
    mem1 = _create_test_zone(hass, mock_gateway, runtime, "36", "media_player.badkamer")
    mem2 = _create_test_zone(hass, mock_gateway, runtime, "41", "media_player.kitchen")

    for z in (leader, mem1, mem2):
        z._options = lambda: dict(base_options)
        z._gateway_handler.config_entry.options = dict(base_options)
        z._attr_state = MediaPlayerState.ON
        z._attr_source = "Cambridge"

    await pool.claim("media_player.living_room", preferred_source=2)
    leader._active_decoder = "media_player.streamer"
    await leader.async_join_players(["media_player.badkamer", "media_player.kitchen"])

    assert leader.group_members == [
        "media_player.living_room",
        "media_player.badkamer",
        "media_player.kitchen",
    ]

    # Badkamer (env 3) switches source on wall switch to Source 1 (Tuner) -> *16*3*131##
    mem1.handle_event(OWNSoundEvent.parse("*16*3*131##"))
    await hass.async_block_till_done()

    # Badkamer dropped from group, stayed ON on Radio
    assert pool.get_leader("media_player.badkamer") is None
    assert mem1.group_members is None
    assert mem1._attr_source == "Radio"
    assert mem1.state == MediaPlayerState.ON

    # Leader and Kitchen stay grouped on Cambridge and playing
    assert leader.group_members == ["media_player.living_room", "media_player.kitchen"]
    assert leader.state == MediaPlayerState.PLAYING
    assert mem2.state == MediaPlayerState.PLAYING


@pytest.mark.asyncio
async def test_leader_source_switch_to_tuner_transfers_leadership_and_leaves_group(hass, mock_gateway):
    """When a leader switches to Tuner, leadership is transferred to the next member and remaining members keep playing."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", "idle")

    base_options = {
        CONF_SOURCE_NAME.format(1): "Radio",
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_TUNER.format(1): True,
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    leader = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_room")
    mem1 = _create_test_zone(hass, mock_gateway, runtime, "36", "media_player.badkamer")
    mem2 = _create_test_zone(hass, mock_gateway, runtime, "41", "media_player.kitchen")

    for z in (leader, mem1, mem2):
        z._options = lambda: dict(base_options)
        z._gateway_handler.config_entry.options = dict(base_options)
        z._attr_state = MediaPlayerState.ON
        z._attr_source = "Cambridge"

    await pool.claim("media_player.living_room", preferred_source=2)
    hass.states.async_set("media_player.streamer", MediaPlayerState.PLAYING)
    leader._active_decoder = "media_player.streamer"
    await leader.async_join_players(["media_player.badkamer", "media_player.kitchen"])

    # Living room (env 2) switches source on wall switch to Source 1 (Tuner) -> *16*3*121##
    leader.handle_event(OWNSoundEvent.parse("*16*3*121##"))
    await hass.async_block_till_done()

    # Living room handed over group, stayed ON on Radio, no decoder
    assert leader._active_decoder is None
    assert leader.group_members is None
    assert leader._attr_source == "Radio"
    assert leader.state == MediaPlayerState.ON

    # Badkamer is the new leader, holds the decoder, kitchen is still member
    assert mem1._active_decoder == "media_player.streamer"
    assert mem1.group_members == ["media_player.badkamer", "media_player.kitchen"]
    assert mem1.state == MediaPlayerState.PLAYING
    assert mem2.state == MediaPlayerState.PLAYING


@pytest.mark.asyncio
async def test_leader_source_switch_with_same_environment_member(hass, mock_gateway):
    """When leader switches source, members in the same environment also switch to Tuner; other envs transfer leadership."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", "idle")

    base_options = {
        CONF_SOURCE_NAME.format(1): "Radio",
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_TUNER.format(1): True,
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    lr_front = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_front")
    lr_rear = _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.living_rear")
    kitchen = _create_test_zone(hass, mock_gateway, runtime, "41", "media_player.kitchen")

    for z in (lr_front, lr_rear, kitchen):
        z._options = lambda: dict(base_options)
        z._gateway_handler.config_entry.options = dict(base_options)
        z._attr_state = MediaPlayerState.ON
        z._attr_source = "Cambridge"

    await pool.claim("media_player.living_front", preferred_source=2)
    hass.states.async_set("media_player.streamer", MediaPlayerState.PLAYING)
    lr_front._active_decoder = "media_player.streamer"
    await lr_front.async_join_players(["media_player.living_rear", "media_player.kitchen"])

    # Environment 2 routes to Source 1 (Tuner) -> *16*3*121##
    lr_front.handle_event(OWNSoundEvent.parse("*16*3*121##"))
    lr_rear.handle_event(OWNSoundEvent.parse("*16*3*121##"))
    await hass.async_block_till_done()

    # Both living room zones are now ON on Radio, not grouped
    assert lr_front.group_members is None
    assert lr_front._attr_source == "Radio"
    assert lr_front._active_decoder is None
    assert lr_front.state == MediaPlayerState.ON

    assert lr_rear.group_members is None
    assert lr_rear._attr_source == "Radio"
    assert lr_rear.state == MediaPlayerState.ON

    # Kitchen (env 4) took over leadership of the streamer
    assert kitchen._active_decoder == "media_player.streamer"
    assert kitchen.state == MediaPlayerState.PLAYING


@pytest.mark.asyncio
async def test_solo_leader_source_switch_releases_and_stops_decoder(hass, mock_gateway):
    """When a solo playing zone switches to Tuner, decoder is stopped and released; zone stays ON on Tuner."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", MediaPlayerState.PLAYING)

    base_options = {
        CONF_SOURCE_NAME.format(1): "Radio",
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_TUNER.format(1): True,
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    leader = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_room")
    leader._options = lambda: dict(base_options)
    leader._gateway_handler.config_entry.options = dict(base_options)
    leader._attr_state = MediaPlayerState.ON
    leader._attr_source = "Cambridge"

    await pool.claim("media_player.living_room", preferred_source=2)
    leader._active_decoder = "media_player.streamer"

    with patch("homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock) as mock_service:
        leader.handle_event(OWNSoundEvent.parse("*16*3*121##"))
        await hass.async_block_till_done()
        mock_service.assert_called_with(
            "media_player", "media_stop", {"entity_id": "media_player.streamer"}
        )

    assert leader._active_decoder is None
    assert pool.get_assignment("media_player.living_room") is None
    assert leader._attr_source == "Radio"
    assert leader.state == MediaPlayerState.ON


@pytest.mark.asyncio
async def test_handle_event_invalid_what_string(hass, mock_gateway):
    """Event with non-numeric what string is handled gracefully without exception."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    zone = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_room")
    zone._attr_state = MediaPlayerState.OFF

    mock_msg = MagicMock()
    mock_msg.where = "21"
    mock_msg.is_source_event = False
    mock_msg.is_off = False
    mock_msg.is_on = False
    mock_msg.volume = None
    mock_msg.what = "not_a_number"
    mock_msg._what = "not_a_number"

    zone.handle_event(mock_msg)
    assert zone.state == MediaPlayerState.OFF


@pytest.mark.asyncio
async def test_leader_source_change_fallback_to_previous_source(hass, mock_gateway):
    """When leader has no active decoder in pool, expected source falls back to previous source."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool

    base_options = {
        CONF_SOURCE_NAME.format(1): "Radio",
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_TUNER.format(1): True,
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    leader = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_room")
    mem = _create_test_zone(hass, mock_gateway, runtime, "36", "media_player.badkamer")

    for z in (leader, mem):
        z._options = lambda: dict(base_options)
        z._gateway_handler.config_entry.options = dict(base_options)
        z._attr_state = MediaPlayerState.ON
        z._attr_source = "Cambridge"

    await pool.claim("media_player.living_room", preferred_source=2)
    await leader.async_join_players(["media_player.badkamer"])

    # Simulate active decoder being None or not in pool.decoder_source map
    leader._active_decoder = None
    with patch.object(pool, "owned_decoder", return_value=None):
        leader.handle_event(OWNSoundEvent.parse("*16*3*121##"))
        await hass.async_block_till_done()

    assert leader._attr_source == "Radio"
    assert leader.group_members is None


@pytest.mark.asyncio
async def test_solo_leader_source_switch_handles_stop_errors_and_separate_proxy(hass, mock_gateway):
    """Solo leader source change handles exceptions from media_stop and stops companion proxy target."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool

    base_options = {
        CONF_SOURCE_NAME.format(1): "Radio",
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_TUNER.format(1): True,
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    leader = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_room")
    leader._options = lambda: dict(base_options)
    leader._gateway_handler.config_entry.options = dict(base_options)
    leader._attr_state = MediaPlayerState.ON
    leader._attr_source = "Cambridge"

    await pool.claim("media_player.living_room", preferred_source=2)
    leader._active_decoder = "media_player.streamer"

    # Streaming target returns separate proxy player, and media_stop raises on both
    with (
        patch.object(leader, "_streaming_target", return_value="media_player.proxy_companion"),
        patch("homeassistant.core.ServiceRegistry.async_call", side_effect=Exception("stop error")),
    ):
        leader.handle_event(OWNSoundEvent.parse("*16*3*121##"))
        await hass.async_block_till_done()

    assert leader._active_decoder is None
    assert pool.get_assignment("media_player.living_room") is None
    assert leader._attr_source == "Radio"


@pytest.mark.asyncio
async def test_auto_join_reentrancy_and_guards(hass, mock_gateway):
    """Test re-entrancy guard, state guards, and missing source in _async_auto_join_active_stream."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool

    zone = _create_test_zone(hass, mock_gateway, runtime, "36", "media_player.badkamer")

    # 1. Re-entrancy guard: if _auto_joining is True, early return
    zone._auto_joining = True
    await zone._async_auto_join_active_stream()
    assert zone._auto_joining is True
    zone._auto_joining = False

    # 2. State guards: not ON
    zone._attr_state = MediaPlayerState.OFF
    await zone._async_auto_join_active_stream()
    assert zone.group_members is None

    # 3. Source is None and _default_source is None
    zone._attr_state = MediaPlayerState.ON
    zone._attr_source = None
    with patch.object(zone, "_default_source", return_value=None):
        await zone._async_auto_join_active_stream()
    assert zone.group_members is None


@pytest.mark.asyncio
async def test_auto_join_streaming_disabled(hass, mock_gateway):
    """Auto-join is skipped when CONF_AUTO_JOIN_STREAMING is False."""
    from custom_components.myhome.const import CONF_AUTO_JOIN_STREAMING

    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", MediaPlayerState.PLAYING)

    base_options = {
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_AUTO_JOIN_STREAMING: False,
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    leader = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_room")
    zone = _create_test_zone(hass, mock_gateway, runtime, "36", "media_player.badkamer")

    for z in (leader, zone):
        z._options = lambda: dict(base_options)
        z._gateway_handler.config_entry.options = dict(base_options)
        z._attr_state = MediaPlayerState.ON
        z._attr_source = "Cambridge"

    await pool.claim("media_player.living_room", preferred_source=2)
    leader._active_decoder = "media_player.streamer"

    # Zone turns on via bus
    zone._attr_state = MediaPlayerState.OFF
    zone.handle_event(OWNSoundEvent.parse("*16*3*36##"))
    await hass.async_block_till_done()

    assert zone.group_members is None
    assert pool.get_leader("media_player.badkamer") is None


@pytest.mark.asyncio
async def test_auto_join_decoder_not_playing(hass, mock_gateway):
    """Auto-join is skipped when decoder is paused or idle."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", "idle")

    base_options = {
        CONF_SOURCE_NAME.format(2): "Cambridge",
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    leader = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_room")
    zone = _create_test_zone(hass, mock_gateway, runtime, "36", "media_player.badkamer")

    for z in (leader, zone):
        z._options = lambda: dict(base_options)
        z._gateway_handler.config_entry.options = dict(base_options)
        z._attr_state = MediaPlayerState.ON
        z._attr_source = "Cambridge"

    await pool.claim("media_player.living_room", preferred_source=2)
    leader._active_decoder = "media_player.streamer"

    # Decoder is paused: _resolve_playback_state returns PAUSED
    hass.states.async_set("media_player.streamer", MediaPlayerState.PAUSED)
    await zone._async_auto_join_active_stream()

    assert zone.group_members is None
    assert pool.get_leader("media_player.badkamer") is None
    zone._cancel_auto_off()


@pytest.mark.asyncio
async def test_auto_join_leader_not_found_in_runtime(hass, mock_gateway):
    """Auto-join returns cleanly if leader entity is not in runtime.media_players."""
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", MediaPlayerState.PLAYING)

    base_options = {CONF_SOURCE_NAME.format(2): "Cambridge"}
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    zone = _create_test_zone(hass, mock_gateway, runtime, "36", "media_player.badkamer")
    zone._options = lambda: dict(base_options)
    zone._gateway_handler.config_entry.options = dict(base_options)
    zone._attr_state = MediaPlayerState.ON
    zone._attr_source = "Cambridge"

    with patch.object(pool, "get_decoder_owner", return_value="media_player.missing_leader"):
        await zone._async_auto_join_active_stream()

    assert zone.group_members is None


@pytest.mark.asyncio
async def test_auto_join_handles_environment_busy_and_exception(hass, mock_gateway):
    """Auto-join handles EnvironmentBusyError and generic exceptions without crashing."""
    from custom_components.myhome.decoder_pool import EnvironmentBusyError

    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", "idle")

    base_options = {CONF_SOURCE_NAME.format(2): "Cambridge"}
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    leader = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_room")
    zone = _create_test_zone(hass, mock_gateway, runtime, "36", "media_player.badkamer")

    for z in (leader, zone):
        z._options = lambda: dict(base_options)
        z._gateway_handler.config_entry.options = dict(base_options)
        z._attr_state = MediaPlayerState.ON
        z._attr_source = "Cambridge"

    await pool.claim("media_player.living_room", preferred_source=2)
    leader._active_decoder = "media_player.streamer"
    hass.states.async_set("media_player.streamer", MediaPlayerState.PLAYING)

    # 1. EnvironmentBusyError
    with patch.object(pool, "add_member", side_effect=EnvironmentBusyError("3", "media_player.other")):
        await zone._async_auto_join_active_stream()
    assert zone.group_members is None

    # 2. Generic Exception
    with patch.object(pool, "add_member", side_effect=RuntimeError("unexpected")):
        await zone._async_auto_join_active_stream()
    assert zone.group_members is None


@pytest.mark.asyncio
async def test_mute_volume_remembers_previous_volume(hass, player):
    """Muting when volume > 0 records pre-mute volume; unmuting restores it."""
    player._attr_volume_level = 0.8
    player._attr_is_volume_muted = False
    with patch.object(player, "async_set_volume_level", new_callable=AsyncMock) as set_vol:
        await player.async_mute_volume(True)
        set_vol.assert_called_with(0.0)
        assert player._pre_mute_volume == 0.8

        await player.async_mute_volume(False)
        set_vol.assert_called_with(0.8)


@pytest.mark.asyncio
async def test_issue_669_intercom_video_stream_volume_ducking_leaves_amplifiers_off(
    hass, mock_gateway
):
    """#669: Video intercom audio ducking and volume restoration frames must keep OFF zones OFF.

    Authentic frames from issue #669 trace where starting intercom stream broadcasts
    Dimension 1 volume reports (*#16*WHERE*1*1##, *#22*3#AREA#POINT*1*1##) and stream
    stop restores volume (*#16*41*1*6##, *#22*3#4#1*1*6##, etc.).
    Amplifiers must remain MediaPlayerState.OFF across both dialects.
    """
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.streamer": 2})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.streamer", MediaPlayerState.PLAYING)

    base_options = {
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_DEFAULTS: {"3": 2, "4": 2, "5": 2, "6": 2, "7": 2, "8": 2},
    }
    mock_gateway.config_entry = MagicMock(options=dict(base_options))

    # An active streaming leader playing in living room (zone 21)
    leader = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.living_room")
    leader._options = lambda: dict(base_options)
    leader._gateway_handler.config_entry.options = dict(base_options)
    leader._attr_state = MediaPlayerState.ON
    leader._attr_source = "Cambridge"
    await pool.claim("media_player.living_room", preferred_source=2)
    leader._active_decoder = "media_player.streamer"

    # Six amplifier zones that are currently OFF in the house
    zones = {}
    for where in ("31", "41", "51", "61", "71", "81"):
        z = _create_test_zone(hass, mock_gateway, runtime, where, f"media_player.zone_{where}")
        z._options = lambda: dict(base_options)
        z._gateway_handler.config_entry.options = dict(base_options)
        z._attr_state = MediaPlayerState.OFF
        zones[where] = z

    def _target_where(raw: str) -> str:
        """Resolve amplifier WHERE from WHO 16 (*#16*EA*...) or WHO 22 (*#22*3#A#P*...)."""
        if raw.startswith("*#16*"):
            return raw.split("*")[2]
        if raw.startswith("*#22*3#"):
            _, area, point = raw.split("*")[2].split("#")
            return f"{area}{point}"
        raise ValueError(f"Unrecognized frame: {raw}")

    # 1. Video intercom stream starts: matrix ducks all amplifiers to volume 1.
    # The authentic MH202 trace (#669) interleaves WHO 22 status frames (*#22*3#A#P*1*1##)
    # with classic WHO 16 volume reports (*#16*AP*1*1##).
    stream_start_frames = [
        "*#22*3#4#1*1*1##",
        "*#16*41*1*1##",
        "*#22*3#3#1*1*1##",
        "*#16*31*1*1##",
        "*#16*61*1*1##",
        "*#22*3#6#1*1*1##",
        "*#22*3#5#1*1*1##",
        "*#16*51*1*1##",
        "*#16*71*1*1##",
        "*#22*3#7#1*1*1##",
        "*#22*3#8#1*1*1##",
        "*#16*81*1*1##",
    ]
    for raw in stream_start_frames:
        msg = OWNSoundEvent.parse(raw)
        where = _target_where(raw)
        zones[where].handle_event(msg)

    await hass.async_block_till_done()

    # Verify all 6 amplifiers remain OFF, volume level is updated to 1/31, and none joined the stream
    for where, z in zones.items():
        assert z.state == MediaPlayerState.OFF, f"Zone {where} woke to {z.state} on volume ducking"
        assert z.volume_level == pytest.approx(1 / 31.0)
        assert pool.get_leader(z.entity_id) is None

    # Group leader was unaffected
    assert leader.group_members is None

    # 2. Video intercom stream stops: matrix restores volumes (*6*9**## camera off).
    # Interleaved WHO 16 and WHO 22 restoration frames across the zones.
    stream_stop_frames = [
        "*#22*3#5#1*1*0##",
        "*#16*51*1*0##",
        "*#16*31*1*1##",
        "*#22*3#3#1*1*1##",
        "*#16*41*1*6##",
        "*#22*3#4#1*1*6##",
        "*#22*3#6#1*1*7##",
        "*#16*61*1*7##",
    ]
    for raw in stream_stop_frames:
        msg = OWNSoundEvent.parse(raw)
        where = _target_where(raw)
        zones[where].handle_event(msg)

    await hass.async_block_till_done()

    # Verify amplifiers remain OFF with updated restored volume levels
    assert zones["51"].state == MediaPlayerState.OFF
    assert zones["51"].volume_level == pytest.approx(0.0)

    assert zones["31"].state == MediaPlayerState.OFF
    assert zones["31"].volume_level == pytest.approx(1 / 31.0)

    assert zones["41"].state == MediaPlayerState.OFF
    assert zones["41"].volume_level == pytest.approx(6 / 31.0)

    assert zones["61"].state == MediaPlayerState.OFF
    assert zones["61"].volume_level == pytest.approx(7 / 31.0)

    for where, z in zones.items():
        assert z.state == MediaPlayerState.OFF
        assert pool.get_leader(z.entity_id) is None


@pytest.mark.asyncio
async def test_who22_volume_reports_do_not_wake_amplifiers(hass, mock_gateway):
    """Confirm WHO 22 volume status reports do not wake OFF amplifiers or bypass guards.

    In systems with intercom and sound diffusion, ducking and volume restoration frames
    are broadcast in both WHO 16 and WHO 22 dialects.
    - If parsed as OWNSoundEvent and dispatched to an amplifier entity (Area 3, Point 6),
      it updates volume without waking or auto-joining.
    - On the live gateway bus, OWNMessage.parse produces an OWNEvent (WHO=22) which is
      safely ignored by PlatformDiscovery (who="16", event_type=OWNSoundEvent).
    """
    _runtime, pool, leader, zone36 = await _setup_streaming_environment(hass, mock_gateway)
    assert zone36.state == MediaPlayerState.OFF

    # 1. Direct handle_event on amplifier entity with parsed WHO 22 sound event (Area 3, Point 6)
    msg22 = OWNSoundEvent.parse("*#22*3#3#6*1*20##")
    zone36.handle_event(msg22)
    await hass.async_block_till_done()

    assert zone36._attr_state == MediaPlayerState.OFF
    assert zone36.volume_level == pytest.approx(20 / 31.0)
    assert pool.get_leader("media_player.audio_zone_36") is None
    assert leader.group_members is None
    assert zone36.state == MediaPlayerState.OFF

    # 2. Live bus frame parsing via OWNMessage.parse produces an OWNEvent (WHO=22)
    # which is not an OWNSoundEvent, confirming the gateway discovery filtering.
    live_msg = OWNMessage.parse("*#22*3#3#6*1*20##")
    assert not isinstance(live_msg, OWNSoundEvent)


