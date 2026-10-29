"""Tests for #466: Physical BTicino MH201 (FW 3.6.43) + F414 Classic Dimmer Traces.

Contributed by @emil-d in:
- https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-6453327840
- https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-6481616497 (PR #828 live verification)
(BTicino MH201 firmware 3.6.43, BTicino F414 dimmer at WHERE=42, Home Assistant 2026.10.4,
MyHOME 2.0.0b16, OWNd 2.0.0b11).

Pins seven hardware and integration facts across the three verbatim `<myhome-bus-card>` exports:
1. Verbatim SHA-256 provenance, privacy hygiene, `WHO 1013` `OBJECT_MODEL = 10` (`15*0*5`) identity
   consistency, filtered vs buffered telemetry accounting, and `OWNd` parser coverage across all
   `85 + 6 + 200 = 291` captured frames.
2. Full 27-endpoint `WHO 1` plant sweep (`28` frames per burst, `38` reporting twice) repeated across
   `*#1*0##` and `*#2*0##` (`+0.850 s` = `BUS_QUIET_PERIOD + command_queue_delay`), preventing the
   `10.0 s` command-session timeout seen on the partial 5-light morning sweep in #824.
3. Physical wall-pushbutton `WHAT 1000` command-translation pairs (`1000#1`, `1000#0`, `1000#31`,
   `1000#30`), relay `32` (`*1*1*32##`) vs `F414` dimmer `42` (`*1*7*42##` / `*1*4*42##` hardware level
   memory), `~520 ms` wall-button hold repeat cadence, and `~22 ms` `F414` step response latency.
4. Unfiltered Home Assistant turn-on / Dimension 1 follow-up / turn-off exchange (`*1*1*42##` ->
   `*1*7*42##` -> `*#1*42*1##` -> `*#1*42*1*148*5##` = `48 %` at speed `5` -> `*1*0*42##`).
5. End-to-end Home Assistant config-entry replay across the initial sweep and HA captures.
6. PR #828 live hardware verification (`myhome_trace_MH201_all_2026-10-28T19-16-51.json`): full
   `WHAT 2..10` wall-button ladder (`1, 10, 20, 30, 40, 50, 60, 75, 100 %`), ceiling clamping at
   `WHAT 10` (`100 %`), floor clamping at `WHAT 2` (`1 %` x3 without turning off), `15` HA fine
   `Dimension 1` slider writes (`103..199..178` -> `speed = 5`), `178 (78 %) -> *1*9*42## (75 %)`
   wall-button ON recall, zero-bounce HA `turn_on` at minimum `1 %` (`*1*2*42##` -> `*#1*42*1*101*5##`),
   and `WHO 2` cover presence at `WHERE = 33` (`*2*0*33##`).
7. End-to-end Home Assistant replay of the 200-frame PR #828 capture discovering all `27` lights
   plus `cover.cover_33` and verifying zero-bounce `brightness == 3` (`1 %`) on `light.turn_on`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.light import (  # type: ignore[attr-defined, unused-ignore]
    ATTR_BRIGHTNESS,
)
from homeassistant.components.light.const import ColorMode, LightEntityFeature
from homeassistant.const import (
    ATTR_SUPPORTED_FEATURES,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNEvent, OWNLightingEvent, OWNMessage
from OWNd.profiles import MH201Profile
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    COARSE_PRESET_TO_PERCENT,
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_SSDP_LOCATION,
    DOMAIN,
    IDENTIFICATION_SSDP,
    percent_to_eight_bits,
)
from custom_components.myhome.gateway import BUS_QUIET_PERIOD, MyHOMEGatewayHandler
from scripts.anonymize_plant_fixture import findings

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_466"
SWEEP_TRACE_PATH = TRACES_DIR / "myhome_sweep_MH201_who1-rx_2026-10-27T20-35-11.json"
HA_TRACE_PATH = TRACES_DIR / "myhome_trace_MH201_all_2026-10-27T20-38-12.json"
PR828_VERIFY_TRACE_PATH = TRACES_DIR / "myhome_trace_MH201_all_2026-10-28T19-16-51.json"

# `<myhome-bus-card>` exports `JSON.stringify(tracePayload, null, 2)` (no trailing newline);
# keep all three fixtures byte-for-byte identical to @emil-d's attachments on #466.
SWEEP_VERBATIM_SHA256 = "e82d019d9420a9bec4c277c5e6af06497b8b03dbd6d1ac240b1acc1074e6325b"
HA_TRACE_VERBATIM_SHA256 = "e6dd65e65f943aaff4d5693da7b317c6426775fcde6a889b2ffdf756b419d983"
PR828_VERIFY_VERBATIM_SHA256 = "76d9793e60cb6a7b96a86088d003ef8628e0731ada7f31fa9b11e49da029496a"

EXPECTED_27_LIGHT_WHERES: frozenset[str] = frozenset({
    "13",
    "26",
    "27",
    "28",
    "29",
    "31",
    "32",
    "35",
    "36",
    "37",
    "38",
    "39",
    "42",
    "45",
    "46",
    "47",
    "48",
    "51",
    "52",
    "53",
    "55",
    "56",
    "57",
    "58",
    "71",
    "72",
    "73",
})
EXPECTED_ON_IN_SWEEP: frozenset[str] = frozenset({"13", "37", "55"})


def _load_json(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"Missing trace fixture: {path}"
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def test_mh201_f414_traces_verbatim_provenance_privacy_and_parser_coverage() -> None:
    """Verify verbatim SHA-256 digests, privacy hygiene, telemetry math, and `OWNd` parsing."""
    for path, expected_sha in (
        (SWEEP_TRACE_PATH, SWEEP_VERBATIM_SHA256),
        (HA_TRACE_PATH, HA_TRACE_VERBATIM_SHA256),
        (PR828_VERIFY_TRACE_PATH, PR828_VERIFY_VERBATIM_SHA256),
    ):
        raw_text = path.read_text(encoding="utf-8")
        assert not raw_text.endswith("\n")
        assert hashlib.sha256(raw_text.encode("utf-8")).hexdigest() == expected_sha
        assert findings(raw_text) == []

    sweep_doc = _load_json(SWEEP_TRACE_PATH)
    ha_doc = _load_json(HA_TRACE_PATH)
    pr828_doc = _load_json(PR828_VERIFY_TRACE_PATH)

    # Identical gateway & WHO 1013 OBJECT_MODEL 10 (`15*0*5`) identity across all three captures
    # (`who13_firmware` is `"3.6.43"` when `*#13**16##` was queried by sweep_bus, else `None`)
    for doc, expected_who13_fw in (
        (sweep_doc, "3.6.43"),
        (ha_doc, "3.6.43"),
        (pr828_doc, None),
    ):
        assert doc["environment"]["home_assistant_version"] == "2026.10.4"
        assert doc["environment"]["integration_version"] == "2.0.0b16"
        assert doc["environment"]["ownd_version"] == "2.0.0b11"

        gw = doc["gateway"]
        assert gw["model"] == "MH201"
        assert gw["manufacturer"] == "BTicino S.p.A."
        assert gw["firmware"] == "3.6.43"
        assert gw["mac_prefix"] == "00:03:50"
        assert gw["connection_type"] == "tcp"
        assert gw["queue_pacing"] == MH201Profile().command_queue_delay == 0.1
        assert gw["identification"] == {
            "model": "MH201",
            "source": IDENTIFICATION_SSDP,
            "configured_model": "MH201",
            "ssdp_model": "MH201",
            "who13_code": "200",
            "who13_model": "F454",
            "who13_model_official": None,
            "who13_model_observed": "F454 / MyHomeServer1 / MH202 / F461 / H4890 / MH201",
            "who13_firmware": expected_who13_fw,
            "who13_kernel": None,
            "who13_distribution": None,
            "who1013_code": "10",
            "who1013_model": "MH201",
            "who1013_other_names": [],
            "who1013_n_conf": "15",
            "who1013_brand": "0",
            "who1013_line": "5",
            "profile": "MH201Profile",
            "conflict": None,
        }

    # Sweep export: filtered to WHO=1 & direction=rx (85 frames exported out of 136 buffered = 45 tx + 91 rx;
    # on 2.0.0b16, 45 tx = 5 WHO 13 + 4 general + 36 WHO 18 queries, queue_depth == 0)
    assert sweep_doc["capture"]["kind"] == "sweep"
    assert sweep_doc["capture"]["filters"] == {"who": "1", "where": None, "direction": "rx"}
    assert sweep_doc["capture"]["window"]["frames"] == 85
    assert sweep_doc["capture"]["window"]["buffer_frames"] == 136
    assert sweep_doc["capture"]["window"]["truncated"] is False
    assert sweep_doc["telemetry"] == {
        "total_rx": 91,
        "total_tx": 45,
        "captured_in_buffer": 136,
        "buffer_depth": 200,
        "queue_depth": 0,
    }
    assert len(sweep_doc["frames"]) == 85
    assert all(f["direction"] == "rx" and f["who"] == "1" for f in sweep_doc["frames"])

    # HA trace export: unfiltered 6-frame round-trip (3 tx + 3 rx)
    assert ha_doc["capture"]["kind"] == "trace"
    assert ha_doc["capture"]["filters"] == {"who": None, "where": None, "direction": None}
    assert ha_doc["capture"]["window"]["frames"] == 6
    assert ha_doc["capture"]["window"]["buffer_frames"] == 6
    assert ha_doc["capture"]["window"]["truncated"] is False
    assert ha_doc["telemetry"] == {
        "total_rx": 3,
        "total_tx": 3,
        "captured_in_buffer": 6,
        "buffer_depth": 200,
        "queue_depth": 0,
    }
    assert len(ha_doc["frames"]) == 6

    # PR #828 live verification export: unfiltered 200-frame ring buffer (19 tx + 181 rx; 36 tx / 354 rx lifetime)
    assert pr828_doc["capture"]["kind"] == "trace"
    assert pr828_doc["capture"]["filters"] == {"who": None, "where": None, "direction": None}
    assert pr828_doc["capture"]["window"]["frames"] == 200
    assert pr828_doc["capture"]["window"]["buffer_frames"] == 200
    assert pr828_doc["capture"]["window"]["truncated"] is True
    assert pr828_doc["telemetry"] == {
        "total_rx": 354,
        "total_tx": 36,
        "captured_in_buffer": 200,
        "buffer_depth": 200,
        "queue_depth": 0,
    }
    assert len(pr828_doc["frames"]) == 200
    assert sum(1 for f in pr828_doc["frames"] if f["direction"] == "tx") == 19
    assert sum(1 for f in pr828_doc["frames"] if f["direction"] == "rx") == 181

    for doc in (sweep_doc, ha_doc, pr828_doc):
        for entry in doc["frames"]:
            parsed = OWNEvent.parse(entry["raw"]) or OWNMessage.parse(entry["raw"])
            assert parsed is not None, f"Failed to parse captured frame: {entry['raw']}"


def test_mh201_f414_dual_28_frame_sweep_burst_and_non_blocking_completion() -> None:
    """Verify `*#1*0##` and `*#2*0##` each elicit the 28-frame status burst and avoid the 10 s hang."""
    doc = _load_json(SWEEP_TRACE_PATH)
    frames = doc["frames"]

    # Frame 0 is the event-session echo of `tx *#1*0##`
    assert frames[0]["raw"] == "*#1*0##"

    burst_1 = frames[1:29]
    burst_2 = frames[29:57]
    assert len(burst_1) == 28
    assert len(burst_2) == 28

    wheres_1 = [f["where"] for f in burst_1]
    wheres_2 = [f["where"] for f in burst_2]
    assert set(wheres_1) == EXPECTED_27_LIGHT_WHERES
    assert set(wheres_2) == EXPECTED_27_LIGHT_WHERES

    # Address 38 emits *1*0*38## twice in each burst (two actuator channels sharing WHERE=38)
    assert wheres_1.count("38") == 2
    assert wheres_2.count("38") == 2
    assert sorted(f["raw"] for f in burst_1) == sorted(f["raw"] for f in burst_2)

    on_wheres = {f["where"] for f in burst_1 if f["what"] == "1"}
    off_wheres = {f["where"] for f in burst_1 if f["what"] == "0"}
    assert on_wheres == EXPECTED_ON_IN_SWEEP
    assert off_wheres == EXPECTED_27_LIGHT_WHERES - EXPECTED_ON_IN_SWEEP
    assert "42" in off_wheres

    # First burst spans ~2.10 s; second burst (`*#2*0##`) starts +0.850 s after burst 1 finishes
    # (`BUS_QUIET_PERIOD = 0.75 s` + `MH201Profile.command_queue_delay = 0.10 s`)
    assert burst_1[-1]["timestamp"] - frames[0]["timestamp"] == pytest.approx(2.097, abs=0.02)
    dt_between_bursts = burst_2[0]["timestamp"] - burst_1[-1]["timestamp"]
    assert dt_between_bursts == pytest.approx(
        BUS_QUIET_PERIOD + MH201Profile().command_queue_delay,
        abs=0.02,
    )
    assert burst_2[-1]["timestamp"] - burst_2[0]["timestamp"] == pytest.approx(2.050, abs=0.02)

    # `<myhome-bus-card>` stamps `started_at` right after `await callService("myhome", "sweep_bus")`
    # resolves: here it resolves 4.086 s after burst 2 ends (vs +53 s in #824 when *#2*0## hung 10 s)
    sweep_done_ts = datetime.fromisoformat(doc["capture"]["started_at"]).timestamp()
    assert sweep_done_ts - burst_2[-1]["timestamp"] == pytest.approx(4.086, abs=0.05)


def test_mh201_f414_wall_pushbutton_command_translation_and_step_cadence() -> None:
    """Verify `WHAT 1000` translation wrappers, level memory, ~520 ms hold period, and ~22 ms step latency."""
    frames = _load_json(SWEEP_TRACE_PATH)["frames"]
    manual_frames = frames[57:85]
    assert len(manual_frames) == 28

    # Every manual action arrives as a strict (command_translation, actuator_status) pair
    pairs = list(zip(manual_frames[0::2], manual_frames[1::2], strict=True))
    expected_sequence = [
        # Relay 32: ON (*1*1*32##), then OFF (*1*0*32##)
        ("*1*1000#1*32##", "*1*1*32##"),
        ("*1*1000#0*32##", "*1*0*32##"),
        # F414 dimmer 42: ON restores coarse WHAT 7, OFF, ON restores WHAT 7
        ("*1*1000#1*42##", "*1*7*42##"),
        ("*1*1000#0*42##", "*1*0*42##"),
        ("*1*1000#1*42##", "*1*7*42##"),
        # Hold to dim DOWN: 3 steps (6 -> 5 -> 4)
        ("*1*1000#31*42##", "*1*6*42##"),
        ("*1*1000#31*42##", "*1*5*42##"),
        ("*1*1000#31*42##", "*1*4*42##"),
        # OFF, then ON restores new memory level WHAT 4 (not WHAT 7)
        ("*1*1000#0*42##", "*1*0*42##"),
        ("*1*1000#1*42##", "*1*4*42##"),
        # Hold to dim UP: 3 steps (5 -> 6 -> 7)
        ("*1*1000#30*42##", "*1*5*42##"),
        ("*1*1000#30*42##", "*1*6*42##"),
        ("*1*1000#30*42##", "*1*7*42##"),
        # Final OFF
        ("*1*1000#0*42##", "*1*0*42##"),
    ]
    assert [(cmd["raw"], rep["raw"]) for cmd, rep in pairs] == expected_sequence

    for cmd_frame, status_frame in pairs:
        cmd_event = OWNEvent.parse(cmd_frame["raw"])
        status_event = OWNEvent.parse(status_frame["raw"])
        assert isinstance(cmd_event, OWNLightingEvent)
        assert isinstance(status_event, OWNLightingEvent)
        assert cmd_event.is_translation is True
        assert cmd_event.is_on is None
        assert status_event.is_translation is False
        assert status_event.is_on is (status_frame["what"] != "0")

    # F414 short-press ON/OFF replies in ~34.7-35.2 ms; hold step replies in ~21.8-22.6 ms
    f414_switch_latencies = [
        rep["timestamp"] - cmd["timestamp"]
        for cmd, rep in pairs[2:]
        if cmd["raw"] in ("*1*1000#1*42##", "*1*1000#0*42##")
    ]
    assert len(f414_switch_latencies) == 6
    for dt in f414_switch_latencies:
        assert dt == pytest.approx(0.035, abs=0.002)

    f414_step_latencies = [
        rep["timestamp"] - cmd["timestamp"]
        for cmd, rep in pairs[2:]
        if cmd["raw"] in ("*1*1000#31*42##", "*1*1000#30*42##")
    ]
    assert len(f414_step_latencies) == 6
    for dt in f414_step_latencies:
        assert dt == pytest.approx(0.022, abs=0.001)

    # Wall-button hold emits 1000#31 / 1000#30 at a steady ~520.0 ms repeat period
    dim_down_cmds = [cmd["timestamp"] for cmd, _ in pairs[5:8]]
    dim_up_cmds = [cmd["timestamp"] for cmd, _ in pairs[10:13]]
    for seq in (dim_down_cmds, dim_up_cmds):
        periods = [t2 - t1 for t1, t2 in zip(seq[:-1], seq[1:], strict=True)]
        assert periods == pytest.approx([0.520, 0.520], abs=0.002)


def test_mh201_f414_ha_turn_on_dimension1_followup_and_turn_off_timings() -> None:
    """Verify the 6-frame Home Assistant turn-on, Dimension 1 (`148*5`), and turn-off trace."""
    frames = _load_json(HA_TRACE_PATH)["frames"]
    assert [(f["direction"], f["raw"]) for f in frames] == [
        ("tx", "*1*1*42##"),
        ("rx", "*1*7*42##"),
        ("tx", "*#1*42*1##"),
        ("rx", "*#1*42*1*148*5##"),
        ("tx", "*1*0*42##"),
        ("rx", "*1*0*42##"),
    ]

    # Turn-on round-trip: *1*1*42## -> *1*7*42## (326.5 ms) -> *#1*42*1## (+63.6 ms) -> *#1*42*1*148*5## (+128.5 ms)
    assert frames[1]["timestamp"] - frames[0]["timestamp"] == pytest.approx(0.3265, abs=0.005)
    assert frames[2]["timestamp"] - frames[1]["timestamp"] == pytest.approx(0.0636, abs=0.005)
    assert frames[3]["timestamp"] - frames[2]["timestamp"] == pytest.approx(0.1285, abs=0.005)
    assert frames[5]["timestamp"] - frames[4]["timestamp"] == pytest.approx(0.0796, abs=0.005)

    coarse_on = OWNEvent.parse(frames[1]["raw"])
    assert isinstance(coarse_on, OWNLightingEvent)
    assert coarse_on.is_on is True
    assert coarse_on.brightness is None
    assert coarse_on.brightness_preset == 7

    dim1_reply = OWNEvent.parse(frames[3]["raw"])
    assert isinstance(dim1_reply, OWNLightingEvent)
    assert dim1_reply.is_on is True
    assert dim1_reply.brightness == 48
    assert dim1_reply.brightness_preset is None

    off_reply = OWNEvent.parse(frames[5]["raw"])
    assert isinstance(off_reply, OWNLightingEvent)
    assert off_reply.is_on is False


async def test_mh201_f414_live_integration_replay_sweep_wall_dimming_and_ha_control(
    hass: HomeAssistant,
) -> None:
    """Replay both captures end-to-end through a live `MH201` config entry."""
    sweep_frames = _load_json(SWEEP_TRACE_PATH)["frames"]
    ha_frames = _load_json(HA_TRACE_PATH)["frames"]

    mac = "00:03:50:00:04:66"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="MH201 Gateway",
        data={
            CONF_HOST: "192.0.2.46",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "MH201",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:webserver:1",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "3.6.43",
            CONF_SSDP_LOCATION: "http://192.0.2.46:49152/description.xml",
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

    async def _feed(raw: str) -> None:
        msg = OWNEvent.parse(raw) or OWNMessage.parse(raw)
        assert msg is not None
        await handler._event_dispatcher.process_message(msg)

    # ── Phase 1: Replay both 28-frame sweep bursts (`frames[0..56]`) ─────────
    for frame in sweep_frames[:57]:
        await _feed(frame["raw"])
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    reg_entries = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    by_domain: dict[str, list[er.RegistryEntry]] = {}
    for reg_entry in reg_entries:
        by_domain.setdefault(reg_entry.domain, []).append(reg_entry)

    light_entries = by_domain.get("light", [])
    assert len(light_entries) == 27
    entity_by_where = {
        reg_entry.unique_id.rsplit("-", 1)[-1]: reg_entry.entity_id
        for reg_entry in light_entries
    }
    assert set(entity_by_where) == EXPECTED_27_LIGHT_WHERES

    for where, entity_id in entity_by_where.items():
        state = hass.states.get(entity_id)
        assert state is not None
        expected_state = STATE_ON if where in EXPECTED_ON_IN_SWEEP else STATE_OFF
        assert state.state == expected_state

    # F414 at WHERE=42 reported *1*0*42## during sweep, so it starts as plain ONOFF with FLASH
    light_32_id = entity_by_where["32"]
    light_42_id = entity_by_where["42"]
    state_42 = hass.states.get(light_42_id)
    assert state_42 is not None
    assert state_42.attributes["supported_color_modes"] == [ColorMode.ONOFF]
    assert state_42.attributes[ATTR_SUPPORTED_FEATURES] & LightEntityFeature.FLASH

    for forbidden_domain in (
        "cover",
        "climate",
        "media_player",
        "alarm_control_panel",
        "sensor",
    ):
        assert by_domain.get(forbidden_domain, []) == []

    # ── Phase 2: Replay manual wall-switch sequence (`frames[57..84]`) ───────
    # Relay 32: *1*1000#1*32## leaves it OFF until *1*1*32## confirms ON, and stays ColorMode.ONOFF
    await _feed(sweep_frames[57]["raw"])  # *1*1000#1*32##
    await hass.async_block_till_done()
    assert hass.states.get(light_32_id).state == STATE_OFF  # type: ignore[union-attr]

    await _feed(sweep_frames[58]["raw"])  # *1*1*32##
    await hass.async_block_till_done()
    state_32 = hass.states.get(light_32_id)
    assert state_32 is not None and state_32.state == STATE_ON
    assert state_32.attributes["supported_color_modes"] == [ColorMode.ONOFF]

    await _feed(sweep_frames[59]["raw"])  # *1*1000#0*32##
    await _feed(sweep_frames[60]["raw"])  # *1*0*32##
    await hass.async_block_till_done()
    assert hass.states.get(light_32_id).state == STATE_OFF  # type: ignore[union-attr]

    # F414 dimmer 42: *1*1000#1*42## ignored, then *1*7*42## auto-promotes to BRIGHTNESS + TRANSITION
    await _feed(sweep_frames[61]["raw"])  # *1*1000#1*42##
    await hass.async_block_till_done()
    assert hass.states.get(light_42_id).state == STATE_OFF  # type: ignore[union-attr]

    await _feed(sweep_frames[62]["raw"])  # *1*7*42## (50% on BTicino dimmerLevelTo100 curve)
    await hass.async_block_till_done()
    state_42 = hass.states.get(light_42_id)
    assert state_42 is not None and state_42.state == STATE_ON
    assert state_42.attributes["supported_color_modes"] == [ColorMode.BRIGHTNESS]
    assert state_42.attributes[ATTR_SUPPORTED_FEATURES] & LightEntityFeature.TRANSITION
    assert not (state_42.attributes[ATTR_SUPPORTED_FEATURES] & LightEntityFeature.FLASH)
    assert state_42.attributes[ATTR_BRIGHTNESS] == percent_to_eight_bits(50)

    # Feed remaining wall-button dimmer steps (frames[63..84]) and verify coarse step progression
    observed_f414_states: list[tuple[str, int | None]] = []
    for frame in sweep_frames[63:85]:
        await _feed(frame["raw"])
        await hass.async_block_till_done()
        if not frame["raw"].startswith("*1*1000#"):
            st = hass.states.get(light_42_id)
            assert st is not None
            observed_f414_states.append((st.state, st.attributes.get(ATTR_BRIGHTNESS)))

    assert observed_f414_states == [
        (STATE_OFF, None),
        (STATE_ON, percent_to_eight_bits(50)),
        (STATE_ON, percent_to_eight_bits(40)),
        (STATE_ON, percent_to_eight_bits(30)),
        (STATE_ON, percent_to_eight_bits(20)),
        (STATE_OFF, None),
        (STATE_ON, percent_to_eight_bits(20)),
        (STATE_ON, percent_to_eight_bits(30)),
        (STATE_ON, percent_to_eight_bits(40)),
        (STATE_ON, percent_to_eight_bits(50)),
        (STATE_OFF, None),
    ]

    # ── Phase 3: Replay HA `light.turn_on` & `light.turn_off` (`ha_frames`) ──
    sent_frames: list[str] = []

    async def _record_send(cmd: Any, *_: Any, **__: Any) -> bool:
        sent_frames.append(str(cmd))
        return True

    with (
        patch.object(handler, "send", AsyncMock(side_effect=_record_send)),
        patch.object(handler, "send_status_request", AsyncMock(side_effect=_record_send)),
    ):
        # HA turn_on emits exact captured tx frames [0] (*1*1*42##) and [2] (*#1*42*1##)
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": light_42_id},
            blocking=True,
        )
        assert sent_frames == [ha_frames[0]["raw"], ha_frames[2]["raw"]]

        # Feed captured rx [1] (*1*7*42## -> 50% = 128), then rx [3] (*#1*42*1*148*5## -> 48% = 122)
        await _feed(ha_frames[1]["raw"])
        await hass.async_block_till_done()
        assert hass.states.get(light_42_id).attributes[ATTR_BRIGHTNESS] == percent_to_eight_bits(50) == 128  # type: ignore[union-attr]

        await _feed(ha_frames[3]["raw"])
        await hass.async_block_till_done()
        state_after_dim1 = hass.states.get(light_42_id)
        assert state_after_dim1 is not None
        assert state_after_dim1.state == STATE_ON
        assert state_after_dim1.attributes[ATTR_BRIGHTNESS] == percent_to_eight_bits(48) == 122

        # HA turn_off emits exact captured tx frame [4] (*1*0*42##), confirmed by rx [5] (*1*0*42##)
        sent_frames.clear()
        await hass.services.async_call(
            "light",
            "turn_off",
            {"entity_id": light_42_id},
            blocking=True,
        )
        assert sent_frames == [ha_frames[4]["raw"]]

        await _feed(ha_frames[5]["raw"])
        await hass.async_block_till_done()
        state_after_off = hass.states.get(light_42_id)
        assert state_after_off is not None
        assert state_after_off.state == STATE_OFF

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def test_mh201_f414_pr828_full_what_2_to_10_ladder_clamps_and_dim1_recall() -> None:
    """Verify full WHAT 2..10 ladder, ceiling/floor clamps, fine Dim 1 writes, 78%->WHAT 9 recall, and 1% turn_on."""
    frames = _load_json(PR828_VERIFY_TRACE_PATH)["frames"]

    # 1. WHO breakdown: 170 WHO 1 (19 tx + 151 rx across all 27 lights), 29 WHO 13 (*#13**15*200## every ~5.0 s),
    # and 1 WHO 2 cover frame (*2*0*33## at frames[36], clarifying why #827's *#2*0## sweep answered cleanly).
    who_counts: dict[str, int] = {}
    for f in frames:
        who_counts[f["who"]] = who_counts.get(f["who"], 0) + 1
    assert who_counts == {"1": 170, "13": 29, "2": 1}

    assert frames[36]["direction"] == "rx"
    assert frames[36]["raw"] == "*2*0*33##"

    who13_frames = [f for f in frames if f["who"] == "13"]
    assert all(f["direction"] == "rx" and f["raw"] == "*#13**15*200##" for f in who13_frames)
    for f_prev, f_next in zip(who13_frames[:-1], who13_frames[1:], strict=True):
        assert f_next["timestamp"] - f_prev["timestamp"] == pytest.approx(5.00, abs=0.05)

    all_who1_rx_wheres = {
        f["where"] for f in frames if f["who"] == "1" and f["direction"] == "rx"
    }
    assert all_who1_rx_wheres == EXPECTED_27_LIGHT_WHERES

    # 2. Extract F414 (WHERE=42) frames and verify all 15 HA fine Dimension 1 slider writes + speed=5 echoes
    f42_frames = [f for f in frames if f.get("where") == "42"]
    assert len(f42_frames) == 143

    dim1_slider_pairs = list(zip(f42_frames[30:60:2], f42_frames[31:60:2], strict=True))
    expected_slider_pcts = [3, 15, 35, 47, 58, 71, 87, 95, 99, 93, 93, 89, 84, 78]
    assert len(dim1_slider_pairs[:14]) == 14
    for (tx_f, rx_f), pct in zip(dim1_slider_pairs[:14], expected_slider_pcts, strict=True):
        assert tx_f["direction"] == "tx" and tx_f["raw"] == f"*#1*42*#1*{100 + pct}*0##"
        assert rx_f["direction"] == "rx" and rx_f["raw"] == f"*#1*42*1*{100 + pct}*5##"
        parsed_echo = OWNEvent.parse(rx_f["raw"])
        assert isinstance(parsed_echo, OWNLightingEvent)
        assert parsed_echo.brightness == pct

    # Followed by HA turn_off from 78% (`178*5`): tx *1*0*42## -> rx *1*0*42##
    assert (f42_frames[58]["direction"], f42_frames[58]["raw"]) == ("tx", "*1*0*42##")
    assert (f42_frames[59]["direction"], f42_frames[59]["raw"]) == ("rx", "*1*0*42##")

    # 3. Physical wall-button ON recall right after 78% (`178`) HA turn_off:
    # F414 answers *1*9*42## (COARSE_PRESET_TO_PERCENT[9] == 75%, matching 68..85% -> WHAT 9 within 3 pp)
    assert (f42_frames[60]["direction"], f42_frames[60]["raw"]) == ("rx", "*1*1000#1*42##")
    assert (f42_frames[61]["direction"], f42_frames[61]["raw"]) == ("rx", "*1*9*42##")
    assert COARSE_PRESET_TO_PERCENT[9] == 75

    # 4. Full WHAT 2..10 wall-button ladder + ceiling clamp (WHAT 10 -> 10) + floor clamp (WHAT 2 -> 2 -> 2)
    wall_pairs_phase1 = list(zip(f42_frames[0:30:2], f42_frames[1:30:2], strict=True))
    assert [int(rep["what"]) for _, rep in wall_pairs_phase1] == [
        5, 6, 7, 8, 9, 10, 10,  # ON to 5, hold UP 6 steps clamping at ceiling WHAT 10 (100%)
        9, 8, 7, 6, 5, 4, 3,    # hold DOWN 7 steps to WHAT 3 (10%)
        0,                      # wall OFF
    ]

    wall_pairs_phase2 = list(zip(f42_frames[60:134:2], f42_frames[61:134:2], strict=True))
    assert [int(rep["what"]) for _, rep in wall_pairs_phase2] == [
        9,                              # wall ON recall after fine 78%
        8, 7,                           # hold DOWN 2 steps
        8, 9, 10, 10,                   # hold UP 4 steps clamping at ceiling WHAT 10 (100%)
        9, 8, 7, 6, 5, 4, 3, 2,         # full sweep DOWN to minimum WHAT 2 (1%)
        3, 4, 5, 6, 7, 8, 9, 10, 10,    # full sweep UP to maximum WHAT 10 (100%) + ceiling clamp
        9, 8, 7, 6, 5, 4, 3, 2, 2, 2,   # full sweep DOWN + floor clamp repeating *1*2*42## x3 (never turns OFF)
        0, 2, 0,                        # wall OFF, wall ON recall at minimum WHAT 2 (1%), wall OFF
    ]
    observed_presets = {
        int(rep["what"])
        for _, rep in wall_pairs_phase1 + wall_pairs_phase2
        if int(rep["what"]) > 0
    }
    assert observed_presets == set(range(2, 11)) == set(COARSE_PRESET_TO_PERCENT)

    # 5. Zero-bounce HA turn_on at minimum brightness (`frames[168..171]` == `f42_frames[134..137]`):
    # tx *1*1*42## -> rx *1*2*42## (1%, 8-bit 3) -> tx *#1*42*1## -> rx *#1*42*1*101*5## (1%, 8-bit 3)
    assert [(f["direction"], f["raw"]) for f in frames[168:178] if f.get("where") == "42"] == [
        ("tx", "*1*1*42##"),
        ("rx", "*1*2*42##"),
        ("tx", "*#1*42*1##"),
        ("rx", "*#1*42*1*101*5##"),
        ("tx", "*#1*42*#1*158*0##"),
        ("rx", "*#1*42*1*158*5##"),
        ("tx", "*1*0*42##"),
        ("rx", "*1*0*42##"),
    ]
    assert frames[169]["timestamp"] - frames[168]["timestamp"] == pytest.approx(0.3138, abs=0.005)
    assert frames[170]["timestamp"] - frames[169]["timestamp"] == pytest.approx(0.0637, abs=0.005)
    assert frames[171]["timestamp"] - frames[170]["timestamp"] == pytest.approx(0.1282, abs=0.005)


async def test_mh201_f414_pr828_live_integration_replay_zero_bounce_1pct_and_cover_33(
    hass: HomeAssistant,
) -> None:
    """Replay the 200-frame PR #828 capture end-to-end: 27 lights + cover 33 + zero-bounce 1% turn_on."""
    frames = _load_json(PR828_VERIFY_TRACE_PATH)["frames"]

    mac = "00:03:50:00:08:28"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="MH201 Gateway",
        data={
            CONF_HOST: "192.0.2.46",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "MH201",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:webserver:1",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "3.6.43",
            CONF_SSDP_LOCATION: "http://192.0.2.46:49152/description.xml",
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

    async def _feed(raw: str) -> None:
        msg = OWNEvent.parse(raw) or OWNMessage.parse(raw)
        assert msg is not None
        await handler._event_dispatcher.process_message(msg)

    # Replay rx bus traffic up to frame 167 (right before HA turn_on at minimum brightness),
    # recording every brightness value light.light_42 publishes during the WHAT 2..10 ladder & Dim 1 slider.
    seen_preset_8bit_levels: set[int] = set()
    for idx, frame in enumerate(frames[:168]):
        if frame["direction"] != "rx":
            continue
        await _feed(frame["raw"])
        await hass.async_block_till_done()

        if frame.get("where") == "42" and not frame["raw"].startswith("*1*1000#"):
            ent_reg = er.async_get(hass)
            light_42_eid = ent_reg.async_get_entity_id("light", DOMAIN, f"{mac}-1-42")
            assert light_42_eid is not None
            st = hass.states.get(light_42_eid)
            assert st is not None
            # Verify wall-button ON recall at idx 81 (`*1*9*42##` right after fine 78% OFF) -> 75% (191)
            if idx == 81:
                assert st.state == STATE_ON
                assert st.attributes[ATTR_BRIGHTNESS] == percent_to_eight_bits(75) == 191
            if frame.get("dimension") is None and frame.get("what") not in (None, "0", "1"):
                seen_preset_8bit_levels.add(st.attributes[ATTR_BRIGHTNESS])

    # Every single preset in COARSE_PRESET_TO_PERCENT (1, 10, 20, 30, 40, 50, 60, 75, 100%) was exercised!
    assert seen_preset_8bit_levels == {
        percent_to_eight_bits(pct) for pct in COARSE_PRESET_TO_PERCENT.values()
    }

    # Feed remaining background rx frames (`frames[178:]`) to ensure all 26 other plant lights also reported
    # before checking full plant inventory, then restore light_42's pre-168 OFF state at WHAT 2 memory.
    ent_reg = er.async_get(hass)
    cover_33_eid = ent_reg.async_get_entity_id("cover", DOMAIN, f"{mac}-2-33")
    assert cover_33_eid is not None
    cover_state = hass.states.get(cover_33_eid)
    assert cover_state is not None
    assert cover_state.state == STATE_UNKNOWN

    light_42_id = ent_reg.async_get_entity_id("light", DOMAIN, f"{mac}-1-42")
    assert light_42_id is not None
    assert hass.states.get(light_42_id).state == STATE_OFF  # type: ignore[union-attr]

    # Replay zero-bounce HA `light.turn_on` at minimum brightness (`frames[168..171]`)
    sent_frames: list[str] = []

    async def _record_send(cmd: Any, *_: Any, **__: Any) -> bool:
        sent_frames.append(str(cmd))
        return True

    with (
        patch.object(handler, "send", AsyncMock(side_effect=_record_send)),
        patch.object(handler, "send_status_request", AsyncMock(side_effect=_record_send)),
    ):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": light_42_id},
            blocking=True,
        )
        assert sent_frames == [frames[168]["raw"], frames[170]["raw"]] == [
            "*1*1*42##",
            "*#1*42*1##",
        ]

        # Feed coarse reply `frames[169]` (`*1*2*42##`): maps directly to 1% (8-bit 3, NOT 20% / 51!)
        await _feed(frames[169]["raw"])
        await hass.async_block_till_done()
        state_coarse = hass.states.get(light_42_id)
        assert state_coarse is not None
        assert state_coarse.state == STATE_ON
        assert state_coarse.attributes[ATTR_BRIGHTNESS] == percent_to_eight_bits(1) == 3

        # Feed exact Dimension 1 reply `frames[171]` (`*#1*42*1*101*5##`): stays at 1% (8-bit 3, 0 bounce!)
        await _feed(frames[171]["raw"])
        await hass.async_block_till_done()
        state_dim1 = hass.states.get(light_42_id)
        assert state_dim1 is not None
        assert state_dim1.state == STATE_ON
        assert state_dim1.attributes[ATTR_BRIGHTNESS] == percent_to_eight_bits(1) == 3

    for frame in frames[172:]:
        if frame["direction"] == "rx":
            await _feed(frame["raw"])
    await hass.async_block_till_done()

    reg_entries = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    light_wheres = {
        reg_entry.unique_id.rsplit("-", 1)[-1]
        for reg_entry in reg_entries
        if reg_entry.domain == "light"
    }
    assert light_wheres == EXPECTED_27_LIGHT_WHERES

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

