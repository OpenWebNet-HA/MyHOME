"""Support for MyHome heating."""

from homeassistant.components.climate import (
    DOMAIN as PLATFORM,
)
from homeassistant.components.climate import (
    ClimateEntity,
)
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
    UnitOfTemperature,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from OWNd.message import (
    CLIMATE_MODE_AUTO,
    CLIMATE_MODE_COOL,
    CLIMATE_MODE_HEAT,
    CLIMATE_MODE_OFF,
    MESSAGE_TYPE_ACTION,
    MESSAGE_TYPE_FAN_SPEED,
    MESSAGE_TYPE_LOCAL_OFFSET,
    MESSAGE_TYPE_LOCAL_TARGET_TEMPERATURE,
    MESSAGE_TYPE_MAIN_HUMIDITY,
    MESSAGE_TYPE_MAIN_TEMPERATURE,
    MESSAGE_TYPE_MODE,
    MESSAGE_TYPE_MODE_TARGET,
    MESSAGE_TYPE_TARGET_TEMPERATURE,
    OWNHeatingCommand,
    OWNHeatingEvent,
)

from .const import (
    CONF_BUS_INTERFACE,
    CONF_CENTRAL,
    CONF_COOLING_SUPPORT,
    CONF_DEVICE_MODEL,
    CONF_ENTITY,
    CONF_FAN_SUPPORT,
    CONF_HEATING_SUPPORT,
    CONF_MANUFACTURER,
    CONF_PLATFORMS,
    CONF_STANDALONE,
    CONF_WHERE,
    CONF_WHO,
    CONF_ZONE,
    DOMAIN,
    LOGGER,
)
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the MyHOME climate platform dynamically via Discovery."""
    mac = config_entry.data.get(CONF_MAC)
    if not mac or mac not in hass.data.get(DOMAIN, {}):
        return True
    if PLATFORM not in hass.data[DOMAIN][mac].get(CONF_PLATFORMS, {}):
        return True

    gateway = hass.data[DOMAIN][mac].get(CONF_ENTITY)
    if not gateway:
        return True

    known_climates = set()

    # 1. Restore previously registered climate entities from the Entity Registry
    try:
        entity_registry = er.async_get(hass)
        existing_entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    except Exception:
        entity_registry = None
        existing_entries = []

    _configured_climate_devices = hass.data[DOMAIN][mac].get(CONF_PLATFORMS, {}).get(PLATFORM, {})

    restored_climates = []
    for entry in existing_entries:
        if entry.domain == PLATFORM:
            unique_id = entry.unique_id
            # unique_id format: "{mac}-4-{device_id}"
            after_mac = unique_id.replace(f"{gateway.mac}-", "", 1).replace(f"{mac}-", "", 1)
            parts_who = after_mac.split("-", 1)
            device_id = parts_who[-1] if len(parts_who) > 1 else after_mac
            if "#4#" in device_id:
                parts = device_id.split("#4#")
                where = parts[0]
                interface = parts[1] if len(parts) > 1 else None
            else:
                where = device_id
                interface = None

            clean_where = where.split("-")[-1].replace("#", "")
            if clean_where.isdigit() and int(clean_where) >= 100:
                LOGGER.debug("Skipping non-zone address %s for climate platform", where)
                continue
            default_suffix = f"{clean_where}I{interface}" if interface else clean_where
            cfg = (
                _configured_climate_devices.get(device_id)
                or _configured_climate_devices.get(where)
                or _configured_climate_devices.get(clean_where)
                or _configured_climate_devices.get(f"4-{clean_where}")
                or _configured_climate_devices.get(f"4-{where}")
                or _configured_climate_devices.get(f"4-{device_id}")
                or _configured_climate_devices.get(f"4-#{clean_where}")
                or _configured_climate_devices.get(f"4-#{where}")
                or _configured_climate_devices.get(f"#{clean_where}")
                or _configured_climate_devices.get(f"#{where}")
                or _configured_climate_devices.get(f"zone_{clean_where}")
                or _configured_climate_devices.get(f"zone_{where}")
                or {}
            )

            is_central = cfg.get(CONF_CENTRAL, clean_where in ("0", "01") or where in ("#0", "#0#1"))
            _customs = hass.data.get(DOMAIN, {}).get("customizations", {})
            _custom_entry = _customs.get(entry.entity_id, {})
            _entry_name = getattr(entry, "name", None)
            if not isinstance(_entry_name, str):
                _entry_name = None
            default_name = f"Central Unit {default_suffix}" if is_central else f"Climate Zone {default_suffix}"
            _name = (
                cfg.get(CONF_NAME)
                or _custom_entry.get("friendly_name")
                or _entry_name
                or default_name
            )
            default_model = "Central Unit (3550)" if where == "#0" else ("Central Unit (4695)" if where == "#0#1" else "Heating Zone")
            _climate = MyHOMEClimate(
                hass=hass,
                device_id=device_id,
                who="4",
                where=where,
                interface=interface,
                name=_name,
                heating=cfg.get(CONF_HEATING_SUPPORT, True),
                cooling=cfg.get(CONF_COOLING_SUPPORT, True),
                fan=cfg.get(CONF_FAN_SUPPORT, False),
                standalone=cfg.get(CONF_STANDALONE, not is_central),
                central=is_central,
                manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
                model=cfg.get(CONF_DEVICE_MODEL, default_model),
                gateway=gateway,
            )
            known_climates.add(device_id)
            known_climates.add(where)
            if not interface:
                known_climates.add(clean_where)
            restored_climates.append(_climate)

    # 2. Also instantiate any configured climate devices from myhome.yaml not yet in registry
    seen_configured_where = set()
    for dev_id, cfg in _configured_climate_devices.items():
        where = str(cfg.get(CONF_ZONE, cfg.get(CONF_WHERE, dev_id)))
        interface = cfg.get(CONF_BUS_INTERFACE) or cfg.get("bus_interface") or cfg.get("interface")
        clean_where = where.split("-")[-1].replace("#", "")
        if clean_where.isdigit() and int(clean_where) >= 100:
            LOGGER.debug("Skipping non-zone address %s for climate platform", where)
            continue
        device_where_id = f"{where}#4#{interface}" if interface else str(where)
        clean_unique_id = f"{clean_where}#4#{interface}" if interface else clean_where
        default_suffix = f"{clean_where}I{interface}" if interface else clean_where

        if (
            clean_unique_id in seen_configured_where
            or device_where_id in known_climates
            or dev_id in known_climates
            or where in known_climates
        ):
            continue
        seen_configured_where.add(clean_unique_id)

        is_central = cfg.get(CONF_CENTRAL, clean_where in ("0", "01") or where in ("#0", "#0#1"))
        default_name = f"Central Unit {default_suffix}" if is_central else f"Climate Zone {default_suffix}"
        default_model = "Central Unit (3550)" if where == "#0" else ("Central Unit (4695)" if where == "#0#1" else "Heating Zone")
        _climate = MyHOMEClimate(
            hass=hass,
            device_id=device_where_id,
            who=str(cfg.get(CONF_WHO, "4")),
            where=where,
            interface=interface,
            name=cfg.get(CONF_NAME) or default_name,
            heating=cfg.get(CONF_HEATING_SUPPORT, True),
            cooling=cfg.get(CONF_COOLING_SUPPORT, True),
            fan=cfg.get(CONF_FAN_SUPPORT, False),
            standalone=cfg.get(CONF_STANDALONE, not is_central),
            central=is_central,
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, default_model),
            gateway=gateway,
        )
        known_climates.add(device_where_id)
        known_climates.add(dev_id)
        known_climates.add(where)
        if not interface:
            known_climates.add(clean_where)
        restored_climates.append(_climate)

    if restored_climates:
        async_add_entities(restored_climates)

    @callback
    def async_add_climate(message: OWNHeatingEvent):
        """Add a climate zone from a discovered message."""
        raw_where = getattr(message, "where", None)
        zone = getattr(message, "zone", None)
        interface = getattr(message, "interface", None)

        target_zone = zone
        what = getattr(message, "what", None)
        what_param = (
            getattr(message, "what_param", None) or getattr(message, "_what_param", None) or []
        )
        where_param = (
            getattr(message, "where_param", None) or getattr(message, "_where_param", None) or []
        )

        if not interface and where_param and len(where_param) > 1 and where_param[0] == "4":
            interface = str(where_param[1])

        calling_zones = []
        if target_zone is not None and target_zone > 0:
            calling_zones.append(str(target_zone))
        if what in ("4001", "4002", 4001, 4002) and what_param:
            try:
                calling_zones.append(str(int(what_param[0])))
            except (ValueError, TypeError):
                pass
        if where_param and where_param[0] != "4":
            try:
                calling_zones.append(str(int(where_param[0])))
            except (ValueError, TypeError):
                pass

        if not calling_zones and raw_where and raw_where not in ("0", ""):
            clean_raw = str(raw_where).split("-")[-1].replace("#", "")
            if not (clean_raw.isdigit() and int(clean_raw) >= 100):
                calling_zones.append(str(raw_where))

        if not calling_zones and (not raw_where or raw_where == "0"):
            # Broadcast frame with no specific zone; ignore for entity creation
            pass
        else:
            primary_zone = calling_zones[0] if calling_zones else str(raw_where)
            where = primary_zone
            clean_where = where.split("-")[-1].replace("#", "")
            unique_id = f"{where}#4#{interface}" if interface else str(where)
            default_suffix = f"{clean_where}I{interface}" if interface else clean_where

            if (
                not (clean_where.isdigit() and int(clean_where) >= 100)
                and unique_id not in known_climates
                and clean_where not in known_climates
                and where not in known_climates
            ):
                cfg = (
                    _configured_climate_devices.get(unique_id)
                    or _configured_climate_devices.get(where)
                    or _configured_climate_devices.get(clean_where)
                    or _configured_climate_devices.get(f"4-{clean_where}")
                    or _configured_climate_devices.get(f"4-{where}")
                    or _configured_climate_devices.get(f"4-{unique_id}")
                    or _configured_climate_devices.get(f"4-#{clean_where}")
                    or _configured_climate_devices.get(f"4-#{where}")
                    or _configured_climate_devices.get(f"#{clean_where}")
                    or _configured_climate_devices.get(f"#{where}")
                    or _configured_climate_devices.get(f"zone_{clean_where}")
                    or _configured_climate_devices.get(f"zone_{where}")
                    or {}
                )
                is_central = clean_where in ("0", "01") or where in ("#0", "#0#1")
                _customs = hass.data.get(DOMAIN, {}).get("customizations", {})
                _predicted_id = f"climate.climate_zone_{default_suffix.lower().replace(' ', '_')}"
                _custom_entry = _customs.get(_predicted_id, {})
                _name = (
                    cfg.get(CONF_NAME)
                    or _custom_entry.get("friendly_name")
                    or f"Climate Zone {default_suffix}"
                )
                default_model = "Central Unit (3550)" if where == "#0" else ("Central Unit (4695)" if where == "#0#1" else "Heating Zone")
                _climate = MyHOMEClimate(
                    hass=hass,
                    device_id=unique_id,
                    who=str(getattr(message, "who", "4")),
                    where=where,
                    interface=interface,
                    name=_name,
                    heating=cfg.get(CONF_HEATING_SUPPORT, True),
                    cooling=cfg.get(CONF_COOLING_SUPPORT, True),
                    fan=cfg.get(CONF_FAN_SUPPORT, False),
                    standalone=cfg.get(CONF_STANDALONE, not is_central),
                    central=is_central,
                    manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
                    model=cfg.get(CONF_DEVICE_MODEL, default_model),
                    gateway=gateway,
                )
                known_climates.add(unique_id)
                known_climates.add(where)
                if not interface:
                    known_climates.add(clean_where)
                async_add_entities([_climate])
                _climate.handle_event(message)

        # Dispatch updates
        zone_where = f"#{message.zone}" if message.zone == 0 else str(message.zone)
        async_dispatcher_send(
            hass,
            f"myhome_update_{mac}_4_{zone_where}",
            message,
        )
        if hasattr(message, "where") and message.where:
            async_dispatcher_send(
                hass,
                f"myhome_update_{mac}_4_{message.where}",
                message,
            )
        for z in calling_zones:
            async_dispatcher_send(
                hass,
                f"myhome_update_{mac}_4_{z}",
                message,
            )
            if interface:
                async_dispatcher_send(
                    hass,
                    f"myhome_update_{mac}_4_{z}#4#{interface}",
                    message,
                )

    @callback
    def _handle_climate_message(msg):
        """Filter and forward climate messages."""
        if isinstance(msg, OWNHeatingEvent):
            async_add_climate(msg)

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"myhome_message_{mac}",
            _handle_climate_message,
        )
    )
    return True


async def async_unload_entry(hass, config_entry):
    mac = config_entry.data.get(CONF_MAC)
    if not mac or mac not in hass.data.get(DOMAIN, {}):
        return True
    if PLATFORM not in hass.data[DOMAIN][mac].get(CONF_PLATFORMS, {}):
        return True

    _configured_climate_devices = hass.data[DOMAIN][mac][CONF_PLATFORMS][PLATFORM]

    for _climate_device in list(_configured_climate_devices.keys()):
        del hass.data[DOMAIN][mac][CONF_PLATFORMS][PLATFORM][_climate_device]
    return True


class MyHOMEClimate(MyHOMEEntity, ClimateEntity):
    def __init__(
        self,
        hass,
        name: str,
        device_id: str,
        who: str,
        where: str,
        heating: bool,
        cooling: bool,
        fan: bool,
        standalone: bool,
        central: bool,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
        interface: str = None,
    ):
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=device_id,
            who=who,
            where=where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
        )
        self.hass = hass

        self._interface = interface
        self._full_where = (
            f"{self._where}#4#{self._interface}" if self._interface is not None else self._where
        )

        self._standalone = False if (self._where in ("#0", "#0#1") or central) else standalone
        self._central = True if self._where in ("#0", "#0#1") else central

        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_precision = 0.1
        self._attr_target_temperature_step = 0.5
        self._attr_min_temp = 5
        self._attr_max_temp = 40

        self._attr_supported_features = 0
        self._attr_hvac_modes = [HVACMode.OFF]
        self._heating = heating
        self._cooling = cooling
        if heating or cooling:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
            self._attr_hvac_modes.append(HVACMode.AUTO)
            if heating:
                self._attr_hvac_modes.append(HVACMode.HEAT)
            if cooling:
                self._attr_hvac_modes.append(HVACMode.COOL)

        # Fan mode support (fancoil 3-speed + auto)
        self._fan = fan
        if self._fan:
            self._attr_supported_features |= ClimateEntityFeature.FAN_MODE
            self._attr_fan_modes = ["auto", "low", "medium", "high"]
            self._attr_fan_mode = "auto"

        self._attr_current_temperature = None
        self._attr_current_humidity = None
        self._target_temperature = None
        self._local_offset = 0
        self._local_target_temperature = None

        self._attr_hvac_mode = None
        self._attr_hvac_action = None

    @property
    def extra_state_attributes(self):
        """Return device specific attributes."""
        attrs = {
            "local_offset": self._local_offset,
        }
        if self._fan:
            attrs["fan_mode"] = self._attr_fan_mode
        if self._interface is not None:
            attrs["Int"] = self._interface
        return attrs

    async def async_restore_last_state(self, last_state) -> None:
        """Restore climate state from HA storage."""
        if last_state is not None and last_state.state is not None:
            try:
                restored_mode = HVACMode(last_state.state)
                if restored_mode in self._attr_hvac_modes:
                    self._attr_hvac_mode = restored_mode
                else:
                    self._attr_hvac_mode = HVACMode.OFF
            except (ValueError, TypeError):
                self._attr_hvac_mode = HVACMode.OFF
            target_temp = last_state.attributes.get("temperature")
            if target_temp is not None:
                try:
                    self._target_temperature = float(target_temp)
                except (ValueError, TypeError):
                    pass

    async def async_update(self) -> None:
        """Request status update from gateway."""
        if self._central:
            await self._gateway_handler.send_status_request(OWNHeatingCommand.central_status(self._where))
        else:
            await self._gateway_handler.send_status_request(OWNHeatingCommand.status(self._full_where))

    async def async_added_to_hass(self):
        """Run when entity about to be added to hass."""
        target_hass = self.hass or self._hass
        if target_hass is not None:
            self.async_on_remove(
                async_dispatcher_connect(
                    target_hass,
                    f"myhome_update_{self._gateway_handler.mac}_4_{self._where}",
                    self.handle_event,
                )
            )
            if self._full_where != self._where:
                self.async_on_remove(
                    async_dispatcher_connect(
                        target_hass,
                        f"myhome_update_{self._gateway_handler.mac}_4_{self._full_where}",
                        self.handle_event,
                    )
                )
            if not self._standalone and not self._central:
                self.async_on_remove(
                    async_dispatcher_connect(
                        target_hass,
                        f"myhome_central_mode_{self._gateway_handler.mac}",
                        self._handle_central_mode_update,
                    )
                )
        await super().async_added_to_hass()

    @callback
    def _handle_central_mode_update(self, master_mode: HVACMode) -> None:
        """Update subordinate zone mode when central unit changes seasonal mode."""
        if master_mode == HVACMode.OFF:
            self._attr_hvac_mode = HVACMode.OFF
            self._attr_hvac_action = HVACAction.OFF
        elif master_mode in (HVACMode.HEAT, HVACMode.COOL):
            if self._attr_hvac_mode != HVACMode.OFF:
                self._attr_hvac_mode = master_mode
        elif master_mode == HVACMode.AUTO:
            if self._attr_hvac_mode != HVACMode.OFF and HVACMode.AUTO in self._attr_hvac_modes:
                self._attr_hvac_mode = HVACMode.AUTO
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str):
        """Set new target fan mode."""
        fan_mode_map = {
            "auto": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
        }
        speed_code = fan_mode_map.get(str(fan_mode).lower())
        if speed_code is not None:
            self._attr_fan_mode = fan_mode
            await self._gateway_handler.send(
                OWNHeatingCommand.set_fan_speed(
                    where=self._where,
                    speed=speed_code,
                    standalone=self._standalone,
                )
            )
            if self.hass is not None:
                self.async_write_ha_state()

    @property
    def target_temperature(self) -> float:
        if self._local_target_temperature is not None:
            return self._local_target_temperature
        else:
            return self._target_temperature

    async def async_set_hvac_mode(self, hvac_mode):
        """Set new target hvac mode."""
        if self._central:
            mode_map = {
                HVACMode.OFF: "off",
                HVACMode.HEAT: "heat",
                HVACMode.COOL: "cool",
                HVACMode.AUTO: "auto",
            }
            cmd_mode = mode_map.get(hvac_mode)
            if cmd_mode:
                await self._gateway_handler.send(
                    OWNHeatingCommand.set_central_mode(
                        where=self._where,
                        mode=cmd_mode,
                    )
                )
                self._attr_hvac_mode = hvac_mode
                if self.hass is not None:
                    self.async_write_ha_state()
                    async_dispatcher_send(
                        self.hass,
                        f"myhome_central_mode_{self._gateway_handler.mac}",
                        hvac_mode,
                    )
            return

        if hvac_mode == HVACMode.OFF:
            await self._gateway_handler.send(
                OWNHeatingCommand.set_mode(
                    where=self._where,
                    mode=CLIMATE_MODE_OFF,
                    standalone=self._standalone,
                )
            )
        elif hvac_mode == HVACMode.AUTO:
            await self._gateway_handler.send(
                OWNHeatingCommand.set_mode(
                    where=self._where,
                    mode=CLIMATE_MODE_AUTO,
                    standalone=self._standalone,
                )
            )
        elif hvac_mode == HVACMode.HEAT:
            if self._target_temperature is not None:
                await self._gateway_handler.send(
                    OWNHeatingCommand.set_temperature(
                        where=self._where,
                        temperature=self._target_temperature,
                        mode=CLIMATE_MODE_HEAT,
                        standalone=self._standalone,
                    )
                )
        elif hvac_mode == HVACMode.COOL:
            if self._target_temperature is not None:
                await self._gateway_handler.send(
                    OWNHeatingCommand.set_temperature(
                        where=self._where,
                        temperature=self._target_temperature,
                        mode=CLIMATE_MODE_COOL,
                        standalone=self._standalone,
                    )
                )

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        target_temperature = (
            kwargs.get("temperature", self._local_target_temperature) - self._local_offset
        )
        if self._central:
            mode = "heat" if self._attr_hvac_mode != HVACMode.COOL else "cool"
            await self._gateway_handler.send(
                OWNHeatingCommand.set_central_temperature(
                    where=self._where,
                    temperature=target_temperature,
                    mode=mode,
                )
            )
            self._target_temperature = target_temperature
            if self.hass is not None:
                self.async_write_ha_state()
            return
        if self._attr_hvac_mode == HVACMode.HEAT:
            await self._gateway_handler.send(
                OWNHeatingCommand.set_temperature(
                    where=self._where,
                    temperature=target_temperature,
                    mode=CLIMATE_MODE_HEAT,
                    standalone=self._standalone,
                )
            )
        elif self._attr_hvac_mode == HVACMode.COOL:
            await self._gateway_handler.send(
                OWNHeatingCommand.set_temperature(
                    where=self._where,
                    temperature=target_temperature,
                    mode=CLIMATE_MODE_COOL,
                    standalone=self._standalone,
                )
            )
        else:
            await self._gateway_handler.send(
                OWNHeatingCommand.set_temperature(
                    where=self._where,
                    temperature=target_temperature,
                    mode=CLIMATE_MODE_AUTO,
                    standalone=self._standalone,
                )
            )

    @callback
    def handle_event(self, message: OWNHeatingEvent):
        """Handle an event message."""
        if message.message_type == MESSAGE_TYPE_MAIN_TEMPERATURE:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._attr_current_temperature = message.main_temperature
        elif message.message_type == MESSAGE_TYPE_MAIN_HUMIDITY:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._attr_current_humidity = message.main_humidity
        elif message.message_type == MESSAGE_TYPE_TARGET_TEMPERATURE:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._target_temperature = message.set_temperature
            self._local_target_temperature = self._target_temperature + self._local_offset
        elif message.message_type == MESSAGE_TYPE_LOCAL_OFFSET:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._local_offset = message.local_offset
            if self._target_temperature is not None:
                self._local_target_temperature = self._target_temperature + self._local_offset
        elif message.message_type == MESSAGE_TYPE_LOCAL_TARGET_TEMPERATURE:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._local_target_temperature = message.local_set_temperature
            self._target_temperature = self._local_target_temperature - self._local_offset
        elif message.message_type == MESSAGE_TYPE_MODE:
            if message.mode == CLIMATE_MODE_AUTO and HVACMode.AUTO in self._attr_hvac_modes:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._attr_hvac_mode = HVACMode.AUTO
                if self._attr_hvac_action == HVACAction.OFF:
                    self._attr_hvac_action = HVACAction.IDLE
            elif message.mode == CLIMATE_MODE_COOL and HVACMode.COOL in self._attr_hvac_modes:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._attr_hvac_mode = HVACMode.COOL
                if self._attr_hvac_action == HVACAction.OFF:
                    self._attr_hvac_action = HVACAction.IDLE
            elif message.mode == CLIMATE_MODE_HEAT and HVACMode.HEAT in self._attr_hvac_modes:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._attr_hvac_mode = HVACMode.HEAT
                if self._attr_hvac_action == HVACAction.OFF:
                    self._attr_hvac_action = HVACAction.IDLE
            elif message.mode == CLIMATE_MODE_OFF:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._attr_hvac_mode = HVACMode.OFF
                self._attr_hvac_action = HVACAction.OFF
            if self._central and self.hass is not None and self._attr_hvac_mode is not None:
                async_dispatcher_send(
                    self.hass,
                    f"myhome_central_mode_{self._gateway_handler.mac}",
                    self._attr_hvac_mode,
                )
        elif message.message_type == MESSAGE_TYPE_MODE_TARGET:
            if message.mode == CLIMATE_MODE_AUTO and HVACMode.AUTO in self._attr_hvac_modes:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._attr_hvac_mode = HVACMode.AUTO
                if self._attr_hvac_action == HVACAction.OFF:
                    self._attr_hvac_action = HVACAction.IDLE
            elif message.mode == CLIMATE_MODE_COOL and HVACMode.COOL in self._attr_hvac_modes:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._attr_hvac_mode = HVACMode.COOL
                if self._attr_hvac_action == HVACAction.OFF:
                    self._attr_hvac_action = HVACAction.IDLE
            elif message.mode == CLIMATE_MODE_HEAT and HVACMode.HEAT in self._attr_hvac_modes:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._attr_hvac_mode = HVACMode.HEAT
                if self._attr_hvac_action == HVACAction.OFF:
                    self._attr_hvac_action = HVACAction.IDLE
            elif message.mode == CLIMATE_MODE_OFF:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._attr_hvac_mode = HVACMode.OFF
                self._attr_hvac_action = HVACAction.OFF
            self._target_temperature = message.set_temperature
            self._local_target_temperature = self._target_temperature + self._local_offset
            if self._central and self.hass is not None and self._attr_hvac_mode is not None:
                async_dispatcher_send(
                    self.hass,
                    f"myhome_central_mode_{self._gateway_handler.mac}",
                    self._attr_hvac_mode,
                )
        elif message.message_type == MESSAGE_TYPE_ACTION:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            if message.is_active():
                if self._heating and self._cooling:
                    if message.is_heating():
                        self._attr_hvac_action = HVACAction.HEATING
                    elif message.is_cooling():
                        self._attr_hvac_action = HVACAction.COOLING
                elif self._heating:
                    self._attr_hvac_action = HVACAction.HEATING
                elif self._cooling:
                    self._attr_hvac_action = HVACAction.COOLING
            elif self._attr_hvac_mode == HVACMode.OFF:
                self._attr_hvac_action = HVACAction.OFF
            else:
                self._attr_hvac_action = HVACAction.IDLE
        elif message.message_type == MESSAGE_TYPE_FAN_SPEED or (
            hasattr(message, "fan_speed") and message.fan_speed is not None
        ):
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            speed = getattr(message, "fan_speed", None)
            if speed == 0:
                self._attr_fan_mode = "auto"
            elif speed == 1:
                self._attr_fan_mode = "low"
            elif speed == 2:
                self._attr_fan_mode = "medium"
            elif speed == 3:
                self._attr_fan_mode = "high"

        if self.hass is not None or hasattr(self.async_schedule_update_ha_state, "assert_called"):
            try:
                self.async_schedule_update_ha_state()
            except RuntimeError:
                pass
