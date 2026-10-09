"""Tests for MyHOME lock platform (WHO=6 door entry electric strikes)."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.lock import LockEntityFeature
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from OWNd.message import (
    OWNCommand,
    OWNEvent,
)

try:
    from OWNd.message import (
        OWNDoorEntryCommand,
        OWNDoorEntryEvent,
    )
except ImportError:  # pragma: no cover
    from custom_components.myhome.lock import (
        OWNDoorEntryCommand,
        OWNDoorEntryEvent,
    )
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_fire_time_changed_exact,
)

from custom_components.myhome.const import (
    CONF_PLATFORMS,
    CONF_WHERE,
    DOMAIN,
)
from custom_components.myhome.lock import (
    DEFAULT_LOCK_DURATION,
    PLATFORM,
    MyHOMELock,
    async_setup_entry,
    async_unload_entry,
)
from tests.conftest import attach_runtime


@pytest.fixture
def mock_gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:66:66"
    gw.unique_id = "00:03:50:00:66:66"
    gw.log_id = "[Test Lock Gateway]"
    gw.device_registry_id = "mock_gw_dev_id"
    gw.available = True
    gw.availability_signal = "myhome_gateway_availability_000350006666"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


async def test_lock_setup_restores_and_discovers(hass: HomeAssistant, mock_gateway):
    """Test lock platform setup: restoring from registry, configuring from YAML, and bus discovery."""
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {
                    PLATFORM: {
                        "2": {
                            CONF_WHERE: "2",
                            CONF_NAME: "Side Gate Lock",
                        },
                    }
                },
            }
        }
    }

    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "lock_test_entry"

    mock_er = MagicMock()
    reg_1 = MagicMock()
    reg_1.domain = Platform.LOCK
    reg_1.unique_id = f"{mac}-6-1"

    with (
        patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_er),
        patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[reg_1]),
    ):
        added_entities = []

        def fake_add_entities(entities):
            added_entities.extend(entities)

        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, fake_add_entities)

        # Restored (1) + Configured from YAML (2) = 2 locks
        assert len(added_entities) == 2
        assert any(e._where == "1" for e in added_entities)
        assert any(e._where == "2" for e in added_entities)

        # Bus event: lock open on where 3 -> should be discovered
        lock_open_msg = OWNEvent.parse("*6*10*3##")
        assert isinstance(lock_open_msg, (OWNDoorEntryEvent, OWNEvent))
        async_dispatcher_send(hass, f"myhome_message_{mac}", lock_open_msg)
        assert len(added_entities) == 3
        assert any(e._where == "3" for e in added_entities)

        # Incoming call event -> must NOT create a lock entity
        call_msg = OWNEvent.parse("*6*6*4##")
        assert isinstance(call_msg, (OWNDoorEntryEvent, OWNEvent))
        async_dispatcher_send(hass, f"myhome_message_{mac}", call_msg)
        assert len(added_entities) == 3

        # Broadcast lock or invalid address -> must NOT create a lock entity
        broadcast_msg = OWNEvent.parse("*6*10*4100##")
        async_dispatcher_send(hass, f"myhome_message_{mac}", broadcast_msg)
        assert len(added_entities) == 3

        # Unload
        attach_runtime(hass, config_entry)
        assert await async_unload_entry(hass, config_entry) is True


class TestMyHOMELockEntity:
    """Test MyHOMELock entity functionality and timing behavior."""

    @pytest.fixture
    def lock_entity(self, hass: HomeAssistant, mock_gateway):
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            lock = MyHOMELock(
                hass=hass,
                name="Front Door Lock",
                entity_name=None,
                device_id="1",
                who="6",
                where="1",
                interface=None,
                manufacturer="BTicino",
                model="Door Entry Lock",
                gateway=mock_gateway,
            )
            lock.hass = hass
            lock.entity_id = "lock.front_door_lock"
            lock.async_write_ha_state = MagicMock()
            lock.async_schedule_update_ha_state = MagicMock()
            return lock

    def test_lock_attributes(self, lock_entity):
        """Verify lock features and initial state."""
        assert lock_entity.supported_features == LockEntityFeature.OPEN
        assert lock_entity.is_locked is True
        assert lock_entity.extra_state_attributes["where"] == "1"
        assert "interface" not in lock_entity.extra_state_attributes

    async def test_unlock_and_auto_relock(self, hass: HomeAssistant, lock_entity, mock_gateway):
        """Test unlock command, state change, and automatic relock after timeout."""
        await lock_entity.async_unlock()

        # Gateway command should be sent
        mock_gateway.send.assert_called_once()
        cmd = mock_gateway.send.call_args[0][0]
        assert isinstance(cmd, (OWNDoorEntryCommand, OWNCommand))
        assert str(cmd) == "*6*10*1##"

        # Lock is momentarily unlocked
        assert lock_entity.is_locked is False

        # Fast forward time by DEFAULT_LOCK_DURATION + 0.1s
        future = dt_util.utcnow() + timedelta(seconds=DEFAULT_LOCK_DURATION + 0.1)
        async_fire_time_changed(hass, future)
        await hass.async_block_till_done()

        # Lock should be automatically relocked
        assert lock_entity.is_locked is True

    async def test_open_action(self, hass: HomeAssistant, lock_entity, mock_gateway):
        """Test open action releases strike like unlock."""
        await lock_entity.async_open()

        mock_gateway.send.assert_called_once()
        assert lock_entity.is_locked is False

        # Explicit lock call before timeout relocks immediately
        await lock_entity.async_lock()
        assert lock_entity.is_locked is True

    def test_handle_event_lock_open(self, hass: HomeAssistant, lock_entity):
        """Test bus event *6*10*1## triggers momentary unlock."""
        event_msg = OWNEvent.parse("*6*10*1##")
        assert isinstance(event_msg, (OWNDoorEntryEvent, OWNEvent))
        assert getattr(event_msg, "is_lock_open", getattr(event_msg, "_what", None) in (10, 22)) is True

        lock_entity.handle_event(event_msg)
        assert lock_entity.is_locked is False

        # Advance timer to verify relock
        future = dt_util.utcnow() + timedelta(seconds=DEFAULT_LOCK_DURATION + 0.1)
        async_fire_time_changed(hass, future)

        assert lock_entity.is_locked is True

    def test_handle_event_call_ignored_by_lock(self, lock_entity):
        """Call event (*6*6*1##) should not alter lock state."""
        event_msg = OWNEvent.parse("*6*6*1##")
        assert isinstance(event_msg, (OWNDoorEntryEvent, OWNEvent))
        assert getattr(event_msg, "is_call", getattr(event_msg, "_what", None) == 6) is True
        assert getattr(event_msg, "is_lock_open", False) is False

        lock_entity.handle_event(event_msg)
        assert lock_entity.is_locked is True

    async def test_routed_lock_interface(self, hass: HomeAssistant, mock_gateway):
        """Test lock on a secondary bus via F422 interface (#4#01)."""
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            lock = MyHOMELock(
                hass=hass,
                name="Back Gate",
                entity_name=None,
                device_id="5#4#01",
                who="6",
                where="5",
                interface="01",
                manufacturer="BTicino",
                model="Door Entry Lock",
                gateway=mock_gateway,
            )
            lock.hass = hass
            lock.entity_id = "lock.back_gate"
            lock.async_write_ha_state = MagicMock()

            assert lock.extra_state_attributes["where"] == "5"
            assert lock.extra_state_attributes["interface"] == "01"

            await lock.async_unlock()
            cmd = mock_gateway.send.call_args[0][0]
            assert str(cmd) == "*6*10*5#4#01##"
            await lock.async_will_remove_from_hass()


async def test_setup_and_unload_missing_runtime(hass: HomeAssistant):
    """Test setup and unload return True when runtime or platform is not available."""
    mock_entry = MagicMock()
    mock_entry.runtime_data = None
    assert await async_setup_entry(hass, mock_entry, MagicMock()) is True
    assert await async_unload_entry(hass, mock_entry) is True


async def test_impulse_lock_who1_code_and_pulse(hass: HomeAssistant, mock_gateway):
    """Test WHO=1 impulse lock with security code and momentary auto-off."""
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        lock = MyHOMELock(
            hass=hass,
            name="Impulse Gate Lock",
            entity_name=None,
            device_id="25",
            who="1",
            where="25",
            interface=None,
            manufacturer="BTicino",
            model="Impulse Lock",
            gateway=mock_gateway,
            code="1234",
            pulse_duration=0.5,
        )
        lock.hass = hass
        lock.entity_id = "lock.impulse_gate_lock"
        lock.async_write_ha_state = MagicMock()

        assert lock.code_format == r"^\d+$"
        assert lock.is_locked is True

        # Invalid code raises ServiceValidationError
        with pytest.raises(ServiceValidationError, match="Invalid code"):
            await lock.async_unlock(code="9999")

        # Valid code unlocks and pulses relay with WHAT 18 hardware timed pulse
        await lock.async_unlock(code="1234")
        assert mock_gateway.send.call_count == 1
        cmd = mock_gateway.send.call_args[0][0]
        assert str(cmd) == "*1*18*25##"
        assert lock.is_locked is False

        # Advance 0.6s -> auto relock
        future = dt_util.utcnow() + timedelta(seconds=0.6)
        async_fire_time_changed(hass, future)
        await hass.async_block_till_done()
        assert lock.is_locked is True

        # Handle event: *1*1*25## unlocks momentarily
        event_msg = OWNEvent.parse("*1*1*25##")
        lock.handle_event(event_msg)
        assert lock.is_locked is False

        # Advance 0.6s -> auto relock
        future = dt_util.utcnow() + timedelta(seconds=0.6)
        async_fire_time_changed(hass, future)
        await hass.async_block_till_done()
        assert lock.is_locked is True

        # Custom duration uses software timer
        lock._pulse_duration = 1.0
        await lock.async_unlock(code="1234")
        assert lock._pulse_off_unsub is not None
        await lock.async_unlock(code="1234")
        assert lock._pulse_off_unsub is not None

        # Removal while pulse is pending immediately turns relay off (lock.py:345-346)
        mock_gateway.send.reset_mock()
        await lock.async_will_remove_from_hass()
        assert lock._pulse_off_unsub is None
        assert mock_gateway.send.call_count == 1
        assert str(mock_gateway.send.call_args[0][0]) == "*1*0*25##"

        # Handle event with translation flag returns early (lock.py:306)
        trans_msg = MagicMock(is_translation=True)
        lock.handle_event(trans_msg)


async def test_lock_setup_who1_and_bus_discovery_branches(hass: HomeAssistant, mock_gateway):
    """Cover build_who1, accept_who6 bus discovery with is_lock_open, and accept_who1 from registry."""
    mac = mock_gateway.mac
    hass.data.setdefault(DOMAIN, {})[mac] = {
        "entity": mock_gateway,
        CONF_PLATFORMS: {
            PLATFORM: {
                "26": {
                    "who": "1",
                    CONF_WHERE: "26",
                    CONF_NAME: "Side Gate Impulse Lock",
                    "code": "4321",
                    "pulse_duration": 1.0,
                },
            }
        },
    }

    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "lock_who1_entry"
    added = []

    mock_er = MagicMock()
    reg_who1 = MagicMock()
    reg_who1.domain = Platform.LOCK
    reg_who1.unique_id = f"{mac}-1-28"

    with (
        patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_er),
        patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[reg_who1]),
    ):
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)

        # 1. Restored who: 1 from registry (accept_who1 registry branch: lock.py:159)
        # 2. Configured who: 1 from YAML (build_who1: lock.py:120-129)
        assert len(added) == 2
        assert any(e._who == "1" and e._where == "26" for e in added)
        assert any(e._who == "1" and e._where == "28" for e in added)

        # 3. Bus discovery: accept_who6 with is_lock_open message (lock.py:152)
        bus_msg = OWNEvent.parse("*6*10*27##")
        async_dispatcher_send(hass, f"myhome_message_{mac}", bus_msg)
        assert len(added) == 3
        assert any(e._where == "27" for e in added)


async def test_lock_confirmed_write_future_and_off_failure(hass: HomeAssistant, mock_gateway):
    """Test write_fut await paths and exception handling in timed OFF and removal."""
    # 1. WHO 6 unlock with awaitable write_fut

    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        lock_who6 = MyHOMELock(
            hass=hass,
            name="Front Door Lock",
            entity_name=None,
            device_id="1",
            who="6",
            where="1",
            interface=None,
            manufacturer="BTicino",
            model="Door Entry Lock",
            gateway=mock_gateway,
        )
        lock_who6.hass = hass
        lock_who6.entity_id = "lock.front_door_lock"
        lock_who6.async_write_ha_state = MagicMock()
        lock_who6.async_schedule_update_ha_state = MagicMock()

    fut_who6 = asyncio.Future()
    fut_who6.set_result(0.05)
    mock_gateway.send.return_value = fut_who6
    await lock_who6.async_unlock()
    assert fut_who6.done()
    lock_who6._cancel_auto_relock()

    # 2. WHO 1 lock with WHAT 18 and awaitable write_fut
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        lock_who1 = MyHOMELock(
            hass=hass,
            name="Impulse Lock",
            entity_name=None,
            device_id="1-25",
            who="1",
            where="25",
            interface=None,
            pulse_duration=0.5,
            code=None,
            manufacturer="BTicino",
            model="Relay Lock",
            gateway=mock_gateway,
        )
        lock_who1.hass = hass
        lock_who1.entity_id = "lock.impulse_lock"
        lock_who1.async_write_ha_state = MagicMock()

    fut_who1 = asyncio.Future()
    fut_who1.set_result(0.05)
    mock_gateway.send.return_value = fut_who1
    await lock_who1.async_unlock()
    assert fut_who1.done()
    lock_who1._cancel_auto_relock()

    # 3. WHO 1 custom duration with awaitable write_fut and timed OFF
    lock_who1._pulse_duration = 1.0
    fut_on = asyncio.Future()
    fut_on.set_result(0.05)
    mock_gateway.send.side_effect = None
    mock_gateway.send.return_value = fut_on
    await lock_who1.async_unlock()
    assert fut_on.done()
    assert lock_who1._pulse_off_unsub is not None

    # Advance timer to trigger _send_off
    fut_off = asyncio.Future()
    fut_off.set_result(0.05)
    mock_gateway.send.return_value = fut_off
    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=1.2))
    await hass.async_block_till_done()
    assert fut_off.done()
    lock_who1._cancel_auto_relock()

    # 4. WHO 1 custom duration with OFF send failure
    lock_who1._pulse_duration = 1.0
    mock_gateway.send.return_value = None
    await lock_who1.async_unlock()
    mock_gateway.send.side_effect = RuntimeError("OFF send error")
    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=1.2))
    await hass.async_block_till_done()
    assert lock_who1._pulse_off_unsub is None
    lock_who1._cancel_auto_relock()

    # 5. Removal fail-safe with awaitable write_fut and exception handling
    fut_rem = asyncio.Future()
    fut_rem.set_result(0.05)
    mock_gateway.send.side_effect = None
    mock_gateway.send.return_value = fut_rem
    lock_who1._pulse_off_unsub = MagicMock()
    await lock_who1.async_will_remove_from_hass()
    assert fut_rem.done()

    mock_gateway.send.side_effect = RuntimeError("Removal send error")
    lock_who1._pulse_off_unsub = MagicMock()
    await lock_who1.async_will_remove_from_hass()
    lock_who1._cancel_auto_relock()




