# ruff: noqa
"""Report before command-session ACK (composed order)."""
import asyncio
import time
from homeassistant.components.climate import HVACMode
from tests.test_central_auto_not_projected_2026_11_22 import plant, HEATING_PROGRAM, ZONE_3_MANUAL, _replay

async def test_program_report_during_auto_delivery_is_not_overwritten(plant):
    gateway, central, zone, _ = await plant('3')
    _replay(zone, [ZONE_3_MANUAL])
    delivery = asyncio.get_running_loop().create_future()
    gateway.send.return_value = delivery
    call = asyncio.create_task(central.async_set_hvac_mode(HVACMode.AUTO))
    await asyncio.sleep(0)
    assert not call.done()
    _replay(central, [HEATING_PROGRAM])
    assert central.hvac_mode == HVACMode.HEAT
    assert central.extra_state_attributes['program'] == 1
    delivery.set_result(time.monotonic())
    await call
    assert central.hvac_mode == HVACMode.HEAT, 'resolved heating program was replaced by the auto command on ACK'
    assert central.extra_state_attributes['program'] == 1
