"""Device renames must be opt-in and conservative about existing entity IDs."""

from contextlib import ExitStack
from unittest.mock import patch
from uuid import uuid4

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_entity_naming import MAC, _entry, _gateway_patches, _setup

from custom_components.myhome.const import CONF_SYNC_ENTITY_IDS, DOMAIN
from custom_components.myhome.entity_id_sync import async_setup_entity_id_sync


@pytest.fixture
def registries(hass):
    """A loaded listener with real HA device and entity registries."""
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_SYNC_ENTITY_IDS: True})
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "lamp")}, name="Kitchen"
    )
    async_setup_entity_id_sync(hass, entry)
    return entry, devices, entities, device


async def _entity(registries, domain="light", object_id=None, **kwargs):
    """Use Core's real naming path, with explicit overrides only for custom IDs."""
    entry, _devices, entities, device = registries
    defaults = dict(config_entry=entry, device_id=device.id, has_entity_name=True)
    defaults.update(kwargs)
    defaults.setdefault("object_id_base", defaults.get("original_name"))
    name = defaults.pop("name", None)
    entity = entities.async_get_or_create(domain, DOMAIN, uuid4().hex, **defaults)
    if object_id is not None and entity.entity_id != f"{domain}.{object_id}":
        entity = entities.async_update_entity(entity.entity_id, new_entity_id=f"{domain}.{object_id}")
    if name is not None:
        entity = entities.async_update_entity(entity.entity_id, name=name)
    await entities.hass.async_block_till_done()
    return entity


async def test_rename_primary_secondary_disabled_and_reset(hass, registries):
    """Repeat renames preserve registry identity and all entity customisations."""
    _entry, devices, entities, device = registries
    area = ar.async_get(hass).async_create("Ground floor")
    devices.async_update_device(device.id, area_id=area.id)
    primary = await _entity(registries)
    entities.async_update_entity(primary.entity_id, icon="mdi:lamp")
    power = await _entity(registries, "sensor", original_name="Power")
    disabled = await _entity(
        registries,
        "sensor",
        original_name="Energy (today)",
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    for name, prefix in [("Dining room", "dining_room"), ("Salón", "salon"), (None, "kitchen")]:
        devices.async_update_device(device.id, name_by_user=name)
        await hass.async_block_till_done()
        prefix = f"ground_floor_{prefix}"
        light = entities.async_get(f"light.{prefix}")
        assert light.id == primary.id and light.unique_id == primary.unique_id
        assert devices.async_get(device.id).area_id == area.id and light.icon == "mdi:lamp"
        assert entities.async_get(f"sensor.{prefix}_power").id == power.id
        assert entities.async_get(f"sensor.{prefix}_energy_today").id == disabled.id
        assert (
            entities.async_get(f"sensor.{prefix}_energy_today").disabled_by
            is er.RegistryEntryDisabler.INTEGRATION
        )


@pytest.mark.parametrize(
    "custom",
    [
        {"object_id": "ceiling_spots"},
        {"object_id": "kitchen_custom"},
        {"object_id": "kitchen_0"},
        {"object_id": "kitchen_1"},
        {"object_id": "kitchen_02"},
        {"object_id": "kitchen_2_custom"},
        {"name": "Ceiling spots"},
        {"has_entity_name": False},
    ],
)
async def test_preserve_custom_and_ambiguous_ids(hass, registries, custom):
    _, devices, entities, device = registries
    entity = await _entity(registries, **custom)
    devices.async_update_device(device.id, name_by_user="Dining room")
    await hass.async_block_till_done()
    assert entities.async_get(entity.entity_id).id == entity.id
    assert entities.async_get("light.dining_room") is None


async def test_ownership_and_unrelated_device_updates(hass, registries):
    entry, devices, entities, device = registries
    other = MockConfigEntry(domain=DOMAIN)
    other.add_to_hass(hass)
    foreign = MockConfigEntry(domain="test")
    foreign.add_to_hass(hass)
    devices.async_get_or_create(config_entry_id=other.entry_id, identifiers=device.identifiers)
    devices.async_get_or_create(config_entry_id=foreign.entry_id, identifiers=device.identifiers)
    other_entity = await _entity(
        registries, "sensor", "kitchen_power", config_entry=other, original_name="Power"
    )
    foreign_entity = entities.async_get_or_create(
        "switch",
        "test",
        "foreign",
        config_entry=foreign,
        device_id=device.id,
        suggested_object_id="kitchen",
        has_entity_name=True,
    )
    unrelated = devices.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={(DOMAIN, "other")}, name="Kitchen"
    )
    own = await _entity(registries)
    devices.async_update_device(unrelated.id, name_by_user="Elsewhere")
    devices.async_update_device(device.id, area_id="somewhere")
    await hass.async_block_till_done()
    assert entities.async_get(own.entity_id) is not None
    devices.async_update_device(device.id, name_by_user="Dining room")
    await hass.async_block_till_done()
    assert entities.async_get("light.dining_room").id == own.id
    assert entities.async_get(other_entity.entity_id).id == other_entity.id
    assert entities.async_get(foreign_entity.entity_id).id == foreign_entity.id


@pytest.mark.parametrize("object_id", ["kitchen", "kitchen_2"])
@pytest.mark.parametrize("occupied", ["registry", "disabled", "state"])
async def test_target_collision_uses_core_available_suffix(
    hass, registries, occupied, object_id
):
    _, devices, entities, device = registries
    if object_id == "kitchen_2":
        hass.states.async_set("light.kitchen", "on")
    original = await _entity(registries, object_id=object_id)
    if occupied == "state":
        hass.states.async_set("light.dining_room", "on")
    else:
        target = await _entity(
            registries,
            object_id="dining_room",
            disabled_by=(er.RegistryEntryDisabler.USER if occupied == "disabled" else None),
        )
    devices.async_update_device(device.id, name_by_user="Dining room")
    await hass.async_block_till_done()
    assert entities.async_get(original.entity_id) is None
    assert entities.async_get("light.dining_room_2").id == original.id
    if occupied != "state":
        assert entities.async_get("light.dining_room").id == target.id
    else:
        assert hass.states.get("light.dining_room").state == "on"


@pytest.mark.parametrize("new_name", ["KITCHEN", "!!!", "", "   "])
async def test_empty_or_unchanged_slug(hass, registries, new_name):
    _, devices, entities, device = registries
    original = await _entity(registries)
    devices.async_update_device(device.id, name_by_user=new_name)
    await hass.async_block_till_done()
    assert entities.async_get(original.entity_id).id == original.id


@pytest.mark.parametrize("old_name", [None, "!!!"])
async def test_no_usable_previous_device_name(hass, registries, old_name):
    _, devices, entities, device = registries
    original = await _entity(registries)
    devices.async_update_device(device.id, name=old_name)
    await hass.async_block_till_done()
    devices.async_update_device(device.id, name_by_user="Dining room")
    await hass.async_block_till_done()
    assert entities.async_get(original.entity_id).id == original.id


async def test_integration_names_and_removed_devices_are_ignored(hass, registries):
    _, devices, entities, device = registries
    original = await _entity(registries)
    devices.async_update_device(device.id, name="Dining room")
    await hass.async_block_till_done()
    assert entities.async_get(original.entity_id).id == original.id
    # An update queued just before the device is deleted must be harmless.
    devices.async_update_device(device.id, name_by_user="Bedroom")
    devices.async_remove_device(device.id)
    hass.bus.async_fire(
        dr.EVENT_DEVICE_REGISTRY_UPDATED,
        {
            "action": "update",
            "device_id": "removed",
            "changes": {"name_by_user": None},
        },
    )
    await hass.async_block_till_done()


async def test_invalid_target_does_not_abort_other_entities(hass, registries, caplog):
    _, devices, entities, device = registries
    primary = await _entity(registries)
    secondary = await _entity(registries, "sensor", original_name="Power")
    update = entities.async_update_entity

    def reject_primary(entity_id, **kwargs):
        if entity_id == primary.entity_id:
            raise ValueError("invalid target")
        return update(entity_id, **kwargs)

    with patch.object(entities, "async_update_entity", side_effect=reject_primary):
        devices.async_update_device(device.id, name_by_user="Dining room")
        await hass.async_block_till_done()
    assert entities.async_get(primary.entity_id).id == primary.id
    assert entities.async_get("sensor.dining_room_power").id == secondary.id
    assert "invalid target" in caplog.text


async def test_opt_in_does_not_catch_up_existing_ids(hass, tmp_path):
    """Upgrading/enabling/reloading never migrates an already configured device."""
    entry = _entry(tmp_path)
    await _setup(hass, entry)
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    original = entities.async_get("light.kitchen_light")
    devices.async_update_device(original.device_id, name_by_user="Already renamed")
    await hass.async_block_till_done()
    assert entities.async_get(original.entity_id).id == original.id
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_SYNC_ENTITY_IDS: True}
    )
    patches = _gateway_patches()
    for p in patches:
        p.start()
    try:
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert entities.async_get(original.entity_id).id == original.id
        devices.async_update_device(original.device_id, name_by_user="Another name")
        await hass.async_block_till_done()
        # The ID no longer matches the old device name, so leave it untouched.
        assert entities.async_get(original.entity_id).id == original.id
    finally:
        for p in patches:
            p.stop()


async def test_live_entity_rename_reload_and_unload(hass: HomeAssistant, tmp_path):
    """The setup hook renames live entities and unregisters on unload."""
    base = _entry(tmp_path)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=base.data,
        unique_id=base.unique_id,
        options={**base.options, CONF_SYNC_ENTITY_IDS: True},
    )
    await _setup(hass, entry)
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    original = entities.async_get("light.kitchen_light")
    devices.async_update_device(original.device_id, name_by_user="Dining room")
    await hass.async_block_till_done()
    assert hass.states.get("light.kitchen_light") is None
    assert hass.states.get("light.dining_room") is not None
    assert entities.async_get("light.dining_room").id == original.id
    assert hass.states.get("button.dining_room_lock") is not None
    entry.runtime_data.gateway._available = True
    with patch.object(entry.runtime_data.gateway, "send") as send:
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.dining_room"}, blocking=True
        )
        send.assert_called_once()
    patches = _gateway_patches()
    for p in patches:
        p.start()
    try:
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert entities.async_get("light.dining_room").id == original.id
        assert await hass.config_entries.async_unload(entry.entry_id)
        devices.async_update_device(original.device_id, name_by_user="Renamed while unloaded")
        await hass.async_block_till_done()
        assert entities.async_get("light.dining_room").id == original.id
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entities.async_get("light.dining_room").id == original.id
    finally:
        for p in patches:
            p.stop()


@pytest.mark.parametrize(
    "stored, submitted, expected",
    [
        (None, None, False),
        (None, True, True),
        (True, None, True),
        (True, False, False),
    ],
)
async def test_options_opt_in_default_enable_preserve_and_disable(
    hass, stored, submitted, expected
):
    """Unrelated options edits must not silently enable or disable ID updates."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.0.2.10", "port": 20000, "mac": "00:03:50:00:12:34", "name": "F454"},
        options={} if stored is None else {CONF_SYNC_ENTITY_IDS: stored},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.myhome.config_flow.find_gateways"):
        result = await hass.config_entries.options.async_init(entry.entry_id)
    schema_key = next(k for k in result["data_schema"].schema if k == CONF_SYNC_ENTITY_IDS)
    assert schema_key.description["suggested_value"] is bool(stored)
    user_input = {"command_worker_count": 1, "generate_events": False, "address": "192.0.2.10"}
    if submitted is not None:
        user_input[CONF_SYNC_ENTITY_IDS] = submitted
    with (
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value={"Success": True},
        ),
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input=user_input
        )
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.options.get(CONF_SYNC_ENTITY_IDS, False) is expected
    if stored is None and submitted is None:
        assert CONF_SYNC_ENTITY_IDS not in entry.options


@pytest.mark.parametrize("number", [2, 3, 10, 20, 123])
async def test_numbered_sensor_id_keeps_entity_suffix(hass, registries, number):
    """Only the old collision number disappears; the sensor's own name survives."""
    _, devices, entities, device = registries
    for suffix in [""] + [f"_{n}" for n in range(2, number)]:
        hass.states.async_set(f"sensor.kitchen_power{suffix}", "0")
    original = await _entity(
        registries,
        "sensor",
        original_name="Power",
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    assert original.entity_id == f"sensor.kitchen_power_{number}"
    devices.async_update_device(device.id, name_by_user="Dining room")
    await hass.async_block_till_done()
    renamed = entities.async_get("sensor.dining_room_power")
    assert renamed.id == original.id and renamed.unique_id == original.unique_id
    assert renamed.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_discovered_duplicate_light_01_renames_to_luce_2(hass, tmp_path):
    """Reproduce the user's device-page rename with an actual HA-generated _2 ID."""
    base = _entry(tmp_path)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=base.data,
        unique_id=base.unique_id,
        options={**base.options, CONF_SYNC_ENTITY_IDS: True},
    )
    with ExitStack() as stack:
        for p in _gateway_patches():
            stack.enter_context(p)
        await _setup(hass, entry)
        entities = er.async_get(hass)
        existing = entities.async_get_or_create(
            "light", "test", "existing-light", suggested_object_id="light_01"
        )
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNMessage.parse("*1*1*01##"))
        await hass.async_block_till_done()
        original = entities.async_get("light.light_01_2")
        assert original is not None
        devices = dr.async_get(hass)
        assert devices.async_get(original.device_id).name == "Light 01"
        devices.async_update_device(original.device_id, name_by_user="Luce 2")
        await hass.async_block_till_done()
        renamed = entities.async_get("light.luce_2")
        assert renamed.id == original.id and renamed.unique_id == original.unique_id
        assert entities.async_get("light.light_01").id == existing.id
        assert hass.states.get("light.light_01_2") is None
        assert hass.states.get("light.luce_2").attributes["friendly_name"] == "Luce 2"
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert entities.async_get("light.luce_2").id == original.id
        assert hass.states.get("light.luce_2") is not None


async def test_previously_renamed_numbered_entity_can_be_retried(hass, registries):
    """Do not catch up automatically; restoring the original name allows a retry."""
    _, devices, entities, device = registries
    devices.async_update_device(device.id, name="Light 01", name_by_user="Luce 1")
    await hass.async_block_till_done()
    hass.states.async_set("light.light_01", "on")
    original = await _entity(registries, object_id="light_01_2")
    devices.async_update_device(device.id, name_by_user="Luce 2")
    await hass.async_block_till_done()
    assert entities.async_get(original.entity_id).id == original.id
    devices.async_update_device(device.id, name_by_user="Light 01")
    await hass.async_block_till_done()
    assert entities.async_get(original.entity_id).id == original.id
    devices.async_update_device(device.id, name_by_user="Luce 2")
    await hass.async_block_till_done()
    assert entities.async_get("light.luce_2").id == original.id


async def test_new_device_name_already_matches_numbered_id(hass, registries, caplog):
    """Turning the previous collision suffix into the chosen name is a no-op."""
    _, devices, entities, device = registries
    hass.states.async_set("light.kitchen", "on")
    original = await _entity(registries, object_id="kitchen_2")
    devices.async_update_device(device.id, name_by_user="Kitchen 2")
    await hass.async_block_till_done()
    assert entities.async_get(original.entity_id).id == original.id
    assert "would collide" not in caplog.text


async def test_italian_live_entities_follow_native_id_language(hass, tmp_path):
    """An Italian installation retains Core’s selected language for entity IDs."""
    hass.config.language = "it"
    base = _entry(tmp_path)
    entry = MockConfigEntry(
        domain=DOMAIN, data=base.data, unique_id=base.unique_id,
        options={**base.options, CONF_SYNC_ENTITY_IDS: True},
    )
    await _setup(hass, entry)
    entities = er.async_get(hass)
    original = entities.async_get("button.kitchen_light_blocca")
    assert original.original_name == "Blocca"
    assert original.object_id_base == "Blocca"
    dr.async_get(hass).async_update_device(original.device_id, name_by_user="Luce cucina")
    await hass.async_block_till_done()
    renamed = entities.async_get("button.luce_cucina_blocca")
    assert renamed.id == original.id
    assert renamed.original_name == "Blocca"
    assert hass.states.get(renamed.entity_id).attributes["friendly_name"] == "Luce cucina Blocca"


async def test_entity_area_and_configured_naming_parts(hass, registries):
    """Respect native name ordering and an entity area overriding its device area."""
    _, devices, entities, device = registries
    areas = ar.async_get(hass)
    downstairs = areas.async_create("Downstairs")
    upstairs = areas.async_create("Upstairs")
    devices.async_update_device(device.id, area_id=downstairs.id)
    entities.async_update_settings(entity_id_parts=[
        er.EntityNamePart.DEVICE, er.EntityNamePart.AREA, er.EntityNamePart.ENTITY,
    ])
    original = await _entity(registries, "sensor", original_name="Potenza", object_id_base="Power")
    assert original.entity_id == "sensor.kitchen_downstairs_power"
    devices.async_update_device(device.id, name_by_user="Dining room")
    await hass.async_block_till_done()
    original = entities.async_get("sensor.dining_room_downstairs_power")
    assert original is not None
    original = entities.async_update_entity(original.entity_id, area_id=upstairs.id)
    original = entities.async_update_entity(
        original.entity_id, new_entity_id=entities.async_regenerate_entity_id(original)
    )
    await hass.async_block_till_done()
    assert original.entity_id == "sensor.upstairs_power"
    devices.async_update_device(device.id, name_by_user="Bedroom")
    await hass.async_block_till_done()
    renamed = entities.async_get("sensor.upstairs_power")
    assert renamed.id == original.id and renamed.area_id == upstairs.id


async def test_manual_edit_after_tracking_and_entity_removal(hass, registries):
    _, devices, entities, device = registries
    original = await _entity(registries)
    custom = entities.async_update_entity(original.entity_id, new_entity_id="light.ceiling_spots")
    removed = await _entity(registries, "sensor", original_name="Power")
    entities.async_remove(removed.entity_id)
    await hass.async_block_till_done()
    devices.async_update_device(device.id, name_by_user="Dining room")
    await hass.async_block_till_done()
    assert entities.async_get(custom.entity_id).id == original.id
    assert entities.async_get("light.dining_room") is None
    assert entities.async_get("sensor.dining_room_power") is None


async def test_area_changes_do_not_catch_up_old_ids(hass, registries):
    """An ID no longer matching Core's current default remains untouched."""
    _, devices, entities, device = registries
    area = ar.async_get(hass).async_create("Ground floor")
    devices.async_update_device(device.id, area_id=area.id)
    original = await _entity(registries)
    ar.async_get(hass).async_update(area.id, name="Upstairs")
    await hass.async_block_till_done()
    devices.async_update_device(device.id, name_by_user="Dining room")
    await hass.async_block_till_done()
    assert entities.async_get(original.entity_id).id == original.id


async def test_no_device_and_suggested_ids_are_not_rewritten(hass, registries):
    _, devices, entities, device = registries
    standalone = await _entity(registries, "sensor", device_id=None, original_name="Power")
    suggested = await _entity(registries, suggested_object_id="fixed_id")
    devices.async_update_device(device.id, name_by_user="Dining room")
    await hass.async_block_till_done()
    assert entities.async_get(standalone.entity_id).id == standalone.id
    assert entities.async_get(suggested.entity_id).id == suggested.id


async def test_vacated_collision_suffix_is_conservatively_preserved(hass):
    """Core cannot prove the origin of an old suffix when lower IDs are now free."""
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_SYNC_ENTITY_IDS: True})
    entry.add_to_hass(hass)
    devices, entities = dr.async_get(hass), er.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "lamp")}, name="Kitchen"
    )
    hass.states.async_set("light.kitchen", "on")
    original = await _entity((entry, devices, entities, device))
    assert original.entity_id == "light.kitchen_2"
    hass.states.async_remove("light.kitchen")
    async_setup_entity_id_sync(hass, entry)
    devices.async_update_device(device.id, name_by_user="Dining room")
    await hass.async_block_till_done()
    assert entities.async_get(original.entity_id).id == original.id


@pytest.mark.parametrize("domain, translated, canonical", [
    ("button", "Blocca", "Lock"), ("sensor", "Potenza", "Power"),
])
async def test_localized_original_name_does_not_define_id(hass, registries, domain, translated, canonical):
    """A registry's canonical ID base can differ from its translated display name."""
    _, devices, entities, device = registries
    original = await _entity(
        registries, domain, original_name=translated, object_id_base=canonical,
    )
    assert original.entity_id == f"{domain}.kitchen_{canonical.lower()}"
    devices.async_update_device(device.id, name_by_user="Luce cucina")
    await hass.async_block_till_done()
    renamed = entities.async_get(f"{domain}.luce_cucina_{canonical.lower()}")
    assert renamed.id == original.id and renamed.original_name == translated


async def test_custom_name_edit_after_tracking_is_preserved(hass, registries):
    """A previously eligible entity becomes ineligible after a display-name edit."""
    _, devices, entities, device = registries
    original = await _entity(registries)
    entities.async_update_entity(original.entity_id, name="Ceiling spots")
    devices.async_update_device(device.id, name_by_user="Dining room")
    await hass.async_block_till_done()
    assert entities.async_get(original.entity_id).name == "Ceiling spots"
    assert entities.async_get("light.dining_room") is None
