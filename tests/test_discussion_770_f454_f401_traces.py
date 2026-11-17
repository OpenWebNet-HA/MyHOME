"""Replay and verification tests for Discussion #770: BTicino F454 + F401 shutter & slat-tilt traces.

Fixtures:
- tests/fixtures/traces/discussion_770_f454_f401/myhome_trace_F454_2026-10-23_anon.json
- tests/fixtures/traces/discussion_770_f454_f401/myhome_trace_F454_all_2026-10-23T11-11-26.json
Contributed by @eC0y0te on https://github.com/orgs/OpenWebNet-HA/discussions/770
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.cover import ATTR_POSITION, ATTR_TILT_POSITION
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from OWNd.message import OWNCommand, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import CONF_BROADCAST_RESYNC, DOMAIN
from custom_components.myhome.cover import MyHOMECover
from custom_components.myhome.cover import async_setup_entry as async_setup_cover
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.router import FrameRouter

FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "discussion_770_f454_f401"
)
TRACE1_PATH = FIXTURE_DIR / "myhome_trace_F454_2026-10-23_anon.json"
TRACE2_PATH = FIXTURE_DIR / "myhome_trace_F454_all_2026-10-23T11-11-26.json"
TRACE3_PATH = FIXTURE_DIR / "myhome_trace_F454_all_2026-10-23T13-13-34.json"
MAC = "00:03:50:aa:bb:21"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_discussion_770_trace1_schema_and_frame_parsing() -> None:
    """Verify Trace 1 (H4660M2 keypad, 5%/2% steps, full run & mid-stop) schema and parsing."""
    assert TRACE1_PATH.is_file()
    data = _load_json(TRACE1_PATH)

    assert data["gateway"]["model"] == "F454"
    assert data["gateway"]["firmware"] == "2.0.51"
    assert data["gateway"]["mac_prefix"] == "00:03:50"
    assert data["gateway"]["identification"]["profile"] == "F454Profile"
    assert data["environment"]["integration_version"] == "2.0.0b16"
    assert data["environment"]["ownd_version"] == "2.0.0b11"
    assert data["capture"]["window"]["frames"] == 220

    frames = data["frames"]
    assert len(frames) == 220
    assert sum(1 for f in frames if f["direction"] == "rx") == 186
    assert sum(1 for f in frames if f["direction"] == "tx") == 34

    whos_seen: set[str] = set()
    dim10_stopped_positions: list[int] = []
    keypad_translations: list[str] = []

    for entry in frames:
        raw = entry["raw"]
        direction = entry["direction"]
        parsed = OWNCommand.parse(raw) if direction == "tx" else OWNMessage.parse(raw)
        assert parsed is not None, f"Failed to parse {direction} frame: {raw}"
        whos_seen.add(str(entry["who"]))

        if direction == "rx" and entry["who"] == "2":
            if getattr(parsed, "is_translation", False) and raw.startswith(
                ("*2*1000#10#", "*2*1000#11#", "*2*1000#12#")
            ):
                keypad_translations.append(raw)
            if getattr(parsed, "dimension", None) == 10 and getattr(parsed, "state", None) == 10:
                dim10_stopped_positions.append(int(parsed.current_position))

    assert whos_seen == {"1", "2"}
    assert keypad_translations == [
        "*2*1000#11#100#001#1*21##",
        "*2*1000#10#001#1*21##",
        "*2*1000#11#100#001#1*21##",
        "*2*1000#10#001#1*21##",
        "*2*1000#11#100#001#1*21##",
        "*2*1000#10#001#1*21##",
    ]
    assert dim10_stopped_positions == [
        # 3 keypad pulses at 2% then close to 0% (leading 2 -> 0)
        2, 2, 2, 2, 0,
        # 5% steps up and down (0 -> 5 -> 10 -> 15 -> 20 -> 15 -> 10 -> 5 -> leading 5 -> 0)
        5, 10, 15, 20, 15, 10, 5, 5, 0,
        # 2% steps up and down (0 -> 2..20 -> 18..2 -> leading 2 -> 0)
        2, 4, 6, 8, 10, 15, 20, 18, 16, 14, 12, 10, 8, 6, 4, 2, 2, 0,
        # Full open (leading 0 -> 100), mid-stop (leading 100 -> 47), close to bottom (leading 47 -> 0)
        0, 100, 100, 47, 47, 0,
    ]

    # Between full-open motor start (frame 196, 10:29:31.592) and top limit stop (frame 198, 10:31:02.874),
    # no intermediate WHO=2 frames are emitted by the F401 during the 91.28 s run.
    assert frames[196]["raw"] == "*#2*21*10*11*0*001*15##"
    assert frames[197]["raw"] == "*2*1*21##"
    assert frames[198]["raw"] == "*#2*21*10*10*100*001*15##"


@pytest.mark.asyncio
async def test_discussion_770_trace1_cover_21_replay(hass: HomeAssistant) -> None:
    """Replay Trace 1 through MyHOMECover (advanced F401 on WHERE='21')."""
    trace = _load_json(TRACE1_PATH)
    frames = trace["frames"]

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.40",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: MAC,
            CONF_NAME: "F454",
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
            "cover": {
                "vr_bureau_terrasse": {
                    "where": "21",
                    "name": "Shutter 21",
                    "advanced_shutter": True,
                    "manufacturer": "BTicino",
                    "model": "F401",
                }
            }
        },
    )

    added_covers: list[MyHOMECover] = []
    await async_setup_cover(
        hass, entry, lambda entities, update=False: added_covers.extend(entities)
    )
    assert len(added_covers) == 1
    cover = added_covers[0]
    cover.hass = hass
    cover.async_write_ha_state = lambda: None
    cover.async_schedule_update_ha_state = lambda force_refresh=False: None
    await cover.async_added_to_hass()

    def _deliver_rx(raw_frame: str) -> None:
        msg = OWNMessage.parse(raw_frame)
        assert msg is not None
        if msg.who == 2 and not getattr(msg, "is_translation", False):
            where = getattr(msg, "where", None)
            if where:
                router.publish("2", (str(where),), msg)

    # 1. H4660M2 3 short opening pulses (frames 0..19): all three settle at 2%
    for idx in range(20):
        if frames[idx]["direction"] == "rx":
            _deliver_rx(frames[idx]["raw"])
    assert cover.current_cover_position == 2
    assert cover.is_closed is False

    # Close back to 0% (frames 20..28)
    for idx in range(20, 29):
        if frames[idx]["direction"] == "rx":
            _deliver_rx(frames[idx]["raw"])
    assert cover.current_cover_position == 0
    assert cover.is_closed is True

    # 2. Replay 5% steps (frames 29..81)
    for idx in range(29, 82):
        if frames[idx]["direction"] == "rx":
            _deliver_rx(frames[idx]["raw"])
    assert cover.current_cover_position == 0
    assert cover.is_closed is True

    # 3. Replay 2% step sequence (frames 82..188):
    # Verify HA set_cover_position emits exact TX frames and F454 #11#001#1*LEVEL echo does not jump position early
    expected_2pct_steps = [2, 4, 6, 8, 10, 15, 20, 18, 16, 14, 12, 10, 8, 6, 4, 2, 0]
    tx_indices = [
        idx
        for idx in range(82, 189)
        if frames[idx]["direction"] == "tx" and frames[idx]["who"] == "2"
    ]
    assert len(tx_indices) == len(expected_2pct_steps)

    cursor = 82
    for step_idx, target_pos in enumerate(expected_2pct_steps):
        tx_i = tx_indices[step_idx]
        while cursor < tx_i:
            if frames[cursor]["direction"] == "rx":
                _deliver_rx(frames[cursor]["raw"])
            cursor += 1

        prev_pos = cover.current_cover_position
        await cover.async_set_cover_position(**{ATTR_POSITION: target_pos})
        emitted = str(handler.send.call_args[0][0])
        assert emitted == frames[tx_i]["raw"]
        cursor = tx_i + 1

        # Deliver the immediate F454 #11#001#1*<LEVEL> echo if present; position must remain at prev_pos
        if frames[cursor]["raw"].startswith("*#2*21*#11#001#1*"):
            _deliver_rx(frames[cursor]["raw"])
            assert cover.current_cover_position == prev_pos
            cursor += 1

        # Deliver remaining RX frames until next TX (or end of block 189)
        next_stop = tx_indices[step_idx + 1] if step_idx + 1 < len(tx_indices) else 189
        while cursor < next_stop:
            if frames[cursor]["direction"] == "rx":
                _deliver_rx(frames[cursor]["raw"])
            cursor += 1
        assert cover.current_cover_position == target_pos

    # 4. Full open (0% -> 100%), mid-run stop at 47%, and close to 0% (frames 191..219)
    await cover.async_open_cover()
    assert str(handler.send.call_args[0][0]) == frames[191]["raw"] == "*2*1*21##"
    for idx in range(192, 198):
        _deliver_rx(frames[idx]["raw"])
    assert cover.is_opening is True
    assert cover.current_cover_position == 0

    for idx in range(198, 200):
        _deliver_rx(frames[idx]["raw"])
    assert cover.is_opening is False
    assert cover.current_cover_position == 100

    # Start closing from 100% (frame 200) and stop mid-run at 47% (frame 207)
    await cover.async_close_cover()
    assert str(handler.send.call_args[0][0]) == frames[200]["raw"] == "*2*2*21##"
    for idx in range(201, 207):
        _deliver_rx(frames[idx]["raw"])
    assert cover.is_closing is True
    assert cover.current_cover_position == 100

    await cover.async_stop_cover()
    assert str(handler.send.call_args[0][0]) == frames[207]["raw"] == "*2*0*21##"
    for idx in range(208, 211):
        _deliver_rx(frames[idx]["raw"])
    assert cover.is_closing is False
    assert cover.current_cover_position == 47

    # Final close from 47% to 0% (frames 211..219)
    await cover.async_close_cover()
    assert str(handler.send.call_args[0][0]) == frames[211]["raw"] == "*2*2*21##"
    for idx in range(212, 220):
        _deliver_rx(frames[idx]["raw"])
    assert cover.is_closing is False
    assert cover.is_closed is True
    assert cover.current_cover_position == 0


@pytest.mark.asyncio
async def test_discussion_770_trace2_slat_tilt_schema_and_replay(
    hass: HomeAssistant,
) -> None:
    """Verify Trace 2 (slat_tilt=True, slat_time=1.4, min_motor_pulse=0.2) schema and replay."""
    assert TRACE2_PATH.is_file()
    data = _load_json(TRACE2_PATH)

    assert data["gateway"]["model"] == "F454"
    assert data["gateway"]["firmware"] == "2.0.51"
    assert data["capture"]["window"]["frames"] == 125

    frames = data["frames"]
    assert len(frames) == 125
    assert sum(1 for f in frames if f["direction"] == "rx") == 103
    assert sum(1 for f in frames if f["direction"] == "tx") == 22

    tx_frames: list[str] = []
    dim10_stopped_levels: list[int] = []

    for entry in frames:
        raw = entry["raw"]
        direction = entry["direction"]
        parsed = OWNCommand.parse(raw) if direction == "tx" else OWNMessage.parse(raw)
        assert parsed is not None, f"Failed to parse {direction} frame: {raw}"
        if direction == "tx":
            tx_frames.append(raw)
        elif (
            entry["who"] == "2"
            and getattr(parsed, "dimension", None) == 10
            and getattr(parsed, "state", None) == 10
        ):
            dim10_stopped_levels.append(int(parsed.current_position))

    # Initial close (*2*2*21##), 5 open tilt pulses (*2*1*21## + *2*0*21##),
    # 5 close tilt pulses (*2*2*21## + *2*0*21##), and 1 final set_cover_position: 2 (*#2*21*#11#001*2##)
    assert tx_frames == [
        "*2*2*21##",                # initial close sync
        "*2*1*21##", "*2*0*21##",  # 0 -> 15%
        "*2*2*21##", "*2*0*21##",  # 15 -> 0%
        "*2*1*21##", "*2*0*21##",  # 0 -> 20%
        "*2*2*21##", "*2*0*21##",  # 20 -> 0%
        "*2*1*21##", "*2*0*21##",  # 0 -> 25%
        "*2*2*21##", "*2*0*21##",  # 25 -> 0%
        "*2*1*21##", "*2*0*21##",  # 0 -> 50%
        "*2*2*21##", "*2*0*21##",  # 50 -> 0%
        "*2*1*21##", "*2*0*21##",  # 0 -> 100%
        "*2*2*21##", "*2*0*21##",  # 100 -> 0%
        "*#2*21*#11#001*2##",      # set_cover_position: 2
    ]
    # After initial close sync from 2% -> 0%, across all 10 tilt pulses F401 Dimension 10 position stayed in {0, 1},
    # then moved to 2 on set_cover_position: 2
    assert dim10_stopped_levels[0] == 2
    assert set(dim10_stopped_levels[1:-1]) <= {0, 1}
    assert dim10_stopped_levels[-1] == 2

    # Set up cover 21 with slat_tilt=True, slat_time=1.4, min_motor_pulse=0.2
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.40",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: MAC,
            CONF_NAME: "F454",
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
            "cover": {
                "vr_bureau_terrasse": {
                    "where": "21",
                    "name": "Shutter 21",
                    "advanced_shutter": True,
                    "slat_tilt": True,
                    "slat_time": 1.4,
                    "min_motor_pulse": 0.2,
                }
            }
        },
    )

    added_covers: list[MyHOMECover] = []
    await async_setup_cover(
        hass, entry, lambda entities, update=False: added_covers.extend(entities)
    )
    assert len(added_covers) == 1
    cover = added_covers[0]
    cover.hass = hass
    cover.async_write_ha_state = lambda: None
    cover.async_schedule_update_ha_state = lambda force_refresh=False: None
    await cover.async_added_to_hass()

    # Simulate tilt at 15% and verify 15% -> 20% (5% of 1.4s = 0.07s < 0.2s min_motor_pulse) is skipped
    cover._attr_current_cover_tilt_position = 15
    await cover.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 20})
    handler.send.assert_not_called()
    assert cover.current_cover_tilt_position == 15

    # Replay all RX frames from Trace 2 and verify cover ends at 2% position
    for frame_entry in frames:
        if frame_entry["direction"] == "rx":
            msg = OWNMessage.parse(frame_entry["raw"])
            assert msg is not None
            if msg.who == 2 and not getattr(msg, "is_translation", False):
                where = getattr(msg, "where", None)
                if where:
                    router.publish("2", (str(where),), msg)

    assert cover.current_cover_position == 2


def test_discussion_770_trace3_schema_and_frame_parsing() -> None:
    """Verify Trace 3 (min_position_delta=1, 1% step progression 1%..10%) schema and parsing."""
    assert TRACE3_PATH.is_file()
    data = _load_json(TRACE3_PATH)

    assert data["gateway"]["model"] == "F454"
    assert data["gateway"]["firmware"] in ("2.0", "2.0.51")
    assert data["capture"]["window"]["frames"] == 69

    frames = data["frames"]
    assert len(frames) == 69
    assert sum(1 for f in frames if f["direction"] == "rx") == 58
    assert sum(1 for f in frames if f["direction"] == "tx") == 11

    tx_frames: list[str] = []
    dim10_stopped_levels: list[int] = []

    for entry in frames:
        raw = entry["raw"]
        direction = entry["direction"]
        parsed = OWNCommand.parse(raw) if direction == "tx" else OWNMessage.parse(raw)
        assert parsed is not None, f"Failed to parse {direction} frame: {raw}"
        if direction == "tx":
            tx_frames.append(raw)
        elif (
            entry["who"] == "2"
            and getattr(parsed, "dimension", None) == 10
            and getattr(parsed, "state", None) == 10
        ):
            dim10_stopped_levels.append(int(parsed.current_position))

    # Initial close sync (*2*2*21##) + 10 consecutive 1% steps (*#2*21*#11#001*<LEVEL>##)
    assert tx_frames == [
        "*2*2*21##",
        "*#2*21*#11#001*1##",
        "*#2*21*#11#001*2##",
        "*#2*21*#11#001*3##",
        "*#2*21*#11#001*4##",
        "*#2*21*#11#001*5##",
        "*#2*21*#11#001*6##",
        "*#2*21*#11#001*7##",
        "*#2*21*#11#001*8##",
        "*#2*21*#11#001*9##",
        "*#2*21*#11#001*10##",
    ]
    # Stopped levels report [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    # Initial position before close was 2%, then closes to 0%, followed by 1%..10% steps
    assert dim10_stopped_levels == [2] + list(range(11))


@pytest.mark.asyncio
async def test_discussion_770_trace3_cover_21_replay(hass: HomeAssistant) -> None:
    """Replay Trace 3 step-by-step with min_position_delta=1 through MyHOMECover."""
    trace = _load_json(TRACE3_PATH)
    frames = trace["frames"]

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.40",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: MAC,
            CONF_NAME: "F454",
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
            "cover": {
                "vr_bureau_terrasse": {
                    "where": "21",
                    "name": "Shutter 21",
                    "advanced_shutter": True,
                    "min_position_delta": 1,
                }
            }
        },
    )

    added_covers: list[MyHOMECover] = []
    await async_setup_cover(
        hass, entry, lambda entities, update=False: added_covers.extend(entities)
    )
    assert len(added_covers) == 1
    cover = added_covers[0]
    cover.hass = hass
    cover.async_write_ha_state = lambda: None
    cover.async_schedule_update_ha_state = lambda force_refresh=False: None
    if hasattr(cover, "_strategy") and hasattr(cover._strategy, "min_position_delta"):
        cover._strategy.min_position_delta = 1
    await cover.async_added_to_hass()

    def _deliver_rx(raw_frame: str) -> None:
        msg = OWNMessage.parse(raw_frame)
        assert msg is not None
        if msg.who == 2 and not getattr(msg, "is_translation", False):
            where = getattr(msg, "where", None)
            if where:
                router.publish("2", (str(where),), msg)

    # Deliver initial close frames (0..9)
    for idx in range(10):
        if frames[idx]["direction"] == "rx":
            _deliver_rx(frames[idx]["raw"])
    assert cover.current_cover_position == 0
    assert cover.is_closed is True

    # Step through 1% to 10%
    tx_indices = [idx for idx, f in enumerate(frames) if f["direction"] == "tx"]
    # tx_indices[0] is initial close at frame 0
    step_tx_indices = tx_indices[1:]
    assert len(step_tx_indices) == 10

    cursor = 10
    for step_num, tx_i in enumerate(step_tx_indices, start=1):
        while cursor < tx_i:
            if frames[cursor]["direction"] == "rx":
                _deliver_rx(frames[cursor]["raw"])
            cursor += 1

        prev_pos = cover.current_cover_position
        await cover.async_set_cover_position(**{ATTR_POSITION: step_num})
        emitted = str(handler.send.call_args[0][0])
        assert emitted == frames[tx_i]["raw"]
        cursor = tx_i + 1

        # Check immediate F454 echo
        if frames[cursor]["raw"].startswith("*#2*21*#11#001#1*"):
            _deliver_rx(frames[cursor]["raw"])
            assert cover.current_cover_position == prev_pos
            cursor += 1

        # Deliver remaining RX frames up to next step or end
        next_boundary = step_tx_indices[step_num] if step_num < len(step_tx_indices) else len(frames)
        while cursor < next_boundary:
            if frames[cursor]["direction"] == "rx":
                _deliver_rx(frames[cursor]["raw"])
            cursor += 1

        assert cover.current_cover_position == step_num
