"""The 4-digit spelling of an area-0 point (``0004``) resolves to the 2-digit form the gateways use (#147)."""
from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.light import ColorMode
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome import validate
from custom_components.myhome.binary_sensor import MyHOMEMotionSensor
from custom_components.myhome.binary_sensor import async_setup_entry as binary_sensor_setup_entry
from custom_components.myhome.const import (
    CONF_COVER_TRAVEL_TIMES,
    CONF_PLATFORMS,
    CONF_WHERE,
    DOMAIN,
    PLATFORMS,
    SUBENTRY_TYPE_EXPOSE,
)
from custom_components.myhome.cover_calibration import _stored_calibration
from custom_components.myhome.expose import ExposeBinding
from custom_components.myhome.legacy_yaml import (
    CONF_MIGRATED_YAML_PLATFORMS,
    canonicalise_imported_platforms,
    load_legacy_myhome_yaml,
    migrate_legacy_yaml_to_entry_options,
)
from custom_components.myhome.light import async_setup_entry as light_setup_entry
from custom_components.myhome.migrate import (
    _carry_entity_customisations,
    migrate_entry_and_registries,
)
from custom_components.myhome.sensor import MyHOMEIlluminanceSensor
from custom_components.myhome.sensor import async_setup_entry as sensor_setup_entry
from custom_components.myhome.validate import (
    PointToPoint,
    binary_sensor_schema,
    cover_schema,
    expose_entry_schema,
    light_schema,
    sensor_schema,
)
from custom_components.myhome.where_grammar import (
    canonical_point_address,
    canonical_point_spelling,
    normalize_expose_where,
)
from tests.conftest import attach_runtime

MAC = "00:03:50:00:01:47"


@pytest.fixture(autouse=True)
def _fresh_warnings():
    validate._warned_point_rewrites.clear()
    yield
    validate._warned_point_rewrites.clear()


@pytest.mark.parametrize(
    ("where", "expected"),
    [
        ("0004", "04"),
        ("0209", "0209"),
        ("0809", "0809"),
        ("0101", "0101"),
        ("0109", "0109"),
        ("0015", "0015"),
        ("0112", "0112"),
        ("1015", "1015"),
        ("1005", "1005"),
        ("0010", "0010"),
        ("0100", "0100"),
        ("0000", "0000"),
        ("04", "04"),
        ("89", "89"),
    ],
)
def test_point_to_point_canonical_spelling(where: str, expected: str) -> None:
    assert PointToPoint()(where) == expected


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("0004", "04"),
        ("0009#4#01", "09#4#01"),
        ("0001#4#15", "01#4#15"),
        ("0000", "0000"),
        ("0010", "0010"),
        ("0015", "0015"),
        ("0015#4#01", "0015#4#01"),
        ("04", "04"),
        ("04#4#01", "04#4#01"),
        ("1015", "1015"),
        ("#4", "#4"),
        ("", ""),
    ],
)
def test_silent_helpers_rewrite_only_area0_points(address: str, expected: str, caplog) -> None:
    """The stored-spelling helpers rewrite ``000N`` (keeping ``#4#II``) and log nothing."""
    with caplog.at_level(logging.WARNING):
        assert canonical_point_address(address) == expected
        assert canonical_point_spelling(address.partition("#")[0]) == expected.partition("#")[0]
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_one_warning_per_rewritten_address(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            PointToPoint()("0004")
        PointToPoint()("0005")
        PointToPoint()("0015")
        PointToPoint()("0209")
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert "'0004'" in warnings[0] and "'04'" in warnings[0]
    assert "'0005'" in warnings[1] and "'05'" in warnings[1]


def test_yaml_device_keys_use_the_canonical_spelling() -> None:
    lights = light_schema({"kitchen": {CONF_WHERE: "0004", CONF_NAME: "Kitchen"}})
    assert list(lights) == ["1-04"]
    assert lights["1-04"][CONF_WHERE] == "04"
    covers = cover_schema({"blind": {CONF_WHERE: "0005", CONF_NAME: "Blind"}})
    assert covers["2-05"][CONF_WHERE] == "05"


async def test_yaml_light_0004_follows_the_bus_frame_for_04(hass: HomeAssistant) -> None:
    """`where: '0004'` is the light the gateway reports as 04: no duplicate, and its state follows."""
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.log_id = "[test]"
    hass.data = {DOMAIN: {MAC: {"entity": gateway, CONF_PLATFORMS: {"light": light_schema(
        {"kitchen": {CONF_WHERE: "0004", CONF_NAME: "Kitchen"}}
    )}}}}
    config_entry = MagicMock()
    config_entry.data = {"mac": MAC}
    config_entry.entry_id = "test_entry"
    added: list = []
    with patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]), \
         patch("custom_components.myhome.discovery.er.async_get"):
        attach_runtime(hass, config_entry)
        await light_setup_entry(hass, config_entry, added.extend)
    assert len(added) == 1
    light = added[0]
    assert light.unique_id == f"{MAC}-1-04"
    light.async_write_ha_state = MagicMock()
    light.async_schedule_update_ha_state = MagicMock()

    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*1*1*04##"))
    await hass.async_block_till_done()
    assert len(added) == 1  # bus discovery found the configured light, not a new one
    assert light.is_on is True


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="GW", data={CONF_MAC: MAC}, unique_id=MAC, entry_id="e1")
    entry.add_to_hass(hass)
    return entry


def test_registry_moves_0004_to_04_and_merges_the_bus_duplicate(hass: HomeAssistant, config_entry, caplog) -> None:
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    yaml_dev = dev_reg.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-0004")})
    bus_dev = dev_reg.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-04")})
    # The user had placed, named and labelled the bus-created device: all survive on the configured one.
    dev_reg.async_update_device(bus_dev.id, area_id="kitchen", name_by_user="Kitchen ceiling", labels={"ceiling"})
    dev_reg.async_update_device(yaml_dev.id, labels={"yaml"})
    yaml_light = ent_reg.async_get_or_create(
        "light", DOMAIN, f"{MAC}-1-0004", config_entry=config_entry, device_id=yaml_dev.id, suggested_object_id="kitchen"
    )
    yaml_button = ent_reg.async_get_or_create(
        "button", DOMAIN, f"{MAC}-1-0004-disable", config_entry=config_entry, device_id=yaml_dev.id
    )
    bus_light = ent_reg.async_get_or_create(
        "light", DOMAIN, f"{MAC}-1-04", config_entry=config_entry, device_id=bus_dev.id, suggested_object_id="light_04"
    )
    # The user had customised the working bus entity; the yaml one (which never worked) has nothing set.
    ent_reg.async_update_entity(bus_light.entity_id, area_id="kitchen", icon="mdi:ceiling-light", name="Ceiling", labels={"lamps"})
    # An illuminance sensor bus discovery hung on its device (its real unique id, without the WHO):
    # nothing in the yaml claims it, so it is handed over to the surviving device.
    bus_sensor = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-04-illuminance", config_entry=config_entry, device_id=bus_dev.id
    )
    keep = ent_reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-0015", config_entry=config_entry)

    migrate_entry_and_registries(hass, config_entry, {})

    assert ent_reg.async_get(bus_light.entity_id) is None
    assert ent_reg.async_get(bus_sensor.entity_id).device_id == yaml_dev.id
    moved = ent_reg.async_get(yaml_light.entity_id)
    assert moved is not None and moved.unique_id == f"{MAC}-1-04"
    assert moved.entity_id == "light.kitchen"
    assert (moved.area_id, moved.icon, moved.name, moved.labels) == ("kitchen", "mdi:ceiling-light", "Ceiling", {"lamps"})
    assert ent_reg.async_get(yaml_button.entity_id).unique_id == f"{MAC}-1-04-disable"
    assert ent_reg.async_get(keep.entity_id).unique_id == f"{MAC}-1-0015"
    assert dev_reg.async_get(bus_dev.id) is None
    device = dev_reg.async_get(yaml_dev.id)
    assert device.identifiers == {(DOMAIN, f"{MAC}-1-04")}
    assert device.area_id == "kitchen" and device.name_by_user == "Kitchen ceiling"
    assert device.labels == {"yaml", "ceiling"}
    assert moved.device_id == yaml_dev.id

    # A second run finds nothing to migrate and changes nothing.
    def snapshot():
        return (
            {e.entity_id: e for e in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)},
            {d.id: d for d in dr.async_entries_for_config_entry(dev_reg, config_entry.entry_id)},
        )

    before = snapshot()
    caplog.clear()
    with caplog.at_level(logging.INFO):
        migrate_entry_and_registries(hass, config_entry, {})
    assert not [r for r in caplog.records if r.name == "custom_components.myhome" and "igrat" in r.getMessage()]
    assert snapshot() == before


def test_registry_without_duplicate_is_renamed(hass: HomeAssistant, config_entry, caplog) -> None:
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{MAC}-2-0009#4#01")})
    cover = ent_reg.async_get_or_create(
        "cover", DOMAIN, f"{MAC}-2-0009#4#01", config_entry=config_entry, device_id=device.id
    )
    with caplog.at_level(logging.WARNING):
        migrate_entry_and_registries(hass, config_entry, {})
    assert ent_reg.async_get(cover.entity_id).unique_id == f"{MAC}-2-09#4#01"
    assert dev_reg.async_get(device.id).identifiers == {(DOMAIN, f"{MAC}-2-09#4#01")}
    # The registry spelling is rewritten silently: the "Write '09' in myhome.yaml" warning
    # belongs to the yaml validator, and the file may already say '09' or not exist.
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "0009" not in validate._warned_point_rewrites


def test_registry_errors_are_logged_and_do_not_block_the_setup(hass: HomeAssistant, config_entry, caplog) -> None:
    """A registry refusal (another entity or device already holds the id) is a WARNING, not a failed setup."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-0004")})
    light = ent_reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-0004", config_entry=config_entry, device_id=device.id)
    with caplog.at_level(logging.WARNING), \
         patch.object(er.EntityRegistry, "async_update_entity", side_effect=ValueError("id taken")), \
         patch.object(dr.DeviceRegistry, "async_update_device", side_effect=ValueError("id taken")):
        migrate_entry_and_registries(hass, config_entry, {})
    assert ent_reg.async_get(light.entity_id).unique_id == f"{MAC}-1-0004"
    assert dev_reg.async_get(device.id).identifiers == {(DOMAIN, f"{MAC}-1-0004")}
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert f"Could not migrate {light.entity_id} to unique_id {MAC}-1-04: id taken" in warnings
    assert f"Could not migrate device {device.id} to the canonical address: id taken" in warnings


def test_a_refused_rename_puts_the_bus_duplicate_back(hass: HomeAssistant, config_entry, caplog) -> None:
    """The duplicate is only gone once the rename succeeded: on a refusal it is restored as it was."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    yaml_dev = dev_reg.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-0004")})
    bus_dev = dev_reg.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-04")})
    yaml_light = ent_reg.async_get_or_create(
        "light", DOMAIN, f"{MAC}-1-0004", config_entry=config_entry, device_id=yaml_dev.id, suggested_object_id="kitchen"
    )
    bus_light = ent_reg.async_get_or_create(
        "light", DOMAIN, f"{MAC}-1-04", config_entry=config_entry, device_id=bus_dev.id, suggested_object_id="light_04"
    )
    ent_reg.async_update_entity(bus_light.entity_id, name="Bus light", area_id="hall")
    bus_sensor = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-04-illuminance", config_entry=config_entry, device_id=bus_dev.id
    )
    real_update_entity = er.EntityRegistry.async_update_entity

    def refuse_new_unique_id(self, entity_id, **kwargs):
        if "new_unique_id" in kwargs:
            raise ValueError("id taken")
        return real_update_entity(self, entity_id, **kwargs)

    with caplog.at_level(logging.WARNING), \
         patch.object(er.EntityRegistry, "async_update_entity", refuse_new_unique_id), \
         patch.object(dr.DeviceRegistry, "async_update_device", side_effect=ValueError("id taken")):
        migrate_entry_and_registries(hass, config_entry, {})

    assert ent_reg.async_get(yaml_light.entity_id).unique_id == f"{MAC}-1-0004"
    restored = ent_reg.async_get("light.light_04")
    assert restored is not None and restored.unique_id == f"{MAC}-1-04"
    assert restored.config_entry_id == config_entry.entry_id
    assert restored.device_id == bus_dev.id
    assert restored.name == "Bus light" and restored.area_id == "hall"
    # The device rename was refused too, so the bus device and its entities are untouched.
    assert dev_reg.async_get(bus_dev.id) is not None
    assert ent_reg.async_get(bus_sensor.entity_id).device_id == bus_dev.id
    assert dev_reg.async_get(yaml_dev.id).identifiers == {(DOMAIN, f"{MAC}-1-0004")}
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert f"Could not migrate {yaml_light.entity_id} to unique_id {MAC}-1-04: id taken" in warnings
    assert f"Could not migrate device {yaml_dev.id} to the canonical address: id taken" in warnings


def test_a_canonical_id_held_by_another_config_entry_is_a_collision(hass: HomeAssistant, config_entry, caplog) -> None:
    """Only this entry's duplicates are merged: another gateway's entity under 04 is left alone."""
    other_entry = MockConfigEntry(domain=DOMAIN, title="GW2", data={CONF_MAC: MAC}, unique_id="other", entry_id="e2")
    other_entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    mine = ent_reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-0004", config_entry=config_entry)
    theirs = ent_reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-04", config_entry=other_entry)
    with caplog.at_level(logging.WARNING):
        migrate_entry_and_registries(hass, config_entry, {})
    assert ent_reg.async_get(mine.entity_id).unique_id == f"{MAC}-1-0004"
    assert ent_reg.async_get(theirs.entity_id).config_entry_id == other_entry.entry_id
    assert any(
        f"Not migrating {mine.entity_id} to unique_id {MAC}-1-04: {theirs.entity_id} of another config entry" in r.getMessage()
        for r in caplog.records if r.levelno == logging.WARNING
    )


# ── The imported copy of myhome.yaml (entry options, #733) ─────────────────────────

LIGHT_0004 = {
    "who": "1", "where": "0004", "name": "Kitchen", "dimmable": True, "manufacturer": "BTicino S.p.A.",
    "model": None, "icon": None, "icon_on": None, "entity_name": None, "device_class": "switch", "entities": {},
}


def _old_beta_snapshot() -> dict:
    """What a 2.0 beta before this change stored for ``where: "0004"`` (light and its button alias)."""
    return {"light": {"1-0004": dict(LIGHT_0004)}, "button": {"1-0004": dict(LIGHT_0004)}, "climate": {}}


def test_imported_copy_is_rewritten_to_the_canonical_spelling(caplog) -> None:
    snapshot = _old_beta_snapshot()
    snapshot["cover"] = {
        "2-0005#4#01": {"who": "2", "where": "0005", "interface": "01", "name": "Blind", "entities": {}},
        "2-0009": {"who": "2", "where": "0009", "members": ["0001", "0015", "11"], "name": "All", "entities": {}},
        "2-0015": {"who": "2", "where": "0015", "name": "Fifteen", "entities": {}},
    }
    snapshot["binary_sensor"] = {"25-0004": {"who": "25", "where": "0004", "name": "CEN", "entities": {}}}
    snapshot["alarm_control_panel"] = {"5-0004": {"who": "5", "where": "0004", "name": "Zone 4", "entities": {}}}
    before = json.dumps(snapshot, sort_keys=True)
    with caplog.at_level(logging.WARNING):
        result, rewritten = canonicalise_imported_platforms(snapshot)
    assert json.dumps(snapshot, sort_keys=True) == before  # the stored copy is not mutated
    assert result["light"] == {"1-04": {**LIGHT_0004, "where": "04"}}
    assert result["button"] == {"1-04": {**LIGHT_0004, "where": "04"}}
    assert set(result["cover"]) == {"2-05#4#01", "2-09", "2-0015"}
    assert result["cover"]["2-05#4#01"]["where"] == "05"
    assert result["cover"]["2-09"]["members"] == ["01", "0015", "11"]
    assert result["cover"]["2-0015"] is snapshot["cover"]["2-0015"]
    # Only WHO 1 / WHO 2 points: a CEN button at 0004 and WHO 5 zone 0004 are other grammars.
    assert result["binary_sensor"] == snapshot["binary_sensor"]
    assert result["alarm_control_panel"] == snapshot["alarm_control_panel"]
    assert result["climate"] == {}
    assert rewritten == ["light: 1-0004 -> 1-04", "button: 1-0004 -> 1-04", "cover: 2-0005#4#01 -> 2-05#4#01", "cover: 2-0009 -> 2-09"]
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
    # Idempotent: the canonical copy is returned unchanged.
    again, rewritten_again = canonicalise_imported_platforms(result)
    assert again == result and rewritten_again == []


def test_imported_copy_reports_a_members_only_rewrite_as_such() -> None:
    """A scope cover under a canonical key whose `members` hold `0004`: the line names the members, not a key move."""
    snapshot = {"cover": {"2-1": {"who": "2", "where": "1", "members": ["0004", "12"], "name": "Area 1", "entities": {}}}}
    result, rewritten = canonicalise_imported_platforms(snapshot)
    assert result["cover"]["2-1"]["members"] == ["04", "12"]
    assert rewritten == ["cover: 2-1 members ['0004', '12'] -> ['04', '12']"]


def test_imported_copy_keeps_the_canonical_device_on_a_collision(caplog) -> None:
    """A copy holding both `1-0004` and `1-04` keeps `1-04`; the twin is dropped, not overwritten."""
    snapshot = {"light": {
        "1-0004": dict(LIGHT_0004),
        "1-04": {**LIGHT_0004, "where": "04", "name": "Kitchen (bus)", "dimmable": False},
    }}
    with caplog.at_level(logging.WARNING):
        result, rewritten = canonicalise_imported_platforms(snapshot)
    assert result["light"] == {"1-04": snapshot["light"]["1-04"]}
    assert rewritten == []
    assert any(
        "dropping '1-0004' (where '0004'), the copy already has '1-04'" in r.getMessage()
        for r in caplog.records if r.levelno == logging.WARNING
    )


def test_new_snapshots_are_stored_canonically(hass: HomeAssistant, config_entry) -> None:
    """The guard on the write path: a platform mapping with `0004` is stored as `04`."""
    assert migrate_legacy_yaml_to_entry_options(hass, config_entry, _old_beta_snapshot()) is True
    assert list(config_entry.options[CONF_MIGRATED_YAML_PLATFORMS]["light"]) == ["1-04"]


async def test_imported_copy_with_0004_boots_twice_without_churn(hass: HomeAssistant, caplog) -> None:
    """Snapshot with `0004`, no myhome.yaml: two boots give one `…-1-04` light that keeps `dimmable`.

    Before the fix, boot 1 renamed the registry entry to `…-1-04`, restored it with an empty
    config (`config_for` missed `04` in the imported copy) and created `light.kitchen_2` for
    `0004`; boot 2 deleted `…-1-04` as a duplicate and renamed `…-1-04` again, and so on.
    The add is simulated by registering the created entities' unique ids as HA would.
    """
    entry = MockConfigEntry(
        domain=DOMAIN, title="GW", data={CONF_MAC: MAC}, unique_id=MAC, entry_id="e_imported",
        options={CONF_MIGRATED_YAML_PLATFORMS: _old_beta_snapshot()},
    )
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-0004")})
    ent_reg.async_get_or_create(
        "light", DOMAIN, f"{MAC}-1-0004", config_entry=entry, device_id=device.id, suggested_object_id="kitchen"
    )
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.log_id = "[test]"

    async def boot() -> list:
        configured: dict = {platform: {} for platform in PLATFORMS}
        await load_legacy_myhome_yaml(hass, entry, configured)  # no myhome.yaml: the imported copy
        migrate_entry_and_registries(hass, entry, configured)
        hass.data.setdefault(DOMAIN, {})[MAC] = {"entity": gateway, CONF_PLATFORMS: configured}
        attach_runtime(hass, entry)
        added: list = []
        await light_setup_entry(hass, entry, added.extend)
        for light in added:  # what EntityPlatform does on add
            ent_reg.async_get_or_create("light", DOMAIN, light.unique_id, config_entry=entry, device_id=device.id)
        return added

    def lights_in_registry() -> dict[str, str]:
        return {
            e.entity_id: e.unique_id
            for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id) if e.domain == "light"
        }

    with caplog.at_level(logging.INFO):
        first = await boot()
    assert [light.unique_id for light in first] == [f"{MAC}-1-04"]
    assert ColorMode.BRIGHTNESS in first[0].supported_color_modes  # `dimmable: true` kept
    assert lights_in_registry() == {"light.kitchen": f"{MAC}-1-04"}
    assert list(entry.options[CONF_MIGRATED_YAML_PLATFORMS]["light"]) == ["1-04"]
    assert entry.options[CONF_MIGRATED_YAML_PLATFORMS]["light"]["1-04"]["where"] == "04"
    assert any("2 point address(es) moved to the canonical spelling (light: 1-0004 -> 1-04; button: 1-0004 -> 1-04)" in r.getMessage() for r in caplog.records)
    assert dev_reg.async_get(device.id).identifiers == {(DOMAIN, f"{MAC}-1-04")}

    caplog.clear()
    with caplog.at_level(logging.INFO):
        second = await boot()
    assert [light.unique_id for light in second] == [f"{MAC}-1-04"]
    assert ColorMode.BRIGHTNESS in second[0].supported_color_modes
    assert lights_in_registry() == {"light.kitchen": f"{MAC}-1-04"}
    assert ent_reg.async_get("light.kitchen").device_id == device.id
    assert not [r for r in caplog.records if r.name == "custom_components.myhome" and "igrat" in r.getMessage()]
    assert not [r for r in caplog.records if "canonical spelling" in r.getMessage()]


# ── expose (myhome.yaml `expose:` and the UI subentries) ─────────────────────────


@pytest.mark.parametrize(
    ("who", "where", "expected"),
    [
        (1, "0004", "04"),
        (2, "0009#4#01", "09#4#01"),
        (1, "04", "04"),
        (1, "0015", "0015"),
        (2, "0010", "0010"),
        (1, "0000", "0000"),
        (1, "1015", "1015"),
        (4, "0004", "0004"),  # a WHO 4 probe keeps the WHO 4 grammar
        (18, "0004", "0004"),
    ],
)
def test_expose_where_is_canonical_for_who_1_and_2(who: int, where: str, expected: str, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        assert normalize_expose_where(who, where) == expected
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_expose_entry_with_0004_is_the_virtual_actuator_04() -> None:
    """The yaml `expose:` entry and the UI flow share `expose_entry_schema`: both store `04`."""
    item = expose_entry_schema({"who": 1, "where": "0004", "entity_id": "light.kitchen"})
    assert item["where"] == "04"
    routed = expose_entry_schema({"who": 2, "where": "0009#4#01", "entity_id": "cover.blind"})
    assert routed["where"] == "09#4#01"
    binding = ExposeBinding.from_dict(item, source="yaml")
    assert binding.key == (1, "04", None)


def test_expose_subentries_at_0004_are_migrated(hass: HomeAssistant, caplog) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="GW", data={CONF_MAC: MAC}, unique_id=MAC, entry_id="e_expose",
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_EXPOSE, "subentry_id": "sub_0004", "unique_id": None,
                "title": "WHO 1 / WHERE 0004 (light.kitchen)",
                "data": {"who": 1, "where": "0004", "entity_id": "light.kitchen"},
            },
            {
                "subentry_type": SUBENTRY_TYPE_EXPOSE, "subentry_id": "sub_routed", "unique_id": None,
                "title": "WHO 2 / WHERE 0009#4#01 (cover.blind)",
                "data": {"who": 2, "where": "0009#4#01", "entity_id": "cover.blind"},
            },
            {
                "subentry_type": SUBENTRY_TYPE_EXPOSE, "subentry_id": "sub_keep", "unique_id": None,
                "title": "WHO 1 / WHERE 0015 (light.hall)",
                "data": {"who": 1, "where": "0015", "entity_id": "light.hall"},
            },
            {
                "subentry_type": SUBENTRY_TYPE_EXPOSE, "subentry_id": "sub_probe", "unique_id": None,
                "title": "WHO 4 / WHERE 0004 (sensor.t)",
                "data": {"who": 4, "where": "0004", "entity_id": "sensor.t"},
            },
            {
                "subentry_type": SUBENTRY_TYPE_EXPOSE, "subentry_id": "sub_clock", "unique_id": None,
                "title": "Bus Clock Sync (WHO 13, dim 22)", "data": {"who": 13},
            },
            {
                "subentry_type": "other", "subentry_id": "sub_other", "unique_id": None,
                "title": "Not an expose subentry", "data": {"who": 1, "where": "0004"},
            },
            # Both spellings of one address: the canonical binding is the one that worked on the bus.
            {
                "subentry_type": SUBENTRY_TYPE_EXPOSE, "subentry_id": "sub_twin_old", "unique_id": None,
                "title": "WHO 2 / WHERE 0007 (cover.old)", "data": {"who": 2, "where": "0007", "entity_id": "cover.old"},
            },
            {
                "subentry_type": SUBENTRY_TYPE_EXPOSE, "subentry_id": "sub_twin", "unique_id": None,
                "title": "WHO 2 / WHERE 07 (cover.new)", "data": {"who": 2, "where": "07", "entity_id": "cover.new"},
            },
        ],
    )
    entry.add_to_hass(hass)
    with caplog.at_level(logging.INFO):
        migrate_entry_and_registries(hass, entry, {})
    subs = entry.subentries
    assert subs["sub_other"].data == {"who": 1, "where": "0004"}
    assert "sub_twin_old" not in subs and subs["sub_twin"].data["where"] == "07"
    assert any(
        "Removing expose subentry sub_twin_old (WHO 2 / WHERE 0007 (cover.old)): sub_twin (WHO 2 / WHERE 07 (cover.new)) already exposes WHO 2 WHERE 07" in r.getMessage()
        for r in caplog.records if r.levelno == logging.WARNING
    )
    assert subs["sub_0004"].data["where"] == "04" and subs["sub_0004"].title == "WHO 1 / WHERE 04 (light.kitchen)"
    assert subs["sub_routed"].data["where"] == "09#4#01" and subs["sub_routed"].title == "WHO 2 / WHERE 09#4#01 (cover.blind)"
    assert subs["sub_keep"].data["where"] == "0015" and subs["sub_keep"].title == "WHO 1 / WHERE 0015 (light.hall)"
    assert subs["sub_probe"].data["where"] == "0004"
    assert subs["sub_clock"].data == {"who": 13}
    assert sum("Migrated expose subentry" in r.getMessage() for r in caplog.records) == 2

    caplog.clear()
    with caplog.at_level(logging.INFO):
        migrate_entry_and_registries(hass, entry, {})
    assert not [r for r in caplog.records if "expose subentry" in r.getMessage()]


# ── WHO 1 binary_sensor (motion) and sensor (illuminance) ─────────────────────────


def test_who1_sensor_schemas_use_the_canonical_spelling(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        motion = binary_sensor_schema({"pir": {"who": "1", "where": "0004", "name": "PIR", "class": "motion"}})
        lux = sensor_schema({"lux": {"where": "0004", "name": "Lux", "class": "illuminance"}})
        explicit = sensor_schema({"lux2": {"who": "1", "where": "0005", "name": "Lux 2", "class": "illuminance"}})
    assert list(motion) == ["1-04"] and motion["1-04"]["where"] == "04"
    assert list(lux) == ["1-04"] and lux["1-04"]["where"] == "04" and lux["1-04"]["who"] == "1"
    assert list(explicit) == ["1-05"] and explicit["1-05"]["where"] == "05"
    # One yaml warning per address, as for lights.
    assert [r.getMessage()[:14] for r in caplog.records if r.levelno == logging.WARNING] == ["WHERE '0004' i", "WHERE '0005' i"]


def test_other_who_sensor_schemas_keep_the_address() -> None:
    """A CEN button, a WHO 9 input, a WHO 4 probe and a WHO 18 meter at 0004 are other grammars."""
    cen = binary_sensor_schema({"cen": {"where": "0004", "name": "CEN"}})
    assert list(cen) == ["25-0004"] and cen["25-0004"]["where"] == "0004"
    aux = binary_sensor_schema({"aux": {"who": "9", "where": "0004", "name": "Aux"}})
    assert list(aux) == ["9-0004"]
    probe = sensor_schema({"t": {"where": "0004", "name": "T", "class": "temperature"}})
    assert list(probe) == ["4-0004"]
    meter = sensor_schema({"m": {"where": "0004", "name": "M", "class": "power"}})
    assert list(meter) == ["18-0004"]
    kept = sensor_schema({"lux": {"where": "0015", "name": "Lux", "class": "illuminance"}})
    assert list(kept) == ["1-0015"]


def test_registry_moves_who1_sensors_and_merges_their_bus_twins(hass: HomeAssistant, config_entry, caplog) -> None:
    """The yaml motion / illuminance entity is `{mac}-1-0004-<class>`; its bus twin is `{mac}-04-<class>`.

    Bus discovery never spells the WHO into a motion / illuminance unique id (`build_motion`,
    `build_illuminance`), only into the device identifier `{mac}-1-04`. The twin is removed,
    its customisations carried where the yaml entity has none, and other WHOs are untouched.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    yaml_dev = dev_reg.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-0004")})
    bus_dev = dev_reg.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-04")})
    motion = ent_reg.async_get_or_create(
        "binary_sensor", DOMAIN, f"{MAC}-1-0004-motion", config_entry=config_entry, device_id=yaml_dev.id,
        suggested_object_id="hall_motion",
    )
    lux = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-1-0004-illuminance", config_entry=config_entry, device_id=yaml_dev.id
    )
    ent_reg.async_update_entity(lux.entity_id, icon="mdi:sun-wireless", labels={"mine"})
    bus_motion = ent_reg.async_get_or_create(
        "binary_sensor", DOMAIN, f"{MAC}-04-motion", config_entry=config_entry, device_id=bus_dev.id,
        suggested_object_id="motion_sensor_04",
    )
    ent_reg.async_update_entity(bus_motion.entity_id, area_id="hall", icon="mdi:motion-sensor", name="Hall PIR", labels={"pir"})
    bus_lux = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-04-illuminance", config_entry=config_entry, device_id=bus_dev.id
    )
    ent_reg.async_update_entity(bus_lux.entity_id, area_id="hall", icon="mdi:brightness-5", name="Hall lux", labels={"lux"})
    cen = ent_reg.async_get_or_create("binary_sensor", DOMAIN, f"{MAC}-25-0004", config_entry=config_entry)
    probe = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-0004-temperature", config_entry=config_entry)
    meter = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-18-0004-power", config_entry=config_entry)
    legacy = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-0004-illuminance", config_entry=config_entry)
    # The same twin held by another config entry is not this entry's to remove.
    other_entry = MockConfigEntry(domain=DOMAIN, title="GW2", data={CONF_MAC: MAC}, unique_id="other", entry_id="e2")
    other_entry.add_to_hass(hass)
    ent_reg.async_get_or_create("binary_sensor", DOMAIN, f"{MAC}-1-0005-motion", config_entry=config_entry)
    theirs = ent_reg.async_get_or_create("binary_sensor", DOMAIN, f"{MAC}-05-motion", config_entry=other_entry)

    with caplog.at_level(logging.INFO):
        migrate_entry_and_registries(hass, config_entry, {})

    moved_motion = ent_reg.async_get(motion.entity_id)
    assert moved_motion.unique_id == f"{MAC}-1-04-motion" and moved_motion.entity_id == "binary_sensor.hall_motion"
    assert (moved_motion.area_id, moved_motion.icon, moved_motion.name, moved_motion.labels) == (
        "hall", "mdi:motion-sensor", "Hall PIR", {"pir"},
    )
    moved_lux = ent_reg.async_get(lux.entity_id)
    assert moved_lux.unique_id == f"{MAC}-1-04-illuminance"
    # One way: what the yaml entity already had wins, the rest is carried.
    assert (moved_lux.area_id, moved_lux.icon, moved_lux.name, moved_lux.labels) == ("hall", "mdi:sun-wireless", "Hall lux", {"mine"})
    assert ent_reg.async_get(bus_motion.entity_id) is None and ent_reg.async_get(bus_lux.entity_id) is None
    assert ent_reg.async_get_entity_id("binary_sensor", DOMAIN, f"{MAC}-04-motion") is None
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, f"{MAC}-04-illuminance") is None
    assert ent_reg.async_get(theirs.entity_id).unique_id == f"{MAC}-05-motion"
    assert dev_reg.async_get(bus_dev.id) is None
    assert dev_reg.async_get(yaml_dev.id).identifiers == {(DOMAIN, f"{MAC}-1-04")}
    assert moved_motion.device_id == yaml_dev.id and moved_lux.device_id == yaml_dev.id
    for untouched in (cen, probe, meter, legacy):
        assert ent_reg.async_get(untouched.entity_id).unique_id == untouched.unique_id
    assert sorted(r.getMessage() for r in caplog.records if "bus-discovered twin" in r.getMessage()) == [
        f"Removed {bus_motion.entity_id}, the bus-discovered twin of binary_sensor.hall_motion (unique_id {MAC}-04-motion)",
        f"Removed {bus_lux.entity_id}, the bus-discovered twin of {lux.entity_id} (unique_id {MAC}-04-illuminance)",
    ]

    # A second run finds nothing to do: no log line and no registry write.
    caplog.clear()
    with caplog.at_level(logging.INFO), \
         patch.object(er.EntityRegistry, "async_update_entity", side_effect=AssertionError("write")), \
         patch.object(er.EntityRegistry, "async_remove", side_effect=AssertionError("write")), \
         patch.object(er.EntityRegistry, "async_get_or_create", side_effect=AssertionError("write")), \
         patch.object(dr.DeviceRegistry, "async_update_device", side_effect=AssertionError("write")), \
         patch.object(dr.DeviceRegistry, "async_remove_device", side_effect=AssertionError("write")):
        migrate_entry_and_registries(hass, config_entry, {})
    assert not [r for r in caplog.records if r.name == "custom_components.myhome" and "igrat" in r.getMessage()]
    assert not [r for r in caplog.records if "twin" in r.getMessage()]


def test_an_already_canonical_who1_sensor_still_loses_its_bus_twin(hass: HomeAssistant, config_entry, caplog) -> None:
    """Later boot (or a yaml that said `04` from the start): `{mac}-1-04-motion` next to `{mac}-04-motion`."""
    ent_reg = er.async_get(hass)
    mine = ent_reg.async_get_or_create(
        "binary_sensor", DOMAIN, f"{MAC}-1-04-motion", config_entry=config_entry, suggested_object_id="hall_motion"
    )
    twin = ent_reg.async_get_or_create("binary_sensor", DOMAIN, f"{MAC}-04-motion", config_entry=config_entry)
    ent_reg.async_update_entity(twin.entity_id, area_id="hall", labels={"pir"})
    routed = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-1-09#4#01-illuminance", config_entry=config_entry)
    routed_twin = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-09#4#01-illuminance", config_entry=config_entry)
    # Not area-0 points, or not WHO 1: no twin lookup.
    kept = [
        ent_reg.async_get_or_create("binary_sensor", DOMAIN, f"{MAC}-1-15-motion", config_entry=config_entry),
        ent_reg.async_get_or_create("binary_sensor", DOMAIN, f"{MAC}-15-motion", config_entry=config_entry),
        ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-1-0015-illuminance", config_entry=config_entry),
        ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-0015-illuminance", config_entry=config_entry),
        ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-04-temperature", config_entry=config_entry),
        ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-04-temperature", config_entry=config_entry),
    ]
    with caplog.at_level(logging.INFO):
        migrate_entry_and_registries(hass, config_entry, {})
    assert ent_reg.async_get(twin.entity_id) is None and ent_reg.async_get(routed_twin.entity_id) is None
    survivor = ent_reg.async_get(mine.entity_id)
    assert survivor.unique_id == f"{MAC}-1-04-motion" and survivor.entity_id == "binary_sensor.hall_motion"
    assert (survivor.area_id, survivor.labels) == ("hall", {"pir"})
    assert ent_reg.async_get(routed.entity_id).unique_id == f"{MAC}-1-09#4#01-illuminance"
    for entry in kept:
        assert ent_reg.async_get(entry.entity_id).unique_id == entry.unique_id
    assert not [r for r in caplog.records if "Migrated" in r.getMessage()]

    caplog.clear()
    with caplog.at_level(logging.INFO), \
         patch.object(er.EntityRegistry, "async_update_entity", side_effect=AssertionError("write")), \
         patch.object(er.EntityRegistry, "async_remove", side_effect=AssertionError("write")):
        migrate_entry_and_registries(hass, config_entry, {})
    assert not [r for r in caplog.records if r.name == "custom_components.myhome" and ("twin" in r.getMessage() or "igrat" in r.getMessage())]


def test_carry_over_onto_a_vanished_survivor_is_a_no_op(hass: HomeAssistant, config_entry) -> None:
    """Defensive: the survivor was renamed a moment ago, but a missing entry must not raise."""
    ent_reg = er.async_get(hass)
    twin = ent_reg.async_get_or_create("binary_sensor", DOMAIN, f"{MAC}-04-motion", config_entry=config_entry)
    ent_reg.async_update_entity(twin.entity_id, area_id="hall")
    with patch.object(er.EntityRegistry, "async_update_entity", side_effect=AssertionError("write")):
        _carry_entity_customisations(ent_reg, "binary_sensor.gone", ent_reg.async_get(twin.entity_id))


def _seed_who1_sensor_twins(hass: HomeAssistant, config_entry, domain: str, device_class: str) -> tuple[str, str]:
    """A bus twin `{mac}-04-<class>` listed BEFORE the yaml entity `{mac}-1-0004-<class>` in the registry.

    Returns ``(bus entity_id, yaml entity_id)``; the bus one carries the user's customisations.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    bus_dev = dev_reg.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-04")})
    bus = ent_reg.async_get_or_create(
        domain, DOMAIN, f"{MAC}-04-{device_class}", config_entry=config_entry, device_id=bus_dev.id,
        suggested_object_id=f"{device_class}_04", original_device_class=device_class,
    )
    ent_reg.async_update_entity(bus.entity_id, area_id="hall", icon="mdi:eye", name="Hall", labels={"hall"})
    yaml_dev = dev_reg.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-0004")})
    mine = ent_reg.async_get_or_create(
        domain, DOMAIN, f"{MAC}-1-0004-{device_class}", config_entry=config_entry, device_id=yaml_dev.id,
        suggested_object_id=f"hall_{device_class}", original_device_class=device_class,
    )
    entries = [e.unique_id for e in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id) if e.domain == domain]
    assert entries == [f"{MAC}-04-{device_class}", f"{MAC}-1-0004-{device_class}"]
    return bus.entity_id, mine.entity_id


async def test_motion_sensor_setup_keeps_the_yaml_entity_when_its_bus_twin_is_listed_first(hass: HomeAssistant, config_entry, caplog) -> None:
    """End to end through `binary_sensor.async_setup_entry`: the registry lists `{mac}-04-motion` first.

    Before the fix the migration renamed the yaml entity to `{mac}-1-04-motion` and left the twin
    to the platform's restore, which keeps the first entry it meets for an address and removes the
    other: here the device hand-over happened to re-list the twin last, so the twin went, with the
    user's area, icon, name and labels on it; see the next test for the order that removed the
    configured entity instead.
    """
    ent_reg = er.async_get(hass)
    bus_id, mine_id = _seed_who1_sensor_twins(hass, config_entry, "binary_sensor", "motion")
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.log_id = "[test]"
    configured = {"binary_sensor": binary_sensor_schema(
        {"hall_motion": {"who": "1", CONF_WHERE: "0004", CONF_NAME: "Hall motion", "class": "motion"}}
    )}
    assert list(configured["binary_sensor"]) == ["1-04"]
    hass.data.setdefault(DOMAIN, {})[MAC] = {"entity": gateway, CONF_PLATFORMS: configured}
    added: list = []
    with caplog.at_level(logging.INFO):
        migrate_entry_and_registries(hass, config_entry, configured)
        attach_runtime(hass, config_entry)
        await binary_sensor_setup_entry(hass, config_entry, added.extend)

    assert [type(e) for e in added] == [MyHOMEMotionSensor]
    assert added[0].unique_id == f"{MAC}-1-04-motion"
    assert ent_reg.async_get(bus_id) is None
    survivor = ent_reg.async_get(mine_id)
    assert survivor is not None and survivor.unique_id == f"{MAC}-1-04-motion"
    assert survivor.entity_id == "binary_sensor.hall_motion"
    assert (survivor.area_id, survivor.icon, survivor.name, survivor.labels) == ("hall", "mdi:eye", "Hall", {"hall"})
    assert [e.unique_id for e in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id) if e.domain == "binary_sensor"] == [
        f"{MAC}-1-04-motion"
    ]
    assert not [r for r in caplog.records if "removed stale registry entry" in r.getMessage()]

    # The configured entity follows the bus frames for 04 without a second entity being discovered.
    motion = added[0]
    motion.async_write_ha_state = MagicMock()
    motion.async_schedule_update_ha_state = MagicMock()
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*1*34*04##"))
    await hass.async_block_till_done()
    assert len(added) == 1
    assert motion.is_on is True


async def test_motion_sensor_setup_keeps_an_already_canonical_yaml_entity_listed_after_its_twin(hass: HomeAssistant, config_entry, caplog) -> None:
    """No rename and no device hand-over to reorder the registry: the twin stays listed first.

    `{mac}-04-motion` and the yaml `{mac}-1-04-motion` share the device `{mac}-1-04` (the yaml
    said `04`, or an earlier boot already renamed it). Before the fix the platform restored the
    twin first and removed `binary_sensor.hall_motion`, the user's configured entity, as the
    duplicate; its entity_id, area and name were lost with it.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-04")})
    twin = ent_reg.async_get_or_create(
        "binary_sensor", DOMAIN, f"{MAC}-04-motion", config_entry=config_entry, device_id=device.id,
        suggested_object_id="motion_04", original_device_class="motion",
    )
    ent_reg.async_update_entity(twin.entity_id, icon="mdi:eye")
    mine = ent_reg.async_get_or_create(
        "binary_sensor", DOMAIN, f"{MAC}-1-04-motion", config_entry=config_entry, device_id=device.id,
        suggested_object_id="hall_motion", original_device_class="motion",
    )
    ent_reg.async_update_entity(mine.entity_id, area_id="hall", name="Hall PIR")
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.log_id = "[test]"
    configured = {"binary_sensor": binary_sensor_schema(
        {"hall_motion": {"who": "1", CONF_WHERE: "04", CONF_NAME: "Hall motion", "class": "motion"}}
    )}
    hass.data.setdefault(DOMAIN, {})[MAC] = {"entity": gateway, CONF_PLATFORMS: configured}
    added: list = []
    with caplog.at_level(logging.INFO):
        migrate_entry_and_registries(hass, config_entry, configured)
        attach_runtime(hass, config_entry)
        await binary_sensor_setup_entry(hass, config_entry, added.extend)

    assert [e.unique_id for e in added] == [f"{MAC}-1-04-motion"]
    assert ent_reg.async_get(twin.entity_id) is None
    survivor = ent_reg.async_get(mine.entity_id)
    assert survivor is not None and survivor.entity_id == "binary_sensor.hall_motion"
    assert (survivor.area_id, survivor.icon, survivor.name) == ("hall", "mdi:eye", "Hall PIR")
    assert not [r for r in caplog.records if "removed stale registry entry" in r.getMessage()]
    assert not [r for r in caplog.records if "Migrated" in r.getMessage()]


async def test_illuminance_sensor_setup_keeps_the_yaml_entity_when_its_bus_twin_is_listed_first(hass: HomeAssistant, config_entry, caplog) -> None:
    """End to end through `sensor.async_setup_entry`: the registry lists `{mac}-04-illuminance` first.

    Before the fix the platform's restore pruned both entries at the configured address and the
    yaml device re-created `{mac}-1-04-illuminance` from scratch: the twin's area, icon, name and
    labels were lost.
    """
    ent_reg = er.async_get(hass)
    bus_id, mine_id = _seed_who1_sensor_twins(hass, config_entry, "sensor", "illuminance")
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.log_id = "[test]"
    configured = {"sensor": sensor_schema({"hall_illuminance": {CONF_WHERE: "0004", CONF_NAME: "Hall lux", "class": "illuminance"}})}
    assert list(configured["sensor"]) == ["1-04"]
    hass.data.setdefault(DOMAIN, {})[MAC] = {"entity": gateway, CONF_PLATFORMS: configured}
    added: list = []
    with caplog.at_level(logging.INFO):
        migrate_entry_and_registries(hass, config_entry, configured)
        attach_runtime(hass, config_entry)
        await sensor_setup_entry(hass, config_entry, added.extend)
        for sensor in added:  # what EntityPlatform does on add
            ent_reg.async_get_or_create("sensor", DOMAIN, sensor.unique_id, config_entry=config_entry)

    assert [type(e) for e in added] == [MyHOMEIlluminanceSensor]
    assert added[0].unique_id == f"{MAC}-1-04-illuminance"
    assert ent_reg.async_get(bus_id) is None
    survivor = ent_reg.async_get(mine_id)
    assert survivor is not None and survivor.unique_id == f"{MAC}-1-04-illuminance"
    assert (survivor.area_id, survivor.icon, survivor.name, survivor.labels) == ("hall", "mdi:eye", "Hall", {"hall"})
    assert [e.unique_id for e in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id) if e.domain == "sensor"] == [
        f"{MAC}-1-04-illuminance"
    ]


# ── persisted cover calibration (`cover_travel_times`) ───────────────────────────


def test_cover_travel_times_are_rekeyed_to_the_canonical_address(hass: HomeAssistant, caplog) -> None:
    measured = {"down": 18.4, "up": 20.1, "measured_at": "x"}
    routed = {"slat_time": 1.2}
    bus_measured = {"down": 17.0, "up": 19.0, "measured_at": "y"}
    entry = MockConfigEntry(
        domain=DOMAIN, title="GW", data={CONF_MAC: MAC}, unique_id=MAC, entry_id="e_calib",
        options={CONF_COVER_TRAVEL_TIMES: {
            "0004": measured, "0009#4#01": routed, "0015": {"down": 1.0}, "21": {"down": 2.0},
            "0005": {"down": 9.9}, "05": bus_measured,
        }},
    )
    entry.add_to_hass(hass)
    with caplog.at_level(logging.INFO):
        migrate_entry_and_registries(hass, entry, {})
    assert entry.options[CONF_COVER_TRAVEL_TIMES] == {
        "04": measured, "09#4#01": routed, "0015": {"down": 1.0}, "21": {"down": 2.0}, "05": bus_measured,
    }
    assert _stored_calibration(entry, "04") == measured
    assert _stored_calibration(entry, "09#4#01") == routed
    assert _stored_calibration(entry, "0004") is None
    assert any("Migrated the cover calibration of 0004 to the canonical address 04" in r.getMessage() for r in caplog.records)
    assert any("Dropping the cover calibration stored under 0005: 05 already has one" in r.getMessage() for r in caplog.records)

    # Idempotent: the second run writes nothing.
    with patch.object(hass.config_entries, "async_update_entry") as update:
        migrate_entry_and_registries(hass, entry, {})
    update.assert_not_called()


def test_stored_calibration_falls_back_to_the_4_digit_key() -> None:
    """A record a migration could not move is still found under its old spelling."""
    entry = MagicMock()
    entry.options = {CONF_COVER_TRAVEL_TIMES: {"0004": {"down": 1.0}, "0009#4#01": {"slat_time": 1.5}, "0015": {"down": 2.0}}}
    assert _stored_calibration(entry, "04") == {"down": 1.0}
    assert _stored_calibration(entry, "09#4#01") == {"slat_time": 1.5}
    assert _stored_calibration(entry, "15") is None  # 0015 is area 00 PL 15, not 15
    assert _stored_calibration(entry, "00") is None


# ── cross-domain twin: a WHO 1 impulse cover (or switch) whose bus twin is a light ──


def test_impulse_cover_migration_removes_the_ghost_light_and_its_buttons(hass: HomeAssistant, config_entry, caplog) -> None:
    """`cover:` with `who: "1"`, `where: "0004"`: bus traffic on `04` had created a light and its buttons.

    The twin is in another domain, so the same-domain duplicate lookup does not see it. Without
    this handling the device merge hands `light.light_04` and `…-1-04-disable` / `-enable` to the
    cover's device; light.py then removes the light (a cover address never holds a light) with
    `registry.async_remove`, which leaves the two buttons orphaned on the impulse cover's device.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    yaml_dev = dev_reg.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-0004")})
    bus_dev = dev_reg.async_get_or_create(config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-04")})
    gate = ent_reg.async_get_or_create(
        "cover", DOMAIN, f"{MAC}-1-0004", config_entry=config_entry, device_id=yaml_dev.id, suggested_object_id="gate"
    )
    ghost = ent_reg.async_get_or_create(
        "light", DOMAIN, f"{MAC}-1-04", config_entry=config_entry, device_id=bus_dev.id, suggested_object_id="light_04"
    )
    buttons = [
        ent_reg.async_get_or_create("button", DOMAIN, f"{MAC}-1-04{suffix}", config_entry=config_entry, device_id=bus_dev.id)
        for suffix in ("-disable", "-enable")
    ]
    # A light of another config entry under the canonical id is not this entry's ghost.
    other_entry = MockConfigEntry(domain=DOMAIN, title="GW2", data={CONF_MAC: MAC}, unique_id="other", entry_id="e2")
    other_entry.add_to_hass(hass)
    theirs = ent_reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-0005", config_entry=other_entry)
    mine_switch = ent_reg.async_get_or_create("switch", DOMAIN, f"{MAC}-1-0005", config_entry=config_entry)
    theirs_canonical = ent_reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-05", config_entry=other_entry)

    with caplog.at_level(logging.INFO):
        migrate_entry_and_registries(hass, config_entry, {})

    assert ent_reg.async_get(gate.entity_id).unique_id == f"{MAC}-1-04"
    assert ent_reg.async_get(gate.entity_id).device_id == yaml_dev.id
    assert ent_reg.async_get(ghost.entity_id) is None
    assert all(ent_reg.async_get(button.entity_id) is None for button in buttons)
    assert dev_reg.async_get(bus_dev.id) is None
    assert dev_reg.async_get(yaml_dev.id).identifiers == {(DOMAIN, f"{MAC}-1-04")}
    assert {e.entity_id for e in er.async_entries_for_device(ent_reg, yaml_dev.id, include_disabled_entities=True)} == {"cover.gate"}
    assert any(
        f"Removed {ghost.entity_id} and 2 button(s): a light bus discovery created at the address of cover.gate" in r.getMessage()
        for r in caplog.records
    )
    assert ent_reg.async_get(mine_switch.entity_id).unique_id == f"{MAC}-1-05"
    assert ent_reg.async_get(theirs.entity_id).unique_id == f"{MAC}-1-0005"
    assert ent_reg.async_get(theirs_canonical.entity_id).unique_id == f"{MAC}-1-05"

    # An owner already spelled canonically (an earlier run renamed it) still gets the cleanup.
    late_ghost = ent_reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-04", config_entry=config_entry)
    late_button = ent_reg.async_get_or_create("button", DOMAIN, f"{MAC}-1-04-enable", config_entry=config_entry)
    switch_06 = ent_reg.async_get_or_create("switch", DOMAIN, f"{MAC}-1-06", config_entry=config_entry)
    light_06 = ent_reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-06", config_entry=config_entry)
    light_15 = ent_reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-15", config_entry=config_entry)
    ent_reg.async_get_or_create("switch", DOMAIN, f"{MAC}-1-15", config_entry=config_entry)
    migrate_entry_and_registries(hass, config_entry, {})
    assert ent_reg.async_get(late_ghost.entity_id) is None and ent_reg.async_get(late_button.entity_id) is None
    assert ent_reg.async_get(light_06.entity_id) is None  # a switch address never holds a light (light.py rule)
    assert ent_reg.async_get(switch_06.entity_id) is not None
    assert ent_reg.async_get(light_15.entity_id) is not None  # not an area-0 point: not this migration's business


def test_the_surviving_entity_keeps_its_own_customisations(hass: HomeAssistant, config_entry) -> None:
    """Carry-over is one way: what the yaml entity already has is not overwritten by the twin's."""
    ent_reg = er.async_get(hass)
    mine = ent_reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-0004", config_entry=config_entry)
    ent_reg.async_update_entity(mine.entity_id, area_id="hall", icon="mdi:lamp", name="Mine", labels={"a"})
    twin = ent_reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-04", config_entry=config_entry)
    ent_reg.async_update_entity(twin.entity_id, area_id="kitchen", icon="mdi:bulb", name="Twin", labels={"b"})
    with patch.object(er.EntityRegistry, "async_update_entity", wraps=ent_reg.async_update_entity) as update:
        migrate_entry_and_registries(hass, config_entry, {})
    moved = ent_reg.async_get(mine.entity_id)
    assert moved.unique_id == f"{MAC}-1-04"
    assert (moved.area_id, moved.icon, moved.name, moved.labels) == ("hall", "mdi:lamp", "Mine", {"a"})
    assert all("area_id" not in call.kwargs for call in update.call_args_list)
