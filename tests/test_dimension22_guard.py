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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components import websocket_api
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome import services
from custom_components.myhome.climate_discovery import _calling_zones, _zone_route_keys
from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)
from custom_components.myhome.websocket import ws_bus_monitor_send
from custom_components.myhome.where_grammar import (
    is_split_unit,
    thermo_event_zone,
    validate_thermo_trigger_command,
)


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


# ── a split unit's WHERE is ``3#Z#N``: no zone's frame, least of all zone 3's ──

# Built from the published forms (no capture exists): request ``*#4*3#Z#N*22##``, write
# ``*#4*3#Z#N*#22*MOD*SP*VEL*SWING##`` and status ``*#4*3#Z#N*22*MOD*SP*VEL*SWING##``
# (Encyclopedia functional/who-4-temperature-control/dimensions.md, DIMENSION 22), with the
# values of the oracle suite's write ``*#4*3#2#1*#22*1*0215*2*0##`` (findings/thermo-dim22.md).
SPLIT_FRAMES = [
    "*#4*3#2#1*22*1*0215*2*0##",  # status of split unit 1 of zone 2
    "*#4*3#4#1*22*1*0215*2*0##",  # ... of zone 4: not "zone 3 behind interface 1"
    "*#4*3#12#2*22*1*0215*2*0##",
    "*#4*3#2#1#4#01*22*1*0215*2*0##",  # behind an F422
    "*#4*3#2#1*#22*1*0215*2*0##",  # another client's write
    "*#4*3#2#1*22##",
]


@pytest.mark.parametrize("frame", SPLIT_FRAMES)
def test_a_split_unit_frame_names_no_zone(frame):
    message = OWNMessage.parse(frame)
    assert is_split_unit(message) is True
    assert thermo_event_zone(message) is None
    assert _calling_zones(message)[0] == []
    assert _zone_route_keys(message, None) == []


@pytest.mark.parametrize(
    "frame",
    [
        "*#4*3*0*0244##",  # zone 3 temperature (F454, tests/fixtures/traces/issue_429/xtimmy86x_capture.24092026.00-13.txt)
        "*#4*3#1*20*0##",  # actuator 1 of zone 3 (F454 issue_866, MyHomeServer1 issue_429)
        # zone 3 behind interface 01, the two frames above in the routed spelling (no capture)
        "*#4*3#4#01*20*0##",
        "*#4*3#4#01*0*0244##",
    ],
)
def test_zone_3_frames_are_still_zone_3(frame):
    message = OWNMessage.parse(frame)
    assert is_split_unit(message) is False
    assert thermo_event_zone(message) == "3"
    assert "3" in _calling_zones(message)[0]


async def test_a_split_unit_report_does_not_create_zone_3(hass: HomeAssistant):
    """``*#4*3#2#1*22*...##`` created ``climate.climate_zone_3`` on a plant without a zone 3."""
    mac = "00:03:50:00:11:2e"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.1", CONF_PORT: 20000, CONF_PASSWORD: "pass", CONF_MAC: mac, CONF_NAME: "F454",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:webserver:1", CONF_FRIENDLY_NAME: "F454",
            CONF_MANUFACTURER: "BTicino S.p.A.", CONF_FIRMWARE: "1.0",
        },
        unique_id=mac,
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.gateway._on_event_connection_state_change(True)

    for frame in SPLIT_FRAMES:
        async_dispatcher_send(hass, f"myhome_message_{mac}", OWNMessage.parse(frame))
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert [e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id) if e.domain == "climate"] == []

    # zone 3 itself is still discovered by its own frames: an unconfigured zone is created on
    # its second independent frame (#966)
    for frame in ("*#4*3*0*0244##", "*#4*3*0*0251##"):
        async_dispatcher_send(hass, f"myhome_message_{mac}", OWNMessage.parse(frame))
    await hass.async_block_till_done()
    assert [e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id) if e.domain == "climate"] == [f"{mac}-4-3"]
    await hass.config_entries.async_unload(entry.entry_id)
