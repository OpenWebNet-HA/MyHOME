"""Startup requests the firmware forwards, per gateway (own-firmware-oracle verdicts, #185, #257, #674).

- ``*#4*0##`` is refused (not forwarded to the bus) by F450, F454, F459, F460, F461, H4684,
  MH200, MH202 and MyHomeServer1; F453, F453AV and MH200N forward it
  (``results/<P>/<V>/oracle/full/trace-corpus.tsv``). The MH201 is not in the oracle: a
  physical MH201 3.3.6 answered ``*#4*0##`` with no zone frame (``issue_466`` sweep trace). Where it is refused, an SCS-only
  gateway's zones ask for their temperature one by one (``*#4*Z*0##``), which every
  emulated gateway forwards.
- ``*#16*0*5##`` is refused by F450, F459, F460, F461, MH202 and MyHomeServer1
  (``results/<P>/<V>/oracle/full/sound-who16-source.tsv``).
- A restored ``current_temperature`` is flagged ``current_temperature_restored`` until the
  zone reports again.

The "captured frames" section replays rows of three committed traces (the loaders the other
trace tests use): ``f461_live_2026-10-23`` (zone 2 answers ``*#4*2*0##`` in 119 ms, the
unpopulated zone 1 NACKs after 2.07 s), ``myhomeserver1_live_2026-10-24`` (zones 35..70
report dimension 0, ``*#4*0##`` refused in 51 ms) and the ``issue_466`` MH201 sweep (frame 11
``*#4*0##`` with no zone reply, while the sibling trace shows zones 2, 3 and 4). The earlier
cases keep a synthetic zone 3 on purpose: they pin the request a zone sends, not a plant.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import State
from OWNd.message import OWNHeatingEvent

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.const import CONF_FIRMWARE
from custom_components.myhome.discovery import message_has_state
from custom_components.myhome.gateway import MyHOMEGatewayHandler

MAC = "00:03:50:00:01:85"
TRACES = Path(__file__).resolve().parent / "fixtures" / "traces"
F461_LIVE = TRACES / "f461_live_2026-10-23" / "myhome_trace_F461_live_2026-10-23.json"
MHS1_LIVE = TRACES / "myhomeserver1_live_2026-10-24" / "myhome_trace_MyHomeServer1_live_2026-10-24.json"
MH201_SWEEP = TRACES / "issue_466" / "myhome_sweep_MH201_all_2026-09-25T19-11-35.json"
MH201_TRACE = TRACES / "issue_466" / "myhome_trace_MH201_all_2026-09-25T19-05-42.json"
ZONE_TEMPERATURE = re.compile(r"^\*#4\*(\d{1,2})\*0\*(\d{4})##$")  # *#4*Z*0*TTTT## of a plain zone 1..99


def _entry(model: str) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = f"entry-{model}"
    entry.data = {
        CONF_HOST: "192.0.2.185", CONF_PORT: 20000, CONF_PASSWORD: "x",
        CONF_NAME: model, CONF_FIRMWARE: "2.0.11", CONF_MAC: MAC,
    }
    entry.runtime_data = None
    return entry


def _handler(hass, model: str) -> MyHOMEGatewayHandler:
    return MyHOMEGatewayHandler(hass, _entry(model))


def _zone(hass, handler, where="3", fan=False, **kwargs) -> MyHOMEClimate:
    zone = MyHOMEClimate(
        hass=hass, name=f"Zone {where}", device_id=f"4-{where}", who="4", where=where,
        heating=True, cooling=False, fan=fan, standalone=True, central=False,
        manufacturer="BTicino", model="Heating Zone", gateway=handler, **kwargs,
    )
    zone.hass = hass
    zone.entity_id = f"climate.zone_{where}"
    zone.async_write_ha_state = MagicMock()
    return zone


# ------------------------------------------------------------------ startup sweep

@pytest.mark.parametrize(
    ("model", "zone_sweep", "sound"),
    [
        ("F461", False, False), ("MyHomeServer1", False, False), ("MH202", False, False),
        ("F459", False, False), ("F460", False, False), ("F450", False, False),
        ("F454", False, True), ("MH200", False, True), ("MH201", False, True),
        ("H4684", False, True), ("HL4684", False, True), ("H/L4684", False, True),
        ("F453", True, True), ("F453AV", True, True), ("MH200N", True, True), ("Generic", True, True),
    ],
)
def test_sweep_skips_requests_the_gateway_refuses(hass, model, zone_sweep, sound):
    frames = _handler(hass, model)._discovery_frames()
    assert ("*#4*0##" in frames) is zone_sweep
    assert ("*#16*0*5##" in frames) is sound
    assert frames[:2] == ["*#1*0##", "*#2*0##"]


def test_hardware_compatibility_table_matches_the_sweep(hass):
    """``sweep_omits`` in scripts/update_gateway_profiles.py (the "Startup Sweep Requests" column of
    docs/getting-started/hardware-compatibility.md) is exactly what the sweep leaves out per profile."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import update_gateway_profiles as ugp

    from custom_components.myhome.gateway import DISCOVERY_REQUESTS

    assert len(ugp.GATEWAY_DISCOVERY_METADATA) == 11
    for row in ugp.calibrate_discovery_with_ownd():
        frames = _handler(hass, str(row["profile_name"]))._discovery_frames()
        omitted = [frame for who, frame in DISCOVERY_REQUESTS if who in row["queried_whos"] and frame not in frames]
        assert omitted == list(row["sweep_omits"]), row["model"]
        assert sorted({who for who, frame in DISCOVERY_REQUESTS if frame in frames}) == ugp.sweep_whos(row), row["model"]


def test_f461_sweep_in_full(hass):
    assert _handler(hass, "F461")._discovery_frames() == ["*#1*0##", "*#2*0##", "*#18*51*51##", "*#18*51*113##"]


@pytest.mark.parametrize(
    ("force", "general"),
    [
        (False, ["*#1*0##", "*#2*0##"]),  # the F461 firmware drops *#4*0## and *#16*0*5##, no WHO 5 in the profile
        (True, ["*#1*0##", "*#2*0##", "*#4*0##", "*#5*0##", "*#16*0*5##"]),
    ],
)
async def test_sweep_bus_leaves_out_the_refused_requests_unless_forced(hass, attach_gateway, force, general):
    """``myhome.sweep_bus`` applies the startup sweep's firmware gate; ``force`` sends everything."""
    from custom_components.myhome.const import ATTR_GATEWAY, DOMAIN
    from custom_components.myhome.services import SERVICE_SWEEP_BUS, async_setup_services

    await async_setup_services(hass)
    handler = _handler(hass, "F461")
    handler.send_status_request = AsyncMock()
    handler.send_paced = AsyncMock()
    attach_gateway(MAC, handler)
    await hass.services.async_call(DOMAIN, SERVICE_SWEEP_BUS, {ATTR_GATEWAY: MAC, "force": force}, blocking=True)
    handler.send_paced.assert_awaited_once_with(general)


@pytest.mark.parametrize(
    ("model", "profile_model"),
    [("L4684", "H4684"), ("NT4684", "H4684"), ("003598", "F454"), ("03535", "MH202")],
)
def test_aliased_model_strings_follow_their_profile(hass, model, profile_model):
    """OWNd resolves these spellings to a profile; the SCS-only gate follows the profile's model name."""
    handler = _handler(hass, model)
    assert handler.gateway.profile.model_name == profile_model
    assert handler._model_key == profile_model
    assert handler.supports_zone_status is False
    assert handler.zone_temperature_poll_needed is True


@pytest.mark.parametrize("ssdp_model", ["H/L4684", "HL4684", "HC4684"])
def test_ssdp_model_string_hl4684_is_keyed_as_h4684(hass, ssdp_model):
    """SSDP announces the H4684 as ``H/L4684`` (or ``HL4684``); handler resolves ``H4684Profile``."""
    handler = _handler(hass, ssdp_model)
    assert handler.model == ssdp_model
    assert handler.gateway.profile.model_name == "H4684"
    assert handler._model_key == "H4684"
    assert handler.supports_zone_status is False
    assert handler.general_zone_request_refused is True
    assert handler.sound_status_request_refused is False
    assert handler.interface_zone_request_refused is True


@pytest.mark.parametrize("flag", [True, False])
def test_profile_flag_for_the_general_heating_status_wins(hass, flag):
    """An OWNd profile that states ``supports_general_heating_status`` is believed over the table."""
    handler = _handler(hass, "F453" if not flag else "F461")
    handler.gateway.profile = SimpleNamespace(
        model_name=handler.gateway.profile.model_name,
        supports_general_heating_status=flag,
        supports_who=lambda who: True,
    )
    assert handler.general_zone_request_refused is (not flag)
    assert ("*#4*0##" in handler._discovery_frames()) is flag


def test_profile_without_who_16_skips_the_sound_request(hass):
    handler = _handler(hass, "F454")
    handler.gateway.profile = SimpleNamespace(model_name="F454", supports_who=lambda who: who != 16)
    assert handler.sound_status_request_refused is True
    assert "*#16*0*5##" not in handler._discovery_frames()


# ------------------------------------------------------------------ zone temperature (#185)

@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("MH201", True), ("MH202", True), ("F450", True), ("F454", True), ("MH200", True), ("H4684", True),
        ("HL4684", True), ("H/L4684", True), ("F459", True),
        ("F455", False), ("F453", False), ("MH200N", False),  # unknown, or the sweep answers
        ("F460", False), ("F461", False), ("MyHomeServer1", False), ("Generic", False),  # polled with *#4*Z##
    ],
)
def test_which_gateways_need_a_zone_temperature_poll(hass, model, expected):
    assert _handler(hass, model).zone_temperature_poll_needed is expected


@pytest.mark.parametrize("model", ["MH201", "MH202"])
async def test_scs_only_zone_asks_for_its_temperature(hass, model):
    handler = _handler(hass, model)
    handler.send_status_request = AsyncMock()
    zone = _zone(hass, handler)
    assert zone._poll_on_add is True
    await zone.async_update()
    assert [str(call.args[0]) for call in handler.send_status_request.await_args_list] == ["*#4*3*0##"]


@pytest.mark.parametrize(
    "model",
    ["F450", "F454", "F459", "MH202", "H4684", "MH200", "MH201", "F460", "F461", "MyHomeServer1", "Generic"],
)
async def test_interface_zone_is_not_polled_across_any_unrouted_gateway(hass, model, caplog):
    """0 of 12 oracle gateways route WHO 4 across #4#II (findings/thermo-zone-requests.md).

    ``_polls_temperature`` is a pure predicate: construction and reading it log nothing; the
    one-shot DEBUG line is written by ``async_update`` when it actually withholds the poll.
    """
    handler = _handler(hass, model)
    handler.send_status_request = AsyncMock()

    def skipped() -> list:
        return [record for record in caplog.records if "does not route WHO 4 requests across `#4#01`" in record.message]

    with caplog.at_level("DEBUG", logger="custom_components.myhome"):
        zone = _zone(hass, handler, interface="01")
        assert zone._polls_temperature is False
        assert zone._interface_poll_refused is True
        assert zone._poll_on_add is False
        assert handler.interface_zone_request_refused is True
        assert skipped() == []  # nothing logged in __init__ or by the property
        await zone.async_update()
        handler.send_status_request.assert_not_awaited()
        assert len(skipped()) == 1 and skipped()[0].levelname == "DEBUG"
        await zone.async_update()
        handler.send_status_request.assert_not_awaited()
        assert len(skipped()) == 1  # once per entity


async def test_withheld_interface_poll_also_withholds_humidity_and_fan_cross_polls(hass, caplog):
    """On F459, *#4*3#4#01*60## truncates at # and cross-polls local main-bus zone 3 ($06D102030308000000);
    all WHO 4 polls on a routed zone are withheld and logged once."""
    handler = _handler(hass, "F459")
    handler.send_status_request = AsyncMock()
    zone = _zone(hass, handler, interface="01", humidity=True, fan=True)
    assert zone._polls_temperature is False and zone._interface_poll_refused is True
    assert zone._poll_on_add is False
    with caplog.at_level("DEBUG", logger="custom_components.myhome"):
        await zone.async_update()
        await zone.async_update()
    handler.send_status_request.assert_not_awaited()
    assert len([record for record in caplog.records if "does not route WHO 4 requests across `#4#01`" in record.message]) == 1


@pytest.mark.parametrize(
    ("model", "flag", "expected_temp", "expected_frames"),
    [
        ("MH202", True, True, ["*#4*3#4#01*0##"]),
        ("H4684", True, True, ["*#4*3#4#01*0##"]),
        ("H4684", False, False, []),
        ("F461", True, False, ["*#4*3#4#01##"]),
    ],
)
async def test_profile_flag_wins_over_the_interface_zone_default(hass, model, flag, expected_temp, expected_frames):
    """An OWNd profile that states ``supports_interface_zone_requests`` overrides the fleet default."""
    handler = _handler(hass, model)
    handler.gateway.profile = SimpleNamespace(
        model_name=handler.gateway.profile.model_name,
        supports_interface_zone_requests=flag,
        supports_who=lambda who: True,
    )
    handler.send_status_request = AsyncMock()
    assert handler.interface_zone_request_refused is (not flag)
    zone = _zone(hass, handler, interface="01")
    assert zone._polls_temperature is expected_temp
    assert zone._poll_on_add is flag
    await zone.async_update()
    assert [str(call.args[0]) for call in handler.send_status_request.await_args_list] == expected_frames


async def test_f455_zone_still_sends_nothing(hass, caplog):
    handler = _handler(hass, "F455")
    handler.send_status_request = AsyncMock()
    zone = _zone(hass, handler, interface="01")
    assert zone._poll_on_add is False
    assert zone._interface_poll_refused is True
    with caplog.at_level("DEBUG", logger="custom_components.myhome"):
        await zone.async_update()
    handler.send_status_request.assert_not_awaited()
    assert not [record for record in caplog.records if "does not route WHO 4 requests" in record.message]


async def test_central_unit_is_not_a_temperature_poll(hass):
    handler = _handler(hass, "MH202")
    handler.send_status_request = AsyncMock()
    central = MyHOMEClimate(
        hass=hass, name="Central", device_id="4-#0", who="4", where="#0",
        heating=True, cooling=False, fan=False, standalone=False, central=True,
        manufacturer="BTicino", model="Central Unit", gateway=handler,
    )
    assert central._polls_temperature is False


def test_bus_discovered_zone_keeps_its_poll_until_a_temperature_arrives(hass):
    polled = _zone(hass, _handler(hass, "MH202"))
    assert polled._polls_temperature is True
    assert message_has_state(polled, OWNHeatingEvent("*#4*3*14*0210*3##")) is False
    assert message_has_state(polled, OWNHeatingEvent("*#4*3*0*0215##")) is True
    silent = _zone(hass, _handler(hass, "F455"))
    assert message_has_state(silent, OWNHeatingEvent("*#4*3*14*0210*3##")) is True


# ------------------------------------------------------------------ restored temperature

async def test_current_temperature_is_restored_and_flagged_until_the_bus_reports(hass):
    zone = _zone(hass, _handler(hass, "MH202"))
    await zone.async_restore_last_state(
        State("climate.zone_3", "heat", {"temperature": 21.0, "current_temperature": 23.9})
    )
    assert zone.current_temperature == 23.9
    assert zone.extra_state_attributes["current_temperature_restored"] is True
    zone.handle_event(OWNHeatingEvent("*#4*3*0*0241##"))
    assert zone.current_temperature == 24.1
    assert "current_temperature_restored" not in zone.extra_state_attributes


async def test_a_malformed_restored_temperature_is_ignored(hass):
    zone = _zone(hass, _handler(hass, "MH202"))
    await zone.async_restore_last_state(State("climate.zone_3", "heat", {"current_temperature": "warm"}))
    assert zone.current_temperature is None
    assert "current_temperature_restored" not in zone.extra_state_attributes


@pytest.mark.parametrize(
    ("model", "flag", "expected"),
    [
        ("F454", True, False),  # listed in the fallback table, but the profile says the sweep is answered
        ("MH201", True, False),  # the #185 field-report entry yields to a stated profile flag too
        ("F453", False, True),  # not in the table; the profile says the sweep is refused
        ("F461", False, False),  # polled with *#4*Z##: never a temperature poll
    ],
)
def test_profile_flag_wins_over_the_zone_temperature_poll_table(hass, model, flag, expected):
    handler = _handler(hass, model)
    handler.gateway.profile = SimpleNamespace(
        model_name=handler.gateway.profile.model_name,
        supports_general_heating_status=flag,
        supports_who=lambda who: True,
    )
    assert handler.zone_temperature_poll_needed is expected


# ------------------------------------------------------------------ captured frames (fixtures)

@pytest.fixture(scope="module")
def f461_rows() -> list[dict]:
    assert F461_LIVE.is_file(), F461_LIVE
    return json.loads(F461_LIVE.read_text(encoding="utf-8"))["rows"]


@pytest.fixture(scope="module")
def mhs1_rows() -> list[dict]:
    assert MHS1_LIVE.is_file(), MHS1_LIVE
    return json.loads(MHS1_LIVE.read_text(encoding="utf-8"))["rows"]


@pytest.fixture(scope="module")
def mh201_sweep() -> dict:
    assert MH201_SWEEP.is_file(), MH201_SWEEP
    return json.loads(MH201_SWEEP.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mh201_trace() -> dict:
    assert MH201_TRACE.is_file(), MH201_TRACE
    return json.loads(MH201_TRACE.read_text(encoding="utf-8"))


def _result(rows: list[dict], run: str, frame: str) -> dict:
    found = [r for r in rows if r["dir"] == "result" and r["run"] == run and r["frame"] == frame]
    assert len(found) == 1, (run, frame, len(found))
    return found[0]


def _tx(rows: list[dict], run: str, frame: str) -> dict:
    found = [r for r in rows if r["dir"] == "tx" and r["run"] == run and r["frame"] == frame]
    assert len(found) == 1, (run, frame, len(found))
    return found[0]


def test_f461_capture_answers_the_populated_zone_and_nacks_the_unpopulated_one(f461_rows):
    """f461_live_2026-10-23, run1: *#4*2*0## is answered in 119 ms; *#4*1*0## (no thermostat) NACKs after 2.07 s."""
    run = "run1_oracle_replay_readonly"
    assert _tx(f461_rows, run, "*#4*2*0##")["session"] == "cmd"
    zone_2 = _result(f461_rows, run, "*#4*2*0##")
    assert zone_2["ack"] is True
    assert zone_2["replies"] == ["*#4*2*0*0239##"]
    assert zone_2["reply_time_s"] == 0.119
    zone_1 = _result(f461_rows, run, "*#4*1*0##")  # the bus TX timeout the oracle images show on every zone
    assert zone_1["ack"] is False
    assert zone_1["replies"] == []
    assert zone_1["reply_time_s"] == 2.071
    general = _result(f461_rows, run, "*#4*0##")
    assert general["ack"] is False and general["replies"] == [] and general["reply_time_s"] < 0.01
    assert general["note"] == "general_zone_status_refused_locally"


async def test_captured_f461_dimension_0_reply_clears_a_restored_temperature(hass, f461_rows):
    """The request an SCS-only zone 2 sends is the captured *#4*2*0##; the captured reply replaces the restored value."""
    run = "run1_oracle_replay_readonly"
    handler = _handler(hass, "MH202")
    handler.send_status_request = AsyncMock()
    zone = _zone(hass, handler, where="2")
    await zone.async_restore_last_state(State("climate.zone_2", "heat", {"current_temperature": 21.0}))
    assert zone.extra_state_attributes["current_temperature_restored"] is True
    await zone.async_update()
    assert [str(call.args[0]) for call in handler.send_status_request.await_args_list] == [
        _tx(f461_rows, run, "*#4*2*0##")["frame"]
    ]
    (reply,) = _result(f461_rows, run, "*#4*2*0##")["replies"]
    event = OWNHeatingEvent(reply)
    assert message_has_state(zone, event) is True
    zone.handle_event(event)
    assert zone.current_temperature == 23.9
    assert "current_temperature_restored" not in zone.extra_state_attributes


async def test_dimension_0_poll_result_is_not_counted_against_the_zone(hass):
    """No _poll_answered on *#4*Z*0##: a NACK (cancelled write) must not mark a healthy SCS zone unresponsive."""
    handler = _handler(hass, "F454")
    written: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    written.cancel()
    handler.send_status_request = AsyncMock(return_value=written)
    handler.is_connected = True
    zone = _zone(hass, handler)
    zone._poll_answered = MagicMock()
    await zone.async_update()
    await zone.async_update()
    handler.send_status_request.assert_awaited()
    zone._poll_answered.assert_not_called()
    assert zone._poll_health.should_skip(time.time()) is False


def test_myhomeserver1_capture_refuses_the_general_request_and_answers_zone_status(mhs1_rows):
    """myhomeserver1_live_2026-10-24: *#4*0## refused in 51 ms (run 3), *#4*35## answered with five frames (run 2)."""
    general = _result(mhs1_rows, "run3_reads_lights_addresses_humidity", "*#4*0##")
    assert general["ack"] is False and general["replies"] == [] and general["reply_time_s"] == 0.051
    assert general["note"] == "general_zone_status_refused_locally"
    zone_35 = _result(mhs1_rows, "run2_reads_thermo_sound_alarm_lights", "*#4*35##")
    assert zone_35["ack"] is True
    assert zone_35["replies"] == [
        "*#4*35*0*0231##", "*#4*35*12*0200*3##", "*4*1*35##", "*#4*35*14*0200*3##", "*#4*35*13*00##",
    ]


async def test_captured_myhomeserver1_zones_report_without_a_temperature_poll(hass, mhs1_rows):
    """Zones 35..70 of the capture report *#4*Z*0*TTTT##; the MyHomeServer1 keeps *#4*Z## and needs no *#4*Z*0##."""
    handler = _handler(hass, "MyHomeServer1")
    handler.send_status_request = AsyncMock()
    assert handler.general_zone_request_refused is True
    assert handler.supports_zone_status is True
    assert handler.zone_temperature_poll_needed is False
    reports = {}
    for row in mhs1_rows:
        match = ZONE_TEMPERATURE.match(row["frame"]) if row["run"] == "run1_identify" and row["dir"] == "rx" else None
        if match is not None:
            reports.setdefault(match.group(1), row["frame"])
    assert len(reports) == 33
    assert min(map(int, reports)) == 35 and max(map(int, reports)) == 70
    for where, frame in reports.items():
        zone = _zone(hass, handler, where=where)
        assert zone._polls_temperature is False
        zone.handle_event(OWNHeatingEvent(frame))
        assert zone.current_temperature == int(ZONE_TEMPERATURE.match(frame).group(2)) / 10
        assert "current_temperature_restored" not in zone.extra_state_attributes
    zone_35 = _zone(hass, handler, where="35")
    await zone_35.async_update()
    sent = [str(call.args[0]) for call in handler.send_status_request.await_args_list]
    assert sent[0] == _tx(mhs1_rows, "run2_reads_thermo_sound_alarm_lights", "*#4*35##")["frame"]
    assert "*#4*35*0##" not in sent


def test_mh201_sweep_capture_gets_no_zone_reply_to_the_general_request(mh201_sweep, mh201_trace):
    """issue_466: a physical MH201 3.3.6 sent *#4*0## (frame 11) and no zone frame followed; its zones do report."""
    gateway = mh201_sweep["gateway"]
    assert gateway["model"] == "MH201" and gateway["firmware"] == "3.3.6"
    assert gateway["identification"]["who13_code"] == "200"
    frames = mh201_sweep["frames"]
    assert frames[11]["direction"] == "tx" and frames[11]["raw"] == "*#4*0##"
    after = [f["raw"] for f in frames[12:] if f["who"] == "4"]
    assert not [raw for raw in after if ZONE_TEMPERATURE.match(raw)]
    # the only WHO 4 traffic afterwards: a dimension 17 broadcast of WHERE 0#0 and zone 105's dimension 15
    assert set(after) == {"*#4*0#0*17##", "*#4*105*15*0*0000*0000##"}
    sibling = [f["raw"] for f in mh201_trace["frames"] if f["who"] == "4"]
    assert {"*#4*4*0*0241##", "*#4*2*14*0100*3##", "*#4*3*12*0090*3##", "*#4*2*13*00##"} <= set(sibling)
    assert {raw.split("*")[2] for raw in sibling if raw.startswith("*#4*")} >= {"2", "3", "4"}


async def test_mh201_leaves_the_general_request_out_and_polls_the_captured_zone(hass, mh201_trace):
    handler = _handler(hass, "MH201")
    handler.send_status_request = AsyncMock()
    assert "*#4*0##" not in handler._discovery_frames()
    assert handler.zone_temperature_poll_needed is True
    zone = _zone(hass, handler, where="4")
    await zone.async_restore_last_state(State("climate.zone_4", "heat", {"current_temperature": 20.0}))
    await zone.async_update()
    assert [str(call.args[0]) for call in handler.send_status_request.await_args_list] == ["*#4*4*0##"]
    (captured,) = [f["raw"] for f in mh201_trace["frames"] if f["raw"] == "*#4*4*0*0241##"]
    zone.handle_event(OWNHeatingEvent(captured))
    assert zone.current_temperature == 24.1
    assert "current_temperature_restored" not in zone.extra_state_attributes
