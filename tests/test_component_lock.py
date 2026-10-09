"""Tests for MyHOME lock platform (WHO=6 door entry electric strikes)."""
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
from pytest_homeassistant_custom_component.common import async_fire_time_changed

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

        # Valid code unlocks and pulses relay
        await lock.async_unlock(code="1234")
        assert mock_gateway.send.call_count == 1
        cmd = mock_gateway.send.call_args[0][0]
        assert str(cmd) == "*1*1*25##"
        assert lock.is_locked is False

        # Advance 0.6s -> auto-off pulse and relock
        future = dt_util.utcnow() + timedelta(seconds=0.6)
        async_fire_time_changed(hass, future)
        await hass.async_block_till_done()

        assert mock_gateway.send.call_count == 2
        off_cmd = mock_gateway.send.call_args[0][0]
        assert str(off_cmd) == "*1*0*25##"
        assert lock.is_locked is True

        # Handle event: *1*1*25## unlocks momentarily
        event_msg = OWNEvent.parse("*1*1*25##")
        lock.handle_event(event_msg)
        assert lock.is_locked is False

        await lock.async_will_remove_from_hass()

