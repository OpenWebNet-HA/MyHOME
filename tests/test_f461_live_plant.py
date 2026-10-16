"""A physical BTicino F461 (firmware 2.0.11) against MyHOME, captured 2026-10-15.

The frames are in ``tests/fixtures/traces/f461_live_2026-10-15/myhome_trace_F461_live_2026-10-15.json``
(README there: plant and file format). Plant: F411 lights at WHERE 01 and 04, standalone zone 2
with a heating valve (actuator 1) and a cooling fan coil (actuator 2), no central unit.

What the capture changed in MyHOME:

1. ``supports_zone_status`` was False for an F461 (#674 generalised an F455 trace). This F461
   NACKs the sweep ``*#4*0##`` and answers ``*#4*2##`` in 1.3 s with five frames; its
   thermostat pushes nothing on its own (one humidity frame in 7 minutes of listening), so
   without the poll the zone never gets a state.
2. ``set_hvac_mode(AUTO)`` sent ``*4*311*#2##``: ACKed, ignored (a central-unit command on a
   plant without a central unit; ``*4*311*2##`` is NACKed). A standalone zone without a
   central unit now leaves OFF the way the plant accepts: a generic setpoint write.
3. Dimension 19 tells which side of a zone is a fan coil (``*#4*2*19*5*1##``: cooling fan
   coil off, heating valve on). The fan speed request ``*#4*2*11##`` is NACKed in heating
   season and answered in cooling; the poll now asks only in the fan coil's season.
4. ``myhome.send_message`` refuses WHO 4 dimension 22 requests: ``*#4*2*22##`` killed the
   gateway's thermostat handler and rebooted the gateway three minutes later, three times.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import CONF_NAME
from OWNd.message import OWNEvent, OWNMessage

from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, CONF_WHERE, DOMAIN
from tests.conftest import attach_platform, attach_runtime

MAC = "00:03:50:00:04:97"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "f461_live_2026-10-15"
    / "myhome_trace_F461_live_2026-10-15.json"
)


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    assert FIXTURE.is_file(), FIXTURE
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data["rows"]


def _gateway(model: str = "F461") -> MagicMock:
    gw = MagicMock()
    gw.mac = MAC
    gw.unique_id = MAC
    gw.log_id = f"[{model} live]"
    gw.model = model
    gw.has_central_unit = False
    gw.supports_zone_status = model.upper() == "F461"
    gw.initial_discovery_pending = False
    gw.is_connected = True
    gw.sent: list[str] = []

    async def _send(cmd, *a, **k):
        gw.sent.append(str(cmd))
        return True

    gw.send = AsyncMock(side_effect=_send)
    gw.send_status_request = AsyncMock(side_effect=_send)
    return gw


async def _plant(hass, gw):
    from custom_components.myhome.climate import async_setup_entry as climate_setup
    from custom_components.myhome.light import async_setup_entry as light_setup

    hass.data = {
        DOMAIN: {
            MAC: {
                CONF_ENTITY: gw,
                CONF_PLATFORMS: {
                    "light": {
                        "office_light": {CONF_WHERE: "01", CONF_NAME: "Office Light", "model": "F411"},
                        "bath_light": {CONF_WHERE: "04", CONF_NAME: "Bath Light", "model": "F411"},
                    },
                    "climate": {
                        "thermo_office": {
                            "who": "4", "zone": "2", "name": "Thermo office",
                            "heat": True, "cool": True, "standalone": True, "central": False,
                            "manufacturer": "BTicino", "model": "F461",
                        }
                    },
                },
            }
        }
    }
    entry = MagicMock()
    entry.data = {"mac": MAC}
    entry.entry_id = "f461_live"
    entry.options = {}
    attach_runtime(hass, entry, MAC, gw)
    lights, climates = [], []
    with patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]), \
         patch("custom_components.myhome.discovery.er.async_get", return_value=MagicMock()):
        await light_setup(hass, entry, lights.extend)
        await climate_setup(hass, entry, climates.extend)
    for e in lights + climates:
        e.hass = hass
        e.entity_id = f"{'climate' if e in climates else 'light'}.{e._device_id}"
        e.async_write_ha_state = MagicMock()
        e.async_schedule_update_ha_state = MagicMock()
        attach_platform(e, entry)
    return {e._where: e for e in lights + climates}


def _replay(rows: list[dict], run: str, entities: dict) -> None:
    for r in rows:
        if r["run"] != run or r["dir"] != "rx" or r.get("session") != "evt":
            continue
        msg = OWNMessage.parse(r["frame"])
        if msg is None:
            continue
        who, where = getattr(msg, "who", None), getattr(msg, "where", None)
        if who == 1 and where in entities:
            entities[where].handle_event(msg)
        elif who == 4 and getattr(msg, "zone", None) == 2:
            entities["2"].handle_event(msg)


# --------------------------------------------------------------- 1. the zone is polled on an F461

def test_f461_supports_zone_status_and_f455_does_not() -> None:
    from custom_components.myhome.gateway import MyHOMEGatewayHandler

    handler = MagicMock(spec=MyHOMEGatewayHandler)
    for model, expected in (("F461", True), ("f461", True), ("F455", False), ("F454", False), ("MH200N", False), ("MyHomeServer1", True)):
        handler.model = model
        assert MyHOMEGatewayHandler.supports_zone_status.fget(handler) is expected, model


def test_capture_backs_the_poll(rows: list[dict]) -> None:
    answered = [r for r in rows if r["dir"] == "result" and r["frame"] == "*#4*2##" and r["ack"]]
    assert answered and len(answered[0]["replies"]) == 5
    sweep = [r for r in rows if r["dir"] == "result" and r["frame"] == "*#4*0##"]
    assert sweep and not any(r["ack"] for r in sweep)
    listening = [r["frame"] for r in rows if r["run"] == "probe_monitor5min" and r["dir"] == "rx" and r["frame"].startswith("*#4*")]
    assert listening == ["*#4*2*60*34##"], "the thermostat pushed only one humidity frame in 300 s"


async def test_zone_update_on_f461_sends_the_status_request(hass) -> None:
    gw = _gateway("F461")
    zone = (await _plant(hass, gw))["2"]
    assert zone._poll_on_add is True
    await zone.async_update()
    assert gw.sent == ["*#4*2##"]


async def test_zone_update_on_f455_still_sends_nothing(hass) -> None:
    gw = _gateway("F455")
    zone = (await _plant(hass, gw))["2"]
    assert zone._poll_on_add is False
    await zone.async_update()
    assert gw.sent == []


async def test_replay_fills_the_zone_from_the_poll_replies(hass, rows: list[dict]) -> None:
    ents = await _plant(hass, _gateway())
    zone, light01, light04 = ents["2"], ents["01"], ents["04"]
    _replay(rows, "run1_full", ents)
    assert zone.current_temperature == 24.1
    assert zone.target_temperature == 24.5
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.hvac_action == HVACAction.HEATING  # actuator 1 (valve) on
    assert zone.extra_state_attributes["season"] == "heating"
    assert light01.is_on is False and light04.is_on is False


# --------------------------------------------------------------- 2. AUTO on a standalone zone

async def test_auto_on_a_standalone_zone_without_central_writes_the_setpoint(hass) -> None:
    gw = _gateway()
    zone = (await _plant(hass, gw))["2"]
    zone.handle_event(OWNEvent.parse("*#4*2*14*0245*3##"))
    zone.handle_event(OWNEvent.parse("*4*303*2##"))
    assert zone.hvac_mode == HVACMode.OFF
    gw.sent.clear()
    await zone.async_set_hvac_mode(HVACMode.AUTO)
    assert gw.sent == ["*#4*2*#14*0245*3##"]
    # the plant answers a setpoint write with its season frame, which turns the zone on
    zone.handle_event(OWNEvent.parse("*4*0*2##"))
    assert zone.hvac_mode == HVACMode.COOL


async def test_auto_without_a_known_setpoint_sends_nothing(hass) -> None:
    gw = _gateway()
    zone = (await _plant(hass, gw))["2"]
    await zone.async_set_hvac_mode(HVACMode.AUTO)
    assert gw.sent == []


async def test_auto_behind_a_central_unit_keeps_the_central_form(hass) -> None:
    gw = _gateway()
    gw.has_central_unit = True
    zone = (await _plant(hass, gw))["2"]
    zone._standalone_config = None  # not configured: follows the plant
    zone.handle_event(OWNEvent.parse("*#4*2*14*0245*3##"))
    await zone.async_set_hvac_mode(HVACMode.AUTO)
    assert gw.sent == ["*4*311*#2##"]


def test_capture_shows_311_is_ignored_and_the_setpoint_write_acts(rows: list[dict]) -> None:
    auto = [r for r in rows if r["dir"] == "tx" and "utc" in r and r["frame"] == "*4*311*#2##"]
    assert auto and auto[0]["ack"] and not any(e.startswith("*4*311") for e in auto[0]["events_after"])
    plain = [r for r in rows if r["dir"] == "result" and r["frame"] == "*4*311*2##"]
    assert plain and not plain[0]["ack"]
    # run3: *4*303*2## (OFF) ... restore *#4*2*#14*0200*3## -> snapshot shows the season frame again
    results = [r for r in rows if r["run"] == "run3_climate" and r["dir"] == "result"]
    i_restore = next(i for i, r in enumerate(results) if r["frame"] == "*#4*2*#14*0200*3##")
    after = next(r for r in results[i_restore:] if r["frame"] == "*#4*2##")
    assert "*4*0*2##" in after["replies"] and not any(x.startswith("*4*303") for x in after["replies"])


# --------------------------------------------------------------- 3. fan coil per season

async def test_dimension_19_records_which_side_is_a_fan_coil(hass) -> None:
    gw = _gateway()
    zone = (await _plant(hass, gw))["2"]
    assert "fan_coil_heating" not in zone.extra_state_attributes
    zone.handle_event(OWNEvent.parse("*#4*2*19*5*1##"))
    attrs = zone.extra_state_attributes
    assert attrs["fan_coil_cooling"] is True and attrs["fan_coil_heating"] is False
    assert zone.hvac_action == HVACAction.HEATING  # heating valve on even with cooling fan off (OWNd <= 2.0.0b11)
    zone.handle_event(OWNEvent.parse("*#4*2*19*0*1##"))  # a valve-only plant
    attrs = zone.extra_state_attributes
    assert attrs["fan_coil_cooling"] is False and attrs["fan_coil_heating"] is False


async def test_restore_last_state_preserves_season_and_fan_coil_sides(hass) -> None:
    from homeassistant.core import State

    gw = _gateway()
    zone = (await _plant(hass, gw))["2"]
    await zone.async_restore_last_state(
        State(
            "climate.thermo_office",
            HVACMode.HEAT,
            {
                "temperature": 21.5,
                "fan_mode": "auto",
                "season": "heating",
                "fan_coil_heating": False,
                "fan_coil_cooling": True,
            },
        )
    )
    attrs = zone.extra_state_attributes
    assert attrs["season"] == "heating"
    assert attrs["fan_coil_heating"] is False
    assert attrs["fan_coil_cooling"] is True
    gw.sent.clear()
    await zone.async_update()
    assert gw.sent == ["*#4*2##"], "restored heating season + cooling-only fan coil skips *#4*2*11##"


async def test_fan_speed_is_polled_only_in_the_fan_coil_season(hass) -> None:
    gw = _gateway()
    zone = (await _plant(hass, gw))["2"]
    zone.handle_event(OWNEvent.parse("*#4*2#2*20*5##"))  # fan coil actuator seen: fan enabled
    assert zone._fan is True
    zone.handle_event(OWNEvent.parse("*#4*2*19*5*1##"))
    zone.handle_event(OWNEvent.parse("*4*1*2##"))  # heating season
    gw.sent.clear()
    await zone.async_update()
    assert gw.sent == ["*#4*2##"], "no *#4*2*11## in heating season (the F461 NACKs it)"
    zone.handle_event(OWNEvent.parse("*4*0*2##"))  # cooling season
    gw.sent.clear()
    await zone.async_update()
    assert gw.sent == ["*#4*2##", "*#4*2*11##"]


async def test_legacy_ownd_mode_event_extracts_season_fallback(hass) -> None:
    """Older OWNd releases decode *4*1*Z## / *4*0*Z## as MESSAGE_TYPE_MODE."""
    from OWNd.message import (
        CLIMATE_MODE_COOL,
        CLIMATE_MODE_HEAT,
        MESSAGE_TYPE_MODE,
    )

    from custom_components.myhome.climate import SEASON_HEATING

    gw = _gateway()
    zone = (await _plant(hass, gw))["2"]

    # Emulate older OWNd delivering *4*1*2## as MESSAGE_TYPE_MODE
    ev_heat = MagicMock()
    ev_heat.message_type = MESSAGE_TYPE_MODE
    ev_heat.mode = CLIMATE_MODE_HEAT
    ev_heat.__str__ = MagicMock(return_value="*4*1*2##")
    ev_heat.human_readable_log = "Zone 2 mode heat"
    zone.handle_event(ev_heat)
    assert zone._season == SEASON_HEATING

    # Emulate older OWNd delivering *4*0*2## as MESSAGE_TYPE_MODE
    ev_cool = MagicMock()
    ev_cool.message_type = MESSAGE_TYPE_MODE
    ev_cool.mode = CLIMATE_MODE_COOL
    ev_cool.__str__ = MagicMock(return_value="*4*0*2##")
    ev_cool.human_readable_log = "Zone 2 mode cool"
    zone.handle_event(ev_cool)
    assert zone._season == "cooling"


async def test_fan_speed_is_polled_while_the_sides_are_unknown(hass) -> None:
    gw = _gateway()
    zone = (await _plant(hass, gw))["2"]
    zone.handle_event(OWNEvent.parse("*#4*2#2*20*5##"))
    zone.handle_event(OWNEvent.parse("*4*1*2##"))
    gw.sent.clear()
    await zone.async_update()
    assert gw.sent == ["*#4*2##", "*#4*2*11##"]


def test_capture_shows_the_fan_speed_request_depends_on_the_season(rows: list[dict]) -> None:
    probes = [r for r in rows if r["run"] == "probe_fancoil" and r["dir"] == "tx" and "utc" in r]
    acks = [(r["frame"], r["ack"]) for r in probes]
    assert acks == [
        ("*#4*2*19##", True), ("*#4*2*20##", True), ("*#4*2*11##", False),   # heating
        ("*4*0*2##", True), ("*#4*2*19##", True), ("*#4*2*20##", True), ("*#4*2*11##", True),  # cooling
        ("*#4*2*#14*0180*3##", True), ("*#4*2*19##", True), ("*#4*2*20##", True),
        ("*#4*2*#14*0245*3##", True), ("*4*1*2##", True), ("*#4*2*19##", True), ("*#4*2*20##", True),
        ("*#4*2*11##", False), ("*#4*2##", True),
    ]
    assert probes[0]["replies"] == ["*#4*2*19*5*1##"]
    assert probes[8]["replies"] == ["*#4*2*19*8*0##"]


async def test_replay_of_the_fancoil_probe(hass, rows: list[dict]) -> None:
    ents = await _plant(hass, _gateway())
    zone = ents["2"]
    _replay(rows, "probe_fancoil", ents)
    attrs = zone.extra_state_attributes
    assert attrs["fan_coil_cooling"] is True and attrs["fan_coil_heating"] is False
    assert zone._fan is True
    assert zone.hvac_action == HVACAction.HEATING  # ends in heating season, valve on


# --------------------------------------------------------------- 4. dimension 22 guard

@pytest.mark.parametrize("frame", ["*#4*2*22##", "*#4*#0*22##", "*#4*2*#22*3**1*1##"])
async def test_send_message_refuses_who4_dimension_22(hass, attach_gateway, frame: str, caplog) -> None:
    from custom_components.myhome.const import ATTR_GATEWAY, ATTR_MESSAGE
    from custom_components.myhome.services import SERVICE_SEND_MESSAGE, async_setup_services

    await async_setup_services(hass)
    gw = _gateway()
    attach_gateway(MAC, gw)
    await hass.services.async_call(DOMAIN, SERVICE_SEND_MESSAGE, {ATTR_GATEWAY: MAC, ATTR_MESSAGE: frame}, blocking=True)
    assert gw.sent == []
    assert "dimension 22" in caplog.text


async def test_send_message_still_forwards_other_frames(hass, attach_gateway) -> None:
    from custom_components.myhome.const import ATTR_GATEWAY, ATTR_MESSAGE
    from custom_components.myhome.services import SERVICE_SEND_MESSAGE, async_setup_services

    await async_setup_services(hass)
    gw = _gateway()
    attach_gateway(MAC, gw)
    await hass.services.async_call(DOMAIN, SERVICE_SEND_MESSAGE, {ATTR_GATEWAY: MAC, ATTR_MESSAGE: "*#4*2*20##"}, blocking=True)
    assert gw.sent == ["*#4*2*20##"]


def test_capture_shows_the_crash(rows: list[dict]) -> None:
    probe = [r for r in rows if r["run"] == "probe_dim22_confirm" and r["dir"] == "tx" and "utc" in r]
    frames = [(r["frame"], r["ack"]) for r in probe[:5]]
    assert frames == [("*#4*2*0##", True), ("*#4*2*22##", False), ("*#4*2*0##", False), ("*#4*2*0##", False), ("*#1*01##", True)]
    assert all(r["reply_time_s"] < 0.05 for r in probe[1:4]), "refused locally, no bus round trip"
