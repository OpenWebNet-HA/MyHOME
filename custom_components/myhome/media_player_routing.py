"""Matrix routing addresses of the WHO=16 sound system.

Amplifier addresses are ``EA`` (environment digit, amplifier digit), and the
F441M routes per environment with a ``1ES`` pseudo address.  These helpers are
pure functions of an address, kept apart from the entities that use them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import callback

from .discovery import Address, KnownDevices


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
