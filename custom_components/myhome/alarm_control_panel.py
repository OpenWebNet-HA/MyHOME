from typing import Any

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
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from OWNd.message import (
    OWNAlarmCommand,
    OWNAlarmEvent,
)

from .const import (
    CONF_DEVICE_MODEL,
    CONF_ENTITY_NAME,
    CONF_MANUFACTURER,
    DOMAIN,
    LOGGER,
)
from .data import MyHOMERuntimeData
from .discovery import Address, DeviceContext, PlatformDiscovery, default_known_keys
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity

PLATFORM = Platform.ALARM_CONTROL_PANEL
PARALLEL_UPDATES = 0

STATE_DISARMED = AlarmControlPanelState.DISARMED
STATE_ARMED_AWAY = AlarmControlPanelState.ARMED_AWAY
STATE_TRIGGERED = AlarmControlPanelState.TRIGGERED

CENTRAL_UNIT_KEY = "0"


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

    def accept(ctx: DeviceContext) -> bool:
        """Filter alarm device discovery from the bus.

        Individual zones/partitions (WHERE starting with '#', e.g. '#1'..'#8') are
        not independent alarm control panels (as documented in known limitations),
        and status telemetry (*5*11*#...##, 'active zone') emitted by gateways
        such as the MH200 and MH200N when polled with '*#5*0##' must not trigger autonomous
        entity discovery when no central alarm unit is installed.
        """
        if ctx.source != "bus":
            return True
        return not ctx.address.where.startswith("#")

    def reject_registry_entry(entry: er.RegistryEntry, ctx: DeviceContext) -> bool:
        """Purge phantom zone partition entities previously created from status dumps."""
        if ctx.cfg:
            return False
        return ctx.address.where.startswith("#") or (
            ctx.device_id is not None and str(ctx.device_id).startswith("#")
        )

    def route_keys(message: Any, address: Address | None) -> list[str]:
        if address is not None:
            return [address.key]
        # System-scope broadcasts with empty WHERE (*5*WHAT*##) route to the
        # central unit, which is followed by all panels.
        return [CENTRAL_UNIT_KEY]

    # WHERE=0 is the central unit, a real device on this subsystem.
    PlatformDiscovery(
        hass, config_entry, async_add_entities,
        platform=PLATFORM, who="5", event_type=OWNAlarmEvent, build=build, general_is_device=True,
        accept=accept,
        reject_registry_entry=reject_registry_entry,
        route_keys=route_keys,
        # WHERE=0 is the central unit, and every panel follows its broadcasts
        known_keys=lambda ctx: [*default_known_keys(ctx), CENTRAL_UNIT_KEY],
    ).start()
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:  # pylint: disable=unused-argument
    """Unload alarm platform."""
    return True


class MyHOMEAlarmControlPanel(MyHOMEEntity, AlarmControlPanelEntity):
    """A MyHOME burglar alarm central unit, read-only.

    The central unit rejects arm/disarm sent as WHO 5 frames over SCS (#564:
    BTicino support, via the plant owner; no TX capture shows one accepted).
    Plants arm through a WHO 9 AUX frame (e.g. *9*1*7##) that an automation
    programmed on the central unit maps to zones, so the frames are the
    installer's choice. This entity reports the state; a core template alarm
    panel sends the AUX frames with myhome.send_message
    (docs/configuration/alarm.md).
    """

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
        # Read-only: no arm/trigger actions (see the class docstring).
        self._attr_supported_features = AlarmControlPanelEntityFeature(0)
        # The central unit takes no code over the bus.
        self._attr_code_arm_required = False
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
        """Disarm has no feature flag in core, so refuse it here instead of sending a frame the panel rejects."""
        raise ServiceValidationError(
            f"{self._display_name} is read-only: arm and disarm through the AUX frames your central unit is programmed for",
            translation_domain=DOMAIN,
            translation_key="alarm_read_only",
            translation_placeholders={"name": self._display_name},
        )

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
        elif message.is_disarmed:
            self._attr_alarm_state = STATE_DISARMED

        self._attr_extra_state_attributes["raw_state"] = message.state_name
        self._attr_extra_state_attributes["state_code"] = message.state_code

        if self.hass is not None:
            self.async_schedule_update_ha_state()
