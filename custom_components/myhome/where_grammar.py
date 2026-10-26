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

The pure WHO 1 / WHO 2 point-spelling helpers (``0004`` -> ``04``, #147) live
here too, so every module that stores or compares an address spells it alike.
"""

from __future__ import annotations

import re
from typing import Any

import OWNd.message as _ownd_msg
from OWNd.message import (
    OWNCommand,
    OWNLightingCommand,
    OWNLightingEvent,
    OWNMessage,
    OWNSoundEvent,
    area_of_where,
    is_apl_address,
    normalize_where,
)

for _const_name, _const_val in (
    ("MESSAGE_TYPE_SEASON", "hvac_season"),
    ("SEASON_HEATING", "heating"),
    ("SEASON_CONDITIONING", "conditioning"),
    ("MESSAGE_TYPE_AUTO_UPDATE_INTERVAL", "auto_update_interval"),
):
    if not hasattr(_ownd_msg, _const_name):  # pragma: no cover - PyPI OWNd < 2.0.0b11
        setattr(_ownd_msg, _const_name, _const_val)

_DIM_VAL_ATTR = "_" + "dimension_value"
if not hasattr(OWNMessage, "dimension_value"):  # pragma: no cover - PyPI OWNd < 2.0.0b11
    setattr(OWNMessage, "what", property(lambda self: getattr(self, "_what", None)))
    setattr(OWNMessage, "what_param", property(lambda self: getattr(self, "_what_param", [])))
    setattr(OWNMessage, "where_param", property(lambda self: getattr(self, "_where_param", [])))
    setattr(OWNMessage, "dimension_param", property(lambda self: getattr(self, "_dimension_param", [])))
    setattr(OWNMessage, "dimension_value", property(lambda self: getattr(self, _DIM_VAL_ATTR, [])))

def is_valid_hs_tuple(hs: Any, value: Any = None) -> bool:
    """Return True if hs is a valid OpenWebNet (hue, saturation) tuple within bounds."""
    if not (isinstance(hs, (tuple, list)) and len(hs) == 2):
        return False
    if not (0 <= hs[0] <= 360 and 0 <= hs[1] <= 100):
        return False
    if isinstance(value, (int, float)) and not (0 <= value <= 100):
        return False
    return True


def is_valid_rgb_tuple(rgb: Any, value: Any = None) -> bool:
    """Return True if rgb is a valid OpenWebNet (r, g, b) tuple within bounds."""
    if not (isinstance(rgb, (tuple, list)) and len(rgb) == 3):
        return False
    if not all(0 <= c <= 255 for c in rgb):
        return False
    if isinstance(value, (int, float)) and not (0 <= value <= 100):
        return False
    return True


if not hasattr(OWNLightingEvent, "color_temp_kelvin"):  # pragma: no cover - PyPI OWNd < 2.0.0b11
    setattr(
        OWNLightingEvent,
        "color_temp_kelvin",
        property(
            lambda self: int(1000000 / self._color_temp)
            if getattr(self, "_color_temp", None) is not None and self._color_temp > 1
            else None
        ),
    )

if OWNLightingEvent("*#1*11*12*400*150*200##").hs is not None:  # pragma: no cover - pre-OWNd#128
    def _evt_hs(self: Any) -> tuple[int, int] | None:
        hs = getattr(self, "_hs", None)
        v = getattr(self, "_value", None)
        if is_valid_hs_tuple(hs, v) and hs is not None:
            return (int(hs[0]), int(hs[1]))
        return None

    setattr(OWNLightingEvent, "hs", property(_evt_hs))
    setattr(OWNLightingEvent, "hs_color", property(_evt_hs))

if not hasattr(OWNLightingCommand, "brightness"):  # pragma: no cover - PyPI OWNd < 2.0.0b11
    def _cmd_brightness(self: Any) -> int | None:
        vals = getattr(self, _DIM_VAL_ATTR, [])
        if getattr(self, "_dimension", None) in (1, 4) and vals:
            try:
                raw = int(vals[0])
            except (TypeError, ValueError):
                return None
            if 100 <= raw <= 200:
                return raw - 100
        return None

    def _cmd_color_temp(self: Any) -> int | None:
        vals = getattr(self, _DIM_VAL_ATTR, [])
        if getattr(self, "_dimension", None) == 14 and vals:
            try:
                val = int(vals[0])
            except (TypeError, ValueError):
                return None
            if val > 1:
                return val
        return None

    setattr(OWNLightingCommand, "brightness", property(_cmd_brightness))
    setattr(OWNLightingCommand, "color_temp", property(_cmd_color_temp))
    setattr(
        OWNLightingCommand,
        "color_temp_kelvin",
        property(lambda self: int(1000000 / self.color_temp) if self.color_temp is not None else None),
    )

if getattr(OWNLightingCommand("*#1*11*#12*200*50*150*0##"), "hs", (200, 50)) is not None:  # pragma: no cover - pre-OWNd#128
    def _cmd_hs(self: Any) -> tuple[int, int] | None:
        vals = getattr(self, _DIM_VAL_ATTR, [])
        if getattr(self, "_dimension", None) == 12 and len(vals) >= 3:
            try:
                h, s, v = int(vals[0]), int(vals[1]), int(vals[2])
            except (TypeError, ValueError):
                return None
            if is_valid_hs_tuple((h, s), v):
                return (h, s)
        return None

    setattr(OWNLightingCommand, "hs", property(_cmd_hs))
    setattr(OWNLightingCommand, "hs_color", property(_cmd_hs))

if not hasattr(OWNLightingCommand, "switch_on_timed"):  # pragma: no cover - PyPI OWNd < 2.0.0b11
    _FIXED_TIMER_MAP = {
        "0.5_sec": 18,
        "1_min": 11,
        "2_min": 12,
        "3_min": 13,
        "4_min": 14,
        "5_min": 15,
        "15_min": 16,
        "30_sec": 17,
    }

    def _switch_on_timed(cls: Any, where: str | int, preset: str | int) -> Any:
        what = preset if isinstance(preset, int) else _FIXED_TIMER_MAP.get(preset)
        if what not in (11, 12, 13, 14, 15, 16, 17, 18):
            raise ValueError(f"Unknown WHO=1 fixed timer preset: {preset!r}")
        return cls.parse(f"*1*{what}*{where}##")

    def _set_variable_timer(
        cls: Any, where: str | int, hours: int = 0, minutes: int = 0, seconds: int = 0
    ) -> Any:
        if not (0 <= hours <= 255 and 0 <= minutes <= 59 and 0 <= seconds <= 59):
            raise ValueError("Invalid timer values")
        return cls.parse(f"*#1*{where}*#2*{hours}*{minutes}*{seconds}##")

    setattr(OWNLightingCommand, "switch_on_timed", classmethod(_switch_on_timed))
    setattr(OWNLightingCommand, "set_variable_timer", classmethod(_set_variable_timer))

if not hasattr(OWNSoundEvent, "volume_step_delta"):  # pragma: no cover - PyPI OWNd < 2.0.0b11
    def _sound_step_delta(self: Any) -> int | None:
        raw = getattr(self, "_volume_step_delta", None)
        if isinstance(raw, int):
            return raw
        w = getattr(self, "_what", None)
        wp = getattr(self, "_what_param", [])
        if w == 13 and wp and wp[0].isdigit() and int(wp[0]) > 0:
            return int(wp[0])
        if w == 14 and wp and wp[0].isdigit() and int(wp[0]) > 0:
            return -int(wp[0])
        if isinstance(w, int) and 1001 <= w <= 1015 and not wp:
            return w - 1000
        if isinstance(w, int) and 1101 <= w <= 1115 and not wp:
            return -(w - 1100)
        return None

    setattr(OWNSoundEvent, "volume_step_delta", property(_sound_step_delta))

__all__ = [
    "area_of_where",
    "expose_message_where",
    "is_apl_address",
    "is_probe",
    "is_pump",
    "is_valid_hs_tuple",
    "is_valid_rgb_tuple",
    "normalize_expose_where",
    "normalize_where",
    "parse_thermo_actuator_where",
    "thermo_dim20_is_active",
    "thermo_event_actuator",
    "thermo_event_interface",
    "thermo_event_zone",
    "validate_thermo_trigger_command",
    "where_param",
    "zone_number",
]


def zone_number(where: str) -> str:
    """The zone without a legacy ``4-`` prefix or ``#``: ``"#0"`` -> ``"0"``, ``"4-12"`` -> ``"12"``."""
    return where.removeprefix("4-").replace("#", "")


def is_probe(where: str) -> bool:
    """A WHERE of ``PZZ``: probe ``P`` of zone ``ZZ`` (``105``, ``0105``, ``#105``, ``4-105``).

    Only a bare number counts. ``12#1`` is actuator 1 of zone 12, and read
    digit by digit (``121``) it would look like a probe. ``001``..``099`` is
    not a probe but the group of all probes in zone ``ZZ``.
    """
    bare = where.removeprefix("4-").removeprefix("#")
    return bare.isdigit() and int(bare) >= 100


def where_param(message: Any) -> list[str]:
    """The ``#``-separated tail of a frame's WHERE, however the OWNd version names it."""
    return getattr(message, "where_param", None) or getattr(message, "_where_param", None) or []


def is_pump(message: Any) -> bool:
    """WHERE ``0#N``: OWNd reports WHERE ``0`` with the pump number as a parameter."""
    return str(getattr(message, "where", None)) == "0" and bool(where_param(message))


def canonical_point_spelling(where: str) -> str:
    """``0004`` -> ``04``, silently; anything else as typed.

    The pure rewrite behind :func:`canonical_point_where`, for stored spellings
    (registry ids, entry options) that nobody typed in myhome.yaml.
    """
    if len(where) == 4 and where.isdigit() and where[:3] == "000" and where[3] != "0":
        return f"{where[1]}{where[3]}"
    return where


def canonical_point_address(address: str) -> str:
    """:func:`canonical_point_spelling` of the WHERE before an optional ``#4#II`` suffix."""
    base, sep, rest = address.partition("#")
    return f"{canonical_point_spelling(base)}{sep}{rest}"


def normalize_expose_where(who: int, where: str | int | None) -> str:
    """Return canonical WHERE string for an expose binding on ``who``."""
    raw = str(where if where is not None else "").strip()
    if who == 13 or not raw:
        return ""
    if who == 4:
        clean = raw.removeprefix("4-")
        bare = clean.removeprefix("#")
        if is_probe(bare):
            return bare
        return clean
    if who in (1, 2):
        # OWNd keeps `0004` as typed (OWNd#123); the gateways only accept and
        # report an area-0 point in two digits, so the MyHOME boundary spells
        # it `04` (#147). Silent: the yaml / UI value is validated elsewhere.
        return canonical_point_address(normalize_where(raw) or raw)
    return raw


def expose_message_where(message: Any) -> str:
    """Extract canonical WHERE string from a received OpenWebNet message for expose matching."""
    who = getattr(message, "who", None)
    raw_where = getattr(message, "where", None)
    if who == 13 or raw_where is None:
        return ""
    raw_str = str(raw_where).strip()
    tail = where_param(message)
    full_str = f"{raw_str}#{'#'.join(str(p) for p in tail)}" if tail else raw_str
    return normalize_expose_where(who, full_str) if isinstance(who, int) else full_str


_THERMO_ACTUATOR_WHERE_RE = re.compile(
    r"^(0?[1-9]|[1-9][0-9])(?:#([1-9]))?(?:#4#([0-9]{1,2}))?$"
)


def parse_thermo_actuator_where(
    raw: str | int | None, interface: str | None = None
) -> tuple[str, str, str | None, str | None]:
    """Parse and normalize a WHO=4 thermo actuator WHERE address.

    Accepts ``Z`` (``1``..``99``) or ``Z#N`` (``Z=1..99``, ``N=1..9``), with an
    optional F422 bus routing suffix ``#4#IF`` (``00``..``15``) or ``interface``
    parameter. Rejects central units (``#0`` / ``0``), pumps (``0#N``), and
    temperature probes (``>= 100``).

    Returns ``(normalized_where, zone_str, actuator_or_none, normalized_interface)``.
    """
    if raw is None or isinstance(raw, bool) or isinstance(interface, bool):
        raise ValueError("Invalid WHO=4 thermo actuator WHERE address")
    clean = str(raw).strip().removeprefix("4-")
    match = _THERMO_ACTUATOR_WHERE_RE.fullmatch(clean)
    if match is None:
        raise ValueError(f"Invalid WHO=4 thermo actuator WHERE address: {raw!r}")

    zone_str = str(int(match.group(1)))
    actuator = match.group(2)
    emb_if_raw = match.group(3)
    emb_if: str | None = None
    if emb_if_raw is not None:
        emb_int = int(emb_if_raw)
        if emb_int > 15:
            raise ValueError(f"Invalid F422 bus interface: {emb_if_raw!r}")
        emb_if = f"{emb_int:02d}"

    arg_if: str | None = None
    if interface is not None and str(interface).strip() != "":
        if_clean = str(interface).strip()
        if not if_clean.isdigit() or int(if_clean) > 15:
            raise ValueError(f"Invalid F422 bus interface: {interface!r}")
        arg_if = f"{int(if_clean):02d}"

    if emb_if is not None and arg_if is not None and emb_if != arg_if:
        raise ValueError(
            f"Conflicting F422 bus interface in WHERE ({emb_if}) and interface ({arg_if})"
        )

    norm_if = arg_if if arg_if is not None else emb_if
    norm_where = f"{zone_str}#{actuator}" if actuator is not None else zone_str
    return norm_where, zone_str, actuator, norm_if


def thermo_event_interface(message: Any) -> str | None:
    """Return normalized 2-digit F422 interface from a WHO=4 event, or ``None``."""
    raw_if = getattr(message, "interface", None)
    if not raw_if:
        tail = where_param(message)
        if len(tail) > 1 and tail[0] == "4":
            raw_if = str(tail[1])
        elif len(tail) > 2 and tail[1] == "4":
            raw_if = str(tail[2])
    if not raw_if:
        return None
    if_str = str(raw_if)
    return if_str.zfill(2) if if_str.isdigit() else if_str


_DIM20_ACTIVE_CODES = frozenset({1, 2, 6, 7, 8, 9})
_DIM20_INACTIVE_CODES = frozenset({0, 3, 4, 5, 100})


def thermo_event_zone(message: Any) -> str | None:
    """Return zone ``"1"``..``"99"`` of a WHO=4 event or command, or ``None`` for pumps/probes/central."""
    raw_where = str(getattr(message, "where", None) or "")
    if is_pump(message) or is_probe(raw_where):
        return None
    zone = getattr(message, "zone", None)
    if isinstance(zone, int) and not isinstance(zone, bool) and 1 <= zone <= 99:
        return str(zone)
    bare = raw_where.removeprefix("4-").removeprefix("#")
    if bare.isdigit() and 1 <= int(bare) <= 99:
        return str(int(bare))
    return None


def thermo_event_actuator(message: Any) -> str:
    """Return actuator id ``"0"``..``"9"`` (default ``"1"``) of a WHO=4 Dimension 20 frame."""
    act = getattr(message, "actuator", None)
    if act is not None and str(act).isdigit():
        return str(int(str(act)))
    tail = where_param(message)
    if tail and tail[0] != "4" and str(tail[0]).isdigit():
        return str(int(str(tail[0])))
    return "1"


def thermo_dim20_is_active(message: Any) -> bool | None:
    """Return actuator active state for a WHO=4 Dimension 20 event or ``#20`` write, or ``None``.

    On ``MyHomeServer1`` (``bt_thermo_manager``), toggling a Towel Warmer in Home + Control,
    running a ``BNTR`` Scenario, or reverting an unauthorized write broadcasts a Dimension 20
    *write* frame (``*#4*Z#N*#20*1##`` / ``*#4*Z#N*#20*0##``, parsed by ``OWNd`` as
    :class:`OWNHeatingCommand` with ``dimension == 20`` and non-empty ``dimension_value``)
    while the physical ``F430`` actuator only emits ``*#4*Z#N*20*VAL##`` when polled.
    Dimension 20 status *requests* (``*#4*Z#N*20##``) have an empty ``dimension_value``
    and return ``None``.
    """
    if getattr(message, "dimension", None) != 20:
        return None
    if getattr(message, "message_type", None) == "hvac_action" and callable(
        getattr(message, "is_active", None)
    ):
        return bool(message.is_active())
    if isinstance(message, OWNCommand):
        vals = getattr(message, "dimension_value", None)
        if not isinstance(vals, (list, tuple)) or not vals:
            return None
        first = str(vals[0])
        if not first.isdigit():
            return None
        code = int(first)
        if code in _DIM20_ACTIVE_CODES or 101 <= code <= 200:
            return True
        if code in _DIM20_INACTIVE_CODES:
            return False
    return None

_TRIGGER_COMMAND_RE = re.compile(
    r"^\*(?:[0-9]+\*[0-9#]+\*[0-9#]+|#[0-9]+\*[0-9#]+\*#[0-9]+(?:\*[0-9#]+)+)##$"
)


def validate_thermo_trigger_command(raw: str | None) -> str:
    """Validate an OpenWebNet command frame used to trigger a thermo switch."""
    if not isinstance(raw, str):
        raise ValueError("OpenWebNet command must be a string")
    frame = raw.strip()
    if not frame.startswith("*") or not frame.endswith("##"):
        raise ValueError("OpenWebNet command must start with '*' and end with '##'")
    if frame.startswith("*#*"):
        raise ValueError("ACK/NACK frames cannot be used as trigger commands")
    if frame.startswith("*#") and "*#" not in frame[2:]:
        raise ValueError(
            "OpenWebNet status request cannot be used as a write/trigger command"
        )
    if frame.startswith("*#4*") and "*#22*" in frame:
        raise ValueError(
            "WHO=4 Dimension 22 is a status dimension, not an actuator write command"
        )
    if not _TRIGGER_COMMAND_RE.fullmatch(frame):
        raise ValueError(f"Invalid OpenWebNet command frame: {frame}")
    try:
        parsed = OWNCommand.parse(frame)
    except Exception as err:  # pragma: no cover - defensive against future OWNd changes
        raise ValueError(f"Invalid OpenWebNet command frame: {frame}") from err
    if parsed is None:  # pragma: no cover - defensive against future OWNd changes
        raise ValueError(f"Invalid OpenWebNet command frame: {frame}")
    return frame

