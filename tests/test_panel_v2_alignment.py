"""Contracts between the experimental panel and the current v2 runtime."""
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.myhome import cover as native
from custom_components.myhome.cover_calibration import ready_cover
from custom_components.myhome.cover_profiles import (
    ProfileError,
    bind_cover,
    get_store,
    write_profile,
)
from tests.test_cover_profiles import message
from tests.test_cover_profiles import plant as plant_fixture

plant = plant_fixture


async def test_panel_assignment_restores_native_directional_times_on_reset(hass, plant):
    cover, entry, gateway = plant.covers[0], plant.entries[0], plant.gateways[0]
    gateway.config_entry = entry
    hass.config_entries.async_update_entry(entry, options={native.CONF_COVER_TRAVEL_TIMES: {
        cover._device_id: {"up": 24.5, "down": 18.5, "source": "manual", "measured_at": "2026-09-16T12:00:00+00:00"},
    }})
    # Recreate the store to exercise first-load migration of the seeded native options.
    from custom_components.myhome.cover_profiles import DATA_KEY
    store = get_store(hass, entry.entry_id)
    await store.store.async_remove()
    hass.data[DATA_KEY].pop(entry.entry_id)
    await bind_cover(hass, cover)
    assert (cover._travel_time_up, cover._travel_time_down) == (24.5, 18.5)
    await write_profile(hass, message(plant, profile={"name": "Profile", "opening_time": 35, "closing_time": 25}))
    assert (cover._travel_time_up, cover._travel_time_down) == (35, 25)
    for action in [cover.async_calibrate, cover.async_reset_travel_time,
                   lambda: cover.async_set_travel_time(travel_time=40)]:
        with pytest.raises(ServiceValidationError, match="managed by the MyHOME panel"):
            await action()
    gateway.send.assert_not_awaited()
    await write_profile(hass, message(plant, 1, action="assign", profile_id=None))
    assert (cover._travel_time_up, cover._travel_time_down) == (24.5, 18.5)
    assert cover.extra_state_attributes["calibration_source"] == "manual"
    assert cover.extra_state_attributes["calibrated_at"] == "2026-09-16T12:00:00+00:00"
    await cover.async_set_travel_time(travel_time_down=20, travel_time_up=30)
    assert (cover._travel_time_up, cover._travel_time_down) == (30, 20)


@pytest.mark.parametrize("queued", [False, True])
async def test_native_calibration_blocks_panel_measurement_and_profile_writes(hass, plant, monkeypatch, queued):
    cover = plant.covers[0]
    key = native._gateway_key(cover._gateway_handler)
    monkeypatch.setitem(native._CALIBRATION_QUEUED if queued else native._CALIBRATION_ACTIVE,
                        key, {cover} if queued else cover)
    store = get_store(hass, plant.entries[0].entry_id)
    with pytest.raises(ProfileError, match="calibration_busy"):
        ready_cover(hass, store, plant.entries[0].entry_id, cover.entity_id)
    with pytest.raises(ProfileError, match="calibration_busy"):
        await write_profile(hass, message(plant))
    assert store.data["revision"] == 0


async def test_native_timing_rejects_other_panel_session_and_pending_persistence(hass, plant):
    cover, gateway, entry = plant.covers[1], plant.gateways[0], plant.entries[0]
    gateway.config_entry = entry
    store = get_store(hass, entry.entry_id)
    store.calibration = object()
    with pytest.raises(ServiceValidationError):
        await cover.async_calibrate()
    store.calibration = None
    async with store.lock:
        with pytest.raises(ServiceValidationError):
            await cover.async_set_travel_time(travel_time=40)
    gateway.send.assert_not_awaited()


async def test_pending_profile_waits_for_stop_delivery(hass, plant):
    import asyncio

    from OWNd.message import OWNMessage

    cover = plant.covers[0]
    cover.handle_event(OWNMessage.parse("*2*1*11##"))
    await write_profile(hass, message(plant))
    written = asyncio.get_running_loop().create_future()
    plant.gateways[0].send = AsyncMock(return_value=written)
    await cover.async_stop_cover()
    assert cover._pending_profile is not None
    assert cover._travel_time_up == 30
    written.set_result(native.time.monotonic())
    await asyncio.sleep(0)
    assert cover._pending_profile is None
    assert cover._travel_time_up == 42.5
