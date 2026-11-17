"""Tests for #466: Real-World BTicino F454 and MH202 Gateway Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from physical
F454 and MH202 gateways (contributed by @anotherjulien in issue #466 comment 5849027587)
can be deterministically parsed and replayed against the integration state machine
without exceptions or regressions.
"""

from __future__ import annotations

import json
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
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    OWNAutomationCommand,
    OWNAutomationEvent,
    OWNCENPlusEvent,
    OWNDryContactEvent,
    OWNEnergyEvent,
    OWNEvent,
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

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_466"
F454_SWEEP_FILE = TRACES_DIR / "myhome_sweep_F454_all_2026-09-26T16-59-13.json"
MH202_TRACE_FILE = TRACES_DIR / "myhome_trace_MH202_all_2026-09-26T16-59-17.json"
F414_TRACE_FILE = TRACES_DIR / "myhome_trace_MH200_f414_dimmer_2026-09-26T21-59-00.json"
F418U2_TRACE_FILE = TRACES_DIR / "myhome_trace_F454_f418u2_dimmer_2026-09-27T08-58-54.json"
MH200_F418U2_TRACE_FILE = TRACES_DIR / "myhome_trace_MH200_f418u2_dimmer_2026-09-27T12-15-00.json"
MH202_F418U2_TRACE_FILE = TRACES_DIR / "myhome_trace_MH202_f418u2_dimmer_2026-09-27T09-43-45.json"


@pytest.mark.asyncio
async def test_f454_sweep_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 238 on-wire frames from the physical F454 bus sweep capture.

    Ensures every frame across WHO 1 (lights/dimmers), WHO 2 (covers), WHO 4 (climate),
    WHO 9 (auxiliary), WHO 13 (gateway), WHO 14 (actuator lock), WHO 18 (energy),
    and WHO 25 (CEN+/dry contact) replays cleanly through the event dispatcher.
    """
    assert F454_SWEEP_FILE.is_file(), f"Missing trace fixture: {F454_SWEEP_FILE}"

    with open(F454_SWEEP_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "F454"
    assert trace_data["gateway"]["firmware"] == "2.0.51"
    assert trace_data["gateway"]["identification"]["who13_code"] == "200"

    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 238

    mac = "00:03:50:00:04:54"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.54",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "F454",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "F454 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.0.51",
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
            pytest.fail(f"Failed to parse authentic F454 frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            if hasattr(msg, "who") and msg.who:
                whos_seen.add(str(msg.who))
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 238

    expected_whos = {"1", "2", "4", "9", "13", "14", "18", "25"}
    assert expected_whos.issubset(whos_seen), (
        f"Missing expected WHOs. Found: {whos_seen}, expected: {expected_whos}"
    )

    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_mh202_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 314 on-wire frames from the physical MH202 trace capture.

    Ensures every frame across WHO 1 (lights/dimmers), WHO 2 (covers), WHO 4 (climate),
    WHO 9 (auxiliary), WHO 13 (gateway), WHO 14 (actuator lock), WHO 18 (energy),
    and WHO 25 (CEN+/dry contact) replays cleanly through the event dispatcher.
    """
    assert MH202_TRACE_FILE.is_file(), f"Missing trace fixture: {MH202_TRACE_FILE}"

    with open(MH202_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "MH202"
    assert trace_data["gateway"]["firmware"] == "1.0.21"
    assert trace_data["gateway"]["identification"]["who13_code"] == "200"

    raw_frames = trace_data["frames"]
    assert len(raw_frames) == 314

    mac = "00:03:50:00:02:02"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.202",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MH202",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "MH202 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "1.0.21",
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
            pytest.fail(f"Failed to parse authentic MH202 frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            if hasattr(msg, "who") and msg.who:
                whos_seen.add(str(msg.who))
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 314

    expected_whos = {"1", "2", "4", "9", "13", "14", "18", "25"}
    assert expected_whos.issubset(whos_seen), (
        f"Missing expected WHOs. Found: {whos_seen}, expected: {expected_whos}"
    )

    await hass.config_entries.async_unload(entry.entry_id)


def test_actuator_lock_frames() -> None:
    """Verify actuator lock/unlock (WHO 14) frames from the authentic traces.

    Closes critical hardware matrix gap for both F454 and MH202.
    """
    lock_frame = "*14*1*32##"
    unlock_frame = "*14*0*32##"

    msg_lock = OWNMessage.parse(lock_frame)
    assert isinstance(msg_lock, OWNEvent)
    assert msg_lock.who == 14
    assert msg_lock._what == 1
    assert msg_lock.where == "32"

    msg_unlock = OWNMessage.parse(unlock_frame)
    assert isinstance(msg_unlock, OWNEvent)
    assert msg_unlock.who == 14
    assert msg_unlock._what == 0
    assert msg_unlock.where == "32"


def test_energy_meter_f520_telemetry() -> None:
    """Verify F520 energy totalizers and active power telemetry from authentic traces."""
    # Totalizer reading (dimension 51): Sensor 2 total power consumption
    totalizer = OWNMessage.parse("*#18*52*51*14159553##")
    assert isinstance(totalizer, OWNEnergyEvent)
    assert totalizer.who == 18
    assert totalizer.where == "52"
    assert totalizer.dimension == 51
    assert totalizer._total_consumption == 14159553

    # Active power draw reading (dimension 113)
    active_power = OWNMessage.parse("*#18*52*113*1##")
    assert isinstance(active_power, OWNEnergyEvent)
    assert active_power.who == 18
    assert active_power.where == "52"
    assert active_power.dimension == 113
    assert active_power._active_power == 1


def test_cenplus_and_dry_contact_frames() -> None:
    """Verify CEN+ pushbuttons and dry contact interface frames."""
    # CEN+ Pushbutton short press
    btn_short = OWNMessage.parse("*25*21#1*21##")
    assert isinstance(btn_short, OWNCENPlusEvent)
    assert btn_short.who == 25
    assert btn_short._what == 21
    assert btn_short.push_button == 1
    assert btn_short.object == "1"
    assert btn_short.is_short_pressed is True

    # CEN+ Pushbutton release after long press
    btn_rel = OWNMessage.parse("*25*21#2*21##")
    assert isinstance(btn_rel, OWNCENPlusEvent)
    assert btn_rel.who == 25
    assert btn_rel._what == 21
    assert btn_rel.push_button == 2

    # Dry contact transitions from physical contact interface
    dc_off = OWNMessage.parse("*25*32#1*33##")
    assert isinstance(dc_off, OWNDryContactEvent)
    assert dc_off.who == 25
    assert dc_off._what == 32
    assert dc_off.sensor == "3"
    assert dc_off.is_on is False

    dc_on = OWNMessage.parse("*25*31#1*33##")
    assert isinstance(dc_on, OWNDryContactEvent)
    assert dc_on.who == 25
    assert dc_on._what == 31
    assert dc_on.sensor == "3"
    assert dc_on.is_on is True


def test_physical_dimmer_progression_issue_434() -> None:
    """Verify physical wall switch dimming commands and level reporting (Issue #434).

    Confirms that physical 100-level dimmers emit Dimension 1 reports with speed
    parameter rather than Dimension 4.
    """
    # Wall switch physical interactions
    dim_up = OWNMessage.parse("*1*1000#30*14##")
    assert isinstance(dim_up, OWNLightingEvent)
    assert dim_up.who == 1
    assert dim_up._what == 1000
    assert dim_up._what_param == ["30"]
    assert dim_up.where == "14"

    dim_down = OWNMessage.parse("*1*1000#31*14##")
    assert isinstance(dim_down, OWNLightingEvent)
    assert dim_down.who == 1
    assert dim_down._what == 1000
    assert dim_down._what_param == ["31"]
    assert dim_down.where == "14"

    direct_on = OWNMessage.parse("*1*1000#1*14##")
    assert isinstance(direct_on, OWNLightingEvent)
    assert direct_on.who == 1
    assert direct_on._what == 1000
    assert direct_on._what_param == ["1"]
    assert direct_on.where == "14"

    direct_off = OWNMessage.parse("*1*1000#0*14##")
    assert isinstance(direct_off, OWNLightingEvent)
    assert direct_off.who == 1
    assert direct_off._what == 1000
    assert direct_off._what_param == ["0"]
    assert direct_off.where == "14"

    # Dimension 1 physical dimmer status reports with speed parameter
    level_30 = OWNMessage.parse("*#1*14*1*130*5##")
    assert isinstance(level_30, OWNLightingEvent)
    assert level_30.who == 1
    assert level_30.where == "14"
    assert level_30.dimension == 1
    assert level_30.brightness == 30
    assert level_30.transition == 5

    level_100 = OWNMessage.parse("*#1*14*1*200*5##")
    assert isinstance(level_100, OWNLightingEvent)
    assert level_100.who == 1
    assert level_100.where == "14"
    assert level_100.dimension == 1
    assert level_100.brightness == 100
    assert level_100.transition == 5


def test_advanced_cover_positioning_and_presets() -> None:
    """Verify advanced shutter preset commands and Dimension 10 position telemetry."""
    # Preset height command
    preset_cmd = OWNMessage.parse("*#2*31*#11#001*40##")
    assert isinstance(preset_cmd, OWNAutomationCommand)
    assert preset_cmd.who == 2
    assert preset_cmd.where == "31"
    assert preset_cmd.dimension == 11
    assert preset_cmd._dimension_param == ["001"]
    assert preset_cmd._dimension_value == ["40"]

    # Dimension 10 multi-parameter position feedback: 40% open
    dim10_pos40 = OWNMessage.parse("*#2*31*10*10*40*001*0##")
    assert isinstance(dim10_pos40, OWNAutomationEvent)
    assert dim10_pos40.who == 2
    assert dim10_pos40.where == "31"
    assert dim10_pos40.dimension == 10
    assert dim10_pos40.current_position == 40
    assert dim10_pos40._position_unknown is False

    # Dimension 10 movement: closing towards 66%
    dim10_closing = OWNMessage.parse("*#2*31*10*12*66*001*0##")
    assert isinstance(dim10_closing, OWNAutomationEvent)
    assert dim10_closing.who == 2
    assert dim10_closing.current_position == 66
    assert dim10_closing.is_closing is True


@pytest.mark.asyncio
async def test_f414_dimmer_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 18 on-wire frames from the physical F414 dimmer capture on MH200.

    Empirically verifies that classic 10-level F414 modular dimmers (*#1*99*...)
    replay cleanly through the event dispatcher without unhandled exceptions.
    """
    assert F414_TRACE_FILE.is_file(), f"Missing trace fixture: {F414_TRACE_FILE}"

    with open(F414_TRACE_FILE, encoding="utf-8") as f:
        data = json.load(f)

    frames = data["frames"]
    assert len(frames) == 18

    mac = "00:03:50:00:02:00"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.40",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MH200",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "MH200 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.1.0",
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
    for item in frames:
        raw = item.get("raw")
        if not raw or raw in ("*#*1##", "*#*0##"):
            continue

        try:
            msg = OWNMessage.parse(raw)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"Failed to parse authentic F414 frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 18

    await hass.config_entries.async_unload(entry.entry_id)


def test_f414_classic_dimmer_dimension_1_and_writes() -> None:
    """Verify F414 classic dimmer Dimension 1 parsing and non-linear level mappings (#466)."""
    # 100% brightness status report (Level 10)
    level_100 = OWNMessage.parse("*#1*99*1*200*2##")
    assert isinstance(level_100, OWNLightingEvent)
    assert level_100.who == 1
    assert level_100.where == "99"
    assert level_100.dimension == 1
    assert level_100.brightness == 100
    assert level_100.transition == 2

    # Discrete Level 9 maps to 74%
    level_74 = OWNMessage.parse("*#1*99*1*174*2##")
    assert isinstance(level_74, OWNLightingEvent)
    assert level_74.where == "99"
    assert level_74.dimension == 1
    assert level_74.brightness == 74
    assert level_74.transition == 2

    # Discrete Level 8 maps to 63%
    level_63 = OWNMessage.parse("*#1*99*1*163*2##")
    assert isinstance(level_63, OWNLightingEvent)
    assert level_63.where == "99"
    assert level_63.dimension == 1
    assert level_63.brightness == 63
    assert level_63.transition == 2

    # Fine Dimension 1 write response (50% at speed 5)
    level_50 = OWNMessage.parse("*#1*99*1*150*5##")
    assert isinstance(level_50, OWNLightingEvent)
    assert level_50.where == "99"
    assert level_50.dimension == 1
    assert level_50.brightness == 50
    assert level_50.transition == 5

    # Discrete WHAT levels
    evt_l8 = OWNMessage.parse("*1*8*99##")
    assert isinstance(evt_l8, OWNLightingEvent)
    assert evt_l8._what == 8
    assert evt_l8.where == "99"

    evt_l7 = OWNMessage.parse("*1*7*99##")
    assert isinstance(evt_l7, OWNLightingEvent)
    assert evt_l7._what == 7
    assert evt_l7.where == "99"

    evt_l10 = OWNMessage.parse("*1*10*99##")
    assert isinstance(evt_l10, OWNLightingEvent)
    assert evt_l10._what == 10
    assert evt_l10.where == "99"


@pytest.mark.asyncio
async def test_f418u2_dimmer_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 20 on-wire frames from the physical F454 + F418U2 modern dimmer trace (#466, #501).

    Ensures Dimension 4 status reports (off and at 30%), Dimension 1 reports,
    discrete WHAT commands, and Dimension 4 / Dimension 1 writes replay cleanly
    through the event dispatcher without exceptions.
    """
    assert F418U2_TRACE_FILE.is_file(), f"Missing trace fixture: {F418U2_TRACE_FILE}"

    with open(F418U2_TRACE_FILE, encoding="utf-8") as f:
        data = json.load(f)

    frames = data["frames"]
    assert len(frames) == 20

    mac = "00:03:50:00:04:54"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.54",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "F454",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "F454 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.0.51",
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
    for item in frames:
        raw = item.get("raw")
        if not raw or raw in ("*#*1##", "*#*0##"):
            continue

        try:
            msg = OWNMessage.parse(raw)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"Failed to parse authentic F418U2 frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 20

    await hass.config_entries.async_unload(entry.entry_id)


def test_f418u2_modern_dimmer_dimension_4_and_writes() -> None:
    """Verify F418U2 modern dimmer Dimension 4 status fallback and write behavior (#466, #501)."""
    # Dimension 4 status report when OFF (100 = 0% at transition speed 2)
    dim4_off = OWNMessage.parse("*#1*32*4*100*2##")
    assert isinstance(dim4_off, OWNLightingEvent)
    assert dim4_off.who == 1
    assert dim4_off.where == "32"
    assert dim4_off.dimension == 4
    assert dim4_off.brightness == 0
    assert dim4_off.transition == 2
    assert dim4_off.is_on is False

    # Dimension 4 status report when ON (130 = 30% at transition speed 2)
    dim4_on = OWNMessage.parse("*#1*32*4*130*2##")
    assert isinstance(dim4_on, OWNLightingEvent)
    assert dim4_on.dimension == 4
    assert dim4_on.brightness == 30
    assert dim4_on.transition == 2
    assert dim4_on.is_on is True

    # Dimension 1 status report when ON (130 = 30% with active transition speed 5)
    dim1_on = OWNMessage.parse("*#1*32*1*130*5##")
    assert isinstance(dim1_on, OWNLightingEvent)
    assert dim1_on.dimension == 1
    assert dim1_on.brightness == 30
    assert dim1_on.transition == 5
    assert dim1_on.is_on is True

    # Dimension 4 write command (ignored by physical actuator)
    dim4_write = OWNMessage.parse("*#1*32*#4*130*0##")
    assert isinstance(dim4_write, OWNLightingCommand)
    assert dim4_write.where == "32"
    assert dim4_write.dimension == 4

    # Dimension 1 write command (accepted and confirmed by physical actuator)
    dim1_write = OWNMessage.parse("*#1*32*#1*130*0##")
    assert isinstance(dim1_write, OWNLightingCommand)
    assert dim1_write.where == "32"
    assert dim1_write.dimension == 1

    # Discrete WHAT level 5 command/event (30%)
    evt_l5 = OWNMessage.parse("*1*5*32##")
    assert isinstance(evt_l5, OWNLightingEvent)
    assert evt_l5._what == 5
    assert evt_l5.where == "32"

    # Discrete OFF command/event
    evt_off = OWNMessage.parse("*1*0*32##")
    assert isinstance(evt_off, OWNLightingEvent)
    assert evt_off._what == 0
    assert evt_off.where == "32"
    assert evt_off.is_on is False


@pytest.mark.asyncio
async def test_mh200_f418u2_dimmer_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 30 on-wire frames from the physical MH200 + F418U2 modern dimmer trace (#466, #501).

    Empirically verifies that modern F418U2 universal modular dimmers routed through
    an authentic 1st-generation MH200 gateway (firmware 2.1.0) replay cleanly through
    the event dispatcher without unhandled exceptions.
    """
    assert MH200_F418U2_TRACE_FILE.is_file(), f"Missing trace fixture: {MH200_F418U2_TRACE_FILE}"

    with open(MH200_F418U2_TRACE_FILE, encoding="utf-8") as f:
        data = json.load(f)

    frames = data["frames"]
    assert len(frames) == 30

    mac = "00:03:50:00:02:00"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.40",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MH200",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "MH200 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.1.0",
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
    for item in frames:
        raw = item.get("raw")
        if not raw or raw in ("*#*1##", "*#*0##"):
            continue

        try:
            msg = OWNMessage.parse(raw)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"Failed to parse authentic MH200 F418U2 frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 30

    await hass.config_entries.async_unload(entry.entry_id)


def test_mh200_f418u2_modern_dimmer_dimension_1_and_dimming_curves() -> None:
    """Verify F418U2 dimmer Dimension 1 status, dimming curves, and write behavior on MH200 (#466, #501)."""
    # Initial 100% status report (LEVEL100=200, transition speed 2)
    dim1_100 = OWNMessage.parse("*#1*62*1*200*2##")
    assert isinstance(dim1_100, OWNLightingEvent)
    assert dim1_100.who == 1
    assert dim1_100.where == "62"
    assert dim1_100.dimension == 1
    assert dim1_100.brightness == 100
    assert dim1_100.transition == 2
    assert dim1_100.is_on is True

    # 50% write response with active transition speed 5 (LEVEL100=150)
    dim1_50_trans = OWNMessage.parse("*#1*62*1*150*5##")
    assert isinstance(dim1_50_trans, OWNLightingEvent)
    assert dim1_50_trans.dimension == 1
    assert dim1_50_trans.brightness == 50
    assert dim1_50_trans.transition == 5
    assert dim1_50_trans.is_on is True

    # Discrete WHAT 7 corresponding to 50% brightness
    evt_l7 = OWNMessage.parse("*1*7*62##")
    assert isinstance(evt_l7, OWNLightingEvent)
    assert evt_l7._what == 7
    assert evt_l7.where == "62"

    # Discrete WHAT 3 dimming curve mapping -> 10% brightness (LEVEL100=110)
    dim1_10 = OWNMessage.parse("*#1*62*1*110*2##")
    assert isinstance(dim1_10, OWNLightingEvent)
    assert dim1_10.brightness == 10
    assert dim1_10.transition == 2

    # Discrete WHAT 5 dimming curve mapping -> 30% brightness (LEVEL100=130), identical to F454
    dim1_30 = OWNMessage.parse("*#1*62*1*130*2##")
    assert isinstance(dim1_30, OWNLightingEvent)
    assert dim1_30.brightness == 30
    assert dim1_30.transition == 2

    # Dimension 1 report when OFF on MH200 gateway (LEVEL100=100 -> 0% brightness)
    dim1_off = OWNMessage.parse("*#1*62*1*100*2##")
    assert isinstance(dim1_off, OWNLightingEvent)
    assert dim1_off.dimension == 1
    assert dim1_off.brightness == 0
    assert dim1_off.transition == 2
    assert dim1_off.is_on is False

    # Dimension 1 write 50%
    write_50 = OWNMessage.parse("*#1*62*#1*150*0##")
    assert isinstance(write_50, OWNLightingCommand)
    assert write_50.where == "62"
    assert write_50.dimension == 1

    # Dimension 4 write (ignored by physical actuator)
    write_dim4 = OWNMessage.parse("*#1*62*#4*130*0##")
    assert isinstance(write_dim4, OWNLightingCommand)
    assert write_dim4.where == "62"
    assert write_dim4.dimension == 4

    # Discrete Level 10 restoration
    evt_l10 = OWNMessage.parse("*1*10*62##")
    assert isinstance(evt_l10, OWNLightingEvent)
    assert evt_l10._what == 10
    assert evt_l10.where == "62"


@pytest.mark.asyncio
async def test_mh202_f418u2_dimmer_trace_replay_without_exceptions(hass: HomeAssistant) -> None:
    """Replay all 41 on-wire frames from the physical MH202 + F418U2 modern dimmer trace (#466, #501).

    Verifies that the MH202 gateway trace testing an F418U2 dimmer on WHERE 32
    (contributed by @anotherjulien in #466 comment 5854805415) replays cleanly
    through the event dispatcher without unhandled exceptions.
    """
    assert MH202_F418U2_TRACE_FILE.is_file(), f"Missing trace fixture: {MH202_F418U2_TRACE_FILE}"

    with open(MH202_F418U2_TRACE_FILE, encoding="utf-8") as f:
        data = json.load(f)

    frames = data["frames"]
    assert len(frames) == 41

    mac = "00:03:50:00:02:02"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.52",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MH202",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "MH202 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "1.0.21",
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
    for item in frames:
        raw = item.get("raw")
        if not raw or raw in ("*#*1##", "*#*0##"):
            continue

        try:
            msg = OWNMessage.parse(raw)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"Failed to parse authentic MH202 F418U2 frame {raw!r}: {exc}")

        if msg is not None:
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        replayed += 1

    await hass.async_block_till_done()
    assert replayed == 41

    await hass.config_entries.async_unload(entry.entry_id)


def test_mh202_f418u2_dimmer_dimension_4_positive_write_and_extended_events() -> None:
    """Verify MH202 F418U2 dimmer positive Dimension 4 write, DIM 1/DIM 4 reads, and translations (#466, #501)."""
    # Dimension 1 read when OFF returns Dimension 1 (level 100 = 0% at speed 0)
    dim1_off = OWNMessage.parse("*#1*32*1*100*0##")
    assert isinstance(dim1_off, OWNLightingEvent)
    assert dim1_off.who == 1
    assert dim1_off.where == "32"
    assert dim1_off.dimension == 1
    assert dim1_off.brightness == 0
    assert dim1_off.transition == 0
    assert dim1_off.is_on is False

    # Dimension 4 read when OFF returns Dimension 4 (level 100 = 0% at speed 0)
    dim4_off = OWNMessage.parse("*#1*32*4*100*0##")
    assert isinstance(dim4_off, OWNLightingEvent)
    assert dim4_off.dimension == 4
    assert dim4_off.brightness == 0
    assert dim4_off.transition == 0
    assert dim4_off.is_on is False

    # Dimension 4 positive write command echo (accepted on MH202)
    dim4_write = OWNMessage.parse("*#1*32*#4*130*0##")
    assert isinstance(dim4_write, OWNLightingCommand)
    assert dim4_write.where == "32"
    assert dim4_write.dimension == 4

    # Dimension 4 positive write response broadcast (turns ON to 30% at speed 2)
    dim4_on = OWNMessage.parse("*#1*32*4*130*2##")
    assert isinstance(dim4_on, OWNLightingEvent)
    assert dim4_on.dimension == 4
    assert dim4_on.brightness == 30
    assert dim4_on.transition == 2
    assert dim4_on.is_on is True

    # Extended Command Translation event (*1*1000#5*32##)
    evt_trans = OWNMessage.parse("*1*1000#5*32##")
    assert isinstance(evt_trans, OWNLightingEvent)
    assert evt_trans.where == "32"
    assert evt_trans._what == 1000
    assert evt_trans.is_translation is True

    # Multi-parameter Dimension 13 feedback (*#1*32*13*2*130*5*0##)
    dim13_evt = OWNMessage.parse("*#1*32*13*2*130*5*0##")
    assert isinstance(dim13_evt, OWNLightingEvent)
    assert dim13_evt.dimension == 13
    assert dim13_evt.where == "32"

    # Speed parameter event (*1*1#0*32##)
    evt_speed0 = OWNMessage.parse("*1*1#0*32##")
    assert isinstance(evt_speed0, OWNLightingEvent)
    assert evt_speed0.where == "32"
    assert evt_speed0._what == 1
    assert evt_speed0.is_on is True

