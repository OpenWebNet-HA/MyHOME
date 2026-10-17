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

import OWNd.message as _ownd_msg
from OWNd.message import (
    OWNLightingCommand,
    OWNLightingEvent,
    OWNMessage,
    OWNSoundEvent,
)

try:
    from OWNd.message import area_of_where, is_apl_address, normalize_where
except ImportError:  # pragma: no cover - PyPI OWNd < 2.0.0b11 compatibility
    def is_apl_address(where: str | int | None) -> bool:
        """Return True if where is a valid WHO=1 Point-to-Point (APL) address."""
        if where is None:
            return False
        base = str(where).strip().split("#", 1)[0]
        if base.startswith("#") or not base.isdigit():
            return False
        if len(base) == 2:
            a, pl = int(base[0]), int(base[1])
            return (1 <= pl <= 9) if a == 0 else (1 <= a <= 9 and 1 <= pl <= 9)
        if len(base) == 4:
            a, pl = int(base[:2]), int(base[2:])
            if a in (0, 10):
                return 1 <= pl <= 15
            if 1 <= a <= 9:
                return 10 <= pl <= 15
        return False

    def area_of_where(where: str | int | None) -> str | None:
        """Return the area status WHERE for a given Point-to-Point (APL) WHERE."""
        if where is None:
            return None
        base = str(where).strip().split("#", 1)[0]
        if not is_apl_address(base):
            return None
        if len(base) == 2:
            return "00" if base[0] == "0" else base[0]
        a = base[:2]
        return "00" if a == "00" else ("100" if a == "10" else str(int(a)))

    def normalize_where(where: str | int | None) -> str:
        """Normalize OpenWebNet address while preserving Point-to-Point (APL) addressing."""
        if where is None:
            return ""
        where_str = str(where).strip()
        if not where_str:
            return ""
        parts = where_str.split("#", 1)
        base = parts[0]
        if is_apl_address(base) or base in ("00", "100", "0"):
            norm_base = base
        elif base.isdigit() and base.startswith("00") and int(base) > 15:
            norm_base = str(int(base))
        else:
            norm_base = base
        return f"{norm_base}#{parts[1]}" if len(parts) > 1 else norm_base

    setattr(_ownd_msg, "is_apl_address", is_apl_address)
    setattr(_ownd_msg, "area_of_where", area_of_where)
    setattr(_ownd_msg, "normalize_where", normalize_where)

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

if not hasattr(OWNLightingEvent, "color_temp_kelvin"):  # pragma: no cover - PyPI OWNd < 2.0.0b11
    def _evt_hs(self: Any) -> tuple[int, int] | None:
        hs = getattr(self, "_hs", None)
        v = getattr(self, "_value", None)
        if (
            isinstance(hs, tuple)
            and len(hs) == 2
            and 0 <= hs[0] <= 360
            and 0 <= hs[1] <= 100
            and (v is None or 0 <= v <= 100)
        ):
            return hs
        return None

    setattr(
        OWNLightingEvent,
        "color_temp_kelvin",
        property(
            lambda self: int(1000000 / self._color_temp)
            if getattr(self, "_color_temp", None) is not None and self._color_temp > 1
            else None
        ),
    )
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

    def _cmd_hs(self: Any) -> tuple[int, int] | None:
        vals = getattr(self, _DIM_VAL_ATTR, [])
        if getattr(self, "_dimension", None) == 12 and len(vals) >= 3:
            try:
                h, s, v = int(vals[0]), int(vals[1]), int(vals[2])
            except (TypeError, ValueError):
                return None
            if 0 <= h <= 360 and 0 <= s <= 100 and 0 <= v <= 100:
                return (h, s)
        return None

    setattr(OWNLightingCommand, "brightness", property(_cmd_brightness))
    setattr(OWNLightingCommand, "color_temp", property(_cmd_color_temp))
    setattr(
        OWNLightingCommand,
        "color_temp_kelvin",
        property(lambda self: int(1000000 / self.color_temp) if self.color_temp is not None else None),
    )
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


def _ensure_ownd_profile_oracle_parity() -> None:  # pragma: no cover - PyPI OWNd < 2.0.0b12
    """Bridge OWNd < 2.0.0b12 profile metadata to match firmware oracle verdicts."""
    try:
        import OWNd.profiles as _ownd_profiles
    except ImportError:
        return

    _no_who3 = tuple(w for w in _ownd_profiles.DEFAULT_SUPPORTED_WHO if w != 3)
    _mh200_who = (1, 2, 3, 4, 6, 8, 15, 16, 17)
    _mh200n_who = (1, 2, 3, 4, 6, 8, 15, 17, 25, 16, 22)

    def _patch_profile_instance(prof: object) -> None:
        model = getattr(prof, "model_name", "")
        if model in ("F453", "F453AV", "F454"):
            object.__setattr__(prof, "auth_measured", True)
            object.__setattr__(prof, "audio_measured", True)
        elif model in ("F461", "MyHomeServer1"):
            object.__setattr__(prof, "supported_who", _no_who3)
        elif model == "MH200":
            object.__setattr__(prof, "supported_who", _mh200_who)
        elif model == "MH200N":
            object.__setattr__(prof, "supported_who", _mh200n_who)

    for _prof in getattr(_ownd_profiles, "_PROFILES", {}).values():
        _patch_profile_instance(_prof)

    for _cls_name in (
        "F453Profile",
        "F453AVProfile",
        "F454Profile",
        "F461Profile",
        "MyHomeServer1Profile",
        "MH200Profile",
        "MH200NProfile",
    ):
        _cls = getattr(_ownd_profiles, _cls_name, None)
        if _cls is not None and not getattr(_cls, "_oracle_patched", False):
            _orig_init = _cls.__init__

            def _make_init(orig: object) -> object:
                def _wrapped_init(self: object, *args: object, **kwargs: object) -> None:
                    orig(self, *args, **kwargs)  # type: ignore[operator]
                    _patch_profile_instance(self)

                return _wrapped_init

            _cls.__init__ = _make_init(_orig_init)
            _cls._oracle_patched = True


_ensure_ownd_profile_oracle_parity()

__all__ = [
    "area_of_where",
    "is_apl_address",
    "is_probe",
    "is_pump",
    "normalize_where",
    "where_param",
    "zone_number",
]


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
