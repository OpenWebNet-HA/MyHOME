"""Test services module directly."""
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant

from custom_components.myhome.const import (
    ATTR_GATEWAY,
    ATTR_MESSAGE,
    DOMAIN,
)
from custom_components.myhome.services import (
    SERVICE_SEND_MESSAGE,
    SERVICE_SWEEP_BUS,
    SERVICE_SYNC_TIME,
    _get_gateway_handler,
    async_setup_services,
)


async def test_services_setup_is_idempotent(hass: HomeAssistant) -> None:
    """Services are registered once in async_setup and never unregistered."""
    assert not hass.services.has_service(DOMAIN, SERVICE_SYNC_TIME)
    assert not hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE)
    assert not hass.services.has_service(DOMAIN, SERVICE_SWEEP_BUS)

    await async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_SYNC_TIME)
    assert hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE)
    assert hass.services.has_service(DOMAIN, SERVICE_SWEEP_BUS)

    # Idempotent re-setup
    await async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_SYNC_TIME)


async def test_get_gateway_handler_helper(hass: HomeAssistant, attach_gateway) -> None:
    """Test _get_gateway_handler lookup helper."""
    # No config entries at all
    assert _get_gateway_handler(hass, None) is None

    # An entry that is not set up (no runtime_data) does not count
    attach_gateway("00:03:50:00:00:01", MagicMock(), legacy_only=True)
    assert _get_gateway_handler(hass, None) is None

    # When valid gateway configured
    mock_handler = MagicMock()
    gw_mac = "00:03:50:AA:BB:CC"
    attach_gateway(gw_mac, mock_handler)

    # Default lookup (None)
    assert _get_gateway_handler(hass, None) == mock_handler

    # Specific valid lookup
    assert _get_gateway_handler(hass, gw_mac) == mock_handler

    # Invalid MAC format
    assert _get_gateway_handler(hass, "invalid_mac") is None

    # Unconfigured MAC
    assert _get_gateway_handler(hass, "00:03:50:99:99:99") is None

    # Format MAC lookup
    gw_mac_fmt = "00:03:50:11:22:33"
    attach_gateway(gw_mac_fmt, mock_handler)
    assert _get_gateway_handler(hass, "000350112233") == mock_handler

    # Case-insensitive MAC lookup
    gw_mac_case = "00:03:50:AA:BB:DD"
    attach_gateway(gw_mac_case, mock_handler)
    assert _get_gateway_handler(hass, "000350aabbdd") == mock_handler


async def test_services_edge_cases(hass: HomeAssistant, attach_gateway) -> None:
    """Test edge cases for service calls."""
    await async_setup_services(hass)

    mock_handler = MagicMock()
    gw_mac = "00:03:50:aa:bb:cc"
    entry = attach_gateway(gw_mac, mock_handler)

    # Test send_message with message=None
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SEND_MESSAGE,
        {ATTR_GATEWAY: gw_mac, ATTR_MESSAGE: None},
        blocking=True,
    )

    # Test sweep_bus when no gateway is set up
    entry.runtime_data = None
    await hass.services.async_call(DOMAIN, SERVICE_SWEEP_BUS, {}, blocking=True)


async def test_sweep_bus_queries_sent(hass: HomeAssistant, attach_gateway) -> None:
    """sweep_bus sends the gateway, general and energy queries; the general ones paced (#578)."""
    from unittest.mock import AsyncMock
    await async_setup_services(hass)

    mock_handler = MagicMock()
    mock_handler.send = AsyncMock()
    mock_handler.send_status_request = AsyncMock()
    mock_handler.send_paced = AsyncMock()
    order = MagicMock()
    order.attach_mock(mock_handler.send_status_request, "send_status_request")
    order.attach_mock(mock_handler.send_paced, "send_paced")
    gw_mac = "00:03:50:aa:bb:cc"
    attach_gateway(gw_mac, mock_handler)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SWEEP_BUS,
        {ATTR_GATEWAY: gw_mac},
        blocking=True,
    )

    # General requests go through the paced sweep, in one call, as status requests (a NACK is then logged at DEBUG)
    mock_handler.send_paced.assert_awaited_once_with(
        ["*#1*0##", "*#2*0##", "*#4*0##", "*#5*0##", "*#16*0*5##"]
    )
    # ... after the gateway queries and before the energy queries
    names = [call[0] for call in order.mock_calls]
    paced_at = names.index("send_paced")
    assert names[:paced_at] == ["send_status_request"] * 3
    assert names[paced_at + 1:] == ["send_status_request"] * 36

    sent_raw = [str(call.args[0]) for call in mock_handler.send_status_request.await_args_list]
    assert sent_raw[:3] == ["*#13**0##", "*#13**15##", "*#13**16##"]
    assert not any(raw.startswith(("*#1*", "*#2*", "*#4*", "*#5*", "*#16*")) for raw in sent_raw)
    mock_handler.send.assert_not_called()
    # Energy Management (WHO 18) F520 discovery queries
    assert "*#18*51*51##" in sent_raw
    assert "*#18*51*1200##" in sent_raw
    assert "*#18*52*51##" in sent_raw
    assert "*#18*52*1200##" in sent_raw
    assert "*#18*59*51##" in sent_raw
    assert "*#18*59*1200##" in sent_raw
    assert "*#18*71#0*51##" in sent_raw
    assert "*#18*71#0*1200##" in sent_raw
    assert "*#18*79#0*51##" in sent_raw
    assert "*#18*79#0*1200##" in sent_raw


async def test_sweep_bus_follower_delegated_who1(hass: HomeAssistant, attach_gateway) -> None:
    """Test sweep_bus sends *#1*0## when a follower gateway has WHO 1 delegated."""
    from unittest.mock import AsyncMock
    await async_setup_services(hass)

    mock_handler = MagicMock()
    mock_handler.send = AsyncMock()
    mock_handler.send_status_request = AsyncMock()
    mock_handler.send_paced = AsyncMock()
    mock_handler.is_follower = True
    mock_handler.delegated_whos = {1}
    gw_mac = "00:03:50:aa:bb:dd"
    attach_gateway(gw_mac, mock_handler)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SWEEP_BUS,
        {ATTR_GATEWAY: gw_mac},
        blocking=True,
    )

    mock_handler.send_paced.assert_awaited_once_with(["*#1*0##"])
    sent_raw = [str(call.args[0]) for call in mock_handler.send_status_request.await_args_list]
    assert sent_raw == ["*#13**0##", "*#13**15##", "*#13**16##"]
    mock_handler.send.assert_not_called()


async def test_sweep_bus_discovers_f520_active_power_sensor(hass: HomeAssistant, fast_bus_pacing) -> None:
    """Test sweep_bus sends Dimension 1200 query and successfully discovers active power sensors (#494)."""
    from unittest.mock import AsyncMock, patch

    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from OWNd.message import OWNMessage
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import CONF_ENTITY

    mac = "00:03:50:aa:bb:52"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.52", "mac": mac, "name": "F454"},
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    with (
        patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True}),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
        patch("asyncio.sleep", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        handler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)
        await hass.async_block_till_done()

        sent_frames: list[str] = []
        original_send = handler.send
        original_status = handler.send_status_request

        async def capture_send(msg):
            sent_frames.append(str(msg))
            return await original_send(msg)

        async def capture_status(msg):
            sent_frames.append(str(msg))
            return await original_status(msg)

        handler.send = capture_send
        handler.send_status_request = capture_status

        # Trigger sweep_bus for this gateway
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SWEEP_BUS,
            {ATTR_GATEWAY: mac},
            blocking=True,
        )

        # 1. Verify sweep_bus sent Dimension 1200 query for address 52
        assert "*#18*52*1200##" in sent_frames
        assert "*#18*52*51##" in sent_frames

        # 2. In response to Dimension 1200 query, F520 reports active power on Dimension 113
        # (*#18*52*113*1##: Sensor 52 reporting active power draw of 1 W)
        async_dispatcher_send(
            hass, f"myhome_message_{mac}", OWNMessage.parse("*#18*52*113*1##")
        )
        await hass.async_block_till_done()

        # 3. Verify active power sensor entity was discovered and registered
        registry = er.async_get(hass)
        power_unique_id = f"{mac}-18-52-power"
        power_entity_id = registry.async_get_entity_id("sensor", DOMAIN, power_unique_id)
        assert power_entity_id is not None

        power_state = hass.states.get(power_entity_id)
        assert power_state is not None
        assert power_state.state == "1"
        assert power_state.attributes.get("unit_of_measurement") == "W"
        assert power_state.attributes.get("device_class") == "power"

        # Also verify totalizer response on Dimension 51 discovers total energy sensor
        async_dispatcher_send(
            hass, f"myhome_message_{mac}", OWNMessage.parse("*#18*52*51*14159553##")
        )
        await hass.async_block_till_done()

        energy_unique_id = f"{mac}-18-52-total-energy"
        energy_entity_id = registry.async_get_entity_id("sensor", DOMAIN, energy_unique_id)
        assert energy_entity_id is not None
        energy_state = hass.states.get(energy_entity_id)
        assert energy_state is not None
        assert energy_state.state == "14159553"




async def test_stop_cover_calibration_service_forwards_gateway(hass: HomeAssistant) -> None:
    """myhome.stop_cover_calibration hands the optional gateway MAC to the cover helper."""
    from unittest.mock import AsyncMock, patch

    await async_setup_services(hass)
    with patch("custom_components.myhome.cover.async_stop_cover_calibration", AsyncMock(return_value=True)) as stop:
        await hass.services.async_call(DOMAIN, "stop_cover_calibration", {ATTR_GATEWAY: "00:03:50:aa:bb:cc"}, blocking=True)
    stop.assert_awaited_once_with(hass, gateway_mac="00:03:50:aa:bb:cc")
