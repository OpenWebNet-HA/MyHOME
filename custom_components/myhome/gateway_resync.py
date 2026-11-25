"""Lighting resync manager for MyHOME.

Encapsulates debounced state sweeps following group (#G) or general (WHERE=0)
lighting frames, and manages group echo cancellation.
"""
from __future__ import annotations

import asyncio
import collections
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_MAC
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from OWNd.message import OWNLightingCommand, OWNLightingEvent

from .const import (
    BUS_ROUTING,
    LOGGER,
    RESYNC_DEBOUNCE_S,
    RESYNC_LEADING_WINDOW_S,
    RESYNC_REPLY_GRACE_S,
    RESYNC_STORM_SWEEPS,
    RESYNC_STORM_WINDOW_S,
    area_of_where,
    normalize_where,
)
from .discovery import Address, parse_unique_id
from .ignored import IgnoredAddresses
from .where_grammar import canonical_point_spelling

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler


@dataclass
class _SweepStorm:
    """A scope that was swept `RESYNC_STORM_SWEEPS` times in a row for one state."""

    last_activity: float
    trailing_done: bool = False


class LightingResyncManager:
    """Manages lighting resynchronization sweeps and echo suppression."""

    def __init__(
        self,
        handler: MyHOMEGatewayHandler,
        *,
        resync_timers: dict[str, CALLBACK_TYPE] | None = None,
        resync_group_echoes: dict[str, int] | None = None,
        recent_ptp: collections.deque[tuple[float, str, str | None]] | None = None,
    ) -> None:
        """Initialize the lighting resync manager."""
        self.handler = handler
        self._resync_timers: dict[str, CALLBACK_TYPE] = (
            resync_timers if resync_timers is not None else {}
        )
        self._resync_group_echoes: dict[str, int] = (
            resync_group_echoes if resync_group_echoes is not None else {}
        )
        self._recent_ptp: collections.deque[tuple[float, str, str | None]] = (
            recent_ptp if recent_ptp is not None else collections.deque()
        )
        self._ptp_polarities: dict[tuple[float, str], bool | None] = {}
        self._resync_area_echoes: dict[str, set[str]] = {}
        self._group_members: dict[str, set[str]] = {}
        # Polarity the scope frame commands, or None when it says none (a WHAT 30/31
        # dim step): then both ON and OFF member echoes count towards cancelling.
        self._resync_expected_on: dict[str, bool | None] = {}
        self._resync_echoed_members: dict[str, set[str]] = {}
        # Scope -> (monotonic time its status request was queued, state of the command
        # it reads back), until the request is closed (ACK, NACK, dropped); then
        # scope -> (end of the reply grace, that state).
        self._own_sweeps: dict[str, tuple[float, bool | None]] = {}
        self._reply_guard: dict[str, tuple[float, bool | None]] = {}
        # (scope, commanded state) -> times of its last sweeps / its storm, if any.
        self._sweep_times: dict[tuple[str, bool], collections.deque[float]] = {}
        self._sweep_storms: dict[tuple[str, bool], _SweepStorm] = {}
        # Sweeps running now (a routed sweep may wait in the full command queue):
        # `cancel_all` cancels them so none queues a read after unload.
        self._sweep_tasks: set[asyncio.Task[Any]] = set()

    @property
    def hass(self) -> HomeAssistant:
        """Return HomeAssistant instance."""
        return self.handler.hass

    @property
    def resync_timers(self) -> dict[str, CALLBACK_TYPE]:
        """Return active resync timers dictionary."""
        return self._resync_timers

    @property
    def resync_group_echoes(self) -> dict[str, int]:
        """Return group echoes counter dictionary."""
        return self._resync_group_echoes

    @property
    def group_members(self) -> dict[str, set[str]]:
        """Return registered group members mapping (#G -> set of point WHEREs)."""
        return self._group_members

    @staticmethod
    def _message_interface(message: Any) -> str | None:
        """Extract the F422 bus interface string (e.g. ``"01"``) from a message, if any."""
        iface = getattr(message, "interface", None)
        if isinstance(iface, str) and iface:
            return iface
        params = getattr(message, "where_param", getattr(message, "_where_param", None))
        if isinstance(params, (list, tuple)) and len(params) > 1 and str(params[0]) == "4":
            return str(params[1])
        return None

    @staticmethod
    def _normalize_group_key(group_where: str | int, interface: str | None = None) -> str:
        """Normalize a group address to ``#G`` or ``#G#4#<interface>``."""
        raw = str(group_where).strip()
        body = raw[1:] if raw.startswith("#") else raw
        grp_part, sep, iface_part = body.partition(BUS_ROUTING)
        grp_norm = str(int(grp_part)) if grp_part.isdigit() else grp_part
        iface = iface_part if (sep and iface_part) else (str(interface).strip() if interface else "")
        if iface:
            iface_norm = iface.zfill(2) if iface.isdigit() else iface
            return f"#{grp_norm}{BUS_ROUTING}{iface_norm}"
        return f"#{grp_norm}"

    @staticmethod
    def _normalize_ptp_where(raw_where: str | int | None, interface: str | None = None) -> str:
        """Normalize a point-to-point WHERE while preserving any ``#4#<interface>`` suffix."""
        where_str = str(raw_where).strip()
        base_part, sep, iface_part = where_str.partition(BUS_ROUTING)
        # `0009` is the area-zero point `09` (the one alias the integration renames).
        clean_base = canonical_point_spelling(str(normalize_where(base_part.split("#", 1)[0])))
        iface = iface_part if (sep and iface_part) else (str(interface).strip() if interface else "")
        if iface:
            iface_norm = iface.zfill(2) if iface.isdigit() else iface
            return f"{clean_base}{BUS_ROUTING}{iface_norm}"
        return clean_base

    @staticmethod
    def _point_area(where: str | int | None) -> str | None:
        """Return the area scope WHERE for a point-to-point lighting address.

        Delegates to ``OWNd.area_of_where`` for ``PL >= 1`` addresses and covers
        the real-plant ``PL = 0`` points that ``OWNd`` omits: ``10``..``90``
        (``A = 1..9, PL = 0`` -> ``"1"``..``"9"``; see #402) and ``1000``
        (``A = 10, PL = 00`` -> ``"100"``). Broadcast areas (``"0"``, ``"00"``,
        ``"1"``..``"9"``, ``"100"``) and ``"0000"`` still return ``None``.
        """
        area = area_of_where(where)
        if isinstance(area, str) or where is None:
            return area if isinstance(area, str) else None
        base = str(where).strip().split("#", 1)[0]
        if len(base) == 2 and base[0] in "123456789" and base[1] == "0":
            return base[0]
        if base == "1000":
            return "100"
        return None

    @staticmethod
    def _area_scope(area: str, interface: str | None = None) -> str:
        """Return the sweep scope of an area: ``A``, or ``A#4#<interface>`` behind an F422.

        `<A>` is an area of the gateway's own bus and `<A>#4#<Int>` an area of the bus
        behind interface `Int` (WHO 1 PDF, WHERE table). The same `A` / `PL` exists on
        every bus, so the two are different sets of lights: a status request for `A`
        is not answered by the lights behind the interface, and their echoes say
        nothing about the lights of `A`.
        """
        iface = str(interface).strip() if interface else ""
        if not iface:
            return area
        return f"{area}{BUS_ROUTING}{iface.zfill(2) if iface.isdigit() else iface}"

    def register_group_members(self, group_where: str, members: list[str] | set[str]) -> None:
        """Register the point-to-point member WHERE addresses belonging to a group."""
        key = self._normalize_group_key(group_where)
        grp_iface = key.lstrip("#").partition(BUS_ROUTING)[2] or None
        cleaned = {self._normalize_ptp_where(m, grp_iface) for m in members if str(m).strip()}
        if cleaned:
            self._group_members[key] = cleaned
        else:
            self._group_members.pop(key, None)

    @property
    def _time(self) -> Any:
        from . import gateway as gw_module

        return getattr(gw_module, "time", time)

    @property
    def recent_ptp(self) -> collections.deque[tuple[float, str, str | None]]:
        """Return recent point-to-point frame deque."""
        return self._recent_ptp

    def _prune_recent_ptp(self, now: float) -> None:
        """Prune expired point-to-point echo records older than the leading window."""
        while self._recent_ptp and self._recent_ptp[0][0] < now - RESYNC_LEADING_WINDOW_S:
            old_ts, old_where, _ = self._recent_ptp.popleft()
            self._ptp_polarities.pop((old_ts, old_where), None)

    def handle_ptp_echo(self, message: OWNLightingEvent) -> None:
        """Handle point-to-point lighting message echo cancellation."""
        now = float(self._time.monotonic())
        self._prune_recent_ptp(now)
        interface = self._message_interface(message)
        ptp_where = self._normalize_ptp_where(message.where, interface)
        clean_where = normalize_where(str(message.where).split("#", 1)[0])
        bare_area = self._point_area(clean_where)
        # The area on the echo's own bus: `09#4#01` reports for `00#4#01`, not for `00`.
        area = self._area_scope(bare_area, interface) if bare_area else None
        msg_on = getattr(message, "is_on", None)
        self._recent_ptp.append((now, ptp_where, area))
        self._ptp_polarities[(now, ptp_where)] = msg_on

        if area and area in self._resync_timers:
            expected_area_on = self._resync_expected_on.get(area)
            has_known = bool(self.known_area_entities(area))
            if (
                has_known
                and expected_area_on is not None
                and isinstance(msg_on, bool)
                and msg_on != expected_area_on
            ):
                if area in self._resync_area_echoes:
                    self._resync_area_echoes[area].discard(ptp_where)
            else:
                echoed = self._resync_area_echoes.setdefault(area, set())
                echoed.add(ptp_where)
                if self._area_fully_echoed(area, echoed):
                    LOGGER.debug("%s area %s echoed point status, cancelling sweep", self.handler.log_id, area)
                    self._resync_timers.pop(area)()
                    self._resync_area_echoes.pop(area, None)
                    self._resync_expected_on.pop(area, None)
        for g in [k for k in self._resync_timers if k.startswith("#")]:
            expected_on = self._resync_expected_on.get(g)
            if expected_on is not None and isinstance(msg_on, bool) and msg_on != expected_on:
                if g in self._resync_echoed_members:
                    self._resync_echoed_members[g].discard(ptp_where)
                    self._resync_group_echoes[g] = len(self._resync_echoed_members[g])
                continue
            members = self._group_members.get(g)
            if members is not None:
                if ptp_where not in members:
                    continue
            elif g.lstrip("#").partition(BUS_ROUTING)[2] != ptp_where.partition(BUS_ROUTING)[2]:
                continue
            echoed_grp = self._resync_echoed_members.setdefault(g, set())
            echoed_grp.add(ptp_where)
            self._resync_group_echoes[g] = len(echoed_grp)
            required = len(members) if members else 2
            if self._resync_group_echoes[g] >= required:
                LOGGER.debug("%s group %s saw member echoes, cancelling sweep", self.handler.log_id, g)
                self._resync_timers.pop(g)()
                self._resync_group_echoes.pop(g, None)
                self._resync_echoed_members.pop(g, None)
                self._resync_expected_on.pop(g, None)

    def _known_area_map(self) -> dict[str, set[str]]:
        """Return mapping of area scope to registered light/switch WHERE addresses.

        Keyed per bus: `05` belongs to `00`, `09#4#01` to `00#4#01` (`_area_scope`).
        """
        area_map: dict[str, set[str]] = {}
        # A warm standby or WHO=1-delegated secondary carrying the primary's traffic
        # reads the primary's lights too: entities discovered before delegation stay on
        # the primary's config entry (discovery.py).
        owners: list[Any] = [self.handler]
        if getattr(self.handler, "is_follower", False) is True or getattr(self.handler, "is_standby", False) is True:
            primary = self.handler._get_primary_gateway()
            if primary is not None and primary is not self.handler:
                owners.append(primary)
        registry: er.EntityRegistry | None = None
        seen_entries: set[str] = set()
        handler_ignored = IgnoredAddresses.from_config_entry(getattr(self.handler, "config_entry", None))
        for owner in owners:
            config_entry = getattr(owner, "config_entry", None)
            entry_id = getattr(config_entry, "entry_id", None) if config_entry is not None else None
            if not isinstance(entry_id, str) or entry_id in seen_entries:
                continue
            seen_entries.add(entry_id)
            if registry is None:
                registry = er.async_get(self.hass)
            owner_ignored = (
                handler_ignored
                if owner is self.handler
                else IgnoredAddresses.from_config_entry(config_entry)
            )
            ignored_for_owner = (
                (owner_ignored,) if owner is self.handler else (owner_ignored, handler_ignored)
            )
            # Both identities, as the switch platform's setup parses them: the ids carry
            # the config entry's MAC, which can differ from the live gateway's (else the
            # MAC is read as the WHO and a WHO 4 switch passes for a light).
            entry_data = getattr(config_entry, "data", None)
            raw_entry_mac = entry_data.get(CONF_MAC) if isinstance(entry_data, Mapping) else None
            entry_mac = raw_entry_mac if isinstance(raw_entry_mac, str) else None
            for entry in er.async_entries_for_config_entry(registry, entry_id):
                if entry.domain not in ("light", "switch") or getattr(entry, "disabled", False) is True:
                    continue
                who, key = parse_unique_id(entry.unique_id, str(owner.mac), entry_mac)
                if not key or (who is not None and who.isdigit() and who != "1"):
                    continue
                address = Address.from_device_id(key)
                clean_where = normalize_where(address.clean_where)
                area = self._point_area(clean_where)
                if not area:
                    continue
                ptp_where = self._normalize_ptp_where(clean_where, address.interface)
                if any(
                    ig.is_ignored(1, address.where, address.interface)
                    or ig.is_ignored(1, ptp_where)
                    for ig in ignored_for_owner
                ):
                    continue
                scope = self._area_scope(area, address.interface)
                area_map.setdefault(scope, set()).add(ptp_where)
        return area_map

    def known_light_areas(self) -> list[str]:
        """Return sorted list of configured light/switch area scopes (``A`` / ``A#4#<interface>``)."""
        return sorted(self._known_area_map().keys())

    _known_light_areas = known_light_areas

    def known_area_entities(self, area: str) -> set[str]:
        """Return configured light/switch WHERE addresses in a given area scope."""
        return self._known_area_map().get(area, set())

    def _area_fully_echoed(self, area: str, echoed_wheres: set[str]) -> bool:
        """Return True if all known entities in area have emitted point-to-point echoes.

        When the entity registry has one or more configured entities for ``area``,
        every registered entity in that area must have echoed before the area
        resync query (`*#1*<area>##`, or the point requests of an area behind an
        F422 interface) can be skipped or cancelled. When no
        entities are registered for ``area`` (e.g. unit tests mocking
        ``_known_light_areas`` without populating the entity registry), any
        echo in ``area`` is treated as sufficient.
        """
        if not echoed_wheres:
            return False
        known = self.known_area_entities(area)
        if known:
            return known.issubset(echoed_wheres)
        return True

    def _is_own_sweep_reply(self, where: str, msg_is_on: bool | None, now: float) -> bool:
        """Return True when a scope frame is the answer to this gateway's own status request.

        The reply to `*#1*<where>##` is `*1*<status>*<where>##` on the command and the
        event session (WHO 1 PDF, 3.3.1). For a scope that is, frame for frame, a scope
        command: a physical F461 2.0.11 and F455 1.0.76 answered `*#1*00##` with
        `*1*0*00##` frames between the point statuses, each one armed the next
        `*#1*00##`, and the poll never stopped (live, 2026-11-18).

        So while the request for `where` is queued or being answered, and for
        `RESYNC_REPLY_GRACE_S` after it is closed, a frame for `where` that states the
        ON/OFF of the command being read back is taken as its answer. One that states
        the other is a new command (a group switched on and off again within a second)
        and is swept; its own answers then match it, so the chain ends there. A dim
        step states neither and is never an answer.
        """
        if not isinstance(msg_is_on, bool):
            return False
        from .gateway import PACED_WRITE_TIMEOUT

        own = self._own_sweeps.get(where)
        if own is not None:
            if now - own[0] <= PACED_WRITE_TIMEOUT:
                return own[1] is None or own[1] == msg_is_on
            # The request's future never closed (worker gone): do not stay deaf.
            self._own_sweeps.pop(where, None)
        guard = self._reply_guard.get(where)
        if guard is not None:
            if now <= guard[0]:
                return guard[1] is None or guard[1] == msg_is_on
            self._reply_guard.pop(where, None)
        return False

    def _sweep_answered(self, where: str, started: float, *, arm_guard: bool = True) -> None:
        """Close the reply window of the status request queued at `started`."""
        own = self._own_sweeps.get(where)
        if own is None or own[0] != started:
            return
        self._own_sweeps.pop(where, None)
        if arm_guard:
            self._reply_guard[where] = (float(self._time.monotonic()) + RESYNC_REPLY_GRACE_S, own[1])

    def _sweep_delay(self, where: str, expected_on: bool | None, now: float) -> float | None:
        """Return the debounce for a sweep of `where`, or None when it must not be armed.

        A scope swept `RESYNC_STORM_SWEEPS` times inside `RESYNC_STORM_WINDOW_S` for the
        same commanded state is in a storm: something on the bus keeps repeating the
        scope frame (the answer to another client's status request on a shared bus looks
        like one). It then gets a single sweep once it has been quiet for the window,
        so the last command is still read back, and nothing more until it has been
        quiet for the window again. A dim step (no commanded state) is never held: a
        long press is a train of them and each pause must be read back (#367).
        """
        if expected_on is None:
            return RESYNC_DEBOUNCE_S
        key = (where, expected_on)
        storm = self._sweep_storms.get(key)
        if storm is not None:
            if now - storm.last_activity <= RESYNC_STORM_WINDOW_S:
                storm.last_activity = now
                return None if storm.trailing_done else RESYNC_STORM_WINDOW_S
            self._sweep_storms.pop(key, None)
            self._sweep_times.pop(key, None)
        times = self._sweep_times.get(key)
        while times and times[0] < now - RESYNC_STORM_WINDOW_S:
            times.popleft()
        if times is not None and len(times) >= RESYNC_STORM_SWEEPS:
            self._sweep_storms[key] = _SweepStorm(last_activity=now)
            LOGGER.warning(
                "%s Lighting scope %s was swept %d times in %.0f s for the same command; "
                "holding its status request until the bus has been quiet for %.0f s.",
                self.handler.log_id,
                where,
                RESYNC_STORM_SWEEPS,
                RESYNC_STORM_WINDOW_S,
                RESYNC_STORM_WINDOW_S,
            )
            return RESYNC_STORM_WINDOW_S
        return RESYNC_DEBOUNCE_S

    def _closing(self) -> bool:
        """Whether the gateway's listener or sender is closing: nothing new is sent."""
        return (
            getattr(self.handler, "_terminate_listener", False) is True
            or getattr(self.handler, "_terminate_sender", False) is True
        )

    def schedule_resync(self, message: Any) -> None:
        """Schedule a debounced state resynchronization sweep."""
        if not getattr(self.handler, "broadcast_resync", True) or self._closing():
            return

        dim = getattr(message, "dimension", None)
        is_dimension = isinstance(dim, int) and not isinstance(dim, bool)
        # A WHAT 30/31 dim step moves the members without saying whether they end up
        # on or off (#367): it is swept with an unknown polarity, whatever `is_on`
        # OWNd gives it (True in 2.0.0b11, None once it stops calling a step "on").
        is_dim_step = getattr(message, "what", None) in (30, 31)
        msg_is_on: bool | None = None if is_dim_step else getattr(message, "is_on", None)
        if (msg_is_on is None and not is_dim_step) or is_dimension:
            return

        now = float(self._time.monotonic())
        self._prune_recent_ptp(now)

        def _leading_echoes_for_area(target_area: str) -> set[str]:
            has_known = bool(self.known_area_entities(target_area))
            latest_pol: dict[str, bool | None] = {}
            for t, w, entry_a in self._recent_ptp:
                if entry_a != target_area:
                    continue
                latest_pol[str(w)] = self._ptp_polarities.get((t, str(w)))
            return {
                ptp_w
                for ptp_w, entry_pol in latest_pol.items()
                if not has_known or entry_pol is None or msg_is_on is None or entry_pol == msg_is_on
            }

        targets: list[str] = []
        area_seed_echoes: dict[str, set[str]] = {}
        group_seed_echoes: dict[str, set[str]] = {}
        if getattr(message, "is_group", False):
            group_key = self._normalize_group_key(message.group, self._message_interface(message))
            if self._is_own_sweep_reply(group_key, msg_is_on, now):
                LOGGER.debug("%s group %s frame answers our own status request, not sweeping", self.handler.log_id, group_key)
                return
            members = self._group_members.get(group_key)
            latest_grp_pol: dict[str, bool | None] = {}
            for t, w, _ in self._recent_ptp:
                ptp_w = self._normalize_ptp_where(w)
                if members is not None:
                    if ptp_w not in members:
                        continue
                elif group_key.lstrip("#").partition(BUS_ROUTING)[2] != ptp_w.partition(BUS_ROUTING)[2]:
                    continue
                latest_grp_pol[ptp_w] = self._ptp_polarities.get((t, str(w)))
            matching_wheres = {
                ptp_w
                for ptp_w, entry_pol in latest_grp_pol.items()
                if not isinstance(msg_is_on, bool) or entry_pol is None or entry_pol == msg_is_on
            }
            required = len(members) if members else 2
            if len(matching_wheres) >= required:
                LOGGER.debug(
                    "%s group %s had %d leading member echoes, skipping sweep",
                    self.handler.log_id,
                    group_key,
                    len(matching_wheres),
                )
                return
            group_seed_echoes[group_key] = matching_wheres
            targets.append(group_key)
        elif getattr(message, "is_area", False):
            # The frame's own WHERE, on the frame's own bus: `*1*0*1#4#01##` is area 1
            # behind interface 01, not the gateway's area 1.
            raw_where = self._area_scope(str(message.where), self._message_interface(message))
            if self._is_own_sweep_reply(raw_where, msg_is_on, now):
                LOGGER.debug("%s area %s frame answers our own status request, not sweeping", self.handler.log_id, raw_where)
                return
            leading_echoes = _leading_echoes_for_area(raw_where)
            if self._area_fully_echoed(raw_where, leading_echoes):
                LOGGER.debug("%s area %s had leading member echoes, skipping sweep", self.handler.log_id, raw_where)
                return
            area_seed_echoes[raw_where] = leading_echoes
            targets.append(raw_where)
        elif getattr(message, "is_general", False):
            known_areas = (
                self.handler._known_light_areas()
                if hasattr(self.handler, "_known_light_areas")
                else self.known_light_areas()
            )
            # `0` is the general of the system and `0#4#<Int>` the general of the bus
            # behind interface `Int` (WHO 1 PDF, WHERE table): the first sweeps every
            # known scope, the second only the scopes of that bus.
            general_iface = self._area_scope("", self._message_interface(message))
            for a in known_areas:
                if general_iface and not a.endswith(general_iface):
                    continue
                leading_echoes = _leading_echoes_for_area(a)
                if self._area_fully_echoed(a, leading_echoes):
                    LOGGER.debug("%s general sweep skipping area %s (had leading echoes)", self.handler.log_id, a)
                    continue
                area_seed_echoes[a] = leading_echoes
                targets.append(f"{a}")

        from . import gateway as gw_module

        call_later = getattr(gw_module, "async_call_later", async_call_later)

        for where in targets:
            expected_on = msg_is_on if isinstance(msg_is_on, bool) else None
            delay = self._sweep_delay(where, expected_on, now)
            if delay is None:
                # In a storm, after its one late sweep: a timer still armed for the
                # scope (another commanded state) is left to run.
                LOGGER.debug("%s scope %s is held after repeated sweeps, not sweeping", self.handler.log_id, where)
                continue
            if (
                delay > RESYNC_DEBOUNCE_S
                and where in self._resync_timers
                and self._resync_expected_on.get(where) != expected_on
            ):
                # A storming state's trailing sweep must not displace an already-armed
                # short timer for a different commanded state.
                LOGGER.debug(
                    "%s scope %s is held for %s; keeping armed sweep for %s",
                    self.handler.log_id,
                    where,
                    expected_on,
                    self._resync_expected_on.get(where),
                )
                continue
            if where in self._resync_timers:
                self._resync_timers.pop(where)()
            self._resync_expected_on[where] = expected_on
            if where.startswith("#"):
                seeded = set(group_seed_echoes.get(where, set()))
                self._resync_echoed_members[where] = seeded
                self._resync_group_echoes[where] = len(seeded)
            else:
                self._resync_area_echoes[where] = set(area_seed_echoes.get(where, set()))

            @callback
            def _cb(_now_cb: Any, w: str = where) -> None:
                self.hass.async_create_task(self.handler._resync_broadcast(w))

            self._resync_timers[where] = call_later(self.hass, delay, _cb)

    _schedule_resync = schedule_resync

    async def execute_resync(self, where: str) -> None:
        """Execute the status query for the given area or group."""
        self._resync_timers.pop(where, None)
        self._resync_group_echoes.pop(where, None)
        self._resync_area_echoes.pop(where, None)
        self._resync_echoed_members.pop(where, None)
        expected_on = self._resync_expected_on.pop(where, None)
        if self._closing():
            return
        routed = not where.startswith("#") and BUS_ROUTING in where
        members = sorted(self.known_area_entities(where)) if routed else []
        if routed and not members:
            # Nothing to read: not a sweep, so it does not count towards a storm.
            return

        def _record_sweep(queued_at: float) -> None:
            if isinstance(expected_on, bool):
                key = (where, expected_on)
                self._sweep_times.setdefault(key, collections.deque(maxlen=RESYNC_STORM_SWEEPS)).append(queued_at)
                storm = self._sweep_storms.get(key)
                if storm is not None:
                    storm.trailing_done = True
                    storm.last_activity = queued_at

        task = asyncio.current_task()
        if task is not None:
            self._sweep_tasks.add(task)
        try:
            if routed:
                # An area behind an F422 interface is read light by light:
                # `*#1*<APL>#4#<Int>##` is answered on a physical F461 2.0.11 and F455 1.0.76
                # (`*#1*09#4#01##` -> `*1*0*09#4#01##`, live 2026-11-17). No capture or
                # oracle row holds an area request on an interface (`*#1*<A>#4#<Int>##`).
                recorded = False
                for member in members:
                    if self._closing():
                        return
                    await self.handler.send_status_request(OWNLightingCommand.status(member))
                    if not recorded:
                        _record_sweep(float(self._time.monotonic()))
                        recorded = True
                return
            written = await self.handler.send_status_request(OWNLightingCommand.status(where))
            started = float(self._time.monotonic())
            _record_sweep(started)
            if isinstance(written, asyncio.Future):
                # From here until the request is closed, a frame for this scope that states
                # the command being read back is its answer (`_is_own_sweep_reply`).
                self._own_sweeps[where] = (started, expected_on)
                self._reply_guard.pop(where, None)

                def _answered(fut: asyncio.Future[Any], w: str = where, s: float = started) -> None:
                    arm_guard = (
                        not fut.cancelled()
                        or getattr(fut, "_written_under", None) is not None
                        or getattr(fut, "_gateway_nack", False) is True
                        or getattr(fut, "_command_timeout", False) is True
                    )
                    self._sweep_answered(w, s, arm_guard=arm_guard)

                written.add_done_callback(_answered)
        finally:
            if task is not None:
                self._sweep_tasks.discard(task)

    _execute_resync = execute_resync
    _resync_broadcast = execute_resync

    def cancel_all(self) -> None:
        """Cancel all pending resync timers and clear state."""
        for t in self._resync_timers.values():
            t()
        self._resync_timers.clear()
        current = asyncio.current_task()
        for sweep in self._sweep_tasks:
            if not sweep.done() and sweep is not current:
                sweep.cancel()
        self._sweep_tasks.clear()
        self._resync_group_echoes.clear()
        self._resync_area_echoes.clear()
        self._resync_echoed_members.clear()
        self._resync_expected_on.clear()
        self._recent_ptp.clear()
        self._ptp_polarities.clear()
        self._own_sweeps.clear()
        self._reply_guard.clear()
        self._sweep_times.clear()
        self._sweep_storms.clear()
