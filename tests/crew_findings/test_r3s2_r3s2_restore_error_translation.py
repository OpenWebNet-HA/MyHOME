# ruff: noqa
"""The restore refusal is a HA service exception, not just a log message."""
import asyncio
import time

import pytest
from homeassistant.components.climate.const import HVACMode
from homeassistant.exceptions import HomeAssistantError

from tests.test_crew_central_season_restore import ANTIFREEZE, RESTORE
from tests.test_issue_944_actuator_answer_not_central import _plant, _receive


async def test_refused_restore_has_a_frontend_translation(hass):
    handler, entities = await _plant(hass)
    await _receive(hass, handler, [ANTIFREEZE])
    central = next(c for c in entities if c._central)

    async def send(command):
        future = asyncio.get_running_loop().create_future()
        if str(command) == RESTORE:
            future._gateway_nack = True
            future.cancel()
        else:
            future.set_result(time.monotonic())
        return future

    handler.send.side_effect = send
    with pytest.raises(HomeAssistantError, match='refused') as error:
        await central.async_set_hvac_mode(HVACMode.HEAT)
    assert error.value.translation_key is not None
