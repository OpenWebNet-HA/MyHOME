"""The OpenWebNet WHO 4 (heating) WHERE grammar, in one place.

A heating WHERE is not always a zone. Every time one was read as if it were,
the routing delivered one device's frame to another: actuator ``#N`` of zone Z
(``Z#N``, #333), pump ``0#N`` (#431) and probe ``PZZ`` (#549) each became a
"zone". Code that reads ``message.where`` for routing must go through this
module; ``tests/test_where_usage_guard.py`` fails on any other raw read.

=====================  =================================================
WHERE                  meaning
=====================  =================================================
``1``..``99``          zone
``#0``                 central unit (``#0#N`` on a 4-zone central unit)
``Z#N``                actuator N of zone Z - the frame concerns zone Z
``0#N``                pump N (actuator N of zone 0) - names no zone
``PZZ`` (>= 100)       probe P of zone ZZ - a sensor's frame, no zone's
=====================  =================================================

``*4*4001#Z*0#N##`` (zone Z calls pump N) carries the zone in WHAT.
The optional ``4-`` prefix (legacy ids) and ``#`` prefix are tolerated.
"""

from __future__ import annotations

from typing import Any


def zone_number(where: str) -> str:
    """The zone without a legacy ``4-`` prefix or ``#``: ``"#0"`` -> ``"0"``, ``"4-12"`` -> ``"12"``."""
    return where.split("-")[-1].replace("#", "")


def is_probe(where: str) -> bool:
    """A WHERE of ``PZZ``: probe ``P`` of zone ``ZZ`` (``105``, ``0105``, ``#105``, ``4-105``).

    Only a bare number counts. ``12#1`` is actuator 1 of zone 12, and read
    digit by digit (``121``) it would look like a probe.
    """
    bare = where.split("-")[-1].lstrip("#")
    return bare.isdigit() and int(bare) >= 100


def where_param(message: Any) -> list[str]:
    """The ``#``-separated tail of a frame's WHERE, however the OWNd version names it."""
    return getattr(message, "where_param", None) or getattr(message, "_where_param", None) or []


def is_pump(message: Any) -> bool:
    """WHERE ``0#N``: OWNd reports WHERE ``0`` with the pump number as a parameter."""
    return str(getattr(message, "where", None)) == "0" and bool(where_param(message))
