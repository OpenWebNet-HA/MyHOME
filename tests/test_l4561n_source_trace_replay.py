"""Replay the MH200 + L4561N trace of 2026-10-07 (source 102, zone 21).

The trace is filtered on WHO 16 and WHERE 102/21, so it holds no ACK/NACK:
it shows which frames went out on a real plant and what the bus reported
back, not whether the gateway accepted them. See the fixture's README.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import CONF_MAC
from OWNd.message import OWNSoundEvent

from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.media_player import MyHOMEMediaPlayer
from custom_components.myhome.sound_source import MyHOMESoundSource
from tests.conftest import attach_platform

FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "traces"
    / "mh200_l4561n_source"
    / "myhome_trace_MH200_l4561n_sound_source.json"
)


def _frames(direction: str, where: str | None = None) -> list[str]:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        frames = json.load(f)["frames"]
    return [
        frame["raw"]
        for frame in frames
        if frame["direction"] == direction and (where is None or frame.get("where") == where)
    ]


def _sent(gateway: MagicMock) -> list[str]:
    return [str(call.args[0]) for call in gateway.send.call_args_list]


@pytest.fixture
def mock_gateway():
    gateway = MagicMock()
    gateway.mac = "00:11:22:33:44:55"
    gateway.log_id = "[MYHOME gateway - 192.168.1.5]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    return gateway


def _runtime_entry(gateway: MagicMock) -> MagicMock:
    entry = MagicMock()
    entry.data = {CONF_MAC: gateway.mac}
    entry.runtime_data = MyHOMERuntimeData(gateway=gateway)
    return entry


@pytest.fixture
def source(hass, mock_gateway):
    entity = MyHOMESoundSource(
        hass=hass,
        name="Stereo Control",
        device_id="102#16",
        who="16",
        where="102",
        manufacturer="BTicino",
        model="L4561N",
        gateway=mock_gateway,
    )
    entity.hass = hass
    entity.entity_id = "media_player.stereo_control"
    entity.async_schedule_update_ha_state = MagicMock()
    attach_platform(entity, _runtime_entry(mock_gateway))
    return entity


@pytest.fixture
def zone(hass, mock_gateway):
    player = MyHOMEMediaPlayer(
        hass=hass,
        name="Audio Zone 21",
        entity_name=None,
        device_id="21#16",
        who="16",
        where="21",
        manufacturer="BTicino",
        model="Audio System",
        gateway=mock_gateway,
    )
    player.hass = hass
    player.entity_id = "media_player.audio_zone_21"
    attach_platform(player, _runtime_entry(mock_gateway))
    return player


def test_trace_shape():
    """The fixture is the 44-frame recording described in its README."""
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        trace = json.load(f)
    assert trace["gateway"]["model"] == "MH200"
    assert trace["gateway"]["firmware"] == "2.0.32"
    assert len(trace["frames"]) == 44
    assert len(_frames("tx")) == 30
    assert len(_frames("rx")) == 14


async def test_source_commands_are_the_frames_sent_on_the_plant(source, mock_gateway):
    """Every command the source entity builds went out on the live bus as-is."""
    await source.async_turn_on()
    await source.async_turn_off()
    await source.async_media_next_track()
    await source.async_media_previous_track()
    for station in (1, 2, 5):
        await source.async_select_station(station)

    sent_on_plant = set(_frames("tx", "102"))
    assert _sent(mock_gateway) == [
        "*16*3*102##",
        "*16*13*102##",
        "*16*6001*102##",
        "*16*6101*102##",
        "*#16*102*#7*1##",
        "*#16*102*#7*2##",
        "*#16*102*#7*5##",
    ]
    assert set(_sent(mock_gateway)) <= sent_on_plant


def test_source_102_reports_on_throughout(source):
    """Source 102 only ever answered `*16*3*102##`, even after the off commands."""
    replies = _frames("rx", "102")
    assert replies and set(replies) == {"*16*3*102##"}

    for raw in replies:
        source.handle_event(OWNSoundEvent(raw))
        assert source.state == MediaPlayerState.ON

    # No station, frequency or RDS reply came back for the L4561N.
    assert source.extra_state_attributes == {}
    assert source.media_title is None


def test_zone_21_stays_off_at_volume_zero(zone):
    """Zone 21 reported off and volume 0 before and after `*16*0*21##`."""
    replies = _frames("rx", "21")
    assert set(replies) == {"*16*13*21##", "*#16*21*1*0##"}

    zone._async_handle_turn_off = AsyncMock()
    for raw in replies:
        zone.handle_event(OWNSoundEvent(raw))
        assert zone.state == MediaPlayerState.OFF

    assert zone.volume_level == 0.0
