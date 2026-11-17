"""Replay and verification tests for #746: BTicino MH200N General OFF scenario and switch 04 resync.

Fixture: tests/fixtures/traces/issue_746/myhome_trace_MH200N_all_2026-10-17T14-28-20.json
Contributed by @wave68runner on https://github.com/OpenWebNet-HA/MyHOME/issues/746
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNCommand, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import CONF_BROADCAST_RESYNC, DOMAIN
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.router import FrameRouter
from custom_components.myhome.switch import async_setup_entry as async_setup_switch

TRACE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_746"
    / "myhome_trace_MH200N_all_2026-10-17T14-28-20.json"
)
MAC = "00:03:50:aa:bb:cc"


def _load_trace() -> dict[str, Any]:
    return json.loads(TRACE_PATH.read_text(encoding="utf-8"))


def test_issue_746_trace_fixture_schema_and_metadata() -> None:
    """Verify the Issue #746 MH200N trace fixture metadata and frame parsing."""
    assert TRACE_PATH.is_file()
    data = _load_trace()

    assert data["gateway"]["model"] == "MH200N"
    assert data["gateway"]["identification"]["source"] == "ssdp"
    assert data["gateway"]["identification"]["who13_code"] == "44"
    assert data["gateway"]["identification"]["profile"] == "MH200NProfile"
    assert data["environment"]["integration_version"] == "2.0.0b15"
    assert data["capture"]["window"]["frames"] == 117
    assert data["telemetry"]["total_rx"] == 108
    assert data["telemetry"]["total_tx"] == 9

    frames = data["frames"]
    assert len(frames) == 117

    whos_seen: set[str] = set()
    for entry in frames:
        raw = entry["raw"]
        direction = entry["direction"]
        parsed = OWNCommand.parse(raw) if direction == "tx" else OWNMessage.parse(raw)
        assert parsed is not None, f"Failed to parse {direction} frame: {raw}"
        whos_seen.add(str(entry["who"]))

    assert whos_seen == {"1", "13", "15", "16", "17", "22", "1001"}


@pytest.mark.asyncio
async def test_issue_746_mh200n_trace_replay_sweeps_area_00_and_updates_switch_04(
    hass: HomeAssistant,
) -> None:
    """Replaying the Issue #746 MH200N trace must poll `*#1*00##` and turn switch `04` OFF."""
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
        options={CONF_BROADCAST_RESYNC: True},
    )
    entry.add_to_hass(hass)

    scheduled_cbs: list[Any] = []

    def _fake_call_later(_hass: HomeAssistant, _delay: float, action: Any) -> Any:
        cancelled = False

        def _cancel() -> None:
            nonlocal cancelled
            cancelled = True

        def _fire() -> None:
            if not cancelled:
                action(None)

        scheduled_cbs.append(_fire)
        return _cancel

    with (
        patch("custom_components.myhome.gateway.OWNGateway"),
        patch("custom_components.myhome.gateway.async_call_later", side_effect=_fake_call_later),
    ):
        handler = MyHOMEGatewayHandler(
            hass, entry, generate_events=True, broadcast_resync=True
        )
        handler.gateway.serial = MAC
        handler.send = AsyncMock()
        handler.send_status_request = AsyncMock()

        router = FrameRouter()
        entry.runtime_data = MyHOMERuntimeData(
            gateway=handler,
            router=router,
            platforms={
                "switch": {
                    "socket_04": {
                        "where": "04",
                        "name": "Wall Switch 04",
                        "class": "switch",
                    },
                },
                "light": {},
            },
        )

        added_switches = []
        await async_setup_switch(
            hass, entry, lambda entities, update=False: added_switches.extend(entities)
        )
        assert len(added_switches) == 1
        switch_04 = added_switches[0]
        switch_04.hass = hass
        switch_04.async_write_ha_state = lambda: None
        await switch_04.async_added_to_hass()

        # Register the Area 00 (01, 03, 04, 09, 0011) and Area 1..5 entities in the entity registry
        reg = er.async_get(hass)
        for where in ("01", "03", "09", "0011", "11", "26", "31", "41", "51"):
            reg.async_get_or_create("light", DOMAIN, f"myhome-{MAC}-1-{where}", config_entry=entry)
        reg.async_get_or_create("switch", DOMAIN, switch_04.unique_id, config_entry=entry)

        assert handler._resync_manager.known_area_entities("00") == {
            "01",
            "03",
            "04",
            "09",
            "0011",
        }
        assert handler._resync_manager.known_light_areas() == ["00", "1", "2", "3", "4", "5"]

        def _deliver_rx(raw_frame: str) -> OWNMessage:
            msg = OWNMessage.parse(raw_frame)
            assert msg is not None
            if msg.who == 1 and not getattr(msg, "is_translation", False):
                where = getattr(msg, "where", None)
                if where and not (
                    getattr(msg, "is_general", False)
                    or getattr(msg, "is_area", False)
                    or getattr(msg, "is_group", False)
                ):
                    router.publish("1", (str(where),), msg)
            return msg

        # Frame 0..1: physical wall switch turns 04 ON (*1*1000#1*04## -> *1*1*04##)
        for frame_entry in frames[:2]:
            msg = _deliver_rx(frame_entry["raw"])
            await handler._process_message(msg)
        assert switch_04.is_on is True

        # Frame 5..6: HA turns 04 OFF (*1*0*04##)
        await switch_04.async_turn_off()
        assert str(handler.send.call_args[0][0]) == frames[5]["raw"] == "*1*0*04##"
        msg = _deliver_rx(frames[6]["raw"])
        await handler._process_message(msg)
        assert switch_04.is_on is False

        # Frame 11..12: wall switch turns 04 ON (*1*1000#1*04## -> *1*1*04##)
        for frame_entry in frames[11:13]:
            msg = _deliver_rx(frame_entry["raw"])
            await handler._process_message(msg)
        assert switch_04.is_on is True

        # Frame 15..27: CEN button *15*01*81## -> Scenario 6 *17*1*6## ->
        # partial Area 00 echoes (01, 03, 09, 0011) -> General OFF *1*0*0## -> *17*2*6##
        for frame_entry in frames[15:28]:
            if frame_entry["direction"] == "rx":
                msg = _deliver_rx(frame_entry["raw"])
                await handler._process_message(msg)

        # Even though 01, 03, 09, 0011 echoed OFF in Area "00", switch 04 did NOT echo,
        # so Area "00" resync timer MUST remain armed!
        assert "00" in handler._resync_timers

        while scheduled_cbs:
            cb = scheduled_cbs.pop(0)
            cb()
        await hass.async_block_till_done()

        sent_status_frames = [
            str(call[0][0]) for call in handler.send_status_request.call_args_list
        ]
        assert sent_status_frames == [
            "*#1*00##",
            "*#1*1##",
            "*#1*2##",
            "*#1*3##",
            "*#1*4##",
            "*#1*5##",
        ]

        # When MH200N responds to `*#1*00##` with point-to-point status `*1*0*04##`,
        # switch_04 updates to OFF:
        msg = _deliver_rx("*1*0*04##")
        await handler._process_message(msg)
        assert switch_04.is_on is False
