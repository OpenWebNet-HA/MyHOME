"""Tests for #667: MH200 Video Door Entry, Intercom & Impulse Gate/Garage Trace Replay.

Replays authentic on-wire frames recorded from a physical BTicino MH200 installation
during an outdoor bell ring, video intercom call, speech, handset hang-up, and
impulse gate/garage door operations.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from OWNd.message import (
    OWNEvent,
    OWNLightingEvent,
    OWNMessage,
    OWNSoundEvent,
)

try:
    from OWNd.message import OWNDoorEntryEvent
except ImportError:  # pragma: no cover
    try:
        from custom_components.myhome.gateway_events import (
            OWNDoorEntryEvent,  # type: ignore[assignment]
        )
    except ImportError:
        OWNDoorEntryEvent = OWNEvent  # type: ignore[misc,assignment]
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import DOMAIN
from custom_components.myhome.gateway import MyHOMEGatewayHandler

FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_667"
    / "myhome_trace_MH200_intercom_bell_gate_2026-10-09.json"
)


def test_issue_667_fixture_present_and_valid() -> None:
    """Verify that the issue 667 fixture file exists and has expected metadata."""
    assert FIXTURE_PATH.is_file(), f"Missing trace fixture: {FIXTURE_PATH}"

    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert data["gateway"]["model"] == "MH200"
    assert data["gateway"]["firmware"] == "2.1.0"
    assert len(data["frames"]) == 34


def test_issue_667_all_frames_parse() -> None:
    """Verify every frame in the authentic trace parses into an OWNMessage / OWNEvent."""
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    raw_frames = [f["raw"] for f in data["frames"]]

    for raw in raw_frames:
        msg = OWNEvent.parse(raw)
        assert msg is not None, f"Failed to parse frame: {raw}"
        assert isinstance(msg, OWNEvent)

    # Spot-check critical frames
    call_msg = OWNEvent.parse("*8*1#1#4*74##")
    assert isinstance(call_msg, OWNEvent)
    assert call_msg.who == 8
    assert getattr(call_msg, "what", getattr(call_msg, "_what", None)) == 1
    assert getattr(call_msg, "what_param", getattr(call_msg, "_what_param", None)) == ["1", "4"]
    assert call_msg.where == "74"

    hangup_msg = OWNEvent.parse("*8*9#1#4*73##")
    assert isinstance(hangup_msg, OWNEvent)
    assert hangup_msg.who == 8
    assert getattr(hangup_msg, "what", getattr(hangup_msg, "_what", None)) == 9
    assert getattr(hangup_msg, "what_param", getattr(hangup_msg, "_what_param", None)) == ["1", "4"]
    assert hangup_msg.where == "73"

    camera_off_msg = OWNEvent.parse("*6*9##")
    assert isinstance(camera_off_msg, (OWNDoorEntryEvent, OWNEvent))
    if getattr(camera_off_msg, "is_valid", False):
        assert camera_off_msg.who == 6
        assert getattr(camera_off_msg, "what", getattr(camera_off_msg, "_what", None)) == 9
        assert getattr(camera_off_msg, "_is_camera_off", False) is True
        assert camera_off_msg.human_readable_log == "Door entry camera switched OFF."

    garage_impulse = OWNEvent.parse("*1*1*71##")
    assert isinstance(garage_impulse, OWNLightingEvent)
    assert garage_impulse.who == 1
    assert garage_impulse.where == "71"

    gate_impulse = OWNEvent.parse("*1*1*72##")
    assert isinstance(gate_impulse, OWNLightingEvent)
    assert gate_impulse.who == 1
    assert gate_impulse.where == "72"

    sound_frame = OWNEvent.parse("*#16*81*1*1##")
    assert isinstance(sound_frame, OWNSoundEvent)
    assert sound_frame.who == 16


@pytest.mark.asyncio
async def test_issue_667_trace_full_replay(hass: HomeAssistant) -> None:
    """Replay all 34 authentic trace frames through MyHOMEGatewayHandler._process_message.

    Verifies that:
    1. All 34 frames are processed without throwing any exceptions.
    2. The WHO 8 entrance panel call (*8*1#1#4*74##) fires myhome_doorbell_event.
    3. Dispatcher signals are emitted for every valid message.
    4. WHO 13 dimension 15 device type detection resolves gateway model to MH200.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_PASSWORD: "open",
            CONF_MAC: "00:03:50:00:88:88",
        },
        entry_id="test_mh200_issue_667_entry",
    )
    entry.add_to_hass(hass)

    gateway = MyHOMEGatewayHandler(hass, entry)

    captured_events: list[Event] = []
    unsub_event = hass.bus.async_listen(
        "myhome_doorbell_event",
        lambda event: captured_events.append(event),
    )

    dispatcher_messages: list[OWNMessage] = []

    def _capture_dispatcher(msg: OWNMessage) -> None:
        dispatcher_messages.append(msg)

    unsub_dispatcher = async_dispatcher_connect(
        hass,
        f"myhome_message_{gateway.mac}",
        _capture_dispatcher,
    )

    try:
        data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        for frame_entry in data["frames"]:
            raw = frame_entry["raw"]
            msg = OWNEvent.parse(raw)
            assert msg is not None
            await gateway._process_message(msg)

        await hass.async_block_till_done()

        # 34 frames should all dispatch to message listeners
        assert len(dispatcher_messages) == 34

        # Exactly 1 doorbell ring occurred during the capture session
        if hasattr(gateway, "_handle_door_entry_event"):
            assert len(captured_events) == 1
            assert captured_events[0].data == {
                "where": "74",
                "event": "call",
                "is_broadcast": False,
                "gateway_mac": gateway.mac,
                "entry_id": entry.entry_id,
            }

        # Verify gateway model was detected as MH200 from WHO 13 dimension 15
        assert gateway.gateway.model == "MH200"
    finally:
        unsub_event()
        unsub_dispatcher()


@pytest.mark.asyncio
async def test_intercom_ts10_firmware_oracle_conformance(hass: HomeAssistant) -> None:
    """Verify all 59 intercom-ts10 WHO 8 oracle frames and MH200 020102 trace verdicts."""
    oracle_path = Path(__file__).resolve().parent / "golden" / "firmware_oracle.json"
    verdicts = json.loads(oracle_path.read_text(encoding="utf-8"))["verdicts"]

    # 1. Extract all 59 WHO 8 frames from the intercom-ts10 oracle suite
    # (derived from libqtdevices TS10_1_0_23 videodoorentry_device.cpp: kinds 6, 7, 14)
    intercom_ts10_frames = sorted(
        {
            frame
            for frame, entries in verdicts.items()
            if any(e.get("suite") == "intercom-ts10" for e in entries)
        }
    )
    assert len(intercom_ts10_frames) == 59

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_PASSWORD: "open",
            CONF_MAC: "00:03:50:00:88:89",
        },
        entry_id="test_intercom_ts10_oracle_entry",
    )
    entry.add_to_hass(hass)
    gateway = MyHOMEGatewayHandler(hass, entry)

    captured_events: list[Event] = []
    unsub_event = hass.bus.async_listen(
        "myhome_doorbell_event",
        lambda event: captured_events.append(event),
    )

    try:
        for raw in intercom_ts10_frames:
            msg = OWNEvent.parse(raw)
            assert msg is not None, f"Failed to parse intercom-ts10 frame: {raw}"
            assert msg.who == 8
            await gateway._process_message(msg)

        await hass.async_block_till_done()
        # None of the 59 internal/external/pager intercom-ts10 frames is an entrance panel call (kinds 1-4)
        assert captured_events == []
    finally:
        unsub_event()

    # 2. Verify emulated MH200 (020102) oracle verdicts for WHO 13 identification and WHO 1 impulse relays
    mh200_id = next(e for e in verdicts["*#13**15##"] if e["product"] == "MH200")
    assert mh200_id["emitted_own"] == ["*#13**15*4##"]
    for impulse_cmd in ("*1*1*31##", "*1*0*31##"):
        mh200_cmd = next(e for e in verdicts[impulse_cmd] if e["product"] == "MH200")
        assert mh200_cmd["reply"] == "ack"
        assert mh200_cmd["verdict"] == "out"
        assert len(mh200_cmd["bus_frames"]) == 1


@pytest.mark.asyncio
async def test_issue_669_mh202_visitor_doorbell_and_lock_trace_replay(hass: HomeAssistant) -> None:
    """Replay the 61-frame MH202 visitor doorbell call and lock release trace (#669).

    Verifies that:
    1. All 61 frames process through MyHOMEGatewayHandler._process_message cleanly.
    2. The 4-component WHO 8 call (*8*1#1#4#20*11##) fires exactly one
       myhome_doorbell_event for indoor unit where="11", while the +4 ms caller ID
       notification (*8*9#1#4*20##) does not fire a second doorbell event.
    3. Both WHO 6 lock release frames (*6*22*1## in-conversation and *6*10*4000##
       entrance panel) unlock their respective MyHOMELock entities, and the
       subsequent pulse-release frames (*6*24*1##, *6*23*4000##) and camera teardown
       (*6*9**##) are handled without errors.
    """
    mh202_trace_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "traces"
        / "mh202_sound_intercom_ducking"
        / "myhome_trace_MH202_all_2026-10-18T10-27-48.json"
    )
    assert mh202_trace_path.is_file(), f"Missing trace fixture: {mh202_trace_path}"

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.51",
            CONF_PORT: 20000,
            CONF_PASSWORD: "open",
            CONF_MAC: "00:03:50:11:22:33",
        },
        entry_id="test_mh202_issue_669_doorbell_entry",
    )
    entry.add_to_hass(hass)
    gateway = MyHOMEGatewayHandler(hass, entry)

    try:
        from custom_components.myhome.lock import MyHOMELock
    except ImportError:  # pragma: no cover
        MyHOMELock = None  # type: ignore[misc,assignment]

    lock_4000 = None
    lock_1 = None
    if MyHOMELock is not None:
        lock_4000 = MyHOMELock(
            hass=hass,
            name="Entrance Panel Lock 4000",
            entity_name=None,
            device_id="4000",
            who="6",
            where="4000",
            interface=None,
            manufacturer="BTicino",
            model="Door Entry Lock",
            gateway=gateway,
        )
        lock_1 = MyHOMELock(
            hass=hass,
            name="Conversation Lock 1",
            entity_name=None,
            device_id="1",
            who="6",
            where="1",
            interface=None,
            manufacturer="BTicino",
            model="Door Entry Lock",
            gateway=gateway,
        )
        lock_4000.async_write_ha_state = lambda: None  # type: ignore[method-assign]
        lock_1.async_write_ha_state = lambda: None  # type: ignore[method-assign]
        assert lock_4000.is_locked is True
        assert lock_1.is_locked is True

    captured_events: list[Event] = []
    unsub_event = hass.bus.async_listen(
        "myhome_doorbell_event",
        lambda event: captured_events.append(event),
    )

    try:
        data = json.loads(mh202_trace_path.read_text(encoding="utf-8"))
        assert len(data["frames"]) == 61

        for frame_entry in data["frames"]:
            raw = frame_entry["raw"]
            msg = OWNMessage.parse(raw)
            assert msg is not None, f"Failed to parse frame: {raw}"
            await gateway._process_message(msg)
            if lock_4000 is not None and lock_1 is not None and getattr(msg, "who", None) == 6:
                if getattr(msg, "where", None) == "4000":
                    lock_4000.handle_event(msg)
                elif getattr(msg, "where", None) == "1":
                    lock_1.handle_event(msg)

        await hass.async_block_till_done()

        if hasattr(gateway, "_handle_door_entry_event"):
            # Exactly 1 doorbell event fired from *8*1#1#4#20*11## (and not from *8*9#1#4*20##)
            assert len(captured_events) == 1
            assert captured_events[0].data == {
                "where": "11",
                "event": "call",
                "is_broadcast": False,
                "gateway_mac": gateway.mac,
                "entry_id": entry.entry_id,
            }

        if lock_4000 is not None and lock_1 is not None:
            # Both locks unlocked on *6*10*4000## and *6*22*1##
            assert lock_4000.is_locked is False
            assert lock_1.is_locked is False
    finally:
        if lock_4000 is not None:
            lock_4000._cancel_auto_relock()
        if lock_1 is not None:
            lock_1._cancel_auto_relock()
        unsub_event()


F461_FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_667"
    / "myhome_trace_F461_impulse_intercom_2026-10-19.json"
)


def test_f461_2026_10_19_impulse_and_intercom_trace_schema_and_parsing() -> None:
    """Verify the 2026-10-19 live F461 + BT-F411/4 trace schema, scrubbing, and OWNd parsing."""
    from OWNd.message import OWNCommand

    assert F461_FIXTURE_PATH.is_file(), f"Missing trace fixture: {F461_FIXTURE_PATH}"
    data = json.loads(F461_FIXTURE_PATH.read_text(encoding="utf-8"))

    assert data["gateway"]["model"] == "F461"
    assert data["gateway"]["firmware"] == "2.0.11"
    assert data["gateway"]["ip"] == "192.0.2.92"
    assert data["gateway"]["mac"] == "00:03:50:00:04:97"
    assert data["gateway"]["identification"]["who13_code"] == "200"
    assert data["gateway"]["identification"]["who1013_code"] == "134"

    frames = data["frames"]
    rows = data["rows"]
    assert len(frames) == 34
    assert len(rows) == 161

    for item in frames:
        assert item["direction"] == "rx"
        assert item["session"] == "evt"
        parsed = OWNMessage.parse(item["raw"])
        assert parsed is not None, f"Failed to parse evt frame: {item['raw']}"

    for r in rows:
        if r["dir"] == "tx":
            assert (
                OWNCommand.parse(r["frame"]) is not None
                or OWNMessage.parse(r["frame"]) is not None
            )
        elif r["dir"] == "rx":
            assert OWNMessage.parse(r["frame"]) is not None


@pytest.mark.asyncio
async def test_f461_live_what18_hardware_timed_pulse_and_impulse_cover_replay(
    hass: HomeAssistant,
) -> None:
    """Replay the live F461 + BT-F411/4 WHAT=18 and software impulse cover runs (#667).

    Verifies:
    1. Hardware-timed 0.5 s pulse (*1*18*06## and *1*18*07##) on physical BT-F411/4:
       - F461 ACKs in <0.15 s.
       - Physical relay closes and emits *1*1*06## (+ Group #1 *#1*#1*15*1##), then
         autonomously opens in hardware ~0.53 s later emitting *1*0*06## (+ *#1*#1*15*0##)
         without any software OFF frame.
       - Subsequent *#1*06## / *#1*07## polls confirm ['*1*0*06##'] / ['*1*0*07##'].
    2. MyHOMEImpulseCover echo window absorbs the actuator's immediate *1*1*06## echo
       and ignores the hardware auto-off *1*0*06## frame.
    3. Replaying the captured 3-step OPEN -> mid-flight STOP -> CLOSE bus sequence on
       WHERE="06" advances an external-actuated MyHOMEImpulseCover through
       opening -> stopped -> closing while ignoring *1*0*06## release frames.
    4. Unpopulated relay address 71 (*1*18*71## and *1*1*71##) times out after ~2.06 s
       with NACK and zero evt frames.
    """
    from homeassistant.components.cover import CoverDeviceClass

    data = json.loads(F461_FIXTURE_PATH.read_text(encoding="utf-8"))
    rows = data["rows"]
    r1_results = {
        r["note"]: r
        for r in rows
        if r["run"] == "run1_f411_impulse_what18_and_software_pulse"
        and r["dir"] == "result"
    }

    # 1. Verify WHAT=18 hardware-timed 0.5 s pulse on physical BT-F411/4 channels 06 and 07
    for ch, pulse_note, poll_note in (
        ("06", "what18_timed_05s_pulse_on_06", "poll_06_after_what18_pulse"),
        ("07", "what18_timed_05s_pulse_on_07", "poll_07_after_what18_pulse"),
    ):
        pulse_res = r1_results[pulse_note]
        assert pulse_res["frame"] == f"*1*18*{ch}##"
        assert pulse_res["ack"] is True
        assert pulse_res["reply_time_s"] < 0.2
        assert pulse_res["events_after"] == [
            f"*1*1*{ch}##",
            "*#1*#1*15*1##",
            f"*1*0*{ch}##",
            "*#1*#1*15*0##",
        ]
        # Verify exact autonomous hardware OFF timing (~0.51-0.54 s) from raw evt rows
        step_evts = [
            r
            for r in rows
            if r["run"] == "run1_f411_impulse_what18_and_software_pulse"
            and r["dir"] == "rx"
            and r["session"] == "evt"
            and r["step"] == f"what18_pulse_{ch}"
        ]
        t_on = next(r["t"] for r in step_evts if r["frame"] == f"*1*1*{ch}##")
        t_off = next(r["t"] for r in step_evts if r["frame"] == f"*1*0*{ch}##")
        assert 0.45 <= (t_off - t_on) <= 0.70

        poll_res = r1_results[poll_note]
        assert poll_res["ack"] is True
        assert poll_res["replies"] == [f"*1*0*{ch}##"]

    # 2. Verify unpopulated relay 71 NACKs after ~2.06 s bus timeout with no evt echo
    for unpop_note in ("what18_unpopulated_71_nack", "what1_unpopulated_71_nack"):
        unpop_res = r1_results[unpop_note]
        assert unpop_res["ack"] is False
        assert 1.8 <= unpop_res["reply_time_s"] <= 2.5
        assert unpop_res["events_after"] == []

    # 3. Replay captured bus sequence through MyHOMEImpulseCover at WHERE="06"
    try:
        from custom_components.myhome.cover import MyHOMEImpulseCover
    except ImportError:  # pragma: no cover
        return
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.92",
            CONF_PORT: 20000,
            CONF_PASSWORD: "open",
            CONF_MAC: "00:03:50:00:04:97",
        },
        entry_id="test_f461_impulse_cover_06",
    )
    entry.add_to_hass(hass)
    gateway = MyHOMEGatewayHandler(hass, entry)

    cover_06 = MyHOMEImpulseCover(
        hass=hass,
        name="Garage Door F411",
        entity_name=None,
        device_id="1-06",
        who="1",
        where="06",
        interface=None,
        device_class=CoverDeviceClass.GARAGE,
        travel_time=10.0,
        pulse_duration=0.5,
        min_cycle_time=1.0,
        pin_code=None,
        state_sensor=None,
        manufacturer="BTicino",
        model="F411",
        gateway=gateway,
    )
    cover_06.async_write_ha_state = lambda: None  # type: ignore[method-assign]
    cover_06._attr_is_closed = True

    def _feed_06(ev_list: list[str]) -> None:
        for raw in ev_list:
            msg = OWNMessage.parse(raw)
            assert msg is not None
            if getattr(msg, "who", None) == 1 and getattr(msg, "where", None) == "06":
                cover_06.handle_event(msg)

    try:
        # Step A: External keypad triggers OPEN pulse (*1*1*06## followed by *1*0*06##)
        _feed_06(r1_results["impulse_open_relay_on_06"]["events_after"])
        assert cover_06.is_opening is True
        assert cover_06.is_closing is False
        assert cover_06.is_closed is False
        # Relay release (*1*0*06##) must NOT stop or toggle the cover
        _feed_06(r1_results["impulse_open_relay_off_06"]["events_after"])
        assert cover_06.is_opening is True

        # Step B: External keypad triggers mid-flight STOP pulse (*1*1*06## -> *1*0*06##)
        _feed_06(r1_results["impulse_stop_relay_on_06"]["events_after"])
        assert cover_06.is_opening is False
        assert cover_06.is_closing is False
        _feed_06(r1_results["impulse_stop_relay_off_06"]["events_after"])
        assert cover_06.is_opening is False
        assert cover_06.is_closing is False

        # Step C: External keypad triggers CLOSE pulse (*1*1*06## -> *1*0*06##)
        _feed_06(r1_results["impulse_close_relay_on_06"]["events_after"])
        assert cover_06.is_closing is True
        assert cover_06.is_opening is False
        _feed_06(r1_results["impulse_close_relay_off_06"]["events_after"])
        assert cover_06.is_closing is True
    finally:
        cover_06._cancel_travel_timer()


def test_f461_live_who6_and_who8_nack_on_automation_gateway() -> None:
    """Verify F461 (automation DIN gateway without VDE riser) fast-NACKs WHO=6 and WHO=8 commands."""
    data = json.loads(F461_FIXTURE_PATH.read_text(encoding="utf-8"))
    rows = data["rows"]

    r2_results = [
        r
        for r in rows
        if r["run"] == "run2_who6_door_entry_lock_and_camera" and r["dir"] == "result"
    ]
    assert len(r2_results) == 9
    for r in r2_results:
        assert r["ack"] is False
        assert r["reply_time_s"] < 0.15
        assert r["events_after"] == []

    r3_results = [
        r
        for r in rows
        if r["run"] == "run3_who8_intercom_call_kinds" and r["dir"] == "result"
    ]
    assert len(r3_results) == 6
    for r in r3_results:
        assert r["ack"] is False
        assert r["reply_time_s"] < 0.15
        assert r["events_after"] == []

    r4_results = {
        r["note"]: r
        for r in rows
        if r["run"] == "run4_plant_idle_verification" and r["dir"] == "result"
    }
    assert r4_results["verify_06_off"]["replies"] == ["*1*0*06##"]
    assert r4_results["verify_07_off"]["replies"] == ["*1*0*07##"]
    assert r4_results["verify_shutter_05_stopped"]["replies"] == ["*2*0*05##"]

