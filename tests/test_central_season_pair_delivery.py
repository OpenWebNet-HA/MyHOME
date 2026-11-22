"""The season frame and its restore on the 99-zone central unit: delivery and echoes.

Ordering probes built on the frames of tests/test_crew_central_season_restore.py, not a recorded
transaction.
"""

import asyncio
import time
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.climate.const import HVACMode
from homeassistant.exceptions import HomeAssistantError

from custom_components.myhome import climate
from tests.test_crew_central_season_restore import (
    ANTIFREEZE,
    COOL,
    FLAGS,
    HEAT,
    RESTORE,
    _climate,
    _gateway,
    _replay,
    _sent,
)
from tests.test_crew_central_season_restore import _entity_base_init as _entity_base_init


async def _pending_sends(gateway: Any) -> list[asyncio.Future[float]]:
    futures: list[asyncio.Future[float]] = []

    async def send(_command: Any) -> asyncio.Future[float]:
        future: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        futures.append(future)
        return future

    gateway.send.side_effect = send
    return futures


async def _until(condition: Any) -> None:
    for _ in range(50):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")


async def test_restore_echo_right_after_a_late_write_is_still_an_echo() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, [ANTIFREEZE, *FLAGS])
    futures = await _pending_sends(gateway)
    call = asyncio.create_task(central.async_set_hvac_mode(HVACMode.HEAT))
    await _until(lambda: len(futures) == 1)
    later = time.monotonic() + climate._CENTRAL_ECHO_PENDING_LIMIT + 2
    futures[0].set_result(later)
    await _until(lambda: len(futures) == 2)
    # Written after a long wait in the queue; the F455 echo comes before the future resolves.
    with patch("custom_components.myhome.climate.time.monotonic", return_value=later + 0.001):
        _replay(central, [RESTORE])
    futures[1].set_result(later)
    await call
    assert _sent(gateway) == [HEAT, RESTORE]
    assert central.hvac_mode == HVACMode.HEAT
    assert central.extra_state_attributes["central_operation"] == "antifreeze"


async def test_off_pressed_during_the_pair_goes_out_after_the_restore() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, [ANTIFREEZE, *FLAGS])
    futures = await _pending_sends(gateway)
    cool = asyncio.create_task(central.async_set_hvac_mode(HVACMode.COOL))
    await _until(lambda: len(futures) == 1)
    off = asyncio.create_task(central.async_set_hvac_mode(HVACMode.OFF))
    await asyncio.sleep(0)
    assert _sent(gateway) == [COOL]
    futures[0].set_result(time.monotonic())
    await _until(lambda: len(futures) == 2)
    assert _sent(gateway) == [COOL, RESTORE]
    futures[1].set_result(time.monotonic())
    await _until(lambda: len(futures) == 3)
    assert _sent(gateway)[-1] == "*4*303*#0##"
    futures[2].set_result(time.monotonic())  # `off` waits for the gateway's answer
    await asyncio.wait_for(asyncio.gather(cool, off), 1)


async def test_refused_restore_raises_and_clears_only_its_echo() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, [ANTIFREEZE, *FLAGS])
    futures = await _pending_sends(gateway)
    call = asyncio.create_task(central.async_set_hvac_mode(HVACMode.HEAT))
    await _until(lambda: len(futures) == 1)
    futures[0].set_result(time.monotonic())
    await _until(lambda: len(futures) == 2)
    setattr(futures[1], "_gateway_nack", True)
    futures[1].cancel()
    with pytest.raises(HomeAssistantError, match="refused"):
        await call
    assert central._central_restore_echo is None
    assert central._central_echo is not None  # the season frame was written: its echo is still expected
