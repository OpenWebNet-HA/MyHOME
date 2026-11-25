"""Bidirectional WHO=14 and WHO=2 Dimension 10 actuator lock state tracking."""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, callback
from OWNd.message import (
    OWNActuatorLockCommand,
    OWNActuatorLockEvent,
    area_of_where,
    normalize_where,
)

from .const import CONF_BUS_INTERFACE, CONF_MEMBERS, CONF_WHERE, DOMAIN
from .data import get_runtime_data
from .expose import is_exposed_on_bus
from .where_grammar import canonical_point_spelling, expose_message_where

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler
    from .myhome_device import MyHOMEEntity

__all__ = [
    "AREA_WHERES",
    "ActuatorLockManager",
    "actuator_locks_of",
    "apply_actuator_lock_state",
    "entity_key",
    "is_lockable_entity",
    "is_valid_point_where",
    "normalize_iface",
    "parse_hash_group",
    "point_area",
    "split_point_key",
]

_AREA_WHERES = frozenset({"00", "1", "2", "3", "4", "5", "6", "7", "8", "9", "100"})
AREA_WHERES = _AREA_WHERES
_LOCKABLE_PLATFORMS = frozenset({"light", "switch", "cover"})
_LOCK_SEQ = itertools.count(1)


def _is_ascii_uint(value: str) -> bool:
    """Return True when ``value`` consists solely of ASCII decimal digits."""
    return bool(value) and value.isascii() and value.isdigit()


def _normalize_iface(interface: Any) -> str | None:
    """Normalize an F422 bus interface to two zero-padded digits (``00``..``15``), or ``None``."""
    if interface is None or isinstance(interface, bool):
        return None
    text = str(interface).strip()
    if not _is_ascii_uint(text) or len(text) > 2 or int(text) > 15:
        return None
    return f"{int(text):02d}"


def _is_valid_point_where(pt: str) -> bool:
    """Return True if ``pt`` is a valid canonical WHO=1/2 point-to-point APL address."""
    if not _is_ascii_uint(pt) or pt == "0" or pt in _AREA_WHERES:
        return False
    if len(pt) == 2:
        return True
    if len(pt) == 4 and pt != "0000":
        return int(pt[:2]) <= 10 and int(pt[2:]) <= 15
    return False


def _point_area(pt: str) -> str | None:
    """Return the OpenWebNet area WHERE (``00``, ``1``..``9``, ``100``) for a valid point ``pt``."""
    area = area_of_where(pt)
    if area is not None:
        return area
    if len(pt) == 2:
        return pt[0]
    return "100" if pt[:2] == "10" else str(int(pt[:2]))


def _split_point_key(where: Any, interface: Any = None) -> tuple[str, str | None]:
    """Return ``(canonical_point_where, normalized_iface)``, or ``("", None)`` when invalid."""
    if where is None or isinstance(where, bool) or isinstance(interface, bool):
        return ("", None)
    raw = str(where).strip()
    if raw[:2] in ("1-", "2-"):
        raw = raw[2:]
    if not raw or raw.startswith("#") or "#9" in raw:
        return ("", None)
    base_raw, routing, inline_iface = raw.rpartition("#4#")
    if routing:
        inline_norm = _normalize_iface(inline_iface)
        if inline_norm is None:
            return ("", None)
        if interface is not None and str(interface).strip() != "":
            if _normalize_iface(interface) != inline_norm:
                return ("", None)
        iface: str | None = inline_norm
        point_raw = base_raw.strip()
    else:
        if interface is not None and str(interface).strip() != "":
            iface = _normalize_iface(interface)
            if iface is None:
                return ("", None)
        else:
            iface = None
        point_raw = raw
    if "#" in point_raw:
        return ("", None)
    canon = canonical_point_spelling(normalize_where(point_raw) or point_raw)
    if not _is_valid_point_where(canon):
        return ("", None)
    return (canon, iface)


split_point_key = _split_point_key


def _entity_key(entity: Any) -> tuple[str, str | None]:
    """Return ``(canonical_point_where, normalized_iface)`` for an entity."""
    return _split_point_key(
        getattr(entity, "_where", ""),
        getattr(entity, "_interface", None),
    )


def _parse_hash_group(raw: Any) -> str | None:
    """Parse an explicit ``#G`` (``#1``..``#255``) group WHERE into canonical ``#G``."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text.startswith("#"):
        return None
    body = text[1:]
    if not _is_ascii_uint(body) or not (1 <= int(body) <= 255):
        return None
    return f"#{int(body)}"


entity_key = _entity_key
is_valid_point_where = _is_valid_point_where
normalize_iface = _normalize_iface
parse_hash_group = _parse_hash_group
point_area = _point_area


def _parse_group_ref(raw: Any) -> str | None:
    """Parse a group item (``1``, ``"1"``, ``"#1"``) from a device's ``groups`` list into ``#G``."""
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip()
    if text.startswith("#"):
        text = text[1:]
    if not _is_ascii_uint(text) or not (1 <= int(text) <= 255):
        return None
    return f"#{int(text)}"


def is_lockable_entity(entity: Any) -> bool:
    """Return True if ``entity`` is a point-to-point WHO=1/2 light, switch, or cover."""
    if str(getattr(entity, "_who", "")) not in ("1", "2"):
        return False
    if getattr(entity, "_platform", None) not in _LOCKABLE_PLATFORMS:
        return False
    if getattr(entity, "scope", None) is not None or hasattr(entity, "_member_entity_ids"):
        return False
    return bool(_entity_key(entity)[0])


def apply_actuator_lock_state(entity: Any, locked: bool) -> None:
    """Set ``_actuator_locked`` and ``extra_state_attributes['actuator_locked']`` on ``entity``."""
    lock_state = bool(locked)
    entity._actuator_locked = lock_state
    attrs = getattr(entity, "_attr_extra_state_attributes", None)
    if isinstance(attrs, dict):
        attrs["actuator_locked"] = lock_state
    else:
        entity._attr_extra_state_attributes = {"actuator_locked": lock_state}


def actuator_locks_of(handler: Any) -> ActuatorLockManager | None:
    """Return the active :class:`ActuatorLockManager` of ``handler``, or ``None``."""
    owner_fn = getattr(handler, "actuator_lock_owner", None)
    mgr = owner_fn() if callable(owner_fn) else getattr(handler, "_actuator_locks", None)
    if isinstance(mgr, ActuatorLockManager):
        mgr._sync_secondary_gateways()
        return mgr
    return None


class ActuatorLockManager:
    """Tracks WHO=14 and Dimension 10 actuator lock state across WHO=1 and WHO=2 endpoints."""

    def __init__(self, handler: MyHOMEGatewayHandler) -> None:
        """Initialize the actuator lock manager for a gateway."""
        self.handler = handler
        self._locks: dict[tuple[str, str | None], bool] = {}
        self._lock_seq: dict[tuple[str, str | None], int] = {}
        self._observed_keys: set[tuple[str, str | None]] = set()
        self._entities: dict[tuple[str, str | None], list[MyHOMEEntity]] = {}

    def register(self, entity: MyHOMEEntity) -> CALLBACK_TYPE:
        """Register a WHO=1/2 point actuator entity and apply any cached lock state."""

        @callback
        def _noop() -> None:
            return

        if not is_lockable_entity(entity):
            return _noop

        self._sync_secondary_gateways()
        key = _entity_key(entity)
        if key in self._locks:
            apply_actuator_lock_state(entity, self._locks[key])

        bucket = self._entities.setdefault(key, [])
        if entity not in bucket:
            bucket.append(entity)

        @callback
        def _unsub() -> None:
            self.unregister(entity)

        return _unsub

    @callback
    def unregister(self, entity: MyHOMEEntity) -> None:
        """Remove ``entity`` from this lock manager if currently registered."""
        key = _entity_key(entity)
        ents = self._entities.get(key)
        if ents is not None and entity in ents:
            ents.remove(entity)
            if not ents:
                self._entities.pop(key, None)

    def _bus_config_entries(self) -> list[Any]:
        """Return config entries sharing this manager's physical SCS bus (primary + followers)."""
        entries: list[Any] = []
        seen_entries: set[int] = set()
        own_entry = getattr(self.handler, "config_entry", None)
        if own_entry is not None:
            seen_entries.add(id(own_entry))
            entries.append(own_entry)
        hass = getattr(self.handler, "hass", None)
        my_mac = getattr(self.handler, "mac", None)
        if hass is not None and my_mac and hasattr(hass, "config_entries"):
            for entry in hass.config_entries.async_entries(DOMAIN):
                if id(entry) in seen_entries:
                    continue
                rt = get_runtime_data(entry)
                gw = getattr(rt, "gateway", None) if rt is not None else None
                if (
                    gw is not None
                    and (
                        getattr(gw, "is_follower", False) is True
                        or getattr(gw, "is_secondary", False) is True
                        or getattr(gw, "is_standby", False) is True
                    )
                    and getattr(gw, "primary_gateway_mac", None) == my_mac
                ):
                    seen_entries.add(id(entry))
                    entries.append(entry)
        return entries

    def _copy_locks_to(self, target: ActuatorLockManager) -> None:
        """Copy cached lock states and monotonic sequences to ``target`` without downgrading newer observations."""
        for key, locked in self._locks.items():
            src_obs = key in self._observed_keys
            src_seq = self._lock_seq.get(key, 0)
            dst_obs = key in target._observed_keys and key in target._locks
            dst_seq = target._lock_seq.get(key, 0)
            if (src_obs and (not dst_obs or src_seq >= dst_seq)) or (
                not dst_obs and (key not in target._locks or src_seq >= dst_seq)
            ):
                target._locks[key] = locked
                target._lock_seq[key] = src_seq
                if src_obs:
                    target._observed_keys.add(key)
                else:
                    target._observed_keys.discard(key)

    def _sync_secondary_gateways(self) -> None:
        """Adopt entities and lock states queued on secondary gateways before primary loaded."""
        if getattr(self.handler, "is_follower", False) is True:
            return
        touched_keys: set[tuple[str, str | None]] = set()
        for entry in self._bus_config_entries():
            rt = get_runtime_data(entry)
            gw = getattr(rt, "gateway", None) if rt is not None else None
            if gw is None or gw is self.handler:
                continue
            sec_mgr = getattr(gw, "_actuator_locks", None)
            if not isinstance(sec_mgr, ActuatorLockManager) or (
                not sec_mgr._entities and not sec_mgr._locks
            ):
                continue
            for key, locked in sec_mgr._locks.items():
                sec_obs = key in sec_mgr._observed_keys
                sec_seq = sec_mgr._lock_seq.get(key, 0)
                cur_obs = key in self._observed_keys
                cur_seq = self._lock_seq.get(key, 0)
                if (sec_obs and (not cur_obs or sec_seq > cur_seq)) or (
                    not cur_obs and (key not in self._locks or sec_seq > cur_seq)
                ):
                    self._locks[key] = locked
                    self._lock_seq[key] = sec_seq
                    if sec_obs:
                        self._observed_keys.add(key)
                    touched_keys.add(key)
            for key, ents in list(sec_mgr._entities.items()):
                bucket = self._entities.setdefault(key, [])
                for ent in ents:
                    if ent not in bucket:
                        bucket.append(ent)
                    touched_keys.add(key)
            sec_mgr.clear()

        for key in touched_keys:
            if key not in self._locks:
                continue
            locked = self._locks[key]
            is_obs = key in self._observed_keys
            for ent in list(self._entities.get(key, ())):
                if is_obs:
                    ent.handle_actuator_lock(locked)
                elif getattr(ent, "_actuator_locked", None) is not locked:
                    apply_actuator_lock_state(ent, locked)

    def evacuate_secondary_entities(self) -> None:
        """Re-home still-loaded secondary entities and bus locks before primary unload."""
        copied_mgrs: set[int] = set()
        for entry in self._bus_config_entries():
            rt = get_runtime_data(entry)
            gw = getattr(rt, "gateway", None) if rt is not None else None
            if gw is None or gw is self.handler:
                continue
            sec_mgr = getattr(gw, "_actuator_locks", None)
            if isinstance(sec_mgr, ActuatorLockManager):
                self._copy_locks_to(sec_mgr)
                copied_mgrs.add(id(sec_mgr))
        for key, ents in list(self._entities.items()):
            for ent in list(ents):
                ent_gw = getattr(ent, "_gateway_handler", None)
                if ent_gw is None or ent_gw is self.handler:
                    continue
                sec_mgr = getattr(ent_gw, "_actuator_locks", None)
                if isinstance(sec_mgr, ActuatorLockManager):
                    if id(sec_mgr) not in copied_mgrs:
                        self._copy_locks_to(sec_mgr)
                        copied_mgrs.add(id(sec_mgr))
                    sec_bucket = sec_mgr._entities.setdefault(key, [])
                    if ent not in sec_bucket:
                        sec_bucket.append(ent)

    def _is_key_exposed(self, key: tuple[str, str | None]) -> bool:
        """Return True if ``key`` is an exposed virtual WHO=1 or WHO=2 device on this bus."""
        pt, iface = key
        raw_target = f"{pt}#4#{iface}" if iface is not None else pt
        hass = getattr(self.handler, "hass", None)
        entries = self._bus_config_entries() or [None]
        return any(
            is_exposed_on_bus(hass, entry, 1, raw_target)
            or is_exposed_on_bus(hass, entry, 2, raw_target)
            for entry in entries
        )

    def _apply_observed_key(self, key: tuple[str, str | None], locked: bool) -> None:
        """Record a live bus lock observation on ``key`` and notify registered entities."""
        self._locks[key] = locked
        self._lock_seq[key] = next(_LOCK_SEQ)
        self._observed_keys.add(key)
        for entity in list(self._entities.get(key, ())):
            entity.handle_actuator_lock(locked)

    def _tracked_keys_on_iface(self, iface: str | None) -> list[tuple[str, str | None]]:
        """Return all registered and previously tracked point keys on ``iface``."""
        seen: dict[tuple[str, str | None], None] = {}
        for key in self._locks:
            if key[1] == iface:
                seen[key] = None
        for key in self._entities:
            if key[1] == iface:
                seen[key] = None
        return list(seen)

    def _resolve_group_keys(
        self, group_where: str, iface: str | None
    ) -> list[tuple[str, str | None]]:
        """Resolve member point keys belonging to ``group_where`` (``#G``) on ``iface``."""
        found: dict[tuple[str, str | None], None] = {}

        def _add_key(candidate_key: tuple[str, str | None]) -> None:
            if candidate_key[0] and candidate_key[1] == iface:
                found[candidate_key] = None

        candidates: list[Any] = []
        seen_ids: set[int] = set()

        def _collect_entity(obj: Any) -> None:
            if obj is not None and id(obj) not in seen_ids:
                seen_ids.add(id(obj))
                candidates.append(obj)

        for bucket in self._entities.values():
            for reg_ent in bucket:
                family = getattr(reg_ent, "_family", None)
                for cov in getattr(family, "_covers", ()):
                    _collect_entity(cov)

        for entry in self._bus_config_entries():
            runtime = get_runtime_data(entry)
            if runtime is None:
                continue
            for plat_ents in runtime.entities.values():
                for ent in plat_ents.values():
                    _collect_entity(ent)
            for handlers in getattr(runtime.router, "_handlers", {}).values():
                for cb in handlers:
                    _collect_entity(getattr(cb, "__self__", None))

            for plat_name in _LOCKABLE_PLATFORMS:
                plat_cfgs = runtime.platforms.get(plat_name)
                if not plat_cfgs:
                    continue
                for dev_id, dev_cfg in plat_cfgs.items():
                    cfg_where = str(dev_cfg.get(CONF_WHERE) or dev_id or "").strip()
                    raw_cfg_iface = dev_cfg.get(CONF_BUS_INTERFACE)
                    if raw_cfg_iface is None or str(raw_cfg_iface).strip() == "":
                        raw_cfg_iface = dev_cfg.get("bus_interface")
                    has_explicit_iface = (
                        raw_cfg_iface is not None and str(raw_cfg_iface).strip() != ""
                    )
                    base_where, has_inline_iface, inline_iface = cfg_where.rpartition("#4#")
                    if has_inline_iface:
                        inline_norm = _normalize_iface(inline_iface)
                        if inline_norm is None or (
                            has_explicit_iface and _normalize_iface(raw_cfg_iface) != inline_norm
                        ):
                            continue
                        cfg_iface: str | None = inline_norm
                        clean_where = base_where.strip()
                    elif has_explicit_iface:
                        cfg_iface = _normalize_iface(raw_cfg_iface)
                        if cfg_iface is None:
                            continue
                        clean_where = cfg_where
                    else:
                        cfg_iface = None
                        clean_where = cfg_where
                    if cfg_iface != iface:
                        continue
                    if _parse_hash_group(clean_where) == group_where:
                        for m_raw in dev_cfg.get(CONF_MEMBERS) or ():
                            _add_key(_split_point_key(m_raw, iface))
                    groups_cfg = dev_cfg.get("groups")
                    if isinstance(groups_cfg, (list, tuple, set, frozenset)) and any(
                        _parse_group_ref(g) == group_where for g in groups_cfg
                    ):
                        _add_key(_split_point_key(clean_where, iface))

        for cand in candidates:
            scope = getattr(cand, "scope", None)
            if (
                scope is not None
                and getattr(scope, "kind", None) == "group"
                and _parse_hash_group(scope.where) == group_where
                and _normalize_iface(getattr(scope, "interface", None)) == iface
            ):
                members_fn = getattr(cand, "_members", None)
                if callable(members_fn):
                    for member_ent in members_fn():
                        _add_key(_entity_key(member_ent))
                for m_raw in getattr(scope, "members", ()):
                    _add_key(_split_point_key(m_raw, iface))
            elif (
                hasattr(cand, "_member_entity_ids")
                and _parse_hash_group(getattr(cand, "_where", None)) == group_where
                and _normalize_iface(getattr(cand, "_interface", None)) == iface
            ):
                for m_raw in getattr(cand, "_declared_members", ()):
                    _add_key(_split_point_key(m_raw, iface))

        return list(found)

    @callback
    def observe(self, message: Any) -> bool:
        """Observe an inbound event or ACKed outbound WHO=14 command and update lock states."""
        if not isinstance(message, (OWNActuatorLockEvent, OWNActuatorLockCommand)):
            return False
        self._sync_secondary_gateways()
        if getattr(message, "is_translation", None) is True:
            return False
        what = getattr(message, "what", None)
        if isinstance(what, bool) or what not in (0, 1):
            return False
        locked = what == 0

        raw_full = expose_message_where(message)
        if not raw_full or "#9" in raw_full:
            return False

        base_raw, routing, iface_raw = raw_full.rpartition("#4#")
        if routing:
            iface = _normalize_iface(iface_raw)
            if iface is None:
                return False
            base = base_raw.strip()
        else:
            iface = None
            base = raw_full.strip()

        if not base:
            return False

        if base == "0":
            applied = False
            for key in self._tracked_keys_on_iface(iface):
                if not self._is_key_exposed(key):
                    self._apply_observed_key(key, locked)
                    applied = True
            return applied

        if base in _AREA_WHERES:
            applied = False
            for key in self._tracked_keys_on_iface(iface):
                if _point_area(key[0]) == base and not self._is_key_exposed(key):
                    self._apply_observed_key(key, locked)
                    applied = True
            return applied

        if base.startswith("#"):
            group_where = _parse_hash_group(base)
            if group_where is None:
                return False
            applied = False
            for key in self._resolve_group_keys(group_where, iface):
                if not self._is_key_exposed(key):
                    self._apply_observed_key(key, locked)
                    applied = True
            return applied

        key = _split_point_key(base, iface)
        if not key[0] or self._is_key_exposed(key):
            return False
        self._apply_observed_key(key, locked)
        return True

    @callback
    def set_point_lock(
        self,
        where: Any,
        interface: Any,
        locked: bool,
        *,
        observed: bool = True,
    ) -> None:
        """Set point lock state from live Dimension 10 ``SHUTTER_INFO`` or ``RestoreEntity``."""
        self._sync_secondary_gateways()
        key = _split_point_key(where, interface)
        if not key[0]:
            return
        lock_state = bool(locked)
        if not observed:
            if key in self._observed_keys:
                return
            self._locks[key] = lock_state
            self._lock_seq[key] = next(_LOCK_SEQ)
            for entity in list(self._entities.get(key, ())):
                if getattr(entity, "_actuator_locked", None) is None:
                    apply_actuator_lock_state(entity, lock_state)
            return

        self._apply_observed_key(key, lock_state)

    @callback
    def clear_lock_from_actuation(self, where: Any, interface: Any = None) -> None:
        """Mark an actuator unlocked after real physical actuation is observed on the bus."""
        self._sync_secondary_gateways()
        key = _split_point_key(where, interface)
        if not key[0]:
            return
        self._locks[key] = False
        self._lock_seq[key] = next(_LOCK_SEQ)
        self._observed_keys.add(key)
        for entity in list(self._entities.get(key, ())):
            changed = False
            if getattr(entity, "_actuator_locked", None) is True:
                apply_actuator_lock_state(entity, False)
                changed = True
            if getattr(entity, "_actuation_unconfirmed", False) is True:
                setattr(entity, "_actuation_unconfirmed", False)
                setattr(entity, "_scope_run_active_until", None)
                changed = True
            if changed:
                entity._publish_state()

    def is_locked(self, where: Any, interface: Any = None) -> bool | None:
        """Return the tracked lock state for ``(where, interface)``, or ``None`` if unseen."""
        self._sync_secondary_gateways()
        key = _split_point_key(where, interface)
        if not key[0]:
            return None
        return self._locks.get(key)

    def clear(self) -> None:
        """Clear all cached lock states, observed keys, and registered entities."""
        self._locks.clear()
        self._lock_seq.clear()
        self._observed_keys.clear()
        self._entities.clear()
