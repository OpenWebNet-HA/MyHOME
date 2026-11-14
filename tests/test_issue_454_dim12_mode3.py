"""#454: the trailing ``3`` of WHO 4 dimension 12 is not a protection marker.

A MyHomeServer1 answers every manual setpoint write, and every schedule change,
with ``*#4*Z*12*<T>*3##`` followed by ``*4*1*Z##``. Reading that ``3`` as
"protection or off" left the nominal setpoint stale, and a later dimension 13
frame or OFF -> HEAT transition copied the stale value back (#454). The antifreeze
sweep of #383 (``12*0070*3`` then ``*4*102*Z##``) must keep working.
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACMode
from OWNd.message import OWNHeatingEvent

from custom_components.myhome import climate as climate_module
from custom_components.myhome.climate import MyHOMEClimate

TRACE = (
    Path(__file__).resolve().parent
    / "fixtures/traces/issue_466/myhome_sweep_MyHomeServer1_all_2026-09-29T18-20-22.json"
)


def _zone(hass, where="55"):
    gateway = MagicMock()
    gateway.mac = "00:03:50:00:04:54"
    gateway.send = AsyncMock()
    zone = MyHOMEClimate(
        hass=hass, name=f"Zone {where}", device_id=f"4-{where}", who="4", where=where,
        heating=True, cooling=False, fan=False, standalone=True, central=False,
        manufacturer="BTicino", model="Heating Zone", gateway=gateway,
    )
    zone.hass = hass
    zone.entity_id = f"climate.zone_{where}"
    zone.async_write_ha_state = MagicMock()
    return zone


def _feed(zone, *frames):
    for frame in frames:
        zone.handle_event(OWNHeatingEvent(frame))


async def test_manual_write_refreshes_the_nominal_setpoint(hass):
    """The gdluck write echo: dimension 12 mode 3 then heating mode, no dimension 7 or 14."""
    zone = _zone(hass)
    _feed(zone, "*#4*55*7*1*1*0230##")
    assert zone._target_temperature == 23.0

    _feed(zone, "*#4*55*12*0225*3##", "*4*1*55##", "*#4*55*0*0246##")
    assert zone.target_temperature == 22.5
    assert zone._target_temperature == 22.5


async def test_stale_nominal_is_not_copied_back_by_the_offset_frame(hass):
    zone = _zone(hass)
    _feed(zone, "*#4*55*7*1*1*0230##", "*#4*55*12*0225*3##", "*#4*55*13*00##")
    assert zone.target_temperature == 22.5


async def test_off_and_back_on_restores_the_written_setpoint(hass, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(climate_module.time, "monotonic", lambda: clock[0])
    zone = _zone(hass)
    _feed(zone, "*#4*55*7*1*1*0230##", "*#4*55*12*0225*3##")
    clock[0] += 60  # the user turns the zone off a minute after the write
    _feed(zone, "*4*102*55##")
    assert zone.hvac_mode == HVACMode.OFF
    _feed(zone, "*4*1*55##")
    assert zone.target_temperature == 22.5


async def test_protection_setpoint_of_a_running_zone_keeps_the_nominal(hass):
    """Antifreeze 7.0 (mode 3) right before the OFF frame: the nominal 17.0 survives (#383)."""
    zone = _zone(hass)
    _feed(zone, "*#4*55*7*1*1*0170##", "*4*1*55##")
    _feed(zone, "*#4*55*12*0070*3##", "*4*102*55##")
    assert zone.hvac_mode == HVACMode.OFF
    assert zone._target_temperature == 17.0
    _feed(zone, "*4*1*55##")
    assert zone.target_temperature == 17.0


async def test_protection_setpoint_is_only_undone_when_off_follows_at_once(hass, monkeypatch):
    """An OFF hours after a schedule change must not roll the setpoint back."""
    clock = [1000.0]
    monkeypatch.setattr(climate_module.time, "monotonic", lambda: clock[0])
    zone = _zone(hass)
    _feed(zone, "*#4*55*7*1*1*0170##", "*4*1*55##", "*#4*55*12*0200*3##")
    clock[0] += 3 * 3600
    _feed(zone, "*4*102*55##", "*4*1*55##")
    assert zone.target_temperature == 20.0


async def test_zone_of_unknown_mode_still_guards_the_nominal(hass):
    """Before any mode frame a dimension 12 mode 3 may be antifreeze: nominal untouched (#383)."""
    zone = _zone(hass, "1")
    _feed(zone, "*#4*1*14*0190*3##", "*#4*1*12*0070*3##")
    assert zone.target_temperature == 7.0
    assert zone._target_temperature == 19.0


@pytest.mark.parametrize("zone_id", ["55", "60", "68"])
async def test_gdluck_write_echo_replay(hass, zone_id):
    """Replay each of gdluck's real write echoes on a running zone."""
    frames = [f["raw"] for f in json.loads(TRACE.read_text(encoding="utf-8"))["frames"]]
    writes = [f for f in frames if f.startswith(f"*#4*{zone_id}*#14*")]
    assert writes
    zone = _zone(hass, zone_id)
    _feed(zone, f"*#4*{zone_id}*7*1*1*0100##")
    for write in writes:
        expected = int(write.split("*")[4]) / 10
        echo = f"*#4*{zone_id}*12*{write.split('*')[4]}*3##"
        assert echo in frames
        _feed(zone, echo, f"*4*1*{zone_id}##")
        assert zone._target_temperature == expected
