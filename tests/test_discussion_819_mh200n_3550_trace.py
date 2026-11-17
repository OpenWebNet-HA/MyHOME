"""Tests for Discussion #819: Physical BTicino MH200N (FW 1.1.8) + 3550 Central Unit Sweep & WHO 4 Trace.

Contributed by @wave68runner in:
- https://github.com/orgs/OpenWebNet-HA/discussions/819#discussioncomment-19348637
(BTicino MH200N firmware 1.1.8, BTicino 3550 99-zone thermoregulation central unit,
Home Assistant 2026.9.4, MyHOME 2.0.0b16, OWNd 2.0.0b11).

Pins four hardware and integration facts from the verbatim 148-frame `<myhome-bus-card>` export:
1. Verbatim SHA-256 provenance, privacy hygiene, `WHO 13` (`44` / `MH200N`, `1.1.8`, kernel `2.3.8`,
   distro `3.0.1`) identity metadata, exact `14 tx` and `134 rx` (`total_rx = 135`) telemetry
   accounting (`5 WHO 13 + 4 general + 5 WHO 4 diagnostic commands = 14 tx`), and `OWNd` parser
   coverage across all 148 frames.
2. Dual 39-endpoint `WHO 1` lighting sweep bursts (`3` dimmers `11, 14, 41` ON at coarse levels
   `9, 8, 7`, `1` relay `52` ON, and `35` OFF endpoints including 4-digit addresses `0011, 0510,
   0512, 0513, 0514`) + `WHO 1001` Dimension 11 autodiagnostic mask on `72` (`*#1001*72*11*...##`),
   zero `WHO 4` replies to general `*#4*0##`, and `16` dual-dialect `WHO 16` + `WHO 22` audio
   frames on `*#16*0*5##`.
3. Physical `BTicino 3550` (`#0`) + Zone `1` (`#1` / `1`) 5-command diagnostic sequence:
   - `*#4*#0##` -> `*4*1101*#0##` (Heating Weekly Program 1), `*4*20*#0##` (Remote Control
     Disabled / Telegestione OFF), `*4*30*#0##` (Failure Discovered), `*4*31*#0##` (Central Unit
     Battery KO), `*4*22*#0##` (At least one probe OFF), and `10` automatic-heating zone broadcasts
     `*4*111*#1..#5##`.
   - `*#4*1##` -> `*#4*1*0*0199##` (`19.9 °C`), `*#4*1*12*0170*3##` (`17.0 °C` effective),
     `*4*1*1##` (heating season), `*#4*1*14*0160*3##` (`16.0 °C` nominal), `*#4*1*13*01##`
     (`+1 °C` local knob offset), `*4*111*#1##`.
   - `*#4*1*13##` -> `*#4*1*13*01##`, `*4*111*#1##`, `*#4*1*12*0170*3##`, `*4*1*1##`.
   - `*#4*#1*#14*0210*1##` (manual heating override `21.0 °C` on `#1`) -> rejected in `113.2 ms`
     by the `3550` with `*4*111*#1##` because `*4*20*#0##` (Remote Control Disabled) is active.
   - `*4*311*#1##` -> `*4*111*#1##`.
4. End-to-end Home Assistant config-entry replay across the 148-frame capture: dynamic discovery of
   `39` lights, `5` audio media players (`11, 12, 21, 31, 41`), `3` sound source entities
   (`101, 102, 103`), central unit `climate.climate_zone_0` (`#0`), and `5` heating zones
   (`climate.climate_zone_1..5`), with `handler.has_central_unit` latched `True` on `#0` and Zone `1`
   hydrated to `current_temperature = 19.9`, `temperature = 17.0`, `local_offset = 1`, `knob_pos = "+1"`,
   and `season = "heating"`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNEvent, OWNHeatingEvent, OWNMessage
from OWNd.profiles import MH200NProfile
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_BROADCAST_RESYNC,
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_SSDP_LOCATION,
    DOMAIN,
    IDENTIFICATION_SSDP,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from scripts.anonymize_plant_fixture import findings

TRACE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_466"
    / "myhome_sweep_MH200N_all_2026-11-02T12-53-15.json"
)
TRACE_VERBATIM_SHA256 = "f68477c7fc90627fd63f5ce01f6435f76469dd2fe5324bac89ee5ff3ce3a9661"

EXPECTED_39_LIGHT_WHERES: frozenset[str] = frozenset({
    "0011",
    "01",
    "03",
    "04",
    "05",
    "0510",
    "0512",
    "0513",
    "0514",
    "06",
    "08",
    "09",
    "11",
    "12",
    "13",
    "14",
    "15",
    "18",
    "22",
    "23",
    "24",
    "26",
    "31",
    "34",
    "36",
    "37",
    "41",
    "42",
    "43",
    "52",
    "53",
    "55",
    "56",
    "57",
    "61",
    "63",
    "64",
    "71",
    "72",
})
EXPECTED_ON_LIGHTS: dict[str, str] = {
    "11": "9",
    "14": "8",
    "41": "7",
    "52": "1",
}


def _load_trace() -> dict[str, Any]:
    assert TRACE_PATH.is_file(), f"Missing trace fixture: {TRACE_PATH}"
    return cast(dict[str, Any], json.loads(TRACE_PATH.read_text(encoding="utf-8")))


def test_mh200n_3550_trace_verbatim_provenance_privacy_and_parser_coverage() -> None:
    """Verify verbatim SHA-256 digest, privacy hygiene, telemetry accounting, and `OWNd` parsing."""
    raw_text = TRACE_PATH.read_text(encoding="utf-8")
    assert not raw_text.endswith("\n")
    assert hashlib.sha256(raw_text.encode("utf-8")).hexdigest() == TRACE_VERBATIM_SHA256
    assert findings(raw_text) == []

    doc = _load_trace()
    assert doc["environment"] == {
        "home_assistant_version": "2026.9.4",
        "integration_version": "2.0.0b16",
        "ownd_version": "2.0.0b11",
        "exported_at": "2026-11-02T12:53:15.224Z",
    }
    assert doc["capture"] == {
        "kind": "sweep",
        "started_at": "2026-11-02T12:51:42.333Z",
        "filters": {"who": None, "where": None, "direction": None},
        "window": {
            "first": "2026-11-02T12:51:27.358Z",
            "last": "2026-11-02T12:53:03.921Z",
            "frames": 148,
            "buffer_frames": 148,
            "buffer_depth": 200,
            "truncated": False,
        },
    }

    gw = doc["gateway"]
    assert gw["model"] == "MH200N"
    assert gw["manufacturer"] == "BTicino S.p.A."
    assert gw["firmware"] == "1.1.8"
    assert gw["mac_prefix"] == "00:03:50"
    assert gw["connection_type"] == "tcp"
    assert gw["queue_pacing"] == MH200NProfile().command_queue_delay == 0.15
    assert gw["identification"] == {
        "model": "MH200N",
        "source": IDENTIFICATION_SSDP,
        "configured_model": "MH200N",
        "ssdp_model": "MH200N",
        "who13_code": "44",
        "who13_model": "MH200N",
        "who13_model_official": None,
        "who13_model_observed": "MH200N",
        "who13_firmware": "1.1.8",
        "who13_kernel": "2.3.8",
        "who13_distribution": "3.0.1",
        "who1013_code": None,
        "who1013_model": None,
        "who1013_other_names": [],
        "who1013_n_conf": None,
        "who1013_brand": None,
        "who1013_line": None,
        "profile": "MH200NProfile",
        "conflict": None,
    }

    assert doc["telemetry"] == {
        "total_rx": 135,
        "total_tx": 14,
        "captured_in_buffer": 148,
        "buffer_depth": 200,
        "queue_depth": 0,
    }
    frames = doc["frames"]
    assert len(frames) == 148
    assert sum(1 for f in frames if f["direction"] == "tx") == 14
    assert sum(1 for f in frames if f["direction"] == "rx") == 134

    for entry in frames:
        parsed = OWNMessage.parse(entry["raw"])
        assert parsed is not None, f"Failed to parse captured frame: {entry['raw']}"
        if entry["direction"] == "rx":
            assert OWNEvent.parse(entry["raw"]) is not None, f"Failed OWNEvent.parse on rx frame: {entry['raw']}"


def test_mh200n_3550_sweep_subsystems_audio_and_unanswered_general_zone_query() -> None:
    """Verify WHO 13 replies, dual 39-light + WHO 1001 bursts, silent `*#4*0##`, and 16-frame audio inventory."""
    frames = _load_trace()["frames"]

    # WHO 13 identification queries & replies (frames[0..9]) + four ~30 s Dimension 22 date/time beacons
    who13_pairs = [(frames[i]["raw"], frames[i + 1]["raw"]) for i in range(0, 10, 2)]
    assert who13_pairs == [
        ("*#13**0##", "*#13**0*13*51*26*001##"),
        ("*#13**15##", "*#13**15*44##"),
        ("*#13**16##", "*#13**16*1*1*8##"),
        ("*#13**23##", "*#13**23*2*3*8##"),
        ("*#13**24##", "*#13**24*3*0*1##"),
    ]
    dim22_indices = [idx for idx, f in enumerate(frames) if f["who"] == "13" and f["dimension"] == "22"]
    assert dim22_indices == [73, 111, 137, 145]

    # Both *#1*0## (frames[10..50]) and *#2*0## (frames[51..92]) elicit the same 39 lights + WHO 1001 on 72
    assert frames[10]["raw"] == "*#1*0##"
    assert frames[51]["raw"] == "*#2*0##"
    assert frames[33]["raw"] == "*#1001*72*11*111011111111111101101101##"
    assert frames[75]["raw"] == "*#1001*72*11*111011111111111101101101##"
    for burst in (frames[11:51], frames[52:93]):
        who1_frames = [f for f in burst if f["who"] == "1"]
        assert len(who1_frames) == 39
        assert {f["where"] for f in who1_frames} == EXPECTED_39_LIGHT_WHERES
        assert {f["where"]: f["what"] for f in who1_frames if f["what"] != "0"} == EXPECTED_ON_LIGHTS

    # *#4*0## (frame 93) produces zero WHO 4 replies during the sweep before *#16*0*5## (frame 94)
    assert frames[93]["raw"] == "*#4*0##"
    assert frames[94]["raw"] == "*#16*0*5##"
    sweep_gap = float(frames[94]["timestamp"]) - float(frames[93]["timestamp"])
    assert 1.3 <= sweep_gap <= 1.4
    assert [f for f in frames[:112] if f["who"] == "4" and f["direction"] == "rx"] == []

    # *#16*0*5## (frame 94) elicits 8 paired WHO 16 + WHO 22 replies (frames[95..110])
    audio_rx = [f["raw"] for f in frames[95:111]]
    assert audio_rx == [
        "*16*13*31##",
        "*#22*3#3#1*12*0*10##",
        "*16*3*101##",
        "*#22*2#1*12*1*4##",
        "*16*13*12##",
        "*#22*3#1#2*12*0*10##",
        "*16*13*11##",
        "*#22*3#1#1*12*0*10##",
        "*16*13*21##",
        "*#22*3#2#1*12*0*10##",
        "*16*13*41##",
        "*#22*3#4#1*12*0*10##",
        "*16*13*103##",
        "*#22*2#3*12*0*10##",
        "*16*13*102##",
        "*#22*2#2*12*0*10##",
    ]

    # Exact 14 tx frames: 5 WHO 13 + 4 general sweep + 5 WHO 4 diagnostic commands
    tx_raws = [f["raw"] for f in frames if f["direction"] == "tx"]
    assert tx_raws == [
        "*#13**0##",
        "*#13**15##",
        "*#13**16##",
        "*#13**23##",
        "*#13**24##",
        "*#1*0##",
        "*#2*0##",
        "*#4*0##",
        "*#16*0*5##",
        "*#4*#0##",
        "*#4*1##",
        "*#4*1*13##",
        "*#4*#1*#14*0210*1##",
        "*4*311*#1##",
    ]


def test_mh200n_3550_central_unit_diagnostics_and_rejected_override_sequence() -> None:
    """Verify the 5-step `3550` (`#0`) + Zone `1` diagnostic sequence and rejected manual override."""
    frames = _load_trace()["frames"]

    # Step 1: *#4*#0## (frame 112) -> 5 central unit `#0` frames + 10 `#1..#5` zone mode frames
    assert frames[112]["raw"] == "*#4*#0##"
    central_status_rx = [f["raw"] for f in frames[113:118]]
    assert central_status_rx == [
        "*4*1101*#0##",  # Weekly Heating Program 1
        "*4*20*#0##",  # Remote Control Disabled (Telegestione OFF)
        "*4*30*#0##",  # Failure Discovered
        "*4*31*#0##",  # Central Unit Battery KO
        "*4*22*#0##",  # At least one probe OFF
    ]
    zone_modes_rx = [f["raw"] for f in frames[118:128]]
    assert zone_modes_rx == [
        "*4*111*#4##",
        "*4*111*#1##",
        "*4*111*#4##",
        "*4*111*#2##",
        "*4*111*#1##",
        "*4*111*#2##",
        "*4*111*#5##",
        "*4*111*#3##",
        "*4*111*#4##",
        "*4*111*#5##",
    ]

    msg_prog1 = OWNEvent.parse("*4*1101*#0##")
    assert isinstance(msg_prog1, OWNHeatingEvent)
    assert msg_prog1.where == "#0"
    assert msg_prog1.program == 1
    assert msg_prog1.season == "heating"

    # Step 2: *#4*1## (frame 128) -> Zone 1 full status expansion (frames[129..136])
    assert frames[128]["raw"] == "*#4*1##"
    zone1_expansion_rx = [f["raw"] for f in frames[129:137]]
    assert zone1_expansion_rx == [
        "*#4*1*0*0199##",
        "*#4*1*12*0170*3##",
        "*4*1*1##",
        "*#4*1*14*0160*3##",
        "*#4*1*13*01##",
        "*4*111*#1##",
        "*#4*1*12*0170*3##",
        "*4*1*1##",
    ]

    # Step 3: *#4*1*13## (frame 138) -> Zone 1 local knob offset + status (frames[139..142])
    assert frames[138]["raw"] == "*#4*1*13##"
    assert [f["raw"] for f in frames[139:143]] == [
        "*#4*1*13*01##",
        "*4*111*#1##",
        "*#4*1*12*0170*3##",
        "*4*1*1##",
    ]

    # Step 4: *#4*#1*#14*0210*1## (frame 143) -> rejected in 113.2 ms with *4*111*#1## (frame 144)
    assert frames[143]["raw"] == "*#4*#1*#14*0210*1##"
    assert frames[144]["raw"] == "*4*111*#1##"
    reject_delta_ms = (float(frames[144]["timestamp"]) - float(frames[143]["timestamp"])) * 1000.0
    assert 100.0 <= reject_delta_ms <= 130.0

    # Step 5: *4*311*#1## (frame 146) -> *4*111*#1## (frame 147)
    assert frames[146]["raw"] == "*4*311*#1##"
    assert frames[147]["raw"] == "*4*111*#1##"


@pytest.mark.asyncio
async def test_mh200n_3550_live_integration_replay(hass: HomeAssistant) -> None:
    """Replay the 148-frame MH200N + 3550 capture end-to-end against a live config entry."""
    frames = _load_trace()["frames"]
    mac = "00:03:50:00:02:00"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.200",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MH200N",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:ScenarioModule:1",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "1.1.8",
            CONF_SSDP_LOCATION: "http://192.0.2.200:49152/description.xml",
        },
        options={CONF_BROADCAST_RESYNC: True},
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

    async def _feed_rx_slice(start: int, stop: int) -> None:
        for frame in frames[start:stop]:
            if frame["direction"] != "rx":
                continue
            msg = OWNEvent.parse(frame["raw"]) or OWNMessage.parse(frame["raw"])
            assert msg is not None
            await handler._event_dispatcher.process_message(msg)
        await hass.async_block_till_done()

    with (
        patch.object(handler, "send", AsyncMock(return_value=True)),
        patch.object(handler, "send_status_request", AsyncMock(return_value=True)),
    ):
        # ── Phase 1: Sweep (`frames[0..112]`: WHO 13, 39 lights, 0 climate, 5 audio zones + 3 sources)
        await _feed_rx_slice(0, 112)

        ent_reg = er.async_get(hass)
        reg_entries = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        lights = [e for e in reg_entries if e.domain == "light"]
        climates = [e for e in reg_entries if e.domain == "climate"]
        assert len(lights) == 39
        assert climates == []
        assert handler.has_central_unit is False

        assert hass.states.get("light.light_11").state == STATE_ON  # type: ignore[union-attr]
        assert hass.states.get("light.light_14").state == STATE_ON  # type: ignore[union-attr]
        assert hass.states.get("light.light_41").state == STATE_ON  # type: ignore[union-attr]
        assert hass.states.get("light.light_52").state == STATE_ON  # type: ignore[union-attr]
        assert hass.states.get("light.light_72").state == STATE_OFF  # type: ignore[union-attr]

        # ── Phase 2: `*#4*#0##` response (`frames[112..128]`: #0 central unit + #1..#5 zones)
        await _feed_rx_slice(112, 128)

        assert handler.has_central_unit is True
        climates = [
            e
            for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
            if e.domain == "climate"
        ]
        assert len(climates) == 6

        central_state = hass.states.get("climate.climate_zone_0")
        assert central_state is not None
        assert central_state.state == HVACMode.HEAT
        assert central_state.attributes["season"] == "heating"
        assert central_state.attributes["program"] == 1

        for zone_num in range(1, 6):
            zone_state = hass.states.get(f"climate.climate_zone_{zone_num}")
            assert zone_state is not None
            assert zone_state.state in (HVACMode.HEAT, HVACMode.AUTO)

        # ── Phase 3: Zone 1 status expansion & rejected override (`frames[128..148]`)
        await _feed_rx_slice(128, 148)

        zone1_state = hass.states.get("climate.climate_zone_1")
        assert zone1_state is not None
        assert zone1_state.attributes["current_temperature"] == 19.9
        assert zone1_state.attributes["temperature"] == 17.0
        assert zone1_state.attributes["local_target_temperature"] == 17.0
        assert zone1_state.attributes["local_offset"] == 1
        assert zone1_state.attributes["knob_pos"] == "+1"
        assert zone1_state.attributes["season"] == "heating"

    await hass.config_entries.async_unload(entry.entry_id)
