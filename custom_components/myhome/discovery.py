"""Shared platform setup: restore, configure, discover and route bus devices.

Every entity platform of this integration follows the same life cycle for one
gateway (config entry):

1. **restore** - entities already in the entity registry are re-created at
   once, so they exist before the gateway has said anything;
2. **configure** - devices declared in ``myhome.yaml`` that are not in the
   registry yet are created;
3. **discover** - the first frame from an unknown address creates the entity;
4. **route** - every frame is delivered to the entities that own the address.

Delivery goes through the gateway's :class:`~.router.FrameRouter`: an entity
is subscribed the moment it is created, under every key it answers to, so
no frame of a burst is lost while Home Assistant adds it; a platform
publishes each frame under the keys derived from it.

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

import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.cover import CoverEntity
from homeassistant.components.light import LightEntity
from homeassistant.components.light.const import ColorMode
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity import Entity

from .const import (
    BUS_ROUTING,
    CONF_BUS_INTERFACE,
    CONF_CENTRAL,
    CONF_DEVICE_CLASS,
    CONF_STANDALONE_ZONES,
    CONF_WHERE,
    CONF_WHO,
    CONF_ZONE,
    DOMAIN,
    LOGGER,
)
from .data import MyHOMEConfigEntry, MyHOMERuntimeData, get_runtime_data
from .expose import is_exposed_on_bus
from .ignored import IgnoredAddresses
from .myhome_device import MyHOMEEntity
from .topology import entry_for_mac, peer_unique_id
from .where_grammar import (
    canonical_central_zone,
    canonical_thermo_zone,
    contact_where_aliases,
    is_actuator_where,
    is_probe,
    validate_standalone_zones,
)


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
        base, routing, interface = device_id.rpartition(BUS_ROUTING)
        if (
            routing
            and base
            and interface.isascii()
            and interface.isdigit()
            and 1 <= len(interface) <= 2
            and int(interface) <= 15
        ):
            return cls(base, interface.zfill(2), key_suffix)
        return cls(device_id, None, key_suffix)

    @classmethod
    def from_message(cls, message: Any) -> Address | None:
        """Address of an OWNd event, or ``None`` when it carries no WHERE."""
        where = getattr(message, "where", None)
        if not where:
            return None
        return cls(str(where), getattr(message, "interface", None) or None)

    @classmethod
    def from_config(cls, dev_id: str, cfg: dict[str, Any]) -> Address:
        """Address of a ``myhome.yaml`` device: ``where`` (``zone`` for heating), else the key."""
        interface = next(
            (
                cfg[k]
                for k in (CONF_BUS_INTERFACE, "bus_interface", "interface")
                if k in cfg and cfg[k] is not None and cfg[k] != ""
            ),
            None,
        )
        where = str(cfg.get(CONF_WHERE, cfg.get(CONF_ZONE, dev_id)))
        if interface is None:
            base, routing, inline_iface = where.rpartition(BUS_ROUTING)
            if (
                routing
                and base
                and inline_iface.isascii()
                and inline_iface.isdigit()
                and 1 <= len(inline_iface) <= 2
                and int(inline_iface) <= 15
            ):
                return cls(base, inline_iface.zfill(2))
            return cls(where, None)
        iface_str = str(interface)
        if iface_str.isascii() and iface_str.isdigit() and 1 <= len(iface_str) <= 2 and int(iface_str) <= 15:
            iface_str = iface_str.zfill(2)
        base, routing, inline_iface = where.rpartition(BUS_ROUTING)
        if (
            routing
            and base
            and inline_iface.isascii()
            and inline_iface.isdigit()
            and 1 <= len(inline_iface) <= 2
            and inline_iface.zfill(2) == iface_str
        ):
            where = base
        return cls(where, iface_str)

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


def is_broadcast_where(where: str | None) -> bool:
    """Return whether an address is an OpenWebNet general or area broadcast (0 or 00).

    Central units (#0, #0#1) and CEN 4-digit zone 0 pushbuttons (0001..0015)
    are physical point targets and return False.
    """
    if not where:
        return False
    clean = str(where).split("-")[-1]
    return not clean.startswith("#") and clean.split("#")[0] in ("0", "00")


def is_motion_end(message: Any) -> bool:
    """``*1*39*WHERE##``: a WHO 1 detector reports the end of movement (WHAT 39).

    The gateway translator decodes the SCS presence status frame (class 0x1D) value 00
    as WHAT 34 and value 01 as WHAT 39 (OpenWebNet-HA/own-firmware-oracle#56, merged, findings/who1-motion-detector.md:
    F454, F459, F460, F461, MH200N, MH202, MyHomeServer1); an F454 + Legrand 067226 sends WHAT 39 about
    45 s after the last WHAT 34 (#777). OWNd releases without WHAT 39 report it as
    ``unknown_state`` 39, later ones as ``motion_end``.
    """
    return getattr(message, "motion_end", False) is True or getattr(message, "unknown_state", None) == 39


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
    """The ``myhome.yaml`` entry for an address.

    An unrouted address is tried by key, WHERE, clean WHERE, then the extras.
    A routed one (behind an F422) is tried only under interface-qualified
    keys and the extras: a bare WHERE entry belongs to the local bus, and the
    same WHERE exists on every bus (#408).
    """
    if address.interface is None:
        candidates = [address.key, address.where, address.clean_where, *extra_keys]
    else:
        iface = (
            address.interface.zfill(2)
            if address.interface.isascii() and address.interface.isdigit()
            else address.interface
        )
        candidates = [
            address.key,
            address.clean_key,
            f"{address.where}{BUS_ROUTING}{iface}",
            f"{address.clean_where}{BUS_ROUTING}{iface}",
            *extra_keys,
        ]
    for key in candidates:
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


#: Buttons button.py hangs off an actuator, as suffixes of the actuator's unique id.
COMPANION_SUFFIXES = ("-disable", "-enable", "-calibrate")
#: Companion temperature / humidity / actuator-output sensors sensor.py attaches to a WHO=4 climate zone.
CLIMATE_COMPANION_SENSOR_SUFFIXES = ("-temperature", "-humidity", "-output-level")
_CLIMATE_ACTUATOR_OUTPUT_RE = re.compile(
    r"^4-([1-9][0-9]?)#[1-9](?:#4#(0?[0-9]|1[0-5]))?-output-level$", re.ASCII
)
#: Platforms whose entities get those buttons.
ACTUATOR_DOMAINS = ("light", "switch", "cover")
ACTUATOR_DEFAULT_WHOS = {"light": "1", "switch": "1", "cover": "2"}


@callback
def _kept_by_sibling_actuator(
    hass: HomeAssistant,
    registry: er.EntityRegistry,
    entry: er.RegistryEntry,
    config_entry_id: str,
) -> tuple[bool, set[str]]:
    """Whether another registered or configured actuator shares ``entry``'s bus address."""
    uids: set[str] = {entry.unique_id} if entry.unique_id else set()
    cfg_entry = hass.config_entries.async_get_entry(config_entry_id)
    runtime = get_runtime_data(cfg_entry) if cfg_entry is not None else None
    who: str | None = None
    target: Address | None = None
    if runtime is not None and cfg_entry is not None and entry.unique_id:
        gw_mac = str(getattr(runtime.gateway, "mac", runtime.mac))
        entry_mac = str(cfg_entry.data.get(CONF_MAC, runtime.mac))
        parsed_who, dev_id = parse_unique_id(entry.unique_id, gw_mac, entry_mac)
        if parsed_who in ("1", "2") and dev_id:
            cand = Address.from_device_id(dev_id)
            if cand.where.isascii() and cand.where.isdigit():
                who, target = parsed_who, cand
                uids.update(
                    f"{m}-{who}-{target.clean_key}"
                    for m in {gw_mac, str(runtime.mac), entry_mac}
                    if m
                )
    if any(registry.async_get_entity_id(d, DOMAIN, uid) for d in ACTUATOR_DOMAINS for uid in uids):
        return True, uids
    if runtime is None or cfg_entry is None or who is None or target is None:
        return False, uids
    who_int = int(who)
    full_where = f"{target.where}{BUS_ROUTING}{target.interface}" if target.interface else target.where
    if (
        IgnoredAddresses.from_config_entry(cfg_entry).is_ignored(who_int, target.where, target.interface)
        or is_exposed_on_bus(hass, cfg_entry, who_int, full_where)
        or (
            runtime.is_follower
            and bool(runtime.primary_gateway_mac)
            and any(
                actuator_only_on_primary(registry, uid, str(runtime.mac), str(runtime.primary_gateway_mac))
                for uid in uids
            )
        )
    ):
        return False, uids
    for dom in ACTUATOR_DOMAINS:
        if dom == entry.domain:
            continue
        for cfg_key, cfg in runtime.platforms.get(dom, {}).items():
            if not isinstance(cfg, Mapping) or str(cfg.get(CONF_WHO, ACTUATOR_DEFAULT_WHOS[dom])) != who:
                continue
            addr = Address.from_config(str(cfg_key), dict(cfg))
            if addr.clean_key == target.clean_key or addr.key == target.key:
                return True, uids
    return False, uids


def _extract_who4_device_id(
    unique_id: str | None, macs: set[str]
) -> tuple[str, set[str]]:
    """Extract the WHO=4 ``device_id`` from a modern (``-4-``) or pre-v2 climate ``unique_id``."""
    if not unique_id:
        return "", set()
    known_macs = set(macs)
    uid_lower = unique_id.lower()
    matched_mac = next(
        (m for m in known_macs if uid_lower.startswith(f"{m.lower()}-")),
        None,
    )
    if matched_mac is not None:
        known_macs.add(unique_id[: len(matched_mac)])
        after_mac = unique_id[len(matched_mac) + 1 :]
        if after_mac.startswith("4-"):
            return after_mac[2:], known_macs
        if "-" not in after_mac:
            return after_mac, known_macs
        return "", known_macs
    if "-4-" in unique_id:
        mac_prefix, _, dev_id = unique_id.partition("-4-")
        if mac_prefix:
            known_macs.add(mac_prefix)
            return dev_id, known_macs
    return "", known_macs


def _climate_zone_from_unique_id(
    unique_id: str | None, gw_mac: str, entry_mac: str, *extra_macs: str
) -> tuple[str | None, str | None, set[str]]:
    """Extract canonical ``(zone, interface, mac_prefixes)`` from a WHO=4 climate unique_id."""
    dev_id, macs = _extract_who4_device_id(
        unique_id, {m for m in (gw_mac, entry_mac, *extra_macs) if m}
    )
    if not dev_id:
        return None, None, macs
    c_addr = Address.from_device_id(dev_id)
    bare_where = c_addr.where.removeprefix("4-").removeprefix("#")
    if not (bare_where.isascii() and bare_where.isdigit() and len(bare_where) <= 2):
        return None, None, macs
    canon_zone = canonical_thermo_zone(c_addr.where)
    if canon_zone is None:
        return None, None, macs
    return canon_zone, c_addr.interface, macs


def _four_zone_central_master(where: str) -> str | None:
    """Return canonical master zone ``"1"``..``"99"`` for a ``#0#N`` WHERE, else ``None``."""
    clean = str(where).strip().removeprefix("4-")
    while clean.startswith("#0##0"):
        clean = clean[len("#0#") :]
    if not clean.startswith("#0#"):
        return None
    tail = clean[len("#0#") :].removeprefix("#")
    if not (tail.isascii() and tail.isdigit() and len(tail) <= 2):
        return None
    zone_int = int(tail)
    return str(zone_int) if 1 <= zone_int <= 99 else None


def _four_zone_central_from_unique_id(
    unique_id: str | None, macs: set[str]
) -> tuple[str | None, str | None]:
    """Return ``(master_zone, interface)`` when ``unique_id`` names a ``#0#N`` central unit."""
    dev_id, _ = _extract_who4_device_id(unique_id, macs)
    if not dev_id:
        return None, None
    c_addr = Address.from_device_id(dev_id)
    master = _four_zone_central_master(c_addr.where)
    return (master, c_addr.interface) if master is not None else (None, None)


def _climate_config_map(runtime: MyHOMERuntimeData | None) -> Mapping[str, Any]:
    """Return the gateway's climate configuration mapping, if any."""
    if runtime is None:
        return {}
    platforms_cfg = runtime.platforms.get("climate")
    if isinstance(platforms_cfg, Mapping) and platforms_cfg:
        return platforms_cfg
    raw_config = getattr(runtime, "config", None)
    if isinstance(raw_config, Mapping):
        climate_cfg = raw_config.get("climate")
        if isinstance(climate_cfg, Mapping):
            return climate_cfg
    return {}


def _has_separate_master_zone_config(
    cfg_entry: Any,
    climate_cfgs: Mapping[str, Any],
    zone: str,
    iface: str | None,
) -> bool:
    """Whether master ``(zone, iface)`` is configured as a separate standalone/explicit zone."""
    if cfg_entry is not None and iface is None:
        standalone_zones, _ = validate_standalone_zones(
            cfg_entry.options.get(CONF_STANDALONE_ZONES)
        )
        if zone in standalone_zones:
            return True
    for cfg_key, cfg in climate_cfgs.items():
        if not isinstance(cfg, Mapping) or bool(cfg.get(CONF_CENTRAL, False)):
            continue
        cfg_addr = Address.from_config(str(cfg_key), dict(cfg))
        if _four_zone_central_master(cfg_addr.clean_where) is not None:
            continue
        if canonical_thermo_zone(cfg_addr.clean_where) == zone and cfg_addr.interface == iface:
            return True
    return False


def _has_unified_four_zone_central(
    registry: er.EntityRegistry,
    config_entry_id: str,
    cfg_entry: Any,
    runtime: MyHOMERuntimeData | None,
    zone: str,
    iface: str | None,
    macs: set[str],
) -> bool:
    """Whether master ``(zone, iface)`` is unified into a ``#0#N`` 4-zone central unit."""
    climate_cfgs = _climate_config_map(runtime)
    if _has_separate_master_zone_config(cfg_entry, climate_cfgs, zone, iface):
        return False
    for other in er.async_entries_for_config_entry(registry, config_entry_id):
        if other.domain == "climate" and _four_zone_central_from_unique_id(
            other.unique_id, macs
        ) == (zone, iface):
            return True
    if runtime is not None:
        live_climates = runtime.entities.get("climate")
        if isinstance(live_climates, Mapping):
            for ent_key, ent in live_climates.items():
                if getattr(ent, "_unified_master", True) is False:
                    continue
                k_addr = Address.from_device_id(str(ent_key))
                c_pair = (_four_zone_central_master(k_addr.where), k_addr.interface)
                if c_pair[0] is None:
                    c_pair = _four_zone_central_from_unique_id(
                        getattr(ent, "unique_id", None), macs
                    )
                if c_pair == (zone, iface):
                    return True
    for cfg_key, cfg in climate_cfgs.items():
        if not isinstance(cfg, Mapping):
            continue
        cfg_addr = Address.from_config(str(cfg_key), dict(cfg))
        cand_where = (
            canonical_central_zone(cfg_addr.clean_where)
            if bool(cfg.get(CONF_CENTRAL, False))
            else cfg_addr.clean_where
        )
        if _four_zone_central_master(cand_where) == zone and cfg_addr.interface == iface:
            return True
    return False


def _device_owns_four_zone_central(
    dev_reg: dr.DeviceRegistry,
    device_id: str | None,
    zone: str,
    iface: str | None,
    macs: set[str],
) -> bool:
    """Whether ``device_id`` carries the ``#0#N`` central identifier for ``(zone, iface)``."""
    dev = dev_reg.async_get(device_id) if device_id else None
    if dev is None:
        return False
    return any(
        dom == DOMAIN and _four_zone_central_from_unique_id(ident, macs) == (zone, iface)
        for dom, ident in dev.identifiers
    )


def _is_who4_probe_temperature_uid(unique_id: str | None) -> bool:
    """Whether ``unique_id`` belongs to a standalone WHO=4 probe (``PZZ >= 100``)."""
    if not unique_id or not unique_id.endswith("-temperature") or "-4-" not in unique_id:
        return False
    _, _, tail = unique_id.partition("-4-")
    addr = Address.from_device_id(tail.removesuffix("-temperature"))
    return is_probe(addr.clean_where)


def _has_active_yaml_zone_temperature(
    hass: HomeAssistant,
    registry: er.EntityRegistry,
    cfg_entry: Any,
    runtime: MyHOMERuntimeData | None,
    zone: str,
    iface: str | None,
) -> bool:
    """Whether an explicit active ``myhome.yaml`` WHO=4 temperature sensor owns ``(zone, iface)``."""
    if runtime is None or cfg_entry is None:
        return False
    zkey = f"{zone}{BUS_ROUTING}{iface}" if iface else zone
    if (
        IgnoredAddresses.from_config_entry(cfg_entry).is_ignored(4, zone, iface)
        or is_exposed_on_bus(hass, cfg_entry, 4, zkey)
    ):
        return False
    follower_primary = (
        str(runtime.primary_gateway_mac)
        if runtime.is_follower and runtime.primary_gateway_mac
        else ""
    )
    if follower_primary:
        peer = peer_unique_id(
            f"{runtime.mac}-4-{zkey}-temperature",
            str(runtime.mac),
            follower_primary,
        )
        if peer is not None and registry.async_get_entity_id("sensor", DOMAIN, peer) is not None:
            return False
    sensor_cfgs: Mapping[str, Any] = runtime.platforms.get("sensor", {})
    for cfg_key, cfg in sensor_cfgs.items():
        if not isinstance(cfg, Mapping):
            continue
        dev_cls = cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class")
        cfg_who = cfg.get(CONF_WHO)
        if dev_cls != "temperature" or (cfg_who is not None and str(cfg_who) != "4"):
            continue
        cfg_addr = Address.from_config(str(cfg_key), dict(cfg))
        if is_probe(cfg_addr.clean_where) or is_actuator_where(cfg_addr.clean_where):
            continue
        if canonical_thermo_zone(cfg_addr.clean_where) == zone and cfg_addr.interface == iface:
            if follower_primary:
                raw_peer = peer_unique_id(
                    f"{runtime.mac}-4-{cfg_addr.clean_key}-temperature",
                    str(runtime.mac),
                    follower_primary,
                )
                if (
                    raw_peer is not None
                    and registry.async_get_entity_id("sensor", DOMAIN, raw_peer) is not None
                ):
                    return False
            return True
    return False


@callback
def _prune_climate_companions(
    hass: HomeAssistant,
    registry: er.EntityRegistry,
    entry: er.RegistryEntry,
    config_entry_id: str,
) -> tuple[set[str], set[str]]:
    """Remove WHO=4 companion sensors of a pruned climate entry unless kept by YAML, sibling, or ``#0#N``."""
    touched_devices: set[str] = set()
    cfg_entry = hass.config_entries.async_get_entry(config_entry_id)
    runtime = get_runtime_data(cfg_entry) if cfg_entry is not None else None
    runtime_mac = str(runtime.mac) if runtime is not None and runtime.mac else ""
    gw_mac = str(getattr(runtime.gateway, "mac", runtime_mac)) if runtime is not None else ""
    entry_mac = (
        str(cfg_entry.data.get(CONF_MAC, runtime_mac))
        if cfg_entry is not None
        else ""
    )
    zone, iface, macs = _climate_zone_from_unique_id(
        entry.unique_id, gw_mac, entry_mac, runtime_mac
    )
    if runtime_mac:
        macs.add(runtime_mac)

    sibling_climate = False
    if zone is not None:
        for other in er.async_entries_for_config_entry(registry, config_entry_id):
            if other.domain != "climate" or not other.unique_id:
                continue
            o_zone, o_iface, _ = _climate_zone_from_unique_id(
                other.unique_id, gw_mac, entry_mac, runtime_mac
            )
            if (o_zone, o_iface) == (zone, iface):
                sibling_climate = True
                break

    dev_reg = dr.async_get(hass)
    unified_into_central = zone is not None and _has_unified_four_zone_central(
        registry, config_entry_id, cfg_entry, runtime, zone, iface, macs
    )
    keep_yaml_temp = zone is not None and _has_active_yaml_zone_temperature(
        hass, registry, cfg_entry, runtime, zone, iface
    )
    excluded_suffixes: set[str] = set()
    if keep_yaml_temp:
        excluded_suffixes.add("-temperature")
    if unified_into_central:
        excluded_suffixes.add("-output-level")
    allowed_suffixes = tuple(
        s for s in CLIMATE_COMPANION_SENSOR_SUFFIXES if s not in excluded_suffixes
    )

    def _remove_sensor(entity_id: str) -> None:
        reg_ent = registry.async_get(entity_id)
        if reg_ent is not None:
            if (
                zone is not None
                and (reg_ent.unique_id or "").endswith("-output-level")
                and _device_owns_four_zone_central(
                    dev_reg, reg_ent.device_id, zone, iface, macs
                )
            ):
                return
            if reg_ent.device_id:
                touched_devices.add(reg_ent.device_id)
        registry.async_remove(entity_id)

    if not sibling_climate:
        stems: set[str] = {entry.unique_id} if entry.unique_id else set()
        if zone is not None:
            zkey = f"{zone}{BUS_ROUTING}{iface}" if iface else zone
            stems.update(f"{m}-4-{zkey}" for m in macs if m)
        for stem in stems:
            for suffix in allowed_suffixes:
                comp_sensor = registry.async_get_entity_id("sensor", DOMAIN, f"{stem}{suffix}")
                if comp_sensor is not None:
                    _remove_sensor(comp_sensor)

        if zone is not None and macs and not unified_into_central:
            for cand in list(er.async_entries_for_config_entry(registry, config_entry_id)):
                uid = cand.unique_id or ""
                if cand.domain != "sensor" or not uid.endswith("-output-level"):
                    continue
                after_mac = next(
                    (
                        uid[len(m) + 1 :]
                        for m in macs
                        if uid.lower().startswith(f"{m.lower()}-")
                    ),
                    "",
                )
                match = _CLIMATE_ACTUATOR_OUTPUT_RE.fullmatch(after_mac)
                if match is not None:
                    m_zone, m_iface = match.groups()
                    norm_iface = m_iface.zfill(2) if m_iface is not None else None
                    if (m_zone, norm_iface) == (zone, iface):
                        _remove_sensor(cand.entity_id)

    if entry.device_id and not sibling_climate:
        remaining = er.async_entries_for_device(registry, entry.device_id, include_disabled_entities=True)
        if not any(
            e.domain == "climate" and e.config_entry_id == config_entry_id
            for e in remaining
        ):
            for e in list(remaining):
                if (
                    e.domain == "sensor"
                    and e.config_entry_id == config_entry_id
                    and (e.unique_id or "").endswith(allowed_suffixes)
                    and not _is_who4_probe_temperature_uid(e.unique_id)
                ):
                    _remove_sensor(e.entity_id)

    protected_devices = (
        {
            dev.id
            for dev in dr.async_entries_for_config_entry(dev_reg, config_entry_id)
            if any(
                dom == DOMAIN
                and _four_zone_central_from_unique_id(ident, macs) == (zone, iface)
                for dom, ident in dev.identifiers
            )
        }
        if zone is not None
        else set()
    )
    return touched_devices - protected_devices, protected_devices


@callback
def prune_entity(hass: HomeAssistant, registry: er.EntityRegistry, entry: er.RegistryEntry, config_entry_id: str) -> None:
    """Remove an entity with its lock / unlock / calibrate buttons, and its device once empty (#524).

    Removing only the entity would leave its buttons behind as orphans ("no
    longer provided") on a device without its actuator.
    """
    registry.async_remove(entry.entity_id)
    if not isinstance(getattr(registry, "entities", None), Mapping):
        return
    kept_by_sibling, candidate_uids = _kept_by_sibling_actuator(hass, registry, entry, config_entry_id)
    if not kept_by_sibling:
        for uid in candidate_uids:
            for suffix in COMPANION_SUFFIXES:
                button = registry.async_get_entity_id("button", DOMAIN, f"{uid}{suffix}")
                if button is not None:
                    registry.async_remove(button)
    if entry.domain == "cover":
        for uid in candidate_uids:
            delta_number = registry.async_get_entity_id(
                "number", DOMAIN, f"{uid}-min_position_delta"
            )
            if delta_number is not None:
                registry.async_remove(delta_number)
    if entry.domain == "binary_sensor" and entry.device_id:
        remaining = er.async_entries_for_device(registry, entry.device_id, include_disabled_entities=True)
        if not any(e.domain == "binary_sensor" for e in remaining):
            for e in list(remaining):
                if e.domain == "switch" and (e.unique_id or "").endswith("-invert"):
                    registry.async_remove(e.entity_id)
    extra_devices, protected_devices = (
        _prune_climate_companions(hass, registry, entry, config_entry_id)
        if entry.domain == "climate"
        else (set(), set())
    )
    devices_to_check = set(extra_devices)
    if not kept_by_sibling and entry.device_id:
        devices_to_check.add(entry.device_id)
    elif entry.device_id:
        devices_to_check.discard(entry.device_id)
    devices_to_check -= protected_devices
    if devices_to_check:
        dev_reg = dr.async_get(hass)
        for dev_id in devices_to_check:
            if dev_reg.async_get(dev_id) is not None and not er.async_entries_for_device(
                registry, dev_id, include_disabled_entities=True
            ):
                dev_reg.async_remove_device(dev_id)


@callback
def actuator_only_on_primary(registry: er.EntityRegistry, actuator_unique_id: str, own_mac: str, primary_mac: str) -> bool:
    """Whether a follower no longer has this actuator while its primary gateway does.

    That is the state ``PlatformDiscovery`` leaves behind when it prunes a follower's
    duplicate (#453): the lock / unlock / calibrate buttons of such an actuator do not
    belong on the follower either.
    """
    peer = peer_unique_id(actuator_unique_id, own_mac, primary_mac)
    if peer is None or any(registry.async_get_entity_id(d, DOMAIN, actuator_unique_id) for d in ACTUATOR_DOMAINS):
        return False
    return any(registry.async_get_entity_id(d, DOMAIN, peer) for d in ACTUATOR_DOMAINS)


@callback
def devices_with_binary_sensor(
    hass: HomeAssistant, registry: er.EntityRegistry, config_entry_id: str, primary_mac: str
) -> set[str]:
    """MyHOME device identifiers that carry a binary sensor, on a follower and on its primary.

    One pass over the entities of those two config entries, so a caller with many
    ``myhome.yaml`` contacts scans once instead of walking the whole entity registry
    for each of them.
    """
    entry_ids = [config_entry_id]
    primary = entry_for_mac(hass, primary_mac, exclude=config_entry_id)
    if primary is not None:
        entry_ids.append(primary.entry_id)
    devices = dr.async_get(hass)
    with_sensor: set[str] = set()
    for entry_id in entry_ids:
        for entry in er.async_entries_for_config_entry(registry, entry_id):
            if entry.domain != "binary_sensor" or not entry.device_id:
                continue
            device = devices.async_get(entry.device_id)
            if device is not None:
                with_sensor.update(identifier for domain, identifier in device.identifiers if domain == DOMAIN)
    return with_sensor


def binary_sensor_only_on_primary(with_sensor: set[str], device_identifier: str, own_mac: str, primary_mac: str) -> bool:
    """Whether a follower has no binary sensor on this device while its primary gateway does.

    That is the state ``PlatformDiscovery`` leaves behind for a follower's dry contact or
    auxiliary channel the primary already has (#453): its "Invert state" switch, which
    shares the device, does not belong on the follower either. ``with_sensor`` comes from
    ``devices_with_binary_sensor``; ``own_mac`` is the gateway's normalised MAC, the prefix
    of every device identifier (an entry's stored ``CONF_MAC`` may differ in case).
    """
    peer = peer_unique_id(device_identifier, own_mac, primary_mac)
    if peer is None or device_identifier in with_sensor:
        return False
    if peer in with_sensor:
        return True
    head, sep, where = peer.partition("-25-")
    if sep:
        return any(f"{head}-25-{alias}" in with_sensor for alias in contact_where_aliases(where))
    return False


@callback
def prune_orphaned_companions(hass: HomeAssistant, config_entry_id: str, own_mac: str, primary_mac: str) -> None:
    """Remove a follower's companion entities whose actuator was pruned in favour of the primary (#524).

    Earlier versions removed only the actuator, leaving its companion buttons/numbers
    and device behind. A companion counts as orphaned when this gateway no longer has
    its actuator and the primary does; companions of an actuator the user deleted are
    left alone.
    """
    registry = er.async_get(hass)
    for entry in er.async_entries_for_config_entry(registry, config_entry_id):
        if entry.domain == "button" and entry.unique_id.endswith(COMPANION_SUFFIXES):
            actuator = entry.unique_id.rsplit("-", 1)[0]
        elif entry.domain == "number" and entry.unique_id.endswith("-min_position_delta"):
            actuator = entry.unique_id.removesuffix("-min_position_delta")
        else:
            continue
        if actuator_only_on_primary(registry, actuator, own_mac, primary_mac):
            LOGGER.info(
                "Pruned orphaned %s %s: its actuator lives on primary gateway %s",
                entry.domain,
                entry.entity_id,
                primary_mac,
            )
            prune_entity(hass, registry, entry, config_entry_id)


@callback
def announce_new_device(hass: HomeAssistant, mac: str, who: str, address: Address, name: str) -> None:
    """Tell the button and number platforms a lockable/native actuator exists."""
    async_dispatcher_send(
        hass,
        f"myhome_new_device_{dr.format_mac(mac)}",
        {"who": who, "where": address.where, "interface": address.interface, "name": name, "device_id": address.key},
    )


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
    #: Device id the entity is created under: registry entries keep the id from
    #: their unique id verbatim (older ids may lack a suffix), ``myhome.yaml``
    #: devices use ``yaml_device_id``; bus devices default to the address key.
    device_id: str | None = None
    #: The ``myhome.yaml`` key (``source == "yaml"`` only).
    config_id: str | None = None

    @property
    def key(self) -> str:
        return self.device_id or self.address.key

    @property
    def suffix(self) -> str:
        return self.address.suffix


BuildFn = Callable[[DeviceContext], "MyHOMEEntity | Sequence[MyHOMEEntity] | None"]


def default_known_keys(ctx: DeviceContext) -> list[str]:
    """Keys an entity is remembered and reached under: its id and address, plus the
    yaml key and bare WHERE for yaml devices."""
    keys = [ctx.key, ctx.address.key, ctx.address.clean_key]
    if ctx.source == "yaml":
        keys.append(ctx.config_id or "")
        if not ctx.address.interface:
            keys.append(ctx.address.clean_where)
    return [k for k in keys if k]


def message_has_state(entity: MyHOMEEntity, message: Any) -> bool:
    """Whether the revealing bus message already carries the entity's complete state.

    When True, poll-on-add is suppressed so the gateway command session is not
    flooded during general status request bursts. When False (e.g. a moving cover
    without position, or a dimmer without brightness), the entity retains
    poll-on-add to query its full state once the general sweep completes.
    """
    if message is None:
        return False

    # Cover: needs current position or dimension 10 reply
    if isinstance(entity, CoverEntity):
        return getattr(message, "current_position", None) is not None or getattr(message, "dimension", None) == 10

    # Light: a dimmer or colour light needs brightness or a dimension status
    if isinstance(entity, LightEntity):
        modes = set(entity.supported_color_modes or ())
        if modes - {ColorMode.ONOFF, ColorMode.UNKNOWN}:
            return getattr(message, "brightness", None) is not None or getattr(message, "dimension", None) is not None
        return getattr(message, "is_on", None) is not None

    # Heating zone: mode, setpoint and temperature arrive in separate frames, so a
    # temperature-only frame must not cancel the poll for the rest on gateways that support it.
    # A 99-zone central unit (#0) always answers *#4*#0## on all gateways (including SCS-only
    # gateways like MH200N / F454) with a multi-frame burst of mode + diagnostic flags.
    if isinstance(entity, ClimateEntity):
        if getattr(entity, "_central", False):
            return False
        if not getattr(entity, "_gateway_supports_zone_status", True):
            owes_knob = getattr(entity, "_local_offset_read_pending", False)
            polls_temperature = getattr(entity, "_polls_temperature", False) is True
            if not owes_knob:
                # SCS-only gateway: only a zone that asks for its temperature (#185) still needs to.
                return not polls_temperature or getattr(message, "main_temperature", None) is not None
            # A zone that will ask *#4*Z*13## (#466) is not complete until the knob arrives.
            # No single frame carries both the temperature and the knob, so a temperature-poll
            # zone keeps polling; a non-temperature-poll zone (MH200N, F453) is complete only
            # when the revealing frame is itself a Dimension 13 report. Match on the dimension,
            # not local_offset: a local-OFF (*13*4##) or protection (*13*5##) knob is a valid
            # Dimension 13 report that handle_event treats as reported, yet OWNd leaves its
            # local_offset None, so a local_offset test would wrongly keep such a zone polling.
            # _local_offset_reported is still False here: _create runs before handle_event.
            if polls_temperature:
                return False
            return getattr(message, "dimension", None) == 13
        return False

    # Switch / binary device
    if getattr(message, "is_on", None) is not None:
        return True

    # General dimension reply
    if getattr(message, "dimension", None) is not None:
        return True

    return False


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
        Creates the entity (or entities - a meter has one per measurement)
        for a :class:`DeviceContext`; ``None`` skips it.
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
    on_scope:
        Optional: called with an area or group frame (``is_area`` /
        ``is_group``) and its :class:`Address`; without it those frames are
        ignored. They address many devices, never discover one.
    yaml_device_id:
        Optional: how the device id of a ``myhome.yaml`` device is formed;
        defaults to :attr:`Address.key` (switches use the clean WHERE).
    registry_address:
        Optional: the :class:`Address` of a registry entry, or ``None`` when
        the entry is not this instance's (platforms serving several WHOs, or
        unique ids carrying a device-class suffix). Default: parse the
        ``{mac}-{who}-{device_id}`` unique id.
    known_keys:
        Optional: every key a created entity is remembered under and
        subscribed to - the spellings a later frame or registry entry may
        use for the same device (``0021`` and ``21``), ``general`` for a
        cover. Default: the device id and address, plus the yaml key and
        bare WHERE for ``myhome.yaml`` devices.
    route_keys:
        Optional: the keys a frame is published under, given ``(message,
        address)`` where ``address`` is ``None`` for frames without one.
        Default: the address key.
    one_per_address:
        A ``myhome.yaml`` address is one device (default): a second entry for
        the same WHERE is skipped. Off for sensors, where a power and an
        energy entry may share a meter address. Aliases (one entry under
        several keys) are always created once.
    route_translations:
        Route command-translation frames (``is_translation``) to known entities
        and scope handlers without discovering new devices from them.
    peer_spellings:
        Optional: other unique ids the same device may have on the primary
        gateway, given this follower's unique id rewritten onto the primary
        (a dry contact an F455 registered as ``301`` is the primary's ``31``).
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
        on_scope: Callable[[Any, Address], None] | None = None,
        general_is_device: bool = False,
        yaml_device_id: Callable[[Address], str] | None = None,
        address: Callable[[Any], Address | None] | None = None,
        key_suffix: str = "",
        registry_address: Callable[[er.RegistryEntry], Address | None] | None = None,
        known_keys: Callable[[DeviceContext], Iterable[str]] | None = None,
        route_keys: Callable[[Any, Address | None], Iterable[str]] | None = None,
        one_per_address: bool = True,
        route_translations: bool = False,
        peer_spellings: Callable[[str], Iterable[str]] | None = None,
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
        self.on_scope = on_scope
        self.general_is_device = general_is_device
        self.address_of = address or Address.from_message
        self.key_suffix = key_suffix
        self.yaml_device_id = yaml_device_id or (lambda address: address.key)
        self.registry_address = registry_address
        self.known_keys = known_keys or default_known_keys
        self.route_keys = route_keys
        self.one_per_address = one_per_address
        self.route_translations = route_translations
        self.peer_spellings = peer_spellings
        self.runtime: MyHOMERuntimeData = config_entry.runtime_data
        # Signals are keyed on the entry's MAC (what the gateway handler publishes under).
        self.mac: str = str(config_entry.data.get(CONF_MAC) or self.runtime.mac)
        self.known = KnownDevices()
        self.router = self.runtime.router
        self.configured: dict[str, Any] = self.runtime.platforms.get(platform, {})
        self.ignored_addresses = IgnoredAddresses.from_config_entry(config_entry)

    def _is_ignored(self, address: Address) -> bool:
        """Whether this address is configured to be ignored or exposed as a virtual device on this gateway."""
        try:
            who_int = int(self.who)
        except (ValueError, TypeError):
            return False
        if self.ignored_addresses.is_ignored(who_int, address.where, address.interface):
            return True
        if self.platform != "climate":
            full_where = (
                f"{address.where}#4#{address.interface}"
                if address.interface
                else address.where
            )
            if is_exposed_on_bus(self.hass, self.config_entry, who_int, full_where):
                return True
        return False

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
            device_id: str | None
            if self.registry_address is not None:
                address_or_none = self.registry_address(entry)
                if address_or_none is None:
                    continue
                address, device_id = address_or_none, None
            else:
                _who, device_id = parse_unique_id(entry.unique_id, str(self.runtime.mac), self.mac)
                address = Address.from_device_id(device_id, self.key_suffix)
            ctx = DeviceContext(
                address=address, who=self.who, source="registry", registry_entry=entry, device_id=device_id,
                cfg=config_for(self.configured, address),
            )
            if self.reject_registry_entry and self.reject_registry_entry(entry, ctx):
                if registry is not None:
                    try:
                        prune_entity(self.hass, registry, entry, self.config_entry.entry_id)
                        LOGGER.info("%s: removed stale registry entry %s", self.platform, entry.entity_id)
                    except Exception as err:  # the entry may already be gone
                        LOGGER.debug("%s: could not remove %s: %s", self.platform, entry.entity_id, err)
                continue

            if not self.general_is_device and not ctx.cfg and is_broadcast_where(address.clean_where):
                LOGGER.info(
                    "%s: Pruned phantom broadcast entity %s (WHO %s WHERE %s)",
                    self.platform,
                    entry.entity_id,
                    self.who,
                    address.key,
                )
                if registry is not None:
                    prune_entity(self.hass, registry, entry, self.config_entry.entry_id)
                continue

            # Ignored addresses: permanently skip discovery, entities and fault tracking
            if self._is_ignored(address):
                LOGGER.info(
                    "%s: Pruned ignored entity %s (WHO %s WHERE %s)",
                    self.platform,
                    entry.entity_id,
                    self.who,
                    address.key,
                )
                if registry is not None:
                    prune_entity(self.hass, registry, entry, self.config_entry.entry_id)
                continue

            # Shared bus: a device the primary already has stays on the primary (#453)
            if registry is not None and self._owned_by_primary(registry, entry.unique_id):
                LOGGER.info(
                    "%s: Pruned duplicate secondary entity %s in favor of primary gateway %s",
                    self.platform,
                    entry.entity_id,
                    getattr(self.runtime, "primary_gateway_mac", None),
                )
                prune_entity(self.hass, registry, entry, self.config_entry.entry_id)
                continue

            if self.accept and not self.accept(ctx):
                continue
            if ctx.key in self.known:
                continue
            entities.extend(self._create(ctx))
        return entities

    # ── myhome.yaml ─────────────────────────────────────────────────────

    def configure(self) -> list[Entity]:
        """Create the ``myhome.yaml`` devices that are not in the registry yet."""
        entities: list[Entity] = []
        seen: set[str] = set()
        seen_configs: set[int] = set()
        for dev_id, cfg in self.configured.items():
            if not isinstance(cfg, dict) or id(cfg) in seen_configs:
                continue  # aliases expose one entry under its key and its WHERE
            seen_configs.add(id(cfg))
            address = Address.from_config(dev_id, cfg)
            if self.key_suffix:
                address = Address(address.where, address.interface, self.key_suffix)
            ctx = DeviceContext(
                address=address, who=str(cfg.get(CONF_WHO, self.who)), cfg=cfg, source="yaml",
                device_id=self.yaml_device_id(address), config_id=str(dev_id),
            )
            if self._is_ignored(address):
                LOGGER.debug(
                    "%s: Skipping ignored YAML device (WHO %s WHERE %s)",
                    self.platform,
                    ctx.who,
                    address.key,
                )
                continue
            if self.one_per_address and (address.clean_key in seen or ctx.key in self.known or dev_id in self.known):
                continue
            if self.accept and not self.accept(ctx):
                continue
            seen.add(address.clean_key)
            created = self._create(ctx)
            if not created:
                continue
            entities.extend(created)
            if self.announce:
                announce_new_device(self.hass, self.mac, ctx.who, address, self._name_of(created[0], address))
        return entities

    # ── shared bus (#453) ───────────────────────────────────────────────

    @property
    def _is_follower(self) -> bool:
        return getattr(self.runtime, "is_follower", False) is True

    def _runtime_whos(self, name: str) -> set[int]:
        whos = getattr(self.runtime, name, None)
        return set(whos) if isinstance(whos, (set, frozenset, list, tuple)) else set()

    def _owned_by_primary(self, registry: er.EntityRegistry, unique_id: str | None) -> bool:
        """Whether this follower's entity already exists on its primary gateway."""
        primary_mac = getattr(self.runtime, "primary_gateway_mac", None)
        if not unique_id or not isinstance(primary_mac, str) or not primary_mac or not self._is_follower:
            return False
        # Unique ids start with the gateway's normalised MAC; the entry's stored MAC may differ in case
        peer = peer_unique_id(unique_id, str(self.runtime.mac), primary_mac)
        peers = [peer, *(self.peer_spellings(peer) if self.peer_spellings else ())] if peer else []
        return any(isinstance(registry.async_get_entity_id(self.platform, DOMAIN, p), str) for p in peers)

    def _discovers(self) -> bool:
        """Whether this gateway creates new entities of this platform's WHO from bus traffic.

        On a shared bus each WHO has one discovering gateway: the follower for the
        WHOs delegated to it, the primary for the rest.
        """
        who = int(self.who) if str(self.who).isdigit() else None
        if who is None:
            return True
        if self._is_follower:
            return who in self._runtime_whos("delegated_whos")
        return who not in self._runtime_whos("delegated_away_whos")

    def _create(self, ctx: DeviceContext) -> list[MyHOMEEntity]:
        """Build the entities of a context and remember every key they answer to."""
        built = self.build(ctx)
        if built is None:
            return []
        created: list[MyHOMEEntity] = list(built) if isinstance(built, (list, tuple)) else [cast(MyHOMEEntity, built)]
        keys = list(self.known_keys(ctx))
        if ctx.source in ("bus", "yaml") and self._is_follower:
            # A delegated WHO: a device the primary found before the delegation stays there.
            # The same for a myhome.yaml device of a follower: restore() has just pruned
            # its registry entry as a duplicate of the primary's, so configure() must not
            # create it again (a live F455 standby re-created all 17 pruned entities at
            # every start, polled them and commanded the bus through them, 2026-11-12).
            registry, _entries = self.registry_entries()
            if registry is not None:
                created = [e for e in created if not self._owned_by_primary(registry, e.unique_id)]
            if not created:
                self.known.add(*keys)  # asked once, not on every frame of that device
                return []
        if not created:
            return []
        if ctx.source == "bus":
            for entity in created:
                if message_has_state(entity, ctx.message):
                    entity._poll_on_add = False
        elif (
            ctx.registry_entry is not None
            and not ctx.registry_entry.has_entity_name
            and ctx.registry_entry.name
        ):
            for entity in created:
                entity._legacy_compat_name = ctx.registry_entry.name
        self.known.add(*keys)
        for entity in created:
            entity.async_on_remove(self.router.subscribe(self.who, keys, entity.handle_event))
        return created

    # ── bus ─────────────────────────────────────────────────────────────

    @callback
    def handle_message(self, message: Any) -> None:
        """Discover from, then route, one frame of this platform's WHO."""
        if self.event_type is not None and not isinstance(message, self.event_type):
            return
        msg_who = getattr(message, "who", getattr(message, "_who", None))
        if isinstance(msg_who, (str, int)) and str(msg_who) != str(self.who):
            return
        is_translation = getattr(message, "is_translation", None) is True
        if is_translation and not self.route_translations:
            return
        where_raw = str(getattr(message, "where", ""))
        is_general_msg = (
            getattr(message, "is_general", False) is True
            or is_broadcast_where(where_raw)
        )
        if not self.general_is_device and is_general_msg:
            if self.on_general:
                self.on_general(message)
            return
        address = self.address_of(message)
        if address is None and self.route_keys is None:
            return
        if address is not None:
            if self._is_ignored(address):
                return
            if getattr(message, "is_group", False) is True or getattr(message, "is_area", False) is True:
                if self.on_scope:
                    self.on_scope(message, address)
                return
            if self.pre_message and self.pre_message(message, address, self.known):
                return
            if not is_translation and address.key not in self.known and self._discovers():
                ctx = DeviceContext(
                    address=address, who=str(getattr(message, "who", self.who)), source="bus", message=message,
                    cfg=config_for(self.configured, address),
                )
                if not self.accept or self.accept(ctx):
                    created = self._create(ctx)
                    if created:
                        # Already subscribed: the frame below is their first state
                        self.async_add_entities(created)
                        if self.announce:
                            announce_new_device(
                                self.hass, self.mac, self.who, address, self._name_of(created[0], address)
                            )
        self.route(message, address)

    @callback
    def route(self, message: Any, address: Address | None) -> None:
        """Deliver a frame to the entities owning its keys."""
        if self.route_keys is not None:
            keys = list(self.route_keys(message, address))
        else:
            keys = [address.key] if address is not None else []
        if keys:
            self.router.publish(self.who, keys, message)

    def listen(self) -> None:
        """Subscribe to the gateway's frames for the life of the config entry."""
        self.config_entry.async_on_unload(
            async_dispatcher_connect(self.hass, f"myhome_message_{self.mac}", self.handle_message)
        )

    # ── the whole cycle ─────────────────────────────────────────────────

    def start(self, *, listen: bool = True, add: bool = True) -> list[Entity]:
        """Restore, configure, add the result (unless the platform adds it), and start discovery."""
        entities = self.restore() + self.configure()
        if entities and add:
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
