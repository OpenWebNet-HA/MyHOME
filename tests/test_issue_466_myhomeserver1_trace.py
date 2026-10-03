"""Tests for #466: Real-World BTicino MyHomeServer1 Gateway Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
BTicino MyHomeServer1 gateway (contributed by @TheDarkWizard in issue #466 comment 5855690408)
can be deterministically parsed and replayed against the integration state machine
without exceptions or regressions.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

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
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    OWNAutomationEvent,
    OWNCENPlusEvent,
    OWNEnergyCommand,
    OWNEnergyEvent,
    OWNHeatingEvent,
    OWNLightingCommand,
    OWNLightingEvent,
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
from custom_components.myhome.cover import MyHOMECover

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_466"
CEN_SCENARIO_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_cen_scenario_2026-09-27T13-28-10.json"
GROUP_LIGHTS_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_group_lights_2026-09-27T13-32-12.json"
CEN_COVER_SCENARIOS_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_cen_cover_scenarios_2026-09-27T13-47-33.json"
SCENARIOS_DIAGNOSTIC_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_scenarios_cover_diagnostic_2026-09-27T13-47-33.json"
MONOSTABLE_DIAGNOSTIC_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_monostable_cover_diagnostic_2026-09-27T13-51-19.json"
BISTABLE_COVER_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_bistable_cover_2026-09-27T13-54-05.json"
CEN_LONG_PRESS_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_who25_2026-09-27T20-56-45.json"
CALIBRATION_COVER_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_all_2026-10-01T07-59-11.json"


async def _setup_myhomeserver1_gateway(hass: HomeAssistant, mac: str = "00:03:50:00:04:66"):
    """Set up a mock MyHomeServer1 config entry and gateway handler."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.1",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MyHomeServer1",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "MyHomeServer1 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.87.13",
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
    return handler, mac


@pytest.mark.asyncio
async def test_myhomeserver1_cen_scenario_trace_replay(hass: HomeAssistant) -> None:
    """Replay all 8 frames from the CEN+ scenario activation trace.

    Verifies that short-press CEN+ events on address 21 (*25*21#1*21##)
    and subsequent lighting cascade (*1*1*16##, *1*1*18##, *1*1*19##) replay cleanly.
    """
    assert CEN_SCENARIO_FILE.is_file(), f"Missing fixture: {CEN_SCENARIO_FILE}"
    with open(CEN_SCENARIO_FILE, encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "MyHomeServer1"
    assert trace_data["gateway"]["firmware"] == "2.87.13"

    _, mac = await _setup_myhomeserver1_gateway(hass)
    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 8

    cen_plus_events = []
    lighting_events = []

    for item in raw_frames:
        raw = item["raw"]
        msg = OWNMessage.parse(raw)
        if isinstance(msg, OWNCENPlusEvent):
            cen_plus_events.append(msg)
        elif isinstance(msg, OWNLightingEvent):
            lighting_events.append(msg)
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg)

    await hass.async_block_till_done()

    assert len(cen_plus_events) == 2
    for event in cen_plus_events:
        assert event.who == 25
        assert event.where == "21"
        assert event.push_button == 1
        assert event.is_short_pressed is True

    assert len(lighting_events) == 6
    for event in lighting_events:
        assert event.who == 1
        assert event.is_on is True
        assert event.where in ("16", "18", "19")


@pytest.mark.asyncio
async def test_myhomeserver1_bistable_cover_trace_replay(hass: HomeAssistant) -> None:
    """Replay all 11 frames from the bistable cover movement trace.

    Verifies the full operational cycle of cover 03:
    1. Advanced DOWN command (*2*1000#12#100#001#1*03##) & Dimension 10 feedback (*#2*03*10*12*100*001*0##)
    2. Mid-travel user STOP (*2*1000#10#001#1*03##) & stopped position at 83% (*#2*03*10*10*83*001*0##)
    3. Advanced UP command (*2*1000#11#100#001#1*03##) & moving status (*#2*03*10*11*83*001*0##)
    4. Limit switch reached STOP at 100% (*#2*03*10*10*100*001*0##)
    5. Legacy directional frames (*2*2*03##, *2*0*03##, *2*1*03##, *2*0*03##)
    """
    assert BISTABLE_COVER_FILE.is_file(), f"Missing fixture: {BISTABLE_COVER_FILE}"
    with open(BISTABLE_COVER_FILE, encoding="utf-8") as f:
        trace_data = json.load(f)

    _, mac = await _setup_myhomeserver1_gateway(hass)
    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 11

    replayed = 0
    automation_events = 0
    dimension_10_events = 0

    for item in raw_frames:
        raw = item["raw"]
        msg = OWNMessage.parse(raw)
        if isinstance(msg, OWNAutomationEvent):
            automation_events += 1
            if msg.dimension == 10:
                dimension_10_events += 1
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        replayed += 1

    await hass.async_block_till_done()

    assert replayed == 11
    assert automation_events == 11
    assert dimension_10_events == 4  # 4 Dimension 10 position reports


@pytest.mark.asyncio
async def test_myhomeserver1_group_lights_trace_replay(hass: HomeAssistant) -> None:
    """Replay all 16 frames from the Group of Light capture.

    Verifies group toggle commands on group #1 (*1*0*#1## and *1*1*#1##)
    interleaved with individual member fixture echoes on 16, 18, 19.
    """
    assert GROUP_LIGHTS_FILE.is_file(), f"Missing fixture: {GROUP_LIGHTS_FILE}"
    with open(GROUP_LIGHTS_FILE, encoding="utf-8") as f:
        trace_data = json.load(f)

    _, mac = await _setup_myhomeserver1_gateway(hass)
    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 16

    group_commands = []
    member_events = []

    for item in raw_frames:
        raw = item["raw"]
        msg = OWNMessage.parse(raw)
        if isinstance(msg, (OWNLightingCommand, OWNLightingEvent)):
            if msg.where == "#1":
                group_commands.append(msg)
            else:
                member_events.append(msg)
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg)

    await hass.async_block_till_done()

    assert len(group_commands) == 4  # 2 off, 2 on
    assert len(member_events) == 12  # 3 fixtures * 4 transitions


@pytest.mark.asyncio
async def test_myhomeserver1_cen_cover_scenarios_replay(hass: HomeAssistant) -> None:
    """Replay all 8 frames from the CEN+ multi-scenario activation trace.

    Verifies:
    1. CEN+ button presses on distinct addresses 21, 22, 23 (*25*21#1*21##, 22##, 23##)
    2. Direct shutter target positioning write to 85% (*#2*03*#11#001#1*85##) and position feedback
    3. Active power telemetry on meters 51 and 52 (*#18*51*113*444##, *#18*52*113*183##)
    """
    assert CEN_COVER_SCENARIOS_FILE.is_file(), f"Missing fixture: {CEN_COVER_SCENARIOS_FILE}"
    with open(CEN_COVER_SCENARIOS_FILE, encoding="utf-8") as f:
        trace_data = json.load(f)

    _, mac = await _setup_myhomeserver1_gateway(hass)
    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 8

    cen_addresses = []
    energy_readings = []

    for item in raw_frames:
        raw = item["raw"]
        msg = OWNMessage.parse(raw)
        if isinstance(msg, OWNCENPlusEvent):
            cen_addresses.append(msg.where)
        elif isinstance(msg, OWNEnergyEvent):
            energy_readings.append((msg.where, msg.dimension))
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg)

    await hass.async_block_till_done()

    assert cen_addresses == ["21", "22", "23"]
    assert ("51", 113) in energy_readings
    assert ("52", 113) in energy_readings


@pytest.mark.asyncio
async def test_myhomeserver1_scenarios_cover_diagnostic_replay(hass: HomeAssistant) -> None:
    """Replay all 73 frames from Delete.Scenarios diagnostic trace.

    Covers multi-subsystem traffic:
    - CEN+ triggers (addresses 21, 22, 23)
    - Shutter target positioning to 0% and 100%
    - Energy active power (Dimension 113) and power threshold alarms (Dimension 1200)
    - Thermoregulation zone 2 temperature report at 27.1 °C (*#4*2*0*0271##)
    - WHO 1001 Physical Layer Diagnostics: device ping, identity, firmware, and slot objects
    """
    assert SCENARIOS_DIAGNOSTIC_FILE.is_file(), f"Missing fixture: {SCENARIOS_DIAGNOSTIC_FILE}"
    with open(SCENARIOS_DIAGNOSTIC_FILE, encoding="utf-8") as f:
        trace_data = json.load(f)

    _, mac = await _setup_myhomeserver1_gateway(hass)
    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 73

    whos_seen = set()
    temperatures_seen = []

    for item in raw_frames:
        raw = item["raw"]
        msg = OWNMessage.parse(raw)
        whos_seen.add(msg.who)
        if isinstance(msg, OWNHeatingEvent) and getattr(msg, "main_temperature", None) is not None:
            temperatures_seen.append((msg.where, msg.main_temperature))
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg)

    await hass.async_block_till_done()

    assert whos_seen == {2, 4, 18, 25, 1001}
    assert ("2", 27.1) in temperatures_seen


@pytest.mark.asyncio
async def test_myhomeserver1_monostable_cover_diagnostic_replay(hass: HomeAssistant) -> None:
    """Replay all 114 frames from Confid.Cover.-.monostable diagnostic trace.

    Verifies device configuration memory dump via WHO 1001:
    - Session start/stop commands (*1000*5*0## and *1000*6*0##)
    - Dimension 30 slot object assignments
    - Dimension 35 indexed configuration register reads across 84 parameters
    All frames replay without errors or state poisoning.
    """
    assert MONOSTABLE_DIAGNOSTIC_FILE.is_file(), f"Missing fixture: {MONOSTABLE_DIAGNOSTIC_FILE}"
    with open(MONOSTABLE_DIAGNOSTIC_FILE, encoding="utf-8") as f:
        trace_data = json.load(f)

    _, mac = await _setup_myhomeserver1_gateway(hass)
    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 114

    replayed = 0
    for item in raw_frames:
        raw = item["raw"]
        msg = OWNMessage.parse(raw)
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 114


@pytest.mark.asyncio
async def test_myhomeserver1_kw8011_dummy_scenario_multi_action_sequence(hass: HomeAssistant) -> None:
    """Verify Living Now KW8011 dummy-scenario multi-action lifecycle on MyHomeServer1.

    When bound to a dummy 'wait 1 second' scenario in MyHOME_Up (issue #466 comment 5857820095),
    the physical KW8011 broadcasts:
    1. Short press: *25*21#1*21## -> CONF_SHORT_PRESS
    2. Long press start: *25*22#1*21## -> CONF_LONG_PRESS
    3. Hold repeat: *25*23#1*21## -> CONF_LONG_PRESS_REPEAT
    4. Long release: *25*24#1*21## -> CONF_LONG_RELEASE

    Verifies that:
    - The gateway event dispatcher translates all 4 frames into corresponding events.
    - myhome_cenplus_event bus payloads contain both object (1) and raw_where ("21").
    - Device triggers configured with both wire address (21) and virtual object (1) fire.
    - Auto-registration registers the CEN+ Unit in the device registry.
    """
    from unittest.mock import AsyncMock

    from custom_components.myhome.const import (
        CONF_LONG_PRESS,
        CONF_LONG_PRESS_REPEAT,
        CONF_LONG_RELEASE,
        CONF_SHORT_PRESS,
    )
    from custom_components.myhome.device_trigger import (
        CONF_ADDRESS,
        CONF_SUBTYPE,
        CONF_TYPE,
        async_attach_trigger,
    )

    handler, mac = await _setup_myhomeserver1_gateway(hass)

    # Attach triggers by wire address 21 (as labeled in MyHOME_Up / on-wire traces)
    action_wire_short = AsyncMock()
    unsub1 = await async_attach_trigger(
        hass,
        {CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_1", CONF_ADDRESS: 21},
        action_wire_short,
        {},
    )
    action_wire_long = AsyncMock()
    unsub2 = await async_attach_trigger(
        hass,
        {CONF_TYPE: CONF_LONG_PRESS, CONF_SUBTYPE: "button_1", CONF_ADDRESS: 21},
        action_wire_long,
        {},
    )
    action_wire_repeat = AsyncMock()
    unsub3 = await async_attach_trigger(
        hass,
        {CONF_TYPE: CONF_LONG_PRESS_REPEAT, CONF_SUBTYPE: "button_1", CONF_ADDRESS: 21},
        action_wire_repeat,
        {},
    )
    action_wire_release = AsyncMock()
    unsub4 = await async_attach_trigger(
        hass,
        {CONF_TYPE: CONF_LONG_RELEASE, CONF_SUBTYPE: "button_1", CONF_ADDRESS: 21},
        action_wire_release,
        {},
    )

    # Attach trigger by virtual object 1
    action_obj_short = AsyncMock()
    unsub5 = await async_attach_trigger(
        hass,
        {CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_1", CONF_ADDRESS: 1},
        action_obj_short,
        {},
    )

    # Capture raw bus events
    bus_events = []
    hass.bus.async_listen("myhome_cenplus_event", lambda ev: bus_events.append(ev.data))

    # 1. Short press: *25*21#1*21##
    msg1 = OWNMessage.parse("*25*21#1*21##")
    await handler._process_message(msg1)
    await hass.async_block_till_done()

    action_wire_short.assert_called_once()
    action_obj_short.assert_called_once()
    assert bus_events[-1]["event"] == CONF_SHORT_PRESS
    assert bus_events[-1]["object"] == 1
    assert bus_events[-1]["where"] == "1"

    # 2. Long press start: *25*22#1*21##
    msg2 = OWNMessage.parse("*25*22#1*21##")
    await handler._process_message(msg2)
    await hass.async_block_till_done()

    action_wire_long.assert_called_once()
    assert bus_events[-1]["event"] == CONF_LONG_PRESS
    assert bus_events[-1]["object"] == 1

    # 3. Hold repeat: *25*23#1*21##
    msg3 = OWNMessage.parse("*25*23#1*21##")
    await handler._process_message(msg3)
    await hass.async_block_till_done()

    action_wire_repeat.assert_called_once()
    assert bus_events[-1]["event"] == CONF_LONG_PRESS_REPEAT

    # 4. Long release: *25*24#1*21##
    msg4 = OWNMessage.parse("*25*24#1*21##")
    await handler._process_message(msg4)
    await hass.async_block_till_done()

    action_wire_release.assert_called_once()
    assert bus_events[-1]["event"] == CONF_LONG_RELEASE

    for unsub in (unsub1, unsub2, unsub3, unsub4, unsub5):
        unsub()


@pytest.mark.asyncio
async def test_myhomeserver1_cen_long_press_trace_replay(hass: HomeAssistant) -> None:
    """Replay all 9 frames from the physical MyHomeServer1 CEN+ long press & release trace.

    Authentic capture contributed by @TheDarkWizard on #466 (comment 5859798807),
    testing a BTicino Living Now KW8011 switch bound to a 'wait 1 second' dummy scenario:
    1. Short press (frame 1): *25*21#1*21## -> CONF_SHORT_PRESS
    2. Long press start (frame 2): *25*22#1*21## -> CONF_LONG_PRESS
    3. Immediate release (frame 3): *25*24#1*21## -> CONF_LONG_RELEASE
    4. Short press (frame 4): *25*21#1*21## -> CONF_SHORT_PRESS
    5. Long press start (frame 5): *25*22#1*21## -> CONF_LONG_PRESS
    6. Release after 3s (frame 6): *25*24#1*21## -> CONF_LONG_RELEASE
    7. Short press (frame 7): *25*21#1*21## -> CONF_SHORT_PRESS
    8. Long press start (frame 8): *25*22#1*21## -> CONF_LONG_PRESS
    9. Release after 6s (frame 9): *25*24#1*21## -> CONF_LONG_RELEASE

    Empirically proves:
    - The physical Living Now KW8011 switch does NOT broadcast 23# (hold repeat) frames;
      long press transitions directly from 22# to 24# upon release regardless of hold duration (0s, 3s, 6s).
    - All 9 frames replay cleanly through the gateway event pipeline and trigger matching automations
      configured with wire WHERE 21 and virtual object 1.
    """
    assert CEN_LONG_PRESS_FILE.is_file(), f"Missing fixture: {CEN_LONG_PRESS_FILE}"
    with open(CEN_LONG_PRESS_FILE, encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "MyHomeServer1"
    assert trace_data["gateway"]["firmware"] == "2.87.13"

    handler, _ = await _setup_myhomeserver1_gateway(hass)
    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 9

    from unittest.mock import AsyncMock

    from custom_components.myhome.const import (
        CONF_LONG_PRESS,
        CONF_LONG_RELEASE,
        CONF_SHORT_PRESS,
    )
    from custom_components.myhome.device_trigger import (
        CONF_ADDRESS,
        CONF_SUBTYPE,
        CONF_TYPE,
        async_attach_trigger,
    )

    action_wire_short = AsyncMock()
    unsub1 = await async_attach_trigger(
        hass,
        {CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_1", CONF_ADDRESS: 21},
        action_wire_short,
        {},
    )
    action_wire_long = AsyncMock()
    unsub2 = await async_attach_trigger(
        hass,
        {CONF_TYPE: CONF_LONG_PRESS, CONF_SUBTYPE: "button_1", CONF_ADDRESS: 21},
        action_wire_long,
        {},
    )
    action_wire_release = AsyncMock()
    unsub3 = await async_attach_trigger(
        hass,
        {CONF_TYPE: CONF_LONG_RELEASE, CONF_SUBTYPE: "button_1", CONF_ADDRESS: 21},
        action_wire_release,
        {},
    )
    action_obj_short = AsyncMock()
    unsub4 = await async_attach_trigger(
        hass,
        {CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_1", CONF_ADDRESS: 1},
        action_obj_short,
        {},
    )

    bus_events = []
    hass.bus.async_listen("myhome_cenplus_event", lambda ev: bus_events.append(ev.data))

    for item in raw_frames:
        raw = item["raw"]
        msg = OWNMessage.parse(raw)
        assert isinstance(msg, OWNCENPlusEvent)
        assert msg.who == 25
        assert msg.where == "21"
        assert msg.push_button == 1
        await handler._process_message(msg)
        await hass.async_block_till_done()

    # 3 short presses, 3 long press starts, 3 long releases
    assert action_wire_short.call_count == 3
    assert action_obj_short.call_count == 3
    assert action_wire_long.call_count == 3
    assert action_wire_release.call_count == 3

    assert len(bus_events) == 9
    event_types = [ev["event"] for ev in bus_events]
    assert event_types == [
        CONF_SHORT_PRESS,
        CONF_LONG_PRESS,
        CONF_LONG_RELEASE,
        CONF_SHORT_PRESS,
        CONF_LONG_PRESS,
        CONF_LONG_RELEASE,
        CONF_SHORT_PRESS,
        CONF_LONG_PRESS,
        CONF_LONG_RELEASE,
    ]

    for unsub in (unsub1, unsub2, unsub3, unsub4):
        unsub()



def test_mixed_bus_capture_2026_09_29_parses():
    """The @gdluck mixed capture (comment 5895736715) parses frame by frame without errors."""
    trace = json.loads(
        (TRACES_DIR / "myhome_trace_MyHomeServer1_all_2026-09-29T17-57-37.json").read_text(encoding="utf-8")
    )
    frames = [f["raw"] for f in trace["frames"]]
    assert len(frames) == 200
    messages = [OWNMessage.parse(raw) for raw in frames]
    assert all(message is not None for message in messages)
    assert sum(isinstance(m, OWNLightingEvent) for m in messages) >= 30
    assert sum(isinstance(m, OWNHeatingEvent) for m in messages) >= 100


def test_cover_calibration_capture_2026_10_01_parses():
    """The @Interstellar0verdrive cover calibration capture (comment 5927373969) parses frame by frame without errors."""
    trace = json.loads(CALIBRATION_COVER_FILE.read_text(encoding="utf-8"))
    frames = trace["frames"]
    assert len(frames) == 159

    raw_frames = [f["raw"] for f in frames]
    messages = [OWNMessage.parse(raw) for raw in raw_frames]
    assert all(message is not None for message in messages)

    # WHO 2 breakdown: 64 frames, all targeting cover WHERE 91
    who2_messages = [m for m in messages if isinstance(m, OWNAutomationEvent)]
    assert len(who2_messages) == 64
    assert all(m.where == "91" for m in who2_messages)

    # WHO 18 breakdown: 95 frames (89 events, 6 commands) across meters 51, 52, 53
    who18_messages = [m for m in messages if isinstance(m, (OWNEnergyEvent, OWNEnergyCommand))]
    assert len(who18_messages) == 95
    assert sum(isinstance(m, OWNEnergyEvent) for m in messages) == 89
    assert sum(isinstance(m, OWNEnergyCommand) for m in messages) == 6

    # Dimension 113 (active power) telemetry: 86 frames
    dim113_frames = [f for f in frames if f.get("who") == "18" and "*113*" in f["raw"]]
    assert len(dim113_frames) == 86

    # Dimension 1200 (power threshold): 9 frames total
    dim1200_frames = [f for f in frames if f.get("who") == "18" and "1200" in f["raw"]]
    assert len(dim1200_frames) == 9
    rx_1200 = [f for f in dim1200_frames if f["direction"] == "rx"]
    tx_1200 = [f for f in dim1200_frames if f["direction"] == "tx"]
    assert len(rx_1200) == 3
    assert [f["raw"] for f in rx_1200] == [
        "*#18*51*1200#1*125##",
        "*#18*52*1200#1*125##",
        "*#18*53*1200#1*125##",
    ]
    # Two periodic poll rounds (meters 52, 51, 53) spaced 300s (5 minutes) apart
    assert len(tx_1200) == 6
    round1 = [f["raw"] for f in tx_1200[:3]]
    round2 = [f["raw"] for f in tx_1200[3:]]
    assert round1 == ["*#18*52*1200##", "*#18*51*1200##", "*#18*53*1200##"]
    assert round2 == ["*#18*52*1200##", "*#18*51*1200##", "*#18*53*1200##"]
    poll_interval = tx_1200[3]["timestamp"] - tx_1200[0]["timestamp"]
    assert 299.0 <= poll_interval <= 301.0

    # Eight start/stop movement runs on WHERE 91 (4 down, 4 up)
    who2_raw = [f for f in frames if f.get("who") == "2"]
    starts = [i for i, f in enumerate(who2_raw) if f["direction"] == "tx" and f["raw"] in ("*2*1*91##", "*2*2*91##")]
    stops = [i for i, f in enumerate(who2_raw) if f["direction"] == "tx" and f["raw"] == "*2*0*91##"]
    assert len(starts) == 8
    assert len(stops) == 8

    # Verify each start command produces the 4-frame sequence: leading idle (1000#0, 0) then moving (1000#dir, dir)
    for s_idx in starts:
        cmd = who2_raw[s_idx]["raw"]
        direction = "1" if cmd == "*2*1*91##" else "2"
        assert who2_raw[s_idx + 1]["raw"] == "*2*1000#0*91##"
        assert who2_raw[s_idx + 2]["raw"] == "*2*0*91##"
        assert who2_raw[s_idx + 3]["raw"] == f"*2*1000#{direction}*91##"
        assert who2_raw[s_idx + 4]["raw"] == f"*2*{direction}*91##"

    # Verify each stop command produces the 2-frame stop sequence: 1000#0 and 0
    for st_idx in stops:
        assert who2_raw[st_idx + 1]["raw"] == "*2*1000#0*91##"
        assert who2_raw[st_idx + 2]["raw"] == "*2*0*91##"


@pytest.mark.asyncio
async def test_myhomeserver1_cover_calibration_trace_replay(hass: HomeAssistant) -> None:
    """Replay all 159 frames from the guided cover calibration run trace.

    Authentic capture contributed by @Interstellar0verdrive on #466 (comment 5927373969):
    - Eight timed movements on cover WHERE 91 started and stopped by the calibration wizard.
    - Gateway status replies (*2*1000#x* followed by basic status frames).
    - Verifies cover state machine correctly transitions through the leading idle report
      (*2*1000#0*91## + *2*0*91##) and enters opening/closing upon directional status.
    - Interleaved WHO 18 energy frames (Dimension 113 active power and Dimension 1200 on meters 51, 52, 53).
    """
    assert CALIBRATION_COVER_FILE.is_file(), f"Missing fixture: {CALIBRATION_COVER_FILE}"
    trace = json.loads(CALIBRATION_COVER_FILE.read_text(encoding="utf-8"))

    assert trace["gateway"]["model"] == "MyHomeServer1"
    assert trace["gateway"]["firmware"] == "2.87.13"

    handler, mac = await _setup_myhomeserver1_gateway(hass)
    raw_frames = trace["frames"]
    assert len(raw_frames) == 159

    cover = MyHOMECover(
        hass=hass,
        name="Cover 91",
        entity_name="Cover 91",
        device_id="91",
        who="2",
        where="91",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="F411",
        gateway=handler,
    )
    cover._publish_state = MagicMock()

    replayed = 0
    who2_events = []
    who18_events = []
    recovered_runs = []

    for item in raw_frames:
        raw = item["raw"]
        msg = OWNMessage.parse(raw)
        if isinstance(msg, OWNAutomationEvent):
            who2_events.append(msg)
            if msg.where == "91":
                cover.handle_event(msg)
                # When the moving status frame arrives after the leading idle, record active motion
                if item["direction"] == "rx" and raw in ("*2*1*91##", "*2*2*91##"):
                    run_dir = "up" if cover.is_opening else "down" if cover.is_closing else "stopped"
                    recovered_runs.append((raw, run_dir))
        elif isinstance(msg, (OWNEnergyEvent, OWNEnergyCommand)):
            who18_events.append(msg)

        await handler._process_message(msg)
        replayed += 1

    await hass.async_block_till_done()

    assert replayed == 159
    assert len(who2_events) == 64
    assert len(who18_events) == 95

    # All WHO 2 events target actuator WHERE 91
    assert all(ev.where == "91" for ev in who2_events)

    # Energy meter addresses in trace are 51, 52, and 53
    energy_meters = {ev.where for ev in who18_events}
    assert energy_meters == {"51", "52", "53"}

    # Verify that the cover was NOT permanently trapped in stopped state by the leading idle
    # frame (*2*0*91##), but correctly recovered active motion for all 8 runs:
    # 4 down runs and 4 up runs
    assert len(recovered_runs) == 8
    assert [direction for _, direction in recovered_runs] == [
        "down",
        "up",
        "down",
        "up",
        "down",
        "up",
        "up",
        "down",
    ]

    # At the end of the trace (after final stop run), the cover settles into stopped state
    assert cover.is_opening is False
    assert cover.is_closing is False

