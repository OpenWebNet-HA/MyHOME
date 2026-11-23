"""The fallback read of a temperature probe (``WHERE >= 100``): a wider window and a latch.

A probe sends its reading by itself (#308); the periodic update asks it only when no reading
arrived for a while: a slave probe ``PZZ`` with ``*#4*PZZ*0##`` (WHO 4 PDF 1.5.1; answered by an
F455 1.0.76 and an F461 2.0.11, the two files of 2026-11-18 in issue_932), an external probe
``N00`` with ``*#4*N00*15##`` as before. Until then every probe was asked ``*#4*ZPP*15##``, and
the captures below are of that read. On a physical plant (F455 1.0.76 and an F461 2.0.11 on one bus,
2026-11-17) probe 303 reported every 10 minutes (``*#4*303*0*0229##`` 08:31:22Z,
``*#4*303*0*0230##`` 08:41:23Z). With the poll interval (300 s) as the window every second update
sent ``*#4*303*15##``, and the F455 NACKed each one only after 9.3 to 9.4 s, holding a command
session that long; one such read was ACKed after 1.6 s because the probe's own report arrived
while it was out.

- A reading of a probe keeps the read back for ``PROBE_PUSH_FRESH`` (900 s), three poll intervals.
- A read the gateway refused is not sent again. Refused means that the sender marked the read:
  the gateway answered it with a NACK, or with nothing before the command timeout. Not a refusal:
  a read that was cancelled without such an answer (the command session could not be opened or
  lost its socket), a read dropped by the #886 guard, a read cancelled with the connection, and a
  read during which the probe reported.
- The #886 guard checks the read of a probe like the read of a zone.
- A zone sensor (``WHERE < 100``) keeps its window and is asked as before.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.exceptions import (
    OWNConnectionError,
    OWNError,
    OWNNackError,
    OWNProtocolError,
    OWNTimeoutError,
)
from OWNd.message import OWNHeatingCommand, OWNHeatingEvent, OWNMessage

from custom_components.myhome.gateway_sessions import (
    CommandWorkerPool,
    _mismatched_who4_reply_zones,
    _who4_zone_token,
)
from custom_components.myhome.sensor import PROBE_PUSH_FRESH, SCAN_INTERVAL, MyHOMETemperatureSensor
from custom_components.myhome.where_grammar import is_external_probe
from tests.mock_gateway_harness import MockGatewayHarness
from tests.test_command_session_contract import _handler as _handler_on_port
from tests.test_issue_886_myhomeserver1_41_zones import _make_handler

READ = "*#4*303*0##"  # the read of a slave probe (PZZ)
DIM15_READ = "*#4*303*15##"  # what probe 303 was asked in the captures of 2026-11-17; refused by both gateways
REPORT = "*#4*303*0*0229##"


@pytest.fixture
def gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:11:06"
    gw.send_status_request = AsyncMock()
    gw.log_id = "[F455 gateway]"
    gw.device_registry_id = None
    gw.is_connected = True
    gw.event_connections = 1  # the count of its event sessions, as the handler keeps it
    gw._who4_target = MagicMock(return_value=gw)  # no secondary and no standby: it serves WHO 4 itself
    return gw


def _sensor(hass, gateway, where: str = "303") -> MyHOMETemperatureSensor:
    sensor = MyHOMETemperatureSensor(
        hass=hass,
        name=f"Probe {where}",
        device_id=f"temp_{where}",
        who="4",
        where=where,
        device_class="temperature",
        manufacturer="BTicino",
        model="Probe",
        gateway=gateway,
    )
    sensor.hass = hass
    sensor.entity_id = f"sensor.probe_{where}"
    sensor.async_schedule_update_ha_state = MagicMock()
    return sensor


def _sent(gateway) -> list[str]:
    return [str(call.args[0]) for call in gateway.send_status_request.await_args_list]


def _refused(hass, mark: str = "_gateway_nack") -> asyncio.Future:
    """What the sender leaves of a read the gateway NACKed: a cancelled future with its mark."""
    written = hass.loop.create_future()
    setattr(written, mark, True)
    written.cancel()
    return written


async def _settle() -> None:
    for _ in range(3):  # the done callback of a future runs on the next loop turn
        await asyncio.sleep(0)


def test_the_window_of_a_probe_covers_a_ten_minute_report_interval() -> None:
    assert PROBE_PUSH_FRESH == 900.0
    assert PROBE_PUSH_FRESH > 600 + SCAN_INTERVAL.total_seconds() - 1  # a report every 10 min, checked every 5


@pytest.mark.parametrize(("age", "asked"), [(299.0, False), (301.0, False), (601.0, False), (899.0, False), (901.0, True)])
async def test_a_probe_is_asked_only_after_fifteen_minutes_of_silence(hass, gateway, age, asked) -> None:
    probe = _sensor(hass, gateway)
    probe.handle_event(OWNHeatingEvent(REPORT))
    assert probe.native_value == 22.9
    probe._last_push_at = time.monotonic() - age
    await probe.async_update()
    assert _sent(gateway) == ([READ] if asked else [])


@pytest.mark.parametrize(("age", "asked"), [(299.0, False), (301.0, True)])
async def test_a_zone_sensor_keeps_the_poll_interval_as_its_window(hass, gateway, age, asked) -> None:
    zone = _sensor(hass, gateway, "2")
    zone.handle_event(OWNHeatingEvent("*#4*2*0*0239##"))
    zone._last_push_at = time.monotonic() - age
    await zone.async_update()
    assert _sent(gateway) == (["*#4*2*0##"] if asked else [])


@pytest.mark.parametrize(
    ("where", "read"),
    [
        ("303", "*#4*303*0##"),  # probe 3 of zone 03, F455 / F461 plant (issue_932)
        ("105", "*#4*105*0##"),  # probe 1 of zone 05, MH200N / MH201 plant (issue_466)
        ("101", "*#4*101*0##"),
        ("899", "*#4*899*0##"),
    ],
)
async def test_a_slave_probe_is_asked_for_dimension_0(hass, gateway, where, read) -> None:
    """WHO 4 PDF 1.5.1: "Slave probe temperature request: where = [1-8]+[01-99]", ``*#4*where*0##``."""
    probe = _sensor(hass, gateway, where)
    assert probe._is_probe is True and is_external_probe(where) is False
    await probe.async_update()
    assert _sent(gateway) == [read]


@pytest.mark.parametrize(("where", "read"), [("100", "*#4*100*15##"), ("900", "*#4*900*15##")])
async def test_an_external_probe_keeps_the_dimension_15_read_and_its_latch(hass, gateway, where, read) -> None:
    """``N00``: the 3455 behind an L4577 of #308 reports ``*#4*100*15*1*0253*0001##``; its read is unchanged."""
    probe = _sensor(hass, gateway, where)
    assert probe._is_probe is True and is_external_probe(where) is True
    if where == "100":  # the frame of #308, as captured
        probe.handle_event(OWNHeatingEvent("*#4*100*15*1*0253*0001##"))
        assert probe.native_value == 25.3
        probe._last_push_at = time.monotonic() - PROBE_PUSH_FRESH - 1
    gateway.send_status_request.return_value = _refused(hass)
    await probe.async_update()
    await _settle()
    assert _sent(gateway) == [read] and probe._fallback_read_refused is True
    await probe.async_update()
    assert _sent(gateway) == [read]


def test_an_external_probe_is_a_bare_n00() -> None:
    assert [w for w in ("100", "200", "900", "0100", "#100", "4-100") if not is_external_probe(w)] == []
    # A slave probe, a zone, the group of a zone's probes, an actuator of zone 10, and a number above 900.
    assert [w for w in ("101", "303", "899", "1", "99", "001", "000", "10#0", "1000", "") if is_external_probe(w)] == []


@pytest.mark.parametrize(("gw", "answered_after", "refused_after"), [("F455", 0.089, 8.948), ("F461", 0.14, 2.133)])
def test_the_direct_reads_of_probe_303_answer_dimension_0_and_refuse_dimension_15(
    hass, gateway, gw, answered_after, refused_after
) -> None:
    """issue_932/README.md, 2026-11-18: both gateways answer ``*#4*303*0##`` and NACK ``*#4*303*15##``."""
    folder = Path(__file__).parent / "fixtures" / "traces" / "issue_932"
    lines = (folder / f"{gw}_direct_read_probe_303_2026-11-18.log").read_text(encoding="utf-8").splitlines()
    at = [datetime.strptime(line.split(" ")[0], "%H:%M:%S.%f") for line in lines]
    assert [" ".join(line.split()[1:]) for line in lines] == [
        f">> {READ}",
        "<< *#4*303*0*0233##",
        "<< *#*1##",
        f">> {DIM15_READ}",
        "<< *#*0##",
    ]
    assert (at[2] - at[0]).total_seconds() == answered_after
    assert (at[4] - at[3]).total_seconds() == refused_after
    # The read the sensor sends is the answered one, and the answer is a reading of the probe.
    probe = _sensor(hass, gateway)
    assert str(probe._fallback_read()) == READ
    probe.handle_event(OWNHeatingEvent(lines[1].split()[-1]))
    assert probe.native_value == 23.3


async def test_a_refused_read_is_not_sent_again(hass, gateway, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.myhome")
    probe = _sensor(hass, gateway)
    gateway.send_status_request.return_value = _refused(hass)
    await probe.async_update()
    await _settle()
    assert _sent(gateway) == [READ] and probe._fallback_read_refused is True
    assert "refused the read of probe 303" in caplog.text
    await probe.async_update()
    await probe.async_update()
    assert _sent(gateway) == [READ]
    # Its own reports are still taken, and they do not bring the read back.
    probe.handle_event(OWNHeatingEvent(REPORT))
    assert probe.native_value == 22.9
    probe._last_push_at = time.monotonic() - PROBE_PUSH_FRESH - 1
    await probe.async_update()
    assert _sent(gateway) == [READ]


async def test_a_probe_is_asked_once_more_after_the_gateway_came_back_from_an_outage(hass, gateway, caplog) -> None:
    """The availability signal of the gateway (sent when it is unavailable, and when it is available again)."""
    caplog.set_level(logging.DEBUG, logger="custom_components.myhome")
    probe = _sensor(hass, gateway)
    probe.async_write_ha_state = MagicMock()
    gateway.send_status_request.return_value = _refused(hass)
    await probe.async_update()
    await _settle()
    assert _sent(gateway) == [READ] and probe._fallback_read_refused is True

    gateway.is_connected = False  # the outage: nothing changes, and nothing is asked
    probe._handle_availability_update()
    assert probe._fallback_read_refused is True
    await probe.async_update()
    assert _sent(gateway) == [READ]

    gateway.is_connected = True  # available again, on a new event session (the handler counts each one)
    gateway.event_connections += 1
    probe._handle_availability_update()
    assert probe._fallback_read_refused is False
    assert "the gateway is available again; probe 303 is asked once more" in caplog.text
    assert probe.async_write_ha_state.call_count == 2  # the state is written as before
    await probe.async_update()
    await _settle()
    assert _sent(gateway) == [READ, READ] and probe._fallback_read_refused is True  # refused again: latched again


async def test_a_refusal_that_takes_nine_seconds_does_not_hold_the_update(hass, gateway) -> None:
    probe = _sensor(hass, gateway)
    pending = hass.loop.create_future()
    gateway.send_status_request.return_value = pending
    await probe.async_update()  # returns with the read still out
    assert not pending.done() and probe._fallback_read_refused is False
    pending._gateway_nack = True
    pending.cancel()
    await _settle()
    assert probe._fallback_read_refused is True


async def test_a_read_that_got_no_reply_before_the_command_timeout_is_not_sent_again(hass, gateway) -> None:
    probe = _sensor(hass, gateway)
    gateway.send_status_request.return_value = _refused(hass, "_command_timeout")
    await probe.async_update()
    await _settle()
    assert probe._fallback_read_refused is True
    await probe.async_update()
    assert _sent(gateway) == [READ]


async def test_a_read_cancelled_without_an_answer_of_the_gateway_is_not_a_refusal(hass, gateway) -> None:
    """The command session could not be opened, lost its socket, or the queue was shut down."""
    probe = _sensor(hass, gateway)
    unanswered = hass.loop.create_future()
    unanswered.cancel()
    gateway.send_status_request.return_value = unanswered
    await probe.async_update()
    await _settle()
    assert probe._fallback_read_refused is False
    await probe.async_update()
    assert _sent(gateway) == [READ, READ]


async def test_an_answered_read_keeps_the_fallback(hass, gateway) -> None:
    probe = _sensor(hass, gateway)
    answered = hass.loop.create_future()
    answered.set_result(0.0)
    gateway.send_status_request.return_value = answered
    await probe.async_update()
    await _settle()
    assert probe._fallback_read_refused is False
    await probe.async_update()
    assert _sent(gateway) == [READ, READ]


async def test_a_read_dropped_by_the_desync_guard_is_not_a_refusal(hass, gateway) -> None:
    probe = _sensor(hass, gateway)
    dropped = hass.loop.create_future()
    dropped._gateway_desync = True
    dropped.cancel()
    gateway.send_status_request.return_value = dropped
    await probe.async_update()
    await _settle()
    assert probe._fallback_read_refused is False


async def test_a_read_cancelled_with_the_connection_is_not_a_refusal(hass, gateway) -> None:
    probe = _sensor(hass, gateway)
    pending = hass.loop.create_future()
    gateway.send_status_request.return_value = pending
    await probe.async_update()
    gateway.is_connected = False
    pending._command_timeout = True
    pending.cancel()
    await _settle()
    assert probe._fallback_read_refused is False


async def test_a_report_that_arrived_during_the_read_is_not_a_refusal(hass, gateway) -> None:
    probe = _sensor(hass, gateway)
    pending = hass.loop.create_future()
    gateway.send_status_request.return_value = pending
    await probe.async_update()
    probe.handle_event(OWNHeatingEvent(REPORT))
    pending._gateway_nack = True
    pending.cancel()
    await _settle()
    assert probe._fallback_read_refused is False


async def test_a_zone_sensor_has_no_latch(hass, gateway) -> None:
    zone = _sensor(hass, gateway, "2")
    gateway.send_status_request.return_value = _refused(hass)
    await zone.async_update()
    await _settle()
    assert zone._fallback_read_refused is False
    await zone.async_update()
    assert _sent(gateway) == ["*#4*2*0##", "*#4*2*0##"]


async def test_a_gateway_double_that_returns_no_future_changes_nothing(hass, gateway) -> None:
    probe = _sensor(hass, gateway)
    gateway.send_status_request.return_value = None
    await probe.async_update()
    await probe.async_update()
    assert _sent(gateway) == [READ, READ] and probe._fallback_read_refused is False


def test_the_capture_of_probe_303_is_covered_by_the_window() -> None:
    """issue_932/README.md: the probe reported 601 s apart; the read queued 563 s after the report
    Home Assistant had received (the window was the poll interval then) was NACKed 9.3 s later.
    900 s covers the time between two reports."""
    folder = Path(__file__).parent / "fixtures" / "traces" / "issue_932"
    monitor = (folder / "F455_who4_monitor_probe_303_2026-11-17T11-21.log").read_text(encoding="utf-8").splitlines()
    reports = [datetime.fromisoformat(line.split(" ")[0]) for line in monitor]
    assert [line.split(" ")[2] for line in monitor] == ["*#4*303*0*0232##", "*#4*303*0*0232##"]
    log = (folder / "F455_ha_log_probe_303_read_2026-11-17T11-31.log").read_text(encoding="utf-8").splitlines()
    received, queued, _unqueued, refused, _restart, _next = (
        datetime.fromisoformat(" ".join(line.split(" ")[:2])) for line in log
    )
    assert "Message received: `*#4*303*0*0232##`" in log[0]
    assert f"Message `{DIM15_READ}` was successfully queued." in log[1]
    assert f"Gateway rejected status request {DIM15_READ} (NACK, 0 response(s))" in log[3]
    assert 0 < (received - reports[0]).total_seconds() < 1  # the report Home Assistant had is the monitor's first
    between = (reports[1] - reports[0]).total_seconds()
    asked_after = (queued - received).total_seconds()
    assert (between, asked_after) == (601.305, 563.455)
    assert SCAN_INTERVAL.total_seconds() < asked_after < between < PROBE_PUSH_FRESH
    assert (refused - queued).total_seconds() == 9.324


# ── The sender: which cancelled read carries the mark of a refusal ──────────────────────────────


def _command_session(*, connect: list[dict[str, Any]] | None = None, is_open: bool = True) -> MagicMock:
    session = MagicMock()
    session.connect = AsyncMock(side_effect=connect or [{"Success": True}])
    session.close = AsyncMock()
    session.is_connected = True
    session._stream_reader = session._stream_writer = object() if is_open else None
    session.last_error = None
    session.send = AsyncMock(return_value=True)
    return session


async def _read_through_the_sender(hass, gateway, session) -> tuple[MyHOMETemperatureSensor, asyncio.Future]:
    """One periodic update of probe 303 whose read a worker of the real pool takes to ``session``."""
    handler = _make_handler(hass, "F455")
    handler.is_connected = True
    pool = CommandWorkerPool(handler)
    pool._event_session_ready.set()
    futures: list[asyncio.Future] = []

    async def _send(message: Any) -> asyncio.Future:
        futures.append(await pool.send_status_request(message))
        return futures[-1]

    gateway.send_status_request = AsyncMock(side_effect=_send)
    probe = _sensor(hass, gateway)
    with (
        patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session),
        patch("custom_components.myhome.gateway.STATUS_TIMEOUT_BACKOFF", 0.02),
    ):
        await probe.async_update()
        await pool.send_buffer.put(None)
        await pool.sending_loop(0)
    pool.send_buffer.clear_status_pause()
    await _settle()
    assert _sent(gateway) == [READ]
    return probe, futures[0]


def _marks(written: asyncio.Future) -> set[str]:
    return {mark for mark in ("_gateway_nack", "_command_timeout", "_gateway_desync") if getattr(written, mark, False)}


def _answers(session: MagicMock, collected: Any, error: OWNError | None = None) -> None:
    async def _send(message: Any, is_status_request: bool = False) -> Any:
        session.last_error = error
        return collected

    session.send = AsyncMock(side_effect=_send)


async def test_the_sender_marks_a_nacked_read_and_the_probe_is_not_asked_again(hass, gateway) -> None:
    session = _command_session()
    _answers(session, None, OWNNackError(f"Gateway rejected message `{READ}` with NACK", frame=READ))
    probe, written = await _read_through_the_sender(hass, gateway, session)
    assert written.cancelled() and _marks(written) == {"_gateway_nack"}
    assert probe._fallback_read_refused is True


async def test_the_sender_marks_a_read_that_timed_out_and_the_probe_is_not_asked_again(hass, gateway) -> None:
    session = _command_session()
    _answers(session, None, OWNTimeoutError("Timed out awaiting the complete response", reason="command_timeout"))
    probe, written = await _read_through_the_sender(hass, gateway, session)
    assert written.cancelled() and _marks(written) == {"_command_timeout"}
    assert probe._fallback_read_refused is True


@pytest.mark.parametrize(
    "error",
    [
        OWNConnectionError("Command session unavailable; message not sent", reason="connection_unavailable"),
        OWNConnectionError("Connection lost before acknowledgement", reason="connection_lost"),
        OWNConnectionError("Command session crashed", reason="session_crash"),
        OWNProtocolError("Unexpected response", reason="unexpected_response"),
        # What OWNd's send() leaves when it had to reopen the session and the gateway NACKed the negotiation.
        OWNNackError("Gateway refused session negotiation with NACK", reason="negotiation_refused"),
        None,
    ],
    ids=lambda error: getattr(error, "reason", "no error"),
)
async def test_a_read_that_failed_on_the_command_session_keeps_the_fallback(hass, gateway, error) -> None:
    """OWNd's send() returned None for a reason that is not an answer of the gateway to the read."""
    session = _command_session()
    _answers(session, None, error)
    probe, written = await _read_through_the_sender(hass, gateway, session)
    assert written.cancelled() and _marks(written) == set()
    assert probe._fallback_read_refused is False


@pytest.mark.parametrize(
    "reopen",
    [{"Success": False, "Message": "connection_error"}, {"Success": False, "Message": "negotiation_refused"}],
    ids=["session unavailable", "connect refused"],
)
async def test_a_read_whose_command_session_could_not_be_reopened_keeps_the_fallback(hass, gateway, reopen) -> None:
    """The idle command socket was closed and reopening it failed: nothing was written."""
    session = _command_session(connect=[{"Success": True}, reopen], is_open=False)
    probe, written = await _read_through_the_sender(hass, gateway, session)
    session.send.assert_not_awaited()
    assert written.cancelled() and _marks(written) == set()
    assert probe._fallback_read_refused is False


async def test_a_probe_read_that_collected_another_zones_frames_is_dropped_by_the_desync_guard(
    hass, gateway, caplog
) -> None:
    """#886: the guard covers a probe address, so such a read is neither answered nor refused."""
    session = _command_session()
    _answers(session, [OWNMessage.parse("*#4*4*0*0256##"), OWNMessage.parse("*#4*4*12*0225*3##")])
    probe, written = await _read_through_the_sender(hass, gateway, session)
    assert f"Gateway FIFO desync on `{READ}`" in caplog.text
    assert written.cancelled() and _marks(written) == {"_gateway_desync"}
    assert probe._fallback_read_refused is False


async def test_a_probe_read_answered_with_the_probes_own_frame_is_delivered(hass, gateway, caplog) -> None:
    session = _command_session()
    _answers(session, [OWNMessage.parse("*#4*303*0*0232##")])
    probe, written = await _read_through_the_sender(hass, gateway, session)
    assert "Gateway FIFO desync" not in caplog.text
    assert written.done() and not written.cancelled() and _marks(written) == set()
    assert probe._fallback_read_refused is False


def test_the_desync_guard_reads_a_probe_address() -> None:
    # A probe as in validate.py: an external probe N00 (100..900) or a probe PZZ (101..899).
    assert [_who4_zone_token(where) for where in ("100", "303", "0105", "899", "900", "303#4#01")] == [
        "100",
        "303",
        "105",
        "899",
        "900",
        "303",
    ]
    # Not a probe: a zone, an actuator of a zone, the central unit, and a number above 900.
    assert [_who4_zone_token(where) for where in ("99", "12#1", "#0", "0", "901", "4001")] == [
        "99",
        "12",
        None,
        None,
        None,
        None,
    ]
    # The frames of probes in the captures carry the probe's WHERE (issue_932, issue_466, #264).
    for own in ("*#4*303*0*0232##", "*#4*303*15*1*0232*0001##"):
        assert _mismatched_who4_reply_zones(READ, [own]) is None
    assert _mismatched_who4_reply_zones("*#4*105*15##", ["*#4*105*15*0*0000*0000##"]) is None
    assert _mismatched_who4_reply_zones("*#4*100*15##", ["*#4*100*15*1*0200*0001##"]) is None
    # Another zone's or another probe's frames alone are foreign; with one frame of its own they are not.
    assert _mismatched_who4_reply_zones(READ, ["*#4*4*0*0256##", "*4*0*4##"]) == {"4"}
    assert _mismatched_who4_reply_zones(READ, ["*#4*103*0*0229##"]) == {"103"}
    assert _mismatched_who4_reply_zones(READ, ["*#4*4*0*0256##", "*#4*303*0*0232##"]) is None
    # And the other way: the read of a zone that collected only a probe's frame.
    assert _mismatched_who4_reply_zones("*#4*4*0##", ["*#4*303*0*0232##"]) == {"303"}
    assert _mismatched_who4_reply_zones("*#4*4*0##", ["*#4*303*0*0232##", "*#4*4*0*0256##"]) is None


async def test_a_nacked_command_carries_the_mark_too() -> None:
    """Review of #951, R1: a setpoint write is a command, and its refusal has to be told from a lost frame."""
    harness = MockGatewayHarness()
    harness.set_nack_commands(True)
    handler = _handler_on_port(await harness.start())
    handler.is_connected = True
    handler._event_session_ready.set()
    worker = asyncio.create_task(handler.sending_loop(0))
    try:
        written = await handler.send(
            OWNHeatingCommand.set_temperature(where="2", temperature=21.0, mode="auto", standalone=True)
        )
        await asyncio.wait([written], timeout=10)
    finally:
        await handler.send_buffer.put(None)
        await asyncio.wait_for(worker, timeout=10)
        await harness.stop()
    assert written.cancelled()
    assert getattr(written, "_gateway_nack", False) is True


async def test_a_nack_through_ownds_own_command_session_carries_the_mark() -> None:
    """Worker -> the real ``OWNCommandSession`` -> TCP -> a gateway double that NACKs every frame."""
    harness = MockGatewayHarness()
    harness.set_nack_commands(True)
    handler = _handler_on_port(await harness.start())
    handler.is_connected = True
    handler._event_session_ready.set()
    worker = asyncio.create_task(handler.sending_loop(0))
    try:
        written = await handler.send_status_request(OWNHeatingCommand.get_temperature("303"))
        await asyncio.wait([written], timeout=5)
    finally:
        await handler.send_buffer.put(None)
        await asyncio.wait_for(worker, timeout=5)
        await harness.stop()
    assert READ in harness.received_messages
    assert written.cancelled() and _marks(written) == {"_gateway_nack"}
