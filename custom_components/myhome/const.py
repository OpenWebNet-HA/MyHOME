"""Constants for the MyHome component."""
import logging
from functools import lru_cache

LOGGER = logging.getLogger(__package__)
DOMAIN = "myhome"

ATTR_GATEWAY = "gateway"
ATTR_MESSAGE = "message"
INTEGRATION_VERSION = "2.0.0b11"
REQUIRED_OWND_VERSION = "2.0.0b6"


@lru_cache(maxsize=1)
def get_ownd_version() -> str:
    """Return the installed version of the OWNd protocol engine."""
    try:
        import importlib.metadata

        return importlib.metadata.version("OWNd")
    except Exception:
        return "unknown"

CONF = "config"
CONF_ENTITY = "entity"
CONF_ENTITIES = "entities"
CONF_ENTITY_NAME = "entity_name"
CONF_ICON = "icon"
CONF_ICON_ON = "icon_on"
CONF_PLATFORMS = "platforms"
CONF_ADDRESS = "address"
CONF_OWN_PASSWORD = "password"
CONF_FIRMWARE = "firmware"
CONF_SSDP_LOCATION = "ssdp_location"
CONF_SSDP_ST = "ssdp_st"
CONF_DEVICE_TYPE = "deviceType"
CONF_DEVICE_MODEL = "model"
CONF_MANUFACTURER = "manufacturer"
CONF_MANUFACTURER_URL = "manufacturerURL"
CONF_UDN = "UDN"
CONF_WORKER_COUNT = "command_worker_count"
CONF_FILE_PATH = "config_file_path"
CONF_GENERATE_EVENTS = "generate_events"
CONF_PARENT_ID = "parent_id"
CONF_WHO = "who"
CONF_WHERE = "where"
CONF_BUS_INTERFACE = "interface"
CONF_ZONE = "zone"
CONF_DIMMABLE = "dimmable"
CONF_COLOR_TEMP = "color_temp"
CONF_RGB = "rgb"
CONF_HS = "hs"
CONF_GATEWAY = "gateway"
CONF_DEVICE_CLASS = "class"
CONF_INVERTED = "inverted"
CONF_ADVANCED_SHUTTER = "advanced"
CONF_HEATING_SUPPORT = "heat"
CONF_COOLING_SUPPORT = "cool"
CONF_FAN_SUPPORT = "fan"
CONF_STANDALONE = "standalone"
CONF_CENTRAL = "central"
CONF_SHORT_PRESS = "pushbutton_short_press"
CONF_SHORT_RELEASE = "pushbutton_short_release"
CONF_LONG_PRESS = "pushbutton_long_press"
CONF_LONG_RELEASE = "pushbutton_long_release"
CONF_ROTARY_CW_SLOW = "rotary_cw_slow"
CONF_ROTARY_CW_FAST = "rotary_cw_fast"
CONF_ROTARY_CCW_SLOW = "rotary_ccw_slow"
CONF_ROTARY_CCW_FAST = "rotary_ccw_fast"
CONF_TRAVEL_TIME = "travel_time"
DEFAULT_TRAVEL_TIME = 25
WHO_BURGLAR_ALARM = "5"
PLATFORM_ALARM = "alarm_control_panel"

# ── Decoder pool (Dynamic Proxy for Music Assistant / Spotify) ──────────────
# Up to 4 decoder slots, one per BTicino physical source input.
# Keys follow the pattern: decoder_{n}_{field}, n = 1..4
CONF_DECODER_ENTITY = "decoder_{}_entity"     # HA media_player entity_id
CONF_DECODER_SOURCE = "decoder_{}_source"     # BTicino source number (int 1-4)
CONF_DECODER_PRE_GAIN = "decoder_{}_pre_gain" # Volume offset % added to decoder (0-50)
CONF_DECODER_SLOTS = 4                        # Maximum number of decoder slots

# ── Light transition modes (software stepped dimming) ─────────────────────
CONF_TRANSITION_MODE = "transition_mode"
TRANSITION_MODE_NATIVE = "native"
TRANSITION_MODE_SOFTWARE = "software_stepped"
TRANSITION_MODE_AUTO = "auto"  # back-compat alias → software_stepped
TRANSITION_MODES = [TRANSITION_MODE_SOFTWARE, TRANSITION_MODE_NATIVE, TRANSITION_MODE_AUTO]
DEFAULT_TRANSITION_MODE = TRANSITION_MODE_SOFTWARE

# Tuning for software stepped fades (best-effort)
SOFTWARE_TRANSITION_STEP_INTERVAL = 0.3   # target seconds between steps
SOFTWARE_TRANSITION_MIN_STEPS = 2
SOFTWARE_TRANSITION_MAX_STEPS = 25


def is_apl_address(base: str) -> bool:
    """Check if base address is a valid OpenWebNet Point-to-Point (APL) address.

    Point-to-point addressing combinations:
      - A = 00; PL [01-15]     -> 4 digits (e.g. 0015 = Area 00, PL 15)
      - A [1-9]; PL [1-9]      -> 2 digits (e.g. 15 = Area 1, PL 5)
      - A = 10; PL [01-15]     -> 4 digits (e.g. 1015 = Area 10, PL 15)
      - A [01-09]; PL [10-15]  -> 4 digits (e.g. 0115 = Area 1, PL 15)
    """
    if not base.isdigit():
        return False
    if len(base) == 2:
        a = int(base[0])
        pl = int(base[1])
        return 1 <= a <= 9 and 1 <= pl <= 9
    if len(base) == 4:
        a = int(base[:2])
        pl = int(base[2:])
        if a == 0:
            return 1 <= pl <= 15
        if 1 <= a <= 9:
            return 10 <= pl <= 15
        if a == 10:
            return 1 <= pl <= 15
    return False


def normalize_where(where: str | int | None) -> str:
    """Normalize OpenWebNet address while preserving Point-to-Point (APL) addressing.

    Point-to-point WHERE addresses must never have leading zeros stripped:
      - '0015' means Area 00, Point 15 (4 digits)
      - '15' means Area 1, Point 5 (2 digits)
    Area broadcasts '00' (Area 0) and '100' (Area 10) are also preserved.
    Other numeric addresses (such as zero-padded CEN+/dry contact object IDs like '0021')
    have leading zeros stripped to match integer IDs.
    """
    if where is None:
        return ""
    where_str = str(where).strip()
    if not where_str:
        return ""
    parts = where_str.split("#", 1)
    base = parts[0]
    if is_apl_address(base) or base in ("00", "100", "0"):
        norm_base = base
    elif base.isdigit():
        norm_base = str(int(base))
    else:
        norm_base = base
    return f"{norm_base}#{parts[1]}" if len(parts) > 1 else norm_base


SERVICE_TURN_ON_TIMED = "turn_on_timed"

PRESET_TIMERS: dict[float, int] = {
    0.5: 18,
    30.0: 17,
    60.0: 11,
    120.0: 12,
    180.0: 13,
    240.0: 14,
    300.0: 15,
    900.0: 16,
}


def build_timed_turn_on_command(
    where: str,
    duration: float | None = None,
    hours: int = 0,
    minutes: int = 0,
    seconds: float = 0,
):
    """Build OpenWebNet hardware timer command for WHO=1."""
    from OWNd.message import OWNCommand

    total_seconds = float(duration if duration is not None else 0.0)
    total_seconds += (int(hours) * 3600) + (int(minutes) * 60) + float(seconds)

    if total_seconds <= 0:
        total_seconds = 0.5

    rounded_secs = round(total_seconds, 1)
    if rounded_secs in PRESET_TIMERS:
        what = PRESET_TIMERS[rounded_secs]
        frame = f"*1*{what}*{where}##"
    else:
        int_secs = int(round(total_seconds))
        h = max(0, min(255, int_secs // 3600))
        m = max(0, min(59, (int_secs % 3600) // 60))
        s = max(0, min(59, int_secs % 60))
        frame = f"*#1*{where}*#2*{h}*{m}*{s}##"

    parsed = OWNCommand.parse(frame)
    return parsed if parsed is not None else OWNCommand(frame)
