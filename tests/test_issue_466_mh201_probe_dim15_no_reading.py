"""An all-zero WHO 4 dimension 15 frame is not a reading of 0.0 °C (MH201 3.3.6, issue_466).

The sweep of an MH201 3.3.6 of 2026-09-25 holds ``*#4*105*15*0*0000*0000##`` twice, 8 s apart,
and no request of the integration before them. Probe 105 of that plant (the MH200N and the MH201
of issue_466 share it) reports on dimension 0: ``*#4*105*0*0296##`` in the trace of the same
MH201 seven minutes earlier, ``*#4*105*0*0297##`` in a trace of the MH200N. OWNd reads the
all-zero frame as "secondary probe 0 is reporting a temperature of 0.0°C", and the sensor of the
probe showed 0.0 °C and took the frame for a report of the probe.

The dimension 15 reports of the other captures carry sensor 1 (``*#4*100*15*1*0253*0001##``,
#308) and stay readings, also at ``0000``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_MAC
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent, OWNHeatingEvent

from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, DOMAIN
from custom_components.myhome.sensor import (
    MyHOMETemperatureSensor,
    _probe_frame_without_reading,
    async_setup_entry,
)
from tests.conftest import attach_runtime

MAC = "00:03:50:00:04:66"
TRACES = Path(__file__).parent / "fixtures" / "traces" / "issue_466"
SWEEP = TRACES / "myhome_sweep_MH201_all_2026-09-25T19-11-35.json"
TRACE = TRACES / "myhome_trace_MH201_all_2026-09-25T19-05-42.json"
NO_READING = "*#4*105*15*0*0000*0000##"
REPORT = "*#4*105*0*0296##"
SECOND_REPORT = "*#4*105*0*0297##"


def _who4_probe_frames(path: Path) -> list[dict]:
    frames = json.loads(path.read_text(encoding="utf-8"))["frames"]
    return [f for f in frames if f["direction"] == "rx" and f["raw"].startswith("*#4*105*")]


@pytest.fixture
def gateway():
    gw = MagicMock()
    gw.mac = MAC
    gw.send_status_request = AsyncMock()
    gw.log_id = "[MH201 gateway]"
    gw.device_registry_id = None
    gw.is_connected = True
    return gw


def _sensor(hass, gateway, where: str = "105") -> MyHOMETemperatureSensor:
    sensor = MyHOMETemperatureSensor(
        hass=hass, name=f"Probe {where}", device_id=f"4-{where}", who="4", where=where,
        device_class="temperature", manufacturer="BTicino", model="Temperature Probe", gateway=gateway,
    )
    sensor.hass = hass
    sensor.entity_id = f"sensor.probe_{where}"
    sensor.async_schedule_update_ha_state = MagicMock()
    return sensor


def test_the_capture_holds_the_all_zero_frame_twice_and_a_report_on_dimension_0() -> None:
    zero = _who4_probe_frames(SWEEP)
    assert [f["raw"] for f in zero] == [NO_READING, NO_READING]
    assert round(zero[1]["timestamp"] - zero[0]["timestamp"], 2) == 8.01
    report = _who4_probe_frames(TRACE)
    assert [f["raw"] for f in report] == [REPORT]
    assert round(zero[0]["timestamp"] - report[0]["timestamp"]) == 431
    # OWNd's reading of the frame, which the sensor must not show.
    event = OWNEvent.parse(NO_READING)
    assert isinstance(event, OWNHeatingEvent) and event.dimension == 15
    assert event.secondary_temperature == [0, 0.0]


def test_only_the_all_zero_frame_is_without_a_reading() -> None:
    assert _probe_frame_without_reading(OWNEvent.parse(NO_READING)) is True
    for raw in (
        "*#4*100*15*1*0253*0001##",  # #308, a 3455 behind an L4577 on an F454
        "*#4*100*15*1*0200*0001##",  # #264
        "*#4*100*15*1*0000*0001##",  # not a capture: the frame of #308 at 0.0 °C; sensor 1 is a reading
        REPORT,
        "*#4*105*0*0000##",  # not a capture: dimension 0 is not concerned
    ):
        assert _probe_frame_without_reading(OWNEvent.parse(raw)) is False, raw


def test_the_sensor_keeps_its_reading_when_the_all_zero_frame_arrives(hass, gateway) -> None:
    probe = _sensor(hass, gateway)
    probe.handle_event(OWNEvent.parse(_who4_probe_frames(TRACE)[0]["raw"]))
    assert probe.native_value == 29.6 and probe._pushes == 1
    pushed_at = probe._last_push_at
    probe.async_schedule_update_ha_state.reset_mock()
    for frame in _who4_probe_frames(SWEEP):
        probe.handle_event(OWNEvent.parse(frame["raw"]))
    assert probe.native_value == 29.6
    # Not a report either: the fallback window and the count of reports are unchanged.
    assert probe._pushes == 1 and probe._last_push_at == pushed_at
    probe.async_schedule_update_ha_state.assert_not_called()


def test_a_dimension_15_report_of_sensor_1_is_still_shown(hass, gateway) -> None:
    probe = _sensor(hass, gateway, "100")
    probe.handle_event(OWNEvent.parse("*#4*100*15*1*0253*0001##"))
    assert probe.native_value == 25.3 and probe._pushes == 1


async def test_the_all_zero_frame_creates_no_sensor_and_the_report_does(hass) -> None:
    """Through the platform: discovery from the bus, then routing to the sensor."""
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.log_id = "[MH201 gateway]"
    gateway.send_status_request = AsyncMock()
    hass.data[DOMAIN] = {MAC: {CONF_PLATFORMS: {"sensor": {}}, CONF_ENTITY: gateway}}
    entry = MagicMock()
    entry.entry_id = "mh201_probe_105"
    entry.data = {CONF_MAC: MAC}
    attach_runtime(hass, entry)
    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    for frame in _who4_probe_frames(SWEEP):
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(frame["raw"]))
    await hass.async_block_till_done()
    assert added == []

    # An unconfigured probe is created on its second independent reading (#967): the first
    # report is held, the next one (probe 105 on the MH200N of the same plant,
    # ``myhome_trace_MH200N_all_2026-09-25T18-12-34.json``) creates the sensor.
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(REPORT))
    await hass.async_block_till_done()
    assert added == []
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(SECOND_REPORT))
    await hass.async_block_till_done()
    assert [type(entity) for entity in added] == [MyHOMETemperatureSensor]
    assert added[0]._where == "105"
