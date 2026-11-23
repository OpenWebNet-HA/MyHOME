"""`zone_operation`: what a zone's mode frame says besides its season.

A zone the central unit holds in antifreeze / thermal protection reports `*4*102*Z##` /
`*4*202*Z##` and its protection setpoint; Home Assistant shows it `off` at that setpoint (#383,
kept by #880), exactly like a zone that is switched off (`*4*303*Z##`). A zone running the
central unit's program reports `*4*111*#Z##` / `*4*211*#Z##` and is shown `heat` / `cool`, like
one in manual (`*4*110*#Z##` / `*4*210*#Z##`). The mode shown is unchanged here; the attribute
`zone_operation` (`antifreeze`, `thermal_protection`, `protection`, `manual`, `automatic`) tells
them apart,
as `central_operation` does for the central unit.

Frames:
- zone 3 of a 3550, F461 2.0.11 event session of the test plant, 2026-11-17 23:24:58 - 23:25:16
  (manual heating 25.0, antifreeze by the central unit, manual again);
- zone 4 of the same 3550 in thermal protection, 2026-11-17 23:28:31;
- F454 #383 (`*4*102*2##`, `*#4*2*12*0070*3##`, `*#4*2*14*0170*3##`);
- `tests/fixtures/traces`: F453AV antifreeze (`issue_619`), MH200N zones in automatic
  (`issue_466`, 2026-11-02), F455 / F461 weekly program in conditioning
  (`central_restore_3100_live_2026-11-12`), MyHomeServer1 dimension 7 protection (`issue_429`).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import State
from OWNd.message import OWNHeatingEvent, OWNMessage

from custom_components.myhome.climate import MyHOMEClimate

TRACES = Path(__file__).parent / "fixtures" / "traces"


def _gateway(central: bool = True) -> MagicMock:
    gateway = MagicMock()
    gateway.mac = "00:03:50:00:04:97"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    gateway.has_central_unit = central
    return gateway


def _zone(hass, where: str, gateway: MagicMock | None = None, standalone: bool | None = None) -> MyHOMEClimate:
    zone = MyHOMEClimate(
        hass=hass, name=f"Zone {where}", device_id=f"4-{where}", who="4", where=where,
        heating=True, cooling=True, fan=False, standalone=standalone, central=False,
        manufacturer="BTicino", model="Heating Zone", gateway=gateway or _gateway(),
    )
    zone.hass = hass
    zone.entity_id = f"climate.zone_{where}"
    zone.async_write_ha_state = MagicMock()
    return zone


def _feed(zone: MyHOMEClimate, *frames: str) -> None:
    for frame in frames:
        zone.handle_event(OWNHeatingEvent(frame))


def _operation(zone: MyHOMEClimate) -> str | None:
    return zone.extra_state_attributes.get("zone_operation")


def _rx_zone_frames(trace: Path, zone: int) -> list[str]:
    frames = json.loads(trace.read_text(encoding="utf-8"))["frames"]
    raws = []
    for frame in frames:
        if frame.get("direction") != "rx" or not frame["raw"].startswith(("*4*", "*#4*")):
            continue
        message = OWNMessage.parse(frame["raw"])
        if isinstance(message, OWNHeatingEvent) and message.zone == zone:
            raws.append(frame["raw"])
    return raws


async def test_zone_of_the_central_in_antifreeze_is_off_with_its_operation(hass):
    zone = _zone(hass, "3")
    _feed(zone, "*#4*3*12*0250*3##", "*4*1*3##", "*4*110*#3##", "*#4*#3*14*0250*1##")
    assert zone.hvac_mode == HVACMode.HEAT
    assert _operation(zone) == "manual"
    _feed(zone, "*4*102*#3##", "*4*102*3##", "*#4*3*0*0255##", "*#4*3*12*0070*3##", "*4*102*3##")
    assert zone.hvac_mode == HVACMode.OFF  # unchanged (#383)
    assert zone.target_temperature == 7.0
    assert _operation(zone) == "antifreeze"
    # manual heating again (23:25:15.919 - 23:25:16.412)
    _feed(zone, "*4*1*3##", "*#4*3*0*0255##", "*4*110*#3##", "*#4*#3*14*0250*1##")
    assert zone.hvac_mode == HVACMode.HEAT
    assert _operation(zone) == "manual"


async def test_zone_in_thermal_protection_is_off_with_its_operation(hass):
    zone = _zone(hass, "4")
    _feed(zone, "*4*202*4##", "*#4*4*12*0350*3##", "*4*202*4##")
    assert zone.hvac_mode == HVACMode.OFF
    assert zone.target_temperature == 35.0
    assert _operation(zone) == "thermal_protection"


async def test_standalone_zone_in_antifreeze_and_back(hass):
    """F454 #383: antifreeze at 7.0 with the nominal 17.0 kept; the season frame ends it."""
    zone = _zone(hass, "2", gateway=_gateway(central=False), standalone=True)
    _feed(zone, "*#4*2*12*0070*3##", "*4*102*2##", "*#4*2*14*0170*3##", "*#4*2*13*00##")
    assert zone.hvac_mode == HVACMode.OFF
    assert _operation(zone) == "antifreeze"
    _feed(zone, "*4*1*2##")
    assert zone.hvac_mode == HVACMode.HEAT
    assert _operation(zone) is None


async def test_switched_off_zone_has_no_operation(hass):
    zone = _zone(hass, "3")
    _feed(zone, "*4*110*#3##", "*4*303*3##")
    assert zone.hvac_mode == HVACMode.OFF
    assert _operation(zone) is None
    zone._zone_operation = "manual"
    zone._handle_central_mode_update(HVACMode.OFF)
    assert _operation(zone) is None


async def test_generic_protection_is_not_given_a_season(hass):
    """`*4*302*where##`, where = [1-99] (WHO 4 1.4.6 monitor frame; "Protection (generic)", p. 5): in no capture."""
    zone = _zone(hass, "3")
    _feed(zone, "*4*1*3##", "*4*302*3##")
    assert zone.hvac_mode == HVACMode.OFF
    assert _operation(zone) == "protection"
    _feed(zone, "*4*1*3##")
    assert _operation(zone) is None


async def test_f453av_antifreeze_on_trace(hass):
    """Every zone of the F453AV capture that reports `*4*102*Z##` is off / antifreeze at the end."""
    trace = TRACES / "issue_619" / "myhome_trace_F453AV_who4_2026-10-11T11-13-06.Antifreeze.ON.json"
    frames = json.loads(trace.read_text(encoding="utf-8"))["frames"]
    zones = sorted(
        {int(match[1]) for f in frames if (match := re.fullmatch(r"\*4\*102\*(\d+)##", f["raw"]))}
    )
    assert len(zones) >= 6  # *4*102*3##, *4*102*4##, *4*102*7##, *4*102*8##, *4*102*10##, *4*102*11## ...
    for number in zones:
        zone = _zone(hass, str(number))
        _feed(zone, *_rx_zone_frames(trace, number))
        assert zone.hvac_mode == HVACMode.OFF, number
        assert _operation(zone) == "antifreeze", number


async def test_mh200n_zones_in_automatic_are_heat_with_their_operation(hass):
    """MH200N + 3550 on weekly program 1: `*4*111*#1##` .. `#5` are shown heat (OWNd), operation automatic."""
    trace = TRACES / "issue_466" / "myhome_sweep_MH200N_all_2026-11-02T12-53-15.json"
    for number in (1, 2, 3, 4, 5):
        frames = _rx_zone_frames(trace, number)
        assert f"*4*111*#{number}##" in frames
        zone = _zone(hass, str(number))
        _feed(zone, *frames)
        assert zone.hvac_mode == HVACMode.HEAT, number
        assert _operation(zone) == "automatic", number


@pytest.mark.parametrize("model", ["F455", "F461"])
async def test_zone_4_on_the_conditioning_program_is_cool_with_its_operation(hass, model):
    trace = (
        TRACES / "central_restore_3100_live_2026-11-12" / f"myhome_trace_{model}_central_restore_2026-11-12.json"
    )
    frames = _rx_zone_frames(trace, 4)
    assert "*4*211*#4##" in frames
    zone = _zone(hass, "4")
    _feed(zone, *frames[: len(frames) - frames[::-1].index("*4*211*#4##")])
    assert zone.hvac_mode == HVACMode.COOL
    assert _operation(zone) == "automatic"


async def test_dimension_7_protection_names_the_operation(hass):
    """MyHomeServer1 (`issue_429`): `*#4*4*7*1*2##` heating protection, `*#4*2*7*2*2##` cooling protection."""
    zone = _zone(hass, "4", gateway=_gateway(central=False))
    _feed(zone, "*#4*4*7*1*2##")
    assert zone.hvac_mode == HVACMode.OFF
    assert _operation(zone) == "antifreeze"
    zone = _zone(hass, "2", gateway=_gateway(central=False))
    _feed(zone, "*#4*2*7*2*2##")
    assert _operation(zone) == "thermal_protection"
    _feed(zone, "*#4*2*7*2*3##")  # cooling comfort (F461 KW4691, 2026-10-24)
    assert _operation(zone) is None


async def test_zone_operation_is_restored_and_the_central_has_none(hass):
    live = _zone(hass, "3")
    _feed(live, "*4*102*3##", "*#4*3*12*0070*3##")
    saved = State("climate.zone_3", "off", {"temperature": 7.0, **live.extra_state_attributes})
    assert saved.attributes["zone_operation"] == "antifreeze"
    restored = _zone(hass, "3")
    await restored.async_restore_last_state(saved)
    assert _operation(restored) == "antifreeze"
    other = _zone(hass, "3")
    await other.async_restore_last_state(State("climate.zone_3", "off", {"zone_operation": "holiday"}))
    assert _operation(other) is None

    central = MyHOMEClimate(
        hass=hass, name="Central", device_id="4-#0", who="4", where="#0", heating=True, cooling=True,
        fan=False, standalone=False, central=True, manufacturer="BTicino", model="Central Unit (3550)",
        gateway=_gateway(),
    )
    central.hass = hass
    central.entity_id = "climate.central"
    central.async_write_ha_state = MagicMock()
    central.handle_event(OWNHeatingEvent("*4*102*#0##"))
    assert central.extra_state_attributes["central_operation"] == "antifreeze"
    assert "zone_operation" not in central.extra_state_attributes
