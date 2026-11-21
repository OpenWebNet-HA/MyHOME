# ruff: noqa
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.climate import HVACMode
from homeassistant.core import State
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import async_setup_entry
from custom_components.myhome.const import DOMAIN
from tests.conftest import attach_runtime


async def test_discovery_before_restore(hass):
    mac = "<mac>"
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": mac}, unique_id=mac)
    entry.add_to_hass(hass)
    gw = MagicMock()
    gw.mac = mac
    gw.is_connected = True
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    runtime = attach_runtime(hass, entry, mac, gw)
    runtime.platforms["climate"] = {}
    entities = []
    await async_setup_entry(hass, entry, entities.extend)
    # The message router delivers to newly created entities before HA adds them.
    async_dispatcher_send(
        hass, f"myhome_message_{mac}", OWNMessage.parse("*4*102*#0##")
    )  # climate_mode_2026_11_08/frames.json:103
    (cu,) = entities
    assert cu.hvac_mode == HVACMode.HEAT
    cu.entity_id = "climate.test"
    cu.hass = hass
    cu.platform = MagicMock()
    cu.async_get_last_state = AsyncMock(
        return_value=State(cu.entity_id, "heat", {"season": "heating"})
    )
    await cu.async_added_to_hass()
    await cu.async_set_hvac_mode(HVACMode.HEAT)
    assert [str(call.args[0]) for call in gw.send.await_args_list] == [
        "*4*1*#0##",
        "*4*3100*#0##",
    ]  # tests/test_crew_central_season_restore.py:15-17
