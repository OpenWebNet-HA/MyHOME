"""Tests for declarative OpenWebNet bus exposure (ExposeManager, validation, subentries)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from OWNd.message import (
    OWNAutomationEvent,
    OWNEnergyCommand,
    OWNEnergyEvent,
    OWNHeatingEvent,
    OWNLightingEvent,
    OWNMessage,
)
from OWNd.profiles import MH202Profile
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.button import async_setup_entry as button_setup_entry
from custom_components.myhome.config_flow import ExposeSubentryFlowHandler, MyhomeFlowHandler
from custom_components.myhome.const import (
    CONF_BRIGHTNESS_STEP_PCT,
    CONF_COOLDOWN,
    CONF_DIMENSION,
    CONF_EXPOSE,
    CONF_MIN_DELTA,
    CONF_PERIODIC_SEND,
    CONF_RESPOND_TO_READ,
    CONF_WHO,
    DOMAIN,
    SUBENTRY_TYPE_EXPOSE,
)
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.diagnostics import async_get_config_entry_diagnostics
from custom_components.myhome.discovery import PlatformDiscovery
from custom_components.myhome.expose import ExposeManager
from custom_components.myhome.legacy_yaml import load_legacy_myhome_yaml
from custom_components.myhome.validate import expose_entry_schema, expose_schema, gateway_schema
from custom_components.myhome.where_grammar import (
    expose_message_where,
    is_probe,
    normalize_expose_where,
)


def _make_gateway(profile=None, mac: str = "00:03:50:aa:bb:cc"):
    """Build a mock MyHomeGateway with async send and send_status_request."""
    gw = MagicMock()
    gw.mac = mac
    gw.bus_group = mac
    gw.primary_gateway_mac = None
    gw.delegated_whos = set()
    gw.delegated_away_whos = set()
    gw.send = AsyncMock(return_value=True)
    gw.send_status_request = AsyncMock(return_value=True)
    gw._gateway = SimpleNamespace(profile=profile or MH202Profile())
    return gw


def _make_entry(hass: HomeAssistant, gateway=None, expose_list=None, subentries=None):
    """Create a MockConfigEntry with runtime_data initialized for ExposeManager tests."""
    gw = gateway or _make_gateway()
    data = {"mac": gw.mac, "host": "192.168.1.35", "port": 20000}
    if expose_list is not None:
        data[CONF_EXPOSE] = expose_list
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        unique_id=gw.mac,
        subentries_data=subentries or [],
    )
    entry.add_to_hass(hass)
    entry.runtime_data = MyHOMERuntimeData(
        gateway=gw,
        platforms={},
        entities={},
        expose=list(expose_list or []),
    )
    return entry


# ---------------------------------------------------------------------------
# 1. Grammar & Validation Tests
# ---------------------------------------------------------------------------


def test_where_grammar_probe_and_expose_helpers():
    """Verify 3-digit probe recognition and expose WHERE normalization."""
    assert is_probe("000") is True
    assert is_probe("001") is True
    assert is_probe("099") is True
    assert is_probe("101") is True
    assert is_probe("501") is True
    assert is_probe("1") is False
    assert is_probe("99") is False
    assert is_probe("") is False

    assert normalize_expose_where(1, "99") == "99"
    assert normalize_expose_where(1, "   ") == ""
    assert normalize_expose_where(4, "0") == "000"
    assert normalize_expose_where(4, "000") == "000"
    assert normalize_expose_where(4, "102") == "102"
    assert normalize_expose_where(18, "51") == "51"
    assert normalize_expose_where(18, "511") == "511"
    assert normalize_expose_where(18, "71#0") == "71#0"
    assert normalize_expose_where(13, None) == ""

    msg = OWNMessage.parse("*1*1000#1*99##")
    assert expose_message_where(msg) == "99"
    assert expose_message_where(SimpleNamespace(who=1, where=None)) == ""
    assert expose_message_where(SimpleNamespace(who="custom", where="51")) == "51"


def test_expose_schema_validation():
    """Validate expose_entry_schema and expose_schema rules and error cases."""
    valid_entries = [
        {
            "who": 1,
            "where": "99",
            "entity_id": "media_player.living_room_tv",
        },
        {
            "who": 1,
            "where": "98",
            "entity_id": "media_player.kitchen_sonos",
            "on_states": ["playing"],
            "turn_on_action": {"action": "media_player.media_play"},
            "turn_off_action": {"action": "media_player.media_pause"},
            "step_up_action": [{"action": "media_player.volume_up"}],
            "step_down_action": [{"action": "media_player.volume_down"}],
            "brightness_step_pct": 15,
        },
        {
            "who": 2,
            "where": "97",
            "entity_id": "cover.pergola_somfy",
        },
        {
            "who": 4,
            "where": "000",
            "entity_id": "sensor.weather_station_outdoor_temperature",
            "cooldown": 30,
            "min_delta": 0.1,
            "periodic_send": 900,
        },
        {
            "who": 18,
            "where": "51",
            "dimension": 113,
            "entity_id": "sensor.p1_meter_active_power",
            "cooldown": 5,
            "min_delta": 25,
        },
        {
            "who": 13,
            "periodic_send": 3600,
            "respond_to_read": True,
        },
    ]
    validated = expose_schema(valid_entries)
    assert len(validated) == 6
    assert validated[3]["where"] == "000"
    assert validated[5]["who"] == 13
    assert expose_schema({}) == []

    with pytest.raises(vol.Invalid, match="must be a list of bindings or a mapping"):
        expose_schema("not_a_list_or_dict")

    # Duplicate (who, where, dimension) rejected
    with pytest.raises(vol.Invalid, match="Duplicate expose binding"):
        expose_schema(
            [
                {"who": 1, "where": "99", "entity_id": "switch.a"},
                {"who": 1, "where": "99", "entity_id": "switch.b"},
            ]
        )

    # WHO=1 missing where
    with pytest.raises(vol.Invalid, match="requires a non-empty 'where'"):
        expose_entry_schema({"who": 1, "entity_id": "light.test"})

    # WHO=4 missing entity_id
    with pytest.raises(vol.Invalid, match="requires 'entity_id'"):
        expose_entry_schema({"who": 4, "where": "000"})

    # WHO=1 missing entity_id
    with pytest.raises(vol.Invalid, match="requires 'entity_id'"):
        expose_entry_schema({"who": 1, "where": "99"})

    # WHO=1 invalid dimension
    with pytest.raises(vol.Invalid, match="does not support 'dimension'"):
        expose_entry_schema({"who": 1, "where": "99", "entity_id": "light.test", "dimension": 1})

    # WHO=1 invalid WHERE
    with pytest.raises(vol.Invalid, match="point-to-point actuator address"):
        expose_entry_schema({"who": 1, "where": "0", "entity_id": "light.test"})

    # WHO=4 invalid WHERE (zone 1..99 not a probe)
    with pytest.raises(vol.Invalid, match="must be a probe address"):
        expose_entry_schema({"who": 4, "where": "1", "entity_id": "sensor.temp"})

    # WHO=4 invalid dimension
    with pytest.raises(vol.Invalid, match="supports only dimension 0"):
        expose_entry_schema(
            {"who": 4, "where": "000", "dimension": 14, "entity_id": "sensor.temp"}
        )

    # WHO=18 missing dimension
    with pytest.raises(vol.Invalid, match="requires 'dimension'"):
        expose_entry_schema({"who": 18, "where": "51", "entity_id": "sensor.power"})

    # WHO=18 invalid dimension
    with pytest.raises(vol.Invalid, match="dimension must be one of"):
        expose_entry_schema(
            {"who": 18, "where": "51", "dimension": 99, "entity_id": "sensor.power"}
        )

    # WHO=18 invalid WHERE
    with pytest.raises(vol.Invalid, match="must be '5N'"):
        expose_entry_schema(
            {"who": 18, "where": "12", "dimension": 113, "entity_id": "sensor.power"}
        )

    # WHO=13 invalid dimension
    with pytest.raises(vol.Invalid, match="dimension must be one of"):
        expose_entry_schema({"who": 13, "dimension": 99})

    # WHO=13 non-empty WHERE rejected
    with pytest.raises(vol.Invalid, match="does not take a 'where' address"):
        expose_entry_schema({"who": 13, "where": "1", "dimension": 22})

    # gateway_schema accepts CONF_EXPOSE
    gw_cfg = gateway_schema(
        {
            "mac": "00:03:50:aa:bb:cc",
            "expose": [{"who": 13, "dimension": 22}],
        }
    )
    assert len(gw_cfg[CONF_EXPOSE]) == 1


# ---------------------------------------------------------------------------
# 2. Scenario 1: Unpopulated Wall Switch Toggling Non-SCS Device (WHO=1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario1_wall_switch_toggles_tv_with_led_feedback(hass: HomeAssistant):
    """Test *1*1000#1*99## and *1*1000#0*99## toggling TV and syncing LED feedback."""
    hass.states.async_set("media_player.living_room_tv", "off")
    calls = []

    async def _record_call(call):
        calls.append((call.domain, call.service, dict(call.data)))

    hass.services.async_register("media_player", "turn_on", _record_call)
    hass.services.async_register("media_player", "turn_off", _record_call)

    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {
                "who": 1,
                "where": "99",
                "entity_id": "media_player.living_room_tv",
                "cooldown": 0,
            }
        ],
    )

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()

    # Initial sync puts *1*0*99## on low-priority queue
    await mgr.async_sync_all()
    assert any(
        str(call.args[0]) == "*1*0*99##" for call in gw.send_status_request.call_args_list
    )
    gw.send.reset_mock()
    gw.send_status_request.reset_mock()

    # Physical button press ON via Command Translation (*1*1000#1*99##)
    msg_on = OWNMessage.parse("*1*1000#1*99##")
    assert isinstance(msg_on, OWNLightingEvent)
    await mgr.handle_bus_message(msg_on)

    # Virtual actuator immediately confirms ON on the bus (*1*1*99##) via high-priority send()
    sent_frames = [str(c.args[0]) for c in gw.send.call_args_list]
    assert "*1*1*99##" in sent_frames
    assert calls == [("media_player", "turn_on", {"entity_id": "media_player.living_room_tv"})]

    # Echo guard: the gateway loops back our own *1*1*99## frame -> must NOT re-trigger turn_on
    calls.clear()
    gw.send.reset_mock()
    echo_msg = OWNMessage.parse("*1*1*99##")
    await mgr.handle_bus_message(echo_msg)
    assert calls == []

    # Physical button press OFF via Command Translation (*1*1000#0*99##)
    msg_off = OWNMessage.parse("*1*1000#0*99##")
    await mgr.handle_bus_message(msg_off)
    sent_frames = [str(c.args[0]) for c in gw.send.call_args_list]
    assert "*1*0*99##" in sent_frames
    assert calls == [("media_player", "turn_off", {"entity_id": "media_player.living_room_tv"})]

    # External state change in HA (TV turned on by Apple TV remote) updates wall LED
    gw.send.reset_mock()
    gw.send_status_request.reset_mock()
    hass.states.async_set("media_player.living_room_tv", "playing")
    await hass.async_block_till_done()

    status_frames = [str(c.args[0]) for c in gw.send_status_request.call_args_list]
    assert "*1*1*99##" in status_frames

    # Bus status query *#1*99## replies on high-priority queue
    gw.send.reset_mock()
    query_msg = OWNMessage.parse("*#1*99##")
    await mgr.handle_bus_message(query_msg)
    assert [str(c.args[0]) for c in gw.send.call_args_list] == ["*1*1*99##"]

    await mgr.async_stop()


# ---------------------------------------------------------------------------
# 3. Scenario 2: Dimmer Wall Button with Custom Actions & Step Up/Down
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario2_dimmer_button_sonos_custom_actions(hass: HomeAssistant):
    """Test custom actions, on_states, and step_up/step_down on *1*1000#30*98##."""
    hass.states.async_set("media_player.kitchen_sonos", "paused", {"volume_level": 0.4})
    calls = []

    async def _record_call(call):
        calls.append((call.domain, call.service, dict(call.data)))

    for svc in ("media_play", "media_pause", "volume_up", "volume_down"):
        hass.services.async_register("media_player", svc, _record_call)

    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {
                "who": 1,
                "where": "98",
                "entity_id": "media_player.kitchen_sonos",
                "on_states": ["playing"],
                "turn_on_action": {
                    "action": "media_player.media_play",
                    "target": {"entity_id": "media_player.kitchen_sonos"},
                },
                "turn_off_action": {
                    "action": "media_player.media_pause",
                    "target": {"entity_id": "media_player.kitchen_sonos"},
                },
                "step_up_action": {
                    "action": "media_player.volume_up",
                    "target": {"entity_id": "media_player.kitchen_sonos"},
                },
                "step_down_action": {
                    "action": "media_player.volume_down",
                    "target": {"entity_id": "media_player.kitchen_sonos"},
                },
                "cooldown": 0,
            }
        ],
    )

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()

    # "paused" is not in on_states=["playing"], so initial sync sends OFF (*1*0*98##)
    await mgr.async_sync_all()
    status_frames = [str(c.args[0]) for c in gw.send_status_request.call_args_list]
    assert "*1*0*98##" in status_frames

    # Short press ON (*1*1000#1*98##) runs turn_on_action (media_play)
    await mgr.handle_bus_message(OWNMessage.parse("*1*1000#1*98##"))
    assert calls[-1][1] == "media_play"

    # Hold up (*1*1000#30*98##) runs step_up_action (volume_up)
    await mgr.handle_bus_message(OWNMessage.parse("*1*1000#30*98##"))
    assert calls[-1][1] == "volume_up"

    # Hold down (*1*1000#31*98##) runs step_down_action (volume_down)
    await mgr.handle_bus_message(OWNMessage.parse("*1*1000#31*98##"))
    assert calls[-1][1] == "volume_down"

    # Short press OFF (*1*1000#0*98##) runs turn_off_action (media_pause)
    await mgr.handle_bus_message(OWNMessage.parse("*1*1000#0*98##"))
    assert calls[-1][1] == "media_pause"

    await mgr.async_stop()


@pytest.mark.asyncio
async def test_who1_light_brightness_levels_and_default_stepping(hass: HomeAssistant):
    """Test WHO=1 virtual dimmer with a Home Assistant light entity (levels 2..10, step, dim query)."""
    hass.states.async_set("light.hue_lamp", "on", {"brightness": 128})
    calls = []

    async def _record_call(call):
        calls.append((call.domain, call.service, dict(call.data)))

    hass.services.async_register("light", "turn_on", _record_call)
    hass.services.async_register("light", "turn_off", _record_call)

    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {
                "who": 1,
                "where": "96",
                "entity_id": "light.hue_lamp",
                "brightness_step_pct": 15,
                "cooldown": 0,
            }
        ],
    )

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()

    # Explicit dimmer level 7 (70%) on the bus -> light.turn_on(brightness_pct=70)
    await mgr.handle_bus_message(OWNMessage.parse("*1*7*96##"))
    assert calls[-1] == (
        "light",
        "turn_on",
        {"entity_id": "light.hue_lamp", "brightness_pct": 70},
    )

    # Step up without custom action -> light.turn_on(brightness_step_pct=15)
    await mgr.handle_bus_message(OWNMessage.parse("*1*30*96##"))
    assert calls[-1] == (
        "light",
        "turn_on",
        {"entity_id": "light.hue_lamp", "brightness_step_pct": 15},
    )

    # Step down without custom action -> light.turn_on(brightness_step_pct=-15)
    await mgr.handle_bus_message(OWNMessage.parse("*1*31*96##"))
    assert calls[-1] == (
        "light",
        "turn_on",
        {"entity_id": "light.hue_lamp", "brightness_step_pct": -15},
    )

    # Brightness dimension query *#1*96*1## -> replies *#1*96*1*150*0## (50% -> 100+50=150)
    gw.send.reset_mock()
    await mgr.handle_bus_message(OWNMessage.parse("*#1*96*1##"))
    sent_frames = [str(c.args[0]) for c in gw.send.call_args_list]
    assert sent_frames == ["*#1*96*1*150*0##"]

    await mgr.async_stop()


# ---------------------------------------------------------------------------
# 4. Scenario 3: WHO=2 Cover, WHO=4 Probe, WHO=18 Energy Meter, WHO=13 Clock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_who2_virtual_cover_actuator(hass: HomeAssistant):
    """Test WHO=2 virtual cover actuator handling UP/DOWN/STOP, position, and queries."""
    hass.states.async_set("cover.pergola_somfy", "open", {"current_position": 75})
    calls = []

    async def _record_call(call):
        calls.append((call.domain, call.service, dict(call.data)))

    for svc in ("open_cover", "close_cover", "stop_cover", "set_cover_position"):
        hass.services.async_register("cover", svc, _record_call)

    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {
                "who": 2,
                "where": "97",
                "entity_id": "cover.pergola_somfy",
                "cooldown": 0,
            }
        ],
    )

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()

    # Physical button UP via Command Translation (*2*1000#1*97##)
    await mgr.handle_bus_message(OWNMessage.parse("*2*1000#1*97##"))
    assert calls[-1] == ("cover", "open_cover", {"entity_id": "cover.pergola_somfy"})
    assert str(gw.send.call_args_list[-1].args[0]) == "*2*1*97##"

    # Physical button DOWN (*2*1000#2*97##)
    await mgr.handle_bus_message(OWNMessage.parse("*2*1000#2*97##"))
    assert calls[-1] == ("cover", "close_cover", {"entity_id": "cover.pergola_somfy"})
    assert str(gw.send.call_args_list[-1].args[0]) == "*2*2*97##"

    # Physical button STOP (*2*1000#0*97##)
    await mgr.handle_bus_message(OWNMessage.parse("*2*1000#0*97##"))
    assert calls[-1] == ("cover", "stop_cover", {"entity_id": "cover.pergola_somfy"})
    assert str(gw.send.call_args_list[-1].args[0]) == "*2*0*97##"

    # Dimension 10 position write (*#2*97*#10*11*40*0*0##) -> set_cover_position(position=40)
    await mgr.handle_bus_message(OWNMessage.parse("*#2*97*#10*11*40*0*0##"))
    assert calls[-1] == (
        "cover",
        "set_cover_position",
        {"entity_id": "cover.pergola_somfy", "position": 40},
    )

    # Dimension 10 query (*#2*97*10##) -> replies with current_position=75
    gw.send.reset_mock()
    await mgr.handle_bus_message(OWNMessage.parse("*#2*97*10##"))
    assert [str(c.args[0]) for c in gw.send.call_args_list] == ["*#2*97*10*10*75*0*0##"]

    # Standard status query (*#2*97##) -> replies *2*0*97## (stopped)
    gw.send.reset_mock()
    await mgr.handle_bus_message(OWNMessage.parse("*#2*97##"))
    assert [str(c.args[0]) for c in gw.send.call_args_list] == ["*2*0*97##"]

    await mgr.async_stop()


@pytest.mark.asyncio
async def test_who4_virtual_temperature_probe_and_deadband(hass: HomeAssistant):
    """Test WHO=4 virtual temperature probe encoding (+21.5°C, -3.2°C), min_delta, and query."""
    hass.states.async_set("sensor.outdoor_temp", "21.5")
    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {
                "who": 4,
                "where": "000",
                "dimension": 0,
                "entity_id": "sensor.outdoor_temp",
                "cooldown": 0,
                "min_delta": 0.2,
            }
        ],
    )

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()
    await mgr.async_sync_all()

    # Initial sync sends *#4*000*0*0215## via low-priority queue
    assert [str(c.args[0]) for c in gw.send_status_request.call_args_list] == [
        "*#4*000*0*0215##"
    ]
    gw.send_status_request.reset_mock()

    # Small fluctuation within min_delta (21.5 -> 21.6, delta 0.1 < 0.2) is suppressed
    hass.states.async_set("sensor.outdoor_temp", "21.6")
    await hass.async_block_till_done()
    assert gw.send_status_request.call_count == 0

    # Larger change (21.5 -> -3.2°C) is encoded with leading '1' -> 1032
    hass.states.async_set("sensor.outdoor_temp", "-3.2")
    await hass.async_block_till_done()
    assert [str(c.args[0]) for c in gw.send_status_request.call_args_list] == [
        "*#4*000*0*1032##"
    ]

    # Bus query *#4*000*0## replies on high-priority queue with current value (-3.2°C -> 1032)
    gw.send.reset_mock()
    await mgr.handle_bus_message(OWNMessage.parse("*#4*000*0##"))
    assert [str(c.args[0]) for c in gw.send.call_args_list] == ["*#4*000*0*1032##"]

    await mgr.async_stop()


@pytest.mark.asyncio
async def test_who18_virtual_energy_meter_and_dim1200_streaming(hass: HomeAssistant):
    """Test WHO=18 virtual energy meter dimensions and Dim 1200 touchscreen streaming handshake."""
    hass.states.async_set("sensor.p1_power", "1450")
    hass.states.async_set("sensor.p1_total_kwh", "12345.4")
    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {
                "who": 18,
                "where": "51",
                "dimension": 113,
                "entity_id": "sensor.p1_power",
                "cooldown": 0,
            },
            {
                "who": 18,
                "where": "51",
                "dimension": 51,
                "entity_id": "sensor.p1_total_kwh",
                "cooldown": 0,
            },
        ],
    )

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()

    # Query Dimension 113 (*#18*51*113##) -> *#18*51*113*1450##
    await mgr.handle_bus_message(OWNMessage.parse("*#18*51*113##"))
    assert [str(c.args[0]) for c in gw.send.call_args_list] == ["*#18*51*113*1450##"]
    gw.send.reset_mock()

    # Query Dimension 51 (*#18*51*51##) -> *#18*51*51*12345##
    await mgr.handle_bus_message(OWNMessage.parse("*#18*51*51##"))
    assert [str(c.args[0]) for c in gw.send.call_args_list] == ["*#18*51*51*12345##"]
    gw.send.reset_mock()

    # Touchscreen sends Dimension 1200 streaming activation (*#18*51*#1200#1*65##)
    stream_msg = OWNMessage.parse("*#18*51*#1200#1*65##")
    assert isinstance(stream_msg, OWNEnergyCommand)
    assert stream_msg.is_auto_update_command is True
    assert stream_msg.stream_duration == 65
    await mgr.handle_bus_message(stream_msg)

    sent_frames = [str(c.args[0]) for c in gw.send.call_args_list]
    assert "*#18*51*1200#1*65##" in sent_frames
    assert "*#18*51*113*1450##" in sent_frames

    await mgr.async_stop()


@pytest.mark.asyncio
async def test_who13_bus_clock_sync(hass: HomeAssistant):
    """Test WHO=13 bus clock synchronization for Dimensions 0, 1, and 22."""
    await hass.config.async_set_time_zone("UTC")
    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {
                "who": 13,
                "dimension": 22,
                "respond_to_read": True,
            }
        ],
    )

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()
    try:
        fixed_dt = datetime(2026, 7, 15, 14, 30, 45, tzinfo=timezone.utc)
        with patch("custom_components.myhome.expose.dt_util.now", return_value=fixed_dt):
            # Initial sync pushes Dimension 22 write frame (*#13**#22*14*30*45*000*03*15*07*2026##)
            await mgr.async_sync_all()
            status_frames = [str(c.args[0]) for c in gw.send_status_request.call_args_list]
            assert status_frames == ["*#13**#22*14*30*45*000*03*15*07*2026##"]

            # Bus query for Dimension 0 (*#13**0##) -> replies with time report frame
            gw.send.reset_mock()
            await mgr.handle_bus_message(OWNMessage.parse("*#13**0##"))
            assert [str(c.args[0]) for c in gw.send.call_args_list] == [
                "*#13**0*14*30*45*000##"
            ]

            # Bus query for Dimension 1 (*#13**1##) -> replies with date report frame
            gw.send.reset_mock()
            await mgr.handle_bus_message(OWNMessage.parse("*#13**1##"))
            assert [str(c.args[0]) for c in gw.send.call_args_list] == [
                "*#13**1*03*15*07*2026##"
            ]
    finally:
        await mgr.async_stop()


# ---------------------------------------------------------------------------
# 5. Cooldown, Periodic Send, and Gateway Profile Guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trailing_edge_cooldown_coalesces_rapid_updates(hass: HomeAssistant):
    """Verify trailing-edge cooldown sends the first update immediately and coalesces rapid changes."""
    hass.states.async_set("sensor.p1_power", "100")
    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {
                "who": 18,
                "where": "51",
                "dimension": 113,
                "entity_id": "sensor.p1_power",
                "cooldown": 10,
                "min_delta": 1,
            }
        ],
    )

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()

    # First state change fires immediately
    hass.states.async_set("sensor.p1_power", "200")
    await hass.async_block_till_done()
    assert [str(c.args[0]) for c in gw.send_status_request.call_args_list] == [
        "*#18*51*113*200##"
    ]
    gw.send_status_request.reset_mock()

    # Rapid updates within the 10s cooldown window are deferred to a trailing-edge timer
    hass.states.async_set("sensor.p1_power", "300")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.p1_power", "450")
    await hass.async_block_till_done()
    assert gw.send_status_request.call_count == 0
    assert (18, "51", 113) in mgr._cooldown_unsubs

    await mgr.async_stop()


# ---------------------------------------------------------------------------
# 6. Phantom Entity Prevention in Discovery & Button Platform
# ---------------------------------------------------------------------------


def test_discovery_suppresses_phantom_entities_for_exposed_addresses(hass: HomeAssistant):
    """Verify PlatformDiscovery ignores traffic for exposed virtual addresses while preserving climate zones."""
    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {"who": 1, "where": "99", "entity_id": "media_player.living_room_tv"},
            {"who": 2, "where": "97", "entity_id": "cover.pergola_somfy"},
            {"who": 4, "where": "000", "dimension": 0, "entity_id": "sensor.outdoor_temp"},
            {"who": 18, "where": "51", "dimension": 113, "entity_id": "sensor.p1_power"},
        ],
    )

    mgr = ExposeManager(hass, entry)
    entry.runtime_data.expose_manager = mgr

    added_lights: list = []

    def _build_dummy(ctx):
        ent = MagicMock()
        ent.unique_id = f"{gw.mac}-{ctx.who}-{ctx.key}"
        ent._poll_on_add = False
        ent.async_on_remove = MagicMock()
        ent.handle_event = MagicMock()
        return ent

    light_disc = PlatformDiscovery(
        hass,
        entry,
        added_lights.extend,
        platform="light",
        who="1",
        event_type=OWNLightingEvent,
        build=_build_dummy,
    )

    # Exposed WHO=1 WHERE=99 must NOT create a phantom light
    light_disc.handle_message(OWNMessage.parse("*1*1*99##"))
    assert len(added_lights) == 0

    # Unexposed WHO=1 WHERE=12 still discovers normally
    light_disc.handle_message(OWNMessage.parse("*1*1*12##"))
    assert len(added_lights) == 1

    # Exposed WHO=2 WHERE=97 must NOT create a phantom cover
    added_covers: list = []
    cover_disc = PlatformDiscovery(
        hass,
        entry,
        added_covers.extend,
        platform="cover",
        who="2",
        event_type=OWNAutomationEvent,
        build=_build_dummy,
    )
    cover_disc.handle_message(OWNMessage.parse("*2*1*97##"))
    assert len(added_covers) == 0

    # Exposed WHO=4 WHERE=000 must NOT create a phantom standalone probe sensor
    added_sensors: list = []
    sensor_disc_4 = PlatformDiscovery(
        hass,
        entry,
        added_sensors.extend,
        platform="sensor",
        who="4",
        event_type=OWNHeatingEvent,
        build=_build_dummy,
    )
    sensor_disc_4.handle_message(OWNMessage.parse("*#4*000*0*0215##"))
    assert len(added_sensors) == 0

    # Exposed WHO=18 WHERE=51 must NOT create a phantom energy sensor
    sensor_disc_18 = PlatformDiscovery(
        hass,
        entry,
        added_sensors.extend,
        platform="sensor",
        who="18",
        event_type=OWNEnergyEvent,
        build=_build_dummy,
    )
    sensor_disc_18.handle_message(OWNMessage.parse("*#18*51*113*1450##"))
    assert len(added_sensors) == 0


@pytest.mark.asyncio
async def test_button_platform_skips_exposed_virtual_actuators(hass: HomeAssistant):
    """Verify button.py does not create lock/unlock or calibration buttons for exposed virtual WHO=1/2 actuators."""
    from homeassistant.helpers import entity_registry as er

    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {"who": 1, "where": "99", "entity_id": "media_player.living_room_tv"},
            {"who": 2, "where": "97", "entity_id": "cover.pergola_somfy"},
        ],
    )
    mgr = ExposeManager(hass, entry)
    entry.runtime_data.expose_manager = mgr
    entry.runtime_data.platforms["button"] = {
        "99": {"where": "99", "who": "1", "name": "Virtual TV Switch"},
        "12": {"where": "12", "who": "1", "name": "Physical Relay"},
    }

    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "cover",
        DOMAIN,
        f"{gw.mac}-2-97",
        config_entry=entry,
        original_name="Exposed Cover 97",
    )

    added = []
    await button_setup_entry(hass, entry, added.extend)
    # Only physical actuator "12" gets lock/unlock buttons; virtual actuators "99" and "97" are skipped
    button_wheres = {
        getattr(btn, "_where", None)
        for btn in added
        if getattr(btn, "_where", None) is not None
    }
    assert button_wheres == {"12"}


# ---------------------------------------------------------------------------
# 7. Legacy YAML, Subentry Config Flow & Diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_yaml_loads_expose_section(hass: HomeAssistant, tmp_path):
    """Verify load_legacy_myhome_yaml parses and validates the top-level expose: block."""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(
        """
light:
  office:
    where: '11'
    name: Office Light
    dimmable: false
expose:
  - who: 1
    where: '99'
    entity_id: media_player.living_room_tv
  - who: 4
    where: '000'
    dimension: 0
    entity_id: sensor.outdoor_temp
""",
        encoding="utf-8",
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac": "00:03:50:aa:bb:cc", "host": "192.168.1.35", "port": 20000},
        options={"file_path": str(yaml_file)},
        unique_id="00:03:50:aa:bb:cc",
    )
    entry.add_to_hass(hass)

    platforms: dict = {"light": {}}
    expose_list: list = []
    await load_legacy_myhome_yaml(
        hass,
        entry,
        configured_platforms=platforms,
        configured_expose=expose_list,
    )
    assert "11" in platforms["light"]
    assert len(expose_list) == 2
    assert expose_list[0]["where"] == "99"
    assert expose_list[1]["where"] == "000"


@pytest.mark.asyncio
async def test_expose_subentry_flow_and_diagnostics(hass: HomeAssistant):
    """Test ExposeSubentryFlowHandler user/reconfigure steps and diagnostics export."""
    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {"who": 1, "where": "99", "entity_id": "media_player.living_room_tv"},
        ],
        subentries=[
            {
                "subentry_type": SUBENTRY_TYPE_EXPOSE,
                "subentry_id": "sub_1",
                "title": "WHO 1 / WHERE 98 (light.kitchen)",
                "unique_id": None,
                "data": {
                    "who": 1,
                    "where": "98",
                    "entity_id": "light.kitchen",
                    "on_states": ["on", "playing"],
                },
            }
        ],
    )
    hass.states.async_set("media_player.living_room_tv", "on")

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()
    entry.runtime_data.expose_manager = mgr

    # Supported subentry types includes SUBENTRY_TYPE_EXPOSE
    subentry_types = MyhomeFlowHandler.async_get_supported_subentry_types(entry)
    assert SUBENTRY_TYPE_EXPOSE in subentry_types
    assert subentry_types[SUBENTRY_TYPE_EXPOSE] is ExposeSubentryFlowHandler

    # Test ExposeSubentryFlowHandler user step directly
    flow = ExposeSubentryFlowHandler()
    flow.hass = hass
    flow.handler = (entry.entry_id, SUBENTRY_TYPE_EXPOSE)
    flow.context = {"source": "user"}

    form_res = await flow.async_step_user(None)
    assert form_res["type"] == FlowResultType.FORM
    assert form_res["step_id"] == "user"

    # Validation error branches in _validate_and_clean
    bad_who = await flow.async_step_user({CONF_WHO: "not_an_int"})
    assert bad_who["errors"][CONF_WHO] == "invalid_who"

    bad_dim_str = await flow.async_step_user(
        {CONF_WHO: "13", CONF_DIMENSION: "abc"}
    )
    assert bad_dim_str["errors"][CONF_DIMENSION] == "invalid_dimension"

    bad_who13_dim = await flow.async_step_user(
        {CONF_WHO: "13", CONF_DIMENSION: "99"}
    )
    assert bad_who13_dim["errors"][CONF_DIMENSION] == "invalid_dimension"

    missing_fields = await flow.async_step_user({CONF_WHO: "4", CONF_DIMENSION: "14"})
    assert missing_fields["errors"]["where"] == "where_required"
    assert missing_fields["errors"]["entity_id"] == "entity_required"
    assert missing_fields["errors"][CONF_DIMENSION] == "invalid_dimension"

    bad_who18_dim = await flow.async_step_user(
        {CONF_WHO: "18", "where": "51", "entity_id": "sensor.p", CONF_DIMENSION: "99"}
    )
    assert bad_who18_dim["errors"][CONF_DIMENSION] == "invalid_dimension"

    # Schema-level invalid_expose error (e.g. WHO=1 with non-point-to-point where="0")
    bad_schema = await flow.async_step_user(
        {
            CONF_WHO: "1",
            "where": "0",
            "entity_id": "light.test",
            "attribute": "brightness",
            "on_states": "on, playing",
            "value_template": "{{ value }}",
        }
    )
    assert bad_schema["errors"]["base"] == "invalid_expose"

    # Submitting duplicate binding (WHO=1 WHERE=99 already in entry) returns invalid_expose error
    dup_res = await flow.async_step_user(
        {
            CONF_WHO: "1",
            "where": "99",
            "entity_id": "switch.other",
            CONF_COOLDOWN: 0,
            CONF_PERIODIC_SEND: 0,
            CONF_RESPOND_TO_READ: True,
            CONF_BRIGHTNESS_STEP_PCT: 10,
        }
    )
    assert dup_res["type"] == FlowResultType.FORM
    assert dup_res["errors"]["base"] == "invalid_expose"

    # Submitting valid WHO=13 clock binding succeeds
    with patch.object(hass.config_entries, "async_schedule_reload"):
        clock_res = await flow.async_step_user(
            {
                CONF_WHO: "13",
                CONF_DIMENSION: "22",
                CONF_COOLDOWN: 1,
                CONF_PERIODIC_SEND: 3600,
                CONF_RESPOND_TO_READ: True,
                CONF_BRIGHTNESS_STEP_PCT: 10,
            }
        )
    assert clock_res["type"] == FlowResultType.CREATE_ENTRY
    assert "Bus Clock Sync" in clock_res["title"]

    # Submitting valid WHO=4 probe binding succeeds
    with patch.object(hass.config_entries, "async_schedule_reload") as mock_reload:
        ok_res = await flow.async_step_user(
            {
                CONF_WHO: "4",
                "where": "000",
                CONF_DIMENSION: 0,
                "entity_id": "sensor.outdoor_temp",
                CONF_COOLDOWN: 30,
                CONF_MIN_DELTA: 0.1,
                CONF_PERIODIC_SEND: 900,
                CONF_RESPOND_TO_READ: True,
                CONF_BRIGHTNESS_STEP_PCT: 10,
            }
        )
        assert mock_reload.called
    assert ok_res["type"] == FlowResultType.CREATE_ENTRY
    assert ok_res["data"]["who"] == 4
    assert ok_res["data"]["where"] == "000"
    assert ok_res["data"]["dimension"] == 0

    # Reconfigure step on existing subentry "sub_1"
    flow.context = {"source": "reconfigure", "subentry_id": "sub_1"}
    reconf_form = await flow.async_step_reconfigure(None)
    assert reconf_form["type"] == FlowResultType.FORM
    assert reconf_form["step_id"] == "reconfigure"

    # Reconfigure with validation error re-renders form with user_input merged
    reconf_err = await flow.async_step_reconfigure(
        {CONF_WHO: "1", "where": "", "entity_id": "light.kitchen"}
    )
    assert reconf_err["type"] == FlowResultType.FORM
    assert reconf_err["errors"]["where"] == "where_required"

    # Reconfigure keeping same (who=1, where="98") is allowed via exclude_subentry_id
    with patch.object(hass.config_entries, "async_schedule_reload"):
        reconf_ok = await flow.async_step_reconfigure(
            {
                CONF_WHO: "1",
                "where": "98",
                "entity_id": "light.kitchen",
                CONF_COOLDOWN: 2,
                CONF_PERIODIC_SEND: 0,
                CONF_RESPOND_TO_READ: True,
                CONF_BRIGHTNESS_STEP_PCT: 15,
            }
        )
    assert reconf_ok["type"] == FlowResultType.ABORT
    assert reconf_ok["reason"] == "reconfigure_successful"

    # Diagnostics includes expose telemetry
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert "expose" in diag
    assert len(diag["expose"]) == 2
    assert diag["expose"][0]["who"] == 1
    assert diag["expose"][0]["where"] == "99"
    assert diag["expose"][0]["current_ha_state"] == "on"

    await mgr.async_stop()


@pytest.mark.asyncio
async def test_expose_templates_attributes_defaults_and_unit_scaling(hass: HomeAssistant):
    """Test value_template, attribute extraction, unavailable default fallback, and WHO=18 kW/kWh scaling."""
    hass.states.async_set("sensor.grid_kw", "2.35", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.month_kwh", "120.5", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.partial_month_wh", "4500")
    hass.states.async_set("sensor.partial_day_wh", "890")
    hass.states.async_set("sensor.unavailable_temp", "unavailable")
    hass.states.async_set("climate.nest", "heat", {"current_temperature": 22.4})

    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {
                "who": 18,
                "where": "51",
                "dimension": 113,
                "entity_id": "sensor.grid_kw",
                "cooldown": 0,
            },
            {
                "who": 18,
                "where": "51",
                "dimension": 52,
                "entity_id": "sensor.month_kwh",
                "cooldown": 0,
            },
            {
                "who": 18,
                "where": "51",
                "dimension": 53,
                "entity_id": "sensor.partial_month_wh",
                "cooldown": 0,
            },
            {
                "who": 18,
                "where": "51",
                "dimension": 54,
                "entity_id": "sensor.partial_day_wh",
                "cooldown": 0,
            },
            {
                "who": 4,
                "where": "001",
                "entity_id": "sensor.unavailable_temp",
                "default": 19.5,
                "cooldown": 0,
            },
            {
                "who": 4,
                "where": "002",
                "entity_id": "climate.nest",
                "attribute": "current_temperature",
                "value_template": "{{ (value | float) - 0.4 }}",
                "cooldown": 0,
            },
        ],
    )

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()
    await mgr.async_sync_all()

    sent = [str(c.args[0]) for c in gw.send_status_request.call_args_list]
    assert "*#18*51*113*2350##" in sent
    assert "*#18*51*52*120500##" in sent
    assert "*#18*51*53*4500##" in sent
    assert "*#18*51*54*890##" in sent
    assert "*#4*001*0*0195##" in sent
    assert "*#4*002*0*0220##" in sent

    await mgr.async_stop()


@pytest.mark.asyncio
async def test_who1_dim_write_and_default_media_cover_stepping(hass: HomeAssistant):
    """Test WHO=1 Dimension 1 write (*#1*96*#1*180*0##) and default media_player/cover stepping."""
    hass.states.async_set("light.hue_lamp", "off", {"brightness": 0})
    hass.states.async_set("media_player.sonos", "paused")
    hass.states.async_set("cover.blind", "closed")
    calls = []

    async def _record_call(call):
        calls.append((call.domain, call.service, dict(call.data)))

    hass.services.async_register("light", "turn_on", _record_call)
    hass.services.async_register("light", "turn_off", _record_call)
    for svc in ("media_play", "volume_up", "volume_down"):
        hass.services.async_register("media_player", svc, _record_call)
    for svc in ("open_cover", "close_cover"):
        hass.services.async_register("cover", svc, _record_call)

    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {"who": 1, "where": "96", "entity_id": "light.hue_lamp", "attribute": "brightness"},
            {"who": 1, "where": "95", "entity_id": "media_player.sonos"},
            {"who": 1, "where": "94", "entity_id": "cover.blind"},
        ],
    )

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()

    # Dimension 1 write on bus (*#1*96*#1*180*0## -> 80%)
    await mgr.handle_bus_message(OWNMessage.parse("*#1*96*#1*180*0##"))
    assert calls[-1] == ("light", "turn_on", {"entity_id": "light.hue_lamp", "brightness_pct": 80})

    # Default WHO=1 ON on a paused media_player calls media_player.media_play
    await mgr.handle_bus_message(OWNMessage.parse("*1*1*95##"))
    assert calls[-1] == ("media_player", "media_play", {"entity_id": "media_player.sonos"})

    # Default WHO=1 step up/down on media_player calls volume_up/volume_down
    await mgr.handle_bus_message(OWNMessage.parse("*1*30*95##"))
    assert calls[-1] == ("media_player", "volume_up", {"entity_id": "media_player.sonos"})
    await mgr.handle_bus_message(OWNMessage.parse("*1*31*95##"))
    assert calls[-1] == ("media_player", "volume_down", {"entity_id": "media_player.sonos"})

    # Default WHO=1 step up/down on cover calls open_cover/close_cover
    await mgr.handle_bus_message(OWNMessage.parse("*1*30*94##"))
    assert calls[-1] == ("cover", "open_cover", {"entity_id": "cover.blind"})
    await mgr.handle_bus_message(OWNMessage.parse("*1*31*94##"))
    assert calls[-1] == ("cover", "close_cover", {"entity_id": "cover.blind"})

    await mgr.async_stop()


@pytest.mark.asyncio
async def test_tx_echo_guard_suppresses_command_translation_and_who2_premove_stop(hass: HomeAssistant):
    """Verify _tx_echo_guard suppresses MH202 1000#WHAT translation echoes and F454/MHS1 WHO=2 pre-move STOP."""
    hass.states.async_set("switch.virtual_relay", "off")
    hass.states.async_set("cover.pergola_somfy", "closed", {"current_position": 0})
    calls: list[tuple[str, str, dict]] = []

    async def _record_call(call):
        calls.append((call.domain, call.service, dict(call.data)))

    for svc in ("turn_on", "turn_off"):
        hass.services.async_register("switch", svc, _record_call)
    for svc in ("open_cover", "close_cover", "stop_cover"):
        hass.services.async_register("cover", svc, _record_call)

    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {"who": 1, "where": "99", "entity_id": "switch.virtual_relay", "cooldown": 0},
            {"who": 2, "where": "97", "entity_id": "cover.pergola_somfy", "cooldown": 0},
        ],
    )

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()

    # 1. WHO=1 state transitions to "on" -> transmits *1*1*99##
    hass.states.async_set("switch.virtual_relay", "on")
    await hass.async_block_till_done()
    assert [str(c.args[0]) for c in gw.send_status_request.call_args_list] == ["*1*1*99##"]

    # MH202 echoes *1*1000#1*99## on the event session -> must be suppressed by echo guard
    await mgr.handle_bus_message(OWNMessage.parse("*1*1000#1*99##"))
    assert len(calls) == 0

    # 2. WHO=2 state transitions to "opening" -> transmits *2*1*97##
    gw.send_status_request.reset_mock()
    hass.states.async_set("cover.pergola_somfy", "opening", {"current_position": 10})
    await hass.async_block_till_done()
    assert "*2*1*97##" in [str(c.args[0]) for c in gw.send_status_request.call_args_list]

    # F454 / MyHomeServer1 / F461 emit ['*2*1000#0*97##', '*2*1000#1*97##'] when *2*1*97## is sent
    await mgr.handle_bus_message(OWNMessage.parse("*2*1000#0*97##"))
    await mgr.handle_bus_message(OWNMessage.parse("*2*1000#1*97##"))
    assert len(calls) == 0

    # A subsequent real physical STOP button press (*2*0*97##) is NOT suppressed (pre-move STOP is one-shot)
    await mgr.handle_bus_message(OWNMessage.parse("*2*0*97##"))
    assert calls == [("cover", "stop_cover", {"entity_id": "cover.pergola_somfy"})]

    await mgr.async_stop()


@pytest.mark.asyncio
async def test_physical_button_press_cancels_pending_cooldown_timer(hass: HomeAssistant):
    """Verify an incoming bus command cancels any pending trailing-edge cooldown timer."""
    hass.states.async_set("switch.virtual_relay", "off")
    calls: list[tuple[str, str, dict]] = []

    async def _record_call(call):
        calls.append((call.domain, call.service, dict(call.data)))

    for svc in ("turn_on", "turn_off"):
        hass.services.async_register("switch", svc, _record_call)

    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {"who": 1, "where": "99", "entity_id": "switch.virtual_relay", "cooldown": 30},
        ],
    )

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()

    # First HA state change fires immediately, starting the 30s cooldown window
    hass.states.async_set("switch.virtual_relay", "on")
    await hass.async_block_till_done()
    # Second HA state change within cooldown schedules a trailing-edge timer
    hass.states.async_set("switch.virtual_relay", "off")
    await hass.async_block_till_done()
    assert (1, "99", None) in mgr._cooldown_unsubs

    # Physical button press *1*1000#0*99## arrives during cooldown -> cancels stale timer
    await mgr.handle_bus_message(OWNMessage.parse("*1*1000#0*99##"))
    assert (1, "99", None) not in mgr._cooldown_unsubs
    assert calls == [("switch", "turn_off", {"entity_id": "switch.virtual_relay"})]

    await mgr.async_stop()


@pytest.mark.asyncio
async def test_media_player_default_on_states_keeps_led_on_when_idle_or_paused(hass: HomeAssistant):
    """Verify media_player entities default to keeping the wall-switch LED ON while idle/paused/buffering."""
    hass.states.async_set("media_player.smart_tv", "idle")
    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {"who": 1, "where": "99", "entity_id": "media_player.smart_tv", "cooldown": 0},
        ],
    )

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()
    await mgr.async_sync_all()

    # "idle" maps to *1*1*99## (LED ON)
    assert [str(c.args[0]) for c in gw.send_status_request.call_args_list] == ["*1*1*99##"]

    # "standby" maps to *1*0*99## (LED OFF)
    gw.send_status_request.reset_mock()
    hass.states.async_set("media_player.smart_tv", "standby")
    await hass.async_block_till_done()
    assert [str(c.args[0]) for c in gw.send_status_request.call_args_list] == ["*1*0*99##"]

    await mgr.async_stop()


@pytest.mark.asyncio
async def test_legacy_yaml_named_slug_mapping_syntax(hass: HomeAssistant, tmp_path):
    """Verify load_legacy_myhome_yaml supports named-slug dict syntax for expose: without corrupting WHO=13."""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(
        """
expose:
  tv_knop:
    who: 1
    where: '99'
    entity_id: media_player.living_room_tv
  bus_klok:
    who: 13
    dimension: 22
""",
        encoding="utf-8",
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac": "00:03:50:aa:bb:cc", "host": "192.168.1.35", "port": 20000},
        options={"file_path": str(yaml_file)},
        unique_id="00:03:50:aa:bb:cc",
    )
    entry.add_to_hass(hass)

    platforms: dict = {}
    expose_list: list = []
    await load_legacy_myhome_yaml(
        hass,
        entry,
        configured_platforms=platforms,
        configured_expose=expose_list,
    )
    assert len(expose_list) == 2
    by_who = {item["who"]: item for item in expose_list}
    assert by_who[1]["where"] == "99"
    assert by_who[13]["where"] == ""
    assert by_who[13]["dimension"] == 22


@pytest.mark.asyncio
async def test_who18_dim1200_read_query_and_active_streaming_expiry(hass: HomeAssistant):
    """Test WHO=18 *#18*51*1200## status query and active streaming timer expiry notification."""
    hass.states.async_set("sensor.p1_power", "900")
    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {"who": 18, "where": "51", "dimension": 113, "entity_id": "sensor.p1_power", "cooldown": 0},
        ],
    )

    mgr = ExposeManager(hass, entry)
    await mgr.async_start()

    # Read query *#18*51*1200## while inactive -> replies *#18*51*1200#1*0##
    await mgr.handle_bus_message(OWNMessage.parse("*#18*51*1200##"))
    assert [str(c.args[0]) for c in gw.send.call_args_list] == ["*#18*51*1200#1*0##"]
    gw.send.reset_mock()

    # Activate streaming for 65s -> schedules expiry timer
    await mgr.handle_bus_message(OWNMessage.parse("*#18*51*#1200#1*65##"))
    assert (18, "51", 113) in mgr._stream_timers

    # Read query *#18*51*1200## while active -> replies *#18*51*1200#1*65##
    gw.send.reset_mock()
    await mgr.handle_bus_message(OWNMessage.parse("*#18*51*1200##"))
    assert [str(c.args[0]) for c in gw.send.call_args_list] == ["*#18*51*1200#1*65##"]

    await mgr.async_stop()


@pytest.mark.asyncio
async def test_shared_bus_secondary_gateway_delegation_and_phantom_suppression(hass: HomeAssistant):
    """Verify WHO=18 delegated to a secondary gateway uses secondary profile and suppresses phantoms."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from OWNd.profiles import F454Profile, F455Profile

    hass.states.async_set("sensor.p1_power", "1800")

    # Primary gateway is an F455 (supports_dimension_response_injection=False natively)
    gw_primary = _make_gateway(profile=F455Profile(), mac="00:03:50:11:11:11")
    gw_primary.delegated_away_whos = {18}
    entry_primary = _make_entry(
        hass,
        gateway=gw_primary,
        expose_list=[
            {"who": 18, "where": "51", "dimension": 113, "entity_id": "sensor.p1_power", "cooldown": 0},
        ],
    )

    # Secondary gateway is an F454 on the same bus with WHO=18 delegated to it
    gw_secondary = _make_gateway(profile=F454Profile(), mac="00:03:50:22:22:22")
    gw_secondary.bus_group = "00:03:50:11:11:11"
    gw_secondary.primary_gateway_mac = "00:03:50:11:11:11"
    gw_secondary.delegated_whos = {18}
    gw_secondary.is_follower = True
    gw_secondary.is_connected = True
    gw_primary._get_secondary_for_who = lambda w: gw_secondary if w == 18 else None

    entry_secondary = _make_entry(hass, gateway=gw_secondary, expose_list=[])

    mgr = ExposeManager(hass, entry_primary)
    entry_primary.runtime_data.expose_manager = mgr
    await mgr.async_start()
    await mgr.async_sync_all()

    # Because WHO=18 is delegated to the F454 secondary (which supports dimension response injection),
    # _effective_profile(18) allows the frame to be built and sent!
    assert [str(c.args[0]) for c in gw_primary.send_status_request.call_args_list] == [
        "*#18*51*113*1800##"
    ]

    # Bus query arriving on the secondary gateway's dispatcher signal is answered by primary's ExposeManager
    gw_primary.send.reset_mock()
    async_dispatcher_send(
        hass,
        f"myhome_message_{gw_secondary.mac}",
        OWNMessage.parse("*#18*51*113##"),
    )
    await hass.async_block_till_done()
    assert [str(c.args[0]) for c in gw_primary.send.call_args_list] == [
        "*#18*51*113*1800##"
    ]

    # Secondary gateway's PlatformDiscovery suppresses phantom sensor for primary's exposed WHO=18 WHERE=51
    added_sensors: list = []

    def _build_dummy(ctx):
        ent = MagicMock()
        ent.unique_id = f"{gw_secondary.mac}-{ctx.who}-{ctx.key}"
        ent._poll_on_add = False
        ent.async_on_remove = MagicMock()
        ent.handle_event = MagicMock()
        return ent

    sensor_disc_secondary = PlatformDiscovery(
        hass,
        entry_secondary,
        added_sensors.extend,
        platform="sensor",
        who="18",
        event_type=OWNEnergyEvent,
        build=_build_dummy,
    )
    sensor_disc_secondary.handle_message(OWNMessage.parse("*#18*51*113*1800##"))
    assert len(added_sensors) == 0

    await mgr.async_stop()


@pytest.mark.asyncio
async def test_gateway_reconnect_triggers_expose_sync_all(hass: HomeAssistant):
    """Verify MyHOMEGatewayHandler event-session reconnect schedules expose_manager.async_sync_all()."""
    from custom_components.myhome.gateway import MyHOMEGatewayHandler

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac": "00:03:50:aa:bb:cc", "host": "192.168.1.35", "port": 20000, "name": "MH202"},
        unique_id="00:03:50:aa:bb:cc",
    )
    entry.add_to_hass(hass)

    handler = MyHOMEGatewayHandler(hass, entry)
    mock_mgr = MagicMock()
    mock_mgr.async_sync_all = AsyncMock()
    entry.runtime_data = MyHOMERuntimeData(
        gateway=handler,
        platforms={},
        entities={},
        expose=[],
        expose_manager=mock_mgr,
    )

    # Initial connection before _initial_discovery_done is set -> does not call async_sync_all yet
    handler._on_event_connection_state_change(True)
    await hass.async_block_till_done()
    assert mock_mgr.async_sync_all.call_count == 0

    # Mark initial discovery done, simulate disconnect and reconnect
    handler._initial_discovery_done.set()
    with patch("custom_components.myhome.gateway.async_call_later", return_value=MagicMock()):
        handler._on_event_connection_state_change(False)
        handler._on_event_connection_state_change(True)
    await hass.async_block_till_done()
    assert mock_mgr.async_sync_all.call_count == 1


@pytest.mark.asyncio
async def test_expose_edge_cases_and_timers(hass: HomeAssistant):
    """Exercise all remaining helper, coercion, timer, echo-guard, and bus-handler branches in expose.py."""
    import time
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    from custom_components.myhome.expose import (
        ExposeBinding,
        _coerce_action_list,
        _coerce_on_states,
        is_exposed_on_bus,
    )

    # 1. Coercion helpers & is_exposed_on_bus edge cases
    assert _coerce_action_list([{"service": "light.turn_on"}, "not_a_dict"]) == [
        {"service": "light.turn_on"}
    ]
    assert _coerce_action_list("invalid") is None
    assert _coerce_on_states("on, playing") == ("on", "playing")
    assert _coerce_on_states(123) == ("on", "playing", "open", "unlocked")

    assert is_exposed_on_bus(hass, None, 1, "99") is False
    dummy_entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id="dummy_1")
    dummy_entry.add_to_hass(hass)
    assert is_exposed_on_bus(None, dummy_entry, 1, "99") is False
    assert is_exposed_on_bus(hass, dummy_entry, 1, "99") is False

    # 2. ExposeBinding.from_dict defaults for WHO=18, WHO=13, periodic_send, bidirectional
    b18 = ExposeBinding.from_dict({"who": 18, "where": "51", "entity_id": "sensor.p"})
    assert b18.dimension == 113
    b13 = ExposeBinding.from_dict({"who": 13})
    assert b13.dimension == 22
    b4 = ExposeBinding.from_dict(
        {
            "who": 4,
            "where": "000",
            "entity_id": "sensor.t",
            "periodic_send": 60,
            "bidirectional": False,
        }
    )
    assert b4.periodic_send == 60.0
    assert b4.bidirectional is False

    # 3. ExposeManager init fallbacks (config_entry.data["expose"] and empty) & invalid bindings
    gw = _make_gateway()
    entry_data_fallback = MockConfigEntry(
        domain=DOMAIN,
        data={"expose": [{"who": 13}, {"who": "invalid"}]},
        subentries_data=[
            {
                "subentry_type": "other_type",
                "subentry_id": "sub_other",
                "title": "Other",
                "unique_id": None,
                "data": {},
            },
            {
                "subentry_type": SUBENTRY_TYPE_EXPOSE,
                "subentry_id": "sub_bad",
                "title": "Bad",
                "unique_id": None,
                "data": {"who": "not_int"},
            },
        ],
        unique_id="fallback_1",
    )
    mgr_fb = ExposeManager(hass, entry_data_fallback, gateway=gw)
    assert len(mgr_fb.bindings) == 1
    assert mgr_fb.is_exposed("bad_who", "99") is False
    assert mgr_fb.is_exposed(1, None) is False

    entry_empty = MockConfigEntry(domain=DOMAIN, data={}, unique_id="empty_1")
    mgr_empty = ExposeManager(hass, entry_empty, gateway=gw)
    assert mgr_empty.bindings == []
    await mgr_empty.async_start()  # No-op when bindings is empty

    # 4. State listener with removed state (new_state=None), periodic callback, cooldown & streaming timers
    hass.states.async_set("sensor.power_stream", "500")
    entry_timers = _make_entry(
        hass,
        gateway=gw,
        expose_list=[
            {
                "who": 18,
                "where": "51",
                "dimension": 113,
                "entity_id": "sensor.power_stream",
                "cooldown": 10.0,
                "periodic_send": 60.0,
            },
        ],
    )
    mgr_t = ExposeManager(hass, entry_timers, gateway=gw)
    await mgr_t.async_start()
    await mgr_t.async_start()  # Second start is a no-op

    # Remove entity state -> triggers _on_state_change with new_state=None
    hass.states.async_remove("sensor.power_stream")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.power_stream", "600")
    await hass.async_block_till_done()

    # Periodic callback direct invocation
    periodic_cb = mgr_t._make_periodic_callback(mgr_t.bindings[0])
    await periodic_cb(dt_util.now())

    # Active stream shortens effective_cooldown to min(cooldown, 2.0) and schedules trailing timer
    b_stream = mgr_t.bindings[0]
    b_stream.stream_expires_at = time.monotonic() + 120.0
    b_stream.last_sent_at = time.monotonic()
    captured_trailing = []

    def _capture_call_later(_hass, _delay, action):
        captured_trailing.append(action)
        return MagicMock()

    with patch("custom_components.myhome.expose.async_call_later", side_effect=_capture_call_later):
        mgr_t._schedule_binding_send(b_stream)
        # Second call while timer already present in _cooldown_timers is a no-op
        mgr_t._schedule_binding_send(b_stream)
    assert len(captured_trailing) == 1
    captured_trailing[0](dt_util.now())
    await hass.async_block_till_done()
    await mgr_t.async_stop()

    # 5. Echo guard expiration and WHO=2 direct move echo clearing pre-move STOP guard
    mgr_t._tx_echo_guard["*1*1*99##"] = time.monotonic() - 5.0
    mgr_t._pre_move_stop_guard[(2, "97")] = time.monotonic() - 5.0
    mgr_t._record_echo_guard("*1*0*99##")
    assert "*1*1*99##" not in mgr_t._tx_echo_guard
    assert (2, "97") not in mgr_t._pre_move_stop_guard

    mgr_t._pre_move_stop_guard[(2, "97")] = time.monotonic() - 1.0
    assert mgr_t._is_echo_guarded(OWNMessage.parse("*2*0*97##")) is False
    assert (2, "97") not in mgr_t._pre_move_stop_guard

    mgr_t._pre_move_stop_guard[(2, "97")] = time.monotonic() + 5.0
    mgr_t._is_echo_guarded(OWNMessage.parse("*2*1*97##"))
    assert (2, "97") not in mgr_t._pre_move_stop_guard

    # 6. _effective_profile fallback when profile is None
    gw_no_prof = _make_gateway()
    gw_no_prof.profile = None
    gw_no_prof._gateway = None
    mgr_np = ExposeManager(hass, entry_empty, gateway=gw_no_prof)
    assert mgr_np._effective_profile(4) is None

    # 7. _resolve_raw_value and _build_frames edge cases across WHO=1, 2, 4, 18, 13, 99
    hass.states.async_set("light.unavail_no_def", "unavailable")
    hass.states.async_set("light.unavail_bool_def", "unavailable")
    hass.states.async_set("light.attr_with_def", "on", {})
    hass.states.async_set("light.attr_no_def", "on", {})
    hass.states.async_set("light.bad_tpl", "on", {"brightness": 128})
    hass.states.async_set("light.bad_b_val", "on", {"brightness": "not_num"})
    hass.states.async_set("light.tpl_b_val", "on", {"brightness": 200})

    # Unavailable without default -> (None, state) -> [], None
    b_u1 = ExposeBinding(who=1, where="11", entity_id="light.unavail_no_def")
    assert mgr_t._build_frames(b_u1, force=True, is_reply=False) == ([], None)

    # Unavailable with boolean default=True -> raw is bool True
    b_u2 = ExposeBinding(who=1, where="11", entity_id="light.unavail_bool_def", default=True)
    frames_u2, val_u2 = mgr_t._build_frames(b_u2, force=True, is_reply=False)
    assert [str(f) for f in frames_u2] == ["*1*1*11##"]
    assert val_u2 == (True, None)
    # Dedup when force=False and last_sent_value matches
    b_u2.last_sent_value = val_u2
    assert mgr_t._build_frames(b_u2, force=False, is_reply=False) == ([], None)

    # Missing attribute with default and without default
    b_attr_def = ExposeBinding(
        who=1, where="11", entity_id="light.attr_with_def", attribute="missing", default="on"
    )
    assert mgr_t._resolve_raw_value(b_attr_def)[0] == "on"
    b_attr_nodef = ExposeBinding(
        who=1, where="11", entity_id="light.attr_no_def", attribute="missing"
    )
    assert mgr_t._resolve_raw_value(b_attr_nodef)[0] is None

    # Broken template logs warning and returns None
    b_bad_tpl = ExposeBinding(
        who=1, where="11", entity_id="light.bad_tpl", value_template="{{ 1 / 0 }}"
    )
    assert mgr_t._resolve_raw_value(b_bad_tpl)[0] is None

    # WHO=1 brightness with value_template (0..100 scale) and non-numeric brightness fallback
    b_tpl_b = ExposeBinding(
        who=1,
        where="11",
        entity_id="light.tpl_b_val",
        attribute="brightness",
        dimension=1,
        value_template="{{ 75 }}",
    )
    frames_tb, _ = mgr_t._build_frames(b_tpl_b, force=True, is_reply=False)
    assert [str(f) for f in frames_tb] == ["*1*1*11##", "*#1*11*1*175*0##"]

    b_bad_b = ExposeBinding(
        who=1, where="11", entity_id="light.bad_b_val", attribute="brightness", dimension=1
    )
    frames_bb, _ = mgr_t._build_frames(b_bad_b, force=True, is_reply=False)
    assert [str(f) for f in frames_bb] == ["*1*0*11##", "*#1*11*1*100*0##"]

    # WHO=2 numeric state without current_position attribute, "closing" state, dedup, and dimension=10
    hass.states.async_set("sensor.shutter_pct", "55")
    b_w2_num = ExposeBinding(who=2, where="21", entity_id="sensor.shutter_pct", dimension=10)
    frames_w2n, val_w2n = mgr_t._build_frames(b_w2_num, force=True, is_reply=False)
    assert [str(f) for f in frames_w2n] == ["*2*0*21##", "*#2*21*10*10*55*0*0##"]
    b_w2_num.last_sent_value = val_w2n
    assert mgr_t._build_frames(b_w2_num, force=False, is_reply=False) == ([], None)

    hass.states.async_set("cover.closing_cov", "closing", {"current_position": 40})
    b_w2_cl = ExposeBinding(who=2, where="21", entity_id="cover.closing_cov", dimension=10)
    frames_w2c, _ = mgr_t._build_frames(b_w2_cl, force=True, is_reply=False)
    assert [str(f) for f in frames_w2c] == ["*2*2*21##", "*#2*21*10*12*40*0*0##"]

    # WHO=4 & WHO=18 profile gate (supports_dimension_response_injection=False), non-numeric values, min_delta / equality dedup, unknown dim
    gw_f455 = _make_gateway(profile=SimpleNamespace(supports_dimension_response_injection=False))
    mgr_f455 = ExposeManager(hass, entry_empty, gateway=gw_f455)
    hass.states.async_set("sensor.temp_ok", "21.5")
    hass.states.async_set("sensor.temp_bad", "not_a_float")
    b_w4 = ExposeBinding(who=4, where="000", entity_id="sensor.temp_ok")
    assert mgr_f455._build_frames(b_w4, force=True, is_reply=False) == ([], None)
    assert mgr_t._build_frames(
        ExposeBinding(who=4, where="000", entity_id="sensor.temp_bad"),
        force=True,
        is_reply=False,
    ) == ([], None)
    b_w4.last_sent_value = 21.5
    assert mgr_t._build_frames(b_w4, force=False, is_reply=False) == ([], None)

    b_w18 = ExposeBinding(who=18, where="51", dimension=113, entity_id="sensor.temp_ok")
    assert mgr_f455._build_frames(b_w18, force=True, is_reply=False) == ([], None)
    assert mgr_t._build_frames(
        ExposeBinding(who=18, where="51", dimension=113, entity_id="sensor.temp_bad"),
        force=True,
        is_reply=False,
    ) == ([], None)
    b_w18_delta = ExposeBinding(
        who=18, where="51", dimension=113, entity_id="sensor.temp_ok", min_delta=50.0
    )
    b_w18_delta.last_sent_value = 20.0
    assert mgr_t._build_frames(b_w18_delta, force=False, is_reply=False) == ([], None)
    b_w18.last_sent_value = 21.5
    assert mgr_t._build_frames(b_w18, force=False, is_reply=False) == ([], None)
    b_w18_unk = ExposeBinding(who=18, where="51", dimension=999, entity_id="sensor.temp_ok")
    assert mgr_t._build_frames(b_w18_unk, force=True, is_reply=False) == ([], None)

    # WHO=13 dim 22 reply, dim 0 & dim 1 non-reply, and unsupported WHO=99
    b_w13_22 = ExposeBinding(who=13, where="", dimension=22)
    frames_13_r22, _ = mgr_t._build_frames(
        b_w13_22, force=True, is_reply=True, query_dimension=22
    )
    assert len(frames_13_r22) == 1
    assert str(frames_13_r22[0]).startswith("*#13**22*")
    frames_13_s0, _ = mgr_t._build_frames(
        ExposeBinding(who=13, where="", dimension=0), force=True, is_reply=False
    )
    assert str(frames_13_s0[0]).startswith("*#13**#0*")
    frames_13_s1, _ = mgr_t._build_frames(
        ExposeBinding(who=13, where="", dimension=1), force=True, is_reply=False
    )
    assert str(frames_13_s1[0]).startswith("*#13**#1*")
    assert mgr_t._build_frames(
        ExposeBinding(who=99, where="11", default="1"), force=True, is_reply=False
    ) == ([], None)

    # 8. _async_run_actions & _async_execute_who1_* edge cases
    calls = []

    async def _rec(call):
        calls.append((call.domain, call.service, dict(call.data)))

    hass.services.async_register("switch", "turn_on", _rec)
    hass.services.async_register("switch", "turn_off", _rec)
    await mgr_t._async_run_actions(
        [
            {"service": "invalid_no_dot"},
            {"service": "switch.turn_on", "entity_id": "switch.direct_ent"},
        ]
    )
    assert calls[-1] == ("switch", "turn_on", {"entity_id": "switch.direct_ent"})

    b_no_ent = ExposeBinding(who=1, where="99", entity_id=None)
    await mgr_t._async_execute_who1_on(b_no_ent)
    await mgr_t._async_execute_who1_off(b_no_ent)
    await mgr_t._async_execute_who1_step(b_no_ent, up=True)

    # 9. Bus message handler edge cases: unsupported WHO, respond_to_read=False, bidirectional=False,
    #    WHO=18 streaming timer replacement, expiry callback, duration=0 stop, WHO=1 preset on non-light,
    #    and WHO=2 custom turn_on_action / turn_off_action
    hass.states.async_set("switch.non_light", "off")
    hass.states.async_set("cover.custom_shutter", "closed", {"current_position": 0})
    b_noread = ExposeBinding(
        who=18,
        where="52",
        dimension=113,
        entity_id="sensor.temp_ok",
        respond_to_read=False,
    )
    b_stream51 = ExposeBinding(
        who=18,
        where="51",
        dimension=113,
        entity_id="sensor.temp_ok",
        respond_to_read=True,
    )
    b_nobidir = ExposeBinding(
        who=1,
        where="91",
        entity_id="switch.non_light",
        bidirectional=False,
    )
    b_switch92 = ExposeBinding(
        who=1,
        where="92",
        entity_id="switch.non_light",
        bidirectional=True,
    )
    b_shutter93 = ExposeBinding(
        who=2,
        where="93",
        entity_id="cover.custom_shutter",
        bidirectional=True,
        turn_on_action=[{"service": "switch.turn_on", "entity_id": "switch.shutter_up"}],
        turn_off_action=[{"service": "switch.turn_off", "entity_id": "switch.shutter_down"}],
    )
    mgr_t.bindings = [b_noread, b_stream51, b_nobidir, b_switch92, b_shutter93]

    # Unsupported WHO (WHO=9)
    await mgr_t.handle_bus_message(OWNMessage.parse("*#9*1*1##"))
    # Status/dim read & dim 1200 write when respond_to_read=False
    await mgr_t.handle_bus_message(OWNMessage.parse("*#18*52*113##"))
    await mgr_t.handle_bus_message(OWNMessage.parse("*#18*52*#1200#1*5##"))

    # WHO=18 dim 1200 write with synthetic message lacking stream_duration attribute, replacing existing timer,
    # firing expiry callback, and stopping with duration=0
    stream_callbacks = []

    def _capture_stream_timer(_hass, _delay, cb):
        stream_callbacks.append(cb)
        return MagicMock()

    fake_dim1200_msg = SimpleNamespace(
        who=18,
        where="51",
        is_status_request=False,
        is_dimension_request=False,
        is_dimension_writing=True,
        dimension=1200,
        stream_duration=None,
        dimension_value=["2"],
        _dimension_param=["1"],
    )
    with patch("custom_components.myhome.expose.async_call_later", side_effect=_capture_stream_timer):
        await mgr_t.handle_bus_message(fake_dim1200_msg)  # starts stream timer 1
        await mgr_t.handle_bus_message(fake_dim1200_msg)  # cancels timer 1, starts timer 2
    assert len(stream_callbacks) == 2
    stream_callbacks[-1](dt_util.now())  # fire _on_stream_expired
    await hass.async_block_till_done()
    assert b_stream51.stream_expires_at == 0.0

    # Stop streaming with duration=0 (*#18*51*#1200#1*0##)
    await mgr_t.handle_bus_message(OWNMessage.parse("*#18*51*#1200#1*0##"))
    assert b_stream51.stream_expires_at == 0.0

    # Command on bidirectional=False binding is ignored
    await mgr_t.handle_bus_message(OWNMessage.parse("*1*1*91##"))

    # WHO=1 preset level (*1*5*92##) on a switch entity falls back to _async_execute_who1_on
    await mgr_t.handle_bus_message(OWNMessage.parse("*1*5*92##"))
    assert calls[-1] == ("switch", "turn_on", {"entity_id": "switch.non_light"})

    # WHO=2 UP/DOWN on shutter with custom turn_on_action / turn_off_action
    await mgr_t.handle_bus_message(OWNMessage.parse("*2*1*93##"))
    assert calls[-1] == ("switch", "turn_on", {"entity_id": "switch.shutter_up"})
    await mgr_t.handle_bus_message(OWNMessage.parse("*2*2*93##"))
    assert calls[-1] == ("switch", "turn_off", {"entity_id": "switch.shutter_down"})

