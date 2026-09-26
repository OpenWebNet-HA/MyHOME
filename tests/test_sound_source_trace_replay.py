"""Replay and integration tests for WHO=16 F500/F500N tuner sound sources.

Validates the MyHOMESoundSource entity against authentic on-wire traces captured
from an MH200N gateway connected to an F500N tuner (PR #427 / comment 5847535313).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.media_player.const import MediaType
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
        dimension = frame.get("dimension")
        values = frame.get("dimension_values", [])

        # The tuner entity subscribes to WHO 16 frames addressed to 101
        if who == "16" and frame.get("where") == "101":
            event = MagicMock(spec=OWNSoundEvent)
            event.raw = raw
            event.who = "16"
            event.where = "101"
            event.dimension = int(dimension) if dimension is not None else None
            event.dimension_value = values
            event.is_on = False
            event.is_off = False
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
        dimension = frame.get("dimension")
        values = frame.get("dimension_values", [])

        if who == "16" and frame.get("where") == "101" and frame["direction"] == "rx":
            event = MagicMock(spec=OWNSoundEvent)
            event.raw = raw
            event.who = "16"
            event.where = "101"
            event.dimension = int(dimension) if dimension is not None else None
            event.dimension_value = values
            event.is_on = False
            event.is_off = False
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

