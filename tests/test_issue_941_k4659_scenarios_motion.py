"""Trace verification and regression tests for #941: BTicino F461 (2.0.11) + K4659 in Scenarios mode.

Fixture: tests/fixtures/traces/issue_941/f461_k4659_scenarios_where1001.json
Contributed by @lyubomirtraykov on https://github.com/OpenWebNet-HA/MyHOME/issues/941.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import STATE_ON
from homeassistant.util import dt as dt_util
from OWNd.message import (
    MESSAGE_TYPE_MOTION,
    MESSAGE_TYPE_MOTION_TIMEOUT,
    MESSAGE_TYPE_PIR_SENSITIVITY,
    OWNCommand,
    OWNEvent,
    OWNLightingCommand,
    OWNLightingEvent,
    OWNMessage,
)
from pytest_homeassistant_custom_component.common import async_fire_time_changed_exact

from custom_components.myhome.binary_sensor import (
    MOTION_TIMEOUT_GRACE,
    MyHOMEMotionSensor,
)
from custom_components.myhome.const import DOMAIN

TRACE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_941"
    / "f461_k4659_scenarios_where1001.json"
)
MAC = "00:03:50:aa:94:10"


def _load_trace() -> dict:
    return json.loads(TRACE_PATH.read_text(encoding="utf-8"))


def _make_motion_sensor(hass, *, where: str = "1001", inverted: bool = False) -> MyHOMEMotionSensor:
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.log_id = "[F461]"
    gateway.device_registry_id = None
    gateway.send_status_request = AsyncMock()
    hass.data.setdefault(DOMAIN, {}).setdefault(
        MAC,
        {"platforms": {"binary_sensor": {where: {"entities": {}}}}},
    )
    sensor = MyHOMEMotionSensor(
        hass=hass,
        name=f"Motion Sensor {where}",
        entity_name="Motion",
        device_id=where,
        who="1",
        where=where,
        inverted=inverted,
        device_class=BinarySensorDeviceClass.MOTION,
        manufacturer="BTicino",
        model="K4659",
        gateway=gateway,
    )
    sensor.async_write_ha_state = MagicMock()
    sensor.async_schedule_update_ha_state = MagicMock()
    return sensor


def test_issue_941_trace_fixture_and_repeat_cadence() -> None:
    """K4659 in Scenarios mode (Object 128) repeats WHAT 34 per delay and emits no WHAT 39."""
    assert TRACE_PATH.is_file()
    data = _load_trace()

    assert data["gateway"]["model"] == "F461"
    assert data["gateway"]["firmware"] == "2.0.11"
    assert data["plant"]["device"]["model"] == "K4659"
    assert data["plant"]["device"]["modobj"] == 43
    assert data["plant"]["device"]["object_id"] == 128
    assert data["plant"]["device"]["reported_motion_timeout_s"] == 15

    frames = data["frames"]
    who1_frames = [f for f in frames if f["who"] == 1]
    assert who1_frames

    for entry in who1_frames:
        raw = entry["raw"]
        parsed = OWNCommand.parse(raw) if entry["direction"] == "tx" else OWNMessage.parse(raw)
        assert parsed is not None, f"Failed to parse WHO=1 frame: {raw}"
        assert isinstance(parsed, (OWNLightingCommand, OWNLightingEvent))

    # K4659 replies to DIMENSION 5 and 7 with its actual WHERE 01, and never emits WHAT 39.
    sens = OWNEvent.parse("*#1*01*5*3##")
    assert sens.message_type == MESSAGE_TYPE_PIR_SENSITIVITY
    assert sens.pir_sensitivity == 3

    timeout = OWNEvent.parse("*#1*01*7*0*0*15##")
    assert timeout.message_type == MESSAGE_TYPE_MOTION_TIMEOUT
    assert timeout.motion_timeout == timedelta(seconds=15)

    motion_ev = OWNEvent.parse("*1*34*1001##")
    assert motion_ev.message_type == MESSAGE_TYPE_MOTION
    assert motion_ev.motion is True

    assert not any(f["raw"].startswith("*1*39*") for f in frames)

    # Verify the captured *1*34*1001## re-trigger deltas inside continuous cycles:
    # nominal 15 s delay has up to ~0.56 s jitter, comfortably inside 15 s + MOTION_TIMEOUT_GRACE.
    what34_times = [f["t"] for f in frames if f["raw"] == "*1*34*1001##"]
    assert len(what34_times) == 9
    repeat_deltas = [
        what34_times[i] - what34_times[i - 1]
        for i in range(1, len(what34_times))
        if (what34_times[i] - what34_times[i - 1]) < 20.0
    ]
    assert len(repeat_deltas) == 6
    assert min(repeat_deltas) >= 14.8
    assert max(repeat_deltas) < 15.0 + MOTION_TIMEOUT_GRACE.total_seconds()


@pytest.mark.asyncio
async def test_motion_sensor_clears_via_scheduled_timer_without_polling(hass, freezer) -> None:
    """A 5 s K4659 Scenarios sensor clears after delay + grace without 30 s polling lag."""
    sensor = _make_motion_sensor(hass, where="1001")
    assert sensor.should_poll is False

    # Configure 5 s Time delay from DIMENSION 7 -> 5 s + 3 s grace = 8 s.
    sensor.handle_event(OWNEvent.parse("*#1*1001*7*0*0*5##"))
    expected_timeout = timedelta(seconds=5) + MOTION_TIMEOUT_GRACE
    assert sensor._timeout == expected_timeout
    assert sensor.extra_state_attributes["Timeout"] == expected_timeout.total_seconds()

    # Motion starts at t=0.
    sensor.handle_event(OWNEvent.parse("*1*34*1001##"))
    assert sensor.is_on is True

    # Continuous motion re-trigger arrives after 5.5 s (0.5 s jitter past 5 s delay):
    # sensor must stay ON without flapping and extend the hold deadline by another 8 s.
    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=5.5))
    await hass.async_block_till_done()
    assert sensor.is_on is True

    freezer.tick(timedelta(seconds=5.5))
    t1 = dt_util.utcnow()
    sensor.handle_event(OWNEvent.parse("*1*34*1001##"))
    assert sensor.is_on is True

    # Still ON just before the new deadline (7.5 s after the re-trigger).
    async_fire_time_changed_exact(hass, t1 + timedelta(seconds=7.5))
    await hass.async_block_till_done()
    assert sensor.is_on is True

    # Clears immediately once delay + grace (8.0 s) elapses — no async_update() poll needed.
    async_fire_time_changed_exact(hass, t1 + expected_timeout + timedelta(seconds=0.1))
    await hass.async_block_till_done()
    assert sensor.is_on is False


@pytest.mark.asyncio
async def test_motion_sensor_metadata_replies_do_not_extend_hold_timer(hass, freezer) -> None:
    """DIMENSION 5/7 replies must not refresh _last_updated; DIMENSION 7 shortens active hold."""
    sensor = _make_motion_sensor(hass, where="1001")

    # Motion starts while timeout is still the 303 s default.
    sensor.handle_event(OWNEvent.parse("*1*34*1001##"))
    assert sensor.is_on is True
    motion_started = sensor._last_updated

    # At +2 s, DIMENSION 5 sensitivity reply arrives: must not change _last_updated.
    freezer.tick(timedelta(seconds=2))
    sensor.handle_event(OWNEvent.parse("*#1*1001*5*3##"))
    assert sensor._last_updated == motion_started

    # At +3 s, DIMENSION 7 reports 5 s timeout (8 s total -> 5 s remaining from t=0).
    freezer.tick(timedelta(seconds=1))
    sensor.handle_event(OWNEvent.parse("*#1*1001*7*0*0*5##"))
    assert sensor._last_updated == motion_started
    assert sensor.is_on is True

    # At +7.5 s total (4.5 s after dim7 reschedule), still ON; at +8.1 s total, clears OFF.
    async_fire_time_changed_exact(hass, motion_started + timedelta(seconds=7.5))
    await hass.async_block_till_done()
    assert sensor.is_on is True

    async_fire_time_changed_exact(hass, motion_started + timedelta(seconds=8.1))
    await hass.async_block_till_done()
    assert sensor.is_on is False


@pytest.mark.asyncio
async def test_motion_end_what39_cancels_timer_and_clears_immediately(hass) -> None:
    """Stand-alone detectors emitting WHAT 39 still clear immediately and cancel the fallback timer."""
    sensor = _make_motion_sensor(hass, where="52")
    sensor.handle_event(OWNEvent.parse("*#1*52*7*0*0*45##"))

    sensor.handle_event(OWNEvent.parse("*1*34*52##"))
    assert sensor.is_on is True
    assert sensor._timeout_unsub is not None

    sensor.handle_event(OWNEvent.parse("*1*39*52##"))
    assert sensor.is_on is False
    assert sensor._timeout_unsub is None


@pytest.mark.asyncio
async def test_motion_sensor_inverted_clears_to_true_on_timeout(hass) -> None:
    """An inverted motion sensor clears back to True when the hold timer expires."""
    sensor = _make_motion_sensor(hass, where="1001", inverted=True)
    sensor.handle_event(OWNEvent.parse("*#1*1001*7*0*0*5##"))

    sensor.handle_event(OWNEvent.parse("*1*34*1001##"))
    assert sensor.is_on is False

    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=8.1))
    await hass.async_block_till_done()
    assert sensor.is_on is True


@pytest.mark.asyncio
async def test_motion_sensor_dim7_already_elapsed_clears_immediately(hass, freezer) -> None:
    """If DIMENSION 7 arrives after the shortened deadline already passed, motion clears immediately."""
    sensor = _make_motion_sensor(hass, where="1001")
    sensor.handle_event(OWNEvent.parse("*1*34*1001##"))
    assert sensor.is_on is True
    assert sensor._timeout_unsub is not None

    freezer.tick(timedelta(seconds=10))
    sensor.handle_event(OWNEvent.parse("*#1*1001*7*0*0*5##"))
    assert sensor.is_on is False
    assert sensor._timeout_unsub is None


@pytest.mark.asyncio
async def test_restored_active_motion_arms_remaining_timer(hass) -> None:
    """Restoring an active STATE_ON motion entity schedules clearing for the remaining window."""
    sensor = _make_motion_sensor(hass, where="1001")
    sensor._timeout = timedelta(seconds=5) + MOTION_TIMEOUT_GRACE  # 8 s
    restored_at = dt_util.utcnow() - timedelta(seconds=2)
    restored = MagicMock()
    restored.state = STATE_ON
    restored.last_updated = restored_at
    sensor.async_get_last_state = AsyncMock(return_value=restored)

    await sensor.async_added_to_hass()
    assert sensor.is_on is True
    assert sensor._timeout_unsub is not None

    # 5 s more (= 7 s since restored_at < 8 s): still ON.
    async_fire_time_changed_exact(hass, restored_at + timedelta(seconds=7.0))
    await hass.async_block_till_done()
    assert sensor.is_on is True

    # 8.1 s since restored_at: timer fires and clears OFF.
    async_fire_time_changed_exact(hass, restored_at + timedelta(seconds=8.1))
    await hass.async_block_till_done()
    assert sensor.is_on is False
