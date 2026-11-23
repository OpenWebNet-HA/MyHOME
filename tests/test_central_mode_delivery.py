"""Central unit mode commands: what the service does when a frame of the season / program pair is
refused, not written, not confirmed, or overtaken by a report or by a later command.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACMode
from homeassistant.exceptions import HomeAssistantError
from OWNd.exceptions import OWNConnectionError
from OWNd.message import OWNHeatingCommand

from custom_components.myhome.gateway_sessions import (
    CommandWorkerPool,
    _cancel_written,
    _resolve_written,
)
from tests import test_central_auto_not_projected_2026_11_22, test_gateway_sessions
from tests.test_central_auto_not_projected_2026_11_22 import ZONE_3_MANUAL
from tests.test_central_season_pair_delivery import _pending_sends, _until
from tests.test_crew_central_season_restore import (
    ANTIFREEZE,
    COOL,
    HEAT,
    HEATING_PROGRAM,
    RESTORE,
    THERMAL_PROTECTION,
    _climate,
    _gateway,
    _replay,
    _sent,
)
from tests.test_crew_central_season_restore import (
    _entity_base_init as _entity_base_init,
)
from tests.test_f455_zone_polling import _handler
from tests.test_issue_944_actuator_answer_not_central import _plant, _receive
from tests.test_probe_fallback_read import _command_session, _make_handler

# Fixtures of other test modules, asked for by name below.
mock_handler = test_gateway_sessions.mock_handler
plant = test_central_auto_not_projected_2026_11_22.plant


@pytest.mark.parametrize(
    ("reported", "mode", "season", "refused", "error"),
    [
        # The season frame is dropped before it is written (a cancelled receipt, no refusal).
        (HEATING_PROGRAM, HVACMode.COOL, COOL, False, "did not confirm"),
        # The gateway refuses the season frame (the command worker marks the receipt, gateway_sessions.py).
        # A season frame refused with nothing put on the bus, emulated firmware only:
        # own-firmware-oracle results/MH200/020102/oracle/full/thermo-central-mode.tsv:18.
        (HEATING_PROGRAM, HVACMode.COOL, COOL, True, "refused"),
        (THERMAL_PROTECTION, HVACMode.HEAT, HEAT, True, "refused"),
    ],
)
async def test_a_season_frame_that_failed_is_not_followed_by_the_program(
    reported: str, mode: HVACMode, season: str, refused: bool, error: str
) -> None:
    gw = _gateway()
    central = _climate(gw, "#0", standalone=None, central=True)
    _replay(central, [reported])

    async def send(command):
        receipt = asyncio.get_running_loop().create_future()
        if str(command) == season:
            if refused:
                receipt._gateway_nack = True
            receipt.cancel()
        else:
            receipt.set_result(time.monotonic())
        return receipt

    gw.send.side_effect = send
    with pytest.raises(HomeAssistantError, match=error):
        await central.async_set_hvac_mode(mode)
    assert _sent(gw) == [season], (
        "the program of a season that was not selected must not be started"
    )


async def test_worker_nack_of_restore_reaches_caller(mock_handler):
    ready = asyncio.Event()
    ready.set()
    pool = CommandWorkerPool(mock_handler, event_session_ready=ready)
    central = _climate(mock_handler, "#0", standalone=None, central=True)
    mock_handler.send = pool.send
    _replay(central, [ANTIFREEZE])
    session = MagicMock()
    session.is_connected = True
    session._stream_reader = object()
    session._stream_writer = object()
    session.connect = AsyncMock(return_value={"Success": True})
    session.close = AsyncMock()
    session.send = AsyncMock(side_effect=[True, None])
    session.last_error.reason = "nack"
    mock_handler.config_entry = None  # ordinary target, not an expose binding
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        call = asyncio.create_task(central.async_set_hvac_mode(HVACMode.HEAT))
        worker = asyncio.create_task(pool.sending_loop(0))
        try:
            with pytest.raises(HomeAssistantError, match="refused"):
                await asyncio.wait_for(call, 1)
        finally:
            await pool.send_buffer.put(None)
            await asyncio.wait_for(worker, 1)
    assert [str(c.kwargs["message"]) for c in session.send.await_args_list] == [HEAT, RESTORE]


async def test_unwritten_restore_fails_service():
    """A scheduled write can fail after gateway.send has returned its receipt."""
    gw = _gateway()
    central = _climate(gw, "#0", standalone=None, central=True)
    _replay(central, [ANTIFREEZE])

    async def send(cmd):
        receipt = asyncio.get_running_loop().create_future()
        if str(cmd) == HEAT:
            receipt.set_result(time.monotonic())
        else:
            assert str(cmd) == RESTORE
            # Same as an unavailable command session: gateway_sessions.py:1540-1547
            asyncio.get_running_loop().call_soon(receipt.cancel)
        return receipt

    gw.send.side_effect = send
    with pytest.raises(HomeAssistantError, match="did not confirm"):
        await central.async_set_hvac_mode(HVACMode.HEAT)
    assert central._central_restore_echo is None


async def test_unwritten_restore():
    gw = _gateway()
    # Use the real send() queue/future contract with a controlled consumer.
    gw.gateway.profile.max_queue_size = 250
    pool = CommandWorkerPool(gw)

    async def send(cmd):
        return await CommandWorkerPool.send(pool, cmd)

    gw.send.side_effect = send
    cu = _climate(gw, "#0", standalone=None, central=True)
    _replay(cu, [ANTIFREEZE])
    service = asyncio.create_task(cu.async_set_hvac_mode(HVACMode.HEAT))
    first = await asyncio.wait_for(pool.send_buffer.get(), 1)
    _resolve_written(first, time.monotonic())
    second = await asyncio.wait_for(pool.send_buffer.get(), 1)
    assert str(second["message"]) == RESTORE
    _cancel_written(second)
    await asyncio.sleep(0)
    assert cu._central_restore_echo is None
    (result,) = await asyncio.gather(service, return_exceptions=True)
    assert isinstance(result, BaseException), (
        "service reported success for a restore that was never written"
    )


async def test_restore_applied_but_command_ack_lost(hass):
    handler = _make_handler(hass, "F455")
    handler.is_connected = True
    pool = handler._command_pool
    pool._event_session_ready.set()
    central = _climate(handler, "#0", standalone=None, central=True)
    _replay(central, [ANTIFREEZE])
    session = _command_session()

    async def on_wire(message, is_status_request=False):
        if str(message) == HEAT:
            return True
        assert str(message) == RESTORE
        # Program report on the separate monitor; socket lost before ACK on command session.
        _replay(central, [HEATING_PROGRAM])
        session.last_error = OWNConnectionError(
            "Connection lost before acknowledgement", reason="connection_lost"
        )
        return None

    session.send = AsyncMock(side_effect=on_wire)
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        worker = asyncio.create_task(pool.sending_loop(0))
        try:
            with pytest.raises(HomeAssistantError, match="did not confirm") as error:
                await central.async_set_hvac_mode(HVACMode.HEAT)
            assert central.extra_state_attributes["program"] == 1
            assert "did not write" not in str(error.value), (
                "a lost acknowledgement is not evidence that the program frame was never written"
            )
        finally:
            await pool.send_buffer.put(None)
            await worker


async def test_refused_restore_has_a_frontend_translation(hass):
    """The restore refusal is a HA service exception, not just a log message."""
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
    with pytest.raises(HomeAssistantError, match="refused") as error:
        await central.async_set_hvac_mode(HVACMode.HEAT)
    assert error.value.translation_key is not None


async def test_central_timeout_after_transmit(hass, monkeypatch):
    handler = _make_handler(hass, "F455")
    handler.is_connected = True
    pool = handler._command_pool
    pool._event_session_ready.set()
    central = _climate(handler, "#0", standalone=None, central=True)
    _replay(central, [ANTIFREEZE])
    session = _command_session()
    transmitted = []
    ack = asyncio.Event()

    async def on_wire(message, is_status_request=False):
        transmitted.append(str(message))
        await ack.wait()  # command was sent; only ACK is outstanding
        return True

    session.send = AsyncMock(side_effect=on_wire)
    monkeypatch.setattr("custom_components.myhome.climate.SETPOINT_DELIVERY_TIMEOUT", 0.1)
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        worker = asyncio.create_task(pool.sending_loop(0))
        try:
            with pytest.raises(HomeAssistantError) as error:
                await central.async_set_hvac_mode(HVACMode.HEAT)
            assert transmitted == [HEAT]
            assert error.value.translation_key != "central_command_not_written"
        finally:
            ack.set()
            await pool.send_buffer.put(None)
            await worker


async def test_terminal_command_worker_refusal_does_not_hold_central_lock_forever(hass):
    gw = _handler(hass, "F455")
    central = _climate(gw, "#0", standalone=None, central=True)
    _replay(central, [ANTIFREEZE])
    gw._event_session_ready.set()
    call = asyncio.create_task(central.async_set_hvac_mode(HVACMode.HEAT))
    await asyncio.sleep(0)
    with patch("custom_components.myhome.gateway.OWNCommandSession") as session:
        session.return_value.connect = AsyncMock(
            return_value={"Success": False, "Message": "connection_refused"}
        )
        session.return_value.close = AsyncMock()
        await gw._command_pool.sending_loop(1)
    try:
        # Worker has terminated, not merely returned a late ACK. Advance the event-loop
        # clock so any reasonable service-side delivery deadline would fire.
        loop = asyncio.get_running_loop()
        with patch.object(loop, "time", return_value=loop.time() + 3600):
            for _ in range(8):
                await asyncio.sleep(0)
        assert call.done(), (
            "No sender left, but HEAT still holds the central command lock an hour later"
        )
        assert isinstance(call.exception(), HomeAssistantError)
    finally:
        gw._command_pool.close()
        await asyncio.gather(call, return_exceptions=True)


async def test_newer_off_is_not_undone_by_older_restore(mock_handler):
    # Equivalent to a nearly full production scheduler with one free place per dequeue.
    mock_handler.gateway.profile.max_queue_size = 1
    pool = CommandWorkerPool(mock_handler)
    central = _climate(mock_handler, "#0", standalone=None, central=True)
    mock_handler.send = pool.send
    _replay(central, [ANTIFREEZE])
    off = OWNHeatingCommand.set_central_mode(where="#0", mode="off")
    await pool.send(off)  # fill the queue
    heat_task = asyncio.create_task(central.async_set_hvac_mode(HVACMode.HEAT))
    await asyncio.sleep(0)
    off_task = asyncio.create_task(central.async_set_hvac_mode(HVACMode.OFF))
    await asyncio.sleep(0)
    sent = []
    for _ in range(20):
        if not pool.send_buffer.empty():
            item = pool.send_buffer.get_nowait()
            sent.append(str(item["message"]))
            item["written"].set_result(time.monotonic())
        await asyncio.sleep(0)
    await asyncio.wait_for(asyncio.gather(heat_task, off_task), 1)
    assert sent[1] == HEAT
    assert sent[-1] == str(off), f"The program command undoes the later OFF request: {sent[1:]}"


async def test_program_report_during_auto_delivery_is_not_overwritten(plant):
    """Report before command-session ACK (composed order)."""
    gateway, central, zone, _ = await plant("3")
    _replay(zone, [ZONE_3_MANUAL])
    delivery = asyncio.get_running_loop().create_future()
    gateway.send.return_value = delivery
    call = asyncio.create_task(central.async_set_hvac_mode(HVACMode.AUTO))
    await asyncio.sleep(0)
    assert not call.done()
    _replay(central, [HEATING_PROGRAM])
    assert central.hvac_mode == HVACMode.HEAT
    assert central.extra_state_attributes["program"] == 1
    delivery.set_result(time.monotonic())
    await call
    assert central.hvac_mode == HVACMode.HEAT, (
        "resolved heating program was replaced by the auto command on ACK"
    )
    assert central.extra_state_attributes["program"] == 1


async def test_manual_run_confirmed_while_season_waits_for_ack():
    gw = _gateway()
    central = _climate(gw, "#0", standalone=None, central=True)
    _replay(central, [ANTIFREEZE])
    receipt = asyncio.get_running_loop().create_future()
    gw.send.side_effect = [receipt, None]
    call = asyncio.create_task(central.async_set_hvac_mode(HVACMode.HEAT))
    await _until(lambda: _sent(gw) == [HEAT])
    # A local manual selection while HA is waiting: real manual heating report,
    # tests/fixtures/traces/issue_466/README.md:30, not an echo of HEAT.
    _replay(central, ["*4*110#0180*#0##"])
    receipt.set_result(time.monotonic())
    await call
    assert _sent(gw) == [HEAT], "Must not replace the newly confirmed matching manual setpoint"


async def test_stale_season_under_lock():
    gateway = _gateway()
    central = _climate(gateway, "#0", central=True, standalone=None)
    _replay(central, [THERMAL_PROTECTION])
    futures = await _pending_sends(gateway)
    heat = asyncio.create_task(central.async_set_hvac_mode(HVACMode.HEAT))
    await _until(lambda: len(futures) == 1)
    slider = asyncio.create_task(central.async_set_temperature(temperature=16.5))
    await asyncio.sleep(0)
    _replay(central, [ANTIFREEZE])
    futures[0].set_result(time.monotonic())
    await _until(lambda: len(futures) == 2)
    _replay(central, [HEATING_PROGRAM])
    futures[1].set_result(time.monotonic())
    await _until(lambda: len(futures) == 3)
    futures[2].set_result(time.monotonic())
    await asyncio.gather(heat, slider)
    assert (
        _sent(gateway)[-1] == "*#4*#0*#14*0165*1##"
    )  # issue_697/f454_99zone_central_subordinate_zones.json:38


async def test_nacked_central_setpoint():
    gateway = _gateway()
    central = _climate(gateway, "#0", central=True, standalone=None)
    _replay(central, ["*4*110#0180*#0##"])  # tests/test_crew_central_season_restore.py:116
    assert central.target_temperature == 18.0
    nack = asyncio.get_running_loop().create_future()
    nack._gateway_nack = True
    nack.cancel()
    gateway.send.return_value = nack
    with pytest.raises(HomeAssistantError):
        await central.async_set_temperature(temperature=16.5)
    assert central.target_temperature == 18.0
