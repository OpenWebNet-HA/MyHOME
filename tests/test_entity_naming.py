"""Entity naming under has_entity_name (quality-scale has-entity-name / entity-translations).

Pins the entity ids and friendly names a fresh install gets for a representative
myhome.yaml plant, and proves that an accidental delete + re-add of the config
entry keeps user-set names and areas (the registry restores deleted entries).
"""
from unittest.mock import patch

from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import DOMAIN

MAC = "00:03:50:00:12:35"

PLANT = f"""
{MAC}:
  light:
    l1:
      where: "12"
      name: "Kitchen Light"
    l2:
      where: "13"
      name: "Hall"
      entity_name: "Hall Spot"
  switch:
    s1:
      where: "22"
      name: "Garden Socket"
      class: outlet
  cover:
    c1:
      where: "31"
      name: "Bedroom Shutter"
  binary_sensor:
    b1:
      where: "31"
      name: "Cancello"
      class: opening
    b2:
      where: "32"
      name: "Front Door"
      entity_name: "Contact"
      class: door
    b3:
      where: "33"
      name: "Garage"
      entity_name: "Garage"
      class: garage_door
  sensor:
    p1:
      where: "51"
      name: "House"
      class: power
    t1:
      where: "1"
      who: "4"
      name: "Probe 100"
      class: temperature
"""

# entity_id -> friendly name a fresh install produces. Primary entities are their
# device (id and name = device name); sensors / binary sensors are named after their
# device class or entity_name; buttons carry a translated name.
EXPECTED = {
    "light.kitchen_light": "Kitchen Light",
    "light.hall": "Hall",  # entity_name never named a light and still does not
    "switch.garden_socket": "Garden Socket",
    "cover.bedroom_shutter": "Bedroom Shutter",
    "binary_sensor.cancello_opening": "Cancello Opening",
    "binary_sensor.front_door_contact": "Front Door Contact",
    "binary_sensor.garage": "Garage",  # entity_name == name: the entity is the device
    "sensor.house_power": "House Power",
    "sensor.house_energy": "House Energy",
    "sensor.probe_100_temperature": "Probe 100 Temperature",
    "button.kitchen_light_lock": "Kitchen Light Lock",
    "button.kitchen_light_unlock": "Kitchen Light Unlock",
    "button.bedroom_shutter_calibrate_travel_time": "Bedroom Shutter Calibrate travel time",
    "button.f454_gateway_calibrate_all_covers": "F454 Gateway Calibrate all covers",
}
DISABLED_BY_DEFAULT = {
    "sensor.house_energy_today": "Energy (today)",
    "sensor.house_energy_current_month": "Energy (current month)",
}


def _entry(tmp_path):
    (tmp_path / "myhome.yaml").write_text(PLANT, encoding="utf-8")
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "1.2.3.4", CONF_PORT: 20000, CONF_PASSWORD: "p", CONF_MAC: MAC, "name": "F454"},
        options={"file_path": str(tmp_path / "myhome.yaml")},
        unique_id=MAC,
    )


def _gateway_patches():
    return [
        patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.send"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.send_status_request"),
    ]


async def _setup(hass, entry):
    entry.add_to_hass(hass)
    patches = _gateway_patches()
    for p in patches:
        p.start()
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        for p in patches:
            p.stop()


async def test_fresh_install_entity_ids_and_friendly_names(hass: HomeAssistant, tmp_path):
    entry = _entry(tmp_path)
    await _setup(hass, entry)

    registry = er.async_get(hass)
    for entity_id, friendly in EXPECTED.items():
        state = hass.states.get(entity_id)
        assert state is not None, f"{entity_id} missing; have {sorted(s.entity_id for s in hass.states.async_all())}"
        assert state.attributes["friendly_name"] == friendly, entity_id
    for entity_id, original_name in DISABLED_BY_DEFAULT.items():
        reg_entry = registry.async_get(entity_id)
        assert reg_entry is not None and reg_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION, entity_id
        assert reg_entry.original_name == original_name

    # every entity uses the device/entity naming model, and the device carries the configured name
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        assert reg_entry.has_entity_name is True, reg_entry.entity_id
    device = dr.async_get(hass).async_get(registry.async_get("cover.bedroom_shutter").device_id)
    assert device.name == "Bedroom Shutter"


async def test_delete_and_re_add_keeps_user_names_and_areas(hass: HomeAssistant, tmp_path):
    """The registry remembers deleted entries; re-adding the gateway restores customisations."""
    entry = _entry(tmp_path)
    await _setup(hass, entry)

    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    area = ar.async_get(hass).async_create("Kitchen")
    registry.async_update_entity("light.kitchen_light", name="Ceiling spots", area_id=area.id, icon="mdi:ceiling-light")
    registry.async_update_entity("sensor.house_power", new_entity_id="sensor.mains_power")
    cover_device = registry.async_get("cover.bedroom_shutter").device_id
    device_registry.async_update_device(cover_device, name_by_user="Master bedroom shutter", area_id=area.id)

    # Accidental delete of the integration entry, then the same gateway is added again
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("light.kitchen_light") is None

    entry2 = _entry(tmp_path)
    await _setup(hass, entry2)

    light = registry.async_get("light.kitchen_light")
    assert light is not None and light.name == "Ceiling spots" and light.area_id == area.id and light.icon == "mdi:ceiling-light"
    assert hass.states.get("light.kitchen_light").attributes["friendly_name"] == "Ceiling spots"
    assert registry.async_get("sensor.mains_power") is not None  # renamed entity id survived
    assert hass.states.get("sensor.house_power") is None
    device = device_registry.async_get(registry.async_get("cover.bedroom_shutter").device_id)
    assert device.name_by_user == "Master bedroom shutter" and device.area_id == area.id
