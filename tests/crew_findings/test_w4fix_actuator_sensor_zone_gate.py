# ruff: noqa
# Reproduction r1s0_actuator_sensor_zone_gate of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from unittest.mock import AsyncMock
from custom_components.myhome.sensor import MyHOMEActuatorOutputSensor
from tests.test_f455_zone_polling import _handler


async def test_actuator_sensor_zone_gate(hass):
    gateway = _handler(hass, 'F453')
    gateway.send_status_request = AsyncMock()
    sensor = MyHOMEActuatorOutputSensor(hass, 'Heating Zone', '2', '2#1', '2', '1', None, None, 'BTicino', 'Heating Zone', gateway)
    assert not gateway.actuator_read_refused('2#1')
    await sensor.async_update()  # HA update_entity
    assert [str(c.args[0]) for c in gateway.send_status_request.await_args_list] == ['*#4*2#1*20##']  # results/F453/010100/oracle/full/thermo-zone-requests.tsv:23
