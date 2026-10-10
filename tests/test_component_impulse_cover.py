"""Tests for MyHOME impulse cover platform (gates, garage doors, impulse relays WHO=1)."""
import asyncio
import time
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
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_fire_time_changed_exact,
    async_mock_service,
)

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

PULSE_FRAME = "*1*18*22##" if hasattr(OWNLightingCommand, "switch_on_timed") else "*1*1*22##"


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

    # Hardware-timed WHAT 18 command sent immediately (actuator shuts off after 0.5s);
    # OWNd releases without the builder send a plain ON and switch it off in software.
    assert mock_gateway.send.call_count == 1
    on_cmd = mock_gateway.send.call_args[0][0]
    assert isinstance(on_cmd, OWNLightingCommand)
    assert str(on_cmd) == PULSE_FRAME
    assert impulse_gate.is_opening is True
    assert impulse_gate.is_closed is False

    # 2. Advance time to complete travel (10s)
    future = dt_util.utcnow() + timedelta(seconds=11.0)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()

    assert impulse_gate.is_opening is False
    assert impulse_gate.is_closed is False

    # 3. Pulse close
    mock_gateway.send.reset_mock()
    impulse_gate._last_pulse_time = -9999.0
    assert await impulse_gate._async_pulse(Intent.CLOSE, Effect.MAY_CLOSE) is True

    assert mock_gateway.send.call_count == 1
    on_cmd = mock_gateway.send.call_args[0][0]
    assert str(on_cmd) == PULSE_FRAME
    assert impulse_gate.is_closing is True

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

            # Reset mock for sensor updates
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


async def test_impulse_cover_deadband_rejections_and_cleanup(hass: HomeAssistant, impulse_gate, mock_gateway):
    """Test deadband drops for close and stop, and cleanup on removal."""
    # Custom duration (1.0s) uses software off timer and removal fail-safe
    impulse_gate._pulse_duration = 1.0

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
    mock_gateway.send.reset_mock()
    await impulse_gate.async_will_remove_from_hass()
    assert impulse_gate._pulse_off_cancel is None
    assert impulse_gate._travel_timer_cancel is None
    # Removal while software pulse is active sends OFF to avoid stuck relay
    assert mock_gateway.send.call_count == 1
    assert str(mock_gateway.send.call_args[0][0]) == "*1*0*22##"


async def test_impulse_cover_additional_branches(hass: HomeAssistant, mock_gateway, impulse_gate):
    """Cover interface attribute, unknown contact state, duplicate pulse-off, and external bus stop."""
    # 1. Interface attribute on cover with interface (cover.py:1349)
    cover_with_iface = MyHOMEImpulseCover(
        hass=hass,
        name="Side Gate",
        entity_name="Side Gate",
        device_id="1-22-01",
        who="1",
        where="22",
        interface="01",
        device_class=CoverDeviceClass.GATE,
        travel_time=10.0,
        pulse_duration=0.5,
        min_cycle_time=2.0,
        pin_code=None,
        state_sensor=None,
        manufacturer="BTicino",
        model="Gate",
        gateway=mock_gateway,
    )
    assert cover_with_iface.extra_state_attributes.get("interface") == "01"

    # 2. Unknown contact sensor state -> None (cover.py:1391)
    impulse_gate._handle_sensor_update("unknown")
    assert impulse_gate.is_closed is None

    # 3. Duplicate impulse cancels pending pulse-off timer (cover.py:1417-1418)
    impulse_gate._pulse_duration = 1.0
    impulse_gate._min_cycle_time = 0.0
    await impulse_gate._async_send_impulse()
    assert impulse_gate._pulse_off_cancel is not None
    await impulse_gate._async_send_impulse()
    assert impulse_gate._pulse_off_cancel is not None

    # 4. Bus event outside echo window while access has pending request cancels access (cover.py:1501)
    user = await hass.auth.async_create_user("Bob")
    hass.states.async_set("person.bob", "home", {"user_id": user.id})
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

    # Advance time beyond echo window
    impulse_gate._last_pulse_time = time.monotonic() - 5.0
    msg = OWNEvent.parse("*1*1*22##")
    impulse_gate.handle_event(msg)
    assert impulse_gate.extra_state_attributes["access_phase"] == "idle"

    # 5. External pulse while opening stops travel timer on cover without sensor (cover.py:1520-1521)
    blind_cover = MyHOMEImpulseCover(
        hass=hass,
        name="Sensorless Gate",
        entity_name="Sensorless Gate",
        device_id="1-23",
        who="1",
        where="23",
        interface=None,
        device_class=CoverDeviceClass.GATE,
        travel_time=10.0,
        pulse_duration=0.5,
        min_cycle_time=0.0,
        pin_code=None,
        state_sensor=None,
        manufacturer="BTicino",
        model="Gate",
        gateway=mock_gateway,
    )
    blind_cover._attr_is_closed = True
    blind_cover._last_pulse_time = time.monotonic() - 5.0
    msg = OWNEvent.parse("*1*1*23##")
    blind_cover.handle_event(msg)
    assert blind_cover.is_opening is True

    # Second external pulse while opening stops it
    blind_cover._last_pulse_time = time.monotonic() - 5.0
    blind_cover.handle_event(msg)
    assert blind_cover.is_opening is False
    assert blind_cover._travel_timer_cancel is None


async def test_impulse_cover_build_invalid_device_class(hass: HomeAssistant, mock_gateway):
    """Cover fallback to CoverDeviceClass.GATE when invalid device_class provided (cover.py:187-188)."""
    from custom_components.myhome.cover import async_setup_entry
    from tests.conftest import attach_runtime

    mac = mock_gateway.mac
    hass.data.setdefault(DOMAIN, {})[mac] = {
        "entity": mock_gateway,
        CONF_PLATFORMS: {
            "cover": {
                "24": {
                    "who": "1",
                    "where": "24",
                    "device_class": "completely_invalid_class",
                    "name": "Invalid Class Gate",
                }
            }
        },
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "test_entry_invalid_class"
    added = []

    attach_runtime(hass, config_entry)
    await async_setup_entry(hass, config_entry, added.extend)
    assert len(added) == 1
    assert added[0].device_class == CoverDeviceClass.GATE


async def test_impulse_cover_gateway_write_failure(hass: HomeAssistant, impulse_gate, mock_gateway):
    """Test that a gateway write failure returns False and does not crash or claim executed."""
    mock_gateway.send.side_effect = RuntimeError("Socket disconnected")
    assert await impulse_gate._async_send_impulse() is False


async def test_impulse_cover_custom_pulse_duration_cycle(hass: HomeAssistant, impulse_gate, mock_gateway):
    """Test custom pulse duration (e.g. 1.5s) uses confirmed ON write and timed OFF."""
    impulse_gate._pulse_duration = 1.5
    mock_gateway.send.reset_mock()

    assert await impulse_gate._async_pulse(Intent.OPEN, Effect.OPEN) is True
    assert mock_gateway.send.call_count == 1
    assert str(mock_gateway.send.call_args[0][0]) == "*1*1*22##"

    # Before 1.5s, no OFF sent yet
    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=0.8))
    await hass.async_block_till_done()
    assert mock_gateway.send.call_count == 1

    # After 1.5s, OFF sent
    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=1.6))
    await hass.async_block_till_done()
    assert mock_gateway.send.call_count == 2
    assert str(mock_gateway.send.call_args[0][0]) == "*1*0*22##"


async def test_impulse_cover_confirmed_write_future_and_off_failure(hass: HomeAssistant, impulse_gate, mock_gateway):
    """Test write_fut await paths and exception handling in timed OFF and removal."""
    # 1. WHAT 18 with awaitable write_fut

    fut = asyncio.Future()
    fut.set_result(0.05)
    mock_gateway.send.return_value = fut
    impulse_gate._pulse_duration = 0.5
    impulse_gate._last_pulse_time = 0.0
    assert await impulse_gate._async_send_impulse() is True
    assert fut.done()

    # 2. Custom duration with awaitable write_fut and OFF failure handling
    fut_on = asyncio.Future()
    fut_on.set_result(0.05)
    mock_gateway.send.side_effect = None
    mock_gateway.send.return_value = fut_on
    impulse_gate._pulse_duration = 1.0
    impulse_gate._last_pulse_time = 0.0
    assert await impulse_gate._async_send_impulse() is True

    # When OFF is scheduled, test success with awaitable future
    fut_off_timed = asyncio.Future()
    fut_off_timed.set_result(0.05)
    mock_gateway.send.return_value = fut_off_timed
    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=1.2))
    await hass.async_block_till_done()
    assert fut_off_timed.done()
    assert impulse_gate._pulse_off_cancel is None

    # Test error handling in _send_off
    impulse_gate._last_pulse_time = 0.0
    mock_gateway.send.return_value = fut_on
    assert await impulse_gate._async_send_impulse() is True
    mock_gateway.send.side_effect = RuntimeError("OFF send error")
    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=1.2))
    await hass.async_block_till_done()
    assert impulse_gate._pulse_off_cancel is None


    # 3. Removal fail-safe with awaitable write_fut and exception handling
    fut_off = asyncio.Future()
    fut_off.set_result(0.05)
    mock_gateway.send.side_effect = None
    mock_gateway.send.return_value = fut_off
    impulse_gate._pulse_off_cancel = MagicMock()
    await impulse_gate.async_will_remove_from_hass()
    assert fut_off.done()

    mock_gateway.send.side_effect = RuntimeError("Removal send error")
    impulse_gate._pulse_off_cancel = MagicMock()
    await impulse_gate.async_will_remove_from_hass()






async def test_impulse_cover_pulse_falls_back_without_timed_on(hass: HomeAssistant, impulse_gate, mock_gateway, monkeypatch):
    """Released OWNd 2.0.0b10 has no OWNLightingCommand.switch_on_timed: ON, then OFF after the pulse."""
    monkeypatch.delattr(OWNLightingCommand, "switch_on_timed", raising=False)

    assert await impulse_gate._async_send_impulse() is True
    assert [str(call.args[0]) for call in mock_gateway.send.call_args_list] == ["*1*1*22##"]

    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=0.6))
    await hass.async_block_till_done()
    assert [str(call.args[0]) for call in mock_gateway.send.call_args_list] == ["*1*1*22##", "*1*0*22##"]


async def test_impulse_cover_failed_send_does_not_start_deadband(hass: HomeAssistant, impulse_gate, mock_gateway):
    """A pulse that never reached the bus must not lock the user out for min_cycle_time."""
    mock_gateway.send.side_effect = RuntimeError("Socket disconnected")
    assert await impulse_gate._async_send_impulse() is False
    assert impulse_gate._last_pulse_time == -1e9

    mock_gateway.send.side_effect = None
    assert await impulse_gate._async_send_impulse() is True
    assert impulse_gate._last_pulse_time > 0

    # The confirmed pulse does start it
    assert await impulse_gate._async_send_impulse() is False


async def test_impulse_cover_failed_awaited_write_does_not_start_deadband(hass: HomeAssistant, impulse_gate, mock_gateway):
    """The write future failing (timeout) counts as a failed send, too."""
    fut = asyncio.get_running_loop().create_future()
    fut.set_exception(TimeoutError("no ACK"))
    mock_gateway.send.return_value = fut
    assert await impulse_gate._async_send_impulse() is False
    assert impulse_gate._last_pulse_time == -1e9


async def test_impulse_cover_in_flight_pulse_blocks_second_request_and_echo(hass: HomeAssistant, impulse_gate, mock_gateway):
    """While the write is pending a second request is dropped and the echo is not a wall button."""
    release = asyncio.Event()

    async def slow_send(cmd, *args, **kwargs):
        await release.wait()

    mock_gateway.send.side_effect = slow_send
    first = asyncio.create_task(impulse_gate._async_send_impulse())
    await asyncio.sleep(0)
    assert impulse_gate._pulse_in_flight is True

    assert await impulse_gate._async_send_impulse() is False

    impulse_gate._attr_is_closed = True
    impulse_gate.handle_event(OWNEvent.parse("*1*1*22##"))
    assert impulse_gate.is_opening is False

    release.set()
    assert await first is True
    assert impulse_gate._pulse_in_flight is False


def test_impulse_cover_wall_button_log_says_the_gate_already_moved(impulse_gate, caplog):
    """A wall button press starts the gate; only Home Assistant's own request is dropped."""
    impulse_gate._access.cancel = MagicMock(return_value=True)
    with caplog.at_level("INFO"):
        impulse_gate.handle_event(OWNEvent.parse("*1*1*22##"))
    impulse_gate._access.cancel.assert_called_once()
    assert "already been actuated" in caplog.text
