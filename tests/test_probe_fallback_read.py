"""The fallback read of a temperature probe (``WHERE >= 100``): a wider window and a latch.

A probe sends its reading by itself (#308); the periodic update asks ``*#4*ZPP*15##`` only when
no reading arrived for a while. On a physical plant (F455 1.0.76 and an F461 2.0.11 on one bus,
2026-11-17) probe 303 reported every 10 minutes (``*#4*303*0*0229##`` 08:31:22Z,
``*#4*303*0*0230##`` 08:41:23Z). With the poll interval (300 s) as the window every second update
sent ``*#4*303*15##``, and the F455 NACKed each one only after 9.3 to 9.4 s, holding a command
session that long; one such read was ACKed after 1.6 s because the probe's own report arrived
while it was out.

- A reading of a probe keeps the read back for ``PROBE_PUSH_FRESH`` (900 s), three poll intervals.
- A read the gateway refused is not sent again. Not a refusal: a read dropped by the #886 guard,
  a read cancelled with the connection, and a read during which the probe reported.
- A zone sensor (``WHERE < 100``) keeps its window and is asked as before.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from OWNd.message import OWNHeatingEvent

from custom_components.myhome.sensor import PROBE_PUSH_FRESH, SCAN_INTERVAL, MyHOMETemperatureSensor

READ = "*#4*303*15##"
REPORT = "*#4*303*0*0229##"


@pytest.fixture
def gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:11:06"
    gw.send_status_request = AsyncMock()
    gw.log_id = "[F455 gateway]"
    gw.device_registry_id = None
    gw.is_connected = True
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


def _refused(hass) -> asyncio.Future:
    """What the sender leaves of a read the gateway NACKed: a cancelled future."""
    written = hass.loop.create_future()
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


async def test_a_refusal_that_takes_nine_seconds_does_not_hold_the_update(hass, gateway) -> None:
    probe = _sensor(hass, gateway)
    pending = hass.loop.create_future()
    gateway.send_status_request.return_value = pending
    await probe.async_update()  # returns with the read still out
    assert not pending.done() and probe._fallback_read_refused is False
    pending.cancel()
    await _settle()
    assert probe._fallback_read_refused is True


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
    pending.cancel()
    await _settle()
    assert probe._fallback_read_refused is False


async def test_a_report_that_arrived_during_the_read_is_not_a_refusal(hass, gateway) -> None:
    probe = _sensor(hass, gateway)
    pending = hass.loop.create_future()
    gateway.send_status_request.return_value = pending
    await probe.async_update()
    probe.handle_event(OWNHeatingEvent(REPORT))
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
    assert f"Message `{READ}` was successfully queued." in log[1]
    assert f"Gateway rejected status request {READ} (NACK, 0 response(s))" in log[3]
    assert 0 < (received - reports[0]).total_seconds() < 1  # the report Home Assistant had is the monitor's first
    between = (reports[1] - reports[0]).total_seconds()
    asked_after = (queued - received).total_seconds()
    assert (between, asked_after) == (601.305, 563.455)
    assert SCAN_INTERVAL.total_seconds() < asked_after < between < PROBE_PUSH_FRESH
    assert (refused - queued).total_seconds() == 9.324
