"""Regressions from F454 bus traces and complete command transactions."""

import asyncio
import datetime
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.connection import (
    OWNCommandSession,
    OWNEventSession,
    OWNGateway,
)
from OWNd.message import OWNMessage


@pytest.mark.parametrize("dimension", ["0", "#0"])
@pytest.mark.parametrize(
    ("suffix", "expected"),
    [("", "15:00:01"), ("*", "15:00:01"), ("*002", "15:00:01+02:00"),
     ("*105", "15:00:01-05:00")],
)
def test_gateway_time_optional_timezone(dimension, suffix, expected):
    """Both broadcasts and replies accept omitted, empty and explicit offsets."""
    message = OWNMessage.parse(f"*#13**{dimension}*15*00*01{suffix}##")
    assert message._time == datetime.time.fromisoformat(expected)


@pytest.mark.parametrize("dimension", ["0", "1", "22"])
def test_gateway_clock_requests_have_no_values(dimension):
    message = OWNMessage.parse(f"*#13**{dimension}##")
    assert message.is_request
    assert message._time is None
    assert message._date is None
    assert message._datetime is None


@pytest.mark.parametrize(
    "frame",
    ["*#13**#0*15*00##", "*#13**0*25*00*00##",
     "*#13**0*15*00*00*200##", "*#13**0*15*00*00*099##",
     "*#13**#1*03*09*09##", "*#13**22*15*00*01*002*03*09*09##",
     "*#13**#22*15*00*01*002*03*31*02*2026##"],
)
def test_malformed_gateway_clock_fields_are_rejected(frame):
    with pytest.raises(ValueError):
        OWNMessage.parse(frame)


def session_with_frames(session_type, frames):
    """Use the real asyncio frame buffer without opening a network connection."""
    session = session_type(
        gateway=OWNGateway({"address": "127.0.0.1", "port": 20000}),
        logger=logging.getLogger("test.f454"),
    )
    reader = asyncio.StreamReader()
    for frame in frames:
        reader.feed_data(frame)
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    session._stream_reader = reader
    session._stream_writer = writer
    return session, reader, writer


async def test_bad_event_does_not_drop_the_next_frame(caplog):
    bad = b"*#13**#0*15*00##"
    session, _, _ = session_with_frames(OWNEventSession, [bad, b"*1*1*11##"])
    assert await session.get_next() == bad.decode()
    assert str(await session.get_next()) == "*1*1*11##"
    assert bad.decode() in caplog.text
    assert "Event session crashed" not in caplog.text


@pytest.mark.parametrize("count", [19, 20, 30, 100])
async def test_discovery_drains_every_reply_before_next_command(count):
    frames = [f"*1*0*{i + 11}##".encode() for i in range(count)]
    session, reader, writer = session_with_frames(OWNCommandSession, [*frames, b"*#*1##"])
    responses = await session.send("*#1*0##", is_status_request=True)
    assert [str(message) for message in responses] == [frame.decode() for frame in frames]

    reader.feed_data(b"*2*0*71##*#*1##")
    responses = await session.send("*#2*0##", is_status_request=True)
    assert [str(message) for message in responses] == ["*2*0*71##"]
    assert writer.write.call_count == 2


async def test_incomplete_response_times_out_and_next_command_reconnects():
    session, _, writer = session_with_frames(OWNCommandSession, [b"*1*0*11##"])
    session.RESPONSE_TIMEOUT = 0.01
    assert await session.send("*#1*0##", is_status_request=True) is None
    writer.close.assert_called_once()
    assert session._stream_reader is None
    assert session._stream_writer is None

    async def reconnect():
        _, session._stream_reader, session._stream_writer = session_with_frames(
            OWNCommandSession, [b"*2*0*71##", b"*#*1##"]
        )
        return {"Success": True}

    with patch.object(session, "connect", side_effect=reconnect) as connect:
        responses = await session.send("*#2*0##", is_status_request=True)
    assert [str(message) for message in responses] == ["*2*0*71##"]
    connect.assert_called_once()


async def test_disconnected_query_retries_only_once():
    session, reader, writer = session_with_frames(OWNCommandSession, [])
    reader.feed_eof()

    async def reconnect():
        _, session._stream_reader, session._stream_writer = session_with_frames(OWNCommandSession, [])
        session._stream_reader.feed_eof()
        return {"Success": True}

    with patch.object(session, "connect", side_effect=reconnect) as connect:
        assert await session.send("*#1*0##", is_status_request=True) is None
    connect.assert_called_once()
    writer.close.assert_called_once()
    assert session._stream_reader is None


async def test_sent_command_is_not_replayed_after_lost_ack():
    session, reader, writer = session_with_frames(OWNCommandSession, [])
    reader.feed_eof()
    with patch.object(session, "connect", new_callable=AsyncMock) as connect:
        assert await session.send("*1*1*11##") is None
    writer.write.assert_called_once_with(b"*1*1*11##")
    connect.assert_not_called()


async def test_cancelled_command_closes_its_stream():
    session, _, writer = session_with_frames(OWNCommandSession, [])
    pending = asyncio.create_task(session.send("*#1*0##", is_status_request=True))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    writer.close.assert_called_once()
    assert session._stream_reader is None


@pytest.mark.parametrize("result", [None, {"Success": False, "Message": "password_error"}])
async def test_failed_reconnect_does_not_send_a_command(result):
    session, _, writer = session_with_frames(OWNCommandSession, [])
    await session.close()
    with patch.object(session, "connect", return_value=result):
        assert await session.send("*#1*0##", is_status_request=True) is None
    writer.write.assert_not_called()


async def test_bad_command_frame_is_consumed_through_ack():
    bad = b"*#13**0*15*00##"
    session, _, _ = session_with_frames(
        OWNCommandSession, [bad, b"*#1234##", b"*1*0*11##", b"*#*1##"]
    )
    replies = await session.send("*#1*0##", is_status_request=True)
    assert [str(reply) for reply in replies] == [bad.decode(), "*1*0*11##"]


async def test_partial_response_followed_by_nack_is_not_retried():
    session, _, writer = session_with_frames(OWNCommandSession, [b"*1*0*11##", b"*#*0##"])
    assert await session.send("*#1*0##", is_status_request=True) is None
    writer.write.assert_called_once()
