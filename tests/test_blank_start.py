"""End-to-end replay test suite for Issue #578: Blank Start Discovery & Suppressing Polling Avalanche.

Validates that when Home Assistant starts with a blank entity registry and no myhome.yaml:
1. Replaying the real MH201 failure trace against the old path proves that un-scoped polling
   avalanche queued point-to-point status requests (*#1*<where>##, *#2*<where>##) and lost the
   5 addresses (02, 04, 06, 09, 0013) during the initial burst.
2. Replaying the trace against the new scoped path suppresses poll-on-add for status replies,
   queues zero point-to-point requests during the sweep, and discovers all 24 lights and 7 covers.
3. Restored registry entities retain _poll_on_add = True but hold their polls until initial
   discovery completes.
4. Bus entities revealed by incomplete state frames (e.g. moving covers without position)
   retain _poll_on_add = True and defer their polls until initial discovery completes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from tests.mock_gateway_harness import MockGatewayHarness

FIXTURES_TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces"
TRACE_FILE = FIXTURES_TRACES_DIR / "mh201-fresh-discovery-20260930.log"


@pytest.fixture(autouse=True)
def short_bus_quiet(monkeypatch):
    """The mock gateway answers instantly; do not wait out the real quiet period."""
    monkeypatch.setattr("custom_components.myhome.gateway.BUS_QUIET_PERIOD", 0.02)


def extract_received_frames(log_path: Path, start_line: int = 0, end_line: int | None = None) -> list[str]:
    """Extract raw received OpenWebNet frames from the MH201 debug log."""
    frames: list[str] = []
    prefix = "Message received: `"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    if end_line is not None:
        lines = lines[start_line:end_line]
    else:
        lines = lines[start_line:]
    for line in lines:
        if prefix in line:
            start = line.index(prefix) + len(prefix)
            end = line.index("`", start)
            frame = line[start:end].strip()
            if frame:
                frames.append(frame)
    return frames


@pytest.mark.asyncio
async def test_old_path_queues_p2p_and_loses_five_addresses(hass: HomeAssistant) -> None:
    """Validate that the old un-scoped poll-on-add path queues p2p queries and misses 5 lights (#578)."""
    assert TRACE_FILE.is_file(), f"Trace file {TRACE_FILE} missing"

    # Lines 1-168 contain the first burst where the gateway throttles under p2p flood
    burst1_frames = extract_received_frames(TRACE_FILE, start_line=0, end_line=168)
    assert len(burst1_frames) > 0

    harness = MockGatewayHarness()
    port = await harness.start()

    mac = "00:03:50:00:00:01"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: port,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "MH201",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:IP scenario module:1",
            CONF_FRIENDLY_NAME: "MH201 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "3.6.27",
        },
        options={},
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        handler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)
        # Mark initial discovery done so any poll-on-add requests are queued immediately
        handler._initial_discovery_done.set()

        # Simulate OLD behaviour where message_has_state was not present and _poll_on_add stayed True
        with patch("custom_components.myhome.discovery.message_has_state", return_value=False):
            for raw in burst1_frames:
                msg = OWNMessage.parse(raw)
                if msg is not None:
                    async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            await hass.async_block_till_done()

        # Collect all queued point-to-point queries
        queued_p2p: list[str] = []
        while not handler.send_buffer.empty():
            item = handler.send_buffer.get_nowait()
            if item and item.get("message"):
                msg_str = str(item["message"])
                if (msg_str.startswith("*#1*") and msg_str != "*#1*0##") or (
                    msg_str.startswith("*#2*") and msg_str != "*#2*0##"
                ):
                    queued_p2p.append(msg_str)

        # Assert that the old path queued the avalanche of point-to-point queries
        expected_queued = [
            "*#1*16##",
            "*#2*0112##",
            "*#2*0111##",
            "*#1*15##",
            "*#1*22##",
            "*#1*0015##",
            "*#1*05##",
            "*#2*0115##",
            "*#1*10##",
            "*#1*08##",
            "*#1*07##",
        ]
        for expected in expected_queued:
            assert expected in queued_p2p, f"Expected {expected} to be queued in old path"

        # Assert that the 5 missing lights were NOT discovered in burst 1
        missing_lights = ["light.light_02", "light.light_04", "light.light_06", "light.light_09", "light.light_0013"]
        for entity_id in missing_lights:
            assert hass.states.get(entity_id) is None, f"{entity_id} unexpectedly present after burst 1"

        # Now replay the 5 individual point-to-point status replies (lines 169-233)
        p2p_replies = extract_received_frames(TRACE_FILE, start_line=168, end_line=233)
        for raw in p2p_replies:
            msg = OWNMessage.parse(raw)
            if msg is not None:
                async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        await hass.async_block_till_done()

        # Assert that all 5 missing lights are now discovered
        for entity_id in missing_lights:
            assert hass.states.get(entity_id) is not None, f"{entity_id} was not discovered after point-to-point reply"
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_new_path_trace_replay_suppresses_polling_and_discovers_all(hass: HomeAssistant) -> None:
    """Validate that the new scoped path suppresses polling and discovers all 24 lights + 7 covers (#578)."""
    assert TRACE_FILE.is_file(), f"Trace file {TRACE_FILE} missing"

    all_received_frames = extract_received_frames(TRACE_FILE)
    assert len(all_received_frames) == 115

    harness = MockGatewayHarness()
    port = await harness.start()

    mac = "00:03:50:00:00:01"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: port,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "MH201",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:IP scenario module:1",
            CONF_FRIENDLY_NAME: "MH201 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "3.6.27",
        },
        options={},
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    # Empty entity registry verification before setup
    registry = er.async_get(hass)
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 0

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        handler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)

        # Run startup initial discovery
        await handler.initial_discovery()
        await hass.async_block_till_done()

        # Wait for the status requests to reach harness
        for _ in range(50):
            if "*#1*0##" in harness.received_messages and "*#2*0##" in harness.received_messages:
                break
            await asyncio.sleep(0.05)

        assert "*#1*0##" in harness.received_messages, "WHO 1 general query *#1*0## was not sent"
        assert "*#2*0##" in harness.received_messages, "WHO 2 general query *#2*0## was not sent"

        # Replay the full trace (burst 1, p2p replies, burst 2)
        for raw in all_received_frames:
            msg = OWNMessage.parse(raw)
            if msg is not None:
                async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        await hass.async_block_till_done()

        # Assert zero point-to-point status requests were queued or sent during discovery
        p2p_sent = [
            m for m in harness.received_messages
            if (m.startswith("*#1*") and m != "*#1*0##") or (m.startswith("*#2*") and m != "*#2*0##")
        ]
        assert p2p_sent == [], f"Unexpected point-to-point requests sent to gateway: {p2p_sent}"

        queued_p2p: list[str] = []
        while not handler.send_buffer.empty():
            item = handler.send_buffer.get_nowait()
            if item and item.get("message"):
                msg_str = str(item["message"])
                if (msg_str.startswith("*#1*") and msg_str != "*#1*0##") or (
                    msg_str.startswith("*#2*") and msg_str != "*#2*0##"
                ):
                    queued_p2p.append(msg_str)
        assert queued_p2p == [], f"Unexpected point-to-point requests queued: {queued_p2p}"

        # Assert all 24 lights are registered in Home Assistant
        light_states = [s for s in hass.states.async_all() if s.entity_id.startswith("light.")]
        assert len(light_states) == 24, f"Expected 24 lights, found {len(light_states)}"

        # Assert the 5 previously lost addresses are all discovered
        missing_lights = ["light.light_02", "light.light_04", "light.light_06", "light.light_09", "light.light_0013"]
        for entity_id in missing_lights:
            assert hass.states.get(entity_id) is not None, f"Expected {entity_id} to be discovered"

        # Assert state accuracy: lights 21 & 22 ON, remaining 22 lights OFF
        light_21 = hass.states.get("light.light_21")
        assert light_21 is not None and light_21.state == "on"

        light_22 = hass.states.get("light.light_22")
        assert light_22 is not None and light_22.state == "on"

        off_lights = [s for s in light_states if s.entity_id not in ("light.light_21", "light.light_22")]
        assert len(off_lights) == 22
        for s in off_lights:
            assert s.state == "off", f"Light {s.entity_id} expected off, but state is {s.state}"

        # Assert all 7 covers are registered in Home Assistant
        cover_states = [s for s in hass.states.async_all() if s.entity_id.startswith("cover.")]
        assert len(cover_states) == 7, f"Expected 7 covers, found {len(cover_states)}"

        expected_cover_positions = {
            "cover.cover_19": 50,
            "cover.cover_0110": 60,
            "cover.cover_0111": 50,
            "cover.cover_0112": 0,
            "cover.cover_0113": 0,
            "cover.cover_0114": 0,
            "cover.cover_0115": 0,
        }
        for entity_id, expected_pos in expected_cover_positions.items():
            cover = hass.states.get(entity_id)
            assert cover is not None, f"Cover {entity_id} missing"
            assert cover.attributes.get("current_position") == expected_pos, (
                f"{entity_id} position {cover.attributes.get('current_position')} != {expected_pos}"
            )
            expected_state = "closed" if expected_pos == 0 else "open"
            assert cover.state == expected_state, f"{entity_id} state {cover.state} != {expected_state}"
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_restored_registry_entities_hold_polls_until_sweep_completes(hass: HomeAssistant) -> None:
    """Validate that restored registry entities hold their polls until initial discovery finishes (#578)."""
    harness = MockGatewayHarness()
    port = await harness.start()

    mac = "00:03:50:00:00:01"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: port,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "MH201",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:IP scenario module:1",
            CONF_FRIENDLY_NAME: "MH201 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "3.6.27",
        },
        options={},
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    # Pre-register light_16 in entity registry (simulating upgraded system / restart)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        domain="light",
        platform=DOMAIN,
        unique_id=f"{mac}-1-16",
        config_entry=entry,
        suggested_object_id="light_16",
    )

    sweep_proceed = asyncio.Event()
    real_initial_discovery = MyHOMEGatewayHandler.initial_discovery

    async def controlled_initial_discovery(self):
        await sweep_proceed.wait()
        return await real_initial_discovery(self)

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
            patch.object(MyHOMEGatewayHandler, "initial_discovery", controlled_initial_discovery),
        ):
            setup_task = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
            for _ in range(50):
                if hass.data.get(DOMAIN, {}).get(mac) and hass.states.get("light.light_16"):
                    break
                await asyncio.sleep(0.05)

            handler = hass.data[DOMAIN][mac][CONF_ENTITY]
            handler._on_event_connection_state_change(True)

            # Restored light has _poll_on_add = True, but initial discovery is paused
            assert not handler._initial_discovery_done.is_set()

            # Check send_buffer and harness: no point-to-point status request for light 16 should be sent or queued yet
            queued_or_sent = [m for m in harness.received_messages if m.startswith("*#")] + [
                str(item["message"]) for item in list(handler.send_buffer._queue)
            ]
            assert "*#1*16##" not in queued_or_sent, f"Restored light polled before initial discovery: {queued_or_sent}"

            # Now allow initial discovery to proceed
            sweep_proceed.set()
            await setup_task
            # The sweep is paced (write + quiet bus per request), so it outlives setup.
            await handler.wait_for_initial_discovery(timeout=10.0)
            await asyncio.sleep(0.5)  # let the deferred poll queue its request
            await hass.async_block_till_done()

            # Now discovery has completed and the deferred poll has run
            assert handler._initial_discovery_done.is_set()
            all_processed = [m for m in harness.received_messages if m.startswith("*#")] + [
                str(item["message"]) for item in list(handler.send_buffer._queue)
            ]
            assert all_processed == ["*#1*0##", "*#2*0##", "*#4*0##", "*#16*0*5##", "*#1*16##"]
    finally:
        sweep_proceed.set()
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_incomplete_state_bus_entity_retains_poll_and_defers(hass: HomeAssistant) -> None:
    """Validate that bus entities revealed by incomplete frames retain poll and defer until sweep completes (#578)."""
    harness = MockGatewayHarness()
    port = await harness.start()

    mac = "00:03:50:00:00:01"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: port,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "MH201",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:IP scenario module:1",
            CONF_FRIENDLY_NAME: "MH201 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "3.6.27",
        },
        options={},
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    sweep_proceed = asyncio.Event()
    real_initial_discovery = MyHOMEGatewayHandler.initial_discovery

    async def controlled_initial_discovery(self):
        await sweep_proceed.wait()
        return await real_initial_discovery(self)

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
            patch.object(MyHOMEGatewayHandler, "initial_discovery", controlled_initial_discovery),
        ):
            setup_task = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
            for _ in range(50):
                if hass.data.get(DOMAIN, {}).get(mac):
                    break
                await asyncio.sleep(0.05)

            handler = hass.data[DOMAIN][mac][CONF_ENTITY]
            handler._on_event_connection_state_change(True)

            # Bus message: moving cover without position (*2*1*19##)
            moving_msg = OWNMessage.parse("*2*1*19##")
            assert moving_msg is not None
            async_dispatcher_send(hass, f"myhome_message_{mac}", moving_msg)

            for _ in range(50):
                if hass.states.get("cover.cover_19"):
                    break
                await asyncio.sleep(0.05)

            # Cover entity is created, but discovery is still in progress
            assert not handler._initial_discovery_done.is_set()
            queued_or_sent = [m for m in harness.received_messages if m.startswith("*#")] + [
                str(item["message"]) for item in list(handler.send_buffer._queue)
            ]
            assert "*#2*19##" not in queued_or_sent, f"Incomplete cover polled before initial discovery: {queued_or_sent}"

            # Now allow initial discovery to proceed
            sweep_proceed.set()
            await setup_task
            # The sweep is a background task and waits for bus quiet after every request.
            await handler.wait_for_initial_discovery(timeout=5.0)
            assert handler._initial_discovery_done.is_set()

            # Now discovery has completed and the deferred poll runs
            def processed() -> list[str]:
                return [m for m in harness.received_messages if m.startswith("*#")] + [
                    str(item["message"]) for item in list(handler.send_buffer._queue)
                ]

            for _ in range(50):
                if "*#2*19##" in processed():
                    break
                await asyncio.sleep(0.05)
            assert processed() == ["*#1*0##", "*#2*0##", "*#4*0##", "*#16*0*5##", "*#2*19##"]
    finally:
        sweep_proceed.set()
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()
