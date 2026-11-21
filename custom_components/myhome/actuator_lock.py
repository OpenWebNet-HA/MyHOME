"""WHO=14 actuator lock state tracking for MyHOME WHO=1 and WHO=2 endpoints."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, callback
from OWNd.message import OWNActuatorLockEvent, area_of_where, normalize_where

from .expose import is_exposed_on_bus
from .where_grammar import canonical_point_spelling, expose_message_where

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler
    from .myhome_device import MyHOMEEntity

_AREA_WHERES = frozenset({"00", "1", "2", "3", "4", "5", "6", "7", "8", "9", "100"})
_IGNORED_PLATFORMS = frozenset({"button", "binary_sensor", "sensor"})


def _normalize_iface(interface: Any) -> str | None:
    """Normalize an F422 bus interface to two zero-padded digits, or None."""
    if interface is None or isinstance(interface, bool):
        return None
    text = str(interface).strip()
    return f"{int(text):02d}" if text.isdigit() else None


def _canonical_point_where(where: Any) -> str:
    """Return the canonical point spelling for a WHO=1/2 WHERE address."""
    raw = str(where).strip() if where is not None else ""
    if not raw:
        return ""
    return canonical_point_spelling(normalize_where(raw) or raw)


def _split_point_key(where: Any, interface: Any = None) -> tuple[str, str | None]:
    """Return ``(canonical_where, normalized_interface)`` for a point address."""
    raw = str(where).strip() if where is not None else ""
    base, routing, inline_iface = raw.rpartition("#4#")
    point_raw = base.strip() if routing else raw
    iface_raw = interface if interface is not None else (inline_iface if routing else None)
    return _canonical_point_where(point_raw), _normalize_iface(iface_raw)


def _entity_key(entity: Any) -> tuple[str, str | None]:
    """Return ``(canonical_where, normalized_interface)`` for a registered entity."""
    return _split_point_key(
        getattr(entity, "_where", ""),
        getattr(entity, "_interface", None),
    )


def apply_actuator_lock_state(entity: Any, locked: bool) -> None:
    """Set ``_actuator_locked`` and ``extra_state_attributes['actuator_locked']`` on an entity."""
    lock_state = bool(locked)
    entity._actuator_locked = lock_state
    attrs = getattr(entity, "_attr_extra_state_attributes", None)
    if isinstance(attrs, dict):
        attrs["actuator_locked"] = lock_state
    else:
        entity._attr_extra_state_attributes = {"actuator_locked": lock_state}


def actuator_locks_of(handler: Any) -> ActuatorLockManager | None:
    """Return the :class:`ActuatorLockManager` of a gateway handler, or ``None``."""
    mgr = getattr(handler, "_actuator_locks", None)
    return mgr if isinstance(mgr, ActuatorLockManager) else None


class ActuatorLockManager:
    """Tracks WHO=14 actuator lock state across WHO=1 and WHO=2 endpoints."""

    def __init__(self, handler: MyHOMEGatewayHandler) -> None:
        """Initialize the actuator lock manager for a gateway."""
        self.handler = handler
        self._states: dict[tuple[str, str | None], bool] = {}
        self._entities: list[MyHOMEEntity] = []

    def register(self, entity: MyHOMEEntity) -> CALLBACK_TYPE:
        """Register a WHO=1/2 actuator entity and apply any cached lock state."""

        @callback
        def _noop() -> None:
            return

        if str(getattr(entity, "_who", "")) not in ("1", "2"):
            return _noop
        if getattr(entity, "_platform", None) in _IGNORED_PLATFORMS:
            return _noop
        if getattr(entity, "scope", None) is not None:
            return _noop
        raw_where = str(getattr(entity, "_where", "") or "").strip()
        if (
            not raw_where
            or raw_where.startswith("#")
            or raw_where == "0"
            or "#9" in raw_where
        ):
            return _noop

        key = _split_point_key(raw_where, getattr(entity, "_interface", None))
        if not key[0] or key[0] == "0" or key[0] in _AREA_WHERES:
            return _noop

        if (state := self._states.get(key)) is not None:
            apply_actuator_lock_state(entity, state)

        if entity not in self._entities:
            self._entities.append(entity)

        @callback
        def _unsub() -> None:
            if entity in self._entities:
                self._entities.remove(entity)

        return _unsub

    def is_locked(self, where: str, interface: str | None = None) -> bool | None:
        """Return the tracked lock state for ``(where, interface)``, or ``None`` if unseen."""
        return self._states.get(_split_point_key(where, interface))

    def clear_lock_from_actuation(
        self, where: str, interface: str | None = None
    ) -> None:
        """Mark an actuator unlocked after real physical actuation is observed on the bus."""
        key = _split_point_key(where, interface)
        if key[0]:
            self._states[key] = False

    @callback
    def observe(self, message: Any) -> None:
        """Observe an incoming OpenWebNet message and update WHO=14 lock states."""
        if not isinstance(message, OWNActuatorLockEvent):
            return
        if getattr(message, "is_translation", False) is True:
            return
        what = getattr(message, "what", None)
        if isinstance(what, bool) or what not in (0, 1):
            return
        locked = what == 0

        raw_full = expose_message_where(message)
        if not raw_full or "#9" in raw_full:
            return

        base_raw, routing, iface_raw = raw_full.rpartition("#4#")
        base = base_raw.strip() if routing else raw_full.strip()
        iface = _normalize_iface(iface_raw) if routing else None
        if not base or base.startswith("#") or (routing and iface is None):
            return

        if base == "0":
            for key in list(self._states):
                if key[1] == iface:
                    self._states[key] = locked
            for entity in list(self._entities):
                ent_key = _entity_key(entity)
                if ent_key[1] == iface:
                    self._states[ent_key] = locked
                    entity.handle_actuator_lock(locked)
            return

        if base in _AREA_WHERES:
            for key in list(self._states):
                if key[1] == iface and area_of_where(key[0]) == base:
                    self._states[key] = locked
            for entity in list(self._entities):
                ent_key = _entity_key(entity)
                if ent_key[1] == iface and area_of_where(ent_key[0]) == base:
                    self._states[ent_key] = locked
                    entity.handle_actuator_lock(locked)
            return

        if not base.isdigit():
            return

        canon = _canonical_point_where(base)
        exposed_where = f"{canon}#4#{iface}" if iface is not None else canon
        hass = getattr(self.handler, "hass", None)
        config_entry = getattr(self.handler, "config_entry", None)
        if is_exposed_on_bus(hass, config_entry, 1, exposed_where) or is_exposed_on_bus(
            hass, config_entry, 2, exposed_where
        ):
            return

        target_key = (canon, iface)
        self._states[target_key] = locked
        for entity in list(self._entities):
            if _entity_key(entity) == target_key:
                entity.handle_actuator_lock(locked)

    def clear(self) -> None:
        """Clear all cached lock states and registered entities."""
        self._states.clear()
        self._entities.clear()
