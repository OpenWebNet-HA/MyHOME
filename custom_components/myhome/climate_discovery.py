"""Which heating zones (WHO 4) a gateway has, and when a zone entity is created.

One place for what used to be spread over the platform's setup:

1. Zones come from the entity registry, from ``myhome.yaml`` and from frames on the bus
   (the platform's ``PlatformDiscovery``, with the rules of ``ZoneDiscovery``). A zone is WHERE 1-99, the central unit ``#0`` / ``#0#1``; WHERE >= 100
   is a temperature probe, which belongs to the sensor platform. Heating frames often carry
   the zone they concern in a parameter rather than in WHERE (``*4*4001#5*0#3##``), so the
   address of a frame is derived here (``_zone_address``, ``_zone_route_keys``).
2. Not every frame of a zone address proves a thermostat (#785): the state of a plain valve,
   a typeless frame or a virtual probe's temperature may belong to an address that has only
   actuators. Such a frame is held (``ZoneDiscovery._held``) and handed to the zone once a
   frame of a thermostat creates it.
3. At start the gateway's sweep asks what its firmware forwards (``gateway._discovery_frames``:
   the general request, or every actuator's state and the central unit's status). A zone
   known only from held actuator frames is then asked for its temperature, which only a
   thermostat answers (``ZoneDiscovery._ask_actuator_only_zones``).

Who runs a zone found this way (the central unit, or nobody: a standalone thermostat) is the
entity's business: ``MyHOMEClimate._standalone``.
"""

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from homeassistant.components.climate.const import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
from OWNd.message import MESSAGE_TYPE_ACTION, OWNHeatingEvent

from .const import BUS_ROUTING, CONF_BUS_INTERFACE, CONF_WHERE, CONF_WHO, LOGGER
from .discovery import (
    Address,
    DeviceContext,
    config_for,
    default_known_keys,
    is_broadcast_where,
)
from .expose import is_exposed_on_bus
from .myhome_device import MyHOMEEntity
from .where_grammar import (
    canonical_thermo_zone,
    is_central_unit_actuator_status,
    is_probe,
    is_pump,
    parse_thermo_actuator_where,
    thermo_dim19_status,
    thermo_dim20_status,
    thermo_event_actuator,
    thermo_event_interface,
    zone_number,
)

# How many zones in a row may leave their temperature read unanswered before the zones known
# only from their actuators are left to report by themselves (``_ask_actuator_only_zones``):
# an unanswered read holds a command session for 2.1 s on an F461 and about 9 s on an F455.
ACTUATOR_ONLY_ZONE_MISSES = 2
# The states of a climate entity that only a thermostat's own frames produce.
_REAL_HVAC_STATES = frozenset(
    {
        HVACMode.HEAT.value,
        HVACMode.COOL.value,
        HVACMode.HEAT_COOL.value,
        HVACMode.AUTO.value,
        HVACMode.DRY.value,
        HVACMode.FAN_ONLY.value,
    }
)
# A zone as the discovery keys it: its number and the F422 interface it is behind, if any.
ZonePair = tuple[str, str | None]


def _zone_config(configured: dict[str, Any], address: Address, key: str) -> dict[str, Any]:
    """The ``myhome.yaml`` entry of a zone under every spelling older versions accepted.

    A routed zone only matches interface-qualified spellings: zone 1 exists
    on every bus, so the bare ones name the local bus's zone (#408).
    """
    where, clean = address.where, address.clean_where
    zone = zone_number(where) if "#" not in clean.removeprefix("#") else clean
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

    Not a zone's frame either: an actuator status with WHERE ``#0`` (``*#4*#0*20*V##``, an
    F461's answer to the all-zone request ``*#4*#0*20##`` of 2.0.0, #944). OWNd reports zone 0 and
    actuator 1 for it. Taken as the central unit's, a fan coil value (``*#4*#0*20*5##``)
    discovered a central unit on a plant that has none, and on a plant that has one the
    frame became an actuator of the central unit.
    """
    if is_pump(message) or is_central_unit_actuator_status(message):
        return None
    if is_probe(str(getattr(message, "where", None))):
        return None  # probe P of zone ZZ (105 = probe 1 of zone 5), not the zone's own frame
    zone: int | None = getattr(message, "zone", None)
    return zone


def _two_digit_interface(interface: str | None) -> str | None:
    """The F422 interface as the bus and the configuration validator spell it (``01``)."""
    if interface is None:
        return None
    interface = str(interface)
    return interface.zfill(2) if interface.isdigit() else interface


def _calling_zones(message: Any) -> tuple[list[str], str | None]:
    """Zones a heating frame concerns, and the F422 interface it came through."""
    raw_where = getattr(message, "where", None)
    zone = _bus_zone(message)
    interface = thermo_event_interface(message)
    # OWNHeatingEvent keeps WHAT only as ``_what``; it has no ``what`` property.
    what = getattr(message, "what", None) or getattr(message, "_what", None)
    what_param = getattr(message, "what_param", None) or getattr(message, "_what_param", None) or []

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
    clean_raw = str(raw_where).split("-")[-1] if raw_where is not None else ""
    if (
        not zones
        and raw_where
        and not is_broadcast_where(clean_raw)
        and clean_raw != ""
        and not is_probe(str(raw_where))
        and not is_central_unit_actuator_status(message)
    ):
        zones.append(str(raw_where))
    return zones, interface


def _zone_address(message: Any) -> Address | None:
    """The zone a frame discovers; ``None`` for broadcasts and probes."""
    zones, interface = _calling_zones(message)
    if not zones:
        return None
    return Address(zones[0], interface)


def _zone_route_keys(message: Any, address: Address | None) -> list[str]:
    """Every key a heating frame is delivered under: the zone, WHERE, and the calling zones.

    A frame that came through an interface is delivered under interface-qualified keys only
    (``4#4#01``): zone 4 exists on every bus, and the bare key is the local bus's zone (#408).
    Under both, the temperature, the mode and the valve status of the zone behind the
    interface were written into the local zone with the same number.
    """
    zones, interface = _calling_zones(message)
    zone = _bus_zone(message)
    keys = [] if zone is None else [f"#{zone}" if zone == 0 else str(zone)]
    # WHERE "0" with a parameter is pump ``0#N``, not the general "0" (#431); an actuator
    # status with WHERE ``#0`` is not the central unit's (#944).
    if getattr(message, "where", None) and not is_probe(str(message.where)) and not (
        is_pump(message) or is_central_unit_actuator_status(message)
    ):
        keys.append(str(message.where))
    keys.extend(zones)
    if interface:
        return list(dict.fromkeys(f"{key}{BUS_ROUTING}{interface}" for key in keys))
    return keys


def _zone_pair(address: Address) -> ZonePair | None:
    """The zone and interface of an address, ``None`` where it names no zone 1-99."""
    zone = canonical_thermo_zone(address.where)
    if zone is None:
        return None
    return (zone, _two_digit_interface(address.interface))


def _thermo_switch_zones(
    platforms: dict[str, Any], configured: dict[str, Any]
) -> tuple[set[ZonePair], set[ZonePair]]:
    """The zones a WHO 4 thermo switch is configured on, with no climate entry of their own (#785).

    First the zones switched as a whole (``Z``): no climate entity is created for them.
    Then the zones with a switch on one actuator (``Z#N``): a climate entity only once a
    thermostat shows itself.
    """
    whole: set[ZonePair] = set()
    actuators: set[ZonePair] = set()
    for sw_cfg in platforms.get("switch", {}).values():
        if not isinstance(sw_cfg, dict) or str(sw_cfg.get(CONF_WHO)) != "4":
            continue
        try:
            _nw, sw_zone, sw_act, sw_if = parse_thermo_actuator_where(
                sw_cfg.get(CONF_WHERE), sw_cfg.get(CONF_BUS_INTERFACE)
            )
        except ValueError:
            continue
        zone_addr = Address(sw_zone, sw_if)
        zpad_addr = Address(f"0{sw_zone}", sw_if) if len(sw_zone) == 1 else None
        has_explicit_climate = bool(
            _zone_config(configured, zone_addr, zone_addr.key)
            or (zpad_addr is not None and _zone_config(configured, zpad_addr, zpad_addr.key))
        )
        if not has_explicit_climate:
            (whole if sw_act is None else actuators).add((sw_zone, sw_if))
    return whole, actuators


class ZoneDiscovery:
    """The heating zones of one gateway: what creates a zone entity, and the reads at start.

    The platform hands ``build``, ``accept``, ``duplicate`` and ``known_keys`` to its
    ``PlatformDiscovery`` and calls ``start_reads`` once that runs. ``build_zone(ctx, cfg)``
    makes the entity for a discovery context and its ``myhome.yaml`` entry; the platform
    supplies it, so this module does not know the entity class.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        runtime: Any,
        build_zone: Callable[[DeviceContext, dict[str, Any]], MyHOMEEntity],
    ) -> None:
        self._hass = hass
        self._config_entry = config_entry
        self._gateway = runtime.gateway
        self._configured: dict[str, Any] = runtime.platforms.get(Platform.CLIMATE, {})
        self._build_zone = build_zone
        self._switch_zones, self._switch_actuator_zones = _thermo_switch_zones(runtime.platforms, self._configured)
        # Frames of a zone that is not created yet, by slot (``dim0``, ``dim19``, ``dim19_off``,
        # ``act:<N>``): the newest frame of a slot replaces the older one.
        self._held: dict[ZonePair, dict[str, OWNHeatingEvent]] = {}

    def start_reads(self) -> None:
        """Ask the zones known only from their actuators, once the start-up sweep is over.

        Which gateways are asked is decided when the sweep is over (``_ask_actuator_only_zones``),
        as the sweep's own frames are: the model may be corrected in band after the entities
        were set up.
        """
        if inspect.iscoroutinefunction(getattr(self._gateway, "read_zone_temperature", None)):
            run = self._ask_actuator_only_zones()
            task: object = self._config_entry.async_create_background_task(
                self._hass, run, name=f"myhome_{self._gateway.mac}_actuator_only_zones"
            )
            if not isinstance(task, asyncio.Future):
                run.close()  # a config entry double that schedules nothing

    def build(self, ctx: DeviceContext) -> MyHOMEEntity:
        """The zone's entity, with the frames that were held for it."""
        entity = self._build_zone(ctx, _zone_config(self._configured, ctx.address, ctx.key) or ctx.cfg)
        pair = _zone_pair(ctx.address)
        if pair is not None:
            for held in self._held.pop(pair, {}).values():
                entity.handle_event(held)
        return entity

    def accept(self, ctx: DeviceContext) -> bool:
        """Whether this frame, registry entry or configuration entry creates a zone now.

        A frame that does not is kept (``_held``) where the zone may still turn out to be a
        thermostat, and handed to the zone when it is created.
        """
        if is_probe(ctx.address.where):
            LOGGER.debug("Skipping non-zone address %s for climate platform", ctx.address.where)
            return False
        if _zone_pair(ctx.address) in self._switch_zones:
            LOGGER.debug(
                "Skipping climate zone %s: configured as WHO=4 thermo switch (#785)",
                ctx.address.key,
            )
            return False
        if (
            not ctx.cfg
            and ctx.source == "bus"
            and getattr(ctx.message, "dimension", None) == 0
        ):
            full_where = (
                f"{ctx.address.where}#4#{ctx.address.interface}"
                if ctx.address.interface
                else ctx.address.where
            )
            if is_exposed_on_bus(self._hass, self._config_entry, 4, full_where):
                zone_pair = _zone_pair(ctx.address)
                if zone_pair is not None:
                    self._held.setdefault(zone_pair, {})["dim0"] = ctx.message
                LOGGER.debug(
                    "Deferring climate zone %s creation on exposed WHO=4 virtual probe Dimension 0 frame",
                    ctx.address.key,
                )
                return False
        if (
            ctx.source == "bus"
            and _zone_pair(ctx.address) in self._switch_actuator_zones
            and getattr(ctx.message, "message_type", None) is None
        ):
            LOGGER.debug(
                "Deferring climate zone %s creation on typeless WHO=4 frame (%s) for thermo switch zone (#785)",
                ctx.address.key,
                ctx.message,
            )
            return False
        if (
            ctx.source == "bus"
            and _zone_pair(ctx.address) in self._switch_actuator_zones
            and getattr(ctx.message, "message_type", None) == MESSAGE_TYPE_ACTION
            and getattr(ctx.message, "dimension", None) == 19
        ):
            dim19 = thermo_dim19_status(ctx.message)
            if dim19 is None or (
                not isinstance(dim19[3], bool) and not isinstance(dim19[4], bool)
            ):
                zone_pair = _zone_pair(ctx.address)
                if dim19 is not None and zone_pair is not None:
                    events = self._held.setdefault(zone_pair, {})
                    if dim19[2]:
                        events.pop("dim19", None)
                        events["dim19"] = ctx.message
                    else:
                        events.pop("dim19", None)
                        events.pop("dim19_off", None)
                        events["dim19_off"] = ctx.message
                LOGGER.debug(
                    "Deferring climate zone %s creation on Dimension 19 valve-only frame (#785)",
                    ctx.address.key,
                )
                return False
        if (
            ctx.source == "bus"
            and getattr(ctx.message, "message_type", None) == MESSAGE_TYPE_ACTION
            and getattr(ctx.message, "dimension", None) == 20
        ):
            dim20 = thermo_dim20_status(ctx.message)
            if dim20 is None or (
                not isinstance(dim20[2], int) and not isinstance(dim20[1], bool)
            ):
                zone_pair = _zone_pair(ctx.address)
                if dim20 is not None and zone_pair is not None:
                    slot = f"act:{thermo_event_actuator(ctx.message)}"
                    events = self._held.setdefault(zone_pair, {})
                    events.pop(slot, None)
                    events[slot] = ctx.message
                LOGGER.debug(
                    "Deferring climate zone %s creation on Dimension 20 actuator-only frame (#785)",
                    ctx.address.key,
                )
                return False
        return True



    def duplicate(self, entry: er.RegistryEntry, ctx: DeviceContext) -> bool:
        """Whether a registry entry is not a zone of its own and is removed."""
        pair = _zone_pair(ctx.address)
        if pair is not None and (
            pair in self._switch_zones
            or (pair in self._switch_actuator_zones and not self._has_thermostat_state(entry.entity_id))
        ):
            LOGGER.info(
                "climate: pruning probe-less zone %s claimed by WHO=4 thermo switch (#785)",
                entry.entity_id,
            )
            return True
        if ctx.cfg:
            return False
        return is_broadcast_where(ctx.address.clean_where)

    @staticmethod
    def known_keys(ctx: DeviceContext) -> list[str]:
        """Every key a zone may be stored under in the registry and in ``myhome.yaml``.

        A zone behind an interface is known under its interface-qualified keys only: under the
        bare zone number it took the frames of the local zone with that number, and where it
        was seen first no local zone was ever created.
        """
        keys = [*default_known_keys(ctx), ctx.config_id or ""]
        if not ctx.address.interface:
            keys += [ctx.address.where, ctx.address.clean_where]
            if "#" not in ctx.address.clean_where.removeprefix("#"):
                keys.append(zone_number(ctx.address.where))
        return [k for k in keys if k]

    def _has_thermostat_state(self, entity_id: str) -> bool:
        """Whether Home Assistant holds a state of this entity that only a thermostat produces."""
        # MyHOMEClimate.async_restore_last_state coerces STATE_UNKNOWN /
        # STATE_UNAVAILABLE into HVACMode.OFF ("off"), which HA then persists in
        # .storage/core.restore_state across restarts (#263). Bare state == "off"
        # without thermostat telemetry is therefore not evidence of a physical
        # thermostat (#785).
        states_obj = getattr(self._hass, "states", None)
        live = states_obj.get(entity_id) if states_obj is not None else None
        hass_data = getattr(self._hass, "data", None)
        restore_obj = hass_data.get(DATA_RESTORE_STATE) if isinstance(hass_data, dict) else None
        # HA 2026.11+ (core#183906) indexes stored states by entity registry ID and
        # exposes async_get_stored_state(entity_id); HA <= 2026.10 stores a dict on
        # RestoreStateData.last_states keyed by entity_id.
        get_stored = getattr(restore_obj, "async_get_stored_state", None)
        if callable(get_stored):
            stored = get_stored(entity_id)
        else:
            last_states = getattr(restore_obj, "last_states", None)
            stored = last_states.get(entity_id) if isinstance(last_states, dict) else None
        restored = getattr(stored, "state", None)
        for st in (live, restored):
            if st is None:
                continue
            attrs = getattr(st, "attributes", None) or {}
            if (
                attrs.get("current_temperature") is not None
                or attrs.get("temperature") is not None
                or attrs.get("current_humidity") is not None
                or attrs.get("season") is not None
                or attrs.get("program") is not None
                or attrs.get("holiday_days") is not None
                or attrs.get("scenario") is not None
                or attrs.get("knob_pos") not in (None, "UNKNOWN")
                or attrs.get("fan_coil_cooling") is True
                or attrs.get("fan_coil_heating") is True
                or getattr(st, "state", None) in _REAL_HVAC_STATES
            ):
                return True
        return False


    async def _ask_actuator_only_zones(self) -> None:
        """Ask each zone known only from its actuators whether it is a thermostat.

        The start-up sweep asks every actuator for its state (``gateway.ALL_ACTUATORS_REQUEST``)
        where the gateway drops the general request. A fan coil's answer creates its zone; a
        plain valve's is kept in ``_held``, because that address may be a towel
        warmer with no thermostat (#785). A thermostat tells the two apart: it answers a
        read of its temperature (``*#4*Z*0##``: 0.09-0.25 s on an F461, an F455 and a
        MyHomeServer1, 2026-11-18), and that frame creates the zone, which then takes the kept
        actuator frames. An address with no thermostat answers nothing and stays as it is.

        One zone at a time, each read after the bus went quiet and under the gateway's start-up
        poll lock, so it does not cross the status polls of the zones being added (on an F455
        a read crossing one got the other zone's reply, 2026-11-18 16:01:05). Where a read
        nothing answers is cheap (``unanswered_zone_read_is_cheap``) every such zone is asked.
        Elsewhere the run ends after two zones in a row that did not answer; it also ends when
        a read could not be told from a busy gateway twice. What is left waits for its own
        report, as before. Not asked: a zone behind an
        interface, and a zone that has a thermo switch configured.
        """
        gateway = self._gateway
        await gateway.wait_for_initial_discovery()
        if getattr(gateway, "asks_every_actuator_at_start", False) is not True:
            # Only where the sweep asks every actuator in place of the general request the
            # firmware drops. Where that request is forwarded every thermostat has answered it,
            # so a read of an address known only from its actuator would be one nothing
            # answers, at a cost nobody measured on those models.
            return
        running = getattr(gateway, "actuator_only_reads", lambda _running: None)  # absent on a bare double
        running(True)  # the zones created here take the kept actuator frames: no second request
        try:
            await self._read_actuator_only_zones()
        finally:
            running(False)

    async def _read_actuator_only_zones(self) -> None:
        """The reads of ``_ask_actuator_only_zones``, one zone at a time."""
        gateway = self._gateway
        asked: set[tuple[str, str | None]] = set()
        silent = 0
        every_zone = getattr(gateway, "unanswered_zone_read_is_cheap", False) is True
        lock = getattr(gateway, "_who4_startup_poll_lock", None)
        while every_zone or silent < ACTUATOR_ONLY_ZONE_MISSES:
            pair = next(
                (
                    known
                    for known, events in self._held.items()
                    if known not in asked
                    and known[1] is None
                    and known not in self._switch_zones
                    and known not in self._switch_actuator_zones
                    # An address exposed on the bus as a virtual probe: its own temperature frame
                    # is held like the actuator frames (``accept``), so the read would count
                    # as one nothing answered.
                    and not is_exposed_on_bus(self._hass, self._config_entry, 4, known[0])
                    and events
                    and all(slot.startswith(("act:", "dim19")) for slot in events)
                ),
                None,
            )
            if pair is None:
                return
            asked.add(pair)
            answered: bool | None = None
            for _attempt in range(2):
                if isinstance(lock, asyncio.Lock):
                    async with lock:
                        answered = await gateway.read_zone_temperature(pair[0])
                else:
                    answered = await gateway.read_zone_temperature(pair[0])
                if answered is not None:
                    break
            if pair not in self._held:
                silent = 0  # built from its answer
                continue
            if answered is None:
                LOGGER.debug(
                    "%s Zone %s: its temperature could not be read while the gateway was busy; "
                    "leaving it and the remaining actuator-only zones to their own reports",
                    gateway.log_id,
                    pair[0],
                )
                return
            LOGGER.debug("%s Zone %s has actuators and no thermostat answered", gateway.log_id, pair[0])
            silent += 1

