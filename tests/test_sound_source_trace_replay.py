"""Replay and integration tests for WHO=16 F500/F500N tuner sound sources.

Validates the MyHOMESoundSource entity against authentic on-wire traces captured
from an MH200N gateway connected to an F500N tuner (PR #427 / comment 5847535313).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.media_player.const import MediaPlayerState, MediaType
from homeassistant.const import CONF_MAC
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNSoundEvent

from custom_components.myhome.const import TUNER_STATION_COUNT
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.sound_source import MyHOMESoundSource
from tests.conftest import attach_platform

FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "traces"
    / "f500_tuner"
    / "myhome_trace_MH200N_f500n_tuner.json"
)

FIXTURE_SEEK_FREQ_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "traces"
    / "f500_tuner"
    / "myhome_trace_MH200N_f500n_tuner_seek_and_frequency.json"
)

FIXTURE_ALL_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "traces"
    / "f500_tuner"
    / "myhome_trace_MH200N_all_2026-09-26T20-00-28.json"
)

FIXTURE_L4561N_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "traces"
    / "l4561n_stereo_control"
    / "myhome_trace_MH200_l4561n_sound_source.json"
)


@pytest.fixture
def mock_gateway():
    """Mock gateway handler for tuner commands."""
    gateway = MagicMock()
    gateway.mac = "00:11:22:33:44:55"
    gateway.log_id = "[MYHOME gateway - 192.168.1.5]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    return gateway


@pytest.fixture
def tuner(hass, mock_gateway):
    """Instantiate a test MyHOMESoundSource entity."""
    entity = MyHOMESoundSource(
        hass=hass,
        name="Living Room Radio",
        device_id="101#16",
        who="16",
        where="101",
        manufacturer="BTicino",
        model="Audio Source",
        gateway=mock_gateway,
    )
    entity.hass = hass
    entity.entity_id = "media_player.living_room_radio"
    entity.async_schedule_update_ha_state = MagicMock()
    entry = MagicMock()
    entry.data = {CONF_MAC: mock_gateway.mac}
    entry.runtime_data = MyHOMERuntimeData(gateway=mock_gateway)
    attach_platform(entity, entry)
    return entity


def test_f500n_authentic_trace_replay(tuner):
    """Replay the authentic MH200N + F500N hardware trace through the entity."""
    assert FIXTURE_PATH.is_file(), f"Missing trace fixture: {FIXTURE_PATH}"

    with open(FIXTURE_PATH, encoding="utf-8") as f:
        trace_data = json.load(f)

    frames = trace_data["frames"]
    assert len(frames) == 13

    # Initial state
    assert tuner.source_list == [f"Station {i}" for i in range(1, TUNER_STATION_COUNT + 1)]
    assert tuner.extra_state_attributes == {}
    assert tuner.media_title is None

    # Step through trace frames
    for frame in frames:
        if frame["direction"] != "rx":
            continue

        raw = frame["raw"]
        who = frame.get("who")

        # The tuner entity subscribes to WHO 16 frames addressed to 101
        if who == "16" and frame.get("where") == "101":
            event = OWNSoundEvent(raw)
            tuner.handle_event(event)

            if raw == "*#16*101*6*0*96200##":
                # 96200 kHz = 96.2 MHz
                assert tuner.extra_state_attributes["frequency"] == 96.2

            elif raw == "*#16*101*7*0*2##":
                assert tuner.extra_state_attributes["station"] == 2
                assert tuner.source == "Station 2"

            elif raw == "*#16*101*8*75*82*79*78*69*72*73*84##":
                # "KRONEHIT" (uppercase RDS)
                assert tuner.media_title == "KRONEHIT"

            elif raw == "*#16*101*8*107*114*111*110*101*104*105*116##":
                # "kronehit" (lowercase RDS dynamic follow-up)
                assert tuner.media_title == "kronehit"

            elif raw == "*#16*101*7*0*3##":
                assert tuner.extra_state_attributes["station"] == 3
                assert tuner.source == "Station 3"


async def test_f500n_extended_presets_and_dynamic_expansion(tuner, mock_gateway):
    """An F500N supports up to 15 stations; receiving or selecting > 5 expands source_list."""
    assert len(tuner.source_list) == 5

    # 1. Bus event reports station 8 (within 1..15)
    event = MagicMock(spec=OWNSoundEvent)
    event.dimension = 7
    event.dimension_value = ["0", "8"]
    event.is_on = False
    event.is_off = False
    tuner.handle_event(event)

    assert tuner.source == "Station 8"
    assert tuner.extra_state_attributes["station"] == 8
    # Dynamic expansion of source_list up to station 8
    assert len(tuner.source_list) == 8
    assert "Station 8" in tuner.source_list
    assert tuner.source_list == [f"Station {i}" for i in range(1, 9)]

    # 2. Selecting station 12 via select_station
    await tuner.async_select_station(12)
    assert str(mock_gateway.send.call_args.args[0]) == "*#16*101*#7*12##"
    assert tuner.source == "Station 12"
    assert len(tuner.source_list) == 12
    assert "Station 12" in tuner.source_list

    # 3. Selecting station 15 via select_source
    await tuner.async_select_source("Station 12")
    assert tuner.source == "Station 12"

    # 4. Station 15 via play_media (CHANNEL)
    # First expand to 15 via bus event
    event.dimension_value = ["0", "15"]
    tuner.handle_event(event)
    assert len(tuner.source_list) == 15

    await tuner.async_play_media(MediaType.CHANNEL, "15")
    assert str(mock_gateway.send.call_args.args[0]) == "*#16*101*#7*15##"
    assert tuner.source == "Station 15"

    # Out of range station (16) raises error on select_station
    with pytest.raises(HomeAssistantError):
        await tuner.async_select_station(16)


async def test_f500n_seek_and_frequency_trace_replay(tuner, mock_gateway):
    """Replay direct frequency tuning, seek up/down, RDS blanking and dynamic station title."""
    assert FIXTURE_SEEK_FREQ_PATH.is_file(), f"Missing trace fixture: {FIXTURE_SEEK_FREQ_PATH}"

    with open(FIXTURE_SEEK_FREQ_PATH, encoding="utf-8") as f:
        trace_data = json.load(f)

    frames = trace_data["frames"]
    assert len(frames) == 23

    # Initially at Station 1
    tuner.handle_event(MagicMock(
        spec=OWNSoundEvent, dimension=6, dimension_value=["0", "88800"],
        is_on=False, is_off=False,
    ))
    tuner.handle_event(MagicMock(
        spec=OWNSoundEvent, dimension=7, dimension_value=["0", "1"],
        is_on=False, is_off=False,
    ))
    assert tuner.extra_state_attributes["station"] == 1
    assert tuner.source == "Station 1"
    assert tuner.extra_state_attributes["frequency"] == 88.8

    for frame in frames:
        raw = frame["raw"]
        who = frame.get("who")

        if who == "16" and frame.get("where") == "101" and frame["direction"] == "rx":
            event = OWNSoundEvent(raw)
            tuner.handle_event(event)

            if raw == "*#16*101*6*0*96200##":
                # Direct frequency write to 96.2 MHz clears station preset
                assert tuner.extra_state_attributes["frequency"] == 96.2
                assert "station" not in tuner.extra_state_attributes
                assert tuner.source is None

            elif raw == "*#16*101*8*32*32*32*32*32*32*32*32##":
                # Blank RDS transition frame
                assert tuner.media_title is None

            elif raw == "*#16*101*8*107*114*111*110*101*104*105*116##":
                # "kronehit"
                assert tuner.media_title == "kronehit"

            elif raw == "*#16*101*6*0*89500##":
                # Seek up locked on 89.5 MHz (unstored)
                assert tuner.extra_state_attributes["frequency"] == 89.5
                assert "station" not in tuner.extra_state_attributes
                assert tuner.source is None

            elif raw == "*#16*101*8*66*97*121*101*114*110*32*50##":
                # "Bayern 2"
                assert tuner.media_title == "Bayern 2"

            elif raw == "*#16*101*7*0*1##":
                # Seek down locked on 88.8 MHz (stored as station 1)
                assert tuner.extra_state_attributes["station"] == 1
                assert tuner.source == "Station 1"

            elif raw == "*#16*101*8*32*32*79*69*32*51*32*32##":
                # "  OE 3  " stripped to "OE 3"
                assert tuner.media_title == "OE 3"

    # Test seek up and seek down command methods
    await tuner.async_seek_up()
    assert str(mock_gateway.send.call_args.args[0]) == "*16*5000*101##"

    await tuner.async_seek_down()
    assert str(mock_gateway.send.call_args.args[0]) == "*16*5100*101##"


def test_f500n_station_advance_and_rds_rotation_replay(tuner):
    """Replay authentic MH200N + F500N trace covering RDS rotation, 12-code glitch, and advance.

    Contributed by @manfredgittmaier-afk on PR #427 / comment 5849429368.
    """
    assert FIXTURE_ALL_PATH.is_file(), f"Missing trace fixture: {FIXTURE_ALL_PATH}"

    with open(FIXTURE_ALL_PATH, encoding="utf-8") as f:
        trace_data = json.load(f)

    frames = trace_data["frames"]

    for frame in frames:
        if frame.get("direction") != "rx" or frame.get("who") != "16" or frame.get("where") != "101":
            continue

        raw = frame["raw"]
        event = OWNSoundEvent(raw)
        tuner.handle_event(event)

        if raw == "*#16*101*8*42*82*65*68*73*79*42*32##":
            # Dynamic RDS title "*RADIO*"
            assert tuner.media_title == "*RADIO*"

        elif raw == "*#16*101*8*42*42*79*79*69*42*42*32##":
            # Dynamic RDS title alternate "**OOE**"
            assert tuner.media_title == "**OOE**"

        elif raw == "*#16*101*8*42*42*79*79*69*42*79*79*69*42*42*32##":
            # Malformed 12-code frame ignored; title remains previous clean title
            assert tuner.media_title in ("*RADIO*", "**OOE**")

        elif raw == "*#16*101*7*0*1##":
            assert tuner.extra_state_attributes["frequency"] == 88.8
            assert tuner.extra_state_attributes["station"] == 1
            assert tuner.source == "Station 1"

        elif raw == "*#16*101*7*0*2##":
            assert tuner.extra_state_attributes["frequency"] == 96.2
            assert tuner.extra_state_attributes["station"] == 2
            assert tuner.source == "Station 2"

        elif raw == "*#16*101*7*0*3##":
            assert tuner.extra_state_attributes["frequency"] == 90.3
            assert tuner.extra_state_attributes["station"] == 3
            assert tuner.source == "Station 3"

        elif raw == "*#16*101*7*0*4##":
            assert tuner.extra_state_attributes["frequency"] == 103.5
            assert tuner.extra_state_attributes["station"] == 4
            assert tuner.source == "Station 4"

        elif raw == "*#16*101*8*65*78*84*69*78*78*69*32##":
            # "ANTENNE "
            assert tuner.media_title == "ANTENNE"


@pytest.fixture
def l4561n_source(hass, mock_gateway):
    """Instantiate a test MyHOMESoundSource entity for L4561N Stereo Control."""
    entity = MyHOMESoundSource(
        hass=hass,
        name="Stereo Control Source 2",
        device_id="102#16",
        who="16",
        where="102",
        manufacturer="BTicino",
        model="L4561N Stereo Control",
        gateway=mock_gateway,
    )
    entity.hass = hass
    entity.entity_id = "media_player.stereo_control_source_2"
    entity.async_schedule_update_ha_state = MagicMock()
    entry = MagicMock()
    entry.data = {CONF_MAC: mock_gateway.mac}
    entry.runtime_data = MyHOMERuntimeData(gateway=mock_gateway)
    attach_platform(entity, entry)
    return entity


async def test_l4561n_authentic_trace_replay(l4561n_source, mock_gateway):
    """Replay authentic MH200 + L4561N stereo control trace through the entity."""
    assert FIXTURE_L4561N_PATH.is_file(), f"Missing trace fixture: {FIXTURE_L4561N_PATH}"

    with open(FIXTURE_L4561N_PATH, encoding="utf-8") as f:
        trace_data = json.load(f)

    # Verify captured trace ground-truth metadata
    assert trace_data["gateway"]["model"] == "MH200"
    assert trace_data["gateway"]["firmware"] == "2.0.32"
    assert trace_data["target_device"]["model"] == "L4561N"
    assert trace_data["target_device"]["address"] == "102"
    assert trace_data["target_device"]["firmware_version"] == "04.00.06"
    assert trace_data["capture"]["kind"] == "trace"
    frames = trace_data["frames"]
    assert len(frames) == 44

    # Initial entity state
    assert l4561n_source.state is None
    assert l4561n_source.source_list == [f"Station {i}" for i in range(1, TUNER_STATION_COUNT + 1)]

    # Step through trace frames
    for frame in frames:
        if frame.get("direction") != "rx":
            continue

        raw = frame["raw"]
        who = frame.get("who")
        where = frame.get("where")

        # Route frames to L4561N (address 102)
        if who == "16" and where == "102":
            event = OWNSoundEvent(raw)
            l4561n_source.handle_event(event)

            if raw == "*16*3*102##":
                assert l4561n_source.state == MediaPlayerState.ON

    # Verify transport command execution produces exact on-wire OpenWebNet telegrams
    await l4561n_source.async_media_next_track()
    assert str(mock_gateway.send.call_args.args[0]) == "*16*6001*102##"

    await l4561n_source.async_media_previous_track()
    assert str(mock_gateway.send.call_args.args[0]) == "*16*6101*102##"

    await l4561n_source.async_turn_off()
    assert str(mock_gateway.send.call_args.args[0]) == "*16*13*102##"

    await l4561n_source.async_turn_on()
    assert str(mock_gateway.send.call_args.args[0]) == "*16*3*102##"


async def test_l4561n_physical_wall_stepping_and_transport(l4561n_source, mock_gateway):
    """Test multi-step pulse tracking (+1..+5, -1..-4) and direct preset selection."""
    # Select baseline station 1
    await l4561n_source.async_select_station(1)
    assert str(mock_gateway.send.call_args.args[0]) == "*#16*102*#7*1##"
    assert l4561n_source.source == "Station 1"
    assert l4561n_source.extra_state_attributes["station"] == 1

    # Multi-step pulses from authentic physical trace
    # +1 step: *16*6001*102## -> Station 2
    l4561n_source.handle_event(OWNSoundEvent("*16*6001*102##"))
    assert l4561n_source.source == "Station 2"
    assert l4561n_source.extra_state_attributes["station"] == 2

    # +3 step: *16*6003*102## -> Station 5
    l4561n_source.handle_event(OWNSoundEvent("*16*6003*102##"))
    assert l4561n_source.source == "Station 5"
    assert l4561n_source.extra_state_attributes["station"] == 5

    # -2 step: *16*6102*102## -> Station 3
    l4561n_source.handle_event(OWNSoundEvent("*16*6102*102##"))
    assert l4561n_source.source == "Station 3"
    assert l4561n_source.extra_state_attributes["station"] == 3

    # -4 step: *16*6104*102## -> Clamped to min 1
    l4561n_source.handle_event(OWNSoundEvent("*16*6104*102##"))
    assert l4561n_source.source == "Station 1"
    assert l4561n_source.extra_state_attributes["station"] == 1

    # Direct preset jump to Station 5
    await l4561n_source.async_select_station(5)
    assert str(mock_gateway.send.call_args.args[0]) == "*#16*102*#7*5##"
    assert l4561n_source.source == "Station 5"



