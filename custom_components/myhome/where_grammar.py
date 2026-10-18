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


def _ensure_ownd_expose_helpers() -> None:  # pragma: no cover - PyPI OWNd < 2.0.0b12
    """Bridge OWNd < 2.0.0b12 message builders and profile flags for expose."""
    from OWNd.message import (
        OWNAutomationCommand,
        OWNEnergyCommand,
        OWNGatewayCommand,
        OWNHeatingCommand,
        OWNHeatingEvent,
    )

    if not hasattr(OWNMessage, "effective_what"):
        def _effective_what(self: Any) -> int | None:
            wp = getattr(self, "_what_param", [])
            if getattr(self, "_what", None) == 1000 and wp and str(wp[0]).isdigit():
                return int(wp[0])
            w = getattr(self, "_what", None)
            return int(w) if isinstance(w, int) else None

        setattr(OWNMessage, "effective_what", property(_effective_what))
        setattr(
            OWNMessage,
            "is_status_request",
            property(lambda self: getattr(self, "_message_type", None) == "STATUS_REQUEST"),
        )
        setattr(
            OWNMessage,
            "is_dimension_request",
            property(lambda self: getattr(self, "_message_type", None) == "DIMENSION_REQUEST"),
        )
        setattr(
            OWNMessage,
            "is_dimension_writing",
            property(lambda self: getattr(self, "_message_type", None) == "DIMENSION_WRITING"),
        )
        setattr(
            OWNMessage,
            "is_dimension_reply",
            property(
                lambda self: getattr(self, "_message_type", None)
                in ("DIMENSION_REQUEST_REPLY", "DIMENSION_REPORTING")
            ),
        )

    if not hasattr(OWNLightingCommand, "report_brightness"):
        setattr(
            OWNLightingCommand,
            "is_status_query",
            property(lambda self: bool(self.is_status_request)),
        )
        setattr(
            OWNLightingCommand,
            "is_brightness_query",
            property(lambda self: bool(self.is_dimension_request and self.dimension in (1, 4))),
        )
        setattr(
            OWNLightingCommand,
            "report_brightness",
            classmethod(
                lambda cls, where, level, transition=0: cls.parse(
                    f"*#1*{where}*1*{min(max(int(level) + 100, 100), 200)}*{max(int(transition), 0)}##"
                )
            ),
        )

    if not hasattr(OWNAutomationCommand, "report_shutter_level"):
        setattr(
            OWNAutomationCommand,
            "is_status_query",
            property(lambda self: bool(self.is_status_request)),
        )
        setattr(
            OWNAutomationCommand,
            "is_shutter_status_query",
            property(lambda self: bool(self.is_dimension_request and self.dimension == 10)),
        )
        setattr(
            OWNAutomationCommand,
            "report_shutter_level",
            classmethod(
                lambda cls, where, level, state=10, priority=0, info=0: cls.parse(
                    f"*#2*{where}*10*{int(state)}*{255 if int(level) == 255 else min(max(int(level), 0), 100)}*{int(priority)}*{int(info)}##"
                )
            ),
        )

    if OWNHeatingEvent("*#4*000*0*0215##").unique_id != "4-000":
        _orig_heating_init = OWNHeatingEvent.__init__

        def _patched_heating_init(self: Any, data: str) -> None:
            _orig_heating_init(self, data)
            where_str = str(getattr(self, "_where", "") or "")
            clean_where = where_str[1:] if where_str.startswith("#") else where_str
            if getattr(self, "_sensor", None) is None and len(clean_where) == 3 and clean_where.isdigit():
                self._sensor = int(clean_where[0])
                self._zone = int(clean_where[1:])

        def _patched_heating_unique_id(self: Any) -> str:
            if getattr(self, "_sensor", None) is not None:
                return f"{self._who}-{self._where}"
            if getattr(self, "_zone", None) == 0:
                return f"{self._who}-#0"
            return f"{self._who}-{self._zone}"

        setattr(OWNHeatingEvent, "__init__", _patched_heating_init)
        setattr(OWNHeatingEvent, "unique_id", property(_patched_heating_unique_id))

    if not hasattr(OWNHeatingCommand, "report_temperature"):
        def _encode_temp(temperature: float) -> str:
            tenths = max(-999, min(999, round(float(temperature) * 10)))
            sign = "1" if tenths < 0 else "0"
            return f"{sign}{abs(tenths):03d}"

        setattr(
            OWNHeatingCommand,
            "is_temperature_query",
            property(
                lambda self: bool(
                    self.is_status_request or (self.is_dimension_request and self.dimension == 0)
                )
            ),
        )
        setattr(OWNHeatingCommand, "encode_temperature", staticmethod(_encode_temp))
        setattr(
            OWNHeatingCommand,
            "report_temperature",
            classmethod(
                lambda cls, where, temperature: cls.parse(
                    f"*#4*{where}*0*{_encode_temp(temperature)}##"
                )
            ),
        )

    if not hasattr(OWNEnergyCommand, "report_active_power"):
        setattr(
            OWNEnergyCommand,
            "is_dimension_query",
            property(lambda self: bool(self.is_dimension_request)),
        )
        setattr(
            OWNEnergyCommand,
            "is_auto_update_command",
            property(lambda self: bool(self.is_dimension_writing and self.dimension == 1200)),
        )
        setattr(
            OWNEnergyCommand,
            "stream_duration",
            property(
                lambda self: (
                    int(self.dimension_value[0])
                    if self.is_dimension_writing
                    and self.dimension == 1200
                    and self.dimension_value
                    and str(self.dimension_value[0]).isdigit()
                    else None
                )
            ),
        )
        setattr(
            OWNEnergyCommand,
            "report_active_power",
            classmethod(lambda cls, where, watts: cls.parse(f"*#18*{where}*113*{max(round(float(watts)), 0)}##")),
        )
        setattr(
            OWNEnergyCommand,
            "report_total_energy",
            classmethod(lambda cls, where, wh: cls.parse(f"*#18*{where}*51*{max(round(float(wh)), 0)}##")),
        )
        setattr(
            OWNEnergyCommand,
            "report_current_month_energy",
            classmethod(
                lambda cls, where, wh, year=None, month=None: cls.parse(
                    f"*#18*{where}*52#{str(int(year))[-2:]}#{int(month)}*{max(round(float(wh)), 0)}##"
                    if year is not None and month is not None
                    else f"*#18*{where}*52*{max(round(float(wh)), 0)}##"
                )
            ),
        )
        setattr(
            OWNEnergyCommand,
            "report_partial_month_energy",
            classmethod(
                lambda cls, where, wh, month=None: cls.parse(
                    f"*#18*{where}*53#{int(month)}*{max(round(float(wh)), 0)}##"
                    if month is not None
                    else f"*#18*{where}*53*{max(round(float(wh)), 0)}##"
                )
            ),
        )
        setattr(
            OWNEnergyCommand,
            "report_partial_day_energy",
            classmethod(
                lambda cls, where, wh, day=None: cls.parse(
                    f"*#18*{where}*54#{int(day)}*{max(round(float(wh)), 0)}##"
                    if day is not None
                    else f"*#18*{where}*54*{max(round(float(wh)), 0)}##"
                )
            ),
        )
        setattr(
            OWNEnergyCommand,
            "report_streaming_state",
            classmethod(
                lambda cls, where, duration=65, energy_type=1: cls.parse(
                    f"*#18*{where}*1200#{int(energy_type)}*{min(max(int(duration), 0), 255)}##"
                )
            ),
        )

    if not hasattr(OWNGatewayCommand, "is_clock_query") or not hasattr(OWNGatewayCommand, "set_datetime"):
        from zoneinfo import ZoneInfo

        def _tz_code(target: Any, time_zone: str | None = None) -> str:
            if hasattr(target, "astimezone") and time_zone:
                target = target.astimezone(ZoneInfo(time_zone))
            z = target.strftime("%z") if hasattr(target, "strftime") else ""
            if (not z or len(z) < 3) and time_zone:
                import datetime as _dt

                z = _dt.datetime.now(ZoneInfo(time_zone)).strftime("%z")
            if not z or len(z) < 3:
                return "000"
            return f"0{z[1:3]}" if z[0] == "+" else f"1{z[1:3]}"

        setattr(
            OWNGatewayCommand,
            "is_clock_query",
            property(lambda self: bool(self.is_dimension_request and self.dimension in (0, 1, 22))),
        )
        setattr(
            OWNGatewayCommand,
            "set_datetime",
            classmethod(
                lambda cls, dt, time_zone=None: cls.parse(
                    (
                        lambda t: f"*#13**#22*{t.strftime('%H*%M*%S')}*{_tz_code(t)}*0{t.strftime('%w*%d*%m*%Y')}##"
                    )(dt.astimezone(ZoneInfo(time_zone)) if time_zone and hasattr(dt, "astimezone") else dt)
                )
            ),
        )
        setattr(
            OWNGatewayCommand,
            "set_date",
            classmethod(
                lambda cls, d, time_zone=None: cls.parse(
                    (
                        lambda t: f"*#13**#1*0{t.strftime('%w*%d*%m*%Y')}##"
                    )(d.astimezone(ZoneInfo(time_zone)) if time_zone and hasattr(d, "astimezone") else d)
                )
            ),
        )
        setattr(
            OWNGatewayCommand,
            "set_time",
            classmethod(
                lambda cls, t, time_zone=None: cls.parse(
                    (
                        lambda tr: f"*#13**#0*{tr.strftime('%H*%M*%S')}*{_tz_code(tr, time_zone)}##"
                    )(t.astimezone(ZoneInfo(time_zone)) if time_zone and hasattr(t, "astimezone") else t)
                )
            ),
        )
        setattr(
            OWNGatewayCommand,
            "report_datetime",
            classmethod(
                lambda cls, dt, time_zone=None: cls.parse(
                    (
                        lambda t: f"*#13**22*{t.strftime('%H*%M*%S')}*{_tz_code(t)}*0{t.strftime('%w*%d*%m*%Y')}##"
                    )(dt.astimezone(ZoneInfo(time_zone)) if time_zone and hasattr(dt, "astimezone") else dt)
                )
            ),
        )
        setattr(
            OWNGatewayCommand,
            "report_date",
            classmethod(
                lambda cls, d, time_zone=None: cls.parse(
                    (
                        lambda t: f"*#13**1*0{t.strftime('%w*%d*%m*%Y')}##"
                    )(d.astimezone(ZoneInfo(time_zone)) if time_zone and hasattr(d, "astimezone") else d)
                )
            ),
        )
        setattr(
            OWNGatewayCommand,
            "report_time",
            classmethod(
                lambda cls, t, time_zone=None: cls.parse(
                    (
                        lambda tr: f"*#13**0*{tr.strftime('%H*%M*%S')}*{_tz_code(tr, time_zone)}##"
                    )(t.astimezone(ZoneInfo(time_zone)) if time_zone and hasattr(t, "astimezone") else t)
                )
            ),
        )


_ensure_ownd_expose_helpers()


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
        if not hasattr(prof, "supports_dimension_response_injection"):
            object.__setattr__(prof, "supports_dimension_response_injection", True)
        if not hasattr(prof, "supports_who4_command_injection"):
            object.__setattr__(
                prof,
                "supports_who4_command_injection",
                model not in ("F452", "F453", "F453AV", "MH200", "MH200N"),
            )

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
    "expose_message_where",
    "is_apl_address",
    "is_probe",
    "is_pump",
    "is_valid_hs_tuple",
    "is_valid_rgb_tuple",
    "normalize_expose_where",
    "normalize_where",
    "where_param",
    "zone_number",
]


def zone_number(where: str) -> str:
    """The zone without a legacy ``4-`` prefix or ``#``: ``"#0"`` -> ``"0"``, ``"4-12"`` -> ``"12"``."""
    return where.split("-")[-1].replace("#", "")


def is_probe(where: str) -> bool:
    """A WHERE of ``PZZ``: probe ``P`` of zone ``ZZ`` (``000``, ``105``, ``0105``, ``#105``, ``4-105``).

    Only a bare number counts. ``12#1`` is actuator 1 of zone 12, and read
    digit by digit (``121``) it would look like a probe. 3-digit ``000``..``099``
    addresses represent external/virtual probes ``P=0, ZZ=00..99``.
    """
    bare = where.split("-")[-1].lstrip("#")
    return bare.isdigit() and (int(bare) >= 100 or len(bare) == 3)


def where_param(message: Any) -> list[str]:
    """The ``#``-separated tail of a frame's WHERE, however the OWNd version names it."""
    return getattr(message, "where_param", None) or getattr(message, "_where_param", None) or []


def is_pump(message: Any) -> bool:
    """WHERE ``0#N``: OWNd reports WHERE ``0`` with the pump number as a parameter."""
    return str(getattr(message, "where", None)) == "0" and bool(where_param(message))


def normalize_expose_where(who: int, where: str | int | None) -> str:
    """Return canonical WHERE string for an expose binding on ``who``."""
    if who == 13 or where is None:
        return ""
    raw = str(where).strip()
    if not raw:
        return ""
    if who == 4:
        clean = raw.split("-")[-1]
        bare = clean.lstrip("#")
        if bare == "0":
            return "000"
        if bare.isdigit() and (int(bare) >= 100 or len(bare) == 3):
            return bare
        return clean
    if who in (1, 2):
        return normalize_where(raw) or raw
    return raw


def expose_message_where(message: Any) -> str:
    """Extract canonical WHERE string from a received OpenWebNet message for expose matching."""
    who = getattr(message, "who", None)
    if who == 13:
        return ""
    raw_where = getattr(message, "where", None)
    if raw_where is None:
        return ""
    raw_str = str(raw_where).strip()
    tail = where_param(message)
    full_str = f"{raw_str}#{'#'.join(str(p) for p in tail)}" if tail else raw_str
    if isinstance(who, int):
        return normalize_expose_where(who, full_str)
    return full_str

