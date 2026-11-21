"""A zone configured as ``#Z`` (``myhome.yaml``) or stored as ``4-Z`` (a registry id of an old version) is one zone.

Every status request and every write of it must be the frame the same zone configured as ``Z`` sends:
``*#4*#2##`` is "Zone 2 via Central Unit" (WHO 4 PDF, addressing table), which a live F461 2.0.11 NACKed for a
thermostat the central unit was not running while ``*#4*2##`` gave the thermostat's five frames (2026-11-17), and
``4-2`` is no WHERE at all. The central unit, an actuator address and a probe keep their WHERE.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.myhome.climate import MyHOMEClimate
from tests.test_f455_zone_polling import _handler, _queued, _ready, _zone


def _fan_zone(hass, handler, where: str) -> MyHOMEClimate:
    zone = MyHOMEClimate(
        hass=hass, name="Zone 2", device_id=f"4-{where}", who="4", where=where, heating=True, cooling=True,
        fan=True, humidity=True, standalone=True, central=False, manufacturer="BTicino", model="Heating Zone",
        gateway=handler,
    )
    zone.hass = hass
    zone.entity_id = "climate.zone_2"
    zone.async_write_ha_state = MagicMock()
    return zone


async def _frames(hass, where: str) -> dict[str, list]:
    """What a zone stored under ``where`` asks and writes."""
    handler = _handler(hass, "F461")
    _ready(handler)
    handler.request_zone_dimension = MagicMock()
    zone = _fan_zone(hass, handler, where)
    zone._fan_coil_sides = {"cooling": True, "heating": True}
    await zone._async_send_zone_polls()
    polls = _queued(handler)
    zone._fan_coil_sides = {}
    zone._ask_fan_coil_sides()
    asked = [call.args for call in handler.request_zone_dimension.call_args_list]
    handler.send = AsyncMock()
    await zone.async_set_temperature(temperature=21.0)
    await zone.async_set_hvac_mode("off")
    await zone.async_set_fan_mode("low")
    writes = [str(call.args[0]) for call in handler.send.await_args_list]
    return {"polls": polls, "asked": asked, "writes": writes}


@pytest.mark.parametrize("where", ["#2", "4-2"])
async def test_a_zone_under_another_spelling_sends_what_zone_2_sends(hass, where: str) -> None:
    reference = await _frames(hass, "2")
    assert reference["polls"] and reference["writes"], reference
    assert all("#2" not in frame.split("*")[2] and "4-2" not in frame for frame in reference["polls"])
    assert await _frames(hass, where) == reference


async def test_the_central_unit_an_actuator_and_a_probe_keep_their_where(hass) -> None:
    handler = _handler(hass, "F461")
    _ready(handler)
    central = _zone(hass, handler, "#0")
    assert central._central is True
    await central._async_send_zone_polls()
    assert _queued(handler) == ["*#4*#0##"]
    assert _zone(hass, handler, "2#1")._request_where == "2#1"
    assert _zone(hass, handler, "105")._request_where == "105"
    assert _zone(hass, handler, "#3", interface="1")._request_where == "3#4#01"
