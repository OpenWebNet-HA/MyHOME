"""An entity action that awaits delivery tells the caller when the gateway refused the frame.

B6-1 (audit 2026-11-18): the WHO 14 lock / unlock buttons returned success on an F455, whose
firmware 1.0.76 answers `*14*0*05##` and `*14*1*05##` with NACK (an F461 2.0.11 acknowledges
both). The end-to-end tests run the real gateway handler, command worker and OWNd command
session against the mock gateway of `tests/mock_gateway_harness.py`.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNCommand

from custom_components.myhome.button import DisableCommandButtonEntity, EnableCommandButtonEntity
from custom_components.myhome.command_delivery import (
    COMMAND_DELIVERY_TIMEOUT,
    async_confirm_delivery,
    async_send_confirmed,
    gateway_label,
)
from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_UDN,
    DOMAIN,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_sessions import FrameScheduler
from tests.mock_gateway_harness import MockGatewayHarness

GW = MagicMock(model="F455")


def test_gateway_label_prefers_model_then_name_then_mac() -> None:
    assert gateway_label(MagicMock(model=" F455 ")) == "F455"
    assert gateway_label(MagicMock(model="", name=None, mac="00:03:50:00:04:55")) == "00:03:50:00:04:55"
    assert gateway_label(object()) == "MyHOME gateway"


async def test_delivered_or_untracked_commands_return() -> None:
    """A resolved future, or a gateway double that returns no future, is a delivered command."""
    done: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    done.set_result(12.5)
    await async_confirm_delivery(done, name="Light 05 Lock", frame="*14*0*05##", gateway=GW)
    await async_confirm_delivery(MagicMock(), name="Light 05 Lock", frame="*14*0*05##", gateway=GW)
    await async_confirm_delivery(None, name="Light 05 Lock", frame="*14*0*05##", gateway=GW)


async def test_nack_is_told_apart_from_a_frame_that_was_not_delivered() -> None:
    """The worker's `_gateway_nack` mark makes `command_rejected`; a bare cancellation does not."""
    nacked: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    setattr(nacked, "_gateway_nack", True)
    nacked.cancel()
    with pytest.raises(HomeAssistantError) as err:
        await async_confirm_delivery(nacked, name="Light 05 Lock", frame="*14*0*05##", gateway=GW)
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "command_rejected"
    assert err.value.translation_placeholders == {"name": "Light 05 Lock", "gateway": "F455", "frame": "*14*0*05##"}

    dropped: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    dropped.cancel()
    with pytest.raises(HomeAssistantError) as err:
        await async_confirm_delivery(dropped, name="Light 05 Lock", frame="*14*0*05##", gateway=GW)
    assert err.value.translation_key == "command_not_delivered"
    assert err.value.__cause__ is None

    failed: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    failed.set_exception(OSError("socket closed"))
    with pytest.raises(HomeAssistantError) as err:
        await async_confirm_delivery(failed, name="Light 05 Lock", frame="*14*0*05##", gateway=GW)
    assert err.value.translation_key == "command_not_delivered"
    assert isinstance(err.value.__cause__, OSError)


async def test_unconfirmed_command_is_withdrawn() -> None:
    """Past the timeout the frame is withdrawn, so it is not written after the error was shown."""
    stuck: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    with pytest.raises(HomeAssistantError) as err:
        await async_confirm_delivery(stuck, name="Light 05 Unlock", frame="*14*1*05##", gateway=GW, timeout=0.01)
    assert err.value.translation_key == "command_not_confirmed"
    assert err.value.translation_placeholders == {
        "name": "Light 05 Unlock",
        "gateway": "F455",
        "frame": "*14*1*05##",
    }
    assert stuck.cancelled()
    assert COMMAND_DELIVERY_TIMEOUT == 15.0


async def test_cancelling_the_caller_is_not_reported_as_a_gateway_failure() -> None:
    """An external cancellation (entity removed, HA stopping) passes through and keeps the frame queued."""
    pending: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(
        async_confirm_delivery(pending, name="Light 05 Lock", frame="*14*0*05##", gateway=GW)
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not pending.done()
    pending.cancel()


async def test_cancelling_the_caller_while_the_command_is_being_queued_passes_through() -> None:
    """A caller cancelled while `send()` still waits for room in the queue is cancelled, not told of a failure."""
    entered = asyncio.Event()

    async def _blocked_send(_command: str) -> None:
        entered.set()
        await asyncio.Event().wait()

    gateway = MagicMock(model="F461")
    gateway.send = _blocked_send
    task = asyncio.create_task(async_send_confirmed(gateway, "*14*0*05##", name="Light 05 Lock"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_send_confirmed_queues_then_waits() -> None:
    gateway = MagicMock(model="F461")
    written: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    written.set_result(1.0)
    gateway.send = AsyncMock(return_value=written)
    await async_send_confirmed(gateway, "*14*0*05##", name="Light 05 Lock")
    gateway.send.assert_awaited_once_with("*14*0*05##")


# ── end to end: real handler, worker and OWNd session against a mock gateway ──────────


def _entry(port: int) -> MagicMock:
    entry = MagicMock()
    entry.data = {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: port,
        CONF_PASSWORD: None,
        CONF_SSDP_LOCATION: "",
        CONF_SSDP_ST: "",
        CONF_DEVICE_TYPE: "Gateway",
        CONF_FRIENDLY_NAME: "GW",
        CONF_MANUFACTURER: "Bticino",
        CONF_MANUFACTURER_URL: "",
        CONF_NAME: "F455",
        CONF_FIRMWARE: "1.0.76",
        CONF_MAC: "00:03:50:00:04:55",
        CONF_UDN: "1234",
    }
    entry.options = {}
    return entry


@pytest.fixture
async def plant(hass):
    """A started mock gateway, a real handler with one command worker, and the two buttons of light 05."""
    harness = MockGatewayHarness()
    port = await harness.start()
    handler = MyHOMEGatewayHandler(hass, _entry(port))
    handler._event_session_ready.set()
    worker = asyncio.create_task(handler.sending_loop(0))
    hass.data.setdefault(DOMAIN, {})[handler.mac] = {"platforms": {"button": {"light_05": {"entities": {}}}}}
    buttons = [
        cls(
            hass=hass,
            platform="button",
            name="Light 05",
            device_id="light_05",
            who="1",
            where="05",
            interface=None,
            manufacturer="BTicino",
            model="F411",
            gateway=handler,
        )
        for cls in (DisableCommandButtonEntity, EnableCommandButtonEntity)
    ]
    try:
        yield harness, handler, buttons
    finally:
        handler._command_pool.close()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await harness.stop()


async def test_lock_buttons_raise_when_the_gateway_nacks_who14(plant) -> None:
    """F455-like gateway: `*14*0*05##` / `*14*1*05##` answered with NACK -> `command_rejected`."""
    harness, _handler, (lock_btn, unlock_btn) = plant
    harness.set_nack_commands(True)
    for button, frame, name in ((lock_btn, "*14*0*05##", "Light 05 Lock"), (unlock_btn, "*14*1*05##", "Light 05 Unlock")):
        with pytest.raises(HomeAssistantError) as err:
            await button.async_press()
        assert err.value.translation_key == "command_rejected"
        assert err.value.translation_placeholders == {"name": name, "gateway": "F455", "frame": frame}
        assert frame in harness.received_messages


async def test_lock_buttons_return_when_the_gateway_acknowledges(plant) -> None:
    """F461-like gateway: both frames acknowledged, the press returns and each frame is written once."""
    harness, _handler, (lock_btn, unlock_btn) = plant
    await lock_btn.async_press()
    await unlock_btn.async_press()
    assert harness.received_messages.count("*14*0*05##") == 1
    assert harness.received_messages.count("*14*1*05##") == 1


async def test_lock_button_raises_when_the_connection_drops_before_the_answer(plant) -> None:
    """A socket closed between the write and the answer is `command_not_delivered`, not a NACK."""
    harness, _handler, (lock_btn, _unlock_btn) = plant
    await lock_btn.async_press()  # opens the command session
    harness.set_nack_commands(False)
    harness.set_disconnect_after_messages(harness._messages_processed + 1)
    with pytest.raises(HomeAssistantError) as err:
        await lock_btn.async_press()
    assert err.value.translation_key == "command_not_delivered"
    # Written once: a command is not replayed after its socket was lost (OWNd#131).
    assert harness.received_messages.count("*14*0*05##") == 2


async def test_full_startup_queue_bounds_the_entire_action(hass):
    handler = MyHOMEGatewayHandler(hass, _entry(1))
    # Startup with the event session not ready: workers do not consume the queue.
    handler._command_pool.send_buffer = FrameScheduler(maxsize=1)
    await handler.send(OWNCommand.parse("*1*1*12##"))  # tests/test_services.py:777
    cmd = OWNCommand.parse("*14*0*05##")  # tests/test_command_delivery.py:181
    press = asyncio.create_task(
        async_send_confirmed(handler, cmd, name="Light 05 Lock", timeout=0.02)
    )
    try:
        finished, _ = await asyncio.wait([press], timeout=0.3)
        assert finished, "still blocked in send_buffer.put well past command delivery deadline"
        with pytest.raises(HomeAssistantError) as e:
            await press
        assert e.value.translation_key == "command_not_confirmed"
    finally:
        press.cancel()
        await asyncio.gather(press, return_exceptions=True)
        handler._command_pool.close()


async def test_send_raises_before_delivery_future_exists(hass):
    handler = MyHOMEGatewayHandler(hass, _entry(1))
    handler._command_pool.send_buffer.shutdown(immediate=True)
    button = EnableCommandButtonEntity(
        hass, "button", "Light 05", "light_05", "1", "05", None, "BTicino", "F411", handler
    )
    try:
        await button.async_press()
    except Exception as exc:
        assert isinstance(exc, HomeAssistantError), "scheduler exception escaped the action"
        assert exc.translation_key == "command_not_delivered"
        assert (
            exc.translation_placeholders["frame"] == "*14*1*05##"
        )  # tests/test_command_delivery.py:195
    else:
        assert False, "succeeded against shut-down queue"


async def test_open_socket_silence_is_not_non_delivery(plant, monkeypatch):
    from OWNd.connection import command_session

    harness, _, (lock, unlock) = plant
    await unlock.async_press()  # opens command channel
    # No reply, not even EOF. Shorten the OWNd response deadline only.
    monkeypatch.setattr(command_session, "COMMAND_TIMEOUT", 0.05)
    harness.set_custom_handler(lambda msg, writer: None)
    with pytest.raises(HomeAssistantError) as exc:
        await lock.async_press()
    assert harness.received_messages.count("*14*0*05##") == 1  # tests/test_command_delivery.py:194
    assert exc.value.translation_key == "command_not_confirmed"


async def test_handed_over_command_names_actual_gateway_on_nack() -> None:
    """When send() delegates or fails over to a peer, NACK errors name the peer gateway."""
    standby = MagicMock(model="F455", name="Standby", mac="00:03:50:00:00:02")
    primary = MagicMock(model="F461", name="Primary", mac="00:03:50:00:00:01")
    cmd = OWNCommand.parse("*14*0*05##")

    async def _send(message: object) -> asyncio.Future[float]:
        setattr(message, "_myhome_handed_to", standby)
        fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        setattr(fut, "_gateway_nack", True)
        fut.cancel()
        return fut

    primary.send = _send
    with pytest.raises(HomeAssistantError) as err:
        await async_send_confirmed(primary, cmd, name="Light 05 Lock")
    assert err.value.translation_key == "command_rejected"
    assert err.value.translation_placeholders["gateway"] == "F455"


async def test_handed_over_command_names_actual_gateway_when_send_fails_or_times_out() -> None:
    """When send() hands off to a peer and then raises or times out, error names the peer gateway."""
    standby = MagicMock(model="F455", name="Standby", mac="00:03:50:00:00:02")
    primary = MagicMock(model="F461", name="Primary", mac="00:03:50:00:00:01")

    async def _send_fail(message: object) -> asyncio.Future[float]:
        setattr(message, "_myhome_handed_to", standby)
        raise RuntimeError("peer queue closed")

    primary.send = _send_fail
    with pytest.raises(HomeAssistantError) as err:
        await async_send_confirmed(primary, OWNCommand.parse("*14*0*05##"), name="Light 05 Lock")
    assert err.value.translation_key == "command_not_delivered"
    assert err.value.translation_placeholders["gateway"] == "F455"

    async def _send_hang(message: object) -> asyncio.Future[float]:
        setattr(message, "_myhome_handed_to", standby)
        await asyncio.sleep(5)
        raise AssertionError("unreachable")

    primary.send = _send_hang
    with pytest.raises(HomeAssistantError) as err2:
        await async_send_confirmed(
            primary, OWNCommand.parse("*14*0*05##"), name="Light 05 Lock", timeout=0.01
        )
    assert err2.value.translation_key == "command_not_confirmed"
    assert err2.value.translation_placeholders["gateway"] == "F455"


async def test_late_future_exception_after_caller_cancellation_is_consumed() -> None:
    """If the caller is cancelled while ``written`` is pending, a later exception logs no unretrieved error."""
    import gc

    loop = asyncio.get_running_loop()
    loop_errors: list[dict[str, object]] = []
    prev_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, ctx: loop_errors.append(dict(ctx)))
    try:
        pending: asyncio.Future[float] = loop.create_future()
        task = asyncio.create_task(
            async_confirm_delivery(pending, name="Light 05 Lock", frame="*14*0*05##", gateway=GW)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        pending.set_exception(RuntimeError("late session failure"))
        await asyncio.sleep(0)
        del pending
        gc.collect()
        assert not loop_errors
    finally:
        loop.set_exception_handler(prev_handler)
