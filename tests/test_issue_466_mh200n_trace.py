"""Tests for #466: Real-World BTicino MH200N Gateway Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
MH200N gateway (contributed by @caiosweet in issue #466 comment 5834435606)
can be deterministically parsed and replayed against the integration state machine
without exceptions or regressions.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACMode
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNHeatingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import (
    LOCAL_CONTROL_NORMAL,
    LOCAL_CONTROL_OFFSET,
    MyHOMEClimate,
)
from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_466"
SWEEP_TRACE_FILE = TRACES_DIR / "myhome_sweep_MH200N_all_2026-09-25T14-40-10.json"
DIAG_TRACE_FILE = TRACES_DIR / "config_entry-myhome-80a1577fb7ae6f68f05e0cc5a1ead27d.json"
TRACE_ALARM_DIAG_FILE = TRACES_DIR / "myhome_trace_MH200N_all_2026-09-26T18-32-34.json"


@pytest.mark.asyncio
async def test_mh200n_sweep_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 200 on-wire frames from the physical MH200N bus sweep capture.

    Ensures every frame across WHO 1 (lights), WHO 2 (covers), WHO 4 (climate),
    WHO 13 (gateway), and WHO 18 (energy) replays cleanly through the event dispatcher.
    """
    assert SWEEP_TRACE_FILE.is_file(), f"Missing trace fixture: {SWEEP_TRACE_FILE}"

    with open(SWEEP_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 200

    mac = "00:03:50:00:04:66"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.1",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MH200N",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "MH200N Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "1.1.8",
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

        try:
            msg = OWNMessage.parse(raw)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"Failed to parse authentic MH200N frame {raw!r}: {exc}")

        async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        replayed += 1
        if hasattr(msg, "who") and msg.who:
            whos_seen.add(str(msg.who))

    await hass.async_block_till_done()
    assert replayed == 200
    assert {"1", "2", "4", "13", "18"}.issubset(whos_seen)

    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_mh200n_diagnostics_trace_replay(hass: HomeAssistant) -> None:
    """Replay all 100 frames from the contributed HA diagnostic download."""
    assert DIAG_TRACE_FILE.is_file(), f"Missing diagnostic fixture: {DIAG_TRACE_FILE}"

    with open(DIAG_TRACE_FILE, "r", encoding="utf-8") as f:
        diag_data = json.load(f)

    gateway_info = diag_data["data"]["gateway"]
    assert gateway_info["model_name"] == "MH200N"

    frames = diag_data["data"]["bus_monitor"]["recent_frames"]
    assert len(frames) == 100

    parsed_count = 0
    for frame in frames:
        raw = frame.get("raw")
        if not raw or raw in ("*#*1##", "*#*0##"):
            continue
        msg = OWNMessage.parse(raw)
        assert msg is not None
        parsed_count += 1

    assert parsed_count == 100


def test_mh200n_thermoregulation_local_offset_wheel_progression(hass: HomeAssistant) -> None:
    """Test thermostat entity state transitions during local offset wheel interaction.

    Replays the exact sequence from issue #466 where the user manually turned
    the probe adjustment wheel through its full range:
    0 -> +1 -> +2 -> +3 -> +2 -> +1 -> 0 -> -1 -> -2 -> -3 -> -2 -> -1.
    """
    gateway = MagicMock()
    gateway.mac = "00:03:50:00:04:66"
    gateway.log_id = "[MH200N]"
    gateway.send = AsyncMock()

    zone4 = MyHOMEClimate(
        hass=hass,
        name="Zone 4",
        device_id="4-4",
        who="4",
        where="4",
        heating=True,
        cooling=False,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    zone4.hass = hass
    zone4.entity_id = "climate.zone_4"
    zone4.async_write_ha_state = MagicMock()

    # Initial setpoint: 18.0 °C manual heating from trace (*#4*4*14*0180*3##)
    setpoint_event = OWNHeatingEvent("*#4*4*14*0180*3##")
    zone4.handle_event(setpoint_event)
    assert zone4._target_temperature == 18.0
    zone4._attr_hvac_mode = HVACMode.HEAT

    # Sequential knob offset transitions from trace
    sequence = [
        ("*#4*4*13*00##", 0, "0", LOCAL_CONTROL_NORMAL),
        ("*#4*4*13*01##", 1, "+1", LOCAL_CONTROL_OFFSET),
        ("*#4*4*13*02##", 2, "+2", LOCAL_CONTROL_OFFSET),
        ("*#4*4*13*03##", 3, "+3", LOCAL_CONTROL_OFFSET),
        ("*#4*4*13*02##", 2, "+2", LOCAL_CONTROL_OFFSET),
        ("*#4*4*13*01##", 1, "+1", LOCAL_CONTROL_OFFSET),
        ("*#4*4*13*00##", 0, "0", LOCAL_CONTROL_NORMAL),
        ("*#4*4*13*11##", -1, "-1", LOCAL_CONTROL_OFFSET),
        ("*#4*4*13*12##", -2, "-2", LOCAL_CONTROL_OFFSET),
        ("*#4*4*13*13##", -3, "-3", LOCAL_CONTROL_OFFSET),
        ("*#4*4*13*12##", -2, "-2", LOCAL_CONTROL_OFFSET),
        ("*#4*4*13*11##", -1, "-1", LOCAL_CONTROL_OFFSET),
    ]

    for raw, expected_offset, expected_knob, expected_state in sequence:
        event = OWNHeatingEvent(raw)
        assert event.local_offset == expected_offset
        assert event.local_control_state == expected_state
        zone4.handle_event(event)
        assert zone4._local_offset == expected_offset
        assert zone4._knob_pos == expected_knob
        # local target temperature reflects setpoint + offset
        assert zone4._local_target_temperature == 18.0 + expected_offset


def test_mh200n_energy_meter_readings() -> None:
    """Verify energy totalizer responses from the MH200N trace."""
    energy_frames = [
        ("*#18*51*51*23791364##", "51", "23791364"),
        ("*#18*52*51*4441102##", "52", "4441102"),
        ("*#18*53*51*591894##", "53", "591894"),
        ("*#18*54*51*2636727##", "54", "2636727"),
        ("*#18*55*51*1057598##", "55", "1057598"),
        ("*#18*56*51*1646551##", "56", "1646551"),
        ("*#18*57*51*87900##", "57", "87900"),
    ]

    for raw, where, value in energy_frames:
        msg = OWNMessage.parse(raw)
        assert msg is not None
        assert msg.who == 18
        assert msg.where == where
        assert msg.dimension == 51
        assert getattr(msg, "_dimension_value", None) == [value]


@pytest.mark.parametrize(
    "trace_file",
    [
        f
        for f in TRACES_DIR.glob("*.json")
        if f.name
        not in (
            "myhome_sweep_MH200N_all_2026-09-25T14-40-10.json",
            "config_entry-myhome-80a1577fb7ae6f68f05e0cc5a1ead27d.json",
        )
    ],
)
def test_new_trace_payload_parsing(trace_file: Path) -> None:
    """Verify that all new traces from issue 466 parse without exceptions."""
    with open(trace_file, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    # Bus card exports hold "frames" (or legacy "history"); an HA diagnostics
    # download holds its bus monitor buffer under data.bus_monitor.recent_frames.
    raw_frames = (
        trace_data.get("frames")
        or trace_data.get("history")
        or trace_data.get("data", {}).get("bus_monitor", {}).get("recent_frames", [])
    )
    assert len(raw_frames) > 0

    parsed_count = 0
    for item in raw_frames:
        raw = item.get("raw") or item.get("frame")
        if not raw or raw in ("*#*1##", "*#*0##"):
            continue

        try:
            msg = OWNMessage.parse(raw)
            if item.get("who") is not None:
                assert msg is not None
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"Failed to parse authentic frame {raw!r} in {trace_file.name}: {exc}")
        if msg is not None:
            parsed_count += 1

    assert parsed_count > 0


@pytest.mark.asyncio
async def test_mh200n_alarm_diagnostic_and_timed_turn_on_trace_replay(hass: HomeAssistant) -> None:
    """Replay all 37 on-wire frames from the physical MH200N diagnostic and alarm trace.

    Authentic capture contributed by @manfredgittmaier-afk in issue #466 comment 5848807742.
    Verifies:
    - WHO 1013 dimension 1 gateway diagnostic (*#1013**1*44*15*0*0##) resolves to MH200N.
    - WHO 5 burglar alarm status query (*#5*0##) and zone responses (*5*11*#1##..*5*11*#8##).
    - Unparseable empty-where frames (*5*0*##, *5*9*##, *5*5*##, *5*7*##) emitted by MH200N
      without an alarm central are handled gracefully without raising exceptions.
    - WHO 25 dry contact interface (*25*32#1*31##) from F428.
    - WHO 9 aux channel query (*#9*0## -> *9*0*0##).
    - WHO 1 timed turn-on (*#1*65*#2*2*0*0## -> *1*1*65##) and WHO 17 scenario module events.
    - WHO 16 & WHO 22 audio frames, WHO 13 clock, and WHO 4 valve actuator frames.
    """
    assert TRACE_ALARM_DIAG_FILE.is_file(), f"Missing trace fixture: {TRACE_ALARM_DIAG_FILE}"

    with open(TRACE_ALARM_DIAG_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "MH200N"
    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 37

    mac = "00:03:50:00:02:00"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.200",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MH200N",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "MH200N Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "1.0",
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
        # Verify that unparseable frames (like *5*0*##) or valid OWNMessage objects
        # pass cleanly through the handler without throwing exceptions.
        if msg is not None:
            await handler._process_message(msg)
            if hasattr(msg, "who") and msg.who:
                whos_seen.add(str(msg.who))
        else:
            # Malformed/gateway-specific frame received from wire
            await handler._process_message(raw)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 37
    # Verified subsystems present in trace
    assert {"1", "4", "5", "9", "13", "16", "17", "22", "25", "1013"}.issubset(whos_seen)

    # Verify WHO 1013 diagnostic resolution for MH200N
    assert handler._who1013["code"] == "44"
    assert handler._who1013["model"] == "MH200N"
    assert handler._who1013["n_conf"] == "15"
    assert handler._who1013["brand"] == "0"
    assert handler._who1013["line"] == "0"

    # Verify no phantom alarm partition entities were created from the MH200N's WHO 5 zone frames
    alarm_entities = hass.states.async_entity_ids("alarm_control_panel")
    assert len(alarm_entities) == 0, f"Expected 0 alarm panels on MH200N without alarm, found: {alarm_entities}"

    await hass.config_entries.async_unload(entry.entry_id)
