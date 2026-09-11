"""MyHOME administration panel backed by Home Assistant's native registries.

The panel owns no configuration store. Its only custom WebSocket command is a
read-only inventory; edits use Home Assistant's registry APIs directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import frontend, http, panel_custom, websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_ENTITY, CONF_FIRMWARE, DOMAIN, INTEGRATION_VERSION

PANEL_URL = "myhome"
PANEL_VERSION = "0.2.0"
PANEL_STATIC_URL = "/myhome_panel"
WS_INVENTORY = "myhome/panel/inventory"
_PANEL_REGISTERED = "_panel_registered"
_STATIC_REGISTERED = "_panel_static_registered"
_WS_REGISTERED = "_panel_ws_registered"
_LOCK = "_panel_setup_lock"


def _device_who(identifiers: set[tuple[str, str]], mac: str | None) -> str | None:
    """Read WHO from the canonical MAC-WHO-device identifier, never the HA type.

    Sensor entity unique IDs may omit WHO entirely, so their device registry
    identifier is the source for both device and entity grouping. Ambiguous or
    legacy identifiers remain unclassified rather than treating WHERE as WHO.
    """
    if not mac:
        return None
    normalized_mac = re.sub(r"[:.\-]", "", mac).lower()
    whos = set()
    for domain, identifier in identifiers:
        if domain != DOMAIN:
            continue
        # Consume all six MAC octets before WHO; device IDs can contain hyphens.
        match = re.fullmatch(
            r"([0-9a-f]{2}(?:[:.\-]?[0-9a-f]{2}){5})-(\d+)-(.+)",
            str(identifier),
            re.IGNORECASE,
        )
        if match and re.sub(r"[:.\-]", "", match[1]).lower() == normalized_mac:
            whos.add(str(int(match[2])))
    return next(iter(whos)) if len(whos) == 1 else None


def _asset_version() -> str:
    """Hash the complete panel bundle off the event loop."""
    digest = hashlib.sha256()
    for path in sorted((Path(__file__).parent / "frontend" / "panel").iterdir()):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


@callback
def async_panel_inventory(hass: HomeAssistant) -> dict[str, Any]:
    """Return an allowlisted inventory, including offline and disabled entries."""
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    gateways = []
    device_payloads = {}
    entity_payloads = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = hass.data.get(DOMAIN, {}).get(entry.data.get(CONF_MAC), {})
        gateway = runtime.get(CONF_ENTITY)
        loaded = entry.state is ConfigEntryState.LOADED and entry.disabled_by is None
        # Never serialize ConfigEntry.data/options or runtime objects wholesale:
        # they contain gateway credentials and configuration unrelated to the UI.
        gateways.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "mac": entry.data.get(CONF_MAC),
                "host": entry.data.get(CONF_HOST),
                "port": entry.data.get(CONF_PORT),
                "serial_port": entry.data.get("serial_port"),
                "model": entry.data.get(CONF_NAME),
                "firmware": entry.data.get(CONF_FIRMWARE),
                "state": entry.state.value,
                "disabled_by": entry.disabled_by,
                "connected": loaded and bool(getattr(gateway, "is_connected", False)),
                "monitor_available": loaded and runtime.get("bus_monitor") is not None,
                "device_id": getattr(gateway, "device_registry_id", None) if loaded else None,
            }
        )
        entry_device_whos = {}
        for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
            who = _device_who(device.identifiers, entry.data.get(CONF_MAC))
            entry_device_whos[device.id] = who
            if device.id in device_payloads:
                device_payloads[device.id]["entry_ids"].append(entry.entry_id)
                if device_payloads[device.id]["who"] != who:
                    device_payloads[device.id]["who"] = None
                continue
            device_payloads[device.id] = {
                "id": device.id,
                "entry_ids": [entry.entry_id],
                "name": device.name,
                "name_by_user": device.name_by_user,
                "area_id": device.area_id,
                "manufacturer": device.manufacturer,
                "model": device.model,
                "disabled_by": device.disabled_by,
                "who": who,
                "identifiers": sorted(
                    str(identifier) for domain, identifier in device.identifiers if domain == DOMAIN
                ),
            }
        for entity in er.async_entries_for_config_entry(entities, entry.entry_id):
            if entity.platform != DOMAIN:
                continue
            entity_payloads[entity.entity_id] = {
                "entity_id": entity.entity_id,
                "entry_id": entry.entry_id,
                "device_id": entity.device_id,
                "domain": entity.domain,
                "name": entity.name,
                "original_name": entity.original_name,
                "area_id": entity.area_id,
                "disabled_by": entity.disabled_by,
                "hidden_by": entity.hidden_by,
                "entity_category": entity.entity_category,
                "unique_id": entity.unique_id,
                "who": entry_device_whos.get(entity.device_id),
            }
    return {
        "version": INTEGRATION_VERSION,
        "panel_version": PANEL_VERSION,
        "gateways": gateways,
        "devices": list(device_payloads.values()),
        "entities": list(entity_payloads.values()),
        "areas": [
            {"id": area.id, "name": area.name} for area in ar.async_get(hass).async_list_areas()
        ],
    }


@websocket_api.websocket_command({vol.Required("type"): WS_INVENTORY})
@websocket_api.require_admin
@callback
def ws_panel_inventory(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Read the native configuration without requiring a connected gateway."""
    connection.send_result(msg["id"], async_panel_inventory(hass))


async def async_setup_panel(hass: HomeAssistant, bus_card_url: str) -> None:
    """Register once across multiple gateways, setup retries and reloads."""
    data = hass.data.setdefault(DOMAIN, {})
    if not data.get(_WS_REGISTERED):
        websocket_api.async_register_command(hass, ws_panel_inventory)
        data[_WS_REGISTERED] = True
    if "frontend" not in hass.config.components or not getattr(hass, "http", None):
        return
    async with data.setdefault(_LOCK, asyncio.Lock()):
        if data.get(_PANEL_REGISTERED):
            return
        build = await hass.async_add_executor_job(_asset_version)
        if not data.get(_STATIC_REGISTERED):
            path = str(Path(__file__).parent / "frontend" / "panel")
            if hasattr(hass.http, "async_register_static_paths"):
                await hass.http.async_register_static_paths(
                    [
                        http.StaticPathConfig(PANEL_STATIC_URL, path, cache_headers=False),
                    ]
                )
            else:  # Home Assistant 2024.4–2024.6
                hass.http.register_static_path(PANEL_STATIC_URL, path, cache_headers=False)
            data[_STATIC_REGISTERED] = True
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL,
            webcomponent_name="myhome-panel",
            sidebar_title="MyHOME",
            sidebar_icon="mdi:home-automation",
            module_url=f"{PANEL_STATIC_URL}/myhome-panel.js?v={PANEL_VERSION}&build={build}",
            require_admin=True,
            config={"bus_card_url": bus_card_url, "panel_version": PANEL_VERSION},
        )
        data[_PANEL_REGISTERED] = True


@callback
def async_remove_panel_if_last_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the sidebar only on deletion of the last gateway, not on unload."""
    if any(other.entry_id != entry.entry_id for other in hass.config_entries.async_entries(DOMAIN)):
        return
    data = hass.data.get(DOMAIN, {})
    if data.pop(_PANEL_REGISTERED, False):
        frontend.async_remove_panel(hass, PANEL_URL)
