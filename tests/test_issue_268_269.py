"""Tests verifying fixes for GitHub Issues #268 and #269.

Issue #268: Friendly Name in Climate section in myhome.yaml not reported after migration
Issue #269: Lock/unlock buttons create new entity ids upon upgrade
"""
import os
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNHeatingEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.button import (
    DisableCommandButtonEntity,
    EnableCommandButtonEntity,
)
from custom_components.myhome.climate import (
    MyHOMEClimate,
)
from custom_components.myhome.climate import (
    async_setup_entry as async_setup_climate_entry,
)
from custom_components.myhome.const import (
    CONF_ENTITY,
    CONF_PLATFORMS,
    DOMAIN,
)
from custom_components.myhome.validate import climate_schema


@pytest.mark.asyncio
async def test_issue_269_button_unique_id_includes_who(hass: HomeAssistant):
    """Verify lock and unlock buttons generate unique IDs matching 0.9.4 format ({mac}-{who}-{where}-disable/enable)."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "00:03:50:81:17:76"

    # Light device lock/unlock button (who=1, where=21)
    light_lock = DisableCommandButtonEntity(
        hass=hass,
        platform="button",
        name="Office Light",
        device_id="21",
        who="1",
        where="21",
        interface=None,
        manufacturer="BTicino",
        model="Actuator",
        gateway=mock_gateway,
    )
    light_unlock = EnableCommandButtonEntity(
        hass=hass,
        platform="button",
        name="Office Light",
        device_id="21",
        who="1",
        where="21",
        interface=None,
        manufacturer="BTicino",
        model="Actuator",
        gateway=mock_gateway,
    )

    # Shutter/Cover device lock/unlock button (who=2, where=21)
    cover_lock = DisableCommandButtonEntity(
        hass=hass,
        platform="button",
        name="Office Shutter",
        device_id="21",
        who="2",
        where="21",
        interface=None,
        manufacturer="BTicino",
        model="Actuator",
        gateway=mock_gateway,
    )
    cover_unlock = EnableCommandButtonEntity(
        hass=hass,
        platform="button",
        name="Office Shutter",
        device_id="21",
        who="2",
        where="21",
        interface=None,
        manufacturer="BTicino",
        model="Actuator",
        gateway=mock_gateway,
    )

    # Assert 0.9.4 backwards-compatible format with who
    assert light_lock.unique_id == "00:03:50:81:17:76-1-21-disable"
    assert light_unlock.unique_id == "00:03:50:81:17:76-1-21-enable"
    assert cover_lock.unique_id == "00:03:50:81:17:76-2-21-disable"
    assert cover_unlock.unique_id == "00:03:50:81:17:76-2-21-enable"

    # Assert no cross-domain collision between light 21 and cover 21 lock buttons
    assert light_lock.unique_id != cover_lock.unique_id
    assert light_unlock.unique_id != cover_unlock.unique_id


@pytest.mark.asyncio
async def test_issue_269_migration_prunes_duplicate_button(hass: HomeAssistant):
    """Verify migration in __init__.py prunes duplicate _2 button entities created by 2.0b3 in favor of canonical 0.9.4 entities."""
    entity_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.50",
            "port": 20000,
            "password": "pass",
            "mac": "00:03:50:81:17:76",
        },
        unique_id="00:03:50:81:17:76",
    )
    config_entry.add_to_hass(hass)

    # 1. Device in device registry
    device = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "00:03:50:81:17:76-1-21")},
        name="Office Light",
    )

    # 2. Canonical 0.9.4 entity (e.g. orphaned during 2.0b3 upgrade)
    canonical_entry = entity_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id="00:03:50:81:17:76-1-21-disable",
        suggested_object_id="office_light_lock",
        config_entry=config_entry,
        device_id=device.id,
    )
    assert canonical_entry.entity_id == "button.office_light_lock"

    # 3. Duplicate entity created by 2.0b3 ({mac}-21-disable)
    dup_entry = entity_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id="00:03:50:81:17:76-21-disable",
        suggested_object_id="office_light_lock_2",
        config_entry=config_entry,
        device_id=device.id,
    )
    assert dup_entry.entity_id == "button.office_light_lock_2"

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    # The duplicate 2.0b3 entity must be pruned
    assert entity_reg.async_get("button.office_light_lock_2") is None
    # The original canonical entity must remain intact
    assert entity_reg.async_get("button.office_light_lock") is not None
    assert entity_reg.async_get("button.office_light_lock").unique_id == "00:03:50:81:17:76-1-21-disable"


@pytest.mark.asyncio
async def test_issue_269_migration_heals_standalone_2_0b3_button(hass: HomeAssistant):
    """Verify migration in __init__.py heals a 2.0b3 button when no old 0.9.4 entity remains."""
    entity_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.51",
            "port": 20000,
            "password": "pass",
            "mac": "00:03:50:81:17:77",
        },
        unique_id="00:03:50:81:17:77",
    )
    config_entry.add_to_hass(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "00:03:50:81:17:77-1-31")},
        name="Kitchen Light",
    )

    # 2.0b3 created button with _2 suffix because user had old entity, but user deleted old entity
    dup_entry = entity_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id="00:03:50:81:17:77-31-disable",
        suggested_object_id="kitchen_light_lock_2",
        config_entry=config_entry,
        device_id=device.id,
    )
    assert dup_entry.entity_id == "button.kitchen_light_lock_2"

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    # The entry must have migrated to canonical unique_id and stripped _2
    entry_after = entity_reg.async_get_entity_id("button", DOMAIN, "00:03:50:81:17:77-1-31-disable")
    assert entry_after == "button.kitchen_light_lock"


@pytest.mark.asyncio
async def test_issue_269_migration_cover_button_resolves_who_2(hass: HomeAssistant):
    """Verify that button migration properly resolves who=2 for cover devices and prunes duplicates."""
    entity_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.57",
            "port": 20000,
            "password": "pass",
            "mac": "00:03:50:81:17:78",
        },
        unique_id="00:03:50:81:17:78",
    )
    config_entry.add_to_hass(hass)

    cover_dev = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "00:03:50:81:17:78-2-22")},
        name="Living Room Shutter",
    )

    # 0.9.4 canonical button
    canonical_btn = entity_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id="00:03:50:81:17:78-2-22-disable",
        suggested_object_id="living_room_shutter_lock",
        config_entry=config_entry,
        device_id=cover_dev.id,
    )
    assert canonical_btn.entity_id == "button.living_room_shutter_lock"

    # 2.0b3 duplicate button without who
    dup_btn = entity_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id="00:03:50:81:17:78-22-disable",
        suggested_object_id="living_room_shutter_lock_2",
        config_entry=config_entry,
        device_id=cover_dev.id,
    )
    assert dup_btn.entity_id == "button.living_room_shutter_lock_2"

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    # The duplicate 2.0b3 cover button must be pruned
    assert entity_reg.async_get("button.living_room_shutter_lock_2") is None
    # The original canonical cover button must remain
    assert entity_reg.async_get("button.living_room_shutter_lock") is not None
    assert (
        entity_reg.async_get("button.living_room_shutter_lock").unique_id
        == "00:03:50:81:17:78-2-22-disable"
    )


@pytest.mark.asyncio
async def test_issue_269_migration_normalizes_unformatted_mac(hass: HomeAssistant):
    """Verify that unformatted raw hex MAC addresses in button unique IDs are normalized to colon format."""
    entity_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.58",
            "port": 20000,
            "password": "pass",
            "mac": "00:03:50:81:17:79",
        },
        unique_id="00:03:50:81:17:79",
    )
    config_entry.add_to_hass(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "00:03:50:81:17:79-1-25")},
        name="Corridor Light",
    )

    # Button entry with unformatted MAC (no colons)
    entry = entity_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id="000350811779-1-25-disable",
        suggested_object_id="corridor_light_lock",
        config_entry=config_entry,
        device_id=device.id,
    )
    assert entry.entity_id == "button.corridor_light_lock"

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    migrated = entity_reg.async_get("button.corridor_light_lock")
    assert migrated is not None
    assert migrated.unique_id == "00:03:50:81:17:79-1-25-disable"


@pytest.mark.asyncio
async def test_issue_268_climate_friendly_name_restored_from_myhome_yaml(hass: HomeAssistant):
    """Verify that climate entities configured in myhome.yaml under climate: preserve friendly name upon migration/restoration."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "00:03:50:81:17:76"

    entity_reg = er.async_get(hass)

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.52",
            "port": 20000,
            "password": "pass",
            "mac": "00:03:50:81:17:76",
        },
        unique_id="00:03:50:81:17:76",
    )
    config_entry.add_to_hass(hass)

    # Pre-existing entity from 0.9.4 in entity registry
    entity_reg.async_get_or_create(
        domain="climate",
        platform=DOMAIN,
        unique_id="00:03:50:81:17:76-4-1",
        suggested_object_id="soggiorno",
        config_entry=config_entry,
    )

    # Configuration loaded from myhome.yaml with zone: '1', name: 'Soggiorno'
    # as specified in issue #268
    climate_cfg = {
        "who": "4",
        "zone": "1",
        "name": "Soggiorno",
        "heat": True,
        "cool": True,
        "standalone": True,
        "manufacturer": "BTicino",
        "model": "KM4691",
        "entities": {},
    }

    hass.data.setdefault(DOMAIN, {})[config_entry.data[CONF_MAC]] = {
        CONF_PLATFORMS: {
            CLIMATE_DOMAIN: {
                # In 0.9.4/validate.py, key is 4-1
                "4-1": climate_cfg,
                # In __init__.py with our fix, aliases '1' and 'zone_1' are also indexed
                "1": climate_cfg,
                "zone_1": climate_cfg,
            }
        },
        CONF_ENTITY: mock_gateway,
    }

    added_entities = []
    await async_setup_climate_entry(hass, config_entry, lambda ents: added_entities.extend(ents))

    assert len(added_entities) == 1
    climate_entity = added_entities[0]
    assert isinstance(climate_entity, MyHOMEClimate)

    # Name must be Soggiorno, NOT "Climate Zone 1"
    assert climate_entity.name == "Soggiorno"
    assert climate_entity.device_info["name"] == "Soggiorno"
    assert climate_entity.device_info["manufacturer"] == "BTicino"
    assert climate_entity.device_info["model"] == "KM4691"
    assert climate_entity.unique_id == "00:03:50:81:17:76-4-1"


@pytest.mark.asyncio
async def test_issue_268_climate_discovery_preserves_name_via_bus_message(hass: HomeAssistant):
    """Verify that climate entities discovered dynamically via bus messages preserve their configured friendly name."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "00:03:50:81:17:76"

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.54",
            "port": 20000,
            "password": "pass",
            "mac": "00:03:50:81:17:76",
        },
        unique_id="00:03:50:81:17:76",
    )
    config_entry.add_to_hass(hass)

    climate_cfg = {
        "who": "4",
        "zone": "2",
        "name": "Camera da letto",
        "heat": True,
        "cool": True,
        "standalone": True,
        "manufacturer": "BTicino",
        "model": "KM4691",
        "entities": {},
    }

    hass.data.setdefault(DOMAIN, {})[config_entry.data[CONF_MAC]] = {
        CONF_PLATFORMS: {
            CLIMATE_DOMAIN: {}
        },
        CONF_ENTITY: mock_gateway,
    }

    added_entities = []
    await async_setup_climate_entry(hass, config_entry, lambda ents: added_entities.extend(ents))

    # Initially no entity in registry or config, so added_entities is empty
    assert len(added_entities) == 0

    # Populate configuration before message arrives from bus
    hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS][CLIMATE_DOMAIN]["4-2"] = climate_cfg

    # Simulate bus message arriving for zone 2
    bus_message = MagicMock(spec=OWNHeatingEvent)
    bus_message.where = "2"
    bus_message.zone = 2
    bus_message.what = "0"
    bus_message.interface = None
    bus_message.who = "4"
    bus_message.what_param = []
    bus_message.where_param = []

    async_dispatcher_send(hass, f"myhome_message_{config_entry.data[CONF_MAC]}", bus_message)
    await hass.async_block_till_done()

    assert len(added_entities) == 1
    climate_entity = added_entities[0]
    assert climate_entity.name == "Camera da letto"
    assert climate_entity.device_info["name"] == "Camera da letto"


@pytest.mark.asyncio
async def test_issue_268_climate_central_unit_name_restoration(hass: HomeAssistant):
    """Verify that central unit zone (#0 or 0) correctly restores custom friendly name and central flag."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "00:03:50:81:17:76"

    entity_reg = er.async_get(hass)

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.55",
            "port": 20000,
            "password": "pass",
            "mac": "00:03:50:81:17:76",
        },
        unique_id="00:03:50:81:17:76",
    )
    config_entry.add_to_hass(hass)

    # Pre-existing entity from 0.9.4 in entity registry for central unit
    entity_reg.async_get_or_create(
        domain="climate",
        platform=DOMAIN,
        unique_id="00:03:50:81:17:76-4-#0",
        suggested_object_id="centrale_termica",
        config_entry=config_entry,
    )

    climate_cfg = {
        "who": "4",
        "zone": "#0",
        "name": "Centrale Termica",
        "heat": True,
        "cool": True,
        "standalone": False,
        "central": True,
        "manufacturer": "BTicino",
        "model": "3550",
        "entities": {},
    }

    hass.data.setdefault(DOMAIN, {})[config_entry.data[CONF_MAC]] = {
        CONF_PLATFORMS: {
            CLIMATE_DOMAIN: {
                "4-#0": climate_cfg,
                "#0": climate_cfg,
                "0": climate_cfg,
                "zone_0": climate_cfg,
            }
        },
        CONF_ENTITY: mock_gateway,
    }

    added_entities = []
    await async_setup_climate_entry(hass, config_entry, lambda ents: added_entities.extend(ents))

    assert len(added_entities) == 1
    climate_entity = added_entities[0]
    assert climate_entity.name == "Centrale Termica"
    assert climate_entity._central is True


@pytest.mark.asyncio
async def test_issue_268_climate_customize_yaml_fallback(hass: HomeAssistant):
    """Verify that customizations (e.g. customize.yaml) are used when no name is in YAML."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "00:03:50:81:17:76"

    entity_reg = er.async_get(hass)

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.56",
            "port": 20000,
            "password": "pass",
            "mac": "00:03:50:81:17:76",
        },
        unique_id="00:03:50:81:17:76",
    )
    config_entry.add_to_hass(hass)

    reg_entry = entity_reg.async_get_or_create(
        domain="climate",
        platform=DOMAIN,
        unique_id="00:03:50:81:17:76-4-3",
        suggested_object_id="zone_3",
        config_entry=config_entry,
    )

    # Empty config without name
    climate_cfg = {
        "who": "4",
        "zone": "3",
        "entities": {},
    }

    hass.data.setdefault(DOMAIN, {})[config_entry.data[CONF_MAC]] = {
        CONF_PLATFORMS: {
            CLIMATE_DOMAIN: {
                "4-3": climate_cfg,
                "3": climate_cfg,
            }
        },
        CONF_ENTITY: mock_gateway,
    }
    # Set customizations
    hass.data[DOMAIN]["customizations"] = {
        reg_entry.entity_id: {"friendly_name": "Salone Principale"}
    }

    added_entities = []
    await async_setup_climate_entry(hass, config_entry, lambda ents: added_entities.extend(ents))

    assert len(added_entities) == 1
    climate_entity = added_entities[0]
    assert climate_entity.name == "Salone Principale"


def test_issue_268_validate_schema_climate_aliases():
    """Verify that climate_schema generates all alias keys for integer and string zones."""
    raw = {
        "zone_1": {
            "zone": "1",
            "name": "Living Room",
        },
        "zone_2": {
            "zone": "2",
            "name": "Bedroom",
        },
        "central_unit": {
            "zone": "#0",
            "name": "Central Heating",
            "central": True,
        },
    }
    validated = climate_schema(raw)
    assert "4-1" in validated
    assert "1" in validated
    assert "zone_1" in validated
    assert validated["1"]["name"] == "Living Room"
    assert validated["zone_1"]["name"] == "Living Room"

    assert "4-2" in validated
    assert "2" in validated
    assert "zone_2" in validated
    assert validated["2"]["name"] == "Bedroom"

    assert "4-#0" in validated
    assert "#0" in validated
    assert "0" in validated
    assert "zone_0" in validated
    assert validated["central_unit"]["name"] == "Central Heating"


@pytest.mark.asyncio
async def test_issue_268_climate_yaml_indexing_in_init(hass: HomeAssistant):
    """Verify that __init__.py correctly indexes climate configuration from yaml into hass.data."""
    raw_yaml = {
        "00:03:50:81:17:76": {
            "mac": "00:03:50:81:17:76",
            "climate": {
                "zone_1": {
                    "zone": "1",
                    "name": "Soggiorno",
                    "heat": True,
                    "cool": True,
                    "standalone": True,
                    "manufacturer": "BTicino",
                    "model": "KM4691",
                }
            }
        }
    }

    real_isfile = os.path.isfile

    def fake_isfile(p):
        if "myhome.yaml" in str(p):
            return True
        return real_isfile(p)

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ), patch(
        "os.path.isfile", side_effect=fake_isfile
    ), patch(
        "homeassistant.util.yaml.loader.load_yaml", return_value=raw_yaml
    ):
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.1.53",
                "port": 20000,
                "password": "pass",
                "mac": "00:03:50:81:17:76",
            },
            unique_id="00:03:50:81:17:76",
        )
        config_entry.add_to_hass(hass)

        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Check that climate configurations are indexed under 4-1, 1, and zone_1
        climate_data = hass.data[DOMAIN]["00:03:50:81:17:76"][CONF_PLATFORMS]["climate"]
        assert "4-1" in climate_data
        assert "1" in climate_data
        assert "zone_1" in climate_data
        assert climate_data["1"]["name"] == "Soggiorno"
        assert climate_data["4-1"]["name"] == "Soggiorno"
        assert climate_data["zone_1"]["name"] == "Soggiorno"


@pytest.mark.asyncio
async def test_issue_269_migration_button_who_fallback_platforms(hass: HomeAssistant):
    """Verify that when button device has no who, migration falls back to cover or light platforms."""
    entity_reg = er.async_get(hass)
    mac = "00:03:50:81:17:80"

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.59",
            "port": 20000,
            "password": "pass",
            "mac": mac,
        },
        unique_id=mac,
    )
    config_entry.add_to_hass(hass)

    # Pre-seed gateway platform data with cover 22
    hass.data.setdefault(DOMAIN, {})[mac] = {
        CONF_PLATFORMS: {
            "cover": {"22": {"where": "22"}}
        }
    }

    # Case A: where=22 matches cover in gw_platforms -> resolves who="2"
    btn_cover = entity_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{mac}-22-disable",
        suggested_object_id="cover_btn",
        config_entry=config_entry,
    )
    # Case B: where=23 does not match cover -> falls back to who="1"
    btn_light = entity_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{mac}-23-disable",
        suggested_object_id="light_btn",
        config_entry=config_entry,
    )

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    cover_migrated = entity_reg.async_get(btn_cover.entity_id)
    assert cover_migrated.unique_id == f"{mac}-2-22-disable"

    light_migrated = entity_reg.async_get(btn_light.entity_id)
    assert light_migrated.unique_id == f"{mac}-1-23-disable"


@pytest.mark.asyncio
async def test_issue_269_migration_unformatted_mac_non_button(hass: HomeAssistant):
    """Verify migration normalizes unformatted MAC for non-button entities (light/cover)."""
    entity_reg = er.async_get(hass)
    raw_mac = "000350811781"
    formatted_mac = "00:03:50:81:17:81"

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.60",
            "port": 20000,
            "password": "pass",
            "mac": formatted_mac,
        },
        unique_id=formatted_mac,
    )
    config_entry.add_to_hass(hass)

    # Light with unformatted MAC and already containing who (e.g. 000350811781-1-21)
    light_entry = entity_reg.async_get_or_create(
        domain="light",
        platform=DOMAIN,
        unique_id=f"{raw_mac}-1-21",
        suggested_object_id="office_light_raw_mac",
        config_entry=config_entry,
    )

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    migrated = entity_reg.async_get(light_entry.entity_id)
    assert migrated.unique_id == f"{formatted_mac}-1-21"


@pytest.mark.asyncio
async def test_issue_269_migration_error_handling(hass: HomeAssistant):
    """Verify error handling during button pruning and entity unique_id updates."""
    entity_reg = er.async_get(hass)
    mac = "00:03:50:81:17:82"
    raw_mac = "000350811782"

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.61",
            "port": 20000,
            "password": "pass",
            "mac": mac,
        },
        unique_id=mac,
    )
    config_entry.add_to_hass(hass)

    # 1. Duplicate button where prune fails (exception in async_remove)
    entity_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{mac}-1-10-disable",
        suggested_object_id="canonical_btn",
        config_entry=config_entry,
    )
    entity_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{mac}-10-disable",
        suggested_object_id="dup_btn",
        config_entry=config_entry,
    )

    # 2. Standalone button where async_update_entity raises ValueError
    entity_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{mac}-11-disable",
        suggested_object_id="fail_update_btn",
        config_entry=config_entry,
    )

    # 3. Button with raw MAC where async_update_entity raises ValueError
    entity_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{raw_mac}-1-12-disable",
        suggested_object_id="fail_raw_btn",
        config_entry=config_entry,
    )

    # 4. Light with raw MAC where async_update_entity raises ValueError
    entity_reg.async_get_or_create(
        domain="light",
        platform=DOMAIN,
        unique_id=f"{raw_mac}-1-13",
        suggested_object_id="fail_raw_light",
        config_entry=config_entry,
    )

    orig_remove = entity_reg.async_remove
    orig_update = entity_reg.async_update_entity

    def mock_remove(entity_id):
        if "dup_btn" in entity_id:
            raise RuntimeError("prune failure")
        return orig_remove(entity_id)

    def mock_update(entity_id, **kwargs):
        if any(tag in entity_id for tag in ("fail_update_btn", "fail_raw_btn", "fail_raw_light")):
            raise ValueError("update error")
        return orig_update(entity_id, **kwargs)

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ), patch.object(
        entity_reg, "async_remove", side_effect=mock_remove
    ), patch.object(
        entity_reg, "async_update_entity", side_effect=mock_update
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

