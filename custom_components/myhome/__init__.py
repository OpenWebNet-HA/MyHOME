from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_MAC
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_BROADCAST_RESYNC,
    CONF_ENTITIES,
    CONF_ENTITY,
    CONF_GENERATE_EVENTS,
    CONF_PLATFORMS,
    CONF_WORKER_COUNT,
    DATA_OWND_VERSION,
    DOMAIN,
    INTEGRATION_VERSION,
    LOGGER,
    PLATFORMS,
    get_ownd_version,
)
from .data import MyHOMEConfigEntry, MyHOMERuntimeData
from .gateway import MyHOMEGatewayHandler, command_session_limit
from .legacy_yaml import load_legacy_myhome_yaml
from .migrate import migrate_entry_and_registries, prune_stale_devices
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _get_card_url(card_path: str, base_url: str = "/myhome_static/myhome-bus-card.js") -> str:
    """Return versioned URL with content hash for Lovelace card cache-busting."""
    try:
        if os.path.isfile(card_path):
            with open(card_path, "rb") as f:
                content_hash = hashlib.sha256(f.read()).hexdigest()[:8]
            return f"{base_url}?v={content_hash}"
    except Exception as err:
        LOGGER.debug("Could not compute card content hash: %s", err)
    return base_url


async def _async_register_lovelace_resource(hass: HomeAssistant, url_path: str) -> bool:
    """Auto-register resource in Lovelace dashboard resources collection."""
    try:
        lovelace = hass.data.get("lovelace")
        if not lovelace:
            return False
        resources = getattr(lovelace, "resources", None)
        if not resources:
            return False
        if hasattr(resources, "loaded") and not resources.loaded:
            await resources.async_load()
            resources.loaded = True
        if hasattr(resources, "async_create_item"):
            clean_url = url_path.split("?")[0]
            existing_match = None
            for item in (resources.async_items() or []):
                if isinstance(item, dict) and isinstance(item.get("url"), str):
                    if item["url"].split("?")[0] == clean_url:
                        existing_match = item
                        break

            if existing_match is None:
                await resources.async_create_item({
                    "res_type": "module",
                    "url": url_path,
                })
                LOGGER.info("Auto-registered Lovelace bus monitor resource: %s", url_path)
            elif existing_match.get("url") != url_path:
                if hasattr(resources, "async_update_item") and "id" in existing_match:
                    await resources.async_update_item(
                        existing_match["id"],
                        {
                            "res_type": "module",
                            "url": url_path,
                        },
                    )
                    LOGGER.info(
                        "Updated Lovelace bus monitor resource URL: %s -> %s",
                        existing_match.get("url"),
                        url_path,
                    )
                else:
                    LOGGER.debug(
                        "Lovelace bus monitor resource URL changed but update not supported: %s -> %s",
                        existing_match.get("url"),
                        url_path,
                    )
            else:
                LOGGER.debug("Lovelace bus monitor resource already present: %s", url_path)
        return True
    except Exception as e:
        LOGGER.debug("Could not auto-register Lovelace resource: %s", e)
        return False


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Register the Lovelace bus monitor card static resource and script."""
    domain_data = hass.data.setdefault(DOMAIN, {})

    card_path = os.path.join(os.path.dirname(__file__), "frontend", "myhome-bus-card.js")
    static_url = "/myhome_static/myhome-bus-card.js"
    versioned_url = await hass.async_add_executor_job(_get_card_url, card_path, static_url)

    http = getattr(hass, "http", None)
    if not domain_data.get("_frontend_registered"):
        if http is not None and os.path.isfile(card_path):
            frontend_dir = os.path.dirname(card_path)
            from homeassistant.components.http.server import StaticPathConfig

            try:
                await http.async_register_static_paths([
                    StaticPathConfig("/myhome_static", frontend_dir, False),
                    StaticPathConfig(static_url, card_path, False),
                ])
            except Exception as err:  # already registered after a reload, or http not ready
                LOGGER.debug("Static path registration for the bus-monitor card skipped: %s", err)

            try:
                from homeassistant.components import frontend
                frontend.add_extra_js_url(hass, versioned_url)
            except Exception as e:
                LOGGER.debug("Could not add extra js url for Lovelace card: %s", e)

            domain_data["_frontend_registered"] = True

    # Auto-register resource in Lovelace dashboard resources collection
    if not await _async_register_lovelace_resource(hass, versioned_url):
        if not domain_data.get("_lovelace_listener_registered"):
            if getattr(hass, "is_running", False):
                async def _delayed_retry() -> None:
                    await asyncio.sleep(1)
                    await _async_register_lovelace_resource(hass, versioned_url)

                hass.async_create_task(_delayed_retry())
            else:
                async def _on_ha_started(event: Event) -> None:
                    await _async_register_lovelace_resource(hass, versioned_url)

                from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
                hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_ha_started)
            domain_data["_lovelace_listener_registered"] = True


async def _async_resolve_ownd_version(hass: HomeAssistant) -> str:
    """Resolve the installed OWNd version once, off the event loop, and cache it."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if DATA_OWND_VERSION not in domain_data:
        domain_data[DATA_OWND_VERSION] = await hass.async_add_executor_job(get_ownd_version)
    return str(domain_data[DATA_OWND_VERSION])


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the MyHOME component."""
    hass.data.setdefault(DOMAIN, {})

    LOGGER.info(
        "Initializing MyHOME integration v%s (OWNd v%s)",
        INTEGRATION_VERSION,
        await _async_resolve_ownd_version(hass),
    )

    from .websocket import async_setup_websocket_api
    async_setup_websocket_api(hass)
    await _async_register_frontend(hass)
    await async_setup_services(hass)

    if DOMAIN not in config:
        return True

    LOGGER.error("configuration.yaml not supported for this component!")

    return False


async def async_setup_entry(hass: HomeAssistant, entry: MyHOMEConfigEntry) -> bool:
    """Set up a MyHOME gateway from a config entry."""
    LOGGER.info(
        "Setting up MyHOME gateway '%s' (v%s, OWNd v%s)",
        entry.title,
        INTEGRATION_VERSION,
        await _async_resolve_ownd_version(hass),
    )

    # Per-platform device configurations and entity objects; myhome.yaml and bus
    # discovery fill them, the platforms read them through entry.runtime_data.
    # A mapping pre-seeded under the deprecated hass.data[DOMAIN][mac] alias (see
    # below) is reused so its containers stay the live ones; dropped in 2.1.
    _seeded = hass.data[DOMAIN].get(entry.data[CONF_MAC])
    _seeded = _seeded if isinstance(_seeded, dict) else {}
    configured_platforms: dict[str, dict[str, dict[str, Any]]] = _seeded.setdefault(CONF_PLATFORMS, {})
    configured_entities: dict[str, dict[str, Any]] = _seeded.setdefault(CONF_ENTITIES, {})
    for _platform in PLATFORMS:
        configured_platforms.setdefault(_platform, {})
        configured_entities.setdefault(_platform, {})

    # Load legacy myhome.yaml if present for seamless backward-compatibility
    await load_legacy_myhome_yaml(hass, entry, configured_platforms)

    _generate_events = (
        entry.options.get(CONF_GENERATE_EVENTS, False)
    )
    _broadcast_resync = entry.options.get(CONF_BROADCAST_RESYNC, True)

    # Migrations for config entry, entity registry, and device registry
    migrate_entry_and_registries(hass, entry, configured_platforms)

    gateway = MyHOMEGatewayHandler(
        hass=hass,
        config_entry=entry,
        generate_events=_generate_events,
        broadcast_resync=_broadcast_resync,
    )
    runtime = MyHOMERuntimeData(
        gateway=gateway, platforms=configured_platforms, entities=configured_entities
    )

    try:
        tests_results = await gateway.test()
    except (asyncio.TimeoutError, ConnectionError, OSError) as e:
        LOGGER.warning("Gateway connection test failed: %s", e)
        tests_results = None

    if tests_results is None:
        # entry.runtime_data and the legacy alias are only published below, after
        # the connection test. Do not leave a half-initialised gateway from a
        # pre-seeded mapping visible while HA retries.
        stale = hass.data[DOMAIN].get(entry.data[CONF_MAC])
        if isinstance(stale, dict):
            stale.pop(CONF_ENTITY, None)
            stale.pop("bus_monitor", None)
        raise ConfigEntryNotReady(
            f"Gateway could not be reached or connection failed at {entry.data[CONF_HOST]}. Home Assistant will natively retry caching."
        )

    if not tests_results.get("Success", False):
        reason = tests_results.get("Message")
        if reason in ("password_error", "password_required"):
            # Home Assistant starts the reauth flow and shows the entry as
            # "Reauthentication required" instead of "Failed to set up".
            raise ConfigEntryAuthFailed(f"Gateway rejected the OpenWebNet password ({reason})")
        raise ConfigEntryNotReady(f"Gateway connection test failed at {entry.data[CONF_HOST]}: {reason}")

    _command_worker_count = (
        int(entry.options[CONF_WORKER_COUNT])
        if CONF_WORKER_COUNT in entry.options
        else 1
    )
    _session_limit = command_session_limit(gateway.model)
    if _session_limit is not None and _command_worker_count > _session_limit:
        LOGGER.warning(
            "%s The %s accepts at most %d command session(s) but %d were configured; "
            "the option is lowered to %d.",
            gateway.log_id,
            gateway.model,
            _session_limit,
            _command_worker_count,
            _session_limit,
        )
        _command_worker_count = _session_limit
        # Stored, so diagnostics show what runs and the options form does not
        # resubmit a count it would reject. The update listener is not yet
        # registered, so this does not trigger it.
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_WORKER_COUNT: _session_limit}
        )

    device_registry = dr.async_get(hass)

    _mfg = gateway.manufacturer or "BTicino S.p.A."
    _fw = gateway.firmware

    gateway_device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, entry.data[CONF_MAC])},
        identifiers={(DOMAIN, gateway.unique_id)},
        manufacturer=str(_mfg),
        name=gateway.name,
        model=gateway.model,
        sw_version=_fw,
        serial_number=gateway.mac or None,
    )

    gateway.device_registry_id = gateway_device_entry.id

    # entry.runtime_data is the only source of truth for the platforms. The
    # hass.data[DOMAIN][mac] mapping is a deprecated alias sharing the same dict
    # objects, kept for one release for out-of-tree readers; removed in 2.1.
    entry.runtime_data = runtime
    hass.data[DOMAIN][entry.data[CONF_MAC]] = {
        CONF_ENTITY: gateway,
        CONF_PLATFORMS: runtime.platforms,
        CONF_ENTITIES: runtime.entities,
        "bus_monitor": runtime.bus_monitor,
    }

    # Start consumers before the platforms enqueue their initial status
    # requests.  With a bounded command queue, forwarding a large plant before
    # a sending worker exists can otherwise block setup indefinitely.
    gateway.listening_worker = entry.async_create_background_task(
        hass, gateway.listening_loop(), name=f"myhome_{entry.entry_id}_listen"
    )
    for i in range(_command_worker_count):
        gateway.sending_workers.append(
            entry.async_create_background_task(
                hass, gateway.sending_loop(i), name=f"myhome_{entry.entry_id}_send_{i}"
            )
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Every platform is now subscribed to gateway messages, so discovery replies
    # can no longer be lost.  Queued from a task because the bounded command
    # queue may still be full of the platforms' own status requests.
    entry.async_create_background_task(
        hass, gateway.initial_discovery(), name=f"myhome_{entry.entry_id}_discovery"
    )

    # Prune orphaned devices with 0 entities from the device registry
    prune_stale_devices(hass, entry, gateway_device_entry, gateway)


    # ── Register options reload listener (rebuilds decoder pool on UI save) ──
    async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Rebuild the decoder pool when the user saves new options via the UI.

        Releases all active decoder assignments first so that no zone is left
        with a stale claim.  The user will need to re-trigger playback after
        changing decoder config.
        """
        # The same builder as platform setup, so the incompatible-decoder
        # repair issues follow the saved options straight away.
        from .media_player import _build_pool

        mac = entry.data[CONF_MAC]
        runtime_data = entry.runtime_data
        old_pool = runtime_data.decoder_pool
        if old_pool:
            await old_pool.release_all()

        pool = _build_pool(hass, entry)
        runtime_data.decoder_pool = pool
        LOGGER.info(
            "MyHOME: decoder pool rebuilt after options update — %d decoder(s) configured",
            len(pool.decoder_entity_ids),
        )

        # Signal all media player entities to re-publish supported_features
        # so Music Assistant picks up the new PLAY_MEDIA capability.
        from homeassistant.helpers.dispatcher import async_dispatcher_send
        async_dispatcher_send(hass, f"myhome_pool_updated_{mac}")

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: MyHOMEConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Let the user delete a device that is no longer on the bus (quality-scale stale-devices).

    Devices are discovered from bus traffic, so a device that is still wired in
    simply reappears on its next status frame; removing it is safe. Only the
    gateway itself is refused: it is the config entry.
    """
    runtime = entry.runtime_data if isinstance(getattr(entry, "runtime_data", None), MyHOMERuntimeData) else None
    gateway_ids = {getattr(runtime.gateway, "unique_id", None), getattr(runtime.gateway, "id", None)} if runtime else set()
    if any(
        ident[0] == DOMAIN and ident[1] in gateway_ids
        for ident in device_entry.identifiers
    ) or (dr.CONNECTION_NETWORK_MAC, str(entry.data.get(CONF_MAC, "")).lower()) in {
        (kind, str(value).lower()) for kind, value in device_entry.connections
    }:
        LOGGER.debug("Refusing to remove gateway device %s", device_entry.id)
        return False
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MyHOMEConfigEntry) -> bool:
    """Unload a config entry."""
    LOGGER.info("Unloading MyHome entry.")

    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    gateway_handler = entry.runtime_data.gateway
    hass.data[DOMAIN].pop(entry.data[CONF_MAC], None)
    setattr(entry, "runtime_data", None)

    return await gateway_handler.close_listener()
