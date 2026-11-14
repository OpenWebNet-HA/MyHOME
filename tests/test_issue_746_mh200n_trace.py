"""Replay and verification tests for #746: BTicino MH200N General OFF scenario and switch 04 trace.

Fixture: tests/fixtures/traces/issue_746/myhome_trace_MH200N_all_2026-10-17T14-28-20.json
Contributed by @wave68runner on https://github.com/OpenWebNet-HA/MyHOME/issues/746
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant, callback
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


def _load_trace() -> dict:
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
async def test_issue_746_switch_04_wall_and_ha_toggling_and_scenario_6_events(
    hass: HomeAssistant,
) -> None:
    """Verify wall switch / HA toggling of switch 04 and CEN / Scenario 6 bus events from the trace."""
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

    cen_events: list = []
    scene_events: list = []
    general_light_events: list = []
    # ``@callback`` keeps the listeners on the event loop: a plain ``list.append`` runs in
    # the executor, where two events fired back to back can be appended out of order
    # (press / release arrived swapped on HA beta).
    def _collect(events: list):
        @callback
        def _append(event) -> None:
            events.append(event)

        return _append

    hass.bus.async_listen("myhome_cen_event", _collect(cen_events))
    hass.bus.async_listen("myhome_scene_event", _collect(scene_events))
    hass.bus.async_listen("myhome_general_light_event", _collect(general_light_events))

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

    # Frame 7..8: HA turns 04 ON (*1*1*04##)
    await switch_04.async_turn_on()
    assert str(handler.send.call_args[0][0]) == frames[7]["raw"] == "*1*1*04##"
    msg = _deliver_rx(frames[8]["raw"])
    await handler._process_message(msg)
    assert switch_04.is_on is True

    # Frame 9..10: wall switch turns 04 OFF (*1*1000#0*04## -> *1*0*04##)
    for frame_entry in frames[9:11]:
        msg = _deliver_rx(frame_entry["raw"])
        await handler._process_message(msg)
    assert switch_04.is_on is False

    # Frame 11..12: wall switch turns 04 ON (*1*1000#1*04## -> *1*1*04##)
    for frame_entry in frames[11:13]:
        msg = _deliver_rx(frame_entry["raw"])
        await handler._process_message(msg)
    assert switch_04.is_on is True

    # Frame 15..27: CEN button *15*01*81## -> Scenario 6 *17*1*6## -> General OFF *1*0*0## -> *17*2*6##
    for frame_entry in frames[15:28]:
        msg = _deliver_rx(frame_entry["raw"])
        await handler._process_message(msg)
    await hass.async_block_till_done()

    assert len(cen_events) == 2
    assert cen_events[0].data["object"] == 81
    assert cen_events[0].data["pushbutton"] == 1
    assert cen_events[0].data["event"] == "pushbutton_short_press"
    assert cen_events[1].data["event"] == "pushbutton_short_release"

    assert len(scene_events) == 2
    assert scene_events[0].data["scenario"] == 6
    assert scene_events[0].data["is_on"] is True
    assert scene_events[1].data["scenario"] == 6
    assert scene_events[1].data["is_on"] is False

    assert len(general_light_events) == 2
    assert general_light_events[0].data["event"] == "off"
