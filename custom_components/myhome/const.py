"""Constants for the MyHome component."""
import logging
import re
from functools import lru_cache
from typing import Any

from homeassistant.const import Platform

LOGGER = logging.getLogger(__package__)
DOMAIN = "myhome"

ATTR_GATEWAY = "gateway"
ATTR_MESSAGE = "message"
INTEGRATION_VERSION = "2.0.0b13"
# hass.data[DOMAIN] key holding the OWNd version resolved off the event loop
DATA_OWND_VERSION = "_ownd_version"


@lru_cache(maxsize=1)
def get_ownd_version() -> str:
    """Return the installed version of the OWNd protocol engine.

    Reads package metadata from disk: call it via ``hass.async_add_executor_job``
    (see ``_async_resolve_ownd_version``), never directly from the event loop.
    """
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
PLATFORMS: tuple[Platform, ...] = (
    Platform.LIGHT,
    Platform.SWITCH,
    Platform.COVER,
    Platform.CLIMATE,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.MEDIA_PLAYER,
    Platform.BUTTON,
    Platform.ALARM_CONTROL_PANEL,
)
CONF_ADDRESS = "address"
CONF_OWN_PASSWORD = "password"
CONF_FIRMWARE = "firmware"
CONF_SSDP_LOCATION = "ssdp_location"
CONF_SSDP_ST = "ssdp_st"
CONF_DEVICE_TYPE = "deviceType"
CONF_DEVICE_MODEL = "model"
CONF_MANUFACTURER = "manufacturer"
CONF_MANUFACTURER_URL = "manufacturerURL"
CONF_MEMBERS = "members"
CONF_UDN = "UDN"
CONF_WORKER_COUNT = "command_worker_count"
CONF_FILE_PATH = "config_file_path"
CONF_GENERATE_EVENTS = "generate_events"
CONF_BROADCAST_RESYNC = "broadcast_resync"
CONF_PARENT_ID = "parent_id"
CONF_WHO = "who"
CONF_WHERE = "where"
CONF_BUS_INTERFACE = "interface"
#: F422 bus-routing separator in a WHERE: ``APL#4#<bus>``.
BUS_ROUTING = "#4#"
CONF_ZONE = "zone"
CONF_DIMMABLE = "dimmable"
CONF_COLOR_TEMP = "color_temp"
CONF_RGB = "rgb"
CONF_HS = "hs"
CONF_LOCK_FEATURES = "lock_features"
CONF_GATEWAY = "gateway"
CONF_DEVICE_CLASS = "class"
CONF_INVERTED = "inverted"
CONF_ADVANCED_SHUTTER = "advanced"
CONF_HEATING_SUPPORT = "heat"
CONF_COOLING_SUPPORT = "cool"
CONF_FAN_SUPPORT = "fan"
CONF_STANDALONE = "standalone"
CONF_SDOMOTICA_COMPAT = "sdomotica_compat"
CONF_CENTRAL = "central"
CONF_SHORT_PRESS = "pushbutton_short_press"
CONF_SHORT_RELEASE = "pushbutton_short_release"
CONF_LONG_PRESS = "pushbutton_long_press"
CONF_LONG_PRESS_REPEAT = "pushbutton_long_press_repeat"
CONF_LONG_RELEASE = "pushbutton_long_release"
CONF_ROTARY_CW_SLOW = "rotary_cw_slow"
CONF_ROTARY_CW_FAST = "rotary_cw_fast"
CONF_ROTARY_CCW_SLOW = "rotary_ccw_slow"
CONF_ROTARY_CCW_FAST = "rotary_ccw_fast"
CONF_TRAVEL_TIME = "travel_time"
DEFAULT_TRAVEL_TIME = 25

# Cover calibration (timed covers measure their own travel times on the bus)
CONF_COVER_TRAVEL_TIMES = "cover_travel_times"  # config entry option: {device_id: {...}}
SERVICE_CALIBRATE_COVER = "calibrate_cover"
SERVICE_STOP_COVER_CALIBRATION = "stop_cover_calibration"
SERVICE_SET_COVER_TRAVEL_TIME = "set_cover_travel_time"
SERVICE_RESET_COVER_TRAVEL_TIME = "reset_cover_travel_time"
EVENT_COVER_CALIBRATION = "myhome_cover_calibration"
CALIBRATION_RUN_TIMEOUT = 180.0  # s to wait for the actuator's stop status per run
CALIBRATION_MIN_RUN = 1.0        # s: anything shorter is not a full travel
CALIBRATION_MAX_RUN = 300.0      # s: anything longer is an actuator with no run-time limit
CALIBRATION_SETTLE = 1.0         # s pause between runs so the actuator relay settles
# An actuator with the factory 60 s run-time limit stops itself, not at the end
# stop: measured 61.5 s for a 14 s shutter on a MyHOMEServer1 (#319). A run that
# ends inside this window measured the actuator, not the shutter, and is refused.
CALIBRATION_CUTOFF_MIN = 59.0
CALIBRATION_CUTOFF_MAX = 65.0
WHO_BURGLAR_ALARM = "5"
PLATFORM_ALARM = "alarm_control_panel"

# ── Decoder pool (Dynamic Proxy for Music Assistant / Spotify) ──────────────
# Up to 4 decoder slots, one per BTicino physical source input.
# Keys follow the pattern: decoder_{n}_{field}, n = 1..4
CONF_DECODER_ENTITY = "decoder_{}_entity"     # HA media_player entity_id
CONF_DECODER_SOURCE = "decoder_{}_source"     # BTicino source number (int 1-4)
CONF_DECODER_PRE_GAIN = "decoder_{}_pre_gain" # Volume offset % added to decoder (0-50)
CONF_DECODER_SLOTS = 4                        # Maximum number of decoder slots

# ── Matrix sources (F441M inputs S1-S4) ────────────────────────────────────
# Friendly name per physical source input. A blank name means "no source wired
# to this input": the integration then never offers it for selection and labels
# a zone routed to it as unconfigured instead of silently showing "Source N".
CONF_SOURCE_NAME = "source_{}_name"           # Friendly name, e.g. "Cambridge"
CONF_SOURCE_SLOTS = 4                         # Matrix inputs S1-S4
SOURCE_UNCONFIGURED_SUFFIX = " (not configured)"

# Default source per environment. The F441M routes per output and an output
# serves one environment, so a default belongs to an environment, not to a
# single amplifier: two zones in the same room cannot sit on different inputs.
# Stored as {environment_digit: source_number}; a missing entry means "leave
# the routing alone", which is the default.
CONF_SOURCE_DEFAULTS = "source_defaults"
CONF_SOURCE_DEFAULT_FIELD = "default_source_env_{}"  # options-flow field name

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

# Debounce window for reactive group / area / general broadcast re-sync (issue #368)
RESYNC_DEBOUNCE_S = 0.5
RESYNC_LEADING_WINDOW_S = 1.5

# ── Multi-Gateway & Shared Bus Support (Issue #453) ─────────────────────────
CONF_BUS_TOPOLOGY = "bus_topology"
TOPOLOGY_STANDALONE = "standalone"
TOPOLOGY_SHARED = "shared"
TOPOLOGY_OPTIONS = [TOPOLOGY_STANDALONE, TOPOLOGY_SHARED]

CONF_GATEWAY_ROLE = "gateway_role"
ROLE_PRIMARY = "primary"
ROLE_SECONDARY = "secondary"
ROLE_STANDBY = "standby"
ROLE_OPTIONS = [ROLE_PRIMARY, ROLE_SECONDARY, ROLE_STANDBY]

CONF_PRIMARY_GATEWAY = "primary_gateway"
CONF_DELEGATED_WHOS = "delegated_whos"
ISSUE_SHARED_BUS_DETECTED = "shared_bus_detected"
ISSUE_GATEWAY_FAILOVER = "gateway_failover_active"
ISSUE_PRIMARY_GATEWAY_MISSING = "primary_gateway_missing"

# Shared-bus detection. The #453 traces (F454 + MH202 on one bus) put the same
# physical frame on both event sessions 4-47 ms apart.
SHARED_BUS_TX_ECHO_S = 1.5
SHARED_BUS_RX_WINDOW_S = 0.3
SHARED_BUS_EVIDENCE_COUNT = 3
SHARED_BUS_EVIDENCE_WINDOW_S = 600.0


def eight_bits_to_percent(value: int) -> int:
    """Convert an 8-bit brightness (0-255) to percentage (0-100)."""
    return int(round((value * 100) / 255, 0))


def percent_to_eight_bits(value: int) -> int:
    """Convert a percentage (0-100) to 8-bit brightness (0-255)."""
    return int(round((value * 255) / 100, 0))


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


def area_of_where(where: str | int | None) -> str | None:
    """Return the area status WHERE for a given Point-to-Point (APL) WHERE."""
    if where is None:
        return None
    where_str = str(where).strip()
    parts = where_str.split("#", 1)
    base = parts[0]
    if not is_apl_address(base):
        return None
    if len(base) == 2:
        return base[0]
    # Length is 4
    a = base[:2]
    if a == "00":
        return "00"
    if a == "10":
        return "100"
    return str(int(a))


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

SUPPORTED_GATEWAY_MODELS = [
    "MyHomeServer1",
    "F454",
    "F455",
    "MH202",
    "MH200N",
    "MH200",
    "MH201",
    "F453AV",
    "F452",
    "F461",
    "AM4890",
    "H4890",
    "LN4890",
    "Generic",
]

# WHO=13 dimension 15 ("MODEL REQUEST", *#13**15*MODEL##) device-type codes.
#
# Official table - BTicino "OpenWebNet_Community_2_device" v1.0.0 (2006-06-13),
# section 1.2.6, reproduced completely. It predates every gateway sold after
# 2006 (F454, F455, MH200N, MH202, MyHOMEServer1 ...), which therefore reuse or
# invent codes: dimension 15 can CORROBORATE an identity, it can never establish
# one for a modern gateway. Identification precedence lives in
# MyHOMEGatewayHandler (SSDP announcement > user's choice > WHO=13).
WHO13_OFFICIAL_DEVICE_TYPES = {
    "2": "MHServer",
    "4": "MH200",
    "6": "F452",
    "7": "F452V",
    "11": "MHServer2",
    "13": "H4684",
}
# Codes seen on real hardware in this project, with the evidence. A value is the
# tuple of every name the code has been seen answering to; the first entry is
# the name a gateway gets labelled with.
#   200: Confirmed on physical hardware for F454 (PR #420 sweep, firmware 2.0.51;
#        earlier in issue #370 with SSDP), MH202 (PR #420 sweep, firmware 1.0.21),
#        MyHOMEServer1 (PR #420 trace, firmware 2.87.13; earlier in issue
#        #292/#297), and H4890 (issue #466 sweep, firmware 4.0.15); reported for
#        F461 in issue #370, no diagnostics yet. Shared across modern Linux-based
#        gateway families, so it identifies none of them (see WHO13_SHARED_DEVICE_TYPES).
#        It contradicts legacy gateways (e.g. MH200/F452), but only as field evidence.
WHO13_OBSERVED_DEVICE_TYPES: dict[str, tuple[str, ...]] = {
    "200": ("F454", "MyHomeServer1", "MH202", "F461", "H4890"),
}
# Codes from an independent implementation: the `device` table of Nmap's
# openwebnet-discovery.nse, whose `device_dimension["Device Type"] = "15"` is this
# same WHO=13 dimension. It has carried these since the script was first committed
# (2017-07-18), predating this project, and nobody here has seen them on a bus:
# they can label a gateway that has no model at all and corroborate one that has,
# but - like field evidence - they never contradict a configured model.
# https://github.com/nmap/nmap/blob/master/scripts/openwebnet-discovery.nse
#
# Its table also carries the six official 2006 codes unchanged, plus `51 -> F454`
# and `200 -> "F454 (new?)"`. That `51` is the entry that matters here: it is
# independent of the OpenWebNet device database @anotherjulien quoted in #420, so
# two unrelated sources agree an F454 can answer 51, even though no capture of
# `*#13**15*51##` exists and no firmware version has ever been tied to it.
#
# A theory, explicitly unproven (#420): the F454 may straddle two identification
# schemes - early/1.x firmware answering the concrete `51`, later/2.x firmware
# answering the generic `200` and leaving the specific identity to WHO=1013
# OBJECT_MODEL 51. Nmap labelling 200 "F454 (new?)" in 2017 fits, but only a dated
# capture tying each value to a firmware version would settle it. Nothing in the
# code depends on the theory being true.
WHO13_THIRD_PARTY_DEVICE_TYPES: dict[str, tuple[str, ...]] = {
    "12": ("F453AV",),
    "15": ("F427",),  # Nmap: "F427 (Gateway Open-KNX)"
    "16": ("F453",),
    "23": ("H4684",),  # a second code for the model the 2006 table gives as 13
    "27": ("L4686SDK",),
    "44": ("MH200N",),
    "51": ("F454",),
}
# Codes answered by several distinct models. Such a code is not evidence of any
# model; it is the cue to ask WHO=1013 dimension 1, whose reply settles it
# (identity.py). Every family the code is seen on must have a WHO=1013 code.
WHO13_SHARED_DEVICE_TYPES: frozenset[str] = frozenset({"200"})

# WHO=1013 (Gateway Diagnostic) dimension 1, OBJECT_MODEL: one code per model, as
# listed by @anotherjulien in issue #370 from the OpenWebNet device database.
#
# This is a different identifier space from WHO=13 dimension 15, not a newer
# spelling of it, and the two disagree for the same model: an F453 is 42 here but
# 16 for Nmap, an H4684 is 29 here but 13 (2006) or 23 (Nmap) there, and no
# WHO=13 code means what 200 means. Some values do coincide (4, 12, 44, 51 ...),
# which is why the tables are kept apart rather than merged on the ones that
# match. This one is consulted only after WHO=13 returned a shared code, and
# outranks it.
# Where the database lists several names for a code, all are kept with the
# BTicino one first, so a gateway announcing any of them over SSDP is
# corroborated rather than contradicted. They are *brand variants of one
# product*, not model numbers or order codes (@anotherjulien in #420): BTicino
# sold the device as F454, Legrand sold the same hardware as 003598. Which name
# to show could in principle follow the BRAND field of a WHO=1013 reply, but the
# only brand value ever traced is 5, "Legrand BTicino", which does not
# discriminate - so the BTicino name is always the one displayed.
#
# Three codes are confirmed on physical hardware (PR #420, fixtures under
# tests/fixtures/plants/pr_420_*): 51 F454, 5 MH202, 67 MyHomeServer1. The rest of
# the table is from the database, untraced.
#
# A dimension-1 reply is four values, not one (@anotherjulien in #420, from the
# OpenWebNet Encyclopedia's work on MHCatalogue.db):
#
#     *#1013**1*OBJECT_MODEL*N_CONF*BRAND*LINE##
#
# All three traced gateways answered `*15*5*0`: N_CONF 15, BRAND 5 (Legrand
# BTicino), LINE 0 (undefined). N_CONF 15 sits outside the ordinary 0..12 physical
# configurator range and looks like the 0xF sentinel, so its gateway-specific
# meaning stays unresolved. None of the three identifies the model, so only
# OBJECT_MODEL decides the identity; all four are recorded and exported in
# diagnostics (see WHO1013_BRANDS / WHO1013_LINES).
WHO1013_OBJECT_MODELS: dict[str, tuple[str, ...]] = {
    "4": ("MH200",),
    "5": ("MH202", "003535"),
    "8": ("F455", "003594"),
    "12": ("F453AV",),
    "29": ("H4684", "L4684"),
    "30": ("AM4890", "H4890", "573958", "067292", "078479", "HW4890", "LN4890", "LN4890A"),
    "35": ("BMNE500",),
    "38": ("573992",),
    "42": ("F453",),
    "44": ("MH200N", "003565"),
    "51": ("F454", "003598"),
    "54": ("MH4892", "MH4893", "067267", "067268"),
    "55": ("MH4892C", "MH4893C", "067228", "067219"),
    "65": ("F459",),
    "67": ("MyHomeServer1",),
    "105": ("F458", "003599"),
    "134": ("F461",),
}
# Code -> the model a WHO=13 reply labels a gateway with. Precedence matches
# read_who13() in identity.py: the 2006 specification, then what this project has
# observed, then the third-party table.
# WHO=1013 dimension 1, third value: BRAND. Only the one value every traced
# gateway answers is listed; the rest of MHCatalogue.db's brand space is not
# reproduced here because nothing has been seen to use it. Note that 5 covers
# both houses, so it cannot tell a BTicino-branded unit from a Legrand one.
WHO1013_BRANDS: dict[str, str] = {
    "5": "Legrand BTicino",
}
# WHO=1013 dimension 1, fourth value: LINE (product line). Same rule: only what
# has actually been observed.
WHO1013_LINES: dict[str, str] = {
    "0": "Undefined",
}

GATEWAY_DEVICE_TYPE_MAP: dict[str, str] = {
    **{code: models[0] for code, models in WHO13_THIRD_PARTY_DEVICE_TYPES.items()},
    **{code: models[0] for code, models in WHO13_OBSERVED_DEVICE_TYPES.items()},
    **WHO13_OFFICIAL_DEVICE_TYPES,
}

# How the configured gateway model was established.
IDENTIFICATION_SSDP = "ssdp"        # the gateway announced its modelName over UPnP/SSDP
IDENTIFICATION_MANUAL = "manual"    # the user picked the model in the config flow
IDENTIFICATION_SERIAL = "serial"    # serial (USB) interface: model fixed by the transport
IDENTIFICATION_WHO13 = "who13"      # no model configured; labelled from WHO=13 dimension 15
IDENTIFICATION_UNKNOWN = "unknown"


def gateway_model_family(model: str | None) -> str:
    """Reduce a model name to its family for identity comparisons.

    ``MH200N`` -> ``MH200``, ``F452V`` -> ``F452``, ``MyHomeServer1`` -> ``MYHOMESERVER1``.
    Trailing letters are variant suffixes the 2006 code table cannot express.
    """
    if not model:
        return ""
    name = str(model).strip().upper().replace(" ", "").replace("-", "").replace("_", "")
    m = re.match(r"^([A-Z]+\d+)[A-Z]*$", name)
    return m.group(1) if m else name


def build_timed_turn_on_command(
    where: str,
    duration: float | None = None,
    hours: int = 0,
    minutes: int = 0,
    seconds: float = 0,
) -> Any:
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
