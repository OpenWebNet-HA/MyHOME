"""Code to handle a MyHome Gateway."""
from __future__ import annotations

import asyncio
import collections
import inspect
import re
import time
from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from OWNd.connection import OWNCommandSession, OWNEventSession, OWNGateway, OWNSession
from OWNd.message import OWNCommand
from OWNd.profiles import GenericGatewayProfile, get_gateway_profile

from .bus_monitor import BusMonitor
from .const import (
    BUS_ROUTING,
    CONF_DEVICE_TYPE,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_UDN,
    LOGGER,
)
from .device_health import DeviceHealth
from .gateway_confirm import LightEchoWatchdog
from .gateway_events import GatewayEventDispatcher
from .gateway_resync import LightingResyncManager
from .gateway_sessions import (
    COMMAND_SESSION_IDLE_TIMEOUT,
    EVENT_INACTIVITY_TIMEOUT,
    EVENT_READY_TIMEOUT,
    EVENT_RESTART_BACKOFF_MAX,
    EVENT_RESTART_BACKOFF_MIN,
    EVENT_STALL_TIMEOUT,
    STATUS_TIMEOUT_BACKOFF,
    CommandPriorityQueue,
    CommandWorkerPool,
    EventSessionRunner,
    FrameScheduler,
    _cancel_written,
    _resolve_written,
    _session_is_open,
)
from .identity import (
    GatewayIdentityEvidence,
    GatewayIdentityMixin,
    GatewayIdentityResolution,
    read_who13,
    read_who1013,
    resolve_gateway_identity,
)
from .repairs import (
    async_create_identity_corrected_issue,
    async_create_identity_issue,
    async_create_unconfigured_timezone_issue,
    async_create_unknown_model_issue,
    async_delete_identity_issue,
    async_delete_unconfigured_timezone_issue,
    async_delete_unknown_model_issue,
)
from .topology import (
    DOOR_ENTRY_AV_MODELS,
    DOOR_ENTRY_MH_BRIDGE_AV_MODELS,
    DOOR_ENTRY_UNSUPPORTED_MODELS,
    DOOR_ENTRY_WHO6_ONLY_MODELS,
    DOOR_ENTRY_WHO8_ONLY_MODELS,
    GatewayTopologyMixin,
    peer_handles_who,
)

__all__ = [
    "AVAILABILITY_GRACE",
    "COMMAND_SESSION_IDLE_TIMEOUT",
    "COVER_PRE_MOVE_STOP_EMITTED",
    "COVER_PRE_MOVE_STOP_OMITTED",
    "CommandPriorityQueue",
    "CommandWorkerPool",
    "EVENT_INACTIVITY_TIMEOUT",
    "EVENT_READY_TIMEOUT",
    "EVENT_RESTART_BACKOFF_MAX",
    "EVENT_RESTART_BACKOFF_MIN",
    "EVENT_STALL_TIMEOUT",
    "EventSessionRunner",
    "FrameScheduler",
    "GatewayEventDispatcher",
    "GatewayIdentityEvidence",
    "GatewayIdentityMixin",
    "GatewayIdentityResolution",
    "GatewayTopologyMixin",
    "GenericGatewayProfile",
    "LightingResyncManager",
    "MyHOMEGatewayHandler",
    "OWNCommandSession",
    "OWNEventSession",
    "OWNGateway",
    "OWNSession",
    "STATUS_TIMEOUT_BACKOFF",
    "WHO4_RECOVERY_QUIET_WINDOWS",
    "_cancel_written",
    "_resolve_written",
    "_session_is_open",
    "async_call_later",
    "async_create_identity_corrected_issue",
    "async_create_identity_issue",
    "async_create_unconfigured_timezone_issue",
    "async_create_unknown_model_issue",
    "async_delete_identity_issue",
    "async_delete_unconfigured_timezone_issue",
    "async_delete_unknown_model_issue",
    "async_dispatcher_send",
    "command_session_default",
    "command_session_limit",
    "dr",
    "er",
    "gateway_emits_cover_pre_move_stop",
    "get_gateway_profile",
    "read_who13",
    "read_who1013",
    "resolve_gateway_identity",
    "time",
]


def _call_get_gateway_profile(
    model: str | None, firmware: str | None = None, *, lookup: Any = None
) -> Any:
    """Call ``get_gateway_profile`` with optional firmware, tolerating 1-arg mocks."""
    fn = lookup if lookup is not None else get_gateway_profile
    if firmware:
        try:
            return fn(model, firmware)
        except TypeError:  # pragma: no cover - fallback for 1-arg mocks or older OWNd
            return fn(model)
    return fn(model)


def _profile_for_model(
    model: str | None, firmware: str | None = None, *, lookup: Any = None
) -> Any:
    """Resolve ``model`` to an OWNd profile (firmware-aware, e.g. the H4684's WHO 22 from 6.0.0).

    SSDP spellings such as ``H/L4684`` / ``HL4684`` / ``HC4684`` are aliased by the pinned OWNd
    itself (OWNd#147, in OWNd 2.0.0b13). Touch screen family spellings (067292, etc.) are mapped
    via ``_MODEL_SPELLINGS`` (#832).
    """
    profile = _call_get_gateway_profile(model, firmware, lookup=lookup)
    if isinstance(profile, GenericGatewayProfile) and model:
        mapped = _MODEL_SPELLINGS.get(_alnum(model))
        if mapped is not None:
            return _call_get_gateway_profile(mapped, firmware, lookup=lookup)
    return profile


def command_session_limit(model: str | None) -> int | None:
    """Return how many command sessions a known gateway model accepts at once."""
    profile = _profile_for_model(model)
    max_sessions = getattr(profile, "max_command_sessions", None)
    if isinstance(profile, GenericGatewayProfile) or max_sessions is None:
        return None
    return int(max_sessions)


def command_session_default(model: str | None) -> int:
    """Return how many command sessions a new entry should start with."""
    profile = _profile_for_model(model)
    default = int(getattr(profile, "default_command_sessions", 1))
    limit = command_session_limit(model)
    return max(1, min(default, limit) if limit is not None else 1)


AVAILABILITY_GRACE = 60
BUS_QUIET_PERIOD = 0.75
BUS_QUIET_CAP = 15.0
PACED_WRITE_TIMEOUT = 60.0
# Extra BUS_QUIET_CAP windows waited by _wait_for_who4_recovery() when a WHO 4 status read
# times out or desyncs while event frames are still actively streaming on the bus (#886).
# On MyHomeServer1 (028206), /home/bticino/cfg/openserver routes WHO 4 to a single-threaded
# bt_termo daemon (127.0.0.1:30004, 9 s timeout) while bt_thermo_manager periodically walks
# its internal thermo.conf zone list (5-15 min DIMENSION 7 + DIMENSION 0 or 5-frame *#4*Z##
# sweeps taking 6 s on fast buses up to 65-115 s across multi-riser plants; zones omitted
# from thermo.conf such as Zone 59 in #886 never appear in those sweeps). Intentionally
# bounded at 4 extra windows (up to (1 + 4) * 15 s = 75 s total) so a typical 65-75 s
# internal sweep can finish and drain the buffered query without stalling forever on a
# permanently chattering bus.
WHO4_RECOVERY_QUIET_WINDOWS = 4
# How long a zone request waits for others once the startup sweep is over: zones discovered
# from incoming frames arrive over 1-2 s, and each would otherwise start its own all-zone
# sweep (*#4*0#0*20##) on an F461.
ZONE_REQUEST_COALESCE = 0.1
DISCOVERY_REQUESTS: tuple[tuple[int, str], ...] = (
    (1, "*#1*0##"),
    (2, "*#2*0##"),
    (4, "*#4*0##"),
    (5, "*#5*0##"),
    (16, "*#16*0*5##"),
    (18, "*#18*51*51##"),
    (18, "*#18*51*113##"),
)
# Startup requests the firmware drops instead of forwarding them to the bus (own-firmware-oracle,
# results/mcp_index.json at 53f9514; "silent" verdicts). Read from the OWNd profile first
# (``supports_general_heating_status``, ``supports_who(16)``); these tables are the fallback
# until the flags exist there. Gateways not listed keep sending the request.
GENERAL_ZONE_REQUEST_REFUSED = frozenset(
    {"F450", "F454", "F455", "F459", "F460", "F461", "H4684", "MH200", "MH201", "MH202", "MYHOMESERVER1"}
)  # *#4*0## (trace-corpus.tsv); F453, F453AV and MH200N forward it. The MH201 is not in the
# oracle: a physical MH201 3.3.6 sent *#4*0## and got no zone frame back although zones 2, 3
# and 4 report on that plant (tests/fixtures/traces/issue_466, sweep 19-11-35 frame 11 against
# trace 19-05-42 frames 0-10). The F455 is not in the oracle either: a physical F455 1.0.76
# ACKed *#4*0## after 9.1 s with no frame on either session, on a plant whose zones answer
# every single read (tests/fixtures/traces/issue_466/F455_direct_requests_2026-11-06.jsonl),
# and again after about 9 s with no frame at every start on 2026-11-17 (#932); the request
# holds a command session for those 9 s.
SOUND_STATUS_REQUEST_REFUSED = frozenset(
    {"F450", "F459", "F460", "F461", "MH202", "MYHOMESERVER1"}
)  # *#16*0*5## (sound-who16-source.tsv)
# Zones of these SCS-only gateways report nothing after the startup sweep, so each zone asks
# for its temperature (*#4*Z*0##, one bus query answered in ~120 ms on a populated zone): the
# single-session or uncaptured models refusing *#4*0## whose *#4*Z## is expanded into four bus
# queries ($0499ZZ3000 / 12 / 0E / 0D in trace-corpus.tsv), and the MH201, whose standalone
# thermostats stayed unknown after every restart (#185; its *#4*0## refusal is the issue_466
# sweep trace, the MH201 is not in the oracle). F454, MH202, F460, F461 and MyHomeServer1 refuse
# *#4*0## too but answer *#4*Z## themselves (F454 supports up to 4 command sessions and answers
# all five frames in ~1.3 s on both central-unit #629 and standalone #866 plants; MH202 answers
# all five frames in ~160-250 ms, #866; F460, F461 and MyHomeServer1 expand it into $0499013000 in
# trace-corpus.tsv and answer all five frames on the SCS bus in ~1.3-1.9 s, #649 / #714 / #886),
# so their zones keep the zone status poll and are not listed here.
ZONE_TEMPERATURE_POLL = frozenset({"F450", "F459", "H4684", "MH200", "MH201"})
# The silent-zone poll (#466, #932), on every gateway. Whether a gateway answers the general
# request (*#4*0##) or the zone status request (*#4*Z##) with the state of a zone is known for
# the models in the tables above and unknown for any other, and a zone whose thermostat nobody
# touches pushes nothing by itself. So the rule goes by the result, not by a model list: a zone
# that has not reported its temperature, setpoint and mode SILENT_ZONE_GRACE seconds after the
# startup sweep, and after its own startup poll, asks for what is missing, read by read: its
# temperature (*#4*Z*0##), and only if that was answered its knob (*#4*Z*13##, unless the zone
# already asked for it when it was added), its setpoint (*#4*Z*14##), its setpoint in force
# (*#4*Z*12##, followed by the zone's season frame) and actuator 1 (*#4*Z#1*20##, not where the
# gateway drops the actuator request, ``silent_zone_actuator_read_refused``). A knob read that
# was sent when the zone was added and brought nothing is repeated once, as the last read. A
# value Home Assistant restored from before the restart is not a report. A zone that got everything from
# the sweep, from its status request or from its thermostat sends nothing; its actuators were
# asked for when it was added (``watch_zone_actuators``), so a zone that only misses them asks
# for actuator 1 here only where that request is not sent (``zone_actuator_request_refused``,
# the F455). A zone that answered neither of its last two status requests is left alone
# (``poll_health``). The grace only lets the frames of the sweep and of the thermostats arrive;
# the poll then waits for the zone's own startup poll, however long that takes on a large plant
# (#886), before it looks at what is missing. The zones are asked one after another (the poll
# holds ``_who4_startup_poll_lock``) and every read waits for the bus to be quiet after the one
# before (``pace_startup_status_write``, BUS_QUIET_PERIOD): on a physical F455 plant the state
# of an actuator came twice, 0.09-0.27 s apart, and the next read took the second copy for its
# reply, which the #886 guard drops (tests/fixtures/traces/issue_932/README.md). A read the
# guard still drops is sent once more, unless the zone's own answer arrived in the meantime.
# Evidence:
# - F455 1.0.76 (physical): answers each read in ~0.1 s and ACKs *#4*Z## only after ~9 s
#   (issue_466/F455_direct_requests_2026-11-06.jsonl); its standalone zone 2 (that entry ran as a
#   standby on a shared bus, so it had skipped the startup sweep) reported nothing on its own in
#   four minutes (issue_466/F455_full_debug_log_2026-11-06T16-24-46.log). On a zone without a
#   probe the same F455 NACKs *#4*Z*0## (and *14##, *12##, *19##) only after ~9 s, with no frame
#   (issue_466/F455_direct_requests_zone3_2026-11-06.jsonl): the one refused read that ends the
#   poll holds a command session for those ~9 s, once per zone added. *#4*Z#1*20## answers in
#   0.09 s there.
# - MH201 3.6.43 (physical, #932; catalogued but not emulated in the oracle): standalone zones
#   1-4 showed no target temperature after a restart until a thermostat published a change. It
#   answered *#4*2*12## with *#4*2*12*0200*3## and *#4*#2*14## with *#4*2*14*0200*3##, and with
#   this poll all four zones had their setpoint after a full restart without anyone touching a
#   thermostat.
# - own-firmware-oracle (main 3c1634b): all thirteen emulated gateways put *#4*Z*0##,
#   *#4*Z*12##, *#4*Z*13## and *#4*Z*14## on the bus (verdict "out" in thermo-ts10.tsv,
#   thermo-zone-requests.tsv and trace-corpus.tsv). No physical capture of the poll exists for
#   F450, F453, F453AV, F459, H4684, MH200 and MH200N.
# The gate is read when the poll runs and through the gateway that serves WHO 4
# (``silent_zone_poll_needed``).
SILENT_ZONE_GRACE = 30.0
# Gateways whose zones do not send the knob read (*#4*Z*13##) when they are added; it goes out
# inside the silent-zone poll instead, after the temperature read (``_local_offset_read_pending``
# in climate.py). The F455: its zones ask nothing on add (#674, sixteen *#4*Z## polls timing out
# one after another), and a single-dimension read on a probe-less channel holds a command
# session ~9 s before the NACK.
KNOB_READ_IN_SILENT_ZONE_POLL = frozenset({"F455"})
# Zones behind an F422 bus interface (WHERE Z#4#II): zero of the twelve emulated gateways in
# own-firmware-oracle (findings/thermo-zone-requests.md) emit $06EC / $06E4 interface-routed SCS
# frames for WHO 4. Gateways either NACK/drop Z#4#II queries, truncate Z#4#II at # and cross-poll
# local main-bus zone Z (F450, F454, F459, F460, F461, H4684, MH200, MyHomeServer1), or on MH202
# 010024 misparse trailing #4#01 as local zone 4 actuator 1 and crash coso with SIGSEGV on
# *#4*Z#4#01## (including while the profile is still Generic). ``interface_zone_request_refused``
# therefore defaults to True across all models unless ``profile.supports_interface_zone_requests``
# is explicitly True.

# Gateways whose firmware drops the per-zone actuator request (``*#4*Z*20##``) instead of
# forwarding it to the bus: own-firmware-oracle ``results/<P>/<V>/oracle/full/thermo-actuator.tsv``
# (``*#4*1*20##`` "silent" on these six, "out" on F450, F454, F459, F460, F461, MyHomeServer1).
# Read from the OWNd profile first (``supports_heating_actuator_status``, OWNd#168 in 2.0.0b14);
# this table is the fallback for ``GenericGatewayProfile`` / brand-prefixed model strings and
# ``None`` (unverified) profile flags.
ACTUATOR_REQUEST_REFUSED = frozenset({"F453", "F453AV", "H4684", "MH200", "MH200N", "MH202"})
# Gateways that refuse the zone form of that request at once but answer the actuator form
# (``*#4*Z#N*20##``): a physical F455 1.0.76 NACKed *#4*2*20##, *#4*4*20##, *#4*3*20## and
# *#4*0*20## in 2-18 ms and answered *#4*2#1*20## / *#4*4#1*20## / *#4*3#1*20## in ~0.09 s
# (issue_466/F455_direct_requests_2026-11-06.jsonl, F455_direct_requests_zone3_2026-11-06.jsonl).
# A zone's watch is not sent there; a thermo actuator switch (``Z#N``) still asks.
ZONE_ACTUATOR_REQUEST_REFUSED = frozenset({"F455"})
# One request for every zone, by dimension.
# Actuators (dimension 20): the published form, ``*#4*0#0*20##`` ("All the actuators [0#0]",
# WHO 4 p. 22 and p. 62). 2.0.0 asked the F461 and the MyHomeServer1 with ``*#4*#0*20##``,
# the form their captures were taken with (the live F461 2.0.11 answered it
# with the actuators of each zone; the MyHomeServer1 3.87.13 of myhomeserver1_live_2026-10-24
# in 0.39 s with 43 actuator frames for its 34 zones, where asking each zone through the paced
# sender takes about 32 s). The two forms are answered alike wherever both were tried, and
# ``#0`` is not a WHERE of dimension 20 in the document:
# - own-firmware-oracle, thermo-zone-requests.tsv:16 and :17, all 13 images: the same row for
#   both (10 images put the same telegram on the bus for either form; H4684, MH200 and MH202
#   drop both);
# - live 2026-11-18, three rounds with a monitor session, the two forms in turn: F461 2.0.11
#   the same 5 actuators of its plant after each; MyHomeServer1 3.87.13 the same 43 actuators
#   after each (ACK after 0.37-0.47 s on both, the first 8 frames on the command session and
#   the rest on the event session);
# - the F455 1.0.76 refuses ``*#4*#0*20##`` at once (NACK) and answers ``*#4*0#0*20##`` with the
#   five actuators of its plant (ACK after 1.34-1.47 s), where the zone form
#   (``ZONE_ACTUATOR_REQUEST_REFUSED``) and ``*#4*Z#0*20##`` name no actuator.
# For humidity (dimension 60), all four humidity-forwarding gateways in own-firmware-oracle
# (thermo-zone-requests: F459, F460, F461, MyHomeServer1) translate ``*#4*0*60##`` into the
# broadcast SCS query ``$06D200030308000000`` ("out"); the live MyHomeServer1 3.87.13 ACKed it
# in 0.50 s with all 17 humidity-probe zones answering (myhomeserver1_live_2026-10-24, #886),
# and the live F461 2.0.11 answered it in 2.13 s (f461_live_2026-10-23).
ALL_ACTUATORS_REQUEST = "*#4*0#0*20##"
ALL_ZONE_REQUESTS: dict[str, dict[int, str]] = {
    "F455": {20: ALL_ACTUATORS_REQUEST},
    "F459": {60: "*#4*0*60##"},
    "F460": {60: "*#4*0*60##"},
    "F461": {20: ALL_ACTUATORS_REQUEST, 60: "*#4*0*60##"},
    "MYHOMESERVER1": {20: ALL_ACTUATORS_REQUEST, 60: "*#4*0*60##"},
}
# What a gateway that drops the general request (``GENERAL_ZONE_REQUEST_REFUSED``) is asked at
# start instead. Without it nothing asks a thermostat on those gateways, and a freshly added
# gateway shows no zone until one speaks by itself (a temperature change, a key, the central
# unit's quarter-hour push): a new F461 entry sent no WHO 4 frame and had no climate entity two
# minutes later; twelve seconds after ``*#4*#0##`` and the zones were read from another client
# it had its central unit and three zones (2026-11-18).
# - The all-actuator request, where the firmware forwards it: live on F455, F461 and
#   MyHomeServer1 (above); F450, F454, F459 and F460 by the oracle row above (not tried on a
#   plant). Not sent where the oracle shows it dropped (H4684, MH200, MH202) nor on the MH201
#   (not in the oracle, no capture). Every actuator answers as ``Z#N``: a fan coil creates its
#   zone at once, a plain valve is kept until the zone is known to be a thermostat (#785), see
#   ``climate._ask_actuator_only_zones``.
# - The central unit's status, ``*#4*#0##``, where an unanswered request is cheap: the F461
#   answered it in 0.16 s with the 3550's state, and a MyHomeServer1 with no central unit
#   NACKed it after 2.09 s. Not on the F455: it answered in 1.07 s on a plant with a 3550, but
#   it holds a WHO 4 read that nothing answers for about 9 s (``*#4*9*0##``: NACK after
#   8.95 s), the reason ``*#4*0##`` is not sent there. Not in the sweep of the models nobody
#   tried it on. On every model the same request is sent once later, when a report about a
#   zone shows that a central unit is there to answer it (``request_central_status``).
STARTUP_ALL_ACTUATORS_REQUEST = frozenset({"F450", "F454", "F455", "F459", "F460", "F461", "MYHOMESERVER1"})
STARTUP_CENTRAL_STATUS_REQUEST = frozenset({"F461", "MYHOMESERVER1"})
# A zone created from the answers to the sweep's all-actuator request asks for its actuators as
# it is added (``watch_zone_actuators``). For this long after the sweep sent that request the
# zone is taken as served: on a live F461 the zones found that way put the same request on the
# bus again 18 s after the sweep (2026-11-18), and on a MyHomeServer1 each one is 43 actuators.
ALL_ACTUATORS_SWEEP_FRESH = 30.0
CENTRAL_STATUS_REQUEST = "*#4*#0##"
# Gateways whose emulated firmware did not forward the zone humidity request (``*#4*Z*60##``:
# nack and nothing on the bus) in the own-firmware-oracle thermo-zone-requests suite
# (findings/thermo-zone-requests.md on main, ``firmware_observed``, emulated firmware); F459,
# F460, F461 and MyHomeServer1 forwarded it, and the live F461 / MyHomeServer1 answer it.
# Read from the OWNd profile first (``supports_heating_humidity_request``: ``True`` / ``False``
# wins, ``None`` = unverified, OWNd#168 in 2.0.0b14); this table is the fallback for
# ``GenericGatewayProfile`` / brand-prefixed model strings and ``None`` (such as F455).
HUMIDITY_REQUEST_REFUSED = frozenset({"F450", "F453", "F453AV", "F454", "F455", "H4684", "MH200", "MH200N", "MH202"})
# The F455 is not in the oracle: a physical F455 1.0.76 NACKed *#4*2*60## in 19 ms with nothing on
# the bus (issue_466/F455_direct_requests_2026-11-06.jsonl, t=68.6).
# Every gateway model the request tables can be keyed on: the twelve images the oracle emulates
# (results/<P>/<V>, refusing and forwarding alike) and the other models OWNd profiles, so a
# forwarding model named with a brand prefix ("BTicino F461") is keyed as itself (``F461``)
# and not left as the whole string. The same key on every table of #792 / #793 / #794.
KNOWN_GATEWAY_MODELS = frozenset(
    {
        "F450", "F452", "F452V", "F453", "F453AV", "F454", "F455", "F459", "F460", "F461",
        "H4684", "H4890", "MH200", "MH200N", "MH201", "MH202", "MYHOMESERVER1",
    }
)
# WHO 2 SCS pre-move STOP behavior (#959): 11 of 12 emulated Linux automation gateways
# (5 bt_luci + 6 coso libplug_automation_tr.so) synthesize a 2-telegram SCS sequence
# ($03<W>00120A STOP + $03<W>001208/1209 UP/DOWN) for *2*1*W## / *2*2*W## (firmware_oracle.json
# and mcp_index.json). MH202 (010024, older coso without pre-STOP) and bare-metal MH201 (3.3.6,
# live plant trace) emit only the single direction telegram, so energized timed relay actuators
# (L4671/2, F411/2, F411/4, F411U2) stop instead of reversing unless HA stops first.
COVER_PRE_MOVE_STOP_EMITTED = frozenset(
    {"3488", "F453", "F453AV", "F454", "F459", "F460", "F461", "H4684", "MH200", "MH200N", "MYHOMESERVER1"}
)
COVER_PRE_MOVE_STOP_OMITTED = frozenset({"MH201", "MH202"})
# The models the tables know, longest first so ``MH200N`` is not read as ``MH200``.
_ZONE_REQUEST_MODELS = sorted(
    KNOWN_GATEWAY_MODELS | ACTUATOR_REQUEST_REFUSED | HUMIDITY_REQUEST_REFUSED | set(ALL_ZONE_REQUESTS)
    | GENERAL_ZONE_REQUEST_REFUSED | SOUND_STATUS_REQUEST_REFUSED | ZONE_TEMPERATURE_POLL
    | KNOB_READ_IN_SILENT_ZONE_POLL
    | ZONE_ACTUATOR_REQUEST_REFUSED
    | COVER_PRE_MOVE_STOP_EMITTED
    | COVER_PRE_MOVE_STOP_OMITTED,
    key=len,
    reverse=True,
)
# Legrand 067292 Céliane 3.5" touch screen family (issue #832) SSDP model names mapped
# to the H4890 profile until OWNd aliases them upstream.
_MODEL_SPELLINGS = {
    "067292": "H4890",
    "67292": "H4890",
    "573958": "H4890",
    "078479": "H4890",
    "78479": "H4890",
}


def _alnum(name: object) -> str:
    """A model string reduced to its upper-case letters and digits (``"BTicino F461"`` -> ``BTICINOF461``)."""
    return "".join(character for character in str(name).upper() if character.isalnum())


def _resolved_model(word: str) -> str | None:
    """The model OWNd resolves one word of a model string to, or ``None`` for a word it does not know.

    ``L4684`` -> ``H4684``, ``HL4684`` / ``HC4684`` -> ``H4684`` (OWNd#147), ``003598`` -> ``F454``,
    ``03565`` -> ``MH200N`` (OWNd's aliases and catalogue item numbers); spellings OWNd has no
    alias for are mapped first via ``_MODEL_SPELLINGS``.
    """
    key = _MODEL_SPELLINGS.get(_alnum(word), word)
    profile = get_gateway_profile(key)
    if isinstance(profile, GenericGatewayProfile):
        return None
    return _alnum(profile.model_name)


def _resolve_model_key(model: str | None, *, profile: Any = None) -> str:
    """Resolve a gateway model string and optional OWNd profile to a table key (``F461``, ``MH201``)."""
    if profile is not None and not isinstance(profile, GenericGatewayProfile):
        name: object = getattr(profile, "model_name", None) or model or ""
    else:
        name = model or ""
    for word in re.findall(r"[A-Za-z0-9]+", str(name)):
        resolved = _resolved_model(word)
        if resolved is not None:
            return resolved
    normalized = _MODEL_SPELLINGS.get(_alnum(name), _alnum(name))
    return next((known for known in _ZONE_REQUEST_MODELS if known in normalized), normalized)


def gateway_emits_cover_pre_move_stop(model: str | None, *, profile: Any = None) -> bool | None:
    """Return whether a gateway injects an SCS STOP (120A) ahead of UP/DOWN (1208/1209) (#959).

    Read from the OWNd profile first when present; otherwise from the firmware-oracle and
    live-trace tables (``True`` on 11/12 emulated Linux automation gateways, ``False`` on
    ``MH201`` and ``MH202``, ``None`` when unmeasured).
    """
    emits = getattr(profile, "emits_cover_pre_move_stop", None)
    if isinstance(emits, bool):
        return emits
    key = _resolve_model_key(model, profile=profile)
    if key in COVER_PRE_MOVE_STOP_EMITTED:
        return True
    if key in COVER_PRE_MOVE_STOP_OMITTED:
        return False
    return None


# A WHO 4 zone address: ``Z`` (0..99), ``#Z``, ``Z#N`` (actuator 0..9), each optionally behind
# an interface of exactly two digits (``Z#4#01``): the grammar of OWNd#140's builder
# (``OWNHeatingCommand.actuators_status``).
_ZONE_WHERE = re.compile(rf"^#?[0-9]{{1,2}}(?:#[0-9])?(?:{re.escape(BUS_ROUTING)}[0-9]{{2}})?$")


def _validate_zone_where(where: str) -> str:
    """The WHO 4 zone address as it goes into ``*#4*WHERE*DIM##``, or ``ValueError``.

    A one-digit interface is written with two (``3#4#1`` -> ``3#4#01``), as the frames on
    the wire and the configuration validator spell it. Anything else would change the
    frame's grammar: ``2*22`` would turn a dimension 20 request into ``*#4*2*22*20##``, a
    dimension 22 write; ``2##`` ends the frame early. The builder of OWNd#140
    (``OWNHeatingCommand.actuators_status``) accepts and rejects the same addresses.
    """
    zone, routing, interface = (where or "").rpartition(BUS_ROUTING)
    if routing and len(interface) == 1 and interface.isdigit():
        where = f"{zone}{routing}{interface.zfill(2)}"
    if not _ZONE_WHERE.match(where or ""):
        raise ValueError(f"Invalid zone address: {where!r}")
    return where


def _zone_request_frame(where: str, dimension: int) -> str:
    """The dimension request of one zone (``*#4*WHERE*DIM##``), validated like a builder."""
    return f"*#4*{_validate_zone_where(where)}*{dimension}##"


def _is_zone_level_where(where: str) -> bool:
    """A zone as a whole (``Z``, ``#Z``, ``Z#4#II``), not one of its actuators (``Z#N``, ``Z#N#4#II``)."""
    return "#" not in where.partition(BUS_ROUTING)[0].lstrip("#")


class MyHOMEGatewayHandler(GatewayTopologyMixin, GatewayIdentityMixin):
    """Manages a single MyHOME Gateway."""

    device_registry_id: str | None = None
    # Behind the ``central_season`` property. Class defaults, so that a handler built without
    # ``__init__`` reads them too.
    _central_season: str | None = None
    central_season_changed_at: float | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        generate_events: bool = False,
        broadcast_resync: bool = True,
    ) -> None:
        """Initialize the MyHOME Gateway handler."""
        build_info = {
            "address": config_entry.data.get(CONF_HOST),
            "port": config_entry.data.get(CONF_PORT, 20000),
            "password": config_entry.data.get(CONF_PASSWORD),
            "ssdp_location": config_entry.data.get(CONF_SSDP_LOCATION, ""),
            "ssdp_st": config_entry.data.get(CONF_SSDP_ST, ""),
            "deviceType": config_entry.data.get(CONF_DEVICE_TYPE, ""),
            "friendlyName": config_entry.data.get(CONF_FRIENDLY_NAME, ""),
            "manufacturer": config_entry.data.get(CONF_MANUFACTURER, ""),
            "manufacturerURL": config_entry.data.get(CONF_MANUFACTURER_URL, ""),
            "modelName": config_entry.data.get(CONF_NAME, "Generic"),
            "modelNumber": config_entry.data.get(CONF_FIRMWARE, ""),
            "serialNumber": config_entry.data.get(CONF_MAC, ""),
            "UDN": config_entry.data.get(CONF_UDN, ""),
        }
        self.hass = hass
        self.config_entry = config_entry
        self.generate_events = generate_events
        self.gateway = OWNGateway(build_info)
        if isinstance(self.gateway.profile, GenericGatewayProfile):
            self.gateway.profile = _profile_for_model(self.gateway.model_name, self.gateway.firmware)
        self.is_connected = False
        self._available = False
        self._failover_active = False
        self._setup_at = time.monotonic()
        self._unavailable_timer: CALLBACK_TYPE | None = None
        self.listening_worker: asyncio.Task[None] | None = None
        self.bus_monitor = BusMonitor()
        self.device_health = DeviceHealth(self)
        self.device_registry_id = None
        self.broadcast_resync = broadcast_resync
        self.has_central_unit = False
        # The season the central unit last reported about itself (``*4*102*#0##`` heating,
        # ``*4*202*#0##`` conditioning); None until it reports one. A thermostat that keeps
        # reporting the other season is not following it (climate ``_check_season_against_central``).
        self._central_season = None
        # When that season last changed from one to the other (``time.monotonic()``); None while
        # it has only been learned. Its zones follow a change one after the other, so none is
        # compared with the new season at once (climate ``CENTRAL_SEASON_SETTLE``).
        self.central_season_changed_at = None

        self._who13: dict[str, Any] = {
            "code": None, "model": None, "model_official": None, "model_observed": None,
            "firmware": None, "kernel": None, "distribution": None,
            "pending_code": False, "pending_firmware": False,
        }
        self._who1013: dict[str, Any] = {
            "code": None, "model": None, "names": (), "pending": False,
            "n_conf": None, "brand": None, "line": None,
        }
        self._identity_conflict: str | None = None
        self._identity_resolution: GatewayIdentityResolution | None = None

        self._event_dispatcher = GatewayEventDispatcher(self)
        self._resync_manager = LightingResyncManager(self)
        self._echo_watchdog = LightEchoWatchdog(self)
        self._event_runner = EventSessionRunner(self)
        self._command_pool = CommandWorkerPool(
            self,
            event_session_ready=self._event_runner.event_session_ready,
        )

        self._cen_devices: set[tuple[int, Any]] = self._event_dispatcher.cen_devices
        self._resync_timers: dict[str, CALLBACK_TYPE] = self._resync_manager.resync_timers
        self._resync_group_echoes: dict[str, int] = self._resync_manager.resync_group_echoes
        self._recent_ptp: collections.deque[tuple[float, str, str | None]] = self._resync_manager.recent_ptp
        self._sender_stop: asyncio.Event = self._command_pool.sender_stop
        self._initial_discovery_done: asyncio.Event = asyncio.Event()
        # Told of every automation (WHO 2) status request sent through this handler, once queued, so that a
        # calibration run can tell the actuator's reply to a request from the motor stopping.
        self.automation_status_listeners: set[Callable[[OWNCommand], None]] = set()
        # Paced sweeps in flight; close_listener() bumps the epoch so a sweep that was
        # running at the time cannot end one started after the reconnect.
        self._active_sweeps: int = 0
        self._sweep_epoch: int = 0
        self._sweep_idle: asyncio.Event = asyncio.Event()
        self._sweep_idle.set()
        self._last_event_frame_at: float = 0.0
        # Heating zones whose actuators are asked after the startup sweep and every reconnect,
        # and the (zone, dimension) requests waiting for the next paced run.
        self._actuator_zones: dict[str, int] = {}
        self._zone_requests: set[tuple[str, int]] = set()
        # Zones whose actuator request goes out although every actuator was asked a moment ago.
        self._zone_requests_again: set[str] = set()
        self._zone_request_task: asyncio.Task[None] | None = None
        self._central_status_task: asyncio.Task[None] | None = None
        self._who4_startup_poll_lock: asyncio.Lock = asyncio.Lock()
        # When the startup sweep last asked every actuator (monotonic), 0 before it did.
        self._all_actuators_swept_at: float = 0.0
        # The same for the central unit's status (``CENTRAL_STATUS_REQUEST``), asked by the
        # sweep or by ``request_central_status``.
        self._central_status_swept_at: float = 0.0
        # Zones (number, interface) found not to be run by the plant's central unit
        # (``climate._mark_standalone_detected``), for what else hangs on the zone's device.
        self.standalone_detected_zones: set[tuple[str, str | None]] = set()
        # While ``climate_discovery`` creates the zones known only from their actuators.
        self._actuator_only_reads_running = False

    @property
    def central_season(self) -> str | None:
        """The season the central unit of this gateway's own bus last reported, None until it does."""
        return self._central_season

    @central_season.setter
    def central_season(self, season: str | None) -> None:
        if season is not None and self._central_season is not None and season != self._central_season:
            self.central_season_changed_at = time.monotonic()
        self._central_season = season

    @property
    def send_buffer(self) -> FrameScheduler | asyncio.Queue[Any]:
        """Return the send queue."""
        return self._command_pool.send_buffer

    @send_buffer.setter
    def send_buffer(self, value: FrameScheduler | asyncio.Queue[Any]) -> None:
        self._command_pool.send_buffer = value

    @property
    def sending_workers(self) -> list[asyncio.Task[None]]:
        """Return the sending workers list."""
        return self._command_pool.sending_workers

    @sending_workers.setter
    def sending_workers(self, value: list[asyncio.Task[None]]) -> None:
        self._command_pool.sending_workers = value

    @property
    def _terminate_listener(self) -> bool:
        """Whether the listener task is terminating."""
        return self._event_runner._terminate_listener

    @_terminate_listener.setter
    def _terminate_listener(self, value: bool) -> None:
        self._event_runner._terminate_listener = value

    @property
    def _terminate_sender(self) -> bool:
        """Whether the sender task is terminating."""
        return self._command_pool._terminate_sender

    @_terminate_sender.setter
    def _terminate_sender(self, value: bool) -> None:
        self._command_pool._terminate_sender = value

    @property
    def _event_session_ready(self) -> asyncio.Event:
        """Event set when the event session is established."""
        return self._event_runner._event_session_ready

    def _ensure_cen_device(self, who: int, object_id: int | str, model: str | None = None) -> None:
        """Ensure CEN/CEN+ scenario unit is registered in device registry."""
        self._event_dispatcher.ensure_cen_device(who, object_id, model=model)

    @property
    def mac(self) -> str:
        """Return normalized MAC address."""
        serial = self.gateway.serial
        if serial:
            formatted = dr.format_mac(serial)
            if formatted:
                return formatted
        return serial or ""

    @property
    def unique_id(self) -> str:
        """Return gateway unique ID."""
        return self.mac

    @property
    def id(self) -> str | None:
        """Return gateway ID."""
        return self.mac

    @property
    def log_id(self) -> str:
        """Return logging prefix."""
        return str(self.gateway.log_id)

    @property
    def manufacturer(self) -> str:
        """Return manufacturer name."""
        mfg = self.gateway.manufacturer
        if isinstance(mfg, (list, tuple)):
            return str(mfg[0]) if mfg else "BTicino S.p.A."
        return str(mfg) if mfg else "BTicino S.p.A."

    @property
    def name(self) -> str:
        """Return gateway name."""
        return f"{self.gateway.model_name} Gateway"

    @property
    def model(self) -> str:
        """Return gateway model name."""
        return str(self.gateway.model_name)

    @property
    def firmware(self) -> str | None:
        """Return gateway firmware version."""
        firmware: str | None = self.gateway.firmware
        return firmware

    @property
    def supports_zone_status(self) -> bool:
        """Whether the gateway supports point-to-point zone status queries (*#4*Z##)."""
        profile = getattr(getattr(self, "gateway", None), "profile", None)
        supported = getattr(profile, "supports_heating_zone_status", None)
        if isinstance(supported, bool):
            return supported
        # Check the configured model string, its alphanumeric form (so "BTicino H/L4684" matches
        # "HL4684"), and the profile model _profile_for_model resolves it to (L4684, NT4684,
        # H/L4684, HL4684, HC4684, and MyHOME_Suite catalogue codes such as 003598).
        names = (
            self.model.upper(),
            _alnum(self.model),
            str(_profile_for_model(self.model).model_name).upper(),
        )
        return not any(
            k in norm
            for norm in names
            for k in (
                "F450",
                "F455",
                "HL4684",  # "BTicino H/L4684" resolves to no profile and "H4684" is not a substring of it
                "MH200",
                "MH201",
                "F452",
                "F453",
                "F458",
                "F459",
                "H4684",
            )
        )

    @property
    def _model_key(self) -> str:
        """The gateway model the request tables are keyed on (``F461``, ``MYHOMESERVER1``).

        A known OWNd profile names the model; a ``GenericGatewayProfile`` (also what
        ``get_gateway_profile("BTicino F461")`` returns) falls back to the configured name.
        There a brand-prefixed alias or catalogue number is resolved word by word through
        OWNd (``"BTicino L4684"`` -> ``H4684``, ``"Legrand 003598"`` -> ``F454``, the SSDP
        spelling ``HL4684`` -> ``H4684``), and otherwise a known model is looked up as
        a substring, longest first (``supports_zone_status`` style), so ``MH200N`` stays
        distinct from ``MH200``. The words are the alphanumeric runs of the string, so
        ``"BTicino-L4684"`` and ``"F454 (003598)"`` resolve too.
        """
        profile = getattr(self.gateway, "profile", None)
        return _resolve_model_key(self.model, profile=profile)

    def _who2_target(self) -> MyHOMEGatewayHandler:
        """The gateway a WHO 2 command goes through (delegated or failover peer when active)."""
        probe = OWNCommand.parse("*2*0*0##")
        if probe is None:  # pragma: no cover - a fixed, well-formed frame
            return self
        return self._delegated_target(probe) or self._failover_target(probe) or self

    @property
    def emits_cover_pre_move_stop(self) -> bool | None:
        """Whether the gateway serving WHO 2 injects an SCS STOP (120A) before UP/DOWN (1208/1209)."""
        target = self._who2_target()
        return gateway_emits_cover_pre_move_stop(
            target.model,
            profile=getattr(target.gateway, "profile", None),
        )

    def _who4_target(self) -> MyHOMEGatewayHandler:
        """The gateway a WHO 4 status request goes through, the way ``send_status_request`` routes it.

        On a shared bus WHO 4 may be delegated to a secondary gateway, or served by the warm
        standby while this primary is down: that gateway's refusal and all-zone tables apply.
        """
        probe = OWNCommand.parse(ALL_ACTUATORS_REQUEST)
        if probe is None:  # pragma: no cover - a fixed, well-formed frame
            return self
        return self._delegated_target(probe) or self._failover_target(probe) or self

    @property
    def actuator_request_refused(self) -> bool:
        """Whether this gateway drops the zone actuator request (``*#4*Z*20##``).

        The OWNd profile flag wins when it exists; otherwise the oracle-derived table.
        """
        supported = getattr(getattr(self.gateway, "profile", None), "supports_heating_actuator_status", None)
        if isinstance(supported, bool):
            return not supported
        return self._model_key in ACTUATOR_REQUEST_REFUSED

    def all_zone_request(self, dimension: int) -> str | None:
        """The single request this gateway answers with every zone's ``dimension``, if any."""
        return ALL_ZONE_REQUESTS.get(self._model_key, {}).get(dimension)

    @callback
    def watch_zone_actuators(self, where: str) -> CALLBACK_TYPE:
        """Ask for a heating zone's actuators (dimension 20) now and after every reconnect.

        Returns the callback that stops it. The zone is always recorded: whether the gateway
        refuses the request is decided when it is sent, once the in-band identity (WHO 13 /
        WHO 1013) has settled, so a model corrected after the entities were added is honoured.
        """
        where = _validate_zone_where(where)  # before the zone is counted: no phantom watch
        self._actuator_zones[where] = self._actuator_zones.get(where, 0) + 1
        self.request_zone_dimension(where, 20)

        @callback
        def _unwatch() -> None:
            count = self._actuator_zones.get(where, 0) - 1
            if count > 0:
                self._actuator_zones[where] = count
            else:
                self._actuator_zones.pop(where, None)

        return _unwatch

    @callback
    def request_zone_dimension(self, where: str, dimension: int, *, again: bool = False) -> None:
        """Ask a heating zone for one dimension once the startup sweep is over.

        Zones asking together share one paced run; where the gateway answers one request
        for every zone (``all_zone_request``), that frame replaces the local zones' own.
        ``again``: the zone lost what it knew (its actuators, wiped by the off of a central
        unit that turned out not to run it), so the request is not taken as served by the one
        that asked every actuator a moment ago.
        """
        if dimension == 22:
            # A zone WHERE has no actuator token, so *#4*Z*22## is the crash form:
            # it kills the thermostat daemon (bt_termo) and a live F461 rebooted.
            # The published split form *#4*3#Z#N*22## goes through send_message once
            # the installed OWNd (>= 2.0.0b12) can tell the forms apart.
            raise ValueError("WHO 4 dimension 22 is never requested for a zone")
        where = _validate_zone_where(where)
        self._zone_requests.add((where, dimension))
        if again:
            self._zone_requests_again.add(where)
        if self._zone_request_task is None or self._zone_request_task.done():
            self._zone_request_task = self.hass.async_create_background_task(
                self._send_zone_requests(), name=f"myhome_{self.mac}_zone_requests"
            )

    async def _send_zone_requests(self) -> None:
        """Send the waiting zone requests through the paced sender, until none is left."""
        coalesce = self._initial_discovery_done.is_set()
        await self.wait_for_initial_discovery()
        while self._zone_requests:
            # Zones added in the same pass join this run; after the sweep, zones discovered
            # from the bus over the next tenth of a second do too instead of each sweeping on
            # its own, and so do zones that arrived while the previous batch was being sent.
            await asyncio.sleep(ZONE_REQUEST_COALESCE if coalesce else 0)
            coalesce = True
            pending, self._zone_requests = self._zone_requests, set()
            again, self._zone_requests_again = self._zone_requests_again, set()
            frames: list[str] = []
            target = self._who4_target()
            for dimension in sorted({dim for _where, dim in pending}):
                wheres = sorted(where for where, dim in pending if dim == dimension)
                if target.interface_zone_request_refused:
                    routed = [where for where in wheres if BUS_ROUTING in where]
                    if routed:
                        LOGGER.debug(
                            "%s Zones %s: %s does not route WHO 4 dimension %s behind an interface; not asking",
                            self.log_id,
                            routed,
                            target.model,
                            dimension,
                        )
                        wheres = [where for where in wheres if BUS_ROUTING not in where]
                        if not wheres:
                            continue
                if dimension == 20 and target.actuator_request_refused:
                    LOGGER.debug(
                        "%s Zones %s: %s refuses the actuator request; not asking", self.log_id, wheres, target.model
                    )
                    continue
                if dimension == 60 and target.humidity_request_refused:
                    LOGGER.debug(
                        "%s Zones %s: %s refuses the humidity request; not asking", self.log_id, wheres, target.model
                    )
                    continue
                # The one request for every zone first: it also serves a gateway that refuses
                # the zone form (the F455 answers *#4*0#0*20## with every actuator). Left to
                # ask on their own: zones behind an interface, and on a gateway that refuses the
                # zone form an actuator named by a thermo switch (``Z#N``, #873), which alone
                # does not call for every actuator of the plant.
                all_zones = target.all_zone_request(dimension)
                own_actuators = dimension == 20 and target.zone_actuator_request_refused
                served = [
                    where
                    for where in wheres
                    if BUS_ROUTING not in where and not (own_actuators and not _is_zone_level_where(where))
                ]
                if all_zones is not None and served:
                    # 0 is "no sweep asked yet", not a time: on a host whose monotonic clock is
                    # itself under ALL_ACTUATORS_SWEEP_FRESH it would read as a sweep just sent.
                    # Every actuator of the bus was asked, by this gateway or by the one that
                    # serves WHO 4 for it (a delegated secondary, the warm standby): the answers
                    # reached this entry's zones either way, so both gateways' marks count.
                    swept_at = max(self._all_actuators_swept_at, getattr(target, "_all_actuators_swept_at", 0.0))
                    reads_running = (
                        self._actuator_only_reads_running
                        or getattr(target, "_actuator_only_reads_running", False) is True
                    )
                    just_swept = (
                        all_zones == ALL_ACTUATORS_REQUEST
                        and swept_at > 0
                        and (time.monotonic() - swept_at < ALL_ACTUATORS_SWEEP_FRESH or reads_running)
                        and again.isdisjoint(served)
                    )
                    if not just_swept:
                        frames.append(all_zones)
                    wheres = [where for where in wheres if where not in served]
                if dimension == 20 and target.zone_actuator_request_refused:
                    zones = [where for where in wheres if _is_zone_level_where(where)]
                    if zones:
                        LOGGER.debug(
                            "%s Zones %s: %s refuses the zone form of the actuator request; asking only actuators",
                            self.log_id,
                            zones,
                            target.model,
                        )
                        wheres = [where for where in wheres if not _is_zone_level_where(where)]
                for where in wheres:
                    # One zone the builder rejects must not drop the others of the batch.
                    try:
                        frames.append(_zone_request_frame(where, dimension))
                    except ValueError as err:
                        LOGGER.warning("%s Zone %s: dimension %s not requested: %s", self.log_id, where, dimension, err)
            if not frames:
                continue  # every item skipped (refusing gateway): no empty sweep
            try:
                if self._last_event_frame_at > 0:
                    await self._wait_for_bus_quiet(self._last_event_frame_at)
                delivered = await self.send_paced(frames)
                if isinstance(delivered, list) and ALL_ACTUATORS_REQUEST in delivered:
                    # Asked for every zone: the zones added in the next moments are served too.
                    self._all_actuators_swept_at = target._all_actuators_swept_at = time.monotonic()
            except Exception:  # a transport error must not end the task silently (#593)
                LOGGER.exception("%s Zone requests %s could not be sent", self.log_id, frames)

    async def read_zone_temperature(self, where: str) -> bool | None:
        """Ask one heating zone for its temperature (``*#4*Z*0##``) once the bus is quiet.

        ``True``: the gateway acknowledged the read; the zone's frame, if any, was dispatched.
        ``False``: the gateway answered with a NACK: nothing answers for that zone (2.1 s on an
        F461, about 9 s on an F455).
        ``None``: not known. The read timed out, came back out of step or was not written. A
        MyHomeServer1 walks its own zone list every few minutes through a single-threaded
        daemon (see ``WHO4_RECOVERY_QUIET_WINDOWS``); a read sent meanwhile waits behind it. On
        2026-11-18 ``*#4*47*0##`` and ``*#4*70*0##`` got no ACK or NACK within 12.5 s while
        54-89 WHO 4 frames per 5 s were on the bus, and were acknowledged in 0.14 s and 0.21 s
        once it had been quiet for 4 s. So the bus is given time to go quiet before the read
        and after one that failed, and the caller may ask again.
        """
        cmd = OWNCommand.parse(_zone_request_frame(where, 0))
        if cmd is None:  # pragma: no cover - a validated, well-formed frame
            return None
        if self._last_event_frame_at > 0:
            await self._wait_for_bus_quiet(self._last_event_frame_at)
        written: object = await self.send_status_request(cmd)
        await self._wait_for_write(written, cmd)
        if not isinstance(written, asyncio.Future):
            return None  # a gateway double without delivery information
        if not written.done():
            # Still queued or unanswered: withdrawn, so it does not go out next to the read
            # the caller repeats (the sender skips a cancelled frame).
            written.cancel()
        if written.done() and not written.cancelled() and written.exception() is None:
            await self._wait_for_bus_quiet(time.monotonic())
            return True
        if getattr(written, "_gateway_nack", False):
            return False
        await self._wait_for_who4_recovery(written)
        return None

    async def wait_for_zone_requests(self) -> None:
        """Let same-turn zone dimension requests coalesce and wait for their paced run."""
        await asyncio.sleep(0)
        while self._zone_request_task is not None and not self._zone_request_task.done():
            await asyncio.gather(self._zone_request_task, return_exceptions=True)

    def _has_status_consumer(self, message: OWNCommand) -> bool:
        """Whether ``send_status_request(message)`` has an active worker, target, or test double."""
        if self.sending_workers:
            return True
        if self._delegated_target(message) is not None or self._failover_target(message) is not None:
            return True
        bound = getattr(self.send_status_request, "__func__", None)
        return bound is not MyHOMEGatewayHandler.send_status_request

    async def pace_startup_status_write(self, written: Any, message: OWNCommand) -> None:
        """Wait for a startup WHO 4 status query and bus quiet when a consumer is active."""
        if not self._has_status_consumer(message):
            return
        await self._wait_for_write(written, message)
        await self._wait_for_bus_quiet(time.monotonic())
        await self._wait_for_who4_recovery(written)

    @property
    def humidity_request_refused(self) -> bool:
        """Whether this gateway drops humidity requests (``*#4*Z*60##``) instead of forwarding them.

        The request goes where ``send_status_request`` sends it: a secondary gateway WHO 4 is
        delegated to, or the warm standby while this primary is down, decides with its own
        profile and table. The OWNd profile flag wins when it exists; otherwise the
        oracle-derived table.
        """
        target = self._who4_target()
        if target is not self:
            return target.humidity_request_refused
        supported = getattr(getattr(self.gateway, "profile", None), "supports_heating_humidity_request", None)
        if isinstance(supported, bool):
            return not supported
        return self._model_key in HUMIDITY_REQUEST_REFUSED

    @property
    def general_zone_request_refused(self) -> bool:
        """Whether this gateway drops the general heating status request (``*#4*0##``)."""
        supported = getattr(getattr(self.gateway, "profile", None), "supports_general_heating_status", None)
        if isinstance(supported, bool):
            return not supported
        return self._model_key in GENERAL_ZONE_REQUEST_REFUSED

    @property
    def sound_status_request_refused(self) -> bool:
        """Whether this gateway drops the sound status request (``*#16*0*5##``)."""
        if not self._profile_supports_who(16):
            return True
        return self._model_key in SOUND_STATUS_REQUEST_REFUSED

    @property
    def zone_actuator_request_refused(self) -> bool:
        """Whether this gateway refuses the zone form of the actuator request (``*#4*Z*20##``) only.

        Decided by the gateway the request goes through, like ``humidity_request_refused``.
        """
        target = self._who4_target()
        if target is not self:
            return target.zone_actuator_request_refused
        return self._model_key in ZONE_ACTUATOR_REQUEST_REFUSED

    @property
    def silent_zone_poll_needed(self) -> bool:
        """Whether a zone still silent after the startup sweep asks its own state once.

        On every gateway that serves its zones, whatever its model: what a zone is asked when
        it is added differs per gateway, and the poll only asks for what none of that brought
        (#466 on an F455, #932 on an MH201). A passive follower (a standby, or a secondary
        without WHO 4) has no zones of its own to ask.
        """
        return not (self.is_follower and 4 not in self.delegated_whos)

    @property
    def knob_read_in_silent_zone_poll(self) -> bool:
        """Whether the knob read (``*#4*Z*13##``) waits for the silent-zone poll (``KNOB_READ_IN_SILENT_ZONE_POLL``).

        Decided like ``silent_zone_poll_needed``: by the gateway the reads go through, and a
        passive follower has no zones of its own to ask.
        """
        if self.is_follower and 4 not in self.delegated_whos:
            return False
        return self._who4_target()._model_key in KNOB_READ_IN_SILENT_ZONE_POLL

    @property
    def silent_zone_actuator_read_refused(self) -> bool:
        """Whether the silent-zone poll leaves the actuator read (``*#4*Z#1*20##``) out.

        ``actuator_request_refused`` of the gateway the reads go through (``_who4_target``).
        """
        return self._who4_target().actuator_request_refused

    @property
    def zone_temperature_poll_needed(self) -> bool:
        """Whether each zone must ask for its temperature (``*#4*Z*0##``) itself.

        Only on SCS-only gateways (no ``*#4*Z##``) whose startup sweep brings no zone
        temperature; elsewhere the zone status poll or the sweep reports it.
        """
        if self.supports_zone_status:
            return False
        if self._model_key in KNOB_READ_IN_SILENT_ZONE_POLL:
            # The F455: its zones ask nothing when they are added and nothing every five minutes
            # (#674: a read on a channel without a probe holds its command session ~9 s before
            # the NACK). Its temperature read goes out once, inside the silent-zone poll. Stated
            # before the profile flag on purpose: the F455 refuses *#4*0## too, so a profile
            # that says ``supports_general_heating_status = False`` must not turn this poll on.
            return False
        supported = getattr(getattr(self.gateway, "profile", None), "supports_general_heating_status", None)
        if isinstance(supported, bool):
            # The OWNd profile wins when it states the flag: a sweep the gateway answers makes
            # the per-zone poll pointless, whatever the fallback table says.
            return not supported
        return self._model_key in ZONE_TEMPERATURE_POLL

    @property
    def interface_zone_request_refused(self) -> bool:
        """Whether WHO 4 requests behind an ``#4#II`` interface are dropped, misrouted, or crash the gateway.

        Zero of the twelve emulated gateways in own-firmware-oracle (``findings/thermo-zone-requests.md``)
        emit ``$06EC`` / ``$06E4`` interface-routed SCS frames for WHO 4: gateways either NACK/drop them,
        truncate ``Z#4#II`` at ``#`` and cross-poll local zone ``Z`` on the main bus, or on MH202 ``010024``
        misparse trailing ``#4#01`` as local zone 4 actuator 1 and crash ``coso`` with ``SIGSEGV`` on
        ``*#4*Z#4#01##`` (including before WHO 13 settles while the profile is still ``Generic``).
        Delegates through ``_who4_target()``; an explicit bool ``supports_interface_zone_requests`` on the
        OWNd profile overrides this default.
        """
        target = self._who4_target()
        if target is not self:
            return target.interface_zone_request_refused
        supported = getattr(getattr(self.gateway, "profile", None), "supports_interface_zone_requests", None)
        if isinstance(supported, bool):
            return not supported
        return True

    @property
    def profile(self) -> Any:
        """Return gateway profile."""
        return self.gateway.profile

    @property
    def command_session_idle_timeout(self) -> float:
        """Idle timeout before releasing the command session socket."""
        profile = getattr(self.gateway, "profile", None)
        profile_timeout = getattr(profile, "command_session_idle_timeout", None) if profile else None
        idle_timeout_default = float(COMMAND_SESSION_IDLE_TIMEOUT)
        return float(profile_timeout) if profile_timeout is not None else idle_timeout_default

    async def test(self) -> dict[str, Any]:
        """Test gateway connection."""
        result: dict[str, Any] = await OWNSession(gateway=self.gateway, logger=LOGGER).test_connection()
        return result

    @callback
    def _on_event_connection_state_change(self, connected: bool) -> None:
        """Gate commands and publish sustained event-session availability."""
        was_connected = self.is_connected
        self.is_connected = connected
        self._event_runner.is_connected = connected
        self._update_event_watchdog(progress=False)
        if connected:
            if self._failover_active:
                self._clear_failover()
                LOGGER.info(
                    "%s Primary gateway reconnected; warm standby failover deactivated, returning to primary gateway.",
                    self.log_id,
                )
            self._event_session_ready.set()
            self._who1013["pending"] = False
            self._who13["pending_code"] = False
            self._who13["pending_firmware"] = False
            if self._unavailable_timer is not None:
                self._unavailable_timer()
                self._unavailable_timer = None
            if not self._available:
                self._available = True
                LOGGER.info("%s Gateway available again.", self.log_id)
                self._notify_availability()
            if not was_connected:
                # Frames may have been missed while the event session was down: the zones'
                # actuator requests that follow are not taken as served by the startup sweep,
                # and the central unit's season is not known until it reports again (a zone is
                # not compared with a season that may have changed meanwhile).
                self._all_actuators_swept_at = 0.0
                self._central_status_swept_at = 0.0
                self.central_season = None
            if not was_connected and self._initial_discovery_done.is_set():
                runtime = getattr(self.config_entry, "runtime_data", None)
                expose_mgr = getattr(runtime, "expose_manager", None)
                sync_fn = getattr(expose_mgr, "async_sync_all", None)
                if inspect.iscoroutinefunction(sync_fn):
                    self.hass.async_create_task(sync_fn())
                # Actuators that switched while the session was down sent their frame to nobody.
                for where in list(self._actuator_zones):
                    self.request_zone_dimension(where, 20)
            return

        self._event_session_ready.clear()
        if self._terminate_listener:
            return
        if self._available and self._unavailable_timer is None:
            LOGGER.warning(
                "%s Gateway connection lost; marking unavailable in %ss if not recovered.",
                self.log_id,
                AVAILABILITY_GRACE,
            )
            self._unavailable_timer = async_call_later(
                self.hass,
                AVAILABILITY_GRACE,
                self._mark_unavailable,
            )

    @callback
    def _mark_unavailable(self, _now: Any) -> None:
        """Mark the gateway unavailable after the reconnect grace period."""
        if self._unavailable_timer is not None:
            self._unavailable_timer()
            self._unavailable_timer = None
        if self.is_connected or not self._available:
            return
        self._available = False
        self._evaluate_failover()
        if self.available:  # carried by the warm standby; refresh entities whose standby coverage differs
            async_dispatcher_send(self.hass, self.availability_signal)
            return
        LOGGER.warning(
            "%s Gateway unavailable (outage exceeded %ss).",
            self.log_id,
            AVAILABILITY_GRACE,
        )
        self._notify_availability()

    @callback
    def _update_event_watchdog(self, *, progress: bool) -> None:
        """Arm the stall deadline while disconnected, disarm it while connected."""
        self._event_runner._update_event_watchdog(progress=progress)

    async def listening_loop(self) -> None:
        """Run the event session, recreating it whenever it dies or stalls."""
        await self._event_runner.listening_loop()

    def profile_supports_who(self, who: int) -> bool:
        """Return whether the startup sweep asks this gateway's profile about a WHO."""
        return self._profile_supports_who(who)

    def _profile_supports_who(self, who: int) -> bool:
        """Return whether the gateway profile advertises a WHO subsystem (True when unknown).

        The H4684's WHO 22 from firmware 6.0.0 comes from the firmware-aware OWNd profile
        (``_profile_for_model(model, firmware)``, refreshed on ``*#13**16*V*R*B##``).
        """
        profile = getattr(self.gateway, "profile", None)
        if who in (6, 8):
            if self._model_key in DOOR_ENTRY_AV_MODELS or self._model_key in DOOR_ENTRY_MH_BRIDGE_AV_MODELS:
                return True
            if self._model_key in DOOR_ENTRY_WHO6_ONLY_MODELS:
                return who == 6
            if self._model_key in DOOR_ENTRY_WHO8_ONLY_MODELS:
                return who == 8
            if self._model_key in DOOR_ENTRY_UNSUPPORTED_MODELS:
                return False
            if isinstance(profile, GenericGatewayProfile):
                return True
        supports = getattr(profile, "supports_who", None)
        if not callable(supports):
            return True
        try:
            return bool(supports(who))
        except Exception:  # pragma: no cover - defensive against foreign profile objects
            return True

    def _natively_hears_who(self, who: int) -> bool:
        """Return whether this gateway hears ``who`` on its local bus without an external F422 bridge."""
        if who in (6, 8):
            if self._model_key in DOOR_ENTRY_AV_MODELS:
                return True
            return who == 8 and self._model_key in DOOR_ENTRY_WHO8_ONLY_MODELS
        return self._profile_supports_who(who)

    async def _process_message(self, message: Any) -> None:
        """Process a received message and dispatch to Home Assistant."""
        await self._event_dispatcher.process_message(message)

    async def sending_loop(self, worker_id: int) -> None:
        """Run sending loop for worker."""
        await self._command_pool.sending_loop(worker_id)

    @property
    def initial_discovery_pending(self) -> bool:
        """Whether the startup sweep is still running."""
        return not self._initial_discovery_done.is_set()

    @property
    def sweep_active(self) -> bool:
        """Whether a paced bus sweep is currently running."""
        return self._active_sweeps > 0

    def _begin_sweep(self) -> int:
        """Mark a paced sweep as active so newly discovered entities defer polls.

        Returns the epoch to hand back to ``_end_sweep``.
        """
        self._active_sweeps += 1
        self._sweep_idle.clear()
        return self._sweep_epoch

    def _end_sweep(self, epoch: int | None = None) -> None:
        """Mark a paced sweep as completed and unblock deferred polls when idle.

        A sweep begun before close_listener() reset the count (an older ``epoch``)
        no longer counts.
        """
        if epoch is not None and epoch != self._sweep_epoch:
            return
        self._active_sweeps = max(0, self._active_sweeps - 1)
        if self._active_sweeps == 0:
            self._sweep_idle.set()

    def note_event_frame(self) -> None:
        """Record that a frame just arrived on the event session."""
        self._last_event_frame_at = time.monotonic()

    async def _wait_for_bus_quiet(self, since: float) -> None:
        """Wait until no event frame arrived for BUS_QUIET_PERIOD since ``since``, at most BUS_QUIET_CAP."""
        deadline = time.monotonic() + BUS_QUIET_CAP
        while True:
            now = time.monotonic()
            if now >= deadline:
                LOGGER.debug("%s Bus still busy after %.0fs; sending the next request", self.log_id, BUS_QUIET_CAP)
                return
            quiet_since = max(since, self._last_event_frame_at)
            remaining = quiet_since + BUS_QUIET_PERIOD - now
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, deadline - now))

    async def _wait_for_write(self, written: Any, message: OWNCommand) -> None:
        """Wait, at most PACED_WRITE_TIMEOUT, until a queued frame was written or dropped."""
        if not isinstance(written, asyncio.Future):
            return
        await asyncio.wait({written}, timeout=PACED_WRITE_TIMEOUT)
        if not written.done():
            LOGGER.warning(
                "%s `%s` not written after %.0fs; sending the next request anyway",
                self.log_id,
                message,
                PACED_WRITE_TIMEOUT,
            )

    async def _wait_for_who4_recovery(self, written: Any) -> None:
        """Wait for an active bus sweep to finish when a WHO 4 status read timed out or desynced (#886).

        Bounded to ``WHO4_RECOVERY_QUIET_WINDOWS`` extra bus-quiet waits so an external
        ``bt_thermo_manager`` sweep can drain ``bt_termo``'s single-threaded SCS queue
        without blocking indefinitely when autonomous bus traffic never goes quiet.
        """
        if not isinstance(written, asyncio.Future):
            return
        if not (
            getattr(written, "_gateway_desync", False)
            or getattr(written, "_command_timeout", False)
        ):
            return
        for _ in range(WHO4_RECOVERY_QUIET_WINDOWS):
            if (
                self._last_event_frame_at <= 0
                or (time.monotonic() - self._last_event_frame_at) >= BUS_QUIET_PERIOD
            ):
                return
            await self._wait_for_bus_quiet(self._last_event_frame_at)

    async def send_paced(self, frames: Iterable[str], *, status_request: bool = True) -> list[str]:
        """Send general requests one at a time, each once the bus finished answering the last.

        Returns the frames the gateway did not refuse or lose: acknowledged, or written through
        a sender that tells nothing about the delivery.
        """
        epoch = self._begin_sweep()
        delivered: list[str] = []
        try:
            for frame in frames:
                cmd = OWNCommand.parse(frame)
                if cmd is None:
                    continue
                written: object = await (self.send_status_request(cmd) if status_request else self.send(cmd))
                await self._wait_for_write(written, cmd)
                if not isinstance(written, asyncio.Future) or (
                    written.done() and not written.cancelled() and written.exception() is None
                ):
                    delivered.append(frame)
                await self._wait_for_bus_quiet(time.monotonic())
                if getattr(cmd, "who", None) == 4:
                    await self._wait_for_who4_recovery(written)
        finally:
            self._end_sweep(epoch)
        return delivered

    def _refused_by_gateway(self, frame: str) -> bool:
        """Whether this gateway's firmware drops the startup request instead of forwarding it."""
        if frame == "*#4*0##":
            return self.general_zone_request_refused
        if frame == "*#16*0*5##":
            return self.sound_status_request_refused
        return False

    @property
    def unanswered_zone_read_is_cheap(self) -> bool:
        """Whether a WHO 4 read nothing answers costs about 2 s here (``STARTUP_CENTRAL_STATUS_REQUEST``).

        An F461 and a MyHomeServer1 NACK it after 2.1 s; an F455 holds the command session for
        about 9 s; the other models were not measured.
        """
        return self._model_key in STARTUP_CENTRAL_STATUS_REQUEST

    @property
    def asks_every_actuator_at_start(self) -> bool:
        """Whether the startup sweep asks every actuator in place of the general request it drops.

        Only there are the zones known from their actuators alone asked for their temperature
        (``climate_discovery.ZoneDiscovery.start_reads``): where the general request is
        forwarded every thermostat has answered it, and an address still known only from its
        actuator has none.
        """
        return self._model_key in STARTUP_ALL_ACTUATORS_REQUEST

    @property
    def central_status_just_asked(self) -> bool:
        """Whether the startup sweep asked the central unit's status in the last ``ALL_ACTUATORS_SWEEP_FRESH`` s.

        The central unit's entity is created from the answer and would ask the same again as
        it is added (live F461, 2026-11-18: ``*#4*#0##`` at 16:00:58.889 and at 16:01:08.227).
        """
        asked_at = self._central_status_swept_at
        return asked_at > 0 and time.monotonic() - asked_at < ALL_ACTUATORS_SWEEP_FRESH

    @property
    def actuator_only_reads_running(self) -> bool:
        """While ``climate_discovery`` reads the zones known only from their actuators."""
        return self._actuator_only_reads_running

    def actuator_only_reads(self, running: bool) -> None:
        """``climate_discovery`` starts or ends its reads of the zones known only from their actuators.

        Each zone it creates takes the actuator frames kept since the sweep's all-actuator
        request, so none of them asks for every actuator again, however long the run takes.
        Live on a MyHomeServer1 with 34 zones, 2026-11-18, before this: the sweep's request
        at 21:02:44.195, then one more for each zone created later than
        ``ALL_ACTUATORS_SWEEP_FRESH``, 15 between 21:04:05.313 and 21:04:45.393. At the end
        the sweep counts as fresh once more, for the zones the last reads created.
        """
        self._actuator_only_reads_running = running
        if not running and self._all_actuators_swept_at > 0:
            self._all_actuators_swept_at = time.monotonic()

    @callback
    def request_central_status(self) -> None:
        """Ask the central unit's status once a report about one of its zones shows it is there.

        The sweep asks it only where a request nothing answers is cheap
        (``STARTUP_CENTRAL_STATUS_REQUEST``). Elsewhere the central unit becomes known from its
        report about a zone (``*4*110*#3##``), and its quarter-hour push names zones only: on
        a live F455 alone on the bus, 2026-11-18, zones 2, 3 and 4 were found, no ``#0`` frame
        came, and the central unit had no entity. Once it is known to exist the request is
        answered (the F455 in 1.07 s on that plant; every emulated firmware puts it on the
        bus, own-firmware-oracle trace-corpus.tsv:50), and the answer creates its entity. Not
        sent when the sweep, or an earlier call, has just asked.
        """
        if self._central_status_task is None or self._central_status_task.done():
            run = self._ask_central_status()
            task: object = self.hass.async_create_background_task(run, name=f"myhome_{self.mac}_central_status")
            if isinstance(task, asyncio.Task):
                self._central_status_task = task
            else:
                run.close()  # a hass double that schedules nothing

    async def _ask_central_status(self) -> None:
        """After the sweep, under the start-up poll lock (an F455 mixes up crossing WHO 4 reads)."""
        await self.wait_for_initial_discovery()
        async with self._who4_startup_poll_lock:
            if self.central_status_just_asked:
                return
            delivered = await self.send_paced([CENTRAL_STATUS_REQUEST])
            if CENTRAL_STATUS_REQUEST in delivered:
                self._central_status_swept_at = time.monotonic()

    def _heating_startup_requests(self) -> list[str]:
        """What replaces ``*#4*0##`` at start on a gateway that drops it."""
        frames: list[str] = []
        if self._model_key in STARTUP_CENTRAL_STATUS_REQUEST:
            frames.append(CENTRAL_STATUS_REQUEST)
        if self._model_key in STARTUP_ALL_ACTUATORS_REQUEST:
            frames.append(ALL_ACTUATORS_REQUEST)
        return frames

    def _discovery_frames(self) -> list[str]:
        """The startup general requests this gateway sends, after topology and profile gates."""
        frames: list[str] = []
        for who, frame in DISCOVERY_REQUESTS:
            if getattr(self, "is_follower", False) is True and who not in getattr(self, "delegated_whos", set()):
                LOGGER.debug("%s Skipping WHO=%s discovery: follower gateway on shared bus.", self.log_id, who)
                continue
            if who in getattr(self, "delegated_away_whos", set()):
                LOGGER.debug("%s Skipping WHO=%s discovery: delegated to a secondary gateway.", self.log_id, who)
                continue
            if not self._profile_supports_who(who):
                LOGGER.debug(
                    "%s Skipping WHO=%s discovery: not supported by %s profile.",
                    self.log_id,
                    who,
                    self.gateway.model_name,
                )
                continue
            if self._refused_by_gateway(frame):
                LOGGER.debug("%s Skipping `%s`: %s firmware refuses it.", self.log_id, frame, self.gateway.model_name)
                if frame == "*#4*0##":
                    frames.extend(self._heating_startup_requests())
                continue
            frames.append(frame)
        return frames

    async def initial_discovery(self) -> None:
        """Send the startup sweep that discovers devices missing from the config."""
        frames = self._discovery_frames()
        try:
            delivered = await self.send_paced(frames)
            # Only a request the gateway took counts as asked: after a refused or lost one the
            # zones' own actuator requests still go out.
            swept = isinstance(delivered, list) and ALL_ACTUATORS_REQUEST in delivered
            if swept:
                self._all_actuators_swept_at = time.monotonic()
            if isinstance(delivered, list) and CENTRAL_STATUS_REQUEST in delivered:
                self._central_status_swept_at = time.monotonic()
            if swept and self.all_zone_request(20) == ALL_ACTUATORS_REQUEST:
                # The sweep has just asked every actuator: the zones that were waiting for it
                # with their own actuator request (``watch_zone_actuators``) are served, and a
                # second burst is not put on the bus (43 actuators on one MyHomeServer1 plant).
                # Still asked: a zone behind an interface, and an actuator named by a thermo
                # switch where the zone form is refused, as in ``_send_zone_requests``.
                own_actuators = self.zone_actuator_request_refused
                self._zone_requests = {
                    (where, dimension)
                    for where, dimension in self._zone_requests
                    if dimension != 20
                    or BUS_ROUTING in where
                    or (own_actuators and not _is_zone_level_where(where))
                }
        finally:
            self._initial_discovery_done.set()
        runtime = getattr(self.config_entry, "runtime_data", None)
        expose_mgr = getattr(runtime, "expose_manager", None)
        sync_fn = getattr(expose_mgr, "async_sync_all", None)
        if inspect.iscoroutinefunction(sync_fn):
            await sync_fn()

    async def wait_for_initial_discovery(self, timeout: float | None = None) -> None:
        """Wait until startup initial discovery and any active paced sweep finish."""
        if self._initial_discovery_done.is_set() and self._sweep_idle.is_set():
            return
        if timeout is None:
            timeout = len(DISCOVERY_REQUESTS) * (PACED_WRITE_TIMEOUT + BUS_QUIET_CAP)
        try:
            async with asyncio.timeout(timeout):
                if not self._initial_discovery_done.is_set():
                    await self._initial_discovery_done.wait()
                if not self._sweep_idle.is_set():
                    await self._sweep_idle.wait()
        except TimeoutError:
            LOGGER.debug("%s Timed out waiting for initial discovery", self.log_id)

    async def close_listener(self) -> bool:
        """Close event listener and cancel pending actions."""
        LOGGER.info("%s Closing event listener", self.log_id)
        if self._unavailable_timer is not None:
            self._unavailable_timer()
        self._resync_manager.cancel_all()
        self._echo_watchdog.cancel_all()
        if self._zone_request_task is not None:
            self._zone_request_task.cancel()
            self._zone_request_task = None
        if self._central_status_task is not None:
            self._central_status_task.cancel()
            self._central_status_task = None
        self._zone_requests.clear()
        self._unavailable_timer = None
        self.is_connected = False
        self._available = False
        self._clear_failover()
        self._active_sweeps = 0
        self._sweep_epoch += 1
        self._sweep_idle.set()
        self._initial_discovery_done.set()
        if self.is_standby:
            primary = self._get_primary_gateway()
            if primary is not None and not primary._available:
                primary._evaluate_failover()
                async_dispatcher_send(self.hass, primary.availability_signal)
        elif self.is_secondary and self.delegated_whos:
            primary = self._get_primary_gateway()
            if primary is not None:
                async_dispatcher_send(self.hass, primary.availability_signal)

        self._event_runner.close()
        self._command_pool.close()
        return True

    def async_queue_calibration(
        self,
        message: OWNCommand,
        guard: Callable[[], bool],
        command_lock: asyncio.Lock,
    ) -> asyncio.Future[float]:
        """Queue a calibration command with a guard checked at write time."""
        written: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        self.send_buffer.put_nowait(
            {
                "message": message,
                "is_status_request": False,
                "guard": guard,
                "command_lock": command_lock,
                "written": written,
            }
        )
        return written

    async def send(self, message: OWNCommand, *, is_cancel: bool = False) -> asyncio.Future[float]:
        """Queue a command; the returned future resolves to the monotonic write time."""
        delegated = self._delegated_target(message)
        if delegated is not None:
            if is_cancel:
                return await delegated.send(message, is_cancel=True)
            return await delegated.send(message)
        standby = self._failover_target(message)
        if standby is not None:
            if is_cancel:
                return await standby.send(message, is_cancel=True)
            return await standby.send(message)
        if is_cancel:
            return await self._command_pool.send(message, is_cancel=True)
        return await self._command_pool.send(message)

    async def send_fade_step(self, message: OWNCommand) -> asyncio.Future[float]:
        """Queue an intermediate software fade step; superseded by newer steps or commands."""
        delegated = self._delegated_target(message)
        if delegated is not None:
            return await delegated.send_fade_step(message)
        standby = self._failover_target(message)
        if standby is not None:
            return await standby.send_fade_step(message)
        return await self._command_pool.send_fade_step(message)

    async def send_status_request(self, message: OWNCommand) -> asyncio.Future[float]:
        """Queue a status request; the returned future resolves to the monotonic write time."""
        written = await self._send_status_request(message)
        if getattr(message, "who", None) == 2:
            for listener in tuple(self.automation_status_listeners):
                listener(message)
        return written

    async def _send_status_request(self, message: OWNCommand) -> asyncio.Future[float]:
        delegated = self._delegated_target(message)
        if delegated is not None:
            return await delegated.send_status_request(message)
        standby = self._failover_target(message)
        if standby is not None:
            return await standby.send_status_request(message)
        return await self._command_pool.send_status_request(message)

    def claim_in_flight_dimension_where(self, who: int, dimension: int) -> str | None:
        """Claim the sole point-to-point WHERE with an in-flight dimension request (#777)."""
        if who not in (13, 1013) and who in self.delegated_away_whos:
            sec = self._get_secondary_for_who(who)
            if sec is not None and sec.is_connected and peer_handles_who(sec, int(who)):
                return sec._command_pool.claim_in_flight_dimension_where(who, dimension)
        if who not in (13, 1013) and not self.is_connected:
            standby = self._get_standby_gateway()
            if standby is not None and standby.is_connected and standby._profile_supports_who(int(who)):
                return standby._command_pool.claim_in_flight_dimension_where(who, dimension)
        return self._command_pool.claim_in_flight_dimension_where(who, dimension)

    def _known_light_areas(self) -> list[str]:
        """Return list of known light areas."""
        return self._resync_manager.known_light_areas()

    def _schedule_resync(self, message: Any) -> None:
        """Schedule a debounced resync."""
        self._resync_manager.schedule_resync(message)

    async def _resync_broadcast(self, where: str) -> None:
        """Execute a resync broadcast."""
        await self._resync_manager.execute_resync(where)

    _execute_resync = _resync_broadcast
