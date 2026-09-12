""" MyHOME integration. """
import asyncio
import hashlib
import os

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.const import CONF_HOST, CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_DECODER_ENTITY,
    CONF_DECODER_PRE_GAIN,
    CONF_DECODER_SLOTS,
    CONF_DECODER_SOURCE,
    CONF_ENTITIES,
    CONF_ENTITY,
    CONF_FILE_PATH,
    CONF_GENERATE_EVENTS,
    CONF_PLATFORMS,
    CONF_WORKER_COUNT,
    CONF_ZONE,
    DOMAIN,
    INTEGRATION_VERSION,
    LOGGER,
    REQUIRED_OWND_VERSION,
    get_ownd_version,
)
from .gateway import MyHOMEGatewayHandler
from .services import async_setup_services, async_unload_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = ["light", "switch", "cover", "climate", "binary_sensor", "sensor", "media_player", "button", "alarm_control_panel"]


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
        if isinstance(lovelace, dict):
            resources = lovelace.get("resources")
        else:
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


def _sync_www_card(card_path: str, www_path: str) -> None:
    """Synchronize bus monitor card to /config/www/ as an additional fail-safe source."""
    try:
        os.makedirs(os.path.dirname(www_path), exist_ok=True)
        import shutil
        shutil.copy2(card_path, www_path)
        LOGGER.debug("Synchronized bus monitor card to %s", www_path)
    except Exception as err:
        LOGGER.debug("Could not copy card to www: %s", err)


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
            import homeassistant.components.http as ha_http

            static_path_cls = getattr(ha_http, "StaticPathConfig", None)

            if static_path_cls is not None and hasattr(http, "async_register_static_paths"):
                try:
                    await http.async_register_static_paths([
                        static_path_cls("/myhome_static", frontend_dir, False),
                        static_path_cls(static_url, card_path, False),
                    ])
                except Exception:
                    if hasattr(http, "register_static_path"):
                        http.register_static_path("/myhome_static", frontend_dir, False)
                        http.register_static_path(static_url, card_path, False)
            elif hasattr(http, "register_static_path"):
                http.register_static_path("/myhome_static", frontend_dir, False)
                http.register_static_path(static_url, card_path, False)

            # Also sync to /config/www/myhome-bus-card.js so /local/ is guaranteed to serve it
            if hasattr(hass, "config") and hasattr(hass.config, "path"):
                www_dir = hass.config.path("www")
                if www_dir:
                    www_card_path = os.path.join(www_dir, "myhome-bus-card.js")
                    await hass.async_add_executor_job(_sync_www_card, card_path, www_card_path)

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
                async def _delayed_retry():
                    await asyncio.sleep(1)
                    await _async_register_lovelace_resource(hass, versioned_url)

                hass.async_create_task(_delayed_retry())
            else:
                async def _on_ha_started(event):
                    await _async_register_lovelace_resource(hass, versioned_url)

                from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
                hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_ha_started)
            domain_data["_lovelace_listener_registered"] = True


async def async_ensure_ownd_engine(hass: HomeAssistant) -> bool:
    """Ensure that the exact matching OWNd engine requirement is installed and loaded."""
    required_pkg = f"OWNd=={REQUIRED_OWND_VERSION}"
    from homeassistant.util import package as pkg_util

    # Fast path: requirement satisfied and currently loaded version matches
    current_ver = await hass.async_add_executor_job(get_ownd_version)
    is_installed = await hass.async_add_executor_job(pkg_util.is_installed, required_pkg)
    if is_installed and current_ver == REQUIRED_OWND_VERSION:
        return True

    LOGGER.warning(
        "OWNd engine mismatch detected (installed: %s, required: %s). "
        "Attempting automatic self-healing installation via Home Assistant package manager...",
        current_ver,
        required_pkg,
    )

    # 1. Install missing/outdated distribution from PyPI via Home Assistant's native processor
    if not is_installed:
        try:
            from homeassistant.requirements import async_process_requirements
            await async_process_requirements(hass, DOMAIN, [required_pkg])
            if hasattr(get_ownd_version, "cache_clear"):
                get_ownd_version.cache_clear()
            LOGGER.info("Successfully installed matching requirement %s", required_pkg)
        except Exception as err:
            LOGGER.error("Automatic installation of %s failed: %s", required_pkg, err)
            try:
                from homeassistant.components import persistent_notification
                persistent_notification.async_create(
                    hass,
                    title="MyHOME Engine Mismatch",
                    message=(
                        f"MyHOME integration v{INTEGRATION_VERSION} requires OWNd v{REQUIRED_OWND_VERSION}, "
                        f"but detected {current_ver}.\n\n"
                        f"Automatic package upgrade failed ({err}). Please check network connectivity or run:\n"
                        f"```bash\npip install '{required_pkg}'\n```"
                    ),
                    notification_id="myhome_ownd_version_mismatch",
                )
            except Exception:  # pragma: no cover
                pass
            return False

    # 2. Invalidate importlib caches and reload loaded in-memory OWNd modules
    import importlib
    import sys

    if hasattr(get_ownd_version, "cache_clear"):
        get_ownd_version.cache_clear()
    importlib.invalidate_caches()
    for mod_name in list(sys.modules.keys()):
        if mod_name == "OWNd" or mod_name.startswith("OWNd."):
            try:
                importlib.reload(sys.modules[mod_name])
            except Exception as reload_err:  # pragma: no cover
                LOGGER.debug("Could not reload module %s: %s", mod_name, reload_err)

    new_ver = await hass.async_add_executor_job(get_ownd_version)
    LOGGER.info("Self-healing complete: OWNd engine synchronized to v%s", new_ver)
    return True


async def async_setup(hass, config):
    """Set up the MyHOME component."""
    hass.data.setdefault(DOMAIN, {})

    await async_ensure_ownd_engine(hass)

    LOGGER.info(
        "Initializing MyHOME integration v%s (OWNd v%s)",
        INTEGRATION_VERSION,
        await hass.async_add_executor_job(get_ownd_version),
    )

    from .websocket import async_setup_websocket_api
    async_setup_websocket_api(hass)
    await _async_register_frontend(hass)

    if DOMAIN not in config:
        return True

    LOGGER.error("configuration.yaml not supported for this component!")

    return False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    if not await async_ensure_ownd_engine(hass):
        ownd_ver = await hass.async_add_executor_job(get_ownd_version)
        raise ConfigEntryNotReady(
            f"Required OWNd engine version {REQUIRED_OWND_VERSION} is not available (found: {ownd_ver})"
        )

    from .websocket import async_setup_websocket_api
    async_setup_websocket_api(hass)
    await _async_register_frontend(hass)

    ownd_ver = await hass.async_add_executor_job(get_ownd_version)
    LOGGER.info(
        "Setting up MyHOME gateway '%s' (v%s, OWNd v%s)",
        entry.title,
        INTEGRATION_VERSION,
        ownd_ver,
    )

    if entry.data[CONF_MAC] not in hass.data[DOMAIN]:
        hass.data[DOMAIN][entry.data[CONF_MAC]] = {
            CONF_PLATFORMS: {p: {} for p in PLATFORMS},
            CONF_ENTITIES: {p: {} for p in PLATFORMS},
        }

    # Load legacy myhome.yaml if present for seamless backward-compatibility
    _opt_path = entry.options.get(CONF_FILE_PATH) or entry.options.get("file_path")
    _config_file_path = str(_opt_path) if _opt_path else hass.config.path("myhome.yaml")
    if not os.path.isfile(_config_file_path) and os.path.isfile("/config/myhome.yaml"):
        _config_file_path = "/config/myhome.yaml"

    if os.path.isfile(_config_file_path):
        from homeassistant.util.yaml.loader import load_yaml

        from .validate import config_schema
        try:
            raw_yaml = await hass.async_add_executor_job(load_yaml, _config_file_path)
            if raw_yaml and isinstance(raw_yaml, dict):
                # Support single-gateway config without MAC address header at root level
                if any(plat in raw_yaml for plat in PLATFORMS):
                    configured_gateways = [
                        e for e in hass.config_entries.async_entries(DOMAIN)
                        if not getattr(e, "disabled_by", None)
                    ]
                    if len(configured_gateways) <= 1:
                        raw_yaml = {entry.data[CONF_MAC]: raw_yaml}
                    else:
                        LOGGER.error(
                            "myhome.yaml contains top-level platform configurations without a gateway MAC, "
                            "but %d gateways are configured. Please specify the gateway MAC address header in myhome.yaml.",
                            len(configured_gateways)
                        )
                        raw_yaml = {}

                # Ensure every gateway has mac and every device has where set if omitted
                for gw_key, gw_val in raw_yaml.items():
                    if isinstance(gw_val, dict):
                        if CONF_MAC not in gw_val:
                            gw_val[CONF_MAC] = str(gw_key)
                        for plat, devs in gw_val.items():
                            if isinstance(devs, dict):
                                for d_key, d_val in devs.items():
                                    if isinstance(d_val, dict) and "where" not in d_val and "zone" not in d_val:
                                        d_val["where"] = str(d_key)

                _validated = config_schema(raw_yaml)
                formatted_entry_mac = dr.format_mac(entry.data[CONF_MAC])
                mac_key = None
                if formatted_entry_mac in _validated:
                    mac_key = formatted_entry_mac
                elif entry.data[CONF_MAC] in _validated:
                    mac_key = entry.data[CONF_MAC]
                else:
                    for k in _validated.keys():
                        try:
                            if dr.format_mac(k) == formatted_entry_mac:
                                mac_key = k
                                break
                        except Exception:
                            continue
                if mac_key and mac_key in _validated:
                    yaml_platforms = _validated[mac_key].get(CONF_PLATFORMS, {})
                    for plat, devices in yaml_platforms.items():
                        if plat in hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS]:
                            for d_id, d_cfg in devices.items():
                                hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][plat][d_id] = d_cfg
                                if isinstance(d_cfg, dict):
                                    if "where" in d_cfg:
                                        hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][plat][str(d_cfg["where"])] = d_cfg
                                    if CONF_ZONE in d_cfg or "zone" in d_cfg:
                                        z_val = str(d_cfg.get(CONF_ZONE) or d_cfg.get("zone"))
                                        hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][plat][z_val] = d_cfg
                                        clean_z = z_val.split("#")[-1]
                                        hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][plat][clean_z] = d_cfg
                                        hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][plat][f"zone_{clean_z}"] = d_cfg
                    LOGGER.info("Loaded legacy myhome.yaml configuration for gateway %s (%s platforms)", entry.data[CONF_MAC], len(yaml_platforms))
        except Exception as e:
            LOGGER.error("Failed to parse myhome.yaml from %s: %s", _config_file_path, e)

    _generate_events = (
        entry.options.get(CONF_GENERATE_EVENTS, False)
    )

    # Migrating the config entry's unique_id if it was not formated to the recommended hass standard
    if entry.unique_id != dr.format_mac(entry.unique_id):
        hass.config_entries.async_update_entry(
            entry, unique_id=dr.format_mac(entry.unique_id)
        )
        LOGGER.warning("Migrating config entry unique_id to %s", entry.unique_id)

    entity_registry = er.async_get(hass)
    _mac = dr.format_mac(entry.data[CONF_MAC])

    _domain_to_who = {
        "light": "1",
        "cover": "2",
        "switch": "1",
        "media_player": "16",
        "climate": "4",
    }

    registry_entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    for reg_entry in registry_entries:
        parts = reg_entry.unique_id.split("-")
        # Old unique_id format: MAC-WHERE (MAC may be formatted with colons or raw hex)
        is_matching_mac = False
        mac_prefix = parts[0] if parts else ""
        if mac_prefix == _mac or mac_prefix == entry.data[CONF_MAC]:
            is_matching_mac = True
        elif mac_prefix:
            try:
                is_matching_mac = (dr.format_mac(mac_prefix) == _mac)
            except Exception:
                is_matching_mac = False

        if not is_matching_mac:
            continue

        after_mac = reg_entry.unique_id[len(mac_prefix) + 1:]

        if reg_entry.domain == "button":
            btn_type = "disable" if after_mac.endswith("-disable") else "enable" if after_mac.endswith("-enable") else None
            if btn_type:
                raw_where = after_mac[:-len(btn_type)-1]
                subparts = raw_where.split("-")
                if len(subparts) == 1:
                    # Missing WHO (2.0b3 unique_id format {mac}-{where}-{btn_type})
                    who = None
                    device_registry = dr.async_get(hass)
                    if reg_entry.device_id:
                        dev = device_registry.async_get(reg_entry.device_id)
                        if dev:
                            for ident in dev.identifiers:
                                if len(ident) == 2 and ident[0] == DOMAIN:
                                    id_parts = str(ident[1]).split("-")
                                    if len(id_parts) >= 3 and id_parts[1].isdigit():
                                        who = id_parts[1]
                                        break
                    if not who:
                        gw_platforms = hass.data.get(DOMAIN, {}).get(entry.data[CONF_MAC], {}).get(CONF_PLATFORMS, {})
                        if "cover" in gw_platforms and (raw_where in gw_platforms["cover"] or f"2-{raw_where}" in gw_platforms["cover"]):
                            who = "2"
                        else:
                            who = "1"

                    target_unique_id = f"{_mac}-{who}-{raw_where}-{btn_type}"
                    existing_canonical_id = entity_registry.async_get_entity_id("button", DOMAIN, target_unique_id)
                    if existing_canonical_id and existing_canonical_id != reg_entry.entity_id:
                        try:
                            entity_registry.async_remove(reg_entry.entity_id)
                            LOGGER.info("Pruned duplicate button entity %s in favor of %s", reg_entry.entity_id, existing_canonical_id)
                        except Exception as err:
                            LOGGER.warning("Could not prune duplicate button entity %s: %s", reg_entry.entity_id, err)
                    else:
                        update_kwargs = {"new_unique_id": target_unique_id}
                        if reg_entry.entity_id.endswith("_2"):
                            base_id = reg_entry.entity_id[:-2]
                            if not entity_registry.async_get(base_id):
                                update_kwargs["new_entity_id"] = base_id
                        try:
                            entity_registry.async_update_entity(reg_entry.entity_id, **update_kwargs)
                            reg_entry = entity_registry.async_get(reg_entry.entity_id)
                            LOGGER.info("Migrated button entity %s to canonical unique_id %s", reg_entry.entity_id, target_unique_id)
                        except ValueError as err:
                            LOGGER.warning("Could not auto-migrate button entity %s: %s", reg_entry.entity_id, err)
                elif mac_prefix != _mac:
                    target_unique_id = f"{_mac}-{after_mac}"
                    if not entity_registry.async_get_entity_id("button", DOMAIN, target_unique_id):
                        try:
                            entity_registry.async_update_entity(reg_entry.entity_id, new_unique_id=target_unique_id)
                            reg_entry = entity_registry.async_get(reg_entry.entity_id)
                        except ValueError:
                            pass
            continue

        # Other platforms (light, cover, switch, media_player, climate)
        subparts = after_mac.split("-")
        if len(subparts) == 1:
            where_part = subparts[0]
            who = _domain_to_who.get(reg_entry.domain)
            if who:
                new_unique_id = f"{_mac}-{who}-{where_part}"
                if not entity_registry.async_get_entity_id(reg_entry.domain, DOMAIN, new_unique_id):
                    try:
                        entity_registry.async_update_entity(
                            reg_entry.entity_id, new_unique_id=new_unique_id
                        )
                        reg_entry = entity_registry.async_get(reg_entry.entity_id) # reload
                        LOGGER.info("Resurrecting orphaned MyHOME entity %s to new unique_id %s", reg_entry.entity_id, new_unique_id)
                    except ValueError as e:
                        LOGGER.warning("Could not auto-migrate entity %s to %s: %s", reg_entry.entity_id, new_unique_id, e)

                # Also migrate matching device in device_registry if present so custom device names and areas are preserved
                device_registry = dr.async_get(hass)
                old_device = (
                    device_registry.async_get_device(identifiers={(DOMAIN, f"{_mac}-{where_part}")})
                    or device_registry.async_get_device(identifiers={(DOMAIN, f"{entry.data[CONF_MAC]}-{where_part}")})
                    or (device_registry.async_get(reg_entry.device_id) if reg_entry.device_id else None)
                )
                if old_device:
                    try:
                        device_registry.async_update_device(
                            old_device.id,
                            new_identifiers={(DOMAIN, f"{_mac}-{who}-{where_part}")},
                        )
                    except Exception as e:
                        LOGGER.warning("Could not auto-migrate device %s to new identifier: %s", old_device.id, e)
        elif mac_prefix != _mac:
            new_unique_id = f"{_mac}-{after_mac}"
            if not entity_registry.async_get_entity_id(reg_entry.domain, DOMAIN, new_unique_id):
                try:
                    entity_registry.async_update_entity(
                        reg_entry.entity_id, new_unique_id=new_unique_id
                    )
                    reg_entry = entity_registry.async_get(reg_entry.entity_id)
                except ValueError:
                    pass

    # Hack to forcefully absorb customize.yaml for users who deleted their integrations
    # and therefore lost the transparent entity_registry migration!
    from homeassistant.util.yaml.loader import load_yaml

    hass.data[DOMAIN]["customizations"] = {}
    customize_file = hass.config.path("customize.yaml")
    if os.path.isfile(customize_file):
        try:
            hass.data[DOMAIN]["customizations"] = (
                await hass.async_add_executor_job(load_yaml, customize_file) or {}
            )
            LOGGER.info("Successfully loaded %s custom names from customize.yaml for recovery", len(hass.data[DOMAIN]["customizations"]))
        except Exception as e:
            LOGGER.error("Failed to parse customize.yaml for friendly_name recovery: %s", e)

    gateway = MyHOMEGatewayHandler(
        hass=hass, config_entry=entry, generate_events=_generate_events
    )
    hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY] = gateway
    hass.data[DOMAIN][entry.data[CONF_MAC]]["bus_monitor"] = gateway.bus_monitor

    try:
        tests_results = await gateway.test()
    except (asyncio.TimeoutError, ConnectionError, OSError) as e:
        LOGGER.warning("Gateway connection test failed: %s", e)
        tests_results = None

    if tests_results is None:
        raise ConfigEntryNotReady(
            f"Gateway could not be reached or connection failed at {entry.data[CONF_HOST]}. Home Assistant will natively retry caching."
        )

    if not tests_results.get("Success", False):
        if (
            tests_results.get("Message") == "password_error"
            or tests_results.get("Message") == "password_required"
        ):
            hass.async_create_task(
                hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
                    data=entry.data,
                )
            )
        del hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY]
        return False

    _command_worker_count = (
        int(entry.options[CONF_WORKER_COUNT])
        if CONF_WORKER_COUNT in entry.options
        else 1
    )

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    _mfg = hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].manufacturer
    if isinstance(_mfg, (list, tuple)):
        _mfg = _mfg[0] if _mfg else "BTicino S.p.A."
    elif not _mfg:
        _mfg = "BTicino S.p.A."

    _fw = hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].firmware
    if isinstance(_fw, (list, tuple)):
        _fw = ".".join(str(x) for x in _fw) if _fw else None
    elif _fw is not None:
        _fw = str(_fw)
    else:
        _fw = None

    gateway_device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, entry.data[CONF_MAC])},
        identifiers={
            (DOMAIN, hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].unique_id)
        },
        manufacturer=str(_mfg),
        name=hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].name,
        model=hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].model,
        sw_version=_fw,
    )

    hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].device_registry_id = (
        gateway_device_entry.id
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Prune orphaned devices with 0 entities from the device registry
    try:
        gateway_dev_id = getattr(gateway_device_entry, "id", None)
        gateway_handler = hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY]
        gateway_unique_id = getattr(gateway_handler, "unique_id", None)
        gateway_id = getattr(gateway_handler, "id", None)
        for dev in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
            if dev.id == gateway_dev_id:
                continue
            if gateway_unique_id and (DOMAIN, gateway_unique_id) in dev.identifiers:
                continue
            if gateway_id and (DOMAIN, gateway_id) in dev.identifiers:
                continue
            # Do not prune scenario devices (CEN / CEN+) that intentionally have no entities
            is_scenario_device = (
                (dev.model and ("Scenario Control" in dev.model or dev.model.startswith("CEN")))
                or (dev.name and (dev.name.startswith("CEN") or "Scenario" in dev.name))
                or any(
                    isinstance(ident[1], str)
                    and (
                        "-15-" in ident[1]
                        or ident[1].startswith("cen")
                        or ident[1].startswith("cenplus")
                    )
                    for ident in dev.identifiers
                    if ident[0] == DOMAIN
                )
            )
            if is_scenario_device:
                continue
            dev_entries = er.async_entries_for_device(
                entity_registry, dev.id, include_disabled_entities=True
            )
            if len(dev_entries) == 0:
                LOGGER.info(
                    "Pruning empty orphaned MyHOME device from registry: %s (%s)",
                    dev.name,
                    dev.id,
                )
                device_registry.async_remove_device(dev.id)
    except Exception as err:
        LOGGER.debug("Error during empty device pruning: %s", err)


    # ── Register options reload listener (rebuilds decoder pool on UI save) ──
    async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Rebuild the decoder pool when the user saves new options via the UI.

        Releases all active decoder assignments first so that no zone is left
        with a stale claim.  The user will need to re-trigger playback after
        changing decoder config.
        """
        from .decoder_pool import DecoderPool

        mac = entry.data[CONF_MAC]
        old_pool = hass.data.get(DOMAIN, {}).get(mac, {}).get("decoder_pool")
        if old_pool:
            await old_pool.release_all()

        options = entry.options
        decoder_map: dict[str, int] = {}
        pre_gain_map: dict[str, int] = {}
        for i in range(1, CONF_DECODER_SLOTS + 1):
            entity_id = options.get(CONF_DECODER_ENTITY.format(i), "").strip()
            source_num = options.get(CONF_DECODER_SOURCE.format(i), i)
            pre_gain = options.get(CONF_DECODER_PRE_GAIN.format(i), 0)
            if entity_id and entity_id.startswith("media_player."):
                decoder_map[entity_id] = int(source_num)
                pre_gain_map[entity_id] = int(pre_gain)

        pool = DecoderPool(hass, decoder_map, pre_gain_map)
        hass.data[DOMAIN][mac]["decoder_pool"] = pool
        LOGGER.info(
            "MyHOME: decoder pool rebuilt after options update — %d decoder(s) configured",
            len(decoder_map),
        )

        # Signal all media player entities to re-publish supported_features
        # so Music Assistant picks up the new PLAY_MEDIA capability.
        from homeassistant.helpers.dispatcher import async_dispatcher_send
        async_dispatcher_send(hass, f"myhome_pool_updated_{mac}")

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    gateway.listening_worker = entry.async_create_background_task(
        hass, gateway.listening_loop(), name=f"myhome_{entry.entry_id}_listen"
    )
    for i in range(_command_worker_count):
        gateway.sending_workers.append(
            entry.async_create_background_task(
                hass, gateway.sending_loop(i), name=f"myhome_{entry.entry_id}_send_{i}"
            )
        )

    # Static entity pruning has been removed in favor of dynamic discovery.

    # Set modern runtime_data while maintaining backwards-compatible hass.data
    entry.runtime_data = gateway

    # Register domain services
    await async_setup_services(hass)

    return True


async def async_unload_entry(hass, entry):
    """Unload a config entry."""

    LOGGER.info("Unloading MyHome entry.")

    for platform in PLATFORMS:
        await hass.config_entries.async_forward_entry_unload(entry, platform)

    # Check if there are other configured entries before unloading services
    entries = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id
    ]
    if not entries:
        await async_unload_services(hass)

    gateway_handler = hass.data[DOMAIN][entry.data[CONF_MAC]].pop(CONF_ENTITY)
    del hass.data[DOMAIN][entry.data[CONF_MAC]]

    return await gateway_handler.close_listener()
