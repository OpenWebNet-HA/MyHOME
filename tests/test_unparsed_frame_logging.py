"""Frames OWNd cannot parse are logged once per shape at WARNING, then at DEBUG (#139)."""
import logging
from unittest.mock import MagicMock

import pytest

from custom_components.myhome.gateway import MyHOMEGatewayHandler


@pytest.fixture
def gateway_handler():
    entry = MagicMock()
    entry.entry_id = "entry_unparsed"
    entry.data = {
        "host": "192.0.2.5",
        "port": 20000,
        "password": "12345",
        "mac": "00:03:50:00:00:01",
        "name": "F461",
        "manufacturer": "BTicino",
        "firmware": "2.0.11",
    }
    entry.options = {}
    entry.title = "F461 Gateway"
    hass = MagicMock()
    hass.data = {}
    return MyHOMEGatewayHandler(hass, entry)


def _not_a_message(caplog, level):
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == level and "Data received is not a message" in r.getMessage()
    ]


async def test_unparsed_frame_warns_once_per_shape(gateway_handler, caplog):
    """A zone thermostat pushing `*#4*#1*14*#0230*1##` every few seconds yields one WARNING, not a flood."""
    with caplog.at_level(logging.DEBUG, logger="custom_components.myhome"):
        for frame in ("*#4*#1*14*#0230*1##", "*#4*#1*14*#0231*1##", "*#4*#2*14*#0205*1##"):
            await gateway_handler._process_message(frame)
        # The live F461 2.0.11 answer to `*#13**27##` (model name as ASCII codes), a str in OWNd 2.0.0b11
        await gateway_handler._process_message("*#13**27*70#52#54#49##")

    warnings = _not_a_message(caplog, logging.WARNING)
    assert len(warnings) == 2, warnings
    assert "*#4*#1*14*#0230*1##" in warnings[0]
    assert "*#13**27*70#52#54#49##" in warnings[1]
    # The repeats are still visible at DEBUG for troubleshooting
    assert len(_not_a_message(caplog, logging.DEBUG)) == 2


def test_frame_shape_keeps_the_whole_frame():
    """The dedup key is the full shape, not a prefix: two long frames differing past 120 characters stay apart."""
    from custom_components.myhome.gateway_events import frame_shape

    head = "*#13**27*" + "#".join(["70"] * 60)
    assert frame_shape(head + "*1##") != frame_shape(head + "*#1##")
    assert frame_shape("*#4*#1*14*#0230*1##") == "*#N*#N*N*#N*N##"


async def test_unparsed_shapes_are_capped(gateway_handler, caplog, monkeypatch):
    """Beyond UNPARSED_SHAPES_MAX distinct shapes everything is DEBUG: the set cannot grow without bound."""
    from custom_components.myhome import gateway_events

    monkeypatch.setattr(gateway_events, "UNPARSED_SHAPES_MAX", 2)
    with caplog.at_level(logging.DEBUG, logger="custom_components.myhome"):
        for frame in ("*#4*#1*14*#0230*1##", "*#13**27*70#52#54#49##", "*#4*#1*14*#0230*1*2##"):
            await gateway_handler._process_message(frame)

    warnings = _not_a_message(caplog, logging.WARNING)
    assert len(warnings) == 2, warnings
    assert "*#4*#1*14*#0230*1*2##" in _not_a_message(caplog, logging.DEBUG)[0]
    assert len(gateway_handler._event_dispatcher._unparsed_shapes) == 2
