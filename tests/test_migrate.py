"""Standalone unit tests for migrate module."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import DOMAIN
from custom_components.myhome.migrate import (
    _device_for_identifier,
    async_migrate_entry_and_registries,
    async_prune_stale_devices,
    migrate_entry_and_registries,
    prune_stale_devices,
)


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Create and register a test config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Gateway",
        data={CONF_MAC: "00:03:50:81:22:33"},
        unique_id="00:03:50:81:22:33",
        entry_id="test_entry_migrate",
    )
    entry.add_to_hass(hass)
    return entry


# ── 1. Config Entry unique_id Normalization ─────────────────────────────────


def test_migrate_config_entry_unique_id(hass: HomeAssistant):
    """Config entry with unformatted MAC unique_id is normalized to formatted MAC."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Unformatted Gateway",
        data={CONF_MAC: "00:03:50:81:22:33"},
        unique_id="000350812233",  # Unformatted hex
        entry_id="unformatted_entry",
    )
    entry.add_to_hass(hass)

    migrate_entry_and_registries(hass, entry, {})

    assert entry.unique_id == "00:03:50:81:22:33"


# ── 2. Entity unique_id Resurrection (MAC-WHERE -> MAC-WHO-WHERE) ───────────


@pytest.mark.parametrize(
    ("domain", "expected_who", "where"),
    [
        ("light", "1", "12"),
        ("cover", "2", "25"),
        ("switch", "1", "31"),
        ("media_player", "16", "1"),
        ("climate", "4", "2"),
    ],
)
def test_migrate_entity_unique_id_per_domain(
    hass: HomeAssistant, config_entry: MockConfigEntry, domain: str, expected_who: str, where: str
):
    """Test old MAC-WHERE unique_ids are migrated to canonical MAC-WHO-WHERE per domain."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    mac = "00:03:50:81:22:33"
    old_unique_id = f"{mac}-{where}"
    expected_unique_id = f"{mac}-{expected_who}-{where}"

    # Register old device and old entity
    device = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, old_unique_id)},
        name=f"Old {domain} {where}",
    )
    entity = ent_reg.async_get_or_create(
        domain=domain,
        platform=DOMAIN,
        unique_id=old_unique_id,
        config_entry=config_entry,
        device_id=device.id,
    )

    migrate_entry_and_registries(hass, config_entry, {})

    # Entity unique_id updated
    updated_entity = ent_reg.async_get(entity.entity_id)
    assert updated_entity is not None
    assert updated_entity.unique_id == expected_unique_id

    # Device identifier updated
    updated_device = dev_reg.async_get(device.id)
    assert updated_device is not None
    assert (DOMAIN, expected_unique_id) in updated_device.identifiers


def test_migrate_entity_already_canonical_skipped(
    hass: HomeAssistant, config_entry: MockConfigEntry
):
    """When the canonical unique_id already exists, migration of the old entity is skipped."""
    ent_reg = er.async_get(hass)
    mac = "00:03:50:81:22:33"

    # Pre-existing canonical entity
    ent_reg.async_get_or_create(
        domain="light",
        platform=DOMAIN,
        unique_id=f"{mac}-1-12",
        config_entry=config_entry,
        suggested_object_id="canonical_light",
    )
    # Old entity with MAC-WHERE
    old_entity = ent_reg.async_get_or_create(
        domain="light",
        platform=DOMAIN,
        unique_id=f"{mac}-12",
        config_entry=config_entry,
        suggested_object_id="old_light",
    )

    migrate_entry_and_registries(hass, config_entry, {})

    # Old entity remains untouched because canonical was already present
    assert ent_reg.async_get(old_entity.entity_id).unique_id == f"{mac}-12"


def test_migrate_entity_mismatched_mac_ignored(
    hass: HomeAssistant, config_entry: MockConfigEntry
):
    """Entities from a different gateway are not modified."""
    ent_reg = er.async_get(hass)
    entity = ent_reg.async_get_or_create(
        domain="light",
        platform=DOMAIN,
        unique_id="00:03:50:99:99:99-12",
        config_entry=config_entry,
    )

    migrate_entry_and_registries(hass, config_entry, {})

    assert ent_reg.async_get(entity.entity_id).unique_id == "00:03:50:99:99:99-12"


def test_migrate_entity_unformatted_mac_normalized(
    hass: HomeAssistant, config_entry: MockConfigEntry
):
    """Entities with already canonical parts but raw hex MAC have MAC prefix normalized."""
    ent_reg = er.async_get(hass)
    raw_mac = "000350812233"
    entity = ent_reg.async_get_or_create(
        domain="light",
        platform=DOMAIN,
        unique_id=f"{raw_mac}-1-12",
        config_entry=config_entry,
    )

    migrate_entry_and_registries(hass, config_entry, {})

    assert ent_reg.async_get(entity.entity_id).unique_id == "00:03:50:81:22:33-1-12"


# ── 3. Button Entity Migrations ─────────────────────────────────────────────


def test_migrate_button_who_from_device(hass: HomeAssistant, config_entry: MockConfigEntry):
    """Button entity missing WHO infers WHO from its associated device identifier."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    mac = "00:03:50:81:22:33"

    # Device identifier has WHO=2 (cover)
    device = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"{mac}-2-25")},
    )
    btn_entity = ent_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{mac}-25-enable",
        config_entry=config_entry,
        device_id=device.id,
    )

    migrate_entry_and_registries(hass, config_entry, {})

    assert ent_reg.async_get(btn_entity.entity_id).unique_id == f"{mac}-2-25-enable"


def test_migrate_button_who_from_configured_platforms(
    hass: HomeAssistant, config_entry: MockConfigEntry
):
    """Button entity without device infers WHO=2 from configured_platforms cover mapping."""
    ent_reg = er.async_get(hass)
    mac = "00:03:50:81:22:33"

    btn_entity = ent_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{mac}-25-disable",
        config_entry=config_entry,
    )

    configured_platforms = {"cover": {"25": {"name": "Cover 25"}}}
    migrate_entry_and_registries(hass, config_entry, configured_platforms)

    assert ent_reg.async_get(btn_entity.entity_id).unique_id == f"{mac}-2-25-disable"


def test_migrate_button_who_default_1(hass: HomeAssistant, config_entry: MockConfigEntry):
    """Button entity without device or cover mapping defaults to WHO=1."""
    ent_reg = er.async_get(hass)
    mac = "00:03:50:81:22:33"

    btn_entity = ent_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{mac}-12-enable",
        config_entry=config_entry,
    )

    migrate_entry_and_registries(hass, config_entry, {})

    assert ent_reg.async_get(btn_entity.entity_id).unique_id == f"{mac}-1-12-enable"


def test_migrate_button_duplicate_pruning(hass: HomeAssistant, config_entry: MockConfigEntry):
    """Old button entity is removed when the canonical button entity already exists."""
    ent_reg = er.async_get(hass)
    mac = "00:03:50:81:22:33"

    # Pre-existing canonical button
    canonical = ent_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{mac}-1-12-enable",
        config_entry=config_entry,
        suggested_object_id="canonical_btn",
    )
    # Old legacy button
    old_btn = ent_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{mac}-12-enable",
        config_entry=config_entry,
        suggested_object_id="old_btn",
    )

    migrate_entry_and_registries(hass, config_entry, {})

    # Old button entity was pruned; canonical survives
    assert ent_reg.async_get(old_btn.entity_id) is None
    assert ent_reg.async_get(canonical.entity_id) is not None


def test_migrate_button_strip_2_entity_id(hass: HomeAssistant, config_entry: MockConfigEntry):
    """Button entity with _2 suffix is renamed to clean entity_id when base is available."""
    ent_reg = er.async_get(hass)
    mac = "00:03:50:81:22:33"

    # Explicitly register with _2 suffix
    ent_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{mac}-12-enable",
        config_entry=config_entry,
        suggested_object_id="myhome_btn_2",
    )

    migrate_entry_and_registries(hass, config_entry, {})

    updated = ent_reg.async_get_entity_id("button", DOMAIN, f"{mac}-1-12-enable")
    assert updated is not None


def test_migrate_button_already_canonical_untouched(
    hass: HomeAssistant, config_entry: MockConfigEntry
):
    """Button entity with canonical unique_id is not modified."""
    ent_reg = er.async_get(hass)
    mac = "00:03:50:81:22:33"

    canonical = ent_reg.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{mac}-1-12-enable",
        config_entry=config_entry,
        suggested_object_id="canonical_existing_btn",
    )

    migrate_entry_and_registries(hass, config_entry, {})

    assert ent_reg.async_get(canonical.entity_id).unique_id == f"{mac}-1-12-enable"


# ── 4. Stale Device Pruning ─────────────────────────────────────────────────


class MockGateway:
    """Minimal mock conforming to GatewayProtocol."""

    def __init__(self, mac: str) -> None:
        self._mac = mac

    @property
    def unique_id(self) -> str:
        return self._mac

    @property
    def id(self) -> str:
        return self._mac


def test_prune_stale_devices(hass: HomeAssistant, config_entry: MockConfigEntry):
    """Verify empty device pruning preserves gateway, CEN scenario devices, and devices with entities."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    mac = "00:03:50:81:22:33"
    gw = MockGateway(mac)

    # 1. Gateway device
    gw_dev = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, mac)},
        name="Gateway Device",
    )

    # 2. CEN scenario devices (zero entities, but must NOT be pruned)
    cen1 = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"{mac}-15-1")},
        name="CEN Scenario 1",
        model="CEN Scenario Control",
    )
    cenplus = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "cenplus_unit_2")},
        name="CEN+ Unit 2",
        model="CEN+ Scenario Control",
    )
    cenplus_who25 = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"{mac}-25-1")},
        name="CEN+ Dial 1",
    )

    # 3. Active device with entity (must NOT be pruned)
    active_dev = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"{mac}-1-12")},
        name="Active Light",
    )
    ent_reg.async_get_or_create(
        domain="light",
        platform=DOMAIN,
        unique_id=f"{mac}-1-12",
        config_entry=config_entry,
        device_id=active_dev.id,
    )

    # 4. Orphaned stale device and empty dry contact interface (0 entities, MUST be pruned)
    empty_dry_contact = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"{mac}-25-31")},
        name="Dry Contact 31",
        model="Dry Contact Interface",
    )
    stale_dev = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"{mac}-old-orphaned")},
        name="Orphaned Device",
        model="Generic Actuator",
    )

    prune_stale_devices(hass, config_entry, gateway_device_entry=gw_dev, gateway=gw)

    # Verifications
    assert dev_reg.async_get(gw_dev.id) is not None, "Gateway device was wrongly pruned"
    assert dev_reg.async_get(cen1.id) is not None, "CEN device was wrongly pruned"
    assert dev_reg.async_get(cenplus.id) is not None, "CEN+ device was wrongly pruned"
    assert dev_reg.async_get(cenplus_who25.id) is not None, "CEN+ WHO 25 device was wrongly pruned"
    assert dev_reg.async_get(active_dev.id) is not None, "Active device with entities was wrongly pruned"
    assert dev_reg.async_get(empty_dry_contact.id) is None, "Empty dry contact interface was NOT pruned"
    assert dev_reg.async_get(stale_dev.id) is None, "Stale empty device was NOT pruned"


def test_prune_stale_devices_exception_handled(config_entry: MockConfigEntry):
    """Exceptions during device pruning are logged and do not raise."""
    mock_hass = MagicMock(spec=HomeAssistant)
    with patch("homeassistant.helpers.device_registry.async_get", side_effect=RuntimeError("Registry error")):
        # Must complete cleanly without raising
        prune_stale_devices(mock_hass, config_entry)


def test_device_for_identifier_helper(hass: HomeAssistant, config_entry: MockConfigEntry):
    """Test _device_for_identifier helper returns matching device scoped to config entry."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "scoped-test-id")},
    )

    found = _device_for_identifier(dev_reg, config_entry, (DOMAIN, "scoped-test-id"))
    assert found is not None
    assert found.id == device.id

    not_found = _device_for_identifier(dev_reg, config_entry, (DOMAIN, "missing-id"))
    assert not_found is None


def test_migrate_entity_invalid_mac_prefix_exception_handled(
    hass: HomeAssistant, config_entry: MockConfigEntry
):
    """Entity with invalid MAC prefix that raises ValueError in format_mac is ignored safely."""
    ent_reg = er.async_get(hass)
    entity = ent_reg.async_get_or_create(
        domain="light",
        platform=DOMAIN,
        unique_id="invalid_not_a_mac-12",
        config_entry=config_entry,
    )
    real_format = dr.format_mac

    def fake_format(val: str) -> str:
        if val == "invalid_not_a_mac":
            raise ValueError("Invalid MAC")
        return real_format(val)

    with patch("homeassistant.helpers.device_registry.format_mac", side_effect=fake_format):
        migrate_entry_and_registries(hass, config_entry, {})
    assert ent_reg.async_get(entity.entity_id).unique_id == "invalid_not_a_mac-12"


def test_migrate_light_update_value_error_handled(
    hass: HomeAssistant, config_entry: MockConfigEntry
):
    """When entity_registry.async_update_entity raises ValueError during light migration, it is logged cleanly."""
    ent_reg = er.async_get(hass)
    mac = "00:03:50:81:22:33"
    light = ent_reg.async_get_or_create(
        domain="light", platform=DOMAIN, unique_id=f"{mac}-12", config_entry=config_entry,
    )
    with patch.object(ent_reg, "async_update_entity", side_effect=ValueError("Collision")):
        migrate_entry_and_registries(hass, config_entry, {})
    assert ent_reg.async_get(light.entity_id) is not None


def test_migrate_unformatted_mac_prefix_update_value_error_handled(
    hass: HomeAssistant, config_entry: MockConfigEntry
):
    """When entity_registry.async_update_entity raises ValueError for unformatted MAC prefix, it is caught cleanly."""
    ent_reg = er.async_get(hass)
    raw_mac = "000350812233"
    light = ent_reg.async_get_or_create(
        domain="light", platform=DOMAIN, unique_id=f"{raw_mac}-1-12", config_entry=config_entry,
    )
    with patch.object(ent_reg, "async_update_entity", side_effect=ValueError("Collision")):
        migrate_entry_and_registries(hass, config_entry, {})
    assert ent_reg.async_get(light.entity_id) is not None


def test_migrate_button_unformatted_mac_value_error_handled(
    hass: HomeAssistant, config_entry: MockConfigEntry
):
    """When entity_registry.async_update_entity raises ValueError for unformatted button MAC, it is caught cleanly."""
    ent_reg = er.async_get(hass)
    raw_mac = "000350812233"
    btn = ent_reg.async_get_or_create(
        domain="button", platform=DOMAIN, unique_id=f"{raw_mac}-1-12-enable", config_entry=config_entry,
    )
    with patch.object(ent_reg, "async_update_entity", side_effect=ValueError("Collision")):
        migrate_entry_and_registries(hass, config_entry, {})
    assert ent_reg.async_get(btn.entity_id) is not None


def test_migrate_button_duplicate_pruning_exception_handled(
    hass: HomeAssistant, config_entry: MockConfigEntry
):
    """When entity_registry.async_remove raises during duplicate button pruning, it is logged and does not crash."""
    ent_reg = er.async_get(hass)
    mac = "00:03:50:81:22:33"

    ent_reg.async_get_or_create(
        domain="button", platform=DOMAIN, unique_id=f"{mac}-1-12-enable", config_entry=config_entry,
        suggested_object_id="canonical_b",
    )
    old_btn = ent_reg.async_get_or_create(
        domain="button", platform=DOMAIN, unique_id=f"{mac}-12-enable", config_entry=config_entry,
        suggested_object_id="old_b",
    )

    with patch.object(ent_reg, "async_remove", side_effect=RuntimeError("Cannot remove")):
        migrate_entry_and_registries(hass, config_entry, {})
    assert ent_reg.async_get(old_btn.entity_id) is not None


def test_migrate_button_update_value_error_handled(
    hass: HomeAssistant, config_entry: MockConfigEntry
):
    """When entity_registry.async_update_entity raises ValueError, it is caught cleanly."""
    ent_reg = er.async_get(hass)
    mac = "00:03:50:81:22:33"
    btn = ent_reg.async_get_or_create(
        domain="button", platform=DOMAIN, unique_id=f"{mac}-12-enable", config_entry=config_entry,
    )
    with patch.object(ent_reg, "async_update_entity", side_effect=ValueError("Collision")):
        migrate_entry_and_registries(hass, config_entry, {})
    assert ent_reg.async_get(btn.entity_id) is not None


def test_migrate_button_raw_mac_prefix_normalized(
    hass: HomeAssistant, config_entry: MockConfigEntry
):
    """Button with already full subparts but unformatted MAC prefix has MAC normalized."""
    ent_reg = er.async_get(hass)
    raw_mac = "000350812233"
    btn = ent_reg.async_get_or_create(
        domain="button", platform=DOMAIN, unique_id=f"{raw_mac}-1-12-enable", config_entry=config_entry,
    )
    migrate_entry_and_registries(hass, config_entry, {})
    assert ent_reg.async_get(btn.entity_id).unique_id == "00:03:50:81:22:33-1-12-enable"


def test_migrate_device_update_exception_handled(
    hass: HomeAssistant, config_entry: MockConfigEntry
):
    """When device registry update fails during entity migration, it is logged and does not crash."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    mac = "00:03:50:81:22:33"
    device = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{mac}-14")},
    )
    ent_reg.async_get_or_create(
        domain="light", platform=DOMAIN, unique_id=f"{mac}-14", config_entry=config_entry, device_id=device.id,
    )
    with patch.object(dev_reg, "async_update_device", side_effect=RuntimeError("Device update error")):
        migrate_entry_and_registries(hass, config_entry, {})


def test_prune_stale_devices_spared_by_gateway_object(
    hass: HomeAssistant, config_entry: MockConfigEntry
):
    """When gateway_device_entry is None, gateway object unique_id and id prevent pruning."""
    dev_reg = dr.async_get(hass)
    mac = "00:03:50:81:22:33"
    gw_dev = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, mac)}, name="GW",
    )
    gw = MockGateway(mac)
    # gateway_device_entry is None, but gateway.unique_id matches
    prune_stale_devices(hass, config_entry, gateway_device_entry=None, gateway=gw)
    assert dev_reg.async_get(gw_dev.id) is not None

    # Test with gateway having id property
    gw2 = MagicMock()
    gw2.unique_id = None
    gw2.id = mac
    prune_stale_devices(hass, config_entry, gateway_device_entry=None, gateway=gw2)
    assert dev_reg.async_get(gw_dev.id) is not None


async def test_backwards_compatibility_aliases(hass: HomeAssistant, config_entry: MockConfigEntry):
    """Verify async_ aliases point to or wrap the synchronous implementations."""
    assert async_prune_stale_devices is prune_stale_devices
    # async_migrate_entry_and_registries is an awaitable coroutine wrapper
    await async_migrate_entry_and_registries(hass, config_entry, {})

