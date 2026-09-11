"""Panel inventory and lifecycle tests using Home Assistant's real registries."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from homeassistant.components import frontend
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_socket import socket_enabled  # noqa: F401  (pyproject disables the socket plugin)

from custom_components.myhome import async_remove_entry
from custom_components.myhome.const import CONF_ENTITY, DOMAIN
from custom_components.myhome.panel import (
    PANEL_URL,
    PANEL_VERSION,
    WS_INVENTORY,
    async_panel_inventory,
    async_setup_panel,
    ws_panel_inventory,
)
from custom_components.myhome.websocket import _get_gateway_and_monitor


@pytest.fixture
def installation(hass):
    """Two gateways plus an unrelated integration, including a trigger-only device."""
    entries = []
    for index in range(3):
        entry = MockConfigEntry(
            domain=DOMAIN if index < 2 else "other",
            title=f"Gateway {index}",
            data={
                "mac": f"00:03:50:00:00:0{index}",
                "host": f"192.0.2.{index + 1}",
                "port": 20000,
                "name": "F454",
                "password": "do-not-expose",
            },
            options={"password": "also-secret", "unrelated_option": 42},
            state=ConfigEntryState.LOADED if index == 0 else ConfigEntryState.SETUP_RETRY,
        )
        entry.add_to_hass(hass)
        entries.append(entry)
    area = ar.async_get(hass).async_create("Living room")
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    devices = []
    entities = []
    for index, entry in enumerate(entries):
        device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(entry.domain, f"{entry.data['mac']}-1-11")},
            name=f"Light {index}",
        )
        device = device_registry.async_update_device(device.id, area_id=area.id)
        entity = entity_registry.async_get_or_create(
            "light",
            entry.domain,
            f"{entry.data['mac']}-1-11",
            config_entry=entry,
            device_id=device.id,
            original_name=f"Light {index}",
            disabled_by=er.RegistryEntryDisabler.USER if index == 1 else None,
        )
        devices.append(device)
        entities.append(entity)
    cen = device_registry.async_get_or_create(
        config_entry_id=entries[0].entry_id,
        identifiers={(DOMAIN, "00:03:50:00:00:00-25-21")},
        name="CEN entrance",
    )
    hass.data[DOMAIN] = {
        entries[0].data["mac"]: {
            CONF_ENTITY: SimpleNamespace(is_connected=True, device_registry_id=devices[0].id),
            "bus_monitor": object(),
        },
        # A stale handler must not make a failed setup look connected.
        entries[1].data["mac"]: {
            CONF_ENTITY: SimpleNamespace(is_connected=True),
            "bus_monitor": object(),
        },
    }
    yield SimpleNamespace(entries=entries, devices=devices, entities=entities, area=area, cen=cen)
    # These entries describe runtime states but were not actually set up. Avoid
    # invoking integration unload against the deliberately minimal handlers.
    for entry in entries:
        entry._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)


async def test_inventory_includes_offline_disabled_and_trigger_only_devices(hass, installation):
    payload = async_panel_inventory(hass)
    assert payload["panel_version"] == PANEL_VERSION
    assert payload["panel_version"] != payload["version"]
    assert len(payload["gateways"]) == 2
    assert payload["gateways"][0]["connected"] is True
    assert payload["gateways"][1]["connected"] is False
    assert payload["gateways"][1]["monitor_available"] is False
    assert {device["id"] for device in payload["devices"]} == {
        installation.devices[0].id,
        installation.devices[1].id,
        installation.cen.id,
    }
    assert {entity["entity_id"] for entity in payload["entities"]} == {
        installation.entities[0].entity_id,
        installation.entities[1].entity_id,
    }
    assert any(entity["disabled_by"] == "user" for entity in payload["entities"])
    assert {entity["who"] for entity in payload["entities"]} == {"1"}
    assert (
        next(device for device in payload["devices"] if device["id"] == installation.cen.id)["who"]
        == "25"
    )
    serialized = json.dumps(payload)
    for secret in ("do-not-expose", "also-secret", "password", "unrelated_option"):
        assert secret not in serialized


async def test_who_uses_device_identifiers_for_legacy_sensor_ids(hass, installation):
    """Temperature and energy sensors share an HA type but have distinct WHOs."""
    entry = installation.entries[1]  # An offline gateway must still be classified.
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    expected = {}
    for index, (who, identifier, unique_id) in enumerate(
        [
            ("4", "000350000001-4-101", "00:03:50:00:00:01-18-temperature"),
            ("18", "00-03-50-00-00-01-18-18-52", "00:03:50:00:00:01-12-power"),
            ("0", "00:03:50:00:00:01-0-1", "scenario-status"),
            (None, "00:03:50:00:00:01-11", "00:03:50:00:00:01-11-energy"),
        ]
    ):
        device = devices.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, identifier)},
            name=f"Meter {index}",
        )
        entity = entities.async_get_or_create(
            "sensor",
            DOMAIN,
            unique_id,
            config_entry=entry,
            device_id=device.id,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
        expected[entity.entity_id] = who
    orphan = entities.async_get_or_create(
        "sensor",
        DOMAIN,
        "00:03:50:00:00:01-18-temperature-orphan",
        config_entry=entry,
    )
    expected[orphan.entity_id] = None
    actual = {
        entity["entity_id"]: entity["who"] for entity in async_panel_inventory(hass)["entities"]
    }
    assert {entity_id: actual[entity_id] for entity_id in expected} == expected


async def test_inventory_reflects_native_registry_changes_without_a_second_store(
    hass, installation
):
    entry = installation.entries[0]
    old_data, old_options = dict(entry.data), dict(entry.options)
    er.async_get(hass).async_update_entity(installation.entities[0].entity_id, name="Reading light")
    dr.async_get(hass).async_update_device(
        installation.devices[0].id, name_by_user="Sofa", area_id=None
    )
    payload = async_panel_inventory(hass)
    assert (
        next(
            e for e in payload["entities"] if e["entity_id"] == installation.entities[0].entity_id
        )["name"]
        == "Reading light"
    )
    device = next(d for d in payload["devices"] if d["id"] == installation.devices[0].id)
    assert device["name_by_user"] == "Sofa"
    assert device["area_id"] is None
    assert dict(entry.data) == old_data
    assert dict(entry.options) == old_options


@pytest.mark.parametrize("user", [None, SimpleNamespace(is_admin=False)])
async def test_inventory_rejects_non_admins(hass, user):
    connection = MagicMock(user=user)
    with pytest.raises(Unauthorized):
        ws_panel_inventory(hass, connection, {"id": 1, "type": WS_INVENTORY})
    connection.send_result.assert_not_called()


async def test_inventory_websocket_round_trip(hass, hass_ws_client, installation):
    await async_setup_panel(hass, "/myhome_static/myhome-bus-card.js?v=test")
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": WS_INVENTORY})
    response = await client.receive_json()
    assert response["success"] is True
    assert len(response["result"]["gateways"]) == 2
    await client.close()


async def test_registration_concurrent_and_repeated_setup(hass):
    hass.config.components.add("frontend")
    hass.http = SimpleNamespace(async_register_static_paths=AsyncMock())
    await asyncio.gather(*(async_setup_panel(hass, "/card.js?v=1") for _ in range(3)))
    hass.http.async_register_static_paths.assert_awaited_once()
    panel = hass.data[frontend.DATA_PANELS][PANEL_URL]
    assert panel.require_admin is True
    assert panel.config["bus_card_url"] == "/card.js?v=1"
    assert panel.config["panel_version"] == PANEL_VERSION
    url = urlsplit(panel.config["_panel_custom"]["module_url"])
    assert url.path == "/myhome_panel/myhome-panel.js"
    assert parse_qs(url.query)["v"] == [PANEL_VERSION]
    assert len(parse_qs(url.query)["build"][0]) == 12


async def test_registration_retry_does_not_register_static_path_twice(hass):
    hass.config.components.add("frontend")
    hass.http = SimpleNamespace(async_register_static_paths=AsyncMock())
    with patch(
        "custom_components.myhome.panel.panel_custom.async_register_panel",
        side_effect=RuntimeError("temporary"),
    ):
        with pytest.raises(RuntimeError, match="temporary"):
            await async_setup_panel(hass, "/card.js")
    await async_setup_panel(hass, "/card.js")
    hass.http.async_register_static_paths.assert_awaited_once()
    assert PANEL_URL in hass.data[frontend.DATA_PANELS]


async def test_legacy_http_registration(hass):
    hass.config.components.add("frontend")
    hass.http = SimpleNamespace(register_static_path=MagicMock())
    await async_setup_panel(hass, "/card.js")
    hass.http.register_static_path.assert_called_once()
    assert PANEL_URL in hass.data[frontend.DATA_PANELS]


async def test_sidebar_removed_only_when_last_gateway_is_deleted(hass, installation):
    hass.config.components.add("frontend")
    hass.http = SimpleNamespace(async_register_static_paths=AsyncMock())
    await async_setup_panel(hass, "/card.js")
    await async_remove_entry(hass, installation.entries[0])
    assert PANEL_URL in hass.data[frontend.DATA_PANELS]
    with patch.object(hass.config_entries, "async_entries", return_value=[installation.entries[1]]):
        await async_remove_entry(hass, installation.entries[1])
    assert PANEL_URL not in hass.data[frontend.DATA_PANELS]
    # Adding a gateway again reuses the registered static route.
    await async_setup_panel(hass, "/card.js")
    assert PANEL_URL in hass.data[frontend.DATA_PANELS]
    hass.http.async_register_static_paths.assert_awaited_once()


async def test_explicit_missing_gateway_never_uses_another_bus(hass, installation):
    assert _get_gateway_and_monitor(hass, "00:03:50:99:99:99") == (None, None)
    gateway, _ = _get_gateway_and_monitor(hass)
    assert gateway is hass.data[DOMAIN][installation.entries[0].data["mac"]][CONF_ENTITY]
