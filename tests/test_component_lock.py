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
    from custom_components.myhome.gateway_events import OWNDoorEntryEvent
    from custom_components.myhome.lock import OWNDoorEntryCommand
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
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


async def test_lock_setup_restores_and_ignores_bus_traffic(hass: HomeAssistant, mock_gateway):
    """Locks come from myhome.yaml (restored from the registry when still configured); bus traffic and removed entries never create one."""
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {
                    PLATFORM: {
                        "1": {
                            CONF_WHERE: "1",
                            CONF_NAME: "Front Door Lock",
                            "code": "1234",
                        },
                        "2": {
                            CONF_WHERE: "2",
                            CONF_NAME: "Side Gate Lock",
                        },
                        "0": {
                            CONF_WHERE: "0",
                            CONF_NAME: "General Lock",
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
    reg_removed = MagicMock()  # removed from myhome.yaml: must not resurrect without its code (H-5)
    reg_removed.domain = Platform.LOCK
    reg_removed.unique_id = f"{mac}-6-9"
    reg_light = MagicMock()  # another subsystem's entry is never restored as a lock
    reg_light.domain = Platform.LOCK
    reg_light.unique_id = f"{mac}-1-7"

    with (
        patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_er),
        patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[reg_1, reg_removed, reg_light]),
    ):
        added_entities = []

        def fake_add_entities(entities):
            added_entities.extend(entities)

        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, fake_add_entities)

        # Restored (1) + Configured from YAML (2) = 2 locks; removed (9) and general (0) are rejected
        assert len(added_entities) == 2
        assert any(e._where == "1" and e._code == "1234" for e in added_entities)
        assert any(e._where == "2" for e in added_entities)
        assert not any(e._where == "9" for e in added_entities)

        # A lock release seen on the bus must NOT create a lock: the entity would have
        # no code, so anyone with dashboard access could open the door.
        lock_open_msg = OWNEvent.parse("*6*10*3##")
        assert isinstance(lock_open_msg, (OWNDoorEntryEvent, OWNEvent))
        async_dispatcher_send(hass, f"myhome_message_{mac}", lock_open_msg)
        await hass.async_block_till_done()
        assert len(added_entities) == 2
        assert not any(e._where == "3" for e in added_entities)

        # Neither do calls nor the broadcast address
        for frame in ("*6*6*4##", "*6*10*4100##", "*6*22*5##"):
            async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse(frame))
        await hass.async_block_till_done()
        assert len(added_entities) == 2

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

    def test_handle_event_translation_ignored_by_lock(self, lock_entity):
        """A translated frame is not a release of this strike."""
        lock_entity.handle_event(MagicMock(who=6, _what=10, is_lock_open=True, is_translation=True))
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


async def test_lock_code_is_required_and_checked_in_constant_time(hass: HomeAssistant, mock_gateway):
    """A lock with a code refuses a missing or wrong code and sends nothing."""
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        lock = MyHOMELock(
            hass=hass,
            name="Front Door Lock",
            entity_name=None,
            device_id="4001#2",
            who="6",
            where="4001#2",
            interface=None,
            manufacturer="BTicino",
            model="Door Entry Lock",
            gateway=mock_gateway,
            code="1234",
        )
        lock.hass = hass
        lock.entity_id = "lock.front_door_lock"
        lock.async_write_ha_state = MagicMock()

        assert lock.code_format == r"^\d+$"
        for bad in ({}, {"code": "9999"}):
            with pytest.raises(ServiceValidationError, match="Invalid code"):
                await lock.async_unlock(**bad)
        mock_gateway.send.assert_not_called()

        await lock.async_unlock(code="1234")
        assert str(mock_gateway.send.call_args[0][0]) == "*6*10*4001#2##"
        assert lock.is_locked is False
        lock._cancel_auto_relock()


async def test_lock_confirmed_write_future(hass: HomeAssistant, mock_gateway):
    """The unlock waits for the gateway's write confirmation."""
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

    fut = asyncio.Future()
    fut.set_result(0.05)
    mock_gateway.send.return_value = fut
    await lock.async_unlock()
    assert fut.done()
    lock._cancel_auto_relock()
    await lock.async_will_remove_from_hass()


async def test_lock_receives_live_bus_frames_and_isolates_sub_bus(hass: HomeAssistant, mock_gateway):
    """Configured locks receive live WHO=6 bus frames (C-1) without auto-discovering unconfigured locks or colliding across F422 (H-2)."""
    mac = mock_gateway.mac
    hass.data.setdefault("myhome", {})[mac] = {
        "entity": mock_gateway,
        "platforms": {
            "lock": {
                "6-1": {"who": "6", "where": "1", "name": "Local Lock", "manufacturer": None, "model": None},
                "6-5#4#01": {"who": "6", "where": "5", "interface": "01", "name": "Sub-bus Lock"},
                "2-9": {"who": "2", "where": "9", "name": "Wrong WHO Lock"},
            }
        },
    }
    entry = MagicMock()
    entry.data = {"mac": mac}
    entry.entry_id = "lock_bus_entry"
    attach_runtime(hass, entry)

    added: list[MyHOMELock] = []
    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.discovery.er.async_get"),
    ):
        await async_setup_entry(hass, entry, added.extend)

    assert len(added) == 2
    local_lock = next(e for e in added if e._where == "1")
    sub_lock = next(e for e in added if e._where == "5")
    assert local_lock._manufacturer == "BTicino"
    assert local_lock._model == "Door Entry Lock"

    for lck in added:
        lck.hass = hass
        lck.async_write_ha_state = MagicMock()

    # Unconfigured bus frame *6*10*2## must NOT auto-discover a new lock
    async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse("*6*10*2##"))
    assert len(added) == 2

    # Local frame *6*10*5## must NOT unlock the F422 sub-bus lock at 5#4#01
    async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse("*6*10*5##"))
    assert sub_lock.is_locked is True

    # Live bus frame *6*10*1## unlocks local_lock via PlatformDiscovery listener
    async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse("*6*10*1##"))
    assert local_lock.is_locked is False

    for lck in added:
        lck._cancel_auto_relock()
        await lck.async_will_remove_from_hass()


def test_gateway_door_entry_support_and_label_helpers():
    """Verify _gateway_supports_door_entry, _effective_send_gateway, and _gateway_model_label across fallbacks and delegation."""
    from custom_components.myhome.lock import (
        _effective_send_gateway,
        _gateway_model_label,
        _gateway_supports_door_entry,
        _gateway_supports_who6,
    )

    # Unsupported primary without delegation -> False
    mhs1 = MagicMock(spec=["model", "delegated_away_whos", "profile_supports_who"])
    mhs1.model = "MyHomeServer1"
    mhs1.delegated_away_whos = set()
    mhs1.profile_supports_who.return_value = False
    assert _gateway_supports_door_entry(mhs1) is False
    assert _gateway_supports_who6(mhs1) is False
    assert _gateway_model_label(_effective_send_gateway(mhs1)) == "MyHomeServer1"

    # Delegated WHO 6 to connected F452V -> True, and effective send gateway resolves to secondary F452V
    f452v = MagicMock(spec=["model", "is_connected", "profile_supports_who"])
    f452v.model = "F452V"
    f452v.is_connected = True
    f452v.profile_supports_who.return_value = True
    mhs1_delegated = MagicMock(
        spec=[
            "model",
            "is_connected",
            "delegated_away_whos",
            "_get_secondary_for_who",
            "_get_standby_gateway",
            "profile_supports_who",
        ]
    )
    mhs1_delegated.model = "MyHomeServer1"
    mhs1_delegated.is_connected = True
    mhs1_delegated.delegated_away_whos = {6, 8}
    mhs1_delegated._get_secondary_for_who.return_value = f452v
    mhs1_delegated._get_standby_gateway.return_value = None
    mhs1_delegated.profile_supports_who.return_value = False
    assert _gateway_supports_door_entry(mhs1_delegated) is True
    assert _effective_send_gateway(mhs1_delegated) is f452v
    assert _gateway_model_label(_effective_send_gateway(mhs1_delegated)) == "F452V"

    # Secondary temporarily disconnected -> send() fell back to primary, so effective gateway is MyHomeServer1
    f452v.is_connected = False
    assert _gateway_supports_door_entry(mhs1_delegated) is True
    assert _effective_send_gateway(mhs1_delegated) is mhs1_delegated
    assert _gateway_model_label(_effective_send_gateway(mhs1_delegated)) == "MyHomeServer1"

    # Both primary and secondary disconnected, connected WHO=6 warm standby (F453AV) carries send()
    f453av = MagicMock(spec=["model", "is_connected", "profile_supports_who"])
    f453av.model = "F453AV"
    f453av.is_connected = True
    f453av.profile_supports_who.return_value = True
    mhs1_delegated.is_connected = False
    mhs1_delegated._get_standby_gateway.return_value = f453av
    assert _effective_send_gateway(mhs1_delegated) is f453av
    assert _gateway_model_label(_effective_send_gateway(mhs1_delegated)) == "F453AV"

    # Warm standby that lacks WHO=6 (F455) is skipped by _failover_target -> falls back to primary
    f453av.profile_supports_who.return_value = False
    assert _effective_send_gateway(mhs1_delegated) is mhs1_delegated

    # Delegated WHO 6 when secondary is not yet loaded -> falls back to primary's label
    mhs1_delegated.is_connected = True
    mhs1_delegated._get_secondary_for_who.return_value = None
    assert _gateway_model_label(_effective_send_gateway(mhs1_delegated)) == "MyHomeServer1"

    # Legacy stub with only _profile_supports_who, raising vs returning bool
    legacy = MagicMock(spec=["name", "_profile_supports_who"])
    legacy.name = "  F455 Gateway  "
    legacy._profile_supports_who.return_value = False
    assert _gateway_supports_door_entry(legacy) is False
    assert _gateway_model_label(legacy) == "F455 Gateway"

    legacy._profile_supports_who.side_effect = RuntimeError("broken profile")
    assert _gateway_supports_door_entry(legacy) is True

    # Bare object without profile helpers or string labels
    bare = object()
    assert _gateway_supports_door_entry(bare) is True
    assert _gateway_model_label(bare) == "MyHOME gateway"


async def test_lock_setup_warns_only_on_unsupported_undelegated_gateway(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
):
    """Startup logs a clear warning when WHO=6 locks are configured on an automation-only gateway unless WHO 6 is delegated."""
    mac = "00:03:50:11:22:33"
    gw = MagicMock()
    gw.mac = mac
    gw.unique_id = mac
    gw.log_id = "[MyHomeServer1 192.168.1.10]"
    gw.model = "MyHomeServer1"
    gw.device_registry_id = "gw_dev"
    gw.available = True
    gw.availability_signal = f"myhome_{mac}_availability"
    gw.delegated_away_whos = set()
    gw.profile_supports_who.return_value = False

    def _build_entry(entry_id: str, locks_cfg: dict) -> MagicMock:
        hass.data[DOMAIN] = {
            mac: {
                "entity": gw,
                CONF_PLATFORMS: {PLATFORM: dict(locks_cfg)},
            }
        }
        entry = MagicMock()
        entry.data = {"mac": mac}
        entry.entry_id = entry_id
        attach_runtime(hass, entry)
        return entry

    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.discovery.er.async_get"),
    ):
        # 1. Unsupported gateway with configured locks -> logs warning naming addresses and workarounds
        caplog.clear()
        entry1 = _build_entry(
            "mhs1_warn",
            {
                "6-4000": {"who": "6", "where": "4000", "name": "Gate 1"},
                "6-5#4#01": {"who": "6", "where": "5", "interface": "01", "name": "Gate 2"},
            },
        )
        added: list[MyHOMELock] = []
        with caplog.at_level("WARNING"):
            await async_setup_entry(hass, entry1, added.extend)
        assert len(added) == 2
        warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "4000, 5#4#01" in msg
            and "MyHomeServer1" in msg
            and "bt_vct" in msg
            and "F454 and F459 route only WHO 8" in msg
            for msg in warnings
        )

        # 2. Unsupported gateway with NO configured locks -> no warning
        caplog.clear()
        entry2 = _build_entry("mhs1_empty", {})
        with caplog.at_level("WARNING"):
            await async_setup_entry(hass, entry2, [].extend)
        assert not [r for r in caplog.records if r.levelname == "WARNING"]

        # 3. Same MyHomeServer1 with WHO 6 delegated to a secondary F452V -> no warning
        caplog.clear()
        gw._who6_locks_unsupported_warned = False
        f452v_sec = MagicMock(spec=["profile_supports_who"])
        f452v_sec.profile_supports_who.return_value = True
        gw._get_secondary_for_who.return_value = f452v_sec
        gw.delegated_away_whos = {6, 8}
        entry3 = _build_entry(
            "mhs1_delegated",
            {"6-4000": {"who": "6", "where": "4000", "name": "Gate 1"}},
        )
        with caplog.at_level("WARNING"):
            await async_setup_entry(hass, entry3, [].extend)
        assert not [r for r in caplog.records if r.levelname == "WARNING"]
        assert gw._who6_locks_unsupported_warned is False


async def test_lock_unlock_raises_homeassistant_error_on_nack_timeout_and_failure(
    hass: HomeAssistant,
):
    """Verify async_unlock translates NACKs, timeouts, and transport errors into HomeAssistantError while preserving external task cancellation."""
    from homeassistant.exceptions import HomeAssistantError

    gw = MagicMock()
    gw.mac = "00:03:50:99:88:77"
    gw.unique_id = gw.mac
    gw.log_id = "[MyHomeServer1]"
    gw.model = "MyHomeServer1"
    gw.is_connected = True
    gw.delegated_away_whos = set()
    gw._get_secondary_for_who.return_value = None
    gw._get_standby_gateway.return_value = None
    gw.profile_supports_who.return_value = False
    gw.send = AsyncMock()

    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        lock = MyHOMELock(
            hass=hass,
            name="Gate 1",
            entity_name=None,
            device_id="4000",
            who="6",
            where="4000",
            interface=None,
            manufacturer="BTicino",
            model="Door Entry Lock",
            gateway=gw,
        )
        lock.hass = hass
        lock.entity_id = "lock.gate_1"
        lock.async_write_ha_state = MagicMock()

    # 1. Gateway NACK (*#*0##) cancels write_fut mid-flight on unsupported MyHomeServer1
    fut_nack: asyncio.Future[float] = hass.loop.create_future()
    hass.loop.call_soon(fut_nack.cancel)
    gw.send.return_value = fut_nack

    with pytest.raises(HomeAssistantError) as exc_info:
        await lock.async_unlock()
    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == "lock_gateway_who6_unsupported"
    assert exc_info.value.translation_placeholders == {
        "name": "Gate 1",
        "gateway": "MyHomeServer1",
        "frame": "*6*10*4000##",
    }
    assert lock.is_locked is True
    assert lock._relock_unsub is None
    lock.async_write_ha_state.assert_not_called()

    # 1b. Delegated secondary (F452V) temporarily disconnected -> MyHomeServer1 NACKs locally and is named in error
    f452v_down = MagicMock()
    f452v_down.model = "F452V"
    f452v_down.is_connected = False
    f452v_down.profile_supports_who.return_value = True
    gw.delegated_away_whos = {6, 8}
    gw._get_secondary_for_who.return_value = f452v_down
    fut_local_nack: asyncio.Future[float] = hass.loop.create_future()
    fut_local_nack.cancel()
    gw.send.return_value = fut_local_nack
    with pytest.raises(HomeAssistantError) as exc_info:
        await lock.async_unlock()
    assert exc_info.value.translation_key == "lock_gateway_who6_unsupported"
    assert exc_info.value.translation_placeholders["gateway"] == "MyHomeServer1"
    gw.delegated_away_whos = set()
    gw._get_secondary_for_who.return_value = None

    # 2. Gateway NACK / drop on a WHO=6-capable gateway (F452V) raises lock_command_rejected
    gw.model = "F452V"
    gw.profile_supports_who.return_value = True
    fut_rejected: asyncio.Future[float] = hass.loop.create_future()
    fut_rejected.cancel()
    gw.send.return_value = fut_rejected

    with pytest.raises(HomeAssistantError) as exc_info:
        await lock.async_open()
    assert exc_info.value.translation_key == "lock_command_rejected"
    assert exc_info.value.translation_placeholders == {
        "name": "Gate 1",
        "gateway": "F452V",
        "frame": "*6*10*4000##",
    }
    assert lock.is_locked is True
    assert lock._relock_unsub is None

    # 3. External task cancellation while write_fut is still pending re-raises CancelledError
    fut_pending: asyncio.Future[float] = hass.loop.create_future()
    gw.send.return_value = fut_pending
    unlock_task = hass.async_create_task(lock.async_unlock())
    await asyncio.sleep(0)
    unlock_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await unlock_task
    assert not fut_pending.cancelled()
    assert lock.is_locked is True

    # 3b. Simultaneous shutdown race: both unlock_task AND write_fut cancelled in the same turn -> re-raises CancelledError
    fut_race: asyncio.Future[float] = hass.loop.create_future()
    gw.send.return_value = fut_race
    race_task = hass.async_create_task(lock.async_unlock())
    await asyncio.sleep(0)
    race_task.cancel()
    fut_race.cancel()
    with pytest.raises(asyncio.CancelledError):
        await race_task
    assert lock.is_locked is True

    # 4. Non-Future awaitable raising CancelledError internally vs external task cancellation
    async def _cancelled_coro() -> None:
        raise asyncio.CancelledError

    gw.send.return_value = _cancelled_coro()
    with pytest.raises(HomeAssistantError) as exc_info:
        await lock.async_unlock()
    assert exc_info.value.translation_key == "lock_command_rejected"
    assert lock.is_locked is True

    gate_evt = asyncio.Event()

    async def _hanging_coro() -> None:
        await gate_evt.wait()

    gw.send.return_value = _hanging_coro()
    coro_task = hass.async_create_task(lock.async_unlock())
    await asyncio.sleep(0)
    coro_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await coro_task
    assert lock.is_locked is True

    # 5. Delivery timeout raises lock_command_timeout and withdraws the queued frame (the sending
    #    worker skips a cancelled delivery future) so the strike cannot open after the error
    fut_slow: asyncio.Future[float] = hass.loop.create_future()
    gw.send.return_value = fut_slow
    with (
        patch("custom_components.myhome.lock.LOCK_WRITE_TIMEOUT", 0.01),
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await lock.async_unlock()
    assert exc_info.value.translation_key == "lock_command_timeout"
    assert fut_slow.cancelled()
    assert lock.is_locked is True
    assert lock._relock_unsub is None
    await asyncio.sleep(0)

    # 5b. Delivery timeout on non-Future coroutine awaitable
    async def _slow_coro() -> None:
        await gate_evt.wait()

    coro_slow = _slow_coro()
    gw.send.return_value = coro_slow
    with (
        patch("custom_components.myhome.lock.LOCK_WRITE_TIMEOUT", 0.01),
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await lock.async_unlock()
    assert exc_info.value.translation_key == "lock_command_timeout"

    # 6. write_fut resolving with HomeAssistantError vs generic Exception
    fut_ha_err: asyncio.Future[float] = hass.loop.create_future()
    fut_ha_err.set_exception(HomeAssistantError("upstream ha error"))
    gw.send.return_value = fut_ha_err
    with pytest.raises(HomeAssistantError, match="upstream ha error"):
        await lock.async_unlock()
    assert lock.is_locked is True

    fut_runtime_err: asyncio.Future[float] = hass.loop.create_future()
    fut_runtime_err.set_exception(RuntimeError("worker crashed"))
    gw.send.return_value = fut_runtime_err
    with pytest.raises(HomeAssistantError) as exc_info:
        await lock.async_unlock()
    assert exc_info.value.translation_key == "lock_command_failed"
    assert exc_info.value.translation_placeholders == {
        "name": "Gate 1",
        "error": "worker crashed",
    }
    assert lock.is_locked is True

    # 7. send() itself raising HomeAssistantError vs generic OSError
    gw.send.side_effect = HomeAssistantError("queue rejected")
    with pytest.raises(HomeAssistantError, match="queue rejected"):
        await lock.async_unlock()
    assert lock.is_locked is True

    gw.send.side_effect = OSError("connection reset")
    with pytest.raises(HomeAssistantError) as exc_info:
        await lock.async_unlock()
    assert exc_info.value.translation_key == "lock_command_failed"
    assert lock.is_locked is True

    # 8. Mid-flight secondary disconnect during send() still attributes NACK/drop to F452V (not MyHomeServer1),
    #    and direct _raise_unlock_rejected() without target_gw resolves dynamically
    gw.send.side_effect = None
    gw.model = "MyHomeServer1"
    gw.profile_supports_who.return_value = False
    f452v_mid = MagicMock()
    f452v_mid.model = "F452V"
    f452v_mid.is_connected = True
    f452v_mid.profile_supports_who.return_value = True
    gw.delegated_away_whos = {6, 8}
    gw._get_secondary_for_who.return_value = f452v_mid

    fut_mid_drop: asyncio.Future[float] = hass.loop.create_future()

    def _drop_mid_flight() -> None:
        f452v_mid.is_connected = False
        fut_mid_drop.cancel()

    hass.loop.call_soon(_drop_mid_flight)
    gw.send.return_value = fut_mid_drop
    with pytest.raises(HomeAssistantError) as exc_info:
        await lock.async_unlock()
    assert exc_info.value.translation_key == "lock_command_rejected"
    assert exc_info.value.translation_placeholders["gateway"] == "F452V"

    with pytest.raises(HomeAssistantError) as exc_info:
        lock._raise_unlock_rejected("*6*10*4000##")
    assert exc_info.value.translation_key == "lock_gateway_who6_unsupported"
    assert exc_info.value.translation_placeholders["gateway"] == "MyHomeServer1"

    from custom_components.myhome.lock import warn_if_locks_unsupported

    gw.delegated_away_whos = set()
    gw._who6_locks_unsupported_warned = False
    assert warn_if_locks_unsupported(gw, [lock]) is True
    assert warn_if_locks_unsupported(gw, [lock]) is False
