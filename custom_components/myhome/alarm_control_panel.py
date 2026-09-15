"""Support for MyHome burglar alarm systems (WHO=5)."""
from homeassistant.components.alarm_control_panel import (
    DOMAIN as PLATFORM,
)
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
)

try:
    from homeassistant.components.alarm_control_panel import AlarmControlPanelState

    STATE_DISARMED = AlarmControlPanelState.DISARMED
    STATE_ARMED_HOME = AlarmControlPanelState.ARMED_HOME
    STATE_ARMED_AWAY = AlarmControlPanelState.ARMED_AWAY
    STATE_TRIGGERED = AlarmControlPanelState.TRIGGERED
except ImportError:
    try:
        from homeassistant.const import (
            STATE_ALARM_ARMED_AWAY as STATE_ARMED_AWAY,
        )
        from homeassistant.const import (
            STATE_ALARM_ARMED_HOME as STATE_ARMED_HOME,
        )
        from homeassistant.const import (
            STATE_ALARM_DISARMED as STATE_DISARMED,
        )
        from homeassistant.const import (
            STATE_ALARM_TRIGGERED as STATE_TRIGGERED,
        )
    except ImportError:
        STATE_DISARMED = "disarmed"
        STATE_ARMED_HOME = "armed_home"
        STATE_ARMED_AWAY = "armed_away"
        STATE_TRIGGERED = "triggered"

from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from OWNd.message import (
    OWNAlarmCommand,
    OWNAlarmEvent,
)

from .const import (
    CONF_DEVICE_MODEL,
    CONF_ENTITY,
    CONF_ENTITY_NAME,
    CONF_MANUFACTURER,
    CONF_PLATFORMS,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    LOGGER,
)
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the MyHOME alarm_control_panel platform dynamically and from config."""
    known_alarms = set()

    entity_registry = er.async_get(hass)
    existing_entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    restored_alarms = []

    gateway = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY]
    _configured_alarms = hass.data[DOMAIN][config_entry.data[CONF_MAC]].get(CONF_PLATFORMS, {}).get(PLATFORM, {})

    for entry in existing_entries:
        if entry.domain == PLATFORM:
            unique_id = entry.unique_id
            after_mac = unique_id.replace(f"{gateway.mac}-", "", 1).replace(f"{config_entry.data[CONF_MAC]}-", "", 1)
            parts_who = after_mac.split("-", 1)
            device_id = parts_who[-1] if len(parts_who) > 1 else after_mac
            where = device_id
            clean_where = where.split('-')[-1]
            cfg = _configured_alarms.get(device_id) or _configured_alarms.get(where) or _configured_alarms.get(clean_where) or {}
            _name = cfg.get(CONF_NAME, f"Alarm {clean_where}")
            _alarm = MyHOMEAlarmControlPanel(
                hass=hass,
                name=_name,
                entity_name=cfg.get(CONF_ENTITY_NAME),
                device_id=device_id,
                who="5",
                where=where,
                manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
                model=cfg.get(CONF_DEVICE_MODEL, "Burglar Alarm"),
                gateway=gateway,
            )
            known_alarms.add(device_id)
            restored_alarms.append(_alarm)

    seen_configured_where = set()
    for dev_id, cfg in _configured_alarms.items():
        where = str(cfg.get(CONF_WHERE, dev_id))
        clean_where = where.split("-")[-1]
        if clean_where in seen_configured_where or where in known_alarms or dev_id in known_alarms:
            continue
        seen_configured_where.add(clean_where)
        _name = cfg.get(CONF_NAME, f"Alarm {clean_where}")
        _alarm = MyHOMEAlarmControlPanel(
            hass=hass,
            name=_name,
            entity_name=cfg.get(CONF_ENTITY_NAME),
            device_id=where,
            who=str(cfg.get(CONF_WHO, "5")),
            where=where,
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, "Burglar Alarm"),
            gateway=gateway,
        )
        known_alarms.add(where)
        restored_alarms.append(_alarm)

    if restored_alarms:
        async_add_entities(restored_alarms)

    @callback
    def async_add_alarm(message: OWNAlarmEvent):
        """Add new alarm entity discovered dynamically on bus."""
        where = str(message.where)
        clean_where = where.split('-')[-1]
        unique_id = str(where)

        if unique_id not in known_alarms and clean_where not in known_alarms:
            cfg = _configured_alarms.get(unique_id) or _configured_alarms.get(where) or _configured_alarms.get(clean_where) or {}
            _name = cfg.get(CONF_NAME, f"Alarm {clean_where}")
            _alarm = MyHOMEAlarmControlPanel(
                hass=hass,
                name=_name,
                entity_name=cfg.get(CONF_ENTITY_NAME),
                device_id=unique_id,
                who="5",
                where=where,
                manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
                model=cfg.get(CONF_DEVICE_MODEL, "Burglar Alarm"),
                gateway=hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY],
            )
            known_alarms.add(unique_id)
            known_alarms.add(clean_where)
            async_add_entities([_alarm])
            _alarm.handle_event(message)

        async_dispatcher_send(hass, f"myhome_update_{config_entry.data[CONF_MAC]}_5_{unique_id}", message)

    @callback
    def _handle_alarm_message(msg):
        """Filter and forward alarm messages."""
        if isinstance(msg, OWNAlarmEvent):
            async_add_alarm(msg)

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"myhome_message_{config_entry.data[CONF_MAC]}",
            _handle_alarm_message,
        )
    )


async def async_unload_entry(hass, config_entry):  # pylint: disable=unused-argument
    """Unload alarm platform."""
    return True


class MyHOMEAlarmControlPanel(MyHOMEEntity, AlarmControlPanelEntity):
    """Representation of a MyHOME burglar alarm control panel."""

    def __init__(
        self,
        hass,
        name: str,
        entity_name: str,
        device_id: str,
        who: str,
        where: str,
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

        self._attr_name = entity_name if entity_name else name
        self._gateway_handler = gateway
        self._attr_supported_features = (
            AlarmControlPanelEntityFeature.ARM_AWAY
            | AlarmControlPanelEntityFeature.ARM_HOME
            | AlarmControlPanelEntityFeature.TRIGGER
        )
        self._attr_alarm_state = STATE_DISARMED
        self._attr_extra_state_attributes = {
            "where": self._where,
            "raw_state": "disarmed",
        }

    @property
    def alarm_state(self):
        """Return the state of the device."""
        return self._attr_alarm_state

    @property
    def state(self):
        """Return the state of the device."""
        return self._attr_alarm_state

    async def async_added_to_hass(self):
        """Register dispatcher listener when added to hass."""
        self._register_availability_listener()
        target_hass = self.hass or self._hass
        if target_hass is not None:
            unsub = async_dispatcher_connect(
                target_hass,
                f"myhome_update_{self._gateway_handler.mac}_5_{self._where}",
                self.handle_event,
            )
            self.async_on_remove(unsub)
            # Also listen to global broadcast zone 0
            if self._where != "0":
                unsub_global = async_dispatcher_connect(
                    target_hass,
                    f"myhome_update_{self._gateway_handler.mac}_5_0",
                    self.handle_event,
                )
                self.async_on_remove(unsub_global)
        await self.async_update()

    async def async_update(self):
        """Request status from the gateway."""
        await self._gateway_handler.send_status_request(OWNAlarmCommand.status(self._where))

    async def async_alarm_disarm(self, code=None):  # pylint: disable=unused-argument
        """Send disarm command."""
        await self._gateway_handler.send(OWNAlarmCommand.disarm(self._where))

    async def async_alarm_arm_home(self, code=None):  # pylint: disable=unused-argument
        """Send arm home command."""
        await self._gateway_handler.send(OWNAlarmCommand.arm_home(self._where))

    async def async_alarm_arm_away(self, code=None):  # pylint: disable=unused-argument
        """Send arm away command."""
        await self._gateway_handler.send(OWNAlarmCommand.arm_away(self._where))

    async def async_alarm_trigger(self, code=None):  # pylint: disable=unused-argument
        """Send panic / alarm trigger command."""
        await self._gateway_handler.send(OWNAlarmCommand.trigger(self._where))

    @callback
    def handle_event(self, message: OWNAlarmEvent):
        """Handle incoming alarm event message."""
        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        if message.is_alarm:
            self._attr_alarm_state = STATE_TRIGGERED
        elif message.is_armed_away:
            self._attr_alarm_state = STATE_ARMED_AWAY
        elif message.is_armed_home:
            self._attr_alarm_state = STATE_ARMED_HOME
        elif message.is_disarmed:
            self._attr_alarm_state = STATE_DISARMED

        self._attr_extra_state_attributes["raw_state"] = message.state_name
        self._attr_extra_state_attributes["state_code"] = message.state_code

        if self.hass is not None:
            self.async_schedule_update_ha_state()
