"""Tests for #541: classic CEN (WHO 15) short press replayed from authentic traces.

Traces contributed by @wave68runner on a shared MH200N + MyHomeServer1 bus.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from OWNd.message import OWNCENEvent, OWNMessage

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_SHORT_PRESS,
    CONF_SHORT_RELEASE,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_UDN,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_541"
TRACE_FILES = sorted(TRACES_DIR.glob("myhome_trace_*.json"))


def _cen_frames(trace_file: Path) -> list[str]:
    data = json.loads(trace_file.read_text(encoding="utf-8"))
    return [f["raw"] for f in data["frames"] if f["raw"].startswith("*15*")]


@pytest.fixture
def gateway_handler() -> MyHOMEGatewayHandler:
    entry = MagicMock()
    entry.entry_id = "entry_541"
    entry.data = {
        CONF_HOST: "192.168.1.5",
        CONF_PORT: 20000,
        CONF_PASSWORD: "open",
        CONF_SSDP_LOCATION: "",
        CONF_SSDP_ST: "",
        CONF_DEVICE_TYPE: "Gateway",
        CONF_FRIENDLY_NAME: "GW",
        CONF_MANUFACTURER: "Bticino",
        CONF_MANUFACTURER_URL: "",
        CONF_NAME: "MYHOME",
        CONF_FIRMWARE: "1.0",
        CONF_MAC: "00:11:22:33:44:55",
        CONF_UDN: "1234",
    }
    hass = MagicMock()
    hass.data = {}
    handler = MyHOMEGatewayHandler(hass, entry)
    handler.device_registry_id = "gateway_device"
    return handler


def test_trace_files_present() -> None:
    assert len(TRACE_FILES) == 2, TRACE_FILES


@pytest.mark.parametrize("trace_file", TRACE_FILES, ids=lambda p: p.name)
def test_cen_frames_parse_as_short_press_then_release(trace_file: Path) -> None:
    frames = _cen_frames(trace_file)
    assert frames == ["*15*01*73##", "*15*01#1*73##"]
    pressed, released = (OWNMessage.parse(f) for f in frames)
    assert isinstance(pressed, OWNCENEvent) and isinstance(released, OWNCENEvent)
    assert (pressed.object, pressed.push_button) == ("73", 1)
    assert pressed.is_pressed and not pressed.is_released_after_short_press
    assert released.is_released_after_short_press and not released.is_pressed


@pytest.mark.asyncio
@pytest.mark.parametrize("trace_file", TRACE_FILES, ids=lambda p: p.name)
async def test_cen_trace_fires_myhome_cen_event(gateway_handler: MyHOMEGatewayHandler, trace_file: Path) -> None:
    messages = [OWNMessage.parse(f) for f in _cen_frames(trace_file)]

    with (
        patch("custom_components.myhome.gateway.OWNEventSession") as session_class,
        patch("homeassistant.helpers.device_registry.async_get", return_value=MagicMock()),
    ):
        session = MagicMock()
        session.connect = AsyncMock(return_value={"Success": True})
        session.get_next = AsyncMock(side_effect=[*messages, asyncio.CancelledError()])
        session_class.return_value = session
        gateway_handler.send_status_request = AsyncMock()
        with patch.object(gateway_handler.hass.bus, "async_fire") as fire:
            try:
                await gateway_handler.listening_loop()
            except asyncio.CancelledError:
                pass

    expected = {"object": 73, "pushbutton": 1, "where": "73", "gateway_mac": gateway_handler.mac, "entry_id": "entry_541"}
    fire.assert_any_call("myhome_cen_event", {**expected, "event": CONF_SHORT_PRESS})
    fire.assert_any_call("myhome_cen_event", {**expected, "event": CONF_SHORT_RELEASE})
