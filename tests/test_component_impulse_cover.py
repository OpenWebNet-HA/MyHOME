"""Tests for MyHOME impulse cover platform (gates, garage doors, impulse relays WHO=1)."""
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import (
    CoverDeviceClass,
)
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
    STATE_OFF,
    STATE_ON,
    Platform,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from OWNd.message import (
    OWNEvent,
    OWNLightingCommand,
)
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from custom_components.myhome.access_policy import (
    AccessConfig,
    AccessDenied,
    Effect,
    Intent,
    RemoteClose,
)
from custom_components.myhome.const import (
    CONF_DEVICE_CLASS,
    CONF_MIN_CYCLE_TIME,
    CONF_PIN_CODE,
    CONF_PLATFORMS,
    CONF_PULSE_DURATION,
    CONF_TRAVEL_TIME,
    CONF_TYPE,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    TYPE_IMPULSE_RELAY,
)
from custom_components.myhome.cover import (
    PLATFORM,
    MyHOMEImpulseCover,
    async_setup_entry,
)
from tests.conftest import attach_runtime


@pytest.fixture
def mock_gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:88:88"
    gw.unique_id = "00:03:50:00:88:88"
    gw.log_id = "[Test Gateway]"
    gw.device_registry_id = "mock_gw_dev_id"
    gw.available = True
    gw.availability_signal = "myhome_gateway_availability_000350008888"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


async def test_impulse_cover_setup_restores_and_configures(hass: HomeAssistant, mock_gateway):
    """Test setup of WHO=1 impulse covers from YAML and entity registry."""
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {
                    PLATFORM: {
                        "1-22": {
                            CONF_WHERE: "22",
                            CONF_WHO: "1",
                            CONF_NAME: "Entrance Gate",
                            CONF_DEVICE_CLASS: CoverDeviceClass.GATE,
                            CONF_TYPE: TYPE_IMPULSE_RELAY,
                            CONF_TRAVEL_TIME: 20,
                            CONF_PULSE_DURATION: 0.5,
                            CONF_MIN_CYCLE_TIME: 5.0,
                            CONF_PIN_CODE: "1234",
                        },
                    }
                },
            }
        }
    }

    config_entry = MagicMock()
    config_entry.data = {CONF_MAC: mac}
    config_entry.entry_id = "cover_test_entry"

    mock_er = MagicMock()
    reg_1 = MagicMock()
    reg_1.domain = Platform.COVER
    reg_1.unique_id = f"{mac}-1-21"

    with (
        patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_er),
        patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[reg_1]),
    ):
        added_entities = []

        def fake_add_entities(entities):
            added_entities.extend(entities)

        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, fake_add_entities)

        # 1 restored (where=21) + 1 configured from YAML (where=22)
        impulse_entities = [e for e in added_entities if isinstance(e, MyHOMEImpulseCover)]
        assert len(impulse_entities) == 2
        gate = next(e for e in impulse_entities if e._where == "22")
        assert gate._device_name == "Entrance Gate"
        assert gate.device_class == CoverDeviceClass.GATE
        assert gate.extra_state_attributes["pin_protected"] is True
        assert gate.assumed_state is True


@pytest.fixture
def impulse_gate(hass: HomeAssistant, mock_gateway):
    """Create an impulse gate entity."""
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMEImpulseCover(
            hass=hass,
            name="Driveway Gate",
            entity_name=None,
            device_id="22",
            who="1",
            where="22",
            interface=None,
            device_class=CoverDeviceClass.GATE,
            travel_time=10.0,
            pulse_duration=0.5,
            min_cycle_time=2.0,
            pin_code="1234",
            state_sensor=None,
            manufacturer="BTicino",
            model="Impulse Cover",
            gateway=mock_gateway,
        )
        cover.hass = hass
        cover.entity_id = "cover.driveway_gate"
        cover.async_write_ha_state = MagicMock()
        yield cover
        if cover._pulse_off_cancel is not None:
            cover._pulse_off_cancel()
            cover._pulse_off_cancel = None
        if cover._travel_timer_cancel is not None:
            cover._travel_timer_cancel()
            cover._travel_timer_cancel = None


async def test_impulse_cover_open_and_close_cycle(hass: HomeAssistant, impulse_gate, mock_gateway):
    """Test full open and close cycle with 500ms auto-reset pulse and travel time."""
    # 1. Pulse open
    assert await impulse_gate._async_pulse(Intent.OPEN, Effect.OPEN) is True

    # Relay ON command sent immediately
    assert mock_gateway.send.call_count == 1
    on_cmd = mock_gateway.send.call_args[0][0]
    assert isinstance(on_cmd, OWNLightingCommand)
    assert str(on_cmd) == "*1*1*22##"
    assert impulse_gate.is_opening is True
    assert impulse_gate.is_closed is False

    # 2. Advance time by 0.5s: Auto-reset pulse turns relay OFF
    future = dt_util.utcnow() + timedelta(seconds=0.6)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()

    assert mock_gateway.send.call_count == 2
    off_cmd = mock_gateway.send.call_args[0][0]
    assert str(off_cmd) == "*1*0*22##"
    assert impulse_gate.is_opening is True

    # 3. Advance time to complete travel (10s)
    future = dt_util.utcnow() + timedelta(seconds=11.0)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()

    assert impulse_gate.is_opening is False
    assert impulse_gate.is_closed is False

    # 4. Pulse close
    mock_gateway.send.reset_mock()
    impulse_gate._last_pulse_time = -9999.0
    assert await impulse_gate._async_pulse(Intent.CLOSE, Effect.MAY_CLOSE) is True

    assert mock_gateway.send.call_count == 1
    on_cmd = mock_gateway.send.call_args[0][0]
    assert str(on_cmd) == "*1*1*22##"
    assert impulse_gate.is_closing is True

    # Auto-off pulse after 0.5s
    future = dt_util.utcnow() + timedelta(seconds=0.6)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()
    assert mock_gateway.send.call_count == 2
    assert str(mock_gateway.send.call_args[0][0]) == "*1*0*22##"

    # Finish closing travel (10s)
    future = dt_util.utcnow() + timedelta(seconds=11.0)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()

    assert impulse_gate.is_closing is False
    assert impulse_gate.is_closed is True


async def test_impulse_cover_access_control_flow(hass: HomeAssistant, impulse_gate):
    """Test access policy integration: human context required and cancellation."""
    # Automated calls without user context are strictly rejected
    with pytest.raises(AccessDenied, match="automations, scripts and integrations may not operate"):
        await impulse_gate.async_open_cover()

    with pytest.raises(AccessDenied, match="automations, scripts and integrations may not operate"):
        await impulse_gate.async_close_cover()

    with pytest.raises(AccessDenied, match="automations, scripts and integrations may not operate"):
        await impulse_gate.async_stop_cover()

    # With authorized user context, creates pending approval request
    user = await hass.auth.async_create_user("Alice")
    hass.states.async_set("person.alice", "home", {"user_id": user.id})
    async_mock_service(hass, "notify", "mobile_app_phone")
    impulse_gate._access.config = AccessConfig(
        allowed_users=(user.id,),
        approvers={user.id: "notify.mobile_app_phone"},
        remote_close=RemoteClose.AT_HOME,
        safety_devices_verified=dt_util.now().date(),
        pin_code="1234",
    )
    impulse_gate._context = Context(user_id=user.id)

    await impulse_gate.async_open_cover()
    assert impulse_gate.extra_state_attributes["access_phase"] == "pending_approval"

    # Cancel request via entity service method
    await impulse_gate.async_cancel_request()
    assert impulse_gate.extra_state_attributes["access_phase"] == "idle"

    # Acknowledge fault when there is no fault raises AccessDenied
    with pytest.raises(AccessDenied, match="there is no fault to acknowledge"):
        await impulse_gate.async_acknowledge_fault()

    await impulse_gate._access.async_shutdown()


async def test_impulse_cover_deadband_lockout(hass: HomeAssistant, impulse_gate, mock_gateway):
    """Test that commands sent within min_cycle_time are dropped."""
    # First pulse succeeds
    assert await impulse_gate._async_pulse(Intent.OPEN, Effect.OPEN) is True
    assert mock_gateway.send.call_count == 1

    # Second pulse 0.5s later (within 2.0s deadband) is dropped
    assert await impulse_gate._async_pulse(Intent.OPEN, Effect.OPEN) is False
    assert mock_gateway.send.call_count == 1


async def test_impulse_cover_stop(hass: HomeAssistant, impulse_gate, mock_gateway):
    """Test stop command cancels transit timer."""
    await impulse_gate._async_pulse(Intent.OPEN, Effect.OPEN)
    assert impulse_gate.is_opening is True

    # Advance time to auto-reset relay pulse
    future = dt_util.utcnow() + timedelta(seconds=0.6)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()

    # Reset last pulse time so stop is not blocked by deadband lockout
    impulse_gate._last_pulse_time = -9999.0
    await impulse_gate._async_pulse(Intent.STOP, Effect.MAY_CLOSE)
    assert impulse_gate.is_opening is False
    assert impulse_gate.is_closing is False


async def test_impulse_cover_state_sensor_binding(hass: HomeAssistant, mock_gateway):
    """Test binding to a physical contact sensor (e.g. Risco door contact)."""
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMEImpulseCover(
            hass=hass,
            name="Garage Door",
            entity_name=None,
            device_id="21",
            who="1",
            where="21",
            interface=None,
            device_class=CoverDeviceClass.GARAGE,
            travel_time=15.0,
            pulse_duration=0.5,
            min_cycle_time=1.0,
            pin_code=None,
            state_sensor="binary_sensor.garage_door_contact",
            manufacturer="BTicino",
            model="Impulse Cover",
            gateway=mock_gateway,
        )
        cover.hass = hass
        cover.entity_id = "cover.garage_door"
        cover.async_write_ha_state = MagicMock()

        try:
            # Set initial sensor state: off (closed)
            hass.states.async_set("binary_sensor.garage_door_contact", STATE_OFF)
            await cover.async_added_to_hass()

            assert cover.assumed_state is False
            assert cover.is_closed is True

            # Pulsing open
            assert await cover._async_pulse(Intent.OPEN, Effect.OPEN) is True
            assert mock_gateway.send.call_count == 1
            assert cover.is_opening is True
            assert cover.is_closed is False

            # Advance 0.6s for auto-off pulse
            future = dt_util.utcnow() + timedelta(seconds=0.6)
            async_fire_time_changed(hass, future)
            await hass.async_block_till_done()
            assert mock_gateway.send.call_count == 2
            mock_gateway.send.reset_mock()

            # Sensor state updates to on (open)
            hass.states.async_set("binary_sensor.garage_door_contact", STATE_ON)
            await hass.async_block_till_done()
            assert cover.is_closed is False
            assert cover.is_opening is False

            # Sensor state updates to off (closed)
            hass.states.async_set("binary_sensor.garage_door_contact", STATE_OFF)
            await hass.async_block_till_done()
            assert cover.is_closed is True
            assert cover.is_opening is False
        finally:
            await cover.async_will_remove_from_hass()


def test_impulse_cover_bus_event_handling(hass: HomeAssistant, impulse_gate):
    """Test handling bus frames from physical wall button presses."""
    # Frame with what=0 (e.g. 5-minute delayed timer expiring) must be ignored
    off_event = OWNEvent.parse("*1*0*22##")
    impulse_gate.handle_event(off_event)
    assert impulse_gate.is_opening is False
    assert impulse_gate.is_closing is False

    # Translation frames are ignored
    translation_event = MagicMock()
    translation_event.is_translation = True
    impulse_gate.handle_event(translation_event)
    assert impulse_gate.is_opening is False

    # External wall press frame *1*1*22## triggers opening when closed
    impulse_gate._attr_is_closed = True
    on_event = OWNEvent.parse("*1*1*22##")
    impulse_gate.handle_event(on_event)
    assert impulse_gate.is_opening is True
    assert impulse_gate.is_closed is False

    # Complete opening travel time
    future = dt_util.utcnow() + timedelta(seconds=11.0)
    async_fire_time_changed(hass, future)
    assert impulse_gate.is_opening is False
    assert impulse_gate.is_closed is False

    # Echo window ignores frames within 1.0s of sending pulse
    impulse_gate._last_pulse_time = 999999999.0
    impulse_gate.handle_event(on_event)
    assert impulse_gate.is_closing is False

    # Reset last pulse time: next press starts closing
    impulse_gate._last_pulse_time = -9999.0
    impulse_gate.handle_event(on_event)
    assert impulse_gate.is_closing is True

    # Complete closing travel time
    future = dt_util.utcnow() + timedelta(seconds=11.0)
    async_fire_time_changed(hass, future)
    assert impulse_gate.is_closing is False
    assert impulse_gate.is_closed is True


async def test_impulse_cover_deadband_rejections_and_cleanup(hass: HomeAssistant, impulse_gate):
    """Test deadband drops for close and stop, and cleanup on removal."""
    # Send open pulse
    assert await impulse_gate._async_pulse(Intent.OPEN, Effect.OPEN) is True
    assert impulse_gate.is_opening is True

    # Closing immediately rejected by deadband
    assert await impulse_gate._async_pulse(Intent.CLOSE, Effect.MAY_CLOSE) is False
    assert impulse_gate.is_opening is True

    # Stopping immediately rejected by deadband
    assert await impulse_gate._async_pulse(Intent.STOP, Effect.MAY_CLOSE) is False
    assert impulse_gate.is_opening is True

    # Call async_will_remove_from_hass with pending timers
    assert impulse_gate._pulse_off_cancel is not None
    assert impulse_gate._travel_timer_cancel is not None
    await impulse_gate.async_will_remove_from_hass()
    assert impulse_gate._pulse_off_cancel is None
    assert impulse_gate._travel_timer_cancel is None

