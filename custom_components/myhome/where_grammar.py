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
    "canonical_thermo_zone",
    "central_report_zone",
    "central_unit_season",
    "expose_message_where",
    "is_actuator_where",
    "is_actuator_status",
    "is_apl_address",
    "is_central_thermo_where",
    "is_central_unit_actuator_status",
    "is_central_unit_report",
    "is_probe",
    "is_pump",
    "is_valid_hs_tuple",
    "is_valid_rgb_tuple",
    "normalize_expose_where",
    "normalize_where",
    "parse_thermo_actuator_where",
    "thermo_dim19_status",
    "thermo_dim20_actuator_target",
    "thermo_dim20_is_active",
    "thermo_dim20_level",
    "thermo_dim20_status",
    "thermo_event_actuator",
    "thermo_event_interface",
    "thermo_event_zone",
    "thermo_output_level",
    "validate_standalone_zones",
    "validate_thermo_trigger_command",
    "where_param",
    "zone_number",
]


def is_actuator_where(where: str) -> bool:
    """Whether ``where`` addresses a WHO 4 actuator or split unit rather than a zone or central unit.

    Covers ``Z#N`` / ``Z#0`` / ``0#N`` / ``0#0`` and 3-token ``3#Z#N`` / ``A#B#C`` addresses
    (with or without a trailing ``#4#II`` interface suffix). ``#0#1``..``#0#4`` is a 4-zone
    central unit, not an actuator, and ``Z#4#II`` is a routed zone (note that a 1-digit
    shorthand ``3#4#1``..``3#4#9`` is normalized as zone 3 behind interface ``01``..``09``;
    wire frames always write two-digit ``#4#01``..``#4#15``). Dimensionless ``*#4*Z#N##``
    NACKs on nine gateways, cross-polls local zone ``Z`` on H4684/MH200, and crashes ``coso``
    with ``SIGSEGV`` on MH202 ``010024`` (where ``libthermo.so`` ``device_recognition`` also
    classifies non-``#`` 3-token ``A#B#C`` with ``1 <= B <= 99`` and ``1 <= C <= 9`` as local
    actuator ``B#C``).
    """
    clean = (where or "").strip().removeprefix("4-")
    base, routing, iface = clean.rpartition("#4#")
    if routing and iface.isdigit() and 1 <= len(iface) <= 2:
        clean = base
    if clean in ("#0#1", "#0#2", "#0#3", "#0#4"):
        return False
    parts = clean.removeprefix("#").split("#")
    if len(parts) in (2, 3):
        return all(part.isdigit() for part in parts) and 0 <= int(parts[-1]) <= 9
    return False


def zone_number(where: str) -> str:
    """The zone without a legacy ``4-`` prefix or ``#``: ``"#0"`` -> ``"0"``, ``"4-12"`` -> ``"12"``."""
    return where.removeprefix("4-").replace("#", "")


def canonical_thermo_zone(where: str) -> str | None:
    """Return canonical zone ``"1"``..``"99"`` for a WHO=4 address, or ``None`` for central/pumps/probes."""
    clean = str(where).strip().removeprefix("4-")
    if clean == "#0" or clean.startswith("#0#") or is_probe(clean):
        return None
    head = clean.removeprefix("#").split("#", 1)[0]
    if not head.isdigit():
        return None
    zone_int = int(head)
    if not (1 <= zone_int <= 99):
        return None
    return str(zone_int)


def validate_standalone_zones(raw: Any) -> tuple[list[str], bool]:
    """Parse the "standalone thermostats" entry option: zone numbers 1..99 of the local bus.

    Returns ``(zones, is_valid)``: the zones as ``canonical_thermo_zone`` spells them
    (``"2"``, never ``"02"`` or ``"#2"``), sorted, without duplicates; ``is_valid`` is False
    when an item is not a zone (the central unit ``#0``, a probe ``102``, ``0``, an actuator
    ``2#1``, text). A string is split on commas, spaces and newlines.
    """
    if raw is None:
        return [], True
    if isinstance(raw, str):
        items: list[Any] = [item for item in re.split(r"[\s,;]+", raw) if item]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        items = [item for item in raw if str(item).strip()]
    else:
        return [], False
    zones: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text.isascii() or not text.isdigit() or is_probe(text):
            return [], False
        zone = canonical_thermo_zone(text)
        if zone is None:
            return [], False
        zones.add(zone)
    return sorted(zones, key=int), True


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


def is_central_thermo_where(message: Any) -> bool:
    """Whether a WHO=4 message's WHERE is central-unit addressed (``#0``, ``#Z``, ``#0#N``), excluding probes."""
    raw = str(getattr(message, "where", None) or "").strip().removeprefix("4-")
    head = raw.removeprefix("#").split("#", 1)[0]
    return raw.startswith("#") and head.isdigit() and not is_probe(head)


# Zone operation mode by central unit with its season (WHO 4 p. 65, 1.9.4; p. 9): 110 / 210
# manual, 111 / 211 automatic, 103 / 203 off, 102 / 202 protection. The generic 310 / 311
# (p. 9 lists them on the monitor session too) are also the command text, and an F455
# makes up *4*311*#Z## for a standalone thermostat; the live 3550 reported its zone 4 as
# *4*210*#4##, also in its quarter-hour push, so a central unit is still found.
_CENTRAL_ZONE_STATUS_WHATS = frozenset({"110", "210", "111", "211", "103", "203", "102", "202"})


def is_actuator_status(message: Any) -> bool:
    """Whether a WHO=4 frame is an actuator status: dimension 20 (WHO 4 p. 7, 1.5.8 p. 22, 1.9.1 p. 62).

    ``*#4*WHERE*20##`` and ``*#4*WHERE*20*V##``, and the write form ``*#4*WHERE*#20*V##`` that an
    F461 shows before the status when an actuator switches (``*#4*2#1*#20*1##``, then
    ``*#4*2#1*20*1##``: f461_actuators_live_2026-10-25).
    """
    if getattr(message, "dimension", None) == 20:
        return True
    fields = str(message).removesuffix("##").split("*")
    return len(fields) > 3 and fields[1] == "#4" and fields[3].removeprefix("#") == "20"


def is_central_unit_actuator_status(message: Any) -> bool:
    """An actuator status with WHERE ``#0``: ``*#4*#0*20*V##`` (#944).

    It names no zone and no actuator (OWNd falls back to actuator 1), and the central unit has
    no actuator status of its own: the WHERE of dimension 20 is ``Z#N``, ``Z#0`` or ``0#0``
    (WHO 4 p. 22, p. 62). So it discovers nothing and is delivered to no entity, like the pump
    frames ``0#N``. The same frame from behind an interface (built, no capture holds one:
    ``*#4*#0#4#01*20*1##``, where the WHERE's tail is the interface and not a zone of a
    4-zone central unit) is the same thing.
    """
    where = str(getattr(message, "where", None) or "").strip().removeprefix("4-")
    tail = [str(part) for part in where_param(message)]
    interface = thermo_event_interface(message)
    routed_only = interface is not None and len(tail) == 2 and tail[0] == "4" and tail[1].zfill(2) == interface
    return where == "#0" and (not tail or routed_only) and is_actuator_status(message)


def is_central_unit_report(message: Any) -> bool:
    """Whether a WHO=4 frame shows that the plant has a central unit.

    ``#0`` and ``#0#N`` are the central unit itself (WHO 4 p. 6, p. 64). A ``#Z`` frame counts
    when it is a dimension or a zone status with its season (WHO 4 p. 65). A generic one does
    not: an F455 puts ``*4*311*#2##`` on its event session after every status of a standalone
    thermostat, on a plant with or without a central unit (live, 2026-11-08).

    An actuator status (dimension 20) does not count on any of these WHEREs. The WHERE of an
    actuator status is ``Z#N``, ``Z#0`` or ``0#0`` (WHO 4 p. 22, p. 62), and the one seen with
    WHERE ``#0`` is an answer to the all-zone request that 2.0.0 sent on an F461 and a
    MyHomeServer1 (``*#4*#0*20##``; the published ``*#4*0#0*20##`` since). An F461 2.0.11 on a plant with no central
    unit answered that request with ``*#4*#0*20*1##`` between the actuator frames of its
    zones (#944), and with the central unit taken as present its standalone zones wrote
    ``*#4*#6*#14*0225*3##``, which nothing answered.
    """
    if not is_central_thermo_where(message) or is_actuator_status(message):
        return False
    raw = str(getattr(message, "where", None) or "").strip().removeprefix("4-")
    if raw.removeprefix("#").split("#", 1)[0] == "0":
        return True
    frame = str(message)
    if not frame.startswith("*4*"):
        return True
    what = frame.split("*")[2].split("#", 1)[0]
    return what in _CENTRAL_ZONE_STATUS_WHATS


def central_unit_season(message: Any) -> str | None:
    """The season a central unit's report carries, for the gateway's own bus; else ``None``.

    Its own status (``*4*102*#0##`` heating, ``*4*202*#0##`` conditioning) and its status of one
    of its zones (``*4*110*#3##``, ``*4*203*#4##``): a central unit runs its zones in its own
    season, and its quarter-hour report names only zones, so a gateway that is not asked for
    the central unit's status (an F455) learns the season from those. A frame from behind an
    interface is another bus's central unit. A report with no season (``*4*21*#0##``,
    ``*4*303*#0##``, a dimension) gives ``None``.
    """
    if not is_central_unit_report(message) or thermo_event_interface(message) is not None:
        return None
    season = getattr(message, "season", None)
    return season if isinstance(season, str) else None


def central_report_zone(message: Any) -> str | None:
    """The zone (``"1"``..``"99"``) a central unit's own report is about, else ``None``.

    For a frame :func:`is_central_unit_report` accepts whose WHERE is ``#Z``: ``*4*110*#3##``
    and ``*#4*#3*14*0250*1##`` give ``"3"``. ``#0`` / ``#0#N`` (the central unit itself) and the
    generic ``*4*310*#Z##`` / ``*4*311*#Z##`` give ``None``.
    """
    if not is_central_unit_report(message):
        return None
    return canonical_thermo_zone(str(getattr(message, "where", None) or "").strip())


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
        return None
    if_str = str(raw_if)
    return if_str.zfill(2) if if_str.isdigit() else if_str


_DIM20_ACTIVE_CODES = frozenset({1, 2, 6, 7, 8, 9, 10, 11, 12, 18, 19, 20, 21, 22})
_DIM20_INACTIVE_CODES = frozenset({0, 3, 4, 5, 13, 14, 15, 16, 17, 100})
_FAN_ACTIVE_CODES = frozenset({6, 7, 8, 9})
_FAN_INACTIVE_CODES = frozenset({5, 14, 15, 16})
_FAN_SPEED_BY_CODE: dict[int, int] = {6: 1, 7: 2, 8: 3}


def _decode_thermo_output_code(
    code: int,
) -> tuple[bool, bool | None, int | None] | None:
    """Decode a WHO=4 Dimension 19/20 output code into ``(is_active, fan_on, fan_speed)``.

    Gateway firmware support matrix (5 inspected gateways):
    - Discrete ``0..13``: ``MH200N`` (``010108``), ``F454`` (``020051``), ``MH202``
      (``010024``), ``MyHomeServer1`` (``028206``), and ``F461`` (``020011``) across
      Dimension 19 and Dimension 20 (including ``9`` on Dimension 19).
    - Expanded discrete ``0..22``: ``MyHomeServer1`` (``028206``) and ``F461``
      (``020011``) across Dimension 19, Dimension 20, and ``#20``.
    - Proportional 0-10 V ``100..200`` (``0%..100%``, SCS ``0x80..0xE4`` as ``100 + pct``):
      unsupported on ``MH200N`` (``010108``); emulator-verified on Dimension 20 only on
      ``F459`` (``020105``) and ``MH202`` (``010024``); ``F454`` (``020051``) is
      Dimension 20 only by firmware reading, not observed on the emulator (the emulated
      ``020051`` image emitted nothing on inbound ``$17B7...``, including the
      ``17B40230F9`` control frame); supported on Dimension 19, Dimension 20, and
      ``#20`` on ``MyHomeServer1`` (``028206``) and ``F461`` (``020011``). Short 4-byte
      inbound ``$17B7<DST>3<ACT><VAL>\r`` (2-digit UART PIC) /
      ``$017B7<DST>3<ACT><VAL>\r`` (3-digit RPMsg PIC on ``F460``/``F461``) translates
      to Dimension 20 (``*#4*Z#N*20*...##``) only (``$04B7...`` is an outbound prefix);
      Dimension 19 ``100..200`` on ``MyHomeServer1`` ``028206`` / ``F461`` ``020011``
      comes only from extended 8-byte zone valve replies
      ``D2 <Z> 03 04 13 <HV> <CV> 00`` (``$18D2...`` / ``$018D2...``) in firmware
      disassembly/emulation with no live capture yet. Live ``MyHomeServer1``
      (``2.87.13``) captures cover ``#908`` (``100``) and ``#949`` / ``#952``
      (``100, 120..200``, including the full 221-frame heating ramp on ``F430V10`` /
      ``F459``); ``10..16`` and ``17..22`` are firmware-verified without a live capture
      yet.

    Per-code semantics:
    - ``0, 3, 4``: valve/actuator OFF, Closed, Stop -> ``(False, None, None)``
      (``MyHomeServer1`` ``bt_thermo_manager`` active bitmask marks ``4`` active,
      but ``MyHOME`` keeps ``4`` = 3-point motorized valve Stop inactive so stopped
      valves report ``HVACAction.IDLE``)
    - ``1, 2``: valve/actuator ON, Opened -> ``(True, None, None)``
    - ``5``: fan-coil OFF -> ``(False, False, None)``
    - ``6, 7, 8``: fan-coil ON at speed 1..3 -> ``(True, True, 1..3)``
    - ``9``: fan-coil ON in Auto speed -> ``(True, True, None)``
    - ``10, 11, 12``: active discrete output per ``MyHomeServer1``
      ``bt_thermo_manager`` bitmask (no fan state claimed) -> ``(True, None, None)``
    - ``13, 17``: inactive discrete output per ``MyHomeServer1``
      ``bt_thermo_manager`` bitmask (no fan state claimed) -> ``(False, None, None)``
    - ``14, 15, 16``: extended fan-coil OFF speeds 1..3 (``MyHomeServer1``/``F461``)
      -> ``(False, False, None)``
    - ``18..22``: active discrete output per ``MyHomeServer1``
      ``bt_thermo_manager`` bitmask (no fan state claimed) -> ``(True, None, None)``
    - ``100``: proportional 0-10 V output at 0% (OFF / stopped) -> ``(False, None, None)``
    - ``101..200``: proportional 0-10 V output at 1%..100% (active)
      -> ``(True, None, None)``
    - ``< 0``, ``23..99``, ``> 200``: unassigned -> ``None`` (note: for out-of-table
      SCS bytes on Dimension 19, emulated ``MyHomeServer1`` ``028206`` and ``F461``
      ``020011`` emit an empty field such as ``*#4*1*19*0*##``, for which
      :func:`thermo_dim19_status` returns ``None``)
    """
    if code in _DIM20_ACTIVE_CODES:
        return (
            True,
            True if code in _FAN_ACTIVE_CODES else None,
            _FAN_SPEED_BY_CODE.get(code),
        )
    if 101 <= code <= 200:
        return (True, None, None)
    if code in _DIM20_INACTIVE_CODES:
        return (
            False,
            False if code in _FAN_INACTIVE_CODES else None,
            None,
        )
    return None


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
    return "1"


def _is_ascii_uint(value: str) -> bool:
    """Return True when ``value`` consists solely of ASCII decimal digits."""
    return bool(value) and value.isascii() and value.isdigit()


def thermo_dim20_status(message: Any) -> tuple[bool, bool | None, int | None] | None:
    """Return ``(is_active, fan_on, fan_speed)`` for a WHO=4 Dimension 20 event or ``#20`` write.

    Decodes ``dimension_value[0]`` directly before delegating to ``OWNd`` because:
    - ``#20`` writes (``OWNCommand``) do not expose ``.is_active()``.
    - ``OWNd <= 2.0.0b14`` treats every Dimension 20 value ``>= 6`` (including
      ``100`` = 0% proportional 0-10 V OFF and ``14..16`` = extended fan-coil OFF
      speeds) as ``is_active = True, fan_on = True`` (#908).
    - ``OWNd`` ``b2a1f9ee`` returned ``None`` for ``10..13`` and ``17..22``;
      ``OWNd#178`` (``549da04``) subsequently adopted the same ``MyHomeServer1``
      ``bt_thermo_manager`` active/inactive bitmask (``13, 17`` inactive;
      ``10..12, 18..22`` active, no fan claim) so both decoders agree across all
      codes ``0..999``.

    Falls back to ``message.is_active()`` only for unit-test mocks without a
    ``dimension_value`` sequence.
    """
    who = getattr(message, "who", None)
    if isinstance(who, (int, str)) and str(who) != "4":
        return None
    if getattr(message, "dimension", None) != 20:
        return None
    is_action = getattr(message, "message_type", None) == "hvac_action"
    if not is_action and not isinstance(message, OWNCommand):
        return None
    vals = getattr(message, "dimension_value", None)
    if isinstance(vals, (list, tuple)):
        if not vals:
            return None
        first = str(vals[0])
        if not _is_ascii_uint(first):
            return None
        return _decode_thermo_output_code(int(first))
    if is_action and callable(getattr(message, "is_active", None)):
        raw_fan_on = getattr(message, "fan_on", None)
        raw_speed = getattr(message, "fan_speed", None)
        fan_on = raw_fan_on if isinstance(raw_fan_on, bool) else None
        speed = (
            raw_speed
            if isinstance(raw_speed, int) and not isinstance(raw_speed, bool)
            else None
        )
        return (bool(message.is_active()), fan_on, speed)
    return None


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
    status = thermo_dim20_status(message)
    return status[0] if status is not None else None


def thermo_output_level(code: int) -> int | None:
    """Return ``0..100`` percentage for a WHO=4 proportional 0-10 V code ``100..200``, else ``None``."""
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    if 100 <= code <= 200:
        return code - 100
    return None


def thermo_dim20_level(message: Any) -> int | None:
    """Return ``0..100`` percentage for a WHO=4 Dimension 20 event or ``#20`` write carrying ``100..200``, else ``None``."""
    who = getattr(message, "who", None)
    if isinstance(who, (int, str)) and str(who) != "4":
        return None
    if getattr(message, "dimension", None) != 20:
        return None
    is_action = getattr(message, "message_type", None) == "hvac_action"
    if not is_action and not isinstance(message, OWNCommand):
        return None
    vals = getattr(message, "dimension_value", None)
    if not isinstance(vals, (list, tuple)) or not vals:
        return None
    first = str(vals[0])
    if not _is_ascii_uint(first):
        return None
    return thermo_output_level(int(first))


def thermo_dim20_actuator_target(
    message: Any,
) -> tuple[str, str, str | None] | None:
    """Return ``(zone, actuator, interface)`` for a point WHO=4 Dimension 20 actuator frame.

    Requires an explicit actuator token ``Z#N`` (``1 <= Z <= 99`` and ``1 <= N <= 9``,
    with an optional 2-digit F422 interface ``#4#00``..``#4#15``; ``WHO_4.pdf`` 1.5.8
    p. 22 defines ``Z#N``, ``Z#0``, ``0#0`` for Dimension 20, never bare ``Z``). Rejects
    bare zone addresses (``Z``, ``Z#4#II``), central-unit addresses (``#0``, ``#0#C``,
    ``#Z``), pumps (``0#N``), probes (``>= 100``), all-actuators selectors (``Z#0``),
    out-of-range actuators (``> 9``), 3-token split addresses (``3#Z#N``), and invalid
    interfaces.
    """
    if is_central_thermo_where(message):
        return None
    zone = thermo_event_zone(message)
    if zone is None:
        return None
    raw_tail = [str(p) for p in where_param(message)]
    if len(raw_tail) == 3 and raw_tail[1] == "4":
        base_tail = [raw_tail[0]]
        raw_if: str | None = raw_tail[2]
    else:
        base_tail = raw_tail
        raw_if = None
    if len(base_tail) != 1:
        return None
    iface = thermo_event_interface(message)
    if raw_if is not None and iface is None:
        iface = raw_if.zfill(2) if _is_ascii_uint(raw_if) else raw_if
    if iface is not None and (not _is_ascii_uint(iface) or int(iface) > 15):
        return None
    if not _is_ascii_uint(base_tail[0]):
        return None
    act_int = int(base_tail[0])
    if not (1 <= act_int <= 9):
        return None
    return (zone, str(act_int), f"{int(iface):02d}" if iface is not None else None)


def thermo_dim19_status(
    message: Any,
) -> tuple[bool, bool, bool, bool | None, bool | None, int | None] | None:
    """Return ``(cooling_active, heating_active, zone_active, cooling_fan_on, heating_fan_on, fan_speed)``.

    Decodes WHO=4 Dimension 19 (``*#4*WHERE*19*CV*HV##``) directly via
    :func:`_decode_thermo_output_code` so proportional 0-10 V 0% OFF (``100``) and
    ``MyHomeServer1``/``F461`` extended fan-coil OFF states (``14..16``) are not
    misclassified as active heating/cooling fans by ``OWNd <= 2.0.0b14`` (#908),
    ``10..13`` and ``17..22`` follow the ``MyHomeServer1`` ``bt_thermo_manager``
    bitmask in alignment with ``OWNd#178`` (``549da04``; older ``OWNd``
    ``b2a1f9ee`` returned ``None``), and active fan speed is preserved across both
    the heating (``HV``) and cooling (``CV``) sides. Frames with an empty field
    (such as ``*#4*1*19*0*##``, emitted by emulated ``MyHomeServer1`` ``028206``
    and ``F461`` ``020011`` for out-of-table SCS bytes) stay untyped in ``OWNd``
    and return ``None``. Falls back to ``message.is_active()`` only for
    unit-test mocks without a ``dimension_value`` sequence.
    """
    who = getattr(message, "who", None)
    if isinstance(who, (int, str)) and str(who) != "4":
        return None
    if (
        getattr(message, "dimension", None) != 19
        or getattr(message, "message_type", None) != "hvac_action"
    ):
        return None
    vals = getattr(message, "dimension_value", None)
    if isinstance(vals, (list, tuple)):
        if (
            len(vals) < 2
            or not _is_ascii_uint(str(vals[0]))
            or not _is_ascii_uint(str(vals[1]))
        ):
            return None
        c_dec = _decode_thermo_output_code(int(str(vals[0])))
        h_dec = _decode_thermo_output_code(int(str(vals[1])))
        if c_dec is None or h_dec is None:
            return None
        fan_speed = (
            h_dec[2]
            if h_dec[1] is True
            else c_dec[2]
            if c_dec[1] is True
            else None
        )
        return (
            c_dec[0],
            h_dec[0],
            bool(c_dec[0] or h_dec[0]),
            c_dec[1],
            h_dec[1],
            fan_speed,
        )
    if callable(getattr(message, "is_active", None)):
        raw_cfan = getattr(message, "cooling_fan_on", None)
        raw_hfan = getattr(message, "fan_on", None)
        raw_cspeed = getattr(message, "cooling_fan_speed", None)
        raw_hspeed = getattr(message, "fan_speed", None)
        c_fan = raw_cfan if isinstance(raw_cfan, bool) else None
        h_fan = raw_hfan if isinstance(raw_hfan, bool) else None
        c_speed = (
            raw_cspeed
            if isinstance(raw_cspeed, int) and not isinstance(raw_cspeed, bool)
            else None
        )
        h_speed = (
            raw_hspeed
            if isinstance(raw_hspeed, int) and not isinstance(raw_hspeed, bool)
            else None
        )
        speed = h_speed if h_fan is True else c_speed if c_fan is True else h_speed
        c_act = bool(message.is_cooling())
        h_act = bool(message.is_heating())
        z_act = bool(message.is_active() or c_act or h_act or c_fan or h_fan)
        return (c_act, h_act, z_act, c_fan, h_fan, speed)
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
        # Dimension 22 (split control) is read/write, but every spelling without
        # an actuator token crashes the thermostat daemon; only the published
        # ``*#4*3#Z#N*#22*MOD*SP*VEL*SWING##`` write may be a trigger, and only
        # with an OWNd that knows the safe form (services.is_refused_dimension_22).
        from .services import is_refused_dimension_22, services_has_dimension_22_detector

        if is_refused_dimension_22(frame):
            if services_has_dimension_22_detector():
                detail = "only the split-unit write *#4*3#Z#N*#22*MOD*SP*VEL*SWING## is accepted"
            else:
                detail = (
                    "the installed OWNd (< 2.0.0b12) cannot tell the split-unit write "
                    "*#4*3#Z#N*#22*MOD*SP*VEL*SWING## from the crash forms, so every "
                    "Dimension 22 write is refused"
                )
            raise ValueError(
                "WHO=4 Dimension 22 in this form crashes the gateway's thermostat daemon; "
                + detail
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

