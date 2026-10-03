"""Issue #441: a temperature probe removed from myhome.yaml comes back as a second entity.

A probe configured in ``myhome.yaml`` is keyed ``4-105`` by the validator, so its
entity's unique id is ``<mac>-4-105-temperature``. Once the YAML entry is gone, the
probe is restored from the registry (and discovered from the bus) with the device id
``105``. If that yields a different unique id, Home Assistant registers a new entity
next to the old one: the reporter's ``sensor.temp_quadro_2``.

These tests pin one unique id per probe, whichever way the probe was created.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_ENTITY,
    CONF_MANUFACTURER,
    CONF_PLATFORMS,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
)
from custom_components.myhome.sensor import async_setup_entry
from tests.conftest import attach_runtime

MAC = "00:03:50:a6:02:a0"
YAML_UNIQUE_ID = f"{MAC}-4-105-temperature"  # from the reporter's core.entity_registry
OLD_BUS_UNIQUE_ID = f"{MAC}-105-temperature"  # what restore/discovery produced before the fix


def _gateway() -> MagicMock:
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.device_registry_id = None
    gateway.send_status_request = AsyncMock()
    return gateway


async def _setup(hass: HomeAssistant, sensors: dict) -> tuple[MockConfigEntry, list]:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC}, unique_id=MAC)
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[MAC] = {
        CONF_PLATFORMS: {"sensor": sensors},
        CONF_ENTITY: _gateway(),
    }
    attach_runtime(hass, entry)
    return entry, []


async def _start(hass: HomeAssistant, entry: MockConfigEntry, added: list) -> None:
    assert await async_setup_entry(hass, entry, added.extend) is True
    await hass.async_block_till_done()


async def test_yaml_probe_unique_id_is_the_reference(hass: HomeAssistant):
    """The unique id a myhome.yaml probe gets, as validate.py keys it (``<who>-<where>``)."""
    entry, added = await _setup(
        hass,
        {
            "4-105": {
                CONF_WHO: "4",
                CONF_WHERE: "105",
                CONF_NAME: "Temp Quadro",
                CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
                CONF_MANUFACTURER: "BTicino",
                CONF_DEVICE_MODEL: "L4692",
            }
        },
    )
    await _start(hass, entry, added)

    assert [e.unique_id for e in added] == [YAML_UNIQUE_ID]


async def test_probe_restored_from_registry_keeps_its_yaml_unique_id(hass: HomeAssistant):
    """YAML entry removed, registry entry disabled: the restored probe must reuse it."""
    entry, added = await _setup(hass, {})
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        YAML_UNIQUE_ID,
        suggested_object_id="temp_quadro",
        config_entry=entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    assert old.entity_id == "sensor.temp_quadro"

    await _start(hass, entry, added)

    assert len(added) == 1
    assert added[0].unique_id == YAML_UNIQUE_ID
    assert added[0].entity_id == "sensor.temp_quadro"


async def test_probe_discovered_from_bus_matches_the_yaml_unique_id(hass: HomeAssistant):
    """No YAML, no registry: a pushed reading creates the probe under the same unique id."""
    entry, added = await _setup(hass, {})
    await _start(hass, entry, added)
    assert added == []

    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*105*0*0215##"))
    await hass.async_block_till_done()

    assert [e.unique_id for e in added] == [YAML_UNIQUE_ID]


def _register(
    hass: HomeAssistant, entry: MockConfigEntry, unique_id: str, object_id: str, **kwargs
) -> str:
    return (
        er.async_get(hass)
        .async_get_or_create(
            "sensor",
            DOMAIN,
            unique_id,
            suggested_object_id=object_id,
            config_entry=entry,
            original_device_class=SensorDeviceClass.TEMPERATURE,
            **kwargs,
        )
        .entity_id
    )


async def test_old_bus_unique_id_is_migrated_in_place(hass: HomeAssistant):
    """A probe discovered before the fix keeps its entity id and gets the YAML-form unique id."""
    entry, added = await _setup(hass, {})
    entity_id = _register(hass, entry, OLD_BUS_UNIQUE_ID, "probe_105_temperature")

    await _start(hass, entry, added)

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, YAML_UNIQUE_ID) == entity_id
    assert registry.async_get_entity_id("sensor", DOMAIN, OLD_BUS_UNIQUE_ID) is None
    assert [(e.unique_id, e.entity_id) for e in added] == [(YAML_UNIQUE_ID, entity_id)]


async def test_reporters_duplicate_is_removed_and_the_original_kept(hass: HomeAssistant):
    """#441 as reported: ``sensor.temp_quadro`` (YAML id, disabled) plus ``sensor.temp_quadro_2``."""
    entry, added = await _setup(hass, {})
    original = _register(
        hass, entry, YAML_UNIQUE_ID, "temp_quadro", disabled_by=er.RegistryEntryDisabler.USER
    )
    duplicate = _register(hass, entry, OLD_BUS_UNIQUE_ID, "temp_quadro")
    assert (original, duplicate) == ("sensor.temp_quadro", "sensor.temp_quadro_2")

    await _start(hass, entry, added)

    registry = er.async_get(hass)
    assert registry.async_get(duplicate) is None
    assert registry.async_get_entity_id("sensor", DOMAIN, YAML_UNIQUE_ID) == original
    assert [(e.unique_id, e.entity_id) for e in added] == [(YAML_UNIQUE_ID, original)]


async def test_migration_skips_foreign_ids_and_survives_a_refused_update(hass: HomeAssistant):
    """Another gateway's id is left alone; a registry refusal is logged, not raised."""
    entry, added = await _setup(hass, {})
    foreign = _register(hass, entry, "11:22:33:44:55:66-105-temperature", "foreign_probe")
    _register(hass, entry, OLD_BUS_UNIQUE_ID, "probe_105_temperature")

    with patch.object(er.EntityRegistry, "async_update_entity", side_effect=ValueError("refused")):
        await _start(hass, entry, added)

    registry = er.async_get(hass)
    assert registry.async_get(foreign).unique_id == "11:22:33:44:55:66-105-temperature"
    assert registry.async_get_entity_id("sensor", DOMAIN, OLD_BUS_UNIQUE_ID) is not None
