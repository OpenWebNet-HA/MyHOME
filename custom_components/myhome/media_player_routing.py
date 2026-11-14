"""Matrix routing addresses of the WHO=16 sound system.

Amplifier addresses are ``EA`` (environment digit, amplifier digit), and the
F441M routes per environment with a ``1ES`` pseudo address.  These helpers are
pure functions of an address, kept apart from the entities that use them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback

from .discovery import Address, KnownDevices
from .sound_source import who22_source_id

# WHO=22 WHAT base codes that announce or command an active area-to-source route:
# 1 = Turn On (baseband), 2 = Turn On (high quality),
# 21 = Active Source Notification (baseband), 23 = Active Source Notification (high quality).
_WHO22_ACTIVE_AREA_WHAT_CODES = ("1", "2", "21", "23")


def zone_environment(zone: str) -> str | None:
    """Return the environment (room) an amplifier address belongs to.

    Amplifier addresses are ``EA`` — environment digit followed by the
    amplifier number within that environment (``23`` = environment 2,
    amplifier 3).  The F441M ties environments to its outputs one to one
    (OUT n serves environment n), which is why matrix routing is announced
    per environment, not per amplifier.

    The WHO=16 WHERE table only knows two-digit amplifiers (``01``-``99``),
    and OWNd hands the address through unpadded.  Anything else — the general
    address ``0``, an environment command ``#E`` or a hand-written ``1`` — has
    no environment we can be sure of, so ``None`` is returned rather than a
    guess that could switch the wrong room.
    """
    if len(zone) == 2 and zone.isdigit():
        return zone[0]
    return None


def routing_address(zone: str, source: int) -> str | None:
    """Return the pseudo address that routes ``zone``'s environment to ``source``.

    The address is ``1`` + environment + source: zone ``23`` (environment 2)
    on source 2 gives ``122``, and on source 1 ``121`` — exactly what a wall
    panel puts on the bus when it switches that room's source.

    Returns ``None`` when the zone has no routing address: not a two-digit
    amplifier (see :func:`zone_environment`), or environment 0 (amplifiers
    ``01``-``09``), where ``10S`` is the source device address itself.
    """
    environment = zone_environment(zone)
    if environment is None or environment == "0":
        return None
    return f"1{environment}{source}"


def parse_routing_address(pseudo: str) -> tuple[int, str] | None:
    """Split a ``1ES`` matrix routing address into ``(source, environment)``.

    ``10S`` is not a routing address but a source device (``101``-``109``),
    so environment 0 is excluded.  The source digit is returned as sent, even
    outside S1-S4: the frame is still a routing frame and must not fall
    through to zone discovery as a phantom amplifier ``1ES``.

    Returns ``None`` when ``pseudo`` is not a routing address.
    """
    if len(pseudo) == 3 and pseudo[0] == "1" and pseudo.isdigit() and pseudo[1] != "0":
        return int(pseudo[2]), pseudo[1]
    return None


def route_pseudo_zones(router: Any) -> Callable[[Any, Address, KnownDevices], bool]:
    """Stereo-module pseudo zones (10x-14x) select the source for an environment."""

    @callback
    def handler(message: Any, address: Address, known: KnownDevices) -> bool:
        parsed = parse_routing_address(address.where)
        if parsed is None:
            return False
        _source, environment = parsed
        zones = [
            player_id
            for player_id in known
            if zone_environment(player_id.split("#")[0]) == environment
        ]
        if zones:
            router.publish("16", zones, message)
        return True

    return handler


def parse_who22_routing(message: Any) -> tuple[int, str] | None:
    """Extract ``(source, environment)`` from a WHO=22 area-source routing event.

    Dual-stack gateways (such as the H/L4684 on FW 5.0) sometimes omit or
    mis-translate the WHO=16 ``*16*3*1ES##`` pseudo-zone frame when an
    amplifier wakes up on its stored source, while ``bt_multi`` still emits the
    canonical WHO=22 active-source notification ``*22*23#4#<area>*5#2#<S>##``
    (or ``*22*21#4#<area>*5#2#<S>##`` / ``*22*1#4#<area>*2#<S>##``).

    Only area-scoped active source frames (``<cmd>#4#<area>`` with ``<cmd>`` in
    ``1``, ``2``, ``21``, ``23`` and ``<area>`` in ``1``-``9``) are matched;
    general area ``0`` and inactive notifications (``22#4#<area>``) are ignored.
    """
    if str(getattr(message, "who", "")) != "22":
        return None
    raw_what = getattr(message, "what", None)
    if raw_what is None:
        return None
    what_str = str(raw_what)
    if "#" in what_str:
        what_parts = what_str.split("#")
    else:
        what_param = getattr(message, "what_param", None)
        if what_param is None:
            what_param = getattr(message, "_what_param", None)
        what_parts = [what_str, *(str(p) for p in (what_param or []))]
    if (
        len(what_parts) != 3
        or what_parts[0] not in _WHO22_ACTIVE_AREA_WHAT_CODES
        or what_parts[1] != "4"
        or len(what_parts[2]) != 1
        or not what_parts[2].isdigit()
        or what_parts[2] == "0"
    ):
        return None

    source = who22_source_id(message)
    if source is None:
        return None
    return source, what_parts[2]


def who22_routing_listener(router: Any, known: Iterable[str]) -> Callable[[Any], None]:
    """Forward WHO=22 area-source routing events to the environment's audio zones."""

    @callback
    def listener(message: Any) -> None:
        parsed = parse_who22_routing(message)
        if parsed is None:
            return
        _source, environment = parsed
        zones = [
            player_id
            for player_id in known
            if zone_environment(player_id.split("#")[0]) == environment
        ]
        if zones:
            router.publish("16", zones, message)

    return listener
