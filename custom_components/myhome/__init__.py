""" MyHOME integration. """
import asyncio
import os

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.const import CONF_HOST, CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNCommand, OWNGatewayCommand

from .const import (
    ATTR_GATEWAY,
    ATTR_MESSAGE,
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
    DOMAIN,
    LOGGER,
)
from .gateway import MyHOMEGatewayHandler

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = ["light", "switch", "cover", "climate", "binary_sensor", "sensor", "media_player", "button", "alarm_control_panel"]


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
            existing = [
                item["url"].split("?")[0]
                for item in (resources.async_items() or [])
                if isinstance(item, dict) and isinstance(item.get("url"), str)
            ]
            if clean_url not in existing:
                await resources.async_create_item({
                    "res_type": "module",
                    "url": url_path,
                })
                LOGGER.debug("Auto-registered Lovelace bus monitor resource: %s", url_path)
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
    url_path = "/myhome_static/myhome-bus-card.js"

    http = getattr(hass, "http", None)
    if not domain_data.get("_frontend_registered"):
        if http is not None and os.path.isfile(card_path):
            if hasattr(http, "async_register_static_paths"):
                try:
                    from homeassistant.components.http import StaticPathConfig
                    await http.async_register_static_paths([
                        StaticPathConfig(url_path, card_path, False)
                    ])
                except Exception:
                    http.register_static_path(url_path, card_path, False)
            elif hasattr(http, "register_static_path"):
                http.register_static_path(url_path, card_path, False)

            try:
                from homeassistant.components import frontend
                frontend.add_extra_js_url(hass, url_path)
            except Exception as e:
                LOGGER.debug("Could not add extra js url for Lovelace card: %s", e)

            domain_data["_frontend_registered"] = True

    # Auto-register resource in Lovelace dashboard resources collection
    if not await _async_register_lovelace_resource(hass, url_path):
        if not domain_data.get("_lovelace_listener_registered"):
            if getattr(hass, "is_running", False):
                async def _delayed_retry():
                    await asyncio.sleep(1)
                    await _async_register_lovelace_resource(hass, url_path)

                hass.async_create_task(_delayed_retry())
            else:
                async def _on_ha_started(event):
                    await _async_register_lovelace_resource(hass, url_path)

                from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
                hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_ha_started)
            domain_data["_lovelace_listener_registered"] = True


async def async_setup(hass, config):
    """Set up the MyHOME component."""
    hass.data.setdefault(DOMAIN, {})

    from .websocket import async_setup_websocket_api
    async_setup_websocket_api(hass)
    await _async_register_frontend(hass)

    if DOMAIN not in config:
        return True

    LOGGER.error("configuration.yaml not supported for this component!")

    return False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    from .websocket import async_setup_websocket_api
    async_setup_websocket_api(hass)
    await _async_register_frontend(hass)

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
                                if isinstance(d_cfg, dict) and "where" in d_cfg:
                                    hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][plat][str(d_cfg["where"])] = d_cfg
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
        if len(parts) == 2:
            if parts[0] == _mac or parts[0] == entry.data[CONF_MAC]:
                is_matching_mac = True
            elif parts[0]:
                try:
                    is_matching_mac = (dr.format_mac(parts[0]) == _mac)
                except Exception:
                    is_matching_mac = False

        if is_matching_mac:
            where_part = parts[1]
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
            if any(
                isinstance(ident[1], str)
                and (
                    "-15-" in ident[1]
                    or "-25-" in ident[1]
                    or ident[1].startswith("cen")
                )
                for ident in dev.identifiers
                if ident[0] == DOMAIN
            ):
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

    # Defining the services
    async def handle_sync_time(call):
        gateway = call.data.get(ATTR_GATEWAY, None)
        if gateway is None:
            _gw_keys = [k for k in hass.data[DOMAIN] if isinstance(k, str) and ":" in k]
            if not _gw_keys:
                LOGGER.error("No MyHOME gateways found, cannot sync time.")
                return False
            gateway = _gw_keys[0]
        else:
            mac = dr.format_mac(gateway)
            if mac is None:
                LOGGER.error(
                    "Invalid gateway mac `%s`, could not send time synchronisation message.",
                    gateway,
                )
                return False
            else:
                gateway = mac
        timezone = hass.config.as_dict()["time_zone"]
        if gateway in hass.data[DOMAIN]:
            await hass.data[DOMAIN][gateway][CONF_ENTITY].send(
                OWNGatewayCommand.set_datetime_to_now(timezone)
            )
        else:
            LOGGER.error(
                "Gateway `%s` not found, could not send time synchronisation message.",
                gateway,
            )
            return False

    hass.services.async_register(DOMAIN, "sync_time", handle_sync_time)

    async def handle_send_message(call):
        gateway = call.data.get(ATTR_GATEWAY, None)
        message = call.data.get(ATTR_MESSAGE, None)
        if gateway is None:
            _gw_keys = [k for k in hass.data[DOMAIN] if isinstance(k, str) and ":" in k]
            if not _gw_keys:
                LOGGER.error("No MyHOME gateways found, cannot send message `%s`.", message)
                return False
            gateway = _gw_keys[0]
        else:
            mac = dr.format_mac(gateway)
            if mac is None:
                LOGGER.error(
                    "Invalid gateway mac `%s`, could not send message `%s`.",
                    gateway,
                    message,
                )
                return False
            else:
                gateway = mac
        LOGGER.debug("Handling message `%s` to be sent to `%s`", message, gateway)
        if gateway in hass.data[DOMAIN]:
            if message is not None:
                own_message = OWNCommand.parse(message)
                if own_message is not None:
                    if own_message.is_valid:
                        LOGGER.debug(
                            "%s Sending valid OpenWebNet Message: `%s`",
                            hass.data[DOMAIN][gateway][CONF_ENTITY].log_id,
                            own_message,
                        )
                        await hass.data[DOMAIN][gateway][CONF_ENTITY].send(own_message)
                else:
                    LOGGER.error(
                        "Could not parse message `%s`, not sending it.", message
                    )
                    return False
        else:
            LOGGER.error(
                "Gateway `%s` not found, could not send message `%s`.", gateway, message
            )
            return False

    hass.services.async_register(DOMAIN, "send_message", handle_send_message)

    return True


async def async_unload_entry(hass, entry):
    """Unload a config entry."""

    LOGGER.info("Unloading MyHome entry.")

    for platform in PLATFORMS:
        await hass.config_entries.async_forward_entry_unload(entry, platform)

    hass.services.async_remove(DOMAIN, "sync_time")
    hass.services.async_remove(DOMAIN, "send_message")

    gateway_handler = hass.data[DOMAIN][entry.data[CONF_MAC]].pop(CONF_ENTITY)
    del hass.data[DOMAIN][entry.data[CONF_MAC]]

    return await gateway_handler.close_listener()
