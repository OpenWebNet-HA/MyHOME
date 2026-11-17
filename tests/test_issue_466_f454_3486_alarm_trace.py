"""Tests for #466: Physical BTicino F454 (FW 2.0.51) + 3486 Burglar Alarm Sweep & Arm/Disarm Trace.

Contributed by @gimprota76 in:
- https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-6562473665
(BTicino F454 firmware 2.0.51, BTicino 3486 burglar alarm central unit, Home Assistant 2026.10.4,
MyHOME 2.0.0b16, OWNd 2.0.0b11).

Pins four hardware and integration facts from the verbatim 156-frame `<myhome-bus-card>` export:
1. Verbatim SHA-256 provenance, privacy hygiene, `WHO 13` (`200`, `2.0.51`, kernel `2.6.32`,
   distro `2.16.11`) + `WHO 1013` (`OBJECT_MODEL = 51` / `003598`) identity metadata, exact `54 tx`
   and `102 rx` telemetry accounting (`5 WHO 13 + 4 general + 36 WHO 18 + 8 *1*0*0## area resyncs +
   1 *1*17*3## area resync = 54 tx`), and `OWNd` parser coverage across all 156 frames.
2. Dual 21-endpoint A/PL sweep bursts (`16` `WHO 1` lights + `5` `WHO 2` covers) on `*#1*0##` and
   `*#2*0##`, silent drop of `*#4*0##` (`0` `WHO 4` frames), `*#16*0*5##`, `36` `WHO 18` probes,
   and periodic `~25.0 s` autonomous `*#13**15*200##` gateway beacons.
3. Physical `3486` transponder Away arming (`*2*2*0## -> *5*1*0## -> *5*8*0## -> #5..#8/#1..#4 ->
   *1*0*0## -> *5*5*## -> *2*0*0##`) and transponder disarming (`*5*2*0## -> *5*1*0## -> *5*9*0## ->
   #5..#8 -> *5*1*0## -> *5*9*0## -> *1*17*3## -> #1..#4 -> *5*5*##`), confirming `*5*1*0##`
   (*activation*) is emitted during disarming as well as arming.
4. End-to-end Home Assistant config-entry replay across the 156-frame capture: dynamic discovery of
   `16` lights, `5` covers, and `alarm_control_panel.alarm_0` (with zero phantom `#1..#8` entities),
   `armed_away` / empty-`WHERE` `*5*5*##` / `disarmed` state machine transitions, populated-area
   broadcast resync (`*#1*2..9##` skipping unpopulated Area `1`, plus `*#1*3##`), and final state
   reconciliation (`light.light_31 == "on"`, remaining `15` lights `"off"`).
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from homeassistant.components.alarm_control_panel import AlarmControlPanelState
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
from homeassistant.util import dt as dt_util
from OWNd.message import OWNAlarmEvent, OWNEvent, OWNMessage
from OWNd.profiles import F454Profile
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

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
    / "myhome_sweep_F454_all_2026-11-01T18-58-06.json"
)
TRACE_VERBATIM_SHA256 = "bfbda13c13f76fc203f23348cedcd0ef22b4e182af54729a93ad919852f7c70d"

EXPECTED_16_LIGHT_WHERES: frozenset[str] = frozenset({
    "21",
    "22",
    "25",
    "31",
    "41",
    "51",
    "53",
    "54",
    "61",
    "62",
    "71",
    "73",
    "74",
    "81",
    "84",
    "99",
})
EXPECTED_5_COVER_WHERES: frozenset[str] = frozenset({"23", "42", "52", "64", "72"})


def _load_trace() -> dict[str, Any]:
    assert TRACE_PATH.is_file(), f"Missing trace fixture: {TRACE_PATH}"
    return cast(dict[str, Any], json.loads(TRACE_PATH.read_text(encoding="utf-8")))


def test_f454_3486_trace_verbatim_provenance_privacy_and_parser_coverage() -> None:
    """Verify verbatim SHA-256 digest, privacy hygiene, telemetry accounting, and `OWNd` parsing."""
    raw_text = TRACE_PATH.read_text(encoding="utf-8")
    assert not raw_text.endswith("\n")
    assert hashlib.sha256(raw_text.encode("utf-8")).hexdigest() == TRACE_VERBATIM_SHA256
    assert findings(raw_text) == []

    doc = _load_trace()
    assert doc["environment"] == {
        "home_assistant_version": "2026.10.4",
        "integration_version": "2.0.0b16",
        "ownd_version": "2.0.0b11",
        "exported_at": "2026-11-01T18:58:06.603Z",
    }
    assert doc["capture"]["kind"] == "sweep"
    assert doc["capture"]["filters"] == {"who": None, "where": None, "direction": None}
    assert doc["capture"]["window"]["frames"] == 156
    assert doc["capture"]["window"]["buffer_frames"] == 156
    assert doc["capture"]["window"]["truncated"] is False

    gw = doc["gateway"]
    assert gw["model"] == "F454"
    assert gw["manufacturer"] == "BTicino S.p.A."
    assert gw["firmware"] == "2.0.51"
    assert gw["mac_prefix"] == "00:03:50"
    assert gw["connection_type"] == "tcp"
    assert gw["queue_pacing"] == F454Profile().command_queue_delay == 0.05
    assert gw["identification"] == {
        "model": "F454",
        "source": IDENTIFICATION_SSDP,
        "configured_model": "F454",
        "ssdp_model": "F454",
        "who13_code": "200",
        "who13_model": "F454",
        "who13_model_official": None,
        "who13_model_observed": "F454 / MyHomeServer1 / MH202 / F461 / H4890 / MH201",
        "who13_firmware": "2.0.51",
        "who13_kernel": "2.6.32",
        "who13_distribution": "2.16.11",
        "who1013_code": "51",
        "who1013_model": "F454",
        "who1013_other_names": ["003598"],
        "who1013_n_conf": "15",
        "who1013_brand": "5 (Legrand BTicino)",
        "who1013_line": "0 (Undefined)",
        "profile": "F454Profile",
        "conflict": None,
    }

    assert doc["telemetry"] == {
        "total_rx": 102,
        "total_tx": 54,
        "captured_in_buffer": 156,
        "buffer_depth": 200,
        "queue_depth": 0,
    }
    frames = doc["frames"]
    assert len(frames) == 156
    assert sum(1 for f in frames if f["direction"] == "tx") == 54
    assert sum(1 for f in frames if f["direction"] == "rx") == 102

    for entry in frames:
        parsed = OWNMessage.parse(entry["raw"])
        assert parsed is not None, f"Failed to parse captured frame: {entry['raw']}"
        if entry["direction"] == "rx":
            assert OWNEvent.parse(entry["raw"]) is not None, f"Failed OWNEvent.parse on rx frame: {entry['raw']}"


def test_f454_3486_sweep_subsystems_and_dropped_general_zone_query() -> None:
    """Verify WHO 13 replies, dual 21-endpoint bursts, dropped `*#4*0##`, and 54-frame `tx` math."""
    frames = _load_trace()["frames"]

    # WHO 13 identification queries & replies (frames[0..9]) + two ~25.0 s autonomous beacons
    who13_pairs = [(frames[i]["raw"], frames[i + 1]["raw"]) for i in range(0, 10, 2)]
    assert who13_pairs == [
        ("*#13**0##", "*#13**0*19*57*17*001##"),
        ("*#13**15##", "*#13**15*200##"),
        ("*#13**16##", "*#13**16*2*0*51##"),
        ("*#13**23##", "*#13**23*2*6*32##"),
        ("*#13**24##", "*#13**24*2*16*11##"),
    ]
    assert frames[56]["raw"] == "*#13**15*200##"
    assert frames[132]["raw"] == "*#13**15*200##"
    beacon_delta = float(frames[132]["timestamp"]) - float(frames[56]["timestamp"])
    assert 24.9 <= beacon_delta <= 25.1

    # Both *#1*0## (frames[10..31]) and *#2*0## (frames[32..53]) elicit the same 16 lights + 5 covers
    assert frames[10]["raw"] == "*#1*0##"
    assert frames[32]["raw"] == "*#2*0##"
    for burst in (frames[11:32], frames[33:54]):
        assert len(burst) == 21
        lights = {f["where"] for f in burst if f["who"] == "1"}
        covers = {f["where"] for f in burst if f["who"] == "2"}
        assert lights == EXPECTED_16_LIGHT_WHERES
        assert covers == EXPECTED_5_COVER_WHERES
        assert all(f["raw"] == f"*1*0*{f['where']}##" for f in burst if f["who"] == "1")
        assert all(f["raw"] == f"*2*0*{f['where']}##" for f in burst if f["who"] == "2")

    # *#4*0## (frame 54) produces zero WHO 4 replies; *#16*0*5## (frame 55) produces zero WHO 16 replies
    assert frames[54]["raw"] == "*#4*0##"
    assert frames[55]["raw"] == "*#16*0*5##"
    assert [f for f in frames if f["direction"] == "rx" and f["who"] in ("4", "16", "18")] == []

    # Exact 54 tx breakdown: 5 WHO 13 + 4 general + 36 WHO 18 + 8 (*#1*2..9##) + 1 (*#1*3##)
    tx_raws = [f["raw"] for f in frames if f["direction"] == "tx"]
    assert len(tx_raws) == 54
    assert tx_raws[:5] == ["*#13**0##", "*#13**15##", "*#13**16##", "*#13**23##", "*#13**24##"]
    assert [r for r in tx_raws if r in ("*#1*0##", "*#2*0##", "*#4*0##", "*#16*0*5##")] == [
        "*#1*0##",
        "*#2*0##",
        "*#4*0##",
        "*#16*0*5##",
    ]
    who18_tx = [r for r in tx_raws if r.startswith("*#18*")]
    assert len(who18_tx) == 36
    assert tx_raws[-9:] == [
        "*#1*2##",
        "*#1*3##",
        "*#1*4##",
        "*#1*5##",
        "*#1*6##",
        "*#1*7##",
        "*#1*8##",
        "*#1*9##",
        "*#1*3##",
    ]


def test_f454_3486_transponder_arm_away_and_disarm_frame_sequences() -> None:
    """Verify exact physical `3486` transponder Away arming and disarming `rx` sequences."""
    frames = _load_trace()["frames"]

    # Approach light 31 turns ON via physical sensor/button translation (frames[76..77])
    assert [frames[76]["raw"], frames[77]["raw"]] == ["*1*1000#1*31##", "*1*1*31##"]

    # Transponder Away arming burst (rx frames inside frames[84..99])
    arm_rx = [f["raw"] for f in frames[84:100] if f["direction"] == "rx"]
    assert arm_rx == [
        "*2*2*0##",
        "*5*1*0##",
        "*5*8*0##",
        "*5*18*#5##",
        "*5*18*#6##",
        "*5*18*#7##",
        "*5*18*#8##",
        "*5*11*#1##",
        "*5*11*#2##",
        "*5*11*#3##",
        "*5*11*#4##",
        "*1*0*0##",
        "*5*5*##",
        "*2*0*0##",
    ]

    # Transponder disarming burst (frames[106..120]): *5*1*0## appears twice during disarm!
    disarm_rx = [f["raw"] for f in frames[106:121] if f["direction"] == "rx"]
    assert disarm_rx == [
        "*5*2*0##",
        "*5*1*0##",
        "*5*9*0##",
        "*5*18*#5##",
        "*5*18*#6##",
        "*5*18*#7##",
        "*5*18*#8##",
        "*5*1*0##",
        "*5*9*0##",
        "*1*17*3##",
        "*5*11*#1##",
        "*5*11*#2##",
        "*5*11*#3##",
        "*5*11*#4##",
        "*5*5*##",
    ]

    msg_engage = OWNEvent.parse("*5*8*0##")
    msg_disengage = OWNEvent.parse("*5*9*0##")
    msg_battery = OWNEvent.parse("*5*5*##")
    assert isinstance(msg_engage, OWNAlarmEvent) and msg_engage.is_armed_away is True
    assert isinstance(msg_disengage, OWNAlarmEvent) and msg_disengage.is_disarmed is True
    assert isinstance(msg_battery, OWNAlarmEvent)
    assert msg_battery.where == ""
    assert not msg_battery.is_armed_away and not msg_battery.is_disarmed and not msg_battery.is_alarm


@pytest.mark.asyncio
async def test_f454_3486_live_integration_replay(hass: HomeAssistant) -> None:
    """Replay the 156-frame F454 + 3486 capture end-to-end against a live config entry."""
    frames = _load_trace()["frames"]
    mac = "00:03:50:00:04:54"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.54",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "F454",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:webserver:1",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.0.51",
            CONF_SSDP_LOCATION: "http://192.0.2.54:49152/description.xml",
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

    resync_requests: list[str] = []

    async def _record_status_request(cmd: Any, *_: Any, **__: Any) -> bool:
        resync_requests.append(str(cmd))
        return True

    current_ts = [float(frames[0]["timestamp"])]

    async def _feed_rx_slice(start: int, stop: int) -> None:
        for frame in frames[start:stop]:
            current_ts[0] = float(frame["timestamp"])
            if frame["direction"] != "rx":
                continue
            msg = OWNEvent.parse(frame["raw"]) or OWNMessage.parse(frame["raw"])
            assert msg is not None
            await handler._event_dispatcher.process_message(msg)
        await hass.async_block_till_done()

    with (
        patch(
            "custom_components.myhome.gateway_resync.LightingResyncManager._time",
            new_callable=PropertyMock,
            return_value=SimpleNamespace(monotonic=lambda: current_ts[0]),
        ),
        patch.object(handler, "send", AsyncMock(return_value=True)),
        patch.object(handler, "send_status_request", AsyncMock(side_effect=_record_status_request)),
    ):
        # ── Phase 1: Sweep + approach light `31` ON (`frames[0..83]`) ─────────
        await _feed_rx_slice(0, 84)
        resync_requests.clear()

        ent_reg = er.async_get(hass)
        reg_entries = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        lights = [e for e in reg_entries if e.domain == "light"]
        covers = [e for e in reg_entries if e.domain == "cover"]
        alarms = [e for e in reg_entries if e.domain == "alarm_control_panel"]
        assert {e.unique_id.rsplit("-", 1)[-1] for e in lights} == EXPECTED_16_LIGHT_WHERES
        assert {e.unique_id.rsplit("-", 1)[-1] for e in covers} == EXPECTED_5_COVER_WHERES
        assert alarms == []
        assert hass.states.get("light.light_31").state == STATE_ON  # type: ignore[union-attr]

        # ── Phase 2: Transponder Away arming (`frames[84..99]`) ───────────────
        await _feed_rx_slice(84, 100)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
        await hass.async_block_till_done()

        alarms = [
            e
            for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
            if e.domain == "alarm_control_panel"
        ]
        assert len(alarms) == 1
        alarm_entity_id = alarms[0].entity_id
        assert alarm_entity_id == "alarm_control_panel.alarm_0"

        alarm_state = hass.states.get(alarm_entity_id)
        assert alarm_state is not None
        assert alarm_state.state == AlarmControlPanelState.ARMED_AWAY
        # Empty-WHERE `*5*5*##` routed to "0" updated raw_state/state_code without clearing ARMED_AWAY
        assert alarm_state.attributes["raw_state"] == "battery ok"
        assert alarm_state.attributes["state_code"] == 5

        # `*1*0*0##` polled only the 8 populated lighting areas (2..9), skipping unpopulated Area 1
        light_area_polls = [r for r in resync_requests if r.startswith("*#1*")]
        assert len(light_area_polls) == 8
        assert set(light_area_polls) == {
            "*#1*2##",
            "*#1*3##",
            "*#1*4##",
            "*#1*5##",
            "*#1*6##",
            "*#1*7##",
            "*#1*8##",
            "*#1*9##",
        }
        resync_requests.clear()

        # ── Phase 3: Transponder disarming + resync replies (`frames[100..156]`)
        await _feed_rx_slice(100, 121)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
        await hass.async_block_till_done()
        assert "*#1*3##" in resync_requests

        await _feed_rx_slice(121, 156)

        alarm_state = hass.states.get(alarm_entity_id)
        assert alarm_state is not None
        assert alarm_state.state == AlarmControlPanelState.DISARMED
        assert alarm_state.attributes["raw_state"] == "battery ok"
        assert alarm_state.attributes["state_code"] == 5

        # Final lighting states: Area 3 courtesy light 31 is ON; all other 15 lights are OFF
        for where in EXPECTED_16_LIGHT_WHERES:
            st = hass.states.get(f"light.light_{where}")
            assert st is not None
            assert st.state == (STATE_ON if where == "31" else STATE_OFF)

    await hass.config_entries.async_unload(entry.entry_id)
