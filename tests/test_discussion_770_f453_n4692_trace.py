"""Tests for Discussion #770: Physical BTicino F453 (FW 2.0.10) + N4692 Knob Sweep & 3550 Trace.

Contributed by @DinoMesina in:
- https://github.com/orgs/OpenWebNet-HA/discussions/770#discussioncomment-19616515
(BTicino F453 firmware 2.0.10, kernel 2.3.8, ETLinux 3.0.1; BTicino 3550 central unit `#0`,
61 active `WHO=4` zones with BTicino N4692 probes, circulation pumps `0#1`/`0#2`, and BTicino
3480 `WHO=25` dry-contact interface `329`; Home Assistant 2026.11.1, MyHOME 2.0.0b17,
OWNd 2.0.0b13).

Pins four hardware and integration facts from the verbatim 200-frame `myhome.sweep_bus` export:
1. Verbatim SHA-256 provenance, privacy hygiene (`findings == []`), `F453` (`16` / `2.0.10`,
   `F453Profile` `0.15 s` pacing) metadata, `196 rx` / `4 tx` (`196 WHO=4`, `4 WHO=25`) frame
   accounting, and `OWNd` parser coverage across all 200 frames.
2. Point-to-point vs broadcast/central `WHO=4` Dimension 13 behaviour (`frames[130..135]`):
   `*#4*1*0##` -> `*#4*1*0*0130##` (`94.8 ms`), `*#4*1*13##` -> `*#4*1*13*00##` (`87.1 ms`),
   while general `*#4*0*13##` and central `*#4*#0*13##` elicit zero replies.
3. Startup poll frame selection on `F453` (`supports_zone_status = False`,
   `zone_temperature_poll_needed = False`): queues general `*#4*0##` in the startup sweep,
   `*#4*#0##` for `#0`, and point-to-point `*#4*1*13##` for Zone `1`'s knob, never
   `*#4*0*13##` or `*#4*#0*13##`.
4. End-to-end Home Assistant config-entry replay across all 200 frames:
   - Tracks every Zone `1` `N4692` transition: initial `13.0 °C` + knob `0`, `-3` -> `*`
     (`OFF`, `7.0 °C`) -> `OFF` (`8.0 °C`) -> `*` (`7.0 °C`) -> `-3` (`HEAT`, `5.0 °C`,
     nominal `8.0 °C`), the 11-step standalone live numeric knob sweep (`-2 -> -1 -> 0 -> -1 ->
     +1 -> +2 -> +3 -> +1 -> 0 -> -1 -> -3` tracking `6.0..11.0..5.0 °C` without any `Dimension 12`
     or `Dimension 14` frames), and the final `*` -> `OFF` -> `-2` (`6.0 °C` with `Dimension 12`)
     -> `-1` (`7.0 °C`, `529.4 ms` later) -> `0` (`8.0 °C`, `686.3 ms` later) sweep.
   - Verifies `#0` (`BTicino 3550`) state (`HEAT`, `8.0 °C`, `remote_control = "enabled"`,
     `at_least_one_probe_antifreeze` and `at_least_one_probe_off` settlement) and `WHO=25` `329`
     (`BTicino 3480`) contact sensor `ON`/`OFF` cycles.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
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
from homeassistant.util import dt as dt_util
from OWNd.message import OWNEvent, OWNMessage
from OWNd.profiles import F453Profile
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.myhome.const import (
    CONF_BROADCAST_RESYNC,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_SSDP_LOCATION,
    DOMAIN,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from scripts.anonymize_plant_fixture import findings

TRACE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "discussion_770_f453_n4692"
    / "myhome_sweep_F453_all_2026-11-14T13-34-42.json"
)
TRACE_SHA256 = "c6baad54a8b09d802855e523c28297a6726838d0461dd66efe3bfe8ce24ee827"


def _load_trace() -> dict[str, Any]:
    assert TRACE_PATH.is_file(), f"Missing trace fixture: {TRACE_PATH}"
    return cast(dict[str, Any], json.loads(TRACE_PATH.read_text(encoding="utf-8")))


def test_discussion_770_f453_n4692_provenance_privacy_and_parser_coverage() -> None:
    """Verify SHA-256 digest, privacy hygiene, gateway metadata, and `OWNd` parsing across all 200 frames."""
    raw_text = TRACE_PATH.read_text(encoding="utf-8")
    assert hashlib.sha256(raw_text.encode("utf-8")).hexdigest() == TRACE_SHA256
    assert findings(raw_text, data_file=True) == []

    doc = _load_trace()
    assert doc["environment"]["home_assistant_version"] == "2026.11.1"
    assert doc["environment"]["integration_version"] == "2.0.0b17"
    assert doc["environment"]["ownd_version"] == "2.0.0b13"

    gw = doc["gateway"]
    assert gw["model"] == "F453"
    assert gw["manufacturer"] == "BTicino S.p.A."
    assert gw["firmware"] == "2.0.10"
    assert gw["queue_pacing"] == F453Profile().command_queue_delay == 0.15
    assert gw["identification"]["who13_code"] == "16"
    assert gw["identification"]["who13_firmware"] == "2.0.10"
    assert gw["identification"]["who13_kernel"] == "2.3.8"
    assert gw["identification"]["who13_distribution"] == "3.0.1"
    assert gw["identification"]["who1013_code"] == "16"
    assert gw["identification"]["who1013_brand"] == "1"

    frames = doc["frames"]
    assert len(frames) == 200
    assert sum(1 for f in frames if f["direction"] == "rx") == 196
    assert sum(1 for f in frames if f["direction"] == "tx") == 4
    assert sum(1 for f in frames if f["who"] == "4") == 196
    assert sum(1 for f in frames if f["who"] == "25") == 4

    for entry in frames:
        parsed = OWNMessage.parse(entry["raw"])
        assert parsed is not None, f"Failed to parse captured frame: {entry['raw']}"
        if entry["direction"] == "rx":
            assert OWNEvent.parse(entry["raw"]) is not None, f"Failed OWNEvent.parse on rx frame: {entry['raw']}"


def test_discussion_770_dim13_point_to_point_vs_general_and_knob_timings() -> None:
    """Verify point-to-point `*#4*1*0##`/`*#4*1*13##` latencies, silent `*#4*0*13##`/`*#4*#0*13##`, and sub-second knob detents."""
    frames = _load_trace()["frames"]

    # Point-to-point Zone 1 temperature and knob queries answer in < 100 ms
    assert frames[130]["direction"] == "tx" and frames[130]["raw"] == "*#4*1*0##"
    assert frames[131]["direction"] == "rx" and frames[131]["raw"] == "*#4*1*0*0130##"
    assert 0.05 < frames[131]["timestamp"] - frames[130]["timestamp"] < 0.15

    assert frames[132]["direction"] == "tx" and frames[132]["raw"] == "*#4*1*13##"
    assert frames[133]["direction"] == "rx" and frames[133]["raw"] == "*#4*1*13*00##"
    assert 0.05 < frames[133]["timestamp"] - frames[132]["timestamp"] < 0.15

    # General (*#4*0*13##) and central (*#4*#0*13##) Dimension 13 queries get zero replies
    assert frames[134]["direction"] == "tx" and frames[134]["raw"] == "*#4*0*13##"
    assert frames[135]["direction"] == "tx" and frames[135]["raw"] == "*#4*#0*13##"
    assert frames[135]["timestamp"] - frames[134]["timestamp"] > 9.0

    # In the final mode-return + knob sequence (frames[190..196]), standalone knob detent -1
    # arrives just 529.4 ms after the mode-entry Dimension 12 frame (*#4*1*12*0060*3##)
    assert [frames[i]["raw"] for i in (190, 191, 192, 195, 196)] == [
        "*4*1*1##",
        "*#4*1*13*12##",
        "*#4*1*12*0060*3##",
        "*#4*1*13*11##",
        "*#4*1*13*00##",
    ]
    assert 0.03 < frames[192]["timestamp"] - frames[191]["timestamp"] < 0.10
    assert 0.50 < frames[195]["timestamp"] - frames[192]["timestamp"] < 0.60
    assert 0.60 < frames[196]["timestamp"] - frames[195]["timestamp"] < 0.75


@pytest.mark.asyncio
async def test_discussion_770_f453_n4692_live_replay_tracks_every_knob_detent(
    hass: HomeAssistant,
    fast_bus_pacing: None,
) -> None:
    """Replay all 200 frames on F453 + 3550 + N4692 + 3480 and verify startup polls and live knob tracking."""
    frames = _load_trace()["frames"]
    mac = "00:03:50:00:07:70"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="F453 Gateway",
        data={
            CONF_HOST: "192.0.2.77",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "F453",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.0.10",
            CONF_SSDP_LOCATION: "http://192.0.2.77:49152/description.xml",
        },
        options={
            CONF_BROADCAST_RESYNC: True,
            "migrated_yaml_platforms": {
                "climate": {
                    "4-#0": {
                        "zone": "#0",
                        "name": "Central Unit",
                        "heat": True,
                        "cool": False,
                        "central": True,
                        "standalone": False,
                    },
                    "4-1": {
                        "zone": "1",
                        "name": "Zone 1",
                        "heat": True,
                        "cool": False,
                        "standalone": False,
                    },
                },
                "binary_sensor": {
                    "25-329": {
                        "who": "25",
                        "where": "329",
                        "name": "Contact 329",
                        "class": "window",
                    },
                },
            },
        },
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    sent_status_frames: list[str] = []

    async def _record_status(cmd: Any, *_args: Any, **_kwargs: Any) -> bool:
        sent_status_frames.append(str(cmd))
        return True

    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
        patch(
            "custom_components.myhome.gateway.MyHOMEGatewayHandler.send_status_request",
            side_effect=_record_status,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        handler: MyHOMEGatewayHandler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)
        await hass.async_block_till_done()

    # F453 startup poll asks #0 status (*#4*#0##), general *#4*0## in the sweep, and Zone 1's
    # point-to-point knob (*#4*1*13##), never broadcast *#4*0*13## or central *#4*#0*13##
    assert "*#4*#0##" in sent_status_frames
    assert "*#4*1*13##" in sent_status_frames
    assert "*#4*0*13##" not in sent_status_frames
    assert "*#4*#0*13##" not in sent_status_frames

    async def _feed_rx(start: int, stop: int) -> None:
        for idx in range(start, stop):
            frame = frames[idx]
            if frame["direction"] != "rx":
                continue
            msg = OWNEvent.parse(frame["raw"]) or OWNMessage.parse(frame["raw"])
            assert msg is not None
            await handler._event_dispatcher.process_message(msg)

    with (
        patch.object(handler, "send", AsyncMock(return_value=True)),
        patch.object(handler, "send_status_request", AsyncMock(return_value=True)),
    ):
        # 1. Background zones + initial Zone 1 temperature & knob centre (frames[0..136])
        await _feed_rx(0, 136)
        z1 = hass.states.get("climate.zone_1")
        assert z1 is not None
        assert z1.attributes["current_temperature"] == 13.0
        assert z1.attributes["knob_pos"] == "0"
        assert z1.attributes["local_offset"] == 0
        assert z1.attributes.get("temperature") is None

        # 2. Knob -> -3 (frame 136), then antifreeze * (frames[137..143])
        await _feed_rx(136, 143)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        await hass.async_block_till_done()
        z1 = hass.states.get("climate.zone_1")
        central = hass.states.get("climate.central_unit")
        assert z1 is not None and central is not None
        assert z1.state == HVACMode.OFF
        assert z1.attributes["knob_pos"] == "*"
        assert z1.attributes["temperature"] == 7.0
        assert central.state == HVACMode.HEAT
        assert central.attributes["temperature"] == 8.0
        assert central.attributes["remote_control"] == "enabled"
        assert central.attributes["at_least_one_probe_antifreeze"] is True
        assert central.attributes["at_least_one_probe_off"] is False

        # 3. Knob -> OFF (frames[143..149]), then back to * (frames[149..157])
        await _feed_rx(143, 149)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        await hass.async_block_till_done()
        z1 = hass.states.get("climate.zone_1")
        central = hass.states.get("climate.central_unit")
        assert z1 is not None and central is not None
        assert z1.state == HVACMode.OFF
        assert z1.attributes["knob_pos"] == "OFF"
        assert central.attributes["at_least_one_probe_off"] is True
        assert central.attributes["at_least_one_probe_antifreeze"] is False

        await _feed_rx(149, 157)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        await hass.async_block_till_done()
        z1 = hass.states.get("climate.zone_1")
        assert z1 is not None
        assert z1.state == HVACMode.OFF
        assert z1.attributes["knob_pos"] == "*"

        # 4. Knob -> -3 active heating (frames[157..162]): *4*1*1##, *#4*1*13*13##, *#4*1*12*0050*3##
        await _feed_rx(157, 162)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        await hass.async_block_till_done()
        z1 = hass.states.get("climate.zone_1")
        central = hass.states.get("climate.central_unit")
        assert z1 is not None and central is not None
        assert z1.state == HVACMode.HEAT
        assert z1.attributes["knob_pos"] == "-3"
        assert z1.attributes["local_offset"] == -3
        assert z1.attributes["temperature"] == 5.0
        assert central.attributes["at_least_one_probe_off"] is False
        assert central.attributes["at_least_one_probe_antifreeze"] is False

        # 5. Full 11-step standalone live knob sweep (frames[162..175]) with ZERO Dim 12/14 frames!
        expected_knob_sweep = [
            (162, "-2", -2, 6.0),
            (164, "-1", -1, 7.0),
            (165, "0", 0, 8.0),
            (166, "-1", -1, 7.0),
            (167, "+1", 1, 9.0),
            (168, "+2", 2, 10.0),
            (170, "+3", 3, 11.0),
            (171, "+1", 1, 9.0),
            (172, "0", 0, 8.0),
            (173, "-1", -1, 7.0),
            (174, "-3", -3, 5.0),
        ]
        cursor = 162
        for frame_idx, expected_knob, expected_offset, expected_temp in expected_knob_sweep:
            await _feed_rx(cursor, frame_idx + 1)
            cursor = frame_idx + 1
            z1 = hass.states.get("climate.zone_1")
            assert z1 is not None
            assert z1.state == HVACMode.HEAT
            assert z1.attributes["knob_pos"] == expected_knob
            assert z1.attributes["local_offset"] == expected_offset
            assert z1.attributes["temperature"] == expected_temp

        # 6. Second mode cycle: * (175..181) -> OFF (182..187) + WHO=25 329 ON/OFF (188..189)
        await _feed_rx(175, 188)
        z1 = hass.states.get("climate.zone_1")
        assert z1 is not None
        assert z1.state == HVACMode.OFF
        assert z1.attributes["knob_pos"] == "OFF"

        await _feed_rx(188, 189)
        contact = hass.states.get("binary_sensor.contact_329_window")
        assert contact is not None and contact.state == STATE_ON
        await _feed_rx(189, 190)
        contact = hass.states.get("binary_sensor.contact_329_window")
        assert contact is not None and contact.state == STATE_OFF

        # 7. Return to -2 with Dim 12 (frames[190..195]: 6.0 °C), followed 529.4 ms later by
        # standalone -1 (frame 195 -> 7.0 °C) and 0 (frame 196 -> 8.0 °C), plus final WHO=25 cycle
        await _feed_rx(190, 195)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        await hass.async_block_till_done()
        z1 = hass.states.get("climate.zone_1")
        assert z1 is not None
        assert z1.state == HVACMode.HEAT
        assert z1.attributes["knob_pos"] == "-2"
        assert z1.attributes["local_offset"] == -2
        assert z1.attributes["temperature"] == 6.0

        await _feed_rx(195, 196)
        z1 = hass.states.get("climate.zone_1")
        assert z1 is not None
        assert z1.attributes["knob_pos"] == "-1"
        assert z1.attributes["local_offset"] == -1
        assert z1.attributes["temperature"] == 7.0

        await _feed_rx(196, 199)
        z1 = hass.states.get("climate.zone_1")
        contact = hass.states.get("binary_sensor.contact_329_window")
        assert z1 is not None and contact is not None
        assert z1.state == HVACMode.HEAT
        assert z1.attributes["knob_pos"] == "0"
        assert z1.attributes["local_offset"] == 0
        assert z1.attributes["temperature"] == 8.0
        assert z1.attributes["current_temperature"] == 13.0
        assert contact.state == STATE_ON

        await _feed_rx(199, 200)
        contact = hass.states.get("binary_sensor.contact_329_window")
        assert contact is not None and contact.state == STATE_OFF

    await hass.config_entries.async_unload(entry.entry_id)
