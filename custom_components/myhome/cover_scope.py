"""Support for MyHome scope covers (general, area, group WHEREs)."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from OWNd.message import OWNAutomationEvent

from .const import BUS_ROUTING, area_of_where

if TYPE_CHECKING:
    from .cover import MyHOMECover

_AREA_WHERES = frozenset({"00", "100", *"123456789"})


@dataclass(frozen=True)
class CoverScope:
    """The point-to-point covers a general, area or group WHERE moves.

    WHO_2.pdf §3.0.1: an Up or Down to ``GEN``, ``A`` or ``GR`` ends with a stop
    from every Automation Object it moved ("as many frames as automation
    objects"), never with one for the scope WHERE itself.
    """

    kind: str  # "general", "area" or "group"
    where: str
    interface: str | None = None
    #: Group only: the address keys ``myhome.yaml`` declares as members; a
    #: group's membership is programmed in the actuators, never seen on the bus.
    members: frozenset[str] = frozenset()

    @classmethod
    def of(cls, where: str, interface: str | None = None, members: Iterable[str] = ()) -> CoverScope | None:
        """The scope a WHERE addresses, or ``None`` for a point-to-point address."""
        where = str(where)
        if where == "0":
            return cls("general", where, interface)
        if where in _AREA_WHERES:
            return cls("area", where, interface)
        if where.startswith("#"):
            keys = frozenset(f"{m}{BUS_ROUTING}{interface}" if interface else str(m) for m in members)
            return cls("group", where, interface, keys)
        return None

    def contains(self, cover: MyHOMECover) -> bool:
        """Whether this scope moves ``cover`` (point-to-point covers only)."""
        if cover.scope is not None:
            return False
        if self.kind == "general":
            # as relay_general delivers it; behind an interface, that interface's covers
            return self.interface is None or cover._interface == self.interface
        if self.kind == "area":
            return cover._interface == self.interface and area_of_where(cover._where) == self.where
        key = f"{cover._where}{BUS_ROUTING}{cover._interface}" if cover._interface else cover._where
        return key in self.members


class CoverFamily:
    """The covers of one gateway, so a scope cover can find and follow its members."""

    def __init__(self) -> None:
        self._covers: list[MyHOMECover] = []

    def add(self, cover: MyHOMECover) -> None:
        self._covers.append(cover)
        cover._family = self

        @callback
        def _removed() -> None:
            if cover in self._covers:
                self._covers.remove(cover)
            self.membership_changed()

        cover.async_on_remove(_removed)

    def members(self, scope: CoverScope) -> list[MyHOMECover]:
        return [c for c in self._covers if scope.contains(c)]

    def keys_moved_by(self, where: str, interface: str | None) -> list[str]:
        """Router keys of the covers a scope frame moves: an area's by address, a group's as declared."""
        scopes = [c.scope for c in self._covers if c.scope is not None and (c.scope.where, c.scope.interface) == (where, interface)]
        if (own := CoverScope.of(where, interface)) is not None:
            scopes.append(own)
        return [c._device_id for c in self._covers if any(s.contains(c) for s in scopes)]

    @callback
    def membership_changed(self) -> None:
        """A cover joined or left Home Assistant: every scope cover re-resolves whom it follows."""
        for cover in self._covers:
            if isinstance(cover, MyHOMEScopeCover):
                cover._follow_members()


from .cover import MyHOMECover  # noqa: E402


class MyHOMEScopeCover(MyHOMECover):
    """A cover on a general, area or group WHERE: one command, many actuators.

    Only the actuators report back. At the end of travel each sends its own
    stop and none arrives for the scope WHERE (WHO_2.pdf §3.0.1), so a scope
    cover that waited for one stayed "opening" for good (issue #433). Its state
    follows its members instead, the way core's cover group does: opening or
    closing while any member is, closed when all are, at their mean position.
    A scope with no known members ends a run after its travel time.
    """

    def __init__(self, *args: Any, scope: CoverScope, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.scope = scope
        self._attr_extra_state_attributes["scope"] = scope.kind
        self._run_timeout: CALLBACK_TYPE | None = None
        self._run_timeout_direction: str | None = None
        self._unsub_members: CALLBACK_TYPE | None = None
        # A member can join before this cover is in Home Assistant (no hass yet).
        self._added = False

    def _members(self) -> list[MyHOMECover]:
        return self._family.members(self.scope) if self._family is not None and self.scope is not None else []

    async def async_added_to_hass(self) -> None:
        self._added = True
        await super().async_added_to_hass()  # ends with membership_changed(): follows the members

    @callback
    def _follow_members(self) -> None:
        """Repaint on every state change of the current members, as core's cover group does."""
        if self._unsub_members is not None:
            self._unsub_members()
            self._unsub_members = None
        if not self._added:
            return
        if entity_ids := [m.entity_id for m in self._members() if m.entity_id]:
            self._unsub_members = async_track_state_change_event(self.hass, entity_ids, self._member_changed)
        self._publish_state()

    @callback
    def _member_changed(self, _event: Event[EventStateChangedData]) -> None:
        self._publish_state()

    @property
    def current_cover_position(self) -> int | None:
        if not (members := self._members()):
            return super().current_cover_position
        positions = [p for m in members if (p := m.current_cover_position) is not None]
        return round(sum(positions) / len(positions)) if positions else None

    @property
    def is_opening(self) -> bool:
        if not (members := self._members()):
            return super().is_opening
        return any(m.is_opening for m in members)

    @property
    def is_closing(self) -> bool:
        if not (members := self._members()):
            return super().is_closing
        return any(m.is_closing for m in members)

    @property
    def is_closed(self) -> bool:
        if not (members := self._members()):
            return super().is_closed
        return all(m.is_closed for m in members)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = dict(super().extra_state_attributes or {})
        attrs["members"] = [m.entity_id for m in self._members() if m.entity_id]
        return attrs

    def _fan_out(self) -> list[MyHOMECover]:
        """The members to command one by one: scope commands do not cross an F422.

        Tested on an MH200 with covers 11-19 behind interface 02 (logical
        ``#4#`` addressing): ``*2*2*1##`` was echoed but moved none of them,
        ``*2*1*1#4#02##`` was not even echoed, ``*2*1*11#4#02##`` worked.
        """
        return self._members() if self.scope is not None and self.scope.interface is not None else []

    async def async_open_cover(self, **kwargs: Any) -> None:
        if members := self._fan_out():
            await asyncio.gather(*(m.async_open_cover() for m in members))
            return
        await super().async_open_cover(**kwargs)

    async def async_close_cover(self, **kwargs: Any) -> None:
        if members := self._fan_out():
            await asyncio.gather(*(m.async_close_cover() for m in members))
            return
        await super().async_close_cover(**kwargs)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        if members := self._fan_out():
            await asyncio.gather(*(m.async_stop_cover() for m in members))
            return
        await super().async_stop_cover(**kwargs)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        if members := self._fan_out():
            await asyncio.gather(*(m.async_set_cover_position(**kwargs) for m in members))
            return
        await super().async_set_cover_position(**kwargs)

    @callback
    def handle_event(self, message: OWNAutomationEvent) -> None:
        super().handle_event(message)
        self._watch_run()

    async def _async_move(self, direction: str) -> asyncio.Future[Any] | None:
        written = await super()._async_move(direction)
        self._watch_run()
        return written

    async def async_will_remove_from_hass(self) -> None:
        self._added = False
        self._cancel_run_timeout()
        if self._unsub_members is not None:
            self._unsub_members()
            self._unsub_members = None
        await super().async_will_remove_from_hass()

    @callback
    def _watch_run(self) -> None:
        """Without members nothing reports the end of a run: end it after the travel time."""
        moving = "open" if self._attr_is_opening else "close" if self._attr_is_closing else None
        if moving is None or self.hass is None or self._members():
            self._cancel_run_timeout()
            return
        if self._run_timeout is not None and self._run_timeout_direction == moving:
            return
        self._cancel_run_timeout()
        self._run_timeout_direction = moving
        self._run_timeout = async_call_later(self.hass, self._travel_for(moving == "open"), self._end_run)

    @callback
    def _cancel_run_timeout(self) -> None:
        if self._run_timeout is not None:
            self._run_timeout()
        self._run_timeout = None
        self._run_timeout_direction = None

    @callback
    def _end_run(self, _now: Any) -> None:
        direction = self._run_timeout_direction
        self._run_timeout = None
        self._run_timeout_direction = None
        opening = self._attr_is_opening
        if direction != ("open" if opening else "close" if self._attr_is_closing else None):
            return  # stopped or reversed meanwhile
        self._freeze_position(time.monotonic())
        # A full run by the model; the anchor may trail the timer by the queue delay.
        self._attr_current_cover_position = self._start_position = 100 if opening else 0
        self._attr_is_closed = not opening
        self._publish_state()
