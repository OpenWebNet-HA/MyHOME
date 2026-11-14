"""A zone without a configured ``standalone`` follows its plant (#697).

Behind a 99-zone central unit the gateway rejects ``*#4*Z*#14*T*M##`` and takes
``*#4*#Z*#14*T*M##``. Trace: https://github.com/OpenWebNet-HA/MyHOME/issues/697
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACMode
from OWNd.message import OWNEvent


@pytest.fixture
def gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:12:34"
    gw.unique_id = gw.mac
    gw.log_id = "[Test Gateway]"
    gw.has_central_unit = False
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


@pytest.fixture(autouse=True)
def _entity_init():
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield


def _zone(gateway, where="3", standalone=None, central=False):
    from custom_components.myhome.climate import MyHOMEClimate

    hass = MagicMock()
    hass.data = {}
    c = MyHOMEClimate(
        hass=hass, name="Zone", device_id=f"4-{where}", who="4", where=where,
        heating=True, cooling=True, fan=False, standalone=standalone, central=central,
        manufacturer="BTicino", model="Heating Zone", gateway=gateway,
    )
    c.platform = MagicMock()
    c.entity_id = "climate.test"
    c.async_schedule_update_ha_state = MagicMock()
    c.async_write_ha_state = MagicMock()
    return c


async def _set_temperature(climate, gateway, temperature=16.0):
    climate._attr_hvac_mode = HVACMode.HEAT
    await climate.async_set_temperature(temperature=temperature)
    return [str(call.args[0]) for call in gateway.send.await_args_list]


async def test_zone_behind_a_central_unit_uses_the_hash_form(gateway):
    gateway.has_central_unit = True
    sent = await _set_temperature(_zone(gateway), gateway)
    assert "*#4*#3*#14*0160*3##" in sent


async def test_zone_without_a_central_unit_stays_standalone(gateway):
    sent = await _set_temperature(_zone(gateway), gateway)
    assert "*#4*3*#14*0160*3##" in sent


async def test_the_plant_is_resolved_when_writing_not_when_building(gateway):
    zone = _zone(gateway)
    gateway.has_central_unit = True  # the central spoke after the zone was discovered
    sent = await _set_temperature(zone, gateway)
    assert "*#4*#3*#14*0160*3##" in sent


@pytest.mark.parametrize("has_central", [True, False])
async def test_configured_standalone_wins(gateway, has_central):
    gateway.has_central_unit = has_central
    sent = await _set_temperature(_zone(gateway, standalone=True), gateway)
    assert "*#4*3*#14*0160*3##" in sent
    gateway.send.reset_mock()
    sent = await _set_temperature(_zone(gateway, standalone=False), gateway)
    assert "*#4*#3*#14*0160*3##" in sent


def test_a_central_entity_marks_the_plant(gateway):
    _zone(gateway, where="#0", central=True)
    assert gateway.has_central_unit is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("*4*202*#0##", True), ("*4*110#0170*#0##", True), ("*4*202*3##", False), ("*#4*3*12*0160*3##", False)],
)
async def test_central_frame_marks_the_plant(raw, expected):
    from custom_components.myhome.gateway_events import GatewayEventDispatcher

    handler = MagicMock()
    handler.has_central_unit = False
    handler.is_standby = False
    handler.is_secondary = False
    handler.delegated_away_whos = set()
    handler.log_id = "[Test]"
    dispatcher = GatewayEventDispatcher(handler)
    with patch("custom_components.myhome.gateway_events.async_dispatcher_send", MagicMock(), create=True):
        try:
            await dispatcher.process_message(OWNEvent.parse(raw))
        except Exception:  # only the plant marker matters here
            pass
    assert handler.has_central_unit is expected
