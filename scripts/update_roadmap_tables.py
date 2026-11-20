#!/usr/bin/env python3
"""Update and verify the Real-World Trace Coverage tables and diagram in ROADMAP.md and docs/roadmap.md.

Derives gateway model coverage (Table 1), protocol subsystem / edge-scenario coverage (Table 2),
and the System Coverage Overview Mermaid schematic dynamically from:
1. Authentic on-wire captures and firmware-oracle fixtures in ``tests/fixtures/`` (``plants/`` + ``traces/``).
2. Supported gateway model constants in ``custom_components/myhome/const.py`` (``SUPPORTED_GATEWAY_MODELS``).

Pure Python standard library implementation so ``python scripts/sync_documentation.py --check``
produces identical results in full Home Assistant test environments and lightweight docs CI runners.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
CONST_PY = REPO_ROOT / "custom_components" / "myhome" / "const.py"
ROADMAP_MD = REPO_ROOT / "ROADMAP.md"
DOCS_ROADMAP_MD = REPO_ROOT / "docs" / "roadmap.md"

MERMAID_START_MARKER = "<!-- ROADMAP_COVERAGE_MERMAID_START -->"
MERMAID_END_MARKER = "<!-- ROADMAP_COVERAGE_MERMAID_END -->"

GATEWAY_TABLE_START_MARKER = "<!-- ROADMAP_GATEWAY_TABLE_START -->"
GATEWAY_TABLE_END_MARKER = "<!-- ROADMAP_GATEWAY_TABLE_END -->"

SUBSYSTEM_TABLE_START_MARKER = "<!-- ROADMAP_SUBSYSTEM_TABLE_START -->"
SUBSYSTEM_TABLE_END_MARKER = "<!-- ROADMAP_SUBSYSTEM_TABLE_END -->"

DIAGNOSTIC_ONLY_WHOS: frozenset[int] = frozenset({13, 130, 1000, 1001, 1004, 1013, 1022})
STANDARD_DISPLAY_WHOS: frozenset[int] = frozenset(
    {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 13, 14, 15, 16, 17, 18, 22, 24, 25, 1000, 1001, 1004, 1013, 1018, 1022}
)

_WHO_PREFIX_RE = re.compile(r"^\*#?(\d+)\*")
_OWN_FRAME_TOKEN_RE = re.compile(r"(?:^|(?<=[\s`])|(?<=##))(\*(?:(?!##)[^\s`])+##)")
_HA_LOG_RX_RE = re.compile(
    r"\[([^\]]+?) gateway - [^\]]*\] (?:Message received: |Collected command response )`([^`]+)`"
)
_BUNDLE_MODEL_RE = re.compile(r"-\s*\*\*Model:\*\*\s*([A-Za-z0-9/_-]+)")
_COMMENT_GATEWAY_RE = re.compile(r"#\s*Gateway:\s*([A-Za-z0-9/_-]+)")
_README_MODEL_RE = re.compile(
    r"\*\*(?:Gateway\s+)?Model\*\*:\s*(?:BTicino\s+|Legrand\s+)?([A-Za-z0-9/_-]+)",
    re.IGNORECASE,
)

# Map raw model aliases seen in fixtures or const.py to canonical model tokens
MODEL_CANONICAL_ALIASES: dict[str, str] = {
    "H/L4684": "H/L4684",
    "HL4684": "H/L4684",
    "L4684": "H4684",
    "LGRH4684": "H4684",
    "HW4890": "H4890",
    "LN4890A": "LN4890",
    "MHS1": "MyHomeServer1",
    "MYHOMESERVER1": "MyHomeServer1",
    # The 3488 HOMETOUCH is set up under the model name MyHomeServer1 and counted with it
    # (docs/trace-availability.md); ``g3488_*`` are its traces in who4_four_gateways_2026_11_18.
    "3488": "MyHomeServer1",
    "G3488": "MyHomeServer1",
    "F459": "F459",
    "F454": "F454",
    "F455": "F455",
    "F461": "F461",
    "MH202": "MH202",
    "MH201": "MH201",
    "MH200N": "MH200N",
    "MH200": "MH200",
    "F453AV": "F453AV",
    "F453": "F453",
    "F452": "F452",
    "F452V": "F452V",
    "H4890": "H4890",
    "AM4890": "AM4890",
    "LN4890": "LN4890",
    "067292": "067292",
    "573958": "573958",
    "078479": "078479",
    "H4684": "H4684",
}

_CASE_INSENSITIVE_MODELS: dict[str, str] = {
    k.lower(): v for k, v in MODEL_CANONICAL_ALIASES.items()
}


@dataclass(frozen=True)
class GatewayRoadmapRowSpec:
    """Declarative specification for a row in Table 1 (Gateways & Transports)."""

    key: str
    display_name: str
    mermaid_id: str
    mermaid_title: str
    models: tuple[str, ...]
    covered_note: str
    missing_target: str
    deferred_note: str = ""
    optional_submodel_families: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class SubsystemRoadmapRowSpec:
    """Declarative specification for a row in Table 2 (Subsystems & Scenarios)."""

    key: str
    display_name: str
    mermaid_id: str
    mermaid_title: str
    frame_regex: str
    covered_note: str
    missing_target: str
    include_calibrated_covers: bool = False
    untraced_status: str = "needed"  # "needed" | "synthetic"
    untraced_evidence: str = ""


GATEWAY_ROADMAP_ROWS: tuple[GatewayRoadmapRowSpec, ...] = (
    GatewayRoadmapRowSpec(
        key="myhomeserver1",
        display_name="**MyHomeServer1 (MHS1)**",
        mermaid_id="GW_MHS1",
        mermaid_title="MyHomeServer1",
        models=("MyHomeServer1",),
        covered_note="*None needed — cloud/local hybrid gateway plant active in CI.*",
        missing_target="**Production plant trace**: Capture from a MyHomeServer1 installation.",
    ),
    GatewayRoadmapRowSpec(
        key="f454",
        display_name="**F454**",
        mermaid_id="GW_F454",
        mermaid_title="F454",
        models=("F454",),
        covered_note="*None needed — high-speed multi-session IP webserver active in CI.*",
        missing_target="**Production plant trace**: Capture from an F454 webserver gateway.",
    ),
    GatewayRoadmapRowSpec(
        key="f455",
        display_name="**F455**",
        mermaid_id="GW_F455",
        mermaid_title="F455",
        models=("F455",),
        covered_note="*None needed — single-bus DIN IP gateway active in CI.*",
        missing_target="**Production plant trace**: Capture from an F455 basic gateway.",
    ),
    GatewayRoadmapRowSpec(
        key="f459",
        display_name="**F459**",
        mermaid_id="GW_F459",
        mermaid_title="F459",
        models=("F459",),
        covered_note="*None needed — WHO 1013 code 65 & WHO 4 capture active in CI.*",
        missing_target="**Production plant trace**: Capture from a BTicino F459 plant server.",
    ),
    GatewayRoadmapRowSpec(
        key="f461",
        display_name="**F461 Web Server**",
        mermaid_id="GW_F461",
        mermaid_title="F461",
        models=("F461",),
        covered_note="*None needed — DIN Ethernet Web Server (DALI DT8 & fancoils) active in CI.*",
        missing_target="**Production plant trace**: Capture from an F461 DIN web server.",
    ),
    GatewayRoadmapRowSpec(
        key="mh202",
        display_name="**MH202**",
        mermaid_id="GW_MH202",
        mermaid_title="MH202",
        models=("MH202",),
        covered_note="*None needed — modern scenario programmer plant active in CI.*",
        missing_target="**Production plant trace**: Residential bus traffic through an MH202 scenario programmer.",
    ),
    GatewayRoadmapRowSpec(
        key="mh201",
        display_name="**MH201**",
        mermaid_id="GW_MH201",
        mermaid_title="MH201",
        models=("MH201",),
        covered_note="*None needed — single-worker scenario programmer plant active in CI.*",
        missing_target="**Production plant trace**: Capture from an MH201 gateway.",
    ),
    GatewayRoadmapRowSpec(
        key="mh200n",
        display_name="**MH200N**",
        mermaid_id="GW_MH200N",
        mermaid_title="MH200N",
        models=("MH200N",),
        covered_note="*None needed — second-generation scenario programmer active in CI.*",
        missing_target="**Production plant trace**: Capture from an MH200N gateway.",
    ),
    GatewayRoadmapRowSpec(
        key="mh200",
        display_name="**MH200** *(Legacy)*",
        mermaid_id="GW_MH200",
        mermaid_title="MH200",
        models=("MH200",),
        covered_note="*None needed — legacy single-session plant & F441M audio active in CI.*",
        missing_target="**Production plant trace**: Capture from a legacy MH200 gateway.",
    ),
    GatewayRoadmapRowSpec(
        key="f452_f453_f453av",
        display_name="**F452 / F453 / F453AV**",
        mermaid_id="GW_F453",
        mermaid_title="F452 / F453 / F453AV",
        models=("F453AV", "F453", "F452", "F452V"),
        covered_note="*None needed — physical F453 & F453AV gateways active in CI.*",
        missing_target="**General trace**: Residential bus captures from F452 / F453 / F453AV gateways.",
        optional_submodel_families=(("F452 / F452V", ("F452", "F452V")),),
    ),
    GatewayRoadmapRowSpec(
        key="h4890_family",
        display_name="**H4890 / AM4890 / 067292**",
        mermaid_id="GW_H4890",
        mermaid_title="H4890 / AM4890 / 067292",
        models=("H4890", "AM4890", "LN4890", "067292", "573958", "078479"),
        covered_note='*None needed — 3.5" touchscreen IP gateways (BTicino & Legrand) active in CI.*',
        missing_target="**Touchscreen gateway trace**: Bus capture from an H4890 / AM4890 / 067292 display.",
    ),
    GatewayRoadmapRowSpec(
        key="h4684_family",
        display_name="**H4684 / H/L4684**",
        mermaid_id="GW_H4684",
        mermaid_title="H4684 / H/L4684",
        models=("H4684", "H/L4684"),
        covered_note="*None needed — colour touchscreen IP gateway (FW 2.x & 6.x) active in CI.*",
        missing_target="**Touchscreen gateway trace**: Bus capture from an H4684 / H/L4684 display.",
    ),
    GatewayRoadmapRowSpec(
        key="legrand_3578",
        display_name="**Legrand 3578 USB/Serial**",
        mermaid_id="GW_3578",
        mermaid_title="Legrand 3578",
        models=(),
        covered_note="*Physical OpenZigBee USB/Serial stream active in CI.*",
        missing_target=(
            "**Hardware tester & raw serial stream**: Active tester + raw byte capture "
            "([Issue #466](https://github.com/OpenWebNet-HA/MyHOME/issues/466)) from a physical "
            "OpenZigBee installation (`WHERE=<id>#9`)."
        ),
        deferred_note=(
            "Deferred out of `v2.0.0` "
            "([Discussion #879](https://github.com/orgs/OpenWebNet-HA/discussions/879))"
        ),
    ),
)

SUBSYSTEM_ROADMAP_ROWS: tuple[SubsystemRoadmapRowSpec, ...] = (
    SubsystemRoadmapRowSpec(
        key="who1_lighting",
        display_name="**Lighting (WHO = 1) — Relays & Dimmers**",
        mermaid_id="SUB_LIGHT",
        mermaid_title="Lighting & Dimmers (WHO 1)",
        frame_regex=r"^(?:\*1\*(?:[0-9]|10|19|2[0-9]|3[0-9]|1000#\d+)\*[^*]+##|\*#1\*[^*]+\*#?(?:1|4|13)\*)",
        covered_note="*Baseline covered across standard, 4-digit, and F418/F418U2 dimmers.*",
        missing_target="**Lighting trace**: Standard relay and dimmer status frames (`WHO = 1`).",
    ),
    SubsystemRoadmapRowSpec(
        key="who1_dali",
        display_name="**Lighting (WHO = 1) — DALI Tunable White & HSV Color**",
        mermaid_id="SUB_DALI",
        mermaid_title="DALI DT8 & HSV (WHO 1)",
        frame_regex=r"^\*#1\*[^*]+\*#?(?:12|14)\*",
        covered_note="*Baseline covered (`Dimension 12` HSV & `Dimension 14` Kelvin/mireds).*",
        missing_target="**DALI ballast trace**: `Dimension 12` HSV or `Dimension 14` Tunable White frames.",
    ),
    SubsystemRoadmapRowSpec(
        key="who1_timers",
        display_name="**Lighting (WHO = 1) — Native DIN Timers**",
        mermaid_id="SUB_TIMER",
        mermaid_title="DIN Bus Timers (WHO 1)",
        frame_regex=r"^(?:\*1\*1[1-8]\*[^*]+##|\*#1\*[^*]+\*#?2\*)",
        covered_note=(
            "*Preset temporization & `Dimension 2` replies covered; optional mid-countdown "
            "`*#1*WHERE*2*H*M*S##` (`H/M/S > 0`) trace welcomed.*"
        ),
        missing_target=(
            "**Actuator countdown trace**: Physical F411 relay executing `Dimension 2` "
            "(`*#1*WHERE*#2*H*M*S##`) or preset temporization (`WHAT = 11..18`)."
        ),
    ),
    SubsystemRoadmapRowSpec(
        key="who1_groups",
        display_name="**Lighting (WHO = 1) — Groups & General Broadcasts (P7)**",
        mermaid_id="SUB_GRP",
        mermaid_title="Lighting Groups (WHO 1)",
        frame_regex=r"^\*1\*\d+\*(?:0|#\d+)(?:#4#\d+)?##$",
        covered_note="*None needed — group (`#G`) and general (`WHERE=0`) resync engine active in CI.*",
        missing_target="**Group broadcast trace**: Group (`WHERE=#G`) and general (`WHERE=0`) bursts.",
    ),
    SubsystemRoadmapRowSpec(
        key="who2_timed_cal",
        display_name="**Covers (WHO = 2) — Travel-Time Positioning & On-Bus Calibration**",
        mermaid_id="SUB_COV_V",
        mermaid_title="Covers Timed & Calibrated (WHO 2)",
        frame_regex=r"^\*2\*(?:[012]|1000#[012])\*[^*]+##$",
        include_calibrated_covers=True,
        covered_note=(
            "*Baseline covered (stopwatch `myhome.calibrate_cover` runs, `cover_travel_times` "
            "profiles, and timed slat pulses active in CI).*"
        ),
        missing_target="**Shutter run trace**: Up/down/stop (`*2*1/2/0*WHERE##`) and travel-time runs.",
    ),
    SubsystemRoadmapRowSpec(
        key="who2_dim10_tilt",
        display_name="**Covers (WHO = 2) — Advanced Position (`Dim 10 / 11`)**",
        mermaid_id="SUB_COV_H",
        mermaid_title="Covers Dim 10 / 11 (WHO 2)",
        frame_regex=r"^(?:\*#2\*[^*]+\*#?(?:10|11)(?:[#*])(?!\d+\*255\*)|\*2\*(?:1[012]#|1000#1[012]#)[^*]+\*[^*]+##$)",
        covered_note="*Baseline covered (`F401` / `LN4661M2` percentage position; the slat angle is not on the bus).*",
        missing_target="**Advanced shutter trace**: `Dimension 10` / `11` position feedback.",
    ),
    SubsystemRoadmapRowSpec(
        key="who2_uncalibrated_255",
        display_name="**Covers (WHO = 2) — Uncalibrated Hardware Sentinel (`Dim 10` `LEVEL=255`)**",
        mermaid_id="SUB_COV_255",
        mermaid_title="Uncalibrated Dim 10=255 (WHO 2)",
        frame_regex=r"^\*#2\*[^*]+\*#?10\*\d+\*255\*",
        covered_note="*Uncalibrated `Dimension 10` `LEVEL=255` sentinel verified from physical hardware.*",
        missing_target=(
            "**Uncalibrated actuator trace**: Bus capture of an uncalibrated `F401`, `LN4672M2`, "
            "or `067557` reporting `*#2*WHERE*10*BS*255*...##` prior to *Push&Learn* end-stop learning."
        ),
        untraced_status="synthetic",
        untraced_evidence=(
            "Unit tests for `*#2*WHERE*10*BS*255*...##` in `tests/test_cover.py` "
            "(0 physical `LEVEL=255` `rx` frames in `tests/fixtures/`)"
        ),
    ),
    SubsystemRoadmapRowSpec(
        key="who4_zones",
        display_name="**Thermoregulation (WHO = 4) — Zone Thermostats, Probes & Humidity**",
        mermaid_id="SUB_THERMO_ZONES",
        mermaid_title="Zone Thermostats & Probes (WHO 4)",
        frame_regex=r"^(?:\*#4\*(?!#0)[^*]+\*#?(?:0|7|12|13|14|19|60)\*|\*4\*[^*]+\*(?!#0(?:\*|#|##))[^*]+##$)",
        covered_note="*Baseline covered (zone temperature `Dim 0`, setpoints `Dim 12/14`, knob offsets `Dim 13` & `Dim 60` humidity).*",
        missing_target="**Zone thermostat trace**: Zone temperature (`Dim 0`), setpoint (`Dim 12/14`), and humidity (`Dim 60`) frames.",
    ),
    SubsystemRoadmapRowSpec(
        key="who4_cu3550",
        display_name="**Thermoregulation (WHO = 4) — 99-Zone Central Unit 3550 (`#0`)**",
        mermaid_id="SUB_CU3550",
        mermaid_title="Central Unit 3550 (WHO 4)",
        frame_regex=r"^\*#?4\*(?:[^*]+\*)?#0(?:\*|##)",
        covered_note="*Baseline covered (`#0` master modes, `*4*3100*#0##` auto restore, & zone sync).*",
        missing_target="**99-zone central unit trace**: `#0` central unit frames from a 3550 installation.",
    ),
    SubsystemRoadmapRowSpec(
        key="who4_cu4695",
        display_name="**Thermoregulation (WHO = 4) — 4-Zone Central Unit 4695 (`#0#1`)**",
        mermaid_id="SUB_CU4695",
        mermaid_title="Central Unit 4695 (WHO 4)",
        frame_regex=r"^\*#?4\*(?:[^*]+\*)?#0#[1-9]",
        covered_note="*4-zone Central Unit 4695 (`#0#1`) verified from physical hardware.*",
        missing_target=(
            "**4-zone central unit trace**: Physical capture from a plant running a 4-zone "
            "`4695` / `HD4695` (`#0#1`) central unit."
        ),
        untraced_status="needed",
        untraced_evidence=(
            "Synthetic unit tests in `tests/test_climate.py` only "
            "(0 inbound `#0#1` `rx` frames in `tests/fixtures/`)"
        ),
    ),
    SubsystemRoadmapRowSpec(
        key="who4_fancoil",
        display_name="**Thermoregulation (WHO = 4) — Fancoils (`Dim 11 / 20`)**",
        mermaid_id="SUB_FANCOIL",
        mermaid_title="Fancoils Dim 11/20 (WHO 4)",
        frame_regex=r"^\*#4\*[^*]+\*#?(?:11\*|20\*[5-8]##)",
        covered_note="*Baseline covered (`Dimension 11` fan speeds & `Dimension 20` fan-coil states `5`–`8`).*",
        missing_target="**Fancoil trace**: `Dimension 11` fan speeds and `Dimension 20` 2-pipe/4-pipe states.",
    ),
    SubsystemRoadmapRowSpec(
        key="who5_alarm",
        display_name="**Burglar Alarm (WHO = 5)**",
        mermaid_id="SUB_ALARM",
        mermaid_title="Burglar Alarm (WHO 5)",
        frame_regex=r"^\*#?5\*",
        covered_note="*Baseline covered (physical `3486` / `MH202` partition & zone captures active in CI).*",
        missing_target="**Central unit alarm trace**: Arm/disarm/zone frames from `3485` / `3486` units.",
    ),
    SubsystemRoadmapRowSpec(
        key="who6_door",
        display_name="**Door Entry & Intercom Locks (WHO = 6 / 8)**",
        mermaid_id="SUB_DOOR",
        mermaid_title="Door Entry (WHO 6/8)",
        frame_regex=r"^\*#?(?:6|8)\*",
        covered_note="*Baseline covered (door lock release & video intercom call events active in CI).*",
        missing_target="**Intercom trace**: Door lock and call frames (`WHO = 6 / 8`).",
    ),
    SubsystemRoadmapRowSpec(
        key="who9_25_dry",
        display_name="**AUX & Dry Contacts (WHO = 9 / 25)**",
        mermaid_id="SUB_DRY",
        mermaid_title="AUX & Dry Contacts (WHO 9/25)",
        frame_regex=r"^(?:\*#?9\*|\*#?25\*3[0-9]#)",
        covered_note="*Baseline covered (`WHO = 9` auxiliary channels & `3477` / `F428` contact sensors).*",
        missing_target="**Dry contact trace**: `WHO = 9` AUX or `WHO = 25` `31#..38#` state transitions.",
    ),
    SubsystemRoadmapRowSpec(
        key="who15_25_cen",
        display_name="**CEN / CEN+ Pushbuttons & Rotary Triggers (WHO = 15 / 25)**",
        mermaid_id="SUB_CEN",
        mermaid_title="CEN / CEN+ Triggers (WHO 15/25)",
        frame_regex=r"^(?:\*#?15\*|\*#?25\*2[1-8]#)",
        covered_note="*Baseline covered (short, long-press, repeat & release active in CI); a `WHO = 25` rotary capture (`WHAT = 25..28`) is welcome.*",
        missing_target="**Pushbutton trace**: `WHO = 15` / `WHO = 25` `21#..25#` wall switch events.",
    ),
    SubsystemRoadmapRowSpec(
        key="who0_17_scenarios",
        display_name="**Scenarios & Schedulers (WHO = 0 / 17)**",
        mermaid_id="SUB_SCENARIOS",
        mermaid_title="Scenarios (WHO 0/17)",
        frame_regex=r"^(?:\*0\*(?:[1-9]|1[0-9]|20)\*|\*#?17\*)",
        covered_note="*Baseline covered (`WHO = 0` activation on an F454, `WHO = 17` frames on MH200 / MH200N).*",
        missing_target="**Scenario module trace**: `WHO = 0` / `WHO = 17` scenario activation frames.",
    ),
    SubsystemRoadmapRowSpec(
        key="who14_locks",
        display_name="**Actuator Maintenance Locks (WHO = 14)**",
        mermaid_id="SUB_LOCK14",
        mermaid_title="Actuator Locks (WHO 14)",
        frame_regex=r"^\*#?14\*",
        covered_note="*Baseline covered (hardware pushbutton lock/unlock status frames active in CI).*",
        missing_target="**Maintenance lock trace**: `WHO = 14` lock/unlock actuator frames.",
    ),
    SubsystemRoadmapRowSpec(
        key="who16_audio",
        display_name="**Sound System (WHO = 16) — Matrix & Streaming Proxy**",
        mermaid_id="SUB_WHO16",
        mermaid_title="Audio Matrix (WHO 16)",
        frame_regex=r"^\*#?16\*",
        covered_note="*Baseline covered (`F441` / `F441M` matrix routing, group takeover & ducking).*",
        missing_target="**Audio matrix trace**: `WHO = 16` amplifier & source status frames.",
    ),
    SubsystemRoadmapRowSpec(
        key="who22_audio",
        display_name="**Sound Diffusion (WHO = 22) — FM RDS Tuners & Active-Source Sync**",
        mermaid_id="SUB_WHO22",
        mermaid_title="Sound Diffusion & RDS (WHO 22)",
        frame_regex=r"^\*#?22\*",
        covered_note=(
            "*Baseline covered (`F500` / `F500N` FM RDS tuners on `WHO = 22`-only & dual-stack "
            "gateways, `4#<area>` active-source tracking, and `H4562` `WHO = 22` zone captures).*"
        ),
        missing_target="**WHO 22 sound trace**: `F500` RDS tuner or `WHO = 22` zone frames.",
    ),
    SubsystemRoadmapRowSpec(
        key="who18_energy",
        display_name="**Energy Management & Metering (WHO = 18)**",
        mermaid_id="SUB_ENERGY",
        mermaid_title="Energy Metering (WHO 18)",
        frame_regex=r"^\*#?18\*",
        covered_note="*Baseline covered (`F520` / `F521` / `3522` active power `W` and total/daily/monthly `Wh` energy counters).*",
        missing_target="**Energy meter trace**: `WHO = 18` power and cumulative energy frames.",
    ),
    SubsystemRoadmapRowSpec(
        key="who13_diag",
        display_name="**Gateway Clock, Identity & Diagnostics (WHO = 13 / 1001–1022)**",
        mermaid_id="SUB_DIAG",
        mermaid_title="Gateway Identity (WHO 13/1013)",
        frame_regex=r"^\*#?(?:13|1001|1004|1013|1022)\*",
        covered_note="*Baseline covered (`WHO = 13` device type/firmware/clock & `WHO = 1013` `OBJECT_MODEL`).*",
        missing_target="**Gateway diagnostic trace**: `WHO = 13` and `WHO = 1013` identity exchanges.",
    ),
    SubsystemRoadmapRowSpec(
        key="f422_router",
        display_name="**F422 Cross-Bus Router (`#4#<bus>`)**",
        mermaid_id="SUB_ROUTER",
        mermaid_title="F422 Cross-Bus (#4#bus)",
        frame_regex=r"^\*#?[12]\*.*#4#\d+",
        covered_note="*None needed — physical F422 local-bus routing (`#4#01`, `#4#02`, ...) active in CI.*",
        missing_target="**Cross-bus router trace**: Frames routed across `#4#<bus>` F422 interfaces.",
    ),
)

GATEWAY_TABLE_FOOTER = (
    "*Table 1 is automatically generated and verified from physical bus captures in "
    "`tests/fixtures/` and supported models in `custom_components/myhome/const.py` "
    "by `scripts/sync_documentation.py`.*"
)

SUBSYSTEM_TABLE_FOOTER = (
    "*Table 2 is automatically generated and verified from inbound (`rx`) OpenWebNet frames "
    "and calibration bundles in `tests/fixtures/` by `scripts/sync_documentation.py`.*"
)


@dataclass
class FrameRecord:
    """Single parsed OpenWebNet frame occurrence inside the fixture corpus."""

    raw: str
    who: int
    direction: str  # "rx" | "tx"
    gateway: str
    artifact_label: str
    file_rel: str
    is_oracle: bool


@dataclass
class GatewayModelStats:
    """Aggregated physical vs oracle statistics for a single canonical gateway model."""

    phys_files: set[str] = field(default_factory=set)
    phys_rx_frames: int = 0
    phys_whos: set[int] = field(default_factory=set)
    phys_artifacts: dict[str, int] = field(default_factory=dict)
    oracle_files: set[str] = field(default_factory=set)
    oracle_rx_frames: int = 0
    oracle_whos: set[int] = field(default_factory=set)
    oracle_artifacts: dict[str, int] = field(default_factory=dict)


@dataclass
class FixtureCorpusSummary:
    """Complete deterministic summary of all trace fixtures under tests/fixtures/."""

    rx_frames: list[FrameRecord] = field(default_factory=list)
    by_model: dict[str, GatewayModelStats] = field(default_factory=dict)
    calibrated_cover_bundles: list[tuple[str, int]] = field(default_factory=list)


def normalize_gateway_model(raw_model: str | None) -> str:
    """Normalize a gateway model string from JSON/log metadata to its canonical token."""
    if not raw_model:
        return ""
    cleaned = raw_model.strip()
    if not cleaned:
        return ""
    if cleaned in MODEL_CANONICAL_ALIASES:
        return MODEL_CANONICAL_ALIASES[cleaned]
    lowered = cleaned.lower()
    if lowered in _CASE_INSENSITIVE_MODELS:
        return _CASE_INSENSITIVE_MODELS[lowered]
    no_slash = cleaned.replace("/", "").lower()
    if no_slash in _CASE_INSENSITIVE_MODELS:
        return _CASE_INSENSITIVE_MODELS[no_slash]
    return cleaned


def extract_who(raw_frame: str) -> int | None:
    """Extract integer WHO from a raw OpenWebNet frame (excluding ACK/NACK ``*#*1##``)."""
    frame = raw_frame.strip()
    if not frame.startswith("*") or not frame.endswith("##"):
        return None
    m = _WHO_PREFIX_RE.match(frame)
    if not m:
        return None
    return int(m.group(1))


def extract_supported_models_from_const(const_py: Path = CONST_PY) -> list[str]:
    """Extract SUPPORTED_GATEWAY_MODELS from custom_components/myhome/const.py via AST."""
    if not const_py.exists():
        return []
    tree = ast.parse(const_py.read_text(encoding="utf-8"), filename=str(const_py))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SUPPORTED_GATEWAY_MODELS":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        return [
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        ]
    return []


def _artifact_label_for_file(file_path: Path, fixtures_dir: Path) -> str:
    """Return a clean repository-relative fixture directory (or file) path for citations."""
    rel = file_path.relative_to(fixtures_dir).as_posix()
    parts = rel.split("/")
    if len(parts) >= 3 and parts[0] in ("plants", "traces"):
        return f"tests/fixtures/{parts[0]}/{parts[1]}/"
    return f"tests/fixtures/{rel}"


_KNOWN_CANONICAL_MODELS: frozenset[str] = frozenset(MODEL_CANONICAL_ALIASES.values())
_FREE_TEXT_MODEL_TOKENS: tuple[str, ...] = (
    "MyHomeServer1",
    "F453AV",
    "F452V",
    "F452",
    "F453",
    "F454",
    "F455",
    "F459",
    "F461",
    "MH200N",
    "MH200",
    "MH201",
    "MH202",
    "H/L4684",
    "HL4684",
    "L4684",
    "H4684",
    "LN4890A",
    "LN4890",
    "AM4890",
    "H4890",
    "067292",
    "573958",
    "078479",
)


def _extract_model_from_free_text(text: str) -> str:
    """Return the earliest canonical gateway model mentioned in free text (longest match on tie)."""
    best: tuple[int, int, str] | None = None
    for model_tok in _FREE_TEXT_MODEL_TOKENS:
        m = re.search(rf"(?<![A-Za-z0-9]){re.escape(model_tok)}(?![A-Za-z0-9])", text, re.IGNORECASE)
        if m:
            candidate = (m.start(), -len(model_tok), normalize_gateway_model(model_tok))
            if best is None or candidate[:2] < best[:2]:
                best = candidate
    return best[2] if best is not None else ""


def _infer_model_from_filename_or_readme(file_path: Path) -> str:
    """Infer gateway model from filename tokens or sibling README.md when log headers omit it."""
    stem = file_path.stem
    m_trace = re.search(r"_(?:trace|sweep)_(?:oracle_)?([A-Za-z0-9]+)(?:_|$)", stem)
    if m_trace:
        cand = normalize_gateway_model(m_trace.group(1))
        if cand and cand != "Generic":
            return cand

    for token in re.split(r"[^A-Za-z0-9/]+", stem):
        cand = normalize_gateway_model(token)
        if cand in _KNOWN_CANONICAL_MODELS:
            return cand

    readme = file_path.parent / "README.md"
    if readme.is_file():
        readme_text = readme.read_text(encoding="utf-8", errors="replace")
        m_rm = _README_MODEL_RE.search(readme_text)
        if m_rm:
            cand = normalize_gateway_model(m_rm.group(1))
            if cand and cand != "Generic":
                return cand
        cand_free = _extract_model_from_free_text(readme_text)
        if cand_free:
            return cand_free
    return ""


def _is_known_gateway(gateway: str) -> bool:
    """Return True when gateway is a concrete model token rather than empty/Generic/Unknown."""
    return bool(gateway) and gateway not in ("Generic", "Unknown")


def _commit_rx_frame(summary: FixtureCorpusSummary, rec: FrameRecord) -> None:
    """Commit a single deduplicated FrameRecord to summary and per-model statistics."""
    summary.rx_frames.append(rec)
    canon_gw = rec.gateway
    if not _is_known_gateway(canon_gw):
        return

    stats = summary.by_model.setdefault(canon_gw, GatewayModelStats())
    if rec.is_oracle:
        stats.oracle_files.add(rec.file_rel)
        stats.oracle_rx_frames += 1
        stats.oracle_whos.add(rec.who)
        stats.oracle_artifacts[rec.artifact_label] = (
            stats.oracle_artifacts.get(rec.artifact_label, 0) + 1
        )
    else:
        stats.phys_files.add(rec.file_rel)
        stats.phys_rx_frames += 1
        stats.phys_whos.add(rec.who)
        stats.phys_artifacts[rec.artifact_label] = (
            stats.phys_artifacts.get(rec.artifact_label, 0) + 1
        )


def _record_rx_frame(
    summary: FixtureCorpusSummary,
    *,
    raw: str,
    gateway: str,
    file_rel: str,
    artifact_label: str,
    is_oracle: bool,
) -> None:
    who = extract_who(raw)
    if who is None:
        return
    canon_gw = normalize_gateway_model(gateway)
    rec = FrameRecord(
        raw=raw.strip(),
        who=who,
        direction="rx",
        gateway=canon_gw,
        artifact_label=artifact_label,
        file_rel=file_rel,
        is_oracle=is_oracle,
    )
    _commit_rx_frame(summary, rec)


def _parse_json_fixture(
    file_path: Path,
    file_rel: str,
    artifact_label: str,
    is_path_oracle: bool,
    summary: FixtureCorpusSummary,
) -> None:
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(payload, dict):
        return

    is_oracle = is_path_oracle or (payload.get("synthetic") is True)
    data_sec = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    if not is_oracle and isinstance(data_sec, dict):
        ctt = (
            data_sec.get("config_entry", {})
            .get("options", {})
            .get("cover_travel_times")
            if isinstance(data_sec.get("config_entry"), dict)
            and isinstance(data_sec["config_entry"].get("options"), dict)
            else None
        )
        if isinstance(ctt, dict) and ctt:
            summary.calibrated_cover_bundles.append((artifact_label, len(ctt)))

    gw_raw = ""
    if isinstance(data_sec, dict) and isinstance(data_sec.get("gateway"), dict):
        gw_raw = str(
            data_sec["gateway"].get("model_name")
            or data_sec["gateway"].get("model")
            or ""
        )
    elif isinstance(payload.get("gateway"), dict):
        gw_raw = str(
            payload["gateway"].get("model")
            or payload["gateway"].get("model_name")
            or ""
        )
    elif isinstance(payload.get("meta"), dict) and isinstance(payload["meta"].get("gateway"), str):
        gw_raw = _extract_model_from_free_text(payload["meta"]["gateway"])
    if not gw_raw or gw_raw == "Unknown":
        gw_raw = _infer_model_from_filename_or_readme(file_path)

    telemetry = payload.get("telemetry") if isinstance(payload.get("telemetry"), dict) else None
    telemetry_zero_rx = bool(telemetry and telemetry.get("total_rx") == 0)

    frames_list: list[object] | None = None
    if isinstance(data_sec, dict) and isinstance(data_sec.get("bus_monitor"), dict):
        recent = data_sec["bus_monitor"].get("recent_frames")
        if isinstance(recent, list):
            frames_list = recent
    elif isinstance(payload.get("frames"), list):
        frames_list = payload["frames"]

    if frames_list is not None:
        for item in frames_list:
            if isinstance(item, dict):
                if "direction" in item or "dir" in item:
                    direction = str(item.get("direction") or item.get("dir") or "").strip().lower()
                    if direction != "rx":
                        continue
                elif telemetry_zero_rx:
                    continue
                raw = item.get("raw") or item.get("frame")
                if isinstance(raw, str):
                    _record_rx_frame(
                        summary,
                        raw=raw,
                        gateway=gw_raw,
                        file_rel=file_rel,
                        artifact_label=artifact_label,
                        is_oracle=is_oracle,
                    )
            elif isinstance(item, str) and not telemetry_zero_rx:
                _record_rx_frame(
                    summary,
                    raw=item,
                    gateway=gw_raw,
                    file_rel=file_rel,
                    artifact_label=artifact_label,
                    is_oracle=is_oracle,
                )
        return

    # Support keyed scenario-window JSON captures (e.g. central_flags_*, climate_mode_*, group_park_and_resume)
    for top_key, top_val in payload.items():
        key_lower = str(top_key).lower()
        if key_lower.startswith("_") or key_lower in ("meta", "findings", "decoder_states_seen", "audio_diagnostics"):
            continue
        if key_lower == "tx" or key_lower.startswith("tx_") or "expected_tx" in key_lower:
            continue

        key_prefix_gw = normalize_gateway_model(str(top_key).split("_", 1)[0])
        effective_gw = key_prefix_gw if key_prefix_gw in _KNOWN_CANONICAL_MODELS else gw_raw

        if isinstance(top_val, dict) and isinstance(top_val.get("bus_rx"), list):
            for rx_obj in top_val["bus_rx"]:
                if isinstance(rx_obj, dict):
                    raw_f = rx_obj.get("raw") or rx_obj.get("frame")
                    if isinstance(raw_f, str):
                        _record_rx_frame(
                            summary,
                            raw=raw_f,
                            gateway=effective_gw,
                            file_rel=file_rel,
                            artifact_label=artifact_label,
                            is_oracle=is_oracle,
                        )
        elif isinstance(top_val, list):
            for entry in top_val:
                if (
                    isinstance(entry, list)
                    and len(entry) == 3
                    and all(isinstance(part, str) for part in entry)
                ):
                    mid = entry[1].strip()
                    mid_low = mid.lower()
                    if mid_low == "tx":
                        continue
                    if mid_low == "rx":
                        _record_rx_frame(
                            summary,
                            raw=entry[2],
                            gateway=effective_gw,
                            file_rel=file_rel,
                            artifact_label=artifact_label,
                            is_oracle=is_oracle,
                        )
                    else:
                        mid_gw = normalize_gateway_model(mid)
                        if mid_gw in _KNOWN_CANONICAL_MODELS:
                            _record_rx_frame(
                                summary,
                                raw=entry[2],
                                gateway=mid_gw,
                                file_rel=file_rel,
                                artifact_label=artifact_label,
                                is_oracle=is_oracle,
                            )
                elif isinstance(entry, dict):
                    direction = str(entry.get("direction") or entry.get("dir") or "").strip().lower()
                    if direction != "rx":
                        continue
                    raw_f = entry.get("raw") or entry.get("frame")
                    if isinstance(raw_f, str):
                        _record_rx_frame(
                            summary,
                            raw=raw_f,
                            gateway=effective_gw,
                            file_rel=file_rel,
                            artifact_label=artifact_label,
                            is_oracle=is_oracle,
                        )


def _parse_jsonl_fixture(
    file_path: Path,
    file_rel: str,
    artifact_label: str,
    is_oracle: bool,
    summary: FixtureCorpusSummary,
) -> None:
    gw_raw = _infer_model_from_filename_or_readme(file_path)
    for line in file_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line_s = line.strip()
        if not line_s:
            continue
        try:
            rec = json.loads(line_s)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        kind = str(rec.get("kind", "")).lower()
        if kind == "event" and isinstance(rec.get("raw"), str):
            _record_rx_frame(
                summary,
                raw=rec["raw"],
                gateway=gw_raw,
                file_rel=file_rel,
                artifact_label=artifact_label,
                is_oracle=is_oracle,
            )
        elif kind == "sent" and isinstance(rec.get("replies"), list):
            for reply in rec["replies"]:
                if isinstance(reply, str):
                    _record_rx_frame(
                        summary,
                        raw=reply,
                        gateway=gw_raw,
                        file_rel=file_rel,
                        artifact_label=artifact_label,
                        is_oracle=is_oracle,
                    )


def _parse_text_or_log_fixture(
    file_path: Path,
    file_rel: str,
    artifact_label: str,
    is_oracle: bool,
    summary: FixtureCorpusSummary,
) -> None:
    text = file_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # Determine fallback gateway model for non-HA-log lines or pre-identification "Generic" lines
    explicit_non_generic: set[str] = set()
    for m_ha in _HA_LOG_RX_RE.finditer(text):
        cand = normalize_gateway_model(m_ha.group(1))
        if cand and cand != "Generic":
            explicit_non_generic.add(cand)

    fallback_gw = ""
    if len(explicit_non_generic) == 1:
        fallback_gw = next(iter(explicit_non_generic))
    if not fallback_gw:
        m_bundle = _BUNDLE_MODEL_RE.search(text)
        if m_bundle:
            fallback_gw = normalize_gateway_model(m_bundle.group(1))
    if not fallback_gw:
        m_comment = _COMMENT_GATEWAY_RE.search(text)
        if m_comment:
            fallback_gw = normalize_gateway_model(m_comment.group(1))
    if not fallback_gw:
        fallback_gw = _infer_model_from_filename_or_readme(file_path)

    for line in lines:
        m_ha = _HA_LOG_RX_RE.search(line)
        if m_ha:
            gw_label = normalize_gateway_model(m_ha.group(1))
            if gw_label == "Generic" and fallback_gw:
                gw_label = fallback_gw
            payload_str = m_ha.group(2)
            tokens = [t.group(1) for t in _OWN_FRAME_TOKEN_RE.finditer(payload_str)]
            for tok in (tokens or [payload_str]):
                _record_rx_frame(
                    summary,
                    raw=tok,
                    gateway=gw_label,
                    file_rel=file_rel,
                    artifact_label=artifact_label,
                    is_oracle=is_oracle,
                )
            continue

        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Skip outbound TX lines in diagnostic bundles or timelines
        if re.search(r"(?:\[TX\]|\bTX\b)", stripped):
            continue
        if re.search(r"(?:\[RX\]|\bRX\b|\bOWN\s+\*)", stripped):
            for m_tok in _OWN_FRAME_TOKEN_RE.finditer(stripped):
                _record_rx_frame(
                    summary,
                    raw=m_tok.group(1),
                    gateway=fallback_gw,
                    file_rel=file_rel,
                    artifact_label=artifact_label,
                    is_oracle=is_oracle,
                )


def _fixture_scan_sort_key(file_path: Path, fixtures_dir: Path) -> tuple[int, int, str]:
    """Order traces/ ahead of plants/ and .json/.jsonl ahead of .log/.txt for canonical citations."""
    rel = file_path.relative_to(fixtures_dir).as_posix()
    in_plants = 1 if rel.startswith("plants/") else 0
    ext_rank = 0 if file_path.suffix.lower() in {".json", ".jsonl"} else 1
    return (in_plants, ext_rank, rel)


def scan_fixture_corpus(
    fixtures_dir: Path = FIXTURES_DIR,
    const_py: Path = CONST_PY,
    validate_models: bool = True,
) -> FixtureCorpusSummary:
    """Scan tests/fixtures/ and return aggregated physical & oracle frame statistics."""
    summary = FixtureCorpusSummary()
    if fixtures_dir.is_dir():
        all_files = sorted(
            (p for p in fixtures_dir.rglob("*") if p.is_file()),
            key=lambda p: _fixture_scan_sort_key(p, fixtures_dir),
        )
        seen_sequences: set[tuple[str, bool, tuple[str, ...]]] = set()
        for file_path in all_files:
            suffix = file_path.suffix.lower()
            if suffix not in {".json", ".jsonl", ".txt", ".log"}:
                continue
            file_rel = file_path.relative_to(fixtures_dir).as_posix()
            is_path_oracle = "oracle" in file_rel.lower()
            artifact_label = _artifact_label_for_file(file_path, fixtures_dir)

            file_summary = FixtureCorpusSummary()
            if suffix == ".json":
                _parse_json_fixture(
                    file_path, file_rel, artifact_label, is_path_oracle, file_summary
                )
            elif suffix == ".jsonl":
                _parse_jsonl_fixture(
                    file_path, file_rel, artifact_label, is_path_oracle, file_summary
                )
            else:
                _parse_text_or_log_fixture(
                    file_path, file_rel, artifact_label, is_path_oracle, file_summary
                )

            if file_summary.calibrated_cover_bundles:
                summary.calibrated_cover_bundles.extend(
                    file_summary.calibrated_cover_bundles
                )

            grouped_recs: dict[tuple[str, bool], list[FrameRecord]] = {}
            for rec in file_summary.rx_frames:
                grouped_recs.setdefault((rec.gateway, rec.is_oracle), []).append(rec)

            for (gw_key, is_oracle), group_recs in grouped_recs.items():
                if _is_known_gateway(gw_key):
                    seq_key = (gw_key, is_oracle, tuple(r.raw for r in group_recs))
                    if seq_key in seen_sequences:
                        continue
                    seen_sequences.add(seq_key)
                for rec in group_recs:
                    _commit_rx_frame(summary, rec)

    if validate_models:
        mapped_models: set[str] = set()
        for row in GATEWAY_ROADMAP_ROWS:
            for m in row.models:
                mapped_models.add(normalize_gateway_model(m))

        const_models = [
            normalize_gateway_model(m)
            for m in extract_supported_models_from_const(const_py)
            if m != "Generic"
        ]
        unmapped_const = sorted(m for m in const_models if m and m not in mapped_models)
        if unmapped_const:
            raise ValueError(
                f"SUPPORTED_GATEWAY_MODELS in const.py missing from GATEWAY_ROADMAP_ROWS: "
                f"{unmapped_const}"
            )

        unmapped_fixtures = sorted(
            m for m in summary.by_model if m and m not in mapped_models
        )
        if unmapped_fixtures:
            raise ValueError(
                f"Gateway models found in {fixtures_dir} missing from GATEWAY_ROADMAP_ROWS: "
                f"{unmapped_fixtures}"
            )

    return summary


def _top_artifacts(artifact_counts: dict[str, int], limit: int = 2) -> list[str]:
    """Return top artifact path strings ordered deterministically by (-count, path)."""
    ordered = sorted(artifact_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [f"`{path}`" for path, _ in ordered[:limit]]


def _format_who_list(whos: set[int]) -> str:
    display = sorted(whos & STANDARD_DISPLAY_WHOS) or sorted(whos)
    return ", ".join(str(w) for w in display)


@dataclass(frozen=True)
class EvaluatedRow:
    """Evaluated status, evidence text, target text, and Mermaid metadata for a table row."""

    display_name: str
    mermaid_id: str
    mermaid_title: str
    mermaid_subtitle: str
    mermaid_class: str  # "covered" | "partial" | "needed"
    status_cell: str
    evidence_cell: str
    target_cell: str
    active_in_harness: bool


def evaluate_gateway_rows(summary: FixtureCorpusSummary) -> list[EvaluatedRow]:
    """Evaluate all GatewayRoadmapRowSpec entries against the scanned fixture summary."""
    evaluated: list[EvaluatedRow] = []

    for spec in GATEWAY_ROADMAP_ROWS:
        phys_files: set[str] = set()
        phys_rx = 0
        phys_whos: set[int] = set()
        phys_artifacts: dict[str, int] = {}
        traced_submodels: list[str] = []

        oracle_files: set[str] = set()
        oracle_rx = 0
        oracle_whos: set[int] = set()
        oracle_artifacts: dict[str, int] = {}

        for model in spec.models:
            canon = normalize_gateway_model(model)
            st = summary.by_model.get(canon)
            if not st:
                continue
            if st.phys_rx_frames > 0:
                if canon not in traced_submodels:
                    traced_submodels.append(canon)
                phys_files.update(st.phys_files)
                phys_rx += st.phys_rx_frames
                phys_whos.update(st.phys_whos)
                for art, cnt in st.phys_artifacts.items():
                    phys_artifacts[art] = phys_artifacts.get(art, 0) + cnt
            if st.oracle_rx_frames > 0:
                oracle_files.update(st.oracle_files)
                oracle_rx += st.oracle_rx_frames
                oracle_whos.update(st.oracle_whos)
                for art, cnt in st.oracle_artifacts.items():
                    oracle_artifacts[art] = oracle_artifacts.get(art, 0) + cnt

        functional_whos = phys_whos - DIAGNOSTIC_ONLY_WHOS

        if phys_rx > 0 and functional_whos:
            status_cell = "🟢 **Covered**"
            mermaid_class = "covered"
            active_in_harness = True
            file_word = "capture" if len(phys_files) == 1 else "captures"
            sub_prefix = (
                f"Traced `{', '.join(traced_submodels)}`: "
                if len(spec.models) > 1
                else ""
            )
            samples = ", ".join(_top_artifacts(phys_artifacts, limit=2))
            evidence_cell = (
                f"{sub_prefix}{len(phys_files)} physical {file_word} ({phys_rx:,} `rx` frames; "
                f"`WHO = {_format_who_list(phys_whos)}`) — e.g. {samples}"
            )
            untraced_families = [
                label
                for label, members in spec.optional_submodel_families
                if not any(
                    summary.by_model.get(normalize_gateway_model(m), GatewayModelStats()).phys_rx_frames > 0
                    for m in members
                )
            ]
            if untraced_families:
                fam_str = ", ".join(f"`{f}`" for f in untraced_families)
                target_cell = (
                    f"*Family covered by physical `{'` & `'.join(traced_submodels)}` captures; "
                    f"optional legacy {fam_str} captures welcomed.*"
                )
            else:
                target_cell = spec.covered_note
            mermaid_subtitle = f"🟢 {spec.mermaid_title}<br/>({len(phys_files)} {file_word} · {phys_rx:,} rx)"
        elif phys_rx > 0:
            status_cell = "🟡 **Partial**"
            mermaid_class = "partial"
            active_in_harness = False
            samples = ", ".join(_top_artifacts(phys_artifacts, limit=2))
            evidence_cell = (
                f"{len(phys_files)} diagnostic-only capture(s) ({phys_rx:,} `rx` frames; "
                f"`WHO = {_format_who_list(phys_whos)}`) — {samples}"
            )
            target_cell = spec.missing_target
            mermaid_subtitle = f"🟡 {spec.mermaid_title}<br/>(Diagnostic WHOs only)"
        elif spec.deferred_note:
            status_cell = "🔴 **Deferred (v2.1+)**"
            mermaid_class = "needed"
            active_in_harness = False
            evidence_cell = spec.deferred_note
            target_cell = spec.missing_target
            mermaid_subtitle = f"🔴 {spec.mermaid_title}<br/>(Deferred to v2.1+ · #879)"
        elif oracle_rx > 0:
            status_cell = "🟡 **Synthetic**"
            mermaid_class = "partial"
            active_in_harness = False
            samples = ", ".join(_top_artifacts(oracle_artifacts, limit=2))
            evidence_cell = (
                f"{len(oracle_files)} firmware-oracle fixture(s) ({oracle_rx:,} `rx` frames; "
                f"`WHO = {_format_who_list(oracle_whos)}`) — {samples}"
            )
            target_cell = spec.missing_target
            mermaid_subtitle = f"🟡 {spec.mermaid_title}<br/>(Firmware oracle only)"
        else:
            status_cell = "🔴 **Needed**"
            mermaid_class = "needed"
            active_in_harness = False
            evidence_cell = "Unit / profile tests only (0 physical captures in `tests/fixtures/`)"
            target_cell = spec.missing_target
            mermaid_subtitle = f"🔴 {spec.mermaid_title}<br/>(Physical capture needed)"

        evaluated.append(
            EvaluatedRow(
                display_name=spec.display_name,
                mermaid_id=spec.mermaid_id,
                mermaid_title=spec.mermaid_title,
                mermaid_subtitle=mermaid_subtitle,
                mermaid_class=mermaid_class,
                status_cell=status_cell,
                evidence_cell=evidence_cell,
                target_cell=target_cell,
                active_in_harness=active_in_harness,
            )
        )

    return evaluated


def evaluate_subsystem_rows(summary: FixtureCorpusSummary) -> list[EvaluatedRow]:
    """Evaluate all SubsystemRoadmapRowSpec entries against scanned inbound (rx) frames."""
    evaluated: list[EvaluatedRow] = []

    for spec in SUBSYSTEM_ROADMAP_ROWS:
        rx_re = re.compile(spec.frame_regex)
        phys_files: set[str] = set()
        phys_rx = 0
        phys_gateways: set[str] = set()
        phys_artifacts: dict[str, int] = {}

        oracle_files: set[str] = set()
        oracle_rx = 0
        oracle_artifacts: dict[str, int] = {}

        for rec in summary.rx_frames:
            if not rx_re.search(rec.raw):
                continue
            if rec.is_oracle:
                oracle_files.add(rec.file_rel)
                oracle_rx += 1
                oracle_artifacts[rec.artifact_label] = (
                    oracle_artifacts.get(rec.artifact_label, 0) + 1
                )
            else:
                phys_files.add(rec.file_rel)
                phys_rx += 1
                if _is_known_gateway(rec.gateway):
                    phys_gateways.add(rec.gateway)
                phys_artifacts[rec.artifact_label] = (
                    phys_artifacts.get(rec.artifact_label, 0) + 1
                )

        cal_suffix = ""
        if spec.include_calibrated_covers and summary.calibrated_cover_bundles:
            total_cal_covers = sum(cnt for _, cnt in summary.calibrated_cover_bundles)
            cal_paths = ", ".join(
                f"`{path}`" for path, _ in sorted(summary.calibrated_cover_bundles)
            )
            cal_suffix = (
                f"; plus {total_cal_covers} calibrated `cover_travel_times` profiles in {cal_paths}"
            )

        if phys_rx > 0:
            status_cell = "🟢 **Covered**"
            mermaid_class = "covered"
            active_in_harness = True
            file_word = "capture" if len(phys_files) == 1 else "captures"
            samples = ", ".join(_top_artifacts(phys_artifacts, limit=2))
            gw_word = "model" if len(phys_gateways) == 1 else "models"
            gw_note = (
                f" on {len(phys_gateways)} gateway {gw_word}"
                if phys_gateways
                else ""
            )
            evidence_cell = (
                f"{phys_rx:,} physical `rx` frames across {len(phys_files)} {file_word}"
                f"{gw_note} — e.g. {samples}{cal_suffix}"
            )
            target_cell = spec.covered_note
            mermaid_subtitle = f"🟢 {spec.mermaid_title}<br/>({phys_rx:,} rx · {len(phys_files)} {file_word})"
        elif oracle_rx > 0:
            status_cell = "🟡 **Synthetic**"
            mermaid_class = "partial"
            active_in_harness = False
            samples = ", ".join(_top_artifacts(oracle_artifacts, limit=2))
            evidence_cell = (
                f"{oracle_rx:,} firmware-oracle `rx` frames in {len(oracle_files)} fixture(s) "
                f"— {samples}"
            )
            target_cell = spec.missing_target
            mermaid_subtitle = f"🟡 {spec.mermaid_title}<br/>(Firmware oracle only)"
        elif spec.untraced_status == "synthetic":
            status_cell = "🟡 **Synthetic**"
            mermaid_class = "partial"
            active_in_harness = False
            evidence_cell = (
                spec.untraced_evidence
                or "Synthetic unit tests only (0 physical `rx` frames in `tests/fixtures/`)"
            )
            target_cell = spec.missing_target
            mermaid_subtitle = f"🟡 {spec.mermaid_title}<br/>(Unit tests only)"
        else:
            status_cell = "🔴 **Needed**"
            mermaid_class = "needed"
            active_in_harness = False
            evidence_cell = (
                spec.untraced_evidence
                or "0 physical `rx` frames in `tests/fixtures/`"
            )
            target_cell = spec.missing_target
            mermaid_subtitle = f"🔴 {spec.mermaid_title}<br/>(Physical capture needed)"

        evaluated.append(
            EvaluatedRow(
                display_name=spec.display_name,
                mermaid_id=spec.mermaid_id,
                mermaid_title=spec.mermaid_title,
                mermaid_subtitle=mermaid_subtitle,
                mermaid_class=mermaid_class,
                status_cell=status_cell,
                evidence_cell=evidence_cell,
                target_cell=target_cell,
                active_in_harness=active_in_harness,
            )
        )

    return evaluated


def build_coverage_mermaid_block(
    gw_rows: list[EvaluatedRow],
    sub_rows: list[EvaluatedRow],
) -> str:
    """Build the marker-wrapped System Coverage Overview Mermaid diagram."""
    lines = [
        MERMAID_START_MARKER,
        "```mermaid",
        "graph TD",
        '    subgraph Gateways["🏛️ Gateways & Transports"]',
    ]
    for r in gw_rows:
        lines.append(f'        {r.mermaid_id}["{r.mermaid_subtitle}"]')
    lines.append("    end")
    lines.append("")
    lines.append('    subgraph Subsystems["⚙️ Protocol Subsystems & Edge Scenarios"]')
    for r in sub_rows:
        lines.append(f'        {r.mermaid_id}["{r.mermaid_subtitle}"]')
    lines.append("    end")
    lines.append("")
    lines.append('    subgraph Engine["🧪 CI Replay & Regression Suite"]')
    lines.append('        HARNESS["tests/test_trace_replay.py & fixture tests<br/>(Deterministic On-Wire Replay)"]')
    lines.append("    end")
    lines.append("")

    for r in (*gw_rows, *sub_rows):
        if r.active_in_harness:
            lines.append(f"    {r.mermaid_id} --> HARNESS")

    lines.append("")
    lines.append("    classDef covered fill:#2e7d32,stroke:#1b5e20,color:#ffffff;")
    lines.append("    classDef partial fill:#f57f17,stroke:#e65100,color:#ffffff;")
    lines.append("    classDef needed fill:#c62828,stroke:#b71c1c,color:#ffffff;")

    by_cls: dict[str, list[str]] = {"covered": [], "partial": [], "needed": []}
    for r in (*gw_rows, *sub_rows):
        by_cls[r.mermaid_class].append(r.mermaid_id)
    for cls_name in ("covered", "partial", "needed"):
        if by_cls[cls_name]:
            lines.append(f"    class {','.join(by_cls[cls_name])} {cls_name};")

    lines.append("```")
    lines.append(MERMAID_END_MARKER)
    return "\n".join(lines)


def build_gateway_table_block(gw_rows: list[EvaluatedRow]) -> str:
    """Build the marker-wrapped Table 1 (Gateways & Transports) markdown block."""
    lines = [
        GATEWAY_TABLE_START_MARKER,
        "| Gateway Model | Status | Current Evidence / Fixture | Community Trace Needed / Target Scenario |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for r in gw_rows:
        lines.append(
            f"| {r.display_name} | {r.status_cell} | {r.evidence_cell} | {r.target_cell} |"
        )
    lines.append("")
    lines.append(GATEWAY_TABLE_FOOTER)
    lines.append(GATEWAY_TABLE_END_MARKER)
    return "\n".join(lines)


def build_subsystem_table_block(sub_rows: list[EvaluatedRow]) -> str:
    """Build the marker-wrapped Table 2 (Subsystems, Dimensions & Edge Scenarios) markdown block."""
    lines = [
        SUBSYSTEM_TABLE_START_MARKER,
        "| Subsystem & Domain | Status | Current Evidence / Fixture | Community Trace Needed / Target Scenario |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for r in sub_rows:
        lines.append(
            f"| {r.display_name} | {r.status_cell} | {r.evidence_cell} | {r.target_cell} |"
        )
    lines.append("")
    lines.append(SUBSYSTEM_TABLE_FOOTER)
    lines.append(SUBSYSTEM_TABLE_END_MARKER)
    return "\n".join(lines)


def build_all_roadmap_blocks(
    fixtures_dir: Path = FIXTURES_DIR,
    const_py: Path = CONST_PY,
    validate_models: bool = True,
) -> tuple[str, str, str]:
    """Compute and return (mermaid_block, gateway_table_block, subsystem_table_block)."""
    summary = scan_fixture_corpus(
        fixtures_dir=fixtures_dir,
        const_py=const_py,
        validate_models=validate_models,
    )
    gw_rows = evaluate_gateway_rows(summary)
    sub_rows = evaluate_subsystem_rows(summary)
    return (
        build_coverage_mermaid_block(gw_rows, sub_rows),
        build_gateway_table_block(gw_rows),
        build_subsystem_table_block(sub_rows),
    )


def sync_roadmap_file(
    target: Path,
    blocks: tuple[str, str, str],
    update: bool = False,
) -> tuple[bool, list[str]]:
    """Verify or update all three roadmap coverage blocks in a single markdown file."""
    messages: list[str] = []
    if not target.exists():
        return False, [f"Roadmap target not found: {target}"]

    content = target.read_text(encoding="utf-8")
    updated_content = content
    all_ok = True

    marker_specs = (
        ("Roadmap Coverage Mermaid diagram", MERMAID_START_MARKER, MERMAID_END_MARKER, blocks[0]),
        ("Roadmap Gateway Table 1", GATEWAY_TABLE_START_MARKER, GATEWAY_TABLE_END_MARKER, blocks[1]),
        ("Roadmap Subsystem Table 2", SUBSYSTEM_TABLE_START_MARKER, SUBSYSTEM_TABLE_END_MARKER, blocks[2]),
    )

    try:
        label_path = str(target.relative_to(REPO_ROOT))
    except ValueError:
        label_path = str(target)

    did_modify = False
    for section_label, start_m, end_m, expected in marker_specs:
        if start_m not in updated_content or end_m not in updated_content:
            messages.append(
                f"Missing markers {start_m} and/or {end_m} in {label_path}"
            )
            all_ok = False
            continue

        pattern = re.compile(
            rf"{re.escape(start_m)}.*?{re.escape(end_m)}",
            re.DOTALL,
        )
        match = pattern.search(updated_content)
        if not match:
            messages.append(f"Could not parse {section_label} marker block in {label_path}")
            all_ok = False
            continue

        expected_stripped = expected.strip()
        if match.group(0).strip() != expected_stripped:
            if update:
                updated_content = pattern.sub(lambda _m, rep=expected_stripped: rep, updated_content)
                did_modify = True
                messages.append(f"Updated {section_label} in {label_path}")
            else:
                messages.append(f"{section_label} out of sync in {label_path}")
                all_ok = False
        else:
            messages.append(f"{section_label} in sync: {label_path}")

    if update and did_modify:
        target.write_text(updated_content, encoding="utf-8")

    return all_ok, messages


def main(argv: list[str] | None = None) -> int:
    """Standalone CLI for inspecting, checking, or updating roadmap coverage tables."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Synchronize dynamic gateway and subsystem coverage tables in ROADMAP.md and docs/roadmap.md."
    )
    parser.add_argument("--check", action="store_true", help="Exit 1 if roadmap tables are out of sync.")
    parser.add_argument("--update", action="store_true", help="Rewrite roadmap tables in place.")
    parser.add_argument("--dry-run", action="store_true", help="Print generated blocks to stdout.")
    args = parser.parse_args(argv)

    blocks = build_all_roadmap_blocks()
    if args.dry_run:
        for b in blocks:
            print(b)
            print()
        return 0

    do_update = args.update and not args.check
    overall_ok = True
    for target in (ROADMAP_MD, DOCS_ROADMAP_MD):
        ok, msgs = sync_roadmap_file(target, blocks, update=do_update)
        overall_ok = overall_ok and ok
        for m in msgs:
            print(f"  • {m}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
