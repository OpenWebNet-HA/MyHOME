"""Test decoder companion resolution and discovery."""
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.decoder_companion import (
    async_find_streaming_companion,
    async_get_excluded_decoders,
)


@pytest.mark.asyncio
async def test_find_streaming_companion_same_device(hass: HomeAssistant) -> None:
    """Test finding a streaming companion registered to the exact same device."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    cam_entry = MockConfigEntry(domain="cambridge_audio")
    cam_entry.add_to_hass(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id=cam_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "00:11:22:33:44:55")},
        identifiers={("cambridge_audio", "unique_cambridge_1")},
        manufacturer="Cambridge Audio",
        model="CXN V2",
    )

    # Cambridge primary entity
    ent_reg.async_get_or_create(
        "media_player",
        "cambridge_audio",
        "unique_cambridge_1",
        device_id=device.id,
        suggested_object_id="streamer",
    )

    # Companion DLNA DMR entity
    ent_reg.async_get_or_create(
        "media_player",
        "dlna_dmr",
        "unique_dlna_1",
        device_id=device.id,
        suggested_object_id="streamer_dlna",
    )

    companion = async_find_streaming_companion(hass, "media_player.streamer")
    assert companion == "media_player.streamer_dlna"


@pytest.mark.asyncio
async def test_find_streaming_companion_by_mac_address(hass: HomeAssistant) -> None:
    """Test finding a streaming companion on another device sharing the same MAC."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    cam_entry = MockConfigEntry(domain="cambridge_audio")
    cam_entry.add_to_hass(hass)
    dlna_entry = MockConfigEntry(domain="dlna_dmr")
    dlna_entry.add_to_hass(hass)

    device1 = dev_reg.async_get_or_create(
        config_entry_id=cam_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff")},
        identifiers={("cambridge_audio", "id1")},
    )
    device2 = dev_reg.async_get_or_create(
        config_entry_id=dlna_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff")},
        identifiers={("dlna_dmr", "id2")},
    )

    ent_reg.async_get_or_create(
        "media_player",
        "cambridge_audio",
        "cambridge_unique",
        device_id=device1.id,
        suggested_object_id="cambridge_streamer",
    )

    ent_reg.async_get_or_create(
        "media_player",
        "dlna_dmr",
        "dlna_unique",
        device_id=device2.id,
        suggested_object_id="cambridge_dlna",
    )

    companion = async_find_streaming_companion(hass, "media_player.cambridge_streamer")
    assert companion == "media_player.cambridge_dlna"


@pytest.mark.asyncio
async def test_find_streaming_companion_mac_step_skips_child_devices(hass: HomeAssistant) -> None:
    """A child device has no connections of its own, so the MAC step cannot match it.

    Reading ``connections`` on one is deprecated in HA 2026.9 and an error from 2027.9;
    the lookup must skip it rather than touch the attribute.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    cam_entry = MockConfigEntry(domain="cambridge_audio")
    cam_entry.add_to_hass(hass)
    dlna_entry = MockConfigEntry(domain="dlna_dmr")
    dlna_entry.add_to_hass(hass)

    device1 = dev_reg.async_get_or_create(
        config_entry_id=cam_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff")},
        identifiers={("cambridge_audio", "id1")},
    )
    device2 = dev_reg.async_get_or_create(
        config_entry_id=dlna_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff")},
        identifiers={("dlna_dmr", "id2")},
    )
    ent_reg.async_get_or_create(
        "media_player", "cambridge_audio", "cambridge_unique",
        device_id=device1.id, suggested_object_id="cambridge_streamer",
    )
    ent_reg.async_get_or_create(
        "media_player", "dlna_dmr", "dlna_unique",
        device_id=device2.id, suggested_object_id="cambridge_dlna",
    )

    child = MagicMock(spec=dr.ChildDeviceEntry)
    child.name = None
    child.name_by_user = None
    real_async_get = dr.DeviceRegistry.async_get

    def async_get(self, device_id, **kwargs):
        return child if device_id == device2.id else real_async_get(self, device_id, **kwargs)

    with patch.object(dr.DeviceRegistry, "async_get", async_get):
        assert async_find_streaming_companion(hass, "media_player.cambridge_streamer") is None


@pytest.mark.asyncio
async def test_find_streaming_companion_by_host(hass: HomeAssistant) -> None:
    """Test finding a streaming companion sharing the same host/IP address."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    cam_entry = MockConfigEntry(domain="cambridge_audio", data={"host": "192.0.2.203"})
    cam_entry.add_to_hass(hass)
    cast_entry = MockConfigEntry(domain="cast", data={"host": "192.0.2.203"})
    cast_entry.add_to_hass(hass)

    dev_cam = dev_reg.async_get_or_create(
        config_entry_id=cam_entry.entry_id,
        identifiers={("cambridge_audio", "cam_id")},
    )
    dev_cast = dev_reg.async_get_or_create(
        config_entry_id=cast_entry.entry_id,
        identifiers={("cast", "cast_id")},
    )

    ent_reg.async_get_or_create(
        "media_player",
        "cambridge_audio",
        "decoder_cam_unique",
        config_entry=cam_entry,
        device_id=dev_cam.id,
        suggested_object_id="audio_decoder",
    )
    ent_reg.async_get_or_create(
        "media_player",
        "cast",
        "decoder_cast_unique",
        config_entry=cast_entry,
        device_id=dev_cast.id,
        suggested_object_id="mxn10_f1",
    )

    companion = async_find_streaming_companion(hass, "media_player.audio_decoder")
    assert companion == "media_player.mxn10_f1"


@pytest.mark.asyncio
async def test_find_streaming_companion_by_name(hass: HomeAssistant) -> None:
    """Test finding a streaming companion sharing the same device name."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    cam_entry = MockConfigEntry(domain="cambridge_audio")
    cam_entry.add_to_hass(hass)
    cast_entry = MockConfigEntry(domain="cast")
    cast_entry.add_to_hass(hass)

    dev_cam = dev_reg.async_get_or_create(
        config_entry_id=cam_entry.entry_id,
        identifiers={("cambridge_audio", "cam_id_2")},
        name="Audio Decoder",
    )
    dev_cast = dev_reg.async_get_or_create(
        config_entry_id=cast_entry.entry_id,
        identifiers={("cast", "cast_id_2")},
        name="Audio Decoder",
    )

    ent_reg.async_get_or_create(
        "media_player",
        "cambridge_audio",
        "cam_name_unique",
        device_id=dev_cam.id,
        suggested_object_id="cam_streamer",
    )
    ent_reg.async_get_or_create(
        "media_player",
        "cast",
        "cast_name_unique",
        device_id=dev_cast.id,
        suggested_object_id="cast_streamer",
    )

    companion = async_find_streaming_companion(hass, "media_player.cam_streamer")
    assert companion == "media_player.cast_streamer"


@pytest.mark.asyncio
async def test_find_streaming_companion_not_found(hass: HomeAssistant) -> None:
    """Test returning None when no streaming companion is available."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    cam_entry = MockConfigEntry(domain="cambridge_audio")
    cam_entry.add_to_hass(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id=cam_entry.entry_id,
        identifiers={("cambridge_audio", "solo_id")},
    )
    ent_reg.async_get_or_create(
        "media_player",
        "cambridge_audio",
        "solo_unique",
        device_id=device.id,
        suggested_object_id="solo_audio",
    )

    companion = async_find_streaming_companion(hass, "media_player.solo_audio")
    assert companion is None

    # Test entity with no device_id
    ent_reg.async_get_or_create(
        "media_player",
        "cambridge_audio",
        "no_device_unique",
        device_id=None,
        suggested_object_id="no_device_audio",
    )
    assert async_find_streaming_companion(hass, "media_player.no_device_audio") is None


@pytest.mark.asyncio
async def test_find_streaming_companion_device_not_found(hass: HomeAssistant) -> None:
    """Test returning None when the entity registry device_id does not exist in device registry."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    cam_entry = MockConfigEntry(domain="cambridge_audio")
    cam_entry.add_to_hass(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id=cam_entry.entry_id,
        identifiers={("cambridge_audio", "ghost_dev")},
    )
    ent_reg.async_get_or_create(
        "media_player",
        "cambridge_audio",
        "ghost_unique",
        device_id=device.id,
        suggested_object_id="ghost_streamer",
    )

    with patch.object(dev_reg, "async_get", return_value=None):
        companion = async_find_streaming_companion(hass, "media_player.ghost_streamer")
        assert companion is None


@pytest.mark.asyncio
async def test_excluded_decoders_includes_myhome_and_mass(hass: HomeAssistant) -> None:
    """Excluded decoders includes mass and myhome, but not physical players."""
    ent_reg = er.async_get(hass)

    ent_reg.async_get_or_create("media_player", "myhome", "myhome_zone_1", suggested_object_id="dining_room")
    ent_reg.async_get_or_create("media_player", "mass", "mass_player_1", suggested_object_id="mass_dining_room")
    ent_reg.async_get_or_create("media_player", "squeezelite", "squeezelite_1", suggested_object_id="kitchen_pi")
    ent_reg.async_get_or_create("media_player", "dlna_dmr", "dlna_1", suggested_object_id="living_dlna")

    excluded = async_get_excluded_decoders(hass)

    assert "media_player.dining_room" in excluded
    assert "media_player.mass_dining_room" in excluded
    assert "media_player.kitchen_pi" not in excluded
    assert "media_player.living_dlna" not in excluded
