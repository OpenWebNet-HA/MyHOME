"""Replay and verification tests for #767: BTicino MH200N pre-09W29 F414 dimmer 61 trace.

Fixture: tests/fixtures/traces/issue_767/myhome_trace_MH200N_all_2026-10-21T18-12-44.json
Contributed by @wave68runner on https://github.com/OpenWebNet-HA/MyHOME/issues/767
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import OWNd.message as ownd_message
import pytest
from homeassistant.components.light.const import ColorMode
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from OWNd.message import OWNCommand, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import CONF_BROADCAST_RESYNC, DOMAIN, percent_to_eight_bits
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.light import MyHOMELight
from custom_components.myhome.light import async_setup_entry as async_setup_light
from custom_components.myhome.router import FrameRouter

TRACE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_767"
    / "myhome_trace_MH200N_all_2026-10-21T18-12-44.json"
)
MAC = "00:03:50:aa:bb:61"


def _load_trace() -> dict:
    return json.loads(TRACE_PATH.read_text(encoding="utf-8"))


def _clear_coarse_registry() -> None:
    clear_fn = getattr(ownd_message, "clear_coarse_dimmer_wheres", None)
    if callable(clear_fn):
        clear_fn()


def test_issue_767_trace_fixture_schema_and_metadata() -> None:
    """Verify the Issue #767 MH200N + F414 trace fixture metadata and frame parsing."""
    _clear_coarse_registry()
    try:
        assert TRACE_PATH.is_file()
        data = _load_trace()

        assert data["gateway"]["model"] == "MH200N"
        assert data["gateway"]["firmware"] == "1.0"
        assert data["gateway"]["mac_prefix"] == "00:03:50"
        assert data["gateway"]["identification"]["source"] == "ssdp"
        assert data["gateway"]["identification"]["who13_code"] == "44"
        assert data["gateway"]["identification"]["profile"] == "MH200NProfile"
        assert data["environment"]["integration_version"] == "2.0.0b15"
        assert data["environment"]["ownd_version"] == "2.0.0b10"
        assert data["capture"]["window"]["frames"] == 43
        assert data["telemetry"]["total_rx"] == 141
        assert data["telemetry"]["total_tx"] == 15

        frames = data["frames"]
        assert len(frames) == 43
        assert sum(1 for f in frames if f["direction"] == "rx") == 35
        assert sum(1 for f in frames if f["direction"] == "tx") == 8

        whos_seen: set[str] = set()
        dim1_replies: list[ tuple[str, int, int] ] = []

        for entry in frames:
            raw = entry["raw"]
            direction = entry["direction"]
            parsed = OWNCommand.parse(raw) if direction == "tx" else OWNMessage.parse(raw)
            assert parsed is not None, f"Failed to parse {direction} frame: {raw}"
            whos_seen.add(str(entry["who"]))
            if direction == "rx" and getattr(parsed, "dimension", None) == 1:
                dim1_replies.append(
                    (
                        str(parsed.where),
                        int(parsed.brightness),
                        int(parsed.transition),
                    )
                )
                if hasattr(parsed, "is_coarse_dimmer"):
                    assert parsed.is_coarse_dimmer is True

        assert whos_seen == {"1", "13"}
        assert dim1_replies == [
            ("61", 30, 240),
            ("61", 78, 240),
            ("61", 46, 240),
            ("61", 81, 240),
            ("61", 40, 240),
        ]
    finally:
        _clear_coarse_registry()


@pytest.mark.asyncio
async def test_issue_767_f414_dimmer_61_step_and_ha_dimming_replay(
    hass: HomeAssistant,
) -> None:
    """Replay physical/iMyHome stepping and HA dimming of F414 dimmer 61 from the trace."""
    _clear_coarse_registry()
    try:
        trace = _load_trace()
        frames = trace["frames"]

        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.1.40",
                CONF_PORT: 20000,
                CONF_PASSWORD: "12345",
                CONF_MAC: MAC,
                CONF_NAME: "MH200N",
            },
            options={CONF_BROADCAST_RESYNC: False},
        )
        entry.add_to_hass(hass)

        with patch("custom_components.myhome.gateway.OWNGateway"):
            handler = MyHOMEGatewayHandler(
                hass, entry, generate_events=False, broadcast_resync=False
            )
            handler.gateway.serial = MAC
            handler.send = AsyncMock()
            handler.send_status_request = AsyncMock()

        router = FrameRouter()
        entry.runtime_data = MyHOMERuntimeData(
            gateway=handler,
            router=router,
            platforms={
                "light": {
                    "dimmer_61": {
                        "where": "61",
                        "name": "F414 Dimmer 61",
                        "dimmable": False,
                    },
                },
                "switch": {},
            },
        )

        added_lights: list[MyHOMELight] = []
        await async_setup_light(
            hass, entry, lambda entities, update=False: added_lights.extend(entities)
        )
        assert len(added_lights) == 1
        light_61 = added_lights[0]
        light_61.hass = hass
        light_61.async_write_ha_state = lambda: None
        light_61.async_schedule_update_ha_state = lambda force_refresh=False: None
        await light_61.async_added_to_hass()

        # Initially configured with dimmable=False -> ColorMode.ONOFF before bus events
        assert light_61.supported_color_modes == {ColorMode.ONOFF}

        def _deliver_rx(raw_frame: str) -> OWNMessage:
            msg = OWNMessage.parse(raw_frame)
            assert msg is not None
            if msg.who == 1 and not getattr(msg, "is_translation", False):
                where = getattr(msg, "where", None)
                if where:
                    router.publish("1", (str(where),), msg)
            return msg

        # 1. Frames 0..1: physical button turns 61 ON (*1*1000#1*61## -> *1*9*61## = 75%)
        for frame_entry in frames[0:2]:
            msg = _deliver_rx(frame_entry["raw"])
            await handler._process_message(msg)
        assert light_61.is_on is True
        assert light_61.supported_color_modes == {ColorMode.BRIGHTNESS}
        assert light_61._attr_brightness_pct == 75
        assert light_61.brightness == percent_to_eight_bits(75)

        # 2. Frames 2..15: 7 step-up sequences (*1*1000#30*61## -> *1*10*61##)
        for frame_entry in frames[2:16]:
            msg = _deliver_rx(frame_entry["raw"])
            await handler._process_message(msg)
        assert light_61.is_on is True
        assert light_61._attr_brightness_pct == 100
        assert light_61.brightness == 255

        # 3. Frames 16..25: 5 step-down sequences (*1*1000#31*61## -> *1*9..5*61## = 75..30%)
        expected_step_down_pcts = [75, 60, 50, 40, 30]
        for idx, expected_pct in enumerate(expected_step_down_pcts):
            pair_start = 16 + idx * 2
            for frame_entry in frames[pair_start : pair_start + 2]:
                msg = _deliver_rx(frame_entry["raw"])
                await handler._process_message(msg)
            assert light_61.is_on is True
            assert light_61._attr_brightness_pct == expected_pct

        # 4. Frame 26: WHO=13 Dimension 22 gateway datetime broadcast
        msg_who13 = _deliver_rx(frames[26]["raw"])
        await handler._process_message(msg_who13)

        # 5. Frames 27..28: HA turns 61 OFF (tx *1*0*61## -> rx *1*0*61##)
        await light_61.async_turn_off()
        assert str(handler.send.call_args[0][0]) == frames[27]["raw"] == "*1*0*61##"
        msg = _deliver_rx(frames[28]["raw"])
        await handler._process_message(msg)
        assert light_61.is_on is False

        # 6. Frames 29..32: HA turns 61 ON (tx *1*1*61## -> rx *1*5*61## + tx *#1*61*1## -> rx *#1*61*1*130*240##)
        await light_61.async_turn_on()
        assert str(handler.send.call_args[0][0]) == frames[29]["raw"] == "*1*1*61##"
        assert (
            str(handler.send_status_request.call_args[0][0])
            == frames[31]["raw"]
            == "*#1*61*1##"
        )
        msg = _deliver_rx(frames[30]["raw"])
        await handler._process_message(msg)
        assert light_61.is_on is True
        assert light_61._attr_brightness_pct == 30

        msg_dim1 = _deliver_rx(frames[32]["raw"])
        await handler._process_message(msg_dim1)
        assert msg_dim1.transition == 240
        assert light_61.is_on is True
        assert light_61._attr_brightness_pct == 30

        # 7. Frames 33..40: HA brightness changes (78%, 46%, 81%, 40%) and F414 Dimension 1 replies
        # With OWNd >= 2.0.0b11 (PR #134), seeing speed=240 auto-registers WHERE=61 as a
        # coarse-only dimmer and emits *1*<preset>*61##; with OWNd 2.0.0b10 it emitted *#1*61*#1*<level>*0##.
        has_coarse_support = callable(getattr(ownd_message, "is_coarse_dimmer_where", None))
        expected_steps = [
            (33, 34, 78, "*#1*61*#1*178*0##", "*1*9*61##"),
            (35, 36, 46, "*#1*61*#1*146*0##", "*1*7*61##"),
            (37, 38, 81, "*#1*61*#1*181*0##", "*1*9*61##"),
            (39, 40, 40, "*#1*61*#1*140*0##", "*1*6*61##"),
        ]
        for tx_idx, rx_idx, pct, legacy_tx, coarse_tx in expected_steps:
            assert frames[tx_idx]["raw"] == legacy_tx
            await light_61.async_turn_on(brightness_pct=pct)
            emitted_tx = str(handler.send.call_args[0][0])
            if has_coarse_support:
                assert emitted_tx == coarse_tx
            else:
                assert emitted_tx == legacy_tx
            msg_reply = _deliver_rx(frames[rx_idx]["raw"])
            await handler._process_message(msg_reply)
            assert msg_reply.transition == 240
            assert light_61._attr_brightness_pct == pct

        # 8. Frames 41..42: HA turns 61 OFF (tx *1*0*61## -> rx *1*0*61##)
        await light_61.async_turn_off()
        assert str(handler.send.call_args[0][0]) == frames[41]["raw"] == "*1*0*61##"
        msg = _deliver_rx(frames[42]["raw"])
        await handler._process_message(msg)
        assert light_61.is_on is False
    finally:
        _clear_coarse_registry()
