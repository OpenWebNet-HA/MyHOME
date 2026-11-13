"""Tests for Discussion #856: Physical BTicino F453 (FW 2.0.10) Sweep & Timed Shutter Diagnostics Trace.

Contributed by @flow68000 in:
- https://github.com/orgs/OpenWebNet-HA/discussions/856#discussioncomment-19587072
(BTicino F453 firmware 2.0.10, kernel 2.3.8, ETLinux 3.0.1; 12 BTicino F411 timed
WHO=2 shutter actuators; Home Assistant 2026.11.1, MyHOME 2.0.0b17, OWNd 2.0.0b13).

Pins four hardware and integration facts from the sanitized 327-frame diagnostics export:
1. SHA-256 provenance, privacy hygiene, gateway metadata (`F453`, `F453Profile`,
   `send_workers = 1`, `0.15 s` pacing), exact `132 tx` / `195 rx` frame accounting,
   and `OWNd` parser coverage across all 327 frames.
2. Dual `myhome.sweep_bus` `WHO=13` + `WHO=1013` diagnostic exchanges (`frames[163..174]`
   and `frames[239..250]`): `*#13**15*16##` (`F453`), `*#13**16*2*0*10##` (`2.0.10`),
   `*#13**23*2*3*8##` (kernel `2.3.8`), `*#13**24*3*0*1##` (ETLinux `3.0.1`), and
   `*#1013**1*16*0*1*0##` (`OBJECT_MODEL = 16`, `N_CONF = 0`, `BRAND = 1` BTicino,
   `LINE = 0` Undefined) answered locally in `6.7-13.7 ms`.
3. Cross-domain SCS general status echo (`*#1*0##` eliciting 12 `WHO=2` `*2*0*<WHERE>##`
   shutter status frames before `*#2*0##` elicits the identical 12-shutter burst),
   general/area shutter broadcasts (`*2*1*0##`, `*2*0*0##`, `*2*1*2##`, `*2*0*2##`),
   and measured `cover_travel_times` (including cover `24`'s `2.97 s` partial downward run).
4. End-to-end Home Assistant config-entry replay across all 327 frames: dynamic/migrated
   setup of all 12 `cover.*` entities, hydrated `identification()` (`who1013_model = "F453"`,
   `who1013_brand = "1 (BTicino)"`), and zero `unknown_gateway_model` or mismatch repair issues.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from OWNd.message import OWNEvent, OWNMessage
from OWNd.profiles import F453Profile
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_BROADCAST_RESYNC,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_SSDP_LOCATION,
    DOMAIN,
    IDENTIFICATION_SSDP,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.repairs import async_create_unknown_model_issue
from scripts.anonymize_plant_fixture import findings

TRACE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "discussion_856"
    / "config_entry-myhome_F453_fw2_0_10.json"
)
TRACE_SHA256 = "cc61cba0a543974e7e2d69eabdd5d4d22529ee9bb70038084d7744d608f482ae"

EXPECTED_12_SHUTTER_WHERES: frozenset[str] = frozenset({
    "11",
    "12",
    "21",
    "22",
    "23",
    "24",
    "25",
    "34",
    "35",
    "36",
    "37",
    "89",
})


def _load_diag() -> dict[str, Any]:
    assert TRACE_PATH.is_file(), f"Missing trace fixture: {TRACE_PATH}"
    return cast(dict[str, Any], json.loads(TRACE_PATH.read_text(encoding="utf-8")))


def test_f453_trace_provenance_privacy_and_parser_coverage() -> None:
    """Verify SHA-256 digest, privacy hygiene, telemetry accounting, and `OWNd` parsing."""
    raw_text = TRACE_PATH.read_text(encoding="utf-8")
    assert hashlib.sha256(raw_text.encode("utf-8")).hexdigest() == TRACE_SHA256
    assert findings(raw_text, data_file=True) == []
    assert "Salle" not in raw_text and "Fenetre" not in raw_text

    doc = _load_diag()
    assert doc["home_assistant"]["version"] == "2026.11.1"
    assert doc["home_assistant"]["timezone"] == "UTC"
    assert set(doc["custom_components"]) == {"myhome"}
    assert doc["data"]["integration_version"] == "2.0.0b17"
    assert doc["data"]["ownd_version"] == "2.0.0b13"

    gw = doc["data"]["gateway"]
    assert gw["model_name"] == "F453"
    assert gw["manufacturer"] == "BTicino S.p.A."
    assert gw["firmware"] == "2.0.10"
    assert gw["send_workers"] == 1
    assert doc["data"]["profile"]["command_queue_delay"] == F453Profile().command_queue_delay == 0.15

    bm = doc["data"]["bus_monitor"]
    assert bm["stats"] == {
        "capacity": 500,
        "captured": 327,
        "total_rx": 195,
        "total_tx": 132,
        "subscribers": 0,
    }
    frames = bm["recent_frames"]
    assert len(frames) == 327
    assert sum(1 for f in frames if f["direction"] == "rx") == 195
    assert sum(1 for f in frames if f["direction"] == "tx") == 132

    for entry in frames:
        parsed = OWNMessage.parse(entry["raw"])
        assert parsed is not None, f"Failed to parse captured frame: {entry['raw']}"
        if entry["direction"] == "rx":
            assert OWNEvent.parse(entry["raw"]) is not None, f"Failed OWNEvent.parse on rx frame: {entry['raw']}"


def test_f453_dual_sweep_who13_who1013_and_shutter_topology() -> None:
    """Verify both WHO=13/1013 sweeps, cross-domain `*#1*0##` shutter echo, and travel-time calibration."""
    doc = _load_diag()
    frames = doc["data"]["bus_monitor"]["recent_frames"]

    for start_idx, expected_time in ((163, "*#13**0*10*47*05*001##"), (239, "*#13**0*10*56*02*001##")):
        pairs = [(frames[i]["raw"], frames[i + 1]["raw"]) for i in range(start_idx, start_idx + 12, 2)]
        assert pairs == [
            ("*#13**0##", expected_time),
            ("*#13**15##", "*#13**15*16##"),
            ("*#13**16##", "*#13**16*2*0*10##"),
            ("*#13**23##", "*#13**23*2*3*8##"),
            ("*#13**24##", "*#13**24*3*0*1##"),
            ("*#1013*0*1##", "*#1013**1*16*0*1*0##"),
        ]
        for i in range(start_idx, start_idx + 12, 2):
            latency_ms = (float(frames[i + 1]["timestamp"]) - float(frames[i]["timestamp"])) * 1000.0
            assert 0.0 < latency_ms < 25.0

    # Cross-domain SCS general status echo: *#1*0## (frame 0) and *#2*0## (frame 13) both elicit all 12 WHO=2 shutters
    assert frames[0]["raw"] == "*#1*0##"
    assert frames[13]["raw"] == "*#2*0##"
    assert {f["where"] for f in frames[1:13]} == EXPECTED_12_SHUTTER_WHERES
    assert {f["where"] for f in frames[14:26]} == EXPECTED_12_SHUTTER_WHERES
    assert all(f["who"] == "2" and f["what"] == "0" for f in frames[1:13] + frames[14:26])

    # General (*2*1*0##, *2*0*0##) and Area 2 (*2*1*2##, *2*0*2##) automation broadcasts appear in live rx traffic
    rx_raws = {f["raw"] for f in frames if f["direction"] == "rx"}
    assert {"*2*1*0##", "*2*0*0##", "*2*1*2##", "*2*0*2##"} <= rx_raws

    # Calibrated travel times: 11 shutters have 13.85-27.70 s downward runs; cover 24 has a 2.97 s partial run
    travel_times = doc["data"]["config_entry"]["options"]["cover_travel_times"]
    assert set(travel_times) == EXPECTED_12_SHUTTER_WHERES
    assert travel_times["24"]["down"] == 2.97 and travel_times["24"]["up"] == 10.56
    for where, cal in travel_times.items():
        if where != "24":
            assert 13.8 <= cal["down"] <= 28.0
            assert 15.9 <= cal["up"] <= 31.1


@pytest.mark.asyncio
async def test_f453_live_integration_replay_clears_unknown_gateway_model(hass: HomeAssistant) -> None:
    """Replay the 327-frame F453 capture against a live config entry and verify identity & zero repair issues."""
    doc = _load_diag()
    frames = doc["data"]["bus_monitor"]["recent_frames"]
    options = doc["data"]["config_entry"]["options"]

    mac = "00:03:50:00:08:56"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="F453 Gateway",
        data={
            CONF_HOST: "192.0.2.53",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "F453",
            CONF_MANUFACTURER: ["BTicino S.p.A."],
            CONF_FIRMWARE: "2.0.10",
            CONF_SSDP_LOCATION: "http://192.0.2.53:49153/description.xml",
        },
        options={
            CONF_BROADCAST_RESYNC: True,
            "cover_travel_times": options["cover_travel_times"],
            "migrated_yaml_platforms": options["migrated_yaml_platforms"],
        },
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    handler: MyHOMEGatewayHandler = hass.data[DOMAIN][mac][CONF_ENTITY]
    handler._on_event_connection_state_change(True)
    # Startup on F453 (WHO=13 code 16, non-shared) must never queue *#1013**1## automatically
    assert handler._who1013["pending"] is False

    # Pre-seed the 2.0.0b17 unknown_gateway_model issue (1013-1-16) and verify replay deletes it
    async_create_unknown_model_issue(hass, entry.entry_id, "1013-1-16")
    assert (DOMAIN, f"unknown_gateway_model_{entry.entry_id}") in ir.async_get(hass).issues

    with (
        patch.object(handler, "send", AsyncMock(return_value=True)),
        patch.object(handler, "send_status_request", AsyncMock(return_value=True)),
    ):
        for frame in frames:
            if frame["direction"] != "rx":
                continue
            msg = OWNEvent.parse(frame["raw"]) or OWNMessage.parse(frame["raw"])
            assert msg is not None
            await handler._event_dispatcher.process_message(msg)
        await asyncio.sleep(0)

        ent_reg = er.async_get(hass)
        reg_entries = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        covers = [e for e in reg_entries if e.domain == "cover"]
        assert len(covers) == 12

        ident = handler.identification()
        assert ident["model"] == "F453"
        assert ident["source"] == IDENTIFICATION_SSDP
        assert ident["who13_code"] == "16"
        assert ident["who13_model"] == "F453"
        assert ident["who13_model_official"] is None
        assert ident["who13_model_observed"] == "F453"
        assert ident["who13_firmware"] == "2.0.10"
        assert ident["who13_kernel"] == "2.3.8"
        assert ident["who13_distribution"] == "3.0.1"
        assert ident["who1013_code"] == "16"
        assert ident["who1013_model"] == "F453"
        assert ident["who1013_n_conf"] == "0"
        assert ident["who1013_brand"] == "1 (BTicino)"
        assert ident["who1013_line"] == "0 (Undefined)"
        assert ident["profile"] == "F453Profile"
        assert ident["conflict"] is None

        issue_reg = ir.async_get(hass)
        active_issues = [
            issue_id
            for (domain, issue_id) in issue_reg.issues
            if domain == DOMAIN and not issue_id.startswith("legacy_yaml_imported")
        ]
        assert active_issues == []

    await hass.config_entries.async_unload(entry.entry_id)
