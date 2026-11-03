"""WHO 4 dimension 22 guard: refuse the crash forms, forward the published split form.

Every dimension 22 spelling whose WHERE carries no actuator token (a zone, the
general ``0``, the central unit ``#0``) kills the thermostat daemon of the Linux
IP gateways (own-firmware-oracle ``thermo-dim22``; a live F461 2.0.11 rebooted,
OWNd#136). The published split forms ``*#4*3#Z#N*22##`` and
``*#4*3#Z#N*#22*MOD*SP*VEL*SWING##`` crashed none of the twelve emulated
gateways and are what OWNd >= 2.0.0b12 forwards (OWNd#151,
``is_who4_dimension_22_crash_form``). MyHOME follows that detector when the
installed OWNd has it and refuses every dimension 22 frame otherwise.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from custom_components.myhome import services
from custom_components.myhome.websocket import ws_bus_monitor_send
from custom_components.myhome.where_grammar import validate_thermo_trigger_command


@pytest.fixture
def mock_ws_connection():
    """A mock active WebSocket connection (as in test_websocket.py)."""
    conn = MagicMock(spec=websocket_api.ActiveConnection)
    conn.send_result = MagicMock()
    conn.send_error = MagicMock()
    conn.send_message = MagicMock()
    conn.subscriptions = {}
    return conn

CRASH_FORMS = [
    "*#4*1*22##",  # zone
    "*#4*0*22##",  # general
    "*#4*#0*22##",  # central unit
    "*#4*#1*22##",  # zone through the central unit
    "*#4*2#1*22##",  # prefix-less actuator form (nack/silent on eleven gateways)
    "*#4*2*#22*1*0200*1*0##",  # zone write
    "*#4*#0*#22*1*0200*1*0##",  # central-unit write
]
SPLIT_FORMS = [
    "*#4*3#2#1*22##",
    "*#4*3#45#1*22##",
    "*#4*3#2#1*#22*1*0200*1*0##",
    "*#4*3#99#9*#22****##",  # every payload field empty, as the touchscreen sends
]
OTHER_FRAMES = ["*1*1*12##", "*#4*1*0##", "*#4*1*14##", "*#22*2#1*#11*1*10150*3##"]

needs_detector = pytest.mark.skipif(
    services._is_dimension_22_crash_form is None,
    reason="installed OWNd has no is_who4_dimension_22_crash_form (< 2.0.0b12)",
)


@needs_detector
@pytest.mark.parametrize("frame", CRASH_FORMS)
def test_crash_forms_are_refused_with_the_detector(frame):
    assert services.is_refused_dimension_22(frame) is True
    assert services.is_refused_dimension_22(f"  {frame}\n") is True


@needs_detector
@pytest.mark.parametrize("frame", SPLIT_FORMS)
def test_published_split_forms_pass_with_the_detector(frame):
    assert services.is_refused_dimension_22(frame) is False


@pytest.mark.parametrize("frame", OTHER_FRAMES)
def test_other_frames_are_never_refused(frame, monkeypatch):
    assert services.is_refused_dimension_22(frame) is False
    monkeypatch.setattr(services, "_is_dimension_22_crash_form", None)
    assert services.is_refused_dimension_22(frame) is False


@pytest.mark.parametrize("frame", CRASH_FORMS + SPLIT_FORMS)
def test_without_the_detector_every_dimension_22_frame_is_refused(frame, monkeypatch):
    """OWNd < 2.0.0b12 knows no safe form: the blanket regex stays."""
    monkeypatch.setattr(services, "_is_dimension_22_crash_form", None)
    assert services.is_refused_dimension_22(frame) is True


@needs_detector
async def test_websocket_forwards_the_split_form_and_blocks_the_zone_form(
    hass: HomeAssistant, mock_ws_connection, attach_gateway
):
    gateway = MagicMock()
    gateway.send = AsyncMock()
    attach_gateway("00:03:50:00:12:34", gateway)

    ws_bus_monitor_send(
        hass,
        mock_ws_connection,
        {"id": 1, "type": "myhome/bus_monitor/send", "frame": "*#4*3#2#1*22##"},
    )
    await hass.async_block_till_done()
    mock_ws_connection.send_result.assert_called_once_with(
        1, {"success": True, "frame": "*#4*3#2#1*22##"}
    )
    gateway.send.assert_awaited_once()

    gateway.send.reset_mock()
    ws_bus_monitor_send(
        hass,
        mock_ws_connection,
        {"id": 2, "type": "myhome/bus_monitor/send", "frame": "*#4*2*22##"},
    )
    await hass.async_block_till_done()
    mock_ws_connection.send_error.assert_called_once_with(
        2,
        websocket_api.ERR_INVALID_FORMAT,
        "WHO 4 dimension 22 in this form crashes the gateway's thermostat daemon "
        "and is blocked for safety",
    )
    gateway.send.assert_not_awaited()


async def test_websocket_blocks_the_split_form_without_the_detector(
    hass: HomeAssistant, mock_ws_connection, attach_gateway, monkeypatch
):
    monkeypatch.setattr(services, "_is_dimension_22_crash_form", None)
    gateway = MagicMock()
    gateway.send = AsyncMock()
    attach_gateway("00:03:50:00:12:34", gateway)

    ws_bus_monitor_send(
        hass,
        mock_ws_connection,
        {"id": 3, "type": "myhome/bus_monitor/send", "frame": "*#4*3#2#1*22##"},
    )
    await hass.async_block_till_done()
    assert mock_ws_connection.send_error.call_count == 1
    gateway.send.assert_not_awaited()


@needs_detector
def test_thermo_trigger_accepts_the_split_write_and_refuses_the_zone_write():
    assert (
        validate_thermo_trigger_command("*#4*3#2#1*#22*1*0200*1*0##")
        == "*#4*3#2#1*#22*1*0200*1*0##"
    )
    with pytest.raises(ValueError, match="crashes the gateway's thermostat daemon"):
        validate_thermo_trigger_command("*#4*2*#22*1*0200*1*0##")
    with pytest.raises(ValueError, match="crashes the gateway's thermostat daemon"):
        validate_thermo_trigger_command("*#4*#0*#22*1*0200*1*0##")


def test_thermo_trigger_refuses_every_dimension_22_write_without_the_detector(monkeypatch):
    monkeypatch.setattr(services, "_is_dimension_22_crash_form", None)
    with pytest.raises(ValueError, match="crashes the gateway's thermostat daemon"):
        validate_thermo_trigger_command("*#4*3#2#1*#22*1*0200*1*0##")
