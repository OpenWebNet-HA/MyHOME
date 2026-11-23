"""A WHO 4 dimension 14 frame on the event session: a write is re-read, a request is not.

``GatewayEventDispatcher`` follows a setpoint written on the bus with a zone status read
(``*#4*Z##``) on gateways that answer it (``supports_zone_status``, #674). OWNd gives the
write (``*#4*2*#14*0200*2##``) and the request (``*#4*2*14##``) the same class,
``OWNHeatingCommand``, and the same dimension; only ``is_dimension_writing`` tells them apart.

Frames, verbatim:
- writes: ``tests/fixtures/traces/central_restore_3100_live_2026-11-12/`` (F461 2.0.11 and
  F455 1.0.76 event sessions) and ``tests/fixtures/plants/issue_333_myhomeserver1/``;
- requests: ``*#4*4*14##`` sent to the F455 in
  ``tests/fixtures/traces/issue_466/F455_direct_requests_2026-11-06.jsonl`` (answered with
  ``*#4*4*14*0215*3##``), ``*#4*2*14##`` in the same file, and ``*#4*#2*14##``, the form the
  MH201 answered in #932.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from OWNd.message import OWNHeatingCommand, OWNHeatingEvent, OWNMessage

from custom_components.myhome.gateway_events import GatewayEventDispatcher

TRACES = Path(__file__).parent / "fixtures" / "traces"
RESTORE = TRACES / "central_restore_3100_live_2026-11-12"
F455_DIRECT = TRACES / "issue_466" / "F455_direct_requests_2026-11-06.jsonl"
MHS1_PLANT = Path(__file__).parent / "fixtures" / "plants" / "issue_333_myhomeserver1" / "diagnostic_summary.json"


def _dispatcher(hass: HomeAssistant, *, supports_zone_status: bool = True) -> tuple[GatewayEventDispatcher, list[str]]:
    sent: list[str] = []

    async def _send_status_request(message: Any) -> None:
        sent.append(str(message))

    handler = MagicMock()
    handler.hass = hass
    handler.mac = "00:03:50:00:04:14"
    handler.log_id = "[dim14]"
    handler.generate_events = False
    handler.supports_zone_status = supports_zone_status
    handler.interface_zone_request_refused = True
    handler.send_status_request = AsyncMock(side_effect=_send_status_request)
    dispatcher = GatewayEventDispatcher(handler)
    dispatcher._is_active_for_who = lambda who: True  # type: ignore[method-assign]
    return dispatcher, sent


def _event_session_frames(path: Path) -> list[str]:
    return [
        frame["raw"]
        for frame in json.loads(path.read_text(encoding="utf-8"))["frames"]
        if frame["direction"] == "rx" and frame.get("session") == "evt"
    ]


@pytest.mark.parametrize("frame", ["*#4*2*14##", "*#4*4*14##", "*#4*#2*14##"])
async def test_dimension_14_request_starts_no_zone_status_read(hass: HomeAssistant, frame: str) -> None:
    """The read of the setpoint is not a change of it: nothing is asked in return."""
    if not frame.startswith("*#4*#"):
        assert f'"frame": "{frame}"' in F455_DIRECT.read_text(encoding="utf-8")  # verbatim, not built
    message = OWNMessage.parse(frame)
    assert isinstance(message, OWNHeatingCommand)
    assert message.dimension == 14
    assert message.is_dimension_request and not message.is_dimension_writing

    dispatcher, sent = _dispatcher(hass)
    await dispatcher.process_message(message)
    assert sent == []


@pytest.mark.parametrize(
    ("frame", "read"),
    [
        ("*#4*2*#14*0200*2##", "*#4*2##"),  # F461 / F455 event session, central_restore_3100
        ("*#4*4*#14*0250*2##", "*#4*4##"),
        ("*#4*#2*#14*0280*1##", "*#4*2##"),  # MyHomeServer1, issue_333
        ("*#4*2*#14*0280*1##", "*#4*2##"),
    ],
)
async def test_dimension_14_write_is_followed_by_a_zone_status_read(hass: HomeAssistant, frame: str, read: str) -> None:
    """The behaviour the branch exists for is kept, frame for frame."""
    sources = "".join(p.read_text(encoding="utf-8") for p in (*sorted(RESTORE.glob("*.json")), MHS1_PLANT))
    assert f'"{frame}"' in sources  # verbatim, not built
    message = OWNMessage.parse(frame)
    assert isinstance(message, OWNHeatingCommand)
    assert message.is_dimension_writing

    dispatcher, sent = _dispatcher(hass)
    await dispatcher.process_message(message)
    assert sent == [read]

    # Where the gateway does not answer *#4*Z## (F455, #674) nothing is asked, as before.
    dispatcher, sent = _dispatcher(hass, supports_zone_status=False)
    await dispatcher.process_message(message)
    assert sent == []


@pytest.mark.parametrize("gateway", ["F461", "F455"])
async def test_central_restore_replay_reads_once_per_setpoint_write(hass: HomeAssistant, gateway: str) -> None:
    """Replay of a real event session: one read per setpoint write where the gateway answers the zone
    status request, none on an F455 (its profile has no zone status request); none for any other frame."""
    frames = _event_session_frames(RESTORE / f"myhome_trace_{gateway}_central_restore_2026-11-12.json")
    dispatcher, sent = _dispatcher(hass, supports_zone_status=gateway != "F455")
    writes: list[str] = []
    for raw in frames:
        message = OWNMessage.parse(raw)
        if message is None:
            continue
        if isinstance(message, OWNHeatingCommand) and message.dimension == 14:
            assert message.is_dimension_writing, raw  # this capture holds no dimension 14 request
            writes.append(raw)
        await dispatcher.process_message(message)

    assert writes, "the capture holds setpoint writes"
    expected = [f"*#4*{raw.split('*')[2].lstrip('#')}##" for raw in writes] if gateway != "F455" else []
    assert sent == expected
    # The setpoint the zone then reports is an event, never a command: it asks nothing.
    report = OWNMessage.parse("*#4*4*14*0215*3##")
    assert isinstance(report, OWNHeatingEvent)
