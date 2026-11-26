"""One cancelled waiter must not cancel the WHO 4 zone-request run every zone shares.

``MyHOMEGatewayHandler.wait_for_zone_requests`` (#886 / #887, used again by the silent-zone
poll of #932 / #933) is awaited by each climate entity's startup poll. Those polls are
background tasks that are cancelled when their entity is removed (``_poll_on_add_task`` in
``myhome_device.py``); the run they wait for (``_zone_request_task``) carries the frames of
all the other zones too.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from OWNd.profiles import get_gateway_profile

from custom_components.myhome.const import CONF_FIRMWARE
from custom_components.myhome.gateway import ALL_ZONE_REQUESTS, MyHOMEGatewayHandler


def _make_handler(hass: Any, model: str) -> MyHOMEGatewayHandler:
    entry = MagicMock()
    entry.entry_id = f"entry-waiter-{model}"
    entry.data = {
        CONF_HOST: "192.0.2.87",
        CONF_PORT: 20000,
        CONF_PASSWORD: "12345",
        CONF_NAME: model,
        CONF_FIRMWARE: "2.0.11",
        CONF_MAC: "00:03:50:00:08:87",
    }
    entry.runtime_data = None
    handler = MyHOMEGatewayHandler(hass, entry)
    handler.gateway.profile = get_gateway_profile(model)
    return handler


async def test_cancelled_waiter_leaves_the_shared_zone_request_run_alone(hass: Any) -> None:
    """A zone removed while it waits does not take the other zones' frames with it."""
    handler = _make_handler(hass, "F461")
    handler._initial_discovery_done.set()
    sending = asyncio.Event()
    release = asyncio.Event()
    sent: list[list[str]] = []

    async def _send_paced(frames: list[str]) -> None:
        sending.set()
        await release.wait()  # the paced run is on the wire: BUS_QUIET_PERIOD between frames
        sent.append(list(frames))

    handler.send_paced = _send_paced  # type: ignore[method-assign]

    # Two zones ask together: one run, one frame for every zone (ALL_ZONE_REQUESTS on F461).
    handler.request_zone_dimension("2", 60)
    handler.request_zone_dimension("4", 60)
    run = handler._zone_request_task
    assert run is not None

    waiter_zone_2 = asyncio.create_task(handler.wait_for_zone_requests())
    waiter_zone_4 = asyncio.create_task(handler.wait_for_zone_requests())
    await sending.wait()

    # Zone 2's entity is removed: its startup poll (the waiter) is cancelled.
    waiter_zone_2.cancel()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert waiter_zone_2.cancelled()
    assert not run.cancelled()
    assert not run.done()
    assert not waiter_zone_4.done()

    release.set()
    await waiter_zone_4
    assert run.done() and not run.cancelled()
    assert sent == [[ALL_ZONE_REQUESTS["F461"][60]]]


async def test_waiter_returns_when_the_run_is_cancelled_by_unload(hass: Any) -> None:
    """Closing the handler cancels the run; a waiter ends instead of raising or hanging."""
    handler = _make_handler(hass, "F461")
    handler._initial_discovery_done.set()
    started = asyncio.Event()

    async def _send_paced(frames: list[str]) -> None:
        started.set()
        await asyncio.Event().wait()

    handler.send_paced = _send_paced  # type: ignore[method-assign]
    handler.request_zone_dimension("2", 60)
    run = handler._zone_request_task
    assert run is not None
    waiter = asyncio.create_task(handler.wait_for_zone_requests())
    await started.wait()

    run.cancel()
    await asyncio.wait_for(waiter, timeout=1)
    assert run.cancelled()
    assert waiter.done() and not waiter.cancelled() and waiter.exception() is None
