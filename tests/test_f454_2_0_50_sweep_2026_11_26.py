"""Provenance, privacy, parser, and HA replay tests for F454 (FW 2.0.50) sweep capture (2026-11-26).

Covers the verbatim `myhome.sweep_bus` + interactive `WHO=4` / `WHO=1` capture
archived under `tests/fixtures/traces/f454_2_0_50_sweep_2026-11-26/` (Refs #466).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import CONF_ENTITY, CONF_FIRMWARE, DOMAIN
from scripts.anonymize_plant_fixture import findings

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces"
TRACE_REL = "f454_2_0_50_sweep_2026-11-26/myhome_sweep_F454_all_2026-11-26T15-23-55.json"
README_REL = "f454_2_0_50_sweep_2026-11-26/README.md"
EXPECTED_SHA256 = "a1f53c737384812f2803b69fb81270cb2da24a4623aa844faafb1804f19aa4ac"


def _load_payload() -> dict[str, Any]:
    return json.loads((TRACES_DIR / TRACE_REL).read_text(encoding="utf-8"))


def test_fixture_provenance_privacy_and_ownd_parsing() -> None:
    """Captured F454 2.0.50 trace preserves exact SHA-256, zero PII, and parses cleanly in OWNd."""
    path = TRACES_DIR / TRACE_REL
    raw_bytes = path.read_bytes()
    assert hashlib.sha256(raw_bytes).hexdigest() == EXPECTED_SHA256
    assert findings(raw_bytes.decode("utf-8")) == []

    payload = json.loads(raw_bytes.decode("utf-8"))
    assert payload["gateway"]["model"] == "F454"
    assert payload["gateway"]["firmware"] == "2.0.50"
    assert payload["capture"]["kind"] == "sweep"
    assert payload["capture"]["window"]["truncated"] is False

    frames = payload["frames"]
    assert len(frames) == 240
    assert sum(1 for fr in frames if fr["direction"] == "rx") == 186
    assert sum(1 for fr in frames if fr["direction"] == "tx") == 54

    for fr in frames:
        parsed = OWNMessage.parse(fr["raw"])
        assert parsed is not None, f"Failed to parse {fr['raw']!r} in {TRACE_REL}"


def test_trace_readme_is_privacy_clean() -> None:
    """Companion trace README carries zero personal data."""
    text = (TRACES_DIR / README_REL).read_text(encoding="utf-8")
    assert findings(text) == []
    assert EXPECTED_SHA256 in text


def test_f454_2_0_50_identity_and_shared_who1_who2_sweep() -> None:
    """F454 FW 2.0.50 identity, 70 lighting addresses (48 2-digit + 22 4-digit), and shared sweep."""
    payload = _load_payload()
    ident = payload["gateway"]["identification"]
    assert ident["who13_code"] == "200"
    assert ident["who13_firmware"] == "2.0.50"
    assert ident["who13_kernel"] == "2.6.32"
    assert ident["who13_distribution"] == "2.16.2"
    assert ident["who1013_code"] == "51"
    assert ident["who1013_model"] == "F454"
    assert ident["profile"] == "F454Profile"

    frames = payload["frames"]
    identity_rx = [
        fr["raw"] for fr in frames[:12] if fr["direction"] == "rx"
    ]
    assert identity_rx == [
        "*#13**0*18*19*23*001##",
        "*#13**15*200##",
        "*#13**16*2*0*50##",
        "*#13**23*2*6*32##",
        "*#13**24*2*16*2##",
        "*#1013**1*51*15*5*0##",
    ]

    # Both *#1*0## (index 12) and *#2*0## (index 98) produce identical 71-frame WHO=1 bursts
    # plus *2*0*91## because both map to the same SCS general status telegram on F454.
    assert frames[12]["direction"] == "tx" and frames[12]["raw"] == "*#1*0##"
    assert frames[98]["direction"] == "tx" and frames[98]["raw"] == "*#2*0##"
    sweep1_who1 = [
        fr["raw"] for fr in frames[13:86] if fr["direction"] == "rx" and str(fr.get("who")) == "1"
    ]
    sweep2_who1 = [
        fr["raw"] for fr in frames[99:172] if fr["direction"] == "rx" and str(fr.get("who")) == "1"
    ]
    assert len(sweep1_who1) == 71
    assert Counter(sweep1_who1) == Counter(sweep2_who1)
    assert frames[23]["raw"] == "*2*0*91##"
    assert frames[117]["raw"] == "*2*0*91##"

    who1_wheres = {
        fr["where"]
        for fr in frames
        if fr["direction"] == "rx" and str(fr.get("who")) == "1" and fr.get("where")
    }
    assert len(who1_wheres) == 70
    assert sum(1 for w in who1_wheres if len(w) == 2) == 48
    assert sum(1 for w in who1_wheres if len(w) == 4) == 22

    # Live light 15 toggle echoes at end of capture.
    assert [(fr["direction"], fr["raw"]) for fr in frames[236:]] == [
        ("tx", "*1*1*15##"),
        ("rx", "*1*1*15##"),
        ("tx", "*1*0*15##"),
        ("rx", "*1*0*15##"),
    ]


def test_f454_2_0_50_who4_central_setpoints_offsets_and_multi_actuator() -> None:
    """WHO=4 captures central-form #14 writes, Dim 12 offsets vs Dim 14 readback, and 6-actuator zone 71."""
    frames = _load_payload()["frames"]
    who4 = [(fr["direction"], fr["raw"]) for fr in frames if str(fr.get("who")) == "4"]
    assert sum(1 for d, _ in who4 if d == "rx") == 34
    assert sum(1 for d, _ in who4 if d == "tx") == 7

    # Zone 64: +1.0 °C local offset (written 22.0 °C -> Dim 14 readback 0220, Dim 12 0230).
    assert ("tx", "*#4*#64*#14*0220*3##") in who4
    assert ("tx", "*#4*64*14##") in who4
    assert ("rx", "*#4*64*14*0220*3##") in who4
    assert ("rx", "*#4*64*12*0230*3##") in who4
    assert ("rx", "*#4*64#1*20*1##") in who4

    # Zone 71: -1.0 °C local offset (written 23.0 °C -> Dim 12 0220) and 6 active actuators.
    assert ("tx", "*#4*#71*#14*0230*3##") in who4
    assert ("rx", "*#4*71*12*0220*3##") in who4
    for act_idx in range(1, 7):
        assert ("rx", f"*#4*71#{act_idx}*20*1##") in who4

    # Zone 72: +1.0 °C local offset across 3 writes (11.5->12.5, 13.5->14.5, 19.0->20.0 °C).
    for written, adjusted in (("0115", "0125"), ("0135", "0145"), ("0190", "0200")):
        assert ("tx", f"*#4*#72*#14*{written}*3##") in who4
        assert ("rx", f"*#4*72*12*{adjusted}*3##") in who4
    assert ("rx", "*#4*72#1*20*1##") in who4


@pytest.mark.asyncio
async def test_f454_2_0_50_sweep_replays_cleanly_in_home_assistant(
    hass: HomeAssistant,
) -> None:
    """Replaying all 186 rx frames into an F454 config entry completes cleanly with expected states."""
    payload = _load_payload()
    mac = "00:03:50:00:04:54"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.1",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "F454",
            CONF_FIRMWARE: "2.0.50",
        },
        unique_id=mac,
        title="F454 Gateway",
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

    handler = hass.data[DOMAIN][mac][CONF_ENTITY]
    handler._on_event_connection_state_change(True)

    replayed = 0
    for fr in payload["frames"]:
        if fr["direction"] != "rx":
            continue
        msg = OWNMessage.parse(fr["raw"])
        assert msg is not None
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 186

    # Lighting: 2-digit toggle (15 ends OFF) and 4-digit extended A/PL (0412 ON, 0110 OFF).
    light_15 = hass.states.get("light.light_15")
    assert light_15 is not None and light_15.state == "off"
    light_0412 = hass.states.get("light.light_0412")
    assert light_0412 is not None and light_0412.state == "on"
    light_0110 = hass.states.get("light.light_0110")
    assert light_0110 is not None and light_0110.state == "off"

    # Automation: cover 91 discovered from *2*0*91##.
    cover_91 = hass.states.get("cover.cover_91")
    assert cover_91 is not None

    # Thermoregulation: Zones 64, 71, 72 reflect final captured Dim 0 temperatures,
    # Dim 12 effective setpoints (23.0, 22.0, 20.0 °C), and Dim 20 active heating actuators.
    climate_64 = hass.states.get("climate.climate_zone_64")
    climate_71 = hass.states.get("climate.climate_zone_71")
    climate_72 = hass.states.get("climate.climate_zone_72")
    assert climate_64 is not None
    assert climate_64.attributes.get("current_temperature") == 22.0
    assert climate_64.attributes.get("temperature") == 23.0
    assert climate_64.attributes.get("hvac_action") == "heating"
    assert climate_71 is not None
    assert climate_71.attributes.get("current_temperature") == 20.7
    assert climate_71.attributes.get("temperature") == 22.0
    assert climate_71.attributes.get("hvac_action") == "heating"
    assert climate_72 is not None
    assert climate_72.attributes.get("current_temperature") == 17.2
    assert climate_72.attributes.get("temperature") == 20.0
    assert climate_72.attributes.get("hvac_action") == "heating"

    await hass.config_entries.async_unload(entry.entry_id)
