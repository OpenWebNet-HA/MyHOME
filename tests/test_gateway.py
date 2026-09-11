import asyncio
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
from OWNd.message import (
    OWNAlarmEvent,
    OWNAutomationEvent,
    OWNCENEvent,
    OWNCENPlusEvent,
    OWNCommand,
    OWNEnergyEvent,
    OWNGatewayCommand,
    OWNGatewayEvent,
    OWNHeatingCommand,
    OWNLightingEvent,
    OWNMessage,
)
from OWNd.profiles import GatewayProfile

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_FIRMWARE,
    CONF_LONG_PRESS,
    CONF_LONG_RELEASE,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_ROTARY_CCW_FAST,
    CONF_ROTARY_CCW_SLOW,
    CONF_ROTARY_CW_FAST,
    CONF_ROTARY_CW_SLOW,
    CONF_SHORT_PRESS,
    CONF_SHORT_RELEASE,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_UDN,
    DOMAIN,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler


@pytest.fixture
def mock_config_entry():
    entry = MagicMock()
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
    return entry


@pytest.fixture
def gateway_handler(mock_config_entry):
    mock_hass = MagicMock()
    mock_hass.data = {}
    handler = MyHOMEGatewayHandler(mock_hass, mock_config_entry)
    return handler


def test_gateway_properties(gateway_handler, mock_config_entry):
    assert gateway_handler.mac == "00:11:22:33:44:55"
    assert gateway_handler.unique_id == "00:11:22:33:44:55"
    assert gateway_handler.log_id == "[MYHOME gateway - 192.168.1.5]"
    assert gateway_handler.manufacturer == "Bticino"
    assert gateway_handler.name == "MYHOME Gateway"
    assert gateway_handler.model == "MYHOME"
    assert gateway_handler.firmware == "1.0"
    assert gateway_handler.profile is not None

    # Test mac fallback when serial is empty
    mock_config_entry.data[CONF_MAC] = ""
    handler_no_mac = MyHOMEGatewayHandler(gateway_handler.hass, mock_config_entry)
    assert handler_no_mac.mac == ""

    # Test mac fallback when dr.format_mac returns None
    with patch("custom_components.myhome.gateway.dr.format_mac", return_value=None):
        assert gateway_handler.mac == "00:11:22:33:44:55"


def test_gateway_availability_requires_connection(gateway_handler):
    """Availability changes only after a real event-session transition."""
    assert gateway_handler.available is False

    with patch("custom_components.myhome.gateway.async_dispatcher_send") as send:
        gateway_handler._on_event_connection_state_change(True)

    assert gateway_handler.available is True
    assert gateway_handler.is_connected is True
    send.assert_called_once_with(
        gateway_handler.hass,
        gateway_handler.availability_signal,
    )


def test_gateway_transient_disconnect_stays_available(gateway_handler):
    """A recovery inside the grace period must not flap entity availability."""
    cancel_timer = MagicMock()
    gateway_handler._on_event_connection_state_change(True)

    with (
        patch(
            "custom_components.myhome.gateway.async_call_later",
            return_value=cancel_timer,
        ) as call_later,
        patch("custom_components.myhome.gateway.async_dispatcher_send") as send,
    ):
        gateway_handler._on_event_connection_state_change(False)
        assert gateway_handler.available is True
        call_later.assert_called_once()

        gateway_handler._on_event_connection_state_change(True)

    cancel_timer.assert_called_once()
    assert gateway_handler.available is True
    send.assert_not_called()


def test_gateway_sustained_disconnect_marks_unavailable(gateway_handler):
    """A disconnect lasting beyond the grace period marks entities unavailable."""
    gateway_handler._on_event_connection_state_change(True)

    with (
        patch("custom_components.myhome.gateway.async_call_later") as call_later,
        patch("custom_components.myhome.gateway.async_dispatcher_send") as send,
    ):
        gateway_handler._on_event_connection_state_change(False)
        mark_unavailable = call_later.call_args.args[2]
        mark_unavailable(None)

    assert gateway_handler.available is False
    assert gateway_handler.is_connected is False
    send.assert_called_once_with(
        gateway_handler.hass,
        gateway_handler.availability_signal,
    )


def test_gateway_stale_unavailable_callback_is_ignored(gateway_handler):
    """A stale grace-period callback must not undo a successful reconnect."""
    gateway_handler._on_event_connection_state_change(True)

    with patch("custom_components.myhome.gateway.async_dispatcher_send") as send:
        gateway_handler._mark_unavailable(None)

    assert gateway_handler.available is True
    assert gateway_handler.is_connected is True
    send.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_test_connection(gateway_handler):
    with patch("custom_components.myhome.gateway.OWNSession") as mock_session_cls:
        mock_session = MagicMock()
        mock_session.test_connection = AsyncMock(return_value={"Success": True})
        mock_session_cls.return_value = mock_session

        res = await gateway_handler.test()
        assert res == {"Success": True}
        mock_session.test_connection.assert_called_once()

@pytest.mark.asyncio
async def test_gateway_test_connection_alphanumeric_password(gateway_handler):
    """Verify Issue #260: F454 gateway with alphanumeric password passes test_connection."""
    gateway_handler.gateway.password = "F454_Alphanumeric_Password"
    nonce_a = "1234567890123456789012345678901234567890"

    mock_reader = AsyncMock()
    mock_writer = MagicMock()
    mock_writer.drain = AsyncMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()

    call_count = 0
    async def mock_readuntil(sep=b"##"):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return b"*#*1##"
        elif call_count == 2:
            return b"*98*1##"  # F454 SHA-1 challenge
        elif call_count == 3:
            return f"*#{nonce_a}##".encode()
        else:
            last_call = mock_writer.write.call_args[0][0].decode()
            parts = last_call.strip("*#").split("*")
            rb = parts[0]
            from OWNd.connection import OWNSession
            dummy = OWNSession(gateway=gateway_handler.gateway)
            server_hmac = dummy._decode_hmac_response("sha1", gateway_handler.gateway.password, nonce_a, rb)
            return f"*#{server_hmac}##".encode()

    mock_reader.readuntil = mock_readuntil

    with patch("asyncio.open_connection", new=AsyncMock(return_value=(mock_reader, mock_writer))):
        res = await gateway_handler.test()
        assert res == {"Success": True, "Message": None}


@pytest.mark.asyncio
async def test_gateway_send_and_send_status_request(gateway_handler):
    cmd = MagicMock(spec=OWNCommand)
    await gateway_handler.send(cmd)
    item = await gateway_handler.send_buffer.get()
    assert item["message"] == cmd
    assert item["is_status_request"] is False

    await gateway_handler.send_status_request(cmd)
    item_status = await gateway_handler.send_buffer.get()
    assert item_status["message"] == cmd
    assert item_status["is_status_request"] is True


@pytest.mark.asyncio
async def test_gateway_close_listener(gateway_handler):
    gateway_handler.sending_workers = [MagicMock(), MagicMock()]
    cancel_timer = MagicMock()
    gateway_handler._unavailable_timer = cancel_timer
    gateway_handler._available = True
    gateway_handler.is_connected = True
    res = await gateway_handler.close_listener()
    assert res is True
    assert gateway_handler._terminate_sender is True
    assert gateway_handler._terminate_listener is True
    assert gateway_handler.available is False
    assert gateway_handler.is_connected is False
    assert gateway_handler._unavailable_timer is None
    cancel_timer.assert_called_once()

    # Queue full handling
    with patch.object(gateway_handler.send_buffer, "put_nowait", side_effect=asyncio.QueueFull):
        res2 = await gateway_handler.close_listener()
        assert res2 is True


@pytest.mark.asyncio
async def test_listening_loop_lighting(gateway_handler):
    with patch("custom_components.myhome.gateway.OWNEventSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.connect = AsyncMock(return_value={"Success": True})
        mock_session.get_next = AsyncMock()

        msg_gen = MagicMock(spec=OWNLightingEvent)
        msg_gen.is_translation = False
        msg_gen.is_general = True
        msg_gen.is_on = True
        msg_gen.human_readable_log = "L Gen"

        msg_area = MagicMock(spec=OWNLightingEvent)
        msg_area.is_translation = False
        msg_area.is_general = False
        msg_area.is_area = True
        msg_area.is_on = False
        msg_area.area = "1"
        msg_area.human_readable_log = "L Area"

        msg_group = MagicMock(spec=OWNLightingEvent)
        msg_group.is_translation = False
        msg_group.is_general = False
        msg_group.is_area = False
        msg_group.is_group = True
        msg_group.is_on = True
        msg_group.group = "5"
        msg_group.human_readable_log = "L Grp"

        mock_session.get_next.side_effect = [
            msg_gen,
            msg_area,
            msg_group,
            asyncio.CancelledError(),
        ]
        mock_session_class.return_value = mock_session

        gateway_handler.send_status_request = AsyncMock()

        try:
            await gateway_handler.listening_loop()
        except asyncio.CancelledError:
            pass

        assert gateway_handler.hass.bus.async_fire.call_count >= 3
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_general_light_event", {"message": str(msg_gen), "event": "on"})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_area_light_event", {"message": str(msg_area), "area": "1", "event": "off"})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_group_light_event", {"message": str(msg_group), "group": "5", "event": "on"})


@pytest.mark.asyncio
async def test_listening_loop_automation(gateway_handler):
    with patch("custom_components.myhome.gateway.OWNEventSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.connect = AsyncMock(return_value={"Success": True})
        mock_session.get_next = AsyncMock()

        msg_gen = MagicMock(spec=OWNAutomationEvent)
        msg_gen.is_translation = False
        msg_gen.is_general = True
        msg_gen.is_opening = True
        msg_gen.is_closing = False
        msg_gen.human_readable_log = "A Gen"

        msg_area = MagicMock(spec=OWNAutomationEvent)
        msg_area.is_translation = False
        msg_area.is_general = False
        msg_area.is_area = True
        msg_area.is_opening = False
        msg_area.is_closing = True
        msg_area.area = "2"
        msg_area.human_readable_log = "A Area"

        msg_group = MagicMock(spec=OWNAutomationEvent)
        msg_group.is_translation = False
        msg_group.is_general = False
        msg_group.is_area = False
        msg_group.is_group = True
        msg_group.is_opening = False
        msg_group.is_closing = False
        msg_group.group = "6"
        msg_group.human_readable_log = "A Grp"

        mock_session.get_next.side_effect = [
            msg_gen,
            msg_area,
            msg_group,
            asyncio.CancelledError(),
        ]
        mock_session_class.return_value = mock_session

        gateway_handler.send_status_request = AsyncMock()

        try:
            await gateway_handler.listening_loop()
        except asyncio.CancelledError:
            pass

        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_general_automation_event", {"message": str(msg_gen), "event": "open"})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_area_automation_event", {"message": str(msg_area), "area": "2", "event": "close"})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_group_automation_event", {"message": str(msg_group), "group": "6", "event": "stop"})


@pytest.mark.asyncio
async def test_listening_loop_automation_remaining_branches(gateway_handler):
    with patch("custom_components.myhome.gateway.OWNEventSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.connect = AsyncMock(return_value={"Success": True})
        mock_session.get_next = AsyncMock()

        # General close and stop
        msg_gen_close = MagicMock(spec=OWNAutomationEvent)
        msg_gen_close.is_translation = False
        msg_gen_close.is_general = True
        msg_gen_close.is_opening = False
        msg_gen_close.is_closing = True
        msg_gen_close.human_readable_log = "A Gen Close"

        msg_gen_stop = MagicMock(spec=OWNAutomationEvent)
        msg_gen_stop.is_translation = False
        msg_gen_stop.is_general = True
        msg_gen_stop.is_opening = False
        msg_gen_stop.is_closing = False
        msg_gen_stop.human_readable_log = "A Gen Stop"

        # Area open and stop
        msg_area_open = MagicMock(spec=OWNAutomationEvent)
        msg_area_open.is_translation = False
        msg_area_open.is_general = False
        msg_area_open.is_area = True
        msg_area_open.is_opening = True
        msg_area_open.is_closing = False
        msg_area_open.area = "3"
        msg_area_open.human_readable_log = "A Area Open"

        msg_area_stop = MagicMock(spec=OWNAutomationEvent)
        msg_area_stop.is_translation = False
        msg_area_stop.is_general = False
        msg_area_stop.is_area = True
        msg_area_stop.is_opening = False
        msg_area_stop.is_closing = False
        msg_area_stop.area = "3"
        msg_area_stop.human_readable_log = "A Area Stop"

        # Group open and close
        msg_grp_open = MagicMock(spec=OWNAutomationEvent)
        msg_grp_open.is_translation = False
        msg_grp_open.is_general = False
        msg_grp_open.is_area = False
        msg_grp_open.is_group = True
        msg_grp_open.is_opening = True
        msg_grp_open.is_closing = False
        msg_grp_open.group = "7"
        msg_grp_open.human_readable_log = "A Grp Open"

        msg_grp_close = MagicMock(spec=OWNAutomationEvent)
        msg_grp_close.is_translation = False
        msg_grp_close.is_general = False
        msg_grp_close.is_area = False
        msg_grp_close.is_group = True
        msg_grp_close.is_opening = False
        msg_grp_close.is_closing = True
        msg_grp_close.group = "7"
        msg_grp_close.human_readable_log = "A Grp Close"

        mock_session.get_next.side_effect = [
            msg_gen_close,
            msg_gen_stop,
            msg_area_open,
            msg_area_stop,
            msg_grp_open,
            msg_grp_close,
            asyncio.CancelledError(),
        ]
        mock_session_class.return_value = mock_session
        gateway_handler.send_status_request = AsyncMock()

        try:
            await gateway_handler.listening_loop()
        except asyncio.CancelledError:
            pass

        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_general_automation_event", {"message": str(msg_gen_close), "event": "close"})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_general_automation_event", {"message": str(msg_gen_stop), "event": "stop"})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_area_automation_event", {"message": str(msg_area_open), "area": "3", "event": "open"})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_area_automation_event", {"message": str(msg_area_stop), "area": "3", "event": "stop"})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_group_automation_event", {"message": str(msg_grp_open), "group": "7", "event": "open"})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_group_automation_event", {"message": str(msg_grp_close), "group": "7", "event": "close"})


@pytest.mark.asyncio
async def test_listening_loop_other_events(gateway_handler):
    with patch("custom_components.myhome.gateway.OWNEventSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.connect = AsyncMock(return_value={"Success": True})
        mock_session.get_next = AsyncMock()

        msg_heat = MagicMock(spec=OWNHeatingCommand)
        msg_heat.dimension = 14
        msg_heat.where = "#4"

        msg_heat2 = MagicMock(spec=OWNHeatingCommand)
        msg_heat2.dimension = 14
        msg_heat2.where = "5"

        msg_cenplus = MagicMock(spec=OWNCENPlusEvent)
        msg_cenplus.is_short_pressed = True
        msg_cenplus.is_short_pressed_and_hold = False
        msg_cenplus.is_held = False
        msg_cenplus.is_still_held = False
        msg_cenplus.is_released = False
        msg_cenplus.object = "1"
        msg_cenplus.push_button = "2"
        msg_cenplus.human_readable_log = "CP"

        msg_cen = MagicMock(spec=OWNCENEvent)
        msg_cen.is_pressed = False
        msg_cen.is_released_after_short_press = False
        msg_cen.is_held = True
        msg_cen.is_released_after_long_press = False
        msg_cen.object = "3"
        msg_cen.push_button = "4"
        msg_cen.human_readable_log = "C"

        msg_alarm = MagicMock(spec=OWNAlarmEvent)
        msg_alarm.where = "0"
        msg_alarm.state_name = "activation"
        msg_alarm.state_code = 1
        msg_alarm.is_alarm = False
        msg_alarm.human_readable_log = "Alarm Activation"

        mock_session.get_next.side_effect = [
            msg_heat,
            msg_heat2,
            msg_cenplus,
            msg_cen,
            msg_alarm,
            asyncio.CancelledError(),
        ]
        mock_session_class.return_value = mock_session

        gateway_handler.send_status_request = AsyncMock()

        try:
            await gateway_handler.listening_loop()
        except asyncio.CancelledError:
            pass

        gateway_handler.hass.bus.async_fire.assert_any_call(
            "myhome_cenplus_event",
            {
                "object": 1,
                "pushbutton": 2,
                "event": CONF_SHORT_PRESS,
                "where": "1",
                "gateway_mac": gateway_handler.mac,
            },
        )
        gateway_handler.hass.bus.async_fire.assert_any_call(
            "myhome_cen_event",
            {
                "object": 3,
                "pushbutton": 4,
                "event": CONF_LONG_PRESS,
                "where": "3",
                "gateway_mac": gateway_handler.mac,
            },
        )
        gateway_handler.hass.bus.async_fire.assert_any_call(
            "myhome_alarm_event",
            {
                "where": "0",
                "state": "activation",
                "state_code": 1,
                "is_alarm": False,
                "message": str(msg_alarm),
            },
        )
        assert gateway_handler.send_status_request.call_count >= 2



@pytest.mark.asyncio
async def test_listening_loop_cen_and_cenplus_variants(gateway_handler):
    with patch("custom_components.myhome.gateway.OWNEventSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.connect = AsyncMock(return_value={"Success": True})
        mock_session.get_next = AsyncMock()

        # CENPlus: held, still_held, released, unmapped
        cp_held = MagicMock(spec=OWNCENPlusEvent, is_short_pressed=False, is_held=True, is_still_held=False, is_released=False, object="1", push_button="1", human_readable_log="cp_held")
        cp_still_held = MagicMock(spec=OWNCENPlusEvent, is_short_pressed=False, is_held=False, is_still_held=True, is_released=False, object="1", push_button="2", human_readable_log="cp_still_held")
        cp_released = MagicMock(spec=OWNCENPlusEvent, is_short_pressed=False, is_held=False, is_still_held=False, is_released=True, object="1", push_button="3", human_readable_log="cp_rel")
        cp_unmapped = MagicMock(spec=OWNCENPlusEvent, is_short_pressed=False, is_held=False, is_still_held=False, is_released=False, object="1", push_button="4", human_readable_log="cp_unm")

        # Rotary CEN+ events
        cp_cw_slow = MagicMock(spec=OWNCENPlusEvent, is_short_pressed=False, is_held=False, is_still_held=False, is_released=False, is_slowly_turned_cw=True, object="1", push_button="5", human_readable_log="cp_cw_s")
        cp_cw_fast = MagicMock(spec=OWNCENPlusEvent, is_short_pressed=False, is_held=False, is_still_held=False, is_released=False, is_quickly_turned_cw=True, object="1", push_button="6", human_readable_log="cp_cw_f")
        cp_ccw_slow = MagicMock(spec=OWNCENPlusEvent, is_short_pressed=False, is_held=False, is_still_held=False, is_released=False, is_slowly_turned_ccw=True, object="1", push_button="7", human_readable_log="cp_ccw_s")
        cp_ccw_fast = MagicMock(spec=OWNCENPlusEvent, is_short_pressed=False, is_held=False, is_still_held=False, is_released=False, is_quickly_turned_ccw=True, object="1", push_button="8", human_readable_log="cp_ccw_f")

        # CEN: pressed, released_after_short, released_after_long, unmapped
        c_pressed = MagicMock(spec=OWNCENEvent, is_pressed=True, is_released_after_short_press=False, is_held=False, is_released_after_long_press=False, object="2", push_button="1", human_readable_log="c_press")
        c_rel_short = MagicMock(spec=OWNCENEvent, is_pressed=False, is_released_after_short_press=True, is_held=False, is_released_after_long_press=False, object="2", push_button="2", human_readable_log="c_rel_short")
        c_rel_long = MagicMock(spec=OWNCENEvent, is_pressed=False, is_released_after_short_press=False, is_held=False, is_released_after_long_press=True, object="2", push_button="3", human_readable_log="c_rel_long")
        c_unmapped = MagicMock(spec=OWNCENEvent, is_pressed=False, is_released_after_short_press=False, is_held=False, is_released_after_long_press=False, object="2", push_button="4", human_readable_log="c_unm")

        mock_session.get_next.side_effect = [
            cp_held,
            cp_still_held,
            cp_released,
            cp_unmapped,
            cp_cw_slow,
            cp_cw_fast,
            cp_ccw_slow,
            cp_ccw_fast,
            c_pressed,
            c_rel_short,
            c_rel_long,
            c_unmapped,
            asyncio.CancelledError(),
        ]
        mock_session_class.return_value = mock_session
        gateway_handler.send_status_request = AsyncMock()

        try:
            await gateway_handler.listening_loop()
        except asyncio.CancelledError:
            pass

        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_cenplus_event", {"object": 1, "pushbutton": 1, "event": CONF_LONG_PRESS, "where": "1", "gateway_mac": gateway_handler.mac})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_cenplus_event", {"object": 1, "pushbutton": 2, "event": CONF_LONG_PRESS, "where": "1", "gateway_mac": gateway_handler.mac})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_cenplus_event", {"object": 1, "pushbutton": 3, "event": CONF_LONG_RELEASE, "where": "1", "gateway_mac": gateway_handler.mac})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_cenplus_event", {"object": 1, "pushbutton": 4, "event": None, "where": "1", "gateway_mac": gateway_handler.mac})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_cenplus_event", {"object": 1, "pushbutton": 5, "event": CONF_ROTARY_CW_SLOW, "where": "1", "gateway_mac": gateway_handler.mac})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_cenplus_event", {"object": 1, "pushbutton": 6, "event": CONF_ROTARY_CW_FAST, "where": "1", "gateway_mac": gateway_handler.mac})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_cenplus_event", {"object": 1, "pushbutton": 7, "event": CONF_ROTARY_CCW_SLOW, "where": "1", "gateway_mac": gateway_handler.mac})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_cenplus_event", {"object": 1, "pushbutton": 8, "event": CONF_ROTARY_CCW_FAST, "where": "1", "gateway_mac": gateway_handler.mac})

        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_cen_event", {"object": 2, "pushbutton": 1, "event": CONF_SHORT_PRESS, "where": "2", "gateway_mac": gateway_handler.mac})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_cen_event", {"object": 2, "pushbutton": 2, "event": CONF_SHORT_RELEASE, "where": "2", "gateway_mac": gateway_handler.mac})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_cen_event", {"object": 2, "pushbutton": 3, "event": CONF_LONG_RELEASE, "where": "2", "gateway_mac": gateway_handler.mac})
        gateway_handler.hass.bus.async_fire.assert_any_call("myhome_cen_event", {"object": 2, "pushbutton": 4, "event": None, "where": "2", "gateway_mac": gateway_handler.mac})


@pytest.mark.asyncio
async def test_listening_loop_translation_gateway_and_unsupported(gateway_handler):
    with patch("custom_components.myhome.gateway.OWNEventSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.connect = AsyncMock(return_value={"Success": True})
        mock_session.get_next = AsyncMock()

        # Translation message
        msg_trans = MagicMock(spec=OWNLightingEvent)
        msg_trans.is_translation = True
        msg_trans.human_readable_log = "Trans"

        # Gateway Event
        msg_gw_evt = MagicMock(spec=OWNGatewayEvent)
        msg_gw_evt.human_readable_log = "GW Evt"

        # Gateway Command
        msg_gw_cmd = MagicMock(spec=OWNGatewayCommand)
        msg_gw_cmd.human_readable_log = "GW Cmd"

        # Energy message
        msg_energy = MagicMock(spec=OWNEnergyEvent)
        msg_energy.who = 18
        msg_energy.human_readable_log = "Energy"

        # Unsupported OWNMessage
        msg_unsupported = MagicMock(spec=OWNMessage)
        msg_unsupported.human_readable_log = "Unsupported"

        # Non-message raw data
        raw_non_msg = "RAW_BYTES_STRING"

        mock_session.get_next.side_effect = [
            msg_trans,
            msg_gw_evt,
            msg_gw_cmd,
            msg_energy,
            msg_unsupported,
            raw_non_msg,
            asyncio.CancelledError(),
        ]
        mock_session_class.return_value = mock_session
        gateway_handler.send_status_request = AsyncMock()

        try:
            await gateway_handler.listening_loop()
        except asyncio.CancelledError:
            pass

        assert len(gateway_handler.bus_monitor.get_recent_frames()) >= 4


@pytest.mark.asyncio
async def test_listening_loop_generate_events_and_clean_termination(gateway_handler):
    gateway_handler.generate_events = True
    with patch("custom_components.myhome.gateway.OWNEventSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.connect = AsyncMock(return_value={"Success": True})
        mock_session.close = AsyncMock()
        mock_session.get_next = AsyncMock()

        msg_own = MagicMock(spec=OWNLightingEvent)
        msg_own.event_content = {"what": "1", "where": "12"}
        msg_own.is_translation = False
        msg_own.is_general = False
        msg_own.is_area = False
        msg_own.is_group = False
        msg_own.human_readable_log = "Msg"

        raw_str = "NON_OWN_MESSAGE"

        def side_effect():
            # Terminate listener after 2 messages
            yield msg_own
            yield raw_str
            gateway_handler._terminate_listener = True
            yield None

        gen = side_effect()
        mock_session.get_next.side_effect = lambda: next(gen)
        mock_session_class.return_value = mock_session
        gateway_handler.send_status_request = AsyncMock()

        await gateway_handler.listening_loop()

        # Both messages dispatched to event bus
        gateway_handler.hass.bus.async_fire.assert_any_call(
            "myhome_message_event",
            {"gateway": "192.168.1.5", "what": "1", "where": "12"},
        )
        gateway_handler.hass.bus.async_fire.assert_any_call(
            "myhome_message_event",
            {"gateway": "192.168.1.5", "message": "NON_OWN_MESSAGE"},
        )
        mock_session.close.assert_called_once()
        assert gateway_handler.is_connected is False


@pytest.mark.asyncio
async def test_listening_loop_auth_failure_lockout_protection(gateway_handler):
    with patch("custom_components.myhome.gateway.OWNEventSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.connect = AsyncMock(return_value={"Success": False, "Message": "password_error"})
        mock_session_class.return_value = mock_session

        await gateway_handler.listening_loop()
        assert gateway_handler.is_connected is False


@pytest.mark.asyncio
async def test_sending_loop(gateway_handler):
    with patch("custom_components.myhome.gateway.OWNCommandSession") as mock_cmd_class:
        mock_cmd_session = MagicMock()
        mock_cmd_session.connect = AsyncMock(return_value={"Success": True})
        mock_cmd_session.send = AsyncMock()
        mock_cmd_session.close = AsyncMock()
        mock_cmd_class.return_value = mock_cmd_session
        gateway_handler._event_session_ready.set()

        gateway_handler.sending_workers = [MagicMock()]

        mock_queue = MagicMock()
        mock_queue.get = AsyncMock(side_effect=[
            {"message": "msg1", "is_status_request": False},
            {"message": "msg2", "is_status_request": True},
        ])
        gateway_handler.send_buffer = mock_queue

        def mock_send(message, is_status_request):
            if message == "msg2":
                gateway_handler._terminate_sender = True

        mock_cmd_session.send.side_effect = mock_send

        await gateway_handler.sending_loop(0)

        assert mock_cmd_session.send.call_count == 2
        mock_cmd_session.close.assert_called_once()


@pytest.mark.asyncio
async def test_sending_loop_auth_failure_lockout_protection(gateway_handler):
    with patch("custom_components.myhome.gateway.OWNCommandSession") as mock_cmd_class:
        mock_cmd_session = MagicMock()
        mock_cmd_session.connect = AsyncMock(return_value={"Success": False, "Message": "negotiation_refused"})
        mock_cmd_class.return_value = mock_cmd_session
        gateway_handler._event_session_ready.set()

        await gateway_handler.sending_loop(0)
        mock_cmd_session.connect.assert_called_once()


@pytest.mark.asyncio
async def test_sending_loop_collected_responses_and_pacing(gateway_handler):
    with patch("custom_components.myhome.gateway.OWNCommandSession") as mock_cmd_class:
        mock_cmd_session = MagicMock()
        mock_cmd_session.connect = AsyncMock(return_value={"Success": True})
        mock_cmd_session.close = AsyncMock()
        gateway_handler._event_session_ready.set()

        resp_msg = MagicMock(spec=OWNMessage)
        resp_raw = "*#1*0##"
        mock_cmd_session.send = AsyncMock(return_value=[resp_msg, resp_raw])
        mock_cmd_class.return_value = mock_cmd_session

        # Configure gateway profile delay
        gateway_handler.gateway.profile = GatewayProfile(
            model_name=gateway_handler.gateway.profile.model_name,
            command_queue_delay=0.01,
        )

        # Queue contains 1 message, then None to terminate
        cmd = MagicMock(spec=OWNCommand)
        await gateway_handler.send_buffer.put({"message": cmd, "is_status_request": False})
        await gateway_handler.send_buffer.put(None)

        with patch("custom_components.myhome.gateway.async_dispatcher_send") as mock_dispatcher:
            await gateway_handler.sending_loop(0)

            mock_cmd_session.send.assert_called_once_with(message=cmd, is_status_request=False)
            mock_dispatcher.assert_called_once_with(
                gateway_handler.hass,
                f"myhome_message_{gateway_handler.mac}",
                resp_msg,
            )
            assert len(gateway_handler.bus_monitor.get_recent_frames()) >= 2
            mock_cmd_session.close.assert_called_once()


@pytest.mark.asyncio
async def test_gateway_cen_event_and_auto_registration(gateway_handler: MyHOMEGatewayHandler):
    """Test receiving OWNCENEvent dispatches bus event and registers CEN scenario device."""
    mock_dr = MagicMock()
    gateway_handler.config_entry.entry_id = "test_entry_123"

    cen_msg = MagicMock(spec=OWNCENEvent)
    cen_msg.object = "5"
    cen_msg.push_button = "1"
    cen_msg.is_pressed = True
    cen_msg.is_released_after_short_press = False
    cen_msg.is_held = False
    cen_msg.is_released_after_long_press = False
    cen_msg.human_readable_log = "Button 1 pressed on CEN 5"

    with patch("custom_components.myhome.gateway.OWNEventSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.connect = AsyncMock(return_value={"Success": True})
        mock_session.get_next = AsyncMock(side_effect=[cen_msg, cen_msg, asyncio.CancelledError()])
        mock_session_class.return_value = mock_session

        with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr):
            with patch.object(gateway_handler.hass.bus, "async_fire") as mock_fire:
                try:
                    await gateway_handler.listening_loop()
                except asyncio.CancelledError:
                    pass

                # Check bus event fired with object, pushbutton, event, where, gateway_mac, entry_id
                mock_fire.assert_any_call(
                    "myhome_cen_event",
                    {
                        "object": 5,
                        "pushbutton": 1,
                        "event": CONF_SHORT_PRESS,
                        "where": "5",
                        "gateway_mac": gateway_handler.mac,
                        "entry_id": "test_entry_123",
                    },
                )

                # Check device registry auto-registration called once (deduplicated)
                mock_dr.async_get_or_create.assert_called_once_with(
                    config_entry_id="test_entry_123",
                    identifiers={(DOMAIN, f"{gateway_handler.mac}-15-5")},
                    name="CEN Unit 5",
                    manufacturer="BTicino",
                    model="CEN Scenario Control",
                    via_device=(DOMAIN, gateway_handler.mac),
                )


@pytest.mark.asyncio
async def test_gateway_cenplus_event_and_auto_registration(gateway_handler: MyHOMEGatewayHandler):
    """Test receiving OWNCENPlusEvent dispatches bus event and registers CEN+ scenario device."""
    mock_dr = MagicMock()
    gateway_handler.config_entry.entry_id = "test_entry_456"

    cenplus_msg = MagicMock(spec=OWNCENPlusEvent)
    cenplus_msg.object = "12"
    cenplus_msg.push_button = "3"
    cenplus_msg.is_short_pressed = False
    cenplus_msg.is_held = False
    cenplus_msg.is_still_held = False
    cenplus_msg.is_released = False
    cenplus_msg.is_slowly_turned_cw = False
    cenplus_msg.is_quickly_turned_cw = True
    cenplus_msg.is_slowly_turned_ccw = False
    cenplus_msg.is_quickly_turned_ccw = False
    cenplus_msg.human_readable_log = "Button 3 quickly rotated CW on CEN+ 12"

    with patch("custom_components.myhome.gateway.OWNEventSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.connect = AsyncMock(return_value={"Success": True})
        mock_session.get_next = AsyncMock(side_effect=[cenplus_msg, asyncio.CancelledError()])
        mock_session_class.return_value = mock_session

        with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr):
            with patch.object(gateway_handler.hass.bus, "async_fire") as mock_fire:
                try:
                    await gateway_handler.listening_loop()
                except asyncio.CancelledError:
                    pass

                # Check bus event fired
                mock_fire.assert_called_once_with(
                    "myhome_cenplus_event",
                    {
                        "object": 12,
                        "pushbutton": 3,
                        "event": CONF_ROTARY_CW_FAST,
                        "where": "12",
                        "gateway_mac": gateway_handler.mac,
                        "entry_id": "test_entry_456",
                    },
                )

                # Check device registry auto-registration
                mock_dr.async_get_or_create.assert_called_once_with(
                    config_entry_id="test_entry_456",
                    identifiers={(DOMAIN, f"{gateway_handler.mac}-25-12")},
                    name="CEN+ Unit 12",
                    manufacturer="BTicino",
                    model="CEN+ Scenario Control",
                    via_device=(DOMAIN, gateway_handler.mac),
                )


@pytest.mark.asyncio
async def test_sending_loop_waits_for_event_session(gateway_handler):
    command_connect_started = asyncio.Event()

    with patch("custom_components.myhome.gateway.OWNCommandSession") as mock_cmd_class:
        mock_cmd_session = MagicMock()

        async def connect():
            command_connect_started.set()
            return {"Success": True}

        mock_cmd_session.connect = connect
        mock_cmd_session.close = AsyncMock()
        mock_cmd_class.return_value = mock_cmd_session

        worker = asyncio.create_task(gateway_handler.sending_loop(0))
        await asyncio.sleep(0)

        assert not command_connect_started.is_set()

        gateway_handler._event_session_ready.set()
        await asyncio.wait_for(command_connect_started.wait(), timeout=1)
        await gateway_handler.send_buffer.put(None)
        await asyncio.wait_for(worker, timeout=1)

        mock_cmd_session.close.assert_called_once()


def test_event_connection_state_controls_command_readiness(gateway_handler):
    assert gateway_handler.is_connected is False
    assert gateway_handler._event_session_ready.is_set() is False

    gateway_handler._on_event_connection_state_change(True)

    assert gateway_handler.is_connected is True
    assert gateway_handler._event_session_ready.is_set() is True

    gateway_handler._on_event_connection_state_change(False)

    assert gateway_handler.is_connected is False
    assert gateway_handler._event_session_ready.is_set() is False


async def test_sending_loop_idle_timeout_closes_session(gateway_handler, monkeypatch):
    """Verify idle timeout in sending_loop closes command session to free gateway sockets."""
    import custom_components.myhome.gateway as gw_module
    monkeypatch.setattr(gw_module, "COMMAND_SESSION_IDLE_TIMEOUT", 0.01)

    with patch("custom_components.myhome.gateway.OWNCommandSession") as mock_cmd_class:
        mock_cmd_session = MagicMock()
        mock_cmd_session.connect = AsyncMock(return_value={"Success": True})
        mock_cmd_session.close = AsyncMock()
        mock_cmd_session.is_connected = True
        mock_cmd_class.return_value = mock_cmd_session

        gateway_handler._event_session_ready.set()
        worker = asyncio.create_task(gateway_handler.sending_loop(0))

        # Allow idle timeout to trigger
        await asyncio.sleep(0.05)
        mock_cmd_session.close.assert_called()

        # Stop worker
        await gateway_handler.send_buffer.put(None)
        await asyncio.wait_for(worker, timeout=1)


async def test_issue_254_mh201_idle_disconnect_and_reconnection_e2e(gateway_handler, monkeypatch):
    """Verify Issue #254: MH201 idle disconnect releases socket and reconnects for subsequent commands."""
    from OWNd.message import OWNCommand

    import custom_components.myhome.gateway as gw_module

    # Set idle timeout short for testing
    monkeypatch.setattr(gw_module, "COMMAND_SESSION_IDLE_TIMEOUT", 0.04)

    with patch("custom_components.myhome.gateway.OWNCommandSession") as mock_cmd_class:
        mock_cmd_session = MagicMock()
        mock_cmd_session.connect = AsyncMock(return_value={"Success": True})
        mock_cmd_session.close = AsyncMock()
        mock_cmd_session.send = AsyncMock(return_value=True)
        mock_cmd_session.is_connected = True
        mock_cmd_class.return_value = mock_cmd_session

        gateway_handler._event_session_ready.set()
        worker = asyncio.create_task(gateway_handler.sending_loop(0))

        # 1. User sends cover command *2*1*14##
        cmd1 = OWNCommand.parse("*2*1*14##")
        await gateway_handler.send(cmd1)
        await asyncio.sleep(0.08)
        mock_cmd_session.send.assert_called_with(message=cmd1, is_status_request=False)

        # 2. Simulate 49-minute idle period: idle timeout fires and releases socket
        await asyncio.sleep(0.12)
        assert mock_cmd_session.close.call_count >= 1

        # 3. User sends another cover command after idle: *2*1*14##
        mock_cmd_session.send.reset_mock()
        cmd2 = OWNCommand.parse("*2*1*14##")
        await gateway_handler.send(cmd2)
        await asyncio.sleep(0.08)
        mock_cmd_session.send.assert_called_with(message=cmd2, is_status_request=False)

        # Clean shutdown
        await gateway_handler.send_buffer.put(None)
        await asyncio.wait_for(worker, timeout=1)


async def test_gateway_properties_and_cen_branches(gateway_handler):
    """Test gateway manufacturer/firmware tuple/list handling and CEN device edge cases."""
    gateway_handler.gateway = MagicMock()
    # Manufacturer as tuple/list
    gateway_handler.gateway.manufacturer = ["BTicino", "Legrand"]
    assert gateway_handler.manufacturer == "BTicino"

    # Firmware as tuple/list
    gateway_handler.gateway.firmware = [1, 0, 5]
    assert gateway_handler.firmware == "1.0.5"

    # CEN device when config_entry has no entry_id
    gateway_handler.config_entry = MagicMock(spec=[])
    gateway_handler._cen_devices.clear()
    gateway_handler._ensure_cen_device(25, 1)
    assert (25, 1) in gateway_handler._cen_devices


async def test_gateway_listening_loop_unhandled_event_status(gateway_handler):
    """Test listening_loop when event session returns an unexpected failure dict or None."""
    with patch("custom_components.myhome.gateway.OWNEventSession") as mock_event_class:
        mock_event_session = MagicMock()
        mock_event_session.connect = AsyncMock(return_value=None)
        mock_event_session.is_connected = False
        mock_event_session.get_next = AsyncMock(return_value=None)
        mock_event_session.close = AsyncMock()
        mock_event_class.return_value = mock_event_session

        async def stop_listener(*args, **kwargs):
            gateway_handler._terminate_listener = True

        with patch.object(gateway_handler, "send_status_request", side_effect=stop_listener) as mock_status:
            await gateway_handler.listening_loop()
            assert gateway_handler.is_connected is False
            assert mock_status.call_count >= 1


async def test_gateway_sending_loop_timeout_and_terminate_branches(gateway_handler, monkeypatch):
    """Test sending_loop timeout while waiting for event session and terminate while waiting."""
    import custom_components.myhome.gateway as gw_module
    monkeypatch.setattr(gw_module, "EVENT_READY_TIMEOUT", 0.01)
    monkeypatch.setattr(gw_module, "COMMAND_SESSION_IDLE_TIMEOUT", 0.02)

    gateway_handler._event_session_ready.clear()
    gateway_handler._terminate_sender = False

    # 1. Trigger timeout in wait loop once, then set event ready
    async def wake_up_event():
        await asyncio.sleep(0.02)
        gateway_handler._event_session_ready.set()

    wake_task = asyncio.create_task(wake_up_event())

    with patch("custom_components.myhome.gateway.OWNCommandSession") as mock_cmd_class:
        mock_cmd = MagicMock()
        mock_cmd.connect = AsyncMock(return_value={"Success": True})
        mock_cmd.close = AsyncMock()
        mock_cmd.is_connected = True
        mock_cmd_class.return_value = mock_cmd

        worker = asyncio.create_task(gateway_handler.sending_loop(0))
        await asyncio.sleep(0.05)

        # Stop worker
        await gateway_handler.send_buffer.put(None)
        await asyncio.wait_for(worker, timeout=1)
        await wake_task

    # 2. Terminate sender while event session not ready
    gateway_handler._event_session_ready.clear()

    async def terminate_during_wait():
        await asyncio.sleep(0.02)
        gateway_handler._terminate_sender = True
        gateway_handler._event_session_ready.set()

    term_task = asyncio.create_task(terminate_during_wait())
    await gateway_handler.sending_loop(1)
    await term_task



