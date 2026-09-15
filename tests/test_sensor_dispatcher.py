"""Exercise sensor discovery and updates through real Home Assistant platforms."""

from unittest.mock import patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import CONF_ENTITY, CONF_FILE_PATH, DOMAIN

MAC = "00:03:50:00:12:34"


@pytest.fixture(autouse=True)
def no_gateway_io():
    with (
        patch("custom_components.myhome.gateway.OWNSession.test_connection",
              return_value={"Success": True}),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    ):
        yield


async def setup_gateway(hass, mac=MAC, options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.50", "mac": mac, "name": "F454"},
        unique_id=mac,
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.data[DOMAIN][mac][CONF_ENTITY]._on_event_connection_state_change(True)
    await hass.async_block_till_done()
    return entry


def sensor_entries(hass, entry):
    return [
        item for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if item.domain == "sensor"
    ]


async def dispatch(hass, frame, mac=MAC):
    async_dispatcher_send(hass, f"myhome_message_{mac}", OWNMessage.parse(frame))
    await hass.async_block_till_done()


async def test_energy_discovery_burst_and_device_link(hass):
    entry = await setup_gateway(hass)
    for watts in (100, 200, 345):
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNMessage.parse(f"*#18*51*113*{watts}##")
        )
    await hass.async_block_till_done()
    entities = sensor_entries(hass, entry)
    assert len(entities) == 1
    power = entities[0]
    assert power.unique_id == f"{MAC}-18-51-power"
    assert hass.states.get(power.entity_id).state == "345"
    assert hass.states.get(power.entity_id).attributes["unit_of_measurement"] == "W"

    registry = dr.async_get(hass)
    device = registry.async_get(power.device_id)
    parent = registry.async_get(device.via_device_id)
    assert (DOMAIN, MAC) in parent.identifiers

    await dispatch(hass, "*#18*51*51*12000##")
    assert len(sensor_entries(hass, entry)) == 2
    energy_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{MAC}-18-51-total-energy")
    assert hass.states.get(energy_id).state == "12000"
    assert hass.states.get(energy_id).attributes["state_class"] == "total_increasing"


async def test_meter_address_alias_and_control_frames(hass):
    entry = await setup_gateway(hass)
    for frame in ("*18*1200#1*71##", "*#18*31*113*500##", "*#18*71*999*1##"):
        await dispatch(hass, frame)
    assert sensor_entries(hass, entry) == []
    await dispatch(hass, "*#18*71*113*50##")
    await dispatch(hass, "*#18*71#0*113*75##")
    entities = sensor_entries(hass, entry)
    assert len(entities) == 1
    assert hass.states.get(entities[0].entity_id).state == "75"


async def test_discovered_entities_restore_names_and_disabled_defaults(hass):
    entry = await setup_gateway(hass)
    for dimension in (113, 51, 53, 54):
        await dispatch(hass, f"*#18*51*{dimension}*100##")
    registry = er.async_get(hass)
    before = {item.unique_id: item for item in sensor_entries(hass, entry)}
    assert len(before) == 4
    for suffix in ("daily-energy", "monthly-energy"):
        assert before[f"{MAC}-18-51-{suffix}"].disabled_by is er.RegistryEntryDisabler.INTEGRATION
    power = before[f"{MAC}-18-51-power"]
    registry.async_update_entity(power.entity_id, new_entity_id="sensor.kitchen_power", name="Kitchen power")
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    hass.data[DOMAIN][MAC][CONF_ENTITY]._on_event_connection_state_change(True)
    await hass.async_block_till_done()
    after = {item.unique_id: item for item in sensor_entries(hass, entry)}
    assert before.keys() == after.keys()
    assert after[power.unique_id].entity_id == "sensor.kitchen_power"
    assert after[power.unique_id].name == "Kitchen power"
    await dispatch(hass, "*#18*51*113*750##")
    assert hass.states.get("sensor.kitchen_power").state == "750"
    assert len(sensor_entries(hass, entry)) == 4

    assert await hass.config_entries.async_unload(entry.entry_id)
    await dispatch(hass, "*#18*52*113*999##")
    assert len(sensor_entries(hass, entry)) == 4


async def test_sensors_are_scoped_to_their_gateway(hass):
    first = await setup_gateway(hass)
    second_mac = "00:03:50:00:56:78"
    second = await setup_gateway(hass, mac=second_mac)
    await dispatch(hass, "*#18*51*113*123##")
    assert len(sensor_entries(hass, first)) == 1
    assert sensor_entries(hass, second) == []
    await dispatch(hass, "*#18*51*113*456##", mac=second_mac)
    assert hass.states.get(sensor_entries(hass, first)[0].entity_id).state == "123"
    assert hass.states.get(sensor_entries(hass, second)[0].entity_id).state == "456"


async def test_yaml_sensors_update_without_alias_duplicates(hass, tmp_path):
    config = tmp_path / "myhome.yaml"
    config.write_text(f'''{MAC}:
  sensor:
    meter:
      where: "51"
      name: "House"
      class: "power"
    energy_only:
      where: "52"
      name: "Solar"
      class: "energy"
    temperature:
      where: "1"
      name: "Bedroom"
      class: "temperature"
    illuminance:
      where: "12"
      name: "Garden"
      class: "illuminance"
''')
    entry = await setup_gateway(hass, options={CONF_FILE_PATH: str(config)})
    entities = {item.unique_id: item for item in sensor_entries(hass, entry)}
    assert len(entities) == 9  # Four power, three energy, temperature, illuminance.
    for frame, suffix, expected in (
        ("*#18*51*113*450##", "18-51-power", "450"),
        ("*#18*51*51*12345##", "18-51-total-energy", "12345"),
        ("*#18*52*51*6789##", "18-52-total-energy", "6789"),
        ("*#4*1*0*0225##", "4-1-temperature", "22.5"),
        ("*#1*12*6*450##", "1-12-illuminance", "450"),
    ):
        await dispatch(hass, frame)
        assert hass.states.get(entities[f"{MAC}-{suffix}"].entity_id).state == expected
    await dispatch(hass, "*#18*52*113*789##")
    assert len(sensor_entries(hass, entry)) == 9
    assert f"{MAC}-18-52-power" not in entities
    assert entities[f"{MAC}-18-51-power"].original_name == "House Power"
