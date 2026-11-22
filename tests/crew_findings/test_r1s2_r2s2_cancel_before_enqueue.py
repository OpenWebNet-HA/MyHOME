# ruff: noqa
import asyncio
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, ON_AT_2, LEVEL_0
from custom_components.myhome import light as mod

async def test_cancelled_service_before_queue_put_keeps_empty_check(hass, monkeypatch):
    # Built scheduling; input frames from dimmer_on_at_level_0_2026_11_20/frames.json:62,67.
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S', 0)
    light, gw = _light(hass)
    verification = asyncio.get_running_loop().create_future()
    async def check(msg):
        return verification
    gw.send_status_request = check
    _feed(light, ON_AT_2, LEVEL_0)
    assert light.is_on
    blocked = asyncio.Event()
    queued = []
    async def send(cmd):
        blocked.set()
        await asyncio.Event().wait()  # a full scheduler; has not made the put yet
        queued.append(cmd)
    gw.send = send
    service = asyncio.create_task(light.async_turn_on())
    await blocked.wait()
    service.cancel()
    await asyncio.gather(service, return_exceptions=True)
    assert service.cancelled() and not queued
    verification.set_result(0.0)
    await hass.async_block_till_done()
    assert light.is_on is False, 'cancelled before any ON was queued, but pending verification was discarded'
