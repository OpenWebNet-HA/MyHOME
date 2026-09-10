"""Support for MyHome binary sensors (dry contacts and motion sensors)."""

from datetime import datetime, timedelta, timezone

from homeassistant.components.binary_sensor import (
    DOMAIN as PLATFORM,
)
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import (
    CONF_ENTITIES,
    CONF_MAC,
    CONF_NAME,
    STATE_ON,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.restore_state import RestoreEntity
from OWNd.message import (
    MESSAGE_TYPE_MOTION,
    MESSAGE_TYPE_MOTION_TIMEOUT,
    MESSAGE_TYPE_PIR_SENSITIVITY,
    OWNAuxEvent,
    OWNDryContactCommand,
    OWNDryContactEvent,
    OWNLightingCommand,
    OWNLightingEvent,
)

from .const import (
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_ENTITY,
    CONF_ENTITY_NAME,
    CONF_INVERTED,
    CONF_MANUFACTURER,
    CONF_PLATFORMS,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    LOGGER,
    normalize_where,
)
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity

SCAN_INTERVAL = timedelta(seconds=30)
PIR_SENSITIVITY = ["low", "medium", "high", "very high"]


async def async_setup_entry(hass, config_entry, async_add_entities):
    gateway = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY]
    _configured_binary_sensors = hass.data[DOMAIN][config_entry.data[CONF_MAC]].get(CONF_PLATFORMS, {}).get(PLATFORM, {})

    _binary_sensors = []
    known_sensors = set()
    known_device_ids = set()

    # Restore previously discovered entities from Entity Registry so they persist across restarts
    try:
        entity_registry = er.async_get(hass)
        existing_entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    except Exception:
        entity_registry = None
        existing_entries = []

    for entry in existing_entries:
        if entry.domain != PLATFORM:
            continue
        unique_id = entry.unique_id
        after_mac = unique_id.replace(f"{gateway.mac}-", "", 1).replace(f"{config_entry.data[CONF_MAC]}-", "", 1)
        if "-motion" in unique_id or entry.original_device_class == BinarySensorDeviceClass.MOTION:
            where = after_mac.replace("-motion", "")
            parts_who = where.split("-", 1)
            where = parts_who[-1] if len(parts_who) > 1 else where
            clean_where = where.split("-")[-1]
            norm_where = normalize_where(where)
            clean_norm = normalize_where(clean_where)

            if any(f"1_{x}" in known_sensors for x in (where, norm_where, clean_where, clean_norm)):
                if entity_registry:
                    try:
                        entity_registry.async_remove(entry.entity_id)
                        LOGGER.info("Removed duplicate motion sensor registry entry: %s", entry.entity_id)
                    except Exception:
                        pass
                continue

            cfg = (
                _configured_binary_sensors.get(f"1-{norm_where}")
                or _configured_binary_sensors.get(f"1-{where}")
                or _configured_binary_sensors.get(norm_where)
                or _configured_binary_sensors.get(where)
                or _configured_binary_sensors.get(clean_norm)
                or _configured_binary_sensors.get(clean_where)
                or {}
            )
            actual_where = str(cfg.get(CONF_WHERE, norm_where or where))
            norm_actual = normalize_where(actual_where)
            dev_id = norm_where or clean_norm or clean_where
            bs = MyHOMEMotionSensor(
                hass=hass,
                device_id=dev_id,
                who="1",
                where=norm_actual or actual_where,
                name=cfg.get(CONF_NAME, f"Motion Sensor {clean_norm or clean_where}"),
                entity_name=cfg.get(CONF_ENTITY_NAME),
                inverted=cfg.get(CONF_INVERTED, False),
                device_class=BinarySensorDeviceClass.MOTION,
                manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
                model=cfg.get(CONF_DEVICE_MODEL, "Motion Sensor"),
                gateway=gateway,
            )
            bs._attr_unique_id = entry.unique_id
            for x in (where, norm_where, clean_where, clean_norm, actual_where, norm_actual, dev_id):
                known_sensors.add(f"1_{x}")
            known_device_ids.add(dev_id)
            _binary_sensors.append(bs)
        elif (
            after_mac.startswith("25-")
            or entry.original_device_class in (
                BinarySensorDeviceClass.OPENING,
                BinarySensorDeviceClass.DOOR,
                BinarySensorDeviceClass.GARAGE_DOOR,
                BinarySensorDeviceClass.WINDOW,
            )
            or any(s in unique_id for s in ("-opening", "-door", "-garage_door", "-window"))
        ):
            raw_id = after_mac.replace("25-", "", 1) if after_mac.startswith("25-") else after_mac
            candidate_id = raw_id
            for s in ("-opening", "-door", "-garage_door", "-window"):
                if candidate_id.endswith(s):
                    candidate_id = candidate_id[:-len(s)]
                    break
            clean_candidate = candidate_id.split("-")[-1]

            cfg = (
                _configured_binary_sensors.get(f"25-{candidate_id}")
                or _configured_binary_sensors.get(candidate_id)
                or _configured_binary_sensors.get(clean_candidate)
                or _configured_binary_sensors.get(normalize_where(candidate_id))
                or _configured_binary_sensors.get(normalize_where(clean_candidate))
                or {}
            )
            actual_where = str(cfg.get(CONF_WHERE, candidate_id))
            norm_where = normalize_where(actual_where)
            clean_where = actual_where.split("-")[-1]
            clean_norm = normalize_where(clean_where)

            is_dup = (
                any(f"25_{x}" in known_sensors for x in (actual_where, norm_where, clean_where, clean_norm))
                or candidate_id in known_device_ids
            )
            if is_dup:
                if entity_registry:
                    try:
                        entity_registry.async_remove(entry.entity_id)
                        LOGGER.info("Removed duplicate dry contact registry entry: %s", entry.entity_id)
                    except Exception:
                        pass
                continue

            device_id = candidate_id if candidate_id in _configured_binary_sensors else (norm_where or clean_norm or clean_where)
            bs = MyHOMEDryContact(
                hass=hass,
                device_id=device_id,
                who="25",
                where=norm_where or actual_where,
                name=cfg.get(CONF_NAME, f"Dry Contact {clean_norm or clean_where}"),
                entity_name=cfg.get(CONF_ENTITY_NAME),
                inverted=cfg.get(CONF_INVERTED, False),
                device_class=cfg.get(CONF_DEVICE_CLASS, entry.original_device_class or BinarySensorDeviceClass.OPENING),
                manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
                model=cfg.get(CONF_DEVICE_MODEL, "Dry Contact Interface"),
                gateway=gateway,
            )
            bs._attr_unique_id = entry.unique_id
            for x in (actual_where, norm_where, clean_where, clean_norm, candidate_id, clean_candidate, device_id):
                known_sensors.add(f"25_{x}")
            known_device_ids.add(candidate_id)
            known_device_ids.add(device_id)
            _binary_sensors.append(bs)
        elif after_mac.startswith("9-"):
            where = after_mac.replace("9-", "", 1)
            clean_where = where.split("-")[-1]
            if any(f"9_{x}" in known_sensors for x in (where, clean_where)):
                if entity_registry:
                    try:
                        entity_registry.async_remove(entry.entity_id)
                        LOGGER.info("Removed duplicate auxiliary registry entry: %s", entry.entity_id)
                    except Exception:
                        pass
                continue

            cfg = _configured_binary_sensors.get(f"9-{where}") or _configured_binary_sensors.get(where) or _configured_binary_sensors.get(clean_where) or {}
            bs = MyHOMEAuxiliary(
                hass=hass,
                device_id=where,
                who="9",
                where=where,
                name=cfg.get(CONF_NAME, f"Auxiliary Channel {clean_where}"),
                entity_name=cfg.get(CONF_ENTITY_NAME),
                inverted=cfg.get(CONF_INVERTED, False),
                device_class=cfg.get(CONF_DEVICE_CLASS) or entry.original_device_class,
                manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
                model=cfg.get(CONF_DEVICE_MODEL, "Auxiliary Channel"),
                gateway=gateway,
            )
            bs._attr_unique_id = entry.unique_id
            known_sensors.add(f"9_{where}")
            known_sensors.add(f"9_{clean_where}")
            known_device_ids.add(where)
            _binary_sensors.append(bs)

    # Also instantiate any configured binary sensors not yet in registry
    for _binary_sensor_key, dev_cfg in _configured_binary_sensors.items():
        _who = int(dev_cfg[CONF_WHO])
        _device_class = dev_cfg.get(CONF_DEVICE_CLASS) or dev_cfg.get("device_class")
        where = str(dev_cfg[CONF_WHERE])
        norm_where = normalize_where(where)
        clean_where = where.split("-")[-1]
        clean_norm = normalize_where(clean_where)

        if (
            any(f"{_who}_{x}" in known_sensors for x in (where, norm_where, clean_where, clean_norm))
            or _binary_sensor_key in known_device_ids
        ):
            continue

        if _who == 25:
            bs = MyHOMEDryContact(
                hass=hass,
                device_id=_binary_sensor_key,
                who=dev_cfg[CONF_WHO],
                where=norm_where or where,
                name=dev_cfg[CONF_NAME],
                entity_name=dev_cfg.get(CONF_ENTITY_NAME),
                inverted=dev_cfg.get(CONF_INVERTED, False),
                device_class=_device_class or BinarySensorDeviceClass.OPENING,
                manufacturer=dev_cfg.get(CONF_MANUFACTURER, "BTicino"),
                model=dev_cfg.get(CONF_DEVICE_MODEL, "Dry Contact"),
                gateway=gateway,
            )
            for x in (where, norm_where, clean_where, clean_norm, _binary_sensor_key):
                known_sensors.add(f"25_{x}")
            known_device_ids.add(_binary_sensor_key)
            _binary_sensors.append(bs)
        elif _who == 9:
            bs = MyHOMEAuxiliary(
                hass=hass,
                device_id=_binary_sensor_key,
                who=dev_cfg[CONF_WHO],
                where=where,
                name=dev_cfg[CONF_NAME],
                entity_name=dev_cfg.get(CONF_ENTITY_NAME),
                inverted=dev_cfg.get(CONF_INVERTED, False),
                device_class=_device_class,
                manufacturer=dev_cfg.get(CONF_MANUFACTURER, "BTicino"),
                model=dev_cfg.get(CONF_DEVICE_MODEL, "Auxiliary Channel"),
                gateway=gateway,
            )
            for x in (where, clean_where, _binary_sensor_key):
                known_sensors.add(f"9_{x}")
            known_device_ids.add(_binary_sensor_key)
            _binary_sensors.append(bs)
        elif _who == 1 and _device_class == BinarySensorDeviceClass.MOTION:
            bs = MyHOMEMotionSensor(
                hass=hass,
                device_id=_binary_sensor_key,
                who=dev_cfg[CONF_WHO],
                where=norm_where or where,
                name=dev_cfg[CONF_NAME],
                entity_name=dev_cfg.get(CONF_ENTITY_NAME),
                inverted=dev_cfg.get(CONF_INVERTED, False),
                device_class=_device_class,
                manufacturer=dev_cfg.get(CONF_MANUFACTURER, "BTicino"),
                model=dev_cfg.get(CONF_DEVICE_MODEL, "Motion Sensor"),
                gateway=gateway,
            )
            for x in (where, norm_where, clean_where, clean_norm, _binary_sensor_key):
                known_sensors.add(f"1_{x}")
            known_device_ids.add(_binary_sensor_key)
            _binary_sensors.append(bs)

    if _binary_sensors:
        async_add_entities(_binary_sensors)

    @callback
    def _handle_binary_sensor_message(msg):
        """Forward incoming bus messages to binary sensor entities."""
        if isinstance(msg, OWNDryContactEvent):
            where = str(msg.where)
            norm_where = normalize_where(where)
            clean_where = where.split("-")[-1]
            clean_norm = normalize_where(clean_where)

            if not any(f"25_{x}" in known_sensors for x in (where, norm_where, clean_where, clean_norm)):
                primary_where = norm_where or clean_norm or clean_where
                name = f"Dry Contact {clean_norm or clean_where}"
                bs = MyHOMEDryContact(
                    hass=hass,
                    device_id=primary_where,
                    who="25",
                    where=primary_where,
                    name=name,
                    entity_name=None,
                    inverted=False,
                    device_class=BinarySensorDeviceClass.OPENING,
                    manufacturer="BTicino",
                    model="Dry Contact Interface",
                    gateway=gateway,
                )
                for x in (where, norm_where, clean_where, clean_norm, primary_where):
                    known_sensors.add(f"25_{x}")
                known_device_ids.add(primary_where)
                async_add_entities([bs])
                bs.handle_event(msg)

            for target in set((where, norm_where, clean_where, clean_norm)):
                async_dispatcher_send(
                    hass,
                    f"myhome_update_{config_entry.data[CONF_MAC]}_25_{target}",
                    msg,
                )
        elif isinstance(msg, OWNAuxEvent):
            where = str(msg.channel)
            clean_where = where.split("-")[-1]
            for target in set((where, clean_where)):
                async_dispatcher_send(
                    hass,
                    f"myhome_update_{config_entry.data[CONF_MAC]}_9_{target}",
                    msg,
                )
        elif isinstance(msg, OWNLightingEvent):
            is_motion = (
                getattr(msg, "is_sensor", False) is True
                or getattr(msg, "motion", False) is True
                or getattr(msg, "message_type", None) in (
                    MESSAGE_TYPE_MOTION,
                    MESSAGE_TYPE_MOTION_TIMEOUT,
                    MESSAGE_TYPE_PIR_SENSITIVITY,
                )
                or getattr(msg, "dimension", None) in (5, 7)
                or getattr(msg, "_state", None) == 34
            )
            if is_motion and hasattr(msg, "where") and msg.where is not None:
                where = str(msg.where)
                norm_where = normalize_where(where)
                clean_where = where.split("-")[-1]
                clean_norm = normalize_where(clean_where)

                if not any(f"1_{x}" in known_sensors for x in (where, norm_where, clean_where, clean_norm)):
                    primary_where = norm_where or clean_norm or clean_where
                    name = f"Motion Sensor {clean_norm or clean_where}"
                    bs = MyHOMEMotionSensor(
                        hass=hass,
                        name=name,
                        entity_name=None,
                        device_id=primary_where,
                        who="1",
                        where=primary_where,
                        inverted=False,
                        device_class=BinarySensorDeviceClass.MOTION,
                        manufacturer="BTicino",
                        model="Motion Sensor",
                        gateway=gateway,
                    )
                    for x in (where, norm_where, clean_where, clean_norm, primary_where):
                        known_sensors.add(f"1_{x}")
                    known_device_ids.add(primary_where)
                    async_add_entities([bs])
                    bs.handle_event(msg)

                for target in set((where, norm_where, clean_where, clean_norm)):
                    async_dispatcher_send(
                        hass,
                        f"myhome_update_{config_entry.data[CONF_MAC]}_1_{target}",
                        msg,
                    )

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"myhome_message_{config_entry.data[CONF_MAC]}",
            _handle_binary_sensor_message,
        )
    )
    return True


async def async_unload_entry(hass, config_entry):
    if PLATFORM not in hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS]:
        return True

    _configured_binary_sensors = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS][PLATFORM]

    for _binary_sensor in list(_configured_binary_sensors.keys()):
        del hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_PLATFORMS][PLATFORM][_binary_sensor]


class MyHOMEDryContact(MyHOMEEntity, BinarySensorEntity):
    def __init__(
        self,
        hass,
        name: str,
        entity_name: str,
        device_id: str,
        who: str,
        where: str,
        inverted: bool,
        device_class: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ):
        norm_where = normalize_where(where) or where
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=device_id,
            who=who,
            where=norm_where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
        )

        self._inverted = inverted

        self._attr_device_class = device_class
        self._attr_name = entity_name if entity_name else self._attr_device_class.replace("_", " ").capitalize()

        self._attr_unique_id = f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"

        self._attr_is_on = False
        sensor_attr = f"({self._where[0]}){self._where[1:]}" if self._where else ""
        self._attr_extra_state_attributes = {"Sensor": sensor_attr}

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        try:
            device_dict = self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id]
            if CONF_ENTITIES not in device_dict or not isinstance(device_dict[CONF_ENTITIES], dict):
                device_dict[CONF_ENTITIES] = {}
            device_dict[CONF_ENTITIES][self._attr_device_class] = self
        except (KeyError, TypeError):
            pass
        target_hass = self.hass or self._hass
        if target_hass is not None:
            unsub = async_dispatcher_connect(
                target_hass,
                f"myhome_update_{self._gateway_handler.mac}_25_{self._where}",
                self.handle_event,
            )
            self.async_on_remove(unsub)
            norm_where = normalize_where(self._where)
            if norm_where != self._where:
                unsub2 = async_dispatcher_connect(
                    target_hass,
                    f"myhome_update_{self._gateway_handler.mac}_25_{norm_where}",
                    self.handle_event,
                )
                self.async_on_remove(unsub2)
        await self.async_update()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        try:
            device_dict = self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id]
            if CONF_ENTITIES in device_dict and isinstance(device_dict[CONF_ENTITIES], dict) and self._attr_device_class in device_dict[CONF_ENTITIES]:
                del device_dict[CONF_ENTITIES][self._attr_device_class]
        except (KeyError, TypeError):
            pass

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        await self._gateway_handler.send_status_request(OWNDryContactCommand.status(self._where))

    @callback
    def handle_event(self, message: OWNDryContactEvent):
        """Handle an event message."""
        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        self._attr_is_on = message.is_on != self._inverted
        if self.hass is not None or hasattr(self.async_schedule_update_ha_state, "assert_called"):
            self.async_schedule_update_ha_state()


class MyHOMEAuxiliary(MyHOMEEntity, BinarySensorEntity):
    def __init__(
        self,
        hass,
        name: str,
        entity_name: str,
        device_id: str,
        who: str,
        where: str,
        inverted: bool,
        device_class: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
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

        self._inverted = inverted

        self._attr_device_class = device_class
        if entity_name:
            self._attr_name = entity_name
        elif self._attr_device_class:
            self._attr_name = self._attr_device_class.replace("_", " ").capitalize()
        else:
            self._attr_name = name

        if self._attr_device_class:
            self._attr_unique_id = f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"
        else:
            self._attr_unique_id = f"{gateway.mac}-{self._device_id}"

        self._attr_is_on = False
        self._attr_extra_state_attributes = {"Auxiliary channel": self._where}

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        try:
            device_dict = self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id]
            if CONF_ENTITIES not in device_dict or not isinstance(device_dict[CONF_ENTITIES], dict):
                device_dict[CONF_ENTITIES] = {}
            device_dict[CONF_ENTITIES][self._attr_device_class] = self
        except (KeyError, TypeError):
            pass
        target_hass = self.hass or self._hass
        if target_hass is not None:
            unsub = async_dispatcher_connect(
                target_hass,
                f"myhome_update_{self._gateway_handler.mac}_9_{self._where}",
                self.handle_event,
            )
            self.async_on_remove(unsub)
        await self.async_update()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        try:
            device_dict = self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id]
            if CONF_ENTITIES in device_dict and isinstance(device_dict[CONF_ENTITIES], dict) and self._attr_device_class in device_dict[CONF_ENTITIES]:
                del device_dict[CONF_ENTITIES][self._attr_device_class]
        except (KeyError, TypeError):
            pass

    async def async_update(self):
        """AUX sensors are read only and cannot be queried, no async_update implementation."""

    @callback
    def handle_event(self, message: OWNDryContactEvent):
        """Handle an event message."""
        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        self._attr_is_on = message.is_on != self._inverted
        if self.hass is not None or hasattr(self.async_schedule_update_ha_state, "assert_called"):
            self.async_schedule_update_ha_state()


class MyHOMEMotionSensor(MyHOMEEntity, BinarySensorEntity, RestoreEntity):
    def __init__(
        self,
        hass,
        name: str,
        entity_name: str,
        device_id: str,
        who: str,
        where: str,
        inverted: bool,
        device_class: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ):
        norm_where = normalize_where(where) or where
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=device_id,
            who=who,
            where=norm_where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
        )

        self._inverted = inverted
        self._attr_force_update = False
        self._last_updated = None
        self._timeout = timedelta(seconds=315)

        self._attr_device_class = device_class
        self._attr_name = entity_name if entity_name else self._attr_device_class.replace("_", " ").capitalize()

        self._attr_unique_id = f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"
        self._attr_should_poll = True
        self._attr_is_on = False
        where_str = str(self._where)
        half = len(where_str) // 2
        a_val = where_str[:half] if half > 0 else "0"
        pl_val = where_str[half:] if half > 0 else where_str
        self._attr_extra_state_attributes = {
            "A": a_val,
            "PL": pl_val,
            "Timeout": self._timeout.total_seconds(),
            "Sensitivity": PIR_SENSITIVITY[1],
        }

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        try:
            device_dict = self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id]
            if CONF_ENTITIES not in device_dict or not isinstance(device_dict[CONF_ENTITIES], dict):
                device_dict[CONF_ENTITIES] = {}
            device_dict[CONF_ENTITIES][self._attr_device_class] = self
        except (KeyError, TypeError):
            pass
        target_hass = self.hass or self._hass
        if target_hass is not None:
            unsub = async_dispatcher_connect(
                target_hass,
                f"myhome_update_{self._gateway_handler.mac}_1_{self._where}",
                self.handle_event,
            )
            self.async_on_remove(unsub)
            norm_where = normalize_where(self._where)
            if norm_where != self._where:
                unsub2 = async_dispatcher_connect(
                    target_hass,
                    f"myhome_update_{self._gateway_handler.mac}_1_{norm_where}",
                    self.handle_event,
                )
                self.async_on_remove(unsub2)
        await self._gateway_handler.send_status_request(OWNLightingCommand.get_pir_sensitivity(self._where))
        await self._gateway_handler.send_status_request(OWNLightingCommand.get_motion_timeout(self._where))
        try:
            state = await self.async_get_last_state()
            if state:
                self._attr_is_on = state.state == STATE_ON
                self._last_updated = state.last_updated
        except Exception:
            pass
        await self.async_update()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        try:
            device_dict = self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id]
            if CONF_ENTITIES in device_dict and isinstance(device_dict[CONF_ENTITIES], dict) and self._attr_device_class in device_dict[CONF_ENTITIES]:
                del device_dict[CONF_ENTITIES][self._attr_device_class]
        except (KeyError, TypeError):
            pass

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        if self._attr_is_on and self._last_updated and self._last_updated + self._timeout < datetime.now(timezone.utc):
            self._attr_is_on = False
            self._last_updated = datetime.now(timezone.utc)
            self.async_schedule_update_ha_state()

    @callback
    def handle_event(self, message: OWNLightingEvent):
        """Handle an event message."""
        if message.message_type not in [
            MESSAGE_TYPE_MOTION,
            MESSAGE_TYPE_MOTION_TIMEOUT,
            MESSAGE_TYPE_PIR_SENSITIVITY,
        ]:
            return True

        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        if message.message_type == MESSAGE_TYPE_MOTION and message.motion:
            self._attr_is_on = message.motion != self._inverted
        elif message.message_type == MESSAGE_TYPE_MOTION_TIMEOUT:
            self._timeout = message.motion_timeout + timedelta(seconds=15)
            self._attr_extra_state_attributes["Timeout"] = self._timeout.total_seconds()
        elif message.message_type == MESSAGE_TYPE_PIR_SENSITIVITY:
            self._attr_extra_state_attributes["Sensitivity"] = PIR_SENSITIVITY[message.pir_sensitivity]
        self._last_updated = datetime.now(timezone.utc)
        self._attr_force_update = True
        try:
            self.async_write_ha_state()
        except Exception:
            try:
                self.async_schedule_update_ha_state()
            except Exception:
                pass
        self._attr_force_update = False
