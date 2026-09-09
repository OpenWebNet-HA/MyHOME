from homeassistant.core import callback
"""Support for MyHome binary sensors (dry contacts and motion sensors)."""
from datetime import datetime, timedelta, timezone
from homeassistant.components.binary_sensor import (
    DOMAIN as PLATFORM,
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import (
    CONF_NAME,
    CONF_MAC,
    CONF_ENTITIES,
    STATE_ON,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers import entity_registry as er

from .ownd.message import (
    OWNDryContactEvent,
    OWNDryContactCommand,
    OWNAuxEvent,
    OWNLightingCommand,
    MESSAGE_TYPE_MOTION,
    MESSAGE_TYPE_PIR_SENSITIVITY,
    MESSAGE_TYPE_MOTION_TIMEOUT,
    OWNLightingEvent,
)

from .const import (
    CONF_PLATFORMS,
    CONF_ENTITY,
    CONF_ENTITY_NAME,
    CONF_WHO,
    CONF_WHERE,
    CONF_MANUFACTURER,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_CLASS,
    CONF_INVERTED,
    DOMAIN,
    LOGGER,
)
from .myhome_device import MyHOMEEntity
from .gateway import MyHOMEGatewayHandler

SCAN_INTERVAL = timedelta(seconds=30)
PIR_SENSITIVITY = ["low", "medium", "high", "very high"]


async def async_setup_entry(hass, config_entry, async_add_entities):
    gateway = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY]
    _configured_binary_sensors = hass.data[DOMAIN][config_entry.data[CONF_MAC]].get(CONF_PLATFORMS, {}).get(PLATFORM, {})

    _binary_sensors = []
    known_sensors = set()

    # Restore previously discovered entities from Entity Registry so they persist across restarts
    try:
        entity_registry = er.async_get(hass)
        existing_entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    except Exception:
        entity_registry = None
        existing_entries = []

    for entry in existing_entries:
        if entry.domain == PLATFORM:
            unique_id = entry.unique_id
            after_mac = unique_id.replace(f"{gateway.mac}-", "", 1).replace(f"{config_entry.data[CONF_MAC]}-", "", 1)
            if "-motion" in unique_id or entry.original_device_class == BinarySensorDeviceClass.MOTION:
                where = after_mac.replace("-motion", "")
                parts_who = where.split("-", 1)
                where = parts_who[-1] if len(parts_who) > 1 else where
                clean_where = where.split("-")[-1]
                cfg = _configured_binary_sensors.get(f"1-{where}") or _configured_binary_sensors.get(where) or _configured_binary_sensors.get(clean_where) or {}
                bs = MyHOMEMotionSensor(
                    hass=hass,
                    device_id=where,
                    who="1",
                    where=where,
                    name=cfg.get(CONF_NAME, f"Motion Sensor {clean_where}"),
                    entity_name=cfg.get(CONF_ENTITY_NAME),
                    inverted=cfg.get(CONF_INVERTED, False),
                    device_class=BinarySensorDeviceClass.MOTION,
                    manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
                    model=cfg.get(CONF_DEVICE_MODEL, "Motion Sensor"),
                    gateway=gateway,
                )
                known_sensors.add(f"1_{where}")
                known_sensors.add(f"1_{clean_where}")
                _binary_sensors.append(bs)
            elif after_mac.startswith("25-"):
                where = after_mac.replace("25-", "", 1)
                clean_where = where.split("-")[-1]
                cfg = _configured_binary_sensors.get(f"25-{where}") or _configured_binary_sensors.get(where) or _configured_binary_sensors.get(clean_where) or {}
                bs = MyHOMEDryContact(
                    hass=hass,
                    device_id=where,
                    who="25",
                    where=where,
                    name=cfg.get(CONF_NAME, f"Dry Contact {clean_where}"),
                    entity_name=cfg.get(CONF_ENTITY_NAME),
                    inverted=cfg.get(CONF_INVERTED, False),
                    device_class=cfg.get(CONF_DEVICE_CLASS, BinarySensorDeviceClass.OPENING),
                    manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
                    model=cfg.get(CONF_DEVICE_MODEL, "Dry Contact Interface"),
                    gateway=gateway,
                )
                known_sensors.add(f"25_{where}")
                known_sensors.add(f"25_{clean_where}")
                _binary_sensors.append(bs)
            elif after_mac.startswith("9-"):
                where = after_mac.replace("9-", "", 1)
                clean_where = where.split("-")[-1]
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
                known_sensors.add(f"9_{where}")
                known_sensors.add(f"9_{clean_where}")
                _binary_sensors.append(bs)

    # Also instantiate any configured binary sensors not yet in registry
    for _binary_sensor_key, dev_cfg in _configured_binary_sensors.items():
        _who = int(dev_cfg[CONF_WHO])
        _device_class = dev_cfg.get(CONF_DEVICE_CLASS) or dev_cfg.get("device_class")
        where = str(dev_cfg[CONF_WHERE])
        clean_where = where.split("-")[-1]
        if f"{_who}_{where}" in known_sensors or f"{_who}_{clean_where}" in known_sensors:
            continue

        if _who == 25:
            bs = MyHOMEDryContact(
                hass=hass,
                device_id=_binary_sensor_key,
                who=dev_cfg[CONF_WHO],
                where=dev_cfg[CONF_WHERE],
                name=dev_cfg[CONF_NAME],
                entity_name=dev_cfg.get(CONF_ENTITY_NAME),
                inverted=dev_cfg.get(CONF_INVERTED, False),
                device_class=_device_class or BinarySensorDeviceClass.OPENING,
                manufacturer=dev_cfg.get(CONF_MANUFACTURER, "BTicino"),
                model=dev_cfg.get(CONF_DEVICE_MODEL, "Dry Contact"),
                gateway=gateway,
            )
            known_sensors.add(f"25_{dev_cfg[CONF_WHERE]}")
            known_sensors.add(f"25_{clean_where}")
            _binary_sensors.append(bs)
        elif _who == 9:
            bs = MyHOMEAuxiliary(
                hass=hass,
                device_id=_binary_sensor_key,
                who=dev_cfg[CONF_WHO],
                where=dev_cfg[CONF_WHERE],
                name=dev_cfg[CONF_NAME],
                entity_name=dev_cfg.get(CONF_ENTITY_NAME),
                inverted=dev_cfg.get(CONF_INVERTED, False),
                device_class=_device_class,
                manufacturer=dev_cfg.get(CONF_MANUFACTURER, "BTicino"),
                model=dev_cfg.get(CONF_DEVICE_MODEL, "Auxiliary Channel"),
                gateway=gateway,
            )
            known_sensors.add(f"9_{dev_cfg[CONF_WHERE]}")
            known_sensors.add(f"9_{clean_where}")
            _binary_sensors.append(bs)
        elif _who == 1 and _device_class == BinarySensorDeviceClass.MOTION:
            bs = MyHOMEMotionSensor(
                hass=hass,
                device_id=_binary_sensor_key,
                who=dev_cfg[CONF_WHO],
                where=dev_cfg[CONF_WHERE],
                name=dev_cfg[CONF_NAME],
                entity_name=dev_cfg.get(CONF_ENTITY_NAME),
                inverted=dev_cfg.get(CONF_INVERTED, False),
                device_class=_device_class,
                manufacturer=dev_cfg.get(CONF_MANUFACTURER, "BTicino"),
                model=dev_cfg.get(CONF_DEVICE_MODEL, "Motion Sensor"),
                gateway=gateway,
            )
            known_sensors.add(f"1_{dev_cfg[CONF_WHERE]}")
            known_sensors.add(f"1_{clean_where}")
            _binary_sensors.append(bs)

    if _binary_sensors:
        async_add_entities(_binary_sensors)

    @callback
    def _handle_binary_sensor_message(msg):
        """Forward incoming bus messages to binary sensor entities."""
        if isinstance(msg, OWNDryContactEvent):
            where = str(msg.where)
            clean_where = where.split("-")[-1]
            if f"25_{where}" not in known_sensors and f"25_{clean_where}" not in known_sensors:
                name = f"Dry Contact {clean_where}"
                bs = MyHOMEDryContact(
                    hass=hass,
                    device_id=where,
                    who="25",
                    where=where,
                    name=name,
                    entity_name=None,
                    inverted=False,
                    device_class=BinarySensorDeviceClass.OPENING,
                    manufacturer="BTicino",
                    model="Dry Contact Interface",
                    gateway=gateway,
                )
                known_sensors.add(f"25_{where}")
                known_sensors.add(f"25_{clean_where}")
                async_add_entities([bs])
                bs.handle_event(msg)
            async_dispatcher_send(
                hass,
                f"myhome_update_{config_entry.data[CONF_MAC]}_25_{where}",
                msg,
            )
        elif isinstance(msg, OWNAuxEvent):
            where = str(msg.channel)
            async_dispatcher_send(
                hass,
                f"myhome_update_{config_entry.data[CONF_MAC]}_9_{where}",
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
                clean_where = where.split("-")[-1]
                if f"1_{where}" not in known_sensors and f"1_{clean_where}" not in known_sensors:
                    name = f"Motion Sensor {clean_where}"
                    bs = MyHOMEMotionSensor(
                        hass=hass,
                        name=name,
                        entity_name=None,
                        device_id=where,
                        who="1",
                        where=where,
                        inverted=False,
                        device_class=BinarySensorDeviceClass.MOTION,
                        manufacturer="BTicino",
                        model="Motion Sensor",
                        gateway=gateway,
                    )
                    known_sensors.add(f"1_{where}")
                    known_sensors.add(f"1_{clean_where}")
                    async_add_entities([bs])
                    bs.handle_event(msg)
                async_dispatcher_send(
                    hass,
                    f"myhome_update_{config_entry.data[CONF_MAC]}_1_{where}",
                    msg,
                )
                if clean_where != where:
                    async_dispatcher_send(
                        hass,
                        f"myhome_update_{config_entry.data[CONF_MAC]}_1_{clean_where}",
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
        self._attr_name = entity_name if entity_name else self._attr_device_class.replace("_", " ").capitalize()

        self._attr_unique_id = f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"

        self._attr_is_on = False
        self._attr_extra_state_attributes = {"Sensor": f"({self._where[0]}){self._where[1:]}"}

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
        LOGGER.info(
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
        LOGGER.info(
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
        self._attr_force_update = False
        self._last_updated = None
        self._timeout = timedelta(seconds=315)

        self._attr_device_class = device_class
        self._attr_name = entity_name if entity_name else self._attr_device_class.replace("_", " ").capitalize()

        self._attr_unique_id = f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"
        self._attr_should_poll = True
        self._attr_is_on = None
        self._attr_extra_state_attributes = {
            "A": where[: len(where) // 2],
            "PL": where[len(where) // 2 :],
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
        await self._gateway_handler.send_status_request(OWNLightingCommand.get_pir_sensitivity(self._where))
        await self._gateway_handler.send_status_request(OWNLightingCommand.get_motion_timeout(self._where))
        state = await self.async_get_last_state()
        if state:
            self._attr_is_on = state.state == STATE_ON
            self._last_updated = state.last_updated
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

        LOGGER.info(
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
        except RuntimeError:
            pass
        self._attr_force_update = False
