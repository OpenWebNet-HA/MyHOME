import asyncio
import time
from typing import Any, cast

from homeassistant.components.climate import (
    ClimateEntity,
)
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    Platform,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from OWNd.message import (
    CLIMATE_MODE_AUTO,
    CLIMATE_MODE_COOL,
    CLIMATE_MODE_HEAT,
    CLIMATE_MODE_OFF,
    LOCAL_CONTROL_NORMAL,
    LOCAL_CONTROL_OFF,
    LOCAL_CONTROL_OFFSET,
    LOCAL_CONTROL_OVERRIDE,
    LOCAL_CONTROL_PROTECTION,
    LOCAL_CONTROL_UNKNOWN,
    MESSAGE_TYPE_ACTION,
    MESSAGE_TYPE_FAN_SPEED,
    MESSAGE_TYPE_LOCAL_OFFSET,
    MESSAGE_TYPE_LOCAL_TARGET_TEMPERATURE,
    MESSAGE_TYPE_MAIN_HUMIDITY,
    MESSAGE_TYPE_MAIN_TEMPERATURE,
    MESSAGE_TYPE_MODE,
    MESSAGE_TYPE_MODE_TARGET,
    MESSAGE_TYPE_TARGET_TEMPERATURE,
    OWNCommand,
    OWNHeatingCommand,
    OWNHeatingEvent,
)

from .const import (
    BUS_ROUTING,
    CONF_CENTRAL,
    CONF_COOLING_SUPPORT,
    CONF_DEVICE_MODEL,
    CONF_FAN_SUPPORT,
    CONF_HEATING_SUPPORT,
    CONF_MANUFACTURER,
    CONF_STANDALONE,
    LOGGER,
    signed_who4_temperature,
)
from .data import get_runtime_data
from .device_health import FaultKind
from .discovery import Address, DeviceContext, PlatformDiscovery, config_for, default_known_keys
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity
from .poll_health import PollHealth
from .where_grammar import is_probe, is_pump, where_param, zone_number

PLATFORM = Platform.CLIMATE
PARALLEL_UPDATES = 0

# WHO 4 dimension 7 zone state (OWNd#60); the values OWNd uses, spelled out
# here until the OWNd pin exports them.  On MyHomeServer1 + Home+Control
# plants this is the only frame carrying the zone's mode and setpoint (#429).
MESSAGE_TYPE_ZONE_STATE = "zone_state"
# A dimension 12 frame and the frame that turns the zone OFF follow within ~0.1 s (#454, #383)
_PROTECTION_FRAME_WINDOW = 2.0
_ZONE_CONTEXT_MODES = {"heating": HVACMode.HEAT, "cooling": HVACMode.COOL, "automatic": HVACMode.AUTO}
_ZONE_STATES_ON = ("setpoint", "comfort", "eco")
_ZONE_STATES_OFF = ("protection", "off")


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    """Set up the heating zones of a gateway (WHO=4): registry, myhome.yaml, then bus discovery.

    A zone is WHERE 1-99 (the central unit is ``#0`` / ``#0#1``); WHERE >= 100
    is a temperature probe, which belongs to the sensor platform. Heating
    frames often carry the zone they concern in a parameter rather than in
    WHERE (``*4*4001#5*0##``), so the address of a frame is derived here.
    """
    runtime = get_runtime_data(config_entry)
    if runtime is None or PLATFORM not in runtime.platforms:
        return True
    gateway = runtime.gateway
    configured = runtime.platforms.get(PLATFORM, {})

    def build(ctx: DeviceContext) -> MyHOMEClimate:
        where, interface = ctx.address.where, ctx.address.interface
        zone = zone_number(where)
        cfg = _zone_config(configured, ctx.address, ctx.key) or ctx.cfg
        suffix = f"{zone}I{interface}" if interface else zone
        is_central = zone in ("0", "01") or where in ("#0", "#0#1")
        if ctx.source != "bus":
            is_central = cfg.get(CONF_CENTRAL, is_central)
        default_name = f"Central Unit {suffix}" if is_central and ctx.source != "bus" else f"Climate Zone {suffix}"
        registry_name = getattr(ctx.registry_entry, "name", None)
        name = cfg.get(CONF_NAME) or (registry_name if isinstance(registry_name, str) else None) or default_name
        default_model = (
            "Central Unit (3550)" if where == "#0" else "Central Unit (4695)" if where == "#0#1" else "Heating Zone"
        )
        return MyHOMEClimate(
            hass=hass,
            device_id=ctx.key,
            who=ctx.who,
            where=where,
            interface=interface,
            name=name,
            heating=cfg.get(CONF_HEATING_SUPPORT, True),
            cooling=cfg.get(CONF_COOLING_SUPPORT, True),
            fan=cfg.get(CONF_FAN_SUPPORT, False),
            standalone=cfg.get(CONF_STANDALONE, not is_central),
            central=is_central,
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, default_model),
            gateway=gateway,
        )

    def accept(ctx: DeviceContext) -> bool:
        if is_probe(ctx.address.where):
            LOGGER.debug("Skipping non-zone address %s for climate platform", ctx.address.where)
            return False
        return True

    def known_keys(ctx: DeviceContext) -> list[str]:
        keys = [*default_known_keys(ctx), ctx.address.where, ctx.address.clean_where, ctx.config_id or ""]
        if not ctx.address.interface:
            keys.append(zone_number(ctx.address.where))
        return [k for k in keys if k]

    PlatformDiscovery(
        hass, config_entry, async_add_entities,
        platform=PLATFORM, who="4", event_type=OWNHeatingEvent, build=build,
        accept=accept, known_keys=known_keys, address=_zone_address, route_keys=_zone_route_keys,
        general_is_device=True,  # WHERE=0 frames name their zone in a parameter; _zone_address decides
    ).start()
    return True


def _zone_config(configured: dict[str, Any], address: Address, key: str) -> dict[str, Any]:
    """The ``myhome.yaml`` entry of a zone under every spelling older versions accepted.

    A routed zone only matches interface-qualified spellings: zone 1 exists
    on every bus, so the bare ones name the local bus's zone (#408).
    """
    where, zone = address.where, zone_number(address.where)
    if address.interface:
        routing = f"{BUS_ROUTING}{address.interface}"
        return config_for(configured, address, key, f"4-{zone}{routing}", f"4-{key}", f"#{zone}{routing}")
    return config_for(
        configured, address, key, zone,
        f"4-{zone}", f"4-{where}", f"4-{key}", f"4-#{zone}", f"4-#{where}",
        f"#{zone}", f"#{where}", f"zone_{zone}", f"zone_{where}",
    )


def _bus_zone(message: Any) -> int | None:
    """OWNd's zone of a frame, or ``None`` where the frame names no heating zone.

    Two different cases end in ``None``.

    Not a zone's frame, though OWNd decodes it correctly: a bare WHERE >= 100 is
    ``PZZ``, probe ``P`` (1-8) of zone ``ZZ`` (probe 105 -> sensor 1, zone 5).
    Read as the zone's it was delivered to that zone and, where the zone is not a
    heating zone (an external probe 105 beside zones 1-4), it discovered a
    phantom one (#549). Probes belong to the sensor platform.

    Misread by OWNd <= 2.0.0b8: on an unhashed WHERE ``0#<p>`` OWNd reports ``p`` as the zone, but ``p`` is
    never one: ``0#<n>`` is actuator ``n`` of zone 0, the pump the zones call
    with ``*4*4001#<zone>*0#<n>##`` (zones 1, 2, 3, 5 and 6 of one plant all
    call ``0#3``: #303, #333, #404), and ``0#4#<if>`` is WHERE=0 behind an
    F422 interface. Taken as zones, pump 2 switched zone 2 (#431) and the F422
    form became zone 4. ``#0#<n>`` (4-zone central unit) keeps OWNd's zone.
    """
    if is_pump(message):
        return None
    if is_probe(str(getattr(message, "where", None))):
        return None  # probe P of zone ZZ (105 = probe 1 of zone 5), not the zone's own frame
    zone: int | None = getattr(message, "zone", None)
    return zone


def _calling_zones(message: Any) -> tuple[list[str], str | None]:
    """Zones a heating frame concerns, and the F422 interface it came through."""
    raw_where = getattr(message, "where", None)
    zone = _bus_zone(message)
    interface = getattr(message, "interface", None)
    # OWNHeatingEvent keeps WHAT only as ``_what``; it has no ``what`` property.
    what = getattr(message, "what", None) or getattr(message, "_what", None)
    what_param = getattr(message, "what_param", None) or getattr(message, "_what_param", None) or []
    tail = where_param(message)
    if not interface and len(tail) > 1 and tail[0] == "4":
        interface = str(tail[1])

    zones: list[str] = []
    # WHERE ``<zone>#<n>`` names actuator <n> of the zone (``*#4*2#1*20*1##``
    # = zone 2, actuator 1 is on), never zone <n>: routing it there made zone 1
    # "heat" whenever any zone's actuator 1 opened (#333), and pump 2 (``0#2``)
    # switch zone 2 (#431).
    if zone is not None and zone > 0:
        zones.append(str(zone))
    if what in ("4001", "4002", 4001, 4002) and what_param:
        try:
            zones.append(str(int(what_param[0])))
        except (ValueError, TypeError):
            pass
    if not zones and raw_where and raw_where not in ("0", "") and not is_probe(str(raw_where)):
        zones.append(str(raw_where))
    return zones, interface


def _zone_address(message: Any) -> Address | None:
    """The zone a frame discovers; ``None`` for broadcasts and probes."""
    zones, interface = _calling_zones(message)
    if not zones:
        return None
    return Address(zones[0], interface)


def _zone_route_keys(message: Any, address: Address | None) -> list[str]:
    """Every key a heating frame is delivered under: the zone, WHERE, and the calling zones."""
    zones, interface = _calling_zones(message)
    zone = _bus_zone(message)
    keys = [] if zone is None else [f"#{zone}" if zone == 0 else str(zone)]
    # WHERE "0" with a parameter is pump ``0#N``, not the general "0" (#431).
    if getattr(message, "where", None) and not is_probe(str(message.where)) and not (
        is_pump(message)
    ):
        keys.append(str(message.where))
    for z in zones:
        keys.append(z)
        if interface:
            keys.append(f"{z}{BUS_ROUTING}{interface}")
    return keys


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    runtime = get_runtime_data(config_entry)
    if runtime is None or PLATFORM not in runtime.platforms:
        return True

    _configured_climate_devices = runtime.platforms[PLATFORM]

    for _climate_device in list(_configured_climate_devices.keys()):
        del runtime.platforms[PLATFORM][_climate_device]
    return True


class MyHOMEClimate(MyHOMEEntity, ClimateEntity):
    def __init__(
        self,
        hass: HomeAssistant | None,
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
        interface: str | None = None,
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
        )
        if hass is not None:
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

        # HVACMode.OFF is always available, so climate.turn_off / turn_on must be
        # advertised explicitly (mandatory since core 2025.1).
        self._attr_supported_features = ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
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
        self._fan: bool = False
        self._attr_fan_mode: str | None = None
        self._attr_fan_modes: list[str] | None = None
        self._running_fan_speed: str | None = None
        self._actuator_states: dict[str, bool] = {}
        if fan:
            self._enable_fan_mode()

        self._attr_current_temperature: float | None = None
        self._attr_current_humidity: float | None = None
        self._target_temperature: float | None = None
        self._local_offset: float = 0
        self._knob_pos: str = "UNKNOWN"
        self._local_target_temperature: float | None = None
        self._nominal_before_dim12: tuple[float | None, float] | None = None
        self._poll_health = PollHealth()

        self._attr_hvac_mode: HVACMode | None = None
        self._attr_hvac_action: HVACAction | None = None

    def _enable_fan_mode(self) -> None:
        """Dynamically enable fan mode support if not already enabled."""
        if not self._fan:
            self._fan = True
            self._attr_supported_features |= ClimateEntityFeature.FAN_MODE
            self._attr_fan_modes = ["auto", "low", "medium", "high"]
            if self._attr_fan_mode is None:
                self._attr_fan_mode = "auto"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return device specific attributes."""
        attrs: dict[str, Any] = {
            "local_offset": self._local_offset,
            "local_target_temperature": self._local_target_temperature,
            "knob_pos": self._knob_pos,
        }
        if self._fan:
            attrs["fan_mode"] = self._attr_fan_mode
            if self._running_fan_speed is not None:
                attrs["running_fan_speed"] = self._running_fan_speed
        if self._interface is not None:
            attrs["Int"] = self._interface
        if not self._central:
            attrs.update(self._poll_health.attributes())
        return attrs

    async def async_restore_last_state(self, last_state: State | None) -> None:
        """Restore climate state from HA storage."""
        if last_state is not None:
            if not self._central:
                self._poll_health.restore(last_state.attributes)
            else:
                self._clear_unresponsive_issue()
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
            if "fan_mode" in last_state.attributes or bool(
                last_state.attributes.get("supported_features", 0) & ClimateEntityFeature.FAN_MODE
            ):
                self._enable_fan_mode()
                restored_fan_mode = last_state.attributes.get("fan_mode")
                if restored_fan_mode in ("auto", "low", "medium", "high"):
                    self._attr_fan_mode = restored_fan_mode

    async def async_update(self) -> None:
        """Request status update from gateway, unless the zone has stopped answering."""
        if self._central:
            # Central units (#0, #0#1) do not answer Dimension 14 status requests (*#4*#0*14##);
            # in OpenWebNet, Dimension 14 status reads only apply to zone addresses 1..99.
            # Central units receive setpoints via commands (*#4*#0*#14*T*M##), broadcast events,
            # or restored state, and do not participate in point-to-point status polling or PollHealth tracking.
            return
        if self._poll_health.should_skip(time.time()):
            LOGGER.debug("%s %s did not answer its last polls; not asking again yet", self._gateway_handler.log_id, self._display_name)
            self._raise_unresponsive_issue()
            return
        request = OWNHeatingCommand.status(self._full_where)
        frames_before = self._poll_health.frames
        written = await self._gateway_handler.send_status_request(request)
        if isinstance(written, asyncio.Future):
            written.add_done_callback(lambda future: self._poll_answered(future, frames_before))
        if self._fan:
            await self._gateway_handler.send_status_request(
                cast(OWNCommand, OWNHeatingCommand.parse(f"*#4*{self._full_where}*11##"))
            )

    @callback
    def _poll_answered(self, written: asyncio.Future[float], frames_before: int) -> None:
        """Count a status request the gateway refused or never answered (see ``poll_health``)."""
        if written.cancelled():
            if not getattr(self._gateway_handler, "is_connected", False) or self._poll_health.frames != frames_before:
                return  # the gateway was away, or the zone did answer
            if self._poll_health.failed(time.time()):
                LOGGER.info("%s %s did not answer its status request twice in a row", self._gateway_handler.log_id, self._display_name)
                self._raise_unresponsive_issue()
            self._publish_state()
        elif self._poll_health.answered():
            self._clear_unresponsive_issue()
            self._publish_state()

    def _raise_unresponsive_issue(self) -> None:
        self._report_fault(FaultKind.UNRESPONSIVE)

    def _clear_unresponsive_issue(self) -> None:
        self._clear_fault(FaultKind.UNRESPONSIVE)

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        target_hass = self.hass or self._hass
        if target_hass is not None:
            if self._central:
                self._clear_unresponsive_issue()
            elif not self._standalone:
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
            self._actuator_states.clear()
        elif master_mode in (HVACMode.HEAT, HVACMode.COOL):
            if self._attr_hvac_mode != HVACMode.OFF:
                self._attr_hvac_mode = master_mode
        elif master_mode == HVACMode.AUTO:
            if self._attr_hvac_mode != HVACMode.OFF and HVACMode.AUTO in self._attr_hvac_modes:
                self._attr_hvac_mode = HVACMode.AUTO
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
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
    def target_temperature(self) -> float | None:
        if self._local_target_temperature is not None:
            return self._local_target_temperature
        else:
            return self._target_temperature

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
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
            cmd = OWNHeatingCommand.set_mode(
                where=self._where,
                mode=CLIMATE_MODE_OFF,
                standalone=self._standalone,
            )
            if cmd is not None:
                await self._gateway_handler.send(cmd)
        elif hvac_mode == HVACMode.AUTO:
            cmd = OWNHeatingCommand.set_mode(
                where=self._where,
                mode=CLIMATE_MODE_AUTO,
                standalone=self._standalone,
            )
            if cmd is not None:
                await self._gateway_handler.send(cmd)
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

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        target_temperature = float(
            kwargs.get("temperature", self._local_target_temperature)  # type: ignore[arg-type]
        ) - self._local_offset
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

    def _dimension_3_is_protection(self, message: OWNHeatingEvent) -> bool:
        """Whether a dimension 12/14 frame may be a protection setpoint rather than the nominal one.

        The trailing ``3`` cannot tell them apart (a manual write on a MyHomeServer1 plant
        ends in it too, #454), so the zone's mode decides: OFF, or not known yet, means
        protection is possible; a zone known to be running heats or cools to what it reports.
        """
        if self._attr_hvac_mode == HVACMode.OFF:
            return True
        value = getattr(message, "_dimension_value", None)
        if not value:
            return False
        return bool(len(value) > 1 and value[1] == "3" and self._attr_hvac_mode is None)

    def _remember_nominal(self) -> None:
        """Keep the nominal setpoint a dimension 12 frame is about to replace."""
        self._nominal_before_dim12 = (self._target_temperature, time.monotonic())

    def _restore_nominal_before_protection(self) -> None:
        """A protection setpoint arrives just before the frame that turns the zone OFF.

        The dimension 12 frame of a running zone was taken as the new nominal setpoint;
        if the OFF frame follows at once, it was the protection setpoint, so put the
        previous nominal back (#383).
        """
        remembered, self._nominal_before_dim12 = self._nominal_before_dim12, None
        if remembered is not None and time.monotonic() - remembered[1] <= _PROTECTION_FRAME_WINDOW:
            self._target_temperature = remembered[0]

    def _apply_zone_state(self, message: OWNHeatingEvent) -> None:
        """Dimension 7: the zone's operating state, and its setpoint in state 'setpoint'.

        Protection and off turn the zone OFF but keep the nominal setpoint, as
        WHAT 102/202 do (#383).  Comfort and eco carry no temperature.
        """
        state = getattr(message, "zone_state", None)
        if state in _ZONE_STATES_OFF:
            self._restore_nominal_before_protection()
            self._attr_hvac_mode = HVACMode.OFF
            self._attr_hvac_action = HVACAction.OFF
            self._actuator_states.clear()
            return
        if state not in _ZONE_STATES_ON:
            return
        prev_mode = self._attr_hvac_mode
        mode = _ZONE_CONTEXT_MODES.get(getattr(message, "zone_context", None) or "")
        if mode is not None and mode in self._attr_hvac_modes:
            self._attr_hvac_mode = mode
            if self._attr_hvac_action == HVACAction.OFF:
                self._attr_hvac_action = HVACAction.IDLE
        temperature = message.set_temperature if state == "setpoint" else None
        if temperature is not None:
            self._target_temperature = temperature
        if self._attr_hvac_mode != HVACMode.OFF and self._target_temperature is not None and (
            temperature is not None or prev_mode == HVACMode.OFF
        ):
            self._local_target_temperature = self._target_temperature + self._local_offset

    @callback
    def handle_event(self, message: OWNHeatingEvent) -> None:
        """Handle an event message."""
        if self._poll_health.frame_seen():
            self._clear_unresponsive_issue()
        if message.message_type == MESSAGE_TYPE_MAIN_TEMPERATURE:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._attr_current_temperature = signed_who4_temperature(message, message.main_temperature)
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
            is_protection_or_off = self._dimension_3_is_protection(message)
            if not is_protection_or_off:
                self._local_target_temperature = (
                    self._target_temperature + self._local_offset
                    if self._target_temperature is not None
                    else None
                )
        elif message.message_type == MESSAGE_TYPE_LOCAL_OFFSET:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._local_offset = message.local_offset if message.local_offset is not None else 0
            if message.local_control_state in (LOCAL_CONTROL_UNKNOWN, None):
                self._knob_pos = "UNKNOWN"
            elif message.local_control_state == LOCAL_CONTROL_OFFSET:
                self._knob_pos = f"{self._local_offset:+d}"
            elif message.local_control_state == LOCAL_CONTROL_NORMAL:
                self._knob_pos = "0"
            elif message.local_control_state == LOCAL_CONTROL_OFF:
                self._knob_pos = "OFF"
            elif message.local_control_state == LOCAL_CONTROL_PROTECTION:
                self._knob_pos = "*"
            elif message.local_control_state == LOCAL_CONTROL_OVERRIDE:
                self._knob_pos = "?"
            else:
                self._knob_pos = "UNKNOWN"
            if self._target_temperature is not None and self._attr_hvac_mode != HVACMode.OFF:
                self._local_target_temperature = self._target_temperature + self._local_offset
        elif message.message_type == MESSAGE_TYPE_LOCAL_TARGET_TEMPERATURE:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._local_target_temperature = message.local_set_temperature
            is_protection_or_off = self._dimension_3_is_protection(message)
            if not is_protection_or_off:
                self._remember_nominal()
                self._target_temperature = (
                    self._local_target_temperature - self._local_offset
                    if self._local_target_temperature is not None
                    else None
                )
        elif message.message_type == MESSAGE_TYPE_MODE:
            prev_mode = self._attr_hvac_mode
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
                self._restore_nominal_before_protection()
                self._attr_hvac_mode = HVACMode.OFF
                self._attr_hvac_action = HVACAction.OFF
                self._actuator_states.clear()
            if (
                prev_mode == HVACMode.OFF
                and self._attr_hvac_mode != HVACMode.OFF
                and self._target_temperature is not None
            ):
                self._local_target_temperature = self._target_temperature + self._local_offset
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
                self._restore_nominal_before_protection()
                self._attr_hvac_mode = HVACMode.OFF
                self._attr_hvac_action = HVACAction.OFF
                self._actuator_states.clear()
            self._target_temperature = message.set_temperature
            self._local_target_temperature = (
                self._target_temperature + self._local_offset
                if self._target_temperature is not None
                else None
            )
            if self._central and self.hass is not None and self._attr_hvac_mode is not None:
                async_dispatcher_send(
                    self.hass,
                    f"myhome_central_mode_{self._gateway_handler.mac}",
                    self._attr_hvac_mode,
                )
        elif message.message_type == MESSAGE_TYPE_ZONE_STATE:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._apply_zone_state(message)
        elif message.message_type == MESSAGE_TYPE_ACTION:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            # Actuator status (Dimension 20) with values >= 5 reports fancoil fan status in OWNd.
            is_fan = isinstance(getattr(message, "fan_speed", None), int) or isinstance(
                getattr(message, "fan_on", None), bool
            )
            if is_fan:
                self._enable_fan_mode()
                speed = getattr(message, "fan_speed", None)
                if speed == 1:
                    self._running_fan_speed = "low"
                elif speed == 2:
                    self._running_fan_speed = "medium"
                elif speed == 3:
                    self._running_fan_speed = "high"
                elif getattr(message, "fan_on", None) is False or speed == 4:
                    self._running_fan_speed = "off"
                else:
                    self._running_fan_speed = None

            actuator_id = str(
                getattr(message, "actuator", None)
                or getattr(message, "_actuator", None)
                or (
                    message._where_param[0]
                    if getattr(message, "_where_param", None)
                    else "valve"
                )
            )
            self._actuator_states[actuator_id] = bool(message.is_active())
            any_active = any(self._actuator_states.values())

            if any_active:
                if self._heating and self._cooling:
                    if message.is_heating():
                        self._attr_hvac_action = HVACAction.HEATING
                    elif message.is_cooling():
                        self._attr_hvac_action = HVACAction.COOLING
                    elif self._attr_hvac_mode == HVACMode.COOL:
                        self._attr_hvac_action = HVACAction.COOLING
                    elif self._attr_hvac_mode == HVACMode.HEAT:
                        self._attr_hvac_action = HVACAction.HEATING
                    elif self._attr_hvac_mode == HVACMode.AUTO:
                        if (
                            self._target_temperature is not None
                            and self._attr_current_temperature is not None
                        ):
                            if self._attr_current_temperature < self._target_temperature:
                                self._attr_hvac_action = HVACAction.HEATING
                            else:
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
            self._enable_fan_mode()
            speed = getattr(message, "fan_speed", None)
            if speed == 0:
                self._attr_fan_mode = "auto"
            elif speed == 1:
                self._attr_fan_mode = "low"
            elif speed == 2:
                self._attr_fan_mode = "medium"
            elif speed == 3:
                self._attr_fan_mode = "high"
            elif getattr(message, "fan_on", None) is True:
                self._attr_fan_mode = "auto"

        self._publish_state()
