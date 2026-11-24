"""Replay and verification tests for Discussion #770: BTicino F454 + F401 shutter & slat-tilt traces.

Fixtures:
- tests/fixtures/traces/discussion_770_f454_f401/myhome_trace_F454_2026-10-23_anon.json
- tests/fixtures/traces/discussion_770_f454_f401/myhome_trace_F454_all_2026-10-23T11-11-26.json
Contributed by @eC0y0te on https://github.com/orgs/OpenWebNet-HA/discussions/770
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import ATTR_POSITION, ATTR_TILT_POSITION
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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


# README "Trace 2" table, steps 1-10: the tilt asked for by each pulse, in capture order.
TRACE2_TILT_TARGETS = [15, 0, 20, 0, 25, 0, 50, 0, 100, 0]
_REAL_SLEEP = asyncio.sleep


def _trace2_pulses() -> list[tuple[int, int]]:
    """(index of the pulse's direction tx, index of its last frame) for the ten tilt pulses."""
    frames = _load_json(TRACE2_PATH)["frames"]
    starts = [
        i for i, f in enumerate(frames) if f["direction"] == "tx" and f["raw"] in ("*2*1*21##", "*2*2*21##")
    ][1:]  # the first tx is the initial close sync, a curtain run
    ends = [*(i - 1 for i in starts[1:]), next(i for i, f in enumerate(frames) if f["raw"] == "*#2*21*#11#001*2##") - 1]
    return list(zip(starts, ends, strict=True))


async def _replay_trace2_pulse(
    cover: MyHOMECover,
    clock: SimpleNamespace,
    first: int,
    last: int,
    target: int,
    *,
    skip: tuple[str, ...] = (),
    offset: float = 0.0,
) -> None:
    """One HA tilt request on the capture's own clock: its rx frames at their offsets, its stop at the tx stop's.

    ``offset`` shifts every time stamp of the capture together (a replay after an earlier one).
    """
    frames = _load_json(TRACE2_PATH)["frames"]
    assert frames[first]["direction"] == "tx"
    release = asyncio.Event()

    async def _sleep(delay: float, *args: Any, **kwargs: Any) -> None:
        if delay > 0:
            await release.wait()
        await _REAL_SLEEP(0)

    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_sleep):
        clock.now = frames[first]["timestamp"] + offset
        await cover.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: target})
        for frame in frames[first + 1 : last + 1]:
            clock.now = frame["timestamp"] + offset
            if frame["direction"] == "tx":
                assert frame["raw"] == "*2*0*21##"
                release.set()  # the pulse's auto-stop writes its stop where the capture has it
                await asyncio.wait_for(cover._stop_task, 1.0)
                continue
            if frame["raw"] in skip:
                continue
            cover.handle_event(OWNMessage.parse(frame["raw"]))
            for _ in range(3):
                await _REAL_SLEEP(0)


Trace2Cover = tuple[MyHOMECover, SimpleNamespace, MagicMock]


@pytest.fixture
def trace2_cover() -> Iterator[Trace2Cover]:
    """Cover 21 as trace 2 had it (advanced F401, slat_time 1.4, min_motor_pulse 0.2), on a clock the test sets."""
    clock = SimpleNamespace(now=0.0)
    gateway = MagicMock()
    gateway.mac = gateway.unique_id = MAC
    gateway.log_id = "[F454]"
    gateway.sent = []

    gateway.hold_stop_write = False
    gateway.held = []

    async def _send(message: Any, *args: Any, **kwargs: Any) -> asyncio.Future[Any]:
        gateway.sent.append(str(message))
        written: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        if gateway.hold_stop_write and str(message) == "*2*0*21##":
            gateway.held.append(written)  # queued, its write not confirmed yet
        else:
            written.set_result(clock.now)
        return written

    gateway.send = AsyncMock(side_effect=_send)
    gateway.send_status_request = AsyncMock()
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=None, name="Shutter 21", entity_name=None, device_id="21", who="2", where="21", interface=None,
            advanced=True, manufacturer="BTicino", model="F401", gateway=gateway, travel_time=25,
            slat_tilt=True, slat_time=1.4, min_motor_pulse=0.2,
        )
    cover.entity_id = "cover.shutter_21"
    cover.async_schedule_update_ha_state = MagicMock()
    # After the capture's initial close sync (2 % -> 0 %): closed, slats closed.
    cover._attr_current_cover_position = 0
    cover._attr_current_cover_tilt_position = 0
    with patch("custom_components.myhome.cover.time", SimpleNamespace(monotonic=lambda: clock.now)):
        yield cover, clock, gateway


@pytest.mark.asyncio
async def test_discussion_770_trace2_tilt_pulses_keep_their_angle(trace2_cover: Trace2Cover) -> None:
    """Every tilt pulse of trace 2 ends at the angle asked for, whatever level the F401 books for it.

    The F401 counts the pulse as travel and reports `*#2*21*10*10*1*001*15##` (level 0 -> 1) or
    `...*10*10*0*...` (1 -> 0) 96-134 ms after the pulse's stop. That report was taken as an external
    level jump and turned the slats a full rotation: 15 %, 25 % and 50 % all ended at 100 %.
    """
    cover, clock, gateway = trace2_cover
    frames = _load_json(TRACE2_PATH)["frames"]
    pulses = _trace2_pulses()
    assert len(pulses) == len(TRACE2_TILT_TARGETS) == 10
    seen: list[tuple[int, int]] = []
    for (first, last), target in zip(pulses, TRACE2_TILT_TARGETS, strict=True):
        gateway.sent.clear()
        await _replay_trace2_pulse(cover, clock, first, last, target)
        assert gateway.sent == [frames[first]["raw"], "*2*0*21##"], (first, target)
        assert cover._is_tilting is False
        assert cover.is_opening is False and cover.is_closing is False
        seen.append((cover.current_cover_tilt_position, cover.current_cover_position))
    # (tilt, level the F401 reported): README table, columns "Tilt Command" and "Reported Dimension 10 Level".
    assert seen == [(15, 1), (0, 0), (20, 0), (0, 0), (25, 1), (0, 0), (50, 1), (0, 0), (100, 1), (0, 0)]


@pytest.mark.asyncio
async def test_discussion_770_level_change_after_a_pulse_is_external_outside_its_window(trace2_cover: Trace2Cover) -> None:
    """The pulse's own level is taken only right after it, and only in its direction (#868 stays as it was)."""
    cover, clock, _gateway = trace2_cover
    first, last = _trace2_pulses()[0]
    await _replay_trace2_pulse(cover, clock, first, last, 15)  # 0 -> 15 %, level 0 -> 1
    assert (cover.current_cover_tilt_position, cover.current_cover_position) == (15, 1)

    # The opposite way inside the window (the capture's own closed report, here without a pulse): a move down.
    clock.now += 0.2
    cover.handle_event(OWNMessage.parse("*#2*21*10*10*0*001*15##"))
    assert (cover.current_cover_tilt_position, cover.current_cover_position) == (0, 0)

    # The second pulse on the capture's frame offsets, but after the first one: the clock never goes back.
    frames = _load_json(TRACE2_PATH)["frames"]
    await _replay_trace2_pulse(cover, clock, first, last, 15, offset=clock.now - frames[first]["timestamp"])
    assert (cover.current_cover_tilt_position, cover.current_cover_position) == (15, 1)
    # The same way, but long after the pulse and with no motion frame (the capture's last report, 2 %):
    # somebody moved the curtain up, which turns the slats open.
    clock.now += 5.0
    cover.handle_event(OWNMessage.parse("*#2*21*10*10*2*001*15##"))
    assert (cover.current_cover_tilt_position, cover.current_cover_position) == (100, 2)


@pytest.mark.asyncio
async def test_discussion_770_level_report_before_the_stop_write_is_confirmed(trace2_cover: Trace2Cover) -> None:
    """The F401's report of the pulse's stop can beat the gateway's confirmation of that stop.

    Constructed from pulse 1 of trace 2: the stop's write stays unconfirmed and the command
    translation `*2*1000#0*21##` is left out (F453, F455, MH200, MH201 relay none in the
    corpus), so `*#2*21*10*10*1*001*15##` is the first frame after the stop. The pulse ends
    there: the motor ran 0.320 s (README step 1) of a 1.4 s rotation.
    """
    cover, clock, gateway = trace2_cover
    first, last = _trace2_pulses()[0]
    gateway.hold_stop_write = True
    await _replay_trace2_pulse(cover, clock, first, last, 15, skip=("*2*1000#0*21##",))
    assert cover._is_tilting is False
    assert (cover.current_cover_tilt_position, cover.current_cover_position) == (23, 1)
    (stop_written,) = gateway.held
    stop_written.set_result(clock.now)
    for _ in range(3):
        await _REAL_SLEEP(0)
    assert (cover.current_cover_tilt_position, cover.current_cover_position) == (23, 1)
    assert cover._pulse_stop_written is None


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


async def test_failed_stop_does_not_open_own_level_window(trace2_cover: Trace2Cover) -> None:
    c, clk, gw = trace2_cover
    level = "*#2*21*10*10*1*001*15##"  # the stopped level after trace 2's first pulse
    gw.hold_stop_write = True
    first, last = _trace2_pulses()[0]
    # Run up to and including TX stop; in this case no stop reaches the actuator.
    await _replay_trace2_pulse(c, clk, first, last - 3, 15)
    gw.held[0].set_exception(HomeAssistantError("stop was never delivered"))
    await _REAL_SLEEP(0)
    assert not c._is_tilting
    c.handle_event(OWNMessage.parse(level))
    assert (c.current_cover_position, c.current_cover_tilt_position) == (1, 100), (
        "failed stop was used as evidence that the new level came from our stop"
    )


async def test_old_level_must_not_cancel_new_auto_stop(trace2_cover: Trace2Cover) -> None:
    cover, clock, gw = trace2_cover
    first, last = _trace2_pulses()[0]
    # Hold just the stopped level from pulse 1 until the next tilt service call.
    level = "*#2*21*10*10*1*001*15##"  # the stopped level after trace 2's first pulse
    await _replay_trace2_pulse(cover, clock, first, last, 15, skip=(level,))
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (0, 15)
    await cover.async_set_cover_tilt_position(tilt_position=0)
    assert cover._is_tilting and cover._stop_task is not None
    cover.handle_event(OWNMessage.parse(level))
    try:
        assert cover.current_cover_position == 1
        assert cover._is_tilting and cover._stop_task is not None, (
            "late level of first tilt cancelled auto-stop of the second"
        )
    finally:
        cover._cancel_stop_task()


async def test_translation_and_level_before_confirm(trace2_cover: Trace2Cover) -> None:
    c, clk, gw = trace2_cover
    gw.hold_stop_write = True
    await _replay_trace2_pulse(c, clk, *_trace2_pulses()[0], 15)
    try:
        assert c.current_cover_position == 1
        assert 0 < c.current_cover_tilt_position < 100, (
            "translation closed pulse but following own level opened slats fully"
        )
    finally:
        for f in gw.held:
            f.set_result(clk.now)


# T: delivery of STOP alone is not evidence that an earlier direction moved the motor.
async def test_cancelled_direction_followed_by_successful_manual_stop(trace2_cover: Trace2Cover) -> None:
    cover, clock, gateway = trace2_cover
    clock.now = 1.0
    direction, stop = (asyncio.get_running_loop().create_future() for _ in range(2))
    with patch.object(gateway, "send", AsyncMock(side_effect=[direction, stop])) as send:
        await cover.async_set_cover_tilt_position(tilt_position=50)
        await cover.async_stop_cover_tilt()
        assert [str(call.args[0]) for call in send.await_args_list] == ["*2*1*21##", "*2*0*21##"]
        # Queue worker cancels the first write without delivery, then sends the stop.
        direction.cancel()
        stop.set_result(clock.now)
        for _ in range(4):
            await asyncio.sleep(0)
    assert not cover._is_tilting
    assert cover.current_cover_tilt_position == 0
    clock.now += 0.25
    cover.handle_event(OWNMessage.parse("*#2*21*10*10*1*001*15##"))
    assert cover.current_cover_position == 1
    assert cover.current_cover_tilt_position == 100, (
        "the direction did not reach the bus: no own movement to suppress"
    )


# T: a completed pulse must not claim the result of a later HA height command.
async def test_own_height_write_after_completed_pulse(trace2_cover: Trace2Cover) -> None:
    cover, clock, gw = trace2_cover
    # Actual third pulse (README, step 3): the F401 booked zero height change.
    await _replay_trace2_pulse(cover, clock, *_trace2_pulses()[2], 20)
    assert (cover.current_cover_tilt_position, cover.current_cover_position) == (20, 0)
    # Constructed ordering, verbatim height write/echo/report from the end of trace 2.
    # The client KNOWS a distinct curtain command was written after this pulse.
    clock.now += 0.1
    await cover.async_set_cover_position(position=2)
    assert gw.sent[-1] == "*#2*21*#11#001*2##"
    cover.handle_event(OWNMessage.parse("*#2*21*#11#001#1*2##"))
    clock.now += 0.9
    cover.handle_event(OWNMessage.parse("*#2*21*10*10*2*001*15##"))
    assert cover.current_cover_position == 2
    assert cover.current_cover_tilt_position == 100


async def test_own_absolute_position_write_supersedes_settled_pulse(trace2_cover: Trace2Cover) -> None:
    cover, clock, gw = trace2_cover
    a, b = _trace2_pulses()[0]
    await _replay_trace2_pulse(cover, clock, a, b, 15)
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (1, 15)
    # Trace 3 from this installation uses min_position_delta 1. Construct the adjacent
    # commands; keep actual step 2 TX->RX offsets, omit the movement reports.
    cover._strategy.min_position_delta = 1
    frames = _load_json(TRACE3_PATH)["frames"]
    i = next(
        i
        for i, f in enumerate(frames)
        if f["direction"] == "tx" and f["raw"] == "*#2*21*#11#001*2##"
    )
    clock.now += 0.01
    start = clock.now
    await cover.async_set_cover_position(position=2)
    assert gw.sent[-1] == frames[i]["raw"]
    for f in frames[i + 1 :]:
        if f["direction"] == "tx":
            break
        msg = OWNMessage.parse(f["raw"])
        if (
            getattr(msg, "current_position", None) == 2
            and not msg.is_opening
            and not msg.is_closing
        ):
            clock.now = start + f["timestamp"] - frames[i]["timestamp"]
            cover.handle_event(msg)
            break
    assert cover.current_cover_position == 2
    assert cover.current_cover_tilt_position == 100, (
        "native position write reached its new target; not another slat-pulse level"
    )


async def test_a_followed_pulse_keeps_the_earlier_pulse_claim_on_its_stopped_level(trace2_cover: Trace2Cover) -> None:
    """A blind that follows an area pulse right after its own pulse still takes that pulse's late level as its own."""
    cover, clock, _gateway = trace2_cover
    first, last = _trace2_pulses()[0]
    level = "*#2*21*10*10*1*001*15##"  # the stopped level after trace 2's first pulse
    await _replay_trace2_pulse(cover, clock, first, last, 15, skip=(level,))
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (0, 15)
    leader = MagicMock()
    cover._follow_pulse(leader, "close")
    cover.handle_event(OWNMessage.parse(level))
    assert cover.current_cover_position == 1
    assert cover._is_tilting and cover._tilt_follows is leader


async def test_a_followed_pulse_that_interrupts_a_running_pulse_keeps_its_claim(trace2_cover: Trace2Cover) -> None:
    """The interrupted pulse ends where the area pulse takes over, and its stopped level is still its own.

    A gateway that stops the actuator ahead of the area's direction frame makes it report the level the
    interrupted pulse left (here the capture's closed report, 1 -> 0, of a closing pulse) while the blind
    already follows the area pulse: it keeps following it.
    """
    cover, clock, _gateway = trace2_cover
    first, last = _trace2_pulses()[0]
    await _replay_trace2_pulse(cover, clock, first, last, 15)
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (1, 15)
    await cover.async_set_cover_tilt_position(tilt_position=0)
    assert cover._is_tilting
    leader = MagicMock()
    leader._pulse_stop_written = None
    cover._follow_pulse(leader, "open")
    assert cover._is_tilting and cover._tilt_follows is leader
    assert (cover._pulse_ended_at, cover._pulse_ended_direction) == (clock.now, "close")
    clock.now += 0.1
    cover.handle_event(OWNMessage.parse("*#2*21*10*10*0*001*15##"))
    assert cover.current_cover_position == 0
    assert cover._is_tilting and cover._tilt_follows is leader
    # The claim was for that one level: the next change under the area pulse is not the old pulse's.
    assert cover._pulse_ended_at is None
