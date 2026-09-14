"""Shared platform setup: restore, configure, discover and route bus devices.

Every entity platform of this integration follows the same life cycle for one
gateway (config entry):

1. **restore** - entities already in the entity registry are re-created at
   once, so they exist before the gateway has said anything;
2. **configure** - devices declared in ``myhome.yaml`` that are not in the
   registry yet are created;
3. **discover** - the first frame from an unknown address creates the entity;
4. **route** - every frame is forwarded to the entity that owns the address.

This module holds that skeleton once. A platform supplies what differs: the
``WHO`` it serves, the OWNd event class it listens to, how a device is built
from its :class:`DeviceContext`, and a few optional hooks (see
:class:`PlatformDiscovery`).

Addressing follows the OpenWebNet WHERE conventions (point-to-point ``APL``,
area ``A``, group ``#G``, general ``0``) with the F422 bus-routing suffix
``APL#4#<bus>``; :class:`Address` keeps the two parts apart and derives the
keys used for unique ids, de-duplication and default names.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity import Entity

from .const import CONF_BUS_INTERFACE, CONF_WHERE, CONF_WHO, LOGGER
from .data import MyHOMEConfigEntry, MyHOMERuntimeData

BUS_ROUTING = "#4#"


@dataclass(frozen=True)
class Address:
    """A bus address: WHERE plus the optional F422 interface it sits behind."""

    where: str
    interface: str | None = None
    #: Platform-specific tail of the unique id (audio zones are ``"<zone>#16"``).
    key_suffix: str = ""

    @classmethod
    def from_device_id(cls, device_id: str, key_suffix: str = "") -> Address:
        """Parse ``"12"`` or ``"12#4#01"`` (the device part of a unique id)."""
        device_id = str(device_id)
        if key_suffix and device_id.endswith(key_suffix):
            device_id = device_id[: -len(key_suffix)]
        where, _, interface = device_id.partition(BUS_ROUTING)
        return cls(where, interface or None, key_suffix)

    @classmethod
    def from_message(cls, message: Any) -> Address | None:
        """Address of an OWNd event, or ``None`` when it carries no WHERE."""
        where = getattr(message, "where", None)
        if not where:
            return None
        return cls(str(where), getattr(message, "interface", None) or None)

    @classmethod
    def from_config(cls, dev_id: str, cfg: dict[str, Any]) -> Address:
        """Address of a ``myhome.yaml`` device (the key is the WHERE when omitted)."""
        interface = cfg.get(CONF_BUS_INTERFACE) or cfg.get("bus_interface") or cfg.get("interface")
        return cls(str(cfg.get(CONF_WHERE, dev_id)), str(interface) if interface else None)

    @property
    def key(self) -> str:
        """Device part of the unique id: ``where`` or ``where#4#interface`` (+ suffix)."""
        base = f"{self.where}{BUS_ROUTING}{self.interface}" if self.interface else self.where
        return base + self.key_suffix

    @property
    def clean_where(self) -> str:
        """WHERE without a legacy ``who-`` prefix (``"1-12"`` -> ``"12"``)."""
        return self.where.split("-")[-1]

    @property
    def clean_key(self) -> str:
        """Like :attr:`key` but on :attr:`clean_where`; the de-duplication key."""
        return f"{self.clean_where}{BUS_ROUTING}{self.interface}" if self.interface else self.clean_where

    @property
    def suffix(self) -> str:
        """Default-name suffix: ``12`` or ``12I01`` for a routed address."""
        return f"{self.clean_where}I{self.interface}" if self.interface else self.clean_where


def parse_unique_id(unique_id: str, mac: str, entry_mac: str | None = None) -> tuple[str | None, str]:
    """Split ``"{mac}-{who}-{device_id}"`` into ``(who, device_id)``.

    Older ids had no WHO part; then ``who`` is ``None`` and the remainder is
    the device id.
    """
    after_mac = unique_id.replace(f"{mac}-", "", 1)
    if entry_mac and entry_mac != mac:
        after_mac = after_mac.replace(f"{entry_mac}-", "", 1)
    who, sep, device_id = after_mac.partition("-")
    if not sep:
        return None, after_mac
    return who, device_id


def config_for(configured: dict[str, Any], address: Address, *extra_keys: str) -> dict[str, Any]:
    """The ``myhome.yaml`` entry for an address, tried by key, WHERE, clean WHERE, then extras."""
    for key in (address.key, address.where, address.clean_where, *extra_keys):
        cfg = configured.get(key)
        if cfg:
            return dict(cfg)
    return {}


class KnownDevices:
    """Set of address keys already owned by an entity of this platform."""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    def add(self, *keys: str | None) -> None:
        self._keys.update(k for k in keys if k)

    def discard(self, key: str) -> None:
        self._keys.discard(key)

    def __contains__(self, key: object) -> bool:
        return key in self._keys

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)


@callback
def announce_new_device(hass: HomeAssistant, mac: str, who: str, address: Address, name: str) -> None:
    """Tell the button platform a lockable actuator exists (lock / unlock / calibrate buttons)."""
    async_dispatcher_send(
        hass,
        f"myhome_new_device_{mac}",
        {"who": who, "where": address.where, "interface": address.interface, "name": name, "device_id": address.key},
    )


def update_signal(mac: str, who: str, key: str) -> str:
    """Dispatcher signal an entity subscribes to for its own frames."""
    return f"myhome_update_{mac}_{who}_{key}"


@dataclass
class DeviceContext:
    """Everything a platform needs to build one entity."""

    address: Address
    who: str
    cfg: dict[str, Any] = field(default_factory=dict)
    #: ``registry`` | ``yaml`` | ``bus``
    source: str = "bus"
    message: Any = None
    registry_entry: er.RegistryEntry | None = None
    #: Registry entries keep their device id verbatim (older ids may lack a suffix).
    device_id: str | None = None

    @property
    def key(self) -> str:
        return self.device_id or self.address.key

    @property
    def suffix(self) -> str:
        return self.address.suffix


BuildFn = Callable[[DeviceContext], Entity | None]


class PlatformDiscovery:
    """Restore / configure / discover / route for one platform of one gateway.

    Parameters
    ----------
    platform:
        Entity domain (``"light"``); also the ``myhome.yaml`` section.
    who:
        WHO the platform serves; used in unique ids, signals and announcements.
    event_type:
        OWNd event class whose frames belong to this platform.
    build:
        Creates the entity for a :class:`DeviceContext`; ``None`` skips it.
    announce:
        Announce lockable actuators to the button platform (lights, switches,
        covers).
    reject_registry_entry:
        Optional: return ``True`` to remove a registry entry instead of
        restoring it (ghost or corrupted ids).
    accept:
        Optional: return ``False`` to skip creating an entity for a context
        (the address belongs to another platform).
    pre_message:
        Optional: called with ``(message, address, known)`` before discovery;
        return ``True`` when the frame was fully handled (routed elsewhere).
    address:
        Optional: derive the :class:`Address` of a frame (default: its WHERE
        and interface); return ``None`` to ignore the frame.
    key_suffix:
        Optional tail appended to every device id (``"#16"`` for audio zones).
    on_general:
        Optional: called with a general (WHERE=0) frame; without it general
        frames are ignored - unless ``general_is_device`` is set, for
        subsystems where WHERE=0 addresses a real device (the burglar alarm
        central unit).
    yaml_device_id:
        Optional: how the device id of a ``myhome.yaml`` device is formed;
        defaults to :attr:`Address.key` (switches use the clean WHERE).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MyHOMEConfigEntry,
        async_add_entities: Callable[[Iterable[Entity]], None],
        *,
        platform: str,
        who: str,
        event_type: type | None,
        build: BuildFn,
        announce: bool = False,
        reject_registry_entry: Callable[[er.RegistryEntry, DeviceContext], bool] | None = None,
        accept: Callable[[DeviceContext], bool] | None = None,
        pre_message: Callable[[Any, Address, KnownDevices], bool] | None = None,
        on_general: Callable[[Any], None] | None = None,
        general_is_device: bool = False,
        yaml_device_id: Callable[[Address], str] | None = None,
        address: Callable[[Any], Address | None] | None = None,
        key_suffix: str = "",
    ) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.async_add_entities = async_add_entities
        self.platform = platform
        self.who = who
        self.event_type = event_type
        self.build = build
        self.announce = announce
        self.reject_registry_entry = reject_registry_entry
        self.accept = accept
        self.pre_message = pre_message
        self.on_general = on_general
        self.general_is_device = general_is_device
        self.address_of = address or Address.from_message
        self.key_suffix = key_suffix
        self.yaml_device_id = yaml_device_id or (lambda address: address.key)
        self.runtime: MyHOMERuntimeData = config_entry.runtime_data
        # Signals are keyed on the entry's MAC (what the gateway handler publishes under).
        self.mac: str = str(config_entry.data.get(CONF_MAC) or self.runtime.mac)
        self.known = KnownDevices()
        self.configured: dict[str, Any] = self.runtime.platforms.get(platform, {})

    # ── registry ────────────────────────────────────────────────────────

    def registry_entries(self) -> tuple[er.EntityRegistry | None, list[er.RegistryEntry]]:
        """Registry entries of this config entry (empty when the registry is unavailable)."""
        try:
            registry = er.async_get(self.hass)
            return registry, list(er.async_entries_for_config_entry(registry, self.config_entry.entry_id))
        except Exception:  # registry not loaded in some harnesses
            return None, []

    def restore(self) -> list[Entity]:
        """Re-create the platform's entities from the entity registry."""
        registry, entries = self.registry_entries()
        entities: list[Entity] = []
        for entry in entries:
            if entry.domain != self.platform or not entry.unique_id:
                continue
            _who, device_id = parse_unique_id(entry.unique_id, str(self.runtime.mac), self.mac)
            address = Address.from_device_id(device_id, self.key_suffix)
            ctx = DeviceContext(
                address=address, who=self.who, source="registry", registry_entry=entry, device_id=device_id,
                cfg=config_for(self.configured, address),
            )
            if self.reject_registry_entry and self.reject_registry_entry(entry, ctx):
                if registry is not None:
                    registry.async_remove(entry.entity_id)
                continue
            if self.accept and not self.accept(ctx):
                continue
            entity = self.build(ctx)
            if entity is None:
                continue
            self.known.add(device_id)
            entities.append(entity)
        return entities

    # ── myhome.yaml ─────────────────────────────────────────────────────

    def configure(self) -> list[Entity]:
        """Create the ``myhome.yaml`` devices that are not in the registry yet."""
        entities: list[Entity] = []
        seen: set[str] = set()
        for dev_id, cfg in self.configured.items():
            if not isinstance(cfg, dict):
                continue
            address = Address.from_config(dev_id, cfg)
            if self.key_suffix:
                address = Address(address.where, address.interface, self.key_suffix)
            device_id = self.yaml_device_id(address)
            if address.clean_key in seen or device_id in self.known or dev_id in self.known:
                continue
            ctx = DeviceContext(address=address, who=str(cfg.get(CONF_WHO, self.who)), cfg=cfg, source="yaml")
            if self.accept and not self.accept(ctx):
                continue
            seen.add(address.clean_key)
            entity = self.build(ctx)
            if entity is None:
                continue
            self.known.add(device_id, dev_id, None if address.interface else address.clean_where)
            entities.append(entity)
            if self.announce:
                announce_new_device(self.hass, self.mac, ctx.who, address, self._name_of(entity, address))
        return entities

    # ── bus ─────────────────────────────────────────────────────────────

    @callback
    def handle_message(self, message: Any) -> None:
        """Discover from, then route, one frame of this platform's WHO."""
        if self.event_type is not None and not isinstance(message, self.event_type):
            return
        if getattr(message, "is_translation", None) is True:
            return
        if not self.general_is_device and (
            getattr(message, "is_general", False) is True or str(getattr(message, "where", "")) == "0"
        ):
            if self.on_general:
                self.on_general(message)
            return
        address = self.address_of(message)
        if address is None:
            return
        if getattr(message, "is_group", False) is True or getattr(message, "is_area", False) is True:
            return
        if self.pre_message and self.pre_message(message, address, self.known):
            return
        if address.key not in self.known:
            ctx = DeviceContext(
                address=address, who=str(getattr(message, "who", self.who)), source="bus", message=message,
                cfg=config_for(self.configured, address),
            )
            if not self.accept or self.accept(ctx):
                entity = self.build(ctx)
                if entity is not None:
                    self.known.add(address.key)
                    self.async_add_entities([entity])
                    entity.handle_event(message)  # type: ignore[attr-defined]
                    if self.announce:
                        announce_new_device(self.hass, self.mac, self.who, address, self._name_of(entity, address))
        self.route(message, address.key)

    @callback
    def route(self, message: Any, key: str) -> None:
        """Forward a frame to the entity owning ``key``."""
        async_dispatcher_send(self.hass, update_signal(self.mac, self.who, key), message)

    def listen(self) -> None:
        """Subscribe to the gateway's frames for the life of the config entry."""
        self.config_entry.async_on_unload(
            async_dispatcher_connect(self.hass, f"myhome_message_{self.mac}", self.handle_message)
        )

    # ── the whole cycle ─────────────────────────────────────────────────

    def start(self, *, listen: bool = True) -> list[Entity]:
        """Restore, configure, add the result, and (optionally) start discovery."""
        entities = self.restore() + self.configure()
        if entities:
            self.async_add_entities(entities)
        if listen:
            self.listen()
        LOGGER.debug(
            "%s: %s restored/configured %d entities (%d addresses known)",
            self.mac, self.platform, len(entities), len(self.known),
        )
        return entities

    @staticmethod
    def _name_of(entity: Entity, address: Address) -> str:
        return str(getattr(entity, "_device_name", None) or address.suffix)
