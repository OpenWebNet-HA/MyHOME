"""Native position discovery must not replace explicit panel positioning settings."""
from unittest.mock import patch

import pytest
from homeassistant.helpers.entity_component import DATA_INSTANCES
from OWNd.message import OWNMessage

from custom_components.myhome.const import CONF_ADVANCED_COVERS
from custom_components.myhome.cover_profiles import DATA_KEY, get_store, write_profile
from tests.mock_gateway_harness import MockGatewayHarness
from tests.test_cover_auto_detect_dim10 import _feed, _make_entry, _setup_entry
from tests.test_cover_profiles import message
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_panel_cover_calibration import calibration as calibration_fixture

plant = plant_fixture
calibration = calibration_fixture

PROFILE = {"name": "Bedroom", "opening_time": 26, "closing_time": 20,
           "reference_travel_cm": 200,
           "geometry": {"slat_time_s": 2, "opening_roll": 2, "closing_roll": 3}}
SETTINGS = [
    {"action": "save", "profile": PROFILE},
    {"action": "overrides", "overrides": {"opening": 19.25}},
    {"action": "manual_travel", "travel_cm": 200},
    {"action": "manual_slats", "slats": False},
]


@pytest.mark.parametrize("settings", SETTINGS)
async def test_live_dim10_preserves_explicit_panel_settings(hass, plant, settings):
    cover = plant.covers[0]
    cover._allow_auto_advanced = True
    await write_profile(hass, message(plant, **settings))
    model = cover._motion.model
    times = (cover._travel_time_up, cover._travel_time_down)
    cover.handle_event(OWNMessage.parse("*#2*11*10*10*60*001*0##"))
    assert not cover._advanced
    assert cover._motion.model == model
    assert (cover._travel_time_up, cover._travel_time_down) == times
    assert not plant.entries[0].options.get(CONF_ADVANCED_COVERS)


async def test_live_dim10_cannot_promote_during_panel_measurement(calibration):
    cal = calibration
    cal.cover._allow_auto_advanced = True
    cal.cover.handle_event(OWNMessage.parse("*#2*11*10*10*60*001*0##"))
    assert not cal.cover._advanced
    assert cal.cover._calibration is cal.session
    assert cal.session.active


async def test_manual_pulse_alone_locks_out_native_promotion(hass, plant):
    cover = plant.covers[0]
    plant.gateways[0].config_entry = plant.entries[0]
    cover._allow_auto_advanced = True
    await cover.async_set_travel_time(min_motor_pulse=0.5)
    cover.handle_event(OWNMessage.parse("*#2*11*10*10*60*001*0##"))
    assert not cover._advanced
    assert cover._min_motor_pulse == 0.5
    assert cover.native_cover_fallback()["min_motor_pulse"] == 0.5


async def test_restart_loads_panel_and_migrated_native_settings_before_auto_detection(hass):
    """Persist through public APIs, discard the cache and rebuild all six entities."""
    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(port)
    addresses = ["0110", "0111", "0112", "0113", "0114", "0115"]
    try:
        with (
            patch("custom_components.myhome.gateway.OWNSession.test_connection",
                  return_value={"Success": True, "Message": None}),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            await _setup_entry(hass, entry)
            for address in addresses:
                await _feed(hass, f"*2*0*{address}##")
            component = hass.data[DATA_INSTANCES]["cover"]
            for address, settings in zip(addresses, SETTINGS):
                store = get_store(hass, entry.entry_id)
                await write_profile(hass, {"entry_id": entry.entry_id,
                    "entity_id": f"cover.cover_{address}", "revision": store.data["revision"], **settings})
            await component.get_entity("cover.cover_0114").async_set_travel_time(min_motor_pulse=0.5)
            await component.get_entity("cover.cover_0115").async_set_travel_time(travel_time=18)
            expected = {address: component.get_entity(f"cover.cover_{address}")._motion.model
                        for address in addresses}
            assert await hass.config_entries.async_unload(entry.entry_id)
            hass.data[DATA_KEY].pop(entry.entry_id)
            # A stale native flag must not take precedence over newer explicit settings.
            hass.config_entries.async_update_entry(entry, options={
                **entry.options, CONF_ADVANCED_COVERS: dict.fromkeys(addresses, True)})
            await _setup_entry(hass, entry)
            component = hass.data[DATA_INSTANCES]["cover"]
            for address in addresses:
                cover = component.get_entity(f"cover.cover_{address}")
                assert not cover._advanced
                await _feed(hass, f"*#2*{address}*10*10*60*001*0##")
                assert not cover._advanced
                assert cover._motion.model == expected[address]
            assert component.get_entity("cover.cover_0114")._min_motor_pulse == 0.5
            # Unconfigured hardware retains upstream automatic native discovery.
            await _feed(hass, "*#2*0116*10*10*60*001*0##")
            assert component.get_entity("cover.cover_0116")._advanced
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()
