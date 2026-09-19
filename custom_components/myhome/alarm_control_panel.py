from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
)
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from OWNd.message import (
    OWNAlarmCommand,
    OWNAlarmEvent,
)

from .const import (
    CONF_DEVICE_MODEL,
    CONF_ENTITY_NAME,
    CONF_MANUFACTURER,
    LOGGER,
)
from .data import MyHOMERuntimeData
from .discovery import DeviceContext, PlatformDiscovery, default_known_keys
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity

PLATFORM = Platform.ALARM_CONTROL_PANEL
PARALLEL_UPDATES = 0

STATE_DISARMED = AlarmControlPanelState.DISARMED
STATE_ARMED_HOME = AlarmControlPanelState.ARMED_HOME
STATE_ARMED_AWAY = AlarmControlPanelState.ARMED_AWAY
STATE_TRIGGERED = AlarmControlPanelState.TRIGGERED


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    """Set up the burglar-alarm panels of a gateway (WHO=5): registry, myhome.yaml, then the bus."""
    runtime: MyHOMERuntimeData = config_entry.runtime_data

    def build(ctx: DeviceContext) -> MyHOMEAlarmControlPanel:
        cfg = ctx.cfg
        name_val = cfg.get(CONF_NAME)
        name = str(name_val) if name_val else f"Alarm {ctx.address.clean_where}"
        raw_entity_name = cfg.get(CONF_ENTITY_NAME)
        entity_name = str(raw_entity_name) if raw_entity_name is not None else None
        manufacturer = str(cfg.get(CONF_MANUFACTURER, "BTicino"))
        model = str(cfg.get(CONF_DEVICE_MODEL, "Burglar Alarm"))
        return MyHOMEAlarmControlPanel(
            hass=hass,
            name=name,
            entity_name=entity_name,
            device_id=ctx.key,
            who=ctx.who,
            where=ctx.address.where,
            manufacturer=manufacturer,
            model=model,
            gateway=runtime.gateway,
        )

    # WHERE=0 is the central unit, a real device on this subsystem.
    PlatformDiscovery(
        hass, config_entry, async_add_entities,
        platform=PLATFORM, who="5", event_type=OWNAlarmEvent, build=build, general_is_device=True,
        # WHERE=0 is the central unit, and every panel follows its broadcasts
        known_keys=lambda ctx: [*default_known_keys(ctx), "0"],
    ).start()
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:  # pylint: disable=unused-argument
    """Unload alarm platform."""
    return True


class MyHOMEAlarmControlPanel(MyHOMEEntity, AlarmControlPanelEntity):
    """Representation of a MyHOME burglar alarm control panel."""

    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        entity_name: str | None,
        device_id: str,
        who: str,
        where: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ) -> None:
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
            entity_name=entity_name,
        )

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
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Return the state of the device."""
        return self._attr_alarm_state

    async def async_added_to_hass(self) -> None:
        """Register dispatcher listener when added to hass."""
        self._register_availability_listener()
        await self.async_update()

    async def async_update(self) -> None:
        """Request status from the gateway."""
        await self._gateway_handler.send_status_request(OWNAlarmCommand.status(self._where))

    async def async_alarm_disarm(self, code: str | None = None) -> None:  # pylint: disable=unused-argument
        """Send disarm command."""
        await self._gateway_handler.send(OWNAlarmCommand.disarm(self._where))

    async def async_alarm_arm_home(self, code: str | None = None) -> None:  # pylint: disable=unused-argument
        """Send arm home command."""
        await self._gateway_handler.send(OWNAlarmCommand.arm_home(self._where))

    async def async_alarm_arm_away(self, code: str | None = None) -> None:  # pylint: disable=unused-argument
        """Send arm away command."""
        await self._gateway_handler.send(OWNAlarmCommand.arm_away(self._where))

    async def async_alarm_trigger(self, code: str | None = None) -> None:  # pylint: disable=unused-argument
        """Send panic / alarm trigger command."""
        await self._gateway_handler.send(OWNAlarmCommand.trigger(self._where))

    @callback
    def handle_event(self, message: OWNAlarmEvent) -> None:
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
