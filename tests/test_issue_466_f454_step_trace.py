"""Tests for #466: Real-World BTicino F454 Relative Step Cover Positioning Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
BTicino F454 gateway (firmware 2.0, contributed by @anotherjulien in
issue #466 comment 6038376669) can be deterministically parsed and replayed
through the integration event dispatcher without exceptions or regressions.

Specifically validates autonomous WHO 2 relative step positioning (*2*11#... / *2*12#...),
command translation echoes (*2*1000#...), in-flight Dimension 10 telemetry,
and autonomous stop completions without explicit HA stop commands.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    OWNAutomationCommand,
    OWNAutomationEvent,
    OWNEnergyEvent,
    OWNGatewayCommand,
    OWNGatewayEvent,
    OWNMessage,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_466"
F454_STEP_TRACE_FILE = TRACES_DIR / "config_entry-myhome_F454_step_commands.json"


@pytest.mark.asyncio
async def test_f454_step_commands_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 500 on-wire frames from the physical F454 step command trace capture."""
    assert F454_STEP_TRACE_FILE.is_file(), f"Missing trace fixture: {F454_STEP_TRACE_FILE}"

    with open(F454_STEP_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    gateway_info = trace_data["data"]["gateway"]
    assert gateway_info["model_name"] == "F454"
    assert gateway_info["firmware"] == "2.0"
    assert gateway_info["identification"]["profile"] == "F454Profile"

    raw_frames = trace_data["data"]["bus_monitor"]["recent_frames"]
    assert len(raw_frames) == 500

    mac = "00:03:50:00:04:54"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.54",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "F454",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:webserver:1",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.0",
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

    handler = hass.data[DOMAIN][mac][CONF_ENTITY]
    handler._on_event_connection_state_change(True)

    replayed = 0
    whos_seen: set[str] = set()

    for item in raw_frames:
        raw = item.get("raw")
        if not raw or raw in ("*#*1##", "*#*0##"):
            continue

        msg = OWNMessage.parse(raw)
        assert msg is not None, f"Failed to parse authentic frame: {raw}"

        if hasattr(msg, "who") and msg.who is not None:
            whos_seen.add(str(msg.who))

        # Replay frame through the gateway event bus
        async_dispatcher_send(hass, f"{DOMAIN}_{mac}_event", msg)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 500
    assert "2" in whos_seen, "Trace must contain WHO 2 automation frames"
    assert "18" in whos_seen, "Trace must contain WHO 18 energy frames"


def test_f454_step_commands_specific_frame_grammar() -> None:
    """Verify syntactic and semantic correctness of key step command frames."""
    # 1. Down 10% step command
    cmd_down_10 = OWNMessage.parse("*2*12#10#001*31##")
    assert isinstance(cmd_down_10, (OWNAutomationEvent, OWNAutomationCommand))
    assert cmd_down_10.where == "31"
    assert cmd_down_10._what == 12

    # 2. Down 10% echo frame with 1000# prefix and selector #1
    echo_down_10 = OWNMessage.parse("*2*1000#12#10#001#1*31##")
    assert isinstance(echo_down_10, OWNAutomationEvent)
    assert echo_down_10.where == "31"
    assert echo_down_10.is_translation is True

    # 3. In-flight Dimension 10 report (moving down from level 40)
    dim10_moving = OWNMessage.parse("*#2*31*10*12*40*001*0##")
    assert isinstance(dim10_moving, OWNAutomationEvent)
    assert dim10_moving.where == "31"
    assert dim10_moving.dimension == 10
    assert dim10_moving.current_position == 40
    assert dim10_moving.state == 12
    assert dim10_moving.is_closing is True

    # 4. Final Dimension 10 report (stopped at level 30 after -10% step)
    dim10_stopped = OWNMessage.parse("*#2*31*10*10*30*001*0##")
    assert isinstance(dim10_stopped, OWNAutomationEvent)
    assert dim10_stopped.where == "31"
    assert dim10_stopped.dimension == 10
    assert dim10_stopped.current_position == 30
    assert dim10_stopped.state == 10
    assert dim10_stopped.is_closing is False

    # 5. Autonomous stop frame
    stop_event = OWNMessage.parse("*2*0*31##")
    assert isinstance(stop_event, OWNAutomationEvent)
    assert stop_event.where == "31"
    assert stop_event.state == 0
    assert stop_event._what == 0
    assert stop_event.is_opening is False
    assert stop_event.is_closing is False

    # 6. Interleaved energy totalizer frame
    energy_frame = OWNMessage.parse("*#18*52*51*14165048##")
    assert isinstance(energy_frame, OWNEnergyEvent)
    assert energy_frame.where == "52"
    assert energy_frame.dimension == 51

    # 7. Gateway date/time broadcast
    clock_frame = OWNMessage.parse("*#13**#1*03*07*10*2026##")
    assert isinstance(clock_frame, (OWNGatewayCommand, OWNGatewayEvent))


def test_f454_trace_motor_deadband_invariant() -> None:
    """MIN_MOTOR_PULSE sits between the runs that did and did not move the motor.

    Durations are the spacing of the bus status frames (start to stop status,
    and the DIMENSION 10 moving/stopped telemetry), not the time the relay was
    energised. The reporter heard the relay click without movement at 1%
    (#466 comment 6038376669); 2% moved the curtain.
    1. 1% steps: status runs of 178-215 ms (DIMENSION 10: 133-216 ms), all
       shorter than MIN_MOTOR_PULSE.
    2. 2% steps: status runs of 390-409 ms (DIMENSION 10: 355-392 ms);
       MIN_MOTOR_PULSE is no longer than the longest of them, so the floor is a
       run known to move this motor.
    3. 2% (MIN_POSITION_DELTA) on this 20.4 s motor is not stretched by the floor.
    """
    from custom_components.myhome.cover_motion import (
        MIN_MOTOR_PULSE,
        MIN_POSITION_DELTA,
        compute_run_duration,
        quantize_position_delta,
    )

    with open(F454_STEP_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    raw_frames = trace_data["data"]["bus_monitor"]["recent_frames"]

    relay_pulses_1pct: list[float] = []
    relay_pulses_2pct: list[float] = []
    dim10_pulses_1pct: list[float] = []
    dim10_pulses_2pct: list[float] = []

    for i, frame in enumerate(raw_frames):
        raw = frame.get("raw", "")
        # Identify relative step commands (*2*11#<step>#... or *2*12#<step>#...)
        if raw.startswith("*2*11#") or raw.startswith("*2*12#"):
            step_pct = int(raw.split("*")[2].split("#")[1])

            relay_start_ts: float | None = None
            relay_stop_ts: float | None = None
            dim10_start_ts: float | None = None
            dim10_stop_ts: float | None = None

            # Scan subsequent frames of this step movement sequence
            for j in range(i + 1, min(i + 15, len(raw_frames))):
                sub_frame = raw_frames[j]
                r = sub_frame.get("raw", "")
                ts = float(sub_frame["timestamp"])

                # Physical relay movement status (*2*1*31## up, *2*2*31## down, *2*0*31## stop)
                if r in ("*2*1*31##", "*2*2*31##") and relay_start_ts is None:
                    relay_start_ts = ts
                elif r == "*2*0*31##":
                    relay_stop_ts = ts
                    break

                # In-flight Dimension 10 telemetry (*#2*31*10*<state>*<level>*001*0##)
                if r.startswith("*#2*31*10*"):
                    parts = r.split("*")
                    state = parts[4]
                    if state in ("11", "12") and dim10_start_ts is None:
                        dim10_start_ts = ts
                    elif state == "10":
                        dim10_stop_ts = ts

            if relay_start_ts is not None and relay_stop_ts is not None:
                duration = relay_stop_ts - relay_start_ts
                if step_pct == 1:
                    relay_pulses_1pct.append(duration)
                elif step_pct == 2:
                    relay_pulses_2pct.append(duration)

            if dim10_start_ts is not None and dim10_stop_ts is not None:
                duration = dim10_stop_ts - dim10_start_ts
                if step_pct == 1:
                    dim10_pulses_1pct.append(duration)
                elif step_pct == 2:
                    dim10_pulses_2pct.append(duration)

    # 1. 1% steps (no movement): every run is shorter than the floor
    assert len(relay_pulses_1pct) == 6, f"Expected 6 1% status runs, found {len(relay_pulses_1pct)}"
    assert 0.175 <= min(relay_pulses_1pct) and max(relay_pulses_1pct) <= 0.220
    assert max(relay_pulses_1pct) < MIN_MOTOR_PULSE, relay_pulses_1pct

    assert len(dim10_pulses_1pct) == 6, f"Expected 6 1% dim10 runs, found {len(dim10_pulses_1pct)}"
    assert 0.130 <= min(dim10_pulses_1pct) and max(dim10_pulses_1pct) <= 0.220
    assert max(dim10_pulses_1pct) < MIN_MOTOR_PULSE, dim10_pulses_1pct

    # 2. 2% steps (movement): the floor is no longer than a run that moved the motor
    assert len(relay_pulses_2pct) == 2, f"Expected 2 2% status runs, found {len(relay_pulses_2pct)}"
    assert 0.380 <= min(relay_pulses_2pct) and max(relay_pulses_2pct) <= 0.415
    assert MIN_MOTOR_PULSE <= max(relay_pulses_2pct), relay_pulses_2pct

    assert len(dim10_pulses_2pct) == 2, f"Expected 2 2% dim10 runs, found {len(dim10_pulses_2pct)}"
    assert 0.350 <= min(dim10_pulses_2pct) and max(dim10_pulses_2pct) <= 0.400

    # 3. A 1% change is widened to the 2% that moved this motor; a 1% timed run
    # is stretched to the floor, a 2% one (0.408 s) is not.
    assert MIN_POSITION_DELTA == 2
    assert quantize_position_delta(curr_pos=40, target_pos=41) == 42
    assert quantize_position_delta(curr_pos=40, target_pos=39) == 38
    assert quantize_position_delta(curr_pos=40, target_pos=40) == 40
    assert quantize_position_delta(curr_pos=40, target_pos=45) == 45

    full_travel_time = 20.4
    assert compute_run_duration(1, full_travel_time) == MIN_MOTOR_PULSE
    assert compute_run_duration(2, full_travel_time) == pytest.approx(0.408)
