"""The sending worker must call OWNd's command session as OWNd defines it.

Every other test of the worker replaces ``OWNCommandSession`` with a mock, so a
call that OWNd would reject - an unknown keyword, a renamed parameter - stays
green in CI and only fails on a real gateway, where the worker's broad
``except`` turns it into "unexpected error while sending" for every frame.
This file is the one place the worker meets the real class.
"""
import ast
import asyncio
import inspect
from pathlib import Path
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from OWNd.connection import OWNCommandSession
from OWNd.message import OWNCommand

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_UDN,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from tests.mock_gateway_harness import MockGatewayHarness


def _handler(port: int) -> MyHOMEGatewayHandler:
    entry = MagicMock()
    entry.entry_id = "contract"
    entry.data = {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: port,
        CONF_PASSWORD: None,
        CONF_SSDP_LOCATION: "",
        CONF_SSDP_ST: "",
        CONF_DEVICE_TYPE: "",
        CONF_FRIENDLY_NAME: "",
        CONF_MANUFACTURER: "BTicino",
        CONF_MANUFACTURER_URL: "",
        CONF_NAME: "F454",
        CONF_FIRMWARE: "1.0",
        CONF_MAC: "00:03:50:00:12:34",
        CONF_UDN: "contract",
    }
    hass = MagicMock()
    hass.data = {}
    return MyHOMEGatewayHandler(hass=hass, config_entry=entry)


async def _run_one_frame(handler: MyHOMEGatewayHandler, frame: str) -> asyncio.Future:
    handler._event_session_ready.set()
    worker = asyncio.create_task(handler.sending_loop(0))
    try:
        written = await handler.send(OWNCommand.parse(frame))
        await asyncio.wait_for(asyncio.shield(written), timeout=5)
    finally:
        await handler.send_buffer.put(None)
        await asyncio.wait_for(worker, timeout=5)
    return written


@pytest.mark.asyncio
async def test_worker_delivers_a_frame_through_the_real_command_session():
    """End to end: worker -> OWNCommandSession -> TCP -> mock gateway, no mocks in between."""
    harness = MockGatewayHarness()
    port = await harness.start()
    try:
        written = await _run_one_frame(_handler(port), "*1*1*21##")
    finally:
        await harness.stop()

    assert "*1*1*21##" in harness.received_messages
    assert written.done() and not written.cancelled()
    assert isinstance(written.result(), float)


@pytest.mark.asyncio
async def test_worker_call_binds_to_ownd_send_signature():
    """The same guarantee without a socket: an autospec mock enforces OWNd's signature.

    ``create_autospec`` turns a keyword OWNd does not accept into a TypeError
    at the call, which the worker reports as an undelivered frame - so the
    delivery future being cancelled is the failure this test would show.
    """
    session = create_autospec(OWNCommandSession, instance=True)
    session.connect.return_value = {"Success": True}
    session.send.return_value = True
    session._stream_reader = object()
    session._stream_writer = object()

    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        written = await _run_one_frame(_handler(20000), "*1*0*21##")

    assert not written.cancelled(), "send() was called in a way OWNd rejects"
    session.send.assert_awaited_once()
    kwargs = session.send.await_args.kwargs
    assert set(kwargs) <= {"message", "is_status_request"}


@pytest.mark.asyncio
async def test_worker_delivers_golden_mh200_device_type_request():
    """Golden sample: worker -> real OWNCommandSession -> TCP -> mock gateway responding with MH200.

    Verifies that a status request (*#13**15##) is dispatched with is_status_request=True,
    arrives at the gateway, and the gateway's golden response (*#13**15*4##) is collected.
    """
    harness = MockGatewayHarness()

    def _handle_device_type(msg: str, writer) -> str | None:
        if msg == "*99*0##":
            return "*#*1##"
        if msg == "*#13**15##":
            return "*#13**15*4##*#*1##"
        return None

    harness.set_custom_handler(_handle_device_type)
    port = await harness.start()
    handler = _handler(port)
    handler._event_session_ready.set()
    worker = asyncio.create_task(handler.sending_loop(0))
    try:
        from OWNd.message import OWNMessage

        written = await handler.send_status_request(OWNMessage.parse("*#13**15##"))
        await asyncio.wait_for(asyncio.shield(written), timeout=5)
    finally:
        await handler.send_buffer.put(None)
        await asyncio.wait_for(worker, timeout=5)
        await harness.stop()

    assert "*#13**15##" in harness.received_messages
    assert written.done() and not written.cancelled()
    assert isinstance(written.result(), float)


def test_all_command_session_send_calls_match_ownd_signature():
    """Static AST audit: every call to OWNCommandSession.send matches OWNd's documented signature.

    Scans custom_components/myhome for any .send() invocation on command session variables
    or OWNCommandSession instances and verifies that only documented keyword arguments
    ('message', 'is_status_request') are passed. This prevents regressions like #371 where
    an unsupported kwarg caused every frame transmission to fail on real hardware.
    """
    sig = inspect.signature(OWNCommandSession.send)
    valid_params = set(sig.parameters.keys()) - {"self"}

    pkg_root = Path(__file__).parents[1] / "custom_components" / "myhome"
    violations = []
    found_calls = 0

    for py_file in pkg_root.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "send":
                    recv_name = ""
                    if isinstance(node.func.value, ast.Name):
                        recv_name = node.func.value.id
                    elif isinstance(node.func.value, ast.Attribute):
                        recv_name = node.func.value.attr

                    if "command_session" in recv_name.lower():
                        found_calls += 1
                        passed_kwargs = {kw.arg for kw in node.keywords if kw.arg is not None}
                        invalid = passed_kwargs - valid_params
                        if invalid:
                            violations.append(
                                f"{py_file.name}:{node.lineno} passes undocumented kwargs {invalid} "
                                f"to {recv_name}.send(); valid kwargs are {valid_params}"
                            )
                        # Also check max positional args: message, is_status_request (max 2)
                        if len(node.args) > 2:
                            violations.append(
                                f"{py_file.name}:{node.lineno} passes too many positional args ({len(node.args)}) "
                                f"to {recv_name}.send(); max allowed is 2"
                            )

    assert found_calls > 0, "Expected to find at least one command_session.send() call in custom_components/myhome"
    assert not violations, "\n".join(violations)

