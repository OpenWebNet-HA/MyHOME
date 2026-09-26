"""Test the WHO=16 tuner source entity.

None of this is verified against a tuner with an antenna: the frames come from
`WHO_16.pdf` v1.0.1, and only the RDS report shape is confirmed by a capture
from a live installation. The tests pin what the integration sends and how it
reads replies, not that a radio obeys.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.media_player import MediaPlayerState, MediaType
from homeassistant.const import CONF_MAC
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNSoundEvent

from custom_components.myhome.const import CONF_SOURCE_NAME, CONF_SOURCE_TUNER
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.media_player import _build_sound_sources, _sound_route_keys
from custom_components.myhome.sound_source import MyHOMESoundSource, rds_text, source_address
from tests.conftest import attach_platform


@pytest.fixture
def mock_gateway():
    gateway = MagicMock()
    gateway.mac = "00:11:22:33:44:55"
    gateway.log_id = "[MYHOME gateway - 192.168.1.5]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    return gateway


@pytest.fixture
def tuner(hass, mock_gateway):
    entity = MyHOMESoundSource(
        hass=hass,
        name="Radio",
        device_id="101#16",
        who="16",
        where="101",
        manufacturer="BTicino",
        model="Audio Source",
        gateway=mock_gateway,
    )
    entity.hass = hass
    entity.entity_id = "media_player.radio"
    entity.async_schedule_update_ha_state = MagicMock()
    entry = MagicMock()
    entry.data = {CONF_MAC: mock_gateway.mac}
    entry.runtime_data = MyHOMERuntimeData(gateway=mock_gateway)
    attach_platform(entity, entry)
    return entity


def _sent(gateway):
    return [str(call.args[0]) for call in gateway.send.call_args_list]


def test_source_address():
    """Source devices live at 101-109."""
    assert source_address(1) == "101"
    assert source_address(4) == "104"


def test_rds_text_decodes_ascii_codes():
    """RDS arrives as eight decimal ASCII codes, not characters."""
    # " Radio 1", the shape captured on a live installation
    assert rds_text(["32", "82", "97", "100", "105", "111", "32", "49"]) == "Radio 1"
    # Control codes are dropped rather than rendered into the title
    assert rds_text(["7", "82", "97", "100", "105", "111"]) == "Radio"
    assert rds_text(["32", "32"]) is None
    assert rds_text([]) is None
    assert rds_text(["not-a-code"]) is None


@pytest.mark.asyncio
async def test_power_and_station_commands(hass, tuner, mock_gateway):
    """Power, station stepping and preset selection use the source address."""
    await tuner.async_turn_on()
    await tuner.async_turn_off()
    await tuner.async_media_next_track()
    await tuner.async_media_previous_track()
    await tuner.async_select_source("Station 3")

    assert _sent(mock_gateway) == [
        "*16*3*101##",
        "*16*13*101##",
        "*16*6001*101##",
        "*16*6101*101##",
        # The station write carries its parameter directly; the report adds a 0
        "*#16*101*#7*3##",
    ]
    assert tuner.source == "Station 3"


@pytest.mark.asyncio
async def test_select_source_rejects_an_unknown_station(hass, tuner, mock_gateway):
    """An unknown label is refused rather than turned into a frame."""
    assert tuner.source_list == [f"Station {n}" for n in range(1, 6)]

    with pytest.raises(HomeAssistantError):
        await tuner.async_select_source("Station 9")
    with pytest.raises(HomeAssistantError):
        await tuner.async_select_source("BNR")
    mock_gateway.send.assert_not_called()


@pytest.mark.asyncio
async def test_seek_commands(hass, tuner, mock_gateway):
    """Hardware seek up and down commands send *16*5000*101## and *16*5100*101##."""
    await tuner.async_seek_up()
    await tuner.async_seek_down()
    assert _sent(mock_gateway) == ["*16*5000*101##", "*16*5100*101##"]


@pytest.mark.asyncio
async def test_set_frequency(hass, tuner, mock_gateway):
    """Frequency is written as six digits in kHz, per the specification's examples."""
    tuner._station = 1
    tuner._attr_source = "Station 1"
    await tuner.async_set_frequency(107.0)
    assert _sent(mock_gateway) == ["*#16*101*#6*0*107000##"]
    assert tuner.extra_state_attributes["frequency"] == 107.0
    assert "station" not in tuner.extra_state_attributes
    assert tuner.source is None


@pytest.mark.asyncio
async def test_set_frequency_outside_the_fm_band_is_refused(hass, tuner, mock_gateway):
    """A frequency that cannot be FM is a mistake, not a command."""
    for value in (10.7, 120.0, 0):
        with pytest.raises(HomeAssistantError):
            await tuner.async_set_frequency(value)
    mock_gateway.send.assert_not_called()


@pytest.mark.asyncio
async def test_play_media_takes_a_station_or_a_frequency(hass, tuner, mock_gateway):
    """A small number is a preset; anything else is read as MHz."""
    await tuner.async_play_media(MediaType.CHANNEL, "2")
    assert _sent(mock_gateway) == ["*#16*101*#7*2##"]

    mock_gateway.send.reset_mock()
    await tuner.async_play_media(MediaType.CHANNEL, "107.5")
    assert _sent(mock_gateway) == ["*#16*101*#6*0*107500##"]

    mock_gateway.send.reset_mock()
    with pytest.raises(HomeAssistantError):
        await tuner.async_play_media(MediaType.CHANNEL, "BNR Nieuwsradio")
    mock_gateway.send.assert_not_called()


@pytest.mark.asyncio
async def test_added_to_hass_asks_for_rds(hass, tuner, mock_gateway):
    """Without WHAT 101 a tuner never reports its RDS text."""
    await tuner.async_added_to_hass()
    assert "*16*101*101##" in _sent(mock_gateway)


@pytest.mark.asyncio
async def test_update_requests_tuning_state(hass, tuner, mock_gateway):
    """An update asks for frequency, station and RDS."""
    await tuner.async_update()
    requested = [str(c.args[0]) for c in mock_gateway.send_status_request.call_args_list]
    assert requested == ["*#16*101*6##", "*#16*101*7##", "*#16*101*8##"]


def test_handle_event_reads_frequency_station_and_rds(hass, tuner):
    """Reports update the tuning state; the report form carries a leading 0."""
    tuner.handle_event(MagicMock(
        spec=OWNSoundEvent, dimension=6, dimension_value=["0", "107000"],
        is_on=False, is_off=False,
    ))
    assert tuner.extra_state_attributes["frequency"] == 107.0

    tuner.handle_event(MagicMock(
        spec=OWNSoundEvent, dimension=7, dimension_value=["0", "4"],
        is_on=False, is_off=False,
    ))
    assert tuner.source == "Station 4"
    assert tuner.extra_state_attributes["station"] == 4

    tuner.handle_event(MagicMock(
        spec=OWNSoundEvent, dimension=8,
        dimension_value=["32", "82", "97", "100", "105", "111", "32", "49"],
        is_on=False, is_off=False,
    ))
    assert tuner.media_title == "Radio 1"

    # A subsequent frequency report to a new frequency clears the stored station
    tuner.handle_event(MagicMock(
        spec=OWNSoundEvent, dimension=6, dimension_value=["0", "96200"],
        is_on=False, is_off=False,
    ))
    assert tuner.extra_state_attributes["frequency"] == 96.2
    assert "station" not in tuner.extra_state_attributes
    assert tuner.source is None


def test_handle_event_ignores_impossible_payloads(hass, tuner):
    """A payload that cannot be a frequency or station is dropped, not stored."""
    for payload in (["0", "45000"], ["0", "200000"], ["0", "not-a-number"]):
        tuner.handle_event(MagicMock(
            spec=OWNSoundEvent, dimension=6, dimension_value=payload,
            is_on=False, is_off=False,
        ))
    assert "frequency" not in tuner.extra_state_attributes

    for payload in (["0", "0"], ["0", "16"], ["0", "x"]):
        tuner.handle_event(MagicMock(
            spec=OWNSoundEvent, dimension=7, dimension_value=payload,
            is_on=False, is_off=False,
        ))
    assert "station" not in tuner.extra_state_attributes
    assert tuner.source is None


def test_handle_event_power_state(hass, tuner):
    """Standby clears the title: a tuner in standby is listening to nothing."""
    tuner.handle_event(MagicMock(
        spec=OWNSoundEvent, dimension=8,
        dimension_value=["32", "82", "97", "100", "105", "111", "32", "49"],
        is_on=False, is_off=False,
    ))
    tuner.handle_event(MagicMock(spec=OWNSoundEvent, dimension=None, dimension_value=[], is_on=True, is_off=False))
    assert tuner.state == MediaPlayerState.ON
    assert tuner.media_content_type == MediaType.CHANNEL
    assert tuner.media_title == "Radio 1"

    tuner.handle_event(MagicMock(spec=OWNSoundEvent, dimension=None, dimension_value=[], is_on=False, is_off=True))
    assert tuner.state == MediaPlayerState.OFF
    assert tuner.media_title is None
    assert tuner.media_content_type is None


def test_build_sound_sources_only_for_declared_tuners(hass, mock_gateway):
    """Only inputs the user marked as tuners get an entity."""
    entry = MagicMock()
    entry.options = {
        CONF_SOURCE_NAME.format(1): "Radio",
        CONF_SOURCE_TUNER.format(1): True,
        CONF_SOURCE_NAME.format(2): "Cambridge",
        CONF_SOURCE_TUNER.format(2): False,
        CONF_SOURCE_TUNER.format(3): True,
    }

    sources = _build_sound_sources(hass, entry, mock_gateway)

    assert [s._where for s in sources] == ["101", "103"]
    assert sources[0].device_info["name"] == "Radio"
    # An unnamed tuner still gets a usable name
    assert sources[1].device_info["name"] == "Audio Source 3"


def test_sound_route_keys_delivers_source_frames(hass):
    """Source frames carry no zone address and would otherwise be dropped."""
    from custom_components.myhome.discovery import Address

    source_event = MagicMock(spec=OWNSoundEvent, is_source_event=True, zone="101")
    assert _sound_route_keys(source_event, None) == ["101#16"]

    zone_event = MagicMock(spec=OWNSoundEvent, is_source_event=False, zone="23")
    assert _sound_route_keys(zone_event, Address("23", key_suffix="#16")) == ["23#16"]

    # Neither a source nor an addressed zone: nothing to deliver to
    assert _sound_route_keys(zone_event, None) == []
    assert _sound_route_keys(MagicMock(spec=OWNSoundEvent, is_source_event=True, zone=""), None) == []


@pytest.mark.asyncio
async def test_setup_entry_adds_and_subscribes_declared_tuners(hass, mock_gateway):
    """A declared tuner exists from setup, without waiting for bus traffic."""
    from homeassistant.const import CONF_MAC as _CONF_MAC

    from custom_components.myhome.media_player import async_setup_entry
    from tests.conftest import attach_runtime

    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {_CONF_MAC: mock_gateway.mac}
    entry.options = {
        CONF_SOURCE_NAME.format(1): "Radio",
        CONF_SOURCE_TUNER.format(1): True,
    }

    added: list = []
    attach_runtime(hass, entry)
    entry.runtime_data.gateway = mock_gateway
    router = entry.runtime_data.router

    with patch("homeassistant.helpers.entity_registry.async_get"), \
         patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]):
        await async_setup_entry(hass, entry, lambda entities, *a, **k: added.extend(entities))

    assert [type(e).__name__ for e in added] == ["MyHOMESoundSource"]
    # Subscribed under its own key, so source frames reach it
    assert router.subscribers("16", "101#16") == 1

    # A source frame now lands on the entity
    tuner = added[0]
    tuner.hass = hass
    tuner.entity_id = "media_player.radio"
    tuner.async_schedule_update_ha_state = MagicMock()
    router.publish("16", ["101#16"], MagicMock(
        spec=OWNSoundEvent, dimension=None, dimension_value=[], is_on=True, is_off=False,
    ))
    assert tuner.state == MediaPlayerState.ON
